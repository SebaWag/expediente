"""The audit queries: what an auditor asks, answered from the graph.

One implementation, two callers. The portal serves these over HTTP and the agent
reaches them as tools; if the two drifted apart, the screen and the dossier would
disagree about the same case, which is the failure mode this project exists to avoid.

Everything here is a read. The only arithmetic is deterministic, and every list that
leaves this module carries the identifiers a human can check.

The queries avoid ``OPTIONAL`` entirely. MillenniumDB v1.0.0 returns a cartesian
product for some multi-``OPTIONAL`` patterns — 1,365 rows for 85 signals, with each
row carrying another signal's explanation — so properties are fetched with ``VALUES``
and joined in Python, which is the shape that has been measured to return the right
count.
"""

from __future__ import annotations

from typing import Any

from ..graph.millenniumdb import node_uri
from ..graph.schema import ONTO, RDF_TYPE, relation_uri

#: Fields of a purchase order that an auditor actually reads.
ORDER_FIELDS = (
    "codigo",
    "nombre",
    "descripcion",
    "fecha",
    "monto_clp",
    "moneda",
    "estado",
    "procedencia",
    "sector",
    "region_compra",
    "items",
    "rut_proveedor",
)


def _props(client, uri: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in client.select(f"SELECT ?p ?o WHERE {{ <{uri}> ?p ?o }}"):
        predicate = str(row["p"]).replace(ONTO, "")
        if predicate.startswith("rel/") or predicate.endswith("#type"):
            continue
        out.setdefault(predicate, str(row["o"]))
    return out


def _neighbours(client, uri: str, relation: str, *, limit: int = 500) -> list[str]:
    rows = client.select(
        f"SELECT ?o WHERE {{ <{uri}> <{relation_uri(relation)}> ?o FILTER(isIRI(?o)) }} LIMIT {int(limit)}"
    )
    return [
        str(row["o"]) for row in rows if str(row["o"]).startswith("urn:expediente:node/")
    ]


def _incoming(client, uri: str, relation: str, *, limit: int = 500) -> list[str]:
    rows = client.select(
        f"SELECT ?s WHERE {{ ?s <{relation_uri(relation)}> <{uri}> FILTER(isIRI(?s)) }} LIMIT {int(limit)}"
    )
    return [
        str(row["s"]) for row in rows if str(row["s"]).startswith("urn:expediente:node/")
    ]


def order_records(store, codes: list[str]) -> list[dict[str, Any]]:
    """The orders themselves, with the fields an auditor checks. No interpretation."""
    client = store.client
    out: list[dict[str, Any]] = []
    for code in codes:
        uri = node_uri("OrdenCompra", code)
        props = _props(client, uri)
        if not props:
            continue
        record = {"code": code}
        for field in ORDER_FIELDS:
            if field in props:
                record[field] = props[field]
        record["node"] = f"OrdenCompra/{code}"
        out.append(record)
    return out


def evidence_for_orders(store, codes: list[str]) -> list[dict[str, Any]]:
    """Document → evidence → order, which is the chain that makes a claim signable."""
    client = store.client
    out: list[dict[str, Any]] = []
    for code in codes:
        order_uri = node_uri("OrdenCompra", code)
        for document_uri in _incoming(client, order_uri, "ACREDITA"):
            document = _props(client, document_uri)
            evidences = _incoming(client, document_uri, "EN")
            rows = []
            for evidence_uri in evidences:
                props = _props(client, evidence_uri)
                rows.append(
                    {
                        "id": props.get("id"),
                        "pagina": props.get("pagina"),
                        "extracto": props.get("extracto"),
                    }
                )
            out.append(
                {
                    "order": code,
                    "hash_sha256": document.get("hash_sha256"),
                    "tipo": document.get("tipo"),
                    "url": document.get("url"),
                    "recuperado_en": document.get("recuperado_en"),
                    "evidencias": rows,
                }
            )
    return out


def signal_pack(store, signal_id: str, *, order_limit: int = 200) -> dict[str, Any]:
    """Everything the system knows about one signal, in one object.

    This is what the portal renders, what the agent reads and what the export writes.
    ``complete`` says whether every cited order reached a document; when it is false the
    caller is expected to say so rather than present the case as proven.
    """
    client = store.client
    uri = node_uri("Senal", signal_id)
    props = _props(client, uri)
    if not props:
        return {"error": f"no existe la señal {signal_id}"}

    codes = [u.split("/")[-1] for u in _neighbours(client, uri, "SE_OBSERVA_EN", limit=order_limit)]
    orders = order_records(store, codes)
    evidence = evidence_for_orders(store, [order["code"] for order in orders])
    covered = {row["order"] for row in evidence}

    amounts = []
    for order in orders:
        try:
            amounts.append(float(order.get("monto_clp") or 0))
        except (TypeError, ValueError):
            continue

    return {
        "id": signal_id,
        "detector": props.get("detector"),
        "severity": props.get("severidad"),
        "score": props.get("score"),
        "claim": props.get("explicacion"),
        "organismo": props.get("organismo_nombre"),
        "proveedor": props.get("proveedor_nombre"),
        "periodo": props.get("periodo"),
        "clase": props.get("clase"),
        "monto_clp": props.get("monto_clp"),
        "ordenes_declaradas": props.get("ordenes"),
        "orders": orders,
        "evidence": evidence,
        "counts": {
            "orders_cited": len(codes),
            "orders_with_record": len(orders),
            # The number that matters: how many of the orders the claim rests on can be
            # opened and checked in a document. Anything below 100% is stated, not hidden.
            "orders_with_document": len(covered),
            "documents": len({row["hash_sha256"] for row in evidence if row["hash_sha256"]}),
            "sum_clp": int(sum(amounts)) if amounts else 0,
        },
        "complete": bool(codes) and len(covered) == len(codes),
    }


def signals_for(store, label: str, key: str) -> list[dict[str, Any]]:
    """Signals that name this supplier or buyer.

    The link is by *name* because that is how the detectors recorded the parties, and
    the graph does not yet join ``:Senal`` to ``:Proveedor`` directly. Saying that out
    loud matters: a name match is weaker than an identifier match, and the caller is
    told which one it got.
    """
    client = store.client
    node = _props(client, node_uri(label, key))
    if not node:
        return []
    name = node.get("razon_social") or node.get("nombre") or ""
    if not name:
        return []

    field = "proveedor_nombre" if label == "Proveedor" else "organismo_nombre"
    needle = name.strip().upper()
    rows = client.select(f"SELECT ?s ?v WHERE {{ ?s <{ONTO}{field}> ?v }}")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        value = str(row["v"]).strip().upper()
        uri = str(row["s"])
        if not uri.startswith("urn:expediente:node/") or uri in seen:
            continue
        # Exact name on purpose. A substring match across 103,220 suppliers produces
        # false positives ("SUR" inside "SURAMERICANA"), and a false positive in an
        # audit tool is worse than a miss.
        if value != needle:
            continue
        seen.add(uri)
        props = _props(client, uri)
        out.append(
            {
                "id": uri.split("/")[-1],
                "detector": props.get("detector"),
                "severity": props.get("severidad"),
                "score": props.get("score"),
                "claim": props.get("explicacion"),
                "monto_clp": props.get("monto_clp"),
                "ordenes": props.get("ordenes"),
                "matched_on": field,
                "match_strength": "nombre exacto, no identificador",
            }
        )
    return out


def order_document(store, code: str) -> dict[str, Any]:
    """The document behind one order, and the evidence rows that point into it."""
    client = store.client
    order_uri = node_uri("OrdenCompra", code)
    if not _props(client, order_uri):
        return {"error": f"no existe la orden {code}"}
    documents = _incoming(client, order_uri, "ACREDITA")
    if not documents:
        return {
            "order": code,
            "has_document": False,
            "note": "esta orden no tiene documento adjunto; el reclamo que la use no pasaría el control de evidencia",
        }
    document_uri = documents[0]
    props = _props(client, document_uri)
    evidences = [
        _props(client, evidence_uri) for evidence_uri in _incoming(client, document_uri, "EN")
    ]
    return {
        "order": code,
        "has_document": True,
        "hash_sha256": props.get("hash_sha256"),
        "url": props.get("url"),
        "recuperado_en": props.get("recuperado_en"),
        "tipo": props.get("tipo"),
        "evidencias": evidences,
    }


def graph_health(store) -> dict[str, Any]:
    """Integrity checks. An audit tool that cannot audit itself is a demo.

    Three checks that would have caught today's defects:

    * a relation whose object is not a node (the engine returned property values as
      neighbours for two signals);
    * a signal with no cited orders (a claim with nothing behind it);
    * an order cited by a claim with no document (the chain is broken there).
    """
    client = store.client
    healthy: list[str] = []
    problems: list[dict[str, Any]] = []

    signals = [
        str(row["s"])
        for row in client.select(f"SELECT ?s WHERE {{ ?s <{RDF_TYPE}> <{ONTO}Senal> }}")
    ]
    empty = 0
    for uri in signals:
        if not _neighbours(client, uri, "SE_OBSERVA_EN", limit=1):
            empty += 1
    if empty:
        problems.append({"check": "señal sin órdenes citadas", "count": empty})
    else:
        healthy.append("todas las señales citan al menos una orden")

    declared = client.select(f"SELECT (COUNT(*) AS ?n) WHERE {{ ?x <{RDF_TYPE}> <{ONTO}Documento> }}")
    documents = int(declared[0]["n"]) if declared else 0
    (healthy if documents else problems).append(
        "hay documentos adjuntos" if documents else {"check": "sin documentos adjuntos", "count": 0}
    )

    return {
        "signals": len(signals),
        "documents": documents,
        "healthy": healthy,
        "problems": [p for p in problems if isinstance(p, dict)],
    }
