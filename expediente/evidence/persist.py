"""Write parsed evidence into the graph, so the citation can be walked.

The two halves of the evidence chain live in different places, and each was useless
without the other:

* :mod:`expediente.evidence.pdf` proves *this text is on this page of this file*.
* :func:`expediente.navigate.paths.citation_trails` walks
  ``Afirmacion -[SOPORTADA_POR]-> Evidencia -[EN]-> Documento``.

The ingestion in ``cli`` already wrote ``:Documento`` and ``ACREDITA``, but never
``:Evidencia`` or ``:Afirmacion`` — so the trail the auditor asks for had no edges to
follow and returned empty. This module writes exactly those two nodes and those two
edges, and refuses to write anything that does not verify first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ..graph.store import GraphStore, NodeKey
from .models import Claim, Evidence
from .pdf import ParsedDocument, locate, verify


@dataclass(frozen=True, slots=True)
class PersistedEvidence:
    """What the write actually produced, and what it refused to produce."""

    document: NodeKey | None = None
    evidence: tuple[NodeKey, ...] = ()
    claims: tuple[NodeKey, ...] = ()
    accredited: tuple[tuple[str, int], ...] = ()
    skipped: tuple[tuple[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document[1] if self.document else None,
            "evidence": [key for _label, key in self.evidence],
            "claims": [key for _label, key in self.claims],
            "accredited": [{"orden": code, "pagina": page} for code, page in self.accredited],
            "skipped": [{"what": what, "why": why} for what, why in self.skipped],
        }


def persist_evidence(
    store: GraphStore,
    parsed: ParsedDocument,
    evidence: Iterable[Evidence] = (),
    *,
    claims: Iterable[Claim] = (),
    orders: Iterable[str] = (),
    expediente: Mapping[str, str] | None = None,
) -> PersistedEvidence:
    """Write a parsed document, its located evidence, and the claims that cite it.

    Everything is verified against ``parsed`` before it is written:

    * an evidence entry whose extract is not on its cited page is **skipped**, because
      a citation that does not survive ``verify`` has no business in a graph an
      auditor will read;
    * an order is only accredited if the document **mentions** the order code — the
      page recorded on the edge is found in the file, not supplied by the caller;
    * a claim is only written if at least one of its citations survived, because an
      assertion with no support is precisely what the export gate deletes.

    Skips are returned, not swallowed: a silent drop would turn a broken document into
    a thinner one and nobody would notice.
    """
    document = store.upsert_node(
        "Documento",
        parsed.document.id,
        url=parsed.document.url,
        tipo=parsed.document.tipo,
        paginas=parsed.document.paginas,
        extractor=parsed.extractor,
    )

    written: dict[str, NodeKey] = {}
    evidence_keys: list[NodeKey] = []
    skipped: list[tuple[str, str]] = []

    for item in evidence:
        if not item.pagina:
            skipped.append((f"evidencia {item.id}", "sin pagina: no es una cita"))
            continue

        result = verify(parsed, item)
        if not result.ok:
            skipped.append((f"evidencia {item.id}", f"{result.reason}: {result.detail}"))
            continue

        node = store.upsert_node(
            "Evidencia",
            item.id,
            pagina=item.pagina,
            extracto=item.extracto,
            extractor=item.extractor,
        )
        store.upsert_edge(node, "EN", document)
        written[item.id] = node
        evidence_keys.append(node)

    accredited: list[tuple[str, int]] = []
    for code in orders:
        hits = locate(parsed, code, max_hits=1)
        if not hits:
            skipped.append((f"orden {code}", "el documento no menciona esa orden"))
            continue
        if store.node("OrdenCompra", code) is None:
            skipped.append((f"orden {code}", "la orden no existe en el grafo"))
            continue
        orden = store.upsert_node("OrdenCompra", code)
        store.upsert_edge(document, "ACREDITA", orden, pagina=hits[0].pagina,
                          evidencia=hits[0].id)
        accredited.append((code, hits[0].pagina))

    claim_keys: list[NodeKey] = []
    for claim in claims:
        supported = [written[eid] for eid in claim.evidence_ids if eid in written]
        if not supported:
            skipped.append(
                (f"afirmacion {claim.id}", "ninguna de sus citas sobrevivio la verificacion")
            )
            continue
        node = store.upsert_node("Afirmacion", claim.id, texto=claim.texto)
        for evidence_node in supported:
            store.upsert_edge(node, "SOPORTADA_POR", evidence_node)
        claim_keys.append(node)

    if expediente:
        holder = store.upsert_node(
            "Expediente",
            expediente["id"],
            titulo=expediente.get("titulo", ""),
            estado=expediente.get("estado", "borrador"),
        )
        for claim_node in claim_keys:
            store.upsert_edge(holder, "CONTIENE", claim_node)

    return PersistedEvidence(
        document=document,
        evidence=tuple(evidence_keys),
        claims=tuple(claim_keys),
        accredited=tuple(accredited),
        skipped=tuple(skipped),
    )
