"""Dossier-side structures: documents, evidence, claims.

Kept separate from the graph models on purpose. The graph is the source of truth
about the world; these three are the source of truth about *what we are willing to
assert*, and the gate is the only thing allowed to move a claim between them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Document:
    """A source file the dossier can point at.

    ``id`` is the content hash, so the same PDF fetched twice is one document and
    the citation survives a re-run. ``paginas`` is optional because a document may
    be known before it is parsed — but a claim that needs a page will be rejected
    until it exists.
    """

    id: str
    url: str
    tipo: str = "pdf"
    paginas: int | None = None


@dataclass(frozen=True, slots=True)
class Evidence:
    """A located extract: this document, this page, this text.

    ``extractor`` records *how* the page was read — text layer, vision — so the
    dossier can be honest about the confidence of each citation.
    """

    id: str
    document_id: str | None = None
    pagina: int | None = None
    extracto: str = ""
    extractor: str = "text-layer"


@dataclass(frozen=True, slots=True)
class Claim:
    """An assertion the dossier wants to make, citing one or more evidence entries."""

    id: str
    texto: str
    evidence_ids: tuple[str, ...] = ()
