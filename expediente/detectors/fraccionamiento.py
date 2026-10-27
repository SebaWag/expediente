"""Detector 1 — purchase splitting below the bidding threshold.

The rule: when one buyer unit issues ``min_orders`` or more purchase orders to
the same supplier inside a single calendar month, and every one of those orders
sits under the amount that would have forced a competitive tender, the total may
be many times the threshold and no tender was ever run.

Why this rule and not a model: the output has to survive an auditor reading it.
"Seven orders, none above the threshold, together 6.4x the threshold, here are
their codes" is checkable by hand in a minute. A probability is not.

Scope note: only orders individually between ``floor_clp`` and ``ceiling_clp``
participate. Those are the ones that avoided a tender; a large order alongside
them does not excuse them, so it is simply not part of the group.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Sequence

from ..domain.models import OrdenCompra
from ..domain.normalizers import period_key

DETECTOR = "fraccionamiento"
VERSION = "0.1.0"

#: Chilean unit-purchase threshold used by the demo. Configurable per organism:
#: the correct value is the one published for the year in the tender rules, and
#: making it a parameter is how the rule stays honest instead of magic.
DEFAULT_CEILING_CLP = 1_000_000

#: Below this, an order is a petty purchase and grouping it proves nothing.
DEFAULT_FLOOR_CLP = 30_000

DEFAULT_MIN_ORDERS = 3

def format_clp(amount: int) -> str:
    """Format a CLP amount the way a Chilean reader expects: ``$1.234.567``."""
    return f"${amount:,}".replace(",", ".")


@dataclass(frozen=True, slots=True)
class Finding:
    """One detected pattern, with the orders that prove it.

    ``order_codes`` is the evidence locator: every code points at a real record
    that the dossier can open. A finding without codes is not a finding.
    """

    organismo: str
    proveedor: str
    period: str
    order_codes: tuple[str, ...]
    total_clp: int
    ceiling_clp: int
    detector: str = DETECTOR
    version: str = VERSION

    @property
    def factor(self) -> float:
        """How many times the threshold the group adds up to."""
        return self.total_clp / self.ceiling_clp if self.ceiling_clp else 0.0

    def explanation(self) -> str:
        """Deterministic sentence for the dossier. No model is involved.

        Amounts are formatted with Chilean separators by :func:`format_clp`.
        A blanket ``replace(",", ".")`` over the whole sentence would also turn
        its punctuation into periods — which is how a "finding" ends up reading
        like three sentence fragments.
        """
        orders = len(self.order_codes)
        return (
            f"{orders} órdenes de compra de {self.organismo} a {self.proveedor} "
            f"en {self.period}, ninguna por sobre {format_clp(self.ceiling_clp)}, "
            f"suman {format_clp(self.total_clp)} ({self.factor:.1f}× el umbral)."
        )

    def to_dict(self) -> dict:
        return {
            "detector": self.detector,
            "version": self.version,
            "organismo": self.organismo,
            "proveedor": self.proveedor,
            "period": self.period,
            "order_codes": list(self.order_codes),
            "orders": len(self.order_codes),
            "total_clp": self.total_clp,
            "ceiling_clp": self.ceiling_clp,
            "factor": round(self.factor, 3),
            "explanation": self.explanation(),
        }


def detect_fraccionamiento(
    orders: Iterable[OrdenCompra],
    *,
    ceiling_clp: int = DEFAULT_CEILING_CLP,
    floor_clp: int = DEFAULT_FLOOR_CLP,
    min_orders: int = DEFAULT_MIN_ORDERS,
) -> list[Finding]:
    """Return every splitting pattern found, ordered by how far past the threshold it goes."""
    groups: dict[tuple[str, str, str], list[OrdenCompra]] = defaultdict(list)
    for order in orders:
        if not _inside_window(order.monto_clp, floor_clp, ceiling_clp):
            continue
        period = period_key(order.fecha)
        if period is None:
            continue
        groups[(order.organismo.codigo, order.proveedor.rut_norm, period)].append(order)

    findings = [
        _build_finding(group, ceiling_clp)
        for group in groups.values()
        if len(group) >= min_orders
    ]
    return sorted(findings, key=lambda f: (-f.factor, f.organismo, f.proveedor, f.period))


def _inside_window(monto: int | None, floor_clp: int, ceiling_clp: int) -> bool:
    return monto is not None and floor_clp <= monto <= ceiling_clp


def _build_finding(group: Sequence[OrdenCompra], ceiling_clp: int) -> Finding:
    first = group[0]
    return Finding(
        organismo=first.organismo.nombre,
        proveedor=first.proveedor.razon_social,
        period=period_key(first.fecha) or "",
        order_codes=tuple(sorted(order.codigo for order in group)),
        total_clp=sum(order.monto_clp for order in group),
        ceiling_clp=ceiling_clp,
    )
