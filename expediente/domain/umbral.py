"""The amount that made a competitive procedure mandatory, as of the order's own date.

A threshold written in pesos is a threshold with an expiry date, and the code that reads it
rarely notices. ``DEFAULT_CEILING_CLP = 1_000_000`` was not 30, nor 100, nor 1,000 units of
anything: it was a round number roughly **fifty-three times** below the real limit for the
years in this corpus, so it excluded every order between itself and that limit before the
rule was ever applied — and it judged a 2021 order with a rule from 2026. This module
replaces it with a table in which every row carries the day it starts applying and the
sentence that says so.

Three things it refuses to do, because each one is a way of inventing a number:

1. **Interpolate.** The UTM is published monthly by the SII, and a month that is not in the
   table returns ``MES_SIN_UTM`` instead of last month's value. December 2026 has not been
   published yet; carrying November's forward would put a wrong number inside a finding,
   where nobody looks at it again.
2. **Apply a rule it cannot cite.** Every regime carries ``verificado`` and the sentence it
   rests on. A rule without one returns ``REGLA_NO_VERIFICADA`` and the detector counts that
   out loud, because a rule we cannot cite is a rule we do not apply.
3. **Pass a derived number off as a literal one.** Where the law states an amount, ``base_legal``
   quotes it. Where the amount follows from two literal rules — the fast-lane cap plus the
   prohibition on fragmenting to change procedure — the note says so, so a reader can tell
   which of the two they are looking at.

## What the law says, verbatim

Read on 2026-10-30 from LeyChile's consolidated text,
``leychile.cl/Consulta/obtxml?opt=7&idNorma=<id>`` — which needs a browser ``User-Agent``
(without one it answers 401) and accepts ``&idVersion=YYYY-MM-DD`` to return the text **as it
stood that day**. That last parameter is how the version in force before the reform was read
without guessing.

**The rule this whole detector rests on — prohibited in both regimes, and sanctioned in the
current one:**

* Pre-reform, Ley 19.886 artículo 7º, inciso final: *"La Administración **no podrá fragmentar
  sus contrataciones con el propósito de variar el procedimiento** de contratación."*
* Vigente: *"Los organismos del Estado no podrán fragmentar sus contrataciones con el
  propósito de variar el procedimiento de contratación. **La infracción de esta disposición
  tendrá como sanción** la señalada en el párrafo quinto del literal c) del artículo 8 bis…"*

**The thresholds, per regime:**

* Pre-reform, artículo 5º: *"La Administración adjudicará los contratos que celebre mediante
  licitación pública, licitación privada o contratación directa. La licitación pública será
  obligatoria cuando las contrataciones **superen las 1.000 unidades tributarias
  mensuales**, salvo lo dispuesto en el artículo 8º."*
* Vigente, artículo 5º: *"Los órganos del Estado adjudicarán los contratos que celebren
  mediante licitación pública. Excepcionalmente, y por un acto debidamente fundado y
  acreditado en la forma que señale el reglamento, podrán adjudicar contratos celebrados
  mediante licitación privada, trato directo o contratación excepcional directa con
  publicidad…"* — la regla general dejó de tener un monto.
* Compra Ágil, vigente, artículo 7 letra d) nº1: *"…pueden adquirir bienes y/o servicios por
  un monto igual o inferior a **100 unidades tributarias mensuales**, previa solicitud de al
  menos tres cotizaciones…"*
* Compra Ágil, antes: *"Se eleva el límite de la Compra Ágil de **30 a 100 UTM**"* —
  ChileCompra, ``chilecompra.cl/ley-compra-agil``.
* Reforma: Ley 21.634, publicada el 11-dic-2023, *"empieza a regir al año de publicarse la ley
  en el Diario Oficial, es decir 12 de diciembre de 2024"* — ChileCompra.

Two of these were found only after the first search failed. The strings ``"UTM"`` and even
``"U.T.M."`` appear **zero times** in Ley 19.886: the law writes *"unidades tributarias
mensuales"*. A search that greps the acronym concludes the law has no thresholds, which is
the wrong conclusion to draw from the right text. And a first pass concluded the pre-reform
threshold was 100 UTM; it was 1.000. The difference between those two numbers is the
difference between a finding and a tenth of one.

## What this module is not

It does not decide whether a purchase was legal. It answers a narrower question the detectors
need: on this date, under this mechanism, this was the amount that made a competitive
procedure mandatory. Whether the exception was justified is what the causal, the resolution
and the dossier are for.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date

#: The day the reform's main body started applying: Ley 21.634 was published on
#: 11 December 2023 and — per ChileCompra — *"empieza a regir al año de publicarse la ley en
#: el Diario Oficial, es decir 12 de diciembre de 2024"*. Everything before it was bought
#: under a rule that did not survive.
CAMBIO_DE_REGIMEN = date(2024, 12, 12)

#: UTM in pesos, one tuple per year, January first. Transcribed from the SII's own tables
#: (``sii.cl/valores_y_fechas/utm/utm<year>.htm``) on 2026-10-30. 2026 has eleven months
#: because December is not published until the last days of November, and a table that
#: guessed the last cell would be a table nobody could check.
UTM_POR_MES: dict[int, tuple[int, ...]] = {
    2021: (50978, 51131, 51489, 51592, 51798, 52005, 52161, 52213, 52631, 52842, 53476, 54171),
    2022: (54442, 54878, 55537, 55704, 56762, 57557, 58248, 58772, 59595, 60310, 60853, 61157),
    2023: (61769, 61954, 62450, 62388, 63074, 63263, 63326, 63199, 63452, 63515, 63960, 64216),
    2024: (64666, 64343, 64793, 65182, 65443, 65770, 65967, 65901, 66362, 66561, 66628, 67294),
    2025: (67429, 67294, 68034, 68306, 68648, 68785, 68923, 68647, 69265, 69265, 69542, 69542),
    2026: (69751, 69611, 69889, 69889, 70588, 71506, 71649, 71649, 71721, 72151, 72440),
}

UTM_FUENTE = "SII, «UTM - UTA - IPC» por año (sii.cl/valores_y_fechas/utm), consultado 2026-10-30"

#: Outcomes of asking this module a question. They are strings rather than exceptions
#: because the caller counts them: a detector that reports *why* it skipped an order can be
#: audited, and one that silently dropped it cannot.
APLICA = "APLICA"
SIN_TOPE = "SIN_TOPE"
NO_VERIFICADA = "REGLA_NO_VERIFICADA"
MES_SIN_UTM = "MES_SIN_UTM"
SIN_REGLA = "SIN_REGLA"

#: Marks the rows whose amount is not quoted anywhere but follows from two literal rules.
#: Kept as a word so a report can print it, instead of the reader having to trust the mix-up
#: of "literal" and "derived" that a single ``base_legal`` string would allow.
DERIVADA = "derivada de dos reglas literales"


class MesSinUtm(ValueError):
    """The UTM for that month is not published (or not transcribed) here.

    Raised instead of extrapolating. A wrong UTM is invisible: it changes a comparison by a
    few percent and the finding still reads the same.
    """


def utm_clp(fecha: date) -> int:
    """The UTM in pesos for the month of ``fecha``.

    The value that applies to an operation is the one published for the month in which it
    took place, which is why this takes a date and not a year.
    """
    meses = UTM_POR_MES.get(fecha.year)
    if meses is None:
        raise MesSinUtm(f"sin UTM transcrita para {fecha.year}")
    if not 1 <= fecha.month <= len(meses):
        raise MesSinUtm(f"sin UTM publicada para {fecha.year}-{fecha.month:02d}")
    return meses[fecha.month - 1]


def normalizar_procedencia(value: str | None) -> str | None:
    """Fold a mechanism name to the key the rules are written in.

    ``"Compra Ágil"`` arrives from the portal, ``"Compra Agil"`` from a CSV, and
    ``"COMPRA ÁGIL"`` from a spreadsheet someone typed by hand. They are one mechanism, and
    a lookup that treats them as three is a lookup that fails quietly.
    """
    if not value:
        return None
    descompuesto = unicodedata.normalize("NFKD", value)
    sin_tildes = "".join(char for char in descompuesto if not unicodedata.combining(char))
    limpio = " ".join(sin_tildes.lower().split())
    return limpio or None


@dataclass(frozen=True, slots=True)
class Regla:
    """One row of the schedule: from this day, under this mechanism, that was too much."""

    procedencia: str
    desde: date
    hasta: date | None
    #: ``None`` means the mechanism has no amount limit in this period — a legitimate
    #: answer, and a different one from "we do not know".
    unidades_utm: float | None
    base_legal: str
    verificado: bool
    nota: str = ""

    def cubre(self, fecha: date, procedencia: str) -> bool:
        if procedencia != self.procedencia:
            return False
        if fecha < self.desde:
            return False
        return self.hasta is None or fecha <= self.hasta


#: The schedule. Two mechanisms are modelled because the detectors only ever ask about two:
#: the low-value fast lane, which is where a cap creates an incentive to sit under it, and
#: direct contracting, which is where the exception is worth checking against the competitive
#: procedure that was available. Anything outside these is ``SIN_REGLA`` rather than a
#: default, because a default here is a number nobody signed.
REGLAS: tuple[Regla, ...] = (
    Regla(
        procedencia="compra agil",
        desde=date(2020, 4, 1),
        hasta=date(2024, 12, 11),
        unidades_utm=30.0,
        base_legal=(
            "ChileCompra, «Ley 21.634» (chilecompra.cl/ley-compra-agil): «Se eleva el límite "
            "de la Compra Ágil de 30 a 100 UTM». El tope vigente hasta el 11-dic-2024 era 30."
        ),
        verificado=True,
        nota="Fuente oficial, aunque la redacción literal del tope de 30 UTM no está en el "
        "texto consolidado actual: la reforma lo reemplazó.",
    ),
    Regla(
        procedencia="compra agil",
        desde=CAMBIO_DE_REGIMEN,
        hasta=None,
        unidades_utm=100.0,
        base_legal=(
            "Ley 19.886, art. 7 letra d) nº1 (texto vigente desde el 12-dic-2024): «...pueden "
            "adquirir bienes y/o servicios por un monto igual o inferior a 100 unidades "
            "tributarias mensuales, previa solicitud de al menos tres cotizaciones...»"
        ),
        verificado=True,
    ),
    Regla(
        procedencia="trato directo",
        desde=date(2004, 9, 24),
        hasta=date(2024, 12, 11),
        unidades_utm=1000.0,
        base_legal=(
            "Ley 19.886, art. 5º (texto al 11-dic-2024): «La licitación pública será "
            "obligatoria cuando las contrataciones superen las 1.000 unidades tributarias "
            "mensuales, salvo lo dispuesto en el artículo 8º». Y art. 7º, inciso final: «La "
            "Administración no podrá fragmentar sus contrataciones con el propósito de variar "
            "el procedimiento de contratación»."
        ),
        verificado=True,
    ),
    Regla(
        procedencia="trato directo",
        desde=CAMBIO_DE_REGIMEN,
        hasta=None,
        unidades_utm=100.0,
        base_legal=(
            "Ley 19.886 vigente, art. 7 letra d) nº1: «...por un monto igual o inferior a 100 "
            "unidades tributarias mensuales, previa solicitud de al menos tres cotizaciones». "
            "Y art. 7, sobre fragmentación: «Los organismos del Estado no podrán fragmentar "
            "sus contrataciones con el propósito de variar el procedimiento de contratación. "
            "La infracción de esta disposición tendrá como sanción...»."
        ),
        verificado=True,
        nota=(
            f"Cantidad {DERIVADA}: la ley ya no fija un monto para el trato directo (procede "
            "por causal), pero sí fija que bajo 100 UTM existía un procedimiento competitivo "
            "—la Compra Ágil, con tres cotizaciones— y prohíbe fragmentar para variar el "
            "procedimiento. Una orden bajo 100 UTM contratada por trato directo usó la "
            "excepción donde había una vía competitiva disponible."
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class Resolucion:
    """What the schedule knows about one order's date and mechanism.

    ``clp`` is only set when ``estado`` is ``APLICA``: everywhere else the absence is the
    answer, and a caller that reads ``clp`` anyway gets ``None`` instead of a number it
    should not use.
    """

    estado: str
    clp: int | None = None
    utm: int | None = None
    unidades_utm: float | None = None
    base_legal: str = ""
    nota: str = ""

    @property
    def aplica(self) -> bool:
        return self.estado == APLICA


def resolver(fecha: date | None, procedencia: str | None) -> Resolucion:
    """The threshold for one order, or the named reason there is not one.

    Returns ``SIN_REGLA`` for a mechanism the schedule does not model — Convenio Marco, a
    licitación — because those are not capped purchases and applying a cap to them is how a
    catalogue order becomes a "finding".
    """
    clave = normalizar_procedencia(procedencia)
    if fecha is None:
        return Resolucion(estado=SIN_REGLA, nota="sin fecha: no se sabe qué régimen aplicaba")
    if clave is None:
        return Resolucion(
            estado=SIN_REGLA,
            nota="sin procedencia: no se puede elegir el régimen que correspondía",
        )

    candidatas = [regla for regla in REGLAS if regla.cubre(fecha, clave)]
    if not candidatas:
        return Resolucion(
            estado=SIN_REGLA,
            nota=f"el calendario no modela «{clave}» en {fecha.isoformat()}",
        )

    regla = candidatas[0]
    if regla.unidades_utm is None:
        return Resolucion(
            estado=SIN_TOPE,
            base_legal=regla.base_legal,
            nota="este mecanismo no tiene tope de monto en este período: la regla de «por "
            "debajo del umbral» no aplica",
        )
    if not regla.verificado:
        return Resolucion(
            estado=NO_VERIFICADA,
            unidades_utm=regla.unidades_utm,
            base_legal=regla.base_legal,
            nota=regla.nota,
        )
    try:
        utm = utm_clp(fecha)
    except MesSinUtm as error:
        return Resolucion(estado=MES_SIN_UTM, base_legal=regla.base_legal, nota=str(error))

    return Resolucion(
        estado=APLICA,
        clp=int(round(regla.unidades_utm * utm)),
        utm=utm,
        unidades_utm=regla.unidades_utm,
        base_legal=regla.base_legal,
        nota=regla.nota,
    )


__all__ = [
    "APLICA",
    "CAMBIO_DE_REGIMEN",
    "DERIVADA",
    "MES_SIN_UTM",
    "MesSinUtm",
    "NO_VERIFICADA",
    "REGLAS",
    "Resolucion",
    "Regla",
    "SIN_REGLA",
    "SIN_TOPE",
    "UTM_FUENTE",
    "UTM_POR_MES",
    "normalizar_procedencia",
    "resolver",
    "utm_clp",
]
