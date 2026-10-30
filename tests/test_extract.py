"""The extractor is trusted for two things: it fills cells, and it admits when it cannot.

So the tests are mostly about absence. A green suite here means an empty answer can
never be mistaken for an empty document.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from expediente.extract import grading
from expediente.extract.backends import Completion, MockBackend, NotConfigured, TunedBackend, parse_completion
from expediente.extract.models import Causa, Page, Usage
from expediente.extract.registry import choose
from expediente.extract.schema import PROCUREMENT_FIELDS, by_name


def completion(content="", finish_reason="stop", reasoning_tokens=0, completion_tokens=0) -> Completion:
    return Completion(
        content=content,
        finish_reason=finish_reason,
        usage=Usage(prompt_tokens=600, completion_tokens=completion_tokens or len(content), reasoning_tokens=reasoning_tokens),
    )


def extract(content, **kwargs):
    return parse_completion(completion(content, **kwargs), "doc-1", "test")


def causes(result) -> dict[str, str]:
    return {u.campo: u.causa.value for u in result.unverified}


def test_an_empty_answer_is_named_and_not_left_silent():
    result = extract("")

    assert result.campos == ()
    assert set(causes(result).values()) == {"EMPTY_RESPONSE"}
    assert len(result.unverified) == len(PROCUREMENT_FIELDS)


def test_reasoning_that_ate_the_output_budget_is_truncation_not_absence():
    result = extract("", finish_reason="length", reasoning_tokens=2048, completion_tokens=2048)

    assert set(causes(result).values()) == {"TRUNCATED"}
    assert result.truncado is True
    # The number that explains the failure survives into the record.
    assert result.usage.reasoning_tokens == 2048


def test_prose_instead_of_json_is_counted_as_invalid_json():
    result = extract("Aquí está el monto total de la orden: $912.400")

    assert set(causes(result).values()) == {"INVALID_JSON"}


def test_a_value_nobody_can_turn_to_a_page_for_is_refused():
    payload = json.dumps({"campos": {"monto_total_clp": {"valor": "912400", "extracto": "Monto total: $912.400"}}})

    result = extract(payload)

    assert result.field("monto_total_clp") is None
    assert causes(result)["monto_total_clp"] == "NO_PAGE"


def test_a_field_with_a_page_survives_and_the_rest_stay_named():
    payload = json.dumps(
        {
            "campos": {
                "monto_total_clp": {"valor": "912.400", "pagina": 2, "extracto": "Monto total: $912.400"},
                "rut_proveedor": {"valor": "11.111.111-1", "pagina": 2, "extracto": "RUT 11.111.111-1"},
            }
        }
    )

    result = extract(payload)

    assert result.field("monto_total_clp").pagina == 2
    assert result.field("monto_total_clp").backend == "test"
    assert causes(result)["unidad_compradora"] == "MISSING_FIELD"
    assert len(result.campos) == 2
    assert len(result.unverified) == len(PROCUREMENT_FIELDS) - 2


def test_a_truncated_answer_still_keeps_the_fields_it_earned():
    payload = json.dumps({"campos": {"monto_total_clp": {"valor": "912400", "pagina": 2}}})

    result = extract(payload, finish_reason="length", reasoning_tokens=2048)

    assert result.field("monto_total_clp") is not None
    assert causes(result)["fecha_orden"] == "TRUNCATED"


def test_a_field_the_schema_never_asked_for_has_no_home():
    payload = json.dumps({"campos": {"color_favorito": {"valor": "azul", "pagina": 1}}})

    result = extract(payload)

    assert result.campos == ()
    assert "color_favorito" not in {u.campo for u in result.unverified}


def test_a_markdown_fence_is_not_allowed_to_hide_the_record():
    payload = "```json\n" + json.dumps({"campos": {"monto_total_clp": {"valor": "912400", "pagina": 2}}}) + "\n```"

    result = extract(payload)

    assert result.field("monto_total_clp").valor == "912400"


def test_a_number_is_graded_within_one_percent_and_not_within_a_factor_of_ten():
    spec = by_name()["monto_total_clp"]

    assert grading.grade(spec, "912.400", "912400") is True
    assert grading.grade(spec, "912000", "912400") is True
    assert grading.grade(spec, "91240", "912400") is False
    assert grading.grade(spec, "9.124.000", "912400") is False
    assert grading.grade(spec, None, "912400") is False


def test_a_chilean_amount_with_both_separators_reads_as_grouping_and_decimals():
    assert grading.parse_number("$1.048.500") == 1048500.0
    assert grading.parse_number("940,000") == 940000.0
    assert grading.parse_number("1048500,50") == 1048500.5


def test_a_rut_matches_without_punctuation_but_a_missing_check_digit_is_not_a_match():
    spec = by_name()["rut_proveedor"]

    assert grading.grade(spec, "76543210K", "76.543.210-K") is True
    assert grading.grade(spec, "76.543.210-K", "76543210K") is True
    assert grading.grade(spec, "76543210", "76.543.210-K") is False


def test_a_date_is_not_allowed_to_swap_day_and_month():
    spec = by_name()["fecha_orden"]

    assert grading.grade(spec, "2026-10-03", "03/10/2026") is True
    assert grading.grade(spec, "10/03/2026", "03/10/2026") is False
    assert grading.grade(spec, "no informada", "03/10/2026") is False


def test_an_enum_matches_the_documents_prose_and_not_a_different_mechanism():
    spec = by_name()["procedencia"]

    assert grading.grade(spec, "Compra Ágil", "compra_agil") is True
    assert grading.grade(spec, "trato directo", "trato_directo") is True
    assert grading.grade(spec, "licitacion publica", "trato_directo") is False


def test_a_kind_without_a_comparison_fails_loudly_instead_of_being_skipped():
    class Wrong:
        tipo = "geometria"

    with pytest.raises(ValueError):
        grading.grade(Wrong(), "1", "1")


def test_the_registry_degrades_to_serverless_when_no_lora_is_deployed(monkeypatch):
    monkeypatch.delenv("EXPEDIENTE_EXTRACT_DEPLOYMENT", raising=False)

    choice = choose()

    assert choice.degraded is True
    assert "not deployed" in choice.motivo
    assert choice.backend.name.startswith("muse-spark-open")


def test_asking_for_the_tuned_backend_when_none_exists_fails_instead_of_pretending(monkeypatch):
    monkeypatch.delenv("EXPEDIENTE_EXTRACT_DEPLOYMENT", raising=False)

    with pytest.raises(NotConfigured):
        choose(prefer="tuned")


def test_a_deployed_lora_is_chosen_and_reported_as_not_degraded(monkeypatch):
    monkeypatch.setenv("EXPEDIENTE_EXTRACT_DEPLOYMENT", "muse-router-demo")
    monkeypatch.setenv("FIREWORKS_ACCOUNT_ID", "acct-test")

    choice = choose()

    assert choice.degraded is False
    assert choice.backend.name.startswith("lora:muse-router-demo")


def test_the_tuned_backend_addresses_a_deployment_not_a_published_model(monkeypatch):
    monkeypatch.setenv("EXPEDIENTE_EXTRACT_DEPLOYMENT", "muse-router-demo")
    monkeypatch.setenv("FIREWORKS_ACCOUNT_ID", "acct-test")

    assert TunedBackend().resolve_model() == "accounts/acct-test/deployments/muse-router-demo"


def load_bench():
    path = Path(__file__).resolve().parents[1] / "benchmarks" / "extract_bench.py"
    spec = importlib.util.spec_from_file_location("extract_bench", path)
    module = importlib.util.module_from_spec(spec)
    # Register before executing: dataclasses with PEP 563 annotations resolve
    # their module through sys.modules, and a module that is not there raises.
    sys.modules["extract_bench"] = module
    spec.loader.exec_module(module)
    return module


def test_the_harness_counts_what_was_not_verified_instead_of_dropping_it():
    bench = load_bench()
    examples = bench.load_examples(Path(__file__).resolve().parents[1] / "examples" / "extract_fixture.jsonl")
    completions = bench.load_mock_completions(
        Path(__file__).resolve().parents[1] / "benchmarks" / "extract_mock_completions.jsonl"
    )

    results = bench.run_backend(MockBackend(completions=completions, name="mock"), examples, list(PROCUREMENT_FIELDS))
    report = bench.score(results, list(PROCUREMENT_FIELDS))

    assert report["ejemplos"] == 3
    # One document was truncated outright: eight fields must appear as TRUNCATED,
    # not as zero-accuracy silent misses.
    assert report["truncados"] == 1
    assert report["causas"]["TRUNCATED"] == len(PROCUREMENT_FIELDS)
    assert report["causas"]["NO_PAGE"] == 1
    assert report["no_verificado"] > 0
    assert report["tasa_no_verificado"] == pytest.approx(
        report["no_verificado"] / report["campos_evaluados"]
    )


def test_mcnemar_says_nothing_when_the_two_backends_disagree_about_one_document():
    bench = load_bench()

    assert bench.mcnemar_exact(1, 0) == pytest.approx(1.0)
    assert bench.mcnemar_exact(10, 0) < 0.01
