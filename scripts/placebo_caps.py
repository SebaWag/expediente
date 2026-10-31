#!/usr/bin/env python3
"""Placebo caps: does this rule depend on the threshold, or only count repetition?

The detector fires when one buyer sends ``min_orders`` or more orders to one supplier in one
month, each of them under the cap that applied that day. Two very different worlds produce
that pattern:

* **the cap matters** — orders are being sized to stay under it, so the rule is measuring
  an incentive to avoid a tender; or
* **the cap is scenery** — the same buyer simply buys often, and any ceiling would have
  produced the same chains.

The way to tell them apart is to run the *same* rule with a **fake cap** and see whether the
number moves. If the chains are stable at 0,6x and 0,8x of the real threshold, the second
reading is the correct one, and any sentence in a dossier of the form "sized to avoid the
threshold" would be unsupported. This script produces that curve so the claim can be
downgraded to what the data supports — a worklist of repeated purchases — instead of argued
after the fact.

Usage:
    python3 scripts/placebo_caps.py out/compras_5y.jsonl --limit 200000
    python3 scripts/placebo_caps.py out/compras_5y.jsonl --factors 0.5,0.75,1.0,1.25
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from expediente.detectors.fraccionamiento import detect_with_coverage  # noqa: E402

DEFAULT_FACTORS = (0.4, 0.6, 0.8, 0.9, 1.0, 1.1, 1.25)


def load_runner():
    """Reuse ``run_detectors.load_orders``: one reader for the corpus, not two."""
    path = ROOT / "scripts" / "run_detectors.py"
    spec = importlib.util.spec_from_file_location("run_detectors_for_placebo", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_detectors_for_placebo"] = module
    spec.loader.exec_module(module)
    return module


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("orders", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--factors", default=",".join(str(f) for f in DEFAULT_FACTORS))
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv[1:])

    factors = [float(f) for f in args.factors.split(",") if f.strip()]
    runner = load_runner()
    orders, stats = runner.load_orders(args.orders, args.limit)
    print(f"órdenes cargadas: {len(orders):,} · {stats}")

    results = []
    print(f"\n{'factor':>7}{'cadenas':>10}{'órdenes':>10}{'monto (M CLP)':>16}{'examinadas':>12}{'fuera de ventana':>18}")
    for factor in factors:
        started = time.monotonic()
        run = detect_with_coverage(orders, cap_factor=factor)
        findings = list(run.findings)
        codes = sum(len(f.order_codes) for f in findings)
        total = sum(f.total_clp for f in findings)
        elapsed = time.monotonic() - started
        row = {
            "factor": factor,
            "chains": len(findings),
            "orders_cited": codes,
            "total_clp": total,
            "examined": run.examinadas,
            "in_window": run.en_ventana,
            "seconds": round(elapsed, 2),
            "discarded": dict(getattr(run, "descartadas", {}) or {}),
        }
        results.append(row)
        print(
            f"{factor:>7.2f}{len(findings):>10,}{codes:>10,}{total / 1e6:>16,.0f}"
            f"{row['examined'] if row['examined'] is not None else 0:>12,}"
            f"{row['discarded'].get('SOBRE_EL_TOPE', 0):>18,}"
        )

    real = next((r for r in results if r["factor"] == 1.0), None)
    print()
    if real:
        print("Lectura: si las cadenas a 0,6x y 0,8x son una fracción parecida de las cadenas a 1,0x,")
        print("la regla está midiendo repetición de compras, no un incentivo a quedarse bajo el tope.")
        for row in results:
            if row["factor"] != 1.0 and real["chains"]:
                print(f"  factor {row['factor']:.2f}: {row['chains'] / real['chains']:.0%} de las cadenas reales")
    else:
        print("No se corrió el factor 1.00: la curva no tiene punto de referencia real.")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps({"orders": len(orders), "stats": stats, "results": results}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
