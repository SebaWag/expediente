"""Each detector is a rule. These tests are the rule's specification."""

from datetime import date

from expediente.detectors import REGISTRY, run_all
from expediente.detectors.colicotizacion import detect_colicotizacion
from expediente.detectors.concentracion import detect_concentracion
from expediente.detectors.modificaciones import detect_modificaciones
from expediente.domain.models import CompraAgil, OrdenCompra, Organismo, Proveedor

MUNICIPIO = Organismo(codigo="69.260.000-1", nombre="Municipalidad de Prueba")
OTRO = Organismo(codigo="69.260.000-2", nombre="Municipalidad Vecina")


def prov(rut: str) -> Proveedor:
    return Proveedor(rut_norm=rut, razon_social=f"Proveedor {rut}")


def order(code: str, monto: int, supplier: str = "11111111-1", *,
          organismo: Organismo = MUNICIPIO, day: int = 10,
          modificada: date | None = None, cancelada: date | None = None,
          procedencia: str | None = "Trato Directo") -> OrdenCompra:
    """A synthetic order. The mechanism defaults to a direct award because that is the
    population the splitting rule measures; without one there is no regime to apply."""
    return OrdenCompra(
        codigo=code, organismo=organismo, proveedor=prov(supplier), monto_clp=monto,
        fecha=date(2026, 10, day),
        fecha_ultima_modificacion=modificada, fecha_cancelacion=cancelada,
        procedencia=procedencia,
    )


# ------------------------------------------------------------------ concentración
def test_concentration_flags_a_single_supplier_taking_everything():
    orders = [order(f"OC-{i}", 1_000_000) for i in range(6)]

    findings = detect_concentracion(orders, hhi_umbral=2500, min_ordenes=5)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.hhi == 10_000.0
    assert finding.top_share == 1.0
    assert finding.n_proveedores == 1
    assert len(finding.order_codes) == 6


def test_concentration_does_not_flag_a_spread_buyer():
    orders = [order(f"OC-{i}", 1_000_000, supplier=f"{i}{i}{i}{i}{i}{i}{i}{i}-1")
              for i in range(1, 6)]

    assert detect_concentracion(orders, hhi_umbral=2500, min_ordenes=5) == []


def test_concentration_needs_enough_orders_to_mean_anything():
    orders = [order(f"OC-{i}", 1_000_000) for i in range(4)]
    assert detect_concentracion(orders, min_ordenes=5) == []


def test_concentration_is_per_buyer_unit():
    orders = [order(f"A-{i}", 1_000_000) for i in range(3)] + [
        order(f"B-{i}", 1_000_000, organismo=OTRO) for i in range(3)
    ]
    # neither unit alone reaches the minimum
    assert detect_concentracion(orders, min_ordenes=5) == []


# ------------------------------------------------------------------ modificaciones
def test_a_late_modification_is_flagged():
    orders = [order("OC-1", 500_000, modificada=date(2026, 12, 15), day=1)]

    findings = detect_modificaciones(orders, min_dias=30)

    assert len(findings) == 1
    assert findings[0].kind == "modificada"
    assert findings[0].dias == 75


def test_a_same_week_correction_is_not_flagged():
    orders = [order("OC-1", 500_000, modificada=date(2026, 10, 12), day=10)]
    assert detect_modificaciones(orders, min_dias=30) == []


def test_a_cancellation_is_flagged_regardless_of_how_long_it_took():
    orders = [order("OC-1", 500_000, cancelada=date(2026, 10, 11), day=10)]

    findings = detect_modificaciones(orders)

    assert findings[0].kind == "cancelada"
    assert "cancelada" in findings[0].explanation()


def test_an_order_without_changes_is_silent():
    assert detect_modificaciones([order("OC-1", 500_000)]) == []


# ------------------------------------------------------------------ co-licitación
def compra(code: str, ruts: tuple[str, ...], organismo: Organismo = MUNICIPIO) -> CompraAgil:
    return CompraAgil(
        codigo=code, organismo=organismo, monto_clp=500_000,
        fecha_publicacion=date(2026, 10, 5), proveedores_cotizando=ruts,
    )


def test_a_pair_that_always_quotes_together_is_flagged():
    compras = [
        compra("CA-1", ("11111111-1", "22222222-2", "33333333-3")),
        compra("CA-2", ("11111111-1", "22222222-2")),
    ]

    findings = detect_colicotizacion(compras, min_coocurrencias=2)

    assert len(findings) == 1
    assert findings[0].proveedor_a == "11111111-1"
    assert findings[0].proveedor_b == "22222222-2"
    assert findings[0].coincidencias == 2
    assert findings[0].compra_codes == ("CA-1", "CA-2")


def test_a_one_off_coincidence_is_not_flagged():
    compras = [compra("CA-1", ("11111111-1", "22222222-2")), compra("CA-2", ("11111111-1",))]
    assert detect_colicotizacion(compras, min_coocurrencias=2) == []


def test_quoting_alone_is_not_enough_to_be_a_pair():
    compras = [compra(f"CA-{i}", ("11111111-1",)) for i in range(3)]
    assert detect_colicotizacion(compras) == []


# ------------------------------------------------------------------ registro
def test_registry_declares_every_detector():
    assert set(REGISTRY) == {"fraccionamiento", "concentracion", "modificaciones", "colicotizacion"}


def test_run_all_returns_findings_from_every_rule_that_fires():
    orders = [order(f"OC-{i}", 1_000_000, modificada=date(2026, 12, 1), day=1 + i) for i in range(6)]
    compras = [compra("CA-1", ("11111111-1", "22222222-2")), compra("CA-2", ("11111111-1", "22222222-2"))]

    findings = run_all(orders, compras)

    assert {finding.detector for finding in findings} == {
        "fraccionamiento", "concentracion", "modificaciones", "colicotizacion",
    }
    assert all(finding.order_codes for finding in findings), "every finding must locate its evidence"
