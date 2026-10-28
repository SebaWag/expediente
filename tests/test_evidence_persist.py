"""Writing evidence to the graph is what makes a citation walkable.

The point of these tests is not that nodes were created. It is that
``citation_trails`` — the call an auditor makes when they ask "show me where this
claim came from" — returns a path, and that it returns nothing when the citation is
not real.
"""

from expediente.evidence.models import Claim, Evidence
from expediente.evidence.pdf import locate, parse_pdf
from expediente.evidence.persist import persist_evidence
from expediente.graph.store import InMemoryGraphStore
from expediente.navigate.paths import citation_trails

from test_evidence_pdf import build_pdf


PAGES = [
    "RESOLUCION EXENTA 4412 ORDEN DE COMPRA 1000813-396-AG26",
    "Proveedor: COMERCIAL DEL SUR LIMITADA RUT 76543210-K",
    "Monto total 940.000 pesos. Autoriza la jefatura de abastecimiento.",
]


def parsed_pdf(tmp_path, pages=None):
    path = tmp_path / "oc.pdf"
    path.write_bytes(build_pdf(pages or PAGES))
    return parse_pdf(path)


def setup(tmp_path):
    parsed = parsed_pdf(tmp_path)
    store = InMemoryGraphStore()
    found = locate(parsed, "1000813-396-AG26")
    return parsed, store, found


# --------------------------------------------------------------------------- #
# The nodes and edges the auditor's question needs
# --------------------------------------------------------------------------- #


def test_persist_writes_document_evidence_and_the_en_edge(tmp_path):
    parsed, store, found = setup(tmp_path)

    result = persist_evidence(
        store, parsed, found, claims=[Claim(id="AF-1", texto="La OC existe",
                                            evidence_ids=(found[0].id,))]
    )

    assert result.document == ("Documento", parsed.document.id)
    assert result.evidence == (("Evidencia", found[0].id),)
    assert store.node("Documento", parsed.document.id)["paginas"] == 3
    assert store.node("Evidencia", found[0].id)["pagina"] == 1
    assert len(store.edges("EN", src=("Evidencia", found[0].id))) == 1
    assert len(store.edges("SOPORTADA_POR", src=("Afirmacion", "AF-1"))) == 1


def test_citation_trail_walks_from_the_claim_to_the_document(tmp_path):
    """The whole point: the question 'where did this come from' now has an answer."""
    parsed, store, found = setup(tmp_path)
    persist_evidence(
        store, parsed, found,
        claims=[Claim(id="AF-1", texto="La OC existe", evidence_ids=(found[0].id,))],
    )

    trails = citation_trails(store, ("Afirmacion", "AF-1"))

    assert len(trails) == 1
    assert trails[0].nodes == (
        ("Afirmacion", "AF-1"),
        ("Evidencia", found[0].id),
        ("Documento", parsed.document.id),
    )
    assert [hop.relation for hop in trails[0].hops] == ["SOPORTADA_POR", "EN"]


def test_the_dossier_can_reach_the_document_through_the_expediente(tmp_path):
    parsed, store, found = setup(tmp_path)
    persist_evidence(
        store, parsed, found,
        claims=[Claim(id="AF-1", texto="La OC existe", evidence_ids=(found[0].id,))],
        expediente={"id": "EXP-1", "titulo": "Compras 2026", "estado": "borrador"},
    )

    holder = store.node("Expediente", "EXP-1")
    assert holder["estado"] == "borrador"
    assert len(store.edges("CONTIENE", src=("Expediente", "EXP-1"))) == 1


def test_persist_is_idempotent(tmp_path):
    """Re-ingesting the same document must change nothing: the hash is the key."""
    parsed, store, found = setup(tmp_path)
    claim = Claim(id="AF-1", texto="La OC existe", evidence_ids=(found[0].id,))

    first = persist_evidence(store, parsed, found, claims=[claim])
    nodes_before = len(store.nodes("Evidencia")), len(store.nodes("Afirmacion"))
    second = persist_evidence(store, parsed, found, claims=[claim])

    assert first.evidence == second.evidence
    assert (len(store.nodes("Evidencia")), len(store.nodes("Afirmacion"))) == nodes_before
    assert len(store.edges("EN")) == 1
    assert len(store.edges("SOPORTADA_POR")) == 1


# --------------------------------------------------------------------------- #
# What it refuses to write
# --------------------------------------------------------------------------- #


def test_evidence_without_a_page_is_skipped(tmp_path):
    parsed, store, _found = setup(tmp_path)
    pageless = Evidence(id="ev_sin_pagina", document_id=parsed.document.id,
                        pagina=None, extracto="algo")

    result = persist_evidence(store, parsed, [pageless])

    assert result.evidence == ()
    assert store.nodes("Evidencia") == []
    assert result.skipped[0][1] == "sin pagina: no es una cita"


def test_evidence_that_does_not_verify_is_skipped(tmp_path):
    """A real extract filed under the wrong page never reaches the graph."""
    parsed, store, found = setup(tmp_path)
    lie = Evidence(id=found[0].id, document_id=found[0].document_id,
                   pagina=3, extracto=found[0].extracto)

    result = persist_evidence(store, parsed, [lie])

    assert result.evidence == ()
    assert store.nodes("Evidencia") == []
    assert "EXTRACT_NOT_ON_PAGE" in result.skipped[0][1]


def test_a_fabricated_extract_is_skipped(tmp_path):
    parsed, store, _found = setup(tmp_path)
    invented = Evidence(id="ev_inventada", document_id=parsed.document.id,
                        pagina=1, extracto="El proveedor renuncio a todo pago")

    result = persist_evidence(store, parsed, [invented])

    assert result.evidence == ()
    assert "EXTRACT_NOT_ON_PAGE" in result.skipped[0][1]


def test_a_claim_with_no_surviving_citation_is_not_written(tmp_path):
    parsed, store, found = setup(tmp_path)

    result = persist_evidence(
        store, parsed, found,
        claims=[Claim(id="AF-1", texto="Cita algo que no existe",
                      evidence_ids=("ev_inexistente",))],
    )

    assert result.claims == ()
    assert store.nodes("Afirmacion") == []
    assert result.skipped[0][0] == "afirmacion AF-1"


# --------------------------------------------------------------------------- #
# Accreditations: the page on the edge is found, not supplied
# --------------------------------------------------------------------------- #


def test_an_order_is_accredited_only_when_the_document_mentions_it(tmp_path):
    """The order key is the Mercado Público code: the same string the PDF prints."""
    parsed, store, found = setup(tmp_path)
    store.upsert_node("OrdenCompra", "1000813-396-AG26")
    store.upsert_node("OrdenCompra", "9999999-999-AG99")

    result = persist_evidence(
        store, parsed, found, orders=["1000813-396-AG26", "9999999-999-AG99"]
    )

    assert dict(result.accredited) == {"1000813-396-AG26": 1}

    edges = store.edges("ACREDITA", src=("Documento", parsed.document.id))
    assert len(edges) == 1
    assert edges[0][1] == ("OrdenCompra", "1000813-396-AG26")
    assert edges[0][2]["pagina"] == 1
    assert edges[0][2]["evidencia"] == found[0].id


def test_an_order_the_document_does_not_mention_is_reported(tmp_path):
    parsed, store, found = setup(tmp_path)
    store.upsert_node("OrdenCompra", "9999999-999-AG99")

    result = persist_evidence(store, parsed, found, orders=["9999999-999-AG99"])

    assert result.accredited == ()
    assert result.skipped[0] == (
        "orden 9999999-999-AG99",
        "el documento no menciona esa orden",
    )


def test_an_order_absent_from_the_graph_is_reported(tmp_path):
    parsed, store, found = setup(tmp_path)

    result = persist_evidence(store, parsed, found, orders=["1000813-396-AG26"])

    assert result.accredited == ()
    assert result.skipped[0][1] == "la orden no existe en el grafo"


def test_accreditation_survives_a_line_break(tmp_path):
    """The order code is wrapped by the extractor, as it is in the real bulk."""
    parsed = parsed_pdf(tmp_path, ["ORDEN DE COMPRA 1000813-\n396-AG26 aprobada"])
    store = InMemoryGraphStore()
    store.upsert_node("OrdenCompra", "1000813-396-AG26")

    result = persist_evidence(store, parsed, (), orders=["1000813-396-AG26"])

    assert result.accredited == (("1000813-396-AG26", 1),)


def test_matching_does_not_cross_a_page_boundary(tmp_path):
    """A citation is one page. A code split across two of them is two incomplete pages.

    Joining pages to find it would produce an extract that belongs to neither page,
    so the honest outcome is a miss — and the order stays unaccredited.
    """
    parsed = parsed_pdf(tmp_path, ["ORDEN DE COMPRA 1000813-", "396-AG26 aprobada"])
    store = InMemoryGraphStore()
    store.upsert_node("OrdenCompra", "1000813-396-AG26")

    result = persist_evidence(store, parsed, (), orders=["1000813-396-AG26"])

    assert result.accredited == ()
    assert result.skipped[0][1] == "el documento no menciona esa orden"
