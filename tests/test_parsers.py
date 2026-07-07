"""Parser tests against saved HTML fixtures - no live sites are ever hit."""

from casa_radar.sources.custojusto import CustoJustoSource
from casa_radar.sources.casasapo import CasaSapoSource
from casa_radar.sources.idealista import IdealistaSource, looks_blocked, parse_results_page
from casa_radar.sources.imovirtual import ImovirtualSource
from casa_radar.sources.supercasa import SupercasaSource
from tests.conftest import load_fixture


def test_idealista_parser(search):
    listings = parse_results_page(load_fixture("idealista.html"), "https://www.idealista.pt")
    assert len(listings) == 2
    first, second = listings
    assert first.title == "Moradia em Rua das Flores, Fiães"
    assert first.price == 315_000
    assert first.rooms == 3
    assert first.area_m2 == 142.0
    assert first.location == "Rua das Flores, Fiães"
    assert first.image_url and first.image_url.startswith("https://img.idealista.pt")
    # "sob consulta" listing survives with price=None (golden rule)
    assert second.price is None
    assert second.rooms == 4


def test_idealista_finalize_extracts_native_id(search):
    source = IdealistaSource()
    listings = parse_results_page(load_fixture("idealista.html"), source.portal_root)
    finalized = [source._finalize(l, search) for l in listings]
    assert finalized[0].id == "idealista:33445566"
    # tracking params stripped before anything else
    assert finalized[1].id == "idealista:99887766"
    assert "utm_source" not in finalized[1].url


def test_idealista_block_detection():
    assert looks_blocked("<html><script src='https://ct.captcha-delivery.com/c.js'>")
    assert not looks_blocked(load_fixture("idealista.html"))


def test_imovirtual_next_data_parser(search):
    source = ImovirtualSource()
    listings = source.parse_page(load_fixture("imovirtual.html"), search)
    assert len(listings) == 2
    house = listings[0]
    assert house.title == "Moradia T3 com jardim em Lourosa"
    assert house.price == 289_000
    assert house.rooms == 3
    assert house.area_m2 == 140.0
    assert house.location == "Lourosa"
    # estate/transaction from the JSON become filter hints
    assert house.raw["operation"] == "buy"
    assert house.raw["property_type"] == "moradia"
    assert listings[1].raw["operation"] == "rent"
    assert listings[1].raw["property_type"] == "apartamento"
    finalized = source._finalize(house, search)
    assert finalized.id == "imovirtual:1gXbc"
    assert finalized.url.endswith("-ID1gXbc")


def test_imovirtual_css_fallback(search):
    source = ImovirtualSource()
    listings = source.parse_page(load_fixture("imovirtual_css.html"), search)
    assert len(listings) == 1
    listing = listings[0]
    assert listing.title == "Moradia T4 em Fiães"
    assert listing.price == 340_000
    assert listing.rooms == 4
    assert listing.area_m2 == 180.0


def test_supercasa_parser(search):
    source = SupercasaSource()
    listings = source.parse_page(load_fixture("supercasa.html"), search)
    assert len(listings) == 2
    first = listings[0]
    assert first.title == "Moradia T3 em Espargo, Santa Maria da Feira"
    assert first.price == 335_000
    assert first.rooms == 3
    assert first.area_m2 == 156.0
    assert first.location == "Espargo, Santa Maria da Feira"
    assert first.image_url == "https://cdn.supercasa.pt/images/prop-1.jpg"  # data-src wins
    finalized = source._finalize(first, search)
    assert finalized.id == "supercasa:98765432"
    assert finalized.url.startswith("https://supercasa.pt/")


def test_custojusto_parser(search):
    source = CustoJustoSource()
    listings = source.parse_page(load_fixture("custojusto.html"), search)
    # help page (no id) AND the car from the "related" widget must be ignored
    assert len(listings) == 2
    assert not any("BMW" in l.title for l in listings)
    first = listings[0]
    assert first.title == "Moradia T3 remodelada em Fiães"
    assert first.price == 320_000
    assert first.rooms == 3
    assert first.area_m2 == 150.0
    # hints extracted from the category path -> feed the buy/rent + type filters
    assert first.raw["operation"] == "buy"
    assert first.raw["property_type"] == "moradia"
    assert listings[1].raw["property_type"] == "apartamento"
    assert "operation" not in listings[1].raw
    finalized = source._finalize(first, search)
    assert finalized.id == "custojusto:38123456"
    relative = source._finalize(listings[1], search)
    assert relative.id == "custojusto:38999888"
    assert relative.url.startswith("https://www.custojusto.pt/")


def test_casasapo_parser(search):
    source = CasaSapoSource()
    listings = source.parse_page(load_fixture("casasapo.html"), search)
    assert len(listings) == 1
    listing = listings[0]
    assert listing.title == "Moradia T3 em Fiães"
    assert listing.price == 330_000
    assert listing.rooms == 3
    assert listing.area_m2 == 148.0


def test_empty_page_yields_no_listings(search):
    html = "<html><body><p>Sem resultados</p></body></html>"
    assert SupercasaSource().parse_page(html, search) == []
    assert CustoJustoSource().parse_page(html, search) == []
    assert ImovirtualSource().parse_page(html, search) == []
    assert parse_results_page(html, "https://www.idealista.pt") == []
