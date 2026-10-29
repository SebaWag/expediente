"""The portal: the subgraph view, the board, and the HTTP surface.

The board is the part worth testing carefully. It is a claim about what has been done
to each signal, and it is derived from the run log rather than stored — so a test that
injects a log and asserts the columns is a test of the actual mechanism.
"""

import json
import threading
import urllib.request

import pytest

from expediente.agent import ModelReply, ScriptedProvider, Settings, tool_call
from expediente.graph.store import InMemoryGraphStore
from expediente.portal.server import Portal, RunRegistry, build_server, kanban, subgraph
from expediente.runs import RunStore


# --------------------------------------------------------------------- fixtures
def make_store() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    store.upsert_node("Organismo", "69.260.000-1", nombre="Municipalidad de Renca")
    store.upsert_node("Proveedor", "76.264.138-0", razon_social="CARDIOTEC VASCULAR LIMITADA")
    store.upsert_node("Proveedor", "77.290.194-1", razon_social="Cardiotec Vascularis SpA")
    store.upsert_node("Domicilio", "geo-1", direccion="VALLE DE CASABLANCA NORTE 8219")
    for index in range(3):
        store.upsert_node("OrdenCompra", f"OC-{index}", monto_clp=900_000, fecha="2026-08-01")
        store.upsert_edge(("Organismo", "69.260.000-1"), "EMITE", ("OrdenCompra", f"OC-{index}"))
        store.upsert_edge(("OrdenCompra", f"OC-{index}"), "ADJUDICADA_A", ("Proveedor", "76.264.138-0"))
    store.upsert_edge(("Proveedor", "76.264.138-0"), "TIENE_DOMICILIO", ("Domicilio", "geo-1"))
    store.upsert_edge(("Proveedor", "77.290.194-1"), "TIENE_DOMICILIO", ("Domicilio", "geo-1"))
    return store


class FakeApi:
    """Stands in for ``Api`` so the board can be tested without a graph engine."""

    def __init__(self, signals):
        self._signals = signals

    def signals(self, params):
        return {"count": len(self._signals), "signals": self._signals}

    def stats(self, params):
        return {"nodes": {"OrdenCompra": 3, "Proveedor": 2}, "relations": {}}


def signals():
    return [
        {"id": "sig-1", "detector": "fraccionamiento", "score": 900, "severity": "alta",
         "organismo": "Municipalidad de Renca", "proveedor": "CARDIOTEC VASCULAR LIMITADA",
         "monto_clp": 2_700_000, "ordenes": 3, "periodo": "2026-08", "clase": "mismo_objeto"},
        {"id": "sig-2", "detector": "concentracion", "score": 800, "severity": "alta",
         "organismo": "Municipalidad de Renca", "proveedor": "OTRA", "monto_clp": 1, "ordenes": 1,
         "periodo": None, "clase": None},
    ]


def seed_run(runs: RunStore, *, accept=None, reject=None) -> str:
    run = runs.create(question="¿Qué pasa con Renca?", subject=None)
    if accept:
        run.events.append("claim.accepted", accept)
    if reject:
        run.events.append("claim.rejected", reject)
    run.events.append("run.finished", {})
    return run.id


# -------------------------------------------------------------------- subgraph
def test_the_subgraph_is_the_neighbourhood_and_not_a_sample_of_the_whole():
    view = subgraph(make_store(), "Proveedor", "76.264.138-0", depth=1)
    ids = {node["id"] for node in view["nodes"]}
    assert "Proveedor/76.264.138-0" in ids
    assert "Domicilio/geo-1" in ids, "the shared address must be reachable from the supplier"
    assert "OrdenCompra/OC-0" in ids
    assert view["truncated"] is False


def test_the_subgraph_respects_the_node_cap_and_says_that_it_did():
    view = subgraph(make_store(), "Organismo", "69.260.000-1", depth=2, max_nodes=4)
    assert len(view["nodes"]) <= 4
    assert view["truncated"] is True, "a clipped drawing must admit that it was clipped"


def test_a_node_that_does_not_exist_is_an_error_and_not_an_empty_canvas():
    view = subgraph(make_store(), "Proveedor", "no-existe")
    assert view["nodes"] == []
    assert "no existe" in view["error"]


def test_names_are_human_not_identifiers():
    view = subgraph(make_store(), "Proveedor", "76.264.138-0", depth=1)
    names = {node["id"]: node["name"] for node in view["nodes"]}
    assert names["Proveedor/76.264.138-0"] == "CARDIOTEC VASCULAR LIMITADA"
    assert names["Domicilio/geo-1"] == "VALLE DE CASABLANCA NORTE 8219"


# ---------------------------------------------------------------------- kanban
def test_a_signal_nobody_touched_is_reported_as_unreviewed(tmp_path):
    runs = RunStore(tmp_path / "runs")
    board = kanban(FakeApi(signals()), runs, {})
    states = {card["id"]: card["state"] for column in board["columns"] for card in column["cards"]}
    assert states == {"sig-1": "sin_revisar", "sig-2": "sin_revisar"}


def test_a_signal_moves_only_when_a_recording_says_so(tmp_path):
    runs = RunStore(tmp_path / "runs")
    run_id = seed_run(runs, accept={"texto": "tres órdenes al mismo proveedor", "senales": ["sig-1"], "origen": "grafo"})
    board = kanban(FakeApi(signals()), runs, {})
    states = {card["id"]: card for column in board["columns"] for card in column["cards"]}
    assert states["sig-1"]["state"] == "con_evidencia"
    assert states["sig-1"]["run"] == run_id
    assert states["sig-2"]["state"] == "sin_revisar"


def test_a_rejected_claim_lands_in_discarded_with_its_reason(tmp_path):
    runs = RunStore(tmp_path / "runs")
    seed_run(runs, reject={"texto": "parece fraude", "reason": "sin documento", "senales": ["sig-2"]})
    board = kanban(FakeApi(signals()), runs, {})
    card = next(card for column in board["columns"] for card in column["cards"] if card["id"] == "sig-2")
    assert card["state"] == "descartada"
    assert card["note"] == "sin documento"


def test_a_run_in_flight_marks_its_subject_as_being_analysed(tmp_path):
    runs = RunStore(tmp_path / "runs")
    board = kanban(FakeApi(signals()), runs, {"run-x": {"question": "revisando sig-2", "state": "corriendo"}})
    assert board["active_runs"] == 1


def test_the_board_has_all_four_columns_even_when_empty(tmp_path):
    board = kanban(FakeApi([]), RunStore(tmp_path / "runs"), {})
    assert [column["state"] for column in board["columns"]] == [
        "sin_revisar", "en_analisis", "con_evidencia", "descartada"
    ]


# ------------------------------------------------------------------------ HTTP
@pytest.fixture()
def live_server(tmp_path):
    store = make_store()
    runs = RunStore(tmp_path / "runs")

    def provider_factory():
        return ScriptedProvider([
            ModelReply(text="Buscar al proveedor\nRevisar sus órdenes", tool_calls=()),
            ModelReply(tool_calls=(tool_call("afirmar", {"texto": "tres órdenes al mismo proveedor", "ordenes": ["OC-0", "OC-1", "OC-2"]}),), text=""),
            ModelReply(tool_calls=(tool_call("terminar", {"resumen": "tres órdenes"}),), text=""),
        ])

    server = build_server(
        store,
        runs,
        Settings(max_steps=4),
        "127.0.0.1",
        0,
        provider_factory=provider_factory,
        api=FakeApi(signals()),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    yield f"http://127.0.0.1:{port}", runs
    server.shutdown()
    server.server_close()


def get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def post(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def test_the_portal_serves_the_page_and_the_assets(live_server):
    base, _runs = live_server
    with urllib.request.urlopen(base + "/", timeout=20) as response:
        html = response.read().decode("utf-8")
    assert "Expediente" in html
    assert "/static/app.js" in html
    with urllib.request.urlopen(base + "/static/styles.css", timeout=20) as response:
        assert response.status == 200


def test_static_serving_cannot_walk_out_of_its_directory(live_server):
    base, _runs = live_server
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(base + "/static/../../etc/passwd", timeout=20)
    assert error.value.code == 404


def test_the_portal_exposes_the_subgraph_and_the_board(live_server):
    base, _runs = live_server
    view = get(base + "/api/subgraph?nodo=Proveedor/76.264.138-0&saltos=1")
    assert any(node["id"] == "Domicilio/geo-1" for node in view["nodes"])
    board = get(base + "/api/kanban")
    assert len(board["columns"]) == 4


def test_a_question_runs_end_to_end_and_the_log_is_readable(live_server):
    import time

    base, runs = live_server
    started = post(base + "/api/investigate", {"question": "¿Qué compró Renca a Cardiotec?"})
    run_id = started["run_id"]
    assert run_id

    for _ in range(60):
        detail = get(base + f"/api/runs/{run_id}")
        if (detail["state"].get("finished") or detail["state"].get("failed")) and detail["events"]:
            break
        time.sleep(0.1)

    detail = get(base + f"/api/runs/{run_id}")
    types = [event["type"] for event in detail["events"]]
    assert "agent.plan" in types
    assert "claim.accepted" in types
    assert "dossier.built" in types
    assert detail["dossier"] and "tres órdenes al mismo proveedor" in detail["dossier"]
    assert detail["digest"]

    events = get(base + f"/api/runs/{run_id}/events?since=2")
    assert all(event["seq"] > 2 for event in events["events"])


def test_a_too_short_question_is_refused_with_a_reason(live_server):
    base, _runs = live_server
    with pytest.raises(urllib.error.HTTPError) as error:
        post(base + "/api/investigate", {"question": "hola"})
    assert error.value.code == 400


def test_an_unknown_route_is_json_not_an_html_error_page(live_server):
    base, _runs = live_server
    with pytest.raises(urllib.error.HTTPError) as error:
        get(base + "/api/nada")
    assert error.value.code == 404
    assert json.loads(error.value.read().decode("utf-8"))["error"] == "ruta desconocida"
