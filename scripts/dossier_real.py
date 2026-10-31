#!/usr/bin/env python3
"""Assemble a dossier over real orders, citing the official archive and the row inside it.

The corpus gives rows, not PDFs, so a citation to "page 42 of the award resolution" cannot be
manufactured. What can be done honestly is cite the **official file the row was published in**:
the monthly archive of ``transparenciachc``, and the line where the order appears inside it.
That is a citation a reviewer can check in two minutes with a browser and ``grep`` — which is
the same standard as a page number, provided the dossier says what the number means.

The line numbers are not guessed here. They come from a file produced by searching the actual
archive for the actual order codes, and any code that was not found is reported as missing
rather than cited anyway:

    out/cadena_real_2021-11.json   {codes, lines, archive, member}

That separation matters: this script does not decide what is true, it attaches evidence that
was separately verified. If a code has no line, the order has no document, and the gate is
what decides whether the claim survives without it.

Usage:
    python3 scripts/dossier_real.py --buyer 61.602.226-1 --month 2021-11 \
        --evidence out/cadena_real_2021-11.json --out out/expediente_real.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from expediente.domain.models import OrdenCompra, Organismo, Proveedor  # noqa: E402
from expediente.domain.normalizers import normalize_rut, parse_clp, parse_date  # noqa: E402
from expediente.graph.store import InMemoryGraphStore  # noqa: E402
from expediente.investigate import investigate  # noqa: E402


def load_buyer_orders(path: Path, buyer_rut: str, month: str) -> list[OrdenCompra]:
    """Every order of one buyer inside one month, read from the slim corpus."""
    wanted = normalize_rut(buyer_rut)
    orders: list[OrdenCompra] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if normalize_rut(record.get("organismo_rut")) != wanted:
                continue
            fecha = parse_date(record.get("fecha"))
            if fecha is None or fecha.strftime("%Y-%m") != month:
                continue
            monto = parse_clp(record.get("monto_total_clp"))
            rut = normalize_rut(record.get("proveedor_rut"))
            if not monto or not rut:
                continue
            orders.append(
                OrdenCompra(
                    codigo=str(record.get("codigo") or ""),
                    organismo=Organismo(
                        codigo=wanted,
                        nombre=(record.get("organismo") or "SIN NOMBRE").strip(),
                        region=record.get("region_compra"),
                    ),
                    proveedor=Proveedor(
                        rut_norm=rut,
                        razon_social=(record.get("proveedor") or "SIN NOMBRE").strip(),
                        giro=record.get("rubro"),
                    ),
                    monto_clp=monto,
                    fecha=fecha,
                    descripcion=record.get("descripcion"),
                    procedencia=record.get("procedencia"),
                )
            )
    return orders


def build_store(orders: list[OrdenCompra], evidence: dict) -> tuple[InMemoryGraphStore, dict]:
    """Load the orders into a graph and accredit the ones we actually found in the archive."""
    store = InMemoryGraphStore()
    order_keys = {}
    for order in orders:
        organismo = store.upsert_node("Organismo", order.organismo.codigo, nombre=order.organismo.nombre)
        proveedor = store.upsert_node("Proveedor", order.proveedor.rut_norm, razon_social=order.proveedor.razon_social)
        orden = store.upsert_node(
            "OrdenCompra", order.codigo, monto_clp=order.monto_clp, fecha=str(order.fecha)
        )
        store.upsert_edge(organismo, "EMITE", orden)
        store.upsert_edge(orden, "ADJUDICADA_A", proveedor)
        order_keys[order.codigo] = orden

    lines = evidence.get("lines") or {}
    archive = evidence.get("archive") or ""
    member = evidence.get("member") or ""
    document_key = None
    if lines and archive:
        document_key = store.upsert_node(
            "Documento",
            f"official:{member or archive}",
            url=archive,
            tipo=f"csv · {member}" if member else "csv",
        )

    attached = 0
    missing = []
    for code, line in lines.items():
        orden = order_keys.get(code)
        if orden is None or document_key is None:
            missing.append(code)
            continue
        store.upsert_edge(document_key, "ACREDITA", orden, pagina=int(line))
        attached += 1

    return store, {"attached": attached, "missing": missing, "documented": bool(document_key)}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--orders", type=Path, default=Path("out/orders_b4.jsonl"))
    parser.add_argument("--buyer", required=True, help="buyer RUT")
    parser.add_argument("--month", required=True, help="YYYY-MM")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("out/expediente_real.md"))
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    parser.add_argument("--generado", default="")
    args = parser.parse_args(argv[1:])

    evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    orders = load_buyer_orders(args.orders, args.buyer, args.month)
    if not orders:
        print(f"no hay órdenes de {args.buyer} en {args.month}", file=sys.stderr)
        return 1

    store, coverage = build_store(orders, evidence)
    print(f"órdenes del comprador en {args.month}: {len(orders)}")
    print(
        f"  acreditadas contra el archivo oficial: {coverage['attached']}"
        + (f" · sin línea verificada: {len(coverage['missing'])}" if coverage["missing"] else "")
    )

    notas = [
        "La «página» de una cita a un archivo CSV del Estado es su **número de línea**. El "
        "documento enlazado es el archivo mensual oficial; la línea se verificó buscando el "
        "código de la orden dentro de ese archivo, no se estimó.",
        "Solo las órdenes que se encontraron en el archivo llevan cita. Una afirmación que "
        "cite una orden sin línea verificada no se emite: se elimina y se cuenta.",
        "Estas cifras describen registros públicos de compras. No imputan conducta a persona "
        "ni a institución alguna.",
    ]

    result = investigate(
        store,
        orders,
        titulo="Expediente de compras públicas — cadena de órdenes bajo el tope",
        sujeto=f"Comprador {args.buyer} · {args.month} · órdenes de trato directo",
        generado_en=args.generado or date.today().isoformat(),
        placebo=True,
        notas=notas,
    )

    body = result.dossier.to_json() if args.format == "json" else result.dossier.to_markdown()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(body, encoding="utf-8")

    for index, finding in enumerate(result.findings, start=1):
        print(f"[{index}] {finding.explanation()}")
    print()
    print(f"expediente {result.dossier.id}")
    print(f"  afirmaciones:  {result.kept} conservadas · {result.dropped} eliminadas")
    print(f"  tasa de citas: {result.dossier.citation_rate:.0%}")
    print(f"  firmable:      {'sí' if result.dossier.signable else 'no'}")
    print(f"  escrito en:    {args.out} ({args.format})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
