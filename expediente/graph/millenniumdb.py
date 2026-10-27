"""MillenniumDB backend — the store port, over SPARQL.

Ontology, chosen so that the same edge can be both *traversed* and *cited*:

    node        urn:expediente:node/{Label}/{key}
                a exp:{Label} ; exp:{prop} "value"

    edge        <src> exp:rel/{RELATION} <dst>            <- traversable natively
                urn:expediente:edge/{RELATION}/{hash}     <- citable
                a exp:EdgeAnnotation ; exp:rel "RELATION" ;
                  exp:source <src> ; exp:target <dst> ; exp:{prop} "value"

The split is deliberate. Edges are plain triples so that SPARQL **property paths**
— the very thing MillenniumDB is built around — evaluate our route expressions
directly. Provenance rides on a parallel annotation node, because a citation is a
statement about a statement and has nowhere to live on a bare triple.

The annotation URI is derived from (relation, source, target), never generated, so
re-ingesting the same edge produces the same identifier.
"""

from __future__ import annotations

import hashlib
import os
import urllib.parse
from typing import Any, Iterable

from ..navigate.rpq import PATTERNS, parse, to_property_path
from .sparql import SparqlClient
from .store import NodeKey, Triple

# Imported, never redefined: the adapter and the exporter must agree on one namespace.
from .schema import EDGE_PREFIX, NODE_PREFIX, ONTO, RDF_TYPE, REL_PREFIX, XSD
from .schema import node_uri, relation_uri

XSD_INTEGER = f"{XSD}integer"
XSD_DECIMAL = f"{XSD}decimal"
XSD_BOOLEAN = f"{XSD}boolean"

#: Properties that describe the edge itself, not the world.
_RESERVED_EDGE_PROPS = {"rel", "source", "target"}


def edge_uri(relation: str, source: NodeKey, target: NodeKey) -> str:
    """Canonical annotation URI, derived from the edge's own participants.

    Derived, not generated: this is the difference between a graph that can be
    rebuilt identically and one that merely looks reproducible.
    """
    digest = hashlib.sha1(f"{relation}|{source[0]}:{source[1]}|{target[0]}:{target[1]}".encode()).hexdigest()
    return f"{EDGE_PREFIX}{relation}/{digest[:16]}"


class MillenniumDBStore:
    """Implements :class:`GraphStore` against a MillenniumDB HTTP endpoint."""

    def __init__(self, client: SparqlClient) -> None:
        self.client = client

    @classmethod
    def from_env(cls, variable: str = "EXPEDIENTE_MDB_URL") -> "MillenniumDBStore":
        """Build from ``EXPEDIENTE_MDB_URL`` (plus optional user/password variables)."""
        base_url = os.environ.get(variable, "").strip()
        if not base_url:
            raise RuntimeError(
                f"{variable} is not set. Start an isolated MillenniumDB and export the URL, "
                "for example http://127.0.0.1:1234"
            )
        client = SparqlClient(
            base_url,
            user=os.environ.get(f"{variable}_USER"),
            password=os.environ.get(f"{variable}_PASS"),
        )
        return cls(client)

    # -- writes ------------------------------------------------------------
    def upsert_node(self, label: str, key: str, **props: Any) -> NodeKey:
        uri = node_uri(label, key)
        triples = [f"<{uri}> <{RDF_TYPE}> <{ONTO}{label}>"]
        triples += [_triple(uri, name, value) for name, value in sorted(props.items()) if value is not None]
        self.client.replace(uri, triples)
        return (label, key)

    def upsert_edge(self, src: NodeKey, rel: str, dst: NodeKey, **props: Any) -> None:
        """Insert the traversable triple, then record provenance if there is any.

        RDF is a set, so re-inserting the same edge is a no-op — that is the
        idempotency the port requires, obtained without an update dialect.
        """
        self.client.insert([f"<{node_uri(*src)}> <{relation_uri(rel)}> <{node_uri(*dst)}>"])
        if not props:
            return

        annotation = edge_uri(rel, src, dst)
        triples = [
            f"<{annotation}> <{RDF_TYPE}> <{ONTO}EdgeAnnotation>",
            f'<{annotation}> <{ONTO}rel> "{rel}"',
            f"<{annotation}> <{ONTO}source> <{node_uri(*src)}>",
            f"<{annotation}> <{ONTO}target> <{node_uri(*dst)}>",
        ]
        triples += [_triple(annotation, name, value) for name, value in sorted(props.items()) if value is not None]
        self.client.replace(annotation, triples)

    # -- reads -------------------------------------------------------------
    def node(self, label: str, key: str) -> dict[str, Any] | None:
        uri = node_uri(label, key)
        # Edges are not properties. Without the filter, a buyer with 4,000 orders answers
        # this with 4,000 rows it then has to discard — measured at 24 s against the full
        # national graph, for four useful values.
        rows = self.client.select(
            f'SELECT ?p ?o WHERE {{ <{uri}> ?p ?o . '
            f'FILTER(!STRSTARTS(STR(?p), "{REL_PREFIX}")) }}'
        )
        if not rows:
            return None
        return _props_from_rows(rows)

    def nodes(self, label: str) -> list[NodeKey]:
        rows = self.client.select(
            f"SELECT ?s WHERE {{ ?s <{RDF_TYPE}> <{ONTO}{label}> }} ORDER BY ?s"
        )
        return [_key_from_uri(row["s"], label) for row in rows if "s" in row]

    def edges(self, rel: str, src: NodeKey | None = None) -> list[Triple]:
        predicate = relation_uri(rel)
        if src is None:
            query = f"SELECT ?s ?o WHERE {{ ?s <{predicate}> ?o }} ORDER BY ?s ?o"
        else:
            query = f"SELECT ?s ?o WHERE {{ <{node_uri(*src)}> <{predicate}> ?o }} ORDER BY ?o"
        rows = self.client.select(query)
        return [
            (_key_from_uri(row["s"]), _key_from_uri(row["o"]), {})
            for row in rows
            if "s" in row and "o" in row
        ]

    def incoming(self, rel: str, dst: NodeKey) -> list[Triple]:
        rows = self.client.select(
            f"SELECT ?s WHERE {{ ?s <{relation_uri(rel)}> <{node_uri(*dst)}> }} ORDER BY ?s"
        )
        return [(_key_from_uri(row["s"]), dst, {}) for row in rows if "s" in row]

    def adjacent(self, node: NodeKey, direction: str = "out") -> list[tuple[str, NodeKey, dict[str, Any]]]:
        return [
            (relation, other, props)
            for relation, other, props, _reversed in self.adjacent_directed(node, direction)
        ]

    def adjacent_directed(
        self, node: NodeKey, direction: str = "out", limit: int | None = None
    ) -> list[tuple[str, NodeKey, dict[str, Any], bool]]:
        found: list[tuple[str, NodeKey, bool]] = []
        # The cap belongs in the query, not after it: provenance is fetched per edge,
        # and a buyer with 4,000 orders turned one neighbour lookup into a 4,000-value
        # VALUES clause. Bounded at the engine, the same call answers in a second.
        cap = f" LIMIT {int(limit)}" if limit else ""

        if direction in ("out", "both"):
            rows = self.client.select(
                f"SELECT ?p ?o WHERE {{ <{node_uri(*node)}> ?p ?o . "
                f'FILTER(STRSTARTS(STR(?p), "{REL_PREFIX}")) }}{cap}'
            )
            for row in rows:
                found.append((str(row["p"])[len(REL_PREFIX) :], _key_from_uri(row["o"]), False))

        if direction in ("in", "both"):
            rows = self.client.select(
                f"SELECT ?p ?s WHERE {{ ?s ?p <{node_uri(*node)}> . "
                f'FILTER(STRSTARTS(STR(?p), "{REL_PREFIX}")) }}{cap}'
            )
            for row in rows:
                found.append((str(row["p"])[len(REL_PREFIX) :], _key_from_uri(row["s"]), True))

        # the edge as it is stored, regardless of how we walked it
        edges = [
            (relation, other, node) if reversed_ else (relation, node, other)
            for relation, other, reversed_ in found
        ]
        annotations = self._annotations(edges)

        enriched = [
            (relation, other, annotations.get((relation, src, dst), {}), reversed_)
            for (relation, other, reversed_), (relation, src, dst) in zip(found, edges)
        ]
        return sorted(enriched, key=lambda item: (item[0], item[1][0], item[1][1], item[3]))

    def _annotations(
        self, pairs: Iterable[tuple[str, NodeKey, NodeKey]]
    ) -> dict[tuple[str, NodeKey, NodeKey], dict[str, Any]]:
        """Fetch provenance for a batch of edges in one round trip."""
        pairs = list(pairs)
        wanted = {edge_uri(relation, src, dst) for relation, src, dst in pairs}
        if not wanted:
            return {}

        values = " ".join(f"<{uri}>" for uri in sorted(wanted))
        rows = self.client.select(
            f"SELECT ?e ?p ?o WHERE {{ VALUES ?e {{ {values} }} ?e ?p ?o }}"
        )

        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            grouped.setdefault(str(row["e"]), {})[_local_name(str(row["p"]))] = row["o"]

        by_edge: dict[tuple[str, NodeKey, NodeKey], dict[str, Any]] = {}
        for relation, src, dst in pairs:
            props = grouped.get(edge_uri(relation, src, dst), {})
            by_edge[(relation, src, dst)] = {
                name: value
                for name, value in props.items()
                if name not in _RESERVED_EDGE_PROPS and name != "type"
            }
        return by_edge

    # -- native path evaluation -------------------------------------------
    def match(self, pattern: str, start: NodeKey, *, simple_paths: bool = True) -> list[NodeKey]:
        """Evaluate a route expression *in the engine*, using SPARQL property paths.

        This is the point of having written the traversal as a regular expression:
        the same string the in-memory adapter interprets is compiled to a property
        path the database evaluates with its own algorithms.

        **The two evaluators do not agree by default, and the difference is not a
        bug.** SPARQL property paths use arbitrary-path semantics: a node may be
        revisited, so ``REPRESENTA_A^/REPRESENTA_A`` from a supplier returns the
        supplier itself as well as its sibling. Our navigator evaluates *simple*
        paths, because an evidence chain that loops back to where it started
        proves nothing. ``simple_paths=True`` excludes the start node to align the
        two, which is exact for two-hop patterns but not for longer cycles — full
        simple-path semantics is not expressible in property paths, so a strict
        check has to happen after the query. That limit is stated here rather than
        discovered later in a dossier.
        """
        expression = PATTERNS.get(pattern, pattern)
        # the same expression, compiled against this ontology's relation IRIs
        property_path = to_property_path(
            parse(expression), relation=lambda name: f"<{relation_uri(name)}>"
        )
        query = (
            f"SELECT DISTINCT ?end WHERE {{ <{node_uri(*start)}> {property_path} ?end"
        )
        if simple_paths:
            query += f" FILTER(?end != <{node_uri(*start)}>)"
        query += " } ORDER BY ?end"

        rows = self.client.select(query)
        return [_key_from_uri(row["end"]) for row in rows if "end" in row]

    def __len__(self) -> int:
        rows = self.client.select(f"SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE {{ ?s <{RDF_TYPE}> ?t }}")
        return int(rows[0]["n"]) if rows and "n" in rows[0] else 0


def _triple(subject_uri: str, prop: str, value: Any) -> str:
    return f"<{subject_uri}> <{ONTO}{prop}> {_object_term(value)}"


def _object_term(value: Any) -> str:
    if isinstance(value, bool):
        return f'"{str(value).lower()}"^^<{XSD_BOOLEAN}>'
    if isinstance(value, int):
        return f'"{value}"^^<{XSD_INTEGER}>'
    if isinstance(value, float):
        return f'"{value}"^^<{XSD_DECIMAL}>'
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _local_name(uri: str) -> str:
    for separator in ("#", "/"):
        if separator in uri:
            uri = uri.rsplit(separator, 1)[-1]
    return uri


def _props_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for row in rows:
        name = _local_name(str(row.get("p", "")))
        if name == "type":
            props.setdefault("types", []).append(_local_name(str(row.get("o", ""))))
        else:
            props[name] = row.get("o")
    return props


def _key_from_uri(uri: Any, label: str | None = None) -> NodeKey:
    """Turn a node URI back into (label, key)."""
    text = str(uri)
    if not text.startswith(NODE_PREFIX):
        raise ValueError(f"not an expediente node URI: {text}")
    remainder = text[len(NODE_PREFIX) :]
    if label is not None:
        prefix = f"{label}/"
        if remainder.startswith(prefix):
            remainder = remainder[len(prefix) :]
            return (label, urllib.parse.unquote(remainder))
    found_label, _, key = remainder.partition("/")
    return (found_label, urllib.parse.unquote(key))
