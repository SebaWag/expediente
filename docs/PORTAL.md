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
