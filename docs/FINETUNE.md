# A LoRA that looked like +21.7 points, and what it actually was

**Run:** `r20261031-193318` · 2026-10-31 · **base:** `muse-glimmer-30b` · **method:** LoRA SFT (rank 8, 1 epoch, 37 steps) · **training cost:** $0 (promotional, before 2026-12-01)
**Data:** 580 train / 60 holdout from `benchmarks/clf_train.jsonl` · **task:** classify `tipo_documento` + `procedencia`

## The short version

| | base | tuned | delta |
|---|---:|---:|---:|
| Exact match **by citation string** (the recipe's grader) | 48/60 · 0,800 | 60/60 · 1,000 | **+12 docs · +21,7 pts** |
| Exact match **by value + page** (our grader) | 59/60 · **0,983** | 60/60 · 1,000 | **+1 doc · +1,7 pts, p ≈ 1,0** |

Same sixty answers, two graders, one difference of twenty points. The recipe's scorer
compares the `extracto` field as an exact string. A free-text citation never matches
byte for byte, so the base model was failing documents it had answered correctly:

```
GOLD : "procedencia": {"valor": "licitacion_publica", "pagina": 1,
        "extracto": "VISTO: la licitación que dio origen a la Orden de Compra N° 2788-333-SE22, procedimiento Licitación Pública."}
BASE : "procedencia": {"valor": "licitacion_publica", "pagina": 1,
        "extracto": "procedimiento Licitación Pública."}
```

Identical value, identical page, wrong "answer" — because the sentence was shorter.
And the tuned model scored 60/60 for a reason that also disqualifies it as evidence: it
was trained on those citation strings, so it reproduced them. The delta measures
**fidelity to the training target's wording**, not comprehension.

This is the `MUSE_AHEAD.md` failure ("drop that panel") in a subtler form: here the
grader's bias happens to favour the trained model, which is the direction nobody
audits. Both numbers are reported because the difference is the finding.

## What the fine-tune did buy

* **Output tokens fell 7,5×** for equal quality: 42.121 → 5.585 sampled tokens over the
  same 60 prompts. That is a real cost and latency win, and it is not a grading artifact.
* **Nothing broke.** 59 → 60 on value+page; no document regressed.
* The tuned model also learned the output contract well enough to be parseable on every
  document, which the base managed too — so this is a tie, not a gain.

## What the fine-tune was not needed for

`ficha_compra_agil` — the class added to the enum in this same session — was answered
**17/17 by the base model** on the holdout, with the class named in the system prompt.
Naming the class was the fix. The LoRA was not required to recognise a page whose first
line reads "COMPRA ÁGIL — FICHA DE ADJUDICACIÓN".

That is worth stating plainly, because it is the opposite of the plan this session
started with: we set out to fine-tune a classifier that turned out to need a vocabulary
entry, not a gradient step.

## The measurement that made this visible

The training run wrote `predictions_base.jsonl` and `predictions_tuned.jsonl` into the
run directory. Re-grading those two files with a value-based comparison took one script
and cost nothing — and it is the only reason the +21,7 was caught before it reached a
slide. A benchmark that reports one number and discards its own per-document output
cannot be audited, which is the same lesson as the journal in `extract_bench.py`.

## Honest limits

* **n = 60 holdout**, carved from the 640 training rows. Our 160-document stratified
  holdout (`benchmarks/clf_holdout.jsonl`) was **not** used here: scoring the tuned
  model on it needs a deployment, which bills GPU-hours. It stays pristine for that run.
* **One run, one seed, one epoch.** No variance estimate.
* The recipe's renderer injects `Reasoning strength: high` into the system prompt of
  both base and tuned, which our sweep measured as the worst setting for extraction
  (90,4 % unverified at 800 documents). Both sides suffer it equally, so the comparison
  holds — but the absolute numbers are not the ones this harness would produce in
  production, where the prompt does not carry that line.
* Documents are rendered from real corpus rows, not scans. This measures reading and
  citation discipline over clean text.

## Reproduce

```bash
python3 benchmarks/fix_gold_labels.py            # correct the labels first
python3 benchmarks/build_classifier_sft.py       # stratified 640/160 split, no leak
python3 benchmarks/extract_bench.py --examples benchmarks/clf_holdout.jsonl \
    --efforts low --journal out/journal_low.jsonl --regrade   # baseline, no API call
```

Serverless training (from `github.com/fw-ai/cookbook`, `training/`):

```bash
FIREWORKS_BASE_URL=https://api.fireworks.ai \
python -m examples.sft.muse_glimmer_starter prepare --file benchmarks/clf_train.jsonl --n-eval 60
FIREWORKS_BASE_URL=https://api.fireworks.ai \
python -m examples.sft.muse_glimmer_starter launch --surface serverless \
    --file benchmarks/clf_train.jsonl --run-id <id> --n-eval 60
```

`FIREWORKS_BASE_URL` must be the control-plane root: pointed at the inference endpoint it
produces `404 /v1/training/v1/serverless/api/v1/create_session`.
