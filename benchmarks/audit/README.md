# Auditoría humana — 60 pares

**Tiempo:** 10–15 minutos. **Archivo a llenar:** `hoja_60.csv`.

## Protocolo

Abrí `hoja_60.csv` y **no abras `clave_60.csv` hasta terminar**. La hoja no muestra
RUT ni etiqueta a propósito: si los ves, la pregunta se contesta sola y la auditoría no
mide nada.

Para cada fila, una sola pregunta:

> ¿Los dos nombres designan la **misma entidad legal**?

| Columna | Qué escribir |
|---|---|
| `misma_entidad_SI_NO_DUDOSO` | `SI`, `NO` o `DUDOSO` |
| `confianza_1a5` | 1 = adiviné, 5 = lo firmaría |
| `nota` | opcional, una línea. Útil cuando dudás |

## Cómo decidir

- **`SI`** — es la misma empresa: razón social completa contra nombre de fantasía, o
  el mismo nombre con otro sufijo (`SPA` / `LIMITADA` / `S.A.`).
- **`NO`** — son dos empresas. Aunque compartan raíz (`LAYNER` / `LAYNER OTEC`), aunque
  compartan dirección, aunque el nombre de una esté contenido en el otro.
- **`DUDOSO`** — no se puede decidir con lo que hay en la fila. Es una respuesta válida
  y **es la más informativa**: significa que el sistema tampoco debería decidir.

Regla que importa: **un nombre más corto no es la misma entidad.** «Bernardo» no es
«BERNARDO PILQUINAO ÑANCULAF» hasta que alguien lo confirma.

## Qué se calcula después

```bash
python3 benchmarks/score_audit.py
```

Devuelve tres cosas:

1. **Acuerdo humano ↔ RUT** — la verdad del benchmark sale del RUT, no de un modelo.
   Las discrepancias son el hallazgo: pares donde dos RUT distintos son el mismo
   negocio, o donde dos nombres parecidos son empresas distintas.
2. **Reranker ↔ humano** en los umbrales 0,30 (candidato) y 0,90 (afirmación).
3. **La lista de discrepancias**, porque una tasa no es un hallazgo.

## Cómo se eligieron los 60

Semilla fija en `make_audit_sample.py`: el mismo sample cada vez. Estratificado hacia el
borde, no hacia lo fácil:

| Estrato | Pares | Qué son |
|---|---|---|
| hard_positive | 20 | mismo RUT, nombres genuinamente distintos |
| positive | 10 | mismo RUT, variantes de capitalización |
| hard_negative | 15 | distinto RUT, parte alta de la distribución de coseno |
| address_negative | 10 | distinto RUT, misma dirección |
| easy_negative | 5 | distinto RUT, distinta región y domicilio |

Dos pares están forzados en la muestra porque son los que sostienen el argumento del
benchmark: `LAYNER SPA` / `LAYNER OTEC SPA` y `César Antonio` /
`CÉSAR ANTONIO HERRERA MUÑOZ`.
