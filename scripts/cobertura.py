"""Describe what is in the corpus, including what is missing from it.

The reason this file exists is a question asked on the morning of 30 October 2026: *why does
a search for JUNAEB return nothing?* The answer was not a search bug. The corpus had four of
the five sectors ChileCompra publishes, and the fifth — central government and universities —
was never downloaded. **A search that returns nothing cannot tell "does not exist" from "was
never ingested"**, and in this project that distinction is the product.

So every dataset this project publishes comes with a coverage file that a reader can put next
to any empty result. It is written by measuring the data, not by restating what the download
script believed it fetched.

    python3 scripts/cobertura.py out/orders_5y.jsonl out/compras_agiles_5y.jsonl \
        --json-out out/cobertura.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import orjson

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expediente.domain.normalizers import parse_date  # noqa: E402

#: What this corpus is built from and — with the same weight — what it does not contain.
#: Every entry here is a claim a reader can check against the archives on disk.
FUENTES = {
    "origen": "https://chc-oc-files.mercadopublico.cl/sector/<año>/<SemN>/<Sector>.7z",
    "sectores_ingeridos": [
        "GobCentralUniversidades",
        "Municipalidades",
        "Salud",
        "FuerzasArmadas",
        "Otros",
    ],
    "semestres_ingeridos": "2021/Sem1 → 2026/Sem2 (12 semestres)",
    "no_ingerido": [
        "Compra Ágil: sólo desde 2021/Sem1 (los archivos anteriores no están en el bucket "
        "con este patrón)",
        "Órdenes en EUR: el monto en pesos viene convertido por ChileCompra, pero no se "
        "verificó la tasa aplicada",
        "Licitaciones y sus ofertas: este corpus tiene órdenes de compra, no el proceso "
        "licitatorio completo (ver OCDS, ingesta aparte)",
        "Convenio Marco como catálogo: se ingieren las órdenes emitidas bajo él, no el "
        "catálogo de productos",
    ],
}


def parse_fecha(value: str) -> date | None:
    """Delegate to the domain normaliser.

    This module used to carry its own ``dd-mm-yyyy`` parser. When the corpus was normalised
    to ISO it silently returned ``None`` for all 10.3 M records and reported a corpus with no
    dates at all — the same defect as a search that cannot tell "absent" from "not loaded",
    one layer down. One parser, in one place.
    """
    return parse_date(value)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args(argv[1:])

    sector: Counter[str] = Counter()
    procedencia: Counter[str] = Counter()
    moneda: Counter[str] = Counter()
    anio: Counter[int] = Counter()
    semestre: Counter[str] = Counter()
    organismo: dict[str, str] = {}
    proveedor: set[str] = set()
    cotizaciones: set[str] = set()
    por_archivo: dict[str, int] = {}
    sin_fecha = 0
    sin_monto = 0
    monto_negativo = 0
    total = 0
    monto_total = 0.0
    fechas: list[date] = []

    for path in args.files:
        vistos = 0
        with path.open("rb") as handle:
            for line in handle:
                row = orjson.loads(line)
                vistos += 1
                total += 1
                sector[row.get("sector") or "(vacío)"] += 1
                procedencia[row.get("procedencia") or "(vacío)"] += 1
                moneda[row.get("moneda") or "(vacío)"] += 1
                nombre_org = (row.get("organismo") or "").strip()
                rut_org = (row.get("organismo_rut") or "").strip()
                if nombre_org or rut_org:
                    organismo[rut_org or nombre_org] = nombre_org or rut_org
                if row.get("proveedor_rut"):
                    proveedor.add(row["proveedor_rut"])
                if row.get("cotizacion"):
                    cotizaciones.add(row["cotizacion"])
                fecha = parse_fecha(row.get("fecha") or "")
                if fecha is None:
                    sin_fecha += 1
                else:
                    fechas.append(fecha)
                    anio[fecha.year] += 1
                    semestre[f"{fecha.year}/Sem{1 if fecha.month <= 6 else 2}"] += 1
                monto = row.get("monto_total_clp")
                if not monto:
                    sin_monto += 1
                elif monto < 0:
                    monto_negativo += 1
                else:
                    monto_total += monto
        por_archivo[path.name] = vistos
        print(f"  {path.name}: {vistos:,} registros", flush=True)

    payload = {
        "generado": date.today().isoformat(),
        "fuentes": FUENTES,
        "registros": total,
        "por_archivo": por_archivo,
        "rango_de_fechas": {
            "desde": min(fechas).isoformat() if fechas else None,
            "hasta": max(fechas).isoformat() if fechas else None,
        },
        "por_sector": dict(sector.most_common()),
        "por_procedencia": dict(procedencia.most_common()),
        "por_moneda": dict(moneda.most_common()),
        "por_anio": {str(k): v for k, v in sorted(anio.items())},
        "por_semestre": dict(sorted(semestre.items())),
        "organismos": len(organismo),
        "proveedores": len(proveedor),
        "cotizaciones": len(cotizaciones),
        "monto_total_clp": round(monto_total),
        "descartes": {
            "sin_fecha": sin_fecha,
            "sin_monto": sin_monto,
            "monto_negativo": monto_negativo,
        },
    }

    print()
    print(f"registros          {total:>12,}")
    print(f"organismos         {len(organismo):>12,}")
    print(f"proveedores        {len(proveedor):>12,}")
    print(f"cotizaciones       {len(cotizaciones):>12,}")
    print(f"rango              {payload['rango_de_fechas']}")
    print(f"sectores           {list(payload['por_sector'])}")
    print(f"procedencias       {dict(procedencia.most_common())}")
    print(f"monedas            {dict(moneda.most_common(5))}")
    print(f"descartes          {payload['descartes']}")

    if args.json_out:
        args.json_out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\n-> {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
