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
    client = getattr(store, "client", None)
    if client is None:
        return []
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
    client = getattr(store, "client", None)
    if client is None:
        return []
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
    client = getattr(store, "client", None)
    if client is None:
        # The in-memory adapter has no client: the audit layer is written against the
        # engine's query interface, and saying so beats returning an empty list that
        # looks like a case with nothing in it.
        return {"error": "esta consulta necesita el motor MillenniumDB (store.client)"}
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
    client = getattr(store, "client", None)
    if client is None:
        # The in-memory adapter has no client: the audit layer is written against the
        # engine's query interface, and saying so beats returning an empty list that
        # looks like a case with nothing in it.
        return {"error": "esta consulta necesita el motor MillenniumDB (store.client)"}
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
    client = getattr(store, "client", None)
    if client is None:
        # The in-memory adapter has no client: the audit layer is written against the
        # engine's query interface, and saying so beats returning an empty list that
        # looks like a case with nothing in it.
        return {"error": "esta consulta necesita el motor MillenniumDB (store.client)"}
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
    client = getattr(store, "client", None)
    if client is None:
        # The in-memory adapter has no client: the audit layer is written against the
        # engine's query interface, and saying so beats returning an empty list that
        # looks like a case with nothing in it.
        return {"error": "esta consulta necesita el motor MillenniumDB (store.client)"}
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

    # Text that was decoded with the wrong encoding. 253 values were repaired on
    # 2026-10-29 (the bulk archives are not consistently Latin-1); what remains is
    # truncated or ambiguous, and the repair script refuses to guess. Reported here so
    # the number is visible instead of being discovered by a reader who cannot spell
    # the word they are searching for.
    mojibake = 0
    for prop in ("descripcion", "nombre", "razon_social"):
        rows = client.select(
            f'SELECT (COUNT(*) AS ?n) WHERE {{ ?x <{ONTO}{prop}> ?v FILTER(CONTAINS(STR(?v), "\u00c3")) }}'
        )
        if rows:
            mojibake += int(rows[0]["n"])
    if mojibake:
        problems.append(
            {
                "check": "texto con acentos corruptos (no reparable por round-trip)",
                "count": mojibake,
                "note": "originated in the bulk import; scripts/fix_encoding.py repaired 253 values, "
                "the rest are truncated sequences",
            }
        )
    else:
        healthy.append("sin texto con encoding corrupto")

    return {
        "signals": len(signals),
        "documents": documents,
        "mojibake_values": mojibake,
        "healthy": healthy,
        "problems": [p for p in problems if isinstance(p, dict)],
    }


#: The bidding threshold used by the detectors, in CLP. Kept here so a profile and a
#: signal are measured against the same number.
UMBRAL_CLP = 1_000_000

#: How many orders a profile reads. A municipality with 2,145 orders reads all of them
#: in about 5 s; the cap exists so a 50,000-order buyer degrades into a stated sample
#: instead of a spinner. When it bites, the answer says so.
PROFILE_ORDER_CAP = 4000


def spend_profile(store, label: str, key: str, *, top: int = 8) -> dict[str, Any]:
    """How an organism spends, or how a supplier earns.

    Three queries, and the shape is the point. The first version asked the engine for a
    COUNT, then a SUM, then a filtered COUNT, then two GROUP BYs — each one re-evaluating
    the same join, and a supplier with 670 orders took **24 s**. Reading the orders once
    and the properties once, then computing in Python, answers the same question in
    about 5 s, and the arithmetic is deterministic code rather than an engine feature.

    What the numbers mean, without adjectives: how much was spent, through which
    procedures, with whom, and how many orders sit below the bidding threshold. A direct
    award is not irregular by itself; the count is what a human then judges.
    """
    client = getattr(store, "client", None)
    if client is None:
        # The in-memory adapter has no client: the audit layer is written against the
        # engine's query interface, and saying so beats returning an empty list that
        # looks like a case with nothing in it.
        return {"error": "esta consulta necesita el motor MillenniumDB (store.client)"}
    uri = node_uri(label, key)
    props = _props(client, uri)
    if not props:
        return {"error": f"no existe {label}/{key}"}

    if label == "Organismo":
        order_query = f"SELECT ?o WHERE {{ <{uri}> <{relation_uri('EMITE')}> ?o }}"
        counterparty = "?o <{rel}> ?c"
        counterparty_rel = "ADJUDICADA_A"
        role = "comprador"
    elif label == "Proveedor":
        order_query = f"SELECT ?o WHERE {{ ?o <{relation_uri('ADJUDICADA_A')}> <{uri}> }}"
        counterparty = "?c <{rel}> ?o"
        counterparty_rel = "EMITE"
        role = "proveedor"
    else:
        return {"error": f"perfil de gasto no aplica a {label}"}

    rows = client.select(f"{order_query} LIMIT {PROFILE_ORDER_CAP + 1}")
    orders = [
        str(row["o"]) for row in rows if str(row["o"]).startswith("urn:expediente:node/")
    ]
    capped = len(orders) > PROFILE_ORDER_CAP
    orders = orders[:PROFILE_ORDER_CAP]
    if not orders:
        return {
            "label": label, "key": key,
            "name": props.get("nombre") or props.get("razon_social"),
            "role": role, "orders": 0, "note": "sin órdenes registradas en el grafo",
        }

    values = " ".join(f"<{order}>" for order in orders)
    amounts: dict[str, float] = {}
    procedures: dict[str, int] = {}
    for row in client.select(
        f"SELECT ?s ?p ?o WHERE {{ VALUES ?s {{ {values} }} ?s ?p ?o . "
        f"VALUES ?p {{ <{ONTO}monto_clp> <{ONTO}procedencia> }} }}"
    ):
        subject, predicate = str(row["s"]), str(row["p"]).replace(ONTO, "")
        if predicate == "monto_clp":
            try:
                amounts[subject] = float(row["o"])
            except (TypeError, ValueError):
                continue
        elif predicate == "procedencia":
            procedures[str(row["o"])] = procedures.get(str(row["o"]), 0) + 1

    party_counts: dict[str, int] = {}
    for row in client.select(
        f"SELECT ?o ?c WHERE {{ VALUES ?o {{ {values} }} {counterparty.format(rel=relation_uri(counterparty_rel))} "
        f"FILTER(isIRI(?c)) }}"
    ):
        other = str(row["c"])
        if other.startswith("urn:expediente:node/"):
            party_counts[other] = party_counts.get(other, 0) + 1

    names: dict[str, str] = {}
    top_uris = [uri_ for uri_, _n in sorted(party_counts.items(), key=lambda item: -item[1])[: int(top)]]
    if top_uris:
        party_values = " ".join(f"<{u}>" for u in top_uris)
        for row in client.select(
            f"SELECT ?s ?p ?o WHERE {{ VALUES ?s {{ {party_values} }} ?s ?p ?o . "
            f"VALUES ?p {{ <{ONTO}razon_social> <{ONTO}nombre> }} }}"
        ):
            names.setdefault(str(row["s"]), str(row["o"]))

    total_clp = int(sum(amounts.values()))
    over = sum(1 for amount in amounts.values() if amount >= UMBRAL_CLP)
    count = len(orders)

    return {
        "label": label,
        "key": key,
        "name": props.get("nombre") or props.get("razon_social"),
        "role": role,
        "orders": count,
        "orders_declared_in_graph": props.get("ordenes"),
        "capped": capped,
        "total_clp": total_clp,
        "average_clp": int(total_clp / count) if count else 0,
        "threshold_clp": UMBRAL_CLP,
        "orders_at_or_above_threshold": over,
        "orders_below_threshold": count - over,
        "procedures": [
            {"procedimiento": name, "ordenes": amount, "share": round(amount / count, 4)}
            for name, amount in sorted(procedures.items(), key=lambda item: -item[1])
        ],
        "counterparties": [
            {
                "node": uri_[len("urn:expediente:node/"):],
                "name": names.get(uri_) or uri_.split("/")[-1],
                "orders": amount,
                "share": round(amount / count, 4),
            }
            for uri_, amount in sorted(party_counts.items(), key=lambda item: -item[1])[: int(top)]
        ],
        "note": (
            ("Leí las primeras {n} órdenes de {total}: el perfil es una muestra declarada. "
             .format(n=count, total=props.get("ordenes") or "más"))
            if capped else "Conté todas las órdenes de esta entidad en el grafo, no una muestra. "
        )
        + "Un trato directo no es irregular por sí mismo: hay causales legales. Lo que se mide es cuánto hay.",
    }
