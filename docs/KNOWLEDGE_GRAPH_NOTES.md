# Notes from the knowledge-graph literature

What follows is the set of ideas we took from published work — mostly Aidan Hogan's
(Professor at DCC, Universidad de Chile, and associate researcher at IMFD), who is
also a co-author of MillenniumDB, the graph engine this project targets. Each entry
says what the idea is, why it matters here, and **whether we adopted it**, because a
reading list that never changes the code is just decoration.

---

## 1. Regular path queries as the traversal primitive — **adopted**

> Arroyuelo, Hogan, Navarro, Rojas-Ledesma. *Time- and Space-Efficient Regular Path
> Queries*. ICDE 2022.
>
> MillenniumDB paper (Vrgoč, Rojas, Angles, Arenas, Arroyuelo, Buil-Aranda, **Hogan**,
> Navarro, Riveros, Romero, arXiv:2111.01540): the engine combines worst-case-optimal
> joins with *graph-specific algorithms for evaluating path queries*.

**The idea.** The unit of a graph query is not a single hop but a *regular expression
over edge labels*: alternation, concatenation, inversion, and bounded repetition.

**Why it matters here.** Our first traversal was an ad-hoc BFS with a relation filter
— convenient, and impossible to hand to a backend. With RPQ, the product query

```
REPRESENTA_A^/REPRESENTA_A/ADJUDICADA_A^/EMITE^
```

is a single expression that the agent can *name* and the reviewer can *read*:
supplier → shared person → second supplier → award → buyer. That is the hidden
network, written in one line, and it is exactly what MillenniumDB evaluates natively.

**Adopted in** `expediente/navigate/rpq.py`, with the named patterns in `PATTERNS`.

---

## 2. Reification and edge annotations — **adopted**

> Hernández, Hogan, Krötzsch. *Reifying RDF: What Works Well With Wikidata?* SSWS
> 2015.

**The idea.** A statement about a *statement* — its source, its date, its confidence
— cannot be attached to a node. It has to be attached to the edge, either by
reifying the edge into a node or by annotating it directly.

**Why it matters here.** Our product claim is that *every claim is footnoted*. If only
documents carry provenance, then a route is a chain of nodes with one citation at the
end. The honest version is that **each hop carries its own citation**: the registry
record proves the representation, the award resolution proves the sale. A derived
relation with no per-edge provenance is not evidence, it is a guess with a nice font.

**Adopted in** `apply_to_store`: the `REPRESENTA_A` and `TIENE_DOMICILIO` edges carry
`documento_id` and `pagina`, and `Hop.props` surfaces them in the route output.

---

## 3. Provenance-aware reasoning and trust — **adopted as a rule**

> Bonatti, Hogan, Polleres, Sauro. *Robust and Scalable Linked Data Reasoning
> Incorporating Provenance and Trust Annotations*. Journal of Web Semantics, 2011.

**The idea.** Provenance is not metadata to display after the fact; it changes which
conclusions you are entitled to draw. A conclusion reached through weak sources must
be marked weaker, and strength does not increase by composition.

**Why it matters here.** It gives a principled ordering that we can enforce:

| How a relation was obtained | Strength |
|---|---|
| asserted by a source document (a registry says X represents Y) | **asserted** |
| derived by walking asserted edges (X and Y share a person) | **derived** |
| suggested by string similarity (same normalised name) | **candidate — never exported** |

That ordering is why the resolver produces *relations*, *candidates* and *rejections*
as three separate outputs instead of one list with a score.

---

## 4. Object consolidation instead of "entity resolution" — **adopted terminology**

> Hogan, Harth, Decker. *Performing Object Consolidation on the Semantic Web Data
> Graph*. WWW 2007 workshop on Identity, Identifiers, Identification.

**The idea.** The problem on a graph is not deciding whether two names denote the same
entity in the abstract — it is consolidating *objects* that a graph has already
scattered, without destroying the structure that made them distinguishable.

**Why it matters here.** Two suppliers with the same normalised name are legitimately
two suppliers. Merging them is how a naive system fabricates an accusation. Naming the
problem correctly ("consolidation", with candidates) rather than aspirationally
("resolution") is what keeps the output defensible.

---

## 5. Canonical labels for determinism — **partially adopted**

> Project **BLabel**: computing canonical labels for blank nodes in RDF graphs.

**The idea.** If a node has no global identifier, its label must be derived from its
structure in a way that is stable across runs and machines. Otherwise the same graph
gets different identifiers every time it is loaded.

**Why it matters here.** Our keys are already canonical where the domain provides one
(`rut_norm`, `hash_sha256`, `geo_bucket`). Where it does not — a relation, a signal —
the identifier should be derived from its participants rather than generated. This is
tracked as an open item: it is the difference between a dossier that can be re-created
byte for byte and one that merely looks reproducible.

**Status:** not yet implemented for derived relations. Deliberately deferred, not
forgotten.

---

## 6. Worst-case-optimal joins — **noted for the storage adapter**

> MillenniumDB (arXiv:2111.01540): relational techniques plus worst-case-optimal joins
> for path evaluation, benchmarked against Wikidata-scale data.

**The idea.** For multi-hop patterns, the *order* in which you join matters
asymptotically, not just anecdotally. A naive left-to-right evaluation of

```
REPRESENTA_A^/REPRESENTA_A/ADJUDICADA_A^/EMITE^
```

is fine on 12 nodes and pathological on 10 million.

**Why it matters here.** Our in-memory adapter evaluates left to right, which is the
correct choice for a demo and the wrong one for a regional dataset. This is the
strongest argument for moving the traverse into MillenniumDB rather than reimplementing
it: the join ordering is the part we should not be writing ourselves.
