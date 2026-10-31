"""From a graph to a signable dossier, in one call.

This is where the parts stop being parts. The detector finds a pattern, but a
pattern is not an assertion; the graph holds the documents, but a document is not a
citation. The pipeline joins them:

    detector finding  ->  one claim per pattern
    per order code    ->  the document that accredits it, and the page

The page comes from **the edge**, not from the document node: the annotation on
``(:Documento)-[:ACREDITA {pagina}]->(:OrdenCompra)`` says which page of that file
proves that order. Using a document-level page would be a guess dressed as a
citation.

Then the gate does what it always does: a claim whose orders have no accrediting
document is deleted, and the deletion is reported with a reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from .detectors import DetectedPattern, run_all
from .detectors.fraccionamiento import (
    DEFAULT_FLOOR_CLP,
    DEFAULT_MIN_ORDERS,
)
from .dossier.build import build_dossier
from .dossier.models import Dossier
from .domain.models import CompraAgil, OrdenCompra
from .evidence.gate import GateReport, evaluate
from .evidence.models import Claim, Document, Evidence
from .graph.store import GraphStore, NodeKey
from .navigate.paths import Path
from .navigate.rpq import match


@dataclass(frozen=True, slots=True)
class Investigation:
    """Everything one investigation produced, in the order a reader needs it."""

    findings: tuple[DetectedPattern, ...]
    routes: tuple[Path, ...]
    report: GateReport
    dossier: Dossier

    @property
    def kept(self) -> int:
        return len(self.report.kept)

    @property
    def dropped(self) -> int:
        return len(self.report.dropped)


def investigate(
    store: GraphStore,
    orders: Sequence[OrdenCompra],
    *,
    titulo: str,
    sujeto: str,
    generado_en: str,
    ceiling_clp: int | None = None,
    floor_clp: int = DEFAULT_FLOOR_CLP,
    min_orders: int = DEFAULT_MIN_ORDERS,
    compras: Sequence[CompraAgil] = (),
    route_pattern: str = "red_oculta",
    route_from: NodeKey | None = None,
) -> Investigation:
    """Run the whole chain: detect, cite, gate, assemble."""
    findings = tuple(run_all(orders, compras))

    claims, evidence, documents = _claims_from_findings(store, findings)
    report = evaluate(claims, evidence, documents)

    routes: tuple[Path, ...] = ()
    if route_from is not None:
        routes = tuple(match(store, route_pattern, route_from))

    dossier = build_dossier(
        report,
        documents,
        titulo=titulo,
        sujeto=sujeto,
        generado_en=generado_en,
        thresholds={
            "umbral_clp": ceiling_clp
            if ceiling_clp is not None
            else "el vigente a la fecha de cada OC (ver expediente/domain/umbral.py)",
            "piso_clp": floor_clp,
            "min_ordenes": min_orders,
        },
        detectors=tuple(sorted({finding.detector for finding in findings})),
    )
    return Investigation(findings=findings, routes=routes, report=report, dossier=dossier)


def _claims_from_findings(
    store: GraphStore, findings: Iterable[DetectedPattern]
) -> tuple[list[Claim], list[Evidence], list[Document]]:
    """One claim per finding, one evidence entry per order that has a document."""
    claims: list[Claim] = []
    evidence: list[Evidence] = []
    seen_documents: dict[str, Document] = {}

    for index, finding in enumerate(findings, start=1):
        claim_id = f"AF-{index}"
        evidence_ids: list[str] = []

        for order_code in finding.order_codes:
            for document_id, pagina in _accrediting_documents(store, order_code):
                evidence_id = f"EV-{index}-{order_code}"
                evidence.append(
                    Evidence(
                        id=evidence_id,
                        document_id=document_id,
                        pagina=pagina,
                        extracto=f"Orden de compra {order_code} acreditada en el documento",
                    )
                )
                evidence_ids.append(evidence_id)
                if document_id not in seen_documents:
                    props = store.node("Documento", document_id) or {}
                    seen_documents[document_id] = Document(
                        id=document_id,
                        url=str(props.get("url", "")),
                        tipo=str(props.get("tipo", "pdf")),
                        paginas=props.get("paginas"),
                    )
                break  # one citation per order is enough; the gate only needs one

        claims.append(Claim(id=claim_id, texto=finding.explanation(), evidence_ids=tuple(evidence_ids)))

    return claims, evidence, list(seen_documents.values())


def _accrediting_documents(store: GraphStore, order_code: str) -> list[tuple[str, int | None]]:
    """Documents that point at this order, with the page carried by the edge."""
    found: list[tuple[str, int | None]] = []
    for document_key, _order, props in store.incoming("ACREDITA", ("OrdenCompra", order_code)):
        found.append((document_key[1], props.get("pagina")))
    return sorted(found)
