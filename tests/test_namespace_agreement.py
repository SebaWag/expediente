"""One namespace, or the graph silently has two.

This file exists because of a real defect: the storage adapter still carried the
placeholder ontology ``http://expediente.local/onto#`` while the bulk exporter wrote
``urn:expediente:onto/``. Every node URI agreed, so nodes and properties resolved, but
the relation prefix did not — and ``adjacent_directed`` returned **zero edges without
raising**. The graph looked populated and was unnavigable.

A test that compares the constants is the cheapest possible defence: it fails in
milliseconds, at the moment somebody reintroduces a literal instead of importing it.
"""

import importlib.util
import sys
from pathlib import Path

from expediente.graph import millenniumdb, schema


def _load_exporter():
    path = Path(__file__).resolve().parent.parent / "scripts" / "export_ntriples.py"
    spec = importlib.util.spec_from_file_location("export_ntriples_under_test", path)
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves string annotations through sys.modules, so an unregistered
    # module raises TypeError on exec. Registering it is the documented loader contract.
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_schema_adapter_and_exporter_share_the_namespace():
    exporter = _load_exporter()
    for name in ("ONTO", "NODE_PREFIX", "REL_PREFIX", "RDF_TYPE"):
        values = {
            "schema": getattr(schema, name),
            "adapter": getattr(millenniumdb, name),
            "exporter": getattr(exporter, name),
        }
        assert len(set(values.values())) == 1, f"{name} disagrees: {values}"


def test_the_same_node_gets_the_same_uri_everywhere():
    exporter = _load_exporter()
    for label, key in (
        ("Organismo", "61.603.000-0"),
        ("Proveedor", "96.905.520-9"),
        ("Domicilio", "av bernardo o higgins 1234|renca"),
    ):
        assert exporter.node_uri(label, key) == millenniumdb.node_uri(label, key)
        assert exporter.node_uri(label, key) == schema.node_uri(label, key)


def test_the_same_edge_gets_the_same_predicate_everywhere():
    exporter = _load_exporter()
    for relation in ("EMITE", "ADJUDICADA_A", "REPRESENTA_A"):
        assert exporter.relation_uri(relation) == millenniumdb.relation_uri(relation)
        assert exporter.relation_uri(relation) == schema.relation_uri(relation)
        assert millenniumdb.relation_uri(relation).startswith(schema.REL_PREFIX)


def test_the_splitting_rule_ignores_framework_purchases():
    """A Convenio Marco order is not avoiding a tender, so it cannot be splitting one.

    Measured on the real corpus, including framework purchases produced 1,508 groups in
    200,000 orders whose top finding was an airline's ticket reservations. Restricting to
    direct awards left 13, and they were medical supplies bought repeatedly just under
    the ceiling.
    """
    from datetime import date

    from expediente.detectors.fraccionamiento import detect_fraccionamiento
    from expediente.domain.models import OrdenCompra, Organismo, Proveedor

    organismo = Organismo(codigo="org-1", nombre="Hospital")
    proveedor = Proveedor(rut_norm="11111111-1", razon_social="Insumos")

    def orders(procedencia: str | None) -> list[OrdenCompra]:
        return [
            OrdenCompra(
                codigo=f"oc-{index}",
                organismo=organismo,
                proveedor=proveedor,
                monto_clp=900_000,
                fecha=date(2024, 5, 10 + index),
                procedencia=procedencia,
            )
            for index in range(4)
        ]

    assert len(detect_fraccionamiento(orders("Trato Directo"))) == 1
    assert detect_fraccionamiento(orders("Convenio Marco")) == []
    # Sin mecanismo no hay régimen que elegir: antes se dejaba pasar ("una exención no
    # probada no es una exención"), pero elegir el régimen del trato directo por defecto es
    # regalarle a la orden el tope más generoso. Se cuenta como SIN_REGLA y se reporta.
    assert detect_fraccionamiento(orders(None)) == []
    # El comportamiento viejo sigue siendo alcanzable, pero hay que pedirlo entero: un techo
    # fijo impuesto a mano es la única forma de medir Convenio Marco, porque el calendario no
    # le da regla (no hay tope que un convenio marco esté evitando).
    from expediente.detectors.fraccionamiento import LEGACY_CEILING_CLP

    assert len(
        detect_fraccionamiento(
            orders("Convenio Marco"), ceiling_clp=LEGACY_CEILING_CLP, procedencias=None
        )
    ) == 1
    assert detect_fraccionamiento(orders("Convenio Marco"), procedencias=None) == []


def test_the_shape_of_a_group_separates_splitting_from_a_programme():
    """Identical amounts with different descriptions are a queue, not a split.

    This is the distinction the 59× case forced: 85 orders of exactly CLP 833,000 to a
    hospital, each citing a different patient. Real, structured, and not one object
    bought repeatedly.
    """
    from datetime import date

    from expediente.detectors.fraccionamiento import detect_fraccionamiento
    from expediente.domain.models import OrdenCompra, Organismo, Proveedor

    organismo = Organismo(codigo="org-1", nombre="Hospital")
    proveedor = Proveedor(rut_norm="11111111-1", razon_social="Insumos")

    def order(code: str, amount: int, description: str) -> OrdenCompra:
        return OrdenCompra(
            codigo=code,
            organismo=organismo,
            proveedor=proveedor,
            monto_clp=amount,
            fecha=date(2024, 5, 12),
            descripcion=description,
            procedencia="Trato Directo",
        )

    # one object, bought four times, descriptions agree
    same_object = detect_fraccionamiento(
        [
            order("a", 900_000, "GUANTES DE NITRILO CAJA 100 UNIDADES"),
            order("b", 905_000, "GUANTES DE NITRILO CAJA 100 UNIDADES"),
            order("c", 898_000, "GUANTES NITRILO CAJA 100 UNIDADES"),
            order("d", 901_000, "GUANTES DE NITRILO CAJA 100 UNIDADES"),
        ]
    )
    assert same_object[0].pattern_kind() == "mismo_objeto"
    assert same_object[0].desc_similarity >= 0.8

    # one order per patient, identical amount, descriptions differ
    por_caso = detect_fraccionamiento(
        [
            order("p1", 833_000, "RESOLUCION EXENTA PACIENTE JESSICA PEREZ"),
            order("p2", 833_000, "RESOLUCION EXENTA PACIENTE ALEJANDRA SOTO"),
            order("p3", 833_000, "RESOLUCION EXENTA PACIENTE YENIFER DIAZ"),
            order("p4", 833_000, "RESOLUCION EXENTA PACIENTE MARIA ROJAS"),
        ]
    )
    assert por_caso[0].pattern_kind() == "por_caso"
    assert por_caso[0].share_identical_amount == 1.0
    assert por_caso[0].desc_similarity < 0.8
