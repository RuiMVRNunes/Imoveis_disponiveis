"""Shared plumbing for portal scrapers: realistic headers, rotating UA,
random delays, retries with exponential backoff, pagination, tolerant field
handling and dedup key assignment."""

from __future__ import annotations

import abc
import logging
import random
import time

import httpx

from ..core.config import RuntimeConfig, SearchConfig
from ..core.dedup import canonical_url, hash_key
from ..core.models import Listing

log = logging.getLogger("casa_radar.sources")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
]

MAX_FETCH_ATTEMPTS = 3


class SourceError(Exception):
    """Raised when a source cannot produce results at all this run."""


class BaseSource(abc.ABC):
    name: str = ""
    portal_root: str = ""
    id_pattern = None  # re.Pattern extracting the native listing id from a URL

    def is_enabled(self) -> bool:
        return True

    # -- to implement per portal ---------------------------------------------

    @abc.abstractmethod
    def build_urls(self, search: SearchConfig) -> list[str]:
        """Best-effort search URLs from structured filters (one per location
        or property type as needed). start_urls bypass this entirely."""

    @abc.abstractmethod
    def page_url(self, base_url: str, page: int) -> str:
        """URL of page N (N >= 2) of a search results URL."""

    @abc.abstractmethod
    def parse_page(self, html: str, search: SearchConfig) -> list[Listing]:
        """Parse one results page. Must be tolerant: a missing secondary field
        is a warning, never a reason to drop the listing."""

    # -- shared driver ---------------------------------------------------------

    def search(self, search: SearchConfig, runtime: RuntimeConfig) -> list[Listing]:
        start_url = search.start_urls.get(self.name)
        if start_url:
            base_urls = [start_url]  # pasted URL wins; we only handle pagination
        else:
            base_urls = self.build_urls(search)
        listings: list[Listing] = []
        seen_urls: set[str] = set()
        for base_url in base_urls:
            for page in range(1, max(1, runtime.max_pages_per_source) + 1):
                url = base_url if page == 1 else self.page_url(base_url, page)
                html = self.fetch(url, runtime)
                items = self.parse_page(html, search)
                fresh = [i for i in items if i.url not in seen_urls]
                if not fresh:
                    break  # empty/repeated page -> stop paginating this URL
                for item in fresh:
                    seen_urls.add(item.url)
                    listings.append(self._finalize(item, search))
                if page < runtime.max_pages_per_source:
                    self._sleep(runtime)
        return listings

    def fetch(self, url: str, runtime: RuntimeConfig) -> str:
        """HTTP GET with realistic headers, retries and exponential backoff."""
        last_error: Exception | None = None
        for attempt in range(1, MAX_FETCH_ATTEMPTS + 1):
            try:
                response = httpx.get(
                    url,
                    headers=self._headers(),
                    timeout=30.0,
                    follow_redirects=True,
                )
                if response.status_code in (403, 429):
                    raise SourceError(
                        f"{self.name}: HTTP {response.status_code} em {url} (bloqueio provável)"
                    )
                response.raise_for_status()
                return response.text
            except SourceError:
                raise
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < MAX_FETCH_ATTEMPTS:
                    backoff = 2**attempt + random.uniform(0, 1)
                    log.warning(
                        "%s: falha de rede (%s), retry %d/%d em %.1fs",
                        self.name, exc, attempt, MAX_FETCH_ATTEMPTS - 1, backoff,
                    )
                    time.sleep(backoff)
        raise SourceError(f"{self.name}: falha de rede persistente em {url}: {last_error}")

    # -- helpers ----------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "pt-PT,pt;q=0.9,en;q=0.6",
            "Referer": self.portal_root or "https://www.google.pt/",
            "Upgrade-Insecure-Requests": "1",
        }

    def _sleep(self, runtime: RuntimeConfig) -> None:
        low, high = runtime.request_delay_seconds
        time.sleep(random.uniform(low, high))

    def extract_id(self, url: str) -> str | None:
        if self.id_pattern is None:
            return None
        match = self.id_pattern.search(url)
        return match.group(1) if match else None

    def _finalize(self, listing: Listing, search: SearchConfig) -> Listing:
        listing.source = self.name
        listing.search_name = search.name
        listing.url = canonical_url(self.absolute_url(listing.url))
        native = self.extract_id(listing.url)
        listing.id = f"{self.name}:{native}" if native else hash_key(self.name, listing.url)
        return listing

    def absolute_url(self, url: str) -> str:
        if url.startswith("http"):
            return url
        return self.portal_root.rstrip("/") + "/" + url.lstrip("/")
