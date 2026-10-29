"""The append-only event log of one investigation run.

Why this is a module and not a logfile
--------------------------------------

An investigation that a human is expected to sign has to be **reproducible**, and
the first thing that makes it irreproducible is an agent whose steps exist only as
print statements. A model traces a different path on every run; the recording of
that path is therefore the only durable artifact. It is written as it happens, one
JSON object per line, and never rewritten.

Three properties are load-bearing, and each has a test:

* **Append-only.** A run directory is written once. Nothing here opens the events
  file for writing twice, so "the log says X" and "X happened" cannot drift.
* **Monotonic sequence.** ``seq`` starts at 1 and never repeats or goes back, even
  if two threads append at the same time. That is what makes the file replayable
  and what lets a browser resume a stream from ``?since=N`` without duplicates.
* **Content digest.** ``digest()`` is the sha256 of the events, in order. Two
  recordings with the same digest are the same investigation; that is the same
  identity rule the dossier uses, applied one level down.

Reading is not re-deriving: :func:`replay` returns the events exactly as written,
so an auditor can re-read the steps without a model, a network or a database.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

# --------------------------------------------------------------------- event types
#
# The vocabulary is closed on purpose. A free-form event type is a log nobody can
# filter, and the UI groups by type: the agent panel draws the plan, the log panel
# draws everything, the evidence panel counts acceptances and rejections. Adding a
# byte to a type name here is cheaper than a regex in three consumers.

RUN_STARTED = "run.started"
RUN_FINISHED = "run.finished"
RUN_FAILED = "run.failed"

PLAN = "agent.plan"
STEP = "agent.step"
MODEL_REQUEST = "model.request"
MODEL_RESPONSE = "model.response"

TOOL_CALL = "tool.call"
TOOL_RESULT = "tool.result"
TOOL_ERROR = "tool.error"

BUDGET = "budget.exceeded"

SOURCE_ADDED = "source.added"
SOURCE_REJECTED = "source.rejected"

CLAIM_ACCEPTED = "claim.accepted"
CLAIM_REJECTED = "claim.rejected"

DOSSIER_BUILT = "dossier.built"

TYPES = frozenset(
    {
        RUN_STARTED,
        RUN_FINISHED,
        RUN_FAILED,
        PLAN,
        STEP,
        MODEL_REQUEST,
        MODEL_RESPONSE,
        TOOL_CALL,
        TOOL_RESULT,
        TOOL_ERROR,
        BUDGET,
        SOURCE_ADDED,
        SOURCE_REJECTED,
        CLAIM_ACCEPTED,
        CLAIM_REJECTED,
        DOSSIER_BUILT,
    }
)


@dataclass(frozen=True)
class Event:
    """One recorded step. Immutable: an event that can be edited is not a record."""

    run_id: str
    seq: int
    ts: float
    type: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "seq": self.seq, "ts": self.ts, "type": self.type, "payload": self.payload}

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Event":
        return cls(
            run_id=str(raw["run_id"]),
            seq=int(raw["seq"]),
            ts=float(raw["ts"]),
            type=str(raw["type"]),
            payload=dict(raw.get("payload") or {}),
        )


class EventLog:
    """Append-only writer and reader for ``events.jsonl`` inside a run directory.

    Thread-safe by a mutex around the sequence counter and the write. The mutex is
    not defensive programming: the portal answers an SSE stream and a model loop on
    two different threads, and an interleaved write would produce a file whose line
    order does not match its ``seq`` order.
    """

    def __init__(self, path: str | Path, run_id: str) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self._lock = threading.Lock()
        self._seq = self._last_seq()
        self._cache: dict[int, Event] = {}

    # -- writing -----------------------------------------------------------
    def _last_seq(self) -> int:
        if not self.path.exists():
            return 0
        last = 0
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    last = max(last, int(json.loads(line)["seq"]))
                except (ValueError, KeyError, TypeError):
                    # A truncated final line is what a killed process leaves behind.
                    # It must not make the whole run unreadable.
                    continue
        return last

    def append(self, event_type: str, payload: dict[str, Any] | None = None) -> Event:
        if not self.path.parent.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._seq += 1
            event = Event(
                run_id=self.run_id,
                seq=self._seq,
                ts=time.time(),
                type=event_type,
                payload=payload or {},
            )
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event.to_json(), ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            self._cache[event.seq] = event
            return event

    # -- reading -----------------------------------------------------------
    def events(self) -> list[Event]:
        if not self.path.exists():
            return []
        out: list[Event] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(Event.from_json(json.loads(line)))
                except (ValueError, KeyError, TypeError):
                    continue
        return out

    def since(self, seq: int) -> list[Event]:
        return [event for event in self.events() if event.seq > seq]

    def tail(self, since: int = 0, timeout_s: float = 30.0) -> Iterator[Event]:
        """Yield events as they are appended. Used by the SSE endpoint.

        Polling the file rather than a queue is deliberate: the stream must keep
        working when the browser reconnects to a *different* process, and a file
        survives a restart while a queue does not.
        """
        cursor = since
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            fresh = self.since(cursor)
            if fresh:
                for event in fresh:
                    cursor = event.seq
                    yield event
                deadline = time.monotonic() + timeout_s
                continue
            time.sleep(0.15)

    def digest(self) -> str:
        """Identity of the investigation, not of the recording.

        The timestamp is deliberately excluded. Two runs that took the same steps
        with the same arguments reached the same conclusion, whether they happened
        on Tuesday or on Thursday; that is the same rule the dossier uses for its
        own id, one level down. Hashing ``ts`` would have made every digest unique
        and therefore useless as an identity — which is exactly the mistake the
        first version of this method made, caught by its own test.
        """
        sha = hashlib.sha256()
        for event in self.events():
            content = {"seq": event.seq, "type": event.type, "payload": event.payload}
            sha.update(json.dumps(content, ensure_ascii=False, sort_keys=True).encode("utf-8"))
            sha.update(b"\n")
        return sha.hexdigest()


def replay(events: Iterable[Event]) -> dict[str, Any]:
    """Rebuild what the run concluded, from the recording alone.

    This is the point of the whole module: given the events, a reader who was not
    present can reconstruct the plan, the tools that were called, the sources that
    were accepted, the claims that were dropped, and the final dossier id — with no
    model and no database.
    """
    plan: list[str] = []
    tools: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    dossier: dict[str, Any] | None = None
    started = finished = failed = None

    for event in events:
        if event.type == PLAN:
            plan = list(event.payload.get("steps") or [])
        elif event.type == TOOL_CALL:
            tools.append({"seq": event.seq, "tool": event.payload.get("tool"), "arguments": event.payload.get("arguments")})
        elif event.type == TOOL_RESULT:
            if tools:
                tools[-1]["result_summary"] = event.payload.get("summary")
                tools[-1]["duration_ms"] = event.payload.get("duration_ms")
        elif event.type == TOOL_ERROR:
            if tools:
                tools[-1]["error"] = event.payload.get("error")
        elif event.type == SOURCE_ADDED:
            sources.append(event.payload)
        elif event.type == CLAIM_ACCEPTED:
            accepted.append(event.payload)
        elif event.type == CLAIM_REJECTED:
            rejected.append(event.payload)
        elif event.type == DOSSIER_BUILT:
            dossier = event.payload
        elif event.type == RUN_STARTED:
            started = event.payload
        elif event.type == RUN_FINISHED:
            finished = event.payload
        elif event.type == RUN_FAILED:
            failed = event.payload

    return {
        "started": started,
        "finished": finished,
        "failed": failed,
        "plan": plan,
        "tools": tools,
        "sources": sources,
        "claims_accepted": accepted,
        "claims_rejected": rejected,
        "dossier": dossier,
        "steps": len(tools),
    }


# --------------------------------------------------------------------- run store
RUN_ID_ALPHABET = "0123456789abcdefghjkmnpqrstvwxyz"  # Crockford base32: no i, l, o, u


def new_run_id(now: float | None = None) -> str:
    """Sortable, human-readable, collision-resistant: ``20261029-101633-k7f2``.

    Time first because the portal lists runs newest-first and a directory listing
    should already be in the right order; four random characters because two runs
    started in the same second must not collide.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now if now is not None else time.time()))
    suffix = "".join(secrets.choice(RUN_ID_ALPHABET) for _ in range(4))
    return f"{stamp}-{suffix}"


class Run:
    """One investigation, as a directory: ``meta.json`` + ``events.jsonl`` + artifacts."""

    def __init__(self, root: str | Path, run_id: str) -> None:
        self.id = run_id
        self.dir = Path(root) / run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events = EventLog(self.dir / "events.jsonl", run_id)

    # -- meta --------------------------------------------------------------
    @property
    def meta_path(self) -> Path:
        return self.dir / "meta.json"

    def set_meta(self, **values: Any) -> dict[str, Any]:
        meta = self.meta()
        meta.update(values)
        meta["id"] = self.id
        self.meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta

    def meta(self) -> dict[str, Any]:
        if not self.meta_path.exists():
            return {"id": self.id}
        try:
            return json.loads(self.meta_path.read_text(encoding="utf-8"))
        except ValueError:
            return {"id": self.id}

    # -- artifacts ---------------------------------------------------------
    def write_artifact(self, name: str, content: str) -> Path:
        path = self.dir / name
        path.write_text(content, encoding="utf-8")
        return path

    def read_artifact(self, name: str) -> str | None:
        path = self.dir / name
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def summary(self) -> dict[str, Any]:
        """What the run list needs, without reading every event twice."""
        meta = self.meta()
        events = self.events.events()
        state = replay(events)
        return {
            "id": self.id,
            "question": meta.get("question"),
            "subject": meta.get("subject"),
            "created": meta.get("created"),
            # ``is not None``, not truthiness: a terminal event with an empty payload
            # (``run.finished`` often carries nothing) is still terminal.
            "status": (
                "failed"
                if state["failed"] is not None
                else ("finished" if state["finished"] is not None else "running")
            ),
            "steps": state["steps"],
            "sources": len(state["sources"]),
            "claims_accepted": len(state["claims_accepted"]),
            "claims_rejected": len(state["claims_rejected"]),
            "dossier_id": (state["dossier"] or {}).get("id"),
            "digest": self.events.digest(),
        }


class RunStore:
    """Directory of runs. The only place that enumerates them."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def create(self, *, question: str, subject: str | None = None, kind: str = "investigation") -> Run:
        run = Run(self.root, new_run_id())
        run.set_meta(
            question=question,
            subject=subject,
            kind=kind,
            created=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        )
        run.events.append(RUN_STARTED, {"question": question, "subject": subject, "kind": kind})
        return run

    def open(self, run_id: str) -> Run:
        if not _safe_id(run_id):
            raise ValueError(f"invalid run id: {run_id!r}")
        if not (self.root / run_id).is_dir():
            raise FileNotFoundError(run_id)
        return Run(self.root, run_id)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for child in sorted(self.root.iterdir(), reverse=True):
            if not child.is_dir() or child.name.startswith("."):
                continue
            try:
                out.append(Run(self.root, child.name).summary())
            except Exception:  # a half-written run must not break the list
                continue
            if len(out) >= limit:
                break
        return out


def _safe_id(run_id: str) -> bool:
    """A run id becomes a path segment. Anything that is not one is refused."""
    if not run_id or len(run_id) > 64:
        return False
    if run_id in {".", ".."}:
        return False
    return all(character.isalnum() or character in "-_" for character in run_id)
