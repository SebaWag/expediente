"""Streaming reader for the ChileCompra bulk archives.

The archives are item-level: one row per line item, with the order's total repeated
on every row. A single order in the sample appears five times, four of them
identical apart from the item. Summing ``MontoTotalOC`` over rows therefore inflates
a buyer's spend by the number of items — an error that produces a dossier with
figures that are wrong and completely believable.

So the rule here is: **aggregate by order code before anything measures anything.**

The second constraint is size. One 81 MB archive expands to 4.66 GB; the full
twelve-semester set is ~2 GB compressed and would be ~114 GB on disk. Nothing is
ever extracted: ``7z`` writes to a pipe, Python reads it, and only the aggregated
order-level record survives.
"""

from __future__ import annotations

import csv
import io
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

#: Canonical field -> the header names it has appeared under. The bulk files are
#: Latin-1, semicolon-separated, and the column set has drifted across semesters;
#: naming both sides here is cheaper than discovering the drift in a dossier.
ALIASES: dict[str, tuple[str, ...]] = {
    "codigo": ("codigoOC", "CodigoOC", "codigo"),
    "fecha": ("FechaEnvioOC", "FechaEnvio", "fecha"),
    "nombre": ("NombreOC", "Nombre", "nombre"),
    "descripcion": ("DescripcionOC", "Descripcion", "descripcion"),
    "estado": ("EstadoOC", "Estado", "estado"),
    "procedencia": ("ProcedenciaOC", "Procedencia", "procedencia"),
    "moneda": ("MonedaOC", "Moneda", "moneda"),
    "monto_total": ("MontoTotalOC", "MontoTotal", "monto_total"),
    "monto_neto_clp": ("MontoNetoOC_CLP", "MontoNetoCLP"),
    #: Impuestos ya convertidos a pesos. Juntos con el neto en CLP reconstruyen el total de
    #: una orden emitida en otra moneda, sin necesidad de traer una serie de tipo de cambio.
    "impuestos_clp": ("ImpuestosOC_CLP", "ImpuestosCLP"),
    "unidad": ("UnidadCompra", "unidad"),
    "organismo_rut": ("UnidadCompraRUT", "RutUnidad", "organismo_rut"),
    "region_compra": ("RegionUnidadCompra", "Region", "region_compra"),
    "entcode": ("entCode", "EntCode"),
    "institucion": ("Institucion", "institucion"),
    "sector": ("Sector", "sector"),
    "proveedor": ("Proveedor", "proveedor"),
    "proveedor_rut": ("ProveedorRUT", "RutProveedor", "proveedor_rut"),
    "proveedor_tamano": ("TamanoProveedor", "TamañoProveedor"),
    "region_proveedor": ("RegionProveedor", "region_proveedor"),
    "rubro": ("RubroN1", "rubro"),
    "producto_onu": ("ONUProducto", "producto_onu"),
    "item_descripcion": ("DescripcionItem", "item_descripcion"),
    "item_monto_clp": ("MontoTotalItem", "MontoNetoItemCLP"),
    # Compra Ágil carries its own process alongside the order: the quotation it came from
    # and every supplier that quoted on it. Reading the order but not these fields produces
    # a dataset of purchases that cannot be related to each other, which is the opposite of
    # what this ingestion is for.
    "cotizacion": ("CodigoCotizacion", "codigoCotizacion"),
    "cotizacion_publicada": ("FechaPublicacionCotizacion",),
    "cotizacion_cierre": ("FechaCierreCotizacion",),
    "cotizante_rut": ("RUTProveedorCotizacion", "RutProveedorCotizacion"),
    "cotizante": ("ProveedorCotizacion",),
    "unidad_compra": ("UnidadCompra",),
}

MEMBER_HINTS = {
    "trato_directo": "TratoDirecto",
    "convenio_marco": "ConvenioMarco",
    "licitacion": "OCLicitacion",
    "compra_agil": "CompraAgil",
}


@dataclass
class OrderAggregate:
    """One purchase order, rebuilt from its item rows."""

    codigo: str
    fecha: str = ""
    nombre: str = ""
    descripcion: str = ""
    estado: str = ""
    procedencia: str = ""
    moneda: str = ""
    monto_total_clp: float = 0.0
    #: Cuando la orden se emitió en otra moneda, ``monto_total_clp`` es la conversión que el
    #: propio archivo trae (neto + impuestos en CLP) y estos dos campos guardan lo original.
    #: Sin esto, una orden de 1.200 UF se leería como "$1.200": 38.000 veces menos, por
    #: debajo de cualquier piso, invisible para todos los detectores.
    moneda_original: str = ""
    monto_original: float = 0.0
    organismo_rut: str = ""
    organismo: str = ""
    region_compra: str = ""
    sector: str = ""
    proveedor: str = ""
    proveedor_rut: str = ""
    proveedor_tamano: str = ""
    region_proveedor: str = ""
    rubro: str = ""
    items: int = 0
    monto_inconsistente: bool = False
    #: Unidad compradora, que en Compra Ágil es más fina que la institución.
    unidad_compra: str = ""
    #: El proceso de Compra Ágil del que salió esta orden, y quiénes cotizaron en él.
    #: ``cotizantes`` va como dict rut -> nombre porque el mismo proveedor repite una fila
    #: por ítem, y una lista con el mismo RUT diecisiete veces no es una red de cotizantes.
    cotizacion: str = ""
    cotizacion_publicada: str = ""
    cotizacion_cierre: str = ""
    cotizantes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        payload = self.__dict__.copy()
        payload["monto_total_clp"] = round(self.monto_total_clp)
        payload["cotizantes"] = [
            {"rut": rut, "nombre": nombre}
            for rut, nombre in sorted(self.cotizantes.items())
        ]
        payload["n_cotizantes"] = len(payload["cotizantes"])
        return payload


@dataclass
class AggregateStats:
    files: int = 0
    rows: int = 0
    orders: int = 0
    inconsistent: int = 0
    nul_bytes: int = 0
    by_member: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "files": self.files,
            "rows": self.rows,
            "orders": self.orders,
            "inconsistent_amounts": self.inconsistent,
            "nul_bytes_removed": self.nul_bytes,
            "by_member": dict(sorted(self.by_member.items())),
        }


#: The decompressor is configurable because it is not interchangeable. p7zip 16.02
#: (Ubuntu 22.04) emits NUL bytes on some of these archives on arm64 and Python's
#: csv module then aborts with ``line contains NUL``; 7-Zip 26.04 decodes the same
#: file cleanly. Pinning the binary in the environment makes the ingestion
#: reproducible across machines instead of silently depending on the host.
SEVEN_ZIP = os.environ.get("EXPEDIENTE_7Z", "7z")

def members(archive: Path) -> list[str]:
    """List the CSV members of an archive without extracting anything."""
    result = subprocess.run([SEVEN_ZIP, "l", "-ba", str(archive)], capture_output=True, text=True)
    found = []
    for line in result.stdout.splitlines():
        name = line.split()[-1] if line.split() else ""
        if name.lower().endswith(".csv"):
            found.append(name)
    return found


def _text_lines(raw, stats: "AggregateStats | None" = None) -> Iterator[str]:
    """Decode one line at a time, dropping the NUL bytes the source actually contains.

    The ChileCompra exports carry empty fields encoded as ``;\x00;`` — 1,977 of them in
    a single 12 MB member. What to do with them is not a style question: Python 3.12+
    lets them through and Python 3.10 raises ``line contains NUL``, so leaving them in
    makes the ingestion depend on the interpreter version rather than on the data.
    They are removed before decoding, which turns ``;\x00;`` into an empty field and
    preserves the column alignment. The count goes into the stats, so the removal is
    visible in the output instead of being a silent repair.
    """
    for line in raw:
        if b"\x00" in line:
            if stats is not None:
                stats.nul_bytes += line.count(b"\x00")
            line = line.replace(b"\x00", b"")
        yield _decode(line)


def _decode(line: bytes) -> str:
    """Decode one CSV line, honouring the encoding the publisher actually used.

    ChileCompra's bulk archives are **not** consistently encoded. Most members are
    Latin-1; some are UTF-8, and decoding one of those as Latin-1 turns every accented
    character into mojibake — "CIRUGÍA" becomes "CIRUGÃA". Reading everything as
    Latin-1 put 366 descriptions and 201 names into the production graph that way,
    measured on 2026-10-29 out of 4.9 M records.

    So: UTF-8 first, because it fails loudly on non-UTF-8 bytes instead of silently
    producing the wrong text, and Latin-1 as the fallback, because Latin-1 decodes any
    byte sequence. The order matters — the reverse (which is what this used to do)
    never fails and is therefore always wrong for the UTF-8 members.
    """
    try:
        return line.decode("utf-8")
    except UnicodeDecodeError:
        return line.decode("latin-1")


def iter_rows(
    archive: Path, member: str, stats: "AggregateStats | None" = None
) -> Iterator[dict[str, str]]:
    """Yield aggregated columns for every row of one CSV inside an archive.

    ``7z e -so`` writes the decompressed member to stdout; nothing touches disk.
    Latin-1 is not an assumption — it is what the files actually are, and decoding
    as UTF-8 raises on the first accented municipality name.
    """
    process = subprocess.Popen(
        [SEVEN_ZIP, "e", "-so", str(archive), member],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    assert process.stdout is not None
    try:
        reader = csv.DictReader(_text_lines(process.stdout, stats), delimiter=";")
        try:
            for row in reader:
                yield _canonical(row)
        except csv.Error as exc:
            raise ValueError(
                f"unreadable CSV in {archive.name} :: {member} ({exc}). "
                f"Decompressor in use: {SEVEN_ZIP}."
            ) from exc
    finally:
        process.stdout.close()
        process.wait()


def _canonical(row: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for canonical, candidates in ALIASES.items():
        for candidate in candidates:
            if candidate in row and row[candidate] not in (None, ""):
                out[canonical] = row[candidate].strip()
                break
    return out


def aggregate(archive: Path, member: str, stats: AggregateStats) -> dict[str, OrderAggregate]:
    """Aggregate one member into order-level records, keyed by order code."""
    orders: dict[str, OrderAggregate] = {}

    for row in iter_rows(archive, member, stats):
        stats.rows += 1
        code = row.get("codigo", "")
        if not code:
            continue

        current = orders.get(code)
        if current is None:
            current = OrderAggregate(
                codigo=code,
                fecha=_fecha(row.get("fecha")),
                nombre=row.get("nombre", "")[:300],
                descripcion=row.get("descripcion", "")[:500],
                estado=row.get("estado", ""),
                procedencia=row.get("procedencia", ""),
                moneda=row.get("moneda", ""),
                monto_total_clp=_total_en_clp(row),
                organismo_rut=row.get("organismo_rut", ""),
                organismo=row.get("institucion", "")[:200],
                region_compra=row.get("region_compra", ""),
                sector=row.get("sector", ""),
                proveedor=row.get("proveedor", "")[:200],
                proveedor_rut=row.get("proveedor_rut", ""),
                proveedor_tamano=row.get("proveedor_tamano", ""),
                region_proveedor=row.get("region_proveedor", ""),
                rubro=row.get("rubro", "")[:120],
                unidad_compra=row.get("unidad_compra", "")[:200],
                cotizacion=row.get("cotizacion", ""),
                cotizacion_publicada=_fecha(row.get("cotizacion_publicada")),
                cotizacion_cierre=_fecha(row.get("cotizacion_cierre")),
            )
            orders[code] = current
        elif not current.monto_inconsistente:
            # La comparación tiene que usar la misma función que el alta. Con
            # ``_amount(monto_total)`` una orden en USD nunca coincidía con su propio total
            # convertido y quedaba marcada como inconsistente: 20.820 falsos positivos en una
            # corrida, todos órdenes en moneda extranjera con más de un ítem.
            repeated = _total_en_clp(row)
            if repeated and current.monto_total_clp and abs(repeated - current.monto_total_clp) > 1:
                # the same order reporting two different totals is a data-quality
                # finding, not something to average away silently
                current.monto_inconsistente = True
                stats.inconsistent += 1

        if current.moneda_original == "" and (row.get("moneda") or "").upper() not in ("", "CLP"):
            current.moneda_original = row.get("moneda", "")
            current.monto_original = _amount(row.get("monto_total"))

        current.items += 1
        cotizante_rut = row.get("cotizante_rut", "")
        if cotizante_rut:
            current.cotizantes.setdefault(cotizante_rut, row.get("cotizante", "")[:200])

    stats.files += 1
    stats.orders += len(orders)
    stats.by_member[member] = stats.by_member.get(member, 0) + len(orders)
    return orders


def _fecha(value: str | None) -> str:
    """Coerce a published date to ISO ``YYYY-MM-DD``, or keep it verbatim if it will not.

    The archives write ``dd-mm-yyyy 0:00:00`` in some members and ``yyyy-mm-dd`` in others.
    Storing both in one column makes a third of the corpus look undated to every consumer
    that expects one format, and an unreadable date reads exactly like an absent one. The
    normalisers downstream already understand ISO; nothing else here should have to guess.
    """
    from ..domain.normalizers import parse_date

    texto = (value or "").strip()
    parsed = parse_date(texto)
    return parsed.isoformat() if parsed else texto


def _total_en_clp(row: dict[str, str]) -> float:
    """The order total in pesos, converting when the publisher already did.

    ``MontoTotalOC`` is in the order's own currency. For the 1.7% of orders issued in USD,
    UF, EUR or UTM that number is not pesos, and treating it as pesos is how a UF purchase
    lands 38,000 times below its real value and under every window without a warning. The
    same row carries ``MontoNetoOC_CLP`` and ``ImpuestosOC_CLP``; their sum is the total in
    pesos at the rate ChileCompra itself applied. No exchange series is invented here and
    none is needed.
    """
    total = _amount(row.get("monto_total"))
    moneda = (row.get("moneda") or "").strip().upper()
    if moneda and moneda != "CLP":
        neto_clp = _amount(row.get("monto_neto_clp"))
        if neto_clp:
            return neto_clp + _amount(row.get("impuestos_clp"))
    return total


def _amount(value: str | None) -> float:
    if value is None:
        return 0.0
    try:
        return float(value.replace(",", "."))
    except ValueError:
        return 0.0


def write_jsonl(orders: dict[str, OrderAggregate], destination: Path) -> int:
    """Append aggregated orders to a JSONL file. Returns how many were written."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8") as handle:
        for code in sorted(orders):
            handle.write(json.dumps(orders[code].to_dict(), ensure_ascii=False) + "\n")
    return len(orders)
