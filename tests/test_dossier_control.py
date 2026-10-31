"""A dossier that measures a threshold has to publish its counterfactual.

The splitting rule counts orders that stayed under a cap. Whether that count means
"avoided a tender" or "buys often" is decided by a second question: does the count follow
the cap? The control answers it on the dossier's own population and the dossier prints the
answer, because a limit stated in a footnote is a limit nobody reads.

Two populations are tested here on purpose. A control that can only say "the cap does not
matter" is not a control, it is a slogan — so one of these must come out cap-sensitive.
"""

from datetime import date

import pytest

from expediente.detectors.fraccionamiento import detect_fraccionamiento
from expediente.dossier.control import FLAT_THRESHOLD, PlaceboControl, ControlPoint, placebo_control
from expediente.dossier.models import Dossier
from expediente.domain.models import OrdenCompra, Organismo, Proveedor

MUNICIPIO = Organismo(codigo="69260000-1", nombre="Municipalidad de Prueba", comuna="Renca")
PROVEEDOR = Proveedor(rut_norm="11111111-1", razon_social="Proveedor Sintético SpA", nombre_key="proveedor sintetico spa")

#: Trato Directo cap on 2026-03-05 is 6.988.900 CLP (100 UTM).
CAP_MARZO_2026 = 6_988_900


def order(code: str, monto: int, day: int = 5) -> OrdenCompra:
    return OrdenCompra(
        codigo=code,
        organismo=MUNICIPIO,
        proveedor=PROVEEDOR,
        monto_clp=monto,
        fecha=date(2026, 3, day),
        procedencia="Trato Directo",
    )


def small_orders() -> list[OrdenCompra]:
    """Well under any cap, so no factor can exclude them: a cap-insensitive population."""
    return [order(f"small-{i}", 100_000, day=i + 1) for i in range(4)]


def sized_orders() -> list[OrdenCompra]:
    """Just under the real cap, so a fake cap of 40 % excludes every one of them."""
    return [order(f"sized-{i}", 6_000_000, day=i + 1) for i in range(4)]


def test_a_cap_insensitive_population_produces_a_flat_curve():
    control = placebo_control(small_orders())

    assert control.real is not None
    assert control.real.chains >= 1
    assert control.share(0.4) == pytest.approx(1.0)
    assert control.flat is True


def test_a_cap_sensitive_population_is_reported_as_sensitive():
    """The control must be able to disagree with us, or it is decoration."""
    control = placebo_control(sized_orders())

    assert control.real is not None
    assert control.real.chains >= 1
    assert control.share(0.4) == pytest.approx(0.0)
    assert control.flat is False


def test_the_flat_reading_refuses_the_strong_claim_in_words():
    reading = placebo_control(small_orders()).reading()

    assert "no una prueba de fraccionamiento" in reading
    assert "compra repetida" in reading


def test_the_sensitive_reading_does_not_overclaim_either():
    reading = placebo_control(sized_orders()).reading()

    assert "sí responde al umbral" in reading
    assert "no probarla" in reading


def test_a_curve_without_the_real_cap_is_refused():
    with pytest.raises(ValueError):
        placebo_control(small_orders(), factors=(0.5, 0.8))


def test_the_control_is_deterministic():
    first = placebo_control(small_orders()).to_dict()
    second = placebo_control(small_orders()).to_dict()

    assert first == second


def test_the_control_level_matches_the_detector_run_at_the_real_cap():
    orders = small_orders()

    control = placebo_control(orders)
    direct = detect_fraccionamiento(orders)

    assert control.real.chains == len(direct)


def dossier_with(control: PlaceboControl | None) -> Dossier:
    return Dossier(
        titulo="Expediente de prueba",
        sujeto="Municipalidad de Prueba",
        generado_en="2026-10-31T17:30:00-03:00",
        claims=(),
        rejected=(),
        thresholds={"min_ordenes": 3},
        detectors=("fraccionamiento",),
        control=control,
    )


def test_two_dossiers_with_different_controls_are_different_dossiers():
    flat = dossier_with(placebo_control(small_orders()))
    sensitive = dossier_with(placebo_control(sized_orders()))

    assert flat.id != sensitive.id, "el control cambia lo que el documento afirma: cambia su identidad"
    assert placebo_control(small_orders()) and dossier_with(placebo_control(small_orders())).id == flat.id


def test_a_dossier_without_a_control_is_not_the_same_dossier_as_one_with_it():
    assert dossier_with(None).id != dossier_with(placebo_control(small_orders())).id


def test_the_markdown_carries_the_curve_and_the_reading():
    rendered = dossier_with(placebo_control(small_orders())).to_markdown()

    assert "## Control de placebo" in rendered
    assert "factor del tope" in rendered
    assert "1.00x" in rendered
    assert "no una prueba de fraccionamiento" in rendered
    # The control must appear before the limitations: a limit buried at the end is decoration.
    assert rendered.index("## Control de placebo") < rendered.index("## Limitaciones")


def test_a_dossier_without_a_control_renders_no_control_section():
    assert "## Control de placebo" not in dossier_with(None).to_markdown()


def test_the_serialised_dossier_exposes_the_control():
    payload = dossier_with(placebo_control(small_orders())).to_dict()
    control = payload["control"]

    assert control["flat"] is True
    assert control["population"].startswith("Trato Directo")
    assert len(control["points"]) == 5
    assert {point["factor"] for point in control["points"]} >= {0.4, 1.0}


def test_the_flat_threshold_is_a_constant_not_a_mood():
    control = placebo_control(small_orders())
    observed = control.share(0.4)

    assert observed is not None
    assert (observed >= FLAT_THRESHOLD) is control.flat
