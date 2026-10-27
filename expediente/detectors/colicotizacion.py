"""Detector 3 — suppliers that keep quoting together.

The rule: the same pair of suppliers appears in the quoting list of at least
``min_coocurrencias`` agile purchases. Co-bidding is normal; always co-bidding, in
the same buyer unit, is a pattern worth reading.

The data comes from ``proveedores_cotizando`` on the v2 detail payload, which is why
this detector needs no scraping: the list of who actually quoted is published.

A note on what this is not: co-occurrence is not collusion. Two suppliers can quote
together because they are the only two that sell the thing. The finding says how
often they appeared together and where — nothing else — and a human decides whether
that is ordinary or not.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations
from typing import Iterable

from ..domain.models import CompraAgil

DETECTOR = "colicotizacion"
VERSION = "0.1.0"

DEFAULT_MIN_COOCURRENCIAS = 2


@dataclass(frozen=True, slots=True)
class Finding:
    proveedor_a: str
    proveedor_b: str
    coincidencias: int
    organismos: tuple[str, ...]
    compra_codes: tuple[str, ...]
    detector: str = DETECTOR
    version: str = VERSION

    @property
    def order_codes(self) -> tuple[str, ...]:
        return self.compra_codes

    def explanation(self) -> str:
        return (
            f"Los proveedores {self.proveedor_a} y {self.proveedor_b} cotizaron juntos "
            f"en {self.coincidencias} compras ágiles"
            + (f" de {', '.join(self.organismos)}" if self.organismos else "")
            + "."
        )

    def to_dict(self) -> dict:
        return {
            "detector": self.detector,
            "version": self.version,
            "proveedor_a": self.proveedor_a,
            "proveedor_b": self.proveedor_b,
            "coincidencias": self.coincidencias,
            "organismos": list(self.organismos),
            "compra_codes": list(self.compra_codes),
            "explanation": self.explanation(),
        }


def detect_colicotizacion(
    compras: Iterable[CompraAgil], *, min_coocurrencias: int = DEFAULT_MIN_COOCURRENCIAS
) -> list[Finding]:
    """Return every supplier pair that quoted together at least ``min_coocurrencias`` times."""
    pairs: dict[tuple[str, str], list[CompraAgil]] = defaultdict(list)

    for compra in compras:
        ruts = sorted(set(compra.proveedores_cotizando))
        for left, right in combinations(ruts, 2):
            pairs[(left, right)].append(compra)

    findings = [
        Finding(
            proveedor_a=left,
            proveedor_b=right,
            coincidencias=len(group),
            organismos=tuple(sorted({compra.organismo.nombre for compra in group})),
            compra_codes=tuple(sorted(compra.codigo for compra in group)),
        )
        for (left, right), group in pairs.items()
        if len(group) >= min_coocurrencias
    ]
    return sorted(findings, key=lambda item: (-item.coincidencias, item.proveedor_a, item.proveedor_b))
