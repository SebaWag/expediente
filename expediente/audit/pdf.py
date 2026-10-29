"""The signable artifact: a PDF an auditor can read, check and sign.

Why PDF and not markdown
------------------------

The output of this system is meant to be handed to someone who signs it. A markdown
file is a draft; a PDF with the case, the orders, the source of every one of them, the
limitations and a signature block is a document. The difference is not the format, it
is that the PDF has to answer *"what exactly am I signing?"* on the page in front of
the reader.

What is on the page, in order:

1. **The case** — one sentence, from the detector, not from a model.
2. **What it rests on** — how many orders, how many of those reach a document, the sum.
3. **The orders** — code, date, amount, what was bought, under which procedure.
4. **The traceability** — per order: the URL, the sha256 of the bytes we received, and
   when we received them. This is the part that turns a table into evidence.
5. **What is NOT claimed** — in the same typeface as everything else, not in a footnote.
6. **The signature** — name, RUT, date, and the sentence the person is putting their
   name to.

The document carries its own identity at the foot of every page: the sha256 of the data
it was built from. Two copies with the same id contain the same case.

``reportlab`` is the one dependency in this repository, and it is scoped to this file:
the deterministic core, the detectors and the CLI still run on the standard library
alone. Generating a PDF by hand would have been a stunt, not an engineering decision.
"""

from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

INK = colors.HexColor("#14181f")
SOFT = colors.HexColor("#4a5261")
LINE = colors.HexColor("#d9d5cc")
ACCENT = colors.HexColor("#16506b")
WARN = colors.HexColor("#a12a1c")
OK = colors.HexColor("#1d5c3a")
BAND = colors.HexColor("#f4f2ec")

DETECTOR_LABEL = {
    "fraccionamiento": "Compras partidas bajo el umbral",
    "concentracion": "Concentración de gasto en un proveedor",
    "colicotizacion": "Proveedores que cotizan siempre juntos",
    "modificaciones": "Cambios posteriores a la adjudicación",
}


def clp(value: Any) -> str:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return "—"
    return "$" + format(number, ",d").replace(",", ".")


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName="Times-Bold", fontSize=22,
                                leading=26, textColor=INK, alignment=0, spaceAfter=2),
        "subtitle": ParagraphStyle("s", parent=base["Normal"], fontName="Helvetica", fontSize=10,
                                   leading=14, textColor=SOFT, spaceAfter=14),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=11,
                             leading=14, textColor=ACCENT, spaceBefore=14, spaceAfter=6),
        "body": ParagraphStyle("b", parent=base["Normal"], fontName="Times-Roman", fontSize=11,
                               leading=16, textColor=INK, spaceAfter=6),
        "claim": ParagraphStyle("c", parent=base["Normal"], fontName="Times-Italic", fontSize=12.5,
                                leading=18, textColor=INK, spaceAfter=10),
        "small": ParagraphStyle("sm", parent=base["Normal"], fontName="Helvetica", fontSize=8,
                                leading=11.5, textColor=SOFT),
        "mono": ParagraphStyle("m", parent=base["Normal"], fontName="Courier", fontSize=7.2,
                               leading=9.5, textColor=SOFT),
        "cell": ParagraphStyle("ce", parent=base["Normal"], fontName="Helvetica", fontSize=7.8,
                               leading=10, textColor=INK),
        "cellr": ParagraphStyle("cr", parent=base["Normal"], fontName="Helvetica", fontSize=7.8,
                                leading=10, textColor=INK, alignment=2),
    }


def _esc(text: Any) -> str:
    return (
        str(text if text is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


class _Doc(BaseDocTemplate):
    """Footer carries the identity, because the identity has to travel with the pages."""

    def __init__(self, buffer, case_id: str, header_right: str, **kwargs):
        super().__init__(buffer, **kwargs)
        self.case_id = case_id
        self.header_right = header_right
        frame = Frame(18 * mm, 20 * mm, A4[0] - 36 * mm, A4[1] - 38 * mm, id="body")
        self.addPageTemplates([PageTemplate(id="p", frames=[frame], onPage=self._decorate)])

    def _decorate(self, canvas, doc):
        canvas.saveState()
        width, height = A4
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.4)
        canvas.line(18 * mm, height - 15 * mm, width - 18 * mm, height - 15 * mm)
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(SOFT)
        canvas.drawString(18 * mm, height - 13 * mm, "EXPEDIENTE · auditoría de compras públicas")
        canvas.drawRightString(width - 18 * mm, height - 13 * mm, self.header_right)

        canvas.line(18 * mm, 16 * mm, width - 18 * mm, 16 * mm)
        canvas.setFont("Courier", 6.6)
        canvas.drawString(18 * mm, 11.5 * mm, f"id del expediente {self.case_id}")
        canvas.drawRightString(width - 18 * mm, 11.5 * mm, f"página {canvas.getPageNumber()}")
        canvas.restoreState()


def case_id(pack: dict[str, Any]) -> str:
    """Identity derived from the content, the same rule the dossier uses."""
    body = {
        "signal": pack.get("id"),
        "claim": pack.get("claim"),
        "orders": sorted(order["code"] for order in pack.get("orders") or []),
        "documents": sorted({row["hash_sha256"] for row in pack.get("evidence") or [] if row.get("hash_sha256")}),
    }
    return "sha256:" + hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:32]


def build_pdf(pack: dict[str, Any], *, generated_at: str | None = None) -> bytes:
    """Render one signal as a signable PDF. Returns the bytes."""
    style = _styles()
    generated = generated_at or datetime.now().strftime("%d-%m-%Y %H:%M")
    identifier = case_id(pack)
    order_count = len(pack.get("orders") or [])
    evidence = pack.get("evidence") or []
    covered = {row["order"] for row in evidence}
    counts = pack.get("counts") or {}
    complete = bool(pack.get("complete"))

    buffer = io.BytesIO()
    doc = _Doc(
        buffer,
        case_id=identifier,
        header_right=f"señal {pack.get('id', '')[:14]} · {generated}",
        pagesize=A4,
        title=f"Expediente {pack.get('id', '')}",
        author="Expediente · Wagner Solutions AI",
        subject="Auditoría de compras públicas de Chile",
    )

    story: list[Any] = []
    story.append(Paragraph("Expediente de auditoría", style["title"]))
    story.append(
        Paragraph(
            "Compras públicas de Chile · documento generado a partir de registros oficiales. "
            "Cada afirmación de este expediente viene con el registro que la sostiene.",
            style["subtitle"],
        )
    )

    # --- what is claimed -------------------------------------------------
    story.append(Paragraph("1 · Qué se afirma", style["h2"]))
    story.append(Paragraph(_esc(pack.get("claim") or "Sin afirmación registrada."), style["claim"]))

    meta_rows = [
        ["Patrón detectado", _esc(DETECTOR_LABEL.get(pack.get("detector") or "", pack.get("detector") or "—"))],
        ["Organismo comprador", _esc(pack.get("organismo") or "—")],
        ["Proveedor", _esc(pack.get("proveedor") or "—")],
        ["Período", _esc(pack.get("periodo") or "—")],
        ["Monto informado por el detector", clp(pack.get("monto_clp"))],
        ["Órdenes citadas por la afirmación", f"{order_count}"],
    ]
    meta = Table(meta_rows, colWidths=[58 * mm, 100 * mm])
    meta.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (0, -1), "Helvetica"),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.6),
                ("TEXTCOLOR", (0, 0), (0, -1), SOFT),
                ("TEXTCOLOR", (1, 0), (1, -1), INK),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE),
            ]
        )
    )
    story.append(meta)

    # --- what it rests on -------------------------------------------------
    story.append(Paragraph("2 · En qué se sostiene", style["h2"]))
    if complete:
        statement = (
            f"Las <b>{order_count}</b> órdenes de compra que sostienen esta afirmación fueron recuperadas "
            f"desde la API pública de Mercado Público y cada una tiene su documento adjunto. "
            f"El monto total de esas órdenes es <b>{clp(counts.get('sum_clp'))}</b>."
        )
        tone = OK
    else:
        statement = (
            f"Se citan <b>{order_count}</b> órdenes y <b>{len(covered)}</b> de ellas tienen documento adjunto. "
            "La diferencia se declara abajo y <b>no se presenta como probada</b>."
        )
        tone = WARN
    story.append(Paragraph(statement, style["body"]))
    story.append(Paragraph(f'<font color="{tone.hexval()}">●</font> {_esc("cadena de evidencia completa" if complete else "cadena de evidencia incompleta: ver limitaciones")}', style["small"]))

    # --- the orders -------------------------------------------------------
    story.append(Paragraph("3 · Las órdenes", style["h2"]))
    if order_count:
        header = [
            Paragraph("<b>Código</b>", style["small"]),
            Paragraph("<b>Fecha</b>", style["small"]),
            Paragraph("<b>Monto</b>", style["small"]),
            Paragraph("<b>Qué se compró</b>", style["small"]),
            Paragraph("<b>Procedencia</b>", style["small"]),
        ]
        rows = [header]
        for order in pack.get("orders") or []:
            rows.append(
                [
                    Paragraph(_esc(order.get("code")), style["cell"]),
                    Paragraph(_esc((order.get("fecha") or "")[:10]), style["cell"]),
                    Paragraph(clp(order.get("monto_clp")), style["cellr"]),
                    Paragraph(_esc((order.get("descripcion") or order.get("nombre") or "")[:150]), style["cell"]),
                    Paragraph(_esc((order.get("procedencia") or "—")[:28]), style["cell"]),
                ]
            )
        table = Table(rows, colWidths=[26 * mm, 18 * mm, 22 * mm, 74 * mm, 28 * mm], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), BAND),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.25, LINE),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(table)
    else:
        story.append(Paragraph("No hay órdenes recuperables para esta señal.", style["body"]))

    # --- traceability -----------------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("4 · Trazabilidad", style["h2"]))
    story.append(
        Paragraph(
            "Por cada orden: dónde está el registro, la huella digital de los bytes que se recibieron "
            "y el momento en que se recibieron. Cualquiera puede repetir la consulta a esa dirección y "
            "comparar la huella. Si no coincide, este documento no vale.",
            style["body"],
        )
    )
    if evidence:
        trace_rows = [
            [
                Paragraph("<b>Orden</b>", style["small"]),
                Paragraph("<b>Huella (sha256)</b>", style["small"]),
                Paragraph("<b>Recuperado</b>", style["small"]),
                Paragraph("<b>Extracto</b>", style["small"]),
            ]
        ]
        for row in evidence:
            extract = ""
            if row.get("evidencias"):
                extract = (row["evidencias"][0].get("extracto") or "")[:110]
            trace_rows.append(
                [
                    Paragraph(_esc(row.get("order")), style["cell"]),
                    Paragraph(_esc((row.get("hash_sha256") or "")[:32]) + "…", style["mono"]),
                    Paragraph(_esc((row.get("recuperado_en") or "")[:16].replace("T", " ")), style["cell"]),
                    Paragraph(_esc(extract), style["cell"]),
                ]
            )
        trace = Table(trace_rows, colWidths=[24 * mm, 40 * mm, 22 * mm, 82 * mm], repeatRows=1)
        trace.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), BAND),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.25, LINE),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        story.append(trace)

        story.append(Paragraph("Direcciones de origen", style["h2"]))
        for row in evidence[:40]:
            story.append(
                Paragraph(
                    f'{_esc(row.get("order"))} · <font name="Courier">{_esc(row.get("url"))}</font>',
                    style["mono"],
                )
            )
        if len(evidence) > 40:
            story.append(Paragraph(f"… y {len(evidence) - 40} más, en el archivo de datos adjunto.", style["small"]))
    else:
        story.append(Paragraph("No hay documentos adjuntos para esta señal.", style["body"]))

    # --- limitations ------------------------------------------------------
    story.append(Paragraph("5 · Lo que este documento NO afirma", style["h2"]))
    limitations = [
        "No afirma que exista delito, fraude ni responsabilidad de ninguna persona o empresa.",
        "No afirma que las compras sean irregulares: afirma que los registros muestran el patrón descrito.",
        "Un domicilio compartido también es lo que parece un edificio de oficinas.",
        "La detección la hizo una regla escrita, no un modelo de lenguaje, y la regla puede estar mal calibrada.",
    ]
    if not complete:
        limitations.append(
            f"{order_count - len(covered)} de las órdenes citadas no tienen documento adjunto todavía."
        )
    for item in limitations:
        story.append(Paragraph(f"• {_esc(item)}", style["body"]))

    # --- signature --------------------------------------------------------
    story.append(Spacer(1, 8 * mm))
    signature = [
        Paragraph("6 · Firma", style["h2"]),
        Paragraph(
            "Quien firma declara haber revisado las órdenes listadas contra los registros oficiales "
            "citados en la sección 4, y que este expediente describe lo que esos registros muestran.",
            style["body"],
        ),
        Spacer(1, 14 * mm),
        Table(
            [
                ["", ""],
                ["Nombre", ""],
                ["RUT", ""],
                ["Cargo u organismo", ""],
                ["Fecha", ""],
            ],
            colWidths=[40 * mm, 110 * mm],
            style=TableStyle(
                [
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica"),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("TEXTCOLOR", (0, 0), (0, -1), SOFT),
                    ("LINEBELOW", (1, 0), (1, 0), 0.6, INK),
                    ("LINEBELOW", (1, 1), (1, -1), 0.3, LINE),
                    ("TOPPADDING", (0, 0), (-1, -1), 9),
                ]
            ),
        ),
    ]
    story.append(KeepTogether(signature))

    story.append(Spacer(1, 6 * mm))
    story.append(
        Paragraph(
            f"Identidad del expediente: <font name='Courier'>{identifier}</font>. "
            f"Generado el {generated} a partir de datos públicos de Mercado Público y del grafo de "
            f"evidencia de Expediente. Si dos copias tienen la misma identidad, contienen el mismo caso.",
            style["small"],
        )
    )

    doc.build(story)
    return buffer.getvalue()
