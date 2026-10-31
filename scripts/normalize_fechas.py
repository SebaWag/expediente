"""Normalise every date field in the corpus to ISO ``YYYY-MM-DD``.

The bulk archives write dates as ``dd-mm-yyyy 0:00:00`` in some members and as
``yyyy-mm-dd`` in others — the same drift that dropped four semesters of files and a whole
sector. Left alone, a third of the corpus parses as "no date" in every consumer that expects
one format, and a record with an unreadable date is indistinguishable from a record without
one. This script exists so that ambiguity is removed once, in the artefact, instead of being
re-handled in every reader.

Deliberately **not** a regular expression over the raw line: a description can legitimately
contain ``04-02-2025`` as text, and rewriting it inside a sentence would corrupt the
evidence. Each line is parsed as JSON, only the date fields are touched, and the record is
written back unchanged otherwise.

    python3 scripts/normalize_fechas.py out/compras_5y.jsonl out/compras_5y_iso.jsonl
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

import orjson

#: Fields that hold a date. Anything not listed here is left exactly as it came.
CAMPOS_FECHA = ("fecha", "cotizacion_publicada", "cotizacion_cierre")


def a_iso(value: str) -> str | None:
    """Return ``YYYY-MM-DD`` for a value in either published format, or ``None``."""
    texto = (value or "").strip()
    if not texto:
        return None
    if len(texto) >= 10 and texto[4] == "-":
        return texto[:10]
    if len(texto) >= 10 and texto[2] == "-":
        try:
            return date(int(texto[6:10]), int(texto[3:5]), int(texto[0:2])).isoformat()
        except ValueError:
            return None
    return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv[1:])

    convertidos = 0
    invalidos = 0
    vacios = 0
    total = 0
    started = time.monotonic()

    with args.source.open("rb") as entrada, args.destination.open("wb") as salida:
        for line in entrada:
            total += 1
            if args.limit and total > args.limit:
                break
            record = orjson.loads(line)
            for campo in CAMPOS_FECHA:
                if campo not in record:
                    continue
                original = record[campo]
                if not original:
                    continue
                iso = a_iso(original)
                if iso is None:
                    invalidos += 1
                    continue
                if iso != original:
                    convertidos += 1
                record[campo] = iso
            salida.write(orjson.dumps(record) + b"\n")

            if total % 2_000_000 == 0:
                print(
                    f"  {total:,} registros · {convertidos:,} fechas convertidas · "
                    f"{time.monotonic() - started:.0f}s",
                    flush=True,
                )

    print()
    print(f"registros          {total:>12,}")
    print(f"fechas convertidas {convertidos:>12,}")
    print(f"fechas ilegibles   {invalidos:>12,}  (quedan como estaban)")
    print(f"salida             {args.destination} ({args.destination.stat().st_size / 1e9:.1f} GB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
