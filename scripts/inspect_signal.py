#!/usr/bin/env python3
"""Open one finding and print the records behind it.

A finding is a claim, and a claim has to be openable. This is the smallest version of
what the dossier does: given a buyer, a supplier and a month, print the orders that the
detector grouped, with their amounts and descriptions, so a human can decide whether the
pattern is splitting or just a hospital buying supplies.

    python3 scripts/inspect_signal.py out/orders_5y.jsonl \
        --organismo "VALDIVIA" --proveedor "TECNOMEDICAL" --period 2021-12
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expediente.domain.normalizers import normalize_rut, parse_clp, parse_date, period_key


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--organismo", required=True)
    parser.add_argument("--proveedor", required=True)
    parser.add_argument("--period", required=True)
    parser.add_argument("--floor", type=int, default=30_000)
    parser.add_argument("--ceiling", type=int, default=1_000_000)
    args = parser.parse_args()

    needle_org = args.organismo.upper()
    needle_sup = args.proveedor.upper()
    selected = []

    with args.source.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            if needle_org not in (record.get("organismo") or "").upper():
                continue
            if needle_sup not in (record.get("proveedor") or "").upper():
                continue
            if (record.get("procedencia") or "") != "Trato Directo":
                continue
            fecha = parse_date(record.get("fecha"))
            monto = parse_clp(record.get("monto_total_clp"))
            if not fecha or not monto:
                continue
            if period_key(fecha) != args.period:
                continue
            if not (args.floor <= monto <= args.ceiling):
                continue
            selected.append((monto, record.get("codigo"), record.get("estado"), record.get("descripcion") or ""))

    selected.sort(reverse=True)
    print(f"{args.organismo} · {args.proveedor} · {args.period}")
    print(f"{len(selected)} órdenes de trato directo en la ventana "
          f"[{args.floor:,} – {args.ceiling:,}]\n")
    for monto, codigo, estado, descripcion in selected[:20]:
        print(f"  {monto:>9,}  {codigo:<22} {estado[:14]:<15} {descripcion[:70]}")
    if len(selected) > 20:
        print(f"  ... {len(selected) - 20} más")

    total = sum(m for m, *_ in selected)
    if selected:
        amounts = [m for m, *_ in selected]
        print(f"\n  total: CLP {total:,} = {total / args.ceiling:.1f}× el umbral")
        print(f"  media: CLP {total // len(selected):,} · "
              f"mínimo {min(amounts):,} · máximo {max(amounts):,}")
        near = sum(1 for m in amounts if m >= args.ceiling * 0.8)
        print(f"  a ≥80% del umbral: {near}/{len(amounts)} ({near / len(amounts) * 100:.0f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
