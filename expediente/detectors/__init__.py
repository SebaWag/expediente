"""Deterministic detectors.

A detector is a rule, not a score. Its output is a statement about records —
which orders, which months, which amounts, which document — because that is the
only kind of output a human can verify and sign.

Every detector satisfies the same small contract, which is what lets the pipeline
treat them uniformly: an identifier, a tuple of record codes that locate the
evidence, and a deterministic ``explanation()`` sentence.
"""

from __future__ import annotations

from typing import Iterable, Protocol

from ..domain.models import CompraAgil, OrdenCompra
from . import colicotizacion, concentracion, fraccionamiento, modificaciones


class DetectedPattern(Protocol):
    """What the pipeline needs from any detector's finding."""

    detector: str
    order_codes: tuple[str, ...]

    def explanation(self) -> str: ...


#: Every detector, in the order a dossier should present them: the pattern first,
#: then concentration, then suppliers in common, then what changed afterwards.
REGISTRY = {
    fraccionamiento.DETECTOR: fraccionamiento.detect_fraccionamiento,
    concentracion.DETECTOR: concentracion.detect_concentracion,
    modificaciones.DETECTOR: modificaciones.detect_modificaciones,
    colicotizacion.DETECTOR: colicotizacion.detect_colicotizacion,
}


def run_all(
    orders: Iterable[OrdenCompra],
    compras: Iterable[CompraAgil] = (),
) -> list[DetectedPattern]:
    """Run every detector and return the findings in registry order.

    ``orders`` is materialised once so four detectors do not re-walk the same list,
    and so the result does not depend on whether the caller passed a generator.
    """
    order_list = list(orders)
    compra_list = list(compras)

    findings: list[DetectedPattern] = []
    findings.extend(fraccionamiento.detect_fraccionamiento(order_list))
    findings.extend(concentracion.detect_concentracion(order_list))
    findings.extend(modificaciones.detect_modificaciones(order_list))
    if compra_list:
        findings.extend(colicotizacion.detect_colicotizacion(compra_list))
    return findings


__all__ = [
    "REGISTRY",
    "DetectedPattern",
    "colicotizacion",
    "concentracion",
    "fraccionamiento",
    "modificaciones",
    "run_all",
]
