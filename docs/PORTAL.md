# The portal

`https://investiga.wagnersolutionsai.com`

Five surfaces over one graph. This note is the operator's view: what each one is
for, what it may and may not claim, and the measurements that shaped it.

```
INICIO      totals, what this is, three ways in
SEÑALES     a board whose columns are derived from the run log
MAPA        a force-directed neighbourhood, never a sample of the whole
INVESTIGAR  natural language -> named verbs -> dossier with citations
REGISTRO    the event log, live, readable without the model
```

## Why it is a separate process from the API

`expediente.api.server` is read-only **by construction**: the store is only ever
asked `select`, and that is what makes the sentence *"exposing this on the internet
cannot write to the graph"* true. The portal writes — runs, event logs, dossiers. If
the two lived in one process, that sentence would be false and the README would be
lying.

So: same graph, same tool functions, two containers. The portal's root filesystem is
mounted `--read-only`; the only writable path is `/data/runs`.

## The board is a view, not a database

Four columns, all derived from recordings:

| Column | Meaning | Derived from |
|---|---|---|
| `sin_revisar` | nothing has touched it | absence from every run |
| `en_analisis` | a run is executing now | the registry of live threads |
| `con_evidencia` | a claim citing it was accepted | `claim.accepted` events |
| `descartada` | a claim citing it was rejected | `claim.rejected` events, with the reason |

A card cannot be dragged. Dragging would let the board report intentions; this board
reports work. If a card is in `con_evidencia`, there is a recording that says which
claim, which run, and which nodes backed it.

## The map, and two measurements

Building a graph view over 64.8 M triples taught two things the hard way.

**One query per node is unusable.** The first implementation walked the port
neighbour by neighbour: correct, and **18.4 s for a single hop**. Batching to two
queries per hop — every outgoing edge from the whole frontier, then every incoming
edge — brought that to **4.1 s**, and 1.15 s over HTTPS in production.

**A map of volume answers nothing.** With a supplier that has 2,044 orders, the
canvas filled with 68 `:OrdenCompra` nodes and one edge worth seeing: the address it
shares with another company. Orders are now capped per expansion and structural
labels (`Domicilio`, `Persona`, `Proveedor`, `Organismo`, `Senal`) are ranked first.
The neighbourhood of the star case now draws seven nodes, and the relation is the
thing you see.

The canvas always reports what it left out: `truncated: true` and a line of text
saying the drawing is a window and the total is far larger.

## The agent

Nine verbs, fixed order, no query language:

```
buscar_en_grafo  vecinos  caminos  red_proveedor  citas
buscar_en_web    leer_fuente  afirmar  terminar
```

The model picks one per turn. `afirmar` is the only way a conclusion enters the
output, and it is a gate: a graph claim must cite nodes that exist in the store, a
web claim must quote the fetched bytes verbatim. Everything else is rejected with a
reason and shown in the dossier under *"qué se descartó, y por qué"*.

Budgets are code, not prompt: 9 steps, 12 model calls, 300 s, 4 web fetches. A run
that hits one closes with what it has and records `budget.exceeded`.

### Live run, production, muse-spark-1.3

```
PREGUNTA  ¿Qué relación hay entre Cardiotec Vascular Limitada y Cardiotec Vascularis SpA?
RESULTADO 8 pasos · 24 s · 1 afirmación · 0 descartes
CITA      Proveedor/76.264.138-0, Proveedor/77.290.194-1, Domicilio/157d5c86c514b86c
SALVEDAD  "solo es una pista para revisar y no prueba que sean el mismo grupo"
VACÍOS    "no se confirmó si comparten socios o representantes: está fuera del grafo"
```

The caveat travels with the claim. That is the product.

## Operating it

```bash
# deploy (on the host)
cd /opt/expediente/portal && bash scripts/deploy_portal.sh

# logs
ssh hetzner-ws 'docker logs expediente-portal --tail 50'

# the runs live here, owned by uid 10001
ssh hetzner-ws 'ls -la /opt/expediente/runs'

# re-read an investigation without the model, the network or the database
ssh hetzner-ws 'cat /opt/expediente/runs/<id>/expediente.md'
ssh hetzner-ws 'cat /opt/expediente/runs/<id>/events.jsonl | head -40'
```

`/opt/expediente/portal.env` holds the three model variables and is `600`. It is not
in the image and not in the repository.

## Known limits

- The portal is single-user. Runs are threads in one process, not a job queue.
- The board reads the last 60 runs; older ones are still on disk and in the API.
- Web search parses DuckDuckGo's HTML. When the markup changes, the parser degrades
  to fewer candidates — and raises `SearchError` rather than returning an empty list,
  because an empty list and a broken search must never look the same to an agent.
- The map caps at 70 nodes. That is a rendering decision, stated on screen.

---

# Two cameras, and the measurements that chose them

Added 2026-11-01. The canvas now has a **2D map** and a **3D walk**, toggled from the header
(stored in `localStorage`, default 2D). Neither was chosen because it looked good.

## What each one is for

| | 2D | 3D |
|---|---|---|
| What it answers | *where does this sit in the system* | *what does this neighbourhood look like from here* |
| Layout | brain silhouette, five rings | shells for a corpus, springs below 80 nodes |
| Node encoding | colour + shape | colour + shape + size + depth fog |
| Cost | 0 dependencies, 1 file | same file, `_projectionOf` + painter's order |

## The number that justifies 3D existing

The two-company case — the case the demo is built around — is four nodes. Laid out in 2D it
collapses into a **horizontal line 8 px tall**: two suppliers, a shared address and a shared
representative, all on one axis, unreadable. In 3D the same four nodes cover **50 % of the
canvas** with every name legible.

```
camino (4 nodos)   2D   caja 650 × 8 px    cobertura  0,3 %
camino (4 nodos)   3D   caja 1008 × 719    cobertura 50,3 %
vecindad (9 nodos) 2D   caja 605 × 421      cobertura 17,7 %
vecindad (9 nodos) 3D   caja 893 × 748      cobertura 46,4 %
```

That is an argument, not a preference. The counter-argument is also recorded: over the **whole
corpus** (318 nodes in the test bench) both modes are a hairball, and 3D adds occlusion on top —
which is why the portal must never open on the corpus. See the last section.

## Four visual ideas, measured

| Idea | Outcome |
|---|---|
| **Semicolon-free fit**: divide world span by available width | ❌ ran **176 px off the right edge**; the perspective factor is never 1. Replaced by a three-pass fit that projects, measures in pixels, and corrects |
| **Lift the route toward the camera** so the story sits in front | ❌ moving a node in depth changes its projected size, and it pushed one of the two companies **off the frame**. Rejected; the route is marked with a ring and a brighter edge |
| **Labels everywhere** | ❌ sixty overlapping pills. Replaced by priority + a shared collision registry |
| **Up to 80 nodes → springs in 3D** instead of shells | ✅ shells answer "where in the system", which is wrong for nine nodes: they cluster on one side and leave **80 % of the canvas empty** |

## Encoding: colour is not enough

Eleven labels cannot ride on colour. A projector loses three of them, a colour-blind reader loses
all of them, and a screenshot in a slide loses whatever is compressed. So each label has a
**shape** as well — hexagon for a buyer, circle for a supplier, square for an order, page for a
document, diamond for an address or a person, triangle for a signal, pentagon for a claim — and a
**Spanish name** in the legend, because the reader signs dossiers, not schemas.

`tests/test_portal.py::test_every_label_is_encodable_and_nameable` fails if a label is added to one
table and not the others, and `test_the_canvas_script_parses` runs `node --check` on the file,
because a syntax error here is a blank screen and nothing else in CI would notice.

## Labels are a budget

Text is placed by priority — detector, selection, hovered, route, then by degree — and a label that
would overlap an already-placed one is **dropped**, not drawn on top. Every node reserves its own
circle before any text is placed, because the first version wrote `ACREDITA` straight across a
supplier's dot.

Two exceptions, both learned from reviews:

- **A graph of 20 nodes or fewer names everything.** Hiding the fourth name in a four-node picture
  leaves half the finding unreadable.
- **The fit reserves the legend's corner.** Fitting into the whole canvas put nodes underneath the
  key; the fit now happens in the rectangle that is left over, and it budgets ~70 px per node for
  the label that hangs below it.

## How the reviews went

Three rounds of a vision model reading a 1600×900 capture of the two demo screens, asking for a
score and the worst remaining defect:

| Round | Score | The defect that moved it |
|---|---|---|
| 1 | 3/10 | one of the two companies **off frame**, `COMPARTE_DOMICILIO` hidden behind the legend |
| 2 | 5/10 | both companies visible, but the person node rendered **nameless** |
| 3 | 6/10 | all four named, relations readable |
| 4 | **7/10** | nothing unreadable; the person node still sits close to the top-right corner |

The reviews were run on `muse-wonder-1.0` through the Meta Model API, which is worth recording for
one reason: the first attempt returned an **empty answer** because all 900 completion tokens went
to reasoning. Exactly the finding in `docs/SWEEP.md`, reproduced by accident.

## The rule this leaves behind

**The portal must not open on the corpus.** A 300-node overview is a hairball in every dimension;
the demo, and the product, start at *one case* and walk outwards. The corpus is a claim in the
README, not a first screen.
