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

from .control import PlaceboControl


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
    #: Every traceable citation for this claim, not just the first one the gate accepted.
    #: A statement about nine orders that shows one line reads as if one line proves nine;
    #: the reviewer has to be able to check each one.
    citations: tuple[Citation, ...] = ()

    @property
    def supporting(self) -> tuple[Citation, ...]:
        """All citations, with a single-citation claim collapsing to its one citation."""
        return self.citations or (self.citation,)

    def to_dict(self) -> dict[str, Any]:
        payload = {"id": self.id, "texto": self.texto, "citation": self.citation.to_dict()}
        if len(self.supporting) > 1:
            payload["citations"] = [citation.to_dict() for citation in self.supporting]
        return payload


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
    #: The counterfactual behind any claim about a threshold, measured on this same
    #: population. Part of the dossier's identity: a document whose control differs is a
    #: document that asserts something different about what its own numbers mean.
    control: PlaceboControl | None = None
    #: How to read this document's citations when they are not PDF pages. A citation to a
    #: row of an official dataset is checkable, but "page 36451" only means something if the
    #: document says so. Part of the identity: a dossier that explains its citations
    #: differently is asserting something different about its own evidence.
    notas: tuple[str, ...] = ()

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
                _claim_payload(claim)
                for claim in self.claims
            ],
            "rejected": [{"id": item.id, "reason": item.reason} for item in self.rejected],
            "thresholds": self.thresholds,
            "detectors": list(self.detectors),
            "control": self.control.to_dict() if self.control else None,
            "notas": list(self.notas),
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
            "control": self.control.to_dict() if self.control else None,
            "notas": list(self.notas),
            "claims": [claim.to_dict() for claim in self.claims],
            "rejected": [item.to_dict() for item in self.rejected],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def to_markdown(self) -> str:
        return _render_markdown(self)


def _claim_payload(claim: DossierClaim) -> dict[str, Any]:
    """Canonical form of a claim, stable for single-citation dossiers.

    The extra list only appears when there is more than one citation, so adding this
    capability did not change the id of any dossier that already had one.
    """
    entry: dict[str, Any] = {"id": claim.id, "texto": claim.texto, **claim.citation.to_dict()}
    if len(claim.supporting) > 1:
        entry["citations"] = [citation.to_dict() for citation in claim.supporting]
    return entry


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
        supporting = claim.supporting
        lines += [f"{number}. {claim.texto}  "]
        if len(supporting) == 1:
            citation = supporting[0]
            lines += [f"   *Fuente:* `{citation.documento_id}` p.{citation.pagina} — {citation.url}"]
        else:
            by_document: dict[tuple[str, str], list[int]] = {}
            for citation in supporting:
                by_document.setdefault((citation.documento_id, citation.url), []).append(citation.pagina)
            rendered = [
                f"`{document_id}` " + ", ".join(f"p.{page}" for page in sorted(pages)) + f" — {url}"
                for (document_id, url), pages in by_document.items()
            ]
            lines += [f"   *Fuentes ({len(supporting)}):* " + " · ".join(rendered)]
        lines.append("")

    if dossier.notas:
        lines += ["## Cómo leer las citas", ""]
        lines += [f"- {nota}" for nota in dossier.notas]
        lines.append("")

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

    if dossier.control:
        control = dossier.control
        real = control.real
        lines += ["## Control de placebo", ""]
        lines += [f"*{control.reading()}*", ""]
        lines += [
            "| factor del tope | cadenas | órdenes citadas | monto (CLP) | % de las reales |",
            "|---:|---:|---:|---:|---:|",
        ]
        for point in control.points:
            share = (point.chains / real.chains) if real and real.chains else None
            share_text = f"{share:.0%}" if share is not None else "—"
            lines.append(
                f"| {point.factor:.2f}x | {point.chains:,} | {point.orders_cited:,} "
                f"| {point.total_clp:,} | {share_text} |"
            )
        lines += [
            "",
            f"*Población: {control.population} · {control.orders_examined:,} órdenes examinadas. "
            "El control corre la misma regla con topes falsos: si la cuenta no se mueve, la regla "
            "mide repetición y no un incentivo.*",
            "",
        ]

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
