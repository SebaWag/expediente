#!/usr/bin/env python3
"""Load real Compra Ágil payloads into the isolated MillenniumDB graph.

Usage:
    python3 scripts/load_agil.py data/agil_20261027.json
    EXPEDIENTE_MDB_URL=http://127.0.0.1:1234 python3 scripts/load_agil.py data/agil.json

What it writes, and why each node exists:

    (:Organismo)  -[:PUBLICA]->  (:CompraAgil)     who is buying
    (:Documento)  -[:RESPALDA]-> (:CompraAgil)     what backs it up
    (:Proveedor)  -[:OFERTA_EN]->(:CompraAgil)     who quoted (when quotes exist)

Documents are nodes without a page: the API publishes an id and a filename, not a
parsed page. The evidence gate will refuse a claim that cites one of them, and that
refusal is correct — a PDF nobody has read cannot support a statement.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expediente.graph.millenniumdb import MillenniumDBStore
from expediente.graph.sparql import SparqlClient
from expediente.ingest.mercado_publico import compra_agil_from_api


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1

    payloads = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    if not isinstance(payloads, list):
        payloads = [payloads]

    store = MillenniumDBStore.from_env()
    counts = {"organismos": 0, "compras": 0, "proveedores": 0, "documentos": 0, "aristas": 0}

    for payload in payloads:
        compra = compra_agil_from_api(payload)
        if not compra.codigo:
            continue

        organismo = store.upsert_node(
            "Organismo",
            compra.organismo.codigo,
            nombre=compra.organismo.nombre,
            region=compra.organismo.region,
        )
        counts["organismos"] += 1

        agil = store.upsert_node(
            "CompraAgil",
            compra.codigo,
            nombre=str(payload.get("nombre", ""))[:300],
            monto_clp=compra.monto_clp,
            fecha_publicacion=str(compra.fecha_publicacion) if compra.fecha_publicacion else None,
            estado=(payload.get("estado") or {}).get("codigo"),
        )
        counts["compras"] += 1
        store.upsert_edge(organismo, "PUBLICA", agil)
        counts["aristas"] += 1

        for document in payload.get("documentos") or []:
            document_id = f"mp:{document.get('id')}"
            document_node = store.upsert_node(
                "Documento",
                document_id,
                url=f"https://api2.mercadopublico.cl/v2/compra-agil/{compra.codigo}/documentos/{document.get('id')}",
                tipo="pdf",
                nombre=str(document.get("nombre", ""))[:200],
            )
            counts["documentos"] += 1
            store.upsert_edge(document_node, "RESPALDA", agil)
            counts["aristas"] += 1

        for rut in compra.proveedores_cotizando:
            supplier = store.upsert_node("Proveedor", rut)
            counts["proveedores"] += 1
            store.upsert_edge(supplier, "OFERTA_EN", agil)
            counts["aristas"] += 1

    print(f"cargado: {len(payloads)} compras ágiles")
    for key, value in counts.items():
        print(f"  {key:12} {value}")
    print(f"  nodos totales en el grafo: {len(store)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
