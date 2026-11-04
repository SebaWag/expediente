# Las reglas del evento, leídas de la página

Extraído el **4-nov-2026, 03:45** desde <https://cerebralvalley.ai/e/meta-ai-developer-hackathon/details>.
La página es una app Next.js: el contenido no está en el HTML sino en el payload RSC, y se puede
reconstruir sin login. Lo de abajo es textual, no interpretado.

## Criterios de evaluación (esto es lo que hay que leer)

| Peso | Criterio | Qué mide, textual |
|---:|---|---|
| **30 %** | **Impact** | *"the real-world potential of the Project, who benefits, how much it matters, whether it could become something people use, and whether it fits one of the problem statements"* |
| **25 %** | **Use of Meta AI Tooling** | *"how creatively the team used Meta's AI tooling, whether they went beyond a basic integration, and whether they surfaced unexpected capabilities"* |
| **20 %** | Technical Depth & Execution | *"whether the engineering is sound and thoughtfully refined and the Project demonstrates real craft"* |
| **25 %** | **Demo** | *"whether the Project has a working, impressive demo, whether the product holds up live, and the quality of the demo video"* |

**Lectura:** el 55 % es impacto + demo. La arquitectura pesa 20 %. Eso no significa descuidarla —
significa que **la explicación técnica no puede comerse el video**.

## Fechas

```
Oct 26            arranque (kickoff)
Oct 27 – Nov 4    workshops y office hours en Discord
Nov 4, 9:00 PM ET envío de proyectos  → 23:00 en Chile
Nov 4 – Nov 8     judging
Nov 9             ceremonia de cierre, finalistas en vivo
```

*"Dates may be subject to change at Meta's sole discretion. Any change will be posted on this page."*
→ **revisar la página el mismo 4-nov antes de enviar.**

## Dónde se entrega

<https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/submit>

Requiere sesión. **No se pudo leer qué campos pide** (está detrás del login). Hay que abrirlo con
tiempo, no a las 22:50.

## Temas y tracks

- **Build for Your World** — traer los modelos de Meta a tu dominio, resolver un problema que
  conoces de primera mano, *"free up time and mental space, and make something possible that
  wasn't before"*.
- **Push the Frontier** — software, robótica, manufactura, energía, materiales.
- **Indie developer track** — builders individuales y estudiantes; se juzga creatividad, ambición
  técnica y si funciona.
- **Startup track** — equipos y fundadores con producto real; se juzga ejecución técnica y **si
  aguanta en producción**. Un proyecto entra en un solo track.

## Modelos que el evento nombra

`Muse Spark` (serie) · `Muse Wonder` · `Muse Image` · `SAM 3.1` · `Muse Code` (agente de código) ·
y los *open weights* de Meta vía Baseten / Modal / Fireworks.

**Contraste con lo que la cuenta tiene disponible hoy** (`GET /v1/models`, 9 modelos):

```
muse-spark-1.3 (+ 1.1, 1.2, contributor) · muse-wonder-1.0 · muse-image-1.0
sam-3.1 · muse-voice-transcribe-1.0
```

`Glimmer` — nombrado en el texto del evento — **no aparece en la lista**. No se puede usar lo que
la cuenta no expone.

## Lo que esto cambia en el pitch

1. **El impacto es el criterio más pesado, y "who benefits" es literal.** La historia personal
   (padre soltero, cuadrar la casa, auditar sin saber escribir una query) deja de ser color y pasa
   a ser el argumento de mayor peso del video.
2. **"Beyond a basic integration" y "unexpected capabilities" son puntaje directo.** Lo que hay
   que nombrar explícitamente: el agente **elige entre 13 verbos acotados** (no escribe SQL), y
   **decide cuándo parar**; el gate **borra** la afirmación que no puede citar. Eso es lo
   inesperado: un agente que se niega a concluir sin evidencia.
3. **"Whether the product holds up live"** → puede haber demo en vivo en las finales del 9-nov. El
   video no reemplaza tener el sistema corriendo.
4. **El 25 % de "Meta tooling" con un solo modelo usado es un riesgo medible.** Alternativas
   honestas, en orden de costo: (a) nombrar la eficiencia medida del uso de Muse Spark (8 pasos,
   45.414 tokens, caché), (b) una pasada real de `SAM 3.1` sobre páginas escaneadas —la capa 1 del
   README está declarada como "not started"—, (c) nada decorativo. Meter Muse Image para una
   portada sería exactamente lo que el criterio llama *"basic integration"*.

## Lo que NO está en la página

- **Los problem statements.** El criterio de Impact dice *"whether it fits one of the problem
  statements"*, pero la página no los lista. Si existen, se anunciaron en el Discord — que es
  justo donde el bot no pudo entrar. **Pendiente: captura de pantalla del canal donde estén.**
- Los campos del formulario de envío.
- La rúbrica interna de los jueces (solo los pesos).
