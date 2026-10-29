"""An MCP server over the graph, with no dependencies.

The hackathon's tooling track asks for the graph to be reachable from an agent's
runtime — Muse Code, Claude Code, anything that speaks MCP. This module implements the
three tools the graph was designed around and nothing else:

    neighbours      what is attached to this node, and through which edge
    find_paths      how A reaches B, hop by hop, with the edges named
    citation_trail  the claim -> evidence -> document -> record chain

Plus one convenience the demo needs: ``search``, because a human starts from
"FONASA", not from ``urn:expediente:node/Organismo/61.601.000-6``.

Why no SDK: the transport is JSON-RPC 2.0 over stdio, newline-delimited. That is a few
hundred lines of dispatch, and a dependency here would mean the demo cannot run on a
machine without pip. The protocol surface implemented is the one a client actually
calls — ``initialize``, ``tools/list``, ``tools/call``, ``ping`` — and unknown methods
answer with a JSON-RPC error instead of silence.

Run it:

    EXPEDIENTE_MDB_URL=http://127.0.0.1:1235 python3 -m expediente.mcp.server

Every response is one JSON object per line on stdout. Diagnostics go to stderr, never
to stdout: a stray print in a stdio server corrupts the protocol.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Iterable

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "expediente-graph"
SERVER_VERSION = "0.1.0"

#: Guard rails. An agent asking for "everything" is a bug, not a request.
MAX_LIMIT = 200
MAX_HOPS = 4


def _split_node(value: str) -> tuple[str, str]:
    """Accept both ``Label/key`` and a full ``urn:expediente:node/Label/key`` URI."""
    text = value.strip()
    prefix = "urn:expediente:node/"
    if text.startswith(prefix):
        text = text[len(prefix) :]
    if "/" not in text:
        raise ValueError(f"node must be 'Label/key' (got {value!r})")
    label, key = text.split("/", 1)
    return label, key


def _node_arg(
    args: dict[str, Any], prefix: str = "", names: tuple[str, ...] = ("node",)
) -> tuple[str, str]:
    """Read a node from a ``Label/key`` argument or from an explicit label+key pair.

    Named arguments matter here: ``find_paths`` takes two nodes, so reading "whatever
    node-ish key is present" would silently return the source for the destination.
    """
    for name in names:
        direct = args.get(name)
        if isinstance(direct, str) and direct:
            return _split_node(direct)
    label = args.get(f"{prefix}label")
    key = args.get(f"{prefix}key")
    if isinstance(label, str) and isinstance(key, str):
        return label, key
    raise ValueError(
        f"a node is required: pass {'/'.join(names)} as 'Label/key', or {prefix}label+{prefix}key"
    )


def _clamp(value: Any, default: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(maximum, number))


# --------------------------------------------------------------------- tools
def _relation_counts(store, label: str, key: str, direction: str) -> list[dict[str, Any]]:
    """Count neighbours per relation, using the engine when it can count and Python
    when it cannot.

    The MCP tools must be usable against the in-memory store too: a tool that only
    works with the database cannot be tested without one, and the demo must run for
    somebody who only has Python. On the engine this is one GROUP BY; in memory it is
    a walk over the adjacency lists, which is fine because they are already there.
    """
    from expediente.graph.schema import REL_PREFIX, node_uri

    if getattr(store, "client", None) is None:
        tally: dict[tuple[str, str], int] = {}
        for relation, _other, _props, reversed_ in store.adjacent_directed((label, key), direction):
            side = "in" if reversed_ else "out"
            tally[(relation, side)] = tally.get((relation, side), 0) + 1
        return [
            {"relation": relation, "direction": side, "count": count}
            for (relation, side), count in sorted(tally.items(), key=lambda kv: -kv[1])
        ]

    uri = node_uri(label, key)
    out: list[dict[str, Any]] = []
    if direction in ("out", "both"):
        rows = store.client.select(
            f'SELECT ?p (COUNT(*) AS ?n) WHERE {{ <{uri}> ?p ?o . '
            f'FILTER(STRSTARTS(STR(?p), "{REL_PREFIX}")) }} GROUP BY ?p'
        )
        out += [
            {"relation": str(r["p"])[len(REL_PREFIX) :], "direction": "out", "count": int(r["n"])}
            for r in rows
        ]
    if direction in ("in", "both"):
        rows = store.client.select(
            f'SELECT ?p (COUNT(*) AS ?n) WHERE {{ ?s ?p <{uri}> . '
            f'FILTER(STRSTARTS(STR(?p), "{REL_PREFIX}")) }} GROUP BY ?p'
        )
        out += [
            {"relation": str(r["p"])[len(REL_PREFIX) :], "direction": "in", "count": int(r["n"])}
            for r in rows
        ]
    return sorted(out, key=lambda r: -r["count"])


def neighbours(store, args: dict[str, Any]) -> dict[str, Any]:
    """Counts per relation, then a bounded sample per relation.

    The three questions are separated because they cost very different things:

    * *how many, by relation* — a GROUP BY, answered by the engine in about a second
      even for a supplier with 44,913 awards;
    * *which ones* — a sample, so the payload is bounded by the caller's limit;
    * *and the provenance of each* — never fetched for the whole neighbourhood.

    A predicate-free ``?s ?p <uri>`` scan is what made this slow: it cannot use the
    index on the relation. The sample therefore asks per relation, with the predicate
    bound, which the engine answers immediately.
    """
    from expediente.graph.schema import REL_PREFIX, node_uri, relation_uri

    label, key = _node_arg(args, "", ("node",))
    direction = args.get("direction") or "both"
    if direction not in ("out", "in", "both"):
        raise ValueError("direction must be 'out', 'in' or 'both'")
    limit = _clamp(args.get("limit"), 50, MAX_LIMIT)

    props = store.node(label, key)
    if props is None:
        return {"node": f"{label}/{key}", "found": False, "neighbours": []}

    uri = node_uri(label, key)
    relations = _relation_counts(store, label, key, direction)
    relations.sort(key=lambda r: -r["count"])
    total = sum(r["count"] for r in relations)

    # Spread the limit over the relations actually present, so one high-degree edge
    # cannot crowd out a rare but interesting one.
    per_relation = max(1, limit // max(1, len(relations)))
    found: list[dict[str, Any]] = []
    if getattr(store, "client", None) is None:
        for relation, other, _props, reversed_ in store.adjacent_directed(
            (label, key), direction, limit=limit
        ):
            found.append(
                {
                    "relation": relation,
                    "direction": "in" if reversed_ else "out",
                    "node": f"{other[0]}/{other[1]}",
                }
            )
    else:
        for entry in relations:
            if len(found) >= limit:
                break
            predicate = relation_uri(entry["relation"])
            if entry["direction"] == "out":
                rows = store.client.select(
                    f"SELECT ?o WHERE {{ <{uri}> <{predicate}> ?o }} LIMIT {per_relation}"
                )
                others = [str(r["o"]) for r in rows if "o" in r]
            else:
                rows = store.client.select(
                    f"SELECT ?s WHERE {{ ?s <{predicate}> <{uri}> }} LIMIT {per_relation}"
                )
                others = [str(r["s"]) for r in rows if "s" in r]
            for other in others[: max(0, limit - len(found))]:
                found.append(
                    {
                        "relation": entry["relation"],
                        "direction": entry["direction"],
                        "node": other.split("urn:expediente:node/", 1)[-1],
                    }
                )

    return {
        "node": f"{label}/{key}",
        "found": True,
        "properties": props,
        "total": total,
        "by_relation": relations,
        "neighbours": found,
        "truncated": total > len(found),
    }


def _paths_of_length(store, src, dst, hops: int, limit: int) -> list[dict[str, Any]]:
    """All forward routes of exactly ``hops`` edges, in one engine-evaluated query.

    A breadth-first walk in Python issues one request per visited node: from a buyer
    with 4,015 orders that is 4,015 round trips for a two-hop question. The engine
    evaluates the same shape as a join over its own indexes, so the depth is the loop
    and the network call is inside it, not per visited node.
    """
    from expediente.graph.schema import REL_PREFIX, node_uri

    src_uri, dst_uri = node_uri(*src), node_uri(*dst)
    predicates = [f"?p{i}" for i in range(1, hops + 1)]
    mids = [f"?n{i}" for i in range(1, hops)]

    if hops == 1:
        pattern = f"<{src_uri}> ?p1 <{dst_uri}> ."
        select = "?p1"
        binders: list[str] = []
    else:
        parts = [f"<{src_uri}> ?p1 ?n1 ."]
        for i in range(1, hops - 1):
            parts.append(f"?n{i} ?p{i + 1} ?n{i + 1} .")
        parts.append(f"?n{hops - 1} ?p{hops} <{dst_uri}> .")
        pattern = " ".join(parts)
        select = " ".join(predicates + mids)
        # simple paths: the start is never an interior node and no node repeats
        binders = [f"FILTER(?n{i} != <{src_uri}> && ?n{i} != <{dst_uri}>)" for i in range(1, hops)]
        for i in range(1, hops):
            for j in range(i + 1, hops):
                binders.append(f"FILTER(?n{i} != ?n{j})")

    relation_filters = " ".join(
        f'FILTER(STRSTARTS(STR({var}), "{REL_PREFIX}"))' for var in predicates
    )
    query = (
        f"SELECT {select} WHERE {{ {pattern} {relation_filters} {' '.join(binders)} }} "
        f"LIMIT {limit}"
    )
    rows = store.client.select(query)

    routes: list[dict[str, Any]] = []
    for row in rows:
        steps: list[dict[str, Any]] = []
        current = f"{src[0]}/{src[1]}"
        for i in range(1, hops + 1):
            if i == hops:
                target = f"{dst[0]}/{dst[1]}"
            else:
                target = str(row[f"n{i}"]).split("urn:expediente:node/", 1)[-1]
            steps.append(
                {
                    "relation": str(row[f"p{i}"])[len(REL_PREFIX) :],
                    "from": current,
                    "to": target,
                }
            )
            current = target
        routes.append({"hops": hops, "path": steps})
    return routes


def _bfs_paths(store, src, dst, max_hops: int, limit: int) -> list[dict[str, Any]]:
    """Depth-first-complete fallback for stores with no query engine.

    Same contract as the engine path, different cost: correct for the in-memory store
    and for tests, never used against the database where the join exists.
    """
    found: list[dict[str, Any]] = []

    def walk(node: tuple[str, str], trail: list[dict[str, Any]], seen: set[tuple[str, str]]) -> None:
        if len(found) >= limit or len(trail) >= max_hops:
            return
        for relation, other, _props, reversed_ in store.adjacent_directed(node, "out"):
            step = {
                "relation": relation,
                "from": f"{node[0]}/{node[1]}",
                "to": f"{other[0]}/{other[1]}",
            }
            if other == dst:
                found.append({"hops": len(trail) + 1, "path": trail + [step]})
                if len(found) >= limit:
                    return
                continue
            if other in seen:
                continue
            walk(other, trail + [step], seen | {other})

    walk(src, [], {src})
    return found


def find_paths(store, args: dict[str, Any]) -> dict[str, Any]:
    src = _node_arg(args, "src_", ("src",))
    dst = _node_arg(args, "dst_", ("dst",))
    if src == dst:
        raise ValueError("src and dst must differ")
    max_hops = _clamp(args.get("max_hops"), 3, MAX_HOPS)
    limit = _clamp(args.get("limit"), 10, MAX_LIMIT)

    paths: list[dict[str, Any]] = []
    if getattr(store, "client", None) is None:
        paths = _bfs_paths(store, src, dst, max_hops, limit)
    else:
        for hops in range(1, max_hops + 1):
            if len(paths) >= limit:
                break
            paths.extend(_paths_of_length(store, src, dst, hops, limit - len(paths)))

    return {
        "src": f"{src[0]}/{src[1]}",
        "dst": f"{dst[0]}/{dst[1]}",
        "max_hops": max_hops,
        "paths_found": len(paths),
        "by_length": {h: sum(1 for p in paths if p["hops"] == h) for h in range(1, max_hops + 1)},
        "paths": paths,
    }


def search(store, args: dict[str, Any]) -> dict[str, Any]:
    """Find nodes of a label by name, expanding acronyms first.

    Two variants are searched: the literal text, and — if it is a known acronym — the
    official name. Results are merged and each keeps how it was found, so a caller can
    tell a literal match from an expanded one instead of guessing why "FONASA" returned
    something.
    """
    from expediente.aliases import expand

    label = args.get("label")
    if not isinstance(label, str) or not label:
        raise ValueError("label is required (e.g. 'Organismo', 'Proveedor')")
    text = (args.get("text") or "").strip()
    if len(text) < 2:
        raise ValueError("text must be at least 2 characters")
    limit = _clamp(args.get("limit"), 20, MAX_LIMIT)

    # Property-aware: a supplier is found by razón social, a buyer by name, and falling
    # back to "any literal" keeps the tool usable before a label has a name property.
    prop = {"Organismo": "nombre", "Proveedor": "razon_social"}.get(label)
    target = f"<urn:expediente:onto/{prop}>" if prop else "?p"

    def _escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    # A three-letter needle inside a Spanish word is a substring, not a name: "EFE"
    # matched "CENTRO DE REFERENCIA" and "SAG" matched "SAGRADA FAMILIA". Short,
    # space-free terms therefore have to be a whole word; longer ones may stay partial
    # so that "AGROSUP" still finds "AGROSUPER COMERCIALIZADORA".
    whole_word = len(text) <= 4 and " " not in text

    # An acronym is searched *only* through its expansion. Searching the literal too is
    # how "EFE" matched "CENTRO DE REFERENCIA" and "SAG" matched "SAGRADA FAMILIA": a
    # three-letter needle inside a Spanish word is a substring, not a name.
    variants: list[tuple[str, str]] = expand(text) or [(text, "texto")]

    merged: dict[str, dict[str, Any]] = {}
    if getattr(store, "client", None) is None:
        # Same contract without an engine: walk the nodes of this label. Correct for the
        # in-memory store and for tests, never the path used against the database.
        for node_key in store.nodes(label):
            props = store.node(*node_key) or {}
            value = str(props.get(prop or "nombre") or props.get("razon_social") or "")
            for variant, reason in variants:
                haystack = f" {value.lower()} " if whole_word else value.lower()
                needle = f" {variant.lower()} " if whole_word else variant.lower()
                if needle in haystack:
                    key = f"{node_key[0]}/{node_key[1]}"
                    merged.setdefault(key, {"node": key, "match": value, "matched_via": reason})
                    break
    else:
        for variant, reason in variants:
            left = 'CONCAT(" ", STR(?value), " ")' if whole_word else "STR(?value)"
            needle = f" {_escape(variant)} " if whole_word else _escape(variant)
            query = f"""
                SELECT DISTINCT ?s ?value WHERE {{
                    ?s <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <urn:expediente:onto/{label}> .
                    ?s {target} ?value .
                    FILTER(isLiteral(?value) && CONTAINS(LCASE({left}), LCASE("{needle}")))
                }} LIMIT {limit}
            """
            for row in store.client.select(query):
                uri = str(row["s"])
                node = uri.split("urn:expediente:node/", 1)[-1]
                if node not in merged:
                    merged[node] = {
                        "node": node,
                        "match": str(row.get("value", "")),
                        "matched_via": reason,
                    }

    return {
        "label": label,
        "text": text,
        "expanded_to": [v for v, r in variants if r != "texto"] or None,
        "count": len(merged),
        "results": list(merged.values())[:limit],
    }


def citation_trail(store, args: dict[str, Any]) -> dict[str, Any]:
    label, key = _node_arg(args, "", ("node",))
    limit = _clamp(args.get("limit"), 25, MAX_LIMIT)

    # Afirmacion -SOPORTADA_POR-> Evidencia -EN-> Documento -ACREDITA-> record
    trail: list[dict[str, Any]] = []
    afirmaciones = (
        [("Afirmacion", key)]
        if label == "Afirmacion"
        else [
            src
            for _rel, src, _props, _rev in store.adjacent_directed((label, key), "in", limit=MAX_LIMIT)
        ]
    )
    for afirmacion in [a for a in afirmaciones if a[0] == "Afirmacion"][:limit]:
        for _rel, evidencia, _p, _r in store.adjacent_directed(afirmacion, "out"):
            if evidencia[0] != "Evidencia":
                continue
            evidencia_props = store.node(*evidencia) or {}
            documents = [
                other
                for rel, other, _p, _r in store.adjacent_directed(evidencia, "out")
                if rel == "EN" and other[0] == "Documento"
            ]
            for documento in documents:
                document_props = store.node(*documento) or {}
                trail.append(
                    {
                        "claim": f"{afirmacion[0]}/{afirmacion[1]}",
                        "claim_text": (store.node(*afirmacion) or {}).get("texto"),
                        "evidence": f"{evidencia[0]}/{evidencia[1]}",
                        "page": evidencia_props.get("pagina"),
                        "extract": evidencia_props.get("extracto"),
                        "document": f"{documento[0]}/{documento[1]}",
                        # The hash is the document's identity — the same bytes we fetched.
                        # It travels as its own field because a reader checking a citation
                        # should not have to split a node id to find it.
                        "document_hash": document_props.get("hash_sha256"),
                        "document_url": document_props.get("url"),
                    }
                )

    return {
        "node": f"{label}/{key}",
        "chain_complete": bool(trail),
        "count": len(trail),
        "trail": trail,
        # Stated in the payload, because an empty trail is a result, not a failure:
        # the bulk dataset has no documents yet, so nothing in it can be cited.
        "note": None
        if trail
        else "No claim reaches a document from this node. In the bulk dataset the "
        "records carry no documents, which is exactly what the evidence gate refuses "
        "to dress up as support.",
    }


def network(store, args: dict[str, Any]) -> dict[str, Any]:
    """The suppliers that share a physical address with this one.

    This is the two-hop path ``Proveedor -> Domicilio <- Proveedor`` as a first-class
    call, because the question it answers is the one a spreadsheet cannot: *who else
    is registered at this address*.

    It is deliberately **not** a ``:Senal``. ``SE_OBSERVA_EN`` runs
    ``(:Senal) -> (:OrdenCompra)``, and a shared address is not observed in a single
    purchase order. What this returns is a bounded list of candidates with the orders
    attached, which is what a reviewer needs before deciding anything.

    The answer also carries what it is *not*: two companies in one office building are
    not a network. That sentence ships with the payload so a demo cannot quietly drop
    it.

    Two queries, not four. The engine answers any single SELECT in about 0.76 s
    regardless of size, so cost here is round trips and nothing else: name, address and
    peers come back together in one query, and the order counts in a second. Asking for
    them separately cost 3.5 s for the same answer.
    """
    from expediente.domain.normalizers import rut_display
    from expediente.graph.schema import ONTO, node_uri, relation_uri

    raw = args.get("rut") or args.get("node") or ""
    rut = rut_display(raw)
    if not rut:
        raise ValueError("a supplier RUT with its check digit is required, e.g. 79.984.240-8")

    limit = _clamp(args.get("limit"), 20, MAX_LIMIT)
    uri = node_uri("Proveedor", rut)

    client = getattr(store, "client", None)
    if client is None:
        return {"rut": rut, "found": False, "peers": [], "note": "no SPARQL client"}

    head = client.select(
        f"SELECT ?nom ?dir ?region ?prut ?pnom WHERE {{ "
        f"<{uri}> <{ONTO}razon_social> ?nom . "
        f"OPTIONAL {{ "
        f"  <{uri}> <{relation_uri('TIENE_DOMICILIO')}> ?d . "
        f"  ?d <{ONTO}direccion_texto> ?dir . "
        f"  OPTIONAL {{ ?d <{ONTO}region> ?region }} "
        f"  OPTIONAL {{ ?peer <{relation_uri('TIENE_DOMICILIO')}> ?d . "
        f"    ?peer <{ONTO}rut_norm> ?prut . ?peer <{ONTO}razon_social> ?pnom . "
        f"    FILTER(?peer != <{uri}>) }} "
        f"}} }} LIMIT {limit + 1}"
    )
    if not head:
        return {"rut": rut, "found": False, "peers": []}

    result: dict[str, Any] = {
        "rut": rut,
        "found": True,
        "proveedor": head[0].get("nom"),
    }

    if head[0].get("dir") is None:
        result.update(
            {"domicilio": None, "peers": [], "note": "no domicilio recorded for this supplier"}
        )
        return result

    peers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in head:
        peer_rut = row.get("prut")
        if not peer_rut or peer_rut in seen:
            continue
        seen.add(peer_rut)
        peers.append({"rut": peer_rut, "razon_social": row.get("pnom"), "orders": 0})
    peers = peers[:limit]

    # One GROUP BY over VALUES, predicate bound: the index is used and the 4.95 M
    # ADJUDICADA_A edges are never scanned.
    if peers:
        values = " ".join(f"<{node_uri('Proveedor', str(peer['rut']))}>" for peer in peers)
        for row in client.select(
            f"SELECT ?p (COUNT(*) AS ?n) WHERE {{ "
            f"?oc <{relation_uri('ADJUDICADA_A')}> ?p . VALUES ?p {{ {values} }} }} GROUP BY ?p"
        ):
            key = str(row["p"]).rsplit("/", 1)[-1]
            for peer in peers:
                if peer["rut"] == key:
                    peer["orders"] = int(row["n"])

    result["domicilio"] = {"direccion": head[0].get("dir"), "region": head[0].get("region")}
    result["peer_count"] = len(peers)
    result["peers"] = peers
    result["claim"] = (
        "these suppliers share a registered address; that is a candidate to review, "
        "not an identity"
    )
    result["not_claimed"] = (
        "a shared address is also what an office building looks like. No peer here is "
        "asserted to be related to any other."
    )
    result["evidence"] = {"source": "OCDS", "field": "parties[].address.streetAddress"}
    return result


TOOLS: list[dict[str, Any]] = [
    {
        "name": "network",
        "description": "Suppliers registered at the same address as this RUT: the "
        "Proveedor -> Domicilio <- Proveedor path, with each peer's order count. "
        "Returns candidates to review, never a claim of relatedness.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rut": {"type": "string", "description": "supplier RUT with check digit, e.g. 79.984.240-8"},
                "limit": {"type": "integer", "default": 20, "maximum": MAX_LIMIT},
            },
            "required": ["rut"],
        },
    },
    {
        "name": "neighbours",
        "description": "What is attached to a node, grouped by relation. Returns node "
        "properties and up to `limit` edges, each naming the relation and direction.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node": {"type": "string", "description": "'Label/key' or a node URI"},
                "label": {"type": "string"},
                "key": {"type": "string"},
                "direction": {"type": "string", "enum": ["out", "in", "both"], "default": "both"},
                "limit": {"type": "integer", "default": 50, "maximum": MAX_LIMIT},
            },
        },
    },
    {
        "name": "find_paths",
        "description": "Breadth-first search for the routes from one node to another, "
        "returning each hop with the relation that produced it.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "src": {"type": "string", "description": "source 'Label/key'"},
                "dst": {"type": "string", "description": "target 'Label/key'"},
                "max_hops": {"type": "integer", "default": 3, "maximum": MAX_HOPS},
                "limit": {"type": "integer", "default": 10, "maximum": MAX_LIMIT},
            },
            "required": ["src", "dst"],
        },
    },
    {
        "name": "search",
        "description": "Find nodes of a label whose name contains the text. Use it to "
        "turn 'FONASA' into a node key before calling the other tools.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "e.g. Organismo, Proveedor"},
                "text": {"type": "string"},
                "limit": {"type": "integer", "default": 20, "maximum": MAX_LIMIT},
            },
            "required": ["label", "text"],
        },
    },
    {
        "name": "citation_trail",
        "description": "The claim -> evidence -> document chain for a node, with page "
        "numbers. An empty trail means the claim cannot be cited, and that is reported "
        "as such.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node": {"type": "string"},
                "label": {"type": "string"},
                "key": {"type": "string"},
                "limit": {"type": "integer", "default": 25, "maximum": MAX_LIMIT},
            },
        },
    },
]

HANDLERS = {
    "neighbours": neighbours,
    "find_paths": find_paths,
    "search": search,
    "citation_trail": citation_trail,
    "network": network,
}


# ------------------------------------------------------------------ protocol
def _response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(request: dict[str, Any], store) -> dict[str, Any] | None:
    """Dispatch one JSON-RPC request. Returns None for notifications."""
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if request_id is None and method and method.startswith("notifications/"):
        return None

    if method == "initialize":
        return _response(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method == "ping":
        return _response(request_id, {})

    if method == "tools/list":
        return _response(request_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if handler is None:
            return _error(request_id, -32602, f"unknown tool: {name}")
        try:
            payload = handler(store, arguments)
        except Exception as exc:  # surfaced to the agent, not swallowed
            return _response(
                request_id,
                {"content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}], "isError": True},
            )
        return _response(
            request_id,
            {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]},
        )

    return _error(request_id, -32601, f"method not found: {method}")


def serve(store, stdin: Iterable[str] | None = None, stdout=None) -> int:
    """Read JSON-RPC lines from stdin until EOF, answering on stdout."""
    stdin = stdin if stdin is not None else sys.stdin
    stdout = stdout if stdout is not None else sys.stdout

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            stdout.write(json.dumps(_error(None, -32700, f"parse error: {exc}")) + "\n")
            stdout.flush()
            continue

        response = handle(request, store)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    from expediente.graph.millenniumdb import MillenniumDBStore

    store = MillenniumDBStore.from_env()
    print(
        f"[{SERVER_NAME}] ready on {os.environ.get('EXPEDIENTE_MDB_URL', '?')}",
        file=sys.stderr,
        flush=True,
    )
    return serve(store)


if __name__ == "__main__":
    raise SystemExit(main())
