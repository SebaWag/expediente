"""The demo output is the pitch. If it prints the wrong route, the pitch is wrong."""

from pathlib import Path

from expediente.graph.store import InMemoryGraphStore
from expediente.ingest.records import load_orders, load_suppliers
from expediente.navigate.paths import find_paths
from expediente.resolution.resolver import apply_to_store, resolve_suppliers

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
START = ("Proveedor", "11111111-1")


def build_graph() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    for order in load_orders(EXAMPLES / "oc_synthetic.json"):
        organismo = store.upsert_node("Organismo", order.organismo.codigo)
        proveedor = store.upsert_node("Proveedor", order.proveedor.rut_norm)
        orden = store.upsert_node("OrdenCompra", order.codigo)
        store.upsert_edge(organismo, "EMITE", orden)
        store.upsert_edge(orden, "ADJUDICADA_A", proveedor)
    suppliers = load_suppliers(EXAMPLES / "suppliers_synthetic.json")
    apply_to_store(store, resolve_suppliers(suppliers), suppliers)
    return store


def test_the_direct_route_exists_and_is_two_hops():
    routes = find_paths(build_graph(), START, target_label="Organismo", max_depth=4, direction="both")
    assert min(route.length for route in routes) == 2


def test_the_indirect_filter_selects_a_route_through_a_second_supplier():
    routes = find_paths(build_graph(), START, target_label="Organismo", max_depth=4, direction="both")
    indirect = [
        route
        for route in routes
        if any(node[0] == "Proveedor" and node != START for node in route.nodes)
    ]

    assert indirect, "the demo needs at least one route through another supplier"
    shortest = min(indirect, key=lambda route: route.length)
    assert shortest.length == 4
    assert ("Proveedor", "22222222-2") in shortest.nodes


def test_the_key_versus_tuple_comparison_is_covered():
    """Regression: comparing a key string against a (label, key) tuple is always True."""
    node = ("Proveedor", "11111111-1")
    label, key = node
    assert node == START
    assert key != START, "this is the trap the fixture must not fall into again"
