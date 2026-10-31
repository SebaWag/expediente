# Reasoning-effort sweep on the extraction gold set

**Generated:** 2026-10-31T16:19:03-0300 · **model:** `muse-spark-open` (serverless)
**Corpus:** 800 labelled documents, `benchmarks/extract_gold.jsonl` · **fields:** 8
**Harness:** `benchmarks/extract_bench.py --resume --journal out/journal_<effort>.jsonl`

## The question

`reasoning_effort` is the cheapest parameter to have an opinion about and the most
expensive to get wrong: it multiplies output tokens, wall-clock time and the chance
that reasoning eats the output budget before the answer is written. The Fireworks
workshop reported `low` beating `high` by ~7 points overall on a public suite. This
is the same question asked of **our** corpus, with **our** harness, so the answer is
measured here rather than cited from there.

Same 800 documents, same prompt, same grader. Only the effort changed.

## Result

| effort | exacto | no verificado | truncados | completion tok | reasoning tok |
|---|---:|---:|---:|---:|---:|
| **low** | **0,760** | **0,03 %** | **0** | 554.084 | 296.118 |
| medium | 0,688 | 10,43 % | 50 | 1.108.010 | 867.295 |
| high | 0,086 | 90,40 % | 620 | 1.621.160 | 1.577.788 |

`prompt tokens = 624.245` for all three. That is the internal-validity check: the
input was byte-identical, so the difference is the effort and nothing else.

### Paired tests (exact McNemar over the 800 per-document outcomes)

| pair | only left | only right | p |
|---|---:|---:|---:|
| low vs medium | 62 | 4 | **2,08e-14** |
| low vs high | 539 | **0** | **1,11e-162** |
| medium vs high | 482 | 1 | 3,88e-143 |

`high` does not win a single document against `low`. This is not a small effect that
needed a bigger sample to resolve; it is a dial that, on this task, only turns one way.

### Per field

| campo | low | medium | high |
|---|---:|---:|---:|
| `orden_compra_id` | 0,998 | 0,890 | 0,096 |
| `tipo_documento` | 0,825 | 0,806 | 0,096 |
| `procedencia` | 0,946 | 0,840 | 0,086 |
| `monto_total_clp` | **1,000** | 0,894 | 0,096 |
| `monto_neto_clp` | **1,000** | 0,924 | 0,093 |
| `rut_proveedor` | **1,000** | 0,894 | 0,096 |
| `unidad_compradora` | 0,969 | 0,811 | 0,095 |
| `fecha_orden` | **1,000** | 0,894 | 0,096 |

## How the higher efforts fail

```
low    causas: MISSING_FIELD=316
medium causas: TRUNCATED=400 · INVALID_JSON=280 · MISSING_FIELD=269
high   causas: TRUNCATED=4960 · INVALID_JSON=824 · MISSING_FIELD=32   (620 documentos truncados)
```

Reasoning tokens are billed as completion tokens and they are spent **before** the
answer. With `max_tokens = 2048`, a high-effort document spends its budget thinking
and returns either nothing or half a JSON object. Both parse as "no value", which is
why the unverified rate — not the accuracy alone — is the number that exposes it.

`high` buys 2,9× the output tokens of `low` to return an extractor that fails on nine
documents out of ten. The cost and the quality move in opposite directions.

## What this means for the build

1. **`low` is the production setting** for extraction, and now it is a measured
   decision with a p-value, not a preference.
2. **Truncation is the failure mode to guard**, not inaccuracy. The layer already
   reports it as a named cause instead of silence; the sweep confirms it is the
   dominant one at higher effort.
3. **The weak fields are `tipo_documento` (0,825) and `procedencia` (0,946).** Every
   numeric field is at 1,000. If anything is fine-tuned, it is the **classifier**, not
   the reader — and this table is its baseline.

## What this is not

* **Documents are rendered from real corpus rows, not scanned PDFs.** No OCR, no
  skew, no handwriting. This measures reading comprehension and citation discipline
  over clean text; it does not measure document capture.
* **Labels come from deterministic rules**, not from a model. That is what makes the
  comparison meaningful: the thing being scored and the thing scoring it share no
  machinery.
* **One run per effort, one model, one corpus.** No seed variance estimate, so the
  point estimates should be read as point estimates. The paired tests do not depend
  on that, because they compare the same documents under both settings.
* **Gold set is 800 documents.** Enough for the effects above; not enough to claim
  anything about a fifth field that moves by one point.

## Reproduce

```bash
bash scripts/lanzar_sweep.sh low,medium,high    # detached, resumable, append-only log
bash scripts/estado_sweep.sh                    # progress from the journal, not the report
python3 scripts/compare_sweep.py low medium high --json out/comparacion_sweep.json
```

Machine-readable version of the tables above: `benchmarks/sweep_comparison.json`.
