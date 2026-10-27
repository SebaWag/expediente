"""The pipeline: pattern -> claim -> citation -> dossier, with the gate in the middle."""

from pathlib import Path

from expediente.graph.store import InMemoryGraphStore
from expediente.ingest.records import load_orders
from expediente.investigate import investigate

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
EVIDENCE = [
    {"orden": "OC-3101", "documento_id": "sha256:oc3101", "url": "https://example.org/oc3101.pdf",
     "pagina": 2, "paginas": 12},
    {"orden": "OC-3102", "documento_id": "sha256:oc3101", "url": "https://example.org/oc3101.pdf",
     "pagina": 5, "paginas": 12},
]


def build() -> tuple[InMemoryGraphStore, list]:
    orders = load_orders(EXAMPLES / "oc_investigation.json")
    store = InMemoryGraphStore()
    for order in orders:
        store.upsert_node("Organismo", order.organismo.codigo, nombre=order.organismo.nombre)
        store.upsert_node("Proveedor", order.proveedor.rut_norm, razon_social=order.proveedor.razon_social)
        store.upsert_node("OrdenCompra", order.codigo, monto_clp=order.monto_clp)
        store.upsert_edge(("Organismo", order.organismo.codigo), "EMITE", ("OrdenCompra", order.codigo))
        store.upsert_edge(("OrdenCompra", order.codigo), "ADJUDICADA_A", ("Proveedor", order.proveedor.rut_norm))
    for item in EVIDENCE:
        store.upsert_node("Documento", item["documento_id"], url=item["url"], paginas=item["paginas"])
        store.upsert_edge(("Documento", item["documento_id"]), "ACREDITA",
                          ("OrdenCompra", item["orden"]), pagina=item["pagina"])
    return store, orders


def run(**overrides):
    store, orders = build()
    payload = dict(
        titulo="Expediente de prueba",
        sujeto="Municipalidad Sintética",
        generado_en="2026-10-27",
    )
    payload.update(overrides)
    return investigate(store, orders, **payload)


def test_the_pipeline_finds_both_patterns():
    result = run()
    assert len(result.findings) == 2
    assert result.kept + result.dropped == 2


def test_a_pattern_without_documents_is_dropped_not_asserted():
    """Supplier D has three orders under the ceiling — and no accrediting document."""
    result = run()

    assert result.kept == 1
    assert result.dropped == 1
    assert result.report.dropped[0].reason.value == "NO_EVIDENCE"


def test_the_surviving_claim_cites_the_page_from_the_edge():
    result = run()
    citation = result.dossier.claims[0].citation

    assert citation.documento_id == "sha256:oc3101"
    assert citation.pagina == 2, "the page comes from the ACREDITA annotation, not the document"
    assert citation.url.endswith("oc3101.pdf")


def test_citation_rate_reflects_reality_not_optimism():
    result = run()
    assert result.dossier.citation_rate == 0.5
    assert result.dossier.signable is True


def test_the_route_is_returned_when_a_starting_point_is_given():
    """The full four-hop route: supplier -> shared person -> second supplier -> buyer.

    The second supplier needs its own order, otherwise there is nothing to walk to:
    a route is not a story the code is allowed to fill in.
    """
    store, orders = build()
    store.upsert_node("Persona", "99999999-9", nombre="Ana Pérez")
    store.upsert_node("Proveedor", "22222222-2", razon_social="Proveedor Sintético B Ltda")
    store.upsert_node("OrdenCompra", "OC-9999", monto_clp=800_000)
    store.upsert_edge(("Persona", "99999999-9"), "REPRESENTA_A", ("Proveedor", "11111111-1"))
    store.upsert_edge(("Persona", "99999999-9"), "REPRESENTA_A", ("Proveedor", "22222222-2"))
    store.upsert_edge(("Organismo", "69.260.000-1"), "EMITE", ("OrdenCompra", "OC-9999"))
    store.upsert_edge(("OrdenCompra", "OC-9999"), "ADJUDICADA_A", ("Proveedor", "22222222-2"))

    result = investigate(
        store, orders,
        titulo="t", sujeto="s", generado_en="2026-10-27",
        route_from=("Proveedor", "11111111-1"),
    )

    assert result.routes, "the route through the shared person must be found"
    shortest = min(result.routes, key=lambda route: route.length)
    assert shortest.length == 4
    assert shortest.end[0] == "Organismo"


def test_the_investigation_is_reproducible():
    assert run().dossier.id == run().dossier.id
