"""Path traversal over the property graph.

Deliberately small: depth-bounded, cycle-free, deterministically ordered. A
navigation agent is only trustworthy if the route it took can be reproduced, and
reproducibility dies the moment traversal order depends on hash seeding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..graph.store import GraphStore, NodeKey

EdgeProps = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Hop:
    """One step: this relation, from here, to there, carrying these properties."""

    source: NodeKey
    relation: str
    target: NodeKey
    props: EdgeProps
    reversed: bool = False

    def describe(self) -> str:
        arrow = "<-" if self.reversed else "->"
        relation = f"-[:{self.relation}]{arrow}"
        return f"(:{self.source[0]} {self.source[1]}) {relation} (:{self.target[0]} {self.target[1]})"

    def to_dict(self) -> dict:
        return {
            "source": {"label": self.source[0], "key": self.source[1]},
            "relation": self.relation,
            "target": {"label": self.target[0], "key": self.target[1]},
            "reversed": self.reversed,
            "props": dict(self.props),
        }


@dataclass(frozen=True, slots=True)
class Path:
    """A route through the graph. The route **is** the answer, not a summary of it."""

    nodes: tuple[NodeKey, ...]
    hops: tuple[Hop, ...]

    @property
    def length(self) -> int:
        return len(self.hops)

    @property
    def end(self) -> NodeKey:
        return self.nodes[-1]

    def describe(self) -> str:
        return " -> ".join(hop.describe() for hop in self.hops)

    def to_dict(self) -> dict:
        return {
            "length": self.length,
            "start": {"label": self.nodes[0][0], "key": self.nodes[0][1]},
            "end": {"label": self.end[0], "key": self.end[1]},
            "path": [f"(:{label} {key})" for label, key in self.nodes],
            "hops": [hop.to_dict() for hop in self.hops],
        }


def walk(
    store: GraphStore,
    start: NodeKey,
    *,
    max_depth: int = 3,
    relations: Sequence[str] | None = None,
    direction: str = "out",
    limit: int | None = None,
) -> list[Path]:
    """Return every cycle-free route from ``start`` up to ``max_depth`` hops.

    ``direction`` is the reason this exists: the interesting relations in this
    domain are *inbound*. A supplier does not point at the person who represents
    it — the person points at the supplier. Finding the second company means
    walking in, then out.
    """
    if direction not in ("out", "in", "both"):
        raise ValueError(f"direction must be out, in or both — got {direction!r}")
    if max_depth < 1:
        return []

    allowed = {relation.upper() for relation in relations} if relations else None
    frontier: list[Path] = [Path(nodes=(start,), hops=())]
    found: list[Path] = []

    for _ in range(max_depth):
        next_frontier: list[Path] = []
        for path in frontier:
            for relation, target, props, reversed_ in store.adjacent_directed(
                path.nodes[-1], direction
            ):
                if allowed and relation not in allowed:
                    continue
                if target in path.nodes:
                    continue
                extended = Path(
                    nodes=path.nodes + (target,),
                    hops=path.hops
                    + (Hop(path.nodes[-1], relation, target, props, reversed=reversed_),),
                )
                next_frontier.append(extended)
                found.append(extended)
                if limit is not None and len(found) >= limit:
                    return found
        if not next_frontier:
            break
        frontier = next_frontier

    return found


def find_paths(
    store: GraphStore,
    start: NodeKey,
    *,
    target_label: str,
    max_depth: int = 3,
    relations: Sequence[str] | None = None,
    direction: str = "out",
    limit: int | None = None,
) -> list[Path]:
    """Routes that *end* on a node of ``target_label`` — the question, as a walk.

    "How is this supplier connected to that buyer" is exactly this call with
    ``target_label="Organismo"``.
    """
    return [
        path
        for path in walk(
            store,
            start,
            max_depth=max_depth,
            relations=relations,
            direction=direction,
            limit=limit,
        )
        if path.end[0] == target_label
    ]


def citation_trails(store: GraphStore, assertion: NodeKey) -> list[Path]:
    """The evidence chain of an assertion: assertion -> evidence -> document.

    This is the graph-side twin of the evidence gate. The gate decides whether a
    claim may leave; this shows the reviewer the route it took to get out.
    """
    return find_paths(
        store,
        assertion,
        target_label="Documento",
        max_depth=2,
        relations=("SOPORTADA_POR", "EN"),
        direction="out",
    )
