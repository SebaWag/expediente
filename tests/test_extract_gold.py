"""The gold set is only honest if every label is written in the page text.

These tests encode that invariant. Without them, a rendering bug could label a
document with a value it never prints, and the extractor would be scored on a
question nobody could answer. That failure mode is invisible in the benchmark
output: it just looks like a worse model.

Run: ``python3 -m pytest tests/test_extract_gold.py -q``
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from expediente.extract.grading import normalize_rut, normalize_text, parse_date, parse_number
from expediente.extract.schema import PROCUREMENT_FIELDS, by_name

GOLD = Path(__file__).resolve().parent.parent / "benchmarks" / "extract_gold.jsonl"

_MONTOS = re.compile(r"\$?\s?\d[\d.,]*")
_FECHAS = re.compile(r"\b\d{1,4}\D{1,3}\d{1,2}\D{1,3}\d{1,4}\b")


def _examples() -> list[dict]:
    if not GOLD.exists():
        pytest.skip(f"gold set no generado: {GOLD}")
    with GOLD.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


EXAMPLES = _examples()


def _texto(ex: dict) -> str:
    return "\n".join(p["texto"] for p in ex["paginas"])


def test_gold_has_enough_documents():
    assert len(EXAMPLES) >= 500, f"solo {len(EXAMPLES)} documentos; el rango útil es 500-1000"


def test_every_example_has_gold_and_pages():
    for ex in EXAMPLES:
        assert ex["gold"], f"{ex['id']}: gold vacío"
        assert ex["paginas"], f"{ex['id']}: sin páginas"
        assert all(p["texto"].strip() for p in ex["paginas"]), f"{ex['id']}: página vacía"


def test_only_known_fields_are_labelled():
    conocidos = set(by_name())
    for ex in EXAMPLES:
        desconocidos = set(ex["gold"]) - conocidos
        assert not desconocidos, f"{ex['id']}: campos fuera del schema: {desconocidos}"


def test_enum_labels_are_valid_choices():
    specs = by_name()
    for ex in EXAMPLES:
        for campo, valor in ex["gold"].items():
            spec = specs[campo]
            if spec.choices:
                assert valor in spec.choices, f"{ex['id']}: {campo}={valor!r} no está en choices"


def test_orden_compra_id_is_printed():
    for ex in EXAMPLES:
        assert ex["gold"]["orden_compra_id"] in _texto(ex), f"{ex['id']}: código no impreso"


def test_rut_is_printed():
    for ex in EXAMPLES:
        esperado = normalize_rut(ex["gold"]["rut_proveedor"])
        assert esperado, f"{ex['id']}: RUT de gold inválido"
        assert esperado in normalize_rut(_texto(ex)), f"{ex['id']}: RUT {esperado} no impreso"


def test_unidad_compradora_is_printed():
    for ex in EXAMPLES:
        esperado = normalize_text(ex["gold"]["unidad_compradora"])
        assert esperado and esperado in normalize_text(_texto(ex)), (
            f"{ex['id']}: unidad compradora no impresa"
        )


def test_montos_are_printed():
    specs = by_name()
    for ex in EXAMPLES:
        texto = _texto(ex)
        encontrados = {parse_number(m.group()) for m in _MONTOS.finditer(texto)}
        encontrados.discard(None)
        for campo in ("monto_total_clp", "monto_neto_clp"):
            valor = ex["gold"].get(campo)
            if valor is None:
                continue
            gold = parse_number(valor)
            assert gold is not None
            # el render imprime con separador de miles; el grader acepta 1%
            assert any(
                abs(m - gold) <= max(1e-6, 0.01 * abs(gold)) for m in encontrados
            ), f"{ex['id']}: {campo}={valor} no aparece en el texto"


def test_fecha_is_printed():
    for ex in EXAMPLES:
        esperado = parse_date(ex["gold"]["fecha_orden"])
        assert esperado is not None, f"{ex['id']}: fecha de gold ilegible"
        halladas = {parse_date(m.group()) for m in _FECHAS.finditer(_texto(ex))}
        assert esperado in halladas, f"{ex['id']}: fecha {esperado} no impresa"


def test_monto_neto_is_absent_when_no_breakdown():
    """Si el documento no desglosa, el gold NO debe traer neto (es MISSING_FIELD legítimo)."""
    sin_neto = [ex for ex in EXAMPLES if "monto_neto_clp" not in ex["gold"]]
    assert sin_neto, "se esperaba una fracción de documentos sin desglose"
    for ex in sin_neto:
        # palabra completa: "alimnetos" contiene "neto" como substring y no es un neto
        assert not re.search(r"\bneto\b", normalize_text(_texto(ex))), (
            f"{ex['id']}: marcado sin neto pero la etiqueta aparece en el texto"
        )
