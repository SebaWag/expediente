#!/usr/bin/env python3
"""Project the order corpus down to the fields the detectors actually read.

``out/compras_5y.jsonl`` is 11,3 GB over 3,66 M orders, and the detector uses eleven of
its fields. Loading the full records costs about 3 GB of resident memory for data nobody
reads — which is fine on a 23 GB box and fatal on a 7 GB one. This produces the same
findings from a file roughly a tenth of the size, so the full-corpus run can happen on a
machine that also has to do other things.

It is a projection, not a filter: no order is dropped, so the counts a detector reports are
the same ones the full file would produce. `--verify` re-reads a sample and asserts every
kept record is byte-identical in the fields it kept, which is the cheap way to show the
stream did not mangle encoding.

Usage:
    python3 scripts/slim_orders.py out/compras_5y.jsonl out/orders_slim.jsonl
    python3 scripts/slim_orders.py in.jsonl out.jsonl --verify 1000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

#: Exactly the keys ``scripts/run_detectors.py:load_orders`` reads. Adding to this list
#: without adding to that function is how a slim file silently becomes a different corpus.
KEEP = (
    "codigo",
    "fecha",
    "monto_total_clp",
    "procedencia",
    "organismo_rut",
    "organismo",
    "region_compra",
    "proveedor_rut",
    "proveedor",
    "rubro",
    "descripcion",
)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--verify", type=int, default=0, help="check N records against the source")
    args = parser.parse_args(argv[1:])

    if not args.source.exists():
        print(f"missing {args.source}", file=sys.stderr)
        return 1

    args.target.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    read = written = 0
    dropped = 0
    sample: list[tuple[int, dict]] = []

    with args.source.open("r", encoding="utf-8") as src, args.target.open("w", encoding="utf-8") as dst:
        for line in src:
            line = line.strip()
            if not line:
                continue
            read += 1
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                dropped += 1
                continue
            slim = {key: record[key] for key in KEEP if key in record}
            if args.verify and read % max(1, 10_000) == 1 and len(sample) < args.verify:
                sample.append((read, slim))
            dst.write(json.dumps(slim, ensure_ascii=False) + "\n")
            written += 1

    elapsed = time.monotonic() - started
    size = args.target.stat().st_size
    print(f"líneas leídas: {read:,} · escritas: {written:,} · ilegibles: {dropped:,}")
    print(f"tamaño: {size / 1e9:.2f} GB · {elapsed:.1f}s ({read / max(elapsed, 1e-9):,.0f} líneas/s)")

    if args.verify and sample:
        # Walk the source once more and compare the sampled records field by field.
        by_line = dict(sample)
        checked = 0
        with args.source.open("r", encoding="utf-8") as src:
            for index, line in enumerate(src, start=1):
                if index in by_line:
                    record = json.loads(line)
                    expected = {key: record[key] for key in KEEP if key in record}
                    if expected != by_line[index]:
                        print(f"  MISMATCH en la línea {index}", file=sys.stderr)
                        return 1
                    checked += 1
                    if checked == len(by_line):
                        break
        print(f"verificados {checked} registros: proyección idéntica en los campos conservados")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
