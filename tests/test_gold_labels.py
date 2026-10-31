"""The labels must be answerable from the page, not from the database row.

A gold set whose label contradicts the document it ships is not a hard benchmark, it is
a broken one: the extractor is asked to recover something the page never says. That is
what happened here -- 194 labels -- and these tests hold the line so it cannot happen
again silently.

The invariant is one line long and it is checked against the real 800-document file:
**the document type must equal what the document's own first line declares.**
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from expediente.extract.schema import by_name

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "benchmarks" / "extract_gold.jsonl"


def load_fixer():
    path = ROOT / "benchmarks" / "fix_gold_labels.py"
    spec = importlib.util.spec_from_file_location("fix_gold_labels", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fix_gold_labels"] = module
    spec.loader.exec_module(module)
    return module


def example(title, tipo="orden_compra", procedencia="compra_agil"):
    return {
        "id": "gold-00001",
        "paginas": [{"numero": 1, "texto": f"{title}\nResto del documento."}],
        "gold": {"tipo_documento": tipo, "procedencia": procedencia},
    }


def test_a_ficha_de_adjudicacion_is_not_a_purchase_order():
    fixer = load_fixer()

    assert fixer.tipo_por_titulo("COMPRA ÁGIL — FICHA DE ADJUDICACIÓN") == "ficha_compra_agil"


def test_the_convenio_title_wins_over_the_generic_order_prefix():
    fixer = load_fixer()

    # "CONVENIO MARCO — ORDEN DE COMPRA N° ..." starts with neither prefix by accident:
    # the order of the rules is the whole test.
    assert fixer.tipo_por_titulo("CONVENIO MARCO — ORDEN DE COMPRA N° 1-2-CM22") == "orden_compra"
    assert fixer.procedencia_por_titulo("CONVENIO MARCO — ORDEN DE COMPRA N° 1-2-CM22") == "convenio_marco"


def test_a_resolution_stays_a_resolution():
    fixer = load_fixer()

    assert fixer.tipo_por_titulo("RESOLUCIÓN DE ADJUDICACIÓN") == "resolucion_adjudicacion"


def test_an_unknown_title_changes_nothing():
    fixer = load_fixer()
    doc = example("DOCUMENTO SIN FORMATO CONOCIDO")

    assert fixer.tipo_por_titulo("DOCUMENTO SIN FORMATO CONOCIDO") is None
    assert fixer.fix(doc) == []


def test_fix_relabels_only_what_the_page_contradicts():
    fixer = load_fixer()
    doc = example("COMPRA ÁGIL — FICHA DE ADJUDICACIÓN", tipo="orden_compra", procedencia="compra_agil")

    changes = fixer.fix(doc)

    assert doc["gold"]["tipo_documento"] == "ficha_compra_agil"
    assert doc["gold"]["procedencia"] == "compra_agil"  # the page does declare this one
    assert changes == ["tipo_documento: orden_compra -> ficha_compra_agil"]


def test_fix_is_idempotent():
    fixer = load_fixer()
    doc = example("CONVENIO MARCO — ORDEN DE COMPRA N° 9", procedencia="trato_directo")

    fixer.fix(doc)
    assert fixer.fix(doc) == []


def test_the_new_class_is_in_the_schema():
    choices = by_name()["tipo_documento"].choices

    assert "ficha_compra_agil" in choices
    assert "orden_compra" in choices


def test_every_document_in_the_real_gold_agrees_with_its_own_first_line():
    fixer = load_fixer()
    rows = [json.loads(line) for line in GOLD.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert len(rows) == 800
    offenders = []
    for row in rows:
        head = fixer.titulo(row)
        expected = fixer.tipo_por_titulo(head)
        if expected is not None and row["gold"]["tipo_documento"] != expected:
            offenders.append((row["id"], head[:40], row["gold"]["tipo_documento"], expected))

    assert offenders == [], f"{len(offenders)} etiquetas contradicen su propia página"


def test_the_real_gold_has_no_unanswerable_convenio_procedencia():
    fixer = load_fixer()
    rows = [json.loads(line) for line in GOLD.read_text(encoding="utf-8").splitlines() if line.strip()]

    offenders = [
        row["id"]
        for row in rows
        if fixer.procedencia_por_titulo(fixer.titulo(row))
        and row["gold"]["procedencia"] != fixer.procedencia_por_titulo(fixer.titulo(row))
    ]

    assert offenders == []


@pytest.mark.parametrize("tipo", ["ficha_compra_agil", "orden_compra", "resolucion_adjudicacion"])
def test_every_gold_type_is_a_valid_enum_value(tipo):
    rows = [json.loads(line) for line in GOLD.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert tipo in {row["gold"]["tipo_documento"] for row in rows}
    assert tipo in by_name()["tipo_documento"].choices
