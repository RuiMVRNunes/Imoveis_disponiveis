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


def test_operation_hint_blocks_cross_leaks(search):
    # a 750 EUR rent ad "passes" a 350k buy ceiling - the hint must catch it
    assert not passes_filters(make_listing(price=750, raw={"operation": "rent"}), search)
    assert passes_filters(make_listing(raw={"operation": "buy"}), search)
    assert passes_filters(make_listing(raw={}), search)  # no hint -> golden rule


def test_property_types_filter(search):
    search.property_types = ["moradia"]
    assert passes_filters(make_listing(title="Moradia T3 em Fiães"), search)
    assert passes_filters(make_listing(title="Casa / Villa T4 em Oiã"), search)
    assert not passes_filters(make_listing(title="Apartamento T3 no centro"), search)
    # explicit source hint beats the title
    assert not passes_filters(
        make_listing(title="T3 espetacular", raw={"property_type": "apartamento"}), search
    )
    # no evidence either way -> keep (golden rule)
    assert passes_filters(make_listing(title="T3 com jardim em Fiães"), search)


def test_portal_prefiltered_skips_structured_checks(search):
    # price/typology/area from the YAML must NOT fight a pasted start_url
    over_budget = make_listing(price=480_000, rooms=5, area_m2=90.0)
    assert not passes_filters(over_budget, search)
    assert passes_filters(over_budget, search, portal_prefiltered=True)
    # keyword excludes and the no-price rule still apply everywhere
    assert not passes_filters(
        make_listing(title="Moradia T3 (leilão)"), search, portal_prefiltered=True
    )
    assert not passes_filters(make_listing(price=None), search, portal_prefiltered=True)
