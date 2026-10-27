"""The property graph vocabulary.

This module is the single source of truth for what a node is called, which
property identifies it, and what an edge means. Both the store adapters and the
schema documentation read from here, so the graph cannot drift from its docs.

The invariant at the bottom is the product: a claim without a document and a
page is not exported.
"""

from __future__ import annotations

NODES: dict[str, dict[str, object]] = {
    "Organismo": {"key": "codigo", "required": ("codigo", "nombre")},
    "Proveedor": {"key": "rut_norm", "required": ("rut_norm", "razon_social")},
    "Persona": {"key": "rut_norm", "required": ("rut_norm", "nombre_norm")},
    "Domicilio": {"key": "geo_hash", "required": ("geo_hash",)},
    "Licitacion": {"key": "codigo", "required": ("codigo",)},
    "OrdenCompra": {"key": "codigo", "required": ("codigo", "monto_clp", "fecha")},
    "CompraAgil": {"key": "codigo", "required": ("codigo",)},
    "Contrato": {"key": "codigo", "required": ("codigo",)},
    "Documento": {"key": "hash_sha256", "required": ("hash_sha256", "tipo", "url")},
    "Evidencia": {"key": "id", "required": ("id", "pagina", "extracto")},
    "InformeContraloria": {"key": "id", "required": ("id", "numero", "fecha")},
    "Hallazgo": {"key": "id", "required": ("id", "tipo")},
    "Detector": {"key": "codigo", "required": ("codigo", "version")},
    "Senal": {"key": "id", "required": ("id", "score", "fecha_deteccion")},
    "Expediente": {"key": "id", "required": ("id", "titulo", "estado")},
    "Afirmacion": {"key": "id", "required": ("id", "texto")},
}

#: Edge name -> (source label, target label). Directed.
EDGES: dict[str, tuple[str, str]] = {
    # Contracting lifecycle
    "EMITE": ("Organismo", "OrdenCompra"),
    "ADJUDICADA_A": ("OrdenCompra", "Proveedor"),
    "DERIVA_DE": ("OrdenCompra", "Licitacion"),
    "CONVOCADA_POR": ("Licitacion", "Organismo"),
    "OFERTA_EN": ("Proveedor", "Licitacion"),
    "GENERA": ("CompraAgil", "OrdenCompra"),
    "PUBLICA": ("Organismo", "CompraAgil"),
    "EJECUTA": ("Contrato", "Licitacion"),
    # Corporate structure
    "SOCIO_DE": ("Persona", "Proveedor"),
    "REPRESENTA_A": ("Persona", "Proveedor"),
    "TIENE_DOMICILIO": ("Proveedor", "Domicilio"),
    # Evidence chain
    "SOPORTADA_POR": ("Afirmacion", "Evidencia"),
    "EN": ("Evidencia", "Documento"),
    "ACREDITA": ("Documento", "OrdenCompra"),
    "RESPALDA": ("Documento", "CompraAgil"),
    # Detection
    "SE_OBSERVA_EN": ("Senal", "OrdenCompra"),
    "PRODUCE": ("Detector", "Senal"),
    "CORRELACIONA_CON": ("Senal", "Senal"),
    # Dossier
    "CONTIENE": ("Expediente", "Afirmacion"),
    # Ground truth
    "CONTIENE_HALLAZGO": ("InformeContraloria", "Hallazgo"),
    "INVOLUCRA": ("Hallazgo", "Organismo"),
    "REDESCUBIERTO_POR": ("Hallazgo", "Senal"),
}

#: The export gate. A dossier item that cannot satisfy this path is dropped.
EVIDENCE_INVARIANT = (
    "Every :Afirmacion must reach a :Documento with a page number through "
    ":Evidencia, via SOPORTADA_POR then EN. Assertions that fail this path are "
    "removed before the dossier is exported, never softened or annotated."
)


def describe() -> str:
    """Render a readable summary of the vocabulary, for docs and for the UI."""
    lines = [EVIDENCE_INVARIANT, "", f"NODES ({len(NODES)})"]
    for label, spec in NODES.items():
        lines.append(f"  (:{label}  key={spec['key']})")
    lines.append("")
    lines.append(f"EDGES ({len(EDGES)})")
    for rel, (src, dst) in EDGES.items():
        lines.append(f"  (:{src})-[:{rel}]->(:{dst})")
    return "\n".join(lines)
