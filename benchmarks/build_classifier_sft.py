#!/usr/bin/env python3
"""Build a classification SFT set and a held-out split from the corrected gold.

The task is deliberately narrow: **classify, don't read**. Only ``tipo_documento``
and ``procedencia`` are trained and scored, because the sweep measured the other six
fields at 1.000 and a fine-tune that touches them could only make them worse.

Why this target and not the whole extractor
-------------------------------------------
After correcting the labels (see ``fix_gold_labels.py``), the base model scores 1.000
on every non-``ficha`` document in ``tipo_documento``, and 0/147 on documents whose
first line reads "COMPRA ÁGIL — FICHA DE ADJUDICACIÓN" -- a class the schema did not
name until now. That is the only place with real headroom, and it is answerable: the
page says what it is, in its first line.

What the examples look like
---------------------------
Same contract as production, so ``parse_completion`` and ``grading`` work unchanged:
the assistant reply is the ``{"campos": {...}}`` object with a ``pagina`` for every
value. A training target without a page would teach the model to answer without
evidence, which is the one thing this layer exists to prevent.

Split is stratified over (tipo, procedencia) so the rare class is represented on both
sides. Seed is fixed, so the holdout is reproducible and the baseline measured on it
is stable.

Usage:
    python3 benchmarks/build_classifier_sft.py
    python3 benchmarks/build_classifier_sft.py --holdout 200
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from expediente.extract.models import Page  # noqa: E402
from expediente.extract.schema import by_name, system_prompt, user_prompt  # noqa: E402

#: The two fields this experiment trains and scores. Nothing else.
CLASSIFIER_FIELDS = ("tipo_documento", "procedencia")


def specs():
    table = by_name()
    return [table[name] for name in CLASSIFIER_FIELDS]


def citation(paginas: list[dict], needle: str) -> tuple[int, str]:
    """Page and line where the visible cue lives. Falls back to the first line.

    The cue is what the page says, not what we wish it said: the title for a document
    type, the "Procedimiento:" line for a mechanism when the layout prints one.
    """
    for page in paginas:
        for line in page["texto"].split("\n"):
            if needle.lower() in line.lower():
                return page["numero"], line.strip()
    first = paginas[0]["texto"].split("\n")[0].strip() if paginas else ""
    return paginas[0]["numero"] if paginas else 1, first


def assistant_reply(example: dict) -> str:
    """The ``{"campos": {...}}`` object the extractor is expected to produce."""
    paginas = example["paginas"]
    head = paginas[0]["texto"].split("\n")[0] if paginas else ""
    campos: dict[str, dict] = {}

    tipo_pagina, tipo_linea = citation(paginas, head[:24] if head else "")
    campos["tipo_documento"] = {
        "valor": example["gold"]["tipo_documento"],
        "pagina": tipo_pagina,
        "extracto": tipo_linea[:200],
    }

    # "procedimiento" without the colon on purpose: the resolution layout prints it
    # inline ("... procedimiento Licitación Pública"), and matching only
    # "Procedimiento:" made those citations fall back to the title, which says nothing
    # about the mechanism.
    proc_pagina, proc_linea = citation(paginas, "procedimiento")
    if "procedimiento" not in proc_linea.lower():
        # Layout prints no mechanism line: the title carries it, and so must the citation.
        proc_pagina, proc_linea = tipo_pagina, tipo_linea
    campos["procedencia"] = {
        "valor": example["gold"]["procedencia"],
        "pagina": proc_pagina,
        "extracto": proc_linea[:200],
    }

    return json.dumps({"campos": campos}, ensure_ascii=False)


def to_chat(example: dict) -> dict:
    selected = specs()
    pages = tuple(Page(numero=p["numero"], texto=p["texto"]) for p in example["paginas"])
    return {
        "messages": [
            {"role": "system", "content": system_prompt(selected)},
            {"role": "user", "content": user_prompt(pages, selected)},
            {"role": "assistant", "content": assistant_reply(example)},
        ]
    }


def holdout_row(example: dict) -> dict:
    """Same shape ``extract_bench.py`` reads, with the classifier gold only."""
    return {
        "id": example["id"],
        "documento_id": example["documento_id"],
        "nota": example.get("nota", ""),
        "paginas": example["paginas"],
        "gold": {name: example["gold"][name] for name in CLASSIFIER_FIELDS if example["gold"].get(name)},
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gold", type=Path, default=ROOT / "benchmarks" / "extract_gold.jsonl")
    parser.add_argument("--train-out", type=Path, default=ROOT / "benchmarks" / "clf_train.jsonl")
    parser.add_argument("--holdout-out", type=Path, default=ROOT / "benchmarks" / "clf_holdout.jsonl")
    parser.add_argument("--holdout", type=int, default=160, help="documents reserved for evaluation")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args(argv[1:])

    examples = [
        json.loads(line)
        for line in args.gold.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("//")
    ]
    if not examples:
        print(f"no examples in {args.gold}", file=sys.stderr)
        return 1

    strata: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for example in examples:
        strata[(example["gold"]["tipo_documento"], example["gold"]["procedencia"])].append(example)

    rng = random.Random(args.seed)
    holdout: list[dict] = []
    train: list[dict] = []
    for key in sorted(strata):
        group = sorted(strata[key], key=lambda ex: ex["id"])
        rng.shuffle(group)
        # Proportional, at least one held out per stratum: a class that disappears from
        # the holdout turns its accuracy into a number nobody can compute.
        share = max(1, round(len(group) * args.holdout / len(examples)))
        holdout.extend(group[:share])
        train.extend(group[share:])

    train.sort(key=lambda ex: ex["id"])
    holdout.sort(key=lambda ex: ex["id"])

    args.train_out.parent.mkdir(parents=True, exist_ok=True)
    args.train_out.write_text(
        "\n".join(json.dumps(to_chat(ex), ensure_ascii=False) for ex in train) + "\n", encoding="utf-8"
    )
    args.holdout_out.write_text(
        "\n".join(json.dumps(holdout_row(ex), ensure_ascii=False) for ex in holdout) + "\n", encoding="utf-8"
    )

    print(f"train:   {len(train):>4} → {args.train_out}")
    print(f"holdout: {len(holdout):>4} → {args.holdout_out}")
    print(f"\n{'tipo_documento':<24}{'train':>7}{'holdout':>9}")
    for tipo in sorted({ex["gold"]["tipo_documento"] for ex in examples}):
        n_train = sum(1 for ex in train if ex["gold"]["tipo_documento"] == tipo)
        n_hold = sum(1 for ex in holdout if ex["gold"]["tipo_documento"] == tipo)
        print(f"{tipo:<24}{n_train:>7}{n_hold:>9}")
    print(f"\n{'procedencia':<24}{'train':>7}{'holdout':>9}")
    for proc in sorted({ex["gold"]["procedencia"] for ex in examples}):
        n_train = sum(1 for ex in train if ex["gold"]["procedencia"] == proc)
        n_hold = sum(1 for ex in holdout if ex["gold"]["procedencia"] == proc)
        print(f"{proc:<24}{n_train:>7}{n_hold:>9}")

    # A split that leaks is worse than no split: check no id appears on both sides.
    overlap = {ex["id"] for ex in train} & {ex["id"] for ex in holdout}
    if overlap:
        print(f"\nLEAK: {len(overlap)} ids in both splits", file=sys.stderr)
        return 1
    print("\nsin fuga entre train y holdout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
