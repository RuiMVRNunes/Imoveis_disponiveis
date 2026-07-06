"""Idealista. Level 2: real browser (Playwright/Chromium) + stealth.

Idealista sits behind DataDome and blocks datacenter IPs outright, so plain
HTTP is pointless. We render pages in Chromium with the obvious automation
tells hidden, reuse cookies/context between runs, and pace requests with
human-ish delays. Even so, expect 0 results from GitHub Actions runners most
of the time - the silent-block alert exists precisely to surface that. A
residential IP (Raspberry Pi at home) makes this source reliable.

Parsing lives in module functions so tests can exercise it on saved HTML
fixtures without Playwright installed.
"""

from __future__ import annotations

import logging
import random
import re
from pathlib import Path

from selectolax.parser import HTMLParser

from ..core.config import RuntimeConfig, SearchConfig
from ..core.models import Listing
from ..core.utils import parse_area, parse_price, parse_rooms, slugify
from .base import BaseSource, SourceError

log = logging.getLogger("casa_radar.sources.idealista")

STORAGE_STATE_PATH = Path(".cache/idealista_storage.json")
_BLOCK_MARKERS = ("captcha-delivery.com", "datadome", "geo.captcha", "blocked")
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def parse_results_page(html: str, portal_root: str) -> list[Listing]:
    """Parse an idealista results page (article.item cards). Tolerant."""
    tree = HTMLParser(html)
    listings = []
    for article in tree.css("article.item"):
        link = article.css_first("a.item-link")
        if link is None or not link.attributes.get("href"):
            continue
        title = (link.attributes.get("title") or link.text(strip=True) or "").strip()
        price_node = article.css_first(".item-price")
        details = [n.text(separator=" ") for n in article.css(".item-detail")]
        details_text = " ".join(details)
        img = article.css_first("img")
        image_url = None
        if img is not None:
            image_url = img.attributes.get("src") or img.attributes.get("data-ondemand-img")
        # idealista titles read "Moradia em Rua X, Fiães" -> location after " em "
        location = title.split(" em ", 1)[1].strip() if " em " in title else ""
        listings.append(
            Listing(
                id="",
                source="idealista",
                search_name="",
                title=title,
                price=parse_price(price_node.text() if price_node else None),
                location=location,
                area_m2=parse_area(details_text),
                rooms=parse_rooms(details_text) or parse_rooms(title),
                url=link.attributes["href"],
                image_url=image_url,
            )
        )
    return listings


def looks_blocked(html: str) -> bool:
    sample = html[:5000].lower()
    return any(marker in sample for marker in _BLOCK_MARKERS)


class IdealistaSource(BaseSource):
    name = "idealista"
    portal_root = "https://www.idealista.pt"
    id_pattern = re.compile(r"/imovel/(\d+)")

    def __init__(self) -> None:
        self._page = None
        self._context = None
        self._playwright = None

    def build_urls(self, search: SearchConfig) -> list[str]:
        operation = "comprar-casas" if search.operation == "buy" else "arrendar-casas"
        filters = []
        if search.price_max is not None:
            filters.append(f"preco-max_{search.price_max}")
        if search.min_area_m2 is not None:
            filters.append(f"tamanho-min_{int(search.min_area_m2)}")
        filters.extend(f"t{r}" for r in sorted(search.wanted_rooms))
        segment = f"com-{','.join(filters)}/" if filters else ""
        return [
            f"{self.portal_root}/{operation}/{slugify(location)}/{segment}"
            "?ordenado-por=data-publicacao-desc"
            for location in (search.locations or ["portugal"])
        ]

    def page_url(self, base_url: str, page: int) -> str:
        path, _, query = base_url.partition("?")
        url = f"{path.rstrip('/')}/pagina-{page}"
        return f"{url}?{query}" if query else url

    def parse_page(self, html: str, search: SearchConfig) -> list[Listing]:
        if looks_blocked(html):
            raise SourceError("idealista: página de captcha DataDome (IP bloqueado)")
        return parse_results_page(html, self.portal_root)

    # -- browser lifecycle -----------------------------------------------------

    def search(self, search: SearchConfig, runtime: RuntimeConfig) -> list[Listing]:
        self._open_browser()
        try:
            return super().search(search, runtime)
        finally:
            self._close_browser()

    def fetch(self, url: str, runtime: RuntimeConfig) -> str:
        page = self._page
        assert page is not None, "browser not started"
        page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        # Human-ish pause + scroll so lazy content (and DataDome) settle.
        page.wait_for_timeout(random.uniform(1_500, 3_500))
        try:
            page.mouse.wheel(0, random.randint(400, 1200))
            page.wait_for_selector("article.item", timeout=8_000)
        except Exception:
            pass  # block/empty pages are handled by parse_page
        return page.content()

    def _open_browser(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise SourceError(
                "idealista: playwright não instalado — corre "
                "'pip install playwright && playwright install chromium'"
            ) from exc
        self._playwright = sync_playwright().start()
        browser = self._playwright.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        storage = str(STORAGE_STATE_PATH) if STORAGE_STATE_PATH.exists() else None
        self._context = browser.new_context(
            user_agent=_BROWSER_UA,
            viewport={"width": 1366, "height": 768},
            locale="pt-PT",
            timezone_id="Europe/Lisbon",
            storage_state=storage,
        )
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "window.chrome = window.chrome || {runtime: {}};"
        )
        self._page = self._context.new_page()

    def _close_browser(self) -> None:
        try:
            if self._context is not None:
                STORAGE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
                self._context.storage_state(path=str(STORAGE_STATE_PATH))
                self._context.browser.close()
        except Exception as exc:
            log.debug("idealista: falha ao fechar browser: %s", exc)
        finally:
            if self._playwright is not None:
                self._playwright.stop()
            self._page = self._context = self._playwright = None
