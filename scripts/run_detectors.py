#!/usr/bin/env python3
"""Run the deterministic detectors over the order-level JSONL, at full scale.

Usage:
    python3 scripts/run_detectors.py out/orders_5y.jsonl out/signals
    python3 scripts/run_detectors.py <orders.jsonl> <outdir> --limit 200000

Why this exists rather than calling the detectors from a notebook: the numbers that go
into a dossier have to be reproducible from a file, with the thresholds written down and
the run's own cost measured. The summary records orders read, findings produced, wall
time and peak memory, so "it scales" is a measurement instead of an adjective.

Two things the data forced:

* **Organismo and Proveedor are interned.** 4.95 M orders each carrying its own copy of
  the buyer and the supplier would be millions of duplicate objects; the detectors only
  ever read their fields, so one instance per real entity is enough and the run fits in
  memory.
* **Concentration findings carry a sample of codes.** The top supplier of a large buyer
  can have 150,000 orders, and a finding with 150,000 locators is not a citation, it is a
  dump. The sample is capped and the true count is kept beside it.

Modifications are not run: the bulk archives carry no ``fecha_ultima_modificacion`` and
no ``fecha_cancelacion``, so the detector would report zero by lack of input, which
would read as "no post-award changes in Chile" instead of "we did not look".
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import resource
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expediente.detectors.concentracion import (
    DEFAULT_HHI_UMBRAL,
    DEFAULT_MIN_ORDENES as CONC_MIN_ORDENES,
    detect_concentracion,
)
from expediente.detectors.fraccionamiento import (
    DIRECT_AWARD_PROCEDENCIAS,
    DEFAULT_FLOOR_CLP,
    DEFAULT_MIN_ORDERS as FRAC_MIN_ORDERS,
    detect_fraccionamiento,
)
from expediente.domain.models import OrdenCompra, Organismo, Proveedor
from expediente.domain.normalizers import normalize_rut, parse_clp, parse_date

#: How many order codes a single finding quotes. Enough to open the case, not a dump.
CODE_SAMPLE = 25


def load_orders(path: Path, limit: int | None) -> tuple[list[OrdenCompra], dict[str, int]]:
    """Read the JSONL into detector inputs, interning buyers and suppliers."""
    organismos: dict[str, Organismo] = {}
    proveedores: dict[str, Proveedor] = {}
    orders: list[OrdenCompra] = []
    counters = {"read": 0, "skipped_no_date": 0, "skipped_no_supplier": 0, "skipped_no_amount": 0}

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if limit is not None and counters["read"] >= limit:
                break
            line = line.strip()
            if not line:
                continue
            counters["read"] += 1
            record = json.loads(line)

            fecha = parse_date(record.get("fecha"))
            if fecha is None:
                counters["skipped_no_date"] += 1
                continue
            monto = parse_clp(record.get("monto_total_clp"))
            if not monto or monto <= 0:
                counters["skipped_no_amount"] += 1
                continue
            rut = normalize_rut(record.get("proveedor_rut"))
            if not rut:
                counters["skipped_no_supplier"] += 1
                continue

            org_key = (record.get("organismo_rut") or "").strip() or "SIN-RUT"
            organismo = organismos.get(org_key)
            if organismo is None:
                organismo = Organismo(
                    codigo=org_key,
                    nombre=(record.get("organismo") or "SIN NOMBRE").strip(),
                    region=(record.get("region_compra") or None),
                )
                organismos[org_key] = organismo

            proveedor = proveedores.get(rut)
            if proveedor is None:
                proveedor = Proveedor(
                    rut_norm=rut,
                    razon_social=(record.get("proveedor") or "SIN NOMBRE").strip(),
                    giro=(record.get("rubro") or None),
                )
                proveedores[rut] = proveedor

            orders.append(
                OrdenCompra(
                    codigo=str(record.get("codigo") or ""),
                    organismo=organismo,
                    proveedor=proveedor,
                    monto_clp=monto,
                    fecha=fecha,
                    descripcion=(record.get("descripcion") or None),
                    procedencia=(record.get("procedencia") or None),
                )
            )

    print(
        f"  órdenes utilizables: {len(orders):,} · organismos: {len(organismos):,} · "
        f"proveedores: {len(proveedores):,}",
        flush=True,
    )
    print(
        f"  descartadas: sin fecha {counters['skipped_no_date']:,} · "
        f"sin monto {counters['skipped_no_amount']:,} · "
        f"sin RUT de proveedor {counters['skipped_no_supplier']:,}",
        flush=True,
    )
    return orders, counters


def serialise(finding, codes_total: int | None = None) -> dict:
    payload = dataclasses.asdict(finding)
    codes = payload.get("order_codes") or ()
    if codes_total is not None:
        payload["order_codes_total"] = codes_total
    payload["order_codes"] = list(codes[:CODE_SAMPLE])
    payload["order_codes_truncated"] = len(codes) > CODE_SAMPLE
    payload["pattern_kind"] = getattr(finding, "pattern_kind", lambda: None)()
    payload["explanation"] = finding.explanation()
    return payload


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("outdir", type=Path)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--hhi", type=float, default=DEFAULT_HHI_UMBRAL)
    parser.add_argument("--min-orders", type=int, default=FRAC_MIN_ORDERS)
    parser.add_argument("--floor", type=int, default=DEFAULT_FLOOR_CLP)
    parser.add_argument(
        "--ceiling",
        type=int,
        default=None,
        help="tope fijo en CLP; por defecto, el vigente a la fecha de cada OC",
    )
    parser.add_argument(
        "--all-procedencias",
        action="store_true",
        help="measure every award mechanism instead of only direct awards",
    )
    args = parser.parse_args(argv[1:])

    args.outdir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    print(f"leyendo {args.source} ...", flush=True)
    orders, counters = load_orders(args.source, args.limit)
    load_seconds = time.monotonic() - started

    print("detector: fraccionamiento ...", flush=True)
    t0 = time.monotonic()
    fraccionamientos = detect_fraccionamiento(
        orders,
        ceiling_clp=args.ceiling,
        floor_clp=args.floor,
        min_orders=args.min_orders,
        procedencias=None if args.all_procedencias else DIRECT_AWARD_PROCEDENCIAS,
    )
    frac_seconds = time.monotonic() - t0
    print(f"  {len(fraccionamientos):,} patrones en {frac_seconds:.0f}s", flush=True)

    print("detector: concentración ...", flush=True)
    t0 = time.monotonic()
    concentraciones = detect_concentracion(
        orders, hhi_umbral=args.hhi, min_ordenes=CONC_MIN_ORDENES
    )
    conc_seconds = time.monotonic() - t0
    print(f"  {len(concentraciones):,} patrones en {conc_seconds:.0f}s", flush=True)

    with (args.outdir / "signals_fraccionamiento.jsonl").open("w", encoding="utf-8") as handle:
        for finding in fraccionamientos:
            handle.write(json.dumps(serialise(finding, len(finding.order_codes)), ensure_ascii=False) + "\n")
    with (args.outdir / "signals_concentracion.jsonl").open("w", encoding="utf-8") as handle:
        for finding in concentraciones:
            handle.write(json.dumps(serialise(finding, len(finding.order_codes)), ensure_ascii=False) + "\n")

    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    summary = {
        "source": str(args.source),
        "orders_read": counters["read"],
        "orders_usable": len(orders),
        "discarded": {
            "no_date": counters["skipped_no_date"],
            "no_amount": counters["skipped_no_amount"],
            "no_supplier": counters["skipped_no_supplier"],
        },
        "thresholds": {
            "fraccionamiento": {
                "floor_clp": args.floor,
                "ceiling_clp": args.ceiling,
                "min_orders": args.min_orders,
                "procedencias": None if args.all_procedencias else list(DIRECT_AWARD_PROCEDENCIAS),
            },
            "concentracion": {"hhi_umbral": args.hhi, "min_ordenes": CONC_MIN_ORDENES},
        },
        "findings": {
            "fraccionamiento": len(fraccionamientos),
            "concentracion": len(concentraciones),
        },
        "totals": {
            "fraccionamiento_clp": sum(f.total_clp for f in fraccionamientos),
            "concentracion_clp": sum(f.total_clp for f in concentraciones),
        },
        "top": {
            "fraccionamiento": [
                {
                    "organismo": f.organismo,
                    "proveedor": f.proveedor,
                    "periodo": f.period,
                    "ordenes": len(f.order_codes),
                    "total_clp": f.total_clp,
                    "veces_el_umbral": round(f.factor, 2),
                    "clase": f.pattern_kind(),
                    "similitud_descripciones": f.desc_similarity,
                    "montos_identicos": f.share_identical_amount,
                    "explanation": f.explanation(),
                }
                for f in fraccionamientos[:10]
            ],
            "concentracion": [
                {
                    "organismo": f.organismo,
                    "hhi": round(f.hhi),
                    "proveedores": f.n_proveedores,
                    "ordenes": f.n_ordenes,
                    "total_clp": f.total_clp,
                    "top_proveedor": f.top_proveedor,
                    "top_share": round(f.top_share, 4),
                    "explanation": f.explanation(),
                }
                for f in concentraciones[:10]
            ],
        },
        "seconds": {
            "load": round(load_seconds, 1),
            "fraccionamiento": round(frac_seconds, 1),
            "concentracion": round(conc_seconds, 1),
            "total": round(time.monotonic() - started, 1),
        },
        "peak_memory_mb": round(peak_mb),
        "not_run": {
            "modificaciones": "the bulk archives carry no fecha_ultima_modificacion nor "
            "fecha_cancelacion; reporting zero would read as 'none exist' instead of "
            "'not measured'",
            "colicotizacion": "needs the quoting-supplier list from Compra Ágil details, "
            "which the order-level bulk does not include",
        },
    }
    (args.outdir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    print(f"total: {time.monotonic() - started:.0f}s · pico de memoria {peak_mb:.0f} MB")
    print(f"resumen: {args.outdir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
