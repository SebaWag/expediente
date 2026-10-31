"""Which clock does an order's date come from?

This is not a formatting question. The threshold that makes a competitive procedure
mandatory is a function of the date, and Ley 21.634 moved the Compra Ágil cap from 30 to
100 UTM on 2024-12-12 — from $2.018.820 to $6.729.400, a factor of 3,3 in one day. An
order created the day before the reform and accepted the day after was, until this test
existed, judged against a limit that did not exist when it was issued.

The payload carries three clocks: ``FechaCreacion`` (the buyer issues the order),
``FechaEnvio`` (it is sent) and ``FechaAceptacion`` (the supplier answers, days later).
The cap follows the **first** one. The bulk files, which carry the five-year corpus, use
``FechaEnvioOC`` and were already right; the API path preferred acceptance and was not.

``orden_from_api`` had no test before this file, which is how the ordering survived.
"""

from datetime import date

import pytest

from expediente.domain.umbral import resolver
from expediente.ingest.bulk import ALIASES
from expediente.ingest.mercado_publico import MercadoPublicoError, orden_from_api


def payload(**fechas) -> dict:
    return {
        "Codigo": "1234-5-SE24",
        "Total": "3500000",
        "Comprador": {
            "RutUnidad": "61602226-1",
            "NombreOrganismo": "Hospital Traiguén",
            "ComunaUnidad": "Traiguén",
            "RegionUnidad": "Araucanía",
        },
        "Proveedor": {"RutProveedor": "76.150.343-k", "NombreProveedor": "INDURA S.A."},
        "Fechas": fechas,
    }


def test_the_order_takes_the_date_it_was_created_not_the_one_it_was_accepted():
    order = orden_from_api(
        payload(FechaCreacion="2024-12-10", FechaEnvio="2024-12-11", FechaAceptacion="2024-12-20")
    )

    assert order.fecha == date(2024, 12, 10)


def test_without_creation_the_send_date_is_next():
    order = orden_from_api(payload(FechaEnvio="2024-12-11", FechaAceptacion="2024-12-20"))

    assert order.fecha == date(2024, 12, 11)


def test_acceptance_is_the_last_resort_not_the_first_choice():
    order = orden_from_api(payload(FechaAceptacion="2024-12-20"))

    assert order.fecha == date(2024, 12, 20)


def test_an_order_with_no_usable_date_is_refused_rather_than_dated_by_guess():
    with pytest.raises(MercadoPublicoError):
        orden_from_api(payload())


def test_the_bulk_corpus_uses_the_send_date():
    """The five-year corpus comes from these files, so this ordering decides its findings."""
    assert ALIASES["fecha"][0] == "FechaEnvioOC"


def test_the_cap_is_the_one_in_force_when_the_order_was_created():
    """The materiality of the ordering, in pesos.

    Created 2024-12-10, accepted 2024-12-20. Under the old precedence this order was
    judged with the post-reform cap, three times larger, and an order worth $5M would have
    counted as "below the threshold" when it was not.
    """
    order = orden_from_api(
        payload(FechaCreacion="2024-12-10", FechaEnvio="2024-12-11", FechaAceptacion="2024-12-20")
    )

    at_creation = resolver(order.fecha, "Compra Ágil")
    at_acceptance = resolver(date(2024, 12, 20), "Compra Ágil")

    assert at_creation.aplica
    assert at_creation.clp == 2_018_820  # 30 UTM
    assert at_acceptance.clp == 6_729_400  # 100 UTM, from 2024-12-12
    assert at_acceptance.clp / at_creation.clp > 3

    # The concrete consequence: a $5M order is below the cap on the wrong date and above it
    # on the right one.
    monto = 5_000_000
    assert monto > at_creation.clp
    assert monto < at_acceptance.clp


def test_the_reform_boundary_is_the_day_it_starts_applying():
    assert resolver(date(2024, 12, 11), "Compra Ágil").clp == 2_018_820
    assert resolver(date(2024, 12, 12), "Compra Ágil").clp == 6_729_400
