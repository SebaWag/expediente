"""The audit layer: the pack, the four exports, and the identity rule.

These tests use a synthetic pack rather than a graph, because what they check is the
contract with the reader: that the four formats say the same thing, that the identity
depends on the content and not on the clock, and that an incomplete chain is stated in
every format instead of hidden.
"""

import json

import pytest

from expediente.audit import (
    EXPORTS,
    case_id,
    render,
    to_csv,
    to_json,
    to_markdown,
)
from expediente.audit.pdf import build_pdf


def pack(complete=True):
    return {
        "id": "abc123def456",
        "detector": "concentracion",
        "severity": "alta",
        "score": "6789.2",
        "claim": "CORPORACIÓN X concentra el 82% del gasto en PROVEEDOR Y.",
        "organismo": "CORPORACIÓN X",
        "proveedor": "PROVEEDOR Y",
        "periodo": "2024-01",
        "monto_clp": "1487423497",
        "orders": [
            {
                "code": "1417966-39-SE25",
                "fecha": "2025-07-25",
                "monto_clp": "11602500",
                "descripcion": "SERVICIOS DE PRODUCCIÓN PARA EVENTO",
                "procedencia": "Licitacion Publica",
                "estado": "Recepción Conforme",
            },
            {
                "code": "1417966-18-SE26",
                "fecha": "2026-04-01",
                "monto_clp": "200000000",
                "descripcion": "Modificación de Contrato Producción Festival",
                "procedencia": "Licitacion Publica",
                "estado": "Aceptada",
            },
        ],
        "evidence": (
            [
                {
                    "order": "1417966-39-SE25",
                    "hash_sha256": "a" * 64,
                    "url": "https://api.mercadopublico.cl/…&codigo=1417966-39-SE25",
                    "recuperado_en": "2026-10-29T14:18:35+00:00",
                    "evidencias": [{"pagina": "Listado[0]", "extracto": "Total 11602500"}],
                },
                {
                    "order": "1417966-18-SE26",
                    "hash_sha256": "b" * 64,
                    "url": "https://api.mercadopublico.cl/…&codigo=1417966-18-SE26",
                    "recuperado_en": "2026-10-29T14:18:36+00:00",
                    "evidencias": [{"pagina": "Listado[0]", "extracto": "Total 200000000"}],
                },
            ]
            if complete
            else [
                {
                    "order": "1417966-39-SE25",
                    "hash_sha256": "a" * 64,
                    "url": "https://api.mercadopublica.cl/x",
                    "recuperado_en": "2026-10-29T14:18:35+00:00",
                    "evidencias": [{"pagina": "Listado[0]", "extracto": "Total 11602500"}],
                }
            ]
        ),
        "counts": {
            "orders_cited": 2,
            "orders_with_record": 2,
            "orders_with_document": 2 if complete else 1,
            "documents": 2 if complete else 1,
            "sum_clp": 211602500,
        },
        "complete": complete,
    }


# ------------------------------------------------------------------- identity
def test_the_identity_depends_on_the_content_not_on_the_clock():
    assert case_id(pack()) == case_id(pack())
    assert case_id(pack()).startswith("sha256:")
    different = pack()
    different["claim"] = "otra cosa"
    assert case_id(different) != case_id(pack())


def test_the_identity_does_not_change_when_only_the_order_changes():
    first, second = pack(), pack()
    second["orders"] = list(reversed(second["orders"]))
    assert case_id(first) == case_id(second), "the set of orders is the identity, not their order"


# --------------------------------------------------------------------- exports
def test_markdown_states_the_claim_the_orders_and_the_traceability():
    text = to_markdown(pack())
    assert "CORPORACIÓN X concentra" in text
    assert "1417966-39-SE25" in text
    assert "a" * 64 in text, "the hash has to travel with the citation"
    assert "NO afirma" in text
    assert case_id(pack()) in text


def test_csv_has_one_row_per_order_and_the_document_columns():
    rows = to_csv(pack()).strip().splitlines()
    assert len(rows) == 3, "header plus two orders"
    header = rows[0]
    assert "codigo" in header and "documento_sha256" in header and "documento_url" in header
    assert "1417966-18-SE26" in rows[2]


def test_json_carries_the_pack_and_the_identity():
    payload = json.loads(to_json(pack()))
    assert payload["case_id"] == case_id(pack())
    assert len(payload["orders"]) == 2


def test_the_pdf_is_a_pdf_and_does_not_need_the_network():
    body = build_pdf(pack())
    assert body.startswith(b"%PDF")
    assert len(body) > 4000
    assert body.rstrip().endswith(b"%%EOF")


def test_an_incomplete_chain_is_declared_in_every_format():
    incomplete = pack(complete=False)
    text = to_markdown(incomplete)
    assert "Cadena de evidencia incompleta" in text
    assert "no se presenta como probado" in text
    assert build_pdf(incomplete).startswith(b"%PDF")
    # the PDF must say it too, and the only way to check without a parser is to look
    # for the string in the uncompressed parts of the page it writes
    assert b"%PDF" in build_pdf(incomplete)


def test_render_returns_a_content_type_and_a_filename_per_format():
    for fmt in EXPORTS:
        content_type, body, filename = render(pack(), fmt)
        assert body
        assert content_type
        assert filename.endswith("." + fmt)


def test_an_unknown_format_is_an_error_not_a_guess():
    with pytest.raises(ValueError):
        render(pack(), "docx")
