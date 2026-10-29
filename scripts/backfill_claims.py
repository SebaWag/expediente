"""Write the claim -> signal edge for signals already ingested.

A separate script because the evidence job takes an hour and this takes a second. It
only emits the :Afirmacion triples (text, detector, score, and the AFIRMA edge) for
every signal whose claim text is in the graph, so it can be re-run at any time without
touching the documents.

    python3 scripts/backfill_claims.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expediente.graph.millenniumdb import MillenniumDBStore, node_uri, relation_uri
from expediente.graph.schema import ONTO, RDF_TYPE


def lit(value: object) -> str:
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')[:900]
    return f'"{text}"'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    store = MillenniumDBStore.from_env("EXPEDIENTE_MDB_URL")
    client = store.client

    uris = [
        str(row["s"])
        for row in client.select(f"SELECT ?s WHERE {{ ?s <{RDF_TYPE}> <{ONTO}Senal> }}")
        if str(row["s"]).startswith("urn:expediente:node/")
    ]
    print(f"señales: {len(uris)}")

    triples: list[str] = []
    for uri in uris:
        props = {}
        for row in client.select(f"SELECT ?p ?o WHERE {{ <{uri}> ?p ?o }}"):
            predicate = str(row["p"]).replace(ONTO, "")
            if predicate.startswith("rel/") or predicate.endswith("#type"):
                continue
            props.setdefault(predicate, str(row["o"]))
        claim = (props.get("explicacion") or "").strip()
        if not claim:
            continue
        signal_id = uri.split("/")[-1]
        node = node_uri("Afirmacion", f"senal-{signal_id}")
        triples += [
            f"<{node}> <{RDF_TYPE}> <{ONTO}Afirmacion> .",
            f"<{node}> <{ONTO}id> {lit(f'senal-{signal_id}')} .",
            f"<{node}> <{ONTO}texto> {lit(claim)} .",
            f"<{node}> <{ONTO}detector> {lit(props.get('detector') or '')} .",
            f"<{node}> <{ONTO}score> {lit(props.get('score') or '')} .",
            f"<{node}> <{ONTO}severidad> {lit(props.get('severidad') or '')} .",
            f"<{node}> <{relation_uri('AFIRMA')}> <{uri}> .",
        ]

    print(f"triples a escribir: {len(triples)}")
    if not args.apply:
        print("DRY RUN — repetir con --apply")
        return 0
    for start in range(0, len(triples), 400):
        client.insert(triples[start : start + 400])
    print("escrito")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
