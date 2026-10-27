# Architecture

## The one decision everything else follows from

The output of this system is meant to be **signed by a human who is legally accountable**.
That single requirement eliminates most of the usual agentic architecture.

An assistant that says *"this looks fraudulent"* cannot be signed. A dossier that says
*"these seven orders exist, none exceeded the threshold, here is page 42 of each award
resolution"* can be checked in a minute and signed. So the design question is not
*"how smart is the model"* but *"how much of the path can be made deterministic"*.

Two properties follow:

- **Deterministic steps are repairable, stochastic steps are not.** A bug in a rule fails the
  same way every time, so a unit test fixes it once, forever. A sampling error in a model is
  re-drawn on every run: you can narrow the distribution, you cannot delete the bug.
- **A step that never enters the critical path cannot fail there.** Extraction feeds the
  graph; it does not decide. If layer 2a goes down, the graph, the detectors and the
  citations survive — the dossier gets thinner, not wrong.

## Layers

```
┌────────────────────────────────────────────────────────────────────┐
│ L3 · REASONING      Muse Spark, reasoning_effort=high              │  ~5% of calls
│   Dossier synthesis, hypothesis. One call per case.                │
├────────────────────────────────────────────────────────────────────┤
│ L2b · AGENT         Muse Spark, reasoning_effort=low               │  short loop (≤6)
│   Chooses which graph query to run. Returns a query, not prose.     │
├────────────────────────────────────────────────────────────────────┤
│ L2a · EXTRACTION    Meta open weights (Muse Spark Open)            │  volume
│   Document → structured rows. Throughput is the constraint.        │
├────────────────────────────────────────────────────────────────────┤
│ L1 · VISION / OCR   SAM 3.1 + vision                               │  rare
│   Only pages with no text layer: scanned minutes, photographed      │
│   annexes, signed orders as images.                                │
├────────────────────────────────────────────────────────────────────┤
│ L0 · DETERMINISTIC  Python + graph store                           │  free, citable
│   Ingest, normalise, resolve, detect, aggregate, trace, export.     │
└────────────────────────────────────────────────────────────────────┘
```

### Why the layers are ordered this way

**L0 is the product.** Everything a dossier asserts is produced here. It has no model in it,
no randomness, and no network. It is also the layer that can be run by a judge with
`python3` and nothing else installed.

**L1 is a last resort.** OCR is expensive and lossy. It runs only on pages where the text
layer is genuinely absent, and whatever it produces is marked as `extractor: vision` so the
dossier can say how a page was read.

**L2a buys throughput, not judgement.** The volume problem is real: tens of thousands of
purchase orders, each with items, plus tender rules and award resolutions. Running that on a
frontier model would consume the entire credit budget on work that is closer to parsing than
to reasoning. Open weights at 2M TPM solve the throughput problem; the output is rows.

**L2b is the navigation agent, and it is the one place the model chooses.** It receives the
question and the tool schema and returns **which edge to walk** — which structural query to run
next, given what the previous one returned. It does not write the answer, and it gets no tools
that mutate anything.

This is the difference between an agent and a retrieval system, and it is worth stating precisely:
a retrieval system maps a question to a text span, in one hop, with no notion of what came before.
A navigation agent maps a question to a *traversal* — and each hop is justified by what the
previous hop found. That is why the choice has to be visible on screen: it is a query, it can be
audited, and it can be replayed. The prose it could write cannot.

**L3 runs once.** Synthesis of the final dossier, at `reasoning_effort=high`. Measured at
roughly seven times the cost of a `minimal` call, which is affordable exactly once per case.

## Data flow

```
Mercado Público bulk + API ──┐
Contraloría audit reports ───┼──► normalise ──► graph (nodes, edges)
Tender documents (PDF) ──────┘                    │
                                                  ├──► detectors (rules) ──► :Senal
                                                  │                              │
                                                  └──► evidence tracing ◄────────┘
                                                              │
                                                              ▼
                                                    :Afirmacion ──► dossier
                                                              │
                                                     (no document + page?)
                                                              │
                                                              ▼
                                                          deleted
```

## The evidence gate

```python
(:Afirmacion)-[:SOPORTADA_POR]->(:Evidencia)-[:EN]->(:Documento{pagina})
```

An assertion that fails this path is dropped before export. Not flagged, not downgraded,
not footnoted with a disclaimer — dropped. This is the only place in the system where
information is destroyed on purpose, and it is what makes the citation rate a number we can
report instead of a claim we make.

It is implemented in [`expediente/evidence/gate.py`](../expediente/evidence/gate.py) and every
rejection carries a reason code, because "two claims were dropped" is not a reviewable statement
and "this claim pointed at evidence with no document" is:

| Reason | Meaning |
|---|---|
| `NO_EVIDENCE` | the assertion cites nothing |
| `EVIDENCE_UNKNOWN` | it cites an evidence id that does not exist |
| `NO_DOCUMENT` | the evidence exists but points at no document |
| `DOCUMENT_UNKNOWN` | it points at a document that does not exist |
| `NO_PAGE` | the document exists but the evidence has no page |
| `PAGE_OUT_OF_RANGE` | the page is beyond the document's page count |
| `EMPTY_EXTRACT` | there is a page but nothing was extracted from it |

A claim survives if **at least one** of its evidence entries is whole. The gate reports the
citation rate over the whole dossier, which is the number that goes in the video.

## Storage

`GraphStore` is a small port: upsert node, upsert edge, read node, list nodes, list edges.
`InMemoryGraphStore` is the reference adapter and it exists for a specific reason — the
MillenniumDB dialect is not pinned down yet, and blocking the rules on a storage decision
would have been the wrong order of work. A rule is the product; the store is a choice.

The port requires `upsert` semantics specifically so that re-ingesting a document is a no-op.
Idempotency is not a nicety here: a re-run must produce the same dossier, byte for byte, or
the citation cannot be defended.

## Cost discipline

The blend is deliberate: the cheap layers carry the volume, the expensive layer is used once.

| Layer | Share of budget | Why |
|---|---|---|
| L2a extraction | ~55% | it is the only layer that scales with document count |
| L2b agent | ~20% | bounded loop, six steps maximum |
| L3 reasoning | ~15% | one call per case |
| L1 vision | ~10% | only pages without a text layer |

Guardrails live in code, not in prompts: a hard ceiling on calls per day, a hard ceiling on
spend, and a circuit breaker. An instruction the model *tries* to follow is not a limit.

## What degrades and how

| Failure | Consequence |
|---|---|
| Open-weights credits never arrive | L2a falls back to `reasoning_effort=minimal` on the Meta Model API. Slower, more expensive, same output shape. |
| Meta Model API unavailable | The graph, the detectors and the citations still work. The dossier loses its prose, not its evidence. |
| A document cannot be parsed | The assertion that depended on it is dropped by the gate. The finding survives only if another document supports it. |
| A detector is wrong | It is a rule, so it is reproducible: one test, one fix, permanently. |
