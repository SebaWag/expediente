# Revisión del grafo de Expediente — 4-nov-2026

Documento de decisión. **No se toca código hasta que la opción esté elegida.** Todo lo que sigue
está verificado en disco y en producción, no de memoria.

---

## 1. Qué es esto, en una línea

Un portal de auditoría de compras públicas de Chile: pregunta en castellano, el agente navega un
grafo de 4,95 millones de órdenes y devuelve un expediente **donde cada afirmación cita su
documento**. El grafo es la prueba, no el adorno.

Eso define el criterio del dibujo, y es lo que nunca escribimos:

> El grafo no tiene que impresionar. Tiene que dejar ver **una relación que el ojo pueda
> sostener**, y declarar todo lo que dejó fuera.

---

## 2. Auditoría: lo que hay de verdad

| Hecho | Dato verificado |
|---|---|
| Código | 14.310 líneas Python · 19 módulos (`agent`, `detectors`, `evidence`, `graph`, `navigate`, `dossier`, `mcp`, `portal`…) |
| Tests | 35 archivos · **388 passed, 17 skipped** |
| Endpoints del portal | **18** (`/api/overview`, `/subgraph`, `/signals`, `/kanban`, `/chat`, `/path`, `/runs`…) |
| Front | `graph.js` 34,8 KB · `graph3d.js` 10,6 KB · `app.js` 52,7 KB · `styles.css` 25,8 KB |
| Datos en producción | 957 nodos · 1.276 aristas · **truncated: true** |
| Composición del landing | 565 señales · 177 órdenes · 177 documentos · 16 proveedores · 16 organismos · **6 detectores** |
| Latencia `/api/overview` | **2,11 s** (cacheado 300 s en el servidor) |
| Grafo en el motor | 64,79 M de tripletas · 4.950.440 órdenes · 103.220 proveedores · 1.400 organismos |
| Despliegue | Hetzner + Traefik + TLS · contenedores `expediente-mdb`, `expediente-api`, `expediente-portal` (`--read-only`) |

**Lo que falta de la lista del 27-oct:** el endpoint `/api/events` (paso, tool, ms, resultado) **no
existe** — los 18 endpoints no lo incluyen. El registro existe (`runs`, `/api/runs`), pero no en
formato de evento por paso consumible por la UI.

### El dato que explica todo el problema del dibujo

**565 de los 957 nodos son señales.** El landing no es "un grafo del Estado": es una nube de
señales con seis detectores colgando. Ningún layout, ningún color y ningún zoom arreglan eso,
porque el problema no es visual: es **qué se elige dibujar**.

---

## 3. Los cuatro intentos y por qué falló cada uno

| # | Intento | Qué se buscaba | Por qué falló | Evidencia |
|---|---|---|---|---|
| 1 | **Anillos** (canvas propio) | Que se entendiera el sistema en capas | Se leía como diagrama orbital, no como red. No era un bug: era la forma | Capturas del 21-oct |
| 2 | **Force-directed con todo** | Que se viera como "cerebro" | A 957 nodos es una bola de pelo | Revisión de la captura en vivo |
| 3 | **Foco de 130** | Que se leyera | **Decisión mía, no consultada.** Esconde 827 nodos; el usuario percibe que faltan | *"faltan la mayoría de los nodos"* |
| 4 | **3D + esferas** | Volumen y libertad de cámara | Funciona, pero no resuelve el problema de fondo: sigue siendo una nube de señales | 3D verificado en vivo, 0 errores |

### Los tres errores técnicos reales (documentados para no repetirlos)

1. **`fcose` con `eles` deja los nodos en grilla.** El layout no relaja; el grafo se ve como un
   tablero. Sin `eles` funciona. *Medido: 130 nodos → 85 valores únicos de X con `eles`; 127 sin.*
2. **`d3ReheatSimulation()` rompe el render** en esta versión de 3d-force-graph (excepción dentro
   del tick). Se reinicia reenviando `graphData`.
3. **Cytoscape no acepta `data()` en los stops de un gradiente.** Las reglas van con color literal.

También: **redimensionar un lienzo oculto lo deja en 0×0** y revienta el motor.

---

## 4. Qué dice la investigación

**Hay umbrales medidos.** *Scalability of Network Visualisation from a Cognitive Load Perspective*
(arXiv 2008.07944) determina los puntos a partir de los cuales conviene **limitar nodos y aristas
en pantalla o cambiar a una representación resumen**. No es opinión de diseño: es carga cognitiva.

**Cómo lo resuelven los que lo hacen bien:**

- **Obsidian** abre en la **red local** del nodo activo (decenas de nodos), y el grafo completo es
  un gesto deliberado. Nunca arranca en los 800.
- **Gephi / Neo4j Bloom** separan **explorar** de **ver todo**: se filtra por tipo y se expande por
  saltos; el "todo" es un modo aparte, con layout barato.
- **sigma.js / cosmos.gl** resuelven el problema de *render* (WebGL, cientos de miles de nodos),
  **no el de lectura**. Dibujar 100.000 nodos rápido sigue siendo un hairball rápido.

**La conclusión que importa:** ninguna herramienta seria arranca mostrando el corpus. Arrancan en
**un caso** y ofrecen el todo como acción explícita. Eso es exactamente lo que el pie de nuestro
lienzo ya declara ("mostrando 130 de 957") pero al revés: mostramos una muestra arbitraria en vez
de **el caso que el producto existe para encontrar**.

---

## 5. Criterios de aceptación (medibles, no opinables)

| # | Criterio | Cómo se mide |
|---|---|---|
| 1 | Primer dibujo en menos de 3 s | `performance` hasta `layoutstop` |
| 2 | **El hallazgo se ve sin zoom**: detector → señal → orden → proveedor → domicilio | 1 ruta legible en pantalla a 1600×1000 |
| 3 | Etiquetas sin solaparse | 0 cruces de bounding box |
| 4 | Errores JS | 0 en consola y `pageerror` |
| 5 | Todo lo que se recorta, se declara | texto en pantalla con "N de M" |
| 6 | Llegar al corpus completo en **1 clic** | botón existente y funcionando |
| 7 | Giro 3D sin caídas | 10 s de arrastre, 0 errores |

---

## 6. Las tres opciones

### Opción A — "Ver todo por defecto" (957)
Dibujar el corpus completo al abrir, layout concéntrico por grado, sin etiquetas.

- **A favor:** honesto, impresiona magnitud, cero decisiones escondidas.
- **En contra:** no se lee nada; 565 señales idénticas. Es el intento #2 otra vez.
- **Costo:** 30 min. **Riesgo:** alto (vuelve el hairball).

### Opción B — Foco actual, pero explicado (130 de 957)
Mantener lo desplegado y arreglar la comunicación: pie más visible, y que "ver todo" sea obvio.

- **A favor:** ya está hecho; se lee.
- **En contra:** el foco es **arbitrario** (los 130 por tipo y grado). No cuenta una historia: es
  una muestra. Y sigue produciendo la sensación de "faltan nodos".
- **Costo:** 1 h. **Riesgo:** medio (el problema de fondo sigue).

### Opción C — "El caso primero" ⭐
El landing dibuja **un caso real** (~35-45 nodos): el detector, su señal, las órdenes que observa,
el proveedor, el domicilio compartido y el documento que lo acredita. Un pie/botón explícito:
**"este es 1 de 85 casos · ver el corpus (957)"**, que cambia al layout concéntrico.

- **A favor:** en 10 segundos se entiende qué hace el producto *y* se puede sostener una afirmación.
  Es lo que hacen Obsidian, Gephi y Bloom. Y el "y hay 957" queda como remate, no como entrada.
- **En contra:** hay que elegir el caso semilla y que el backend lo sirva (ya existe `/api/signals`
  y el caso Puerto Montt está materializado).
- **Costo:** 3-4 h. **Riesgo:** bajo (usa endpoints que ya existen y están probados).

---

## 7. Recomendación

**C**, con **B** como estado intermedio: ya está desplegado y se lee; no lo rompemos. Sobre esa
base, el landing pasa de "muestra arbitraria" a "un caso y su total declarado".

Y una regla de proceso, que es la que faltaba:

> **Un cambio visual = una decisión escrita + una medición.** Sin criterio de aceptación no se
> despliega, aunque "se vea mejor". Los cuatro intentos fallaron por saltarse esto, no por falta
> de talento.

---

## 8. Lo que NO se toca

- El backend, los tests, el motor, los detectores, el gate de evidencia.
- Los 18 endpoints y el contrato de datos.
- El registro y el rebobinado (son el diferencial del producto).

---

## 9. Estado frente al deadline (4-nov 23:00)

| Bloque | Estado |
|---|---|
| Producto y datos | ✅ listo y verificado |
| Portal | ✅ funcional, 0 errores JS, UI en 8/10 (2D) y 7,5/10 (3D) |
| `/api/events` | ❌ pendiente (registro por paso) |
| **Video / PPT del pitch** | ❌ **no existe, y es lo único que no se improvisa** |

**Reparto propuesto del día:** 2 h para cerrar la decisión del grafo (opción elegida), 1-2 h para
`/api/events` si se quiere mostrar el registro en vivo, y **el resto al video**.
