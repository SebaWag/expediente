# Declarado vs citado — la cobertura de la cadena

Documento de decisión y especificación. Medido contra producción el **4-nov-2026, 02:05–02:25**.
Nace de un síntoma que se ve en el landing: la tarjeta dice "73 órdenes · $54,5 M" y el lienzo
dibuja 25 órdenes por $18,6 M. **No es un bug de render: son dos poblaciones distintas con un
solo nombre.**

---

## 1. Qué dice el código hoy

`expediente/audit/queries.py` (signal_pack), líneas 204–244:

```python
codes   = [ ... SE_OBSERVA_EN ... limit=order_limit ]      # lo materializado en el grafo
orders  = order_records(store, codes)
evidence = evidence_for_orders(store, [o["code"] for o in orders])
covered  = {row["order"] for row in evidence}

"counts": {
    "orders_cited": len(codes),
    "orders_with_record": len(orders),
    "orders_with_document": len(covered),   # "the number that matters"
    "documents": ...,
    "sum_clp": int(sum(amounts)),
},
"complete": bool(codes) and len(covered) == len(codes),   # línea 242
```

`complete` responde una pregunta legítima: **"¿todas las órdenes que cité tienen documento?"**.
La respuesta es sí en las 14 señales medidas. El problema es que el lector de la tarjeta
entiende otra pregunta: **"¿la cadena cubre lo que la afirmación declara?"**.

Los dos números que faltan para distinguirlas ya existen y están a la vista:
`ordenes_declaradas` (propiedad `ordenes` del nodo `:Senal`, calculada por el detector sobre la
población completa) y `counts.orders_cited` (lo que se materializó). Nunca se comparan.

---

## 2. La medición

| Señal | Detector | Declaradas | Citadas | Con doc. | Monto declarado | Monto citado | Cobertura monto | `complete` |
|---|---|---:|---:|---:|---:|---:|---:|:--:|
| `93efd38af560fad9` | fraccionamiento | 73 | **25** | 25 | 54.582.378 | 18.631.794 | **34 %** | true |
| `262b39fe6dbd1078` | fraccionamiento | 20 | 20 | 20 | 16.751.969 | 16.751.969 | **100 %** | true |
| `f2fca2d7fe270819` | concentración | 2.435 | **25** | 25 | 2.942.361.351 | 248.727.994 | **8 %** | true |
| `566ee7f9a602b184` | concentración | 369 | **1** | 1 | 11.089.468.684 | 6.998.578.523 | **63 %** | true |
| `82e4d6f04830f912` | concentración | 1.478 | **2** | 2 | 83.080.990.554 | 54.102.108.735 | **65 %** | true |
| `720ea8991ba9b159` | concentración | 8 | 2 | 2 | 1.009.928.255 | 823.273.505 | **82 %** | true |
| `e2f5a6c87194b70f` | concentración | 102 | 4 | 4 | 1.487.423.497 | 1.222.025.500 | **82 %** | true |
| `a5de615524767fc1` | concentración | 10 | 4 | 4 | 1.799.359.914 | 1.364.240.000 | **76 %** | true |

**14 de 14 señales medidas tienen `complete: true`.** Y 12 de 14 tienen cobertura de monto
inferior al 100 % — una, del 8 %.

---

## 3. Por qué no es "`complete` está roto"

Porque `ordenes_declaradas` **significa dos cosas distintas** según el detector:

| Detector | Qué es `ordenes` | Qué es `orders_cited` | Comparación correcta |
|---|---|---|---|
| `fraccionamiento` | órdenes **del patrón** (comprador + proveedor + período) | hasta 25 de esas órdenes | citadas / declaradas = **cobertura** |
| `concentracion` | órdenes **del mercado** (todos los proveedores) | órdenes **del proveedor concentrado** | citadas es el numerador del **share**, no de una cobertura |

Ejemplo del primero: `93efd38af560fad9` — *"73 órdenes … ninguna por sobre $1.000.000, suman
$54.582.378"*. El claim es sobre 73; la evidencia cubre 25. **Hay truncamiento real.**

Ejemplo del segundo: `720ea8991ba9b159` — *"concentra el 82 % de $1.009.928.255 … con 7
proveedores en 8 órdenes"*. El claim es sobre 8 órdenes del mercado; las 2 citadas son las del
proveedor y suman 81,5 %. **No hay truncamiento: hay una definición.**

Mientras los dos casos se llamen igual (`ordenes_declaradas`, `complete`), la interfaz no puede
ser honesta aunque quiera.

## 4. De dónde sale el 25

Tope de materialización: `scripts/attach_evidence.py --per-signal 25` (ver
`docs/REPARACION_DOCUMENTOS_2026-11-04.md`). Es una decisión de costo, no del detector. Por eso
una señal puede declarar 2.435 órdenes y citar 25 sin que nada esté "roto" — pero el landing lo
presenta como si fueran la misma cosa.

---

## 5. Semántica propuesta (campos nuevos, aditivos)

En `signal_pack`, sin tocar el cálculo del detector:

```json
"poblacion": {
  "declarada": { "ordenes": 73, "monto_clp": 54582378, "fuente": "detector" },
  "citada":    { "ordenes": 25, "monto_clp": 18631794, "fuente": "grafo" }
},
"cobertura": { "ordenes": 0.3425, "monto_clp": 0.3414 },
"truncada": true,
"causa_truncamiento": "tope_materializacion",
"alcance_declarado": "patron"
```

- `alcance_declarado`: `"patron"` (fraccionamiento) o `"mercado"` (concentración). Es lo que hoy
  falta para que la UI sepa qué está comparando.
- `causa_truncamiento`: `"tope_materializacion"` | `"sin_documento"` | `null`. Distingue lo que
  recortamos nosotros de lo que no existe en la API.
- `complete` **se conserva** con su significado actual, pero se documenta como
  `cited_chain_complete` en la UI: *"todas las órdenes citadas tienen documento"*.

## 6. El texto que debe mostrar la tarjeta

Fraccionamiento:

> **73 órdenes** desde 2021-12 · **25 citadas** (34 % del monto) · todas con documento
> *El resto del patrón está declarado, no citado: el grafo materializa hasta 25 órdenes por señal.*

Concentración:

> **8 órdenes** en el mercado, 7 proveedores · **2 del proveedor** (81,5 % del gasto) · ambas con documento
> *La participación se calcula sobre el mercado completo; se citan las órdenes del proveedor.*

Sin ese texto, el pingüino del `complete: true` es una promesa que la pantalla no cumple.

## 7. Tests de regresión (con fixtures reales)

| Test | Fixture | Aserción |
|---|---|---|
| `test_cobertura_fraccionamiento_es_menor_que_uno` | `93efd38af560fad9` | `cobertura.monto_clp == 0.3414` y `truncada is True` |
| `test_cobertura_concentracion_cita_al_proveedor` | `720ea8991ba9b159` | `alcance_declarado == "mercado"`, `cobertura.monto_clp == 0.8152`, `truncada is False` |
| `test_complete_solo_habla_de_lo_citado` | `262b39fe6dbd1078` | `complete is True` y `cobertura.ordenes == 1.0` |
| `test_caso_sin_documento_no_se_declara_completo` | señal con `orders_with_document < orders_cited` | `complete is False`, `causa_truncamiento == "sin_documento"` |

Los tres primeros corren contra el adaptador en memoria con los valores fijados; el cuarto
contra el motor (marcado `skip` si no hay servidor, como los 17 actuales).

## 8. Lo que NO se hace

- **No** recalcular el claim con la población citada: cambiaría el hallazgo ("concentra el 100 %")
  y sería falso.
- **No** subir el tope de 25 antes del video: obliga a re-descargar evidencia y a revalidar la
  cadena de documentos, y son 2 h que no hay.
- **No** renombrar `complete` en la API: rompe `audit/pdf.py`, `audit/export.py` y
  `agent/tools.py`, que lo leen. Se renombra **en la pantalla**, no en el contrato.

## 9. Por qué esto entra en el video

Es el beat más fuerte que tiene el producto y dura diez segundos:

> *"El detector encontró 73 órdenes. El expediente cita 25, todas con documento, y te dice que
> son 25 de 73. Un portal de transparencia te muestra el total y te deja creer que lo verificó
> todo."*

Un sistema que declara su propia cobertura es más difícil de creer… hasta que el jurado compara
con cualquier otro dashboard, que no la declara.
