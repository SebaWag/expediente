#!/usr/bin/env python3
"""Score the human audit against the RUT and against the reranker.

The sheet is filled blind, so this is the first time the labels and the truth meet.
Three numbers come out, and they answer different questions:

1. **human vs RUT** — how often a person agrees with the definitional truth. The
   disagreements are the valuable output: they are the pairs where the RUT and the
   business diverge, and they are where a resolver needs a human, not a threshold.

2. **reranker vs human** — at the candidate and assert thresholds, how often the model
   says the same thing a person said. A threshold that disagrees with people at 0.90
   is asserting something a human would not sign.

3. **the disagreements themselves, listed** — because a rate is not a finding.

    python3 benchmarks/score_audit.py

Reads benchmarks/audit/hoja_60.csv (filled) and clave_60.csv.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent / "audit"
SAME = {"SI", "SÍ", "S", "YES", "1", "TRUE", "IGUAL", "MISMA"}


def load_sheet(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8") as handle:
        return {row["id"]: row for row in csv.DictReader(handle)}


def verdict(raw: str) -> bool | None:
    text = (raw or "").strip().upper()
    if not text:
        return None
    if text in SAME:
        return True
    if text.startswith("DUD"):  # DUDOSO: neither confirmed nor denied
        return None
    return False


def main() -> int:
    sheet_path = HERE / "hoja_60.csv"
    key_path = HERE / "clave_60.csv"
    for path in (sheet_path, key_path):
        if not path.exists():
            print(f"missing {path}", file=sys.stderr)
            return 1

    sheet = load_sheet(sheet_path)
    with key_path.open(encoding="utf-8") as handle:
        key = {row["id"]: row for row in csv.DictReader(handle)}

    filled = 0
    agree_truth = 0
    dudosos = 0
    disagreements: list[str] = []

    rerank_vs_human = {0.30: [0, 0], 0.90: [0, 0]}  # threshold -> [agree, total]
    for identifier, row in sheet.items():
        human = verdict(row.get("misma_entidad_SI_NO_DUDOSO", ""))
        if human is None:
            if (row.get("misma_entidad_SI_NO_DUDOSO") or "").strip():
                dudosos += 1
            continue
        filled += 1
        truth = key[identifier]["verdad"] == "same"
        if human == truth:
            agree_truth += 1
        else:
            disagreements.append(
                f"  {identifier} · humano={'MISMA' if human else 'DISTINTA'} · "
                f"RUT dice {'misma' if truth else 'distinta'} · "
                f"{row['nombre_a']} | {row['nombre_b']}"
            )

        score = float(row["rerank"]) if row.get("rerank") else None
        if score is not None:
            for threshold in rerank_vs_human:
                predicted = score >= threshold
                rerank_vs_human[threshold][1] += 1
                if predicted == human:
                    rerank_vs_human[threshold][0] += 1

    if not filled:
        print("la hoja está vacía: llena la columna misma_entidad_SI_NO_DUDOSO")
        return 0

    print(f"pares auditados: {filled} · dudosos: {dudosos}\n")
    print(f"acuerdo humano ↔ RUT: {agree_truth}/{filled} = {agree_truth / filled:.1%}")

    print("\nreranker ↔ humano (¿el modelo decide lo que decide una persona?):")
    for threshold, (agree, total) in sorted(rerank_vs_human.items()):
        if total:
            print(f"  umbral {threshold:.2f} → {agree}/{total} = {agree / total:.1%}")

    if disagreements:
        print("\ndiscrepancias humano ↔ RUT (esto es lo que se viene a buscar):")
        print("\n".join(disagreements))
        print(
            "\nCada línea es un par donde la definición y la lectura humana no coinciden.\n"
            "No son errores de la auditoría: son los pares donde dos RUT distintos son\n"
            "el mismo negocio, o donde dos nombres parecidos son empresas distintas."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
