"""Resolution must produce relations a reviewer can defend, and never a merge."""

from expediente.graph.store import InMemoryGraphStore
from expediente.resolution.models import Rejection, RelationKind, SupplierRecord
from expediente.resolution.resolver import apply_to_store, resolve_suppliers


def record(rut: str, nombre: str, **kwargs) -> SupplierRecord:
    return SupplierRecord(rut=rut, razon_social=nombre, **kwargs)


def test_shared_representative_links_two_suppliers():
    records = [
        record("11.111.111-1", "Comercial Uno SpA", representante_rut="99.999.999-9",
               representante_nombre="Ana Pérez", documento_id="sha256:a", pagina=3),
        record("22.222.222-2", "Servicios Dos Ltda", representante_rut="99999999-9",
               representante_nombre="Ana Pérez", documento_id="sha256:a", pagina=4),
    ]

    result = resolve_suppliers(records)

    assert len(result.relations) == 1
    relation = result.relations[0]
    assert relation.kind is RelationKind.REPRESENTANTE_COMPARTIDO
    assert relation.anchor == "99999999-9"
    assert relation.display == "Ana Pérez"
    assert relation.suppliers == ("11111111-1", "22222222-2")
    assert [ref.pagina for ref in relation.evidence] == [3, 4]


def test_shared_address_links_two_suppliers_without_coordinates():
    records = [
        record("11.111.111-1", "Comercial Uno SpA", direccion="Av. Bernardo O'Higgins 1234", comuna="Renca"),
        record("22.222.222-2", "Servicios Dos Ltda", direccion="AV BERNARDO O HIGGINS 1234", comuna="RENCA"),
    ]

    result = resolve_suppliers(records)

    kinds = [relation.kind for relation in result.relations]
    assert kinds == [RelationKind.DOMICILIO_COMPARTIDO]
    assert result.relations[0].suppliers == ("11111111-1", "22222222-2")


def test_shared_address_links_two_suppliers_by_grid_cell():
    records = [
        record("11.111.111-1", "Uno SpA", latitud=-33.4489, longitud=-70.6693),
        record("22.222.222-2", "Dos Ltda", latitud=-33.4488, longitud=-70.6694),
    ]

    result = resolve_suppliers(records)

    assert result.relations[0].kind is RelationKind.DOMICILIO_COMPARTIDO
    assert result.relations[0].anchor == "-33.449,-70.669"


def test_same_name_different_rut_is_a_candidate_not_a_merge():
    records = [
        record("11.111.111-1", "Comercial Uno SpA", direccion="Calle A 1", comuna="Renca"),
        record("22.222.222-2", "COMERCIAL UNO S.P.A.", direccion="Calle B 2", comuna="Maipu"),
    ]

    result = resolve_suppliers(records)

    assert result.relations == ()
    assert len(result.candidates) == 1
    assert result.candidates[0].ruts == ("11111111-1", "22222222-2")
    assert "sin persona ni domicilio" in result.candidates[0].reason


def test_a_pair_already_linked_is_not_also_a_candidate():
    records = [
        record("11.111.111-1", "Comercial Uno SpA", representante_rut="99.999.999-9"),
        record("22.222.222-2", "Comercial Uno SpA", representante_rut="99.999.999-9"),
    ]

    result = resolve_suppliers(records)

    assert len(result.relations) == 1
    assert result.candidates == ()


def test_one_supplier_per_person_is_not_a_relation():
    records = [record("11.111.111-1", "Uno SpA", representante_rut="99.999.999-9")]

    assert resolve_suppliers(records).relations == ()


def test_records_without_blocking_data_are_rejected_with_a_reason():
    records = [record("11.111.111-1", "Uno SpA")]

    result = resolve_suppliers(records)

    assert result.rejected[0].reason is Rejection.SIN_DATOS_DE_BLOQUEO
    assert result.rejected[0].rut == "11111111-1"


def test_unusable_rut_is_rejected_before_blocking():
    result = resolve_suppliers([record("", "Sin RUT SpA", direccion="Calle A 1")])

    assert result.rejected[0].reason is Rejection.RUT_INUSABLE


def test_resolution_is_deterministic():
    records = [
        record("22.222.222-2", "Dos Ltda", representante_rut="99.999.999-9"),
        record("11.111.111-1", "Uno SpA", representante_rut="99.999.999-9"),
    ]
    first = resolve_suppliers(records).to_dict()
    second = resolve_suppliers(list(reversed(records))).to_dict()
    assert first == second


def test_apply_to_store_writes_persona_and_edges():
    records = [
        record("11.111.111-1", "Uno SpA", representante_rut="99.999.999-9", representante_nombre="Ana Pérez"),
        record("22.222.222-2", "Dos Ltda", representante_rut="99.999.999-9", representante_nombre="Ana Pérez"),
    ]
    result = resolve_suppliers(records)
    store = InMemoryGraphStore()

    written = apply_to_store(store, result, records)

    assert written["personas"] == 1
    assert written["aristas"] == 2
    persona = store.node("Persona", "99999999-9")
    assert persona["nombre"] == "Ana Pérez"
    edges = store.edges("REPRESENTA_A", src=("Persona", "99999999-9"))
    assert {triple[1][1] for triple in edges} == {"11111111-1", "22222222-2"}
