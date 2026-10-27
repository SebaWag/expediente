"""The evidence invariant is the product, so it gets a test of its own."""

import pytest

from expediente.graph.schema import EDGES, NODES, EVIDENCE_INVARIANT
from expediente.graph.store import InMemoryGraphStore


def build_evidence_chain(store: InMemoryGraphStore, *, with_document: bool) -> tuple:
    assertion = store.upsert_node("Afirmacion", "AF-1", texto="Siete órdenes bajo el umbral")
    evidence = store.upsert_node("Evidencia", "EV-1", pagina=42, extracto="OC 1000813-396-AG26")
    store.upsert_edge(assertion, "SOPORTADA_POR", evidence)
    if with_document:
        document = store.upsert_node("Documento", "sha256:abc", tipo="pdf", url="https://example.org/x.pdf")
        store.upsert_edge(evidence, "EN", document)
    return assertion, evidence


def test_chain_reaches_a_document_when_one_exists():
    store = InMemoryGraphStore()
    assertion, _ = build_evidence_chain(store, with_document=True)

    evidence_edges = store.edges("SOPORTADA_POR", src=assertion)
    assert len(evidence_edges) == 1
    _, evidence_key, _ = evidence_edges[0]
    assert store.edges("EN", src=evidence_key), "evidence must point at a document"


def test_chain_without_a_document_fails_the_invariant():
    store = InMemoryGraphStore()
    assertion, _ = build_evidence_chain(store, with_document=False)

    _, evidence_key, _ = store.edges("SOPORTADA_POR", src=assertion)[0]
    assert store.edges("EN", src=evidence_key) == []


def test_upsert_is_idempotent():
    store = InMemoryGraphStore()
    store.upsert_node("Proveedor", "11111111-1", razon_social="A SpA")
    store.upsert_node("Proveedor", "11111111-1", razon_social="A SpA", giro="Comercio")
    assert len(store) == 1
    assert store.node("Proveedor", "11111111-1")["giro"] == "Comercio"


def test_edge_to_unknown_node_is_rejected():
    store = InMemoryGraphStore()
    known = store.upsert_node("Organismo", "69.260.000-1", nombre="Municipalidad")
    with pytest.raises(KeyError):
        store.upsert_edge(known, "EMITE", ("OrdenCompra", "OC-404"))


def test_schema_edges_reference_declared_labels():
    for relation, (source, target) in EDGES.items():
        assert source in NODES, f"{relation} starts at undeclared :{source}"
        assert target in NODES, f"{relation} ends at undeclared :{target}"


def test_evidence_invariant_is_documented():
    assert "removed before the dossier is exported" in EVIDENCE_INVARIANT
