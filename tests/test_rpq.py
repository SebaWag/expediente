"""Regular path queries are the traversal primitive, so they get the hardest tests."""

import pytest

from expediente.graph.store import InMemoryGraphStore
from expediente.navigate.rpq import PATTERNS, PatternError, endpoints, match, parse

PROV_A = ("Proveedor", "11111111-1")
PROV_B = ("Proveedor", "22222222-2")
PROV_C = ("Proveedor", "33333333-3")
PERSONA = ("Persona", "99999999-9")
OC = ("OrdenCompra", "OC-1")
ORGANISMO = ("Organismo", "69.260.000-1")


@pytest.fixture()
def graph() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    for node in (PROV_A, PROV_B, PROV_C):
        store.upsert_node(node[0], node[1])
    store.upsert_node("Persona", PERSONA[1])
    store.upsert_node("OrdenCompra", OC[1])
    store.upsert_node("Organismo", ORGANISMO[1])
    # una persona representa a A y B; C queda fuera
    store.upsert_edge(PERSONA, "REPRESENTA_A", PROV_A, documento_id="sha256:registro", pagina=12)
    store.upsert_edge(PERSONA, "REPRESENTA_A", PROV_B, documento_id="sha256:registro", pagina=27)
    # B le vende al organismo
    store.upsert_edge(ORGANISMO, "EMITE", OC)
    store.upsert_edge(OC, "ADJUDICADA_A", PROV_B)
    return store


def test_parser_handles_inverse_concatenation_and_alternation():
    assert parse("A^/B") is not None
    assert parse("A|B/C") is not None
    assert parse("(A/B)+") is not None


def test_parser_rejects_garbage():
    for bad in ("A//B", "A/", "(", "A^/", "@"):
        with pytest.raises(PatternError):
            parse(bad)


def test_shared_representative_pattern_finds_the_sibling(graph):
    routes = match(graph, "comparte_persona", PROV_A)

    assert endpoints(routes) == [PROV_B]
    assert routes[0].nodes == (PROV_A, PERSONA, PROV_B)


def test_hidden_network_pattern_reaches_the_buyer_through_a_second_supplier(graph):
    routes = match(graph, "red_oculta", PROV_A)

    ends = endpoints(routes)
    assert ends == [ORGANISMO]
    assert routes[0].nodes == (PROV_A, PERSONA, PROV_B, OC, ORGANISMO)


def test_an_unrelated_supplier_produces_no_route(graph):
    assert match(graph, "comparte_persona", PROV_C) == []


def test_alternation_unions_both_branches(graph):
    routes = match(graph, "REPRESENTA_A^|TIENE_DOMICILIO^", PROV_A)
    assert endpoints(routes) == [PERSONA]


def test_inverse_marks_the_hop_as_reversed(graph):
    routes = match(graph, "REPRESENTA_A^", PROV_A)
    assert routes[0].hops[0].reversed is True
    assert routes[0].hops[0].target == PERSONA


def test_optional_matches_zero_or_one_hop(graph):
    zero_or_one = match(graph, "REPRESENTA_A^?", PROV_C)
    assert endpoints(zero_or_one) == [PROV_C]


def test_repetition_is_bounded_to_avoid_looping_forever(graph):
    routes = match(graph, "(REPRESENTA_A^/REPRESENTA_A)*", PROV_A, max_repetition=2)
    assert all(route.length <= 4 for route in routes)


def test_routes_are_cycle_free(graph):
    for route in match(graph, "(REPRESENTA_A^/REPRESENTA_A)*", PROV_A, max_repetition=3):
        assert len(set(route.nodes)) == len(route.nodes)


def test_each_hop_carries_its_own_citation(graph):
    """The two hops cite *different* pages: that is the point of edge provenance.

    If provenance lived only on the document node, a route of four hops would carry
    one citation pretending to support all four. Here the representation of supplier
    A is on page 12 and the one of supplier B is on page 27, because those are two
    different statements in the same registry file.
    """
    routes = match(graph, "comparte_persona", PROV_A)
    hops = routes[0].hops

    assert hops[0].props["documento_id"] == "sha256:registro"
    assert hops[0].props["pagina"] == 12
    assert hops[1].props["pagina"] == 27
    assert hops[0].props["pagina"] != hops[1].props["pagina"]


def test_results_are_deterministic(graph):
    first = [route.to_dict() for route in match(graph, "red_oculta", PROV_A)]
    second = [route.to_dict() for route in match(graph, "red_oculta", PROV_A)]
    assert first == second


def test_named_patterns_are_all_parseable():
    for name, expression in PATTERNS.items():
        parse(expression), name
