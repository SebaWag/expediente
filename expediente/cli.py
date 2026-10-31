"""Command line entry point.

Commands, no dependencies:

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
    DEFAULT_FLOOR_CLP,
    DEFAULT_MIN_ORDERS,
    detect_fraccionamiento,
)
from .dossier.build import build_dossier
from .investigate import investigate
from .evidence.gate import evaluate
from .evidence.models import Claim
from .evidence.pdf import PdfError, locate, parse_pdf
from .evidence.persist import persist_evidence
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
    detect_cmd.add_argument(
        "--ceiling",
        type=int,
        default=None,
        help="tope fijo en CLP para todas las órdenes; por defecto, el vigente a la fecha de cada OC",
    )
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

    dossier_cmd = subparsers.add_parser(
        "dossier", help="gate a dossier and export the signable artifact"
    )
    dossier_cmd.add_argument("--input", required=True, help="path to a dossier JSON file")
    dossier_cmd.add_argument("--out", required=True, help="where to write the export")
    dossier_cmd.add_argument("--format", choices=("markdown", "json"), default="markdown")
    dossier_cmd.add_argument("--titulo", default="Expediente de compras públicas")
    dossier_cmd.add_argument("--sujeto", default="Organismo y período bajo análisis")
    dossier_cmd.add_argument("--generado", default="", help="ISO date; defaults to today")
    dossier_cmd.set_defaults(handler=_cmd_dossier)

    investigate_cmd = subparsers.add_parser(
        "investigate", help="detect, cite from the graph, gate, and export a dossier"
    )
    investigate_cmd.add_argument("--orders", default="examples/oc_investigation.json")
    investigate_cmd.add_argument("--evidence", default="examples/evidence_investigation.json")
    investigate_cmd.add_argument("--suppliers", default="examples/suppliers_synthetic.json",
                                 help="supplier records, to resolve shared representatives")
    investigate_cmd.add_argument("--out", default="out/expediente.md")
    investigate_cmd.add_argument("--format", choices=("markdown", "json"), default="markdown")
    investigate_cmd.add_argument("--key", default="", help="supplier RUT to start the route from")
    investigate_cmd.add_argument("--pattern", default="red_oculta")
    investigate_cmd.add_argument("--titulo", default="Expediente de compras públicas")
    investigate_cmd.add_argument("--sujeto", default="Municipalidad bajo análisis, octubre 2026")
    investigate_cmd.add_argument("--generado", default="")
    investigate_cmd.set_defaults(handler=_cmd_investigate)

    parse_cmd = subparsers.add_parser(
        "parse", help="read a PDF into located evidence: this document, this page, this text"
    )
    parse_cmd.add_argument("--pdf", required=True, help="path to the PDF to read")
    parse_cmd.add_argument("--url", default="", help="source URL for the document node")
    parse_cmd.add_argument("--find", default="", help="text to locate; empty means describe only")
    parse_cmd.add_argument("--claim", default="", help="claim text citing every hit found")
    parse_cmd.add_argument("--contexto", type=int, default=180, help="chars of context per hit")
    parse_cmd.add_argument("--max-hits", type=int, default=10)
    parse_cmd.add_argument(
        "--extractor",
        default="auto",
        help="auto (first installed backend), pypdf, pymupdf (AGPL), or pdfplumber",
    )
    parse_cmd.add_argument("--out", default="", help="write a gate-compatible JSON here")
    parse_cmd.add_argument(
        "--orden", action="append", default=[],
        help="order code to accredit; the page is found in the PDF, never supplied",
    )
    parse_cmd.add_argument(
        "--graph", action="store_true",
        help="write :Documento/:Evidencia/:Afirmacion to EXPEDIENTE_MDB_URL",
    )
    parse_cmd.add_argument("--expediente", default="", help="expediente id to attach claims to")
    parse_cmd.add_argument("--titulo", default="", help="expediente title")
    parse_cmd.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parse_cmd.set_defaults(handler=_cmd_parse)

    fetch_cmd = subparsers.add_parser("fetch-agil", help="download agile purchases from the live API")
    fetch_cmd.add_argument("--desde", required=True, help="change date, YYYY-MM-DD")
    fetch_cmd.add_argument("--out", required=True, help="where to write the JSON payload")
    fetch_cmd.set_defaults(handler=_cmd_fetch_agil)

    return parser


def _cmd_schema(_args: argparse.Namespace) -> int:
    print(describe())
    return 0


def _open_write_store():
    """Open the graph for writing, and explain why not when it cannot be opened.

    The client refuses production hosts on its own; this only turns a stack trace
    into a sentence a person can act on.
    """
    from .graph.millenniumdb import MillenniumDBStore
    from .graph.sparql import SparqlError

    try:
        return MillenniumDBStore.from_env(), ""
    except RuntimeError as exc:
        return None, f"NO_STORE: {exc}"
    except SparqlError as exc:  # pragma: no cover - only reachable with a live endpoint
        return None, f"WRITE_REFUSED: {exc}"


def _cmd_parse(args: argparse.Namespace) -> int:
    """Parse a PDF and emit evidence that the gate can consume.

    This is the command that stops a citation from being a typed number: the page
    comes from the file, and ``--find`` can only return pages that actually say it.
    """
    try:
        parsed = parse_pdf(args.pdf, url=args.url, extractor=args.extractor)
    except PdfError as exc:
        print(f"ERROR {exc.code}: {exc.detail}", file=sys.stderr)
        return 2

    hits = locate(
        parsed, args.find, contexto=args.contexto, max_hits=args.max_hits
    ) if args.find else ()

    if args.find and not hits:
        print(
            f"0 coincidencias de {args.find!r} en {parsed.document.paginas} paginas: "
            "el documento no lo dice, y no se inventa una cita",
            file=sys.stderr,
        )

    payload: dict = {
        "documents": [
            {
                "id": parsed.document.id,
                "url": parsed.document.url,
                "tipo": parsed.document.tipo,
                "paginas": parsed.document.paginas,
            }
        ],
        "evidence": [
            {
                "id": item.id,
                "document_id": item.document_id,
                "pagina": item.pagina,
                "extracto": item.extracto,
                "extractor": item.extractor,
            }
            for item in hits
        ],
    }
    if args.claim and hits:
        payload["claims"] = [
            {
                "id": "AF-1",
                "texto": args.claim,
                "evidence": [item.id for item in hits],
            }
        ]

    persisted = None
    if args.graph:
        store, error = _open_write_store()
        if error:
            print(f"ERROR {error}", file=sys.stderr)
            return 2
        claims = (
            [Claim(id="AF-1", texto=args.claim, evidence_ids=tuple(i.id for i in hits))]
            if args.claim and hits
            else []
        )
        persisted = persist_evidence(
            store,
            parsed,
            hits,
            claims=claims,
            orders=args.orden,
            expediente=(
                {"id": args.expediente, "titulo": args.titulo or args.expediente,
                 "estado": "borrador"}
                if args.expediente else None
            ),
        )
        payload["persisted"] = persisted.to_dict()

    if args.out:
        Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    if args.json:
        print(json.dumps({**parsed.summary(), "hits": len(hits), "payload": payload},
                         ensure_ascii=False, indent=2))
        return 0

    print(parsed.document.url or args.pdf)
    print(
        f"  {parsed.document.id} · {parsed.document.paginas} paginas · "
        f"extractor={parsed.extractor}" + (" · AGPL" if parsed.copyleft else "")
    )
    if parsed.paginas_sin_texto:
        print(f"  paginas sin capa de texto (necesitan OCR): {list(parsed.paginas_sin_texto)}")
    for item in hits:
        preview = item.extracto[:90].replace("\n", " ")
        print(f"  p.{item.pagina}  {item.id}  {preview}")
    if persisted is not None:
        print(
            f"\n  grafo: 1 documento · {len(persisted.evidence)} evidencias · "
            f"{len(persisted.claims)} afirmaciones · "
            f"{len(persisted.accredited)} acreditaciones"
        )
        for what, why in persisted.skipped:
            print(f"  omitido: {what} -> {why}")
    if args.out:
        print(f"\n-> {args.out} (compatible con `expediente gate --input`)")
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


def _cmd_dossier(args: argparse.Namespace) -> int:
    from datetime import date

    from .dossier.build import DossierError

    claims, evidence, documents = load_dossier(args.input)
    report = evaluate(claims, evidence, documents)

    try:
        dossier = build_dossier(
            report,
            documents,
            titulo=args.titulo,
            sujeto=args.sujeto,
            generado_en=args.generado or date.today().isoformat(),
            thresholds={"umbral_clp": 1_000_000},
            detectors=("fraccionamiento",),
        )
    except DossierError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    body = dossier.to_json() if args.format == "json" else dossier.to_markdown()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")

    print(f"expediente {dossier.id}")
    print(f"  firmable:      {'sí' if dossier.signable else 'no'}")
    print(f"  tasa de citas: {dossier.citation_rate:.0%} "
          f"({len(dossier.claims)} de {len(dossier.claims) + len(dossier.rejected)})")
    print(f"  eliminadas:    {len(dossier.rejected)}")
    print(f"  escrito en:    {out_path} ({args.format})")
    return 0


def _cmd_investigate(args: argparse.Namespace) -> int:
    from datetime import date

    orders = load_orders(args.orders)
    store = _graph_with_evidence(orders, args.evidence)

    if args.suppliers and Path(args.suppliers).exists():
        suppliers = load_suppliers(args.suppliers)
        apply_to_store(store, resolve_suppliers(suppliers), suppliers)

    result = investigate(
        store,
        orders,
        titulo=args.titulo,
        sujeto=args.sujeto,
        generado_en=args.generado or date.today().isoformat(),
        route_pattern=args.pattern,
        route_from=("Proveedor", args.key) if args.key else None,
    )

    body = result.dossier.to_json() if args.format == "json" else result.dossier.to_markdown()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(body, encoding="utf-8")

    for route in result.routes:
        print(f"ruta ({route.length} saltos): " + " -> ".join(f"{label} {key}" for label, key in route.nodes))
    print()
    for index, finding in enumerate(result.findings, start=1):
        print(f"[{index}] {finding.explanation()}")

    print()
    print(f"expediente {result.dossier.id}")
    print(f"  afirmaciones:  {result.kept} conservadas · {result.dropped} eliminadas")
    print(f"  tasa de citas: {result.dossier.citation_rate:.0%}")
    print(f"  firmable:      {'sí' if result.dossier.signable else 'no'}")
    print(f"  escrito en:    {out_path} ({args.format})")
    return 0


def _graph_with_evidence(orders, evidence_path: str):
    """Load orders into a graph, then attach the documents that accredit them."""
    store = InMemoryGraphStore()
    for order in orders:
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

    for item in json.loads(Path(evidence_path).read_text(encoding="utf-8")):
        document = store.upsert_node(
            "Documento",
            item["documento_id"],
            url=item.get("url", ""),
            tipo=item.get("tipo", "pdf"),
            paginas=item.get("paginas"),
        )
        orden = store.upsert_node("OrdenCompra", item["orden"])
        # the page lives on the edge: it is the citation for that specific order
        store.upsert_edge(document, "ACREDITA", orden, pagina=item.get("pagina"))
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
