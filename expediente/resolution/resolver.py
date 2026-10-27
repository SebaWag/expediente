"""Deterministic entity resolution.

The question this answers is not "are these the same company" — almost always they
are not. It is "**which suppliers share a person or an address**", because that is
the relation an auditor can act on and the one a spreadsheet cannot show.

Two signals, both exact matches:

- **shared legal representative** — the same person RUT represents two suppliers.
- **shared address** — two suppliers block to the same grid cell (~110 m) when
  coordinates are known, or to the same normalised address string when they are not.

Anything weaker becomes a *candidate*: same normalised name, different RUT, no
shared person or address. A naive system merges those two companies and creates a
false accusation. This one writes the question down and hands it to a human.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from ..domain.normalizers import address_key, geo_bucket, name_key, normalize_rut
from ..graph.store import GraphStore, NodeKey
from .models import (
    Candidate,
    Rejected,
    Rejection,
    Relation,
    RelationKind,
    ResolutionResult,
    SourceRef,
    SupplierRecord,
)


@dataclass(frozen=True, slots=True)
class _Normalized:
    """A record after normalisation. Internal: callers should not depend on it."""

    rut: str
    nombre_key: str
    representante_rut: str | None
    address_block: str | None
    address_display: str
    record: SupplierRecord


def resolve_suppliers(records: Iterable[SupplierRecord]) -> ResolutionResult:
    """Resolve a batch of supplier records into relations, candidates and rejections."""
    normalized, rejected = _normalize(records)
    relations = _representative_relations(normalized) + _address_relations(normalized)
    linked = _pairs_from(relations)
    candidates = _name_candidates(normalized, linked)

    return ResolutionResult(
        relations=tuple(sorted(relations, key=lambda r: (r.kind.value, r.anchor))),
        candidates=tuple(sorted(candidates, key=lambda c: c.nombre_key)),
        rejected=tuple(sorted(rejected, key=lambda r: r.rut)),
    )


def _normalize(records: Iterable[SupplierRecord]) -> tuple[list[_Normalized], list[Rejected]]:
    normalized: list[_Normalized] = []
    rejected: list[Rejected] = []

    for record in records:
        rut = normalize_rut(record.rut)
        if rut is None:
            rejected.append(Rejected(rut=str(record.rut), reason=Rejection.RUT_INUSABLE))
            continue

        representante = normalize_rut(record.representante_rut)
        block = _address_block(record)
        if representante is None and block is None:
            rejected.append(Rejected(rut=rut, reason=Rejection.SIN_DATOS_DE_BLOQUEO))
            continue

        normalized.append(
            _Normalized(
                rut=rut,
                nombre_key=name_key(record.razon_social),
                representante_rut=representante,
                address_block=block,
                address_display=_address_display(record),
                record=record,
            )
        )

    return normalized, rejected


def _address_block(record: SupplierRecord) -> str | None:
    """Geographic blocking when coordinates exist, string blocking when they do not."""
    if record.latitud is not None and record.longitud is not None:
        return geo_bucket(record.latitud, record.longitud)
    key = address_key(record.direccion)
    if not key:
        return None
    comuna = address_key(record.comuna)
    return f"{key}|{comuna}" if comuna else key


def _address_display(record: SupplierRecord) -> str:
    parts = [part for part in (record.direccion, record.comuna) if part]
    return ", ".join(parts) if parts else "(domicilio sin texto)"


def _representative_relations(normalized: list[_Normalized]) -> list[Relation]:
    groups: dict[str, list[_Normalized]] = defaultdict(list)
    for item in normalized:
        if item.representante_rut:
            groups[item.representante_rut].append(item)

    relations: list[Relation] = []
    for persona_rut, group in groups.items():
        suppliers = sorted({item.rut for item in group})
        if len(suppliers) < 2:
            continue
        display = _representative_name(group) or persona_rut
        relations.append(
            Relation(
                kind=RelationKind.REPRESENTANTE_COMPARTIDO,
                anchor=persona_rut,
                display=display,
                suppliers=tuple(suppliers),
                evidence=_evidence(group),
            )
        )
    return relations


def _representative_name(group: list[_Normalized]) -> str | None:
    for item in group:
        if item.record.representante_nombre:
            return item.record.representante_nombre
    return None


def _address_relations(normalized: list[_Normalized]) -> list[Relation]:
    groups: dict[str, list[_Normalized]] = defaultdict(list)
    for item in normalized:
        if item.address_block:
            groups[item.address_block].append(item)

    relations: list[Relation] = []
    for block, group in groups.items():
        suppliers = sorted({item.rut for item in group})
        if len(suppliers) < 2:
            continue
        relations.append(
            Relation(
                kind=RelationKind.DOMICILIO_COMPARTIDO,
                anchor=block,
                display=group[0].address_display,
                suppliers=tuple(suppliers),
                evidence=_evidence(group),
            )
        )
    return relations


def _name_candidates(normalized: list[_Normalized], linked: set[tuple[str, str]]) -> list[Candidate]:
    """Same normalised name, different RUT, and no deterministic link already."""
    groups: dict[str, list[str]] = defaultdict(list)
    for item in normalized:
        if item.nombre_key:
            groups[item.nombre_key].append(item.rut)

    candidates: list[Candidate] = []
    for nombre_key, ruts in groups.items():
        distinct = tuple(sorted(set(ruts)))
        if len(distinct) < 2:
            continue
        if _all_pairs_linked(distinct, linked):
            continue
        candidates.append(Candidate(nombre_key=nombre_key, ruts=distinct))
    return candidates


def _all_pairs_linked(ruts: tuple[str, ...], linked: set[tuple[str, str]]) -> bool:
    for index, left in enumerate(ruts):
        for right in ruts[index + 1 :]:
            if tuple(sorted((left, right))) not in linked:
                return False
    return True


def _pairs_from(relations: list[Relation]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for relation in relations:
        for index, left in enumerate(relation.suppliers):
            for right in relation.suppliers[index + 1 :]:
                pairs.add(tuple(sorted((left, right))))
    return pairs


def _evidence(group: list[_Normalized]) -> tuple[SourceRef, ...]:
    """One citation per distinct source document, deduplicated and ordered."""
    seen: dict[tuple[str, int | None], SourceRef] = {}
    for item in group:
        documento_id = item.record.documento_id
        if not documento_id:
            continue
        ref = SourceRef(documento_id=documento_id, pagina=item.record.pagina)
        seen.setdefault((documento_id, ref.pagina), ref)
    return tuple(seen[key] for key in sorted(seen, key=lambda k: (k[0], k[1] or 0)))


def apply_to_store(
    store: GraphStore,
    result: ResolutionResult,
    records: Iterable[SupplierRecord],
) -> dict[str, int]:
    """Write relations into the graph as :Persona / :Domicilio nodes and edges.

    The edges are the ones the schema already declares — ``REPRESENTA_A`` and
    ``TIENE_DOMICILIO`` — so the navigation agent does not need to know that
    resolution exists. It just finds a path.
    """
    by_rut = {normalize_rut(record.rut): record for record in records if normalize_rut(record.rut)}
    supplier_nodes: dict[str, NodeKey] = {}
    written = {"personas": 0, "domicilios": 0, "aristas": 0}

    for rut, record in by_rut.items():
        supplier_nodes[rut] = store.upsert_node(
            "Proveedor",
            rut,
            razon_social=record.razon_social,
            nombre_key=name_key(record.razon_social),
        )

    for relation in result.relations:
        anchor_node = _write_anchor(store, relation, by_rut, written)
        if anchor_node is None:
            continue
        relation_edges = _ROLE_EDGES[relation.kind]
        # Provenance belongs on the *edge*, not only on the document node: the
        # registry record that says "this person represents that supplier" is the
        # evidence for that hop, and a route whose hops carry no citation is a
        # guess with a nice font.
        edge_props = _edge_provenance(relation)
        for rut in relation.suppliers:
            supplier_node = supplier_nodes.get(rut)
            if supplier_node is None:
                continue
            store.upsert_edge(anchor_node, relation_edges[0], supplier_node, **edge_props)
            written["aristas"] += 1

    return written


def _edge_provenance(relation: Relation) -> dict[str, object]:
    """Attach the citation that supports this specific edge."""
    if not relation.evidence:
        return {}
    first = relation.evidence[0]
    return {"documento_id": first.documento_id, "pagina": first.pagina}


#: Which schema edge each relation kind writes into the graph.
_ROLE_EDGES = {
    RelationKind.REPRESENTANTE_COMPARTIDO: ("REPRESENTA_A",),
    RelationKind.DOMICILIO_COMPARTIDO: ("TIENE_DOMICILIO",),
}


def _write_anchor(
    store: GraphStore,
    relation: Relation,
    by_rut: dict[str, SupplierRecord],
    written: dict[str, int],
) -> NodeKey | None:
    if relation.kind is RelationKind.REPRESENTANTE_COMPARTIDO:
        nombre = next(
            (by_rut[rut].representante_nombre for rut in relation.suppliers if rut in by_rut and by_rut[rut].representante_nombre),
            relation.display,
        )
        written["personas"] += 1
        return store.upsert_node(
            "Persona", relation.anchor, nombre_norm=name_key(nombre), nombre=nombre
        )

    if relation.kind is RelationKind.DOMICILIO_COMPARTIDO:
        written["domicilios"] += 1
        return store.upsert_node("Domicilio", relation.anchor, direccion=relation.display)

    return None
