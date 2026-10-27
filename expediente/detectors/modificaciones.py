"""Detectors 4 and 5 — a contract that changed after it was awarded, or died.

Two rules, both readable straight off a purchase order's dates:

- **modificada**: ``fecha_ultima_modificacion`` is more than ``min_dias`` after the
  order date. A same-week correction is housekeeping; a change signed two months
  later is a different fact about the record.
- **cancelada**: the order carries a ``fecha_cancelacion`` at all. An order that was
  issued and then cancelled is worth a line, especially when its siblings were not.

Without the day threshold this detector would flag almost every record and teach the
reviewer to ignore it, which is worse than not shipping it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..domain.models import OrdenCompra

DETECTOR = "modificaciones"
VERSION = "0.1.0"

DEFAULT_MIN_DIAS = 30


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str  # "modificada" | "cancelada"
    order_code: str
    organismo: str
    proveedor: str
    monto_clp: int
    fecha_emision: str
    fecha_cambio: str
    dias: int
    detector: str = DETECTOR
    version: str = VERSION

    @property
    def order_codes(self) -> tuple[str, ...]:
        return (self.order_code,)

    def explanation(self) -> str:
        if self.kind == "cancelada":
            return (
                f"La orden {self.order_code} de {self.organismo} a {self.proveedor} "
                f"fue cancelada {self.dias} días después de su emisión "
                f"({self.fecha_emision} → {self.fecha_cambio})."
            )
        return (
            f"La orden {self.order_code} de {self.organismo} a {self.proveedor} "
            f"fue modificada {self.dias} días después de su emisión "
            f"({self.fecha_emision} → {self.fecha_cambio}), por "
            f"${self.monto_clp:,}.".replace(",", ".")
        )

    def to_dict(self) -> dict:
        return {
            "detector": self.detector,
            "version": self.version,
            "kind": self.kind,
            "order_code": self.order_code,
            "organismo": self.organismo,
            "proveedor": self.proveedor,
            "monto_clp": self.monto_clp,
            "fecha_emision": self.fecha_emision,
            "fecha_cambio": self.fecha_cambio,
            "dias": self.dias,
            "explanation": self.explanation(),
        }


def detect_modificaciones(
    orders: Iterable[OrdenCompra], *, min_dias: int = DEFAULT_MIN_DIAS
) -> list[Finding]:
    """Return cancellations and late modifications, most recent change first."""
    findings: list[Finding] = []

    for order in orders:
        if order.fecha_cancelacion is not None:
            findings.append(_finding("cancelada", order, order.fecha_cancelacion))
            continue

        change = order.fecha_ultima_modificacion
        if change is None or change == order.fecha:
            continue
        if (change - order.fecha).days >= min_dias:
            findings.append(_finding("modificada", order, change))

    return sorted(findings, key=lambda item: (-item.dias, item.order_code))


def _finding(kind: str, order: OrdenCompra, change) -> Finding:
    return Finding(
        kind=kind,
        order_code=order.codigo,
        organismo=order.organismo.nombre,
        proveedor=order.proveedor.razon_social,
        monto_clp=order.monto_clp,
        fecha_emision=order.fecha.isoformat(),
        fecha_cambio=change.isoformat(),
        dias=(change - order.fecha).days,
    )
