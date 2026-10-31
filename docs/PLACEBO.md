# Placebo caps: what detector 1 may and may not claim

**Run:** 2026-10-31 · **machine:** Oracle Cloud (aarch64, 23 GB) · **orders:** 2.438.215
**Command:** `python3 scripts/placebo_caps.py <orders.jsonl> --json-out placebo_full.json`
**Code:** `cap_factor` on `expediente/detectors/fraccionamiento.py` · **Test:** `tests/test_placebo_caps.py`
**Data:** `benchmarks/placebo_caps_sample.json`

## The question the control answers

Detector 1 fires when one buyer sends `min_orders` or more orders to one supplier in one
month, each of them under the cap in force that day. Two different worlds produce that
shape:

* **the cap matters** — orders are sized to stay under it, so the rule measures an
  incentive to avoid a competitive procedure; or
* **the cap is scenery** — that buyer simply buys often, and any ceiling would have
  produced the same chains.

The way to tell them apart is to run the *same rule* with a **fake cap** and watch whether
the number moves. Each factor below scales the real, date-resolved ceiling **per order**, so
the comparison is against the threshold that actually applied on each date, not against a
constant.

**Population:** the detector's default, `DIRECT_AWARD_PROCEDENCIAS = ("Trato Directo",)`.
The splitting rule is about avoiding a tender, and that is the mechanism where the choice
exists; Compra Ágil is a different population and is not in this run.

## The measurement

| cap factor | chains | orders cited | total (M CLP) | share of real chains |
|---:|---:|---:|---:|---:|
| 0,40x | 16.121 | 77.354 | 157.048 | **94 %** |
| 0,60x | 16.632 | 79.940 | 191.721 | 97 % |
| 0,80x | 16.916 | 81.396 | 215.367 | 99 % |
| 0,90x | 17.040 | 81.977 | 226.840 | 99 % |
| **1,00x** | **17.164** | **82.527** | **238.664** | 100 % |
| 1,10x | 17.215 | 82.806 | 244.759 | 100 % |
| 1,25x | 17.303 | 83.249 | 253.270 | 101 % |

**Cutting the threshold by 60 % moves the finding count by 6 %.** The chains do not follow
the cap.

The first version of this table came from the first 200.000 lines of the corpus and showed
96 % at 0,40x. That slice turned out to be one period of 2021, so the run was repeated on a
sample that spans the whole five years: same conclusion, 94 % instead of 96 %. Reporting the
number from the wrong slice would have been right by accident.

## How the sample was drawn, and why not by line

A **line-stride** sample is the obvious choice and it is wrong here: this detector counts
groups of `(buyer, supplier, month)`, so dropping orders breaks the groups and undercounts
chains. Any sampling that is not closed under the unit of analysis silently changes the
answer.

So the sampling unit is the **buyer**: `crc32(organismo_rut) % 4 == 0`, deterministic, no
seed. 488 of the buyer units, 23,7 % of the orders, and **every chain of a sampled buyer is
complete** because no order of that buyer was dropped.

What this is not: a quarter of the corpus that can be multiplied by four. Buyers are not
equal in size, so the honest statement is "these 17.164 chains belong to the 24 % of buyer
units in the sample". Extrapolating needs the full load, which is what the Oracle box is for
and is a separate run.

## What this forbids

**"The buyer split the purchase to avoid the tender" is not supported by this data.** That
sentence requires evidence that the cap shaped the sizing of the orders, and the curve above
is the counterfactual that says it did not. Reporting the chain count as an anomaly *rate* is
also out: it is a count of repeated purchases, and the base rate of repeated purchases is not
an anomaly.

## What it permits

Every one of those 17.164 chains is still a true, checkable statement:

> These orders exist. No single one exceeded the cap that applied on its date. Together they
> are materially larger than it, and no tender was run for them. Here is the code of each.

That is a **worklist ordered by materiality** — where a review should start — and it survives
an auditor reading the codes. It also survives the obvious question a jury will ask, because
the question has already been asked here and answered with a number.

`Rxyxs/chile-public-procurement-anomalies` reached the same conclusion from the other side:
their bunching signal fell to ~2,6 points once they controlled for buyer, and their README
answers with *"not proof of anything, but where a review should start"*. Our version of that
sentence is now backed by a curve we generated ourselves, on our corpus, with a flag anyone
can re-run.

## Why this is in the repo and not in a slide

The control is one keyword argument on the production detector. Anyone who doubts the number
can re-run it against the same file and get the same table in seconds — on a 23 GB box, since
loading 10,3 M orders as objects needs about 7 GB and does not fit on the development laptop.
A claim whose counterfactual ships next to it can be audited; the rest is marketing with a
decimal point.

## Limits

* **Buyer-stratified sample**, 2,44 M of 10,30 M orders (23,7 %), 488 buyer units, sampled
  deterministically by `crc32(organismo_rut) % 4 == 0`. Chains are complete within the
  sample; the totals are not the national totals.
* **`min_orders` and the floor held at their defaults** for every factor. A different
  `min_orders` changes the level of the curve, and the level is not what the control is
  about.
* **A flat curve rules out cap-shaped sizing for the population.** It does not rule it out
  for an individual chain: one chain may still be deliberate, and that is exactly why the
  output is a list of codes to open and not a verdict.
* **Same-period caveat inverted:** the earlier 200k-line run was one period of 2021, which is
  pre-reform and therefore an *easier* place to find chains sized to a cap that existed. The
  five-year sample is the harder test, and the effect got smaller (94 % vs 96 %), not bigger.
