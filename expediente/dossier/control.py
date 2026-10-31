"""The counterfactual that has to travel with any claim about a threshold.

A dossier that says "these seven orders stayed under the cap" is checkable. A dossier that
says "the buyer split the purchase to avoid the tender" needs something else: evidence that
the cap shaped the sizing. That evidence is a counterfactual, and the cheapest honest way to
produce it is to run the same rule against a **fake** cap and see whether the answer moves.

So the control is not a separate study that lives in a document somewhere. It is computed
over the same orders the dossier is about, at build time, and **printed inside the dossier**,
because the limit of a claim belongs next to the claim rather than in a footnote a reader has
to go find.

Measured on the five-year corpus (2,44 M orders, buyer-stratified): cutting the cap by 60 %
moves the chain count by 6 %. The rule counts repeated buying; it does not measure an
incentive to stay under a threshold. `docs/PLACEBO.md` carries the study; this module makes
every future dossier carry its own copy of the answer for its own population.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from ..detectors.fraccionamiento import detect_with_coverage
from ..domain.models import OrdenCompra

#: Factors around the real cap. 1.0 must be present: without a reference point the curve is
#: a set of numbers with nothing to be a fraction of.
DEFAULT_FACTORS: tuple[float, ...] = (0.4, 0.6, 0.8, 1.0, 1.25)

#: Above this share, the chain count is treated as cap-insensitive. 0,75 is not a law: it is
#: the point below which "halving the threshold halved the findings" becomes hard to argue
#: away, and the number lives here so a change to it is a change to a constant, not to a mood.
FLAT_THRESHOLD = 0.75


@dataclass(frozen=True, slots=True)
class ControlPoint:
    """One row of the curve: the rule run against one multiple of the real ceiling."""

    factor: float
    chains: int
    orders_cited: int
    total_clp: int

    def to_dict(self) -> dict:
        return {
            "factor": self.factor,
            "chains": self.chains,
            "orders_cited": self.orders_cited,
            "total_clp": self.total_clp,
        }


@dataclass(frozen=True, slots=True)
class PlaceboControl:
    """The curve, the population it was measured on, and the sentence it licenses."""

    population: str
    orders_examined: int
    points: tuple[ControlPoint, ...]

    @property
    def real(self) -> ControlPoint | None:
        """The point measured at the real ceiling. Everything else is a fraction of it."""
        for point in self.points:
            if point.factor == 1.0:
                return point
        return None

    @property
    def lowest(self) -> ControlPoint | None:
        return min(self.points, key=lambda point: point.factor) if self.points else None

    def share(self, factor: float) -> float | None:
        real = self.real
        if real is None or not real.chains:
            return None
        for point in self.points:
            if point.factor == factor:
                return point.chains / real.chains
        return None

    @property
    def flat(self) -> bool:
        """True when shrinking the cap does not shrink the findings.

        With no reference point, or no chains at all, the honest state is "nothing to be
        sensitive to" — and the reading below says that instead of inventing a verdict.
        """
        real, low = self.real, self.lowest
        if real is None or low is None or not real.chains:
            return True
        return low.chains / real.chains >= FLAT_THRESHOLD

    def reading(self) -> str:
        """The sentence a dossier may print. It is written to be quotable by an adversary."""
        real, low = self.real, self.lowest
        if real is None or low is None:
            return "No se pudo correr el control: falta el punto de referencia en el tope vigente."
        if not real.chains:
            return (
                "El control se corrió sobre esta población y no produjo cadenas: no hay "
                "afirmación sobre el umbral que rebajar ni que sostener."
            )

        share = low.chains / real.chains
        drop = 1 - low.factor
        if self.flat:
            return (
                f"Bajar el tope un {drop:.0%} mueve la cuenta de cadenas un {1 - share:.0%} "
                f"({low.chains:,} contra {real.chains:,} al tope vigente). Las cadenas no siguen "
                "al umbral: lo que este detector mide es compra repetida, no un incentivo a "
                "quedarse bajo el tope. El resultado es una lista de dónde empezar a revisar, "
                "no una prueba de fraccionamiento."
            )
        return (
            f"Bajar el tope un {drop:.0%} reduce las cadenas un {1 - share:.0%} "
            f"({low.chains:,} contra {real.chains:,}): la cuenta sí responde al umbral. Eso "
            "sostiene revisar el grupo con la hipótesis de dimensionamiento, no probarla: una "
            "cadena puede ser deliberada y otra puede ser casual."
        )

    def to_dict(self) -> dict:
        return {
            "population": self.population,
            "orders_examined": self.orders_examined,
            "points": [point.to_dict() for point in self.points],
            "flat": self.flat,
            "reading": self.reading(),
        }


def placebo_control(
    orders: Sequence[OrdenCompra],
    *,
    factors: Sequence[float] = DEFAULT_FACTORS,
    population: str = "Trato Directo (población por defecto del detector)",
) -> PlaceboControl:
    """Run the splitting rule once per factor over the orders a dossier is about.

    ``orders`` is a sequence, not an iterable: the curve needs the same population at every
    factor, and a generator would be consumed by the first pass. That requirement is the
    whole correctness of the control — comparing two different populations would produce a
    curve that means nothing while looking exactly like one that does.
    """
    if 1.0 not in factors:
        raise ValueError("la curva necesita el factor 1.0 como punto de referencia")

    points: list[ControlPoint] = []
    examined = 0
    for factor in factors:
        run = detect_with_coverage(orders, cap_factor=factor)
        examined = run.examinadas
        findings = run.findings
        points.append(
            ControlPoint(
                factor=factor,
                chains=len(findings),
                orders_cited=sum(len(finding.order_codes) for finding in findings),
                total_clp=sum(finding.total_clp for finding in findings),
            )
        )
    return PlaceboControl(population=population, orders_examined=examined, points=tuple(points))
