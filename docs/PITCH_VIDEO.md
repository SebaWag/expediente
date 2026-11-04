# Guion del video — Expediente (3:00)

Documento de producción. Escrito para grabarse en **una sola toma de pantalla**, sin cortes que
escondan fallos. Si algo falla en vivo, se ve el fallo: el producto se sostiene en que la interfaz
no puede afirmar más de lo que el run hizo.

**Formato:** 1080p, ventana de 1600×1000 (el criterio de aceptación del landing), sin música,
voz en castellano con **subtítulos en inglés quemados**. Los jueces son Meta/Cerebral Valley.

---

## La frase personal, textual de Wagner

> Muchas veces como ciudadano del tercer mundo, hemos aprendido que la única forma de hacer frente
> a la escasez, es a través del manejo adecuado de nuestros recursos. Como padre soltero de 4
> hijas, lo vivo mes a mes, gestionando recursos limitados para mantener mi hogar. Así nace
> Expediente: una herramienta diseñada para la auditoría de recursos públicos, para que cualquier
> persona —funcionarios de Contraloría, Fiscalía, Mercado Público, investigadores, periodistas y
> ciudadanos comunes— pueda auditar sin saber escribir una query.

*Se usa tal cual, sin editar. Es la única parte del guion que no se reescribe.*

---

## Antes de grabar (checklist de 10 minutos)

| # | Acción | Por qué |
|---|---|---|
| 1 | `curl -s https://investiga.wagnersolutionsai.com/api/summary` | calienta la caché del servidor (300 s) |
| 2 | Abrir la señal `720ea8991ba9b159` en una pestaña y volver | deja la tarjeta del caso en memoria |
| 3 | Cerrar Slack, correo y notificaciones | una notificación en pantalla mata la toma |
| 4 | Zoom del navegador al 100 %, ventana 1600×1000 | el criterio del landing |
| 5 | Grabar con OBS, 30 fps, cursor visible | el cursor es parte de la demostración |
| 6 | Tener a mano los códigos `1195695-6-SE22` y `1195695-8-SE22` | para tipear sin buscar |
| 7 | Toma de respaldo: grabar dos veces el bloque 0:35–2:15 | es el 60 % del video |

**Lo que NO se dice en ningún momento:** "detectamos fraude", "encontramos irregularidades",
"el sistema demuestra". Se dice: *"el registro muestra"*, *"la señal observa"*, *"la cadena cita"*.

---

## Escena 1 · 0:00–0:14 · El gancho

**Pantalla:** negro, luego el título *Expediente* y una sola línea: *"Chile publica todas sus
compras. Nadie puede leerlas."*

**Voz:**

> Un municipio de Chile compró lo mismo ocho veces en un mes. Los datos son públicos. La pregunta
> es: ¿puede un ciudadano probarlo?

## Escena 2 · 0:14–0:34 · El problema, con el número

**Pantalla:** el corpus en texto: 10.302.746 órdenes · 240 archivos · 74.777.686 filas · 5 años.
Luego la frase: *"Para responder una pregunta hay que cruzar cientos de documentos a mano."*

**Voz:**

> Diez millones de órdenes de compra, cinco años, doscientos cuarenta archivos que no se hablan
> entre sí. La Contraloría ya alerta anomalías; lo que no existe es la evidencia que un auditor
> pueda firmar. Para eso hay que **conectar** los registros y **probar** la conexión.

## Escena 3 · 0:34–1:05 · La pregunta, y el plan que escribe el modelo

**Pantalla:** el portal. Se escribe en el chat, sin prisa:

```
¿Por qué la Municipalidad de Pumanque concentra el 82% de su gasto en un solo proveedor?
```

Se ve aparecer el plan (los pasos con los nombres reales de las herramientas) y luego el
waterfall de pasos ejecutándose.

**Voz:**

> Pregunto en castellano, sin saber SQL ni SPARQL. Muse Spark escribe el plan de investigación
> nombrando herramientas que existen —buscar en el grafo, abrir la señal, mirar el perfil de
> gasto— y elige una por turno. No escribe consultas: elige entre trece verbos acotados, y cada
> verbo se compila a un recorrido con límites en código. Por eso ocho pasos alcanzan, y por eso
> el número se puede volver a correr.

**Subtítulo EN (clave):**

> *The model plans the investigation and picks one of thirteen bounded tools per turn. It never
> writes a query. The code decides what is true.*

## Escena 4 · 1:05–1:50 · El caso, y la cadena que se puede abrir

**Pantalla:** se abre la señal. La tarjeta dice: **82 % · $1.009.928.255 · 8 órdenes en el
mercado, 7 proveedores · 2 del proveedor, 81,5 % del gasto**. Se hace clic en la primera orden
(`1195695-6-SE22`): $299.537.429, 18-04-2022, Licitación Pública. Se abre el documento.

**Voz:**

> Esta es la señal: un proveedor concentra el 82 % del gasto municipal. Y esta es la parte que
> importa. Cada afirmación viene con la orden, el monto, la fecha y el documento oficial, con su
> huella. No hay una conclusión suelta: hay dos órdenes citadas, ambas con documento. Si una no
> tuviera documento, el sistema la borra.

**Subtítulo EN (clave):**

> *Every claim carries the order, the amount, the date and the document hash. A claim that cannot
> be traced is deleted, not softened.*

## Escena 5 · 1:50–2:15 · La cobertura declarada (el beat que gana)

**Pantalla:** el pie de la tarjeta, con la cadena de evidencia. Se hace zoom lento sobre la
primera cifra. **Texto exacto que muestra hoy el portal** (verificado en producción, 4-nov):

```
Cadena completa · 2 de 8 órdenes (las del proveedor) · 81,5 % del gasto · 2 de 2 con documento · 4 documento(s) citables
```

Luego se abre la vista del corpus y se ve el total declarado.

**Voz:**

> Y dice lo que no tiene: la tarjeta no dice "8 órdenes" a secas. Dice **2 de 8**, aclara que son
> las del proveedor, y dice que suman el 81,5 % del gasto. Las otras seis no están enlazadas.
> Eso está en pantalla, no en una nota al pie. Un portal de transparencia te muestra el total y te
> deja creer que lo verificó todo. Este te dice cuánto verificó.

## Escena 6 · 2:15–2:42 · La escala

**Pantalla:** la cámara 3D alejándose: los 565 señales, los organismos, los proveedores. Luego un
corte a texto: **2.065 organismos · 113.183 proveedores · 6 detectores**.

**Voz:**

> Lo mismo funciona para dos mil organismos y ciento trece mil proveedores, sin ingeniería por
> entidad. No es un tablero más bonito: es la misma interfaz para todo el país. Y el techo no lo
> pone el modelo, lo pone cuánta evidencia existe para citar.

## Escena 7 · 2:42–3:00 · El cierre, en primera persona

**Pantalla:** negro. Solo texto, apareciendo línea por línea, mientras se lee la frase textual:

> *"Como padre soltero de cuatro hijas, vivo mes a mes gestionando recursos limitados. Si yo tengo
> que rendir cada peso en mi casa, no puedo aceptar que un municipio no rinda los suyos."*

**Voz (cierre):**

> Expediente no acusa a nadie. Muestra relaciones que están en el registro público y cita el
> documento de cada una. Para que la persona que tiene la pregunta —no la que sabe escribir una
> query— pueda auditar. Ese es el proyecto.

---

## Qué se muestra y qué no

| Sí | No |
|---|---|
| La pregunta escrita en vivo | Un video de YouTube de fondo |
| El plan con nombres de herramientas reales | Un diagrama de arquitectura animado |
| El documento abriéndose con su hash | Un "detectamos fraude" en pantalla |
| **"2 de 8"** visible y explicado | Un total redondeado que parezca completo |
| El tiempo real del run (48,5 s) | Un "en tiempo real" sin reloj |

## Riesgos de la toma

1. **`/api/overview` tarda 2,11 s en frío.** Por eso se calienta antes: si la primera pantalla
   queda en blanco 2 s, el video parece lento.
2. **La cámara 3D puede no dibujar si el canvas nace oculto.** Abrir la vista 3D antes de grabar,
   no durante.
3. **El run tarda 40–50 s.** No se corta: se acelera 4× en post con el reloj visible, o se deja
   y se narra encima. Acelerar sin reloj es hacer trampa.
4. **Si el modelo falla en vivo**, se muestra el fallo y se cierra con `terminar` sin claims. Ese
   es el comportamiento correcto y también es el argumento: el gate no deja pasar una afirmación
   sin cita.

## Versión de 60 segundos (para redes, si sobra tiempo)

Escena 1 (0:00–0:08) + Escena 3 recortada (0:08–0:25) + Escena 5 completa (0:25–0:42) +
Escena 7 (0:42–1:00).
