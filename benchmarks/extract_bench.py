#!/usr/bin/env python3
"""Score an extractor against labelled documents, and report what it refused to do.

Three numbers matter here, and the third is the one nobody publishes:

1. ``campo`` accuracy  — per field, over the examples whose label has that field.
2. ``exacto``          — the whole record right, every field. This is the number a
   demo shows and the number a reviewer distrusts, correctly.
3. ``no verificado``   — how many requested fields came back with a cause instead
   of a value, broken down by cause. A run that is 80% accurate and leaves 15% of
   the fields unverified is not an 80% extractor; it is an extractor that reads
   four documents out of five, and the report has to say so.

Grading is ``grading.py``: rules, not a judge. The labels come from deterministic
detectors, so the thing being scored and the thing scoring it share no model. That
is the only reason comparing two backends here means anything.

Usage
-----
    # real run, serverless, two effort levels
    python3 benchmarks/extract_bench.py --examples benchmarks/extract_gold.jsonl --efforts low,high

    # the same harness with no network and no credits (see the mock file's header)
    python3 benchmarks/extract_bench.py --examples examples/extract_fixture.jsonl \
        --mock benchmarks/extract_mock_completions.jsonl
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from expediente.extract import grading  # noqa: E402
from expediente.extract.backends import Backend, Completion, MockBackend, TunedBackend, ZeroShotBackend  # noqa: E402
from expediente.extract.models import Causa, ExtractionResult, Page, Usage  # noqa: E402
from expediente.extract.schema import PROCUREMENT_FIELDS, FieldSpec  # noqa: E402


@dataclass(frozen=True, slots=True)
class GoldExample:
    """One document, its pages, and the values a human or a rule confirmed."""

    id: str
    documento_id: str
    paginas: tuple[Page, ...]
    gold: dict[str, str]
    nota: str = ""


def load_examples(path: Path) -> list[GoldExample]:
    examples: list[GoldExample] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        row = json.loads(line)
        examples.append(
            GoldExample(
                id=row["id"],
                documento_id=row.get("documento_id", row["id"]),
                paginas=tuple(Page(numero=int(p["numero"]), texto=p["texto"]) for p in row["paginas"]),
                gold={k: str(v) for k, v in row.get("gold", {}).items() if str(v).strip()},
                nota=row.get("nota", ""),
            )
        )
    return examples


def load_mock_completions(path: Path) -> list[Completion]:
    completions: list[Completion] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        row = json.loads(line)
        usage = row.get("usage") or {}
        completions.append(
            Completion(
                content=row.get("content", ""),
                finish_reason=row.get("finish_reason"),
                usage=Usage(
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    reasoning_tokens=int(usage.get("reasoning_tokens") or 0),
                ),
            )
        )
    return completions


def score(results: list[tuple[GoldExample, ExtractionResult]], specs: list[FieldSpec]) -> dict:
    """Per-field accuracy, whole-record accuracy, and the refusal counts."""
    per_field: dict[str, dict[str, int]] = {spec.nombre: {"ok": 0, "graded": 0, "unverified": 0} for spec in specs}
    exact_hits = 0
    exact_total = 0
    flags: list[int] = []
    causas: dict[str, int] = {causa.value: 0 for causa in Causa}
    tokens = {"prompt": 0, "completion": 0, "reasoning": 0}
    truncados = 0

    for example, result in results:
        graded_here = 0
        hits_here = 0
        for spec in specs:
            gold = example.gold.get(spec.nombre)
            if not gold:
                continue
            graded_here += 1
            per_field[spec.nombre]["graded"] += 1
            campo = result.field(spec.nombre)
            if campo is None:
                per_field[spec.nombre]["unverified"] += 1
                continue
            if grading.grade(spec, campo.valor, gold):
                per_field[spec.nombre]["ok"] += 1
                hits_here += 1
        if graded_here:
            exact_total += 1
            hit = int(hits_here == graded_here)
            exact_hits += hit
            flags.append(hit)
        for item in result.unverified:
            causas[item.causa.value] += 1
        truncados += int(result.truncado)
        tokens["prompt"] += result.usage.prompt_tokens
        tokens["completion"] += result.usage.completion_tokens
        tokens["reasoning"] += result.usage.reasoning_tokens

    requested = sum(v["graded"] for v in per_field.values())
    unverified_total = sum(v["unverified"] for v in per_field.values())
    return {
        "ejemplos": len(results),
        "campos_evaluados": requested,
        "por_campo": per_field,
        "exacto": exact_hits / exact_total if exact_total else 0.0,
        "exactos": exact_hits,
        "exactos_evaluados": exact_total,
        "no_verificado": unverified_total,
        "tasa_no_verificado": unverified_total / requested if requested else 0.0,
        "causas": {k: v for k, v in causas.items() if v},
        "truncados": truncados,
        "flags": flags,
        "tokens": tokens,
    }


def mcnemar_exact(only_a: int, only_b: int) -> float:
    """Two-sided exact McNemar p-value. Stdlib only, because the repo has no deps.

    Used to answer one question honestly: is the difference between two backends
    bigger than the disagreement between them? With 40 held-out documents it
    usually is not, and saying so is cheaper than being asked.
    """
    total = only_a + only_b
    if total == 0:
        return 1.0
    tail = sum(math.comb(total, k) for k in range(0, min(only_a, only_b) + 1)) / (2**total)
    return min(1.0, 2 * tail)


def print_report(label: str, report: dict, specs: list[FieldSpec]) -> None:
    print(f"\n── {label} ─────────────────────────────────────────")
    print(f"  ejemplos {report['ejemplos']} · campos evaluados {report['campos_evaluados']}")
    print(f"  {'campo':<22} {'ok':>4} {'n':>4} {'acc':>7} {'sin verificar':>14}")
    for spec in specs:
        row = report["por_campo"][spec.nombre]
        if not row["graded"]:
            continue
        acc = row["ok"] / row["graded"]
        print(f"  {spec.nombre:<22} {row['ok']:>4} {row['graded']:>4} {acc:>7.3f} {row['unverified']:>14}")
    print(f"  {'EXACTO (registro)':<22} {'':>4} {report['exactos']:>4} {report['exacto']:>7.3f}")
    print(
        f"  no verificado: {report['no_verificado']}/{report['campos_evaluados']} "
        f"({report['tasa_no_verificado']:.1%}) · documentos truncados: {report['truncados']}"
    )
    if report["causas"]:
        causas = " · ".join(f"{k}={v}" for k, v in sorted(report["causas"].items()))
        print(f"  causas: {causas}")
    tok = report["tokens"]
    print(f"  tokens: prompt={tok['prompt']} completion={tok['completion']} reasoning={tok['reasoning']}")
    if report["ejemplos"]:
        print(
            f"  por 1.000 documentos: prompt={tok['prompt'] / report['ejemplos'] * 1000:.0f} "
            f"completion={tok['completion'] / report['ejemplos'] * 1000:.0f}"
        )


def run_backend(backend: Backend, examples: list[GoldExample], specs: list[FieldSpec]) -> list[tuple[GoldExample, ExtractionResult]]:
    results = []
    for example in examples:
        results.append((example, backend.extract(example.documento_id, example.paginas, specs)))
    return results


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--examples", type=Path, default=Path("examples/extract_fixture.jsonl"))
    parser.add_argument("--efforts", default="low", help="comma-separated reasoning efforts to sweep")
    parser.add_argument("--tuned", action="store_true", help="also score the deployed LoRA extractor")
    parser.add_argument("--mock", type=Path, help="replay canned completions instead of calling the API")
    parser.add_argument("--json-out", type=Path, help="write the full report as JSON")
    args = parser.parse_args(argv[1:])

    if not args.examples.exists():
        print(f"missing {args.examples}", file=sys.stderr)
        return 1

    specs = list(PROCUREMENT_FIELDS)
    examples = load_examples(args.examples)
    if not examples:
        print(f"no examples in {args.examples}", file=sys.stderr)
        return 1
    print(f"examples: {len(examples)} · fields: {len(specs)} · labels from deterministic rules")

    runs: dict[str, dict] = {}
    payload: dict[str, object] = {"examples": len(examples), "runs": {}}

    if args.mock:
        if not args.mock.exists():
            print(f"missing {args.mock}", file=sys.stderr)
            return 1
        completions = load_mock_completions(args.mock)
        backend = MockBackend(completions=completions, name="mock")
        results = run_backend(backend, examples, specs)
        report = score(results, specs)
        print_report("mock (sin red, sin créditos)", report, specs)
        runs["mock"] = report
        payload["runs"]["mock"] = report  # type: ignore[index]
    else:
        efforts = [e.strip() for e in args.efforts.split(",") if e.strip()]
        for effort in efforts:
            backend = ZeroShotBackend(effort=effort)
            results = run_backend(backend, examples, specs)
            report = score(results, specs)
            print_report(backend.name, report, specs)
            runs[backend.name] = report
            payload["runs"][backend.name] = report  # type: ignore[index]
        if args.tuned:
            tuned = TunedBackend()
            results = run_backend(tuned, examples, specs)
            report = score(results, specs)
            print_report(tuned.name, report, specs)
            runs[tuned.name] = report
            payload["runs"][tuned.name] = report  # type: ignore[index]

    if len(runs) >= 2:
        names = list(runs)
        left, right = names[0], names[1]
        left_flags, right_flags = runs[left]["flags"], runs[right]["flags"]
        only_left = sum(1 for a, b in zip(left_flags, right_flags) if a and not b)
        only_right = sum(1 for a, b in zip(left_flags, right_flags) if b and not a)
        p_value = mcnemar_exact(only_left, only_right)
        print(f"\n── {left} vs {right}: McNemar exacto p={p_value:.3f} (n={len(left_flags)} documentos)")
        print(f"   pares discordantes: solo {left}={only_left} · solo {right}={only_right}")
        if p_value >= 0.05:
            print("   Con este n, la diferencia no se distingue del azar. Decirlo es más barato que ser preguntado.")
        payload["comparison"] = {
            "left": left,
            "right": right,
            "only_left": only_left,
            "only_right": only_right,
            "mcnemar_p": p_value,
            "n": len(left_flags),
        }

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nwrote {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
