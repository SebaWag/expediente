"""Normalisers are the foundation: if these drift, every citation drifts with them."""

from datetime import date

from expediente.domain.normalizers import (
    geo_bucket,
    is_valid_rut,
    name_key,
    normalize_rut,
    parse_clp,
    parse_date,
    period_key,
    rut_check_digit,
)


def test_normalize_rut_collapses_common_spellings():
    variants = [
        "12.345.678-9",
        "12345678-9",
        "12345678K",
        " 12.345.678 - k ",
        "12345678k",
    ]
    assert normalize_rut(variants[0]) == "12345678-9"
    assert normalize_rut(variants[1]) == "12345678-9"
    assert normalize_rut(variants[2]) == "12345678-K"
    assert normalize_rut(variants[3]) == "12345678-K"
    assert normalize_rut(variants[4]) == "12345678-K"


def test_normalize_rut_rejects_unusable_input():
    assert normalize_rut(None) is None
    assert normalize_rut("") is None
    assert normalize_rut("-") is None


def test_rut_check_digit_known_values():
    assert rut_check_digit("12345678") == "5"
    assert rut_check_digit("11111111") == "1"
    assert rut_check_digit("6") == "K"


def test_is_valid_rut():
    assert is_valid_rut("12.345.678-5") is True
    assert is_valid_rut("11.111.111-1") is True
    assert is_valid_rut("12.345.678-9") is False


def test_name_key_ignores_legal_form_and_accents():
    assert name_key("Comercial Ríos SpA") == name_key("COMERCIAL RIOS S.P.A.")
    assert name_key("Servicios Integrales Limitada") == "servicios integrales"
    assert name_key(None) == ""
    assert name_key("   ") == ""


def test_parse_clp_handles_localised_amounts():
    assert parse_clp(1234567) == 1234567
    assert parse_clp(1234567.0) == 1234567
    assert parse_clp("1.234.567") == 1234567
    assert parse_clp("$ 1.234.567") == 1234567
    assert parse_clp("1234567") == 1234567
    assert parse_clp(None) is None
    assert parse_clp("") is None
    assert parse_clp("no informado") is None
    assert parse_clp(True) is None


def test_parse_date_accepts_iso_and_chilean_formats():
    assert parse_date("2026-10-26") == date(2026, 10, 26)
    assert parse_date("2026-10-26T13:00:00") == date(2026, 10, 26)
    assert parse_date("26/10/2026") == date(2026, 10, 26)
    assert parse_date("26102026") == date(2026, 10, 26)
    assert parse_date("31/02/2026") is None
    assert parse_date("not a date") is None


def test_period_key_groups_by_month():
    assert period_key("2026-10-26T13:00:00") == "2026-10"
    assert period_key(date(2026, 3, 9)) == "2026-03"
    assert period_key("26/10/2026") == "2026-10"


def test_geo_bucket_is_stable_grid():
    assert geo_bucket(-33.4489, -70.6693) == geo_bucket(-33.4488, -70.6694)
    assert geo_bucket(-33.4489, -70.6693) != geo_bucket(-33.5100, -70.6693)


def test_rut_display_matches_the_format_the_bulk_wrote():
    """The graph keys :Proveedor on the dotted form; joining on the compact one is silent."""
    from expediente.domain.normalizers import rut_display

    assert rut_display("79984240-8") == "79.984.240-8"
    assert rut_display("79.984.240-8") == "79.984.240-8"
    assert rut_display("6574609-3") == "6.574.609-3"
    assert rut_display("10194297-K") == "10.194.297-K"
    assert rut_display("10194297-k") == "10.194.297-K"
    assert rut_display(None) is None
    assert rut_display("") is None


def test_rut_display_needs_the_check_digit():
    """A bare body is a different supplier, not a shorthand. Documented, not guessed."""
    from expediente.domain.normalizers import rut_display

    # 79984240 is read as body 7998424 + digit 0, which is not the supplier 79.984.240-8
    assert rut_display("79984240") != "79.984.240-8"
