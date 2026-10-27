"""The dossier is the artifact. Its identity must be derived, not generated."""

from pathlib import Path

import pytest

from expediente.dossier.build import DossierError, build_dossier
from expediente.dossier.models import Citation, Dossier, DossierClaim, RejectedClaim
from expediente.evidence.gate import GateReport, KeptClaim, evaluate
from expediente.evidence.models import Document, Evidence, Claim
from expediente.ingest.records import load_dossier

FIXTURE = Path(__file__).resolve().parent.parent / "examples" / "dossier_synthetic.json"


def build_from_fixture(**overrides) -> Dossier:
    claims, evidence, documents = load_dossier(FIXTURE)
    report = evaluate(claims, evidence, documents)
    payload = dict(
        titulo="Expediente de prueba",
        sujeto="Municipalidad Sintética",
        generado_en="2026-10-27",
        thresholds={"umbral_clp": 1_000_000},
        detectors=("fraccionamiento",),
    )
    payload.update(overrides)
    return build_dossier(report, documents, **payload)


def test_only_claims_that_passed_the_gate_are_exported():
    dossier = build_from_fixture()

    assert [claim.id for claim in dossier.claims] == ["AF-1", "AF-4"]
    assert {item.reason for item in dossier.rejected} == {
        "NO_EVIDENCE", "NO_DOCUMENT", "PAGE_OUT_OF_RANGE", "EVIDENCE_UNKNOWN",
    }
    assert round(dossier.citation_rate, 4) == 0.3333
    assert dossier.signable is True


def test_dossier_id_is_derived_from_content():
    assert build_from_fixture().id == build_from_fixture().id


def test_dossier_id_ignores_when_it_was_generated_and_what_it_is_called():
    """Same evidence tomorrow is the same dossier, whatever the file is named."""
    first = build_from_fixture(generado_en="2026-10-27", titulo="Caso A")
    second = build_from_fixture(generado_en="2027-01-01", titulo="Caso B")
    assert first.id == second.id


def test_dossier_id_changes_when_a_citation_page_changes():
    base = build_from_fixture()
    moved = Dossier(
        titulo=base.titulo,
        sujeto=base.sujeto,
        generado_en=base.generado_en,
        claims=(
            DossierClaim(
                id=base.claims[0].id,
                texto=base.claims[0].texto,
                citation=Citation(
                    documento_id=base.claims[0].citation.documento_id,
                    url=base.claims[0].citation.url,
                    pagina=base.claims[0].citation.pagina + 1,
                ),
            ),
        ) + base.claims[1:],
        rejected=base.rejected,
        thresholds=base.thresholds,
        detectors=base.detectors,
    )
    assert moved.id != base.id, "a different page is a different dossier"


def test_a_dossier_without_surviving_claims_is_not_signable():
    empty = Dossier(
        titulo="t",
        sujeto="s",
        generado_en="2026-10-27",
        claims=(),
        rejected=(RejectedClaim(id="AF-1", reason="NO_EVIDENCE", detail="nada"),),
    )
    assert empty.signable is False
    assert empty.citation_rate == 0.0


def test_markdown_carries_the_citation_of_every_claim():
    text = build_from_fixture().to_markdown()

    assert "1. Cuatro órdenes del mismo municipio" in text
    assert "*Fuente:* `sha256:oc4101` p.2 — https://example.org/oc4101.pdf" in text
    assert "**Tasa de citas:** 33%" in text


def test_markdown_lists_the_rejections_so_the_omission_is_visible():
    text = build_from_fixture().to_markdown()

    assert "## Afirmaciones eliminadas" in text
    assert "PAGE_OUT_OF_RANGE" in text
    assert "no imputa conducta" in text


def test_markdown_ends_with_a_signature_block():
    text = build_from_fixture().to_markdown()

    assert "## Firma" in text
    assert "Nombre: ______________________  RUT:" in text
    assert "requiere revisión y firma humana" in text


def test_json_round_trips_and_exposes_the_counts():
    import json

    payload = json.loads(build_from_fixture().to_json())

    assert payload["id"].startswith("exp-")
    assert payload["counts"] == {"claims": 2, "rejected": 4}
    assert payload["signable"] is True
    assert payload["claims"][0]["citation"]["pagina"] == 2


def test_export_is_byte_identical_across_runs():
    assert build_from_fixture().to_json() == build_from_fixture().to_json()
    assert build_from_fixture().to_markdown() == build_from_fixture().to_markdown()


def test_a_claim_citing_an_absent_document_cannot_be_assembled():
    """If the gate let it through, the document set must still contain it."""
    claims = [Claim(id="AF-1", texto="algo", evidence_ids=("EV-1",))]
    evidence = [Evidence(id="EV-1", document_id="sha256:ghost", pagina=1, extracto="texto")]
    documents = [Document(id="sha256:other", url="https://example.org/other.pdf", paginas=5)]
    report = evaluate(claims, evidence, documents)

    # the gate rejects it (unknown document), so the report has no kept claims
    assert report.kept == ()

    fabricated = GateReport(
        kept=(KeptClaim(claim=claims[0], evidence_id="EV-1",
                        document_id="sha256:ghost", pagina=1),),
        dropped=(),
    )
    with pytest.raises(DossierError):
        build_dossier(fabricated, documents, titulo="t", sujeto="s", generado_en="x")
