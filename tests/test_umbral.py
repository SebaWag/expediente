"""The threshold has to be the one in force that month, and it has to be citable.

These tests are the reason ``1_000_000`` cannot come back: a fixed number passes every test
that only checks arithmetic. The ones below check dates, sources, and the answers that are
supposed to be *no answer*.
"""

from datetime import date

import pytest

from expediente.domain.umbral import (
    APLICA,
    DERIVADA,
    MES_SIN_UTM,
    NO_VERIFICADA,
    SIN_REGLA,
    MesSinUtm,
    normalizar_procedencia,
    resolver,
    utm_clp,
)


def test_utm_is_the_published_value_for_the_month():
    assert utm_clp(date(2021, 1, 5)) == 50_978
    assert utm_clp(date(2023, 12, 31)) == 64_216
    assert utm_clp(date(2024, 12, 1)) == 67_294
    assert utm_clp(date(2026, 10, 30)) == 72_151


def test_a_month_without_a_published_utm_is_an_error_and_not_last_months_value():
    with pytest.raises(MesSinUtm):
        utm_clp(date(2026, 12, 1))  # no publicada todavía
    with pytest.raises(MesSinUtm):
        utm_clp(date(2019, 5, 1))  # fuera de la tabla


def test_one_hundred_utm_in_october_2026_matches_the_figure_published_elsewhere():
    """Cross-check against a number that was not derived from this table.

    A public page states the 2026 Compra Ágil cap as "$7.215.100". That is 100 x 72.151,
    computed from the SII's own October value. Two independent sources, one number.
    """
    assert 100 * utm_clp(date(2026, 10, 1)) == 7_215_100


def test_the_fast_lane_cap_changed_mid_month_and_the_day_is_respected():
    antes = resolver(date(2024, 12, 11), "Compra Ágil")
    despues = resolver(date(2024, 12, 12), "Compra Ágil")

    assert antes.estado == APLICA and antes.unidades_utm == 30.0
    assert antes.clp == 30 * 67_294  # la UTM de diciembre, no la de noviembre
    assert despues.estado == APLICA and despues.unidades_utm == 100.0
    assert despues.clp == 100 * 67_294
    assert despues.clp > antes.clp * 3


def test_direct_award_before_the_reform_used_the_thousand_utm_ladder():
    """The law said 1.000 UTM. A first pass said 100 — a tenfold error in the findings."""
    resolucion = resolver(date(2023, 5, 1), "Trato Directo")

    assert resolucion.estado == APLICA
    assert resolucion.unidades_utm == 1000.0
    assert resolucion.clp == 1000 * 63_074  # UTM de mayo 2023
    assert "1.000 unidades tributarias" in resolucion.base_legal
    assert "no podrá fragmentar" in resolucion.base_legal


def test_direct_award_after_the_reform_is_judged_against_the_lane_it_bypassed():
    """The reform took the amount out of the law; it did not take the prohibition out."""
    resolucion = resolver(date(2025, 6, 1), "Trato Directo")

    assert resolucion.estado == APLICA
    assert resolucion.unidades_utm == 100.0
    assert resolucion.clp == 100 * 68_785
    assert DERIVADA in resolucion.nota
    assert "sanción" in resolucion.base_legal


def test_the_threshold_fell_tenfold_the_day_the_amount_left_the_law():
    antes = resolver(date(2024, 11, 1), "Trato Directo")
    despues = resolver(date(2024, 12, 20), "Trato Directo")

    assert antes.unidades_utm == 1000.0
    assert despues.unidades_utm == 100.0
    assert antes.clp == 1000 * 66_628
    assert despues.clp == 100 * 67_294


def test_a_rule_without_a_source_is_refused_by_the_schedule(monkeypatch):
    """The safeguard, exercised: an uncited rule does not quietly become a threshold."""
    from expediente.domain import umbral

    inventada = umbral.Regla(
        procedencia="compra agil",
        desde=date(2019, 1, 1),
        hasta=date(2019, 12, 31),
        unidades_utm=42.0,
        base_legal="",
        verificado=False,
    )
    monkeypatch.setattr(umbral, "REGLAS", (inventada,) + umbral.REGLAS)

    resolucion = umbral.resolver(date(2019, 6, 1), "Compra Ágil")

    assert resolucion.estado == NO_VERIFICADA
    assert resolucion.clp is None


def test_a_mechanism_the_schedule_does_not_model_gets_no_default():
    resolucion = resolver(date(2024, 5, 1), "Convenio Marco")

    assert resolucion.estado == SIN_REGLA
    assert resolucion.clp is None


def test_an_order_without_a_mechanism_is_not_assigned_the_most_generous_rule():
    assert resolver(date(2024, 5, 1), None).estado == SIN_REGLA
    assert resolver(date(2024, 5, 1), "").estado == SIN_REGLA
    assert resolver(None, "Compra Ágil").estado == SIN_REGLA


@pytest.mark.parametrize(
    "spelling",
    ["Compra Ágil", "compra agil", "COMPRA ÁGIL", "  Compra   Agil ", "Compra Ágil "],
)
def test_the_same_mechanism_written_five_ways_resolves_the_same(spelling):
    assert normalizar_procedencia(spelling) == "compra agil"
    assert resolver(date(2026, 1, 15), spelling).estado == APLICA


def test_mes_sin_utm_is_reported_rather_than_guessed():
    resolucion = resolver(date(2026, 12, 20), "Compra Ágil")

    assert resolucion.estado == MES_SIN_UTM
    assert resolucion.clp is None
