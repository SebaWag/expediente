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
from expediente.portal.server import (
    Portal,
    RunRegistry,
    build_server,
    kanban,
    overview_note,
    subgraph,
)
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

    def health(self, params):
        # El tamaño del grafo sale de acá, no de un literal en el código.
        return {"status": "ok", "triples": 86_012_436}


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


# --------------------------------------------------- the canvas script, as a file
def _graph_js() -> "Path":
    from pathlib import Path

    return Path(__file__).resolve().parents[1] / "expediente" / "portal" / "static" / "graph.js"


def test_the_canvas_script_parses():
    """A syntax error here is a blank screen in production, and it has happened twice.

    The whole graph view is one file with no build step, so nothing else would catch it: the
    server serves it happily and the browser fails silently. `node --check` costs a second.
    """
    import shutil
    import subprocess

    node = shutil.which("node")
    if not node:
        pytest.skip("node no está disponible para comprobar la sintaxis")
    subprocess.run([node, "--check", str(_graph_js())], check=True, capture_output=True)


def test_every_label_is_encodable_and_nameable():
    """Colour, shape and a Spanish name for every node label.

    Colour alone fails on a projector and on a colour-blind reader, so each label needs a shape;
    and the legend is what turns `OrdenCompra` back into something an auditor signs. A label that
    exists in one table and not the others renders as an anonymous grey circle, which is exactly
    what a review of the first version flagged.
    """
    import re

    source = _graph_js().read_text(encoding="utf-8")

    def block(name: str) -> str:
        start = source.index(f"var {name} = {{")
        return source[start:source.index("};", start)]

    colors = set(re.findall(r"^\s{4}(\w+):", block("COLORS"), re.MULTILINE))
    shapes = set(re.findall(r"^\s{4}(\w+):", block("SHAPES"), re.MULTILINE))
    names = set(re.findall(r"^\s{4}(\w+):", block("LABELS_ES"), re.MULTILINE))

    assert colors, "no encontré la tabla de colores"
    assert colors == shapes, f"labels sin forma: {sorted(colors - shapes)} · formas huérfanas: {sorted(shapes - colors)}"
    assert colors == names, f"labels sin nombre en la leyenda: {sorted(colors - names)}"


def test_the_landing_quotes_the_graph_and_not_a_literal():
    """La portada decía «64,8 millones de tripletas» con el motor en 86.012.436.

    Un portal que afirma que ninguno de sus números puede envejecer no puede llevar uno
    escrito a mano en la letra chica de la primera pantalla.
    """
    note = overview_note(1_437, 2_313, 565, 86_012_436)

    assert "86.012.436" in note
    assert "64,8" not in note


def test_the_landing_says_nothing_about_size_when_the_count_could_not_be_read():
    """Sin conteo, la frase se omite: mejor callar que decir un número viejo."""
    note = overview_note(1_437, 2_313, 565, 0)

    assert "tripletas" not in note
    assert "1437 nodos" in note


# ------------------------------------------------------- the subject of a question

@pytest.mark.parametrize(
    "question, expected",
    [
        ("¿Por qué la Municipalidad de Pumanque concentra el 82% de su gasto?", "Municipalidad de Pumanque"),
        ("¿Por qué aparecen juntas las dos Cardiotec?", "Cardiotec"),
        ("¿Cuánto gastó la Municipalidad de San Pedro de Atacama en 2024?", "Municipalidad de San Pedro de Atacama"),
        ("¿Quién es José Luis Pavez Vargas?", "José Luis Pavez Vargas"),
        ("Pumanque", "Pumanque"),
        ("¿Por qué son caras las compras?", None),
    ],
)
def test_the_subject_of_a_question_is_its_capitalised_phrase(tmp_path, question, expected):
    """Length is not specificity.

    "Municipalidad" is longer than "Pumanque" and names eight different buyers, which is how
    the command bar once answered a question about Pumanque by flying to an unrelated
    foundation. What marks the subject is the capital letter, and the phrase around it.
    """
    assert make_portal(tmp_path)._subject_phrase(question) == expected


def test_a_question_about_one_buyer_lands_on_that_buyer_and_not_on_the_first_match(tmp_path):
    """The regression that motivated the phrase: the reply said *"te llevo a"* somebody else.

    The subject is "Municipalidad de Renca" and the store holds exactly one of those, next to
    two suppliers and three orders. Before the phrase, "Municipalidad" — the longest token —
    decided the destination.
    """
    answer = make_portal(tmp_path).chat("¿Por qué la Municipalidad de Renca concentra el 82% de su gasto?")

    assert answer["kind"] == "focus"
    assert answer["node"] == "Organismo/69.260.000-1"
    assert answer["name"] == "Municipalidad de Renca"
    assert answer["can_investigate"] is True


def test_a_connection_question_still_returns_the_two_suppliers(tmp_path):
    """The phrase must not swallow the pair: two names joined by "y" are still two names."""
    answer = make_portal(tmp_path).chat("¿Por qué aparecen juntas las dos Cardiotec?")

    assert answer["kind"] == "path"
    assert {answer["desde"], answer["hasta"]} == {"Proveedor/76.264.138-0", "Proveedor/77.290.194-1"}


# -------------------------------------------------------------------------- path
def make_portal(tmp_path) -> Portal:
    store = make_store()
    runs = RunStore(tmp_path / "runs")
    registry = RunRegistry(store, lambda: ScriptedProvider([]), runs, Settings(max_steps=2))
    return Portal(store, FakeApi(signals()), runs, registry)


def test_the_path_between_two_suppliers_is_the_shared_address(tmp_path):
    """"How are these two connected" — the question the graph exists to answer.

    The fixture is the real case: two suppliers with different RUTs and one address. The answer has
    to name the relation on each hop, because "two hops" proves nothing and "TIENE_DOMICILIO twice
    through the same address" is the whole finding.
    """
    portal = make_portal(tmp_path)
    result = portal.path({"desde": "Proveedor/76.264.138-0", "hasta": "Proveedor/77.290.194-1"})

    assert "error" not in result, result
    assert result["saltos"] == 2
    assert result["caminos"], "dos proveedores que comparten domicilio tienen que tener un camino"
    tramos = result["caminos"][0]["tramos"]
    assert [tramo["rel"] for tramo in tramos] == ["TIENE_DOMICILIO", "TIENE_DOMICILIO"]
    assert tramos[0]["de"] == "Proveedor/76.264.138-0"
    assert tramos[-1]["a"] == "Proveedor/77.290.194-1"
    assert "Domicilio/geo-1" in {node["id"] for node in result["nodos"]}


def test_a_path_that_does_not_fit_the_budget_says_so_instead_of_returning_nothing(tmp_path):
    """An empty list would let a reader conclude the two are unrelated. They are not: they are two
    hops apart and we only looked at one."""
    portal = make_portal(tmp_path)
    result = portal.path({"desde": "Proveedor/76.264.138-0", "hasta": "Proveedor/77.290.194-1", "saltos": "1"})

    assert result["caminos"] == []
    assert "no hay camino" in result["motivo"]
    assert result["saltos"] == 1


def test_the_path_can_be_restricted_to_one_kind_of_relation(tmp_path):
    portal = make_portal(tmp_path)
    allowed = portal.path({"desde": "Proveedor/76.264.138-0", "hasta": "Proveedor/77.290.194-1", "rel": "TIENE_DOMICILIO"})
    blocked = portal.path({"desde": "Proveedor/76.264.138-0", "hasta": "Proveedor/77.290.194-1", "rel": "ADJUDICADA_A"})

    assert allowed["caminos"], "el camino existe por el domicilio compartido"
    assert blocked["caminos"] == [], "no hay camino hecho sólo de adjudicaciones"


def test_a_path_needs_two_real_nodes(tmp_path):
    portal = make_portal(tmp_path)
    assert "error" in portal.path({})
    assert "error" in portal.path({"desde": "sin-barra", "hasta": "Proveedor/77.290.194-1"})
    assert "error" in portal.path({"desde": "Proveedor/76.264.138-0", "hasta": "Proveedor/76.264.138-0"})


def test_the_path_is_reachable_over_http_and_not_only_in_a_unit_test(live_server):
    """The route is where the bug was: `portal.path()` worked and `/api/path` raised NameError.

    A handler is a name, a status and a query string; testing the method skips all three.
    """
    base, _runs = live_server
    result = get(
        f"{base}/api/path?desde=Proveedor/76.264.138-0&hasta=Proveedor/77.290.194-1"
    )
    assert "error" not in result, result
    assert result["saltos"] == 2
    assert result["caminos"][0]["tramos"][0]["rel"] == "TIENE_DOMICILIO"


def test_a_place_is_named_by_its_street_and_not_by_its_hash():
    """The lookup that shows `157d5c86c514b86c` where the graph holds an address.

    A `Domicilio` is keyed by geo hash. The props carry `direccion_texto`; the display name only
    looked for `direccion`, so every shared address in the portal rendered as a hex string — which
    is precisely the finding the demo is built on.
    """
    from expediente.portal.server import _display_name

    props = {"geo_hash": "157d5c86c514b86c", "direccion_texto": "VALLE DE CASABLANCA NORTE N° 8219"}
    assert _display_name("Domicilio", "157d5c86c514b86c", props) == "VALLE DE CASABLANCA NORTE N° 8219"
    # Sin nombre ni dirección, el hash es mejor que nada: la caída sigue siendo honesta.
    assert _display_name("Domicilio", "157d5c86c514b86c", {}) == "157d5c86c514b86c"


def test_the_signals_cache_is_keyed_by_its_parameters(tmp_path):
    """Un límite distinto no puede recibir la respuesta cacheada de otro.

    El bug medido en producción: la caché era un solo slot, así que un sondeo con `?limit=2`
    fijaba la respuesta para todas las llamadas siguientes durante dos minutos. El tablero pedía
    100 señales y recibía 2, y eso se leía como "faltan señales y documentos".
    También se verificaba el filtro: el portal descartaba `detector` al llamar a la API.
    """

    class CountingApi:
        def __init__(self):
            self.seen = []

        def signals(self, params):
            self.seen.append(dict(params))
            limit = int(params.get("limit") or 100)
            return {"count": limit, "signals": [{"id": f"s{i}"} for i in range(limit)]}

    api = CountingApi()
    store = make_store()
    runs = RunStore(tmp_path / "runs")
    registry = RunRegistry(store, lambda: ScriptedProvider([]), runs, Settings(max_steps=2))
    portal = Portal(store, api, runs, registry)

    assert portal.signals({"limit": "2"})["count"] == 2
    assert portal.signals({"limit": "10"})["count"] == 10, "otro límite no puede venir de la caché"
    assert portal.signals({"limit": "2"})["count"] == 2
    assert len(api.seen) == 2, "la tercera llamada tiene que salir de la caché"

    portal.signals({"limit": "5", "detector": "fraccionamiento"})
    assert api.seen[-1].get("detector") == "fraccionamiento", "el filtro por detector se descartaba"
