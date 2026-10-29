"""Delete signal nodes that have nothing in them.

A node typed `:Senal` with no detector, no explanation and no cited orders is not a
finding with missing data — it is a node that should not exist, and it makes the count
lie. Four of them appeared in the production graph, most likely from a batched write that
landed its type triple but not its properties.

This is deliberately narrow: it only removes nodes that are **typed and empty**. A signal
with a detector but no orders is a real problem and it stays, because deleting it would
hide it.

    python3 scripts/prune_orphans.py            # dry run
    python3 scripts/prune_orphans.py --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expediente.graph.millenniumdb import MillenniumDBStore
from expediente.graph.schema import ONTO, RDF_TYPE

#: Properties that make a signal a signal. If none of them is present, the node is empty.
MEANINGFUL = ("detector", "explicacion", "score")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    store = MillenniumDBStore.from_env("EXPEDIENTE_MDB_URL")
    client = store.client

    rows = client.select(f"SELECT ?s WHERE {{ ?s <{RDF_TYPE}> <{ONTO}Senal> }}")
    signals = sorted({str(row["s"]) for row in rows if str(row["s"]).startswith("urn:expediente:node/")})
    print(f"señales: {len(signals)}")

    empty: list[str] = []
    for uri in signals:
        predicates = {
            str(row["p"]).replace(ONTO, "")
            for row in client.select(f"SELECT ?p ?o WHERE {{ <{uri}> ?p ?o }}")
        }
        if not any(name in predicates for name in MEANINGFUL):
            empty.append(uri)

    print(f"sin ninguna propiedad significativa: {len(empty)}")
    for uri in empty:
        print("  ·", uri.split("/")[-1])

    if not empty:
        print("nada que limpiar")
        return 0
    if not args.apply:
        print("\nDRY RUN — repetir con --apply")
        return 0

    for uri in empty:
        client.update(f"DELETE DATA {{ <{uri}> <{RDF_TYPE}> <{ONTO}Senal> . }}")
        print("borrado:", uri.split("/")[-1])

    left = client.select(f"SELECT (COUNT(*) AS ?n) WHERE {{ ?s <{RDF_TYPE}> <{ONTO}Senal> }}")
    print(f"señales ahora: {left[0]['n'] if left else '?'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
