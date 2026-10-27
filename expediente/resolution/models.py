"""Structures for entity resolution.

The naming is deliberate: a *relation* is a fact the sources support, a *candidate*
is something a human still has to decide, and a *rejection* is a record that cannot
participate at all. Only the first one reaches the graph unattended.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RelationKind(str, Enum):
    """The two deterministic signals. Both are exact matches, not scores."""

    REPRESENTANTE_COMPARTIDO = "representante_compartido"
    DOMICILIO_COMPARTIDO = "domicilio_compartido"


class Rejection(str, Enum):
    """Why a record could not take part in resolution."""

    RUT_INUSABLE = "RUT_INUSABLE"
    SIN_DATOS_DE_BLOQUEO = "SIN_DATOS_DE_BLOQUEO"


@dataclass(frozen=True, slots=True)
class SourceRef:
    """Where a fact came from. A relation without one of these is not exportable."""

    documento_id: str
    pagina: int | None = None

    def to_dict(self) -> dict:
        return {"documento_id": self.documento_id, "pagina": self.pagina}


@dataclass(frozen=True, slots=True)
class SupplierRecord:
    """One supplier as a source states it, before any resolution.

    ``latitud``/``longitud`` are optional: when present, blocking is geographic
    (~110 m grid); when absent it falls back to the normalised address string,
    which is weaker and the result says so.
    """

    rut: str
    razon_social: str
    direccion: str | None = None
    comuna: str | None = None
    latitud: float | None = None
    longitud: float | None = None
    representante_rut: str | None = None
    representante_nombre: str | None = None
    documento_id: str | None = None
    pagina: int | None = None


@dataclass(frozen=True, slots=True)
class Relation:
    """A supported connection between suppliers — the output that goes to the graph."""

    kind: RelationKind
    anchor: str
    display: str
    suppliers: tuple[str, ...]
    evidence: tuple[SourceRef, ...] = field(default=())

    def to_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "anchor": self.anchor,
            "display": self.display,
            "suppliers": list(self.suppliers),
            "supplier_count": len(self.suppliers),
            "evidence": [ref.to_dict() for ref in self.evidence],
        }


@dataclass(frozen=True, slots=True)
class Candidate:
    """Same normalised name, different RUT, no shared person or address.

    This is exactly where a naive system merges two companies. Here it becomes a
    question for a human, with the reason written down.
    """

    nombre_key: str
    ruts: tuple[str, ...]
    reason: str = "mismo nombre normalizado, RUT distinto, sin persona ni domicilio en comun"

    def to_dict(self) -> dict:
        return {
            "nombre_key": self.nombre_key,
            "ruts": list(self.ruts),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class Rejected:
    """A record that cannot take part, and why."""

    rut: str
    reason: Rejection

    def to_dict(self) -> dict:
        return {"rut": self.rut, "reason": self.reason.value}


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    """Everything one resolution pass produced, in a stable order."""

    relations: tuple[Relation, ...]
    candidates: tuple[Candidate, ...]
    rejected: tuple[Rejected, ...]

    @property
    def linked_pairs(self) -> set[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        for relation in self.relations:
            for index, left in enumerate(relation.suppliers):
                for right in relation.suppliers[index + 1 :]:
                    pairs.add(tuple(sorted((left, right))))  # type: ignore[arg-type]
        return pairs

    def to_dict(self) -> dict:
        return {
            "relations": [relation.to_dict() for relation in self.relations],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "rejected": [item.to_dict() for item in self.rejected],
            "counts": {
                "relations": len(self.relations),
                "candidates": len(self.candidates),
                "rejected": len(self.rejected),
            },
        }
