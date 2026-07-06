"""Casa SAPO. Plugin ready but OFF by default (shares its backend with
Supercasa, so running both mostly yields duplicates). To enable it, add
"casasapo" to a search's ``sources`` list in config.yaml.
"""

from __future__ import annotations

import logging
import re

from selectolax.parser import HTMLParser

from ..core.config import SearchConfig
from ..core.models import Listing
from ..core.utils import parse_area, parse_price, parse_rooms, slugify
from .base import BaseSource

log = logging.getLogger("casa_radar.sources.casasapo")


class CasaSapoSource(BaseSource):
    name = "casasapo"
    portal_root = "https://casa.sapo.pt"
    id_pattern = re.compile(r"-([a-f0-9]{8,})(?:/|\.html|$)", re.IGNORECASE)

    def build_urls(self, search: SearchConfig) -> list[str]:
        operation = "comprar-casas" if search.operation == "buy" else "alugar-casas"
        return [
            f"{self.portal_root}/{operation}/{slugify(location)}/?ordem=recentes"
            for location in (search.locations or ["portugal"])
        ]

    def page_url(self, base_url: str, page: int) -> str:
        joiner = "&" if "?" in base_url else "?"
        return f"{base_url}{joiner}pn={page}"

    def parse_page(self, html: str, search: SearchConfig) -> list[Listing]:
        tree = HTMLParser(html)
        cards = tree.css("div.property-info-content") or tree.css("div.property") or tree.css(
            "div.searchResultProperty"
        )
        listings = []
        for card in cards:
            link = card.css_first("a[href]")
            if link is None:
                continue
            title_node = card.css_first(".property-type") or card.css_first("p") or link
            price_node = card.css_first(".property-price-value") or card.css_first(
                "[class*='price']"
            )
            location_node = card.css_first(".property-location") or card.css_first(
                "[class*='location']"
            )
            card_text = card.text(separator=" ")
            title = title_node.text(strip=True)
            img = card.css_first("img")
            listings.append(
                Listing(
                    id="",
                    source=self.name,
                    search_name="",
                    title=title,
                    price=parse_price(price_node.text() if price_node else card_text),
                    location=location_node.text(strip=True) if location_node else "",
                    area_m2=parse_area(card_text),
                    rooms=parse_rooms(title) or parse_rooms(card_text),
                    url=link.attributes.get("href", ""),
                    image_url=img.attributes.get("src") if img else None,
                )
            )
        if not listings:
            log.warning("casasapo: 0 cards na página — layout mudou ou bloqueio?")
        return listings
