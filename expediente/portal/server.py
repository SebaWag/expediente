"""The investigation portal: one process, five surfaces, one graph.

Why this is a separate process from the API
-------------------------------------------

``expediente.api.server`` is read-only **by construction** — the store is only ever
asked ``select`` — and that property is load-bearing for the security argument
(*"exposing this on the internet cannot write to the graph"*). The portal writes:
runs, event logs, dossiers. Mixing the two would quietly falsify a sentence the
README makes.

So: same graph, same tool functions, different process. The portal reads the graph
through the same functions the MCP server and the API use, and its writes go to a
run directory, never to the graph.

What it serves
--------------

    GET  /                          the single-page portal
    GET  /static/*                  its assets
    GET  /api/summary               totals + recent runs
    GET  /api/search                find a supplier or a buyer
    GET  /api/subgraph              a small graph around one node, for the canvas
    GET  /api/signals               the shortlist, as kanban cards
    GET  /api/kanban                signals grouped by what has been done to them
    GET  /api/network               shared-address network for a RUT
    POST /api/investigate           start a run in the background -> run_id
    GET  /api/runs                  recent runs
    GET  /api/runs/{id}             one run: plan, claims, sources, dossier
    GET  /api/runs/{id}/events      the event log, from ?since=
    GET  /api/runs/{id}/stream      the same, as server-sent events
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..agent import Investigator, MetaProvider, ProviderError, Settings
from ..api.server import Api
from ..graph.millenniumdb import MillenniumDBStore
from ..mcp.server import network, search
from ..runs import RunStore, replay

STATIC_DIR = Path(__file__).parent / "static"
MAX_NODES = 70
#: How many purchase orders one expansion may add. Orders are the evidence, not the
#: structure; a canvas full of them answers no question a reader has.
ORDER_CAP = 6
SIGNAL_CACHE_S = 120


# --------------------------------------------------------------------- subgraph
def _display_name(label: str, key: str, props: dict[str, Any]) -> str:
    for field in ("razon_social", "nombre", "direccion", "titulo", "codigo", "texto"):
        value = props.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return key


def subgraph(store, label: str, key: str, *, depth: int = 2, limit_per_node: int = 10, max_nodes: int = MAX_NODES) -> dict[str, Any]:
    """A small, honest view of a large graph.

    Four point nine million orders cannot be drawn and must not be sampled as if the
    sample were the whole. So the canvas shows the neighbourhood the user asked for,
    one or two hops out, and the UI puts the real totals next to it.

    Two implementations, and the reason is measured. The first version walked the
    graph node by node through the store port: correct, and **18.4 s for one hop**,
    because each node costs a round trip and a demo cannot ship a graph you wait for.
    The batched version below issues two queries per hop regardless of how wide the
    hop is — outgoing edges for the whole frontier, then incoming edges — which is
    what the engine is actually good at. The port-based walk stays as the fallback so
    the in-memory adapter and the tests keep working.
    """
    # A detector is a hub: its whole point is the list of every case it produced, so
    # the canvas is allowed to hold all of them. Any other node keeps the tight cap,
    # because a municipality with 2,145 orders is a list, not a drawing.
    if label == "Detector":
        max_nodes = max(max_nodes, 110)
        limit_per_node = max(limit_per_node, 110)
    client = getattr(store, "client", None)
    if client is not None:
        try:
            return _subgraph_batched(store, client, label, key, depth, max_nodes, limit_per_node)
        except Exception:
            pass  # a partially drawn map is better than an error page
    return _subgraph_walk(store, label, key, depth, max_nodes, limit_per_node)


#: Lower is drawn first. Structural relations before volume.
_LABEL_RANK = {
    "Domicilio": 0,
    "Persona": 0,
    "Proveedor": 1,
    "Organismo": 1,
    "Senal": 1,
    "Expediente": 1,
    "Documento": 2,
    "Evidencia": 2,
    "Afirmacion": 2,
    "Licitacion": 2,
    "CompraAgil": 2,
    "Contrato": 2,
    "OrdenCompra": 3,
}


def _label_from_uri(uri: str) -> str:
    """The node label, which is the path segment right after the prefix."""
    rest = _node_id_from_uri(uri)
    return rest.split("/", 1)[0] if "/" in rest else rest


def _subgraph_batched(store, client, label: str, key: str, depth: int, max_nodes: int, limit_per_node: int) -> dict[str, Any]:
    from ..graph.millenniumdb import node_uri
    from ..graph.schema import EDGES, NODE_PREFIX, ONTO, relation_uri

    seed_uri = node_uri(label, key)
    if not client.select(f"SELECT ?p WHERE {{ <{seed_uri}> ?p ?o }} LIMIT 1"):
        return {"nodes": [], "edges": [], "error": f"no existe {label}/{key}", "truncated": False}

    relations = " ".join(f"<{relation_uri(name)}>" for name in EDGES)
    seen: dict[str, str] = {seed_uri: label}  # uri -> label
    edges: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    frontier = [seed_uri]

    for _hop in range(max(1, depth)):
        if not frontier or len(seen) >= max_nodes:
            break
        values = " ".join(f"<{uri}>" for uri in frontier[:24])

        # isIRI on both ends: the engine returns property values as objects for some
        # subjects, and a canvas is where a stray float would become a node.
        outgoing = client.select(
            f"SELECT ?s ?p ?o WHERE {{ VALUES ?s {{ {values} }} VALUES ?p {{ {relations} }} ?s ?p ?o "
            f"FILTER(isIRI(?o)) }} LIMIT {max_nodes * 12}"
        )
        incoming = client.select(
            f"SELECT ?s ?p ?o WHERE {{ VALUES ?o {{ {values} }} VALUES ?p {{ {relations} }} ?s ?p ?o "
            f"FILTER(isIRI(?s)) }} LIMIT {max_nodes * 12}"
        )

        per_source: dict[str, int] = {}
        next_frontier: list[str] = []

        def consider(owner_uri: str, other_uri: str, rel_uri: str, reverse: bool) -> None:
            """Add one edge discovered while expanding ``owner_uri``.

            The cap is keyed by the node being expanded, not by the row's subject. The
            first version keyed it by the subject, which for an incoming edge *is* the
            order itself — so every order got its own counter and a supplier with 2,044
            of them filled the canvas anyway.
            """
            if len(seen) >= max_nodes:
                return
            if not other_uri.startswith(NODE_PREFIX) or not owner_uri.startswith(NODE_PREFIX):
                return
            label = _label_from_uri(other_uri)
            # Structural nodes first. A supplier with 2,044 orders otherwise fills the
            # whole canvas with orders and the one thing worth seeing — the address it
            # shares with another company — never gets drawn. The map is for relations,
            # not for volume; the volume is in the numbers above it.
            cap = ORDER_CAP if label == "OrdenCompra" else limit_per_node
            slot = f"{owner_uri}|{label}"
            count = per_source.get(slot, 0)
            if count >= cap:
                return
            per_source[slot] = count + 1
            for uri in (owner_uri, other_uri):
                if uri not in seen:
                    seen[uri] = _label_from_uri(uri)
                    if uri not in frontier:
                        next_frontier.append(uri)
            source, target = (owner_uri, other_uri) if not reverse else (other_uri, owner_uri)
            rel_name = rel_uri.replace(ONTO, "").replace("rel/", "")
            signature = (source, rel_name, target)
            if signature in seen_edges:
                return
            seen_edges.add(signature)
            edges.append({"source": source, "target": target, "rel": rel_name})

        def rank(row, reverse: bool) -> int:
            other = str(row["s"]) if reverse else str(row["o"])
            return _LABEL_RANK.get(_label_from_uri(other), 5)

        for row in sorted(outgoing, key=lambda r: rank(r, False)):
            consider(str(row["s"]), str(row["o"]), str(row["p"]), False)
        for row in sorted(incoming, key=lambda r: rank(r, True)):
            consider(str(row["o"]), str(row["s"]), str(row["p"]), True)

        frontier = next_frontier[:24]

    # One query for every property of every collected node. Doing this per node was
    # the other half of the 18 seconds.
    values = " ".join(f"<{uri}>" for uri in seen)
    props: dict[str, dict[str, Any]] = {uri: {} for uri in seen}
    if values:
        for row in client.select(f"SELECT ?s ?p ?o WHERE {{ VALUES ?s {{ {values} }} ?s ?p ?o }}"):
            uri = str(row["s"])
            predicate = str(row["p"]).replace(ONTO, "")
            if predicate.startswith("rel/") or predicate == "type":
                continue
            if uri in props and predicate not in props[uri]:
                props[uri][predicate] = row["o"]

    nodes = []
    for uri, node_label in seen.items():
        node_key = _node_id_from_uri(uri).split("/", 1)[-1]
        nodes.append(
            {
                "id": _node_id_from_uri(uri),
                "label": node_label,
                "key": node_key,
                "name": _display_name(node_label, node_key, props.get(uri, {})),
                "props": {k: v for k, v in list(props.get(uri, {}).items())[:8]},
            }
        )
    for edge in edges:
        edge["source"] = _node_id_from_uri(edge["source"])
        edge["target"] = _node_id_from_uri(edge["target"])

    return {
        "nodes": nodes,
        "edges": edges,
        "truncated": len(seen) >= max_nodes,
        "seed": f"{label}/{key}",
    }


def _node_id_from_uri(uri: str) -> str:
    """``urn:expediente:node/Proveedor/76.264.138-0`` -> ``Proveedor/76.264.138-0``.

    The prefix is ``NODE_PREFIX`` from the schema, and it contains a **colon** before
    ``node``, not a slash. Splitting on ``"/node/"`` silently returned the whole URI,
    which the UI then drew as a label reading ``urn:expediente:node`` — a reminder
    that a prefix written twice is a prefix that will be written wrong once.
    """
    from ..graph.schema import NODE_PREFIX

    return uri[len(NODE_PREFIX):] if uri.startswith(NODE_PREFIX) else uri


def _subgraph_walk(store, label: str, key: str, depth: int, max_nodes: int, limit_per_node: int) -> dict[str, Any]:
    """Neighbour-by-neighbour fallback, for the in-memory adapter."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, str]] = set()

    def node_id(node_key: tuple[str, str]) -> str:
        return f"{node_key[0]}/{node_key[1]}"

    def add_node(node_key: tuple[str, str]) -> None:
        identifier = node_id(node_key)
        if identifier in nodes:
            return
        props = store.node(*node_key) or {}
        nodes[identifier] = {
            "id": identifier,
            "label": node_key[0],
            "key": node_key[1],
            "name": _display_name(node_key[0], node_key[1], props),
            "props": {k: v for k, v in list(props.items())[:8]},
        }

    seed = (label, key)
    if store.node(*seed) is None:
        return {"nodes": [], "edges": [], "error": f"no existe {label}/{key}", "truncated": False}

    add_node(seed)
    frontier = [seed]
    for _hop in range(max(1, depth)):
        next_frontier: list[tuple[str, str]] = []
        for current in frontier:
            if len(nodes) >= max_nodes:
                break
            try:
                neighbours = store.adjacent_directed(current, "both", limit=limit_per_node)
            except Exception:
                continue
            for rel, other, _props, reversed_ in neighbours:
                if len(nodes) >= max_nodes:
                    break
                other_id = node_id(other)
                add_node(other)
                source, target = (node_id(current), other_id) if not reversed_ else (other_id, node_id(current))
                signature = (source, rel, target)
                if signature in seen_edges:
                    continue
                seen_edges.add(signature)
                edges.append({"source": source, "target": target, "rel": rel})
                if other not in frontier and other not in next_frontier:
                    next_frontier.append(other)
        frontier = next_frontier[:24]
        if not frontier:
            break

    return {"nodes": list(nodes.values()), "edges": edges, "truncated": len(nodes) >= max_nodes, "seed": f"{label}/{key}"}


# ------------------------------------------------------------------------- runs
class RunNotFound(LookupError):
    """A run id that is not on disk. Distinct from any other file the server touches.

    The first version let ``FileNotFoundError`` from *anywhere* fall into one handler
    that answered ``{"error": "run no encontrado"}``, so a missing alias table in the
    container looked like a missing investigation. An error message that names the
    wrong cause costs an hour of debugging.
    """


class RunRegistry:
    """Active and finished runs, and the only place a run is started.

    One run per question, started in a thread, with a lock around the map. The portal
    is a single-user demo; pretending to be a job queue would be the wrong kind of
    engineering for nine days.
    """

    def __init__(self, store, provider_factory, runs: RunStore, settings: Settings) -> None:
        self.store = store
        self._provider_factory = provider_factory
        self.runs = runs
        self.settings = settings
        self._lock = threading.Lock()
        self._active: dict[str, dict[str, Any]] = {}

    def start(self, question: str, subject: str | None = None) -> str:
        run = self.runs.create(question=question, subject=subject)
        with self._lock:
            self._active[run.id] = {"question": question, "started": time.time(), "state": "corriendo"}

        def work() -> None:
            try:
                provider = self._provider_factory()
                investigator = Investigator(self.store, provider, run=run, settings=self.settings)
                investigator.investigate(question, subject=subject)
            except ProviderError as exc:
                run.events.append("run.failed", {"error": f"modelo no disponible: {exc}"})
            except Exception as exc:  # a run must always close
                run.events.append("run.failed", {"error": f"{type(exc).__name__}: {exc}"})
            finally:
                with self._lock:
                    self._active.pop(run.id, None)

        threading.Thread(target=work, name=f"run-{run.id}", daemon=True).start()
        return run.id

    def active(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return dict(self._active)


# ----------------------------------------------------------------------- kanban
SIGNAL_STATES = ("sin_revisar", "en_analisis", "con_evidencia", "descartada")


def kanban(api: Api, runs: RunStore, active: dict[str, dict[str, Any]], *, limit: int = 100) -> dict[str, Any]:
    """Signals grouped by what has actually been done to them.

    The board is a view over the run log, not a second database. A card moves because
    a run touched it and the recording says so — never because someone dragged it.
    That is the difference between a kanban that reports work and a kanban that
    performs it.

    States, all derived:

    * ``sin_revisar``  — no run has cited it
    * ``en_analisis``  — a run is executing right now
    * ``con_evidencia``— a claim citing it was accepted
    * ``descartada``   — a claim citing it was rejected, or the run closed without one
    """
    payload = api.signals({"limit": str(limit)})
    signals = payload.get("signals", [])

    touched: dict[str, dict[str, Any]] = {}
    for summary in runs.list(limit=60):
        try:
            run = runs.open(summary["id"])
        except FileNotFoundError:
            continue
        state = replay(run.events.events())
        for claim in state["claims_accepted"]:
            for signal_id in claim.get("senales") or []:
                touched[signal_id] = {
                    "state": "con_evidencia",
                    "run": summary["id"],
                    "text": claim.get("texto"),
                    "at": summary.get("created"),
                }
        for rejection in state["claims_rejected"]:
            for signal_id in rejection.get("senales") or []:
                touched.setdefault(
                    signal_id,
                    {
                        "state": "descartada",
                        "run": summary["id"],
                        # The event payload uses "reason"; an earlier version read
                        # "motivo" and every discarded card silently lost its motive.
                        "text": rejection.get("reason") or rejection.get("motivo"),
                        "at": summary.get("created"),
                    },
                )

    for run_id, info in active.items():
        # A run in flight marks its subject; the log will confirm it when it asserts.
        subject = None
        try:
            subject = runs.open(run_id).meta().get("subject")
        except FileNotFoundError:
            pass
        if subject:
            touched.setdefault(subject, {"state": "en_analisis", "run": run_id, "text": info.get("question"), "at": None})

    board: dict[str, list[dict[str, Any]]] = {state: [] for state in SIGNAL_STATES}
    for signal in signals:
        entry = dict(signal)
        mark = touched.get(signal["id"] or "", {})
        entry["state"] = mark.get("state", "sin_revisar")
        entry["run"] = mark.get("run")
        entry["note"] = mark.get("text")
        board[entry["state"]].append(entry)

    return {
        "columns": [
            {"state": state, "cards": board[state], "count": len(board[state])} for state in SIGNAL_STATES
        ],
        "total": len(signals),
        "active_runs": len(active),
    }


# ----------------------------------------------------------------------- server
class Portal:
    """Dispatch. Holds the caches, owns nothing else."""

    def __init__(self, store, api: Api, runs: RunStore, registry: RunRegistry) -> None:
        self.store = store
        self.api = api
        self.runs = runs
        self.registry = registry
        self._signals_cache: tuple[float, dict[str, Any]] | None = None
        self._profile_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._overview_cache: tuple[float, dict[str, Any]] | None = None
        #: The bytes we hashed, mounted read-only. Serving them is the whole point: a
        #: citation you cannot download is a citation you have to believe.
        self.evidence_dir = Path(os.environ.get("EXPEDIENTE_EVIDENCE_DIR", "/data/evidencia"))
        self._fetch_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._last_fetch: dict[tuple[str, str], float] = {}

    def summary(self) -> dict[str, Any]:
        stats = self.api.stats({})
        return {
            "stats": stats,
            "runs": self.runs.list(limit=8),
            "active": self.registry.active(),
        }

    def signals(self, params: dict[str, str]) -> dict[str, Any]:
        now = time.time()
        if self._signals_cache and now - self._signals_cache[0] < SIGNAL_CACHE_S:
            return self._signals_cache[1]
        payload = self.api.signals({"limit": params.get("limit") or "100"})
        self._signals_cache = (now, payload)
        return payload

    def kanban(self, params: dict[str, str]) -> dict[str, Any]:
        return kanban(self.api, self.runs, self.registry.active(), limit=int(params.get("limit") or 100))

    def search(self, params: dict[str, str]) -> dict[str, Any]:
        return search(self.store, {"label": params.get("label") or "Proveedor", "text": params.get("texto") or "", "limit": params.get("limit") or "10"})

    def subgraph(self, params: dict[str, str]) -> dict[str, Any]:
        nodo = params.get("nodo") or ""
        if "/" not in nodo:
            return {"nodes": [], "edges": [], "error": "nodo debe ser 'Label/id'"}
        label, key = nodo.split("/", 1)
        depth = max(1, min(3, int(params.get("saltos") or 2)))
        return subgraph(self.store, label, key, depth=depth)

    def network(self, params: dict[str, str]) -> dict[str, Any]:
        return network(self.store, {"rut": params.get("rut") or ""})

    # -- audit -------------------------------------------------------------
    def signal_pack(self, signal_id: str, *, order_limit: int = 120) -> dict[str, Any]:
        from ..audit import signal_pack

        return signal_pack(self.store, signal_id, order_limit=order_limit)

    def export(self, signal_id: str, fmt: str) -> tuple[str, bytes, str]:
        from ..audit import render

        return render(self.signal_pack(signal_id), fmt)

    def document(self, code: str) -> dict[str, Any]:
        from ..audit import order_document

        return order_document(self.store, code)

    # -- exploration -------------------------------------------------------
    def connections(self, nodo: str) -> dict[str, Any]:
        """Every relation this node has, with how many on each side.

        Two queries, not twenty-three: the predicates are bound from the vocabulary and
        the counts come back grouped, the same shape ``/stats`` uses after its scan was
        measured at 45 s.
        """
        from ..graph.schema import EDGES, NODE_PREFIX, relation_uri

        if "/" not in nodo:
            return {"error": "nodo debe ser 'Label/id'"}
        label, key = nodo.split("/", 1)
        from ..graph.millenniumdb import node_uri

        uri = node_uri(label, key)
        client = self.store.client
        predicates = " ".join(f"<{relation_uri(name)}>" for name in EDGES)

        out_counts = {}
        for row in client.select(
            f"SELECT ?p (COUNT(*) AS ?n) WHERE {{ VALUES ?p {{ {predicates} }} <{uri}> ?p ?o "
            f"FILTER(isIRI(?o)) }} GROUP BY ?p"
        ):
            out_counts[str(row["p"]).replace("urn:expediente:onto/rel/", "")] = int(row["n"])
        in_counts = {}
        for row in client.select(
            f"SELECT ?p (COUNT(*) AS ?n) WHERE {{ VALUES ?p {{ {predicates} }} ?s ?p <{uri}> "
            f"FILTER(isIRI(?s)) }} GROUP BY ?p"
        ):
            in_counts[str(row["p"]).replace("urn:expediente:onto/rel/", "")] = int(row["n"])

        connections = []
        for name, (src_label, dst_label) in EDGES.items():
            if src_label == label and out_counts.get(name):
                connections.append(
                    {"rel": name, "direction": "out", "other": dst_label, "count": out_counts[name]}
                )
            if dst_label == label and in_counts.get(name):
                connections.append(
                    {"rel": name, "direction": "in", "other": src_label, "count": in_counts[name]}
                )
        connections.sort(key=lambda item: -item["count"])
        return {"node": nodo, "connections": connections}

    def connection_page(self, params: dict[str, str]) -> dict[str, Any]:
        """One page of a node's neighbours, with the fields a reader needs.

        Paging happens in the engine (``LIMIT``/``OFFSET`` measure at about a second on a
        municipality with 2,145 orders), and the order can be by amount, because "show me
        the biggest first" is the first thing anyone asks of a spending list.
        """
        from ..graph.millenniumdb import node_uri
        from ..graph.schema import NODE_PREFIX, ONTO, relation_uri

        nodo = params.get("nodo") or ""
        rel = (params.get("rel") or "").strip()
        if "/" not in nodo or not rel:
            return {"error": "faltan 'nodo' (Label/id) o 'rel'"}
        label, key = nodo.split("/", 1)
        uri = node_uri(label, key)
        direction = params.get("direction") or "out"
        page = max(1, int(params.get("page") or 1))
        size = max(1, min(100, int(params.get("size") or 25)))
        order = (params.get("orden") or "").strip()
        offset = (page - 1) * size
        client = self.store.client

        if direction == "in":
            pattern = f"?s <{relation_uri(rel)}> <{uri}>"
            variable = "?s"
        else:
            pattern = f"<{uri}> <{relation_uri(rel)}> ?o"
            variable = "?o"

        total_rows = client.select(f"SELECT (COUNT(*) AS ?n) WHERE {{ {pattern} FILTER(isIRI({variable})) }}")
        total = int(total_rows[0]["n"]) if total_rows else 0

        sortable = {"monto": "monto_clp", "fecha": "fecha", "nombre": "nombre"}
        if order in sortable:
            prop = sortable[order]
            rows = client.select(
                f"SELECT {variable} ?v WHERE {{ {pattern} . {variable} <{ONTO}{prop}> ?v "
                f"FILTER(isIRI({variable})) }} ORDER BY DESC(?v) LIMIT {size} OFFSET {offset}"
            )
        else:
            rows = client.select(
                f"SELECT {variable} WHERE {{ {pattern} FILTER(isIRI({variable})) }} LIMIT {size} OFFSET {offset}"
            )

        uris = [
            str(row[variable[1:]]) for row in rows if str(row[variable[1:]]).startswith(NODE_PREFIX)
        ]
        props: dict[str, dict[str, str]] = {uri_: {} for uri_ in uris}
        if uris:
            values = " ".join(f"<{u}>" for u in uris)
            for row in client.select(
                f"SELECT ?s ?p ?o WHERE {{ VALUES ?s {{ {values} }} ?s ?p ?o . "
                f"VALUES ?p {{ <{ONTO}razon_social> <{ONTO}nombre> <{ONTO}monto_clp> <{ONTO}fecha> "
                f"<{ONTO}descripcion> <{ONTO}estado> <{ONTO}procedencia> <{ONTO}score> "
                f"<{ONTO}severidad> <{ONTO}explicacion> }} }}"
            ):
                subject = str(row["s"])
                predicate = str(row["p"]).replace(ONTO, "")
                if subject in props:
                    props[subject].setdefault(predicate, str(row["o"]))

        items = []
        for uri_ in uris:
            node_label = uri_[len(NODE_PREFIX):].split("/", 1)[0]
            node_key = uri_[len(NODE_PREFIX):].split("/", 1)[-1]
            fields = props.get(uri_, {})
            items.append(
                {
                    "id": f"{node_label}/{node_key}",
                    "label": node_label,
                    "name": fields.get("razon_social")
                    or fields.get("nombre")
                    or fields.get("descripcion")
                    or fields.get("explicacion")
                    or node_key,
                    "monto_clp": fields.get("monto_clp"),
                    "fecha": fields.get("fecha"),
                    "estado": fields.get("estado"),
                    "procedencia": fields.get("procedencia"),
                    "severidad": fields.get("severidad"),
                    "score": fields.get("score"),
                }
            )

        return {
            "node": nodo,
            "rel": rel,
            "direction": direction,
            "page": page,
            "size": size,
            "total": total,
            "pages": (total + size - 1) // size if size else 0,
            "orden": order or "grafo",
            "items": items,
        }

    def profile(self, nodo: str) -> dict[str, Any]:
        """The investigation profile of one entity, cached for five minutes.

        Ten seconds on a cold call is the price of counting 2,145 orders across six
        dimensions; repeating it on every click is not. The cache is keyed by node and
        the TTL is short because the graph changes when evidence is attached.
        """
        import time as _time

        if "/" not in nodo:
            return {"error": "nodo debe ser 'Label/id'"}
        now = _time.time()
        cached = self._profile_cache.get(nodo)
        if cached and now - cached[0] < 300:
            return cached[1]

        from ..audit import entity_profile

        label, key = nodo.split("/", 1)
        profile = entity_profile(self.store, label, key)
        self._profile_cache[nodo] = (now, profile)
        if len(self._profile_cache) > 200:
            oldest = sorted(self._profile_cache.items(), key=lambda item: item[1][0])[:80]
            for old_key, _value in oldest:
                self._profile_cache.pop(old_key, None)
        return profile

    # -- the landing view and the chat -------------------------------------
    def overview(self) -> dict[str, Any]:
        """The graph the page opens with: the whole machinery, at a size a person can see.

        Detectors, every signal they produced, a sample of the orders those signals
        observe, and the documents behind those orders. That is the evidence graph of
        the country's procurement in one screen — and it is a *view*, capped and
        declared, of a graph with 64.8 million triples.
        """
        import time as _time

        now = _time.time()
        if self._overview_cache and now - self._overview_cache[0] < 300:
            return self._overview_cache[1]

        from ..graph.millenniumdb import node_uri
        from ..graph.schema import EDGES, NODE_PREFIX, ONTO, RDF_TYPE, relation_uri

        client = self.store.client
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, str]] = []
        seen_edges: set[tuple[str, str, str]] = set()

        def add(uri: str, name: str, label: str, **extra: Any) -> None:
            identifier = uri[len(NODE_PREFIX):] if uri.startswith(NODE_PREFIX) else uri
            if identifier in nodes:
                return
            nodes[identifier] = {"id": identifier, "label": label, "name": name, "key": identifier.split("/", 1)[-1], **extra}

        def link(source: str, rel: str, target: str) -> None:
            signature = (source, rel, target)
            if signature in seen_edges or source not in nodes or target not in nodes:
                return
            seen_edges.add(signature)
            edges.append({"source": source, "target": target, "rel": rel})

        # 1 · the detectors
        detectors = [
            str(row["s"])
            for row in client.select(f"SELECT ?s WHERE {{ ?s <{RDF_TYPE}> <{ONTO}Detector> }}")
        ]
        for uri in detectors:
            add(uri, uri.split("/")[-1].capitalize(), "Detector")

        # 2 · every signal
        signals = [
            str(row["s"])
            for row in client.select(f"SELECT ?s WHERE {{ ?s <{RDF_TYPE}> <{ONTO}Senal> }}")
            if str(row["s"]).startswith(NODE_PREFIX)
        ]
        # A node is drawn because it exists, not because it is well formed. The first
        # version only materialised a signal inside the property loop below, so the
        # four signals with no `explicacion` — the ones the health check already flags
        # as citing no orders — were silently absent from the map. The map showed 562
        # of 565 and nobody could tell which three were missing. A view that hides its
        # own holes is the failure mode this project exists to argue against.
        for uri in signals:
            add(uri, uri.rsplit("/", 1)[-1], "Senal")
        signal_values = " ".join(f"<{uri}>" for uri in signals) if signals else ""
        if signal_values:
            for row in client.select(
                f"SELECT ?s ?p ?o WHERE {{ VALUES ?s {{ {signal_values} }} ?s ?p ?o . "
                f"VALUES ?p {{ <{ONTO}explicacion> <{ONTO}severidad> <{ONTO}detector> <{ONTO}monto_clp> }} }}"
            ):
                uri = str(row["s"])
                label = "Senal"
                props = nodes.get(uri[len(NODE_PREFIX):], {})
                predicate = str(row["p"]).replace(ONTO, "")
                if predicate == "explicacion":
                    props["name"] = str(row["o"])[:90]
                elif predicate == "severidad":
                    props["severity"] = str(row["o"])
                elif predicate == "detector":
                    props["detector"] = str(row["o"])
                elif predicate == "monto_clp":
                    try:
                        props["monto"] = float(row["o"])
                    except (TypeError, ValueError):
                        pass
                props.setdefault("id", uri[len(NODE_PREFIX):])
                props.setdefault("label", label)
                props.setdefault("key", uri.split("/")[-1])
                nodes[uri[len(NODE_PREFIX):]] = props

            for row in client.select(
                f"SELECT ?d ?s WHERE {{ ?d <{relation_uri('PRODUCE')}> ?s . VALUES ?s {{ {signal_values} }} }}"
            ):
                link(str(row["d"])[len(NODE_PREFIX):], "PRODUCE", str(row["s"])[len(NODE_PREFIX):])

        # 3 · a sample of the orders each signal observes, and their documents
        order_uris: list[str] = []
        for uri in signals[:60]:
            rows = client.select(
                f"SELECT ?o WHERE {{ <{uri}> <{relation_uri('SE_OBSERVA_EN')}> ?o FILTER(isIRI(?o)) }} LIMIT 3"
            )
            for row in rows:
                order = str(row["o"])
                if not order.startswith(NODE_PREFIX):
                    continue
                order_uris.append(order)
                # The order has to exist as a node *before* the edge is recorded: link()
                # refuses an edge with a missing endpoint, so linking first silently
                # dropped all 177 SE_OBSERVA_EN edges and the overview came back as two
                # disconnected clouds. Found by counting the relations in the payload.
                order_id = order[len(NODE_PREFIX):]
                nodes.setdefault(order_id, {"id": order_id, "label": "OrdenCompra",
                                            "key": order_id.split("/", 1)[-1], "name": order_id.split("/", 1)[-1]})
                link(uri[len(NODE_PREFIX):], "SE_OBSERVA_EN", order_id)

        if order_uris:
            unique = list(dict.fromkeys(order_uris))
            order_values = " ".join(f"<{uri}>" for uri in unique)
            for row in client.select(
                f"SELECT ?s ?p ?o WHERE {{ VALUES ?s {{ {order_values} }} ?s ?p ?o . "
                f"VALUES ?p {{ <{ONTO}nombre> <{ONTO}descripcion> <{ONTO}monto_clp> }} }}"
            ):
                uri = str(row["s"])
                identifier = uri[len(NODE_PREFIX):]
                entry = nodes.setdefault(identifier, {"id": identifier, "label": "OrdenCompra",
                                                      "key": identifier.split("/", 1)[-1]})
                predicate = str(row["p"]).replace(ONTO, "")
                if predicate in ("nombre", "descripcion") and not entry.get("name"):
                    entry["name"] = str(row["o"])[:70]
                elif predicate == "monto_clp":
                    try:
                        entry["monto"] = float(row["o"])
                    except (TypeError, ValueError):
                        pass

            for row in client.select(
                f"SELECT ?d ?o WHERE {{ ?d <{relation_uri('ACREDITA')}> ?o . VALUES ?o {{ {order_values} }} "
                f"FILTER(isIRI(?d)) }} LIMIT 200"
            ):
                document, order = str(row["d"]), str(row["o"])
                if not document.startswith(NODE_PREFIX):
                    continue
                add(document, "Documento", "Documento")
                link(document[len(NODE_PREFIX):], "ACREDITA", order[len(NODE_PREFIX):])

        payload = {
            "nodes": list(nodes.values()),
            "edges": edges,
            "seed": None,
            "layout": "radial",
            "truncated": True,
            "note": (
                f"{len(nodes)} nodos y {len(edges)} relaciones dibujadas: la maquinaria completa "
                f"({len(signals)} señales, una muestra de sus órdenes y los documentos que las "
                "acreditan). El grafo real tiene 64,8 millones de tripletas."
            ),
        }
        self._overview_cache = (now, payload)
        return payload

    def chat(self, message: str) -> dict[str, Any]:
        """One message in, one action out.

        The chat is not a chatbot: it is a command bar over a graph. A message either
        names something the graph knows — and then the answer is *a place*, not a
        paragraph — or it is a question, and then it starts an investigation. Deciding
        which of the two it is happens here, once, instead of in the browser.
        """
        from ..mcp.server import search as graph_search

        text = (message or "").strip()
        if len(text) < 2:
            return {"kind": "help", "reply": "Escribe el nombre de un organismo, de una empresa o una pregunta."}

        matches: list[dict[str, Any]] = []
        for label in ("Organismo", "Proveedor"):
            try:
                hits = graph_search(self.store, {"label": label, "text": text, "limit": 4})
            except Exception:
                hits = {}
            for node in hits.get("results") or []:
                matches.append(
                    {
                        "node": node.get("node"),
                        "label": label,
                        "name": node.get("match"),
                        "matched_via": node.get("matched_via"),
                    }
                )

        # A direct signal id is also a destination.
        if text.startswith("Senal/"):
            matches.insert(0, {"node": text, "label": "Senal", "name": text, "matched_via": "id"})

        is_question = ("?" in text) or len(text.split()) > 6
        if matches:
            best = matches[0]
            return {
                "kind": "focus",
                "node": best["node"],
                "label": best["label"],
                "name": best["name"],
                "matches": matches,
                "reply": f"Encontré {len(matches)} coincidencia(s). Te llevo a "
                f"{best['name'] or best['node']}.",
                "can_investigate": is_question,
            }

        if is_question:
            run_id = self.registry.start(text)
            return {
                "kind": "run",
                "run_id": run_id,
                "reply": "No es el nombre de una entidad, así que lo tomo como pregunta: "
                "el agente va a recorrer el grafo y te muestro cada paso.",
            }

        return {
            "kind": "help",
            "reply": "No encontré ninguna entidad con ese nombre. Prueba con el nombre completo "
            "o con el RUT, o escribe una pregunta y la investigo.",
        }

    # -- verificación: que no nos tenga que creer ------------------------
    def evidence_file(self, codigo: str) -> tuple[str, bytes] | None:
        """The exact bytes we downloaded and hashed, served from disk.

        This is the artefact the citation is made of. A reader who does not trust the
        summary can take this file, run sha256sum on it, and compare it with the hash
        printed in the dossier. If they differ, the dossier is wrong and they know it.

        Two identifiers arrive here, because the graph calls two different things by a
        name that a URL cannot tell apart. An ``:OrdenCompra`` is cited by its code and
        its file is ``<code>.json``; the ``:Documento`` that accredits it is keyed by the
        sha256 of its own bytes, and the file on disk is still named after the order.
        Serving only the first left every document node in the map answering "no hay
        archivo guardado" — a citation that cannot be opened is not a citation — so the
        digest is translated here, once, instead of teaching the UI that a citation and
        a document have different names.
        """
        if not re.fullmatch(r"[0-9A-Za-z_.\-]{3,64}", codigo or ""):
            return None
        for candidate in (codigo, self._order_for_document(codigo)):
            if not candidate:
                continue
            path = self.evidence_dir / f"{candidate}.json"
            if path.is_file():
                return ("application/json; charset=utf-8", path.read_bytes())
        return None

    def _order_for_document(self, digest: str) -> str | None:
        """The order a document accredits, so a digest can be served as a file."""
        client = getattr(self.store, "client", None)
        if client is None or not re.fullmatch(r"[0-9a-f]{64}", digest or ""):
            return None
        from ..graph.millenniumdb import node_uri
        from ..graph.schema import relation_uri

        try:
            rows = client.select(
                f"SELECT ?o WHERE {{ <{node_uri('Documento', digest)}> "
                f"<{relation_uri('ACREDITA')}> ?o }} LIMIT 1"
            )
        except Exception:
            # A graph that cannot be reached must not turn a download into a 500: the
            # honest answer is the same 404 the reader already understands.
            return None
        if not rows:
            return None
        uri = str(rows[0]["o"])
        prefix = "urn:expediente:node/"
        return uri[len(prefix) + len("OrdenCompra/"):] if uri.startswith(prefix) and "/" in uri else None

    @staticmethod
    def _content_digest(record: dict[str, Any]) -> str | None:
        import hashlib as _hashlib
        import json as _json

        if not record:
            return None
        canonical = _json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)
        return _hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _mp_ticket(self) -> str:
        import os as _os

        return _os.environ.get("MP_TICKET", "")

    def fetch_live(self, kind: str, codigo: str) -> dict[str, Any]:
        """Ask Mercado Público right now and hash the answer.

        The point is not to show the record — it is to let a reader watch the system be
        checked. If the hash of what the API returns today equals the hash recorded when
        we attached the evidence, the citation is intact. If it does not, the source
        changed and the document says so.
        """
        import hashlib as _hashlib
        import json as _json
        import time as _time
        import urllib.parse as _urlparse
        import urllib.request as _urlrequest

        if not re.fullmatch(r"[0-9A-Za-z_.\-]{3,64}", codigo or ""):
            return {"error": "código inválido"}
        if self._last_fetch.get((kind, codigo)) and _time.time() - self._last_fetch[(kind, codigo)] < 600:
            return self._fetch_cache.get((kind, codigo)) or {"error": "consulta en curso"}

        ticket = self._mp_ticket()
        endpoint = (
            "https://api.mercadopublico.cl/servicios/v1/publico/ordenesdecompra.json"
            if kind == "orden"
            else "https://api.mercadopublico.cl/servicios/v1/publico/licitaciones.json"
        )
        public = (
            f"https://www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx?idOC={codigo}"
            if kind == "orden"
            else f"{endpoint}?codigo={codigo}&ticket=<requiere ticket>"
        )
        if not ticket:
            return {
                "kind": kind,
                "code": codigo,
                "verified": False,
                "note": "el portal no tiene ticket configurado para reconsultar la API",
                "source_url": public,
            }

        url = f"{endpoint}?{_urlparse.urlencode({'codigo': codigo, 'ticket': ticket})}"
        request = _urlrequest.Request(
            url,
            headers={"User-Agent": "Expediente/0.6 (verificación; sebastian@wagnersolutionsai.com)",
                     "Accept": "application/json"},
        )
        try:
            with _urlrequest.urlopen(request, timeout=25) as response:
                raw = response.read()
        except Exception as exc:
            return {"kind": kind, "code": codigo, "verified": False,
                    "error": f"no se pudo consultar la API: {type(exc).__name__}", "source_url": public}

        digest = _hashlib.sha256(raw).hexdigest()
        record: dict[str, Any] = {}
        try:
            payload = _json.loads(raw)
            listing = payload.get("Listado") or []
            record = listing[0] if listing else {}
        except ValueError:
            record = {}

        # Hashing the raw response proves nothing: Mercado Público stamps every reply with
        # its own FechaCreacion, so two identical records produce two different byte
        # streams. The first version compared those and reported "the source changed" on
        # a record that had not changed at all — the worst kind of false alarm, because it
        # teaches the reader to ignore the check.
        #
        # So the comparison is over the *content* of the record: the Listado[0] object,
        # canonicalised with sorted keys. The byte digest is still shown and still stored;
        # it proves what file we downloaded. The content digest proves what the source says.
        content_now = self._content_digest(record)

        stored = None
        stored_content = None
        path = self.evidence_dir / f"{codigo}.json"
        if path.is_file():
            stored_bytes = path.read_bytes()
            stored = _hashlib.sha256(stored_bytes).hexdigest()
            try:
                stored_payload = _json.loads(stored_bytes)
                stored_listing = stored_payload.get("Listado") or []
                stored_content = self._content_digest(stored_listing[0]) if stored_listing else None
            except (ValueError, IndexError):
                stored_content = None

        same_content = bool(stored_content) and stored_content == content_now
        result = {
            "kind": kind,
            "code": codigo,
            "verified": same_content,
            "hash_now": content_now,
            "hash_stored": stored_content,
            "hash_file": stored,
            "retrieved_at": _time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source_url": public,
            "record": {
                key: record.get(key)
                for key in ("Codigo", "CodigoExterno", "Nombre", "Descripcion", "Estado",
                            "Total", "CodigoLicitacion", "FechaCierre", "Comprador", "Proveedor")
                if record.get(key) not in (None, "")
            },
            "meaning": (
                "El registro que la API devuelve ahora es idéntico al que se guardó al adjuntar "
                "la evidencia: mismo contenido, misma huella."
                if same_content else
                "El contenido del registro NO coincide con el guardado: la fuente cambió después "
                "de que se adjuntara la evidencia, o no hay archivo local que comparar."
                if stored_content else
                "No hay archivo local para comparar: se muestra el registro en vivo, sin contraste."
            ),
            "note": (
                "La huella se calcula sobre el contenido del registro, no sobre los bytes de la "
                "respuesta: Mercado Público sella cada respuesta con su propia fecha de creación, "
                "así que los bytes cambian aunque el registro no."
            ),
        }
        self._fetch_cache[(kind, codigo)] = result
        self._last_fetch[(kind, codigo)] = _time.time()
        return result

    def health_graph(self) -> dict[str, Any]:
        from ..audit import graph_health

        return graph_health(self.store)

    def roots(self) -> dict[str, Any]:
        """Starting points, so the map is never an empty canvas.

        A graph explorer whose only entry is a search box is a graph explorer nobody
        opens. These are four doors: the two detectors, the strongest signals, and the
        counts that say how much is behind each one.
        """
        detectors: list[dict[str, Any]] = []
        try:
            client = getattr(self.store, "client", None)
            if client is not None:
                from ..graph.schema import ONTO, RDF_TYPE

                rows = client.select(f"SELECT ?s WHERE {{ ?s <{RDF_TYPE}> <{ONTO}Detector> }}")
                codes = sorted(str(row["s"]).split("/")[-1] for row in rows)
            else:
                codes = sorted(key for _label, key in self.store.nodes("Detector"))
        except Exception:
            codes = []

        hints = {
            "fraccionamiento": "Compras partidas justo bajo el umbral de licitación",
            "concentracion": "Un proveedor que se lleva una parte anómala del gasto",
            "colicotizacion": "Empresas que siempre cotizan juntas",
            "modificaciones": "Contratos que crecen después de adjudicados",
        }
        for code in codes:
            detectors.append({"id": f"Detector/{code}", "label": code, "hint": hints.get(code, "")})

        signals = self.api.signals({"limit": "6"}).get("signals", [])
        return {
            "detectors": detectors,
            "signals": [
                {
                    "id": f"Senal/{signal['id']}",
                    "label": (signal.get("proveedor") or "señal")[:60],
                    "hint": (signal.get("organismo") or "")[:60],
                    "severity": signal.get("severity"),
                }
                for signal in signals
            ],
            "counts": {k: v for k, v in (self.api.stats({}).get("nodes") or {}).items()},
        }

    def run_detail(self, run_id: str) -> dict[str, Any]:
        try:
            run = self.runs.open(run_id)
        except FileNotFoundError as exc:
            raise RunNotFound(run_id) from exc
        events = run.events.events()
        state = replay(events)
        return {
            "id": run.id,
            "meta": run.meta(),
            "state": state,
            "dossier": run.read_artifact("expediente.md"),
            "events": [event.to_json() for event in events],
            "digest": run.events.digest(),
        }

    def run_events(self, run_id: str, since: int) -> dict[str, Any]:
        try:
            run = self.runs.open(run_id)
        except FileNotFoundError as exc:
            raise RunNotFound(run_id) from exc
        return {"events": [event.to_json() for event in run.events.since(since)]}


class Handler(BaseHTTPRequestHandler):
    portal: Portal
    server_version = "ExpedientePortal/0.4"

    # -- plumbing ----------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path) -> None:
        if not path.is_file() or not path.resolve().is_relative_to(STATIC_DIR.resolve()):
            self._json(404, {"error": "no encontrado"})
            return
        body = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        if path.suffix in {".html", ".css"}:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    # -- routing -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        portal = self.portal

        if path == "/":
            return self._file(STATIC_DIR / "index.html")
        if path.startswith("/static/"):
            return self._file(STATIC_DIR / path.split("/static/", 1)[1])

        try:
            if path == "/api/health":
                return self._json(200, {"status": "ok"})
            if path == "/api/summary":
                return self._json(200, portal.summary())
            if path == "/api/roots":
                return self._json(200, portal.roots())
            if path.startswith("/api/evidence/"):
                found = portal.evidence_file(path.split("/api/evidence/", 1)[1])
                if found is None:
                    return self._json(404, {"error": "no hay archivo guardado para ese código"})
                content_type, body = found
                import hashlib as _hashlib

                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("X-Evidence-SHA256", _hashlib.sha256(body).hexdigest())
                self.send_header("Content-Disposition", f'inline; filename="{path.rsplit("/", 1)[-1]}"')
                self.send_header("Content-Length", str(len(body)))
                self._cors()
                self.end_headers()
                return self.wfile.write(body)
            match = re.fullmatch(r"/api/verify/(orden|licitacion)/([0-9A-Za-z_.-]+)", path)
            if match:
                return self._json(200, portal.fetch_live(match.group(1), match.group(2)))
            if path == "/api/overview":
                return self._json(200, portal.overview())
            if path == "/api/profile":
                return self._json(200, portal.profile(params.get("nodo") or ""))
            if path == "/api/connections":
                return self._json(200, portal.connections(params.get("nodo") or ""))
            if path == "/api/connection-page":
                return self._json(200, portal.connection_page(params))
            if path == "/api/health/graph":
                return self._json(200, portal.health_graph())
            match = re.fullmatch(r"/api/signals/([A-Za-z0-9_-]+)\.(pdf|md|csv|json)", path)
            if match:
                content_type, body, filename = portal.export(match.group(1), match.group(2))
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Content-Length", str(len(body)))
                self._cors()
                self.end_headers()
                return self.wfile.write(body)
            match = re.fullmatch(r"/api/signals/([A-Za-z0-9_-]+)", path)
            if match:
                return self._json(200, portal.signal_pack(match.group(1)))
            match = re.fullmatch(r"/api/orders/([A-Za-z0-9_.-]+)/document", path)
            if match:
                return self._json(200, portal.document(match.group(1)))
            if path == "/api/signals":
                return self._json(200, portal.signals(params))
            if path == "/api/kanban":
                return self._json(200, portal.kanban(params))
            if path == "/api/search":
                return self._json(200, portal.search(params))
            if path == "/api/subgraph":
                return self._json(200, portal.subgraph(params))
            if path == "/api/network":
                return self._json(200, portal.network(params))
            if path == "/api/runs":
                return self._json(200, {"runs": portal.runs.list(limit=int(params.get("limit") or 20)), "active": portal.registry.active()})
            if path == "/api/tools":
                from ..agent.tools import SCHEMAS

                return self._json(200, {"tools": SCHEMAS})

            match = re.fullmatch(r"/api/runs/([A-Za-z0-9_-]+)", path)
            if match:
                return self._json(200, portal.run_detail(match.group(1)))
            match = re.fullmatch(r"/api/runs/([A-Za-z0-9_-]+)/events", path)
            if match:
                return self._json(200, portal.run_events(match.group(1), int(params.get("since") or 0)))
            match = re.fullmatch(r"/api/runs/([A-Za-z0-9_-]+)/stream", path)
            if match:
                return self._stream(match.group(1), int(params.get("since") or 0))
        except RunNotFound:
            return self._json(404, {"error": "run no encontrado"})
        except ValueError as exc:
            return self._json(400, {"error": str(exc)})
        except Exception as exc:
            return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})

        return self._json(404, {"error": "ruta desconocida"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except ValueError:
            return self._json(400, {"error": "cuerpo no es JSON"})

        if path == "/api/chat":
            return self._json(200, self.portal.chat(body.get("message") or ""))

        if path == "/api/investigate":
            question = (body.get("question") or "").strip()
            subject = (body.get("subject") or "").strip() or None
            if len(question) < 8:
                return self._json(400, {"error": "la pregunta es demasiado corta"})
            try:
                run_id = self.portal.registry.start(question, subject)
            except ProviderError as exc:
                return self._json(503, {"error": f"modelo no disponible: {exc}"})
            return self._json(202, {"run_id": run_id, "question": question})
        return self._json(404, {"error": "ruta desconocida"})

    def _stream(self, run_id: str, since: int) -> None:
        """Server-sent events. The log panel is the point of this endpoint.

        Polling the file rather than subscribing to a queue means the stream survives
        the browser reconnecting, and works when the run started in another process.
        """
        try:
            run = self.portal.runs.open(run_id)
        except FileNotFoundError:
            return self._json(404, {"error": "run no encontrado"})
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()
        try:
            for event in run.events.tail(since=since, timeout_s=600):
                chunk = f"data: {json.dumps(event.to_json(), ensure_ascii=False)}\n\n"
                self.wfile.write(chunk.encode("utf-8"))
                self.wfile.flush()
                if event.type in {"run.finished", "run.failed"}:
                    break
            self.wfile.write(b"data: {\"type\": \"stream.end\"}\n\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return


def build_server(
    store,
    runs: RunStore,
    settings: Settings,
    host: str,
    port: int,
    provider_factory=None,
    api=None,
) -> ThreadingHTTPServer:
    """``api`` is injectable so the portal can be tested without a graph engine.

    The signals view is written against the MillenniumDB client, not against the store
    port — it needs two queries with a VALUES clause the in-memory adapter has no
    reason to implement. Injecting the view keeps the HTTP and board tests honest
    without pretending the port and the engine are interchangeable.
    """
    if api is None:
        api = Api(store)
    if provider_factory is None:
        def provider_factory():
            return MetaProvider()

    registry = RunRegistry(store, provider_factory, runs, settings)
    portal = Portal(store, api, runs, registry)
    handler = type("BoundHandler", (Handler,), {"portal": portal})
    server = ThreadingHTTPServer((host, port), handler)
    server.daemon_threads = True

    def warm() -> None:
        try:
            api.warm()
            portal.signals({})
        except Exception as exc:
            print(f"warm failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

    threading.Thread(target=warm, daemon=True).start()
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Expediente investigation portal")
    parser.add_argument("--host", default=os.environ.get("EXPEDIENTE_PORTAL_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("EXPEDIENTE_PORTAL_PORT", "8080")))
    parser.add_argument("--runs", default=os.environ.get("EXPEDIENTE_RUNS_DIR", "/opt/expediente/runs"))
    parser.add_argument("--no-warm", action="store_true")
    args = parser.parse_args(argv)

    store = MillenniumDBStore.from_env()
    runs = RunStore(args.runs)
    settings = Settings()
    server = build_server(store, runs, settings, args.host, args.port)
    print(f"portal on http://{args.host}:{args.port} · runs in {args.runs}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
