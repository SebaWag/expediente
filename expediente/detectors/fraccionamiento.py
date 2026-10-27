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

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from statistics import median
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

#: Award mechanisms where an amount above the ceiling would have forced a competitive
#: tender. Convenio Marco and Compra Ágil are framework purchases: they are not subject
#: to that threshold, so an order "just under" it proves nothing about them. Orders with
#: an unknown procedencia are kept — not being able to prove an exemption is not one.
DIRECT_AWARD_PROCEDENCIAS = ("Trato Directo",)

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
    #: Evidence about the *shape* of the group, not decoration. Two populations produce
    #: a high factor and they must not be read the same way: the same object bought
    #: repeatedly (splitting) and one order per case at an identical amount (a
    #: programme). The first is a procurement practice, the second is a queue.
    n_orders: int = 0
    median_amount_clp: int = 0
    share_near_ceiling: float = 0.0
    share_identical_amount: float = 0.0
    desc_similarity: float = 0.0
    detector: str = DETECTOR
    version: str = "0.3.0"

    @property
    def factor(self) -> float:
        """How many times the threshold the group adds up to."""
        return self.total_clp / self.ceiling_clp if self.ceiling_clp else 0.0

    def pattern_kind(self) -> str:
        """What the shape of the group suggests, in one of three words.

        This is the difference between a finding and a queue. ``mismo_objeto`` means the
        descriptions agree: one thing bought over and over just under the ceiling.
        ``por_caso`` means the amounts are identical but the descriptions differ — one
        order per patient, per case, per file — which is a programme, not a split.
        ``indeterminado`` is said out loud rather than guessed, because a reviewer who
        is told "indeterminado" goes and looks, and one who is told "fraccionamiento"
        signs.
        """
        if self.desc_similarity >= 0.8:
            return "mismo_objeto"
        # Thresholds measured on the real corpus: per-patient descriptions share their
        # template ("RESOLUCION EXENTA PACIENTE ...") and score 0.43–0.60, while the same
        # object bought repeatedly scores 0.71–1.00. 0.8 is where "same description" and
        # "same template, different case" actually separate.
        if self.share_identical_amount >= 0.5 and self.desc_similarity < 0.8:
            return "por_caso"
        return "indeterminado"

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
    procedencias: tuple[str, ...] | None = DIRECT_AWARD_PROCEDENCIAS,
) -> list[Finding]:
    """Return every splitting pattern found, ordered by how far past the threshold it goes.

    ``procedencias`` restricts the population to the award mechanisms where the ceiling
    actually forces a tender. Passing ``None`` measures every mechanism and is kept for
    comparison, but the default is the rule as it should be read.
    """
    groups: dict[tuple[str, str, str], list[OrdenCompra]] = defaultdict(list)
    for order in orders:
        if not _inside_window(order.monto_clp, floor_clp, ceiling_clp):
            continue
        if procedencias is not None and order.procedencia and order.procedencia not in procedencias:
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


_TOKEN = re.compile(r"[A-ZÑ]{4,}")


def _tokens(text: str | None) -> set[str]:
    """Upper-case word tokens with accents stripped and numbers removed.

    Numbers carry the case identity ("PACIENTE: JESSICA P. P.") or the line item, not the
    object being bought, so keeping them would make two identical purchases look
    different because they mention different patients.
    """
    normalized = unicodedata.normalize("NFKD", (text or "").upper())
    stripped = "".join(char for char in normalized if not unicodedata.combining(char))
    return set(_TOKEN.findall(stripped))


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _description_similarity(orders: Sequence[OrdenCompra], sample: int = 20) -> float:
    """Median pairwise Jaccard over a sample of the group's descriptions."""
    tokens = [_tokens(order.descripcion) for order in orders[:sample]]
    pairs = [
        _jaccard(tokens[i], tokens[j])
        for i in range(len(tokens))
        for j in range(i + 1, len(tokens))
    ]
    return median(pairs) if pairs else 0.0


def _build_finding(group: Sequence[OrdenCompra], ceiling_clp: int) -> Finding:
    first = group[0]
    amounts = [order.monto_clp for order in group]
    if not amounts:
        return Finding(
            organismo=first.organismo.nombre,
            proveedor=first.proveedor.razon_social,
            period=period_key(first.fecha) or "",
            order_codes=(),
            total_clp=0,
            ceiling_clp=ceiling_clp,
        )

    counts: dict[int, int] = defaultdict(int)
    for amount in amounts:
        counts[amount] += 1
    near = sum(1 for amount in amounts if amount >= ceiling_clp * 0.8)

    return Finding(
        organismo=first.organismo.nombre,
        proveedor=first.proveedor.razon_social,
        period=period_key(first.fecha) or "",
        order_codes=tuple(sorted(order.codigo for order in group)),
        total_clp=sum(amounts),
        ceiling_clp=ceiling_clp,
        n_orders=len(amounts),
        median_amount_clp=int(median(amounts)),
        share_near_ceiling=round(near / len(amounts), 4),
        share_identical_amount=round(max(counts.values()) / len(amounts), 4),
        desc_similarity=round(_description_similarity(group), 4),
    )
