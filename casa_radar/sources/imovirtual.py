"""Imovirtual (OLX group). Level 1: plain HTTP.

The site is a Next.js app: the most robust parse is the embedded
``__NEXT_DATA__`` JSON blob; CSS selectors are only the fallback because the
generated class names churn on every deploy.
"""

from __future__ import annotations

import json
import logging
import re

from selectolax.parser import HTMLParser

from ..core.config import SearchConfig
from ..core.models import Listing
from ..core.utils import parse_area, parse_price, parse_rooms, set_query_param, slugify
from .base import BaseSource

log = logging.getLogger("casa_radar.sources.imovirtual")

_ROOMS_ENUM = {
    "ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5,
    "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10,
}

# Municipality slug -> full path used by the portal (district/municipality).
# Best effort for the configured area; for anything else, paste a start_url.
_LOCATION_PATHS = {
    "santa-maria-da-feira": "aveiro/santa-maria-da-feira",
    "fiaes": "aveiro/santa-maria-da-feira/fiaes",
    "lourosa": "aveiro/santa-maria-da-feira/lourosa",
    "aveiro": "aveiro",
    "porto": "porto",
    "lisboa": "lisboa",
}


class ImovirtualSource(BaseSource):
    name = "imovirtual"
    portal_root = "https://www.imovirtual.com"
    id_pattern = re.compile(r"[-/]ID([0-9A-Za-z]+)/?$")

    def build_urls(self, search: SearchConfig) -> list[str]:
        operation = "comprar" if search.operation == "buy" else "arrendar"
        estates = search.property_types or ["apartamento", "moradia"]
        urls: list[str] = []
        for location in search.locations or ["portugal"]:
            slug = slugify(location)
            path = _LOCATION_PATHS.get(slug, slug)
            for estate in estates:
                url = f"{self.portal_root}/pt/resultados/{operation}/{estate}/{path}?by=LATEST&direction=DESC&limit=36"
                if search.price_max is not None:
                    url = set_query_param(url, "priceMax", search.price_max)
                if search.min_area_m2 is not None:
                    url = set_query_param(url, "areaMin", int(search.min_area_m2))
                rooms = sorted(search.wanted_rooms)
                if rooms:
                    names = [name for name, n in _ROOMS_ENUM.items() if n in rooms]
                    url = set_query_param(url, "roomsNumber", "[" + ",".join(names) + "]")
                urls.append(url)
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        return set_query_param(base_url, "page", page)

    def parse_page(self, html: str, search: SearchConfig) -> list[Listing]:
        listings = self._parse_next_data(html)
        if listings:
            return listings
        return self._parse_css(html)

    # -- primary: embedded JSON ------------------------------------------------

    def _parse_next_data(self, html: str) -> list[Listing]:
        tree = HTMLParser(html)
        node = tree.css_first("script#__NEXT_DATA__")
        if node is None:
            return []
        try:
            data = json.loads(node.text())
        except json.JSONDecodeError:
            log.warning("imovirtual: __NEXT_DATA__ ilegível, a usar fallback CSS")
            return []
        items = (
            data.get("props", {})
            .get("pageProps", {})
            .get("data", {})
            .get("searchAds", {})
            .get("items", [])
        )
        listings = []
        for item in items:
            try:
                listings.append(self._item_to_listing(item))
            except Exception as exc:  # tolerant: never drop the whole page
                log.warning("imovirtual: item ilegível (%s): %.120s", exc, item)
        return listings

    def _item_to_listing(self, item: dict) -> Listing:
        slug = item.get("slug") or ""
        url = f"{self.portal_root}/pt/anuncio/{slug}" if slug else str(item.get("url", ""))
        price = None
        total_price = item.get("totalPrice") or {}
        if isinstance(total_price, dict) and total_price.get("value") is not None:
            price = int(total_price["value"])
        rooms = item.get("roomsNumber")
        if isinstance(rooms, str):
            rooms = _ROOMS_ENUM.get(rooms.upper())
        area = item.get("areaInM2")
        location = ""
        address = (item.get("location") or {}).get("address") or {}
        for level in ("city", "municipality", "province"):
            name = (address.get(level) or {}).get("name")
            if name:
                location = name
                break
        images = item.get("images") or []
        image_url = None
        if images and isinstance(images[0], dict):
            image_url = images[0].get("medium") or images[0].get("large")
        raw: dict = {"native_id": item.get("id")}
        transaction = str(item.get("transaction") or "").upper()
        if transaction == "SELL":
            raw["operation"] = "buy"
        elif transaction == "RENT":
            raw["operation"] = "rent"
        estate = str(item.get("estate") or "").upper()
        if estate in ("HOUSE", "TERRACED_HOUSE"):
            raw["property_type"] = "moradia"
        elif estate in ("FLAT", "APARTMENT"):
            raw["property_type"] = "apartamento"
        return Listing(
            id="",
            source=self.name,
            search_name="",
            title=str(item.get("title", "")).strip(),
            price=price,
            location=location,
            area_m2=float(area) if area is not None else None,
            rooms=int(rooms) if rooms is not None else None,
            url=url,
            image_url=image_url,
            published_at=item.get("dateCreated") or item.get("pushedUpAt"),
            raw=raw,
        )

    # -- fallback: CSS ---------------------------------------------------------

    def _parse_css(self, html: str) -> list[Listing]:
        tree = HTMLParser(html)
        listings = []
        for article in tree.css("article[data-cy='listing-item']"):
            link = article.css_first("a[data-cy='listing-item-link']") or article.css_first("a")
            if link is None or not link.attributes.get("href"):
                continue
            url = link.attributes["href"]
            title_node = article.css_first("[data-cy='listing-item-title']")
            title = (title_node.text() if title_node else link.text()).strip()
            text = article.text(separator=" ")
            img = article.css_first("img")
            listings.append(
                Listing(
                    id="",
                    source=self.name,
                    search_name="",
                    title=title,
                    price=parse_price(text),
                    location="",
                    area_m2=parse_area(text),
                    rooms=parse_rooms(title) or parse_rooms(text),
                    url=url,
                    image_url=img.attributes.get("src") if img else None,
                )
            )
        return listings
