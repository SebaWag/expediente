"""Las dos poblaciones de una señal, con los números reales de producción.

Cada caso de acá es una señal que existe en el grafo, con los valores medidos el 4-nov-2026.
La aritmética se comprueba contra lo que el detector declaró y lo que el grafo cita, no contra
una copia de la implementación: si el cálculo cambia, el número cambia y el test cae.

Lo que estos tests fijan:

* una señal de concentración **no** tiene "cobertura de órdenes": lo citado son las órdenes del
  proveedor y el claim habla del mercado. Compararlas como cobertura es el error que la tarjeta
  cometía;
* el truncamiento se detecta cuando la cuota reconstruida con lo citado no alcanza la declarada
  (`cuota_top`), no cuando "faltan órdenes" respecto de un total que no es el mismo conjunto;
* cuando la cadena cita todo lo que declaró, no hay truncamiento aunque falten documentos: eso lo
  dice `complete`, que es otra pregunta.
"""

from __future__ import annotations

from expediente.audit.queries import signal_coverage


def test_concentracion_no_inventa_cobertura_de_ordenes() -> None:
    """Pumanque: 8 órdenes en el mercado, 2 del proveedor, 81,5 % del gasto."""

    cub = signal_coverage(
        "concentracion",
        declared_orders=8,
        declared_amount=1009928255,
        cited_orders=2,
        cited_amount=823273505,
        orders_with_document=2,
        cuota_top=0.8152,
    )

    assert cub["alcance_declarado"] == "mercado"
    assert cub["cobertura"] is None, "en un mercado no hay cobertura: hay cuota"
    assert cub["cuota_citada"] == 0.8152
    assert cub["cuota_declarada"] == 0.8152
    assert cub["truncada"] is False
    assert cub["causa_truncamiento"] is None
    assert cub["poblacion"]["declarada"] == {
        "ordenes": 8,
        "monto_clp": 1009928255,
        "fuente": "detector",
    }
    assert cub["poblacion"]["citada"] == {
        "ordenes": 2,
        "monto_clp": 823273505,
        "fuente": "grafo",
    }


def test_concentracion_con_proveedor_capeado_se_declara_truncada() -> None:
    """Fondo de Orquestas: declara 75 % de $2.942 M y cita 25 órdenes por $248 M (8,5 %)."""

    cub = signal_coverage(
        "concentracion",
        declared_orders=2435,
        declared_amount=2942361351,
        cited_orders=25,
        cited_amount=248727994,
        orders_with_document=25,
        cuota_top=0.75,
    )

    assert cub["alcance_declarado"] == "mercado"
    assert cub["truncada"] is True
    assert cub["cuota_citada"] == 0.0845
    assert cub["causa_truncamiento"] == "tope_materializacion"


def test_fraccionamiento_mide_cobertura_en_ordenes_y_en_monto() -> None:
    """Hospital Puerto Montt: declara 73 órdenes por $54,5 M y cita 25 por $18,6 M."""

    cub = signal_coverage(
        "fraccionamiento",
        declared_orders=73,
        declared_amount=54582378,
        cited_orders=25,
        cited_amount=18631794,
        orders_with_document=25,
    )

    assert cub["alcance_declarado"] == "patron"
    assert cub["cobertura"] == {"ordenes": 0.3425, "monto_clp": 0.3414}
    assert cub["truncada"] is True
    assert cub["causa_truncamiento"] == "tope_materializacion"


def test_fraccionamiento_completo_no_se_declara_truncado() -> None:
    """Valdivia / Vantive: 20 órdenes declaradas, 20 citadas, 20 con documento."""

    cub = signal_coverage(
        "fraccionamiento",
        declared_orders=20,
        declared_amount=16751969,
        cited_orders=20,
        cited_amount=16751969,
        orders_with_document=20,
    )

    assert cub["cobertura"] == {"ordenes": 1.0, "monto_clp": 1.0}
    assert cub["truncada"] is False
    assert cub["causa_truncamiento"] is None


def test_si_faltan_documentos_la_causa_es_otra() -> None:
    """Truncada y además con huecos: la causa que se informa es la del documento, no la del tope."""

    cub = signal_coverage(
        "fraccionamiento",
        declared_orders=40,
        declared_amount=40000000,
        cited_orders=25,
        cited_amount=25000000,
        orders_with_document=19,
    )

    assert cub["truncada"] is True
    assert cub["causa_truncamiento"] == "sin_documento"


def test_sin_poblacion_declarada_no_se_inventa_nada() -> None:
    """Una señal vieja puede no traer `ordenes`. Se dice que no se sabe, no se asume 0."""

    cub = signal_coverage(
        "fraccionamiento",
        declared_orders=None,
        declared_amount=None,
        cited_orders=3,
        cited_amount=1500,
        orders_with_document=3,
    )

    assert cub["poblacion"]["declarada"]["ordenes"] is None
    assert cub["cobertura"] is None
    assert cub["truncada"] is False
