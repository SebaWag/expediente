# Documentos del portal: qué funciona y qué no

Esta nota existe para que nadie repita el sondeo del 28-oct-2026. Es el registro de
una búsqueda real, con lo que se descartó y por qué.

## Resumen

| Capa | Estado |
|---|---|
| **Listado de Compra Ágil** | ✅ **Funciona por HTTP plano, sin login ni navegador** |
| **Detalle por ítem** | ❌ ruta no identificada |
| **Descarga de adjuntos (PDF)** | ❌ ruta no identificada |
| Órdenes de compra (v1, ticket) | ✅ metadata, **sin URLs de documento** |

## Lo que NO funciona (probado y descartado)

La API documentada (`api.mercadopublico.cl`, auth por `MP_TICKET`) devuelve metadata de
órdenes pero **ningún campo con URL de documento**. Probado:

- `api2/v2/compra-agil/{codigo}/documentos` → 403 `Missing Authentication Token` (la ruta no existe en el gateway)
- `api2/v2/compra-agil/documentos?codigo=…` → 400 `PARAMETRO_RUTA_INVALIDO` con cualquier formato de código
- `/documentos/{id}/descargar`, `/contenido`, `/archivos`, `v2/documentos/{id}` → 403, ninguna existe
- `v1/ordenesdecompra.json` y `v1/licitaciones.json` → 200 con datos, **cero URLs**
- `www.mercadopublico.cl` por `curl` → 403 (WAF; solo bloquea clientes sin navegador)

El ítem de Compra Ágil **sí** trae `documentos: [{id, nombre}]` — sabemos que el PDF
existe y cómo se llama (ej. `2042931 · "BASES TECNICAS Y ANEXOS.pdf"`) — pero no la URL.

## Lo que SÍ funciona: el buscador público

`buscador.mercadopublico.cl/compra-agil` es una app Next.js respaldada por
`api.buscador.mercadopublico.cl`. Al cargar la página obtiene dos cosas que necesita:

1. **Bearer token** — `GET https://servicios-prd.mercadopublico.cl/v1/auth/publico`
   (200, sin credenciales; `azp: mercadoPublicoClient`, `preferred_username: anonimouser`)
2. **`x-api-key`** — clave de cliente embebida en la app pública (36 chars). **No es una
   credencial de cuenta** y no se versiona: vive en `.env` como `MP_BUSCADOR_KEY`.

Con ambos headers (`x-api-key`, `Authorization: Bearer …`, `Referer`) la API responde
**200 a HTTP plano**:

```
GET https://api.buscador.mercadopublico.cl/compra-agil
    ?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&order_by=recent&page_number=1&status=2
```

Campos por ítem: `id, codigo, nombre, fecha_publicacion, fecha_cierre, organismo,
unidad, id_estado, estado, monto_disponible, moneda, monto_disponible_CLP,
fecha_cambio, cantidad_proveedores_cotizando, estado_convocatoria`.

**Gotchas medidos:**

- `region=all` (lo que manda la web) hace que la API responda **502**. Omitir el
  parámetro es la misma consulta y funciona.
- Ráfagas de llamadas producen **502**; el script reintenta con backoff.
- El token dura ~8 h (`exp - iat` = 28800 s).

## Lo que falta: el detalle

El detalle por ítem lo dispara un handler de click del SPA: los resultados se renderizan
como tarjetas **sin `href`**, así que no hay URL que seguir desde el HTML. No hay
`<main>` en el DOM (el `inner_text("main")` da timeout).

Siguiente intento sugerido (en este orden):

1. Capturar **el XHR que dispara el click** en una tarjeta real: localizar el nodo por
   el texto del código y usar `page.locator("text=4085-516-COT26").click(force=True)`,
   escuchando `page.on("response")` para aislar la llamada a `api.buscador.*`.
2. Probar la ruta con el `id` numérico ya conocido (`/compra-agil/{id}` devolvió 403
   `Invalid key=value pair in Authorization header`, que es un error de **firma** de API
   Gateway, no de permisos: sugiere un servicio distinto o firma SigV4).
3. Revisar `datos-abiertos.chilecompra.cl/descargas/compra-agil` — el propio portal
   recomienda esa vía para descargas masivas y podría incluir metadatos de documentos.

## Riesgo a declarar

`api.buscador.mercadopublico.cl` es la API interna de una app pública, **no una API
documentada**. Su uso automatizado no está explícitamente autorizado por los términos, y
la ruta puede cambiar sin aviso. Para el producto conviene:

- limitar la tasa (el script ya lo hace) y cachear,
- tratar el listado como *enriquecimiento*, nunca como fuente única de verdad,
- y tener el camino oficial (ticket + bulk de datos abiertos) como respaldo.
