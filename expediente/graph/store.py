"""Graph storage.

``GraphStore`` is the port and ``InMemoryGraphStore`` is the reference adapter.
Detectors and tests run against the port, which is what lets the graph backend
change without touching a single rule.

Why an in-memory adapter exists at all: the MillenniumDB dialect is not pinned
down yet, and blocking the detectors on a storage decision would have been the
wrong order of work. The rule is the product; the store is a choice.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Protocol

NodeKey = tuple[str, str]  # (label, key)
Triple = tuple[NodeKey, NodeKey, dict[str, Any]]


class GraphStore(Protocol):
    """Minimal graph port. Deliberately small: anything bigger becomes a query language."""

    def upsert_node(self, label: str, key: str, **props: Any) -> NodeKey: ...

    def upsert_edge(self, src: NodeKey, rel: str, dst: NodeKey, **props: Any) -> None: ...

    def node(self, label: str, key: str) -> dict[str, Any] | None: ...

    def nodes(self, label: str) -> list[NodeKey]: ...

    def edges(self, rel: str, src: NodeKey | None = None) -> list[Triple]: ...


class InMemoryGraphStore:
    """Reference implementation: enough to run detectors and to prove the schema.

    Not a database. Idempotent by design (``upsert``), which is the property the
    real store must also have so that re-ingesting a document changes nothing.
    """

    def __init__(self) -> None:
        self._nodes: dict[NodeKey, dict[str, Any]] = {}
        self._out: dict[str, list[Triple]] = defaultdict(list)
        self._in: dict[str, list[Triple]] = defaultdict(list)

    def upsert_node(self, label: str, key: str, **props: Any) -> NodeKey:
        node_key = (label, key)
        existing = self._nodes.setdefault(node_key, {})
        existing.update({k: v for k, v in props.items() if v is not None})
        return node_key

    def upsert_edge(self, src: NodeKey, rel: str, dst: NodeKey, **props: Any) -> None:
        for node_key in (src, dst):
            if node_key not in self._nodes:
                raise KeyError(f"edge {rel} references unknown node {node_key}")
        existing = next(
            (t for t in self._out[rel] if t[0] == src and t[1] == dst),
            None,
        )
        if existing is not None:
            existing[2].update(props)
            return
        triple: Triple = (src, dst, dict(props))
        self._out[rel].append(triple)
        self._in[rel].append(triple)

    def node(self, label: str, key: str) -> dict[str, Any] | None:
        return self._nodes.get((label, key))

    def nodes(self, label: str) -> list[NodeKey]:
        return sorted(k for k in self._nodes if k[0] == label)

    def edges(self, rel: str, src: NodeKey | None = None) -> list[Triple]:
        triples = self._out.get(rel, [])
        return [t for t in triples if src is None or t[0] == src]

    def incoming(self, rel: str, dst: NodeKey) -> list[Triple]:
        """Reverse traversal — required by evidence tracing."""
        return [t for t in self._in.get(rel, []) if t[1] == dst]

    def __len__(self) -> int:
        return len(self._nodes)
