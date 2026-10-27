"""Mercado Público client — the two APIs behave differently and it matters.

v1 (purchase orders) takes the ticket as a **query parameter**, and its listing
endpoint returns only ``Codigo``, ``Nombre`` and ``CodigoEstado``. Getting the
buyer, supplier and amounts means one call per order: roughly 10.000 calls a day
at national volume, which no rate limit tolerates. So the historical series comes
from the open-data bulk downloads, and this client is for the deltas and for
detail lookups.

v2 (``Compras Ágiles``) takes the ticket as a **header**, and its detail endpoint
returns ``proveedores_cotizando`` — the list of who actually quoted. That field
is what makes the co-bidding network query possible without scraping a portal.

Rate limiting is built in because the honest failure mode of a scraper is not a
crash, it is a 429 halfway through a run that leaves you unsure what got ingested.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any

from ..domain.models import CompraAgil, OrdenCompra, Organismo, Proveedor
from ..domain.normalizers import name_key, normalize_rut, parse_clp, parse_date

API_V1 = "https://api.mercadopublico.cl/servicios/v1/publico"
API_V2 = "https://api2.mercadopublico.cl/v2"


class MercadoPublicoError(RuntimeError):
    """Raised when the API answers with an error we should not silently swallow."""


class MercadoPublicoClient:
    """Thin, rate-limited client over the two Mercado Público APIs."""

    def __init__(self, ticket: str, *, timeout_s: int = 30, min_interval_s: float = 1.0) -> None:
        if not ticket:
            raise ValueError("ticket is required")
        self._ticket = ticket
        self._timeout_s = timeout_s
        self._min_interval_s = min_interval_s
        self._last_call_at = 0.0

    # -- transport ---------------------------------------------------------
    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_call_at
        if elapsed < self._min_interval_s:
            time.sleep(self._min_interval_s - elapsed)
        self._last_call_at = time.monotonic()

    def _get(self, url: str, params: dict | None = None, headers: dict | None = None) -> Any:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(url, headers=headers or {})
        for attempt in range(4):
            self._throttle()
            try:
                with urllib.request.urlopen(request, timeout=self._timeout_s) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                if error.code != 429 or attempt == 3:
                    raise MercadoPublicoError(f"{url}: HTTP {error.code}") from error
                time.sleep(2**attempt)
            except urllib.error.URLError as error:
                raise MercadoPublicoError(f"{url}: {error.reason}") from error
        raise MercadoPublicoError(f"{url}: rate limited after retries")

    # -- v1: purchase orders ----------------------------------------------
    def listar_ordenes(self, fecha: date) -> list[dict]:
        """List the orders of one day. Returns only code, name and state."""
        payload = self._get(
            f"{API_V1}/ordenesdecompra.json",
            {"fecha": fecha.strftime("%d%m%Y"), "ticket": self._ticket},
        )
        return payload.get("Listado") or []

    def detalle_orden(self, codigo: str) -> dict:
        """Fetch the full record of one order — one request per order."""
        payload = self._get(
            f"{API_V1}/ordenesdecompra.json",
            {"codigo": codigo, "ticket": self._ticket},
        )
        listing = payload.get("Listado") or []
        if not listing:
            raise MercadoPublicoError(f"order {codigo} not found")
        return listing[0]

    # -- v2: compras ágiles -----------------------------------------------
    def compras_agiles(self, cambio_desde: str) -> list[dict]:
        """List agile purchases changed since a date (``YYYY-MM-DD``)."""
        payload = self._get(
            f"{API_V2}/compra-agil",
            {"cambio_desde": cambio_desde},
            {"ticket": self._ticket},
        )
        return ((payload.get("payload") or {}).get("items")) or []

    def detalle_compra_agil(self, codigo: str) -> dict:
        """Full agile-purchase record, including who quoted and the document ids."""
        payload = self._get(f"{API_V2}/compra-agil/{codigo}", headers={"ticket": self._ticket})
        return payload.get("payload") or {}


def _first(mapping: dict, *names: str, default: Any = None) -> Any:
    """Read the first key that exists. The API is not consistent between endpoints."""
    for name in names:
        if name in mapping and mapping[name] not in (None, ""):
            return mapping[name]
    return default


def orden_from_api(record: dict) -> OrdenCompra:
    """Map a v1 order detail payload onto the domain model."""
    buyer = record.get("Comprador") or {}
    supplier = record.get("Proveedor") or {}
    dates = record.get("Fechas") or {}

    rut = _first(supplier, "RutProveedor", "RutSucursal", "Rut")
    rut_norm = normalize_rut(rut)
    if rut_norm is None:
        raise MercadoPublicoError(f"order {record.get('Codigo')} has no usable supplier RUT")

    razon_social = _first(supplier, "NombreProveedor", "Nombre", default="")
    monto = parse_clp(_first(record, "Total", "TotalNeto"))
    fecha = parse_date(_first(dates, "FechaAceptacion", "FechaEnvio", "FechaCreacion"))
    if monto is None or fecha is None:
        raise MercadoPublicoError(f"order {record.get('Codigo')} has no usable amount or date")

    return OrdenCompra(
        codigo=str(_first(record, "Codigo", default="")),
        organismo=Organismo(
            codigo=str(_first(buyer, "RutUnidad", default="desconocido")),
            nombre=_first(buyer, "NombreOrganismo", default="Organismo sin nombre"),
            comuna=_first(buyer, "ComunaUnidad"),
            region=_first(buyer, "RegionUnidad"),
        ),
        proveedor=Proveedor(
            rut_norm=rut_norm,
            razon_social=razon_social,
            nombre_key=name_key(razon_social),
        ),
        monto_clp=monto,
        fecha=fecha,
        descripcion=_first(record, "Descripcion", "Nombre"),
        licitacion_codigo=_first(record, "CodigoLicitacion"),
        fecha_ultima_modificacion=parse_date(_first(dates, "FechaUltimaModificacion")),
        fecha_cancelacion=parse_date(_first(dates, "FechaCancelacion")),
    )


def compra_agil_from_api(record: dict) -> CompraAgil:
    """Map a v2 agile-purchase detail payload onto the domain model."""
    buyer = record.get("institucion") or {}
    dates = record.get("fechas") or {}
    amounts = record.get("montos") or {}
    quoting = record.get("proveedores_cotizando") or []

    quoted_ruts = []
    for candidate in quoting:
        rut = candidate.get("rut") if isinstance(candidate, dict) else candidate
        normalized = normalize_rut(rut)
        if normalized:
            quoted_ruts.append(normalized)

    return CompraAgil(
        codigo=str(record.get("codigo", "")),
        organismo=Organismo(
            codigo=str(_first(buyer, "rut", default="desconocido")),
            nombre=_first(buyer, "organismo_comprador", default="Organismo sin nombre"),
            comuna=_first(buyer, "unidad_compra"),
            region=_first(buyer, "nombre_region"),
        ),
        monto_clp=parse_clp(_first(amounts, "monto_disponible_clp", "monto_disponible")),
        fecha_publicacion=parse_date(_first(dates, "fecha_publicacion")),
        proveedores_cotizando=tuple(quoted_ruts),
        id_orden_compra=_first(record, "id_orden_compra"),
        documentos=tuple(record.get("documentos") or ()),
    )
