#!/usr/bin/env python3
"""Read the detector output and answer the only question that matters: is it signal?

A rule that returns 22,000 findings has not found 22,000 problems. This script buckets
the splitting findings by how far past the threshold they go, because that distribution
is what separates "three small orders happened to share a month" from "29 medical supply
orders, every one just under the ceiling".

    python3 scripts/analyze_signals.py out/signals [--top 15]
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

BUCKETS = (("≥10×", 10.0), ("5–10×", 5.0), ("3–5×", 3.0), ("1–3×", 1.0), ("<1×", 0.0))


def bucket(factor: float) -> str:
    for label, floor in BUCKETS:
        if factor >= floor:
            return label
    return "<1×"


def load(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("outdir", type=Path)
    parser.add_argument("--top", type=int, default=12)
    args = parser.parse_args()

    frac_path = args.outdir / "signals_fraccionamiento.jsonl"
    if frac_path.exists():
        rows = load(frac_path)
        counts = Counter(bucket(r["total_clp"] / r["ceiling_clp"]) for r in rows)
        print(f"FRACCIONAMIENTO · {len(rows):,} patrones")
        for label, _floor in BUCKETS:
            bar = "█" * min(40, round(counts[label] / max(1, len(rows)) * 80))
            print(f"  {label:<6} {counts[label]:>7,}  {bar}")
        material = [r for r in rows if r["total_clp"] / r["ceiling_clp"] >= 3]
        print(f"  materiales (≥3× el umbral): {len(material):,}")
        kinds = Counter(r.get("pattern_kind") or "sin_clasificar" for r in material)
        for kind, count in kinds.most_common():
            print(f"    {kind:<16} {count:>7,}")
        same_object = [r for r in material if r.get("pattern_kind") == "mismo_objeto"]
        print(f"  mismo objeto (similitud ≥0,6): {len(same_object):,}")
        print(f"\n  TOP {args.top} por múltiplo del umbral:")
        ordered = sorted(
            material,
            key=lambda r: (-(r.get("pattern_kind") == "mismo_objeto"), -r["total_clp"] / r["ceiling_clp"]),
        )
        for row in ordered[: args.top]:
            codes = row.get("order_codes_total") or len(row.get("order_codes", []))
            print(
                f"    {row['total_clp'] / row['ceiling_clp']:>6.1f}× · {codes:>3} OC · "
                f"sim {row.get('desc_similarity', 0):.2f} · {row.get('pattern_kind', '?')[:12]:<13} · "
                f"{row['organismo'][:28]:30} · {row['proveedor'][:20]:22} · {row['period']}"
            )

    conc_path = args.outdir / "signals_concentracion.jsonl"
    if conc_path.exists():
        rows = load(conc_path)
        print(f"\nCONCENTRACIÓN · {len(rows):,} patrones")
        below = sum(1 for r in rows if r["hhi"] >= 10_000)
        print(f"  HHI 10.000 (un solo proveedor): {below:,}")
        material = [r for r in rows if r["total_clp"] >= 1_000_000_000]
        print(f"  materiales (≥ CLP 1.000 M de gasto): {len(material):,}")
        print(f"\n  TOP {args.top} por monto involucrado:")
        for row in sorted(material, key=lambda r: -r["total_clp"])[: args.top]:
            print(
                f"    CLP {row['total_clp'] / 1e9:>6.1f} mil M · HHI {row['hhi']:>5} · "
                f"{row['n_proveedores']:>3} prov · top {row['top_share'] * 100:>3.0f}% · "
                f"{row['organismo'][:30]:32} · {row['top_proveedor'][:22]}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
