# Detector benchmark — a desk check, and what it found

**Generated:** 2026-10-29T15:42:14-0300 · **sample:** 48 signals, stratified by family

## What this is, and what it is not

The README promised a benchmark against the Contraloría's published audit findings. That
source turned out not to be machine-readable: the report browser is a Liferay portlet that
loads over AJAX, and `datos.gob.cl` has no dataset of findings. Both checked on 2026-10-29,
not assumed. Scraping a JavaScript application to manufacture ground truth would have taken
the remaining days and produced a number nobody could reproduce.

So this is a **desk check**: every finding is re-derived from the records it cites, and each
rule family is checked against the definition it claims to implement — not against its own
summary. Three questions per finding:

1. **Does it reproduce?** The cited orders must satisfy the rule's own definition.
2. **Is there a benign explanation in the records?** A split purchase where every order is a
   framework agreement is not a split purchase.
3. **Is the evidence complete?** Every cited order must reach a document.

## Results

| Family | n | Reproduces | Benign explanation | Actionable | With document |
|---|---|---|---|---|---|
| concentracion | 8 | 8 | 0 | 8 | 8 |
| descripcion_insuficiente | 8 | 8 | **7** | **1** | 8 |
| fraccionamiento | 8 | 8 | 0 | 8 | 8 |
| ordenes_gemelas | 8 | 8 | 3 | 5 | 8 |
| proveedor_nuevo_grande | 8 | 8 | 0 | 8 | 8 |
| trato_directo_reiterado | 8 | 8 | 0 | 8 | **0** |
| **Total** | **48** | **48** | **10** | **38** | **40** |

- **Reproduce 100%** — every sampled finding satisfied its own definition when the rule was re-applied to the raw records.
- **Benign explanation 21%** — one in five has an innocent explanation visible in the records.
- **Actionable 79%** — what survives both checks.
- **Evidence complete 83%** — the gap is one family, explained below.

## Three things the check found

**1. `descripcion_insuficiente` was flagging a field, not a purchase.** Seven of eight
findings had the object spelled out in `nombre` while `descripcion` was short. The detector
read only the first non-empty field, so it reported purchases that *are* specified — which
is exactly the kind of false positive this project exists to avoid. Fixed to take the longer
of the two; the lesson is that a rule which reads one field measures that field.

**2. `trato_directo_reiterado` has no complete evidence, and that is declared.** Those
signals cite up to 12 orders and the evidence pass capped at 4 per signal to bound API
calls. Eight of eight sampled are incomplete. Reported as zero, not hidden.

**3. `ordenes_gemelas` is right five times out of eight.** The three benign cases are pairs
with the same amount and date but different descriptions: distinct purchases that coincidentally
line up. The detector should also require the descriptions to match, and does not yet.

## What this is worth

An **79% actionable rate on a random sample** is a weaker claim than "we
match the Contraloría", and a much stronger one than "we detect patterns". It is a number a
reader can reproduce with one command, on a graph they can query, with records they can
download and hash.

```bash
EXPEDIENTE_MDB_URL=http://127.0.0.1:1235 python3 scripts/benchmark_detectors.py --sample 8
```

Per-case detail — claim, orders cited, why it reproduces, why it is benign — is in
[`benchmarks/detector_benchmark.json`](../benchmarks/detector_benchmark.json).

## Known limits

- The sample is 48 findings. Enough to find three defects in the rules; not enough to
  estimate a rate to the percentage point.
- "Actionable" here means *survives a deterministic desk check*. It does not mean an
  auditor would open the case — that requires human labels, which we do not have.
- The Contraloría benchmark remains genuinely not started. What this replaces is the claim
  that we cannot measure ourselves.
