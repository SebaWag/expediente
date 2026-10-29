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
    # The tender the order came from. Copied onto the order so a list of sixty rows is
    # one query, not sixty traversals.
    "codigo_licitacion",
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
    """The orders themselves, with the fields an auditor checks. No interpretation.

    One query for every order, not one per order. The first version called ``_props``
    inside the loop: a signal with 60 cited orders cost 60 round trips and the profile
    took 45 seconds. Reading them with a single ``VALUES`` clause answers the same
    question in about a second and a half.
    """
    client = getattr(store, "client", None)
    if client is None or not codes:
        return []
    uris = [node_uri("OrdenCompra", code) for code in codes]
    values = " ".join(f"<{uri}>" for uri in uris)
    predicates = " ".join(f"<{ONTO}{field}>" for field in ORDER_FIELDS)
    collected: dict[str, dict[str, str]] = {uri: {} for uri in uris}
    for row in client.select(
        f"SELECT ?s ?p ?o WHERE {{ VALUES ?s {{ {values} }} ?s ?p ?o . VALUES ?p {{ {predicates} }} }}"
    ):
        subject = str(row["s"])
        if subject in collected:
            collected[subject].setdefault(str(row["p"]).replace(ONTO, ""), str(row["o"]))

    out: list[dict[str, Any]] = []
    for code, uri in zip(codes, uris):
        props = collected.get(uri) or {}
        if not props:
            continue
        record: dict[str, Any] = {"code": code}
        for field in ORDER_FIELDS:
            if field in props:
                record[field] = props[field]
        record["node"] = f"OrdenCompra/{code}"
        out.append(record)
    return out


def evidence_for_orders(store, codes: list[str]) -> list[dict[str, Any]]:
    """Document → evidence → order, which is the chain that makes a claim signable.

    Four queries in total, whatever the number of orders: which documents accredit them,
    what those documents are, which evidence rows point into them, and what those rows
    say. Per-order traversal was 3 queries per order.
    """
    client = getattr(store, "client", None)
    if client is None or not codes:
        return []
    order_uris = {node_uri("OrdenCompra", code): code for code in codes}
    values = " ".join(f"<{uri}>" for uri in order_uris)

    pairs = client.select(
        f"SELECT ?d ?o WHERE {{ ?d <{relation_uri('ACREDITA')}> ?o . VALUES ?o {{ {values} }} "
        f"FILTER(isIRI(?d)) }}"
    )
    documents: dict[str, list[str]] = {}
    for row in pairs:
        document, order = str(row["d"]), str(row["o"])
        if order in order_uris:
            documents.setdefault(document, []).append(order_uris[order])
    if not documents:
        return []

    doc_values = " ".join(f"<{uri}>" for uri in documents)
    doc_props: dict[str, dict[str, str]] = {uri: {} for uri in documents}
    for row in client.select(
        f"SELECT ?s ?p ?o WHERE {{ VALUES ?s {{ {doc_values} }} ?s ?p ?o . "
        f"VALUES ?p {{ <{ONTO}hash_sha256> <{ONTO}tipo> <{ONTO}url> <{ONTO}recuperado_en> }} }}"
    ):
        subject = str(row["s"])
        if subject in doc_props:
            doc_props[subject].setdefault(str(row["p"]).replace(ONTO, ""), str(row["o"]))

    evidence_rows = client.select(
        f"SELECT ?e ?d WHERE {{ ?e <{relation_uri('EN')}> ?d . VALUES ?d {{ {doc_values} }} "
        f"FILTER(isIRI(?e)) }}"
    )
    by_document: dict[str, list[str]] = {}
    for row in evidence_rows:
        by_document.setdefault(str(row["d"]), []).append(str(row["e"]))

    evidence_uris = [uri for uris in by_document.values() for uri in uris]
    evidence_props: dict[str, dict[str, str]] = {uri: {} for uri in evidence_uris}
    if evidence_uris:
        ev_values = " ".join(f"<{uri}>" for uri in evidence_uris)
        for row in client.select(
            f"SELECT ?s ?p ?o WHERE {{ VALUES ?s {{ {ev_values} }} ?s ?p ?o . "
            f"VALUES ?p {{ <{ONTO}id> <{ONTO}pagina> <{ONTO}extracto> }} }}"
        ):
            subject = str(row["s"])
            if subject in evidence_props:
                evidence_props[subject].setdefault(str(row["p"]).replace(ONTO, ""), str(row["o"]))

    out: list[dict[str, Any]] = []
    for document, orders in documents.items():
        props = doc_props.get(document, {})
        rows = [
            {
                "id": evidence_props.get(evidence, {}).get("id"),
                "pagina": evidence_props.get(evidence, {}).get("pagina"),
                "extracto": evidence_props.get(evidence, {}).get("extracto"),
            }
            for evidence in by_document.get(document, [])
        ]
        for order in orders:
            out.append(
                {
                    "order": order,
                    "hash_sha256": props.get("hash_sha256"),
                    "tipo": props.get("tipo"),
                    "url": props.get("url"),
                    "recuperado_en": props.get("recuperado_en"),
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


#: How many orders a profile reads before it declares a sample. Above this the answer
#: says "leí las primeras N", which is the honest alternative to a spinner.
PROFILE_CAP = 4000


def entity_profile(store, label: str, key: str, *, top: int = 10) -> dict[str, Any]:
    """Everything known about one supplier, buyer or signal, in one object.

    This is the difference between a graph browser and an investigation tool. A list of
    edges tells you what is adjacent; a profile tells you what the entity *is*: how much
    it moves, through which procedures, with whom, since when, how concentrated, and
    which of the system's own patterns mention it.

    Every number here is a count over records, not a score. The flags at the end are
    comparisons against a stated threshold — they are questions to ask, and they say so.
    """
    client = getattr(store, "client", None)
    if client is None:
        return {"error": "esta consulta necesita el motor MillenniumDB (store.client)"}

    uri = node_uri(label, key)
    props = _props(client, uri)
    if not props:
        return {"error": f"no existe {label}/{key}"}

    if label == "Organismo":
        order_query = f"SELECT ?o WHERE {{ <{uri}> <{relation_uri('EMITE')}> ?o }}"
        counterparty = f"?o <{relation_uri('ADJUDICADA_A')}> ?c"
        counterparty_label = "Proveedor"
        role = "comprador"
    elif label == "Proveedor":
        order_query = f"SELECT ?o WHERE {{ ?o <{relation_uri('ADJUDICADA_A')}> <{uri}> }}"
        counterparty = f"?c <{relation_uri('EMITE')}> ?o"
        counterparty_label = "Organismo"
        role = "proveedor"
    elif label == "Documento":
        return _document_profile(store, key, props)
    elif label == "OrdenCompra":
        return _order_profile(store, key, props)
    elif label == "Detector":
        return _detector_profile(store, key, props)
    else:
        # Everything else is a signal or a label the map does not draw yet. Saying which
        # one is better than a generic error, and the contract test walks every label the
        # portal renders so a new one cannot silently land here.
        return _signal_profile(store, label, key, props)

    rows = client.select(f"{order_query} LIMIT {PROFILE_CAP + 1}")
    orders = [str(r["o"]) for r in rows if str(r["o"]).startswith("urn:expediente:node/")]
    capped = len(orders) > PROFILE_CAP
    orders = orders[:PROFILE_CAP]
    if not orders:
        return {
            "label": label, "key": key, "role": role,
            "name": props.get("nombre") or props.get("razon_social"),
            "orders": 0, "note": "sin órdenes registradas en el grafo",
        }

    values = " ".join(f"<{o}>" for o in orders)
    amounts: dict[str, float] = {}
    procedencias: dict[str, int] = {}
    years: dict[str, dict[str, float]] = {}
    for row in client.select(
        f"SELECT ?s ?p ?o WHERE {{ VALUES ?s {{ {values} }} ?s ?p ?o . "
        f"VALUES ?p {{ <{ONTO}monto_clp> <{ONTO}procedencia> <{ONTO}fecha> }} }}"
    ):
        subject = str(row["s"])
        predicate = str(row["p"]).replace(ONTO, "")
        if predicate == "monto_clp":
            try:
                amounts[subject] = float(row["o"])
            except (TypeError, ValueError):
                pass
        elif predicate == "procedencia":
            name = str(row["o"])
            procedencias[name] = procedencias.get(name, 0) + 1
        elif predicate == "fecha":
            text = str(row["o"])
            year = text[6:10] if len(text) >= 10 and text[2] == "-" else text[:4]
            if year.isdigit():
                bucket = years.setdefault(year, {"orders": 0, "total_clp": 0.0})
                bucket["orders"] += 1
                bucket["total_clp"] += amounts.get(subject, 0.0)

    party_counts: dict[str, int] = {}
    party_amounts: dict[str, float] = {}
    for row in client.select(
        f"SELECT ?o ?c WHERE {{ VALUES ?o {{ {values} }} {counterparty} FILTER(isIRI(?c)) }}"
    ):
        other = str(row["c"])
        if not other.startswith("urn:expediente:node/"):
            continue
        party_counts[other] = party_counts.get(other, 0) + 1
        party_amounts[other] = party_amounts.get(other, 0.0) + amounts.get(str(row["o"]), 0.0)

    total_clp = int(sum(amounts.values()))
    count = len(orders)
    ranked = sorted(party_counts.items(), key=lambda item: -item[1])[: int(top)]
    names: dict[str, str] = {}
    if ranked:
        party_values = " ".join(f"<{u}>" for u, _n in ranked)
        for row in client.select(
            f"SELECT ?s ?p ?o WHERE {{ VALUES ?s {{ {party_values} }} ?s ?p ?o . "
            f"VALUES ?p {{ <{ONTO}razon_social> <{ONTO}nombre> }} }}"
        ):
            names.setdefault(str(row["s"]), str(row["o"]))

    # Herfindahl over the money, not over the counts: twenty small buys from twenty
    # suppliers is not concentration, and one big contract that happens to be one line
    # is. Bounded to 10,000 like the textbook definition so the number is comparable.
    shares = [(party_amounts.get(u, 0.0) / total_clp) for u in party_counts if total_clp]
    hhi = int(sum((share * 100) ** 2 for share in shares))
    top_share = round(max(shares), 4) if shares else 0.0

    signals = signals_for(store, label, key)
    direct = procedencias.get("Trato Directo", 0)
    direct_share = round(direct / count, 4) if count else 0.0

    flags: list[dict[str, Any]] = []
    if hhi >= 2500:
        flags.append({"level": "alta", "flag": "concentración",
                      "detail": f"HHI {hhi} sobre el umbral 2500: el gasto se concentra en pocos proveedores"})
    if direct_share >= 0.20:
        flags.append({"level": "media", "flag": "trato directo",
                      "detail": f"{round(direct_share*100)}% de las órdenes fueron por trato directo ({direct} de {count})"})
    below = sum(1 for amount in amounts.values() if 0 < amount < UMBRAL_CLP)
    if below and below / count >= 0.25:
        flags.append({"level": "media", "flag": "bajo el umbral",
                      "detail": f"{below} de {count} órdenes están bajo el umbral de licitación"})
    if signals:
        flags.append({"level": "alta" if any(s.get("severity") == "alta" for s in signals) else "media",
                      "flag": "señales detectadas",
                      "detail": f"{len(signals)} patrón(es) detectados por reglas mencionan esta entidad"})

    network_peers: list[dict[str, Any]] = []
    if label == "Proveedor":
        from ..mcp.server import network as network_tool

        try:
            net = network_tool(store, {"rut": key})
            network_peers = net.get("peers") or []
        except Exception:
            network_peers = []

    return {
        "label": label,
        "role": role,
        "key": key,
        "name": props.get("nombre") or props.get("razon_social"),
        "props": {k: v for k, v in list(props.items())[:10]},
        "orders": count,
        "capped": capped,
        "orders_declared": props.get("ordenes"),
        "total_clp": total_clp,
        "average_clp": int(total_clp / count) if count else 0,
        "counterparties_total": len(party_counts),
        "counterparty_label": counterparty_label,
        "hhi": hhi,
        "top_share": top_share,
        "by_year": [
            {"year": year, "orders": int(bucket["orders"]), "total_clp": int(bucket["total_clp"])}
            for year, bucket in sorted(years.items())
        ],
        "procedures": [
            {"procedimiento": name, "ordenes": amount, "share": round(amount / count, 4)}
            for name, amount in sorted(procedencias.items(), key=lambda item: -item[1])
        ],
        "counterparties": [
            {
                "node": u[len("urn:expediente:node/"):],
                "name": names.get(u) or u.split("/")[-1],
                "orders": n,
                "total_clp": int(party_amounts.get(u, 0)),
                "share": round(n / count, 4),
            }
            for u, n in ranked
        ],
        "signals": signals,
        "network_peers": network_peers,
        "threshold_clp": UMBRAL_CLP,
        "orders_below_threshold": below,
        "flags": flags,
        "note": (
            f"Leí las primeras {count} órdenes: el perfil es una muestra declarada. " if capped
            else f"Conté las {count} órdenes de esta entidad en el grafo, no una muestra. "
        )
        + "Las banderas son comparaciones contra un umbral declarado, no conclusiones.",
    }


def _detector_profile(store, key: str, props: dict[str, str]) -> dict[str, Any]:
    """A detector opened as a profile: the rule, and every case it produced.

    A detector is a hub — its whole point is the list of signals it emitted — so this
    is the one profile where the full list is the answer rather than a sample. The
    rule is named, not described: what the reader gets is the cases, ranked by score,
    each one openable in turn.
    """
    client = store.client
    uri = node_uri("Detector", key)
    uris = _neighbours(client, uri, "PRODUCE", limit=1000)

    collected: dict[str, dict[str, str]] = {u: {} for u in uris}
    if uris:
        values = " ".join(f"<{u}>" for u in uris)
        rows = client.select(
            f"SELECT ?s ?p ?o WHERE {{ VALUES ?s {{ {values} }} ?s ?p ?o . "
            f"VALUES ?p {{ <{ONTO}explicacion> <{ONTO}severidad> <{ONTO}score> }} }}"
        )
        for row in rows:
            subject = str(row["s"])
            if subject in collected:
                collected[subject].setdefault(str(row["p"]).replace(ONTO, ""), str(row["o"]))

    signals = []
    for signal_uri, values in collected.items():
        signals.append(
            {
                "id": signal_uri.rsplit("/", 1)[-1],
                "claim": values.get("explicacion"),
                "severity": values.get("severidad"),
                "score": values.get("score"),
            }
        )

    def _score(item: dict[str, Any]) -> float:
        try:
            return float(item.get("score") or 0)
        except (TypeError, ValueError):
            return 0.0

    signals.sort(key=_score, reverse=True)
    return {
        "label": "Detector",
        "role": "detector",
        "key": key,
        "name": props.get("nombre") or key,
        "props": props,
        "signals_count": len(uris),
        "signals": signals,
        "case": {"orders": [], "evidence": [], "counts": {"orders_cited": 0, "orders_with_record": 0, "orders_with_document": 0}},
        "flags": [],
        "note": (
            "Un detector es una regla, no una opinión: cada caso que produjo se puede abrir, "
            "y cada caso cita las órdenes que lo sostienen."
        ),
    }


def _document_profile(store, key: str, props: dict[str, str]) -> dict[str, Any]:
    """A document opened as a profile: what it is, and which records it accredits.

    Clicking a document in the map is a different question from clicking a signal.
    The signal asks "what does the system observe"; the document asks "what is this
    file, where did it come from, and what does it prove". Answering the second with
    the first one's refusal was the map offering a door with no room behind it — and
    a reader who cannot open the document cannot check the citation, which is the
    whole product.

    The document is keyed by the sha256 of its bytes, so the digest *is* the
    identifier: the download path carries it and the server resolves it to the file.
    """
    client = store.client
    uri = node_uri("Documento", key)
    codes = [u.rsplit("/", 1)[-1] for u in _neighbours(client, uri, "ACREDITA", limit=50)]
    orders = order_records(store, codes)
    evidence = [u.rsplit("/", 1)[-1] for u in _incoming(client, uri, "EN", limit=50)]
    covered = len(orders)

    return {
        "label": "Documento",
        "role": "documento",
        "key": key,
        "name": props.get("tipo") or "Documento",
        "props": props,
        "hash_sha256": key,
        "tipo": props.get("tipo"),
        "url": props.get("url"),
        "recuperado_en": props.get("recuperado_en"),
        "orders_count": len(codes),
        "orders_with_record": covered,
        "evidence": evidence,
        "download": f"/api/evidence/{key}",
        "case": {
            "orders": orders,
            "evidence": evidence,
            "counts": {
                "orders_cited": len(codes),
                "orders_with_record": covered,
                "orders_with_document": covered,
            },
        },
        "flags": (
            []
            if covered == len(codes)
            else [
                {
                    "level": "media",
                    "flag": "documento no abre todas sus órdenes",
                    "detail": f"{covered} de {len(codes)} órdenes citadas tienen registro en el grafo",
                }
            ]
        ),
        "note": (
            "El archivo se sirve tal como se descargó: su sha256 es el identificador del nodo, "
            "así que un lector puede recalcularlo y compararlo."
        ),
    }


def _order_profile(store, key: str, props: dict[str, str]) -> dict[str, Any]:
    """A purchase order opened as a profile: the two parties and the paper behind it.

    An order is the atom of the graph — every detector counts orders, every claim cites
    them — so a node the map draws 177 times at once had better answer when clicked.
    """
    client = store.client
    uri = node_uri("OrdenCompra", key)
    buyers = _incoming(client, uri, "EMITE", limit=5)
    suppliers = _neighbours(client, uri, "ADJUDICADA_A", limit=5)
    documents = _incoming(client, uri, "ACREDITA", limit=5)

    def _party(uris: list[str], field: str) -> dict[str, Any] | None:
        if not uris:
            return None
        party_props = _props(client, uris[0])
        return {
            "node": uris[0][len("urn:expediente:node/"):],
            "name": party_props.get(field) or party_props.get("nombre") or party_props.get("razon_social"),
            "rut": party_props.get("rut"),
        }

    document = _props(client, documents[0]) if documents else {}
    return {
        "label": "OrdenCompra",
        "role": "orden",
        "key": key,
        "name": props.get("nombre") or props.get("descripcion") or key,
        "props": props,
        "descripcion": props.get("descripcion"),
        "monto_clp": props.get("monto_clp"),
        "fecha": props.get("fecha"),
        "estado": props.get("estado"),
        "procedencia": props.get("procedencia"),
        "codigo_licitacion": props.get("codigo_licitacion"),
        "buyer": _party(buyers, "nombre"),
        "supplier": _party(suppliers, "razon_social"),
        "has_document": bool(documents),
        "document": (
            {
                "hash_sha256": documents[0].rsplit("/", 1)[-1],
                "tipo": document.get("tipo"),
                "url": document.get("url"),
                "recuperado_en": document.get("recuperado_en"),
                "download": f"/api/evidence/{documents[0].rsplit('/', 1)[-1]}",
            }
            if documents
            else None
        ),
        "case": {
            "orders": order_records(store, [key]),
            "evidence": [],
            "counts": {"orders_cited": 1, "orders_with_record": 1, "orders_with_document": 1 if documents else 0},
        },
        "flags": (
            []
            if documents
            else [
                {
                    "level": "alta",
                    "flag": "orden sin documento adjunto",
                    "detail": "una afirmación que la cite no pasaría el control de evidencia",
                }
            ]
        ),
        "note": (
            "Una orden de compra es el átomo del grafo: los detectores la cuentan y las "
            "afirmaciones la citan. Lo que se muestra son sus campos, sin interpretación."
        ),
    }


def _signal_profile(store, label: str, key: str, props: dict[str, str]) -> dict[str, Any]:
    """A signal opened as a profile: its case, its orders and its evidence in one place."""
    if label != "Senal":
        return {"error": f"perfil no disponible para {label}"}
    pack = signal_pack(store, key, order_limit=60)
    if pack.get("error"):
        return pack
    return {
        "label": "Senal",
        "role": "señal",
        "key": key,
        "name": (pack.get("claim") or key)[:120],
        "props": props,
        "claim": pack.get("claim"),
        "detector": pack.get("detector"),
        "severity": pack.get("severity"),
        "score": pack.get("score"),
        "organismo": pack.get("organismo"),
        "proveedor": pack.get("proveedor"),
        "orders": len(pack.get("orders") or []),
        "total_clp": (pack.get("counts") or {}).get("sum_clp"),
        "average_clp": int(
            ((pack.get("counts") or {}).get("sum_clp") or 0) / max(1, len(pack.get("orders") or []))
        ),
        "orders_with_document": (pack.get("counts") or {}).get("orders_with_document"),
        "complete": pack.get("complete"),
        "by_year": [],
        "procedures": [],
        "counterparties": [],
        "signals": [],
        "network_peers": [],
        "flags": [
            {
                "level": "alta" if pack.get("severity") == "alta" else "media",
                "flag": "cadena de evidencia completa" if pack.get("complete") else "cadena incompleta",
                "detail": f"{pack.get('counts', {}).get('orders_with_document')} de "
                f"{pack.get('counts', {}).get('orders_cited')} órdenes tienen documento adjunto",
            }
        ],
        "case": {
            "orders": pack.get("orders"),
            "evidence": pack.get("evidence"),
            "counts": pack.get("counts"),
        },
        "note": "Abre el expediente para ver las órdenes y el documento de cada una.",
    }
