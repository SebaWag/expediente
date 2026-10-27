"""MillenniumDB adapter.

Two kinds of test:

* pure unit tests, always run — URI derivation, term serialisation, and the
  compilation of a route expression into a SPARQL property path;
* integration tests, skipped unless ``EXPEDIENTE_MDB_URL`` points at an isolated
  instance. The important one is the **differential** test: the same pattern
  evaluated by the in-memory adapter and by the engine must return the same
  destinations. If they disagree, one of the two implementations is wrong.
"""

import os

import pytest

from expediente.graph.millenniumdb import (
    REL_PREFIX,
    _object_term,
    edge_uri,
    node_uri,
    relation_uri,
)
from expediente.graph.sparql import SparqlClient, SparqlError
from expediente.navigate.rpq import PATTERNS, endpoints, match, parse, to_property_path

MDB_URL = os.environ.get("EXPEDIENTE_MDB_URL", "").strip()
needs_mdb = pytest.mark.skipif(not MDB_URL, reason="EXPEDIENTE_MDB_URL is not set")

PROV_A = ("Proveedor", "it-11111111-1")
PROV_B = ("Proveedor", "it-22222222-2")
PROV_C = ("Proveedor", "it-33333333-3")
PERSONA = ("Persona", "it-99999999-9")
OC = ("OrdenCompra", "it-OC-1")
ORGANISMO = ("Organismo", "it-69.260.000-1")


# ---------------------------------------------------------------- unit tests
def test_node_uri_is_canonical_and_quoted():
    assert node_uri("Proveedor", "11111111-1") == "urn:expediente:node/Proveedor/11111111-1"
    assert node_uri("Domicilio", "av bernardo o higgins 1234|renca") == (
        "urn:expediente:node/Domicilio/av%20bernardo%20o%20higgins%201234%7Crenca"
    )


def test_edge_uri_is_derived_not_generated():
    first = edge_uri("REPRESENTA_A", PROV_A, PROV_B)
    second = edge_uri("REPRESENTA_A", PROV_A, PROV_B)
    assert first == second, "re-ingesting an edge must produce the same annotation id"
    assert first != edge_uri("REPRESENTA_A", PROV_B, PROV_A), "direction is part of identity"


def test_relation_uri_uses_its_own_namespace():
    # Asserted against the constant, not against a literal: a literal here is what let
    # the adapter and the bulk exporter drift into two vocabularies.
    assert relation_uri("REPRESENTA_A") == f"{REL_PREFIX}REPRESENTA_A"
    assert REL_PREFIX.endswith("rel/")


def test_object_term_serialises_types():
    assert _object_term(12) == '"12"^^<http://www.w3.org/2001/XMLSchema#integer>'
    assert _object_term(True) == '"true"^^<http://www.w3.org/2001/XMLSchema#boolean>'
    assert _object_term('con "comillas"').count('\\"') == 2


def test_property_path_compilation_matches_the_expression():
    path = to_property_path(parse("REPRESENTA_A^/REPRESENTA_A/ADJUDICADA_A^/EMITE^"))
    assert path == "^<REPRESENTA_A>/<REPRESENTA_A>/^<ADJUDICADA_A>/^<EMITE>"


def test_property_path_supports_alternation_and_repetition():
    assert to_property_path(parse("A|B")) == "(<A>|<B>)"
    assert to_property_path(parse("A+")) == "(<A>)+"
    assert to_property_path(parse("A?")) == "(<A>)?"


def test_bounded_repetition_is_refused_rather_than_miscompiled():
    """SPARQL property paths have no {m,n}; compiling it silently would change meaning."""
    from expediente.navigate.rpq import PatternError, Repeat

    bounded = Repeat(parse("A"), minimum=2, maximum=2)
    with pytest.raises(PatternError):
        to_property_path(bounded)


def test_writes_to_production_memory_are_refused():
    client = SparqlClient("https://graph.wagnersolutionsai.com")
    with pytest.raises(SparqlError):
        client.update("INSERT DATA { <urn:x> <urn:y> <urn:z> }")


# ---------------------------------------------------------------- integration
@pytest.fixture()
def store():
    from expediente.graph.millenniumdb import MillenniumDBStore

    instance = MillenniumDBStore(SparqlClient(MDB_URL))
    for node in (PROV_A, PROV_B, PROV_C):
        instance.upsert_node(node[0], node[1], razon_social=f"Sintético {node[1]}")
    instance.upsert_node("Persona", PERSONA[1], nombre="Ana Pérez")
    instance.upsert_node("OrdenCompra", OC[1], monto_clp=940_000)
    instance.upsert_node("Organismo", ORGANISMO[1], nombre="Municipalidad")
    instance.upsert_edge(PERSONA, "REPRESENTA_A", PROV_A, documento_id="sha256:registro", pagina=12)
    instance.upsert_edge(PERSONA, "REPRESENTA_A", PROV_B, documento_id="sha256:registro", pagina=27)
    instance.upsert_edge(ORGANISMO, "EMITE", OC)
    instance.upsert_edge(OC, "ADJUDICADA_A", PROV_B)
    return instance


@needs_mdb
def test_round_trip_node_and_props(store):
    props = store.node("Proveedor", PROV_C[1])
    assert props["razon_social"] == f"Sintético {PROV_C[1]}"
    assert "Proveedor" in props["types"]


@needs_mdb
def test_upsert_is_idempotent(store):
    before = len(store)
    store.upsert_node("Proveedor", PROV_C[1], razon_social="otro nombre")
    assert len(store) == before
    assert store.node("Proveedor", PROV_C[1])["razon_social"] == "otro nombre"


@needs_mdb
def test_inbound_edges_are_traversable(store):
    incoming = store.incoming("REPRESENTA_A", PROV_A)
    assert [src for src, _dst, _props in incoming] == [PERSONA]


@needs_mdb
def test_adjacency_carries_edge_provenance(store):
    edges = store.adjacent(PROV_B, direction="in")
    hop = next(edge for edge in edges if edge[0] == "REPRESENTA_A")
    _relation, _other, props = hop
    assert props["documento_id"] == "sha256:registro"
    assert props["pagina"] == 27


@needs_mdb
def test_the_engine_matches_the_in_memory_adapter(store):
    """Differential test: two implementations of the same expression, one answer."""
    from expediente.graph.store import InMemoryGraphStore

    memory = InMemoryGraphStore()
    for node in (PROV_A, PROV_B, PROV_C):
        memory.upsert_node(node[0], node[1])
    memory.upsert_node("Persona", PERSONA[1])
    memory.upsert_node("OrdenCompra", OC[1])
    memory.upsert_node("Organismo", ORGANISMO[1])
    memory.upsert_edge(PERSONA, "REPRESENTA_A", PROV_A)
    memory.upsert_edge(PERSONA, "REPRESENTA_A", PROV_B)
    memory.upsert_edge(ORGANISMO, "EMITE", OC)
    memory.upsert_edge(OC, "ADJUDICADA_A", PROV_B)

    for pattern in ("comparte_persona", "red_oculta"):
        in_memory = endpoints(match(memory, pattern, PROV_A))
        in_engine = store.match(pattern, PROV_A)
        assert set(in_engine) == set(in_memory), (
            f"{pattern}: engine returned {in_engine}, adapter returned {in_memory}"
        )


@needs_mdb
def test_the_engine_path_semantics_are_stated_not_hidden(store):
    """Without the filter, SPARQL property paths let the start node come back.

    This is arbitrary-path semantics versus our simple-path semantics. The adapter
    excludes the start node by default; the divergence is documented instead of
    being papered over, because on patterns longer than two hops the filter alone
    is not a proof of simplicity.
    """
    with_cycles = store.match("comparte_persona", PROV_A, simple_paths=False)
    without = store.match("comparte_persona", PROV_A)

    assert PROV_A in with_cycles, "the engine does allow returning to the origin"
    assert PROV_A not in without


@needs_mdb
def test_an_unrelated_node_has_no_native_route(store):
    assert store.match("comparte_persona", PROV_C) == []
