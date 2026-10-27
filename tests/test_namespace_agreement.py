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
