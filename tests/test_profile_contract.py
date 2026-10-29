"""The contract between the map and the profile endpoint.

The portal draws six kinds of node and, until this test existed, only one of them —
``Senal`` — could be opened. Every other node answered
``{"error": "perfil no disponible para <label>"}``, which in the UI reads as a door
that does not open. The defect was invisible to the unit suite because nothing asked
the question the front end asks.

So this is a contract test, not a behaviour test: for every label the map can draw,
opening it must return a profile with no ``error`` key. It needs the engine, because
the audit layer is written against ``store.client``; without ``EXPEDIENTE_MDB_URL`` it
skips, and the CI ``graph`` job is what makes it run.
"""

import os
import uuid

import pytest

from expediente.audit.queries import entity_profile
from expediente.graph.millenniumdb import MillenniumDBStore
from expediente.graph.sparql import SparqlClient

MDB_URL = os.environ.get("EXPEDIENTE_MDB_URL", "").strip()
needs_mdb = pytest.mark.skipif(not MDB_URL, reason="EXPEDIENTE_MDB_URL is not set")

#: Unique per run. The engine is a database, not a sandbox: a fixed key would collide
#: with the nodes the previous run left behind, and a document from an earlier test
#: would answer first for the same order. The suffix makes every run self-contained.
RUN = uuid.uuid4().hex[:10]

#: 64 hex characters, because a document's key is the sha256 of its own bytes.
DIGEST = uuid.uuid4().hex + uuid.uuid4().hex
DOC = ("Documento", DIGEST)
ORDER = ("OrdenCompra", f"ct-{RUN}-OC-1")
BUYER = ("Organismo", f"ct-{RUN}-org-1")
SUPPLIER = ("Proveedor", f"ct-{RUN}-prov-1")
SIGNAL = ("Senal", f"ct-{RUN}-senal-1")

#: Exactly the labels the default map view renders. A new label added to the canvas
#: without a profile behind it is the defect this list exists to catch.
LABELS = [ORDER, DOC, SIGNAL, BUYER, SUPPLIER]


@pytest.fixture()
def graph():
    store = MillenniumDBStore(SparqlClient(MDB_URL))
    store.upsert_node(*BUYER, nombre="Municipalidad de Prueba")
    store.upsert_node(*SUPPLIER, razon_social="Proveedor de Prueba SpA", rut=f"ct-{RUN}-prov-1")
    store.upsert_node(
        *ORDER, nombre="OC de prueba", monto_clp=940_000, fecha="2026-10-01", estado="Aceptada"
    )
    store.upsert_node(
        *DOC,
        hash_sha256=DIGEST,
        tipo="orden_compra",
        url="https://example.invalid/oc/ct-OC-1",
        recuperado_en="2026-10-29T12:00:00Z",
    )
    store.upsert_node(*SIGNAL, detector="fraccionamiento", explicacion="Cuatro compras bajo el umbral", severidad="alta", score=4)

    store.upsert_edge(BUYER, "EMITE", ORDER)
    store.upsert_edge(ORDER, "ADJUDICADA_A", SUPPLIER)
    store.upsert_edge(DOC, "ACREDITA", ORDER)
    store.upsert_edge(SIGNAL, "SE_OBSERVA_EN", ORDER)
    return store


@needs_mdb
@pytest.mark.parametrize("label,key", LABELS, ids=[f"{label}/{key}" for label, key in LABELS])
def test_every_label_the_map_draws_can_be_opened(graph, label, key):
    profile = entity_profile(graph, label, key)
    assert "error" not in profile, profile.get("error")
    assert profile["label"] == label
    assert profile["key"] == key
    assert profile.get("name")


@needs_mdb
def test_a_document_profile_carries_the_orders_it_accredits_and_a_download(graph):
    profile = entity_profile(graph, *DOC)
    assert profile["role"] == "documento"
    assert profile["hash_sha256"] == DIGEST
    assert profile["orders_count"] == 1
    # The download path carries the digest, not the order code: the server resolves it.
    assert profile["download"] == f"/api/evidence/{DIGEST}"
    codes = [order["code"] for order in profile["case"]["orders"]]
    assert codes == [ORDER[1]]


@needs_mdb
def test_an_order_profile_names_both_parties_and_its_document(graph):
    profile = entity_profile(graph, *ORDER)
    assert profile["role"] == "orden"
    assert profile["supplier"]["name"] == "Proveedor de Prueba SpA"
    assert profile["buyer"]["name"] == "Municipalidad de Prueba"
    assert profile["has_document"] is True
    assert profile["document"]["hash_sha256"] == DIGEST


@needs_mdb
def test_the_evidence_bridge_translates_a_digest_into_the_order_code(graph):
    """The portal endpoint, with the store it actually has in production."""
    from expediente.portal.server import Portal

    portal = Portal.__new__(Portal)  # no HTTP, no static files: just the lookup
    portal.store = graph
    assert portal._order_for_document(DIGEST) == ORDER[1]
    # A digest that is not in the graph is not an error, it is a miss.
    assert portal._order_for_document("f" * 64) is None
    # And something that is not a digest is never sent to the engine at all.
    assert portal._order_for_document("1417966-39-SE25") is None
