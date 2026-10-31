# Placebo caps: what detector 1 may and may not claim

**Run:** 2026-10-31 · **corpus:** 199.993 usable orders (`out/compras_5y.jsonl --limit 200000`)
**Command:** `python3 scripts/placebo_caps.py out/compras_5y.jsonl --limit 200000 --json-out out/placebo_caps.json`
**Code:** `cap_factor` on `expediente/detectors/fraccionamiento.py` · **Test:** `tests/test_placebo_caps.py`

## The question the control answers

Detector 1 fires when one buyer sends `min_orders` or more orders to one supplier in one
month, each of them under the cap in force that day. Two different worlds produce that
shape:

* **the cap matters** — orders are sized to stay under it, so the rule measures an
  incentive to avoid a competitive procedure; or
* **the cap is scenery** — that buyer simply buys often, and any ceiling would have
  produced the same chains.

The way to tell them apart is to run the *same rule* with a **fake cap** and watch whether
the number moves. Each factor below scales the real, date-resolved ceiling **per order**.

**Population:** the detector's default, `DIRECT_AWARD_PROCEDENCIAS = ("Trato Directo",)`. The
splitting rule is about avoiding a tender, and that is the mechanism where the choice exists;
Compra Ágil is a different population and is not in this run. Every chain below is therefore
a set of direct-award orders, not a set of agile purchases.

## The measurement

| cap factor | chains | orders cited | total (M CLP) | share of real chains |
|---:|---:|---:|---:|---:|
| 0,40x | 1.061 | 4.713 | 8.986 | **96 %** |
| 0,60x | 1.074 | 4.777 | 10.131 | 97 % |
| 0,80x | 1.089 | 4.838 | 11.830 | 98 % |
| 0,90x | 1.096 | 4.866 | 12.652 | 99 % |
| **1,00x** | **1.106** | **4.904** | **13.999** | 100 % |
| 1,10x | 1.108 | 4.912 | 14.335 | 100 % |
| 1,25x | 1.108 | 4.915 | 14.514 | 100 % |

**Cutting the threshold by 60 % moves the finding count by 4 %.** The chains do not follow
the cap.

## What this forbids

**"The buyer split the purchase to avoid the tender" is not supported by this data.** That
sentence requires evidence that the cap shaped the sizing of the orders, and the curve above
is the counterfactual that says it did not. Reporting the chain count as an anomaly *rate*
is also out: it is a count of repeated purchases, and the base rate of repeated purchases is
not an anomaly.

## What it permits

Every one of those chains is still a true, checkable statement:

> These orders exist. No single one exceeded the cap that applied on its date. Together they
> are materially larger than it, and no tender was run for them. Here is the code of each.

That is a **worklist ordered by materiality** — where a review should start — and it survives
an auditor reading the codes. It also survives the obvious question a jury will ask, because
the question has already been asked here and answered with a number.

This is the same conclusion `Rxyxs/chile-public-public-procurement-anomalies` reached from
the other side: their bunching signal fell to ~2,6 points once they controlled for buyer, and
their README answers with *"not proof of anything, but where a review should start"*. Our
own version of that sentence is now backed by a curve we generated ourselves, on our corpus,
with a flag anyone can re-run.

## Why this is in the repo and not in a slide

The control is one keyword argument on the production detector. Anyone who doubts the number
can re-run it against the same 11 GB file and get the same table in seconds. A claim whose
counterfactual ships next to it is a claim that can be audited; the rest is marketing with a
decimal point.

## Limits

* **200.000 of 3.655.932 orders** (5,5 % of the corpus) for this table; the slice is the
  first 200k lines, not a random sample. Re-running on the full file is a one-argument
  change and is the next step before any number goes into a dossier.
* The chain count depends on `min_orders` and on the floor; this table holds both fixed at
  their defaults.
* A flat curve rules out *cap-shaped sizing* for the population as a whole. It does not rule
  it out for an individual chain: one chain may still be deliberate, and that is exactly why
  the output is a list of codes to open, not a verdict.
