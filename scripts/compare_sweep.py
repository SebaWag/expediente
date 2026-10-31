#!/usr/bin/env python3
"""Compara los tramos de un sweep de extraction: exacto, rechazos, costo y test.

Cada corrida de ``extract_bench.py`` produce un JSON con su reporte y los flags por
documento. Comparar esos archivos a mano es how a claim like "low is better" ends up
without a number behind it, so this prints the table and the paired test.

McNemar se calcula sobre los ``flags`` (un entero por documento, en orden de entrada),
que es lo mismo que hace el harness dentro de una corrida; aquí se hace entre corridas
separadas, que es como se corre un sweep de verdad.

Uso:
    python3 scripts/compare_sweep.py low medium high
    python3 scripts/compare_sweep.py low high --json out/comparacion.json
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def mcnemar_exact(only_a: int, only_b: int) -> float:
    total = only_a + only_b
    if total == 0:
        return 1.0
    tail = sum(math.comb(total, k) for k in range(0, min(only_a, only_b) + 1)) / (2**total)
    return min(1.0, 2 * tail)


def load(effort: str, directory: Path) -> dict:
    path = directory / f"sweep_{effort}.json"
    if not path.exists():
        raise FileNotFoundError(f"falta {path}: ese tramo no terminó o cambió de nombre")
    runs = json.loads(path.read_text(encoding="utf-8"))["runs"]
    return next(iter(runs.items()))[1]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("efforts", nargs="+", default=["low", "medium", "high"])
    parser.add_argument("--dir", type=Path, default=ROOT / "out")
    parser.add_argument("--json", type=Path, help="escribir la comparación como JSON")
    args = parser.parse_args(argv[1:])

    runs = {e: load(e, args.dir) for e in args.efforts}
    payload: dict = {"runs": {}, "pairs": {}}

    print(f"{'effort':<8}{'exacto':>8}{'no verif':>10}{'trunc':>7}{'completion':>12}{'reasoning':>11}")
    for effort, report in runs.items():
        print(
            f"{effort:<8}{report['exacto']:>8.3f}{report['tasa_no_verificado']:>9.2%}"
            f"{report['truncados']:>7}{report['tokens']['completion']:>12}{report['tokens']['reasoning']:>11}"
        )
        payload["runs"][effort] = {
            "exacto": report["exacto"],
            "exactos": report["exactos"],
            "exactos_evaluados": report["exactos_evaluados"],
            "tasa_no_verificado": report["tasa_no_verificado"],
            "truncados": report["truncados"],
            "causas": report["causas"],
            "tokens": report["tokens"],
        }

    print()
    for left, right in itertools.combinations(runs, 2):
        left_flags, right_flags = runs[left]["flags"], runs[right]["flags"]
        if len(left_flags) != len(right_flags):
            print(f"{left} vs {right}: n distinto ({len(left_flags)} vs {len(right_flags)}), sin test")
            continue
        only_left = sum(1 for a, b in zip(left_flags, right_flags) if a and not b)
        only_right = sum(1 for a, b in zip(left_flags, right_flags) if b and not a)
        p_value = mcnemar_exact(only_left, only_right)
        print(f"{left} vs {right}: solo {left}={only_left} · solo {right}={only_right} · McNemar p={p_value:.2e}")
        payload["pairs"][f"{left}_vs_{right}"] = {
            "only_left": only_left,
            "only_right": only_right,
            "mcnemar_p": p_value,
            "n": len(left_flags),
        }

    prompts = {effort: report["tokens"]["prompt"] for effort, report in runs.items()}
    print()
    print(f"prompt tokens por effort (deben coincidir): {prompts}")
    if len(set(prompts.values())) > 1:
        print("  ⚠ difieren: la entrada no fue la misma y la comparación no es pareada de verdad")

    fields = list(next(iter(runs.values()))["por_campo"])
    print()
    print(f"{'campo':<22}" + "".join(f"{effort:>9}" for effort in runs))
    for field in fields:
        row = f"{field:<22}"
        for report in runs.values():
            cell = report["por_campo"][field]
            row += f"{cell['ok'] / cell['graded']:>9.3f}" if cell["graded"] else f"{'-':>9}"
        print(row)
        for effort, report in runs.items():
            cell = report["por_campo"][field]
            payload["runs"][effort].setdefault("por_campo", {})[field] = {
                "ok": cell["ok"],
                "graded": cell["graded"],
                "unverified": cell["unverified"],
            }

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nwrote {args.json}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
