"""Dossier structures.

The identifier is the interesting part. It is **derived from the content**, never
generated: the same evidence produces the same id, on any machine, on any run. That
is what makes "rebuild the dossier and compare" a meaningful instruction instead of
a hopeful one.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Citation:
    """Where a statement comes from: this document, this page, reachable here."""

    documento_id: str
    url: str
    pagina: int

    def to_dict(self) -> dict[str, Any]:
        return {"documento_id": self.documento_id, "url": self.url, "pagina": self.pagina}


@dataclass(frozen=True, slots=True)
class DossierClaim:
    """One statement that survived the gate, with the citation that keeps it alive."""

    id: str
    texto: str
    citation: Citation

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "texto": self.texto, "citation": self.citation.to_dict()}


@dataclass(frozen=True, slots=True)
class RejectedClaim:
    """A statement that did not survive, and why. Kept for the audit trail."""

    id: str
    reason: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "reason": self.reason, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class Dossier:
    """A finished dossier. Immutable: an edited dossier is a different dossier."""

    titulo: str
    sujeto: str
    generado_en: str
    claims: tuple[DossierClaim, ...]
    rejected: tuple[RejectedClaim, ...]
    thresholds: dict[str, Any] = field(default_factory=dict)
    detectors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def id(self) -> str:
        """Content-derived identifier: same evidence, same id, always."""
        return "exp-" + _content_digest(self._canonical_payload())

    @property
    def citation_rate(self) -> float:
        total = len(self.claims) + len(self.rejected)
        return len(self.claims) / total if total else 0.0

    @property
    def signable(self) -> bool:
        """A dossier with no surviving statement is not signable — it is a memo."""
        return bool(self.claims) and all(claim.citation.pagina >= 1 for claim in self.claims)

    def _canonical_payload(self) -> dict[str, Any]:
        """Everything that must determine the identity — and nothing that must not.

        ``generado_en`` is excluded on purpose: the same evidence assembled tomorrow
        is the same dossier. The title is excluded as well; renaming a document does
        not change what it asserts.
        """
        return {
            "claims": [
                {"id": claim.id, "texto": claim.texto, **claim.citation.to_dict()}
                for claim in self.claims
            ],
            "rejected": [{"id": item.id, "reason": item.reason} for item in self.rejected],
            "thresholds": self.thresholds,
            "detectors": list(self.detectors),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "titulo": self.titulo,
            "sujeto": self.sujeto,
            "generado_en": self.generado_en,
            "signable": self.signable,
            "citation_rate": round(self.citation_rate, 4),
            "counts": {"claims": len(self.claims), "rejected": len(self.rejected)},
            "thresholds": self.thresholds,
            "detectors": list(self.detectors),
            "claims": [claim.to_dict() for claim in self.claims],
            "rejected": [item.to_dict() for item in self.rejected],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def to_markdown(self) -> str:
        return _render_markdown(self)


def _content_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _render_markdown(dossier: Dossier) -> str:
    lines = [
        f"# {dossier.titulo}",
        "",
        f"**Sujeto:** {dossier.sujeto}  ",
        f"**Generado:** {dossier.generado_en}  ",
        f"**ID del expediente:** `{dossier.id}`  ",
        f"**Tasa de citas:** {dossier.citation_rate:.0%} "
        f"({len(dossier.claims)} de {len(dossier.claims) + len(dossier.rejected)} afirmaciones)",
        "",
    ]

    if dossier.detectors:
        lines += [f"**Detectores:** {', '.join(dossier.detectors)}", ""]
    if dossier.thresholds:
        rendered = ", ".join(f"{name}={value}" for name, value in sorted(dossier.thresholds.items()))
        lines += [f"**Umbrales aplicados:** {rendered}", ""]

    lines += ["## Afirmaciones", ""]
    for number, claim in enumerate(dossier.claims, start=1):
        citation = claim.citation
        lines += [
            f"{number}. {claim.texto}  ",
            f"   *Fuente:* `{citation.documento_id}` p.{citation.pagina} — {citation.url}",
            "",
        ]

    if dossier.rejected:
        lines += ["## Afirmaciones eliminadas", ""]
        lines += [
            "*Estas afirmaciones no se emitieron: no alcanzaron una página de un documento "
            "verificable. Se listan para que la omisión sea auditable, no invisible.*",
            "",
        ]
        for item in dossier.rejected:
            lines += [f"- `{item.id}` — **{item.reason}** · {item.detail}"]
        lines.append("")

    lines += [
        "## Limitaciones",
        "",
        "Ninguna afirmación de este documento se emite sin una cita a un documento y una "
        "página. Las afirmaciones que no pudieron citarse fueron eliminadas antes de la "
        "exportación, no suavizadas. Este expediente describe relaciones presentes en "
        "registros públicos; no imputa conducta a persona o empresa alguna.",
        "",
        "## Firma",
        "",
        "*Este documento requiere revisión y firma humana. El sistema no emite veredictos.*",
        "",
        "Nombre: ______________________  RUT: ______________  Fecha: ____________",
        "",
    ]
    return "\n".join(lines)
