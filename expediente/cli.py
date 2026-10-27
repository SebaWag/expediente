"""Command line entry point.

Three commands, no dependencies:

    python3 -m expediente.cli schema
    python3 -m expediente.cli detect --input examples/oc_synthetic.json
    python3 -m expediente.cli fetch-agil --desde 2026-10-20 --out data/agil.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .detectors.fraccionamiento import (
    DEFAULT_CEILING_CLP,
    DEFAULT_FLOOR_CLP,
    DEFAULT_MIN_ORDERS,
    detect_fraccionamiento,
)
from .evidence.gate import evaluate
from .graph.store import InMemoryGraphStore
from .ingest.records import load_dossier, load_orders, load_suppliers
from .navigate.paths import find_paths
from .navigate.rpq import PATTERNS, PatternError, match
from .resolution.resolver import apply_to_store, resolve_suppliers
from .graph.schema import describe


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="expediente",
        description="A verifiable evidence engine for public procurement.",
    )
    parser.add_argument("--version", action="version", version=f"expediente {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    schema_cmd = subparsers.add_parser("schema", help="print the graph vocabulary")
    schema_cmd.set_defaults(handler=_cmd_schema)

    detect_cmd = subparsers.add_parser(
        "detect", help="run the purchase-splitting detector over a JSON file"
    )
    detect_cmd.add_argument("--input", required=True, help="path to a JSON array of order records")
    detect_cmd.add_argument("--ceiling", type=int, default=DEFAULT_CEILING_CLP)
    detect_cmd.add_argument("--floor", type=int, default=DEFAULT_FLOOR_CLP)
    detect_cmd.add_argument("--min-orders", type=int, default=DEFAULT_MIN_ORDERS)
    detect_cmd.add_argument("--json", action="store_true", help="emit JSON instead of text")
    detect_cmd.set_defaults(handler=_cmd_detect)

    gate_cmd = subparsers.add_parser(
        "gate", help="apply the evidence gate to a dossier (claims that fail are deleted)"
    )
    gate_cmd.add_argument("--input", required=True, help="path to a dossier JSON file")
    gate_cmd.add_argument("--json", action="store_true", help="emit JSON instead of text")
    gate_cmd.set_defaults(handler=_cmd_gate)

    resolve_cmd = subparsers.add_parser(
        "resolve", help="find suppliers that share a representative or an address"
    )
    resolve_cmd.add_argument("--input", required=True, help="path to a supplier records JSON file")
    resolve_cmd.add_argument("--json", action="store_true", help="emit JSON instead of text")
    resolve_cmd.set_defaults(handler=_cmd_resolve)

    demo_cmd = subparsers.add_parser(
        "demo", help="build a small graph and print the route from a supplier to the buyer"
    )
    demo_cmd.add_argument("--orders", default="examples/oc_synthetic.json")
    demo_cmd.add_argument("--suppliers", default="examples/suppliers_synthetic.json")
    demo_cmd.add_argument("--label", default="Proveedor")
    demo_cmd.add_argument("--key", default="11111111-1")
    demo_cmd.set_defaults(handler=_cmd_demo)

    query_cmd = subparsers.add_parser(
        "query", help="evaluate a named regular path query over the demo graph"
    )
    query_cmd.add_argument("--pattern", default="red_oculta", help="named pattern or expression")
    query_cmd.add_argument("--label", default="Proveedor")
    query_cmd.add_argument("--key", default="11111111-1")
    query_cmd.add_argument("--orders", default="examples/oc_synthetic.json")
    query_cmd.add_argument("--suppliers", default="examples/suppliers_synthetic.json")
    query_cmd.add_argument("--list", action="store_true", help="list the named patterns and exit")
    query_cmd.set_defaults(handler=_cmd_query)

    fetch_cmd = subparsers.add_parser("fetch-agil", help="download agile purchases from the live API")
    fetch_cmd.add_argument("--desde", required=True, help="change date, YYYY-MM-DD")
    fetch_cmd.add_argument("--out", required=True, help="where to write the JSON payload")
    fetch_cmd.set_defaults(handler=_cmd_fetch_agil)

    return parser


def _cmd_schema(_args: argparse.Namespace) -> int:
    print(describe())
    return 0


def _cmd_detect(args: argparse.Namespace) -> int:
    orders = load_orders(args.input)
    findings = detect_fraccionamiento(
        orders,
        ceiling_clp=args.ceiling,
        floor_clp=args.floor,
        min_orders=args.min_orders,
    )
    if args.json:
        print(json.dumps([finding.to_dict() for finding in findings], ensure_ascii=False, indent=2))
        return 0
    patterns = "patrón encontrado" if len(findings) == 1 else "patrones encontrados"
    print(f"{len(orders)} órdenes analizadas · {len(findings)} {patterns}\n")
    for index, finding in enumerate(findings, start=1):
        print(f"[{index}] {finding.explanation()}")
        print(f"    códigos: {', '.join(finding.order_codes)}\n")
    return 0


def _cmd_gate(args: argparse.Namespace) -> int:
    claims, evidence, documents = load_dossier(args.input)
    report = evaluate(claims, evidence, documents)

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print(
        f"{report.total} afirmaciones · {len(report.kept)} conservadas · "
        f"{len(report.dropped)} eliminadas · tasa de citas {report.citation_rate:.1%}\n"
    )
    for kept in report.kept:
        print(f"  OK  [{kept.claim.id}] {kept.claim.texto[:64]}")
        print(f"      -> {kept.document_id} · p.{kept.pagina} · {kept.evidence_id}")
    for dropped in report.dropped:
        print(f"  NO  [{dropped.claim.id}] {dropped.reason.value} · {dropped.detail}")
    return 0


def _cmd_resolve(args: argparse.Namespace) -> int:
    records = load_suppliers(args.input)
    result = resolve_suppliers(records)

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0

    print(
        f"{len(records)} proveedores · {len(result.relations)} relaciones · "
        f"{len(result.candidates)} candidatas · {len(result.rejected)} descartados\n"
    )
    for relation in result.relations:
        print(f"  [{relation.kind.value}] {relation.display}")
        print(f"      proveedores: {', '.join(relation.suppliers)}")
        for ref in relation.evidence:
            print(f"      evidencia: {ref.documento_id} · p.{ref.pagina}")
    for candidate in result.candidates:
        print(f"  [revisar] {candidate.nombre_key} -> {', '.join(candidate.ruts)}")
        print(f"      {candidate.reason}")
    for rejected in result.rejected:
        print(f"  [descartado] {rejected.rut} · {rejected.reason.value}")
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    """Build the graph end to end and show the route. This is the pitch in one command."""
    store = InMemoryGraphStore()

    for order in load_orders(args.orders):
        organismo = store.upsert_node(
            "Organismo", order.organismo.codigo, nombre=order.organismo.nombre
        )
        proveedor = store.upsert_node(
            "Proveedor", order.proveedor.rut_norm, razon_social=order.proveedor.razon_social
        )
        orden = store.upsert_node(
            "OrdenCompra", order.codigo, monto_clp=order.monto_clp, fecha=str(order.fecha)
        )
        store.upsert_edge(organismo, "EMITE", orden)
        store.upsert_edge(orden, "ADJUDICADA_A", proveedor)

    suppliers = load_suppliers(args.suppliers)
    resolution = resolve_suppliers(suppliers)
    written = apply_to_store(store, resolution, suppliers)

    print(f"grafo: {len(store)} nodos · {written['personas']} personas · "
          f"{written['domicilios']} domicilios · {written['aristas']} aristas de resolución\n")

    start = (args.label, args.key)
    routes = find_paths(store, start, target_label="Organismo", max_depth=4, direction="both")

    # The interesting route is the one that reaches the buyer through a *second*
    # supplier. The direct one proves nothing: of course a supplier sells to the
    # buyer it sold to. The hidden one is the answer.
    # Unpacking ``for label, node in route.nodes`` would give the *key*, and
    # comparing a key against a (label, key) tuple is always unequal — the filter
    # silently becomes "any Proveedor at all". Compare whole nodes.
    indirect = [
        route
        for route in routes
        if any(node[0] == "Proveedor" and node != start for node in route.nodes)
    ]
    if not indirect:
        print("sin rutas indirectas: el proveedor no se conecta con el organismo por otro proveedor")
        return 1

    shortest = min(indirect, key=lambda route: route.length)
    print(f"ruta indirecta mas corta: {shortest.length} saltos")
    print(f"  (:{args.label} {args.key})")
    for hop in shortest.hops:
        arrow = "<-" if hop.reversed else "->"
        print(f"    -[:{hop.relation}]{arrow} (:{hop.target[0]} {hop.target[1]})")
    print()
    print("esa es la relacion que ninguna de las dos fuentes contiene por separado:")
    print("el registro dice quien representa a quien, la orden de compra dice quien le vende a quien.")
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    if args.list:
        for name, expression in sorted(PATTERNS.items()):
            print(f"{name:20} {expression}")
        return 0

    store = _build_demo_graph(args.orders, args.suppliers)
    try:
        routes = match(store, args.pattern, (args.label, args.key))
    except PatternError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    expression = PATTERNS.get(args.pattern, args.pattern)
    print(f"patrón: {args.pattern} = {expression}")
    print(f"desde:  (:{args.label} {args.key})")
    print(f"rutas:  {len(routes)}\n")

    for index, route in enumerate(routes, start=1):
        print(f"[{index}] {route.length} saltos -> (:{route.end[0]} {route.end[1]})")
        for hop in route.hops:
            arrow = "<-" if hop.reversed else "->"
            citation = ""
            if hop.props.get("documento_id"):
                citation = f"   [{hop.props['documento_id']} p.{hop.props.get('pagina')}]"
            print(f"      -[:{hop.relation}]{arrow} (:{hop.target[0]} {hop.target[1]}){citation}")
    return 0


def _build_demo_graph(orders_path: str, suppliers_path: str):
    store = InMemoryGraphStore()
    for order in load_orders(orders_path):
        organismo = store.upsert_node(
            "Organismo", order.organismo.codigo, nombre=order.organismo.nombre
        )
        proveedor = store.upsert_node(
            "Proveedor", order.proveedor.rut_norm, razon_social=order.proveedor.razon_social
        )
        orden = store.upsert_node(
            "OrdenCompra", order.codigo, monto_clp=order.monto_clp, fecha=str(order.fecha)
        )
        store.upsert_edge(organismo, "EMITE", orden)
        store.upsert_edge(orden, "ADJUDICADA_A", proveedor)

    suppliers = load_suppliers(suppliers_path)
    apply_to_store(store, resolve_suppliers(suppliers), suppliers)
    return store


def _cmd_fetch_agil(args: argparse.Namespace) -> int:
    from .config import mercadopublico_ticket
    from .ingest.mercado_publico import MercadoPublicoClient, MercadoPublicoError

    try:
        client = MercadoPublicoClient(mercadopublico_ticket())
        items = client.compras_agiles(args.desde)
    except (RuntimeError, MercadoPublicoError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(items)} compras ágiles desde {args.desde} → {out_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
