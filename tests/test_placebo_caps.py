"""What the placebo control says about detector 1, and what it forbids us to claim.

The rule fires on repetition under a cap. Two worlds produce that shape and they mean
opposite things, so the control is simple: run the same rule with a fake cap and watch the
number. If chains track the cap, the cap is doing work. If they ignore it, the rule is
counting frequent buying and the threshold is scenery.

Measured on 199.993 real orders (`scripts/placebo_caps.py --limit 200000`):

| cap factor | chains | share of real |
|---|---:|---:|
| 0,40x | 1.061 | 96 % |
| 0,60x | 1.074 | 97 % |
| 0,80x | 1.089 | 98 % |
| **1,00x** | **1.106** | 100 % |
| 1,25x | 1.108 | 100 % |

Cutting the threshold by 60 % moves the finding count by 4 %. The chains do not follow the
cap, therefore **this detector does not measure an incentive to stay under it.**

What it does measure, and what a dossier may say
------------------------------------------------
* Measured: *these orders exist, no single one exceeded the cap that applied on its date,
  together they are materially larger than it, and no tender was run for them.* Every code
  is a locator into a real record.
* Forbidden: *the buyer split the purchase to avoid the tender.* That requires evidence the
  cap shaped the sizing, and the control above says the data does not carry it.
* Also forbidden: reporting the chain count as an anomaly rate. It is a **worklist**: the
  first place a reviewer should look, ordered by materiality.

The consequence for the product is a downgrade, not a loss. "Where a review should start"
is a claim that survives an auditor reading the codes; "we detected evasion" is a claim that
dies the first time someone asks for the counterfactual. Rxyxs reached the same place from
the other direction: their bunching signal fell to ~2,6 points once they controlled for
buyer, and their README answers with the same sentence this module now insists on.

Why the control lives in code and not in a slide
------------------------------------------------
A placebo run is one flag (`cap_factor`) on the production detector, so it can be re-run on
the same corpus by anyone who doubts the number. A slide cannot be re-run.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from expediente.detectors.fraccionamiento import detect_fraccionamiento, detect_with_coverage  # noqa: E402
from expediente.domain.models import OrdenCompra, Organismo, Proveedor  # noqa: E402


#: The detector measures Trato Directo by default (``DIRECT_AWARD_PROCEDENCIAS``): the
#: splitting rule is about avoiding a tender, and that is the path where the choice exists.
#: Compra Ágil is a *different* population and is not in the default run.


def buyer(code="61602226-1") -> Organismo:
    return Organismo(codigo=code, nombre="Hospital Traiguén", comuna="Traiguén", region="Araucanía")


def supplier(rut="76150343-K") -> Proveedor:
    return Proveedor(rut_norm=rut, razon_social="INDURA S.A.", nombre_key="indura sa")


def order(codigo: str, monto: int, day: int = 5, procedencia: str = "Trato Directo") -> OrdenCompra:
    return OrdenCompra(
        codigo=codigo,
        organismo=buyer(),
        proveedor=supplier(),
        monto_clp=monto,
        fecha=date(2026, 3, day),
        procedencia=procedencia,
    )


def test_a_chain_of_small_orders_is_found_at_the_real_cap():
    orders = [order(f"c-{i}", 1_000_000) for i in range(4)]

    findings = detect_fraccionamiento(orders)

    assert len(findings) == 1
    assert len(findings[0].order_codes) == 4


def test_the_placebo_factor_actually_changes_the_ceiling():
    """If this ever stops being true, every placebo run silently becomes a no-op."""
    # Three orders: ``min_orders`` is 3, and a single order never becomes a finding.
    orders = [order(f"c-{i}", 1_000_000) for i in range(3)]

    run_real = detect_with_coverage(orders)
    run_placebo = detect_with_coverage(orders, cap_factor=0.4)

    real = run_real.findings[0].ceiling_clp
    placebo = run_placebo.findings[0].ceiling_clp

    assert placebo < real
    assert placebo == int(round(real * 0.4))


def test_a_placebo_finding_says_it_is_a_hypothesis_and_not_the_rule():
    orders = [order(f"c-{i}", 900_000) for i in range(4)]

    findings = detect_fraccionamiento(orders, cap_factor=0.6)

    assert findings, "el grupo debería seguir calificando bajo un tope más bajo"
    for finding in findings:
        legal = getattr(finding, "base_legal", "") or ""
        assert "placebo" in legal.lower()
        assert "no es la regla" in legal.lower()


def test_the_real_run_does_not_carry_the_placebo_label():
    orders = [order(f"c-{i}", 900_000) for i in range(4)]

    findings = detect_fraccionamiento(orders)

    assert findings
    assert "placebo" not in (getattr(findings[0], "base_legal", "") or "").lower()


def test_the_shipped_placebo_curve_is_flat_enough_to_forbid_the_strong_claim():
    """The measurement behind docs/PLACEBO.md, asserted so it cannot drift unnoticed.

    Reading the committed curve: halving the cap must not halve the chains. If a future
    change to the rule makes the count cap-sensitive, this fails and the claim in the docs
    has to be revisited -- which is the point of encoding it as a test.
    """
    import json

    curve = ROOT / "out" / "placebo_caps.json"
    if not curve.exists():
        import pytest

        pytest.skip("curva de placebo no generada en este entorno")

    data = json.loads(curve.read_text(encoding="utf-8"))
    by_factor = {row["factor"]: row["chains"] for row in data["results"]}
    real = by_factor[1.0]

    assert by_factor[0.8] / real > 0.9, "las cadenas deberían ignorar el tope"
    assert by_factor[0.6] / real > 0.9
