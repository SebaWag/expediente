"""Export one case in the formats a human actually uses.

Four outputs, one source. They are not four implementations: they all read the same
:func:`expediente.audit.queries.signal_pack`, so the PDF, the markdown, the CSV and the
JSON can never disagree about the same signal — which is the whole point of having one
audit layer instead of one per surface.

* **PDF** — the signable document (see :mod:`expediente.audit.pdf`).
* **Markdown** — for a repository, a ticket or a chat.
* **CSV** — for the accountant who will put the orders in a spreadsheet.
* **JSON** — for whoever wants to re-check the hashes with a script.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

from .pdf import build_pdf, case_id

CSV_COLUMNS = (
    ("code", "codigo"),
    ("fecha", "fecha"),
    ("monto_clp", "monto_clp"),
    ("nombre", "nombre"),
    ("descripcion", "descripcion"),
    ("procedencia", "procedencia"),
    ("estado", "estado"),
    ("sector", "sector"),
    ("region_compra", "region"),
    ("codigo_licitacion", "licitacion"),
)


def to_markdown(pack: dict[str, Any]) -> str:
    identifier = case_id(pack)
    counts = pack.get("counts") or {}
    lines = [
        f"# Expediente · {pack.get('id')}",
        "",
        f"> {pack.get('claim') or 'Sin afirmación registrada.'}",
        "",
        "| | |",
        "|---|---|",
        f"| Patrón | {pack.get('detector') or '—'} |",
        f"| Organismo | {pack.get('organismo') or '—'} |",
        f"| Proveedor | {pack.get('proveedor') or '—'} |",
        f"| Período | {pack.get('periodo') or '—'} |",
        f"| Órdenes citadas | {counts.get('orders_cited')} |",
        f"| Órdenes con documento | {counts.get('orders_with_document')} |",
        f"| Monto de las órdenes | {counts.get('sum_clp')} |",
        f"| Cadena completa | {'sí' if pack.get('complete') else 'no'} |",
        "",
        "## Órdenes",
        "",
        "| Código | Fecha | Monto | Qué se compró | Procedencia | Licitación |",
        "|---|---|---|---|---|---|",
    ]
    for order in pack.get("orders") or []:
        description = (order.get("descripcion") or order.get("nombre") or "").replace("|", "/")[:120]
        lines.append(
            f"| `{order.get('code')}` | {(order.get('fecha') or '')[:10]} | {order.get('monto_clp')} "
            f"| {description} | {order.get('procedencia') or '—'} | {order.get('codigo_licitacion') or '—'} |"
        )

    if not pack.get("complete"):
        # Stated in words, at the top of the traceability section, in the same size as
        # everything else. A table cell that says "no" is not a warning.
        missing = (counts.get("orders_cited") or 0) - (counts.get("orders_with_document") or 0)
        lines += [
            "",
            f"> **Cadena de evidencia incompleta.** {missing} de las "
            f"{counts.get('orders_cited')} órdenes citadas no tienen documento adjunto todavía. "
            "Lo que falta **no se presenta como probado**.",
            "",
        ]

    lines += ["", "## Trazabilidad", ""]
    if pack.get("evidence"):
        for row in pack["evidence"]:
            extract = ""
            if row.get("evidencias"):
                extract = row["evidencias"][0].get("extracto") or ""
            lines += [
                f"- **{row.get('order')}** · `{row.get('hash_sha256')}`",
                f"  - {row.get('url')}",
                f"  - recuperado: {row.get('recuperado_en')}",
                f"  - extracto: {extract}",
            ]
    else:
        lines.append("No hay documentos adjuntos: la afirmación no tiene cadena de evidencia.")

    lines += [
        "",
        "## Lo que este documento NO afirma",
        "",
        "- No afirma que exista delito ni responsabilidad de ninguna persona o empresa.",
        "- Afirma que los registros muestran el patrón descrito, no que las compras sean irregulares.",
        "- Un domicilio compartido también es lo que parece un edificio de oficinas.",
        "",
        "---",
        "",
        f"id del expediente: `{identifier}`",
    ]
    return "\n".join(lines) + "\n"


def to_csv(pack: dict[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([header for _key, header in CSV_COLUMNS] + ["documento_sha256", "documento_url", "recuperado_en"])
    by_order = {row["order"]: row for row in pack.get("evidence") or []}
    for order in pack.get("orders") or []:
        document = by_order.get(order.get("code"), {})
        writer.writerow(
            [order.get(key, "") for key, _header in CSV_COLUMNS]
            + [document.get("hash_sha256", ""), document.get("url", ""), document.get("recuperado_en", "")]
        )
    return buffer.getvalue()


def to_json(pack: dict[str, Any]) -> str:
    payload = dict(pack)
    payload["case_id"] = case_id(pack)
    return json.dumps(payload, ensure_ascii=False, indent=2)


EXPORTS = {
    "pdf": ("application/pdf", build_pdf),
    "md": ("text/markdown; charset=utf-8", to_markdown),
    "csv": ("text/csv; charset=utf-8", to_csv),
    "json": ("application/json; charset=utf-8", to_json),
}


def render(pack: dict[str, Any], fmt: str) -> tuple[str, bytes, str]:
    """``(content_type, body, filename)`` for one format."""
    if fmt not in EXPORTS:
        raise ValueError(f"formato no soportado: {fmt}")
    content_type, builder = EXPORTS[fmt]
    body = builder(pack)
    if isinstance(body, str):
        body = body.encode("utf-8")
    filename = f"expediente-{pack.get('id', 'caso')}.{fmt}"
    return content_type, body, filename
