#!/usr/bin/env python3
"""Draw the human audit sample from the entity benchmark, blind.

Why two files
-------------
The audit asks a person the question the resolver answers: *do these two names
designate the same legal entity?* If the sheet shows the RUT, the question answers
itself — equal RUTs are the same entity by definition. So the sheet carries names and
the model's two scores, and nothing else. The truth lives in the key, which is opened
after the audit is filled.

The sample is stratified and seeded, so it is the same 60 pairs every time. Three of
them are forced in: the negatives that survive the assert threshold, because those are
the pairs the whole argument turns on.

    python3 benchmarks/make_audit_sample.py

Writes:
    benchmarks/audit/hoja_60.csv     the blind sheet a person fills
    benchmarks/audit/clave_60.csv    stratum, truth and RUTs, for after
"""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "audit"
SEED = 424242

#: The sample. Weighted toward the boundary, not toward the easy cases.
PLAN = {
    "hard_positive": 20,      # same RUT, genuinely different names
    "positive": 10,           # same RUT, capitalisation-level variants
    "hard_negative": 15,      # different RUT, top of the cosine distribution
    "address_negative": 10,   # different RUT, same building
    "easy_negative": 5,       # different RUT, different region and address
}

#: Pairs that must be in the sample whatever the seed does. The first two beat the
#: assert threshold; LAYNER is the case that says the threshold cannot be the answer.
FORCED = [
    ("LAYNER SPA", "LAYNER OTEC SPA"),
    ("César Antonio", "CÉSAR ANTONIO HERRERA MUÑOZ"),
]


def main() -> int:
    rows = json.loads((HERE / "pairs_final.json").read_text(encoding="utf-8"))
    random.seed(SEED)

    picked: list[dict] = []
    used: set[tuple[str, str]] = set()

    for pair in rows:
        signature = (pair["name_a"].strip().lower(), pair["name_b"].strip().lower())
        if signature in FORCED and signature not in used:
            picked.append(pair)
            used.add(signature)

    for stratum, count in PLAN.items():
        pool = [p for p in rows if p["stratum"] == stratum]
        already = sum(1 for p in picked if p["stratum"] == stratum)
        need = max(0, count - already)
        remaining = [
            p for p in pool if (p["name_a"].strip().lower(), p["name_b"].strip().lower()) not in used
        ]
        take = random.sample(remaining, min(need, len(remaining)))
        for pair in take:
            used.add((pair["name_a"].strip().lower(), pair["name_b"].strip().lower()))
        picked.extend(take)

    random.shuffle(picked)
    OUT.mkdir(parents=True, exist_ok=True)

    sheet = OUT / "hoja_60.csv"
    with sheet.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["id", "nombre_a", "nombre_b", "coseno", "rerank",
             "misma_entidad_SI_NO_DUDOSO", "confianza_1a5", "nota"]
        )
        for index, pair in enumerate(picked, start=1):
            writer.writerow(
                [
                    f"A{index:02d}",
                    pair["name_a"],
                    pair["name_b"],
                    f"{pair['cosine']:.3f}" if pair.get("cosine") is not None else "",
                    f"{pair['rerank']:.3f}" if pair.get("rerank") is not None else "",
                    "",
                    "",
                    "",
                ]
            )

    key = OUT / "clave_60.csv"
    with key.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "estrato", "verdad", "rut_a", "rut_b"])
        for index, pair in enumerate(picked, start=1):
            writer.writerow([f"A{index:02d}", pair["stratum"], pair["truth"], pair["rut_a"], pair["rut_b"]])

    counts: dict[str, int] = {}
    for pair in picked:
        counts[pair["stratum"]] = counts.get(pair["stratum"], 0) + 1

    print(f"{len(picked)} pares → {sheet}")
    print(f"clave     → {key}")
    print("composición:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
