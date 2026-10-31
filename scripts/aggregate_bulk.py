#!/usr/bin/env python3
"""Aggregate ChileCompra bulk archives into order-level JSONL.

Usage:
    python3 scripts/aggregate_bulk.py ~/expediente-data/bulk out/orders.jsonl
    python3 scripts/aggregate_bulk.py <dir> <out> --members TratoDirecto,OCLicitacion

Nothing is extracted to disk: each archive member is piped through `7z e -so` and
reduced to one record per purchase order. See expediente/ingest/bulk.py for why the
reduction is mandatory rather than convenient.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expediente.ingest.bulk import AggregateStats, aggregate, members, write_jsonl


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 1

    source = Path(argv[1]).expanduser()
    destination = Path(argv[2]).expanduser()
    wanted = None
    if "--members" in argv:
        wanted = tuple(argv[argv.index("--members") + 1].split(","))

    archives = sorted(source.rglob("*.7z"))
    if not archives:
        print(f"no .7z found under {source}")
        return 1

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("", encoding="utf-8")

    stats = AggregateStats()
    started = time.monotonic()

    sin_miembros: list[str] = []

    for archive in archives:
        encontrados = members(archive)
        if wanted and not any(any(t in m for t in wanted) for m in encontrados):
            # Un archivo sin miembros que coincidan es la falla más cara de este script: el
            # corpus queda publicado y completo de aspecto, con un sector o un semestre
            # ausente. Se acumula y se imprime al final, no se pasa por alto.
            sin_miembros.append(f"{archive.parent.parent.name}/{archive.parent.name}/{archive.stem}")
            continue
        for member in encontrados:
            if wanted and not any(token in member for token in wanted):
                continue
            member_started = time.monotonic()
            orders = aggregate(archive, member, stats)
            write_jsonl(orders, destination)
            elapsed = time.monotonic() - member_started
            print(
                f"  {archive.parent.parent.name}/{archive.parent.name}/{archive.stem}"
                f" · {member[:24]:24} {len(orders):>7} OC  {elapsed:6.1f}s",
                flush=True,
            )

    total = time.monotonic() - started
    print()
    if sin_miembros:
        print(f"ADVERTENCIA: {len(sin_miembros)} archivos sin miembros que coincidan con {wanted}:")
        for nombre in sin_miembros:
            print(f"  {nombre}")
        print("  (el corpus NO está completo: revisar los nombres dentro del archivo)")
    print(f"total: {stats.rows:,} filas → {stats.orders:,} órdenes en {total:.0f}s")
    print(f"montos inconsistentes: {stats.inconsistent}")
    print(f"salida: {destination} ({destination.stat().st_size / 1024 / 1024:.1f} MB)")
    stats_dict = stats.to_dict()
    if sin_miembros:
        stats_dict["archivos_sin_miembros"] = sin_miembros
    (destination.parent / "aggregate_stats.json").write_text(
        json.dumps(stats_dict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
