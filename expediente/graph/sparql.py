"""Minimal SPARQL 1.1 client over HTTP.

Standard library only, because the whole deterministic core is dependency-free and
a storage client is not a good reason to break that.

Two dialect facts were verified against MillenniumDB v1.0.0 rather than assumed,
and both are visible here:

- ``INSERT DATA`` and ``DELETE DATA`` work on the update endpoint.
- ``DELETE WHERE {...}`` and ``DELETE {...} WHERE {...}`` **do not exist** in this
  version — the parser rejects both. So an update is always a read, then a
  targeted ``DELETE DATA``, then an ``INSERT DATA``. See :meth:`SparqlClient.replace`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable, Sequence

#: Hosts that belong to another system and must never be written to by mistake.
#: The memory graph lives at graph.wagnersolutionsai.com; an adapter pointed there
#: would merge a hackathon dataset into production knowledge.
FORBIDDEN_WRITE_HOSTS = ("wagnersolutionsai.com",)


class SparqlError(RuntimeError):
    """Raised when the endpoint rejects a query. The server's text is preserved."""


class SparqlClient:
    """Talks SPARQL 1.1 query and update to one endpoint pair."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: int = 30,
        user: str | None = None,
        password: str | None = None,
        allow_production_write: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._auth = None
        if user and password:
            self._auth = f"Basic {_basic_token(user, password)}"
        self._allow_production_write = allow_production_write

    # -- query -------------------------------------------------------------
    def select(self, sparql: str, *, timeout_s: int | None = None) -> list[dict[str, Any]]:
        """Run a SELECT and return its bindings as plain dicts of ``{var: value}``."""
        payload = self._post("/sparql", {"query": sparql}, timeout_s)
        return [
            {variable: _binding_value(binding[variable]) for variable in binding}
            for binding in payload.get("results", {}).get("bindings", [])
        ]

    def ask(self, sparql: str) -> bool:
        payload = self._post("/sparql", {"query": sparql})
        return bool(payload.get("boolean"))

    # -- update ------------------------------------------------------------
    def update(self, sparql: str, *, timeout_s: int | None = None) -> None:
        self._guard_write()
        self._post("/update", {"update": sparql}, timeout_s)

    def insert(self, triples: Iterable[str]) -> None:
        """Insert a batch of serialised triples. RDF is a set, so re-inserting is a no-op."""
        body = _join_triples(triples)
        if body:
            self.update(f"INSERT DATA {{\n{body}\n}}")

    def delete(self, triples: Iterable[str]) -> None:
        """Delete specific serialised triples.

        ``DELETE DATA`` needs every triple spelled out — there are no variables in
        this form, and this engine does not accept the ``WHERE`` variants, so the
        caller must know exactly what it wrote. That is why :meth:`replace` reads
        first.
        """
        body = _join_triples(triples)
        if body:
            self.update(f"DELETE DATA {{\n{body}\n}}")

    def replace(self, subject_uri: str, triples: Sequence[str]) -> None:
        """Make the subject's triples exactly ``triples``.

        Read-modify-write, because the engine's SPARQL Update subset has no way to
        express "delete the current values, then insert these" in one statement.
        Two round trips is the honest cost of that gap.
        """
        existing = self.select(
            f"SELECT ?p ?o WHERE {{ <{subject_uri}> ?p ?o }}"
        )
        removable = [
            f"<{subject_uri}> <{row['p']}> {_as_object_term(row['o'], row.get('o_type'))}"
            for row in existing
            if isinstance(row.get("p"), str) and row.get("o") is not None
        ]
        self.delete(removable)
        self.insert(triples)

    # -- transport ---------------------------------------------------------
    def _guard_write(self) -> None:
        if self._allow_production_write:
            return
        host = urllib.parse.urlparse(self.base_url).hostname or ""
        if any(forbidden in host for forbidden in FORBIDDEN_WRITE_HOSTS):
            raise SparqlError(
                f"refusing to write to {host}: this URL belongs to production memory. "
                "Point EXPEDIENTE_MDB_URL at an isolated instance, or pass "
                "allow_production_write=True deliberately."
            )

    def _post(self, path: str, fields: dict[str, str], timeout_s: int | None = None) -> dict:
        data = urllib.parse.urlencode(fields).encode("utf-8")
        request = urllib.request.Request(f"{self.base_url}{path}", data=data, method="POST")
        request.add_header("Content-Type", "application/x-www-form-urlencoded")
        request.add_header("Accept", "application/sparql-results+json")
        if self._auth:
            request.add_header("Authorization", self._auth)
        try:
            with urllib.request.urlopen(request, timeout=timeout_s or self._timeout_s) as response:
                body = response.read().decode("utf-8").strip()
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace").strip()
            raise SparqlError(f"{path}: HTTP {error.code} — {detail}") from error
        except urllib.error.URLError as error:
            raise SparqlError(f"{path}: {error.reason}") from error

        if not body:
            return {}
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {"raw": body}


def _binding_value(binding: dict[str, Any]) -> Any:
    value = binding.get("value")
    if binding.get("type") == "literal":
        datatype = binding.get("datatype", "")
        if datatype.endswith("#integer"):
            try:
                return int(value)
            except (TypeError, ValueError):
                return value
        if datatype.endswith("#float") or datatype.endswith("#double") or datatype.endswith("#decimal"):
            try:
                return float(value)
            except (TypeError, ValueError):
                return value
    return value


def _as_object_term(value: Any, value_type: str | None = None) -> str:
    """Serialise a binding back into a SPARQL term."""
    if value_type == "uri" or (isinstance(value, str) and value.startswith(("http://", "https://", "urn:"))):
        return f"<{value}>"
    if isinstance(value, bool):
        return f'"{str(value).lower()}"^^<http://www.w3.org/2001/XMLSchema#boolean>'
    if isinstance(value, int):
        return f'"{value}"^^<http://www.w3.org/2001/XMLSchema#integer>'
    if isinstance(value, float):
        return f'"{value}"^^<http://www.w3.org/2001/XMLSchema#decimal>'
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _basic_token(user: str, password: str) -> str:
    """Encode Basic-auth credentials without importing the module at top level."""
    module = __import__("base64")
    credentials = f"{user}:{password}".encode("utf-8")
    return module.b64encode(credentials).decode("ascii")


def _join_triples(triples: Iterable[str]) -> str:
    """Serialise triples for INSERT/DELETE DATA.

    Each triple needs its own terminating ``.``: this engine's parser rejects a
    group where only the last one would close, which is legal in some other
    SPARQL implementations. Being explicit costs one character per line and works
    everywhere.
    """
    cleaned = [triple.strip().rstrip(".") for triple in triples if triple and triple.strip()]
    return " .\n".join(f"  {triple}" for triple in cleaned) + " ." if cleaned else ""
