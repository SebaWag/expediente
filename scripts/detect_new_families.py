"""Four more detector families, computed over the five-year order series.

The first two detectors (splitting below the threshold, spend concentration) each look at
one dimension. These look at four that an auditor asks about and no dashboard answers:

1. **Direct awards repeated to the same supplier.** `Trato Directo` is legal with cause;
   five of them from one buyer to one supplier is a question. Counted, not judged.
2. **Twin orders.** Same buyer, same supplier, same amount, same date. Either a
   split purchase or a data-entry duplicate — both worth a look, and both citable
   because the codes are the evidence.
3. **A large purchase with a description that says nothing.** Above 50× the bidding
   threshold with fewer than 25 characters of description is a specification problem,
   and it is deterministic.
4. **A new supplier whose first order is enormous.** The first order in the series being
   above 20× the threshold is the shape of a supplier that appeared with a contract.

Each family emits the same object the existing detectors emit: a rule, a sentence with
real numbers, a severity, and the order codes that locate the evidence. Nothing here
decides anything; the counts are the finding.

Reads the aggregated series on disk (no graph round trips) and writes signals to the
graph. Idempotent by signal id, dry-run by default.

    python3 scripts/detect_new_families.py            # dry run
    python3 scripts/detect_new_families.py --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expediente.graph.millenniumdb import MillenniumDBStore, node_uri, relation_uri
from expediente.graph.schema import ONTO, RDF_TYPE

UMBRAL = 1_000_000

#: Thresholds, all stated in the sentence the finding carries, because a finding whose
#: threshold is hidden cannot be argued with.
DIRECT_MIN_ORDERS = 5
DIRECT_MIN_SHARE = 0.6          # of that supplier's orders from that buyer
TWIN_MIN_AMOUNT = 5_000_000
SHORT_DESC_MAX_CHARS = 25
BIG_AMOUNT = 50 * UMBRAL        # 50 millones
NEW_SUPPLIER_FIRST_BIG = 20 * UMBRAL


def lit(value: object) -> str:
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    text = "".join(char if ord(char) >= 0x20 or char == "\t" else " " for char in text)[:900]
    return f'"{text}"'


def signal_id(detector: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join([detector, *parts]).encode("utf-8")).hexdigest()
    return digest[:16]


def scan(path: Path) -> dict:
    """One pass over the series, accumulating the four dimensions."""
    direct = defaultdict(lambda: {"count": 0, "monto": 0, "codes": []})
    pairs = defaultdict(int)          # (org, prov) -> total orders, for the share
    twins = defaultdict(lambda: {"count": 0, "codes": []})
    short_desc = defaultdict(lambda: {"count": 0, "monto": 0, "codes": [], "when": "", "samples": []})
    supplier_first = {}               # prov_rut -> (fecha_iso, monto, code, org)
    new_big = defaultdict(lambda: {"count": 0, "monto": 0, "codes": []})
    #: RUT -> name. A finding that names a supplier by RUT is a finding an auditor cannot
    #: read; the names are in the same record, so they are captured here.
    names: dict[str, str] = {}
    total = 0

    started = time.time()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            total += 1
            if total % 500_000 == 0:
                print(f"  {total:,} líneas · {time.time()-started:.0f}s", flush=True)
            try:
                row = json.loads(line)
            except ValueError:
                continue

            org = (row.get("organismo_rut") or "").strip()
            prov = (row.get("proveedor_rut") or "").strip()
            if not org or not prov:
                continue
            code = row.get("codigo") or ""
            fecha = row.get("fecha") or ""
            iso = fecha[6:10] + "-" + fecha[3:5] + "-" + fecha[0:2] if len(fecha) >= 10 else ""
            try:
                monto = int(row.get("monto_total_clp") or 0)
            except (TypeError, ValueError):
                monto = 0
            procedencia = (row.get("procedencia") or "").strip()
            # Both fields, not just the first non-empty one. The benchmark found that 7 of
            # 8 findings in this family had the object spelled out in `nombre` while
            # `descripcion` was short — the detector was flagging a field, not a purchase.
            descripcion = max(
                (row.get("descripcion") or "").strip(),
                (row.get("nombre") or "").strip(),
                key=len,
            )

            pairs[(org, prov)] += 1
            if org not in names and row.get("organismo"):
                names[org] = str(row["organismo"]).strip()[:120]
            if prov not in names and row.get("proveedor"):
                names[prov] = str(row["proveedor"]).strip()[:120]

            if procedencia == "Trato Directo":
                entry = direct[(org, prov)]
                entry["count"] += 1
                entry["monto"] += monto
                if len(entry["codes"]) < 12:
                    entry["codes"].append(code)

            if monto >= TWIN_MIN_AMOUNT:
                key = (org, prov, monto, iso)
                twin = twins[key]
                twin["count"] += 1
                if len(twin["codes"]) < 8:
                    twin["codes"].append(code)

            if monto >= BIG_AMOUNT and len(descripcion) < SHORT_DESC_MAX_CHARS:
                entry = short_desc[(org, prov)]
                entry["count"] += 1
                entry["monto"] += monto
                entry["when"] = iso or entry["when"]
                if len(entry["codes"]) < 10:
                    entry["codes"].append(code)
                if len(entry["samples"]) < 2:
                    entry["samples"].append(descripcion or "(vacía)")

            if prov not in supplier_first or (iso and iso < supplier_first[prov][0]):
                supplier_first[prov] = (iso, monto, code, org, row.get("proveedor") or prov)

            if iso and supplier_first.get(prov) and iso == supplier_first[prov][0] and monto >= NEW_SUPPLIER_FIRST_BIG:
                entry = new_big[(org, prov)]
                entry["count"] += 1
                entry["monto"] += monto
                if len(entry["codes"]) < 6:
                    entry["codes"].append(code)

    print(f"  leídas {total:,} líneas en {time.time()-started:.0f}s", flush=True)
    return {
        "direct": direct, "pairs": pairs, "twins": twins, "short": short_desc,
        "first": supplier_first, "new_big": new_big, "names": names,
    }


def findings(data: dict, names: dict) -> list[dict]:
    """Turn the counters into findings. Every sentence carries its own threshold."""
    out: list[dict] = []

    for (org, prov), entry in data["direct"].items():
        total_pair = data["pairs"].get((org, prov), entry["count"])
        share = entry["count"] / total_pair if total_pair else 0
        if entry["count"] < DIRECT_MIN_ORDERS or share < DIRECT_MIN_SHARE:
            continue
        score = round(entry["count"] * (entry["monto"] / UMBRAL), 2)
        out.append({
            "detector": "trato_directo_reiterado",
            "clase": "mismo_proveedor",
            "org": org, "prov": prov,
            "score": score,
            "severity": "alta" if share >= 0.9 and entry["count"] >= 8 else "media",
            "orders": entry["codes"],
            "monto": entry["monto"],
            "text": (
                f"{entry['count']} órdenes por Trato Directo de {names.get(org, org)} a "
                f"{names.get(prov, prov)} — el {round(share*100)}% de lo que ese comprador le adjudicó, "
                f"por ${entry['monto']:,}. Un trato directo tiene causales legales; el patrón es la pregunta."
            ),
        })

    for (org, prov, monto, iso), entry in data["twins"].items():
        if entry["count"] < 2:
            continue
        out.append({
            "detector": "ordenes_gemelas",
            "clase": "mismo_monto_misma_fecha",
            "org": org, "prov": prov,
            "score": round(entry["count"] * (monto / UMBRAL), 2),
            "severity": "alta" if entry["count"] >= 3 else "media",
            "orders": entry["codes"],
            "monto": monto * entry["count"],
            "text": (
                f"{entry['count']} órdenes de {names.get(org, org)} a {names.get(prov, prov)} por el mismo "
                f"monto exacto (${monto:,}) el mismo día {iso}. Puede ser un fraccionamiento o una "
                f"duplicación de registro; los códigos son la evidencia."
            ),
        })

    for (org, prov), entry in data["short"].items():
        out.append({
            "detector": "descripcion_insuficiente",
            "clase": "objeto_no_especificado",
            "org": org, "prov": prov,
            "score": round(entry["monto"] / UMBRAL, 2),
            "severity": "alta" if entry["monto"] >= 10 * BIG_AMOUNT else "media",
            "orders": entry["codes"],
            "monto": entry["monto"],
            "text": (
                f"{entry['count']} compra(s) de {names.get(org, org)} a {names.get(prov, prov)} sobre "
                f"${BIG_AMOUNT:,} con una descripción de menos de {SHORT_DESC_MAX_CHARS} caracteres "
                f"(ej: «{entry['samples'][0] if entry['samples'] else '—'}»), por ${entry['monto']:,}. "
                f"Una compra grande sin objeto identificable no se puede auditar después."
            ),
        })

    for (org, prov), entry in data["new_big"].items():
        first = data["first"].get(prov)
        out.append({
            "detector": "proveedor_nuevo_grande",
            "clase": "primera_compra_grande",
            "org": org, "prov": prov,
            "score": round(entry["monto"] / UMBRAL, 2),
            "severity": "media",
            "orders": entry["codes"],
            "monto": entry["monto"],
            "text": (
                f"{names.get(prov, prov)} aparece por primera vez en la serie con una orden de "
                f"${entry['monto']:,} a {names.get(org, org)}"
                + (f" el {first[0]}" if first else "")
                + f" — sobre {NEW_SUPPLIER_FIRST_BIG:,} pesos en su primera operación registrada."
            ),
        })

    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/home/seba/expediente/out/orders_5y.jsonl")
    parser.add_argument("--limit", type=int, default=120, help="how many findings per family")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    path = Path(args.input)
    if not path.is_file():
        print(f"no existe {path}", file=sys.stderr)
        return 2

    print(f"leyendo {path} ({path.stat().st_size/1e9:.1f} GB)", flush=True)
    data = scan(path)

    names = data.get("names") or {}
    all_findings = findings(data, names)
    by_detector: dict[str, list[dict]] = defaultdict(list)
    for finding in all_findings:
        by_detector[finding["detector"]].append(finding)

    print()
    for detector, items in sorted(by_detector.items()):
        items.sort(key=lambda f: -f["score"])
        print(f"{detector}: {len(items)} hallazgo(s)")
        for finding in items[:2]:
            print(f"   · [{finding['severity']}] {finding['text'][:118]}…")

    selected = []
    for detector, items in by_detector.items():
        selected += sorted(items, key=lambda f: -f["score"])[: args.limit]
    print(f"\ntotal a materializar: {len(selected)}")

    if not args.apply:
        print("DRY RUN — repetir con --apply")
        return 0

    store = MillenniumDBStore.from_env("EXPEDIENTE_MDB_URL")
    client = store.client

    existing = {
        str(row["s"]).split("/")[-1]
        for row in client.select(f"SELECT ?s WHERE {{ ?s <{RDF_TYPE}> <{ONTO}Senal> }}")
    }

    today = time.strftime("%Y-%m-%d")
    buffer: list[str] = []
    written = 0
    for finding in selected:
        sid = signal_id(finding["detector"], finding["org"], finding["prov"],
                        finding["orders"][0] if finding["orders"] else "")
        if sid in existing:
            continue
        signal = node_uri("Senal", sid)
        detector_node = node_uri("Detector", finding["detector"])
        buffer += [
            f"<{signal}> <{RDF_TYPE}> <{ONTO}Senal> .",
            f"<{signal}> <{ONTO}id> {lit(sid)} .",
            f"<{signal}> <{ONTO}detector> {lit(finding['detector'])} .",
            f"<{signal}> <{ONTO}score> {lit(finding['score'])} .",
            f"<{signal}> <{ONTO}severidad> {lit(finding['severity'])} .",
            f"<{signal}> <{ONTO}clase> {lit(finding['clase'])} .",
            f"<{signal}> <{ONTO}fecha_deteccion> {lit(today)} .",
            f"<{signal}> <{ONTO}monto_clp> {lit(int(finding['monto']))} .",
            f"<{signal}> <{ONTO}ordenes> {lit(len(finding['orders']))} .",
            f"<{signal}> <{ONTO}explicacion> {lit(finding['text'])} .",
            f"<{signal}> <{ONTO}organismo_nombre> {lit(finding['org'])} .",
            f"<{signal}> <{ONTO}proveedor_nombre> {lit(finding['prov'])} .",
            f"<{detector_node}> <{RDF_TYPE}> <{ONTO}Detector> .",
            f"<{detector_node}> <{ONTO}codigo> {lit(finding['detector'])} .",
            f"<{detector_node}> <{ONTO}version> {lit('1.0')} .",
            f"<{detector_node}> <{relation_uri('PRODUCE')}> <{signal}> .",
        ]
        for code in finding["orders"]:
            buffer.append(f"<{signal}> <{relation_uri('SE_OBSERVA_EN')}> <{node_uri('OrdenCompra', code)}> .")
        existing.add(sid)
        written += 1
        if len(buffer) >= 400:
            client.insert(buffer)
            buffer = []
    if buffer:
        client.insert(buffer)

    print(f"escrito: {written} señal(es) nuevas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
