# The MillenniumDB backend

Everything below was verified against **MillenniumDB v1.0.0** (`imfd/millenniumdb`,
`mdb` CLI) running in an isolated container — not read from a README and assumed.
That distinction matters: three of the five facts below contradict what a reasonable
person would expect, and each one changed the code.

## Isolation, first

Expediente's graph must never share an instance with another system. A hackathon
dataset written into a memory graph is not a bug you notice; it is a bug you discover
months later, in a dossier.

| Rule | Why |
|---|---|
| Its own container (`expediente-mdb`) | a demo dataset cannot pollute another graph |
| Its own database folder (`~/expediente-mdb/data/db`) | `mdb import` writes into a folder, and one folder is one database |
| Bound to `127.0.0.1` only | not reachable from the network at all |
| Never `graph.wagnersolutionsai.com` | that host is production memory |

The last rule is enforced in code, not in a convention: `SparqlClient` refuses any
write whose host matches `FORBIDDEN_WRITE_HOSTS` unless `allow_production_write=True`
is passed deliberately. There is a test for it.

## Dialect findings

| # | Expectation | Reality in v1.0.0 | Consequence |
|---|---|---|---|
| 1 | Import takes (db folder, file) | **`mdb import <file> <db_folder>`** — the reverse | the first attempt wrote nothing and said so |
| 2 | `DELETE WHERE { ... }` works | **parser error** | no shorthand deletes |
| 3 | `DELETE { ... } WHERE { ... }` works | **parser error** | no read-modify-write in one statement |
| 4 | `INSERT DATA` / `DELETE DATA` work | ✅ both work, but **each triple needs its own `.`** — a group with only a trailing dot is rejected | `_join_triples` emits an explicit dot per line |
| 5 | Property paths work | ✅ **`^rel`, `rel1/rel2`, `(a|b)`, `+`, `*`, `?` all work** | our route expressions compile to native paths |

Finding 5 is the one that justifies the whole architecture. Finding 3 is why
`SparqlClient.replace()` reads the subject's current triples, deletes them by exact
value, then inserts — two round trips, because the dialect has no one-statement
equivalent.

Finding 4 was a genuine bug in this repository, caught by an integration test: a
single triple inserts fine, and a batch does not.

## Ontology

```
node        urn:expediente:node/{Label}/{key}
            rdf:type exp:{Label} ; exp:{prop} "value"

edge        <src> exp:rel/{RELATION} <dst>                 <- traversable
            urn:expediente:edge/{RELATION}/{hash16}        <- citable
            rdf:type exp:EdgeAnnotation ;
            exp:rel "RELATION" ; exp:source <src> ; exp:target <dst> ;
            exp:{prop} "value"
```

**Why the edge appears twice.** As a plain triple, it is traversable by SPARQL
property paths — the engine's own algorithms, not ours. As an annotation node, it can
carry a citation, because a citation is a statement *about* a statement and a bare
triple has nowhere to put it. Collapsing the two would force a choice between native
traversal and per-hop provenance, and both are required.

Node URIs are quoted (`urllib.parse.quote`), so a key with spaces or a pipe — an
address block, for instance — cannot break the IRI. The annotation URI is derived
from `(relation, source, target)` with a hash, never generated: re-ingesting the same
edge produces the same identifier, which is what `upsert` idempotency rests on.

## The semantic gap that matters

SPARQL property paths use **arbitrary-path semantics**: a node may be revisited.
Our navigator evaluates **simple paths**: no node twice.

```
REPRESENTA_A^/REPRESENTA_A   from supplier A
  engine  -> {A, B}      (A -> person -> A is a valid path)
  adapter -> {B}
```

Returning to the origin is not a finding — an evidence chain that loops back to where
it started proves nothing — so `match(simple_paths=True)` excludes the start node.
That is exact for two-hop patterns and **not** a general proof of simplicity: full
simple-path semantics is not expressible in property paths, so a strict check has to
run after the query.

This is stated rather than hidden because the alternative is a dossier that claims
"route from X to X" as a discovery. The differential test in
`tests/test_millenniumdb.py` asserts both the agreement and the divergence.

## Running it

```bash
scripts/mdb_isolated.sh start     # container + fresh db + import of the seed
scripts/mdb_isolated.sh status
scripts/mdb_isolated.sh stop
scripts/mdb_isolated.sh reset     # drop the database, keep the container

export EXPEDIENTE_MDB_URL=http://127.0.0.1:1234
python3 -m pytest tests/ -q       # integration tests now run instead of skipping
python3 -m expediente.cli query --pattern red_oculta --store mdb
```

Without `EXPEDIENTE_MDB_URL` the suite still passes: the seven integration tests skip
and the deterministic core runs against the in-memory adapter. That is deliberate —
the project must be verifiable by someone who only has Python.

## Bulk load: what a 4.95 M order graph actually costs

Measured on the full national dataset (12 semesters, 129 members, 43 archives), with the
factory on an arm64 host and the graph on the production x86_64 host.

| Stage | Result |
|---|---|
| Aggregate 13,368,668 item rows → orders | **601 s** → 4,950,443 orders, 4.2 GB JSONL |
| Export orders → NTriples (streamed, zstd) | **285 s** → 64,792,071 triples, 8.57 GB raw / 0.45 GB compressed (19.1×) |
| Transfer (arm64 factory → x86_64 graph host) | **56 s** for 416 MB at 7.4 MB/s, direct SSH |
| `mdb import` of the whole graph | **144 s** → 3.1 GB on disk |
| Query: total amount + count over 65 M triples | **< 1 s** |
| Query: top-5 buyers by amount (GROUP BY + ORDER BY) | **4 s** |

### Finding 6 — `mdb import` refuses a non-empty database folder

```
Database folder "/data/db" already exists and it's not empty
```

There is no append mode. A 65 M triple load therefore has to be **one file**, which means
the chunking in `scripts/export_ntriples.py` is a *transfer* device, not a load device:
chunks are concatenated on the target before importing. Worth knowing before designing a
resumable loader, because the resumability has to live outside the engine.

### Finding 7 — the source data contains NUL bytes, and Python version decides the outcome

The ChileCompra exports encode some empty fields as `;\x00;`. The same archive, byte for
byte identical on both hosts, produced:

| Host | Python | Behaviour |
|---|---|---|
| x86_64 | 3.14.8 | tolerates the NUL, ingests |
| arm64 | 3.10.12 | `_csv.Error: line contains NUL`, aborts |

**5,071,739 NUL bytes** across the corpus. They are now removed before decoding — turning
`;\x00;` into an empty field, preserving column alignment — and counted in
`aggregate_stats.json` as `nul_bytes_removed`. Removing them from the reader rather than
from the output is what makes the ingestion independent of the interpreter, and the count
is what makes the repair auditable instead of silent.

The decompressor is configurable for the same reason: `EXPEDIENTE_7Z` defaults to `7z`, and
the arm64 host needed the upstream 7-Zip 26.04 build rather than Ubuntu's p7zip 16.02.

### Identity: 3 order codes repeat across archives

The graph holds 4,950,440 distinct `:OrdenCompra` nodes for 4,950,443 aggregated records:
`584146-6-SE23`, `584146-13-SE23` and `584146-14-SE23` appear in more than one archive.
The URI rule (one node per `codigo`) consolidates them, which is the right answer for a
graph keyed by a natural identifier — and the reason the order count must be quoted as
*records* or *nodes*, never as if the two were the same number.

Graph totals after the load: **64,792,053 triples** (18 fewer than exported, deduplicated
by the engine), 1,400 organismos and 103,220 proveedores.

## Serving the graph to an agent: three query patterns that matter

The MCP server (`python3 -m expediente.mcp.server`) exposes `neighbours`, `find_paths`,
`search` and `citation_trail`. Writing them against the full national graph produced
three findings about the engine, each one a bug before it was a pattern.

### 1. A predicate-free incoming scan cannot use the index

```sparql
SELECT ?p ?s WHERE { ?s ?p <urn:expediente:node/Proveedor/89.862.200-2> . }   -- 8.0 s
SELECT ?s WHERE { ?s <urn:expediente:onto/rel/ADJUDICADA_A> <...> } LIMIT 5    -- 0.001 s
```

That supplier has **44,913** awards. The first form scans; the second binds the
predicate and answers immediately. So `neighbours` counts with a `GROUP BY` (one query,
bounded by the number of *relations*, not edges) and then samples **per relation**, with
the predicate bound. Counts stay exact, the payload stays bounded, and a high-degree
node is no longer a timeout.

### 2. Counting and sampling are different questions

Asking for the edges and then truncating in Python drags the whole neighbourhood across
the wire *and* triggers one provenance lookup per edge — the annotation layer builds a
`VALUES` clause with one URI per edge. Measured on a buyer with 4,015 orders: **over a
minute**. Counting with `GROUP BY` and sampling with `LIMIT` returns the same answer in
about **6 ms** of engine time.

### 3. Paths belong to the engine, not to a Python walk

A breadth-first walk issues one request per visited node: from that same buyer, a
two-hop question is 4,015 round trips. Written as a join over variables (`?s ?p1 ?n1 .
?n1 ?p2 ?dst`, with the relation filters and the simple-path constraints), the whole
answer is one query and comes back in **2.3 s through a Chile→Germany tunnel**.

### And the number that looks bad but is not

Every query measured from the development machine appeared to cost about one second.
The same queries on the host cost **1–6 ms**:

| Where | Trivial query | `GROUP BY` over 4,015 edges |
|---|---|---|
| Host (localhost) | **0.001 s** | **0.006 s** |
| Through the SSH tunnel | 0.896–1.116 s | ~1.1 s |

The engine was never slow; the observer was 12,000 km away. Worth writing down because
"the database is slow" is the conclusion a tunnel will happily sell you.
