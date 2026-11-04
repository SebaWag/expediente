# Caso semilla del landing — Pumanque

Propuesta para la **opción C** de `docs/REVISION_GRAFO_2026-11-04.md` ("el caso primero").
Todo lo que sigue está medido contra producción el **4-nov-2026, 02:10–02:20**, no de memoria.

**Señal:** `720ea8991ba9b159` · `concentracion` · severidad alta · score 6.764,49
**Por qué esta y no otra:** es la única del shortlist cuya cadena está **completa al 100 %**
(2 órdenes declaradas por el proveedor, 2 citadas, 2 con documento) y cuyo tamaño cabe en una
pantalla sin recortar nada.

---

## 1. La afirmación, textual

> I MUNICIPALIDAD DE PUMANQUE concentra el 82% de $1.009.928.255 en JOSE LUIS PAVEZ VARGAS:
> HHI 6764 sobre un umbral de 2500, con 7 proveedores en 8 órdenes.

## 2. Los números medidos

| Campo del nodo `:Senal` | Valor | Verificado |
|---|---|---|
| `monto_clp` | 1.009.928.255 | gasto total del organismo en la ventana |
| `ordenes` | 8 | órdenes del **mercado** (todos los proveedores) |
| `proveedores` | 7 | proveedores del mercado |
| `cuota_top` | **0,8152** | participación del proveedor top |
| `detector` | `concentracion` | |
| `fecha_deteccion` | 2026-10-27 | |

`counts` del `signal_pack`: `orders_cited` 2 · `orders_with_document` 2 · `documents` 4 ·
`sum_clp` 823.273.505 · `complete: true`.

**Comprobación aritmética:** 823.273.505 / 1.009.928.255 = **0,81518** → coincide con
`cuota_top` 0,8152. La cadena del proveedor explica la afirmación, no la contradice.

## 3. Las dos órdenes

| Código | Fecha | Monto CLP | Procedencia | Licitación de origen |
|---|---|---:|---|---|
| `1195695-6-SE22` | 18-04-2022 | 299.537.429 | Licitación Pública | `1195695-4-LR22` |
| `1195695-8-SE22` | 03-05-2022 | 523.736.076 | Licitación Pública | `1195695-8-LR21` |
| | | **823.273.505** | | |

Descripciones: *"Conservación Infraestructura Escuela Pumanque"* y *"Mejoramiento Gimnasio
Municipal Ranquilhué"*. Ambas en `Region del Libertador General Bernardo OHiggins`,
sector `MUNICIPALIDADES`, estado `Aceptada`.

## 4. La evidencia (lo que hace que se pueda firmar)

| Orden | Tipo | Página citada | SHA-256 (12) |
|---|---|---|---|
| `1195695-6-SE22` | `registro_api` | `Listado[0]` | `02f552d5ba4c` |
| `1195695-6-SE22` | `registro_api` | `Listado[0]` | `0442f4265320` |
| `1195695-8-SE22` | `registro_api` | `Listado[0]` | `25d1c6d26902` |
| `1195695-8-SE22` | `registro_api` | `Listado[0]` | `81287205b3ad` |

Recuperadas: `2026-10-29T14:23:23+00:00`. Extracto de la primera:
*"ORDEN DE COMPRA DESDE 1195695-4-LR22 · Total 299537429.0 · creada 2022-04-14"*.

## 5. Los nodos que dibuja el landing

| Nodo (URI del grafo) | Etiqueta en pantalla |
|---|---|
| `Senal/720ea8991ba9b159` | "82 % del gasto municipal en un proveedor" |
| `Organismo/13.781.584-2` | I. Municipalidad de Pumanque |
| `Proveedor/10.213.173-8` | José Luis Pavez Vargas (10.213.173-8) |
| `OrdenCompra/1195695-6-SE22` | OC · $299,5 M · abr-2022 |
| `OrdenCompra/1195695-8-SE22` | OC · $523,7 M · may-2022 |
| `Licitacion/1195695-4-LR22` | Licitación de origen |
| `Licitacion/1195695-8-LR21` | Licitación de origen |
| `Documento/<hash>` ×4 | Registro citado (abre el PDF/JSON) |

**Aristas:** `SE_OBSERVA_EN` ×2 · `EMITE` ×2 · `ADJUDICADA_A` ×2 · `DERIVA_DE` ×2 ·
`ACREDITA` ×4 · `SOBRE_PROVEEDOR` ×1 → **11 nodos, 13 aristas**. Cabe en una pantalla a
1600×1000 sin recortar.

Datos del proveedor: RUT `10.213.173-8`, *Pequeña*, Región de O'Higgins.

## 6. Lo que NO se puede dibujar (y hay que declarar)

El mercado tiene **8 órdenes y 7 proveedores**, pero el grafo solo materializa las **2 órdenes
del proveedor concentrado**. Las otras 6 no están enlazadas a la señal.

Regla que se aplica (la misma del producto): **lo que se recorta se declara**. Texto del pie:

> *Este es 1 de 85 casos del shortlist. El mercado de esta compra tiene 8 órdenes y 7
> proveedores; se citan las 2 del proveedor concentrado, que suman el 81,5 % del gasto.
> Las otras 6 no están enlazadas a la señal en el grafo.*

Y el botón: **"ver el corpus (1.437 nodos)"** — el número viene de `/api/overview`, nunca escrito
a mano.

## 7. Las llamadas exactas

```bash
# 1. el caso completo, con evidencia
GET https://investiga.wagnersolutionsai.com/api/signals/720ea8991ba9b159

# 2. el vecindario para dibujar (11 nodos, 13 aristas)
GET /api/subgraph?nodo=Senal%2F720ea8991ba9b159&saltos=2

# 3. los totales del pie (nunca literales)
GET /api/summary
```

`/api/subgraph` exige `nodo=Label/id`. Con `saltos=3` el caso se llena de proveedores vecinos y
deja de leerse: **el landing debe usar 2**.

## 8. Criterios de aceptación, instanciados

| # | Criterio | Cómo se verifica en este caso |
|---|---|---|
| 1 | Primer dibujo < 3 s | `performance` hasta `layoutstop` con 11 nodos |
| 2 | El hallazgo se ve sin zoom | la ruta señal → 2 OC → proveedor → documento entra a 1600×1000 |
| 3 | Etiquetas sin solaparse | 11 × bounding box, 0 cruces |
| 4 | 0 errores JS | consola y `pageerror` limpios |
| 5 | Lo recortado se declara | el pie dice "8 órdenes del mercado · 2 citadas · 81,5 %" |
| 6 | Corpus completo en 1 clic | el botón abre la vista actual (1.437 nodos, `truncated: true`) |
| 7 | Giro 3D sin caídas | 10 s de arrastre, 0 errores |

## 9. Deudas que este caso expone (no arreglar antes del video; declarar)

1. **Tres nodos para el mismo municipio.** `/api/search?label=Organismo&texto=PUMANQUE`
   devuelve `Organismo/69.090.800-K`, `Organismo/69.090.800-k` y `Organismo/13.781.584-2`.
   Las órdenes cuelgan del tercero. Los otros dos son islas.
2. **104 organismos con el RUT terminado en `k` minúscula** (medido por SPARQL), es decir, el
   mismo defecto de normalización, sistémico.
3. **Nombres con más de un nodo.** Los 12 peores (consulta limitada a 12): `COMANDO DE APOYO A
   LA FUERZA` con **157** nodos, `DIRECCION DE ABASTECIMIENTO DE LA ARMADA` con 146.

Ninguna de las tres se arregla hoy: son cambios de datos que obligan a revalidar el grafo.
Se declaran en el README como hallazgo de calidad de datos — que es exactamente lo que el
producto dice hacer con los datos del Estado.

## 10. Qué no hacer con este caso

- **No** recalcular el claim con las 2 órdenes citadas: daría "concentra el 100 %" y sería falso.
- **No** agregar las 6 órdenes del mercado al vuelo con una consulta al organismo en la request
  del landing: la latencia del landing ya es 2,11 s y esa consulta no está indexada por período.
- **No** usar `saltos=3` "para que se vea más grande".
