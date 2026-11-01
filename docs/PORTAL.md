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

---

# La ruta entre dos nodos, y el waterfall de pasos

Añadido 2026-11-01. Estas dos piezas son las que hacen que el grafo y el agente sean **la misma
historia** en vez de dos pantallas que se miran.

## `GET /api/path` — la pregunta que un grafo contesta y una planilla no

```
GET /api/path?desde=Proveedor/76.264.138-0&hasta=Proveedor/77.290.194-1[&saltos=4][&caminos=3][&rel=A,B]
```

Búsqueda en anchura sobre las aristas de relación, **dos consultas por salto** (toda la frontera
hacia afuera, después toda la frontera hacia adentro). Medido en producción:

| | |
|---|---|
| Las dos Cardiotec, a través de un domicilio compartido | **1,0 s · 2 saltos · 3 nodos** |
| Dirección | cada tramo dice si se recorrió **en reversa** |
| Cota | saltos, frontera (220) y nodos visitados (4.000), y el tope alcanzado se reporta |
| Sin camino | se devuelve el motivo con el número de saltos probados, **no una lista vacía** |

Tres propiedades, cada una porque la alternativa miente:

1. **Acotada.** Un BFS sin cota sobre 64,8 M de tripletas es una denegación de servicio con buena
   interfaz.
2. **No asume dirección.** `A le paga a B` y `B le paga a A` no son el mismo hallazgo; el tramo
   guarda cuál de las dos direcciones está escrita en los datos.
3. **Sin camino es una respuesta.** Una lista vacía se lee como «no están relacionados», que es
   otra afirmación y normalmente es falsa.

Funciona en los dos backends (cliente MillenniumDB y adaptador en memoria), así que los tests
ejercitan el bucle real y no un mock.

## El waterfall

```
LEYENDA   ● modelo   ● grafo   ● documento   ● afirmación   ● descarte

planear           1.761 tokens                       2.712 ms  ██████████ 89%
pensar            3.571 tokens                       3.012 ms  ██████████ 99%
buscar_en_grafo   1 nodo(s)                            24 ms   ▏      3%
vecinos           50 vecino(s)                         50 ms   ▏      3%
perfil_de_gasto   2.145 órdenes · $108.097.415.212    136 ms   ▏      4%

8 pasos · 1 afirma. · 44.732 tok · 16,8 s (96% modelo)
```

**Los pasos se emparejan, no se listan.** El registro emite `tool.call` + `tool.result` y
`model.request` + `model.response`; solo el segundo de cada par trae duración. Pintar eventos uno
por uno muestra cada paso dos veces: una sin tiempo y otra sin verbo.

**El modelo es un paso más, con su propio color y sus tokens** — y ese detalle produce el número
que sostiene la tesis completa, medido con la grabación del propio sistema: **96 % del tiempo de
una corrida es el modelo pensando; el trabajo en el grafo es el 4 % restante.** Es la misma
posición que el 98,99 % de cache hit del README: medido, no afirmado.

Hover en una fila enciende en el canvas los registros que ese paso tocó; clic vuela hasta ellos.

### Los tres defectos que costó

| Defecto | Causa |
|---|---|
| Barras de duración vacías | El filtro «solo importantes» descartaba `tool.result` **antes** de emparejar, y ahí vive `duration_ms`. Emparejar primero, filtrar después |
| `0 señal(es)` tres veces seguidas | Se leía como una falla. Una respuesta vacía es un hecho sobre el registro: ahora dice *"sin resultados"* en voz baja |
| Ninguna fila apuntaba al grafo | `tool.call` guarda sus argumentos en `payload.arguments`, así que `payload.nodo` era siempre `undefined` |

Y una carrera: el panel se reconstruye en cada evento, la fila bajo el puntero se destruye y
dispara `mouseleave`. El paso apuntado vive en el estado y se re-aplica después de cada render.

## La columna de la derecha muestra una cosa a la vez

Camino y registro son dos vistas del mismo trabajo, y ocupaban el mismo sitio: se tapaban entre
sí. Una revisión lo describió como *"el menú de filtros tapa el panel"*, y el «filtro» era el
registro. Ahora abrir uno cierra el otro, el chat se fue a la izquierda y más angosto (760 → 520
px) para no ocupar el centro del lienzo, y la leyenda del canvas se levantó 96 px para no pisar la
letra chica legal.

---

# El rebobinado

Añadido 2026-11-01. Es la pieza que convierte «el expediente se puede reconstruir desde su
grabación» en un gesto que se puede arrastrar.

```
en vivo .......... en vivo · la grabación completa, 22 pasos
rebobinado a 11 .. rebobinando · paso 11 de 22 · sin modelo, sin red, sin base de datos
                   · 2 registros tocados
```

Arrastrar la línea **no vuelve a ejecutar nada**. Se releen los eventos que ya están en memoria y
se re-aplican a la vista:

- los registros que **existían** hasta ese punto se encienden en el canvas;
- los que tenían una afirmación **descartada** hasta ese punto se marcan en rojo;
- las filas del waterfall que **todavía no ocurrieron** se apagan al 28 %, así el futuro no se lee
  como si hubiera pasado;
- la fila del paso actual queda resaltada.

Y lo que no hace, que es el punto: **no llama al servidor**. Ni modelo, ni red, ni base de datos —
la misma frase que el proyecto usa para su replay de línea de comandos, ahora sobre la pantalla.
Es lo único de esta interfaz que funcionaría con el servidor caído, y eso es una propiedad del
diseño, no una anécdota.

## Las dos reglas que costó

| Regla | Por qué |
|---|---|
| **En vivo no se pinta el rebobinado** | Iluminar «todos los registros tocados» durante una corrida normal convertiría el resaltado por defecto en permanente, y entonces el resaltado dejaría de significar algo |
| **Llegar al final es volver a estar en vivo** | Quedarse pegado al último paso haría que la corrida siguiente arrancara mirando el final de la anterior |

Controles: la barra, el botón ▶/⏸ y la **barra espaciadora** (que no se dispara si el foco está en
el compositor del chat).
