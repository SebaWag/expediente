# Scope

## In scope

**Municipal purchase orders and `Compras Ágiles`.** One lane, chosen because the signal is
detectable from data alone, with no opinion required:

- Municipalities are the buyer units with the most public accountability pressure and the
  smallest internal audit capacity.
- `Compra Ágil` is the fast lane for low-value purchases, which is exactly where splitting
  below the threshold lives.
- Both are published, both have stable identifiers, both connect to a tender when one exists.

## Out of scope, explicitly

| Not doing | Why |
|---|---|
| All public procurement in Chile | Ten days is not enough to be right about everything; better to be right about one lane |
| Accusations of fraud | Legal exposure, and impossible to prove inside the window. The output is an evidence chain, not a verdict |
| Training or fine-tuning a model | Nothing here needs one. The rules are the product |
| A general-purpose document assistant | That is the prohibited "RAG app" category, and it would not be signable |
| Named individuals in the demo | Findings reference records and companies; people appear only as graph structure |

## Data sources

| Source | Access | Used for |
|---|---|---|
| Mercado Público v1 (purchase orders) | free ticket, query param | order detail lookups and daily deltas |
| Mercado Público v2 (`Compra Ágil`) | free ticket, header | agile purchases, quoting suppliers, document ids |
| ChileCompra open data bulk | no ticket | the historical series |
| Contraloría audit reports | public PDFs | ground truth for the benchmark |

**A note on the purchase-order listing endpoint.** It returns only the code, the name and the
state. Buyer, supplier and amounts require one call per order — around ten thousand calls a day
at national volume, which no rate limit tolerates. The historical series therefore comes from the
bulk downloads and the API is reserved for detail and deltas. Discovering this before designing
the ingestion was the difference between a working day 1 and a week of 429s.

## Thresholds

The splitting rule needs the amount above which a competitive tender is mandatory. That number is
**a parameter, not a constant**: it is published annually and it can differ by organism. The
default in the code (`1.000.000 CLP`) is the demo value; every finding records the threshold it
was evaluated against, so a dossier cannot be read against the wrong number by accident.

## Legal framing

The system reports relationships present in public records and cites the document each one comes
from. It does not assert misconduct, does not name individuals, and does not rank companies by
suspicion. A finding is a description of a pattern in records that a human then reviews.

This is not a disclaimer bolted onto the product — it is why the product is shaped this way. A
system that accuses needs to be right about intent, which no dataset contains. A system that
produces an evidence chain only needs to be right about records.
