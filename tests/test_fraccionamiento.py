"""The detector must be boring: same input, same findings, same order, every run."""

from datetime import date

from expediente.detectors.fraccionamiento import detect_fraccionamiento
from expediente.domain.models import OrdenCompra, Organismo, Proveedor

MUNICIPIO = Organismo(codigo="69.260.000-1", nombre="Municipalidad de Prueba", comuna="Renca")
PROVEEDOR_A = Proveedor(rut_norm="11111111-1", razon_social="Proveedor Sintético A SpA")
PROVEEDOR_B = Proveedor(rut_norm="22222222-2", razon_social="Proveedor Sintético B Ltda")


def order(code: str, monto: int, day: int, supplier: Proveedor = PROVEEDOR_A) -> OrdenCompra:
    return OrdenCompra(
        codigo=code,
        organismo=MUNICIPIO,
        proveedor=supplier,
        monto_clp=monto,
        fecha=date(2026, 10, day),
    )


def test_detects_splitting_pattern_with_the_orders_as_evidence():
    orders = [
        order("OC-1", 900_000, 3),
        order("OC-2", 850_000, 11),
        order("OC-3", 950_000, 19),
    ]

    findings = detect_fraccionamiento(orders, ceiling_clp=1_000_000, floor_clp=30_000)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.order_codes == ("OC-1", "OC-2", "OC-3")
    assert finding.total_clp == 2_700_000
    assert finding.period == "2026-10"
    assert round(finding.factor, 2) == 2.70


def test_requires_the_minimum_number_of_orders():
    orders = [order("OC-1", 900_000, 3), order("OC-2", 900_000, 4)]
    assert detect_fraccionamiento(orders, ceiling_clp=1_000_000) == []


def test_orders_above_the_ceiling_are_not_part_of_the_group():
    orders = [
        order("OC-1", 900_000, 3),
        order("OC-2", 900_000, 4),
        order("OC-BIG", 5_000_000, 5),
    ]
    assert detect_fraccionamiento(orders, ceiling_clp=1_000_000) == []


def test_orders_below_the_floor_are_ignored():
    orders = [
        order("OC-1", 900_000, 3),
        order("OC-2", 900_000, 4),
        order("OC-TINY", 5_000, 5),
    ]
    findings = detect_fraccionamiento(orders, ceiling_clp=1_000_000, floor_clp=30_000)
    assert findings == []


def test_different_months_are_not_aggregated():
    september = OrdenCompra(
        codigo="OC-SEP",
        organismo=MUNICIPIO,
        proveedor=PROVEEDOR_A,
        monto_clp=900_000,
        fecha=date(2026, 9, 28),
    )
    orders = [september, order("OC-1", 900_000, 3), order("OC-2", 900_000, 4)]
    assert detect_fraccionamiento(orders, ceiling_clp=1_000_000) == []


def test_findings_are_sorted_by_factor_descending():
    small = [order(f"A-{i}", 300_000, i) for i in (1, 2, 3)]
    large = [order(f"B-{i}", 990_000, i, supplier=PROVEEDOR_B) for i in (4, 5, 6)]

    findings = detect_fraccionamiento(small + large, ceiling_clp=1_000_000)

    assert [f.proveedor for f in findings] == [PROVEEDOR_B.razon_social, PROVEEDOR_A.razon_social]


def test_detection_is_deterministic():
    orders = [
        order("OC-3", 950_000, 19),
        order("OC-1", 900_000, 3),
        order("OC-2", 850_000, 11),
    ]
    first = [f.to_dict() for f in detect_fraccionamiento(orders, ceiling_clp=1_000_000)]
    second = [f.to_dict() for f in detect_fraccionamiento(reversed(orders), ceiling_clp=1_000_000)]
    assert first == second


def test_explanation_states_the_dates_amounts_and_threshold():
    orders = [order("OC-1", 900_000, 3), order("OC-2", 850_000, 11), order("OC-3", 950_000, 19)]
    finding = detect_fraccionamiento(orders, ceiling_clp=1_000_000)[0]

    text = finding.explanation()

    assert "3 órdenes de compra" in text
    assert "2026-10" in text
    assert "$2.700.000" in text
    assert "2.7× el umbral" in text


# ---------------------------------------------------------------------------
# La ventana dejó de ser una constante: mismas órdenes, distinto veredicto según la fecha.
# ---------------------------------------------------------------------------

from expediente.detectors.fraccionamiento import (  # noqa: E402
    LEGACY_CEILING_CLP,
    detect_fraccionamiento,
    detect_with_coverage,
)


def agil(codigo: str, monto: int, fecha: date, supplier: Proveedor = PROVEEDOR_A) -> OrdenCompra:
    return OrdenCompra(
        codigo=codigo,
        organismo=MUNICIPIO,
        proveedor=supplier,
        monto_clp=monto,
        fecha=fecha,
        procedencia="Compra Ágil",
    )


def trato(codigo: str, monto: int, fecha: date) -> OrdenCompra:
    return OrdenCompra(
        codigo=codigo,
        organismo=MUNICIPIO,
        proveedor=PROVEEDOR_A,
        monto_clp=monto,
        fecha=fecha,
        procedencia="Trato Directo",
    )


def test_the_same_amounts_are_a_finding_now_and_not_in_2024():
    """$4.000.000 each: above the 30-UTM cap of 2024, below the 100-UTM cap of 2026.

    The orders are identical. Only the date moves. A detector with one fixed ceiling cannot
    tell these two situations apart, and one of them is not a splitting pattern.
    """
    viejas = [
        agil("OC-1", 4_000_000, date(2024, 6, 5)),
        agil("OC-2", 4_000_000, date(2024, 6, 12)),
        agil("OC-3", 4_000_000, date(2024, 6, 20)),
    ]
    nuevas = [
        agil("OC-4", 4_000_000, date(2026, 6, 5)),
        agil("OC-5", 4_000_000, date(2026, 6, 12)),
        agil("OC-6", 4_000_000, date(2026, 6, 20)),
    ]

    run_2024 = detect_with_coverage(viejas, procedencias=("Compra Ágil",))
    run_2026 = detect_with_coverage(nuevas, procedencias=("Compra Ágil",))

    assert run_2024.findings == ()
    assert run_2024.descartadas["SOBRE_EL_TOPE"] == 3  # no en silencio: contadas
    assert len(run_2026.findings) == 1
    finding = run_2026.findings[0]
    assert finding.ceiling_clp == 100 * 71_506
    assert finding.unidades_utm == 100.0
    assert "unidades tributarias mensuales" in finding.base_legal
    assert round(finding.factor, 2) == round(12_000_000 / 7_150_600, 2)


def test_the_legacy_constant_is_kept_only_as_a_hypothesis():
    """Reproducing an old run must be possible; reporting it must not be the default."""
    orders = [
        agil("OC-1", 900_000, date(2024, 6, 5)),
        agil("OC-2", 900_000, date(2024, 6, 12)),
        agil("OC-3", 900_000, date(2024, 6, 20)),
    ]

    con_legado = detect_fraccionamiento(
        orders, ceiling_clp=LEGACY_CEILING_CLP, procedencias=("Compra Ágil",)
    )
    con_regla = detect_fraccionamiento(orders, procedencias=("Compra Ágil",))

    assert len(con_legado) == 1
    assert con_legado[0].unidades_utm is None  # el tope no vino de ninguna regla
    assert len(con_regla) == 1
    assert con_regla[0].unidades_utm == 30.0  # junio de 2024: 30 UTM
    assert con_regla[0].ceiling_clp == 30 * 65_770


def test_before_the_reform_the_window_was_a_thousand_utm_and_the_old_constant_missed_it():
    """The regression test for the recall hole.

    Three orders of $25.000.000 each in June 2023: every one of them under the 1.000 UTM that
    made a public tender mandatory, together $75.000.000. The fixed $1.000.000 ceiling saw
    none of them, because all three were 25 times above it.
    """
    orders = [
        trato("OC-1", 25_000_000, date(2023, 6, 5)),
        trato("OC-2", 25_000_000, date(2023, 6, 12)),
        trato("OC-3", 25_000_000, date(2023, 6, 20)),
    ]

    con_regla = detect_fraccionamiento(orders)
    con_legado = detect_fraccionamiento(orders, ceiling_clp=LEGACY_CEILING_CLP)

    assert len(con_regla) == 1
    assert con_regla[0].ceiling_clp == 1000 * 63_263
    assert con_regla[0].unidades_utm == 1000.0
    assert con_regla[0].total_clp == 75_000_000
    assert round(con_regla[0].factor, 2) == round(75_000_000 / 63_263_000, 2)
    assert con_legado == []


def test_after_the_reform_the_fast_lane_is_the_lane_that_was_bypassed():
    orders = [
        trato("OC-1", 900_000, date(2025, 6, 5)),
        trato("OC-2", 900_000, date(2025, 6, 12)),
        trato("OC-3", 900_000, date(2025, 6, 20)),
    ]

    findings = detect_fraccionamiento(orders)

    assert len(findings) == 1
    assert findings[0].ceiling_clp == 100 * 68_785
    assert "sanción" in findings[0].base_legal


def test_above_the_fast_lane_a_single_order_is_another_family_and_is_counted_as_such():
    """$8.000.000 by direct award is not a split: it is one purchase that needed a tender."""
    orders = [
        trato("OC-1", 8_000_000, date(2025, 6, 5)),
        trato("OC-2", 8_000_000, date(2025, 6, 12)),
        trato("OC-3", 8_000_000, date(2025, 6, 20)),
    ]

    run = detect_with_coverage(orders)

    assert run.findings == ()
    assert run.descartadas["SOBRE_EL_TOPE"] == 3
    assert "SOBRE_EL_TOPE" in run.report()


def test_a_group_that_straddles_the_change_reports_the_ceiling_that_holds_for_all_of_it():
    """December 2024 has two ceilings in it. The sentence must stay true for every code."""
    orders = [
        agil("OC-1", 1_500_000, date(2024, 12, 5)),   # tope 30 UTM
        agil("OC-2", 1_500_000, date(2024, 12, 20)),  # tope 100 UTM
        agil("OC-3", 1_500_000, date(2024, 12, 28)),  # tope 100 UTM
    ]

    findings = detect_fraccionamiento(orders, procedencias=("Compra Ágil",))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.ceiling_clp == 100 * 67_294  # el mayor: cierto para las tres
    assert finding.ceiling_varies is True
    assert "ninguna por sobre" in finding.explanation()


def test_an_order_without_a_mechanism_no_longer_rides_along_with_the_direct_awards():
    """The old code kept unknown procedencias. Without a mechanism there is no regime."""
    orders = [
        OrdenCompra(
            codigo="OC-1",
            organismo=MUNICIPIO,
            proveedor=PROVEEDOR_A,
            monto_clp=900_000,
            fecha=date(2023, 6, 5),
        )
    ] * 3

    run = detect_with_coverage(orders)

    assert run.findings == ()
    assert run.descartadas["SIN_REGLA"] == 3
