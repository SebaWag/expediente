# Entity resolution benchmark — what the threshold is worth

**Date:** 2026-10-28, extended 2026-10-29 · **Pairs:** 1.892 · **Cost of both runs:** ~$0,02

## The three questions this answers

1. Can two name strings be matched with embeddings alone?
2. If not, does a reranker fix it, and by how much?
3. What threshold is defensible, and on what evidence?

## Ground truth, and why it is not circular

The labels come from the **RUT**, not from a model:

- **positive** — two name strings carrying the same `identifier.id`. Same RUT means
  same legal entity. Free, exact, no judgement.
- **easy_negative** — different RUTs, different region, different address.
- **hard_negative** — different RUTs, same region, different address, sampled from
  the **top of the cosine distribution**. These are the pairs a tuned cutoff gets
  wrong, so this stratum is adversarial by construction and its error rate is *not*
  a population estimate.

No model labelled anything. The signals being scored and the labels share no
machinery, which is the only reason the numbers mean something.

## The strata

| Stratum | Pairs | How it was built |
|---|---|---|
| positive | 952 | `legalName` vs trade name(s) from the `\|`-split field |
| easy_negative | 147 | random different-RUT pairs, different region and address |
| hard_negative | 150 | top-cosine different-RUT pairs, same region, different address |

Extracted from 863 suppliers over 1.200 OCDS awards (see `OCDS_FINDINGS.md`).

## Result 1 — embeddings alone do not work

Hard negatives were selected with cosine ≥ 0,862. That is the whole problem in one
line:

```
cosine >= 0.86  →  150/150 hard negatives accepted
cosine >= 0.90  →   19/150
cosine >= 0.95  →    1/150
```

**No cosine threshold separates the strata.** Every cutoff high enough to reject a
hard negative also rejects the mass of true positives. On the mixed set:

| cosine threshold | precision | recall |
|---|---|---|
| 0,70 | 0,801 | 0,694 |
| 0,80 | 0,769 | 0,523 |
| 0,90 | 0,928 | 0,258 |

The worst case in the sample, and the reason this had to be measured:

```
cos = 0.9703
  969292300  TERUMO BCT CHILE S.A.
  875906003  TERUMO CHILE LTDA
```

Different legal entities, same corporate group, 0,97 cosine. A threshold cannot
solve this: it is a *related party*, not a duplicate, and the correct output is a
**candidate for review**, never a merged node.

## Result 2 — the reranker separates them

```
rerank >= 0.10  →  77/150 hard negatives accepted
rerank >= 0.30  →  48/150
rerank >= 0.50  →  37/150
rerank >= 0.90  →   2/150
```

On the mixed set:

| rerank threshold | precision | recall | F1 |
|---|---|---|---|
| 0,10 | 0,895 | **0,728** | 0,803 |
| 0,30 | 0,925 | 0,634 | 0,753 |
| 0,50 | 0,933 | 0,554 | 0,695 |
| 0,70 | 0,962 | 0,430 | 0,594 |
| 0,90 | **0,991** | 0,226 | 0,368 |

Side by side at comparable recall: at ~0,73 recall the reranker gives **0,895**
precision where cosine gives 0,786; at ~0,63 the reranker gives **0,925** where
cosine gives 0,801.

## Result 3 — the defensible operating point

There is no single threshold to declare, because the two outputs need different
guarantees. The measured choice:

| Output | Threshold | Precision | Rationale |
|---|---|---|---|
| escalate to human review (`Candidate`) | rerank ≥ 0,30 | **0,925** | high recall of true pairs *and* a manageable review queue |
| assert automatically (`Relation`) | rerank ≥ 0,90 | **0,991** | a false `Relation` is a false accusation |

That mapping is the point: the benchmark justifies the **architecture** (two stages,
two thresholds), not a magic number. A single cutoff would have to pick one of these
guarantees and break the other.

## Honest limitations

- **~~Positives are easier than the real distribution.~~** Fixed in run 2: 627 positives
  with genuinely different names. The recall figures in run 1 were optimistic — the
  assert threshold reaches 15 % of the hard positives against 23 % of the easy ones.
- **The hard negatives are adversarial.** They were selected *because* cosine ranked
  them high. Their 0,925 precision at 0,30 is not an estimate of production
  precision; it is a floor under hostile sampling.
- **The `easy_negative` stratum is unusually easy** by construction (different region
  and address). Real negatives mostly look like the hard stratum.
- **1.249 pairs over 863 suppliers** is small. The positive pairs are one per
  supplier, so the effective sample is 863 entities, not 1.249 independent draws.
- **`streetAddress` is free text.** Blocking on it will produce both the Cardiotec
  true pair and the Fresenius/Sanderson office-building false pair. It is evidence,
  not identity.

## Reproduce

```bash
# 1. harvest (docs/OCDS_FINDINGS.md)  → benchmarks/raw_ocds_awards.jsonl
# 2. extract suppliers               → benchmarks/ocds_suppliers_2000.json
# 3. embeddings + reranker           → benchmarks/name_emb.npy, pairs_reranked.json
python3 benchmarks/evaluate.py benchmarks/pairs_reranked.json
```

## The sentence this buys

> We did not tune a threshold. We built 1.249 pairs whose labels come from the RUT
> rather than from a model, sampled the negatives adversarially from the top of the
> similarity distribution, and measured. Embeddings alone admit 150 of 150 hard
> negatives; the second stage rejects 148 of them at 0,991 precision. That is why the
> pipeline has two stages and two thresholds instead of one clever cutoff.

---

# Run 2 — the positives were too easy, so the set was rebuilt

Run 1 closed with a limitation written down rather than hidden:

> *Positives are easier than the real distribution. Many are the same string with
> different capitalisation or legal form (`SPA` vs `SpA`). That inflates the top of the
> recall curve.*

That is a real defect in a benchmark. 627 of the 952 positives in run 1 are variants of
a single `legalName`, and some differ only in case. A system that scores those correctly
has demonstrated that it can lowercase text.

Run 2 adds two strata that did not exist:

| Stratum | Pairs | How it was built | Truth from |
|---|---|---|---|
| **hard_positive** | 627 | the same RUT with **genuinely different names**, after folding accents, punctuation and case | the RUT |
| **address_negative** | 16 | different RUTs sharing a `streetAddress` — the Fresenius/Sanderson office building | the RUT |
| positive (run 1) | 952 | `legalName` vs trade name | the RUT |
| easy_negative (run 1) | 147 | different RUT, region and address | the RUT |
| hard_negative (run 1) | 150 | top-cosine, different RUT | the RUT |

`python3 benchmarks/build_entity_benchmark.py` reproduces it: 643 new pairs scored with
`qwen3-reranker-8b` in 75 seconds.

## What each stratum looks like

| Stratum | n | median cosine | median rerank | pass at 0.30 | pass at 0.90 |
|---|---|---|---|---|---|
| hard_positive | 627 | 0,793 | 0,500 | **61 %** | **15 %** |
| positive (run 1) | 952 | 0,807 | 0,562 | 63 % | 23 % |
| easy_negative | 147 | 0,594 | 0,001 | 1 % | 0 % |
| hard_negative | 150 | 0,875 | 0,113 | 32 % | 1 % |
| address_negative | 16 | 0,677 | 0,007 | 12 % | **6 %** |

The hard positives are harder than run 1's: 15 % reach the assert threshold instead of
23 %. The recall figures in run 1 were, as suspected, optimistic.

## The result that changes the design

At the assert threshold, the three negatives that survive — including at 0,95 and 0,97 —
are not noise. They are the ceiling of the signal:

```
0.973   COMERCIALIZADORA Y DISTRIBUIDORA ...   |  COMERCIALIZADORA Y DISTRIBUIDO ...
0.961   César Antonio                          |  CÉSAR ANTONIO HERRERA MUÑOZ
0.953   LAYNER SPA                             |  LAYNER OTEC SPA          ← same address, different RUT
```

Run 3 (the human audit, below) read the third pair differently: a person says LAYNER SPA
and LAYNER OTEC SPA are the **same business**, and the RUT is the one that disagrees.
That does not make the threshold safe — it makes the label imperfect, which is the next
finding.

And raising the threshold does not remove them:

| rerank threshold | precision | recall | false positives kept |
|---|---|---|---|
| 0,30 | 0,882 | 0,606 | 51 |
| 0,90 | 0,969 | 0,150 | 3 |
| 0,93 | 0,961 | 0,118 | 3 |
| 0,95 | 0,943 | 0,080 | 3 |
| 0,97 | 0,967 | 0,046 | 1 |
| 0,98 | 1,000 | 0,026 | 0 |

*(627 hard positives against all 313 negatives. The 0,98 row is not a solution: it
asserts 16 pairs out of 627 and buys its perfect precision by refusing to work.)*

From 0,90 to 0,97 the precision moves from 0,969 to 0,967 — it does not improve — while
recall falls by two thirds. **There is no threshold at which a name signal can safely
assert identity.** Two companies can share a name stem and an address and still be two
companies; `LAYNER SPA` and `LAYNER OTEC SPA` are exactly that, and they score 0,953.

## What this means for the pipeline

The two-stage architecture survives intact. The mapping of stage to output does not.

| Output | Run 1 rule | Run 2 rule |
|---|---|---|
| `Candidate` — a human looks | rerank ≥ 0,30 | rerank ≥ 0,30 · **unchanged** |
| `Relation` — the dossier asserts it | rerank ≥ 0,90 | **a name match is not sufficient at any threshold** |

An asserted relation between two different RUTs requires an **independent** corroborating
edge — a shared legal representative, a shared owner, or the same RUT — and the name
score only orders the queue. That is already what `resolution/resolver.py` does:
`_name_candidates` can only produce a `Candidate`, and `Relation` objects come from
`_representative_relations` and `_address_relations`, which read a field rather than a
similarity. Run 2 is the first measurement that says *why* that split is not a matter of
taste: the best false positive in this set scores higher than 61 % of the true positives.

That is the sentence this benchmark buys:

> We did not pick a threshold. We built 1.892 pairs whose labels come from the RUT,
> made the positives hard on purpose, and found that the signal saturates: a look-alike
> at 0,953 outscores six of every ten true pairs. A name can raise a candidate. It
> cannot, at any cutoff, be allowed to assert one.

---

# Run 3 — a person read 60 pairs blind

Runs 1 and 2 score against the RUT. The RUT is definitional, not infallible: it says two
names are the same entity because they share `identifier.id`, and it says nothing about
whether a *person* would sign that. So 60 pairs were drawn stratified toward the
boundary, and one person labelled them without seeing the RUT, the stratum or the
truth. Two were left as `DUDOSO` and are excluded from the rates.

## Where a person and the RUT disagree

| Stratum | Answered | Agrees with the RUT |
|---|---|---|
| hard_negative | 15 | **15 / 15** (100 %) |
| easy_negative | 5 | **5 / 5** (100 %) |
| positive | 10 | 9 / 10 (90 %) |
| address_negative | 10 | 8 / 10 (80 %) |
| hard_positive | 18 | **12 / 18 (67 %)** |
| **total** | **58** | **49 / 58 (84,5 %)** |

The negatives are clean: **a person never called two different companies the same.** Every
disagreement is in the positive or the address stratum, and they are two different
phenomena:

**1 · A brand is not a name (7 cases, `hard_positive` / `positive`).** The OCDS `name`
field joins the legal name and the trade name with `|`, so a "positive" pair can be
`SERVICIO DE EFICIENCIA ENERGETICA LIMITADA` against `I-Energy Limitada`, or
`LIVISTER CHILE SPA` against `IFX`. Same RUT, and no person can tell from the strings.
A name-only system *cannot* pass these; the benchmark scores them as misses anyway.

**2 · Two RUTs, one business (2 cases, `address_negative`).** `CARDIOTEC VASCULAR
LIMITADA` against `Cardiotec Vascularis SpA`, and `LAYNER SPA` against `LAYNER OTEC
SPA`: same address, different RUT, and a person says same business. The RUT answers a
legal question; the auditor is asking a commercial one.

## What that does to the two thresholds

Judged by a person, on the same 58 pairs:

| Threshold | TP | FP | FN | Precision | Recall |
|---|---|---|---|---|---|
| 0.30 — candidate | 22 | 8 | 1 | 0.733 | **0.957** |
| 0.90 — asserted | **9** | **0** | 14 | **1.000** | 0.391 |

Judged by the RUT, as in run 2:

| Threshold | TP | FP | FN | Precision | Recall |
|---|---|---|---|---|---|
| 0.30 | 22 | 8 | 6 | 0.733 | 0.786 |
| 0.90 | 8 | 1 | 20 | 0.889 | 0.286 |

The assert threshold made **nine assertions and a person rejected none of them.** That is
the strongest result the pipeline has, and it rests on nine cases — enough to keep the
split, not enough to quote a rate.

The two yardsticks also disagree against each other, and that is the finding: run 2's
"one false assertion at 0.90" (`LAYNER`) is a pair a human calls the same business. The
label was wrong, not the model. Conversely, two pairs the model gets right by the RUT
(`Q4 INGENIEROS LIMITADA` / `Q4 INGENIEROS`, `SERVICIO DE EFICIENCIA...` / `I-Energy`)
are pairs a person refuses, because a brand is not a name.

## What the audit changed

1. **The 0.90 threshold survives human judgment** on this sample: 9 asserts, 0 rejected.
2. **The RUT-derived recall is a lower bound, not an estimate.** Seven of the eighteen
   hard positives require knowing a brand, not comparing two strings.
3. **`DUDOSO` is the third answer and it is the honest one.** Two pairs were undecidable
   from the sheet, and a resolver should return them as candidates rather than decide.

## Reproduce

```bash
python3 benchmarks/make_audit_sample.py   # same 60 pairs, fixed seed
# a person fills benchmarks/audit/hoja_60.csv, blind
python3 benchmarks/score_audit.py
```
