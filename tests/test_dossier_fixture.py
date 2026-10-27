"""The shipped example must keep telling the story the README claims it tells."""

from pathlib import Path

from expediente.evidence.gate import evaluate
from expediente.ingest.records import load_dossier

FIXTURE = Path(__file__).resolve().parent.parent / "examples" / "dossier_synthetic.json"


def test_example_dossier_loads_and_gates_predictably():
    claims, evidence, documents = load_dossier(FIXTURE)
    report = evaluate(claims, evidence, documents)

    assert report.total == len(claims)
    assert len(report.kept) >= 1
    assert len(report.dropped) >= 1
    assert 0.0 < report.citation_rate < 1.0, "the example should show both outcomes"
