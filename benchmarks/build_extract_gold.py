#!/usr/bin/env python3
"""Build a labelled extraction gold set from the structured procurement corpus.

Why this file exists
--------------------
``benchmarks/extract_bench.py`` can score an extractor, but only against documents
whose values are already known. The corpus gives us that for free: every order is a
row with ``codigo``, ``monto_total_clp``, ``proveedor_rut`` and friends. So the label
is the row, and the document is a rendering of the same row.

The property that makes this honest
-----------------------------------
**Every value in ``gold`` is written into the page text by this same script.** That is
asserted in ``tests/test_extract_gold.py``: if a label cannot be found in the text it
renders, the gold set is broken and the test fails. A label the document does not
contain would make the benchmark unanswerable, and an unanswerable benchmark always
looks like a bad extractor.

What this is NOT
----------------
These are **rendered** documents, not scanned PDFs. No OCR, no skewed scans, no
handwriting. The number this produces measures reading comprehension and citation
discipline over clean text; it does not measure document capture. Say so wherever
the number is reported.

Sampling
--------
Reservoir by offset: seeks to random byte positions and reads the next complete
line. Linear scanning of the 10.8 GB corpus takes minutes; this takes seconds and is
still uniformly random over lines.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from collections import Counter
from pathlib import Path

# --- mapping from corpus vocabulary to the schema's enum tokens -------------------

PROCEDENCIA = {
    "Compra Agil": "compra_agil",
    "Licitacion Publica": "licitacion_publica",
    "Trato Directo": "trato_directo",
    "Convenio Marco": "convenio_marco",
}

TIPO_DOC = {
    "Compra Agil": "orden_compra",
    "Licitacion Publica": "resolucion_adjudicacion",
    "Trato Directo": "orden_compra",
    "Convenio Marco": "orden_compra",
}

PROC_TEXTO = {
    "Compra Agil": "Compra Ágil (art. 7 Ley 19.886)",
    "Licitacion Publica": "Licitación Pública",
    "Trato Directo": "Trato Directo (art. 8 Ley 19.886)",
    "Convenio Marco": "Convenio Marco (catálogo electrónico)",
}

#: Share of documents that do NOT break the total into net + VAT. A document that
#: never separates it must not have ``monto_neto_clp`` in its gold: the field is then
#: a legitimate ``MISSING_FIELD`` and the extractor is right to omit it.
SIN_NETO = 0.40

#: Share written as two pages, so the citation has to name the page it came from.
DOS_PAGINAS = 0.50


def _rut_ok(rut: str) -> bool:
    body = re.sub(r"[^0-9kK]", "", str(rut)).upper()
    return 2 <= len(body) <= 10 and body[-1].isdigit() or body[-1] == "K"


def _fmt_clp(n: int) -> str:
    return "$" + f"{int(n):,}".replace(",", ".")


def _fecha_corpus(fecha: str) -> str:
    """ISO ``YYYY-MM-DD`` -> ``dd/mm/yyyy``, the form most Chilean documents print."""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(fecha))
    if not m:
        return str(fecha)
    y, mo, d = m.groups()
    return f"{d}/{mo}/{y}"


def _split_monto(total: int) -> tuple[int, int]:
    """Return (neto, iva) so that neto + iva == total exactly."""
    neto = round(total / 1.19)
    return neto, total - neto


# --- the four layouts ------------------------------------------------------------

def _t_orden(row: dict, fecha: str, total: str, neto: int | None) -> list[dict]:
    montos = f"Monto neto: {_fmt_clp(neto)}\nI.V.A. 19%: {_fmt_clp(row['monto_total_clp'] - neto)}\n" if neto else ""
    return [
        {"numero": 1, "texto":
            f"ORDEN DE COMPRA N° {row['codigo']}\n"
            f"{row['organismo']}\n"
            f"R.U.T.: {row['organismo_rut']}\n"
            f"Unidad Compradora: {row['unidad_compra']}\n"
            f"Fecha de emisión: {fecha}\n"
            f"Procedimiento: {PROC_TEXTO[row['procedencia']]}\n\n"
            f"SEÑOR(ES): {row['proveedor']}\n"
            f"R.U.T.: {row['proveedor_rut']}\n\n"
            f"Detalle: {row['nombre'][:180]}"},
        {"numero": 2, "texto":
            f"{montos}"
            f"MONTO TOTAL: {total}\n"
            f"Estado: {row.get('estado','')}\n"
            f"Región de compra: {row.get('region_compra','')}"},
    ]


def _t_resolucion(row: dict, fecha: str, total: str, neto: int | None) -> list[dict]:
    neto_linea = f"Monto neto: {_fmt_clp(neto)}\n" if neto else ""
    return [
        {"numero": 1, "texto":
            f"RESOLUCIÓN EXENTA\n"
            f"{row['organismo']}\n"
            f"R.U.T.: {row['organismo_rut']}\n\n"
            f"VISTO: licitación ID {row['codigo']}, procedimiento "
            f"{PROC_TEXTO[row['procedencia']]}.\n"
            f"Unidad requirente: {row['unidad_compra']}\n\n"
            f"RESUELVO: adjudícase a {row['proveedor']}, R.U.T. {row['proveedor_rut']}, "
            f"la adquisición de {row['nombre'][:180]}."},
        {"numero": 2, "texto":
            f"{neto_linea}"
            f"por un monto total de {total} (IVA included).\n"
            f"Fecha: {fecha}\n"
            f"Estado: {row.get('estado','')}".replace("IVA included", "IVA incluido")},
    ]


def _t_agil(row: dict, fecha: str, total: str, neto: int | None) -> list[dict]:
    neto_linea = f"Monto neto: {_fmt_clp(neto)}\nIVA: {_fmt_clp(row['monto_total_clp'] - neto)}\n" if neto else ""
    return [
        {"numero": 1, "texto":
            f"COMPRA ÁGIL — FICHA DE ADJUDICACIÓN\n"
            f"Código: {row['codigo']}\n"
            f"Organismo comprador: {row['organismo']}\n"
            f"Unidad: {row['unidad_compra']}\n"
            f"Fecha: {fecha}\n"
            f"Procedimiento: {PROC_TEXTO[row['procedencia']]}\n\n"
            f"Proveedor adjudicado: {row['proveedor']}\n"
            f"RUT proveedor: {row['proveedor_rut']}\n\n"
            f"Descripción: {row['nombre'][:180]}\n"
            f"{neto_linea}"
            f"Monto adjudicado: {total}"},
    ]


def _t_convenio(row: dict, fecha: str, total: str, neto: int | None) -> list[dict]:
    neto_linea = f"Neto: {_fmt_clp(neto)}\n" if neto else ""
    return [
        {"numero": 1, "texto":
            f"CONVENIO MARCO — ORDEN DE COMPRA\n"
            f"N° {row['codigo']}\n"
            f"Organismo comprador: {row['organismo']}\n"
            f"Unidad de compra: {row['unidad_compra']}\n"
            f"Fecha: {fecha}\n"
            f"Proveedor: {row['proveedor']}\n"
            f"RUT: {row['proveedor_rut']}\n\n"
            f"Bienes y servicios: {row['nombre'][:180]}\n"
            f"{neto_linea}"
            f"Monto total (IVA incluido): {total}"},
    ]


PLANTILLAS = {
    "Compra Agil": [_t_agil, _t_orden],
    "Licitacion Publica": [_t_resolucion, _t_orden],
    "Trato Directo": [_t_orden, _t_convenio],
    "Convenio Marco": [_t_convenio, _t_orden],
}


def build_example(row: dict, idx: int, rng: random.Random) -> dict | None:
    proc = row.get("procedencia", "")
    if proc not in PROCEDENCIA:
        return None
    if not row.get("codigo") or not row.get("unidad_compra") or not row.get("proveedor"):
        return None
    if not _rut_ok(row.get("proveedor_rut", "")):
        return None
    total_int = row.get("monto_total_clp")
    if not isinstance(total_int, int) or total_int <= 0:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(row.get("fecha", ""))):
        return None

    fecha = _fecha_corpus(row["fecha"])
    total = _fmt_clp(total_int)
    con_neto = rng.random() > SIN_NETO
    neto = _split_monto(total_int)[0] if con_neto else None

    plantilla = rng.choice(PLANTILLAS[proc])
    paginas = plantilla(row, fecha, total, neto)

    if rng.random() > DOS_PAGINAS and len(paginas) > 1:
        # Fusionar en una sola página: el documento existe igual, la cita cambia.
        merged = "\n\n".join(p["texto"] for p in paginas)
        paginas = [{"numero": 1, "texto": merged}]

    gold = {
        "orden_compra_id": row["codigo"],
        "tipo_documento": TIPO_DOC[proc],
        "procedencia": PROCEDENCIA[proc],
        "monto_total_clp": str(total_int),
        "rut_proveedor": row["proveedor_rut"],
        "unidad_compradora": row["unidad_compra"],
        "fecha_orden": fecha,
    }
    if neto is not None:
        gold["monto_neto_clp"] = str(neto)

    return {
        "id": f"gold-{idx:05d}",
        "documento_id": f"sha256:corpus-{row['codigo']}",
        "nota": f"Renderizado desde corpus ({proc})"
                + ("" if neto is not None else " · sin desglose de neto"),
        "paginas": paginas,
        "gold": gold,
    }


def reservoir_offsets(path: Path, n: int, rng: random.Random) -> list[dict]:
    """Read ``n`` random complete lines without scanning the whole file."""
    size = path.stat().st_size
    rows: list[dict] = []
    with path.open("rb") as fh:
        attempts = 0
        while len(rows) < n and attempts < n * 8:
            attempts += 1
            fh.seek(rng.randint(0, size - 65536))
            fh.readline()                     # discard the partial line
            line = fh.readline()
            if not line:
                continue
            try:
                rows.append(json.loads(line.decode("utf-8")))
            except Exception:
                continue
    return rows


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--corpus", type=Path, default=Path("out/compras_5y.jsonl"))
    p.add_argument("--out", type=Path, default=Path("benchmarks/extract_gold.jsonl"))
    p.add_argument("--n", type=int, default=800, help="documentos a generar (rango útil: 500-1000)")
    p.add_argument("--seed", type=int, default=20261030)
    args = p.parse_args(argv)

    rng = random.Random(args.seed)
    print(f"corpus: {args.corpus} ({args.corpus.stat().st_size/1e9:.1f} GB)")
    pool = reservoir_offsets(args.corpus, args.n * 6, rng)
    print(f"candidatos leídos: {len(pool)}")

    examples, vistos = [], set()
    for row in pool:
        if len(examples) >= args.n:
            break
        if row.get("codigo") in vistos:
            continue
        ex = build_example(row, len(examples) + 1, rng)
        if ex is None:
            continue
        vistos.add(row["codigo"])
        examples.append(ex)

    if len(examples) < args.n:
        print(f"AVISO: solo se pudieron construir {len(examples)} de {args.n}", file=sys.stderr)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for ex in examples:
            fh.write(json.dumps(ex, ensure_ascii=False) + "\n")

    # --- reporte: lo que hay que declarar junto al número ------------------------
    campos = Counter()
    for ex in examples:
        for k in ex["gold"]:
            campos[k] += 1
    procedencias = Counter(ex["gold"]["procedencia"] for ex in examples)
    tipos = Counter(ex["gold"]["tipo_documento"] for ex in examples)
    paginas = Counter(len(ex["paginas"]) for ex in examples)

    print(f"\nescritos: {len(examples)} → {args.out}")
    print("\n=== cobertura por campo (gold) ===")
    for k, v in campos.most_common():
        print(f"  {k:20} {v:5} ({v/len(examples)*100:5.1f}%)")
    print("\n=== procedencia ===")
    for k, v in procedencias.most_common():
        print(f"  {k:20} {v:5}")
    print("\n=== tipo de documento ===")
    for k, v in tipos.most_common():
        print(f"  {k:22} {v:5}")
    print(f"\n=== páginas === {dict(paginas)}")
    print("\nrecordar: documentos RENDERIZADOS, no escaneados. No mide OCR.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
