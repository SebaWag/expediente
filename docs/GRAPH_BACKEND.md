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
