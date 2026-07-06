"""Local filtering of scraped listings.

Golden rule: NEVER lose a listing. A listing is only excluded when there is
positive evidence it violates a filter - missing/unparsable fields always pass.
When start_urls are used the portal already filtered; this is a cheap, tolerant
second pass that never throws away ads for lack of data.
"""

from __future__ import annotations

import logging

from .config import SearchConfig
from .models import Listing
from .utils import strip_accents

log = logging.getLogger("casa_radar.filters")


def passes_filters(listing: Listing, search: SearchConfig) -> bool:
    if listing.price is None:
        # "Preço sob consulta" is excluded by explicit user decision - the one
        # deliberate exception to the missing-fields-pass rule below.
        log.debug("filters: '%s' excluído (sem preço / sob consulta)", listing.title)
        return False
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

    if search.keywords_exclude:
        haystack = strip_accents(f"{listing.title} {listing.location}").lower()
        for keyword in search.keywords_exclude:
            if strip_accents(keyword).lower() in haystack:
                log.debug(
                    "filters: '%s' excluído pela keyword '%s'", listing.title, keyword
                )
                return False
    return True
