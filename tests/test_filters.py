from casa_radar.core.filters import passes_filters
from casa_radar.core.models import Listing


def make_listing(**overrides) -> Listing:
    defaults = dict(
        id="",
        source="imovirtual",
        search_name="Casa Feira",
        title="Moradia T3 em Fiães",
        price=320_000,
        location="Fiães",
        area_m2=150.0,
        rooms=3,
        url="https://example.pt/1",
    )
    defaults.update(overrides)
    return Listing(**defaults)


def test_positive_violations_are_excluded(search):
    assert not passes_filters(make_listing(price=400_000), search)
    assert not passes_filters(make_listing(area_m2=80.0), search)
    assert not passes_filters(make_listing(rooms=2), search)
    assert not passes_filters(make_listing(title="Moradia T3 (penhora)"), search)
    # keyword match must be accent-insensitive ("leilão" vs "leilao")
    assert not passes_filters(make_listing(title="Leilao de moradia T3"), search)


def test_missing_data_passes_except_price(search):
    # Golden rule: in doubt, show. Unknown fields are never grounds to drop -
    # EXCEPT missing price ("sob consulta"), excluded by explicit user choice.
    assert not passes_filters(make_listing(price=None), search)
    assert passes_filters(make_listing(area_m2=None), search)
    assert passes_filters(make_listing(rooms=None), search)


def test_within_limits_passes(search):
    assert passes_filters(make_listing(), search)
    assert passes_filters(make_listing(rooms=4, price=350_000, area_m2=100.0), search)
