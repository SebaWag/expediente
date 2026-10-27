#!/usr/bin/env python3
"""Write the detector's shortlist into the graph as :Senal nodes.

The detectors produce 2,443 material patterns and a dossier cannot carry 2,443 of
anything. What goes into the graph is the shortlist: the findings classified as
``mismo_objeto`` (the same description bought over and over just under the threshold) and
the concentration cases above a materiality floor. Each one becomes a node the agent can
find, the kanban can paint, and the API can open.

Why INSERT DATA instead of re-importing: ``mdb import`` refuses a non-empty database
folder (finding 6), and reloading 64.8 M triples to add a few thousand would be absurd.
The engine's update endpoint handles this perfectly well, and each triple carries its own
dot because that is what this dialect requires.

    EXPEDIENTE_MDB_URL=http://expediente-mdb:1234 \
        python3 scripts/materialize_signals.py /signals --top 60

The signal id is derived from (detector, organismo, proveedor, period), so re-running
this twice writes the same nodes instead of doubling them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expediente.graph.millenniumdb import MillenniumDBStore, node_uri, relation_uri
from expediente.graph.schema import NODE_PREFIX, ONTO, RDF_TYPE, XSD

#: Concentration below this is arithmetic, not materiality: any buyer with five orders
#: from one supplier has an HHI of 10,000.
CONCENTRATION_FLOOR_CLP = 1_000_000_000

#: Ordens cited per signal. The rest are reachable through the order's own node; a
#: signal with 73 edges to orders is a finding, one with 150,000 is a dump.
MAX_ORDERS_PER_SIGNAL = 25


def signal_uri(detector: str, organismo: str, proveedor: str, period: str) -> str:
    digest = hashlib.sha256(f"{detector}|{organismo}|{proveedor}|{period}".encode("utf-8")).hexdigest()[:16]
    return f"{NODE_PREFIX}Senal/{digest}"


def lit(value: object) -> str:
    if isinstance(value, bool):
        return f'"{"true" if value else "false"}"^^<{XSD}boolean>'
    if isinstance(value, int):
        return f'"{value}"^^<{XSD}integer>'
    if isinstance(value, float):
        return f'"{value}"^^<{XSD}decimal>'
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")
    text = "".join(char for char in text if ord(char) >= 0x20 or char == "\t")
    return f'"{text}"'


def load(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def build_triples(fraccionamientos: list[dict], concentraciones: list[dict], today: str) -> tuple[list[str], dict]:
    triples: list[str] = []
    stats = {"fraccionamiento": 0, "concentracion": 0, "order_edges": 0}

    detectors = {
        "fraccionamiento": ("0.3.0", "compras bajo el umbral que suman varias veces el umbral"),
        "concentracion": ("0.1.0", "gasto de un organismo concentrado en pocos proveedores"),
    }
    for name, (version, description) in detectors.items():
        uri = f"{NODE_PREFIX}Detector/{name}"
        triples += [
            f"<{uri}> <{RDF_TYPE}> <{ONTO}Detector> .",
            f"<{uri}> <{ONTO}codigo> {lit(name)} .",
            f"<{uri}> <{ONTO}version> {lit(version)} .",
            f"<{uri}> <{ONTO}descripcion> {lit(description)} .",
        ]

    def severity(score: float, high: float) -> str:
        return "alta" if score >= high else "media"

    for row in fraccionamientos:
        factor = row["total_clp"] / row["ceiling_clp"]
        uri = signal_uri("fraccionamiento", row["organismo"], row["proveedor"], row["period"])
        triples += [
            f"<{uri}> <{RDF_TYPE}> <{ONTO}Senal> .",
            f"<{uri}> <{ONTO}detector> {lit('fraccionamiento')} .",
            f"<{uri}> <{ONTO}score> {lit(round(factor, 2))} .",
            f"<{uri}> <{ONTO}severidad> {lit(severity(factor, 20))} .",
            f"<{uri}> <{ONTO}fecha_deteccion> {lit(today)} .",
            f"<{uri}> <{ONTO}organismo_nombre> {lit(row['organismo'])} .",
            f"<{uri}> <{ONTO}proveedor_nombre> {lit(row['proveedor'])} .",
            f"<{uri}> <{ONTO}periodo> {lit(row['period'])} .",
            f"<{uri}> <{ONTO}monto_clp> {lit(int(row['total_clp']))} .",
            f"<{uri}> <{ONTO}ordenes> {lit(int(row.get('order_codes_total') or row.get('n_orders') or 0))} .",
            f"<{uri}> <{ONTO}clase> {lit(row.get('pattern_kind') or 'indeterminado')} .",
            f"<{uri}> <{ONTO}similitud_descripciones> {lit(float(row.get('desc_similarity') or 0))} .",
            f"<{uri}> <{ONTO}explicacion> {lit(row.get('explanation') or '')} .",
            f"<{uri}> <{relation_uri('PRODUCE')}> <{NODE_PREFIX}Detector/fraccionamiento> .",
        ]
        # the edge runs the other way in the vocabulary: Detector -PRODUCE-> Senal
        triples[-1] = (
            f"<{NODE_PREFIX}Detector/fraccionamiento> <{relation_uri('PRODUCE')}> <{uri}> ."
        )
        for code in (row.get("order_codes") or [])[:MAX_ORDERS_PER_SIGNAL]:
            triples.append(
                f"<{uri}> <{relation_uri('SE_OBSERVA_EN')}> <{node_uri('OrdenCompra', code)}> ."
            )
            stats["order_edges"] += 1
        stats["fraccionamiento"] += 1

    for row in concentraciones:
        uri = signal_uri(
            "concentracion", row["organismo"], row["top_proveedor"], row["period"] if "period" in row else "n/a"
        )
        triples += [
            f"<{uri}> <{RDF_TYPE}> <{ONTO}Senal> .",
            f"<{uri}> <{ONTO}detector> {lit('concentracion')} .",
            f"<{uri}> <{ONTO}score> {lit(round(float(row['hhi']), 2))} .",
            f"<{uri}> <{ONTO}severidad> {lit(severity(float(row['hhi']), 5000))} .",
            f"<{uri}> <{ONTO}fecha_deteccion> {lit(today)} .",
            f"<{uri}> <{ONTO}organismo_nombre> {lit(row['organismo'])} .",
            f"<{uri}> <{ONTO}proveedor_nombre> {lit(row['top_proveedor'])} .",
            f"<{uri}> <{ONTO}monto_clp> {lit(int(row['total_clp']))} .",
            f"<{uri}> <{ONTO}ordenes> {lit(int(row.get('n_ordenes') or 0))} .",
            f"<{uri}> <{ONTO}proveedores> {lit(int(row.get('n_proveedores') or 0))} .",
            f"<{uri}> <{ONTO}cuota_top> {lit(round(float(row.get('top_share') or 0), 4))} .",
            f"<{uri}> <{ONTO}explicacion> {lit(row.get('explanation') or '')} .",
            f"<{NODE_PREFIX}Detector/concentracion> <{relation_uri('PRODUCE')}> <{uri}> .",
        ]
        for code in (row.get("order_codes") or [])[:MAX_ORDERS_PER_SIGNAL]:
            triples.append(
                f"<{uri}> <{relation_uri('SE_OBSERVA_EN')}> <{node_uri('OrdenCompra', code)}> ."
            )
            stats["order_edges"] += 1
        stats["concentracion"] += 1

    return triples, stats


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("signals", type=Path)
    parser.add_argument("--top", type=int, default=60)
    parser.add_argument("--dry-run", action="store_true", help="write the triples, do not load")
    parser.add_argument("--batch", type=int, default=2000)
    args = parser.parse_args(argv[1:])

    frac_path = args.signals / "signals_fraccionamiento.jsonl"
    conc_path = args.signals / "signals_concentracion.jsonl"

    fraccionamientos = [
        row
        for row in load(frac_path)
        if row.get("pattern_kind") == "mismo_objeto"
        and row["total_clp"] / row["ceiling_clp"] >= 3
    ]
    fraccionamientos.sort(key=lambda r: -r["total_clp"])
    fraccionamientos = fraccionamientos[: args.top]

    concentraciones = [row for row in load(conc_path) if row["total_clp"] >= CONCENTRATION_FLOOR_CLP]
    concentraciones.sort(key=lambda r: -r["total_clp"])

    today = date.today().isoformat()
    triples, stats = build_triples(fraccionamientos, concentraciones, today)

    print(
        f"señales: {stats['fraccionamiento']} fraccionamiento · "
        f"{stats['concentracion']} concentración · "
        f"{stats['order_edges']} aristas a órdenes · {len(triples):,} tripletas"
    )

    if args.dry_run:
        out = args.signals / "signals.nt"
        out.write_text("\n".join(triples) + "\n", encoding="utf-8")
        print(f"escrito: {out}")
        return 0

    store = MillenniumDBStore.from_env()
    for start in range(0, len(triples), args.batch):
        chunk = triples[start : start + args.batch]
        store.client.insert(chunk)
        print(f"  insertadas {min(start + args.batch, len(triples)):,}/{len(triples):,}", flush=True)

    rows = store.client.select(
        f"SELECT (COUNT(?s) AS ?n) WHERE {{ ?s <{RDF_TYPE}> <{ONTO}Senal> }}"
    )
    print(f"señales en el grafo: {int(rows[0]['n']) if rows else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
