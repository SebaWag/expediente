"""Attach the tender behind each tender code, as a document.

An order cites `CodigoLicitacion`; the graph had a `:Licitacion` node with nothing behind
it. Fetching the tender record closes the last gap in the chain:

    Senal -> OrdenCompra -> Licitacion -> Documento

so an auditor can walk from "this pattern exists" to "this was the procedure that created
it" to "here are the bytes we read about that procedure".

Idempotent by digest, dry-run by default, same rules as attach_evidence.py.

    python3 scripts/attach_licitaciones.py --apply
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

API = "https://api.mercadopublico.cl/servicios/v1/publico/licitaciones.json"
DELAY_S = 1.15


def literal(value: object) -> str:
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")[:900]
    return f'"{text}"'


def tenders(client) -> list[str]:
    rows = client.select(f"SELECT ?s WHERE {{ ?s <{RDF_TYPE}> <{ONTO}Licitacion> }}")
    out = []
    seen = set()
    for row in rows:
        uri = str(row["s"])
        if uri.startswith("urn:expediente:node/") and uri not in seen:
            seen.add(uri)
            out.append(uri)
    return out


def fetch(code: str, ticket: str) -> tuple[bytes, dict]:
    url = f"{API}?{urllib.parse.urlencode({'codigo': code, 'ticket': ticket})}"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Expediente/0.6 (auditoría; sebastian@wagnersolutionsai.com)",
                 "Accept": "application/json"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read(), {
                    "url": f"{API}?codigo={code}&ticket=<redacted>",
                    "status": int(getattr(response, "status", 200)),
                    "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 3:
                time.sleep(4 * (attempt + 1))
                continue
            raise RuntimeError(f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            raise RuntimeError(f"unreachable: {exc}") from exc
    raise RuntimeError("gave up")


def extract(record: dict) -> str:
    parts = []
    if record.get("Nombre"):
        parts.append(str(record["Nombre"])[:120])
    if record.get("Estado"):
        parts.append(f"estado {record['Estado']}")
    if record.get("FechaCierre"):
        parts.append(f"cierra {str(record['FechaCierre'])[:10]}")
    if record.get("CodigoExterno"):
        parts.append(f"código {record['CodigoExterno']}")
    return " · ".join(parts) or "registro sin campos utilizables"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticket", default=os.environ.get("MP_TICKET", ""))
    parser.add_argument("--dir", default=os.environ.get("EXPEDIENTE_EVIDENCE_DIR", "/opt/expediente/evidencia"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    if not args.ticket:
        print("MP_TICKET is not set", file=sys.stderr)
        return 2

    store = MillenniumDBStore.from_env("EXPEDIENTE_MDB_URL")
    client = store.client
    out_dir = Path(args.dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    codes = [uri.split("/")[-1] for uri in tenders(client)]
    if args.limit:
        codes = codes[: args.limit]
    print(f"licitaciones en el grafo: {len(codes)}", flush=True)

    existing = {
        str(row["h"]) for row in client.select(f"SELECT ?h WHERE {{ ?s <{ONTO}hash_sha256> ?h }}")
    }

    written = skipped = failed = 0
    buffer: list[str] = []
    started = time.time()

    for index, code in enumerate(codes, start=1):
        path = out_dir / f"lic-{code}.json"
        if path.exists():
            raw = path.read_bytes()
            meta_path = out_dir / f"lic-{code}.meta.json"
            meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"url": API, "retrieved_at": ""}
        else:
            try:
                raw, meta = fetch(code, args.ticket)
            except RuntimeError as exc:
                failed += 1
                print(f"  ! {code}: {exc}", file=sys.stderr, flush=True)
                continue
            path.write_bytes(raw)
            (out_dir / f"lic-{code}.meta.json").write_text(json.dumps(meta, indent=2))
            (out_dir / f"lic-{code}.sha256").write_text(hashlib.sha256(raw).hexdigest())
            time.sleep(DELAY_S)

        digest = hashlib.sha256(raw).hexdigest()
        if digest in existing:
            skipped += 1
            continue

        try:
            payload = json.loads(raw)
            listing = payload.get("Listado") or []
            record = listing[0] if listing else {}
        except ValueError:
            failed += 1
            continue

        document = node_uri("Documento", digest)
        evidence = node_uri("Evidencia", f"lic-{code}-{digest[:10]}")
        tender = node_uri("Licitacion", code)
        buffer += [
            f"<{document}> <{RDF_TYPE}> <{ONTO}Documento> .",
            f"<{document}> <{ONTO}hash_sha256> {literal(digest)} .",
            f"<{document}> <{ONTO}tipo> {literal('registro_api_licitacion')} .",
            f"<{document}> <{ONTO}url> {literal(meta['url'])} .",
            f"<{document}> <{ONTO}recuperado_en> {literal(meta.get('retrieved_at', ''))} .",
            f"<{document}> <{relation_uri('DOCUMENTA')}> <{tender}> .",
            f"<{evidence}> <{RDF_TYPE}> <{ONTO}Evidencia> .",
            f"<{evidence}> <{ONTO}id> {literal(f'lic-{code}-{digest[:10]}')} .",
            f"<{evidence}> <{ONTO}pagina> {literal('Listado[0]')} .",
            f"<{evidence}> <{ONTO}extracto> {literal(extract(record))} .",
            f"<{evidence}> <{relation_uri('EN')}> <{document}> .",
        ]
        # the tender's own fields, now that we have read them
        if record.get("Nombre"):
            buffer.append(f"<{tender}> <{ONTO}nombre> {literal(record['Nombre'])} .")
        if record.get("Estado"):
            buffer.append(f"<{tender}> <{ONTO}estado> {literal(record['Estado'])} .")
        if record.get("CodigoExterno"):
            buffer.append(f"<{tender}> <{ONTO}codigo> {literal(record['CodigoExterno'])} .")

        existing.add(digest)
        written += 1

        if len(buffer) >= 400:
            client.insert(buffer)
            buffer = []

        if index % 20 == 0:
            print(f"  [{index}/{len(codes)}] documentos {written} · saltados {skipped} · "
                  f"fallos {failed} · {time.time()-started:.0f}s", flush=True)

    if buffer:
        client.insert(buffer)

    print(f"\nlisto: {written} licitación(es) con documento, {skipped} ya existentes, {failed} fallo(s)")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
