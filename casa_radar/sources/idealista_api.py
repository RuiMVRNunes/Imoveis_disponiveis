"""Idealista via the RapidAPI 'idealista17' API (property-search-by-url).

This is the API alternative to the browser scraper (idealista.py): the RapidAPI
servers do the scraping and face DataDome, we just call a REST endpoint - so it
works from GitHub Actions without a home runner. Unlike the kiwimaker API (which
turned out to be Spain-only), idealista17 supports idealista.pt AND takes the
full search URL, so a pasted idealista.pt URL (map polygon and all) *is* the
filter - exactly like the start_urls of the other sources.

Metered: each search URL costs 1 request per run. On a limited free plan this
source is throttled (runtime.min_interval_hours) and guarded by a monthly cap
(runtime.rapidapi_monthly_cap) in the runner. Prefer ONE idealista.pt search
covering your whole area (draw a polygon) over several municipality URLs.

The key lives ONLY in the RAPIDAPI_KEY environment variable / GitHub Secret.
OFF by default: add "idealista_api" to a search's ``sources`` to enable it.
"""

from __future__ import annotations

import json
import logging
import os
import re

import httpx

from ..core.config import RuntimeConfig, SearchConfig
from ..core.models import Listing
from .base import BaseSource, SourceError

log = logging.getLogger("casa_radar.sources.idealista_api")

API_HOST = "idealista17.p.rapidapi.com"
API_URL = f"https://{API_HOST}/property-search-by-url"
RESULT_COUNT = 50  # newest N per URL; plenty for new-listing detection

# idealista typology -> our coarse property_type
_TYPOLOGY_TO_TYPE = {
    "flat": "apartamento",
    "penthouse": "apartamento",
    "duplex": "apartamento",
    "studio": "apartamento",
    "loft": "apartamento",
    "chalet": "moradia",
    "house": "moradia",
    "villa": "moradia",
    "countryHouse": "moradia",
    "terracedHouse": "moradia",
    "semidetachedHouse": "moradia",
}


class IdealistaApiSource(BaseSource):
    name = "idealista_api"
    portal_root = "https://www.idealista.pt"
    # PT /imovel/NNN, ES /inmueble/NNN - extract the native id from the url.
    id_pattern = re.compile(r"/(?:imovel|inmueble)/(\d+)")
    metered = True  # runner applies throttle + monthly cap

    def build_urls(self, search: SearchConfig) -> list[str]:  # pragma: no cover
        return []

    def page_url(self, base_url: str, page: int) -> str:  # pragma: no cover
        return base_url

    # -- driver -------------------------------------------------------------

    def search(self, search: SearchConfig, runtime: RuntimeConfig) -> list[Listing]:
        key = os.environ.get("RAPIDAPI_KEY", "").strip()
        if not key:
            raise SourceError("idealista_api: falta o segredo RAPIDAPI_KEY")
        search_urls = self._search_urls(search)
        if not search_urls:
            raise SourceError(
                f"idealista_api: pesquisa '{search.name}' sem URL do idealista.pt "
                "(mete em idealista_urls ou em start_urls.idealista_api)"
            )
        listings: list[Listing] = []
        seen_ids: set[str] = set()
        for search_url in search_urls:
            payload = self._call(key, search_url)
            data = payload.get("data") or {}
            elements = data.get("listings") or []
            log.info(
                "idealista_api: %s -> %d anúncios (total %s)",
                search.name, len(elements), data.get("total"),
            )
            for element in elements:
                try:
                    listing = self.parse_element(element)
                except Exception as exc:  # tolerant: never drop the whole page
                    log.warning("idealista_api: anúncio ilegível (%s)", exc)
                    continue
                final = self._finalize(listing, search)
                if final.id in seen_ids:
                    continue
                seen_ids.add(final.id)
                listings.append(final)
        return listings

    @staticmethod
    def _search_urls(search: SearchConfig) -> list[str]:
        urls = list(search.idealista_urls)
        pasted = search.start_urls.get("idealista_api")
        if pasted and pasted not in urls:
            urls.append(pasted)
        return urls

    def _call(self, key: str, search_url: str) -> dict:
        params = {
            "url": search_url,
            "country": "pt",
            "language": "pt",
            "page": 1,
            "result_count": RESULT_COUNT,
        }
        try:
            response = httpx.get(
                API_URL,
                params=params,
                headers={"x-rapidapi-host": API_HOST, "x-rapidapi-key": key},
                timeout=45.0,
            )
        except httpx.HTTPError as exc:
            raise SourceError(f"idealista_api: falha de rede: {exc}") from exc
        if response.status_code == 429:
            raise SourceError("idealista_api: quota RapidAPI esgotada (HTTP 429)")
        if response.status_code == 403:
            raise SourceError("idealista_api: RAPIDAPI_KEY inválida ou sem subscrição (HTTP 403)")
        if response.status_code != 200:
            raise SourceError(f"idealista_api: HTTP {response.status_code}")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise SourceError(f"idealista_api: resposta não-JSON: {exc}") from exc
        if not payload.get("success", True):
            raise SourceError(f"idealista_api: API devolveu erro: {str(payload)[:200]}")
        return payload

    # -- parsing (module-testable on saved fixtures) ------------------------

    def parse_page(self, html: str, search: SearchConfig) -> list[Listing]:
        """Parse a saved JSON response body (used by tests)."""
        payload = json.loads(html)
        elements = (payload.get("data") or {}).get("listings") or []
        return [self.parse_element(e) for e in elements]

    def parse_element(self, element: dict) -> Listing:
        price = element.get("price")
        if price is None:
            price = ((element.get("priceInfo") or {}).get("price") or {}).get("amount")
        rooms = element.get("rooms")
        size = element.get("size")
        location = (
            element.get("address")
            or ", ".join(
                p for p in (element.get("municipality"), element.get("province")) if p
            )
            or element.get("municipality")
            or element.get("province")
            or ""
        )
        image_url = element.get("thumbnail")
        if not image_url:
            images = (element.get("multimedia") or {}).get("images") or []
            if images and isinstance(images[0], dict):
                image_url = images[0].get("url")
        title = (
            (element.get("suggestedTexts") or {}).get("title")
            or element.get("address")
            or f"{element.get('propertyType', 'Imóvel')} em {element.get('municipality', '')}".strip()
        )
        raw: dict = {"native_id": element.get("propertyCode")}
        if element.get("operation") == "sale":
            raw["operation"] = "buy"
        elif element.get("operation") == "rent":
            raw["operation"] = "rent"
        typology = (element.get("detailedType") or {}).get("typology") or element.get("propertyType")
        prop_type = _TYPOLOGY_TO_TYPE.get(typology)
        if prop_type:
            raw["property_type"] = prop_type
        if element.get("latitude") is not None and element.get("longitude") is not None:
            raw["lat"] = element["latitude"]
            raw["lng"] = element["longitude"]
        return Listing(
            id="",
            source=self.name,
            search_name="",
            title=str(title).strip(),
            price=int(price) if price is not None else None,
            location=str(location),
            area_m2=float(size) if size is not None else None,
            rooms=int(rooms) if rooms is not None else None,
            url=str(element.get("url", "")),
            image_url=image_url,
            raw=raw,
        )
