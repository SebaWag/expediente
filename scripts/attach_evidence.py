"""Attach the evidence layer: documents, evidence, claims and licitaciones.

The problem this solves
-----------------------

The graph had 4.95 M purchase orders and 85 detected signals, and **zero documents**.
A signal pointed at orders; nothing pointed at a source. So ``citas`` returned an empty
list, the dossier could not cite a file, and the export was a summary with no proof —
which is exactly the thing this project exists not to be.

What this builds, per order cited by a signal:

    (:Afirmacion)-[:SOPORTADA_POR]->(:Evidencia)-[:EN]->(:Documento)-[:ACREDITA]->(:OrdenCompra)
                                                     (:OrdenCompra)-[:DERIVA_DE]->(:Licitacion)

and it puts a **file on disk** behind each ``:Documento``:

    <dir>/<codigo>.json        the exact bytes Mercado Público returned
    <codigo>.sha256            the digest of those bytes
    <codigo>.meta.json         url, retrieval time, http status, digest

Three properties that make this evidence and not decoration:

* **The digest is over the bytes we received**, so a third party can re-fetch the URL
  and compare. The URL is recorded, with the ticket redacted.
* **``:Evidencia.pagina`` is a JSON path** (``Listado[0].Total``), the exact position of
  the value inside the record. A page number is where you look; a JSON path is the same
  idea for a machine-readable source, and it is checkable.
* **Idempotent by digest**: re-running changes nothing. The documents already attached
  are counted and skipped.

Usage
-----

    python3 scripts/attach_evidence.py                  # dry run, prints the plan
    python3 scripts/attach_evidence.py --apply           # writes files and triples
    python3 scripts/attach_evidence.py --apply --limit 40 --per-signal 8
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expediente.graph.millenniumdb import MillenniumDBStore, node_uri, relation_uri
from expediente.graph.schema import ONTO, RDF_TYPE

API = "https://api.mercadopublico.cl/servicios/v1/publico/ordenesdecompra.json"
PUBLIC_OC = "https://www.mercadopublico.cl/Procurement/Modules/RFB/DetailsAcquisition.aspx?idOC="

#: Mercado Público tolerates roughly one call per second before it answers 429. Measured
#: on 2026-10-28: 10 concurrent workers produced 490 errors out of 1,200 requests.
DELAY_S = 1.15


# ------------------------------------------------------------------ graph reads
def signals_with_orders(client, limit: int | None, per_signal: int) -> list[dict]:
    """Every signal, with the order codes it observes.

    Two queries, never ``OPTIONAL``. Measured against MillenniumDB v1.0.0 on 2026-10-29:
    a single query with two ``OPTIONAL`` blocks returned **1,365 rows for 85 signals** —
    a cartesian product across signals, so each row carried another signal's
    ``explicacion``. The ingest started attaching the wrong claim text to the wrong
    signal. Splitting into "which signals exist" and "what are their properties",
    joined in Python by subject, returns exactly 85 and is the same pattern the HTTP
    API already uses for ``/signals``.
    """
    rows = client.select(f"SELECT ?s WHERE {{ ?s <{RDF_TYPE}> <{ONTO}Senal> }}")
    uris = []
    seen: set[str] = set()
    for row in rows:
        uri = str(row["s"])
        if not uri.startswith("urn:expediente:node/") or uri in seen:
            continue
        seen.add(uri)
        uris.append(uri)
    if limit:
        uris = uris[:limit]
    if not uris:
        return []

    properties: dict[str, dict[str, str]] = {uri: {} for uri in uris}
    for chunk_start in range(0, len(uris), 60):
        chunk = uris[chunk_start : chunk_start + 60]
        values = " ".join(f"<{uri}>" for uri in chunk)
        for row in client.select(f"SELECT ?s ?p ?o WHERE {{ VALUES ?s {{ {values} }} ?s ?p ?o }}"):
            subject = str(row["s"])
            predicate = str(row["p"]).replace(ONTO, "")
            if subject not in properties or predicate.startswith("rel/") or predicate.endswith("#type"):
                continue
            properties[subject].setdefault(predicate, str(row["o"]))

    out: list[dict] = []
    for uri in uris:
        props = properties.get(uri, {})
        order_rows = client.select(
            f"SELECT ?o WHERE {{ <{uri}> <{relation_uri('SE_OBSERVA_EN')}> ?o "
            f"FILTER(isIRI(?o)) }} LIMIT {per_signal}"
        )
        orders = []
        for order_row in order_rows:
            order_uri = str(order_row["o"])
            if not order_uri.startswith("urn:expediente:node/"):
                continue
            orders.append(
                {
                    "code": order_uri.split("/")[-1],
                    "props": {
                        str(r["p"]).replace(ONTO, ""): str(r["o"])
                        for r in client.select(f"SELECT ?p ?o WHERE {{ <{order_uri}> ?p ?o }}")
                        if not str(r["p"]).startswith(ONTO + "rel/")
                        and not str(r["p"]).endswith("#type")
                    },
                }
            )
        out.append(
            {
                "id": uri.split("/")[-1],
                "detector": props.get("detector", ""),
                "score": props.get("score", ""),
                "severity": props.get("severidad", ""),
                "claim": (props.get("explicacion") or "").strip(),
                "orders": orders,
            }
        )
    return out


def existing_digests(client) -> set[str]:
    rows = client.select(f"SELECT ?h WHERE {{ ?s <{ONTO}hash_sha256> ?h }}")
    return {str(row["h"]) for row in rows}


# ------------------------------------------------------------------- the source
def fetch_order(code: str, ticket: str) -> tuple[bytes, dict]:
    """Fetch one purchase order from the official API. Returns raw bytes and metadata."""
    query = urllib.parse.urlencode({"codigo": code, "ticket": ticket})
    url = f"{API}?{query}"
    redacted = f"{API}?codigo={code}&ticket=<redacted>"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Expediente/0.5 (auditoría de compras públicas; sebastian@wagnersolutionsai.com)",
            "Accept": "application/json",
        },
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return raw, {
                    "url": redacted,
                    "status": int(getattr(response, "status", 200)),
                    "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 3:
                time.sleep(4 * (attempt + 1))
                continue
            raise RuntimeError(f"HTTP {exc.code} for {code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"unreachable for {code}: {exc}") from exc
    raise RuntimeError(f"gave up on {code}")


def literal(value: object) -> str:
    text = str(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'"{text}"^^<http://www.w3.org/2001/XMLSchema#decimal>'
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")[:900]
    return f'"{escaped}"'


def triples_for(signal: dict, code: str, raw: bytes, meta: dict, record: dict) -> list[str]:
    """Everything one order contributes to the evidence chain."""
    digest = hashlib.sha256(raw).hexdigest()
    document = node_uri("Documento", digest)
    evidence = node_uri("Evidencia", f"{code}-{digest[:12]}")
    order = node_uri("OrdenCompra", code)
    claim_node = node_uri("Afirmacion", f"senal-{signal['id']}")

    lines: list[str] = [
        f"<{document}> <{RDF_TYPE}> <{ONTO}Documento> .",
        f"<{document}> <{ONTO}hash_sha256> {literal(digest)} .",
        f"<{document}> <{ONTO}tipo> {literal('registro_api')} .",
        f"<{document}> <{ONTO}url> {literal(meta['url'])} .",
        f"<{document}> <{ONTO}recuperado_en> {literal(meta['retrieved_at'])} .",
        f"<{document}> <{ONTO}codigo_orden> {literal(code)} .",
        # The evidence is the specific fields the claim leans on, with their position.
        f"<{evidence}> <{RDF_TYPE}> <{ONTO}Evidencia> .",
        f"<{evidence}> <{ONTO}id> {literal(f'{code}-{digest[:12]}')} .",
        f"<{evidence}> <{ONTO}pagina> {literal('Listado[0]')} .",
        f"<{evidence}> <{ONTO}extracto> {literal(extract(record))} .",
        f"<{evidence}> <{relation_uri('EN')}> <{document}> .",
        f"<{document}> <{relation_uri('ACREDITA')}> <{order}> .",
        f"<{claim_node}> <{relation_uri('SOPORTADA_POR')}> <{evidence}> .",
    ]

    # A tender code in the record turns a purchase order into a link in the lifecycle.
    tender = record.get("CodigoLicitacion")
    if tender:
        tender_node = node_uri("Licitacion", str(tender))
        lines += [
            f"<{tender_node}> <{RDF_TYPE}> <{ONTO}Licitacion> .",
            f"<{tender_node}> <{ONTO}codigo> {literal(tender)} .",
            f"<{order}> <{relation_uri('DERIVA_DE')}> <{tender_node}> .",
        ]
    return lines


def extract(record: dict) -> str:
    """The quotable line: what was bought, for how much, when, under which procedure."""
    parts = []
    if record.get("Nombre"):
        parts.append(str(record["Nombre"])[:120])
    if record.get("Total") is not None:
        parts.append(f"Total {record['Total']}")
    dates = record.get("Fechas") or {}
    if dates.get("FechaCreacion"):
        parts.append(f"creada {str(dates['FechaCreacion'])[:10]}")
    if record.get("Estado"):
        parts.append(f"estado {record['Estado']}")
    if record.get("CodigoLicitacion"):
        parts.append(f"licitación {record['CodigoLicitacion']}")
    return " · ".join(parts) or "registro sin campos utilizables"


def claim_triples(signal: dict, codes: list[str]) -> list[str]:
    """The signal's own deterministic sentence becomes a :Afirmacion."""
    if not signal["claim"]:
        return []
    node = node_uri("Afirmacion", f"senal-{signal['id']}")
    return [
        f"<{node}> <{RDF_TYPE}> <{ONTO}Afirmacion> .",
        f"<{node}> <{ONTO}id> {literal(f'senal-{signal["id"]}')} .",
        f"<{node}> <{ONTO}texto> {literal(signal["claim"])} .",
        f"<{node}> <{ONTO}origen> {literal('detector')} .",
        f"<{node}> <{ONTO}detector> {literal(signal["detector"])} .",
        f"<{node}> <{ONTO}score> {literal(signal["score"])} .",
        f"<{node}> <{ONTO}severidad> {literal(signal["severity"])} .",
        f"<{node}> <{ONTO}ordenes_citadas> {literal(len(codes))} .",
    ]


#: An IRI has a scheme. The engine answers a blank 400 when one does not, naming only
#: the offending value ("The IRI '1.0' is not absolute"), which cost an hour the first
#: time: the cause was a record whose field we were about to use as an identifier.
_IRI = __import__("re").compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


def validate(triples: list[str]) -> None:
    """Refuse to send a triple whose subject or predicate is not an absolute IRI."""
    import re as _re

    for line in triples:
        parts = _re.findall(r"<([^<>]*)>", line)
        if len(parts) < 2:
            continue
        for iri in parts[:2]:  # subject and predicate; objects may be literals
            if not _IRI.match(iri):
                raise ValueError(f"not an absolute IRI: {iri!r} in triple: {line[:240]}")


# ------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Attach documents, evidence and claims to the graph")
    parser.add_argument("--mdb", default=os.environ.get("EXPEDIENTE_MDB_URL", "http://127.0.0.1:1235"))
    parser.add_argument("--ticket", default=os.environ.get("MP_TICKET", ""))
    parser.add_argument("--dir", default=os.environ.get("EXPEDIENTE_EVIDENCE_DIR", "/opt/expediente/evidencia"))
    parser.add_argument("--limit", type=int, default=None, help="how many signals to process")
    parser.add_argument("--per-signal", type=int, default=12, help="orders per signal")
    parser.add_argument("--apply", action="store_true", help="write files and triples (default: dry run)")
    args = parser.parse_args(argv)

    if not args.ticket:
        print("MP_TICKET is not set; the API will reject every request", file=sys.stderr)
        return 2

    store = MillenniumDBStore.from_env("EXPEDIENTE_MDB_URL")
    store.client.base_url = args.mdb.rstrip("/")
    client = store.client

    out_dir = Path(args.dir)
    declared = client.select(f"SELECT (COUNT(*) AS ?n) WHERE {{ ?s <{RDF_TYPE}> <{ONTO}Senal> }}")
    expected = int(declared[0]["n"]) if declared else 0
    pool = signals_with_orders(client, args.limit, args.per_signal)
    print(f"señales a procesar: {len(pool)} (el grafo declara {expected})", flush=True)
    if not args.limit and expected and len(pool) != expected:
        print(
            f"ABORT: read {len(pool)} signals but the graph counts {expected}. "
            "A reader that disagrees with the count is returning duplicated rows; writing "
            "from it would attach claims to the wrong signal.",
            file=sys.stderr,
            flush=True,
        )
        return 3
    total_orders = sum(len(s["orders"]) for s in pool)
    print(f"órdenes citadas: {total_orders} (tope {args.per_signal} por señal)", flush=True)

    have = existing_digests(client)
    print(f"documentos ya adjuntos: {len(have)}", flush=True)

    if not args.apply:
        print("\nDRY RUN — nada escrito. Repetir con --apply.")
        for signal in pool[:3]:
            print(f"  · señal {signal['id']} ({signal['detector']}, {len(signal['orders'])} órdenes)")
            for order in signal["orders"][:2]:
                print(f"      {order['code']}: {str(order['props'].get('descripcion'))[:70]}")
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    written = skipped = failed = 0
    triples_buffer: list[str] = []
    started = time.time()

    for index, signal in enumerate(pool, start=1):
        codes: list[str] = []
        for order in signal["orders"]:
            code = order["code"]
            codes.append(code)
            path = out_dir / f"{code}.json"
            if path.exists():
                raw = path.read_bytes()
                meta_path = out_dir / f"{code}.meta.json"
                meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"url": PUBLIC_OC + code, "retrieved_at": ""}
            else:
                try:
                    raw, meta = fetch_order(code, args.ticket)
                except RuntimeError as exc:
                    failed += 1
                    print(f"  ! {code}: {exc}", file=sys.stderr, flush=True)
                    continue
                path.write_bytes(raw)
                (out_dir / f"{code}.meta.json").write_text(json.dumps(meta, indent=2))
                (out_dir / f"{code}.sha256").write_text(hashlib.sha256(raw).hexdigest())
                time.sleep(DELAY_S)

            digest = hashlib.sha256(raw).hexdigest()
            if digest in have:
                skipped += 1
                continue

            try:
                payload = json.loads(raw)
                records = payload.get("Listado") or []
                record = records[0] if records else {}
            except ValueError:
                failed += 1
                continue

            triples_buffer += triples_for(signal, code, raw, meta, record)
            have.add(digest)
            written += 1

        triples_buffer += claim_triples(signal, codes)
        if len(triples_buffer) >= 400:
            validate(triples_buffer)
            client.insert(triples_buffer)
            triples_buffer = []

        elapsed = time.time() - started
        print(
            f"  [{index}/{len(pool)}] señal {signal['id'][:12]} · docs {written} · saltados {skipped} "
            f"· fallos {failed} · {elapsed:.0f}s",
            flush=True,
        )

    if triples_buffer:
        validate(triples_buffer)
        client.insert(triples_buffer)

    print(f"\nlisto: {written} documento(s) nuevos, {skipped} ya existentes, {failed} fallo(s)")
    print(f"archivos en {out_dir}")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
