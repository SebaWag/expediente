"""The MCP server: protocol surface, and the tools against a real graph.

The tools are tested against the in-memory store on purpose. If a tool only worked
against MillenniumDB, the demo would require a database just to answer "does the
protocol work", and a client would discover the difference in production.
"""

import io
import json

import pytest

from expediente.graph.store import InMemoryGraphStore
from expediente.mcp.server import HANDLERS, handle, neighbours, find_paths, search, citation_trail, serve

ORGANISMO = ("Organismo", "61.603.000-0")
PROV_A = ("Proveedor", "89.862.200-2")
PROV_B = ("Proveedor", "96.905.520-9")
OC_1 = ("OrdenCompra", "1563-100-CM21")
OC_2 = ("OrdenCompra", "1563-101-CM21")


@pytest.fixture()
def store() -> InMemoryGraphStore:
    graph = InMemoryGraphStore()
    graph.upsert_node(*ORGANISMO, nombre="FONDO NACIONAL DE SALUD", rut_norm="61.603.000-0")
    graph.upsert_node(*PROV_A, razon_social="PROVEEDOR UNO LTDA", rut_norm="89.862.200-2")
    graph.upsert_node(*PROV_B, razon_social="PROVEEDOR DOS LTDA", rut_norm="96.905.520-9")
    graph.upsert_node(*OC_1, monto_clp=430316)
    graph.upsert_node(*OC_2, monto_clp=120000)
    graph.upsert_edge(ORGANISMO, "EMITE", OC_1)
    graph.upsert_edge(ORGANISMO, "EMITE", OC_2)
    graph.upsert_edge(OC_1, "ADJUDICADA_A", PROV_A)
    graph.upsert_edge(OC_2, "ADJUDICADA_A", PROV_B)
    return graph


# ------------------------------------------------------------------ protocol
def test_initialize_advertises_the_tools_capability(store):
    response = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}, store)
    assert response["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert response["result"]["protocolVersion"]


def test_tools_list_declares_every_handler(store):
    response = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, store)
    names = {tool["name"] for tool in response["result"]["tools"]}
    assert names == set(HANDLERS)
    assert names == {"neighbours", "find_paths", "search", "citation_trail", "network"}
    for tool in response["result"]["tools"]:
        assert tool["inputSchema"]["type"] == "object"


def test_unknown_method_is_an_error_not_silence(store):
    response = handle({"jsonrpc": "2.0", "id": 3, "method": "does/not/exist"}, store)
    assert response["error"]["code"] == -32601


def test_notifications_get_no_reply(store):
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, store) is None


def test_serve_answers_one_json_object_per_line(store):
    request = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "ping"})
    out = io.StringIO()
    serve(store, stdin=io.StringIO(request + "\n\n"), stdout=out)
    lines = [line for line in out.getvalue().splitlines() if line]
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == 7


def test_bad_json_is_a_parse_error_and_does_not_kill_the_server(store):
    out = io.StringIO()
    serve(store, stdin=io.StringIO("{not json}\n" + json.dumps({"jsonrpc": "2.0", "id": 9, "method": "ping"}) + "\n"), stdout=out)
    lines = [json.loads(line) for line in out.getvalue().splitlines() if line]
    assert lines[0]["error"]["code"] == -32700
    assert lines[1]["id"] == 9


# --------------------------------------------------------------------- tools
def test_neighbours_counts_by_relation_and_direction(store):
    result = neighbours(store, {"node": "Organismo/61.603.000-0"})
    assert result["found"] is True
    assert result["total"] == 2
    assert result["by_relation"] == [{"relation": "EMITE", "direction": "out", "count": 2}]


def test_neighbours_reports_incoming_edges_as_incoming(store):
    result = neighbours(store, {"node": "Proveedor/89.862.200-2", "direction": "in"})
    assert result["by_relation"] == [{"relation": "ADJUDICADA_A", "direction": "in", "count": 1}]


def test_neighbours_of_an_unknown_node_says_so(store):
    result = neighbours(store, {"node": "Organismo/no-existe"})
    assert result["found"] is False
    assert result["neighbours"] == []


def test_find_paths_returns_the_hops_with_their_relation(store):
    result = find_paths(store, {"src": "Organismo/61.603.000-0", "dst": "Proveedor/89.862.200-2", "max_hops": 2})
    assert result["paths_found"] == 1
    hops = result["paths"][0]["path"]
    assert [step["relation"] for step in hops] == ["EMITE", "ADJUDICADA_A"]
    assert hops[-1]["to"] == "Proveedor/89.862.200-2"


def test_citation_trail_is_empty_and_says_why(store):
    result = citation_trail(store, {"node": "OrdenCompra/1563-100-CM21"})
    assert result["chain_complete"] is False
    assert "no documents" in result["note"]


def test_limits_are_clamped_not_trusted(store):
    result = neighbours(store, {"node": "Organismo/61.603.000-0", "limit": 10_000})
    assert len(result["neighbours"]) <= 200


def test_search_expands_a_known_acronym(store):
    result = search(store, {"label": "Organismo", "text": "FONASA"})
    assert result["count"] == 1
    assert result["results"][0]["matched_via"] == "sigla:FONASA"
    assert result["expanded_to"] == ["FONDO NACIONAL DE SALUD"]


def test_a_short_acronym_does_not_match_inside_a_word(store):
    """EFE matched "CENTRO DE REFERENCIA" before this rule existed."""
    graph = InMemoryGraphStore()
    graph.upsert_node("Organismo", "x-1", nombre="CENTRO DE REFERENCIA DE SALUD DE MAIPU")
    graph.upsert_node("Organismo", "x-2", nombre="ILUSTRE MUNICIPALIDAD DE SAGRADA FAMILIA")
    assert search(graph, {"label": "Organismo", "text": "EFE"})["count"] == 0
    assert search(graph, {"label": "Organismo", "text": "SAG"})["count"] == 0


def test_a_long_partial_still_matches(store):
    """The whole-word rule is for short needles only; partial names must keep working."""
    assert search(store, {"label": "Proveedor", "text": "AGROSU"})["count"] == 0
    graph = InMemoryGraphStore()
    graph.upsert_node("Proveedor", "p-1", razon_social="AGROSUPER COMERCIALIZADORA DE ALIMENTOS")
    assert search(graph, {"label": "Proveedor", "text": "AGROSU"})["count"] == 1


# ------------------------------------------------------------------ network
def test_network_accepts_any_rut_spelling_and_normalises_it(store):
    """77.290.194-1, 772901941 and 77.290.194-1 must reach the same node key."""
    from expediente.mcp.server import network

    for spelling in ("89.862.200-2", "89862200-2", "898622002"):
        payload = network(store, {"rut": spelling})
        assert payload["rut"] == "89.862.200-2", spelling


def test_network_without_a_check_digit_is_a_value_error(store):
    from expediente.mcp.server import network

    with pytest.raises(ValueError):
        network(store, {"rut": ""})


def test_network_without_a_graph_client_says_so_instead_of_guessing(store):
    """The in-memory store has no SPARQL client; the answer must admit that."""
    from expediente.mcp.server import network

    payload = network(store, {"rut": "89.862.200-2"})
    assert payload["found"] is False
    assert payload.get("note")
    assert payload["peers"] == []
