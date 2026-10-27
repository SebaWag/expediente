# Graph schema

The vocabulary lives in code — [`expediente/graph/schema.py`](../expediente/graph/schema.py) — and
this document explains it. `python3 -m expediente.cli schema` prints the same content, which is how
the docs and the implementation stay from drifting apart.

## Why a graph and not tables

An auditor's question is almost never *"how much"*. It is *"through whom"*:

- Which supplier is behind this one?
- Which companies always bid together?
- Which buyer keeps splitting the same purchase?

Those are path questions. In a relational schema they are recursive joins whose depth you have
to know in advance, and the answer arrives as rows that lose the path that produced them. In a
graph the path *is* the answer, and every edge along it can carry its own citation.

## Nodes

Grouped by what they are for, because that is the thing a reviewer needs to judge.

### Who buys, who sells

| Node | Key | Carries |
|---|---|---|
| `:Organismo` | `codigo` (RUT de la unidad) | name, comuna, region |
| `:Proveedor` | `rut_norm` | razón social, normalised name key, giro |
| `:Persona` | `rut_norm` | name, role (socio, representante legal) |
| `:Domicilio` | `geo_hash` | normalised address, comuna |

`rut_norm` is the canonical `12345678-9` form. That is deliberate: the same supplier appears in
the sources with and without dots, with a detached check digit, with a lowercase `k`. If the key
were the raw string, one supplier would be a dozen nodes and every aggregation would undercount.

### What was bought

| Node | Key | Carries |
|---|---|---|
| `:Licitacion` | `codigo` | title, type, estimated amount, publication date |
| `:OrdenCompra` | `codigo` | amount, date, description, UNSPSc category, last modification, cancellation |
| `:CompraAgil` | `codigo` | amount, publication date, quoting suppliers, document ids |
| `:Contrato` | `codigo` | original and final amount, signature date |

`OrdenCompra` carries `fecha_ultima_modificacion` and `fecha_cancelacion` from the first day,
because the post-award-modification detector reads them and needing a second source for it would
have been a design failure.

### What proves it

| Node | Key | Carries |
|---|---|---|
| `:Documento` | `hash_sha256` | type, url, date, page count, origin |
| `:Evidencia` | `id` | page, bounding box, extract, confidence, extractor |
| `:Afirmacion` | `id` | text, support type, verified |

`hash_sha256` as the document key is the idempotency of the whole ingestion: fetching the same
PDF twice produces one node, and re-running a dossier produces the same citations.

### What was found

| Node | Key | Carries |
|---|---|---|
| `:Detector` | `codigo` | name, version, thresholds |
| `:Senal` | `id` | score, severity, detection date, explanation |
| `:Expediente` | `id` | title, state, creation date, signable flag |
| `:Hallazgo` | `id` | type, description, amount involved, severity |
| `:InformeContraloria` | `id` | number, date, organism, subject, url |

`:InformeContraloria` and `:Hallazgo` are the ground truth. They are in the graph — not in a
spreadsheet next to it — so that "did we find what the auditors found?" is a query rather than a
manual comparison.

## Edges

### Contracting lifecycle

```
(:Organismo)-[:EMITE]->(:OrdenCompra)
(:OrdenCompra)-[:ADJUDICADA_A {fecha, monto_clp}]->(:Proveedor)
(:OrdenCompra)-[:DERIVA_DE]->(:Licitacion)
(:Licitacion)-[:CONVOCADA_POR]->(:Organismo)
(:Proveedor)-[:OFERTA_EN {estado, monto_oferta_clp}]->(:Licitacion)
(:CompraAgil)-[:GENERA]->(:OrdenCompra)
(:Contrato)-[:EJECUTA]->(:Licitacion)
```

### Corporate structure

```
(:Persona)-[:SOCIO_DE {participacion}]->(:Proveedor)
(:Persona)-[:REPRESENTA_A {desde}]->(:Proveedor)
(:Proveedor)-[:TIENE_DOMICILIO]->(:Domicilio)
```

These three edges are what turn "two unrelated suppliers" into "one person, two companies, one
address". They are also the reason entity resolution cannot be a string-similarity function: two
different RUTs with the same legal representative are legitimately two suppliers, and the
interesting fact is the shared person, not a merge.

### Evidence chain

```
(:Afirmacion)-[:SOPORTADA_POR]->(:Evidencia)
(:Evidencia)-[:EN]->(:Documento)
(:Documento)-[:ACREDITA]->(:OrdenCompra)
```

### Detection

```
(:Detector)-[:PRODUCE]->(:Senal)
(:Senal)-[:SE_OBSERVA_EN]->(:OrdenCompra)
(:Senal)-[:CORRELACIONA_CON]->(:Senal)
```

### Dossier

```
(:Expediente)-[:CONTIENE]->(:Afirmacion)
```

### Ground truth

```
(:InformeContraloria)-[:CONTIENE_HALLAZGO]->(:Hallazgo)
(:Hallazgo)-[:INVOLUCRA]->(:Organismo)
(:Hallazgo)-[:REDESCUBIERTO_POR]->(:Senal)
```

`REDESCUBIERTO_POR` is the benchmark edge. The metric it produces — findings the system found on
its own, over findings the Contraloría published — is the one number that turns this from an
opinion into a measurement.

## Queries the schema is built to answer

Five queries, each impossible or brutally expensive in a spreadsheet. They are the demo, so they
are the design constraint.

| # | Question | Path |
|---|---|---|
| Q1 | Who is behind this supplier? | supplier → person → other supplier → award → buyer (3 hops) |
| Q2 | Is this purchase being split? | buyer → orders → supplier, grouped by month, all under the threshold |
| Q3 | Which suppliers always bid together? | tender ← bidders, pairwise co-occurrence, then who wins what |
| Q4 | Is one supplier taking an entire unit's spend? | buyer → orders → supplier, concentration index |
| Q5 | Did we find what the Contraloría found? | audit finding → buyer → orders, against the system's own signals |

## The invariant

```python
EVIDENCE_INVARIANT = (
    "Every :Afirmacion must reach a :Documento with a page number through "
    ":Evidencia, via SOPORTADA_POR then EN. Assertions that fail this path are "
    "removed before the dossier is exported, never softened or annotated."
)
```

It is enforced by dropping, and it is tested. `tests/test_graph_store.py` builds a chain with and
without a document and asserts the difference is visible.

## MillenniumDB

The reference adapter is in-memory because the dialect question is still open: MillenniumDB 1.0
ships both a Cypher-like surface and SPARQL, and pinning the schema to a query syntax before
verifying which one the deployed version accepts would be a coin flip. The port is small enough
that the answer costs one adapter, not a rewrite.
