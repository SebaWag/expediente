"""What the two thresholds disagree about, measured on the real corpus.

The question this answers is not "how many findings are there" but "how many orders each rule
even considers", because the first number can always be lowered by narrowing the window and
the second cannot be argued with. The run prints, for both rules and the same file:

* how many orders fell inside the window (the population the rest of the analysis is over),
* how many did not, by named reason,
* how many groups reached the minimum number of orders, and what they are worth.

``--legacy`` is the fixed ceiling the detector used until this change. It is kept as a
command-line flag rather than a constant so a reader can rerun the old measurement and watch
it miss, instead of taking that claim on trust.

    python3 scripts/measure_umbral.py out/orders_5y.jsonl
    python3 scripts/measure_umbral.py out/orders_5y.jsonl --limit 500000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import orjson

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expediente.detectors.fraccionamiento import format_clp  # noqa: E402
from expediente.domain.normalizers import parse_date  # noqa: E402
from expediente.domain.umbral import normalizar_procedencia, resolver  # noqa: E402

LEGACY_CEILING_CLP = 1_000_000
FLOOR_CLP = 30_000
MIN_ORDERS = 3
PROCEDENCIA = "trato directo"


def parse_fecha(value: str) -> date | None:
    """Whatever the corpus carries, parsed by the one normaliser that knows the formats."""
    return parse_date(value)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv[1:])

    grupos_legado: dict[tuple, list] = defaultdict(lambda: [0, 0])
    grupos_fecha: dict[tuple, list] = defaultdict(lambda: [0, 0])
    cobertura_legado: Counter[str] = Counter()
    cobertura_fecha: Counter[str] = Counter()
    estados: Counter[str] = Counter()
    procedencias: Counter[str] = Counter()

    leidas = 0
    trato_directo = 0
    started = time.monotonic()

    with args.source.open("rb") as handle:
        for line in handle:
            leidas += 1
            if args.limit and leidas > args.limit:
                break
            if leidas % 1_000_000 == 0:
                print(f"  {leidas:,} líneas · {time.monotonic() - started:.0f}s", flush=True)

            row = orjson.loads(line)
            procedencias[row.get("procedencia")] += 1
            if normalizar_procedencia(row.get("procedencia")) != PROCEDENCIA:
                continue
            trato_directo += 1

            monto = row.get("monto_total_clp")
            fecha = parse_fecha(row.get("fecha") or "")
            if monto is None or fecha is None:
                cobertura_legado["SIN_IMPORTE_O_FECHA"] += 1
                cobertura_fecha["SIN_IMPORTE_O_FECHA"] += 1
                continue
            if monto < FLOOR_CLP:
                cobertura_legado["BAJO_EL_PISO"] += 1
                cobertura_fecha["BAJO_EL_PISO"] += 1
                continue

            periodo = f"{fecha.year:04d}-{fecha.month:02d}"
            clave = (row.get("organismo_rut"), row.get("proveedor_rut"), periodo)

            # --- la regla vieja: un número redondo para todas las fechas ---
            if monto <= LEGACY_CEILING_CLP:
                grupos_legado[clave][0] += 1
                grupos_legado[clave][1] += monto
                cobertura_legado["EN_VENTANA"] += 1
            else:
                cobertura_legado["SOBRE_EL_TOPE"] += 1

            # --- la regla con fecha: el umbral que regía ese mes ---
            resolucion = resolver(fecha, row.get("procedencia"))
            estados[resolucion.estado] += 1
            if not resolucion.aplica:
                cobertura_fecha[resolucion.estado] += 1
                continue
            if monto > resolucion.clp:
                cobertura_fecha["SOBRE_EL_TOPE"] += 1
                continue
            grupos_fecha[clave][0] += 1
            grupos_fecha[clave][1] += monto
            cobertura_fecha["EN_VENTANA"] += 1

    def resumen(grupos: dict, cobertura: Counter, etiqueta: str) -> dict:
        hallazgos = [(clave, datos) for clave, datos in grupos.items() if datos[0] >= MIN_ORDERS]
        monto_total = sum(datos[1] for _, datos in hallazgos)
        ordenes_en_hallazgos = sum(datos[0] for _, datos in hallazgos)
        top = sorted(hallazgos, key=lambda pair: -pair[1][1])[:5]
        return {
            "regla": etiqueta,
            "ordenes_en_ventana": cobertura["EN_VENTANA"],
            "descartadas": {k: v for k, v in cobertura.items() if k != "EN_VENTANA"},
            "grupos_con_minimo": len(hallazgos),
            "ordenes_en_grupos": ordenes_en_hallazgos,
            "monto_en_grupos_clp": monto_total,
            "top": [
                {
                    "organismo": clave[0],
                    "proveedor": clave[1],
                    "periodo": clave[2],
                    "ordenes": datos[0],
                    "monto_clp": datos[1],
                }
                for clave, datos in top
            ],
        }

    vieja = resumen(grupos_legado, cobertura_legado, f"techo fijo {format_clp(LEGACY_CEILING_CLP)}")
    nueva = resumen(grupos_fecha, cobertura_fecha, "umbral vigente a la fecha de cada OC")

    print()
    print(f"líneas leídas            {leidas:>14,}")
    print(f"trato directo            {trato_directo:>14,}")
    print(f"procedencias             {dict(procedencias.most_common(5))}")
    print(f"estados del calendario   {dict(estados)}")
    print()
    for bloque in (vieja, nueva):
        print(f"── {bloque['regla']}")
        print(f"   en ventana            {bloque['ordenes_en_ventana']:>12,}")
        print(f"   descartadas           {bloque['descartadas']}")
        print(f"   grupos >= {MIN_ORDERS}           {bloque['grupos_con_minimo']:>12,}")
        print(f"   órdenes en grupos     {bloque['ordenes_en_grupos']:>12,}")
        print(f"   monto en grupos       {format_clp(bloque['monto_en_grupos_clp'])}")
        print("   top:")
        for item in bloque["top"]:
            print(
                f"     {item['ordenes']:>3} OC · {format_clp(item['monto_clp']):>16} · "
                f"{item['periodo']} · {item['organismo']} → {item['proveedor']}"
            )
        print()

    if args.json_out:
        payload = {
            "fuente": str(args.source),
            "lineas": leidas,
            "trato_directo": trato_directo,
            "procedencias": dict(procedencias),
            "estados": dict(estados),
            "reglas": [vieja, nueva],
        }
        args.json_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        print(f"-> {args.json_out}")

    print(f"\n{time.monotonic() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
