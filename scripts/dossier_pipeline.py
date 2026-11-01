#!/usr/bin/env python3
"""Signal → archive → verified citations → dossier, for N chains, in one pass.

`scripts/dossier_real.py` builds one dossier from an evidence file somebody produced by hand.
This script removes the "by hand": it takes the detector's own output, picks chains, finds every
order code inside the State's monthly archive, and only then writes the dossier.

The order matters and is the whole point:

1. **Verify first.** A chain is a hypothesis until its orders are located in the published file.
   Codes that are not found are not cited; the gate downstream decides what survives.
2. **Recompute from the population.** The dossier is not built from the signal's numbers: it
   loads every order of that buyer in that month and re-runs the detector. If the recomputation
   disagrees with the signal, the dossier wins, because the dossier is the artifact that gets
   signed.
3. **Report the coverage.** A run that verified 3 of 3 chains is a different claim from one that
   verified 3 of 5, and the index says which one happened.

Usage:
    python3 scripts/dossier_pipeline.py --pick 3 --distinct-months --min-factor 3 \
        --signals out/signals/signals_fraccionamiento.jsonl \
        --orders out/orders_b4.jsonl --outdir out/dossiers
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import unicodedata
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from expediente.domain.normalizers import normalize_rut  # noqa: E402

import verify_archive  # noqa: E402


def _dossier_real():
    """dossier_real.py is a script, not a module; import it without renaming it."""
    path = ROOT / "scripts" / "dossier_real.py"
    spec = importlib.util.spec_from_file_location("dossier_real_mod", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_signals(path: Path) -> list[dict]:
    signals = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                signals.append(json.loads(line))
    return signals


def pick(signals: list[dict], count: int, min_factor: float, min_codes: int, max_codes: int,
         distinct_months: bool) -> list[dict]:
    """Choose chains worth citing: material factor, a citation count a human can read, no repeats.

    The filters are not cosmetic. A chain of 40 orders produces a citation list nobody will ever
    check, and a chain at 1.2x the ceiling is arithmetic, not a finding. Both are dropped here
    rather than explained later.
    """
    candidates = []
    for signal in signals:
        codes = signal.get("order_codes") or []
        ceiling = signal.get("ceiling_clp") or 0
        total = signal.get("total_clp") or 0
        if not codes or not ceiling:
            continue
        factor = total / ceiling
        if factor < min_factor or not (min_codes <= len(codes) <= max_codes):
            continue
        candidates.append((factor, signal))

    candidates.sort(key=lambda pair: (-pair[0], len(pair[1]["order_codes"])))
    chosen: list[dict] = []
    seen_months: set[str] = set()
    for factor, signal in candidates:
        if distinct_months and signal["period"] in seen_months:
            continue
        chosen.append(dict(signal, _factor=round(factor, 4)))
        seen_months.add(signal["period"])
        if len(chosen) == count:
            break
    return chosen


def extract_slices(orders_path: Path, wanted: list[dict], outdir: Path) -> dict[tuple[str, str], dict]:
    """One pass over the full corpus, writing one small file per **buyer unit**-month.

    Two things this had to learn the hard way.

    *The chains live in the full corpus.* ``out/compras_5y.jsonl`` is 10.3 M orders; the
    ``orders_b4`` file the placebo uses is a buyer sample, and a chain is defined by the buyer,
    so a sample can only ever hold part of one. One pass over 11.3 GB serves every chain.

    *A buyer is a RUT, not a name.* "SERVICIO NACIONAL DE PESCA" is several buyer units with
    different RUTs, and the detector groups by ``(organismo_rut, proveedor, mes)``. Slicing by
    name and then filtering by one RUT collects 80 orders and lets the detector see 18, with the
    chain cut in half — a silent split that looks exactly like a detector bug. So the slice is
    keyed by RUT, and the RUT of a chain is found by asking which slice contains its codes.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    names = {str(signal.get("organismo") or "").strip() for signal in wanted}
    periods = {signal["period"] for signal in wanted}
    codes_wanted = {code for signal in wanted for code in signal["order_codes"]}
    code_owner: dict[str, tuple[str, str]] = {}
    handles: dict[tuple[str, str], object] = {}
    counts: dict[tuple[str, str], int] = {}
    ruts_names: dict[tuple[str, str], str] = {}
    paths: dict[tuple[str, str], Path] = {}
    read = 0

    try:
        with orders_path.open("r", encoding="utf-8") as corpus:
            for line in corpus:
                if not line.strip():
                    continue
                read += 1
                if not any(name in line for name in names):
                    continue
                record = json.loads(line)
                period = str(record.get("fecha") or "")[:7]
                if period not in periods:
                    continue
                name = str(record.get("organismo") or "").strip()
                if name not in names:
                    continue
                rut = normalize_rut(record.get("organismo_rut")) or ""
                if not rut:
                    continue
                key = (rut, period)
                handle = handles.get(key)
                if handle is None:
                    path = outdir / f"slice-{period}-{slug(rut)}.jsonl"
                    handle = path.open("w", encoding="utf-8")
                    handles[key] = handle
                    paths[key] = path
                    counts[key] = 0
                    ruts_names[key] = name
                handle.write(line if line.endswith("\n") else line + "\n")
                counts[key] = counts[key] + 1
                code = str(record.get("codigo") or "")
                if code in codes_wanted:
                    code_owner[code] = key
    finally:
        for handle in handles.values():
            handle.close()

    slices = {
        key: {
            "path": paths[key],
            "orders": counts[key],
            "buyer_rut": key[0],
            "organismo": ruts_names.get(key, ""),
        }
        for key in paths
    }
    slices["_code_owner"] = code_owner  # type: ignore[assignment]
    print(
        f"  corpus: {read:,} órdenes leídas · {len(paths)} unidades de compra encontradas "
        f"· {len(code_owner)} de {len(codes_wanted)} códigos de cadena localizados",
        file=sys.stderr,
    )
    return slices


def owner_of(signals: list[dict], code_owner: dict[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
    """Which buyer unit a chain belongs to: the slice that holds most of its codes.

    Not "the first code's owner", because a chain is the detector's, and if the detector grouped
    those codes under one RUT then the majority of them must agree. A tie or a total miss is
    reported, never resolved by guessing.
    """
    resolved: dict[str, tuple[str, str]] = {}
    for signal in signals:
        tally: dict[tuple[str, str], int] = {}
        for code in signal["order_codes"]:
            key = code_owner.get(code)
            if key:
                tally[key] = tally.get(key, 0) + 1
        if not tally:
            continue
        best = max(tally.items(), key=lambda pair: pair[1])
        if list(tally.values()).count(best[1]) > 1:
            continue
        resolved[signal["period"] + "|" + signal.get("proveedor", "")] = best[0]
    return resolved


def slug(text: str) -> str:
    """A filename that survives a filesystem, a URL and a shell: ASCII, no accents, no spaces."""
    folded = unicodedata.normalize("NFKD", text.lower())
    keep = "".join(char if char.isalnum() else "-" for char in folded if not unicodedata.combining(char))
    while "--" in keep:
        keep = keep.replace("--", "-")
    return keep.strip("-")[:48]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--signals", type=Path, default=Path("out/signals/signals_fraccionamiento.jsonl"))
    parser.add_argument("--orders", type=Path, default=Path("out/compras_5y.jsonl"))
    parser.add_argument("--outdir", type=Path, default=Path("out/dossiers"))
    parser.add_argument("--cache", type=Path, default=verify_archive.DEFAULT_CACHE)
    parser.add_argument("--pick", type=int, default=3)
    parser.add_argument("--min-factor", type=float, default=3.0)
    parser.add_argument("--min-codes", type=int, default=3)
    parser.add_argument("--max-codes", type=int, default=12)
    parser.add_argument("--distinct-months", action="store_true")
    parser.add_argument("--generado", default=date.today().isoformat())
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv[1:])

    stream = open("/dev/null", "w") if args.quiet else sys.stderr
    dossier_real = _dossier_real()

    signals = read_signals(args.signals)
    chosen = pick(signals, args.pick, args.min_factor, args.min_codes, args.max_codes, args.distinct_months)
    if not chosen:
        print("ninguna cadena pasó los filtros", file=sys.stderr)
        return 1
    print(f"{len(signals)} señales leídas · {len(chosen)} elegidas para verificar", file=stream or sys.stderr)

    args.outdir.mkdir(parents=True, exist_ok=True)
    print(f"extrayendo rebanadas de {args.orders.name}…", file=sys.stderr)
    slices = extract_slices(args.orders, chosen, args.outdir / "_slices")
    code_owner = slices.pop("_code_owner", {})
    owners = owner_of(chosen, code_owner)
    index = []
    for signal in chosen:
        codes = list(signal["order_codes"])
        period = signal["period"]
        organismo = signal.get("organismo", "")
        proveedor = signal.get("proveedor", "")

        coverage = verify_archive.verify(period, codes, cache=args.cache, progress=stream or sys.stderr)
        finding = {
            "organismo": organismo,
            "proveedor": proveedor,
            "period": period,
            "total_clp": signal.get("total_clp"),
            "ceiling_clp": signal.get("ceiling_clp"),
            "factor": signal.get("_factor"),
            "n_orders": signal.get("n_orders"),
        }
        evidence = verify_archive.evidence_of(coverage, finding)

        entry = {
            "period": period,
            "organismo": organismo,
            "proveedor": proveedor,
            "factor": signal.get("_factor"),
            "total_clp": signal.get("total_clp"),
            "ceiling_clp": signal.get("ceiling_clp"),
            "codes": len(codes),
            "verified": len(coverage.lines),
            "coverage": round(coverage.rate, 4),
            "missing": coverage.missing,
            "archive": coverage.archive,
            "archive_sha256": coverage.archive_sha256,
            "buyer_rut": "",
            "dossier": None,
            "dossier_id": None,
            "citation_rate": None,
            "signable": None,
            "kept": None,
            "dropped": None,
            "error": None,
        }

        key = owners.get(period + "|" + signal.get("proveedor", ""))
        slice_info = slices.get(key) if key else {}
        buyer_rut = slice_info.get("buyer_rut") or ""
        slice_path = slice_info.get("path")
        entry["buyer_rut"] = buyer_rut
        entry["buyer_organismo"] = slice_info.get("organismo", "")
        if not slice_path or not buyer_rut:
            entry["error"] = "comprador no resuelto en el corpus"
            index.append(entry)
            print(f"  ✗ {period} {organismo[:40]}: {entry['error']}", file=sys.stderr)
            continue

        orders = dossier_real.load_buyer_orders(slice_path, buyer_rut, period)
        entry["buyer_orders"] = len(orders)
        entry["chain_codes_in_population"] = sum(
            1 for code in codes if code in code_owner and code_owner.get(code) == key
        )
        if not orders:
            entry["error"] = "sin órdenes del comprador en ese mes dentro del corpus"
            index.append(entry)
            print(f"  ✗ {period} {organismo[:40]}: {entry['error']}", file=sys.stderr)
            continue

        store, built = dossier_real.build_store(orders, evidence)
        result = dossier_real.investigate(
            store,
            orders,
            titulo="Expediente de compras públicas — cadena de órdenes bajo el tope",
            sujeto=f"Comprador {buyer_rut} · {period} · órdenes de trato directo",
            generado_en=args.generado,
            placebo=True,
            notas=dossier_real.notas_default(),
        )

        name = f"expediente-{period}-{slug(organismo)}"
        markdown = args.outdir / f"{name}.md"
        markdown.write_text(result.dossier.to_markdown(), encoding="utf-8")
        (args.outdir / f"{name}.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

        entry.update(
            dossier=str(markdown),
            dossier_id=result.dossier.id,
            citation_rate=round(result.dossier.citation_rate, 4),
            signable=bool(result.dossier.signable),
            kept=result.kept,
            dropped=result.dropped,
            attached=built["attached"],
            unverified_codes=coverage.missing,
        )
        index.append(entry)
        print(
            f"  ✓ {period} {organismo[:38]:38s} {len(coverage.lines)}/{len(codes)} citas · "
            f"{result.kept} afirmaciones · firmable={'sí' if result.dossier.signable else 'no'}",
            file=sys.stderr,
        )

    ok = [row for row in index if row["dossier"]]
    summary = {
        "generado": args.generado,
        "signals_read": len(signals),
        "chains_selected": len(chosen),
        "chains_with_dossier": len(ok),
        "codes_verified": sum(row["verified"] for row in index),
        "codes_total": sum(row["codes"] for row in index),
        "coverage": round(
            sum(row["verified"] for row in index) / max(1, sum(row["codes"] for row in index)), 4
        ),
        "chains": index,
    }
    summary_path = args.outdir / "index.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    if stream:
        stream.close()

    print()
    print(f"cadenas: {len(ok)}/{len(chosen)} con expediente · citas verificadas "
          f"{summary['codes_verified']}/{summary['codes_total']} ({summary['coverage']:.0%})")
    for row in index:
        state = "✓" if row["dossier"] else "✗"
        print(f"  {state} {row['period']} · {row['organismo'][:40]:40s} {row['verified']}/{row['codes']} "
              f"· {row['factor']}x · {row['dossier'] or row['error'] or ''}")
    print(f"\n  índice → {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
