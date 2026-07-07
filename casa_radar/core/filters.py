"""Local filtering of scraped listings.

Golden rule: NEVER lose a listing. A listing is only excluded when there is
positive evidence it violates a filter - missing/unparsable fields always
pass (the deliberate exception: no price / "sob consulta", excluded by
explicit user decision).

When a search has a start_url for a source, the portal URL *is* the filter:
the structured price/typology/area checks are skipped for that source so the
YAML never silently fights the pasted URL. Keyword excludes, property type
and buy/rent guards still apply everywhere.
"""

from __future__ import annotations

import logging

from .config import SearchConfig
from .models import Listing
from .utils import strip_accents

log = logging.getLogger("casa_radar.filters")

# Title/hint keywords per property type (accent-stripped, lowercase).
PROPERTY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "moradia": ("moradia", "vivenda", "casa ", "quinta", "villa"),
    "apartamento": ("apartamento", "penthouse", "duplex", "estudio", "loft", "andar de moradia"),
}


def passes_filters(
    listing: Listing, search: SearchConfig, *, portal_prefiltered: bool = False
) -> bool:
    raw = listing.raw or {}

    # Buy/rent guard: sources tag the operation when the portal exposes it
    # (imovirtual transaction, custojusto URL category). A rent ad passing a
    # 450k buy filter at 750 EUR was exactly the leak this plugs.
    operation_hint = raw.get("operation")
    if operation_hint and operation_hint != search.operation:
        return False

    if listing.price is None:
        # "Preço sob consulta" - excluded by explicit user decision.
        log.debug("filters: '%s' excluído (sem preço / sob consulta)", listing.title)
        return False

    if not portal_prefiltered:
        if search.price_max is not None and listing.price > search.price_max:
            return False
        if search.price_min is not None and listing.price < search.price_min:
            return False
        if (
            listing.area_m2 is not None
            and search.min_area_m2 is not None
            and listing.area_m2 < search.min_area_m2
        ):
            return False
        wanted_rooms = search.wanted_rooms
        if listing.rooms is not None and wanted_rooms and listing.rooms not in wanted_rooms:
            return False

    if not _matches_property_types(listing, search.property_types):
        return False

    if search.keywords_exclude:
        haystack = strip_accents(f"{listing.title} {listing.location}").lower()
        for keyword in search.keywords_exclude:
            if strip_accents(keyword).lower() in haystack:
                log.debug(
                    "filters: '%s' excluído pela keyword '%s'", listing.title, keyword
                )
                return False
    return True


def _matches_property_types(listing: Listing, wanted: list[str]) -> bool:
    """Positive keyword of a wanted type -> pass; positive keyword of another
    known type -> exclude; no evidence either way -> pass (golden rule)."""
    if not wanted:
        return True
    hint = (listing.raw or {}).get("property_type")
    if hint:
        return hint in wanted
    text = strip_accents(listing.title or "").lower()
    for wanted_type in wanted:
        if any(kw in text for kw in PROPERTY_KEYWORDS.get(wanted_type, (wanted_type,))):
            return True
    for other_type, keywords in PROPERTY_KEYWORDS.items():
        if other_type not in wanted and any(kw in text for kw in keywords):
            log.debug("filters: '%s' excluído (é %s)", listing.title, other_type)
            return False
    return True
