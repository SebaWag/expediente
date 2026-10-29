"""The PDF parser turns a declared page number into a derived one.

The fixtures here are real PDF files built byte by byte in the test, so the parser
is exercised against a file on disk and not against a mock. That matters: the bug
this module exists to prevent is a citation whose page number was never asked of
anybody, and a mocked reader would happily confirm it.
"""

import hashlib

import pytest

# pypdf is the default reader and lives behind an extra, for the same reason.
pytest.importorskip("pypdf")

from expediente.evidence.gate import Rejection, evaluate
from expediente.evidence.models import Claim, Evidence
from expediente.evidence.pdf import (
    PdfError,
    evidence_id,
    file_digest,
    locate,
    parse_pdf,
    dense,
    squash,
    verify,
)


# --------------------------------------------------------------------------- #
# A minimal, standards-correct PDF builder (no third-party dependency)
# --------------------------------------------------------------------------- #


def build_pdf(pages: list[str]) -> bytes:
    """A valid multi-page PDF with one line of text per page.

    Objects: 1 catalog, 2 pages tree, then (page, contents) per page, then the font.
    Offsets are computed as the file is written, which is the only part that has to
    be exactly right for readers to accept it.
    """
    count = len(pages)
    font_object = 3 + 2 * count
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(count))

    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode(),
    ]
    for index, text in enumerate(pages):
        contents_object = 4 + 2 * index
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {contents_object} 0 R "
                f"/Resources << /Font << /F1 {font_object} 0 R >> >> >>"
            ).encode()
        )
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")

    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


@pytest.fixture()
def three_page_pdf(tmp_path):
    pages = [
        "RESOLUCION EXENTA 4412 ORDEN DE COMPRA 1000813-396-AG26",
        "Proveedor: COMERCIAL DEL SUR LIMITADA RUT 76543210-K",
        "Monto total 940.000 pesos. Autoriza la jefatura de abastecimiento.",
    ]
    path = tmp_path / "oc_1000813.pdf"
    path.write_bytes(build_pdf(pages))
    return path


# --------------------------------------------------------------------------- #
# Parsing: the page count stops being a claim
# --------------------------------------------------------------------------- #


def test_page_count_comes_from_the_file(three_page_pdf):
    parsed = parse_pdf(three_page_pdf)

    assert parsed.document.paginas == 3
    assert len(parsed.pages) == 3
    assert parsed.pages[0].numero == 1
    assert "1000813-396-AG26" in parsed.pages[0].texto


def test_document_id_is_the_content_hash(three_page_pdf, tmp_path):
    parsed = parse_pdf(three_page_pdf)

    expected = "sha256:" + hashlib.sha256(three_page_pdf.read_bytes()).hexdigest()
    assert parsed.document.id == expected
    assert file_digest(three_page_pdf) == expected

    # Same bytes, different path: still one document. That is the idempotency the
    # graph depends on.
    copy = tmp_path / "copia.pdf"
    copy.write_bytes(three_page_pdf.read_bytes())
    assert parse_pdf(copy).document.id == parsed.document.id

    # Different content: a different document, never a merge.
    changed = tmp_path / "cambiada.pdf"
    changed.write_bytes(build_pdf(["OTRA COSA"]))
    assert parse_pdf(changed).document.id != parsed.document.id


def test_the_extractor_is_recorded(three_page_pdf):
    parsed = parse_pdf(three_page_pdf)

    assert parsed.extractor in {"pypdf", "pymupdf", "pdfplumber"}
    assert parsed.copyleft is (parsed.extractor == "pymupdf")


def test_a_missing_file_is_an_explicit_error(tmp_path):
    with pytest.raises(PdfError) as exc:
        parse_pdf(tmp_path / "no_existe.pdf")

    assert exc.value.code == PdfError.NOT_FOUND


def test_a_scan_without_text_is_reported_not_returned_empty(tmp_path):
    """A page of blank content is a scan. It must not become empty evidence."""
    path = tmp_path / "escaneado.pdf"
    path.write_bytes(build_pdf(["", ""]))

    with pytest.raises(PdfError) as exc:
        parse_pdf(path)

    assert exc.value.code == PdfError.NO_TEXT_LAYER
    assert "OCR" in exc.value.detail


def test_an_unknown_extractor_is_refused(three_page_pdf):
    with pytest.raises(PdfError) as exc:
        parse_pdf(three_page_pdf, extractor="tesseract")

    assert exc.value.code == PdfError.NO_BACKEND


# --------------------------------------------------------------------------- #
# Locating: no text, no evidence
# --------------------------------------------------------------------------- #


def test_locate_returns_the_page_and_the_original_text(three_page_pdf):
    parsed = parse_pdf(three_page_pdf)

    found = locate(parsed, "1000813-396-AG26")

    assert len(found) == 1
    assert found[0].pagina == 1
    assert found[0].document_id == parsed.document.id
    assert "1000813-396-AG26" in found[0].extracto
    assert found[0].extractor == parsed.extractor


def test_locate_returns_nothing_when_the_text_is_absent(three_page_pdf):
    """The anti-invention test: a miss is an empty tuple, never a nearby page."""
    parsed = parse_pdf(three_page_pdf)

    assert locate(parsed, "9999999-999-AG99") == ()
    assert locate(parsed, "") == ()


def test_locate_finds_the_right_page_in_a_multi_page_document(three_page_pdf):
    parsed = parse_pdf(three_page_pdf)

    assert locate(parsed, "76543210-K")[0].pagina == 2
    assert locate(parsed, "940.000")[0].pagina == 3


def test_locate_survives_ragged_whitespace(tmp_path):
    """Extractors break order codes across lines; the citation must still be found."""
    path = tmp_path / "cortado.pdf"
    path.write_bytes(build_pdf(["OC   1000813 -\n396-AG26   total"]))

    parsed = parse_pdf(path)
    found = locate(parsed, "1000813-396-AG26")

    assert len(found) == 1
    assert found[0].pagina == 1


def test_squash_maps_back_to_original_offsets():
    original = "  OC   123  \n total "
    squashed, index = squash(original)

    assert squashed == "OC 123 total"
    for position, char in enumerate(squashed):
        if char == " ":
            continue
        assert original[index[position]] == char


def test_dense_drops_all_whitespace_and_keeps_the_map():
    original = "OC   1000813 -\n396-AG26   total"
    compact, index = dense(original)

    assert compact == "OC1000813-396-AG26total"
    for position, char in enumerate(compact):
        assert original[index[position]] == char


def test_evidence_id_is_content_addressed(three_page_pdf):
    parsed = parse_pdf(three_page_pdf)
    first = locate(parsed, "1000813-396-AG26")[0]

    again = locate(parse_pdf(three_page_pdf), "1000813-396-AG26")[0]
    assert first.id == again.id

    assert first.id != evidence_id(parsed.document.id, 2, first.extracto)
    assert first.id.startswith("ev_")


def test_locate_deduplicates_repeated_text_on_a_page(tmp_path):
    path = tmp_path / "repetido.pdf"
    path.write_bytes(build_pdf(["TOTAL 940.000 TOTAL 940.000"]))

    found = locate(parse_pdf(path), "940.000")
    assert len({item.id for item in found}) == len(found)


# --------------------------------------------------------------------------- #
# Verifying: the check the gate cannot make
# --------------------------------------------------------------------------- #


def test_verify_accepts_a_faithful_citation(three_page_pdf):
    parsed = parse_pdf(three_page_pdf)
    found = locate(parsed, "76543210-K")[0]

    assert verify(parsed, found).ok


def test_verify_rejects_a_citation_on_the_wrong_page(three_page_pdf):
    """A real extract with an invented page. This is the default failure mode."""
    parsed = parse_pdf(three_page_pdf)
    found = locate(parsed, "76543210-K")[0]
    tampered = Evidence(
        id=found.id,
        document_id=found.document_id,
        pagina=3,
        extracto=found.extracto,
    )

    result = verify(parsed, tampered)

    assert not result.ok
    assert result.reason == "EXTRACT_NOT_ON_PAGE"


def test_verify_rejects_a_fabricated_extract(three_page_pdf):
    parsed = parse_pdf(three_page_pdf)
    invented = Evidence(
        id="ev_inventada",
        document_id=parsed.document.id,
        pagina=1,
        extracto="El proveedor renuncio a todo pago",
    )

    result = verify(parsed, invented)

    assert not result.ok
    assert result.reason == "EXTRACT_NOT_ON_PAGE"


def test_verify_rejects_a_page_out_of_range(three_page_pdf):
    parsed = parse_pdf(three_page_pdf)
    found = locate(parsed, "940.000")[0]
    beyond = Evidence(
        id=found.id,
        document_id=found.document_id,
        pagina=12,
        extracto=found.extracto,
    )

    assert verify(parsed, beyond).reason == "PAGE_OUT_OF_RANGE"


def test_verify_rejects_a_document_from_elsewhere(three_page_pdf):
    parsed = parse_pdf(three_page_pdf)
    found = locate(parsed, "940.000")[0]
    stranger = Evidence(
        id=found.id,
        document_id="sha256:otro",
        pagina=found.pagina,
        extracto=found.extracto,
    )

    assert verify(parsed, stranger).reason == "DOCUMENT_MISMATCH"


def test_verify_rejects_an_empty_extract(three_page_pdf):
    parsed = parse_pdf(three_page_pdf)
    blank = Evidence(id="ev_vacia", document_id=parsed.document.id, pagina=1, extracto="  ")

    assert verify(parsed, blank).reason == "EMPTY_EXTRACT"


# --------------------------------------------------------------------------- #
# The whole point: parsed evidence passes the gate, a tampered page does not
# --------------------------------------------------------------------------- #


def test_parsed_evidence_survives_the_gate(three_page_pdf):
    parsed = parse_pdf(three_page_pdf)
    found = locate(parsed, "1000813-396-AG26")

    report = evaluate(
        [Claim(id="AF-1", texto="La OC existe y es de 940.000", evidence_ids=(found[0].id,))],
        found,
        [parsed.document],
    )

    assert len(report.kept) == 1
    assert report.kept[0].pagina == 1
    assert report.citation_rate == 1.0


def test_the_gate_alone_cannot_tell_a_real_page_from_a_plausible_one(three_page_pdf):
    """Why this module had to exist.

    The gate checks shape: a page between 1 and ``paginas`` with text in it passes.
    An invented page 3 of a real 3-page document is shape-perfect, so the gate keeps
    it — and only ``verify`` re-opening the file can tell the difference.
    """
    parsed = parse_pdf(three_page_pdf)
    found = locate(parsed, "1000813-396-AG26")[0]
    plausible_lie = Evidence(
        id=found.id,
        document_id=found.document_id,
        pagina=3,
        extracto=found.extracto,
    )

    report = evaluate(
        [Claim(id="AF-1", texto="Citado en la pagina 3", evidence_ids=(found.id,))],
        [plausible_lie],
        [parsed.document],
    )

    assert len(report.kept) == 1, "con esto solo, el gate no puede distinguir"
    assert not verify(parsed, plausible_lie).ok, "abriendo el archivo, si"


def test_a_claim_citing_nothing_still_fails(three_page_pdf):
    parsed = parse_pdf(three_page_pdf)
    report = evaluate([Claim(id="AF-1", texto="sin cita")], [], [parsed.document])

    assert report.dropped[0].reason is Rejection.NO_EVIDENCE
