#!/usr/bin/env python3
"""Export order-level JSONL to NTriples, streamed, chunked and compressed.

Usage:
    python3 scripts/export_ntriples.py out/orders_5y.jsonl out/nt
    python3 scripts/export_ntriples.py <in.jsonl> <outdir> --limit 200000 --chunk-triples 2000000

Why streaming and not "build it in memory": the full set is 4.95 M orders and roughly
nine triples each. Materialising that as Python objects costs multiples of the file
size, and the machine that produces it is the one with the least RAM. Here every
order is written and forgotten; only two sets survive — the organismos and the
proveedores already seen — and those are bounded by the number of distinct entities,
not by the number of orders.

Why the source needs cleaning before it becomes a literal: the ChileCompra exports
carry NUL bytes and stray control characters (5,071,739 of them across the corpus).
NTriples has no escape for NUL, so a literal that contains one produces a file that
a strict parser rejects — the same way Python's csv module rejects it. Control
characters are dropped here, at the boundary, and counted, so the loss is visible
in the manifest instead of silently corrupting a load.

The vocabulary is imported from ``expediente.graph.schema``, which is the single
source of truth: if a node or edge is renamed there, the export follows.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expediente.graph.schema import EDGES, NODES, ONTO, NODE_PREFIX, RDF_TYPE, REL_PREFIX, XSD

#: Everything below 0x20 is illegal in an NTriples literal except tab, and the source
#: uses them for padding. They are dropped, not escaped, because there is no escape.
_ILLEGAL = {c: None for c in range(0x00, 0x20) if c not in (0x09,)}


def node_uri(label: str, key: str) -> str:
    if label not in NODES:
        raise KeyError(f"unknown node label: {label}")
    return f"{NODE_PREFIX}{label}/{urllib.parse.quote(str(key), safe='')}"


def relation_uri(relation: str) -> str:
    if relation not in EDGES:
        raise KeyError(f"unknown edge: {relation}")
    return f"{REL_PREFIX}{relation}"


def literal(value: object) -> str:
    """Render a Python value as an NTriples literal, dropping illegal characters."""
    if isinstance(value, bool):
        return f'"{"true" if value else "false"}"^^<{XSD}boolean>'
    if isinstance(value, int):
        return f'"{value}"^^<{XSD}integer>'
    text = str(value).translate(_ILLEGAL)
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{text}"'


@dataclass
class ExportStats:
    orders: int = 0
    triples: int = 0
    organismos: int = 0
    proveedores: int = 0
    skipped_no_code: int = 0
    skipped_no_org: int = 0
    skipped_no_supplier: int = 0
    compras_agiles: int = 0
    cotizantes: int = 0
    chunks: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "orders": self.orders,
            "triples": self.triples,
            "organismos": self.organismos,
            "proveedores": self.proveedores,
            "skipped": {
                "no_code": self.skipped_no_code,
                "no_organismo": self.skipped_no_org,
                "no_proveedor": self.skipped_no_supplier,
            },
            "chunks": self.chunks,
            "triples_per_order": round(self.triples / self.orders, 2) if self.orders else 0,
        }


class ChunkWriter:
    """Writes NTriples into fixed-size chunks, each compressed with zstd.

    The compressor is a subprocess rather than a Python library so the chunk size is
    bounded by triples and not by memory: text goes down the pipe as it is produced.
    """

    def __init__(self, outdir: Path, chunk_triples: int, level: int = 3) -> None:
        self.outdir = outdir
        self.chunk_triples = chunk_triples
        self.level = level
        self.outdir.mkdir(parents=True, exist_ok=True)
        self._index = 0
        self._count = 0
        self._hash = hashlib.sha256()
        self._raw_bytes = 0
        self._proc: subprocess.Popen | None = None
        self._path: Path | None = None
        self.chunks: list[dict] = []
        self._open()

    def _open(self) -> None:
        self._index += 1
        self._path = self.outdir / f"orders-{self._index:04d}.nt.zst"
        self._proc = subprocess.Popen(
            ["zstd", f"-{self.level}", "-T2", "-q", "-o", str(self._path)],
            stdin=subprocess.PIPE,
        )
        self._count = 0
        self._hash = hashlib.sha256()
        self._raw_bytes = 0

    def write(self, line: str) -> None:
        if self._proc is None:  # the previous chunk closed itself at the size limit
            self._open()
        assert self._proc is not None and self._proc.stdin is not None
        data = line.encode("utf-8")
        self._proc.stdin.write(data)
        self._hash.update(data)
        self._raw_bytes += len(data)
        self._count += 1
        if self._count >= self.chunk_triples:
            self.close_chunk()

    def close_chunk(self) -> None:
        if self._proc is None or self._path is None:
            return
        assert self._proc.stdin is not None
        self._proc.stdin.close()
        self._proc.wait()
        size = self._path.stat().st_size
        self.chunks.append(
            {
                "file": self._path.name,
                "triples": self._count,
                "raw_bytes": self._raw_bytes,
                "compressed_bytes": size,
                "sha256_raw": self._hash.hexdigest(),
            }
        )
        self._proc = None


def orders(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 1

    source = Path(argv[1]).expanduser()
    outdir = Path(argv[2]).expanduser()
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    chunk_triples = (
        int(argv[argv.index("--chunk-triples") + 1]) if "--chunk-triples" in argv else 5_000_000
    )
    level = int(argv[argv.index("--level") + 1]) if "--level" in argv else 3

    writer = ChunkWriter(outdir, chunk_triples, level)
    stats = ExportStats()
    seen_org: set[str] = set()
    seen_sup: set[str] = set()
    seen_agil: set[str] = set()
    started = time.monotonic()

    def emit(line: str) -> None:
        writer.write(line)
        stats.triples += 1

    for record in orders(source):
        if limit is not None and stats.orders >= limit:
            break

        code = (record.get("codigo") or "").strip()
        if not code:
            stats.skipped_no_code += 1
            continue

        org_rut = (record.get("organismo_rut") or "").strip()
        sup_rut = (record.get("proveedor_rut") or "").strip()
        order_uri = node_uri("OrdenCompra", code)

        # --- the order itself
        emit(f"<{order_uri}> <{RDF_TYPE}> <{ONTO}OrdenCompra> .\n")
        emit(f"<{order_uri}> <{ONTO}monto_clp> {literal(int(record.get('monto_total_clp') or 0))} .\n")
        for prop in ("fecha", "estado", "procedencia", "moneda", "sector", "region_compra"):
            value = record.get(prop)
            if value:
                emit(f"<{order_uri}> <{ONTO}{prop}> {literal(value)} .\n")
        if record.get("nombre"):
            emit(f"<{order_uri}> <{ONTO}nombre> {literal(record['nombre'][:300])} .\n")
        if record.get("descripcion"):
            emit(f"<{order_uri}> <{ONTO}descripcion> {literal(record['descripcion'][:500])} .\n")
        if record.get("items"):
            emit(f"<{order_uri}> <{ONTO}items> {literal(int(record['items']))} .\n")
        if record.get("monto_inconsistente"):
            emit(f"<{order_uri}> <{ONTO}monto_inconsistente> {literal(True)} .\n")

        # --- the buyer (emitted once per distinct RUT)
        if org_rut:
            org_uri = node_uri("Organismo", org_rut)
            if org_rut not in seen_org:
                seen_org.add(org_rut)
                stats.organismos += 1
                emit(f"<{org_uri}> <{RDF_TYPE}> <{ONTO}Organismo> .\n")
                emit(f"<{org_uri}> <{ONTO}rut_norm> {literal(org_rut)} .\n")
                if record.get("organismo"):
                    emit(f"<{org_uri}> <{ONTO}nombre> {literal(record['organismo'])} .\n")
                if record.get("region_compra"):
                    emit(f"<{org_uri}> <{ONTO}region> {literal(record['region_compra'])} .\n")
            emit(
                f"<{org_uri}> <{relation_uri('EMITE')}> <{order_uri}> .\n"
            )
        else:
            stats.skipped_no_org += 1

        # --- the supplier
        if sup_rut:
            sup_uri = node_uri("Proveedor", sup_rut)
            if sup_rut not in seen_sup:
                seen_sup.add(sup_rut)
                stats.proveedores += 1
                emit(f"<{sup_uri}> <{RDF_TYPE}> <{ONTO}Proveedor> .\n")
                emit(f"<{sup_uri}> <{ONTO}rut_norm> {literal(sup_rut)} .\n")
                if record.get("proveedor"):
                    emit(f"<{sup_uri}> <{ONTO}razon_social> {literal(record['proveedor'])} .\n")
                if record.get("proveedor_tamano"):
                    emit(f"<{sup_uri}> <{ONTO}tamano> {literal(record['proveedor_tamano'])} .\n")
                if record.get("region_proveedor"):
                    emit(f"<{sup_uri}> <{ONTO}region> {literal(record['region_proveedor'])} .\n")
            emit(f"<{order_uri}> <{relation_uri('ADJUDICADA_A')}> <{sup_uri}> .\n")
        else:
            stats.skipped_no_supplier += 1

        # --- the Compra Ágil process behind this order, and everyone who quoted on it
        cotizacion = (record.get("cotizacion") or "").strip()
        if cotizacion:
            agil_uri = node_uri("CompraAgil", cotizacion)
            if cotizacion not in seen_agil:
                seen_agil.add(cotizacion)
                stats.compras_agiles += 1
                emit(f"<{agil_uri}> <{RDF_TYPE}> <{ONTO}CompraAgil> .\n")
                emit(f"<{agil_uri}> <{ONTO}codigo> {literal(cotizacion)} .\n")
                for prop in ("cotizacion_publicada", "cotizacion_cierre"):
                    if record.get(prop):
                        emit(f"<{agil_uri}> <{ONTO}{prop}> {literal(record[prop])} .\n")
                if record.get("unidad_compra"):
                    emit(f"<{agil_uri}> <{ONTO}unidad_compra> {literal(record['unidad_compra'])} .\n")
                if org_rut:
                    emit(
                        f"<{node_uri('Organismo', org_rut)}> <{relation_uri('PUBLICA')}> "
                        f"<{agil_uri}> .\n"
                    )
            emit(f"<{agil_uri}> <{relation_uri('GENERA')}> <{order_uri}> .\n")
            for cotizante in record.get("cotizantes") or ():
                rut_cot = (cotizante.get("rut") or "").strip()
                if not rut_cot:
                    continue
                cot_uri = node_uri("Proveedor", rut_cot)
                if rut_cot not in seen_sup:
                    seen_sup.add(rut_cot)
                    stats.proveedores += 1
                    emit(f"<{cot_uri}> <{RDF_TYPE}> <{ONTO}Proveedor> .\n")
                    emit(f"<{cot_uri}> <{ONTO}rut_norm> {literal(rut_cot)} .\n")
                    if cotizante.get("nombre"):
                        emit(
                            f"<{cot_uri}> <{ONTO}razon_social> "
                            f"{literal(cotizante['nombre'])} .\n"
                        )
                stats.cotizantes += 1
                emit(f"<{cot_uri}> <{relation_uri('COTIZA_EN')}> <{agil_uri}> .\n")

        stats.orders += 1
        if stats.orders % 500_000 == 0:
            elapsed = time.monotonic() - started
            print(
                f"  {stats.orders:,} órdenes · {stats.triples:,} tripletas · "
                f"{elapsed:.0f}s · {stats.orders / elapsed:,.0f} órdenes/s",
                flush=True,
            )

    writer.close_chunk()
    stats.chunks = writer.chunks
    elapsed = time.monotonic() - started

    manifest = {
        "source": str(source),
        "generated_by": "scripts/export_ntriples.py",
        "elapsed_seconds": round(elapsed, 1),
        "zstd_level": level,
        **stats.to_dict(),
    }
    (outdir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    raw = sum(c["raw_bytes"] for c in writer.chunks)
    comp = sum(c["compressed_bytes"] for c in writer.chunks)
    print()
    print(f"total: {stats.orders:,} órdenes → {stats.triples:,} tripletas en {elapsed:.0f}s")
    print(f"organismos: {stats.organismos:,} · proveedores: {stats.proveedores:,}")
    print(f"compras ágiles: {stats.compras_agiles:,} · cotizaciones emitidas: {stats.cotizantes:,}")
    print(f"chunks: {len(writer.chunks)} · crudo {raw / 1e9:.2f} GB · zstd {comp / 1e9:.2f} GB "
          f"({raw / comp if comp else 0:.1f}×)")
    print(f"manifest: {outdir / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
