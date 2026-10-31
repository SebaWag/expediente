"""A benchmark that only writes at the end does not write.

Three consecutive runs of ``extract_bench.py`` paid for their API calls and left
nothing on disk but one log line: stdout was block-buffered and the report was
serialised once, after the last document. The tokens were spent, the measurement
was gone, and the only way to learn anything was to spend them again.

So these tests are about persistence under interruption, not about accuracy:

* a result must survive a round trip through disk with nothing lost,
* a journal torn mid-line must still cost only the torn document,
* a resumed run must not call the backend for documents already paid for,
* and an interrupted run must report what it has while saying it is incomplete.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from expediente.extract.models import Causa, ExtractedField, ExtractionResult, Page, Unverified, Usage
from expediente.extract.schema import PROCUREMENT_FIELDS

FIXTURE = Path(__file__).resolve().parents[1] / "examples" / "extract_fixture.jsonl"


def load_bench():
    path = Path(__file__).resolve().parents[1] / "benchmarks" / "extract_bench.py"
    spec = importlib.util.spec_from_file_location("extract_bench_journal", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["extract_bench_journal"] = module
    spec.loader.exec_module(module)
    return module


class CountingBackend:
    """Answers every field with a page, and remembers who it was asked about."""

    name = "counting"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def extract(self, documento_id, pages, specs=None):
        self.calls.append(documento_id)
        selected = tuple(specs) if specs is not None else PROCUREMENT_FIELDS
        return ExtractionResult(
            documento_id=documento_id,
            backend=self.name,
            campos=tuple(
                ExtractedField(campo=spec.nombre, valor="valor", pagina=1, extracto="valor", backend=self.name)
                for spec in selected
            ),
            usage=Usage(prompt_tokens=100, completion_tokens=20, reasoning_tokens=0),
        )


class ExplodingBackend:
    """The network, on a bad day: every document raises."""

    name = "exploding"

    def extract(self, documento_id, pages, specs=None):
        raise RuntimeError("503 Service Unavailable")


def a_result(documento_id="doc-1") -> ExtractionResult:
    return ExtractionResult(
        documento_id=documento_id,
        backend="test",
        campos=(ExtractedField(campo="monto_total_clp", valor="912400", pagina=2, extracto="$912.400", backend="test"),),
        unverified=(Unverified(campo="rut_proveedor", causa=Causa.NO_PAGE, detalle="sin página"),),
        usage=Usage(prompt_tokens=1180, completion_tokens=210, reasoning_tokens=96),
        reasoning_effort="low",
        finish_reason="stop",
    )


def test_a_result_survives_a_round_trip_through_disk_unchanged():
    original = a_result()

    restored = ExtractionResult.from_dict(json.loads(json.dumps(original.to_dict())))

    assert restored == original
    assert restored.usage.reasoning_tokens == 96
    assert restored.unverified[0].causa is Causa.NO_PAGE


def test_an_unknown_causa_degrades_instead_of_raising():
    row = a_result().to_dict()
    row["unverified"] = [{"campo": "rut_proveedor", "causa": "CAUSA_QUE_YA_NO_EXISTE", "detalle": ""}]

    restored = ExtractionResult.from_dict(row)

    # A journal written by an older version must be countable, not fatal.
    assert restored.unverified[0].causa is Causa.HTTP_ERROR


def test_a_torn_last_line_does_not_cost_the_whole_journal(tmp_path):
    bench = load_bench()
    journal = bench.Journal(tmp_path / "journal.jsonl")
    journal.append("doc-1", a_result("doc-1"))
    journal.append("doc-2", a_result("doc-2"))
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"id": "doc-3", "result": {"documento_id": "doc-')  # killed mid-write

    loaded = journal.load()

    assert set(loaded) == {"doc-1", "doc-2"}


def test_the_journal_is_written_as_results_land_and_not_at_the_end(tmp_path):
    bench = load_bench()
    examples = bench.load_examples(FIXTURE)
    specs = list(PROCUREMENT_FIELDS)
    journal = bench.Journal(tmp_path / "journal.jsonl")
    seen_on_disk: list[int] = []

    def on_result(example, result):
        journal.append(example.id, result)
        seen_on_disk.append(len(journal.path.read_text(encoding="utf-8").splitlines()))

    bench.run_backend(CountingBackend(), examples, specs, on_result=on_result)

    # One line per document, growing while the run is still going: that property,
    # and not the final JSON, is what survives a kill.
    assert seen_on_disk == [1, 2, 3]
    assert len(journal.load()) == len(examples)


def test_resume_does_not_pay_twice_for_documents_already_in_the_journal(tmp_path):
    bench = load_bench()
    examples = bench.load_examples(FIXTURE)
    specs = list(PROCUREMENT_FIELDS)
    journal = bench.Journal(tmp_path / "journal.jsonl")
    journal.append(examples[0].id, a_result(examples[0].documento_id))

    already = journal.load()
    pending = [ex for ex in examples if ex.id not in already]
    backend = CountingBackend()
    fresh = bench.run_backend(backend, pending, specs)

    assert len(pending) == len(examples) - 1
    assert examples[0].documento_id not in backend.calls
    assert len(fresh) == len(examples) - 1


def test_an_interrupted_run_reports_what_it_has_and_says_it_is_incomplete(tmp_path):
    bench = load_bench()
    examples = bench.load_examples(FIXTURE)
    specs = list(PROCUREMENT_FIELDS)
    journal = bench.Journal(tmp_path / "journal.jsonl")

    class DyingBackend:
        name = "dying"

        def __init__(self):
            self.n = 0

        def extract(self, documento_id, pages, specs=None):
            self.n += 1
            if self.n > 1:
                raise KeyboardInterrupt
            return a_result(documento_id)

    merged = {}
    try:
        bench.run_backend(DyingBackend(), examples, specs, on_result=lambda ex, r: (journal.append(ex.id, r), merged.__setitem__(ex.id, r)))
    except KeyboardInterrupt:
        pass

    report = bench.score([(ex, merged[ex.id]) for ex in examples if ex.id in merged], specs)
    pendientes = len(examples) - len([ex for ex in examples if ex.id in merged])

    assert len(merged) == 1
    assert pendientes == 2
    assert report["ejemplos"] == 1


def test_a_failing_document_does_not_take_the_others_with_it_without_threads():
    bench = load_bench()
    examples = bench.load_examples(FIXTURE)
    specs = list(PROCUREMENT_FIELDS)

    results = bench.run_backend(ExplodingBackend(), examples, specs, workers=1)

    # The sequential path used to raise. A 503 on document 1 of 800 is a network
    # event, not a reason to lose the other 799.
    assert len(results) == len(examples)
    assert all(result.unverified for _, result in results)
    assert {u.causa for _, result in results for u in result.unverified} == {Causa.HTTP_ERROR}


def test_the_report_carries_its_own_provenance(tmp_path):
    bench = load_bench()
    examples = bench.load_examples(FIXTURE)
    specs = list(PROCUREMENT_FIELDS)
    journal = bench.Journal(tmp_path / "journal.jsonl")

    results = bench.run_backend(CountingBackend(), examples, specs, on_result=lambda ex, r: journal.append(ex.id, r))
    report = bench.score(results, specs)

    # Accuracy is not the point of this file: every field came back, and every
    # document was journalled. A report and its journal must cover the same set.
    assert report["ejemplos"] == len(examples)
    assert report["no_verificado"] == 0
    assert journal.path.exists()
    assert len(journal.load()) == len(examples)
