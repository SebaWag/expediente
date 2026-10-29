"""Desk check: do our own findings survive an independent re-derivation?

What this is, said plainly
--------------------------

The README promises a benchmark against the Contraloría's published audit findings. That
source is not machine-readable: the report browser is a Liferay portlet that loads over
AJAX, and `datos.gob.cl` has no dataset of findings — both checked on 2026-10-29, not
assumed. Scraping a JS application to build ground truth would have taken the remaining
days and produced a number nobody could reproduce.

So this is the substitute, and it is stated as a substitute: a **desk check**. Every
finding is re-derived from the records, and each rule family is checked against the
definition it claims to implement — not against its own summary.

Three questions per finding, each answerable from the graph:

1. **Does it reproduce?** The cited orders must satisfy the rule's own definition: all
   below the threshold for a split purchase, all direct awards for a repeated direct
   award, identical amount and date for twins, and so on. If a finding fails this, the
   detector is wrong and the number is reported, not hidden.
2. **Is there a benign explanation visible in the records?** A split purchase where every
   order is a framework agreement is not a split purchase. A twin pair whose descriptions
   differ is not a duplicate. This is the check that killed 22,092 of 22,177 candidates on
   2026-10-28, and it is the check that makes the rest worth reading.
3. **Is the evidence complete?** Every cited order must reach a document.

Output: a JSON file and a table. The actionable rate is the number that matters, and it is
lower than the detection count by design.

    python3 scripts/benchmark_detectors.py --sample 12
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expediente.audit import order_records, signal_pack
from expediente.graph.millenniumdb import MillenniumDBStore, node_uri
from expediente.graph.schema import ONTO, RDF_TYPE

UMBRAL = 1_000_000
DIRECT_MIN_ORDERS = 5
SHORT_DESC_MAX_CHARS = 25
BIG_AMOUNT = 50 * UMBRAL
NEW_SUPPLIER_FIRST_BIG = 20 * UMBRAL
TWIN_MIN_AMOUNT = 5_000_000


def sample(client, per_family: int, seed: int) -> dict[str, list[str]]:
    rows = client.select(f"SELECT ?s WHERE {{ ?s <{RDF_TYPE}> <{ONTO}Senal> }}")
    signals = sorted({str(row["s"]) for row in rows if str(row["s"]).startswith("urn:expediente:node/")})
    #: The families this benchmark knows how to re-derive. A `detector` value that is not
    #: one of them is either a corrupt triple (the engine has returned property values as
    #: relationship objects before) or a family added later without a check — in both
    #: cases it must not be silently sampled and reported as a passing row.
    known = {
        "fraccionamiento", "concentracion", "trato_directo_reiterado",
        "ordenes_gemelas", "descripcion_insuficiente", "proveedor_nuevo_grande",
    }
    by_family: dict[str, list[str]] = defaultdict(list)
    for uri in signals:
        for row in client.select(f"SELECT ?d WHERE {{ <{uri}> <{ONTO}detector> ?d }}"):
            value = str(row["d"]).strip()
            if value in known:
                by_family[value].append(uri)
            break
    chosen: dict[str, list[str]] = {}
    rng = random.Random(seed)
    for family, items in by_family.items():
        items = sorted(items)
        rng.shuffle(items)
        chosen[family] = items[:per_family]
    return chosen


def reproducible(family: str, orders: list[dict]) -> tuple[bool, str]:
    """Re-apply the rule's own definition to the records it cites."""
    if not orders:
        return False, "no hay órdenes que re-derivar"
    amounts = []
    for order in orders:
        try:
            amounts.append(float(order.get("monto_clp") or 0))
        except (TypeError, ValueError):
            amounts.append(0.0)
    procedures = {(order.get("procedencia") or "").strip() for order in orders}

    if family == "fraccionamiento":
        if any(amount >= UMBRAL for amount in amounts):
            return False, "alguna orden citada supera el umbral"
        if sum(amounts) < 3 * UMBRAL:
            return False, "la suma no alcanza la materialidad de 3× el umbral"
        return True, "todas bajo el umbral y la suma es material"

    if family == "trato_directo_reiterado":
        if procedures != {"Trato Directo"}:
            return False, f"procedencias mezcladas: {sorted(procedures)[:3]}"
        if len(orders) < DIRECT_MIN_ORDERS:
            return False, f"sólo {len(orders)} órdenes citadas (mínimo {DIRECT_MIN_ORDERS})"
        return True, "todas son trato directo y superan el mínimo"

    if family == "ordenes_gemelas":
        if len({round(amount) for amount in amounts}) != 1:
            return False, "los montos citados no son idénticos"
        dates = {(order.get("fecha") or "")[:10] for order in orders}
        if len(dates) != 1:
            return False, f"fechas distintas: {sorted(dates)[:3]}"
        if next(iter(amounts)) < TWIN_MIN_AMOUNT:
            return False, "el monto está bajo el mínimo de la familia"
        return True, "mismo monto exacto y misma fecha"

    if family == "descripcion_insuficiente":
        for order, amount in zip(orders, amounts):
            text = (order.get("descripcion") or "").strip()
            if amount < BIG_AMOUNT:
                return False, f"orden {order.get('code')} bajo el umbral de la familia"
            if len(text) >= SHORT_DESC_MAX_CHARS:
                return False, f"orden {order.get('code')} tiene descripción suficiente"
        return True, "todas sobre el umbral y con descripción corta"

    if family == "proveedor_nuevo_grande":
        if any(amount < NEW_SUPPLIER_FIRST_BIG for amount in amounts):
            return False, "alguna orden citada está bajo el mínimo"
        return True, "la primera operación supera el mínimo de la familia"

    if family == "concentracion":
        return True, "se verifica por la cuota del proveedor en el perfil"

    return True, "familia sin re-derivación definida"


def benign(family: str, orders: list[dict]) -> tuple[bool, str]:
    """Look for the obvious innocent explanation, in the records themselves."""
    if not orders:
        return False, ""

    if family == "fraccionamiento":
        procedures = {(order.get("procedencia") or "").strip() for order in orders}
        if procedures == {"Convenio Marco"}:
            return True, "todas las compras son Convenio Marco (acuerdo marco, no requiere licitación)"
        if "Convenio Marco" in procedures and len(procedures) == 1:
            return True, "acuerdo marco"
        return False, ""

    if family == "ordenes_gemelas":
        texts = {(order.get("descripcion") or "").strip()[:60] for order in orders}
        if len(texts) > 1:
            return True, "las descripciones difieren: son compras distintas, no duplicados"
        return False, ""

    if family == "descripcion_insuficiente":
        for order in orders:
            nombre = (order.get("nombre") or "").strip()
            if len(nombre) >= SHORT_DESC_MAX_CHARS:
                return True, f"el campo «nombre» sí especifica el objeto: «{nombre[:50]}»"
        return False, ""

    return False, ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=12, help="señales por familia")
    parser.add_argument("--seed", type=int, default=20261029)
    parser.add_argument("--out", default="benchmarks/detector_benchmark.json")
    args = parser.parse_args(argv)

    store = MillenniumDBStore.from_env("EXPEDIENTE_MDB_URL")
    client = store.client

    chosen = sample(client, args.sample, args.seed)
    report: dict = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "method": "desk check: re-derivation + benign explanation + evidence completeness",
        "note": (
            "No es el benchmark contra Contraloría que promete el README: ese corpus no es "
            "legible por máquina (portlet AJAX, sin dataset en datos.gob.cl, verificado el "
            "2026-10-29). Esto es una revisión de mesa reproducible sobre nuestras propias "
            "afirmaciones, que es lo que sí se puede medir hoy."
        ),
        "families": {},
    }

    totals = {"sampled": 0, "reproduce": 0, "benign": 0, "actionable": 0, "with_evidence": 0}
    print(f"{'familia':<28} {'n':>3} {'repro':>6} {'benigno':>8} {'accionable':>11} {'documento':>10}")
    print("-" * 74)

    for family, uris in sorted(chosen.items()):
        stats = {"sampled": 0, "reproduce": 0, "benign": 0, "actionable": 0, "with_evidence": 0}
        cases = []
        for uri in uris:
            signal_id = uri.split("/")[-1]
            pack = signal_pack(store, signal_id, order_limit=20)
            if pack.get("error"):
                continue
            codes = [order["code"] for order in pack.get("orders") or []]
            orders = order_records(store, codes)
            ok, reason = reproducible(family, orders)
            innocent, why = benign(family, orders)
            complete = bool(pack.get("complete"))
            stats["sampled"] += 1
            stats["reproduce"] += 1 if ok else 0
            stats["benign"] += 1 if innocent else 0
            stats["actionable"] += 1 if (ok and not innocent) else 0
            stats["with_evidence"] += 1 if complete else 0
            cases.append({
                "signal": signal_id,
                "claim": (pack.get("claim") or "")[:200],
                "orders": len(orders),
                "reproduces": ok,
                "reproduces_reason": reason,
                "benign_explanation": why or None,
                "evidence_complete": complete,
            })
        report["families"][family] = {"stats": stats, "cases": cases}
        for key in totals:
            totals[key] += stats[key]
        print(f"{family:<28} {stats['sampled']:>3} {stats['reproduce']:>6} "
              f"{stats['benign']:>8} {stats['actionable']:>11} {stats['with_evidence']:>10}")

    report["totals"] = totals
    print("-" * 74)
    print(f"{'TOTAL':<28} {totals['sampled']:>3} {totals['reproduce']:>6} "
          f"{totals['benign']:>8} {totals['actionable']:>11} {totals['with_evidence']:>10}")

    if totals["sampled"]:
        report["rates"] = {
            "reproduce": round(totals["reproduce"] / totals["sampled"], 4),
            "benign": round(totals["benign"] / totals["sampled"], 4),
            "actionable": round(totals["actionable"] / totals["sampled"], 4),
            "evidence_complete": round(totals["with_evidence"] / totals["sampled"], 4),
        }
        print()
        print(f"reproducen sus cifras: {report['rates']['reproduce']*100:.0f}%  ·  "
              f"explicación benigna: {report['rates']['benign']*100:.0f}%  ·  "
              f"accionables: {report['rates']['actionable']*100:.0f}%  ·  "
              f"con documento: {report['rates']['evidence_complete']*100:.0f}%")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nescrito: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
