"""The evidence gate.

The rule, and the only rule this module implements: **a claim that cannot point at
a page of a real document does not leave the building.** Not flagged, not softened,
not exported with a disclaimer — deleted, with a reason code that a reviewer can
read.

This is intentionally the one place in the system that destroys information. That
destruction is what makes the citation rate a measurement instead of a claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from .models import Claim, Document, Evidence


#: (evidence id, document id, page) — the only shape that may leave the gate.
Citation = tuple[str, str, int]


class Rejection(str, Enum):
    """Why a claim did not survive. Codes, not prose, so they can be counted."""

    NO_EVIDENCE = "NO_EVIDENCE"
    EVIDENCE_UNKNOWN = "EVIDENCE_UNKNOWN"
    NO_DOCUMENT = "NO_DOCUMENT"
    DOCUMENT_UNKNOWN = "DOCUMENT_UNKNOWN"
    NO_PAGE = "NO_PAGE"
    PAGE_OUT_OF_RANGE = "PAGE_OUT_OF_RANGE"
    EMPTY_EXTRACT = "EMPTY_EXTRACT"


@dataclass(frozen=True, slots=True)
class KeptClaim:
    """A claim that reached a page of a document. This is exportable."""

    claim: Claim
    evidence_id: str
    document_id: str
    pagina: int

    def to_dict(self) -> dict:
        return {
            "id": self.claim.id,
            "texto": self.claim.texto,
            "evidence_id": self.evidence_id,
            "document_id": self.document_id,
            "pagina": self.pagina,
        }


@dataclass(frozen=True, slots=True)
class DroppedClaim:
    """A claim the gate refused, and the specific reason it refused it."""

    claim: Claim
    reason: Rejection
    detail: str

    def to_dict(self) -> dict:
        return {
            "id": self.claim.id,
            "texto": self.claim.texto,
            "reason": self.reason.value,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class GateReport:
    """The outcome of one pass. ``citation_rate`` is the number for the dossier."""

    kept: tuple[KeptClaim, ...]
    dropped: tuple[DroppedClaim, ...]

    @property
    def total(self) -> int:
        return len(self.kept) + len(self.dropped)

    @property
    def citation_rate(self) -> float:
        return len(self.kept) / self.total if self.total else 0.0

    def reasons(self) -> dict[str, int]:
        """Count of rejections by reason — the diagnostic view of a weak ingestion."""
        counts: dict[str, int] = {}
        for dropped in self.dropped:
            counts[dropped.reason.value] = counts.get(dropped.reason.value, 0) + 1
        return dict(sorted(counts.items()))

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "kept": len(self.kept),
            "dropped": len(self.dropped),
            "citation_rate": round(self.citation_rate, 4),
            "reasons": self.reasons(),
            "claims": [kept.to_dict() for kept in self.kept],
            "rejected": [dropped.to_dict() for dropped in self.dropped],
        }


def evaluate(
    claims: Iterable[Claim],
    evidence: Iterable[Evidence],
    documents: Iterable[Document],
) -> GateReport:
    """Apply the gate to every claim, preserving input order."""
    evidence_by_id = {item.id: item for item in evidence}
    documents_by_id = {item.id: item for item in documents}

    kept: list[KeptClaim] = []
    dropped: list[DroppedClaim] = []

    for claim in claims:
        citation, reason, detail = _trace_claim(claim, evidence_by_id, documents_by_id)
        if citation is not None:
            kept.append(citation)
        else:
            dropped.append(DroppedClaim(claim=claim, reason=reason, detail=detail))

    return GateReport(kept=tuple(kept), dropped=tuple(dropped))


def _trace_claim(
    claim: Claim,
    evidence_by_id: Mapping[str, Evidence],
    documents_by_id: Mapping[str, Document],
) -> tuple[KeptClaim | None, Rejection, str]:
    """Return the first whole citation for a claim, or the reason none was found.

    A claim with several citations survives on any one of them: corroboration is
    the point of listing more than one. Only when every citation is broken does
    the claim fall, and then it reports the first failure — the reviewer needs one
    concrete reason, not a list.
    """
    if not claim.evidence_ids:
        return None, Rejection.NO_EVIDENCE, "the assertion cites nothing"

    first_failure: tuple[Rejection, str] | None = None
    for evidence_id in claim.evidence_ids:
        citation, reason, detail = _trace_evidence(evidence_id, evidence_by_id, documents_by_id)
        if citation is not None:
            cited_evidence, document_id, pagina = citation
            return (
                KeptClaim(
                    claim=claim,
                    evidence_id=cited_evidence,
                    document_id=document_id,
                    pagina=pagina,
                ),
                Rejection.NO_EVIDENCE,
                "",
            )
        if first_failure is None:
            first_failure = (reason, detail)

    reason, detail = first_failure or (Rejection.NO_EVIDENCE, "no usable evidence")
    return None, reason, detail


def _trace_evidence(
    evidence_id: str,
    evidence_by_id: Mapping[str, Evidence],
    documents_by_id: Mapping[str, Document],
) -> tuple[Citation | None, Rejection, str]:
    """Walk evidence -> document -> page. Every hop can break the chain."""
    evidence = evidence_by_id.get(evidence_id)
    if evidence is None:
        return None, Rejection.EVIDENCE_UNKNOWN, f"evidence {evidence_id} does not exist"

    if not evidence.document_id:
        return None, Rejection.NO_DOCUMENT, f"evidence {evidence_id} points at no document"

    document = documents_by_id.get(evidence.document_id)
    if document is None:
        return None, Rejection.DOCUMENT_UNKNOWN, (
            f"evidence {evidence_id} points at unknown document {evidence.document_id}"
        )

    if evidence.pagina is None or evidence.pagina < 1:
        return None, Rejection.NO_PAGE, f"evidence {evidence_id} has no page number"

    if document.paginas is not None and evidence.pagina > document.paginas:
        return None, Rejection.PAGE_OUT_OF_RANGE, (
            f"evidence {evidence_id} cites page {evidence.pagina} of a "
            f"{document.paginas}-page document"
        )

    if not evidence.extracto.strip():
        return None, Rejection.EMPTY_EXTRACT, f"evidence {evidence_id} extracted no text"

    return (evidence_id, document.id, evidence.pagina), Rejection.NO_EVIDENCE, ""
