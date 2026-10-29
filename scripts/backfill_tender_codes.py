"""Copy the tender code onto the order, so the chain is readable in one hop.

The graph already had `(:OrdenCompra)-[:DERIVA_DE]->(:Licitacion)`, which is the correct
place for the relationship. But an auditor reading a list of orders wants to see the
tender code in the row, without expanding anything — and a list of 60 rows should be one
query, not sixty traversals.

No network here: the edge already exists, this only copies the value.

    python3 scripts/backfill_tender_codes.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expediente.graph.millenniumdb import MillenniumDBStore
from expediente.graph.schema import ONTO, relation_uri


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    store = MillenniumDBStore.from_env("EXPEDIENTE_MDB_URL")
    client = store.client

    rows = client.select(
        f"SELECT ?o ?l WHERE {{ ?o <{relation_uri('DERIVA_DE')}> ?l FILTER(isIRI(?l)) }}"
    )
    pairs: dict[str, str] = {}
    for row in rows:
        order = str(row["o"])
        tender = str(row["l"])
        if order.startswith("urn:expediente:node/") and tender.startswith("urn:expediente:node/"):
            pairs.setdefault(order, tender.split("/")[-1])

    already = {
        str(row["s"])
        for row in client.select(f"SELECT ?s WHERE {{ ?s <{ONTO}codigo_licitacion> ?v }}")
    }
    missing = {order: code for order, code in pairs.items() if order not in already}
    print(f"órdenes con licitación: {len(pairs)} · ya tienen el código: {len(already)} · faltan: {len(missing)}")

    if not args.apply:
        print("DRY RUN — repetir con --apply")
        return 0

    buffer: list[str] = []
    written = 0
    for order, code in missing.items():
        safe = code.replace("\\", "\\\\").replace('"', '\\"')
        buffer.append(f'<{order}> <{ONTO}codigo_licitacion> "{safe}" .')
        written += 1
        if len(buffer) >= 400:
            client.insert(buffer)
            buffer = []
    if buffer:
        client.insert(buffer)

    print(f"escrito: {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
