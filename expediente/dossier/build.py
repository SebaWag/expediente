"""Assemble a dossier from a gate report.

The gate decides what may leave; this decides what the reader sees. Keeping them
separate is what allows the gate to be strict without the dossier being unreadable:
rejections are counted and named in an audit section instead of vanishing.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..evidence.gate import GateReport
from ..evidence.models import Document
from .models import Citation, Dossier, DossierClaim, RejectedClaim


class DossierError(ValueError):
    """Raised when a dossier cannot be assembled honestly."""


def build_dossier(
    report: GateReport,
    documents: Iterable[Document],
    *,
    titulo: str,
    sujeto: str,
    generado_en: str,
    thresholds: Mapping[str, Any] | None = None,
    detectors: Iterable[str] = (),
) -> Dossier:
    """Turn a gate report into a signable dossier.

    ``generado_en`` is passed in rather than read from the clock so that building
    twice produces the same bytes — a test can then assert it.
    """
    by_id = {document.id: document for document in documents}

    claims: list[DossierClaim] = []
    for kept in report.kept:
        document = by_id.get(kept.document_id)
        if document is None:
            raise DossierError(
                f"claim {kept.claim.id} cites {kept.document_id}, which is not in the document set"
            )
        claims.append(
            DossierClaim(
                id=kept.claim.id,
                texto=kept.claim.texto,
                citation=Citation(
                    documento_id=document.id,
                    url=document.url,
                    pagina=kept.pagina,
                ),
            )
        )

    rejected = [
        RejectedClaim(id=item.claim.id, reason=item.reason.value, detail=item.detail)
        for item in report.dropped
    ]

    return Dossier(
        titulo=titulo,
        sujeto=sujeto,
        generado_en=generado_en,
        claims=tuple(claims),
        rejected=tuple(rejected),
        thresholds=dict(thresholds or {}),
        detectors=tuple(sorted(detectors)),
    )
