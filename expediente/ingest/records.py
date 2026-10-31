"""Load order records from a local JSON file.

Used by the CLI demo and by tests, so the deterministic core can be exercised
without a network. The record shape is deliberately the same one the API client
produces, so nothing downstream can tell the difference.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..domain.models import OrdenCompra, Organismo, Proveedor
from ..evidence.models import Claim, Document, Evidence
from ..resolution.models import SupplierRecord
from ..domain.normalizers import name_key, normalize_rut, parse_clp, parse_date


class RecordError(ValueError):
    """Raised when a record cannot be turned into a domain object."""


def load_orders(path: str | Path) -> list[OrdenCompra]:
    """Read a JSON array of order records and return domain objects."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise RecordError(f"{path}: expected a JSON array of order records")
    return [order_from_dict(item, index) for index, item in enumerate(raw)]


def order_from_dict(item: dict[str, Any], index: int = 0) -> OrdenCompra:
    """Map one JSON record onto an :class:`OrdenCompra`.

    Raises instead of guessing: a record without a supplier RUT, an amount or a
    date cannot support evidence, and silently defaulting those is how a dossier
    ends up citing something that does not exist.
    """
    where = f"record {index} ({item.get('codigo', 'no code')})"
    codigo = item.get("codigo")
    if not codigo:
        raise RecordError(f"{where}: missing codigo")

    rut_norm = normalize_rut((item.get("proveedor") or {}).get("rut"))
    if rut_norm is None:
        raise RecordError(f"{where}: missing or unusable supplier RUT")

    monto = parse_clp(item.get("monto_clp"))
    if monto is None:
        raise RecordError(f"{where}: missing or unusable monto_clp")

    fecha = parse_date(item.get("fecha"))
    if fecha is None:
        raise RecordError(f"{where}: missing or unusable fecha")

    organismo_raw = item.get("organismo") or {}
    proveedor_raw = item.get("proveedor") or {}
    razon_social = proveedor_raw.get("razon_social") or ""

    return OrdenCompra(
        codigo=str(codigo),
        organismo=Organismo(
            codigo=str(organismo_raw.get("codigo") or "desconocido"),
            nombre=organismo_raw.get("nombre") or "Organismo sin nombre",
            comuna=organismo_raw.get("comuna"),
            region=organismo_raw.get("region"),
        ),
        proveedor=Proveedor(
            rut_norm=rut_norm,
            razon_social=razon_social,
            nombre_key=name_key(razon_social),
        ),
        monto_clp=monto,
        fecha=fecha,
        descripcion=item.get("descripcion"),
        categoria_unspsc=item.get("categoria_unspsc"),
        licitacion_codigo=item.get("licitacion_codigo"),
        fecha_ultima_modificacion=parse_date(item.get("fecha_ultima_modificacion")),
        fecha_cancelacion=parse_date(item.get("fecha_cancelacion")),
        # El mecanismo de adjudicación decide qué umbral aplica: un loader que lo descarta deja
        # al detector sin régimen y el registro se ve igual. Se copia, con o sin tilde.
        procedencia=item.get("procedencia"),
        items=tuple(item.get("items") or ()),
    )


def load_dossier(path: str | Path) -> tuple[list[Claim], list[Evidence], list[Document]]:
    """Read a dossier fixture: claims, the evidence they cite, and the documents.

    Used by the CLI and by tests. Shape errors raise, because a dossier that is
    silently half-loaded would report a citation rate over the wrong denominator.
    """
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise RecordError(f"{path}: expected an object with claims, evidence and documents")

    claims = [
        Claim(
            id=str(item["id"]),
            texto=str(item.get("texto", "")),
            evidence_ids=tuple(str(ref) for ref in item.get("evidence", ())),
        )
        for item in raw.get("claims", ())
    ]
    evidence = [
        Evidence(
            id=str(item["id"]),
            document_id=item.get("document_id"),
            pagina=item.get("pagina"),
            extracto=str(item.get("extracto", "")),
            extractor=str(item.get("extractor", "text-layer")),
        )
        for item in raw.get("evidence", ())
    ]
    documents = [
        Document(
            id=str(item["id"]),
            url=str(item.get("url", "")),
            tipo=str(item.get("tipo", "pdf")),
            paginas=item.get("paginas"),
        )
        for item in raw.get("documents", ())
    ]
    return claims, evidence, documents


def load_suppliers(path: str | Path) -> list[SupplierRecord]:
    """Read supplier records: RUT, name, address and legal representative."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise RecordError(f"{path}: expected a JSON array of supplier records")
    return [
        SupplierRecord(
            rut=str(item.get("rut", "")),
            razon_social=str(item.get("razon_social", "")),
            direccion=item.get("direccion"),
            comuna=item.get("comuna"),
            latitud=item.get("latitud"),
            longitud=item.get("longitud"),
            representante_rut=item.get("representante_rut"),
            representante_nombre=item.get("representante_nombre"),
            documento_id=item.get("documento_id"),
            pagina=item.get("pagina"),
        )
        for item in raw
    ]
