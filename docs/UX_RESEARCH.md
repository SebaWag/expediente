# UX research — what to steal for graph navigation and agent steps

Written 2026-10-31, for the 1–2 Nov polish window. The video is 3 Nov.

## 0 · The constraint that decides everything

The portal is **1,620 lines of vanilla JS, no build step, no dependency**, and the README
says so as a virtue. That rules out D3, Cytoscape, Sigma, G6, React Flow, Cosmograph and
every `npm install` solution. It does **not** rule out the ideas: all of them are patterns —
paths, focus, breadcrumbs, scrubbers — and every one is drawable on a canvas that already has
a camera, a hit-test, `flyTo` and per-label visibility.

Which is the better story anyway: *"the graph UI weighs nothing and loads in one request"* is a
sentence a judge can check with devtools.

## 1 · What the portal does today, audited

| Surface | State |
|---|---|
| Canvas | 2D, ring/brain layout, glow, slow drift, camera with `flyTo` + pulse, hover, select, per-label visibility |
| Expansion | `setData(data, {merge: true})` — the neighbourhood grows in place |
| Chat | natural language → agent → transcript, steps as inline `.paso` rows |
| Node card | detail + rows that are clickable and call `focusOn` |
| Log | flat `<ol>` of events, run selector, "solo pasos importantes" |
| API | 17 endpoints including `/api/subgraph`, `/api/network`, `/api/connections`, `/api/connection-page`, `/api/search`, `/api/investigate` |

### The five gaps that matter, in order

1. **Edges have no names.** The graph shows that two nodes are connected and never says
   *through what*. `ADJUDICADA_A`, `EMITE`, `ACREDITA`, `COMPARTE_DOMICILIO` — the predicate is
   the whole product and it is invisible. A node-and-line drawing without verbs is a constellation.
2. **There is no path.** We have native RPQ in MillenniumDB (inverse, concatenation, alternation),
   and no UI asks the only question a graph answers better than a spreadsheet:
   *"how are these two connected?"*
3. **The recording is not replayable in the UI.** Runs are append-only and the dossier can be
   rebuilt from them with no model, no network and no database. There is no scrubber. This is the
   most valuable unused asset we own.
4. **No focus.** Nothing dims. A selected node glows, but its neighbours are not raised and the
   rest is not lowered, so the eye has nowhere to land.
5. **Steps are a flat list.** No tree, no per-step duration, no link from a step to the part of
   the graph that step changed, no "this is the step that deleted a claim".

## 2 · References, and what each one is actually good at

### Graph navigation

| Project | The idea worth taking | Fits zero-dependency? |
|---|---|---|
| **RelFinder** ([kodymoodley/relfinder](https://github.com/kodymoodley/relfinder), [InteractiveGraph/grapheco](https://grapheco.org/InteractiveGraph/)) | Pick **two** entities, get every connecting path, hop-limited, annotated with the predicates, then **preview/filter/highlight** the path. The literature calls it "revealing relationships"; it exists precisely for the *non-obvious* relation. | ✅ pure convention — 2 selected nodes + one RPQ call + highlight |
| **Gruff** (Franz / AllegroGraph) | Neighbour expansion **by predicate**: you don't click a node, you click an edge type and it grows. Also: a triple list *next to* the picture, so the data is readable as data. | ✅ a predicated expansion menu is a list |
| **Ontodia** ([AKSW](https://github.com/AKSW/ontodia-graph-explorer)) | Elements as **boxes with their properties inside**, not dots. Relations as labelled links. It reads like a diagram of the domain, not a network. | ⚠️ partial — our card panel already does this for one node |
| **Kumu** | Decorated edges and the idea of a **narrative** ("views" that walk through a map like slides). | ✅ the "story" idea is free; the styling is CSS |
| **Obsidian graph view** | The **local graph** with a depth slider, and a grep-style **query filter** over the visible graph. | ✅ depth slider + filter box |
| **Neo4j Bloom / NVL** | Search-first entry, and **"perspectives"**: named lenses that say which labels and which relations are shown. | ✅ a preset is a config object |
| **Linkurious** | The alert → *"open in graph"* handoff: a finding is not a dead end, it is a starting point. | ✅ we have `/api/kanban` and `focusOn` already |
| **Cosmograph / Sigma / G6** | Scale (millions of points, GPU). Not our problem: our scale claim is 64.8 M **triples**, and the map is deliberately a neighbourhood. | ❌ and it would break the dependency claim |

### Agent steps and process

| Project | The idea worth taking | Fits? |
|---|---|---|
| **LangGraph Studio** | The agent as a **graph of nodes** with the live node lit, plus **time travel**: scrub state backwards and watch it re-run. | ✅ this validates the replay idea; ours is stronger because our log is a recording of *claims*, not just tokens |
| **LangSmith / Langfuse** | The **trace tree + waterfall**: every step as a row with duration, input, output, nested calls. Failure is red, not a sentence. | ✅ rows with widths; it is a `<div>` per step |
| **Arize Phoenix / W&B Weave** | Waterfall with the LLM span visually distinct from tool spans — the reader sees *where the money went*. | ✅ same |
| **n8n** | Per-node success/failure colouring. The fastest possible read of "where did it break". | ✅ |
| **OpenHands / Devin** | Steps as **cards with an observation attached** — tool call, what it returned, what it did next. Reading it feels like watching someone work. | ✅ |
| **Claude Code / Cursor** | Diff-style action verbs: `Read`, `Grep`, `Edit` — one word, uppercase, monospaced, then the target. | ✅ |
| **Perplexity / Exa** | Steps stream *while* the answer forms, so waiting is legible rather than blank. | ✅ we already have SSE-ish events; the change is placement |
| **Braintrust / ReviewBench** | Evals next to the artifact: the number and its caveat live in the same card. | ✅ and it matches the placebo-in-the-dossier decision |

## 3 · The three proposals, ranked by what the rubric pays

Rubric: **Impact 30 · Meta tooling 25 · Demo 25 · Depth 20**. UX buys Demo directly and Depth
indirectly, when the interaction *is* an engine capability.

### ① Path between two nodes — "¿cómo se conectan?" ★ highest value

Because it is the one question a graph answers and a table cannot, it is the only interaction
that justifies the entire architecture in five seconds.

- Select two nodes (shift-click), a bar appears: `2 nodos · buscar camino`.
- The portal calls an RPQ: `(:Proveedor)-[:COMPARTE_DOMICILIO|ADJUDICADA_A|EMITE*1..4]-(:Proveedor)`.
- Found paths render **one at a time, hop by hop**, each hop labelled with its predicate.
- Each hop, when clicked, opens the source document for that edge (we have per-edge provenance).
- If no path exists within the budget: **"no hay camino en 4 saltos"**, which is a finding too,
  and the console must say so instead of spinning.

Demo line: *"Estas dos empresas se llaman distinto, tienen RUT distinto y comparten el domicilio
donde se constituyeron. Tres saltos. Aquí está el documento de cada uno."*

### ② Replay with a scrubber — "esto no es un video, es un registro" ★ most defensible

The run log is append-only and the dossier rebuilds from it with no model, no network and no
database. So the UI can play the investigation back: step 1 walks one edge, step 4 deletes a
claim, step 7 emits the dossier.

- One timeline under the canvas, driven by the recording already on disk.
- Dragging it does not re-run anything: it **re-reads the events** and re-applies them to the view.
- The claim that failed turns red and disappears at its own timestamp. The deletion is the show.
- Works offline, in the video, with no API key — which matters on 3 Nov.

Demo line: *"Puedo rebobinar la investigación completa sin el modelo, sin la red y sin la base
de datos, porque el expediente se puede reconstruir desde su grabación."*

### ③ Steps as a waterfall, linked to the graph — "el agente y el grafo son la misma historia"

- Replace the flat `<ol>` with rows: verb, target, duration bar, outcome dot.
- Hovering a row highlights the nodes that step touched; clicking it flies the camera there.
- The four verb families get distinct colours, so the shape of a run is readable at a glance:
  navigate (cyan) · relate (green) · document (blue) · delete (red).
- Budgets already exist in code, so the run can end with *"13 de 20 pasos · 2 afirmaciones
  eliminadas"*.

## 4 · What I would not do, and why

- **No library.** It breaks a documented property and buys nothing we can't paint.
- **No 3D.** It photographs badly and the demo is a 2D screen recording.
- **No new top-level views.** Five surfaces is already a lot for a 3-minute video; the two
  protagonists are the canvas and the steps, and both already exist.
- **No fake progress.** Every animation must be driven by a real event, because the whole pitch is
  that the interface cannot claim more than the recording does.

## 5 · Proposed order for 1–2 Nov

| # | Work | Why first |
|---|---|---|
| 1 | **Edge labels + predicate legend** (①'s foundation) | Cheapest, and without it ① is unreadable |
| 2 | **Path between two nodes** with hop-by-hop reveal + per-hop document | The demo's centrepiece |
| 3 | **Focus/dim on select** + breadcrumb of where you came from | Makes 1 and 2 legible |
| 4 | **Step waterfall linked to the canvas** | Turns the log into the second protagonist |
| 5 | **Replay scrubber** | Most defensible, most expensive; only if 1–4 land clean |
| 6 | Keyboard: `/` to search, `Esc` to focus out, `space` in replay | Small, and reviewers notice |

Each lands with a test where it can (`tests/test_portal.py` already exists) and a line in
`docs/PORTAL.md`, which is where the measurements that shaped the UI live.
