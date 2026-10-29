"""Repair text that was decoded with the wrong encoding.

The bulk import decoded every archive member as Latin-1. Most members are Latin-1, so
the result was correct; a few are UTF-8, and those went into the graph as mojibake —
"CIRUGÍA" stored as "CIRUGÃA". This script finds the affected values and rewrites them.

The reversal is only attempted when it round-trips: a string is repaired as
``text.encode("latin-1").decode("utf-8")`` and the result is kept **only if encoding it
back gives the original bytes**. Without that check the script would also "repair" text
that was never broken, which is how a data-cleaning bug becomes a data-loss bug.

    python3 scripts/fix_encoding.py             # dry run, prints what it would change
    python3 scripts/fix_encoding.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expediente.graph.millenniumdb import MillenniumDBStore
from expediente.graph.schema import ONTO

#: The text properties this project writes. Enumerated instead of scanning every triple:
#: a predicate-free FILTER over 64.8 M triples is the 45-second query the API already
#: replaced with a VALUES list.
TEXT_PROPERTIES = (
    "descripcion",
    "nombre",
    "razon_social",
    "direccion_texto",
    "direccion_norm",
    "explicacion",
    "extracto",
    "titulo",
    "organismo_nombre",
    "proveedor_nombre",
)

#: What a mis-decoded UTF-8 sequence looks like once it has been read as Latin-1.
MARKER = "\u00c3"


def _runs(text: str) -> str | None:
    """Repair the UTF-8 runs inside a string that is otherwise Latin-1.

    Some records carry both: "Paz, Bi\xado BÃio Garantía" is one string where most of
    the text decoded correctly and a fragment did not. Reversing the whole string fails,
    because the correct parts are not valid UTF-8 — so only the runs that *are* valid
    UTF-8 get re-encoded, and everything else is left exactly as it was.

    A run is accepted when it starts with 0xC2-0xF4 and its continuation bytes are
    well-formed, and when decoding it produces a non-ASCII character. Numbers and plain
    ASCII are never touched, which is what keeps a currency amount from becoming a
    different currency amount.
    """
    try:
        raw = text.encode("latin-1")
    except UnicodeEncodeError:
        return None  # a character outside Latin-1: not our damage, do not touch it

    out: list[str] = []
    index = 0
    changes = 0
    while index < len(raw):
        byte = raw[index]
        if byte < 0xC2:
            out.append(chr(byte))
            index += 1
            continue
        expected = 2 if byte < 0xE0 else 3 if byte < 0xF0 else 4 if byte < 0xF5 else 0
        if expected and index + expected <= len(raw):
            candidate = raw[index : index + expected]
            try:
                decoded = candidate.decode("utf-8")
            except UnicodeDecodeError:
                decoded = ""
            if decoded and any(ord(character) > 0x7F for character in decoded) and len(decoded) == 1:
                out.append(decoded)
                index += expected
                changes += 1
                continue
        out.append(chr(byte))
        index += 1

    if not changes:
        return None
    repaired = "".join(out)
    return repaired if repaired != text else None


def repair(text: str) -> str | None:
    """Return the fixed text, or ``None`` if this string was not broken."""
    if MARKER not in text:
        return None
    try:
        candidate = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None
    # Round-trip check: the repair must be reversible, or it is a rewrite, not a repair.
    try:
        if candidate.encode("utf-8").decode("latin-1") != text:
            return None
    except (UnicodeEncodeError, UnicodeDecodeError):
        return None
    return candidate if candidate != text else None


def lit(value: str) -> str:
    """A SPARQL string literal, with control characters escaped as \\uXXXX.

    The mojibake itself carries C1 control characters (0x80-0x9F): "CÁNULA" was stored
    as "CÃ\x81NULA", and that 0x81 is part of the value. Splicing it raw into a query
    is a parse error at best and a different string at worst, so every non-printable
    becomes an escape.
    """
    out = []
    for character in value:
        code = ord(character)
        if character == "\\":
            out.append("\\\\")
        elif character == '"':
            out.append('\\"')
        elif code < 0x20 or 0x7F <= code <= 0x9F:
            out.append(f"\\u{code:04X}")
        else:
            out.append(character)
    return '"' + "".join(out) + '"'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mdb", default=os.environ.get("EXPEDIENTE_MDB_URL", "http://127.0.0.1:1235"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    store = MillenniumDBStore.from_env("EXPEDIENTE_MDB_URL")
    store.client.base_url = args.mdb.rstrip("/")
    client = store.client

    fixed_total = inspected = 0
    for prop in TEXT_PROPERTIES:
        rows = client.select(
            f'SELECT ?s ?o WHERE {{ ?s <{ONTO}{prop}> ?o FILTER(CONTAINS(STR(?o), "{MARKER}")) }}'
        )
        inspected += len(rows)
        changed = 0
        for row in rows:
            subject, value = str(row["s"]), str(row["o"])
            better = repair(value)
            if better is None:
                # Mixed strings: most of the text is right and a fragment is not.
                better = _runs(value)
            if better is None:
                continue
            changed += 1
            if args.apply:
                client.update(f"DELETE DATA {{ <{subject}> <{ONTO}{prop}> {lit(value)} . }}")
                client.update(f"INSERT DATA {{ <{subject}> <{ONTO}{prop}> {lit(better)} . }}")
                if changed % 25 == 0:
                    print(f"  {prop}: {changed} reparados…", flush=True)
        fixed_total += changed
        print(f"{prop}: {len(rows)} con marca, {changed} reparable(s)", flush=True)

    print(f"\ntotal inspeccionados: {inspected} · reparables: {fixed_total}")
    if not args.apply:
        print("DRY RUN — repetir con --apply para escribir.")
        return 0
    print("escrito")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
