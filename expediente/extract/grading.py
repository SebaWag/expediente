"""How a predicted value is compared to a labelled one. Deterministic, no model.

This module is the reason the extraction benchmark can be trusted at all. If the
grader were an LLM, a comparison between two backends would be a comparison of two
models judged by a third model, and a backends' win could come from agreeing with
the judge's taste rather than from reading the document.

So the rules are boring and written down:

- ``numero``  equal within 1%, which absorbs the last-centimo rounding of a real
  document but not a factor-of-ten error.
- ``fecha``   compared as (year, month, day) after parsing the three common
  Chilean formats. Day and month swapped is a different date, and it is the error
  a reader is least likely to catch.
- ``rut``     compared as body + check digit, punctuation and leading zeros
  ignored. A missing check digit is not a match: it means nobody verified it.
- ``enum``    compared after normalisation, with an explicit alias table, because
  the documents say "Compra Ágil" and the schema says "compra_agil".
- ``texto``   compared on a normalised form (case, accents, punctuation, spaces).

Every function here is pure. That is what lets the same labels score two backends
months apart and get the same number.
"""

from __future__ import annotations

import re
import unicodedata

from .schema import FieldSpec

_NUM_CLEAN = re.compile(r"[^0-9,.\-]")
_DATE_PARTS = re.compile(r"(\d{1,4})\D+(\d{1,2})\D+(\d{1,4})")

#: Documents write the mechanism as prose; the schema stores it as a token.
ALIASES = {
    "compra_agil": ("compra agil", "compraagil", "compra ágil", "agil", "cot"),
    "licitacion_publica": ("licitacion publica", "licitación pública", "licitacion", "le"),
    "trato_directo": ("trato directo", "td", "contratacion directa"),
    "convenio_marco": ("convenio marco", "cm", "catalogo"),
    "otro": ("otro", "otra", "otros"),
    "orden_compra": ("orden de compra", "oc", "orden compra"),
    "resolucion_adjudicacion": ("resolucion de adjudicacion", "resolución adjudicación", "adjudicacion", "res adj"),
    "bases_licitacion": ("bases", "bases de licitacion", "bases administrativas"),
    "contrato": ("contrato",),
}


def normalize_text(value: str) -> str:
    """Lowercase, strip accents, collapse punctuation and whitespace."""
    decomposed = unicodedata.normalize("NFKD", str(value))
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = ascii_only.lower()
    cleaned = re.sub(r"[^a-z0-9\s]", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


def parse_number(value: str) -> float | None:
    """Read a Chilean amount: ``$940.000``, ``940,000``, ``1.048.500``, ``940000.50``.

    Both separators are ambiguous in isolation, so the last one that appears with
    exactly two digits after it is read as the decimal separator and everything
    else is grouping. That covers the formats in ChileCompra's own files.
    """
    if value is None:
        return None
    raw = _NUM_CLEAN.sub("", str(value).strip())
    if not raw or raw in {"-", ".", ","}:
        return None
    decimal_at = max(raw.rfind("."), raw.rfind(","))
    if decimal_at != -1 and len(raw) - decimal_at - 1 == 2 and re.search(r"\d", raw[:decimal_at]):
        integer_part = re.sub(r"[.,]", "", raw[:decimal_at])
        decimal_part = raw[decimal_at + 1 :]
        raw = f"{integer_part}.{decimal_part}"
    else:
        raw = re.sub(r"[.,]", "", raw)
    try:
        return float(raw)
    except ValueError:
        return None


def parse_date(value: str) -> tuple[int, int, int] | None:
    """Return ``(year, month, day)`` from ``dd/mm/yyyy``, ``yyyy-mm-dd`` or ``dd-mm-yyyy``."""
    if value is None:
        return None
    match = _DATE_PARTS.search(str(value))
    if not match:
        return None
    first, middle, last = match.groups()
    if len(first) == 4:
        year, month, day = int(first), int(middle), int(last)
    else:
        day, month, year = int(first), int(middle), int(last)
        if year < 100:
            year += 2000
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return (year, month, day)


def normalize_rut(value: str) -> str | None:
    """Reduce a RUT to ``body + check digit``, uppercase, no punctuation."""
    if value is None:
        return None
    cleaned = re.sub(r"[^0-9kK]", "", str(value)).upper()
    if len(cleaned) < 2:
        return None
    return cleaned.lstrip("0") or cleaned


def _same_rut(predicted: str, gold: str) -> bool:
    left, right = normalize_rut(predicted), normalize_rut(gold)
    if left is None or right is None:
        return False
    if left == right:
        return True
    # A body without its check digit is not the same claim as a verified RUT.
    return False


def _same_enum(predicted: str, gold: str, spec: FieldSpec) -> bool:
    predicted_norm, gold_norm = normalize_text(predicted).replace(" ", "_"), normalize_text(gold).replace(" ", "_")
    if predicted_norm == gold_norm:
        return True
    for canonical, aliases in ALIASES.items():
        family = {normalize_text(canonical).replace(" ", "_")}
        family |= {normalize_text(alias).replace(" ", "_") for alias in aliases}
        if gold_norm in family and predicted_norm in family:
            return True
    return False


def grade(spec: FieldSpec, predicted: str | None, gold: str | None) -> bool:
    """Is the predicted value the labelled one? A missing prediction is never right."""
    if predicted is None or gold is None:
        return False
    if not str(gold).strip():
        return False

    if spec.tipo == "numero":
        left, right = parse_number(predicted), parse_number(gold)
        if left is None or right is None:
            return False
        tolerance = max(1e-6, 0.01 * abs(right))
        return abs(left - right) <= tolerance
    if spec.tipo == "fecha":
        return parse_date(predicted) is not None and parse_date(predicted) == parse_date(gold)
    if spec.tipo == "rut":
        return _same_rut(predicted, gold)
    if spec.tipo == "enum":
        return _same_enum(predicted, gold, spec)
    if spec.tipo == "texto":
        left, right = normalize_text(predicted), normalize_text(gold)
        return bool(right) and (left == right or right in left or left in right)
    raise ValueError(f"unknown field kind: {spec.tipo!r} (add its comparison to grading.grade)")
