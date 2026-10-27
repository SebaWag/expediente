# Expediente

**A verifiable evidence engine for public procurement.**

> Every finding in an Expediente dossier is footnoted to the exact document and page.
> If a claim cannot be traced to a source, it is deleted, not softened.

Built for the **Meta Global AI Developer Hackathon 2026** (Startup track, theme *Build for Your World*).

---

## The problem

Chile publishes the full lifecycle of every public purchase — tenders, purchase orders, agile
purchases, contracts — through Mercado Público's open API, and the Contraloría publishes its audit
findings. The data exists in public.

What does not exist is the ability to **connect** it and **prove** it. To answer one question —
*"why did this municipality buy the same thing six times this month from the same supplier?"* — an
auditor, a journalist or a council member must cross-reference hundreds of documents by hand, and
then convince someone else that the cross-reference is right.

The State already has anomaly alerts. The gap is not detection. **The gap is a chain of evidence
that a human can sign.**

## What it does

Give it one entity — a buyer unit, a supplier, a tender — and it returns a dossier where every
claim carries a citation:

1. **Ingest** public records into a property graph (`:Organismo`, `:OrdenCompra`, `:Proveedor`,
   `:CompraAgil`, `:Licitacion`, `:Documento`, `:Evidencia`).
2. **Resolve entities** — the same supplier appears with RUT variants, punctuation differences,
   shared addresses and shared legal representatives. Resolution is a graph problem, not a string
   problem.
3. **Run deterministic detectors** — purchase splitting just below the bidding threshold, spend
   concentration, atypical direct awards, post-award contract growth, supplier co-bidding networks.
   A detector is a rule, and a rule is explainable and citable.
4. **Trace every finding** back to its source document and page, and drop the ones that cannot be
   traced.
5. **Benchmark against ground truth** — the Contraloría's published audit findings. *"The system
   independently rediscovered N of the M irregularities in report X, with correct citations."*

## Not a retrieval system. A navigation agent.

To be unambiguous: there is **no chunking, no embedding index, no vector store and no "ask the
documents" surface anywhere in this repository.** That architecture answers *"which paragraph
mentions X"*, which is not the question an auditor has.

Expediente is a **navigation agent**. It walks across datasets that were never joined, follows
typed edges between them, and produces the one thing none of those datasets contains on its own:
**a relation, with a citation**.

The datasets are all public. The joins are not:

| Dataset | Publisher | Joined to the rest today? |
|---|---|---|
| Purchase orders | ChileCompra | only by order code, inside its own file |
| `Compra Ágil` — including who quoted | ChileCompra | only by order code |
| Tenders and awards | ChileCompra | by tender code, when one exists |
| Contracts and modifications | ChileCompra | partially |
| Audit findings | Contraloría | **not at all** — PDFs, no identifiers |
| Ownership, legal representatives, addresses | company registries | **not at all** |

Nobody joins them because the join is not a foreign key. It is a **person** who appears as legal
representative of two suppliers. An **address** shared by three RUTs. A **sequence in time** —
seven orders that only mean something read together. Each of those requires a different kind of
traversal, and none of them is a similarity search over text.

So the agent has exactly three verbs:

| Verb | What it does | Why a retrieval system cannot |
|---|---|---|
| **Navigate** | chooses which edge to walk, and returns the path | a vector index has no edges to walk |
| **Relate** | asserts the connection between records that never referenced each other | similarity is not a relation |
| **Document** | attaches a document and a page to every assertion, or deletes the assertion | a chunk has a text span, not an accountable citation |

The path is the answer:

> A spreadsheet answers *how much*. A vector store answers *which text looks similar*.
> A graph answers **through whom** — and only that answer can be signed.

## Architecture

Frontier models only where they add something. Everything else is code.

| Layer | Runs on | Share of calls | Job |
|---|---|---|---|
| **0 · Deterministic** | Python + graph store | — | Ingest, normalise, detectors, aggregation, paths. Free, 100% citable. |
| **1 · Vision / OCR** | Meta SAM 3.1 + vision | small | Only pages with no text layer. |
| **2a · Extraction** | Meta open weights (Muse Spark Open) | volume | Document → structured rows. Throughput is the whole constraint. |
| **2b · Agent** | Muse Spark, `reasoning_effort=low` | narrow | Chooses which graph query to run. Does not write prose. |
| **3 · Reasoning** | Muse Spark, `reasoning_effort=high` | ~5% | Synthesis of the dossier. One call per case. |

Two rules hold the design together:

- **The model returns data, never decisions.** Layer 2a turns a document into rows. Deterministic
  code decides what counts as an anomaly. No detector output can be hallucinated.
- **Prompt caching is a design constraint.** Tool schemas and the system prompt are stable so the
  cached prefix stays hot across turns.

## Graph model

See [`docs/GRAPH_SCHEMA.md`](docs/GRAPH_SCHEMA.md) for the full node and edge vocabulary.
The invariant that matters:

```
(:Afirmacion)-[:SOPORTADA_POR]->(:Evidencia)-[:EN]->(:Documento)
```

Any `:Afirmacion` without a path to a `:Documento` with a page number is **removed before export**.

## Repository layout

```
expediente/
  domain/       pure models and normalisers (no I/O, no network)
  ingest/       Mercado Público v1 + v2 clients, bulk loaders
  graph/        graph schema and store interface
  detectors/    deterministic, explainable rules
  evidence/     the gate: what a dossier is allowed to say
  dossier/      the signed artifact: citations, rejections, limitations
  cli.py        command line entry point
scripts/        isolated MillenniumDB instance (start/stop/reset)
docs/           architecture, graph schema, scope, backend notes
tests/          unit tests, deterministic, offline
examples/       small synthetic fixtures (fake data, clearly labelled)
```

## Quickstart

No dependencies. Python 3.11+ and the standard library are enough to run the deterministic core
and the tests:

```bash
git clone https://github.com/SebaWag/expediente
cd expediente

python3 -m pytest tests/ -q            # or: python3 -m unittest discover -s tests
python3 -m expediente.cli detect --input examples/oc_synthetic.json --ceiling 1000000
python3 -m expediente.cli gate   --input examples/dossier_synthetic.json
python3 -m expediente.cli dossier --input examples/dossier_synthetic.json \
        --out out/caso.md --generado 2026-10-27
```

The export in [`examples/dossier_export_sample.md`](examples/dossier_export_sample.md) is
what comes out of that command.

The whole chain, from records to a signable document, is one command:

```bash
python3 -m expediente.cli investigate --key 11111111-1 \
        --out examples/investigation_sample.md --generado 2026-10-27
```


Talking to Mercado Público needs a free ticket (requested at
`api.mercadopublico.cl`, arrives by email):

```bash
cp .env.example .env        # then put your ticket in MP_TICKET
python3 -m expediente.cli fetch-agil --desde 2026-10-20 --out data/agil.json
```

## Status

This is a hackathon build in progress. What is honest as of **day 2**:

| Component | State |
|---|---|
| Domain models + normalisers (RUT, legal name, CLP, periods) | ✅ working, tested |
| Graph schema (nodes, edges, evidence invariant) | ✅ defined |
| Graph store — in memory | ✅ reference adapter, used by the tests |
| Graph store — **MillenniumDB** | ✅ working against v1.0.0: nodes, edges, per-edge provenance, native property paths |
| Mercado Público v2 (Compra Ágil) client | ✅ working against the live API |
| Mercado Público v1 (purchase orders) client | 🟡 list endpoint only returns 3 fields — full record needs one call per order, so historical series come from the open-data bulk downloads |
| Detector 1 — purchase splitting | ✅ working, tested |
| Detector 2 — spend concentration (HHI) | ✅ working, tested |
| Detector 3 — suppliers that always quote together | ✅ working, tested |
| Detector 4/5 — late modification and cancellation | ✅ working, tested |
| Evidence gate (claims without a document and page are deleted) | ✅ working, tested |
| Route expressions (RPQ) with inverse, concatenation and alternation | ✅ working, and evaluated natively by the engine |
| Dossier export — markdown + JSON, content-derived id | ✅ working, tested |
| **End-to-end pipeline** — detect → cite from the graph → gate → dossier | ✅ working, tested |
| Entity resolution (shared address / legal representative) | ⬜ not started |
| Contraloría benchmark | ⬜ not started |

## Built during the hackathon

The hackathon rules require new work built inside the event window, and require contributors to be
identifiable. Both are handled by the commit history rather than by a slide:

- The first commit in this repository is dated **2026-10-27**, after the 2026-10-26 kickoff.
- No code in this repository predates the event window. Nothing was copied from earlier private
  projects; where a technique was already known, it was rewritten here, in this history.

## Third-party components

| Component | License | Used for |
|---|---|---|
| Python standard library only | PSF | everything in this repository |
| Mercado Público open API / open data | public data | source records |
| MillenniumDB | GPL-3.0 | property graph store (planned backend) |
| Meta Model API (Muse Spark, SAM 3.1) | — | layers 1, 2a, 2b, 3 |

## Legal framing

Expediente does not accuse anyone of anything. It reports relationships present in public data and
cites the document each one comes from. Findings are descriptive statements about records, not
allegations about people or companies. The intended output is a dossier a human reviews, verifies
and signs — never an automatic verdict.

## License

MIT — see [LICENSE](LICENSE).
