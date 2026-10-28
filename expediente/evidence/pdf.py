"""Read a PDF into located evidence: this document, this page, this text.

The evidence gate (``expediente.evidence.gate``) decides whether a claim may leave
the building, but it can only inspect what it is given. Until this module existed,
every ``pagina`` in a dossier was *declared*: a number somebody typed into a JSON
file, indistinguishable from an invented one. The gate could check that page 4 of a
12-page document was plausible, never that page 4 said what the claim said.

This module makes the page **derived**. A citation produced here comes from a file
that was opened, hashed, and read, and ``verify`` can prove after the fact that the
text still sits on the page it claims.

Three deliberate constraints:

* **No hard dependency.** The package ships with none, so the PDF backend is
  optional (``pip install -e '.[pdf]'``) and a missing backend is an explicit
  ``NO_BACKEND`` error, not an import crash at startup.
* **No inventing.** A page with no text layer (a scan) is reported as such. It is
  never returned as empty evidence, because empty evidence is exactly what a claim
  needs to look citable without being citable.
* **Licence-aware.** ``pypdf`` is BSD and is the default. ``pymupdf`` is faster but
  **AGPL**, so it is opt-in by name and the choice is recorded in the evidence.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from .models import Document, Evidence

#: Backends in preference order. ``auto`` picks the first one importable.
BACKENDS: tuple[str, ...] = ("pypdf", "pymupdf", "pdfplumber")

#: Backends that carry a copyleft licence into the product. Recorded, never implicit.
COPYLEFT: frozenset[str] = frozenset({"pymupdf"})

_WS = re.compile(r"\s+")

#: One-to-one accent folding. Kept character-for-character so that offsets found in
#: the folded text still point at the same place in the original.
_FOLD = str.maketrans(
    "áàäâãéèëêíìïîóòöôõúùüûñÁÀÄÂÃÉÈËÊÍÌÏÎÓÒÖÔÕÚÙÜÛÑ",
    "aaaaaeeeeiiiiooooouuuunAAAAAEEEEIIIIOOOOOUUUUN",
)


class PdfError(ValueError):
    """A PDF that cannot be turned into evidence, with a reason code.

    Codes rather than sentences, for the same reason the gate uses them: they can be
    counted across a corpus, and a 4.000-document run needs a histogram, not prose.
    """

    NO_BACKEND = "NO_BACKEND"
    NOT_FOUND = "NOT_FOUND"
    NOT_A_PDF = "NOT_A_PDF"
    UNREADABLE = "UNREADABLE"
    ENCRYPTED = "ENCRYPTED"
    EMPTY_DOCUMENT = "EMPTY_DOCUMENT"
    NO_TEXT_LAYER = "NO_TEXT_LAYER"

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ParsedPage:
    """One page of a parsed document. ``texto`` is raw; folding happens at match time."""

    numero: int
    texto: str

    @property
    def tiene_texto(self) -> bool:
        return bool(self.texto.strip())


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """A hashed document plus its pages, in page order.

    ``document.paginas`` is always set: the point of parsing is that the page count
    stops being a claim and becomes a count.
    """

    document: Document
    pages: tuple[ParsedPage, ...]
    extractor: str

    @property
    def copyleft(self) -> bool:
        return any(name in self.extractor for name in COPYLEFT)

    @property
    def paginas_sin_texto(self) -> tuple[int, ...]:
        """Pages with no text layer. These need vision or a human; they are not empty."""
        return tuple(page.numero for page in self.pages if not page.tiene_texto)

    def page(self, numero: int) -> ParsedPage | None:
        if 1 <= numero <= len(self.pages):
            return self.pages[numero - 1]
        return None

    def summary(self) -> dict:
        return {
            "document_id": self.document.id,
            "url": self.document.url,
            "tipo": self.document.tipo,
            "paginas": self.document.paginas,
            "extractor": self.extractor,
            "copyleft_license": self.copyleft,
            "pages_sin_texto": list(self.paginas_sin_texto),
            "chars_por_pagina": [len(page.texto) for page in self.pages],
        }


@dataclass(frozen=True, slots=True)
class Verification:
    """Whether an evidence entry still describes a page that exists and says that."""

    ok: bool
    reason: str = ""
    detail: str = ""

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, "detail": self.detail}


# --------------------------------------------------------------------------- #
# Backends
# --------------------------------------------------------------------------- #


def _read_with_pypdf(path: Path) -> list[str]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - exercised by the NO_BACKEND test
        raise PdfError(PdfError.NO_BACKEND, "pypdf is not installed") from exc

    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            # An empty password is common in procurement files; try it, then give up.
            try:
                if reader.decrypt("") == 0:
                    raise PdfError(PdfError.ENCRYPTED, "password required")
            except PdfError:
                raise
            except Exception as exc:
                raise PdfError(PdfError.ENCRYPTED, str(exc)) from exc
        return [page.extract_text() or "" for page in reader.pages]
    except PdfError:
        raise
    except Exception as exc:
        raise PdfError(PdfError.UNREADABLE, str(exc)[:200]) from exc


def _read_with_pymupdf(path: Path) -> list[str]:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:  # pragma: no cover
        raise PdfError(PdfError.NO_BACKEND, "pymupdf is not installed") from exc

    try:
        with fitz.open(str(path)) as doc:
            if doc.is_encrypted and not doc.authenticate(""):
                raise PdfError(PdfError.ENCRYPTED, "password required")
            return [page.get_text("text") or "" for page in doc]
    except PdfError:
        raise
    except Exception as exc:
        raise PdfError(PdfError.UNREADABLE, str(exc)[:200]) from exc


def _read_with_pdfplumber(path: Path) -> list[str]:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover
        raise PdfError(PdfError.NO_BACKEND, "pdfplumber is not installed") from exc

    try:
        with pdfplumber.open(str(path)) as pdf:
            return [page.extract_text() or "" for page in pdf.pages]
    except Exception as exc:
        raise PdfError(PdfError.UNREADABLE, str(exc)[:200]) from exc


_READERS = {
    "pypdf": _read_with_pypdf,
    "pymupdf": _read_with_pymupdf,
    "pdfplumber": _read_with_pdfplumber,
}


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #


def file_digest(path: str | Path) -> str:
    """``sha256:<hex>`` of the file. Content, not path: the same PDF twice is one node."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def parse_pdf(
    path: str | Path,
    *,
    url: str = "",
    tipo: str = "pdf",
    extractor: str = "auto",
) -> ParsedDocument:
    """Open a PDF and return its pages with the document hashed.

    ``extractor`` is ``auto`` (first importable backend), a backend name, or
    ``texto`` to accept the text the caller already has. Nothing is inferred: if no
    backend is installed the error says so.
    """
    source = Path(path)
    if not source.exists():
        raise PdfError(PdfError.NOT_FOUND, str(source))
    if not source.is_file():
        raise PdfError(PdfError.NOT_A_PDF, f"{source} is not a file")

    chosen, pages = _read_pages(source, extractor)

    if not pages:
        raise PdfError(PdfError.EMPTY_DOCUMENT, "the PDF has no pages")
    if all(not text.strip() for text in pages):
        raise PdfError(
            PdfError.NO_TEXT_LAYER,
            f"{len(pages)} pages and not one character of text: this is a scan, "
            "and a scan needs an OCR pass before it can be cited",
        )

    document = Document(
        id=file_digest(source),
        url=url or source.resolve().as_uri(),
        tipo=tipo,
        paginas=len(pages),
    )
    parsed = tuple(
        ParsedPage(numero=index, texto=text) for index, text in enumerate(pages, start=1)
    )
    return ParsedDocument(document=document, pages=parsed, extractor=chosen)


def _read_pages(source: Path, extractor: str) -> tuple[str, list[str]]:
    if extractor == "auto":
        attempts = BACKENDS
    elif extractor in _READERS:
        attempts = (extractor,)
    else:
        raise PdfError(
            PdfError.NO_BACKEND,
            f"unknown extractor {extractor!r}; expected auto or one of {', '.join(BACKENDS)}",
        )

    last: PdfError | None = None
    for name in attempts:
        try:
            return name, _READERS[name](source)
        except PdfError as exc:
            if exc.code in (PdfError.ENCRYPTED, PdfError.NOT_FOUND, PdfError.NOT_A_PDF):
                raise
            last = exc
    raise last or PdfError(PdfError.NO_BACKEND, "no PDF backend available")


# --------------------------------------------------------------------------- #
# Locating an extract
# --------------------------------------------------------------------------- #


def squash(text: str) -> tuple[str, list[int]]:
    """Collapse whitespace and fold accents, keeping a map back to the original.

    Readability helper: what a reviewer sees when two citations are compared.
    Matching uses :func:`dense`, because a single whitespace is already too much
    for a code the extractor split across a line break.
    """
    folded = text.translate(_FOLD)
    out: list[str] = []
    index: list[int] = []
    pending = False
    for position, char in enumerate(folded):
        if char.isspace():
            pending = bool(out)
            continue
        if pending:
            out.append(" ")
            index.append(position)
            pending = False
        out.append(char)
        index.append(position)
    return "".join(out), index


def dense(text: str) -> tuple[str, list[int]]:
    """Fold accents and drop *all* whitespace, keeping a map back to the original.

    This is the matcher, and it is stricter than it looks. ``1000813-396-AG26`` in a
    procurement PDF routinely arrives as ``1000813-396-`` / newline / ``AG26``, so a
    search that respects whitespace finds nothing and the caller would conclude the
    document does not say it. Dropping whitespace on both sides makes the comparison
    about the characters that carry meaning. The trade-off is that a needle can match
    across a word boundary — acceptable here because a match still has to exist on the
    page, and the extract returned is the original text either way.
    """
    folded = text.translate(_FOLD)
    characters: list[str] = []
    positions: list[int] = []
    for position, char in enumerate(folded):
        if char.isspace():
            continue
        characters.append(char)
        positions.append(position)
    return "".join(characters), positions


def normalize_needle(needle: str) -> str:
    """Fold and de-space the search string the same way pages are folded."""
    return _WS.sub("", needle.translate(_FOLD))


def _window(text: str, start: int, end: int, contexto: int) -> str:
    """A readable extract around a match, cut on word boundaries."""
    left = max(0, start - contexto)
    right = min(len(text), end + contexto)
    if left > 0:
        space = text.rfind(" ", left, start)
        if space != -1:
            left = space + 1
    if right < len(text):
        space = text.find(" ", right)
        if space != -1:
            right = space
    return _WS.sub(" ", text[left:right]).strip()


def evidence_id(document_id: str, pagina: int, extract: str) -> str:
    """Content-addressed evidence id: same document, page and text give the same id."""
    payload = f"{document_id}|{pagina}|{extract}".encode("utf-8")
    return "ev_" + hashlib.sha256(payload).hexdigest()[:16]


def locate(
    parsed: ParsedDocument,
    needle: str,
    *,
    contexto: int = 180,
    max_hits: int = 10,
) -> tuple[Evidence, ...]:
    """Every page where ``needle`` appears, as evidence with the surrounding text.

    Returns an empty tuple when the text is not there. That is the whole point: a
    caller cannot get evidence for something the document does not say.
    """
    target = normalize_needle(needle)
    if not target:
        return ()

    found: list[Evidence] = []
    seen: set[str] = set()

    for page in parsed.pages:
        haystack, index = dense(page.texto)
        cursor = haystack.find(target)
        while cursor != -1 and len(found) < max_hits:
            start = index[cursor]
            end = index[min(cursor + len(target) - 1, len(index) - 1)] + 1
            extract = _window(page.texto, start, end, contexto)
            identifier = evidence_id(parsed.document.id, page.numero, extract)
            if identifier not in seen:
                seen.add(identifier)
                found.append(
                    Evidence(
                        id=identifier,
                        document_id=parsed.document.id,
                        pagina=page.numero,
                        extracto=extract,
                        extractor=parsed.extractor,
                    )
                )
            cursor = haystack.find(target, cursor + len(target))

    return tuple(found)


def verify(parsed: ParsedDocument, evidence: Evidence) -> Verification:
    """Check that this evidence still describes a page that exists and says that.

    This is the check the gate cannot make on its own: given a citation, re-open the
    document and confirm the extract is on the cited page. It is cheap, it is
    deterministic, and it is the difference between a citation and a plausible number.
    """
    if evidence.document_id and evidence.document_id != parsed.document.id:
        return Verification(
            False,
            "DOCUMENT_MISMATCH",
            f"evidence cites {evidence.document_id}, this document is {parsed.document.id}",
        )

    page = parsed.page(evidence.pagina) if evidence.pagina else None
    if page is None:
        return Verification(
            False, "PAGE_OUT_OF_RANGE", f"page {evidence.pagina} of {len(parsed.pages)}"
        )

    if not evidence.extracto.strip():
        return Verification(False, "EMPTY_EXTRACT", "the citation carries no text")

    haystack, _ = dense(page.texto)
    if dense(evidence.extracto)[0] in haystack:
        return Verification(True)

    return Verification(
        False,
        "EXTRACT_NOT_ON_PAGE",
        f"the text is not on page {evidence.pagina}: the citation points elsewhere "
        "or the document changed",
    )
