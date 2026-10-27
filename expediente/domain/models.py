"""Domain models.

Deliberately plain: frozen dataclasses, no ORM, no framework. The same objects
are produced by the API client, by the bulk loaders and by tests, so a detector
never knows where a record came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True, slots=True)
class Organismo:
    """A buyer unit. In Chile this is a municipality, service or hospital unit."""

    codigo: str
    nombre: str
    comuna: str | None = None
    region: str | None = None
    tipo: str | None = None


@dataclass(frozen=True, slots=True)
class Proveedor:
    """A supplier, keyed by its normalised RUT."""

    rut_norm: str
    razon_social: str
    nombre_key: str = ""
    giro: str | None = None


@dataclass(frozen=True, slots=True)
class Licitacion:
    """A public tender."""

    codigo: str
    titulo: str | None = None
    tipo: str | None = None
    monto_estimado_clp: int | None = None
    fecha_publicacion: date | None = None


@dataclass(frozen=True, slots=True)
class OrdenCompra:
    """A purchase order — the atom of the evidence chain.

    ``fecha_ultima_modificacion`` and ``fecha_cancelacion`` are carried from the
    first day because detector 5 (post-award modification) reads them and no
    second source should be needed for it.
    """

    codigo: str
    organismo: Organismo
    proveedor: Proveedor
    monto_clp: int
    fecha: date
    descripcion: str | None = None
    categoria_unspsc: str | None = None
    licitacion_codigo: str | None = None
    fecha_ultima_modificacion: date | None = None
    fecha_cancelacion: date | None = None
    #: How the order was awarded. Not decoration: the splitting rule is about avoiding
    #: a tender, and a Convenio Marco purchase is not subject to one. Detecting on the
    #: wrong population produced 1,508 "findings" of which the top one was an airline's
    #: reservations.
    procedencia: str | None = None
    items: tuple[dict, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class CompraAgil:
    """A ``Compra Ágil`` — the low-value fast lane, where splitting usually lives.

    ``proveedores_cotizando`` comes straight from the v2 detail endpoint and is
    what makes the co-bidding network query possible without scraping.
    """

    codigo: str
    organismo: Organismo
    monto_clp: int | None
    fecha_publicacion: date | None
    proveedores_cotizando: tuple[str, ...] = ()
    id_orden_compra: str | None = None
    documentos: tuple[dict, ...] = field(default=())
