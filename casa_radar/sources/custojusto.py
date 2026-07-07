"""CustoJusto. Level 1: plain HTTP + CSS parsing.

CustoJusto is organised by district, with real-estate categories shaped like
``/{distrito}/imobiliario/{moradias|apartamentos}-{venda|arrendamento}`` and
``ps``/``pe`` as price min/max. Listing URLs end with a long numeric id AND
must live under ``/imobiliario/`` - anchoring on the id alone once let cars
and furniture from "related ads" widgets into the results.
"""

from __future__ import annotations

import logging
import re

from selectolax.parser import HTMLParser

from ..core.config import SearchConfig
from ..core.models import Listing
from ..core.utils import parse_area, parse_price, parse_rooms, set_query_param, slugify
from .base import BaseSource

log = logging.getLogger("casa_radar.sources.custojusto")

# Municipality -> district (CustoJusto's top-level region). Best effort for
# the configured area; anything else falls back to the location slug itself.
_DISTRICTS = {
    "santa-maria-da-feira": "aveiro",
    "fiaes": "aveiro",
    "lourosa": "aveiro",
    "aveiro": "aveiro",
    "porto": "porto",
    "lisboa": "lisboa",
}

_CATEGORY_SEGMENT = {"moradia": "moradias", "apartamento": "apartamentos"}

# Listing URLs end with "-38123456" (or "/38123456"); the /imobiliario/ check
# keeps out other categories (cars, furniture) linked from the same page.
_LISTING_ID = re.compile(r"[-/](\d{7,10})(?:\?|$)")


class CustoJustoSource(BaseSource):
    name = "custojusto"
    portal_root = "https://www.custojusto.pt"
    id_pattern = re.compile(r"[-/](\d{7,10})(?:\?|$)")

    def build_urls(self, search: SearchConfig) -> list[str]:
        operation = "venda" if search.operation == "buy" else "arrendamento"
        property_types = search.property_types or list(_CATEGORY_SEGMENT)
        urls = []
        for location in search.locations or [""]:
            slug = slugify(location)
            district = _DISTRICTS.get(slug, slug)
            for prop_type in property_types:
                segment = _CATEGORY_SEGMENT.get(prop_type)
                if segment is None:
                    continue
                url = f"{self.portal_root}/{district}/imobiliario/{segment}-{operation}"
                if search.price_min is not None:
                    url = set_query_param(url, "ps", search.price_min)
                if search.price_max is not None:
                    url = set_query_param(url, "pe", search.price_max)
                if slug != district and location:
                    url = set_query_param(url, "q", location)
                urls.append(url)
        return urls

    def page_url(self, base_url: str, page: int) -> str:
        return set_query_param(base_url, "o", page)

    def parse_page(self, html: str, search: SearchConfig) -> list[Listing]:
        tree = HTMLParser(html)
        listings = []
        seen_hrefs: set[str] = set()
        for link in tree.css("a[href]"):
            href = link.attributes.get("href", "")
            if "/imobiliario/" not in href:
                continue  # cars, furniture, help pages... never again
            if not _LISTING_ID.search(href) or href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            title_node = link.css_first("h2") or link.css_first("h3")
            title = (title_node.text(strip=True) if title_node else link.text(strip=True))[:200]
            if not title:
                continue
            text = link.text(separator=" ")
            img = link.css_first("img")
            image_url = None
            if img is not None:
                image_url = img.attributes.get("data-src") or img.attributes.get("src")
            raw: dict = {}
            if "arrendamento" in href or "/mês" in text or "/mes" in text:
                raw["operation"] = "rent"
            elif "venda" in href:
                raw["operation"] = "buy"
            if "moradia" in href:
                raw["property_type"] = "moradia"
            elif "apartamento" in href:
                raw["property_type"] = "apartamento"
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
                    url=href,
                    image_url=image_url,
                    raw=raw,
                )
            )
        if not listings:
            log.warning("custojusto: 0 anúncios na página — layout mudou ou bloqueio?")
        return listings
