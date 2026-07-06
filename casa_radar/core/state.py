"""Persistent state (state.json): what we have seen, run history, source health.

Layout:
{
  "meta":     {"version": 1, "created_at": iso, "baselined_searches": [...]},
  "listings": {key: {first_seen, last_seen, last_price, title, location, rooms,
                     area_m2, source, search_name, image_url, fingerprint,
                     urls: [...]}
               or {alias_of: key}},          # cross-source duplicate pointer
  "sources":  {name: {zero_streak, alerted, last_ok}},
  "runs":     [{at, seen: {source: n}, new, price_drops, errors: {source: msg},
                duration_s}],
  "events":   [{type: new|price_drop, at, key, ...listing fields, old_price}]
}
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("casa_radar.state")

LISTING_TTL_DAYS = 90  # forget listings not seen for this long
MAX_RUNS_KEPT = 400    # > 2 weeks of hourly runs


class State:
    def __init__(self, path: str | Path = "state.json") -> None:
        self.path = Path(path)
        self.data: dict[str, Any] = {
            "meta": {"version": 1, "created_at": None, "baselined_searches": []},
            "listings": {},
            "sources": {},
            "runs": [],
            "events": [],
            # alerts queued during quiet hours, delivered on the first
            # morning run: {"new": [...], "drops": [...], "blocks": [[src, h]]}
            "pending": {"new": [], "drops": [], "blocks": []},
        }
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and "listings" in loaded:
                    for key in self.data:
                        if key in loaded:
                            self.data[key] = loaded[key]
            except (json.JSONDecodeError, OSError) as exc:
                # Corrupt state would re-trigger a full baseline, which is safe
                # but noisy - keep a backup for forensics and start clean.
                log.error("state: '%s' ilegível (%s); a começar de novo.", self.path, exc)
                try:
                    self.path.rename(self.path.with_suffix(".corrupt.json"))
                except OSError:
                    pass

    # -- listings -----------------------------------------------------------

    @property
    def listings(self) -> dict[str, Any]:
        return self.data["listings"]

    def resolve(self, key: str) -> tuple[str, dict[str, Any]] | None:
        """Return (canonical_key, entry), following alias pointers."""
        entry = self.listings.get(key)
        if entry is None:
            return None
        if "alias_of" in entry:
            canonical = self.listings.get(entry["alias_of"])
            if canonical is None or "alias_of" in canonical:
                return None
            return entry["alias_of"], canonical
        return key, entry

    def add_listing(self, key: str, entry: dict[str, Any]) -> None:
        self.listings[key] = entry

    def add_alias(self, alias_key: str, canonical_key: str) -> None:
        self.listings[alias_key] = {"alias_of": canonical_key}

    # -- baseline -----------------------------------------------------------

    def is_baselined(self, search_name: str) -> bool:
        return search_name in self.data["meta"].get("baselined_searches", [])

    def mark_baselined(self, search_name: str) -> None:
        names = self.data["meta"].setdefault("baselined_searches", [])
        if search_name not in names:
            names.append(search_name)

    def clear_baselines(self) -> None:
        self.data["meta"]["baselined_searches"] = []

    # -- source health ------------------------------------------------------

    def source_health(self, name: str) -> dict[str, Any]:
        return self.data["sources"].setdefault(
            name, {"zero_streak": 0, "last_ok": None, "last_alert_at": None}
        )

    @property
    def pending(self) -> dict[str, list[Any]]:
        pending = self.data.setdefault("pending", {})
        for key in ("new", "drops", "blocks"):
            pending.setdefault(key, [])
        return pending

    # -- history ------------------------------------------------------------

    def add_run(self, run: dict[str, Any]) -> None:
        self.data["runs"].append(run)
        self.data["runs"] = self.data["runs"][-MAX_RUNS_KEPT:]

    def add_event(self, event: dict[str, Any]) -> None:
        self.data["events"].append(event)

    def runs_since(self, cutoff: datetime) -> list[dict[str, Any]]:
        return [r for r in self.data["runs"] if _parse_iso(r.get("at")) >= cutoff]

    def events_since(self, cutoff: datetime) -> list[dict[str, Any]]:
        return [e for e in self.data["events"] if _parse_iso(e.get("at")) >= cutoff]

    # -- maintenance --------------------------------------------------------

    def prune(self, now: datetime, history_days: int) -> None:
        event_cutoff = now - timedelta(days=max(1, history_days))
        self.data["events"] = [
            e for e in self.data["events"] if _parse_iso(e.get("at")) >= event_cutoff
        ]
        listing_cutoff = now - timedelta(days=LISTING_TTL_DAYS)
        stale = {
            key
            for key, entry in self.listings.items()
            if "alias_of" not in entry
            and _parse_iso(entry.get("last_seen") or entry.get("first_seen")) < listing_cutoff
        }
        if stale:
            log.info("state: a esquecer %d anúncios com >%d dias", len(stale), LISTING_TTL_DAYS)
        for key in list(self.listings):
            entry = self.listings[key]
            if key in stale or entry.get("alias_of") in stale:
                del self.listings[key]

    def save(self) -> None:
        if self.data["meta"].get("created_at") is None:
            self.data["meta"]["created_at"] = datetime.now().astimezone().isoformat()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: never leave a half-written state.json behind.
        fd, tmp_path = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, ensure_ascii=False, indent=1)
            os.replace(tmp_path, self.path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


_VERY_OLD = datetime(1970, 1, 2, tzinfo=timezone.utc)


def _parse_iso(value: Any) -> datetime:
    """Lenient ISO parse; unknown timestamps sort as 'very old'."""
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.astimezone()
            return parsed
        except ValueError:
            pass
    return _VERY_OLD
