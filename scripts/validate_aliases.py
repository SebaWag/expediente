#!/usr/bin/env python3
"""Check every acronym in the table against the loaded graph.

An alias that resolves to nothing is worse than no alias: it makes the search look
broken in a different way, and it is invisible until a demo. This script is the
regression test for the table, run against a real graph rather than the in-memory stub
because the point is whether the *data* contains the name.

    EXPEDIENTE_MDB_URL=http://127.0.0.1:1235 python3 scripts/validate_aliases.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expediente.aliases import table
from expediente.graph.millenniumdb import MillenniumDBStore
from expediente.mcp.server import search


def main() -> int:
    store = MillenniumDBStore.from_env()
    failures: list[str] = []

    print(f"{'sigla':<22} {'resultados':>10}  nombre en los datos")
    print("-" * 96)
    for acronym, official in sorted(table().items()):
        result = search(store, {"label": "Organismo", "text": acronym, "limit": 3})
        count = result["count"]
        example = result["results"][0]["match"] if result["results"] else "—"
        status = "" if count else "  ← SIN RESULTADOS"
        print(f"{acronym:<22} {count:>10}  {example[:52]}{status}")
        if not count:
            failures.append(acronym)

    print()
    print(f"{len(table()) - len(failures)}/{len(table())} siglas resuelven")
    if failures:
        print("sin resultados:", ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
