"""Detector 2 — spend concentration.

The rule: within one buyer unit, the spend is distributed across suppliers with an
HHI above a threshold. HHI is the standard concentration index (sum of squared
percentage shares, 0–10.000): 10.000 is a single supplier taking everything, below
1.500 is a competitive market.

Why this rule and not a model: the index is a formula an auditor can recompute with
a spreadsheet, and the finding quotes the numbers it used. "HHI 5.040 over 6
suppliers, the largest taking 71%" is a statement about records. "Suspicious
concentration: 0.87" is not.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from ..domain.models import OrdenCompra
from .fraccionamiento import format_clp

DETECTOR = "concentracion"
VERSION = "0.1.0"

#: Above 2.500 the US DOJ/FTC treat a market as highly concentrated. We are not
#: measuring a market, only one buyer's spending, so this is a *reporting* threshold:
#: it decides what is worth a human's attention, not what is wrong.
DEFAULT_HHI_UMBRAL = 2500.0

#: Below this many orders, a buyer's spend is too small for a share to mean anything.
DEFAULT_MIN_ORDENES = 5


@dataclass(frozen=True, slots=True)
class Finding:
    organismo: str
    total_clp: int
    hhi: float
    hhi_umbral: float
    n_proveedores: int
    n_ordenes: int
    top_proveedor: str
    top_share: float
    order_codes: tuple[str, ...]
    detector: str = DETECTOR
    version: str = VERSION

    def explanation(self) -> str:
        return (
            f"{self.organismo} concentra el {self.top_share:.0%} de {format_clp(self.total_clp)} "
            f"en {self.top_proveedor}: HHI {self.hhi:.0f} sobre un umbral de "
            f"{self.hhi_umbral:.0f}, con {self.n_proveedores} proveedores en "
            f"{self.n_ordenes} órdenes."
        )

    def to_dict(self) -> dict:
        return {
            "detector": self.detector,
            "version": self.version,
            "organismo": self.organismo,
            "total_clp": self.total_clp,
            "hhi": round(self.hhi, 2),
            "hhi_umbral": self.hhi_umbral,
            "n_proveedores": self.n_proveedores,
            "n_ordenes": self.n_ordenes,
            "top_proveedor": self.top_proveedor,
            "top_share": round(self.top_share, 4),
            "order_codes": list(self.order_codes),
            "explanation": self.explanation(),
        }


def detect_concentracion(
    orders: Iterable[OrdenCompra],
    *,
    hhi_umbral: float = DEFAULT_HHI_UMBRAL,
    min_ordenes: int = DEFAULT_MIN_ORDENES,
) -> list[Finding]:
    """Return one finding per buyer unit whose spend is concentrated beyond the threshold."""
    by_organismo: dict[str, list[OrdenCompra]] = defaultdict(list)
    for order in orders:
        if order.monto_clp > 0:
            by_organismo[order.organismo.codigo].append(order)

    findings = [
        finding
        for group in by_organismo.values()
        if (finding := _concentracion_of(group, hhi_umbral, min_ordenes)) is not None
    ]
    return sorted(findings, key=lambda item: (-item.hhi, item.organismo))


def _concentracion_of(
    group: list[OrdenCompra], hhi_umbral: float, min_ordenes: int
) -> Finding | None:
    if len(group) < min_ordenes:
        return None

    total = sum(order.monto_clp for order in group)
    if total <= 0:
        return None

    by_supplier: dict[str, list[OrdenCompra]] = defaultdict(list)
    for order in group:
        by_supplier[order.proveedor.rut_norm].append(order)

    shares = {
        rut: sum(order.monto_clp for order in orders) / total
        for rut, orders in by_supplier.items()
    }
    hhi = sum((share * 100) ** 2 for share in shares.values())
    if hhi < hhi_umbral:
        return None

    top_rut = max(shares, key=lambda rut: (shares[rut], rut))
    top_orders = by_supplier[top_rut]

    return Finding(
        organismo=group[0].organismo.nombre,
        total_clp=total,
        hhi=hhi,
        hhi_umbral=hhi_umbral,
        n_proveedores=len(by_supplier),
        n_ordenes=len(group),
        top_proveedor=top_orders[0].proveedor.razon_social,
        top_share=shares[top_rut],
        order_codes=tuple(sorted(order.codigo for order in top_orders)),
    )
