# Shooting script — Expediente (3:00)

Producción. **Video en inglés**, demo grabado en una sola toma de pantalla, sin cortes que escondan
fallos. Si algo falla en vivo, se ve el fallo: el producto se sostiene en que la interfaz no puede
afirmar más de lo que el run hizo.

**Formato:** 1080p · ventana del portal 1600×1000 (el criterio de aceptación del landing) ·
sin música · subtítulo en castellano solo en el cierre.

---

## 1. La voz: lo que se midió, y la decisión que queda

**Medido con la key, 4-nov:** la API de Meta expone **9 modelos** —
`muse-wonder-1.0`, `sam-3.1`, `muse-spark-1.3` (+1.1, 1.2, y las variantes *contributor*),
`muse-image-1.0` y `muse-voice-transcribe-1.0`—. **Ninguno sintetiza voz**: el único de voz
transcribe. `/audio/speech`, `/tts`, `/speech`, `/audio` y `/audio/*` devuelven **404** con
credencial válida. **No hay TTS de Meta disponible.**

Así que hay que elegir, y las dos opciones son honestas:

| | **A (mi recomendación)** | **B** |
|---|---|---|
| Voz | **la tuya, todo el video** | tuya al abrir y cerrar; sintética en el medio |
| Riesgo | el acento | nombres chilenos mal dichos, y suena a anuncio |
| Divulgación en pantalla | ninguna: no hay nada que divulgar | `narration: <proveedor real>` |
| Ventaja | el jurado ve a una persona, no un producto | ritmo perfecto, cero titubeos |

**Voto por A**, por una razón concreta: el video ya trae el caso, los números y el documento
abriéndose. Lo único que ningún otro equipo puede tener es que detrás haya un tipo que cuadra su
casa mes a mes. Una voz sintética en el medio cambia *"esto lo construyó alguien"* por *"esto lo
produjo una agencia"*.

Si eliges B, el crédito dice el proveedor real. **Nada de "Muse Spark TTS": no existe**, y un
crédito falso en un video sobre trazabilidad sería la peor firma posible.

> Dato aparte que sirve: `muse-voice-transcribe-1.0` sí existe. Si grabas tu propia voz, ese modelo
> transcribe el audio y los subtítulos salen medidos, no tipeados.

## 2. Guion, escena por escena

Presupuesto: ~430 palabras de voz para 180 s (≈145 palabras/min, el ritmo de un TTS bien dirigido).
**Primero se graba la voz; el video se corta después al ritmo de la voz.** Nunca al revés.

### Escena 1 · 0:00–0:16 · Wagner en cámara

**Plano:** cámara, plano medio, fondo neutro. Sin logo, sin slide.

> "I'm a single father of four. At home I have to account for every peso. In Chile, public
> purchases are public — and nobody can read them. So I built the thing I needed."

### Escena 2 · 0:16–0:34 · TTS · el problema, con el número

**Pantalla:** 10,302,746 purchase orders · 240 archives · 74,777,686 rows · 5 years.

> "Ten million purchase orders. Five years. Two hundred and forty archives that were never meant
> to be read together. The State already flags anomalies. What does not exist is the evidence an
> auditor can sign."

### Escena 3 · 0:34–1:08 · TTS · la pregunta y el plan

**Pantalla:** se escribe la pregunta en el chat del portal. Aparece el plan y luego el waterfall.

> "So I ask in plain Spanish: why does one municipality concentrate 82 percent of its spending in
> a single supplier? Muse Spark writes the plan, naming tools that exist, and picks one per turn.
> It does not write SQL. It never touches the graph directly — it chooses among thirteen bounded
> verbs, and the code compiles each one to a traversal with limits. That is why eight steps are
> enough, and why the number can be reproduced tomorrow."

### Escena 4 · 1:08–1:48 · TTS · el caso y la cadena

**Pantalla:** la señal. La tarjeta con la cadena. Se abre la primera orden y luego el documento.

> "Here is the signal: one supplier, 82 percent of the spending. And here is the part that
> matters. Every claim carries the order, the amount, the date, and the official record with its
> hash. There is no loose conclusion. Two orders cited, both with a document. If one of them had
> no document, the gate would delete the claim rather than soften it."

### Escena 5 · 1:48–2:14 · TTS · lo que el sistema declara (el beat)

**Pantalla:** zoom lento a la cadena de evidencia. Texto exacto que muestra el portal hoy:

```
Cadena completa · 2 de 8 órdenes (las del proveedor) · 81,5 % del gasto · 2 de 2 con documento
```

> "And it tells you what it left out. The card does not say eight orders. It says **two of eight** —
> the supplier's — and that they account for 81.5 percent of the spending. The other six are not
> linked, and that is stated on screen, not in a footnote."

### Escena 6 · 2:14–2:38 · TTS · el contraste y la escala

**Pantalla:** cámara 3D alejándose: 565 signals, organisms, suppliers. Luego, en texto grande:
**"one purchase code at a time"**.

> "Two thousand and sixty-five buyers. One hundred and thirteen thousand suppliers. The same
> interface for all of them, with no per-entity engineering. A dossier a human can sign, because
> the gate deletes whatever it cannot cite. This is what agents powered by Muse Spark are for:
> not to describe the world, but to change it — one auditable purchase code at a time."

### Escena 7 · 2:38–3:00 · Wagner, su voz · el cierre

**Pantalla:** negro. Aparece, línea por línea, tu frase original en castellano como subtítulo.
Tu voz en inglés, mirando a cámara.

> "If I have to account for every peso in my own house, I could not accept that a municipality
> does not. That is the whole project: the person who has the question — not the one who knows how
> to write a query — should be able to audit."

**Subtítulo (castellano, tu texto original):**

> *"Como padre soltero de cuatro hijas, vivo mes a mes gestionando recursos limitados. Si yo tengo
> que rendir cada peso en mi casa, no puedo aceptar que un municipio no rinda los suyos."*

---

## 3. Dirigir la voz (si eliges B) o grabarte (si eliges A)

**Lo que ya no hay que probar:** no hay TTS de Meta. La sección 1 tiene la medición.

**Si eliges A (tu voz):**

- Graba la locución **en una sola pasada**, de corrido, con el guion a la vista pero sin leer
  palabra por palabra: si suena leído, pierde.
- Ritmo objetivo: **145 palabras por minuto**. Con 430 palabras son 2:58. Si te sale en 3:20, se
  recorta guion, no se acelera el audio.
- Graba tres tomas y elige por **claridad de los números**, no por fluidez: "two of eight" y
  "eighty-one point five" son lo que el jurado va a repetir.
- Pasa el audio por `muse-voice-transcribe-1.0` y compara con el guion: donde el modelo transcriba
  algo distinto, se entiende mal. Es una prueba de dicción medida, no una opinión.

**Si eliges B (voz sintética en el medio):**

- Genera **un archivo de audio por escena**, no uno solo de 3 minutos. Así se rehace la escena 4
  sin volver a narrar todo.
- Una sola voz para todo el medio, ritmo pausado, sin énfasis publicitario: el único énfasis
  permitido son los números.
- No persigas la pronunciación con fonética inventada. Si el nombre no sale, **reescribe la frase**
  ("a small municipality in Chile") y deja el nombre en pantalla. Es más rápido y suena mejor.
- Corta el video al audio, no al revés. Y deja el reloj del run visible cuando aceleres.

### Diccionario de pronunciación

Un TTS en inglés va a destrozar nombres y códigos. Reglas:

| Elemento | Qué hacer |
|---|---|
| `Pumanque` | escribirlo fonético en el input (`poo-MAHN-keh`) y escuchar la muestra; si igual suena mal, decir "a small municipality in Chile" y dejar el nombre **en pantalla** |
| `José Luis Pavez Vargas` | no se dice en voz: está en la tarjeta y en el documento |
| `1195695-6-SE22` | **nunca se dice**: los códigos se leen con la vista, no con el oído |
| `81.5 percent` / `82 percent` | decir "eighty-one point five", no "eighty-one comma five" |
| `CLP` | decir "pesos", no "C-L-P" |

Y la regla general: **todo número que importa está también en pantalla.** Si la voz se equivoca,
la vista lo corrige.

---

## 4. Antes de grabar (10 minutos)

| # | Acción | Por qué |
|---|---|---|
| 1 | `curl -s https://investiga.wagnersolutionsai.com/api/summary` | calienta la caché (300 s) |
| 2 | Abrir la señal `720ea8991ba9b159` y volver | deja la tarjeta en memoria |
| 3 | Abrir la vista 3D **antes** de grabar | el canvas nace en 0×0 si se abre oculto y revienta el motor |
| 4 | Cerrar notificaciones, Slack, correo | una notificación mata la toma |
| 5 | Zoom 100 %, ventana 1600×1000 | el criterio del landing |
| 6 | Grabar OBS 30 fps, cursor visible | el cursor es parte de la demostración |
| 7 | **Voz primero, video después** | el corte se hace al ritmo de la narración |
| 8 | Dos tomas del bloque 0:34–2:14 | es el 60 % del video |

---

## 5. Lo que no se dice

| Sí | No |
|---|---|
| "the record shows", "the signal observes", "the chain cites" | "we detected fraud", "we found irregularities", "the system proves" |
| el número con su documento | un total redondeado que parezca completo |
| "two of eight" | "eight orders" sin denominador |
| el reloj del run (48.5 s) | "in real time" sin reloj |
| el fallo, si ocurre | un corte que lo esconda |

**Si el modelo falla en vivo:** se muestra el fallo, se cierra con `terminar` sin claims. Ese es el
comportamiento correcto y también es el argumento: el gate no deja pasar una afirmación sin cita.

---

## 6. Versión de 60 segundos

Escena 1 (0:00–0:10) + Escena 3 recortada (0:10–0:28) + Escena 5 completa (0:28–0:44) +
Escena 7 (0:44–1:00). Voz de Meta en 3 y 5; la tuya en 1 y 7.

---

## Anexo · La frase, textual de Wagner

> Muchas veces como ciudadano del tercer mundo, hemos aprendido que la única forma de hacer frente
> a la escasez, es a través del manejo adecuado de nuestros recursos. Como padre soltero de 4
> hijas, lo vivo mes a mes, gestionando recursos limitados para mantener mi hogar. Así nace
> Expediente: una herramienta diseñada para la auditoría de recursos públicos, para que cualquier
> persona —funcionarios de Contraloría, Fiscalía, Mercado Público, investigadores, periodistas y
> ciudadanos comunes— pueda auditar sin saber escribir una query.

Se usa **tal cual** en el subtítulo del cierre. Es la única parte del guion que no se reescribe.
