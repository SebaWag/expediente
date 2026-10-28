#!/usr/bin/env python3
"""Load OCDS supplier addresses into the graph as :Domicilio nodes.

The bulk archives carry no supplier address, which left the resolver's
``DOMICILIO_COMPARTIDO`` relation with no data source and the "supplier network"
detector with nothing to detect (see docs/OCDS_FINDINGS.md). OCDS has the address on
almost every supplier party. This script wires the two together.

What it writes
--------------
    (:Proveedor) -[:TIENE_DOMICILIO]-> (:Domicilio)

Both the label and the edge already exist in ``graph/schema.py``; nothing here invents
vocabulary. The ``:Domicilio`` key is a 16-hex digest of the normalised address, so
running this twice produces the same URIs and re-inserting is a no-op.

It deliberately does **not** write a ``:Senal``. ``SE_OBSERVA_EN`` runs
``(:Senal) -> (:OrdenCompra)``, and a shared address is not observed in a single
purchase order. Forcing it there would put a fact in the wrong place to make a
dashboard look full. The shared-address finding is a **path** in the graph
(Proveedor -> Domicilio <- Proveedor), which is exactly what the navigation agent
exists to demonstrate. Promoting it to a signal needs a vocabulary decision, not a
workaround.

The RUT format trap
-------------------
``rut_norm`` in this graph holds the ChileCompra display form, dots included
(``79.984.240-8``), not the output of ``normalize_rut`` (``79984240-8``). Confirmed:
103,131 of 103,220 ``:Proveedor`` nodes carry the dotted form. Joining OCDS on
``normalize_rut`` output therefore matches **0 of 863** suppliers; joining on the
display form matches **755 of 863 (87.5%)**. ``rut_display`` below is the adapter.

Usage
-----
    # dry run, prints the plan and never touches the endpoint
    EXPEDIENTE_MDB_URL=http://127.0.0.1:1235 python3 scripts/load_ocds_suppliers.py

    # actually write
    ... python3 scripts/load_ocds_suppliers.py --apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expediente.domain.normalizers import normalize_rut
from expediente.graph.schema import NODE_PREFIX, ONTO, RDF_TYPE, XSD, node_uri, relation_uri
from expediente.graph.sparql import SparqlClient

#: Same noise list the resolver uses, so a Domicilio here blocks like one there.
ADDRESS_NOISE_TOKENS = frozenset(
    {"de", "del", "la", "las", "el", "los", "y", "n", "of", "piso", "oficina", "of", "local", "casa"}
)


def rut_display(raw: str) -> str | None:
    """Canonical RUT rendered the way the bulk wrote it: ``79.984.240-8``."""
    normalised = normalize_rut(raw)
    if not normalised:
        return None
    body, digit = normalised.split("-")
    groups: list[str] = []
    while len(body) > 3:
        groups.insert(0, body[-3:])
        body = body[:-3]
    groups.insert(0, body)
    return ".".join(groups) + "-" + digit


def address_key(raw: str | None) -> str | None:
    """Normalised address, used both for blocking and for the Domicilio key."""
    if not raw:
        return None
    decomposed = unicodedata.normalize("NFD", str(raw))
    plain = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    tokens = [
        token
        for token in re.sub(r"[^a-z0-9]+", " ", plain.lower().replace(".", "")).split()
        if token not in ADDRESS_NOISE_TOKENS
    ]
    return " ".join(tokens) if tokens else None


def domicilio_key(address_key_value: str) -> str:
    return hashlib.sha256(address_key_value.encode("utf-8")).hexdigest()[:16]


def lit(value: object) -> str:
    if isinstance(value, bool):
        return f'"{ "true" if value else "false" }"^^<{XSD}boolean>'
    if isinstance(value, int):
        return f'"{value}"^^<{XSD}integer>'
    text = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("\r", " ")
    text = "".join(char for char in text if ord(char) >= 0x20 or char == "\t")
    return f'"{text}"'


def existing_suppliers(client: SparqlClient, ruts: list[str], batch: int = 200) -> set[str]:
    found: set[str] = set()
    for start in range(0, len(ruts), batch):
        chunk = ruts[start : start + batch]
        values = " ".join(f'"{rut}"' for rut in chunk)
        query = (
            f"SELECT ?rut WHERE {{ ?p <{RDF_TYPE}> <{ONTO}Proveedor> . "
            f"?p <{ONTO}rut_norm> ?rut . VALUES ?rut {{ {values} }} }}"
        )
        for row in client.select(query):
            found.add(row["rut"])
    return found


def build(suppliers: dict, present: set[str], today: str) -> tuple[list[str], dict]:
    triples: list[str] = []
    stats = {"suppliers_total": len(suppliers), "matched": 0, "with_address": 0, "domicilios": 0, "edges": 0}
    seen_domicilios: set[str] = set()

    for rut_raw, entry in suppliers.items():
        rut = rut_display(rut_raw)
        if not rut or rut not in present:
            continue
        stats["matched"] += 1

        raw_address = entry.get("street")
        key = address_key(raw_address)
        if not key:
            continue
        stats["with_address"] += 1

        did = domicilio_key(key)
        domicilio_uri = node_uri("Domicilio", did)
        supplier_uri = node_uri("Proveedor", rut)

        if did not in seen_domicilios:
            seen_domicilios.add(did)
            triples += [
                f"<{domicilio_uri}> <{RDF_TYPE}> <{ONTO}Domicilio> .",
                f"<{domicilio_uri}> <{ONTO}geo_hash> {lit(did)} .",
                f"<{domicilio_uri}> <{ONTO}direccion_norm> {lit(key)} .",
                f"<{domicilio_uri}> <{ONTO}direccion_texto> {lit(raw_address)} .",
            ]
            region = entry.get("region")
            if region:
                triples.append(f"<{domicilio_uri}> <{ONTO}region> {lit(region.strip())} .")
            triples.append(f"<{domicilio_uri}> <{ONTO}fuente> {lit('OCDS')} .")
            stats["domicilios"] += 1

        triples.append(
            f"<{supplier_uri}> <{relation_uri('TIENE_DOMICILIO')}> <{domicilio_uri}> ."
        )
        stats["edges"] += 1

    return triples, stats


def shared_address_pairs(client: SparqlClient, limit: int = 10) -> list[dict]:
    query = f"""
SELECT ?dir (COUNT(DISTINCT ?p) AS ?n) WHERE {{
  ?p <{relation_uri('TIENE_DOMICILIO')}> ?d .
  ?d <{ONTO}direccion_texto> ?dir .
}} GROUP BY ?dir HAVING (COUNT(DISTINCT ?p) > 1)
ORDER BY DESC(?n) LIMIT {limit}
"""
    return client.select(query)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suppliers", default="benchmarks/ocds_suppliers_2000.json")
    parser.add_argument("--url", default="http://127.0.0.1:1235")
    parser.add_argument("--apply", action="store_true", help="write to the graph (default is a dry run)")
    parser.add_argument("--today", default="2026-10-28")
    args = parser.parse_args(argv[1:])

    suppliers = json.loads(Path(args.suppliers).read_text(encoding="utf-8"))
    client = SparqlClient(args.url, timeout_s=180)

    candidates = sorted({rut_display(r) for r in suppliers if rut_display(r)})
    present = existing_suppliers(client, candidates)
    print(f"RUTs OCDS: {len(candidates)} · presentes en el grafo: {len(present)}")

    triples, stats = build(suppliers, present, args.today)
    print(
        f"matched={stats['matched']} with_address={stats['with_address']} "
        f"domicilios={stats['domicilios']} edges={stats['edges']} triples={len(triples)}"
    )

    print("\nmuestra de triples:")
    for line in triples[:6]:
        print("  " + line)

    if not args.apply:
        print("\nDRY RUN — nada escrito. Repetir con --apply para escribir.")
        return 0

    client.insert(triples)
    print(f"\nescritos {len(triples)} triples")

    pairs = shared_address_pairs(client)
    print("\ndirecciones compartidas en el grafo:")
    for row in pairs:
        print(f"  {row.get('n'):>3} proveedores · {row.get('dir')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
