"""Traversal must be bounded, cycle-free and identical on every run."""

import pytest

from expediente.graph.store import InMemoryGraphStore
from expediente.navigate.paths import citation_trails, find_paths, walk
from expediente.navigate.tools import TOOLS, ToolError, execute

PROV_A = ("Proveedor", "11111111-1")
PROV_B = ("Proveedor", "22222222-2")
PERSONA = ("Persona", "99999999-9")
OC = ("OrdenCompra", "OC-1")
ORGANISMO = ("Organismo", "69.260.000-1")


@pytest.fixture()
def network() -> InMemoryGraphStore:
    """Two suppliers, one shared representative, one buyer. The hidden network."""
    store = InMemoryGraphStore()
    for node, props in ((PROV_A, {"razon_social": "Uno SpA"}), (PROV_B, {"razon_social": "Dos Ltda"})):
        store.upsert_node(node[0], node[1], **props)
    store.upsert_node("Persona", PERSONA[1], nombre="Ana Pérez")
    store.upsert_node("OrdenCompra", OC[1], monto_clp=940_000)
    store.upsert_node("Organismo", ORGANISMO[1], nombre="Municipalidad")
    store.upsert_edge(PERSONA, "REPRESENTA_A", PROV_A)
    store.upsert_edge(PERSONA, "REPRESENTA_A", PROV_B)
    store.upsert_edge(ORGANISMO, "EMITE", OC)
    store.upsert_edge(OC, "ADJUDICADA_A", PROV_B)
    return store


def test_walk_reaches_the_second_supplier_through_the_shared_person(network):
    paths = find_paths(network, PROV_A, target_label="Proveedor", max_depth=2, direction="both")

    assert len(paths) == 1
    assert paths[0].end == PROV_B
    assert [hop.relation for hop in paths[0].hops] == ["REPRESENTA_A", "REPRESENTA_A"]
    assert paths[0].nodes == (PROV_A, PERSONA, PROV_B)


def test_walk_never_repeats_a_node(network):
    paths = walk(network, PROV_A, max_depth=4, direction="both")
    for path in paths:
        assert len(set(path.nodes)) == len(path.nodes)


def test_walk_respects_max_depth(network):
    assert all(path.length <= 1 for path in walk(network, PROV_A, max_depth=1, direction="both"))


def test_walk_filters_by_relation(network):
    paths = walk(network, PROV_A, max_depth=3, relations=("EMITE",), direction="both")
    assert paths == []


def test_walk_is_deterministic(network):
    first = [path.to_dict() for path in walk(network, PROV_A, max_depth=3, direction="both")]
    second = [path.to_dict() for path in walk(network, PROV_A, max_depth=3, direction="both")]
    assert first == second


def test_walk_rejects_an_unknown_direction(network):
    with pytest.raises(ValueError):
        walk(network, PROV_A, direction="sideways")


def test_citation_trails_walks_to_the_document():
    store = InMemoryGraphStore()
    store.upsert_node("Afirmacion", "AF-1", texto="Cuatro órdenes bajo el umbral")
    store.upsert_node("Evidencia", "EV-1", pagina=2, extracto="OC 1000813-396-AG26")
    store.upsert_node("Documento", "sha256:abc", url="https://example.org/x.pdf")
    store.upsert_edge(("Afirmacion", "AF-1"), "SOPORTADA_POR", ("Evidencia", "EV-1"))
    store.upsert_edge(("Evidencia", "EV-1"), "EN", ("Documento", "sha256:abc"))

    trails = citation_trails(store, ("Afirmacion", "AF-1"))

    assert len(trails) == 1
    assert trails[0].end == ("Documento", "sha256:abc")


def test_tool_catalogue_is_declared_and_read_only():
    names = {tool["name"] for tool in TOOLS}
    assert names == {"neighbours", "find_paths", "citation_trail"}


def test_execute_neighbours_returns_edges(network):
    payload = execute(network, "neighbours", {"label": "Persona", "key": "99999999-9"})

    assert payload["count"] == 2
    assert {edge["target"]["key"] for edge in payload["edges"]} == {"11111111-1", "22222222-2"}


def test_execute_find_paths_returns_the_route(network):
    """supplier <- person -> other supplier <- award <- order <- buyer: four hops."""
    payload = execute(
        network,
        "find_paths",
        {"label": "Proveedor", "key": "11111111-1", "target_label": "Organismo",
         "max_depth": 4, "direction": "both"},
    )

    assert payload["count"] >= 1
    assert all(path["end"]["label"] == "Organismo" for path in payload["paths"])


def test_the_full_route_to_the_buyer_is_four_hops(network):
    paths = find_paths(network, PROV_A, target_label="Organismo", max_depth=4, direction="both")

    shortest = min(paths, key=lambda item: item.length)
    assert shortest.length == 4
    assert [hop.relation for hop in shortest.hops] == [
        "REPRESENTA_A", "REPRESENTA_A", "ADJUDICADA_A", "EMITE",
    ]
    assert shortest.nodes == (PROV_A, PERSONA, PROV_B, OC, ORGANISMO)


def test_execute_rejects_an_unknown_tool(network):
    with pytest.raises(ToolError):
        execute(network, "drop_table", {})


def test_execute_rejects_an_out_of_range_depth(network):
    with pytest.raises(ToolError):
        execute(network, "find_paths", {"label": "Proveedor", "key": "11111111-1",
                                        "target_label": "Organismo", "max_depth": 99})
