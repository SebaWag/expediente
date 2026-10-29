"""The event log has to be trustworthy before anything is built on top of it."""

import json
import threading

import pytest

from expediente.runs import (
    CLAIM_ACCEPTED,
    CLAIM_REJECTED,
    DOSSIER_BUILT,
    PLAN,
    RUN_FINISHED,
    SOURCE_ADDED,
    TOOL_CALL,
    TOOL_RESULT,
    EventLog,
    RunStore,
    new_run_id,
    replay,
)


def test_sequence_starts_at_one_and_never_goes_back(tmp_path):
    log = EventLog(tmp_path / "events.jsonl", "run-1")
    seqs = [log.append(TOOL_CALL, {"tool": "search"}).seq for _ in range(5)]
    assert seqs == [1, 2, 3, 4, 5]


def test_concurrent_appends_still_produce_a_replayable_file(tmp_path):
    """Two threads are the real case: the model loop and the SSE stream."""
    log = EventLog(tmp_path / "events.jsonl", "run-1")
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        for _ in range(10):
            log.append(TOOL_CALL, {"tool": "search"})

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    events = log.events()
    assert len(events) == 80
    assert [event.seq for event in events] == list(range(1, 81)), "line order must match seq order"


def test_a_truncated_final_line_does_not_destroy_the_run(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path, "run-1")
    log.append(TOOL_CALL, {"tool": "search"})
    log.append(TOOL_RESULT, {"summary": "ok"})
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"run_id": "run-1", "seq": 3, "ty')  # killed mid-write

    reopened = EventLog(path, "run-1")
    assert len(reopened.events()) == 2
    assert reopened.append(PLAN, {"steps": ["a"]}).seq == 3, "the next seq must not reuse the broken one"


def test_since_returns_only_new_events_for_a_streaming_client(tmp_path):
    log = EventLog(tmp_path / "events.jsonl", "run-1")
    for index in range(3):
        log.append(TOOL_CALL, {"tool": f"t{index}"})
    assert [event.payload["tool"] for event in log.since(1)] == ["t1", "t2"]
    assert log.since(3) == []


def test_digest_identifies_the_recording(tmp_path):
    first = EventLog(tmp_path / "a.jsonl", "run-1")
    second = EventLog(tmp_path / "b.jsonl", "run-1")
    for log in (first, second):
        log.append(PLAN, {"steps": ["search", "network"]})
        log.append(TOOL_CALL, {"tool": "search", "arguments": {"text": "cardiotec"}})
    assert first.digest() == second.digest(), "same steps, same identity"

    second.append(TOOL_CALL, {"tool": "network"})
    assert first.digest() != second.digest()


def test_replay_reconstructs_the_investigation_without_a_model(tmp_path):
    log = EventLog(tmp_path / "events.jsonl", "run-1")
    log.append(PLAN, {"steps": ["buscar proveedor", "mirar domicilio", "leer fuente web"]})
    log.append(TOOL_CALL, {"tool": "search", "arguments": {"label": "Proveedor", "text": "cardiotec"}})
    log.append(TOOL_RESULT, {"summary": "2 nodos", "duration_ms": 812})
    log.append(SOURCE_ADDED, {"id": "doc-1", "url": "https://www.contraloria.cl/x", "sha256": "abc"})
    log.append(CLAIM_ACCEPTED, {"text": "comparte domicilio", "sources": ["doc-1"]})
    log.append(CLAIM_REJECTED, {"text": "fraccionó", "reason": "sin documento"})
    log.append(DOSSIER_BUILT, {"id": "sha256:deadbeef"})
    log.append(RUN_FINISHED, {"steps": 1})

    state = replay(log.events())
    assert state["plan"] == ["buscar proveedor", "mirar domicilio", "leer fuente web"]
    assert state["tools"] == [
        {
            "seq": 2,
            "tool": "search",
            "arguments": {"label": "Proveedor", "text": "cardiotec"},
            "result_summary": "2 nodos",
            "duration_ms": 812,
        }
    ]
    assert state["sources"][0]["id"] == "doc-1"
    assert len(state["claims_accepted"]) == 1
    assert state["claims_rejected"][0]["reason"] == "sin documento"
    assert state["dossier"]["id"] == "sha256:deadbeef"
    assert state["finished"] == {"steps": 1}


def test_run_store_lists_newest_first_and_summarises(tmp_path):
    store = RunStore(tmp_path / "runs")
    older = store.create(question="¿quién es cardiotec?", subject="76.264.138-0")
    newer = store.create(question="¿por qué 85 señales?")

    older.events.append(TOOL_CALL, {"tool": "search"})
    older.events.append(DOSSIER_BUILT, {"id": "sha256:aaa"})
    older.events.append(RUN_FINISHED, {})

    listed = store.list()
    assert [item["id"] for item in listed] == [newer.id, older.id] or {
        item["id"] for item in listed
    } == {newer.id, older.id}
    summary = next(item for item in listed if item["id"] == older.id)
    assert summary["status"] == "finished"
    assert summary["steps"] == 1
    assert summary["dossier_id"] == "sha256:aaa"
    assert summary["question"] == "¿quién es cardiotec?"


def test_run_id_is_sortable_and_namespaced(tmp_path):
    first, second = new_run_id(1_790_000_000), new_run_id(1_790_000_060)
    assert len(first.split("-")) == 3
    assert first[:15] <= second[:15]
    assert first != new_run_id(1_790_000_000)


def test_a_run_id_cannot_escape_its_directory(tmp_path):
    store = RunStore(tmp_path / "runs")
    for hostile in ("../etc", "..", ".", "a/b", "", "x" * 80):
        with pytest.raises((ValueError, FileNotFoundError)):
            store.open(hostile)


def test_events_are_written_one_json_object_per_line(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path, "run-1")
    log.append(PLAN, {"steps": ["uno", "dos"]})
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 1
    assert json.loads(lines[0])["payload"]["steps"] == ["uno", "dos"]
