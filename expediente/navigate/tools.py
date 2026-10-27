"""The tool surface the navigation agent is allowed to call.

Three read-only tools, no mutations, no free-form query language. The agent's job
is to pick *which* of these to call and with what arguments — and because the
result is a set of paths rather than prose, its choice is auditable on screen and
replayable in a test.

This is the boundary the whole design turns on: the model navigates, the graph
answers, the code decides what the answer means.
"""

from __future__ import annotations

from typing import Any

from ..graph.store import GraphStore
from .paths import citation_trails, find_paths, walk


class ToolError(ValueError):
    """Raised when a tool is called with something it cannot honour."""


TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "neighbours",
        "description": (
            "List every edge touching a node. Use it to find out what a node is connected to "
            "before deciding which way to walk."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Node label, e.g. Proveedor"},
                "key": {"type": "string", "description": "Node key, e.g. 11111111-1"},
                "direction": {"type": "string", "enum": ["out", "in", "both"]},
            },
            "required": ["label", "key"],
        },
    },
    {
        "name": "find_paths",
        "description": (
            "Walk from a node until reaching a node of a given label. Use it for questions of "
            "the form 'how is X connected to Y' — the returned path is the evidence."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "label": {"type": "string"},
                "key": {"type": "string"},
                "target_label": {"type": "string"},
                "max_depth": {"type": "integer", "minimum": 1, "maximum": 4},
                "relations": {"type": "array", "items": {"type": "string"}},
                "direction": {"type": "string", "enum": ["out", "in", "both"]},
            },
            "required": ["label", "key", "target_label"],
        },
    },
    {
        "name": "citation_trail",
        "description": (
            "Return the evidence chain of an assertion: which evidence supports it and which "
            "document and page that evidence sits on."
        ),
        "parameters": {
            "type": "object",
            "properties": {"label": {"type": "string"}, "key": {"type": "string"}},
            "required": ["label", "key"],
        },
    },
)


def execute(store: GraphStore, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Run one tool call. Returns plain data so it can go straight into a message."""
    handlers = {
        "neighbours": _neighbours,
        "find_paths": _find_paths,
        "citation_trail": _citation_trail,
    }
    handler = handlers.get(name)
    if handler is None:
        raise ToolError(f"unknown tool {name!r}; available: {sorted(handlers)}")
    return handler(store, arguments)


def _node(arguments: dict[str, Any]) -> tuple[str, str]:
    label = arguments.get("label")
    key = arguments.get("key")
    if not label or not key:
        raise ToolError("both 'label' and 'key' are required")
    return str(label), str(key)


def _neighbours(store: GraphStore, arguments: dict[str, Any]) -> dict[str, Any]:
    node = _node(arguments)
    direction = arguments.get("direction", "out")
    edges = store.adjacent(node, direction)
    return {
        "node": {"label": node[0], "key": node[1]},
        "direction": direction,
        "count": len(edges),
        "edges": [
            {
                "relation": relation,
                "target": {"label": target[0], "key": target[1]},
                "props": dict(props),
            }
            for relation, target, props in edges
        ],
    }


def _find_paths(store: GraphStore, arguments: dict[str, Any]) -> dict[str, Any]:
    node = _node(arguments)
    target_label = arguments.get("target_label")
    if not target_label:
        raise ToolError("'target_label' is required")
    depth = int(arguments.get("max_depth", 3))
    if not 1 <= depth <= 4:
        raise ToolError("max_depth must be between 1 and 4")

    paths = find_paths(
        store,
        node,
        target_label=str(target_label),
        max_depth=depth,
        relations=arguments.get("relations"),
        direction=arguments.get("direction", "out"),
    )
    return {
        "start": {"label": node[0], "key": node[1]},
        "target_label": target_label,
        "count": len(paths),
        "paths": [path.to_dict() for path in paths],
    }


def _citation_trail(store: GraphStore, arguments: dict[str, Any]) -> dict[str, Any]:
    node = _node(arguments)
    trails = citation_trails(store, node)
    return {
        "assertion": {"label": node[0], "key": node[1]},
        "count": len(trails),
        "trails": [trail.to_dict() for trail in trails],
        "traceable": bool(trails),
    }
