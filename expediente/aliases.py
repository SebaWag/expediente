"""Acronym expansion, because people search with the name they say out loud.

The graph stores what the buyer wrote: ``FONDO NACIONAL DE SALUD``. A person — and a
judge on a demo — types ``FONASA``. Neither is wrong, and a search that returns zero
results for the name everybody uses reads as a broken system, not as a strict one.

The mapping is curated, not derived. Initials would turn the official name into ``FNS``,
which nobody says, so generating acronyms from names is guesswork dressed as logic. Every
entry in ``data/aliases.json`` is checked against the loaded graph by
``scripts/validate_aliases.py``: an alias that resolves to nothing is a lie in a
configuration file, and this project has enough of those in the sources already.
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path

ALIASES_PATH = Path(__file__).resolve().parent / "data" / "aliases.json"


@lru_cache(maxsize=1)
def table() -> dict[str, str]:
    """Acronym -> the substring that appears in the data. Keys are upper-cased.

    A missing table degrades the search, it does not break it. That distinction was
    learned the hard way: a deployment that dropped this file made ``/api/search``
    raise ``FileNotFoundError``, which the portal reported as "run no encontrado" —
    a search that fails with the wrong error message is worse than a search without
    acronym expansion. The warning goes to stderr so the degradation is visible.
    """
    try:
        raw = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(
            f"warning: alias table missing at {ALIASES_PATH}; searches will not expand acronyms",
            file=sys.stderr,
            flush=True,
        )
        return {}
    return {key.upper(): value for key, value in raw.items() if not key.startswith("_")}


def expand(text: str) -> list[tuple[str, str]]:
    """Return ``(variant, reason)`` pairs to search in addition to the literal text.

    Only the whole term is expanded. Substring expansion would make ``SEC`` fire inside
    "SECCION", which is how alias tables earn a reputation for false positives.
    """
    needle = text.strip().upper()
    if needle in table():
        return [(table()[needle], f"sigla:{needle}")]
    return []
