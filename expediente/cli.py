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
from .ingest.records import load_dossier, load_orders
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
