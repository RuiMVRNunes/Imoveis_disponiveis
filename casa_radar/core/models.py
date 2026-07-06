"""Core data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class Listing:
    """One property listing as scraped from a portal.

    ``id`` is the dedup key ("source:nativeid" or "source:<hash>"); sources may
    leave it empty and the dedup layer fills it in.
    """

    id: str
    source: str
    search_name: str
    title: str
    price: int | None  # EUR; None == "sob consulta"
    location: str
    area_m2: float | None
    rooms: int | None
    url: str  # canonical (tracking params stripped)
    image_url: str | None = None
    published_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def typology(self) -> str | None:
        return f"T{self.rooms}" if self.rooms is not None else None


@runtime_checkable
class SourceScraper(Protocol):
    name: str

    def search(self, search_config: Any, runtime: Any) -> list[Listing]: ...

    def is_enabled(self) -> bool: ...
