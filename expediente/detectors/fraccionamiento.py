"""Detector 1 — purchase splitting below the threshold that was in force that month.

The rule: when one buyer unit issues ``min_orders`` or more purchase orders to the same
supplier inside a single calendar month, and every one of those orders sits under the amount
that would have forced a competitive procedure *on the day it was created*, the total may be
many times the threshold and no tender was ever run.

Why this rule and not a model: the output has to survive an auditor reading it. "Seven
orders, none above the threshold, together 6.4x the threshold, here are their codes" is
checkable by hand in a minute. A probability is not.

## The threshold is not a constant, and that is the whole difficulty

This detector shipped with ``ceiling_clp = 1_000_000`` for every order in every year. That
number was not a legal threshold: it was below every one of them (30 UTM in 2021 is about
$1.53M, 100 UTM in 2026 about $7.22M), so every order between it and the real limit was
excluded from the window before the rule was ever applied. A recall hole with no error
message. :func:`expediente.domain.umbral.resolver` replaces it: it returns the cap for the
order's own month and mechanism, or a named reason there is not one.

The reason matters for the largest period of the corpus. From 12 December 2024 direct
contracting is decided by a causal and has **no amount limit**, so for those orders the
answer is ``SIN_TOPE`` and not a number: there is no threshold to be under, and a rule built
on being under one does not apply. Those orders are counted and reported, not dropped in
silence — a detector that hides its rejections can always be "improved" by rejecting more.
What replaces this rule after the reform is repetition of an exceptional procedure, which is
a different family (``trato_directo_reiterado``) and a different claim.

## Scope

Only orders individually between ``floor_clp`` and their own ceiling participate. Those are
the ones that avoided a procedure; a large order alongside them does not excuse them, so it
is simply not part of the group.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from statistics import median
from typing import Iterable, Sequence

from ..domain.models import OrdenCompra
from ..domain.normalizers import period_key
from ..domain.umbral import (
    APLICA,
    MES_SIN_UTM,
    NO_VERIFICADA,
    SIN_REGLA,
    SIN_TOPE,
    Resolucion,
    normalizar_procedencia,
    resolver,
)

DETECTOR = "fraccionamiento"
VERSION = "0.4.0"

#: The fixed ceiling the first version of this detector used, kept under the name of what it
#: is so an old run can be reproduced and compared. It was not 30, 100 or 1,000 units of
#: anything, and it must never be a default again: a threshold in pesos expires, a threshold
#: in a table of dates does not. See :mod:`expediente.domain.umbral`.
LEGACY_CEILING_CLP = 1_000_000

#: Kept for the imports that already point here. It is the legacy value, not the default.
DEFAULT_CEILING_CLP = LEGACY_CEILING_CLP

#: Below this, an order is a petty purchase and grouping it proves nothing.
DEFAULT_FLOOR_CLP = 30_000

DEFAULT_MIN_ORDERS = 3

#: Award mechanisms where an amount above the ceiling would have forced a competitive
#: procedure. Convenio Marco and Compra Ágil are framework purchases: they are not subject
#: to that threshold, so an order "just under" it proves nothing about them.
DIRECT_AWARD_PROCEDENCIAS = ("Trato Directo",)

#: Why an order did not enter the window, in the words the report will use. Every one of
#: these is a sentence about the corpus, so a reader can argue with the count.
EXPLICACION_CAUSA = {
    SIN_TOPE: "trato directo después del 12-dic-2024: no hay tope que evitar",
    NO_VERIFICADA: "la regla de monto que aplicaría no tiene fuente primaria: no se aplica",
    MES_SIN_UTM: "el mes de la orden no tiene UTM publicada en la tabla",
    SIN_REGLA: "mecanismo o fecha que el calendario no modela",
    "PROCEDENCIA": "mecanismo distinto del que se está midiendo",
    "BAJO_EL_PISO": "importe bajo el piso: compra menor, agruparla no prueba nada",
    "SOBRE_EL_TOPE": "importe sobre el tope vigente: no evitó un procedimiento, lo exigió",
    "SIN_IMPORTE": "orden sin importe",
    "SIN_FECHA": "orden sin fecha utilizable",
}


def format_clp(amount: int) -> str:
    """Format a CLP amount the way a Chilean reader expects: ``$1.234.567``."""
    return f"${amount:,}".replace(",", ".")


@dataclass(frozen=True, slots=True)
class Finding:
    """One detected pattern, with the orders that prove it.

    ``order_codes`` is the evidence locator: every code points at a real record that the
    dossier can open. A finding without codes is not a finding.

    ``ceiling_clp`` is the **largest** ceiling among the cited orders, so the sentence "none
    of these is above $X" stays true for every code it names even when the group straddles a
    change of regime (December 2024 has two ceilings in it). ``factor`` is then computed
    against that same number, which is the conservative reading: it never overstates how far
    past the threshold the group went.
    """

    organismo: str
    proveedor: str
    period: str
    order_codes: tuple[str, ...]
    total_clp: int
    ceiling_clp: int
    #: Evidence about the *shape* of the group, not decoration. Two populations produce a
    #: high factor and they must not be read the same way: the same object bought repeatedly
    #: (splitting) and one order per case at an identical amount (a programme). The first is
    #: a procurement practice, the second is a queue.
    n_orders: int = 0
    median_amount_clp: int = 0
    share_near_ceiling: float = 0.0
    share_identical_amount: float = 0.0
    desc_similarity: float = 0.0
    #: Where the ceiling came from. A finding that cannot name its own threshold is a number
    #: with a story attached.
    unidades_utm: float | None = None
    utm_clp: int | None = None
    base_legal: str = ""
    ceiling_varies: bool = False
    detector: str = DETECTOR
    version: str = VERSION

    @property
    def factor(self) -> float:
        """How many times the threshold the group adds up to."""
        return self.total_clp / self.ceiling_clp if self.ceiling_clp else 0.0

    def pattern_kind(self) -> str:
        """What the shape of the group suggests, in one of three words.

        This is the difference between a finding and a queue. ``mismo_objeto`` means the
        descriptions agree: one thing bought over and over just under the ceiling.
        ``por_caso`` means the amounts are identical but the descriptions differ — one order
        per patient, per case, per file — which is a programme, not a split.
        ``indeterminado`` is said out loud rather than guessed, because a reviewer who is
        told "indeterminado" goes and looks, and one who is told "fraccionamiento" signs.
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

        Amounts are formatted with Chilean separators by :func:`format_clp`. A blanket
        ``replace(",", ".")`` over the whole sentence would also turn its punctuation into
        periods — which is how a "finding" ends up reading like three sentence fragments.

        When the ceiling is expressed in UTM the sentence says so, because "the threshold"
        is not one number across a corpus that spans six years of it.
        """
        orders = len(self.order_codes)
        umbral = format_clp(self.ceiling_clp)
        if self.unidades_utm:
            unidad = f"{self.unidades_utm:g} UTM"
            detalle = f"{umbral} ({unidad} del mes)" if not self.ceiling_varies else f"{umbral} (máximo de los umbrales del mes)"
        else:
            detalle = umbral
        return (
            f"{orders} órdenes de compra de {self.organismo} a {self.proveedor} "
            f"en {self.period}, ninguna por sobre {detalle}, "
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
            "ceiling_varies": self.ceiling_varies,
            "unidades_utm": self.unidades_utm,
            "utm_clp": self.utm_clp,
            "base_legal": self.base_legal,
            "factor": round(self.factor, 3),
            "pattern_kind": self.pattern_kind(),
            "explanation": self.explanation(),
        }


@dataclass(frozen=True, slots=True)
class DetectionRun:
    """The findings and, with the same weight, what did not become one.

    ``descartadas`` is the part that makes the number auditable: a count of orders excluded
    for each named reason. The same run under a different threshold can then be compared
    honestly, because the cost of a narrower rule is visible instead of implicit.
    """

    findings: tuple[Finding, ...]
    examinadas: int
    en_ventana: int
    descartadas: dict[str, int] = field(default_factory=dict)
    #: Which regimes answered the question and how many orders each answered for.
    por_estado: dict[str, int] = field(default_factory=dict)

    def report(self) -> str:
        lines = [
            f"{self.examinadas:,} órdenes examinadas · {self.en_ventana:,} dentro de la ventana",
            f"{len(self.findings):,} patrones",
        ]
        if self.descartadas:
            lines.append("descartadas por causa:")
            for causa, cuantos in sorted(self.descartadas.items(), key=lambda pair: -pair[1]):
                razon = EXPLICACION_CAUSA.get(causa, causa)
                lines.append(f"  {causa:<16} {cuantos:>10,}  {razon}")
        return "\n".join(lines)


def detect_fraccionamiento(
    orders: Iterable[OrdenCompra],
    *,
    ceiling_clp: int | None = None,
    floor_clp: int = DEFAULT_FLOOR_CLP,
    min_orders: int = DEFAULT_MIN_ORDERS,
    procedencias: tuple[str, ...] | None = DIRECT_AWARD_PROCEDENCIAS,
) -> list[Finding]:
    """Return every splitting pattern found, ordered by how far past the threshold it goes.

    ``ceiling_clp=None`` — the default — asks :func:`resolver` for the ceiling that applied
    to each order's own date and mechanism. Passing an ``int`` imposes one fixed ceiling on
    every order: that is what you want to reproduce a run from before this question was
    asked, or to measure a hypothetical, and it is not what you want to report.

    ``procedencias`` restricts the population to the mechanisms being measured. Passing
    ``None`` measures every mechanism and is kept for comparison.
    """
    run = detect_with_coverage(
        orders,
        ceiling_clp=ceiling_clp,
        floor_clp=floor_clp,
        min_orders=min_orders,
        procedencias=procedencias,
    )
    return list(run.findings)


def detect_with_coverage(
    orders: Iterable[OrdenCompra],
    *,
    ceiling_clp: int | None = None,
    floor_clp: int = DEFAULT_FLOOR_CLP,
    min_orders: int = DEFAULT_MIN_ORDERS,
    procedencias: tuple[str, ...] | None = DIRECT_AWARD_PROCEDENCIAS,
) -> DetectionRun:
    """Same rule, plus the count of what the rule refused to judge and why."""
    permitidas = (
        None
        if procedencias is None
        else {normalizar_procedencia(value) for value in procedencias}
    )
    groups: dict[tuple[str, str, str], list[tuple[OrdenCompra, int, Resolucion]]] = defaultdict(list)
    descartadas: Counter[str] = Counter()
    por_estado: Counter[str] = Counter()
    examinadas = 0
    en_ventana = 0

    for order in orders:
        examinadas += 1
        if order.monto_clp is None:
            descartadas["SIN_IMPORTE"] += 1
            continue
        # An order whose mechanism was never recorded stays in the population: not being
        # able to prove an exemption is not one. It will still be refused later by the
        # schedule, which is the place that knows it cannot pick a regime.
        clave = normalizar_procedencia(order.procedencia)
        if permitidas is not None and clave is not None and clave not in permitidas:
            descartadas["PROCEDENCIA"] += 1
            continue
        if order.monto_clp < floor_clp:
            descartadas["BAJO_EL_PISO"] += 1
            continue

        if ceiling_clp is None:
            resolucion = resolver(order.fecha, order.procedencia)
            por_estado[resolucion.estado] += 1
            if not resolucion.aplica:
                descartadas[resolucion.estado] += 1
                continue
            tope = resolucion.clp
        else:
            resolucion = Resolucion(
                estado=APLICA,
                clp=ceiling_clp,
                base_legal="tope fijo impuesto por el llamador: hipótesis, no la regla vigente",
            )
            por_estado[APLICA] += 1
            tope = ceiling_clp

        if order.monto_clp > tope:
            descartadas["SOBRE_EL_TOPE"] += 1
            continue

        period = period_key(order.fecha)
        if period is None:
            descartadas["SIN_FECHA"] += 1
            continue

        en_ventana += 1
        groups[(order.organismo.codigo, order.proveedor.rut_norm, period)].append(
            (order, tope, resolucion)
        )

    findings = [
        _build_finding(group)
        for group in groups.values()
        if len(group) >= min_orders
    ]
    findings.sort(key=lambda f: (-f.factor, f.organismo, f.proveedor, f.period))
    return DetectionRun(
        findings=tuple(findings),
        examinadas=examinadas,
        en_ventana=en_ventana,
        descartadas=dict(descartadas),
        por_estado=dict(por_estado),
    )


_TOKEN = re.compile(r"[A-ZÑ]{4,}")


def _tokens(text: str | None) -> set[str]:
    """Upper-case word tokens with accents stripped and numbers removed.

    Numbers carry the case identity ("PACIENTE: JESSICA P. P.") or the line item, not the
    object being bought, so keeping them would make two identical purchases look different
    because they mention different patients.
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


def _build_finding(group: Sequence[tuple[OrdenCompra, int, Resolucion]]) -> Finding:
    first, _, _ = group[0]
    orders = [order for order, _, _ in group]
    amounts = [order.monto_clp for order in orders]
    if not amounts:
        return Finding(
            organismo=first.organismo.nombre,
            proveedor=first.proveedor.razon_social,
            period=period_key(first.fecha) or "",
            order_codes=(),
            total_clp=0,
            ceiling_clp=0,
        )

    # The ceiling that holds for the whole group: the largest of the per-order ones, so the
    # sentence "none of these is above X" is true of every code it cites.
    tope, resolucion = max(
        ((tope, resolucion) for _, tope, resolucion in group),
        key=lambda pair: pair[0],
    )
    topes = {tope for _, tope, _ in group}

    counts: dict[int, int] = defaultdict(int)
    for amount in amounts:
        counts[amount] += 1
    near = sum(1 for amount in amounts if amount >= tope * 0.8)

    return Finding(
        organismo=first.organismo.nombre,
        proveedor=first.proveedor.razon_social,
        period=period_key(first.fecha) or "",
        order_codes=tuple(sorted(order.codigo for order in orders)),
        total_clp=sum(amounts),
        ceiling_clp=tope,
        n_orders=len(amounts),
        median_amount_clp=int(median(amounts)),
        share_near_ceiling=round(near / len(amounts), 4),
        share_identical_amount=round(max(counts.values()) / len(amounts), 4),
        desc_similarity=round(_description_similarity(orders), 4),
        unidades_utm=resolucion.unidades_utm,
        utm_clp=resolucion.utm,
        base_legal=resolucion.base_legal,
        ceiling_varies=len(topes) > 1,
    )
