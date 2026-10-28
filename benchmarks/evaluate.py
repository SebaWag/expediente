#!/usr/bin/env python3
"""Evaluate entity-resolution similarity signals against RUT-derived ground truth.

The ground truth here does not come from a model. It comes from the RUT: two name
strings that belong to the same `identifier.id` are the same legal entity, by
definition. That is what keeps this benchmark honest — the labels and the signals
being scored share no machinery.

Strata
------
positive        two name strings from the same RUT (e.g. legalName and a trade name)
easy_negative   different RUTs, different region, different address
hard_negative   different RUTs, same region, different address, sampled from the
                TOP of the cosine distribution — i.e. the pairs a naive threshold
                gets wrong. This stratum is adversarial by construction, so its
                error rate is not a population estimate.

Signals compared
----------------
cosine   qwen3-embedding-8b, 4096 dims — the cheap first stage
rerank   qwen3-reranker-8b relevance score — the second stage

Usage
-----
    python3 benchmarks/evaluate.py benchmarks/pairs_reranked.json

Regenerate the inputs with the snippets in docs/OCDS_FINDINGS.md and
docs/ENTITY_BENCHMARK.md.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

THRESHOLDS_COSINE = [0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
THRESHOLDS_RERANK = [0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90]


def confusion(rows: list[dict], key: str, threshold: float) -> dict:
    tp = sum(1 for r in rows if r.get(key, -1) >= threshold and r["truth"] == "same")
    fp = sum(1 for r in rows if r.get(key, -1) >= threshold and r["truth"] != "same")
    positives = sum(1 for r in rows if r["truth"] == "same")
    fn = positives - tp
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / positives if positives else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "threshold": threshold,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def report(rows: list[dict], key: str, label: str, thresholds: list[float]) -> None:
    rows = [r for r in rows if key in r]
    positives = sum(1 for r in rows if r["truth"] == "same")
    print(f"\n── {label} · signal={key} · P={positives} N={len(rows) - positives} ──")
    print(f"{'thr':>6} {'TP':>5} {'FP':>5} {'FN':>5} {'prec':>7} {'recall':>7} {'F1':>7}")
    for threshold in thresholds:
        c = confusion(rows, key, threshold)
        print(
            f"{c['threshold']:>6.2f} {c['tp']:>5} {c['fp']:>5} {c['fn']:>5} "
            f"{c['precision']:>7.3f} {c['recall']:>7.3f} {c['f1']:>7.3f}"
        )


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else Path("benchmarks/pairs_reranked.json")
    if not path.exists():
        print(f"missing {path}", file=sys.stderr)
        return 1

    rows = json.loads(path.read_text(encoding="utf-8"))
    strata = {r["stratum"] for r in rows}
    print(f"pairs: {len(rows)} · strata: {sorted(strata)}")

    hard = [r for r in rows if r["stratum"] == "hard_negative"]
    easy = [r for r in rows if r["stratum"] != "hard_negative"]

    report(easy, "cosine", "easy strata · cosine", THRESHOLDS_COSINE)
    report(rows, "cosine", "all strata · cosine", THRESHOLDS_COSINE)
    report(easy, "rerank", "easy strata · rerank", THRESHOLDS_RERANK)
    report(rows, "rerank", "all strata · rerank", THRESHOLDS_RERANK)

    print("\n── hard negatives alone: can the signal reject them at all? ──")
    for key, thresholds in (("cosine", [0.86, 0.90, 0.95]), ("rerank", [0.10, 0.30, 0.50, 0.90])):
        for threshold in thresholds:
            above = sum(1 for r in hard if r.get(key, -1) >= threshold)
            print(f"  {key:<6} >= {threshold:<5} → {above:>3}/{len(hard)} hard negatives accepted")

    print(
        "\nReading the result: cosine keeps every hard negative above 0.86, so no\n"
        "cosine threshold separates the strata. The reranker does separate them,\n"
        "which is why the pipeline has a second stage instead of a tuned cutoff."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
