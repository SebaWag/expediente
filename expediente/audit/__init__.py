"""Audit queries and exports: the questions an auditor asks, answered with identifiers.

See :mod:`expediente.audit.queries` for the reads and :mod:`expediente.audit.export`
for the four output formats. One implementation serves the portal and the agent, so the
screen and the dossier cannot disagree about the same case.

``reportlab`` is imported lazily, on the first PDF. The queries — the part a benchmark or
an agent needs — must run on the standard library alone; requiring a PDF renderer to count
orders would put a dependency in the middle of the deterministic core.
"""

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
    "ORDER_FIELDS",
    "entity_profile",
    "evidence_for_orders",
    "graph_health",
    "order_document",
    "order_records",
    "signal_pack",
    "signals_for",
    "spend_profile",
    # resolved on first use
    "EXPORTS",
    "build_pdf",
    "case_id",
    "render",
    "to_csv",
    "to_json",
    "to_markdown",
]


def __getattr__(name: str):
    """Resolve the export helpers on demand.

    ``import expediente.audit`` used to pull in reportlab because the package imported the
    PDF module eagerly. A benchmark that only counts orders then failed on a machine
    without the renderer — a dependency leak from the presentation layer into the reads.
    """
    if name in {"build_pdf", "case_id"}:
        from . import pdf as _pdf

        return getattr(_pdf, name)
    if name in {"EXPORTS", "render", "to_csv", "to_json", "to_markdown"}:
        from . import export as _export

        return getattr(_export, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
