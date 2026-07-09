"""Small parsing/formatting helpers shared by sources and notifiers."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Requires the euro sign and rejects digits glued to T-typologies ("T3") so
# that free-text card parsing never mistakes "T3" or "142 m2" for a price.
_PRICE_RE = re.compile(r"(?<![\dTt])(\d{1,3}(?:[.\s]\d{3})+|\d+)(?:,\d+)?\s*€")
_AREA_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m[²2]", re.IGNORECASE)
_ROOMS_RE = re.compile(r"\bT\s?(\d+)", re.IGNORECASE)


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )


def slugify(text: str) -> str:
    """"Santa Maria da Feira" -> "santa-maria-da-feira" (portal URL slugs)."""
    text = strip_accents(text).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _clean_spaces(text: str) -> str:
    # NBSP / narrow NBSP show up as thousands separators on the portals
    return text.replace(" ", " ").replace(" ", " ")


def parse_price(text: str | None) -> int | None:
    """"350.000 €" -> 350000; "Preço sob consulta" -> None."""
    if not text:
        return None
    m = _PRICE_RE.search(_clean_spaces(text))
    if not m:
        return None
    digits = re.sub(r"[^\d]", "", m.group(1))
    if not digits:
        return None
    value = int(digits)
    return value if value > 0 else None


def parse_area(text: str | None) -> float | None:
    """"120 m² área bruta" -> 120.0."""
    if not text:
        return None
    m = _AREA_RE.search(_clean_spaces(text))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def parse_rooms(text: str | None) -> int | None:
    """"Moradia T3 em Fiães" -> 3."""
    if not text:
        return None
    m = _ROOMS_RE.search(text)
    return int(m.group(1)) if m else None


def set_query_param(url: str, key: str, value: str | int) -> str:
    """Return ``url`` with ``key=value`` set (replacing any existing value)."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != key]
    query.append((key, str(value)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def fmt_price(price: int | None) -> str:
    if price is None:
        return "sob consulta"
    return f"{price:,.0f}".replace(",", ".") + " €"


def fmt_source_date(value: str | None) -> str | None:
    """Format a portal's publish/update date ('2026-07-04 16:20:11' or ISO) as
    'DD/MM'. Returns None when there is no parseable date."""
    from datetime import datetime

    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text).strftime("%d/%m")
    except ValueError:
        pass
    # tolerate a bare date at the start ("2026-07-04 ...")
    try:
        return datetime.fromisoformat(text[:10]).strftime("%d/%m")
    except ValueError:
        return None
