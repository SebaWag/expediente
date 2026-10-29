"""Audit queries and exports: the questions an auditor asks, answered with identifiers.

See :mod:`expediente.audit.queries` for the reads and :mod:`expediente.audit.export`
for the four output formats. One implementation serves the portal and the agent, so the
screen and the dossier cannot disagree about the same case. The PDF needs ``reportlab``;
everything else here runs on the standard library.
"""

from .export import EXPORTS, render, to_csv, to_json, to_markdown
from .pdf import build_pdf, case_id
from .queries import (
    ORDER_FIELDS,
    entity_profile,
    evidence_for_orders,
    graph_health,
    order_document,
    order_records,
    signal_pack,
    signals_for,
    spend_profile,
)

__all__ = [
    "EXPORTS",
    "ORDER_FIELDS",
    "build_pdf",
    "case_id",
    "entity_profile",
    "evidence_for_orders",
    "graph_health",
    "order_document",
    "order_records",
    "render",
    "signal_pack",
    "signals_for",
    "spend_profile",
    "to_csv",
    "to_json",
    "to_markdown",
]
