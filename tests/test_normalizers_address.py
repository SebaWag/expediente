"""Address blocking is weaker than geographic blocking, and the tests say so."""

from expediente.domain.normalizers import address_key


def test_address_key_collapses_case_punctuation_and_noise_tokens():
    assert address_key("Av. Bernardo O'Higgins 1234, Of. 51") == address_key(
        "AV BERNARDO O HIGGINS 1234 OF 51"
    )


def test_address_key_keeps_the_number_that_locates():
    assert address_key("Calle Los Aromos 1234") != address_key("Calle Los Aromos 1235")


def test_address_key_removes_accents():
    assert address_key("Avenida Vicuña Mackenna") == "avenida vicuna mackenna"
    assert address_key("Avenida Vicuña Mackenna") == address_key("AVENIDA VICUNA MACKENNA")


def test_empty_address_is_empty_key():
    assert address_key(None) == ""
    assert address_key("   ") == ""
