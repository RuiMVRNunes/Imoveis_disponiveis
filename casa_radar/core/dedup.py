"""Deduplication, 3 layers (per spec):

1. Portal-native ID extracted from the URL (e.g. "idealista:33445566") -
   primary key inside the same portal, resolves ~95%.
2. Hash fallback: sha1(source + canonical_url) when no clean ID exists.
   Tracking params (utm_*, rank, ...) are stripped BEFORE hashing.
3. Cross-source property fingerprint: same property listed on several portals
   with different IDs. Match on normalized location + typology + area (+/-3 m2)
   + price (+/-2%) -> notify once, group the URLs.

Golden rule: when in doubt, DON'T merge - a duplicate alert beats a lost house.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Listing
from .utils import strip_accents

AREA_TOLERANCE_M2 = 3.0
PRICE_TOLERANCE_PCT = 0.02
AREA_BUCKET_M2 = 5

_TRACKING_PARAM_PREFIXES = ("utm_", "mc_", "pk_")
_TRACKING_PARAMS = {
    "rank",
    "order",
    "ordem",
    "fbclid",
    "gclid",
    "msclkid",
    "ref",
    "referer",
    "referrer",
    "origin",
    "source",
    "shid",
    "sessionid",
}


def canonical_url(url: str) -> str:
    """Strip tracking params and fragments so the same ad hashes the same."""
    parts = urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAMS
        and not key.lower().startswith(_TRACKING_PARAM_PREFIXES)
    ]
    path = re.sub(r"/{2,}", "/", parts.path) or "/"
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), "")
    )


def hash_key(source: str, url: str) -> str:
    digest = hashlib.sha1((source + canonical_url(url)).encode("utf-8")).hexdigest()
    return f"{source}:h{digest[:16]}"


def primary_key(listing: Listing) -> str:
    """Layer 1 (native id, already extracted by the source) or layer 2 (hash)."""
    if listing.id:
        return listing.id
    return hash_key(listing.source, listing.url)


def normalize_location(location: str) -> str:
    text = strip_accents(location or "").lower()
    return re.sub(r"[^a-z0-9]+", "", text)


def fingerprint(listing: Listing) -> str | None:
    """Coarse cross-source signature; None when there is not enough data to
    match safely (missing area or price -> never merge, golden rule)."""
    if listing.area_m2 is None or listing.rooms is None or listing.price is None:
        return None
    loc = normalize_location(listing.location)
    if not loc:
        return None
    area_bucket = int(round(listing.area_m2 / AREA_BUCKET_M2) * AREA_BUCKET_M2)
    return f"{loc}|t{listing.rooms}|{area_bucket}"


def _locations_compatible(a: str, b: str) -> bool:
    if not a or not b:
        return False
    return a == b or a in b or b in a


def is_same_property(listing: Listing, entry: dict[str, Any]) -> bool:
    """Tolerant compare of a fresh listing against a stored entry."""
    if entry.get("source") == listing.source:
        return False  # same-portal relisting is treated as new (spec, section 5)
    if listing.rooms is None or entry.get("rooms") is None:
        return False
    if listing.rooms != entry["rooms"]:
        return False
    if listing.area_m2 is None or entry.get("area_m2") is None:
        return False
    if abs(listing.area_m2 - float(entry["area_m2"])) > AREA_TOLERANCE_M2:
        return False
    if listing.price is None or entry.get("last_price") is None:
        return False
    reference = max(listing.price, int(entry["last_price"]))
    if abs(listing.price - int(entry["last_price"])) > reference * PRICE_TOLERANCE_PCT:
        return False
    return _locations_compatible(
        normalize_location(listing.location), normalize_location(entry.get("location", ""))
    )


def find_property_match(listing: Listing, listings: dict[str, Any]) -> str | None:
    """Layer 3: return the key of an existing entry that is (very likely) the
    same physical property seen on another portal, else None."""
    if fingerprint(listing) is None:
        return None
    for key, entry in listings.items():
        if "alias_of" in entry:
            continue
        if is_same_property(listing, entry):
            return key
    return None
