# Expediente de compras públicas — cadena de órdenes bajo el tope

**Sujeto:** Comprador 61.606.800-8 · 2021-11 · órdenes de trato directo  
**Generado:** 2026-10-31  
**ID del expediente:** `exp-d4dbd29acc821d09`  
**Tasa de citas:** 50% (1 de 2 afirmaciones)

**Detectores:** fraccionamiento

**Umbrales aplicados:** min_ordenes=3, piso_clp=30000, umbral_clp=el vigente a la fecha de cada OC (ver expediente/domain/umbral.py)

## Afirmaciones

1. 9 órdenes de compra de SERVICIO DE SALUD LIBERTADOR BDO OHIGGINS a UNIVERSIDAD DE O´HIGGINS en 2021-11, ninguna por sobre $53.476.000 (1000 UTM del mes), suman $370.756.900 (6.9× el umbral).  
   *Fuentes (9):* `official:2021-11.csv` p.36451, p.765954, p.768089, p.768378, p.787916, p.867937, p.868360, p.885309, p.886128 — https://transparenciachc.blob.core.windows.net/oc-da/2021-11.zip

## Cómo leer las citas

- La «página» de una cita a un archivo CSV del Estado es su **número de línea**. El documento enlazado es el archivo mensual oficial; la línea se verificó buscando el código de la orden dentro de ese archivo, no se estimó.
- Solo las órdenes que se encontraron en el archivo llevan cita. Una afirmación que cite una orden sin línea verificada no se emite: se elimina y se cuenta.
- Estas cifras describen registros públicos de compras. No imputan conducta a persona ni a institución alguna.

## Afirmaciones eliminadas

*Estas afirmaciones no se emitieron: no alcanzaron una página de un documento verificable. Se listan para que la omisión sea auditable, no invisible.*

- `AF-2` — **NO_EVIDENCE** · the assertion cites nothing

## Control de placebo

*Bajar el tope un 60% reduce las cadenas un 50% (1 contra 2): la cuenta sí responde al umbral. Eso sostiene revisar el grupo con la hipótesis de dimensionamiento, no probarla: una cadena puede ser deliberada y otra puede ser casual.*

| factor del tope | cadenas | órdenes citadas | monto (CLP) | % de las reales |
|---:|---:|---:|---:|---:|
| 0.40x | 1 | 4 | 6,696,900 | 50% |
| 0.60x | 1 | 4 | 6,696,900 | 50% |
| 0.80x | 2 | 9 | 198,307,700 | 100% |
| 1.00x | 2 | 13 | 377,453,800 | 100% |
| 1.25x | 2 | 13 | 377,453,800 | 100% |

*Población: Trato Directo (población por defecto del detector) · 196 órdenes examinadas. El control corre la misma regla con topes falsos: si la cuenta no se mueve, la regla mide repetición y no un incentivo.*

## Limitaciones

Ninguna afirmación de este documento se emite sin una cita a un documento y una página. Las afirmaciones que no pudieron citarse fueron eliminadas antes de la exportación, no suavizadas. Este expediente describe relaciones presentes en registros públicos; no imputa conducta a persona o empresa alguna.

## Firma

*Este documento requiere revisión y firma humana. El sistema no emite veredictos.*

Nombre: ______________________  RUT: ______________  Fecha: ____________
