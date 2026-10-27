"""A read-only HTTP surface over the graph, for the demo.

The MCP server is for agents that can spawn a process; a browser cannot. This module
is the same four operations over GET, and it deliberately imports its handlers from
:mod:`expediente.mcp.server` instead of reimplementing them: one behaviour, two
transports. If the tools and the API ever disagree, the demo and the agent would be
looking at different graphs.

Read-only by construction, not by convention. There is no update path here at all —
the store is only asked ``select`` — so exposing this on the internet cannot write to
the graph even if a query is malicious. The SPARQL endpoint itself stays bound to
loopback for exactly that reason: it speaks update, and this does not.

    EXPEDIENTE_MDB_URL=http://expediente-mdb:1234 \
        python3 -m expediente.api.server --port 8080

Endpoints (all GET, all JSON, all CORS-enabled for the demo page):

    /health                          liveness plus the triple count
    /stats                           totals by node label and by relation
    /search?label=&text=&limit=      find a node by name
    /neighbours?node=&direction=&limit=
    /paths?src=&dst=&max_hops=&limit=
    /citation?node=                  claim -> evidence -> document, with pages

Values are parsed and clamped by the same functions the MCP tools use, so an agent
and a browser asking the same question cannot get different answers.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from expediente.graph.schema import NODES
from expediente.mcp.server import (
    MAX_HOPS,
    MAX_LIMIT,
    citation_trail,
    find_paths,
    neighbours,
    search,
)

MAX_PATH_SEGMENTS = 4

#: A demo page on another origin must be able to call this without a proxy.
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Cache-Control": "no-store",
}


class Api:
    """Thin dispatch layer. Keeps the HTTP plumbing away from the graph logic."""

    def __init__(self, store) -> None:
        self.store = store
        self._stats_cache: dict[str, Any] | None = None
        self._triple_count: int | None = None

    # -- endpoints ---------------------------------------------------------
    def health(self, params: dict[str, str]) -> dict[str, Any]:
        # Counting 65 M triples is a scan; a liveness probe must not pay for it on every
        # call. Computed once, then reported from memory.
        if self._triple_count is None:
            rows = self.store.client.select(
                "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }", timeout_s=180
            )
            self._triple_count = int(rows[0]["n"]) if rows else 0
        return {"status": "ok", "triples": self._triple_count}

    def warm(self) -> None:
        """Populate the caches before the first caller arrives.

        Both /health and /stats answer a question about the whole graph: 45 s of engine
        time on a cold cache. Doing it in a background thread at startup means a demo
        never opens with a spinner, and a restart does not look like an outage.
        """
        for name in ("health", "stats"):
            try:
                getattr(self, name)({"refresh": "1"})
            except Exception as exc:  # the API still serves the other endpoints
                print(f"warm {name} failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

    def stats(self, params: dict[str, str]) -> dict[str, Any]:
        """Totals by label and by relation.

        Kept out of the per-request path in spirit: it is a full scan of the type index
        and the relation predicates, so it is the one endpoint worth caching at the
        edge. The numbers it returns are the ones the pitch quotes, so they are worth
        being able to check from outside.
        """
        if self._stats_cache is not None and params.get("refresh") not in ("1", "true"):
            return self._stats_cache

        # Two full aggregations, so the client timeout is raised deliberately: the
        # engine answers in seconds, but the default 30 s is a request timeout, not a
        # query budget. The result is cached because the graph only changes on reload.
        by_label = self.store.client.select(
            "SELECT ?t (COUNT(*) AS ?n) WHERE { ?s "
            "<http://www.w3.org/1999/02/22-rdf-syntax-ns#type> ?t } GROUP BY ?t",
            timeout_s=180,
        )
        by_relation = self.store.client.select(
            "SELECT ?p (COUNT(*) AS ?n) WHERE { ?s ?p ?o . "
            'FILTER(STRSTARTS(STR(?p), "urn:expediente:onto/rel/")) } GROUP BY ?p',
            timeout_s=180,
        )
        self._stats_cache = {
            "nodes": {
                str(row["t"]).rsplit("/", 1)[-1]: int(row["n"])
                for row in sorted(by_label, key=lambda r: -int(r["n"]))
                if str(row["t"]) in {f"urn:expediente:onto/{label}" for label in NODES}
            },
            "relations": {
                str(row["p"]).rsplit("/", 1)[-1]: int(row["n"])
                for row in sorted(by_relation, key=lambda r: -int(r["n"]))
            },
        }
        return self._stats_cache

    def search(self, params: dict[str, str]) -> dict[str, Any]:
        return search(self.store, params)

    def neighbours(self, params: dict[str, str]) -> dict[str, Any]:
        return neighbours(self.store, params)

    def paths(self, params: dict[str, str]) -> dict[str, Any]:
        return find_paths(self.store, params)

    def citation(self, params: dict[str, str]) -> dict[str, Any]:
        return citation_trail(self.store, params)

    ROUTES = {
        "/health": "health",
        "/stats": "stats",
        "/search": "search",
        "/neighbours": "neighbours",
        "/paths": "paths",
        "/citation": "citation",
    }


class Handler(BaseHTTPRequestHandler):
    api: Api
    server_version = "expediente-api/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # keep stderr tidy in docker logs
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in CORS_HEADERS.items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_OPTIONS(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        self._send(204, {})

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}

        if path == "/":
            self._send(
                200,
                {
                    "service": "expediente-graph",
                    "endpoints": sorted(self.api.ROUTES),
                    "hint": "GET /stats to see what is in the graph",
                },
            )
            return

        method = self.api.ROUTES.get(path)
        if method is None:
            self._send(404, {"error": f"unknown path {path}", "endpoints": sorted(self.api.ROUTES)})
            return

        try:
            payload = getattr(self.api, method)(params)
        except ValueError as exc:  # bad input from the caller
            self._send(400, {"error": str(exc)})
            return
        except Exception as exc:  # engine or transport failure: say so, do not pretend
            self._send(502, {"error": f"{type(exc).__name__}: {exc}"})
            return

        self._send(200, payload)


def build_server(store, host: str, port: int, *, warm: bool = True) -> ThreadingHTTPServer:
    api = Api(store)
    handler = type("BoundHandler", (Handler,), {"api": api})
    if warm:
        threading.Thread(target=api.warm, daemon=True).start()
    return ThreadingHTTPServer((host, port), handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only HTTP API over the graph")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)

    from expediente.graph.millenniumdb import MillenniumDBStore

    store = MillenniumDBStore.from_env()
    server = build_server(store, args.host, args.port)
    print(f"expediente-api on http://{args.host}:{args.port}", file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
