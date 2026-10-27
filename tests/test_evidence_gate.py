"""The gate is the product's promise, so it is tested refusal by refusal."""

from expediente.evidence.gate import Rejection, evaluate
from expediente.evidence.models import Claim, Document, Evidence

DOC = Document(id="sha256:oc4101", url="https://example.org/oc4101.pdf", paginas=12)
OTHER = Document(id="sha256:agil2255", url="https://example.org/agil2255.pdf", paginas=8)


def whole_evidence(evidence_id: str = "EV-1", pagina: int = 4) -> Evidence:
    return Evidence(
        id=evidence_id,
        document_id=DOC.id,
        pagina=pagina,
        extracto="OC 1000813-396-AG26, total $940.000",
    )


def claim(claim_id: str = "AF-1", *evidence_ids: str) -> Claim:
    return Claim(id=claim_id, texto="Siete órdenes bajo el umbral", evidence_ids=evidence_ids)


def test_a_claim_with_a_whole_citation_survives():
    report = evaluate([claim("AF-1", "EV-1")], [whole_evidence()], [DOC])

    assert len(report.kept) == 1
    assert report.dropped == ()
    assert report.kept[0].document_id == DOC.id
    assert report.kept[0].pagina == 4
    assert report.citation_rate == 1.0


def test_a_claim_without_citations_is_deleted():
    report = evaluate([claim("AF-1")], [], [DOC])

    assert report.kept == ()
    assert report.dropped[0].reason is Rejection.NO_EVIDENCE


def test_citing_evidence_that_does_not_exist_is_deleted():
    report = evaluate([claim("AF-1", "EV-404")], [whole_evidence()], [DOC])

    assert report.dropped[0].reason is Rejection.EVIDENCE_UNKNOWN


def test_evidence_without_a_document_is_deleted():
    orphan = Evidence(id="EV-1", document_id=None, pagina=4, extracto="texto")
    report = evaluate([claim("AF-1", "EV-1")], [orphan], [DOC])

    assert report.dropped[0].reason is Rejection.NO_DOCUMENT


def test_citing_an_unknown_document_is_deleted():
    ghost = Evidence(id="EV-1", document_id="sha256:missing", pagina=4, extracto="texto")
    report = evaluate([claim("AF-1", "EV-1")], [ghost], [DOC])

    assert report.dropped[0].reason is Rejection.DOCUMENT_UNKNOWN


def test_evidence_without_a_page_is_deleted():
    page_less = Evidence(id="EV-1", document_id=DOC.id, pagina=None, extracto="texto")
    report = evaluate([claim("AF-1", "EV-1")], [page_less], [DOC])

    assert report.dropped[0].reason is Rejection.NO_PAGE


def test_a_page_beyond_the_document_is_deleted():
    report = evaluate([claim("AF-1", "EV-1")], [whole_evidence(pagina=99)], [DOC])

    assert report.dropped[0].reason is Rejection.PAGE_OUT_OF_RANGE
    assert "99" in report.dropped[0].detail


def test_a_page_with_no_extracted_text_is_deleted():
    empty = Evidence(id="EV-1", document_id=DOC.id, pagina=3, extracto="   ")
    report = evaluate([claim("AF-1", "EV-1")], [empty], [DOC])

    assert report.dropped[0].reason is Rejection.EMPTY_EXTRACT


def test_one_good_citation_rescues_a_claim_with_broken_ones():
    broken = Evidence(id="EV-1", document_id=None, pagina=4, extracto="texto")
    report = evaluate([claim("AF-1", "EV-1", "EV-2")], [broken, whole_evidence("EV-2")], [DOC])

    assert len(report.kept) == 1
    assert report.kept[0].evidence_id == "EV-2"


def test_report_counts_and_rate():
    claims = [claim("AF-1", "EV-1"), claim("AF-2"), claim("AF-3", "EV-404")]
    report = evaluate(claims, [whole_evidence()], [DOC])

    assert report.total == 3
    assert len(report.kept) == 1
    assert round(report.citation_rate, 4) == 0.3333
    assert report.reasons() == {"EVIDENCE_UNKNOWN": 1, "NO_EVIDENCE": 1}


def test_empty_dossier_does_not_divide_by_zero():
    report = evaluate([], [], [])
    assert report.citation_rate == 0.0
    assert report.total == 0


def test_order_is_preserved():
    claims = [claim("AF-1", "EV-1"), claim("AF-2", "EV-2")]
    report = evaluate(claims, [whole_evidence("EV-1"), whole_evidence("EV-2")], [DOC])
    assert [kept.claim.id for kept in report.kept] == ["AF-1", "AF-2"]
