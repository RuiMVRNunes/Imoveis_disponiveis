"""Supercasa. Level 1: plain HTTP + CSS parsing.

Structured filters are applied locally after fetching the newest listings for
each location (most robust against portal URL-scheme churn); a pasted
start_url is always the most precise option.
"""

from __future__ import annotations

import logging
import re

from selectolax.parser import HTMLParser

from ..core.config import SearchConfig
from ..core.models import Listing
from ..core.utils import parse_area, parse_price, parse_rooms, slugify
from .base import BaseSource

log = logging.getLogger("casa_radar.sources.supercasa")


class SupercasaSource(BaseSource):
    name = "supercasa"
    portal_root = "https://supercasa.pt"
    id_pattern = re.compile(r"-(\d{5,})(?:/|$)")

    def build_urls(self, search: SearchConfig) -> list[str]:
        operation = "comprar-casas" if search.operation == "buy" else "arrendar-casas"
        urls = []
        for location in search.locations or ["portugal"]:
            url = f"{self.portal_root}/{operation}/{slugify(location)}?ordem=date-desc"
            urls.append(url)
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        if "?" in base_url:
            path, _, query = base_url.partition("?")
            return f"{path.rstrip('/')}/pagina-{page}?{query}"
        return f"{base_url.rstrip('/')}/pagina-{page}"

    def parse_page(self, html: str, search: SearchConfig) -> list[Listing]:
        tree = HTMLParser(html)
        cards = tree.css("div.property") or tree.css("article.property") or tree.css(
            "[data-testid='property-card']"
        )
        listings = []
        for card in cards:
            link = (
                card.css_first(".property-list-title a")
                or card.css_first("h2 a")
                or card.css_first("a[href]")
            )
            if link is None or not link.attributes.get("href"):
                continue
            title = link.text(strip=True) or link.attributes.get("title", "")
            price_node = card.css_first(".property-price") or card.css_first(
                "[class*='price']"
            )
            features_text = " ".join(
                n.text(separator=" ") for n in card.css(".property-features span")
            )
            card_text = card.text(separator=" ")
            img = card.css_first("img")
            image_url = None
            if img is not None:
                image_url = img.attributes.get("data-src") or img.attributes.get("src")
            location_node = card.css_first(".property-location") or card.css_first(
                "[class*='location']"
            )
            listings.append(
                Listing(
                    id="",
                    source=self.name,
                    search_name="",
                    title=title,
                    price=parse_price(price_node.text() if price_node else card_text),
                    location=location_node.text(strip=True) if location_node else "",
                    area_m2=parse_area(features_text or card_text),
                    rooms=parse_rooms(title) or parse_rooms(features_text),
                    url=link.attributes["href"],
                    image_url=image_url,
                )
            )
        if not listings:
            log.warning("supercasa: 0 cards na página — layout mudou ou bloqueio?")
        return listings
