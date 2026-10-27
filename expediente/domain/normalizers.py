"""Deterministic normalisers shared by ingestion, resolution and detectors.

Every function here is pure: the same input always yields the same output.
That is a hard requirement of the project. Evidence has to be reproducible,
so the transformations that produce it cannot be stochastic.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_RUT_STRIP = re.compile(r"[^0-9Kk]")
_THOUSANDS_GROUPED = re.compile(r"^\d{1,3}(?:\.\d{3})+$")
_ISO_PERIOD = re.compile(r"^(\d{4})-(\d{2})")
_DMY = re.compile(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})$")
_COMPACT_DATE = re.compile(r"^(\d{2})(\d{2})(\d{4})$")
_ALNUM = re.compile(r"[^a-z0-9]+")

#: Tokens that carry no identifying weight in a Chilean legal name. Dropping
#: them is what lets "Comercial Spa" and "COMERCIAL S.P.A." collapse together.
#: Tokens that appear in Chilean addresses without locating anything.
ADDRESS_NOISE_TOKENS = frozenset({"de", "del", "la", "las", "el", "los", "y", "n", "of", "piso"})

LEGAL_NAME_TOKENS = frozenset(
    {
        "spa", "ltda", "limitada", "sa", "eirl", "cia", "sociedad", "anonima",
        "empresa", "individual", "responsabilidad", "unipersonal", "y",
    }
)


def normalize_rut(raw: str | int | None) -> str | None:
    """Return a canonical Chilean RUT (``12345678-9``), or None if unusable.

    RUTs arrive written a dozen ways: with dots, with a dash, with the check
    digit detached, with a lowercase ``k``. The canonical form is the supplier
    key in the graph, so it has to be stable across every source.
    """
    if raw is None:
        return None
    compact = _RUT_STRIP.sub("", str(raw)).upper()
    if len(compact) < 2:
        return None
    return f"{compact[:-1]}-{compact[-1]}"


def rut_check_digit(body: str) -> str:
    """Compute the Chilean check digit for a RUT body written without separators."""
    total, weight = 0, 2
    for char in reversed(body):
        total += int(char) * weight
        weight = 2 if weight == 7 else weight + 1
    remainder = 11 - (total % 11)
    if remainder == 11:
        return "0"
    if remainder == 10:
        return "K"
    return str(remainder)


def is_valid_rut(raw: str | int | None) -> bool:
    """True when the RUT is well formed and its check digit matches."""
    normalized = normalize_rut(raw)
    if normalized is None:
        return False
    body, check = normalized.split("-")
    return rut_check_digit(body) == check


def name_key(raw: str | None) -> str:
    """Normalise a legal name into a comparable key.

    Strips accents, punctuation and legal-form noise so that variants of the
    same company compare equal. It is a blocking aid for entity resolution,
    never a decision: two names with the same key are still confirmed by RUT,
    address or legal representative.
    """
    if not raw:
        return ""
    decomposed = unicodedata.normalize("NFD", str(raw))
    plain = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    # Dots are removed before tokenising so that "S.P.A." collapses to "spa",
    # while the remaining punctuation becomes a separator.
    tokens = _ALNUM.sub(" ", plain.lower().replace(".", "")).split()
    return " ".join(token for token in tokens if token not in LEGAL_NAME_TOKENS)


def parse_clp(value: object) -> int | None:
    """Parse a CLP amount written as a number or a localised string.

    Handles ``1234567``, ``"1.234.567"``, ``"$ 1.234.567"`` and ``"1234567.0"``.
    Returns None when the value carries no usable amount, so callers can skip
    the record instead of inventing a zero.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    text = str(value).strip().replace("$", "").replace(" ", "")
    if not text:
        return None
    text = text.replace(".", "") if _THOUSANDS_GROUPED.match(text) else text.replace(",", "")
    try:
        return int(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def parse_date(value: object) -> date | None:
    """Parse a date from a ``date``, ``datetime``, ISO string or Chilean DMY string."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    head = text.split("T")[0].split(" ")[0]
    for pattern in (_DMY, _COMPACT_DATE):
        match = pattern.match(head)
        if match:
            day, month, year = (int(part) for part in match.groups())
            try:
                return date(year, month, day)
            except ValueError:
                return None
    try:
        return date.fromisoformat(head)
    except ValueError:
        return None


def period_key(value: object) -> str | None:
    """Return the ``YYYY-MM`` period for a date-like value.

    Monthly grouping is the unit of the splitting rule, so this is the one
    normaliser the detectors cannot do without.
    """
    if isinstance(value, str):
        match = _ISO_PERIOD.match(value.strip())
        if match:
            return f"{match.group(1)}-{match.group(2)}"
    parsed = parse_date(value)
    if parsed is None:
        return None
    return f"{parsed.year:04d}-{parsed.month:02d}"


def geo_bucket(latitude: float, longitude: float, decimals: int = 3) -> str:
    """Round coordinates onto a grid cell, used as a blocking key.

    Three decimals is roughly 110 m at Chilean latitudes: close enough to catch
    two suppliers sharing one address, coarse enough not to split a city block
    across cells.
    """
    return f"{round(latitude, decimals):.{decimals}f},{round(longitude, decimals):.{decimals}f}"


def address_key(raw: str | None) -> str:
    """Normalise an address into a blocking key.

    Addresses arrive as "Av. Bernardo O'Higgins 1234, Of. 51" and "AV BERNARDO
    O HIGGINS 1234 OF 51". Same block: strip accents, unify punctuation, drop the
    noise tokens that carry no locating power.

    This is a *blocking* key, never an equality decision: two suppliers sharing a
    block are a candidate for review, not a confirmed shared address.
    """
    if not raw:
        return ""
    decomposed = unicodedata.normalize("NFD", str(raw))
    plain = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    tokens = _ALNUM.sub(" ", plain.lower().replace(".", "")).split()
    return " ".join(token for token in tokens if token not in ADDRESS_NOISE_TOKENS)
