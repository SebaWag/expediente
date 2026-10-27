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
