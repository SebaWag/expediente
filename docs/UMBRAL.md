# El umbral — dónde dice la ley cuánto es demasiado, y cómo se verificó

**Fecha de verificación:** 30 de octubre de 2026 · **Archivo:** `expediente/domain/umbral.py`

Este documento existe porque el detector de fraccionamiento funcionó durante días con
`ceiling_clp = 1_000_000` — un número redondo, sin fuente, aplicado por igual a una orden de
2021 y a una de 2026. No era el umbral de nada. Estaba unas **53 veces** por debajo del que
regía en 2021, así que cada orden entre un millón y cincuenta y tres millones quedaba fuera
de la ventana antes de que la regla se aplicara. Un hueco de cobertura sin mensaje de error.

El reemplazo es una tabla en la que cada fila lleva el día desde el que rige y la frase que
la sostiene.

---

## 1 · Cómo se lee la ley sin adivinar

`leychile.cl/Consulta/obtxml?opt=7&idNorma=<id>` devuelve el **texto consolidado completo**
de cualquier norma como XML. Dos detalles que costaron un rato:

- **Necesita `User-Agent` de navegador.** Sin él responde **401** y parece que no existe.
- **Acepta `&idVersion=AAAA-MM-DD`** y devuelve el texto *como estaba ese día*. Es lo que
  permitió leer la Ley 19.886 **anterior** a la reforma sin depender de citas de terceros.

```
https://www.leychile.cl/Consulta/obtxml?opt=7&idNorma=213004&idVersion=2024-12-11
```

## 2 · Lo que dice, literal

### La prohibición, que es el fundamento del detector

> **Ley 19.886, art. 7º, inciso final (texto al 11-dic-2024):** *"La Administración **no podrá
> fragmentar sus contrataciones con el propósito de variar el procedimiento** de
> contratación."*

> **Ley 19.886 vigente:** *"Los organismos del Estado no podrán fragmentar sus contrataciones
> con el propósito de variar el procedimiento de contratación. **La infracción de esta
> disposición tendrá como sanción** la señalada en el párrafo quinto del literal c) del
> artículo 8 bis…"*

La regla estaba en la ley desde 2003, con sanción desde 2024, y el código nunca la citó.
Es la clase de hallazgo que importa más que el bug: la evidencia estaba a un `grep` de
distancia.

### Los montos, por régimen

| Norma | Texto | Qué fija |
|---|---|---|
| Ley 19.886, art. 5º (al 11-dic-2024) | *"La licitación pública será obligatoria cuando las contrataciones **superen las 1.000 unidades tributarias mensuales**, salvo lo dispuesto en el artículo 8º"* | **1.000 UTM** pre-reforma |
| Ley 19.886, art. 5º (vigente) | *"…adjudicarán los contratos que celebren mediante licitación pública. Excepcionalmente, y por un acto debidamente fundado… licitación privada, trato directo o contratación excepcional directa con publicidad…"* | sin monto: la regla general es licitar |
| Ley 19.886, art. 7 letra d) nº1 (vigente) | *"…por un monto igual o inferior a **100 unidades tributarias mensuales**, previa solicitud de al menos tres cotizaciones…"* | **100 UTM** Compra Ágil |
| ChileCompra, `chilecompra.cl/ley-compra-agil` | *"Se eleva el límite de la Compra Ágil de **30 a 100 UTM**"* | **30 UTM** antes del 12-dic-2024 |
| ChileCompra, página de datos abiertos | *"empieza a regir al año de publicarse la ley en el Diario Oficial, es decir **12 de diciembre de 2024**"* | la fecha del cambio |

### La UTM

Los 72 valores mensuales de 2021 a 2026 están transcritos de las tablas del SII
(`sii.cl/valores_y_fechas/utm/utm<AAAA>.htm`), consultadas el 30-oct-2026, y embebidos en el
módulo: **el motor de evidencia no consulta la red en tiempo de ejecución**. Diciembre de
2026 no está porque el SII todavía no lo publica, y una tabla que adivina la última celda es
una tabla que nadie puede revisar.

Verificación cruzada que no se buscó: `100 × utm(oct-2026) = $7.215.100`, y una fuente
independiente publica el tope 2026 de Compra Ágil como *"100 UTM ($7.215.100)"*.

## 3 · El calendario que quedó en código

| Procedencia | Período | Tope | ¿Literal? |
|---|---|---|---|
| trato directo | → 11-dic-2024 | **1.000 UTM** | literal (art. 5º) |
| trato directo | 12-dic-2024 → | **100 UTM** | derivada de dos reglas literales |
| compra ágil | → 11-dic-2024 | 30 UTM | oficial secundario |
| compra ágil | 12-dic-2024 → | 100 UTM | literal (art. 7 d) 1) |

La fila marcada como **derivada** no está escrita en ninguna parte y el módulo lo dice: la ley
ya no fija un monto para el trato directo (procede por causal), pero sí fija que bajo 100 UTM
existía un procedimiento competitivo —la Compra Ágil, con tres cotizaciones— y prohíbe
fragmentar para variar el procedimiento. Una orden bajo 100 UTM contratada por trato directo
usó la excepción donde había una vía competitiva disponible.

## 4 · Lo que no se pudo verificar, y se dice

Un primer borrador de este calendario puso **100 UTM** como tope del trato directo
pre-reforma. Era un supuesto heredado, y era falso por un factor de diez: la ley decía
**1.000 UTM**. La corrección salió de leer la versión fechada del texto, no de recordar
mejor.

Queda una regla que **no** se pudo verificar y por eso **no se aplica**: el escalón de monto
del trato directo en el reglamento anterior (Decreto 250). No aparece en el texto consolidado
ni en la fórmula modificatoria de la Ley 21.634, que reemplazó el artículo 8º completo. El
módulo la conserva con `verificado=False` y el resolutor responde `REGLA_NO_VERIFICADA`
antes que inventar un número. Una regla sin fuente es una regla que no se aplica.

## 5 · Por qué no hay un "techo de cordura" en los montos

Se evaluó y se descartó con datos. La orden más grande del corpus es:

```
85-429-SE24 · JUNTA NACIONAL DE AUXILIO ESCOLAR Y BECA
BAES 2025-2026 LÍNEAS 1 Y 2 — servicio de administración de transacciones
electrónicas para el Programa de Beca de Alimentación de Educación Superior
Licitación Pública · CLP · 2 ítems · $403.570.899.169
```

Es real. Cualquier techo por debajo de eso habría eliminado los 21 contratos nacionales más
grandes del país, que son justamente los que importan. La cola es fina —21 órdenes sobre
$100.000 millones, 368 sobre $10.000 millones, 5.322 sobre $1.000 millones, de 5,9 millones—
y es legítima.

## 6 · Cómo se reproduce

```bash
# la regla vigente a la fecha de cada orden (por defecto)
python3 -m expediente.cli detect --input examples/oc_investigation.json

# la regla vieja, para ver el hueco con los propios ojos
python3 -m expediente.cli detect --input examples/oc_investigation.json --ceiling 1000000

# la medición sobre el corpus: misma data, dos reglas, y el conteo de lo que cada una descarta
python3 scripts/measure_umbral.py out/orders_5y.jsonl
```

`scripts/measure_umbral.py` imprime, para ambas reglas, cuántas órdenes entraron a la ventana,
cuántas quedaron fuera por causa, cuántos grupos llegaron al mínimo y cuánto suman. El número
que importa no es cuántos hallazgos hay —eso siempre se puede bajar achicando la ventana—
sino cuántas órdenes cada regla siquiera considera.

## 7 · Limitaciones declaradas

- La cobertura del corpus está descrita en `out/cobertura.json`, no aquí. Un resultado vacío
  sobre un sector no ingerido se lee igual que un sector sin hallazgos, y esa ambigüedad es
  la que este proyecto intenta no repetir.
- 1,7% de las órdenes se emitieron en moneda distinta de CLP. El monto en pesos es la
  conversión que ChileCompra aplicó en la fila (`MontoNetoOC_CLP + ImpuestosOC_CLP`); el
  original queda en `monto_original` y `moneda_original`.
- El tope de 30 UTM pre-reforma proviene de una fuente oficial secundaria (ChileCompra lo
  afirma al anunciar el cambio a 100); la redacción literal de esa cifra no sobrevive en el
  texto consolidado porque la reforma la reemplazó.
