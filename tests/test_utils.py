from casa_radar.core.utils import (
    fmt_price,
    parse_area,
    parse_price,
    parse_rooms,
    slugify,
)


def test_parse_price_formats():
    assert parse_price("350.000 €") == 350_000
    assert parse_price("315.000€") == 315_000
    assert parse_price("850 €/mês") == 850
    assert parse_price("1.250.000 €") == 1_250_000
    assert parse_price("320 000 €") == 320_000  # NBSP thousands separator


def test_parse_price_never_reads_typology_or_area():
    assert parse_price("Moradia T3 em Fiães") is None
    assert parse_price("142 m²") is None
    assert parse_price("T3 320.000 €") == 320_000
    assert parse_price("Preço sob consulta") is None
    assert parse_price(None) is None


def test_parse_area():
    assert parse_area("142 m² área bruta") == 142.0
    assert parse_area("84,5 m2") == 84.5
    assert parse_area("Apartamento T2 mobilado") is None  # "2 m" trap
    assert parse_area(None) is None


def test_parse_rooms():
    assert parse_rooms("Moradia T3 em Fiães") == 3
    assert parse_rooms("t4 duplex") == 4
    assert parse_rooms("Moradia isolada") is None


def test_slugify():
    assert slugify("Santa Maria da Feira") == "santa-maria-da-feira"
    assert slugify("Fiães") == "fiaes"


def test_fmt_price():
    assert fmt_price(350_000) == "350.000 €"
    assert fmt_price(None) == "sob consulta"
