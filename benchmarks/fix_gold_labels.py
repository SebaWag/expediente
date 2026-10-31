#!/usr/bin/env python3
"""Correct the gold labels where the page contradicted the row.

Two label families in ``extract_gold.jsonl`` were derived from the source record
instead of from the rendered document, which makes them unanswerable: an extractor
sees a page, not a database row. The measurement that exposed them was a cluster,
not a diffuse weakness -- 140 of 140 ``tipo_documento`` errors and 43 of 43
``procedencia`` errors fell on exactly the layouts below, while every other layout
scored 1.000.

  1. ``COMPRA ÁGIL — FICHA DE ADJUDICACIÓN``  was labelled ``orden_compra``.
     The page says what it is; the schema had no word for it. Fixed by adding
     ``ficha_compra_agil`` to the enum and relabelling these documents.

  2. ``CONVENIO MARCO — ORDEN DE COMPRA``  was labelled with the record's
     procedencia, which for 43 documents was ``trato_directo``. The layout never
     prints a "Procedimiento:" line, so the only cue on the page is the title.
     A document whose visible cue says CONVENIO MARCO is ``convenio_marco``.

This rewrites labels only. Ids, page text and every other gold field are untouched,
because the model's predictions are already journalled and re-scoring them against
corrected labels costs no API call. That is the whole point of keeping a journal.

Usage:
    python3 benchmarks/fix_gold_labels.py --dry-run
    python3 benchmarks/fix_gold_labels.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

DEFAULT_GOLD = Path("benchmarks/extract_gold.jsonl")

#: Title prefix on page 1 -> the document type the page declares. Ordered: the first
#: prefix that matches wins, and ``CONVENIO MARCO — ORDEN DE COMPRA`` must be tested
#: before ``ORDEN DE COMPRA`` for obvious reasons.
TITLE_TIPO = (
    ("COMPRA ÁGIL", "ficha_compra_agil"),
    ("RESOLUCIÓN DE ADJUDICACIÓN", "resolucion_adjudicacion"),
    ("CONVENIO MARCO", "orden_compra"),
    ("ORDEN DE COMPRA", "orden_compra"),
)

#: Layouts that print no "Procedimiento:" line: the title is the only cue, so it decides.
TITLE_PROCEDENCIA = (
    ("CONVENIO MARCO", "convenio_marco"),
)


def titulo(example: dict) -> str:
    """First line of the first page, uppercased: the document's own name for itself."""
    pages = example.get("paginas") or []
    if not pages:
        return ""
    return pages[0]["texto"].split("\n")[0].strip().upper()


def tipo_por_titulo(titulo_upper: str) -> str | None:
    for prefix, tipo in TITLE_TIPO:
        if titulo_upper.startswith(prefix.upper()):
            return tipo
    return None


def procedencia_por_titulo(titulo_upper: str) -> str | None:
    for prefix, procedencia in TITLE_PROCEDENCIA:
        if titulo_upper.startswith(prefix.upper()):
            return procedencia
    return None


def fix(example: dict) -> list[str]:
    """Relabel in place. Returns the changes made, as ``campo: viejo -> nuevo``."""
    changes: list[str] = []
    gold = example.get("gold") or {}
    head = titulo(example)

    tipo = tipo_por_titulo(head)
    if tipo and gold.get("tipo_documento") != tipo:
        changes.append(f"tipo_documento: {gold.get('tipo_documento')} -> {tipo}")
        gold["tipo_documento"] = tipo

    procedencia = procedencia_por_titulo(head)
    if procedencia and gold.get("procedencia") != procedencia:
        changes.append(f"procedencia: {gold.get('procedencia')} -> {procedencia}")
        gold["procedencia"] = procedencia

    example["gold"] = gold
    return changes


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--dry-run", action="store_true", help="report the changes without writing")
    args = parser.parse_args(argv[1:])

    if not args.gold.exists():
        print(f"missing {args.gold}", file=sys.stderr)
        return 1

    lines = args.gold.read_text(encoding="utf-8").splitlines()
    examples: list[dict] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue
        examples.append(json.loads(stripped))

    total = Counter()
    per_doc = Counter()
    for example in examples:
        changes = fix(example)
        if changes:
            per_doc[example["id"]] = len(changes)
            for change in changes:
                total[change.split(":")[0]] += 1

    print(f"documentos: {len(examples)} · documentos con cambios: {len(per_doc)}")
    for campo, n in sorted(total.items()):
        print(f"  {campo}: {n} etiquetas corregidas")

    if args.dry_run:
        print("\n(dry run: nada escrito)")
        return 0

    if not per_doc:
        print("\nsin cambios: el gold ya estaba consistente")
        return 0

    tmp = args.gold.with_suffix(args.gold.suffix + ".tmp")
    tmp.write_text(
        "\n".join(json.dumps(example, ensure_ascii=False) for example in examples) + "\n",
        encoding="utf-8",
    )
    tmp.replace(args.gold)
    print(f"\nescrito {args.gold} ({len(examples)} documentos)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
