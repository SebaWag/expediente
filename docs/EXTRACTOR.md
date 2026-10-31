# The extraction layer

A document becomes **fields**, never findings. This page is the contract, the
failure mode it was built against, and how the number is measured.

## The contract

Every requested field leaves `expediente/extract` in one of exactly two shapes:

| Shape | Meaning |
|---|---|
| `ExtractedField(campo, valor, pagina, extracto)` | a value, and the page a reviewer can turn to |
| `Unverified(campo, causa)` | the field was asked for and not obtained, with a reason code |

There is no third shape. No empty string standing in for an answer, no `None` a
caller can forget to check, no confidence float inviting a threshold nobody can
defend. `ExtractionResult.ok` is `True` only when every requested field arrived
with a page.

A value returned **without a page** is refused as `NO_PAGE`. The gate downstream
would delete the claim anyway — refusing it here keeps the count honest instead of
producing a claim that dies one layer later.

## The failure this was built against

The dangerous output is not the wrong number. A wrong number is caught by a human
reading the citation. The dangerous output is **the empty answer**: it reads,
downstream, exactly like a document that had nothing to say.

Measured on a public benchmark during the Meta/Fireworks workshop (30-oct-2026):
**19 of 400 answers at maximum reasoning effort came back empty** (4.75%) because
reasoning tokens consumed the 2,048-token output budget. No error was raised. Two
distinct causes are therefore kept apart in `Causa`:

| Causa | What happened | Whose fault |
|---|---|---|
| `TRUNCATED` | output budget exhausted mid-thought | our parameter |
| `EMPTY_RESPONSE` | nothing returned at all | a bug or a refusal |
| `INVALID_JSON` | prose, or JSON without `campos` | the model |
| `MISSING_FIELD` | the field is not in the document | nobody — this is a valid answer |
| `NO_PAGE` | a value with no page attached | the model |
| `HTTP_ERROR` | the call failed | the network |
| `NOT_CONFIGURED` | the tuned backend was not deployed | expected |

`Usage.reasoning_tokens` is broken out of `completion_tokens` because they are
billed the same way and they are the ones that eat the budget. A cost report that
hides them cannot explain a run that went silent.

## Two backends, one interface

```
Extractor.extract(documento_id, pages, specs) -> ExtractionResult
  ├─ ZeroShotBackend   muse-spark-open@low, serverless      ← default, always available
  └─ TunedBackend      LoRA on an on-demand deployment      ← nice-to-have
```

The tuned extractor is a **nice-to-have and never a dependency**. `registry.choose()`
returns the backend *and* a note saying whether it degraded and why; that note goes
into the run report, because "this dossier was produced by the serverless model at
low effort" is a fact a reviewer is entitled to. Dropping the LoRA changes a line of
configuration (`EXPEDIENTE_EXTRACT_DEPLOYMENT`), not a code path.

Two operational facts that decided the shape of this:

- A LoRA adapter is **not serverless-callable**. It needs an on-demand deployment,
  billed per GPU-hour, with a cold start. That is exactly why a demo is recorded
  against the serverless path.
- `datasets.upload` has been disabled on many Fireworks accounts, which is why
  training goes through **serverless training** (data stays local, only token
  batches are sent).

## Effort is a measured parameter, not a default

`DEFAULT_EFFORT = "low"` comes from a sweep, not from taste. On the same
short-answer suite, Muse peaked at `low` (88.5) and `high` scored **81.8** — more
reasoning cost more and was worse. A repo that publishes only max-effort numbers
hides that trade-off; ours is a parameter with a measurement behind it, and
`benchmarks/extract_bench.py --efforts low,medium,high` re-measures it on our corpus.

## How the number is measured

`benchmarks/extract_bench.py` reports three numbers, and the third is the one that
almost never gets published:

1. **per-field accuracy** over the examples whose label has that field;
2. **whole-record accuracy** — every field right;
3. **`no verificado`** — fields that came back with a cause instead of a value,
   broken down by cause.

A run that is 80% accurate while leaving 15% of fields unverified is not an 80%
extractor; it is an extractor that reads four documents out of five. The report says
so, because a benchmark that drops its refusals can be improved by refusing more.

The grader (`extract/grading.py`) is **rules, not a judge**: number within 1%, dates
compared as `(year, month, day)` so a swapped day and month is a miss, RUTs compared
with check digit (a missing check digit is not a match — it means nobody verified
it), enums through an explicit alias table. Two backends compared months apart get
the same number because nothing in the comparison is a model.

Labels are to come from the **deterministic detectors**, so the thing being scored
and the thing scoring it share no machinery — the same principle as the
RUT-labelled entity benchmark.

Run it without network or credits:

```bash
python3 benchmarks/extract_bench.py \
  --examples examples/extract_fixture.jsonl \
  --mock benchmarks/extract_mock_completions.jsonl
```

The mock file is three synthetic completions — one perfect, one partial, one
truncated — that exist only to exercise the report. It is not a benchmark.

## Where the fine-tune fits, if it happens at all

| Question | Answer from the workshop | Our reading |
|---|---|---|
| How many rows for classification/extraction? | "a few hundred" | 500–1,000 labelled documents |
| Cost to train | ~10 min, **free** for Muse Glimmer LoRA SFT until 2026-12-01 | a free afternoon |
| Split | 560 train / 40 holdout | holdout fixed before training, never touched |
| Deliverable | — | **the harness**, not the model |

The extractor fills cells. It never decides whether something is an anomaly — that
stays in `detectors/`, deterministic and citable. A tuned model that decided would
break the one promise the product makes.

## The gold set

`benchmarks/build_extract_gold.py` builds labelled documents from the structured
corpus: the label **is** the row (`codigo`, `monto_total_clp`, `proveedor_rut`, …) and
the page text is a rendering of that same row. No model produces the label, so the
thing being scored and the thing scoring it share nothing.

```bash
python3 benchmarks/build_extract_gold.py --n 800        # → benchmarks/extract_gold.jsonl
python3 -m pytest tests/test_extract_gold.py -q          # 10 assertions on the invariant
python3 benchmarks/extract_bench.py --examples benchmarks/extract_gold.jsonl --efforts low,high
```

Current set: **800 documents**, stratified by mechanism — Compra Ágil 310,
Licitación Pública 304, Trato Directo 96, Convenio Marco 90. Two layouts per
mechanism, 35 % split across two pages so the citation must name a page,
**39 % with no net/VAT breakdown** so `monto_neto_clp` is a legitimate
`MISSING_FIELD` instead of a guess the extractor is rewarded for inventing.

### The invariant that keeps it honest

`tests/test_extract_gold.py` asserts that **every label is literally present in the
page text it renders** — code, RUT, unit, amounts, date. A label the document never
prints would make the benchmark unanswerable, and an unanswerable benchmark is
indistinguishable from a bad extractor. The date test caught a real bug during
development; the "no net" test caught `alimnetos` containing `neto`.

### What this number does NOT measure

These are **rendered** documents, not scans: no OCR, no skew, no stamps, no
handwriting. The result measures reading comprehension and citation discipline over
clean text. Document capture is a separate problem and this set does not touch it.

### Writing new layouts

A layout is a function `(row, fecha, total, neto) -> list[page]`. It must print every
value it expects to be labelled. If it does not, `test_extract_gold` fails — which is
the point.
