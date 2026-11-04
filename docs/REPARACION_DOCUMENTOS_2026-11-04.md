# Reparación de la cadena de documentos — 4-nov-2026

Documento de registro. Lo que se midió, lo que se arregló y lo que queda declarado.

## El síntoma

El portal parecía tener "señales y documentos faltantes". Las señales del tablero eran 2 o 5,
no las 100 que pedía la vista.

## Causa 1 — la caché de señales ignoraba los parámetros (código)

Medido en producción:

```
?limit=2   → 2      (se cachea)
?limit=200 → 2      ← misma respuesta
?limit=5   → 5      (se cachea)
?limit=200 → 5      ← misma respuesta
```

`Portal.signals()` cacheaba en **un solo slot**: la primera llamada fijaba la respuesta por
`SIGNAL_CACHE_S = 120` s, sin importar el `limit`. El tablero pedía 100 y recibía la respuesta
de un sondeo. Además, al llamar a la API se **descartaba `detector`**, así que filtrar por
detector devolvía todo.

**Arreglo:** clave de caché `(limit, detector)`, y el filtro se pasa. Test de regresión en
`tests/test_portal.py::test_the_signals_cache_is_keyed_by_its_parameters`.

## Causa 2 — documentos nunca descargados (datos)

El grafo tenía 3.039 documentos, pero al medir las 200 señales de mayor score:

```
12 órdenes citadas · 12 con registro · solo 4 con documento   (28 de 35 incompletas)
```

Se verificó una señal concreta (`e19e301afb104c36`): **los 8 archivos faltantes no existían en
disco**. Nunca se descargaron. Dos motivos:

1. **Tope por señal.** El script adjuntaba con un tope menor (4–12) que las órdenes que la señal
   cita (hasta 25).
2. **Permisos.** El contenedor corre como uid 10001 y `/opt/expediente/evidencia` es de root:
   `PermissionError` al escribir el archivo. Fallaba sin ruido.

**Arreglo:** re-ejecución de `scripts/attach_evidence.py` dentro de la red del grafo, como root,
con `--per-signal 25`. Es idempotente (salta por archivo existente y por digest).

```
docker run --rm --user 0 --network expediente-net --env-file /opt/expediente/portal.env \
  -e PYTHONPATH=/app -e EXPEDIENTE_MDB_URL=http://expediente-mdb:1234 \
  -v /opt/expediente/evidencia:/data/evidencia \
  -v /opt/expediente/portal/scripts:/scripts:ro \
  expediente-portal:latest python3 /scripts/attach_evidence.py \
  --dir /data/evidencia --apply --limit 200 --per-signal 25
```

## Antes / después (medido, no estimado)

| Métrica | Antes | Después |
|---|---|---|
| `Documento` en el grafo | 3.039 | **4.319** (+42%) |
| `Evidencia` en el grafo | 3.039 | **4.319** |
| Archivos en disco | 3.013 | **3.911** |
| Cadena completa (200 señales top) | 165/200 · **82%** | 195/200 · **97%** |
| Órdenes citadas con documento | 2.093/2.343 · **89%** | 2.329/2.343 · **99%** |
| Descarga de un documento (por digest) | — | **40/40 HTTP 200** |
| Fallos de descarga en la reparación | — | **0** |

## Lo que queda y se declara

- **5 señales de 200 (2,5%) y 14 órdenes de 2.343 (0,6%)** no tienen documento porque la API de
  Mercado Público devuelve un payload sin `Listado` para esos códigos (4 documentos vacíos en
  disco). No se inventan: el campo `complete: false` y el flag lo dicen.
- La reparación del resto de las señales (hasta 565) corre en segundo plano con el mismo script,
  `--limit 600 --per-signal 25`. Es idempotente.
- `/api/signals` tope `MAX_LIMIT = 200`: el tablero pide 100 por defecto y declara el total del
  grafo (565) aparte.

## Regla que este episodio refuerza

Un número que el sistema **declara** como incompleto (`complete: false`) se puede arreglar. Un
número que el sistema **cachea mal** no se puede ver: la respuesta parece correcta. La caché es
parte de la corrección, no una optimización neutra.
