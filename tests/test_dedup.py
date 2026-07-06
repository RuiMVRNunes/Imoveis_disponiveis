from casa_radar.core.dedup import (
    canonical_url,
    find_property_match,
    fingerprint,
    hash_key,
    primary_key,
)
from casa_radar.core.models import Listing


def make_listing(**overrides) -> Listing:
    defaults = dict(
        id="",
        source="supercasa",
        search_name="Casa Feira",
        title="Moradia T3 em Fiães",
        price=320_000,
        location="Fiães, Santa Maria da Feira",
        area_m2=150.0,
        rooms=3,
        url="https://supercasa.pt/venda-moradia-t3-fiaes-98765432",
    )
    defaults.update(overrides)
    return Listing(**defaults)


def test_canonical_url_strips_tracking():
    url = "https://www.Idealista.pt/imovel/123/?utm_source=email&utm_campaign=x&rank=2&shid=abc"
    assert canonical_url(url) == "https://www.idealista.pt/imovel/123/"


def test_canonical_url_keeps_meaningful_params():
    url = "https://example.pt/lista?page=2&preco=100"
    assert canonical_url(url) == "https://example.pt/lista?page=2&preco=100"


def test_hash_key_stable_across_tracking_params():
    a = hash_key("supercasa", "https://supercasa.pt/x-123?utm_source=a")
    b = hash_key("supercasa", "https://supercasa.pt/x-123?utm_source=b")
    assert a == b and a.startswith("supercasa:h")


def test_primary_key_prefers_native_id():
    listing = make_listing(id="supercasa:98765432")
    assert primary_key(listing) == "supercasa:98765432"
    assert primary_key(make_listing(id="")).startswith("supercasa:h")


def test_fingerprint_requires_enough_data():
    assert fingerprint(make_listing()) is not None
    assert fingerprint(make_listing(price=None)) is None
    assert fingerprint(make_listing(area_m2=None)) is None
    assert fingerprint(make_listing(rooms=None)) is None


def _entry(listing: Listing) -> dict:
    return {
        "source": listing.source,
        "location": listing.location,
        "rooms": listing.rooms,
        "area_m2": listing.area_m2,
        "last_price": listing.price,
        "urls": [listing.url],
    }


def test_cross_source_match_within_tolerance():
    stored = {"supercasa:98765432": _entry(make_listing())}
    # Same house on idealista: area 152 (+2 m2), price 322k (+0.6%)
    incoming = make_listing(
        source="idealista",
        url="https://www.idealista.pt/imovel/33445566/",
        area_m2=152.0,
        price=322_000,
        location="Fiães",
    )
    assert find_property_match(incoming, stored) == "supercasa:98765432"


def test_cross_source_no_match_outside_tolerance():
    stored = {"supercasa:98765432": _entry(make_listing())}
    assert find_property_match(make_listing(source="idealista", area_m2=160.0), stored) is None
    assert find_property_match(make_listing(source="idealista", price=340_000), stored) is None
    assert find_property_match(make_listing(source="idealista", rooms=4), stored) is None


def test_same_source_never_matches_cross_source():
    # Relisting on the same portal must be treated as NEW (golden rule)
    stored = {"supercasa:98765432": _entry(make_listing())}
    relisted = make_listing(url="https://supercasa.pt/venda-moradia-t3-fiaes-11111111")
    assert find_property_match(relisted, stored) is None
