"""The property graph vocabulary.

This module is the single source of truth for what a node is called, which
property identifies it, and what an edge means. Both the store adapters and the
schema documentation read from here, so the graph cannot drift from its docs.

The invariant at the bottom is the product: a claim without a document and a
page is not exported.
"""


from __future__ import annotations

import urllib.parse

#: The one namespace. Every URI the system writes is derived from these five lines,
#: which is why they live in the vocabulary module and not in the storage adapter:
#: an adapter and an exporter that disagree about a prefix produce a graph with two
#: vocabularies, and the symptom is a query that returns zero rows without error.
ONTO = "urn:expediente:onto/"
NODE_PREFIX = "urn:expediente:node/"
EDGE_PREFIX = "urn:expediente:edge/"
REL_PREFIX = f"{ONTO}rel/"
RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
XSD = "http://www.w3.org/2001/XMLSchema#"

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
    # A claim about a signal. Runs Afirmacion -> Senal so that citation_trail(), which
    # walks *incoming* edges from the node it is asked about, finds the claim behind a
    # signal. Written the other way round the trail came back empty for every signal,
    # which reads as "this case has no evidence" when it had ten documents.
    "AFIRMA": ("Afirmacion", "Senal"),
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

def node_uri(label: str, key: str) -> str:
    """Canonical node URI. Same label and key always give the same URI.

    The key is quoted, so an address with spaces or a pipe cannot break the IRI.
    """
    return f"{NODE_PREFIX}{label}/{urllib.parse.quote(str(key), safe='')}"


def relation_uri(relation: str) -> str:
    return f"{REL_PREFIX}{relation}"
