"""The agent loop, tested without a model and without a network.

Every case here is a failure mode that would ship silently if the loop were tested
by watching a demo: a claim with no citation, a claim citing an order that does not
exist, a quote that the page never said, a run that runs out of budget, and a model
that answers in prose instead of using the verbs.
"""

import json

import pytest

from expediente.agent import (
    MIN_OUTPUT_TOKENS,
    Investigator,
    ModelReply,
    ProviderError,
    ScriptedProvider,
    Settings,
    ToolCall,
    tool_call,
)
from expediente.agent import tools as toolbox
from expediente.graph.store import InMemoryGraphStore
from expediente.research import Source
from expediente.runs import RunStore, replay

CONTRALORIA_TEXT = (
    "Informe Final N° 412-2025. La municipalidad adquirió insumos médicos mediante seis "
    "órdenes de compra por un total de 54.582.378 pesos, sin acreditar la licitación previa. "
    "Se recomienda instruir un procedimiento disciplinario."
)


# --------------------------------------------------------------------- fixtures
def make_store() -> InMemoryGraphStore:
    store = InMemoryGraphStore()
    store.upsert_node("Organismo", "69.260.000-1", nombre="Municipalidad de Renca")
    store.upsert_node("Proveedor", "76.264.138-0", razon_social="CARDIOTEC VASCULAR LIMITADA")
    for index in range(3):
        store.upsert_node("OrdenCompra", f"OC-{index}", monto_clp=900_000, fecha="2026-08-01")
        store.upsert_edge(("Organismo", "69.260.000-1"), "EMITE", ("OrdenCompra", f"OC-{index}"))
        store.upsert_edge(("OrdenCompra", f"OC-{index}"), "ADJUDICADA_A", ("Proveedor", "76.264.138-0"))
    return store


def fake_fetch(url: str, **_kwargs) -> Source:
    return Source(
        url=url,
        requested_url=url,
        fetched_at="2026-10-29T10:00:00-0300",
        status=200,
        content_type="text/html",
        sha256="a" * 64,
        title="Informe Final N° 412-2025",
        text=CONTRALORIA_TEXT,
        n_bytes=len(CONTRALORIA_TEXT),
        domain="contraloria.cl",
        official=True,
    )


def fake_search(query: str, **_kwargs):
    from expediente.research import SearchHit

    return [SearchHit(title="Informe Final 412-2025", url="https://www.contraloria.cl/412", snippet="…", domain="contraloria.cl", official=True)]


def text_reply(text: str) -> ModelReply:
    return ModelReply(text=text, tool_calls=(), usage={"total_tokens": 10}, model="scripted")


def plan_reply(text: str = "Buscar la municipalidad en el grafo\nVer qué órdenes emitió\nRevisar la cadena de evidencia") -> ModelReply:
    return text_reply(text)


def distinct_fetch(url: str, **_kwargs) -> Source:
    """Same text, different bytes per URL — so the registry does not dedupe them.

    Needed to test the fetch budget: with ``fake_fetch`` every URL returns the same
    sha256 and the registry correctly collapses them into one source, which made an
    earlier version of that test assert against a dedupe rather than a budget.
    """
    import hashlib

    body = CONTRALORIA_TEXT + f" (copia en {url})"
    return Source(
        url=url,
        requested_url=url,
        fetched_at="2026-10-29T10:00:00-0300",
        status=200,
        content_type="text/html",
        sha256=hashlib.sha256(body.encode()).hexdigest(),
        title=f"Documento en {url}",
        text=body,
        n_bytes=len(body),
        domain="contraloria.cl",
        official=True,
    )


def run_with(script, *, store=None, settings=None, runs_dir=None, run_store=None, fetcher=None):
    store = store or make_store()
    provider = ScriptedProvider(script)
    runs = run_store or RunStore(runs_dir)
    investigator = Investigator(
        store,
        provider,
        runs=runs,
        settings=settings or Settings(),
        fetcher=fetcher or fake_fetch,
        searcher=fake_search,
    )
    result = investigator.investigate("¿Compró Renca lo mismo tres veces al mismo proveedor?")
    return result, provider, runs


# ------------------------------------------------------------------------ happy
def test_a_run_plans_acts_and_records_every_step(tmp_path):
    result, provider, runs = run_with(
        [
            plan_reply(),
            ModelReply(tool_calls=(tool_call("buscar_en_grafo", {"label": "Organismo", "texto": "Renca"}),), text=""),
            ModelReply(tool_calls=(tool_call("afirmar", {"texto": "Renca emitió tres órdenes a Cardiotec", "ordenes": ["OC-0", "OC-1", "OC-2"]}),), text=""),
            ModelReply(tool_calls=(tool_call("terminar", {"resumen": "Tres órdenes del mismo organismo al mismo proveedor en el mismo mes.", "vacios": []}),), text=""),
        ],
        runs_dir=tmp_path / "runs",
    )

    assert result.error is None
    assert result.plan[:1] == ["Buscar la municipalidad en el grafo"]
    assert len(result.claims) == 1
    assert result.claims[0]["origen"] == "grafo"
    assert "OrdenCompra/OC-0" in result.claims[0]["nodos"]
    assert result.dossier_id.startswith("sha256:")
    assert result.stopped_by == "terminar"
    assert result.steps == 3

    run = runs.open(result.run_id)
    events = run.events.events()
    types = [event.type for event in events]
    assert "agent.plan" in types
    assert types.count("tool.call") == 3
    assert types.count("claim.accepted") == 1
    assert types[-1] == "run.finished"
    assert run.read_artifact("expediente.md") is not None


def test_the_same_recording_replays_to_the_same_investigation(tmp_path):
    result, _provider, runs = run_with(
        [
            plan_reply(),
            ModelReply(tool_calls=(tool_call("afirmar", {"texto": "Tres órdenes al mismo proveedor", "ordenes": ["OC-0"]}),), text=""),
            ModelReply(tool_calls=(tool_call("terminar", {"resumen": "listo"}),), text=""),
        ],
        runs_dir=tmp_path / "runs",
    )
    state = replay(runs.open(result.run_id).events.events())
    assert state["dossier"]["id"] == result.dossier_id
    assert len(state["claims_accepted"]) == 1


# ------------------------------------------------------------------- the gate
def test_a_claim_citing_an_order_that_does_not_exist_is_refused(tmp_path):
    result, _provider, runs = run_with(
        [
            plan_reply(),
            ModelReply(tool_calls=(tool_call("afirmar", {"texto": "Hubo 40 órdenes", "ordenes": ["OC-INVENTADA"]}),), text=""),
            ModelReply(tool_calls=(tool_call("terminar", {"resumen": "sin hallazgos"}),), text=""),
        ],
        runs_dir=tmp_path / "runs",
    )
    assert result.claims == []
    assert len(result.rejections) == 1
    assert "no existen en el grafo" in result.rejections[0]["motivo"]

    events = runs.open(result.run_id).events.events()
    assert any(event.type == "claim.rejected" for event in events)
    dossier = runs.open(result.run_id).read_artifact("expediente.md")
    assert "Ninguna afirmación sobrevivió" in dossier


def test_a_claim_with_no_support_at_all_is_refused(tmp_path):
    result, _p, _r = run_with(
        [
            plan_reply(),
            ModelReply(tool_calls=(tool_call("afirmar", {"texto": "Esto parece fraude"}),), text=""),
            ModelReply(tool_calls=(tool_call("terminar", {"resumen": "nada"}),), text=""),
        ],
        runs_dir=tmp_path / "runs",
    )
    assert result.claims == []
    assert "sin soporte" in result.rejections[0]["motivo"]


def test_a_web_claim_is_accepted_only_with_a_verbatim_quote(tmp_path):
    result, _p, runs = run_with(
        [
            plan_reply(),
            ModelReply(tool_calls=(tool_call("leer_fuente", {"url": "https://www.contraloria.cl/412"}),), text=""),
            ModelReply(
                tool_calls=(
                    tool_call(
                        "afirmar",
                        {
                            "texto": "La Contraloría detectó seis órdenes sin licitación previa",
                            "source_id": "web:" + "a" * 16,
                            "cita": "adquirió insumos médicos mediante seis órdenes de compra por un total de 54.582.378 pesos",
                        },
                    ),
                ),
                text="",
            ),
            ModelReply(tool_calls=(tool_call("terminar", {"resumen": "con respaldo documental"}),), text=""),
        ],
        runs_dir=tmp_path / "runs",
    )
    assert len(result.claims) == 1
    claim = result.claims[0]
    assert claim["origen"] == "web"
    assert claim["url"] == "https://www.contraloria.cl/412"
    assert claim["sha256"] == "a" * 64
    assert claim["oficial"] is True
    assert len(result.sources) == 1


def test_a_web_claim_whose_quote_the_page_never_said_is_refused(tmp_path):
    result, _p, _r = run_with(
        [
            plan_reply(),
            ModelReply(tool_calls=(tool_call("leer_fuente", {"url": "https://www.contraloria.cl/412"}),), text=""),
            ModelReply(
                tool_calls=(
                    tool_call(
                        "afirmar",
                        {
                            "texto": "La Contraloría concluyó que hubo fraude",
                            "source_id": "web:" + "a" * 16,
                            "cita": "la Contraloría concluyó que hubo fraude",
                        },
                    ),
                ),
                text="",
            ),
            ModelReply(tool_calls=(tool_call("terminar", {"resumen": "nada sostenible"}),), text=""),
        ],
        runs_dir=tmp_path / "runs",
    )
    assert result.claims == []
    assert "no aparece" in result.rejections[0]["motivo"]


# -------------------------------------------------------------------- budgets
def test_the_step_budget_stops_the_loop_and_says_so(tmp_path):
    script = [plan_reply()] + [
        ModelReply(tool_calls=(tool_call("vecinos", {"nodo": "Proveedor/76.264.138-0"}),), text="")
        for _ in range(10)
    ]
    result, _p, runs = run_with(script, settings=Settings(max_steps=2), runs_dir=tmp_path / "runs")
    assert result.steps == 2
    assert "pasos" in result.stopped_by
    assert any(event.type == "budget.exceeded" for event in runs.open(result.run_id).events.events())


def test_the_wall_clock_budget_is_a_control_not_a_prompt(tmp_path):
    result, _p, _r = run_with(
        [plan_reply()] + [ModelReply(tool_calls=(tool_call("vecinos", {"nodo": "Proveedor/76.264.138-0"}),), text="") for _ in range(5)],
        settings=Settings(max_steps=99, max_wall_s=0.0),
        runs_dir=tmp_path / "runs",
    )
    assert result.steps == 0
    assert "tiempo" in result.stopped_by


def test_reasoning_tokens_have_a_floor_because_they_eat_the_budget(tmp_path):
    """A probe against the live API returned 118 reasoning tokens and no tool call
    from a 120-token budget. The floor is the fix, and it is asserted here so a
    future 'let's save tokens' edit fails instead of silently breaking the agent."""
    _result, provider, _runs = run_with(
        [plan_reply(), ModelReply(tool_calls=(tool_call("terminar", {"resumen": "x"}),), text="")],
        runs_dir=tmp_path / "runs",
    )
    assert all(call["max_tokens"] >= MIN_OUTPUT_TOKENS for call in provider.calls)


def test_the_model_always_receives_the_whole_stable_tool_list(tmp_path):
    _result, provider, _runs = run_with(
        [plan_reply(), ModelReply(tool_calls=(tool_call("terminar", {"resumen": "x"}),), text="")],
        runs_dir=tmp_path / "runs",
    )
    step_calls = [call for call in provider.calls if call["tools"]]
    assert step_calls, "the loop must pass tools at least once"
    assert step_calls[0]["tools"] == list(toolbox.TOOL_ORDER)


# ------------------------------------------------------------------- failures
def test_a_prose_answer_gets_one_correction_and_then_the_run_closes(tmp_path):
    result, provider, runs = run_with(
        [
            plan_reply(),
            text_reply("Creo que hubo fraccionamiento porque compró varias veces."),
            text_reply("Sí, definitivamente fue fraccionamiento."),
        ],
        runs_dir=tmp_path / "runs",
    )
    assert result.claims == [], "prose never becomes a claim"
    assert result.stopped_by
    nudges = [call for call in provider.calls if call["messages"][-1].get("role") == "user"
              and "prosa" in str(call["messages"][-1].get("content", ""))]
    assert len(nudges) == 1, "one correction, not a loop of corrections"


def test_a_provider_failure_is_recorded_and_does_not_lose_the_run(tmp_path):
    result, _p, runs = run_with(
        [plan_reply(), ModelReply(tool_calls=(tool_call("afirmar", {"texto": "algo sostenido", "ordenes": ["OC-0"]}),), text="")],
        runs_dir=tmp_path / "runs",
    )
    assert len(result.claims) == 1

    failing = ScriptedProvider([plan_reply()], fail_on=1)
    investigator = Investigator(make_store(), failing, runs=RunStore(tmp_path / "r2"), fetcher=fake_fetch, searcher=fake_search)
    result = investigator.investigate("¿algo?")
    assert result.error is not None
    assert "scripted failure" in result.error
    types = [event.type for event in RunStore(tmp_path / "r2").open(result.run_id).events.events()]
    assert "run.failed" in types


def test_a_tool_error_is_a_result_the_agent_can_recover_from(tmp_path):
    result, _p, runs = run_with(
        [
            plan_reply(),
            ModelReply(tool_calls=(tool_call("vecinos", {"nodo": "no-es-un-id"}),), text=""),
            ModelReply(tool_calls=(tool_call("terminar", {"resumen": "no pude navegar"}),), text=""),
        ],
        runs_dir=tmp_path / "runs",
    )
    events = runs.open(result.run_id).events.events()
    assert any(event.type == "tool.error" for event in events)
    assert result.error is None, "a bad argument is not a dead run"
    assert result.stopped_by == "terminar"


def test_the_fetch_budget_is_enforced_per_run(tmp_path):
    result, _p, runs = run_with(
        [plan_reply()]
        + [ModelReply(tool_calls=(tool_call("leer_fuente", {"url": f"https://www.contraloria.cl/{i}"}),), text="") for i in range(6)]
        + [ModelReply(tool_calls=(tool_call("terminar", {"resumen": "ok"}),), text="")],
        settings=Settings(max_steps=8),
        runs_dir=tmp_path / "runs",
        fetcher=distinct_fetch,
    )
    events = runs.open(result.run_id).events.events()
    budget = [event for event in events if event.type == "budget.exceeded" and event.payload.get("limit") == "web_fetches"]
    assert budget, "the fifth fetch must be refused, not silently attempted"
    assert len(result.sources) == toolbox.MAX_WEB_FETCHES


def test_a_refused_fetch_is_logged_as_a_rejected_source(tmp_path):
    def broken(url, **_kwargs):
        from expediente.research import FetchError

        raise FetchError("HTTP 403")

    store = make_store()
    investigator = Investigator(
        store,
        ScriptedProvider(
            [
                plan_reply(),
                ModelReply(tool_calls=(tool_call("leer_fuente", {"url": "https://www.contraloria.cl/x"}),), text=""),
                ModelReply(tool_calls=(tool_call("terminar", {"resumen": "sin fuente"}),), text=""),
            ]
        ),
        runs=RunStore(tmp_path / "runs"),
        fetcher=broken,
        searcher=fake_search,
    )
    result = investigator.investigate("¿algo?")
    types = [event.type for event in RunStore(tmp_path / "runs").open(result.run_id).events.events()]
    assert "source.rejected" in types
    assert result.sources == []


def test_the_dossier_id_is_derived_from_content_not_from_the_run(tmp_path):
    script = [
        plan_reply(),
        ModelReply(tool_calls=(tool_call("afirmar", {"texto": "Tres órdenes a un proveedor", "ordenes": ["OC-0"]}),), text=""),
        ModelReply(tool_calls=(tool_call("terminar", {"resumen": "listo"}),), text=""),
    ]
    first, _p1, _r1 = run_with(script, runs_dir=tmp_path / "a")
    second, _p2, _r2 = run_with(
        [
            plan_reply("Otro plan distinto para la misma pregunta\nSegundo paso"),
            ModelReply(tool_calls=(tool_call("afirmar", {"texto": "Tres órdenes a un proveedor", "ordenes": ["OC-0"]}),), text=""),
            ModelReply(tool_calls=(tool_call("terminar", {"resumen": "listo"}),), text=""),
        ],
        runs_dir=tmp_path / "b",
    )
    assert first.run_id != second.run_id
    assert first.dossier_id == second.dossier_id, "same conclusion, same identity, whatever the path"
