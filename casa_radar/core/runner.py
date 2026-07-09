"""One run of the radar: scrape -> filter -> dedup -> events -> notify ->
dashboard -> persist. Sources and notification channels are isolated: any of
them failing never takes the run down."""

from __future__ import annotations

import logging
import random
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..notifiers import build_notifiers
from ..notifiers.base import Notifier, NotifyError
from ..notifiers.messages import (
    build_baseline_message,
    build_block_alert,
    build_heartbeat,
    build_new_single,
    build_run_digest,
)
from ..sources import METERED_SOURCES, build_source
from ..sources.idealista_api import IdealistaApiSource
from .config import AppConfig
from .dedup import find_property_match, fingerprint, primary_key
from .filters import passes_filters
from .models import Listing
from .state import State, _parse_iso

log = logging.getLogger("casa_radar.runner")

# When <= this many new listings show up, send one message per listing
# instead of a digest (spec, section 7).
INDIVIDUAL_MESSAGE_LIMIT = 3

# Silent-block active alerts: at most one per source per this window; the
# daily heartbeat carries the ongoing status (user decision, 14-B7).
BLOCK_ALERT_COOLDOWN_HOURS = 24

# Removed-listing detection (daily, at digest hour): check detail URLs of
# tracked listings, oldest-checked first, capped per day to stay low-volume.
# idealista is skipped (DataDome would 403 and prove nothing).
REMOVAL_CHECK_CAP = 40
REMOVAL_CHECK_SOURCES = {"imovirtual", "supercasa", "custojusto", "casasapo"}


@dataclass
class RunResult:
    at: datetime
    seen_by_source: dict[str, int] = field(default_factory=dict)
    errors_by_source: dict[str, str] = field(default_factory=dict)
    new_events: list[dict[str, Any]] = field(default_factory=list)
    drop_events: list[dict[str, Any]] = field(default_factory=list)
    removed_events: list[dict[str, Any]] = field(default_factory=list)
    idealista_detail: dict[str, int] = field(default_factory=dict)  # concelho -> seen
    baseline_counts: dict[str, int] = field(default_factory=dict)  # search -> registered
    block_alerts: list[tuple[str, int]] = field(default_factory=list)  # (source, hours)
    duration_s: float = 0.0


def run_once(
    config: AppConfig,
    state: State,
    *,
    dry_run: bool = False,
    only_source: str | None = None,
    force_baseline: bool = False,
    dashboard_dir: str = "docs",
) -> RunResult:
    tz = _safe_tz(config.runtime.timezone)
    now = datetime.now(tz)
    started = time.monotonic()
    result = RunResult(at=now)
    if force_baseline:
        # Fresh slate: rebuilding the baseline also wipes the event history
        # (dashboard cards) and any queued quiet-hours alerts, so junk from a
        # bad config/parser run disappears instead of lingering for 30 days.
        state.clear_baselines()
        state.data["events"] = []
        state.data["pending"] = {"new": [], "drops": [], "blocks": []}

    seen = defaultdict(int)
    sources_used: set[str] = set()
    baseline_searches: set[str] = set()
    metered_skip: set[str] = set()  # metered sources throttled/capped this run

    for search in config.searches:
        is_baseline = not state.is_baselined(search.name)
        if is_baseline:
            baseline_searches.add(search.name)
        for source_name in search.sources:
            if only_source and source_name != only_source:
                continue
            metered_urls: list[str] | None = None
            if source_name in METERED_SOURCES:
                metered_urls = _metered_gate(
                    source_name, search, state, config, now, metered_skip, is_baseline
                )
                if metered_urls is None:
                    continue  # throttled or over monthly cap: not a zero-seen
            sources_used.add(source_name)
            try:
                scraper = build_source(source_name)
                if not scraper.is_enabled():
                    continue
                if metered_urls is not None:
                    scraper.run_urls = metered_urls
                listings = scraper.search(search, config.runtime)
                for url, count in getattr(scraper, "run_detail", {}).items():
                    result.idealista_detail[_concelho_label(url)] = count
            except Exception as exc:  # source isolation: log, count 0, move on
                log.error("runner: fonte '%s' falhou em '%s': %s", source_name, search.name, exc)
                result.errors_by_source[source_name] = str(exc)
                listings = []
            seen[source_name] += len(listings)
            prefiltered = source_name in search.start_urls
            kept = [
                l for l in listings
                if passes_filters(l, search, portal_prefiltered=prefiltered)
            ]
            log.info(
                "runner: %s/'%s': %d vistos, %d após filtros%s",
                source_name, search.name, len(listings), len(kept),
                " (baseline)" if is_baseline else "",
            )
            for listing in kept:
                _process_listing(listing, state, result, now, is_baseline, config)

    result.seen_by_source = dict(seen)
    _update_source_health(state, result, sources_used, now, config)

    for name in baseline_searches:
        result.baseline_counts.setdefault(name, 0)
        if not dry_run:
            state.mark_baselined(name)
    if baseline_searches and not dry_run:
        # Stamp used by the dashboard inventory: only listings seen after the
        # last baseline count as "currently tracked" (keeps pre-fix junk out).
        state.data["meta"]["last_baseline_at"] = now.isoformat()

    _record_history(state, result, now)

    if dry_run:
        _print_dry_run(result)
    else:
        if now.hour == config.runtime.daily_digest_hour:
            result.removed_events = _check_removals(state, now)
        _notify(config, result, state, now)
        _generate_dashboard(config, state, now, dashboard_dir)
        state.prune(now, config.runtime.history_days)
        state.save()

    result.duration_s = time.monotonic() - started
    log.info(
        "runner: corrida concluída em %.1fs — vistos=%s novos=%d baixas=%d",
        result.duration_s, dict(seen), len(result.new_events), len(result.drop_events),
    )
    return result


# -- listing processing ----------------------------------------------------------


def _process_listing(
    listing: Listing,
    state: State,
    result: RunResult,
    now: datetime,
    is_baseline: bool,
    config: AppConfig,
) -> None:
    key = primary_key(listing)
    resolved = state.resolve(key)
    if resolved is not None:
        canonical_key, entry = resolved
        entry["last_seen"] = now.isoformat()
        entry.pop("removed_at", None)  # it's back on the market after all
        if listing.url not in entry.setdefault("urls", []):
            entry["urls"].append(listing.url)
        old_price = entry.get("last_price")
        if listing.price is not None and old_price is not None and listing.price < old_price:
            entry["last_price"] = listing.price
            drop_pct = (old_price - listing.price) / old_price * 100
            if (
                config.runtime.notify_price_drops
                and not is_baseline
                and drop_pct >= config.runtime.min_price_drop_pct
            ):
                result.drop_events.append(
                    _event_from_listing(listing, "price_drop", now, canonical_key, old_price)
                )
        elif listing.price is not None:
            entry["last_price"] = listing.price
        return

    match_key = find_property_match(listing, state.listings)
    if match_key is not None:
        # Same physical property already known from another portal: group the
        # URL, add an alias so future runs hit layer 1, never re-notify.
        _, entry = state.resolve(match_key) or (match_key, state.listings[match_key])
        if listing.url not in entry.setdefault("urls", []):
            entry["urls"].append(listing.url)
        entry["last_seen"] = now.isoformat()
        state.add_alias(key, match_key)
        log.info("runner: '%s' agrupado com %s (mesmo imóvel)", listing.title, match_key)
        return

    state.add_listing(
        key,
        {
            "first_seen": now.isoformat(),
            "last_seen": now.isoformat(),
            "last_price": listing.price,
            "title": listing.title,
            "location": listing.location,
            "rooms": listing.rooms,
            "area_m2": listing.area_m2,
            "source": listing.source,
            "search_name": listing.search_name,
            "image_url": listing.image_url,
            "fingerprint": fingerprint(listing),
            "urls": [listing.url],
        },
    )
    if is_baseline:
        result.baseline_counts[listing.search_name] = (
            result.baseline_counts.get(listing.search_name, 0) + 1
        )
    else:
        result.new_events.append(_event_from_listing(listing, "new", now, key))


def _event_from_listing(
    listing: Listing, kind: str, now: datetime, key: str, old_price: int | None = None
) -> dict[str, Any]:
    return {
        "type": kind,
        "at": now.isoformat(),
        "key": key,
        "title": listing.title,
        "price": listing.price,
        "old_price": old_price,
        "url": listing.url,
        "image_url": listing.image_url,
        "source": listing.source,
        "search_name": listing.search_name,
        "location": listing.location,
        "rooms": listing.rooms,
        "area_m2": listing.area_m2,
    }


# -- source health / silent-block detection ---------------------------------------


ANTI_DOUBLE_HOURS = 3  # don't run idealista_api twice within the same schedule window


def _metered_gate(
    source_name: str,
    search: Any,
    state: State,
    config: AppConfig,
    now: datetime,
    metered_skip: set[str],
    is_baseline: bool = False,
) -> list[str] | None:
    """Gate a metered source (idealista_api). Returns the URLs to hit this run
    or None to skip.

    Baseline: capture ALL concelhos once, regardless of the schedule, so nothing
    floods as "new" on the first real window. Normal: every token does at most 1
    request per schedule window (one token per concelho -> all run each window;
    a shared token rotates its concelhos). run_hours pins the windows (8/14/20 ->
    3x/day/token). Each token has its own monthly cap. A None is deliberate rest,
    NOT a zero-seen block."""
    if source_name in metered_skip:
        return None
    runtime = config.runtime
    health = state.source_health(source_name)
    last = health.get("last_attempt_at")

    if not is_baseline:
        run_hours = runtime.idealista_run_hours
        if run_hours:
            if now.hour not in run_hours:
                metered_skip.add(source_name)
                return None
            if isinstance(last, str) and now - _parse_iso(last) < timedelta(hours=ANTI_DOUBLE_HOURS):
                metered_skip.add(source_name)  # already ran this window
                return None
        else:
            interval = runtime.min_interval_hours.get(source_name)
            if interval and isinstance(last, str) and now - _parse_iso(last) < timedelta(hours=interval):
                log.info("runner: %s em pausa (intervalo mínimo %sh)", source_name, interval)
                metered_skip.add(source_name)
                return None

    all_urls = IdealistaApiSource._search_urls(search)
    if not all_urls:
        health["last_attempt_at"] = now.isoformat()
        return []  # nothing configured: let the source raise its clear error

    # Group URLs by the token that serves them.
    groups: dict[str, list[str]] = {}
    for url in all_urls:
        token = search.idealista_url_keys.get(url) or "RAPIDAPI_KEY"
        groups.setdefault(token, []).append(url)

    cursors = state.data["meta"].setdefault("idealista_cursor", {})
    working = dict(_rapidapi_counts(state, now))  # copy: local cap bookkeeping
    cap = runtime.rapidapi_monthly_cap
    chosen: list[str] = []
    for token, urls in groups.items():
        # baseline: all this token's URLs; normal: just the next one (rotate)
        picks = urls if is_baseline else [urls[int(cursors.get(f"{search.name}|{token}", 0)) % len(urls)]]
        for url in picks:
            if working.get(token, 0) >= cap:
                log.warning("runner: token %s no tecto mensal (%d) — concelho saltado", token, cap)
                continue
            chosen.append(url)
            working[token] = working.get(token, 0) + 1
            _add_rapidapi_count(state, now, token, 1)
        if not is_baseline:
            cursors[f"{search.name}|{token}"] = (
                int(cursors.get(f"{search.name}|{token}", 0)) + 1
            ) % len(urls)

    if not chosen:
        metered_skip.add(source_name)
        return None
    health["last_attempt_at"] = now.isoformat()
    return chosen


def _rapidapi_counts(state: State, now: datetime) -> dict[str, int]:
    meta = state.data["meta"]
    if meta.get("rapidapi_month") != now.strftime("%Y-%m"):
        return {}  # new month -> counters roll over
    counts = meta.get("rapidapi_count")
    return counts if isinstance(counts, dict) else {}


def _add_rapidapi_count(state: State, now: datetime, token: str, n: int) -> None:
    meta = state.data["meta"]
    month = now.strftime("%Y-%m")
    if meta.get("rapidapi_month") != month or not isinstance(meta.get("rapidapi_count"), dict):
        meta["rapidapi_month"] = month
        meta["rapidapi_count"] = {}
    meta["rapidapi_count"][token] = int(meta["rapidapi_count"].get(token, 0)) + n


def _update_source_health(
    state: State, result: RunResult, sources_used: set[str], now: datetime, config: AppConfig
) -> None:
    threshold = config.runtime.silent_block_threshold
    for name in sorted(sources_used):
        health = state.source_health(name)
        if result.seen_by_source.get(name, 0) > 0:
            health["zero_streak"] = 0
            health["last_ok"] = now.isoformat()
        else:
            # 0 seen covers both a DataDome block and a broken parser (spec).
            health["zero_streak"] = int(health.get("zero_streak", 0)) + 1
            # Active alert only on the transition into blocked state, at most
            # once per cooldown window; the daily heartbeat carries the rest.
            last_alert = health.get("last_alert_at")
            cooldown_over = (
                not isinstance(last_alert, str)
                or now - _parse_iso(last_alert) >= timedelta(hours=BLOCK_ALERT_COOLDOWN_HOURS)
            )
            if health["zero_streak"] == threshold and cooldown_over:
                health["last_alert_at"] = now.isoformat()
                result.block_alerts.append((name, health["zero_streak"]))


# -- history / notifications / dashboard -------------------------------------------


def _concelho_label(url: str) -> str:
    """Human label for an idealista search URL, e.g. 'santa-maria-da-feira'."""
    for marker in ("comprar-casas/", "arrendar-casas/"):
        if marker in url:
            return url.split(marker, 1)[1].split("/")[0].split("?")[0] or url
    return url


def _record_history(state: State, result: RunResult, now: datetime) -> None:
    state.add_run(
        {
            "at": now.isoformat(),
            "seen": result.seen_by_source,
            "new": len(result.new_events),
            "price_drops": len(result.drop_events),
            "errors": result.errors_by_source,
            "idealista": result.idealista_detail,  # concelho -> anúncios obtidos
        }
    )
    for event in result.new_events + result.drop_events:
        state.add_event(event)


def _in_quiet_hours(now: datetime, start: int, end: int) -> bool:
    if start == end:
        return False  # [0,0] (or any equal pair) disables quiet hours
    if start < end:
        return start <= now.hour < end
    return now.hour >= start or now.hour < end  # window wraps past midnight


def _notify(config: AppConfig, result: RunResult, state: State, now: datetime) -> None:
    notifiers = build_notifiers(config)
    if not notifiers:
        log.warning("runner: nenhum canal de notificação ativo/configurado")
    runtime = config.runtime
    pending = state.pending

    new_events = list(result.new_events)
    drop_events = list(result.drop_events)
    block_alerts = list(result.block_alerts)

    if _in_quiet_hours(now, *runtime.quiet_hours):
        # Queue everything except the baseline confirmation (that one is the
        # direct answer to a manual setup step - deliver it immediately).
        pending["new"].extend(new_events)
        pending["drops"].extend(drop_events)
        pending["blocks"].extend([list(b) for b in block_alerts])
        if new_events or drop_events or block_alerts:
            log.info(
                "runner: horas de silêncio (%02dh-%02dh) — %d alerta(s) guardados para a manhã",
                *runtime.quiet_hours,
                len(new_events) + len(drop_events) + len(block_alerts),
            )
        new_events, drop_events, block_alerts = [], [], []
    elif pending["new"] or pending["drops"] or pending["blocks"]:
        log.info(
            "runner: a entregar %d alerta(s) acumulados das horas de silêncio",
            len(pending["new"]) + len(pending["drops"]) + len(pending["blocks"]),
        )
        new_events = pending["new"] + new_events
        drop_events = pending["drops"] + drop_events
        block_alerts = [tuple(b) for b in pending["blocks"]] + block_alerts
        state.data["pending"] = {"new": [], "drops": [], "blocks": []}

    messages: list[tuple[str, str, str | None]] = []
    if result.baseline_counts:
        sources_total = len(result.seen_by_source) or 1
        sources_ok = sum(1 for n in result.seen_by_source.values() if n > 0)
        messages.append(
            build_baseline_message(result.baseline_counts, sources_ok, sources_total)
        )
    if new_events or drop_events:
        if len(new_events) <= INDIVIDUAL_MESSAGE_LIMIT and not drop_events:
            for event in new_events:
                messages.append(build_new_single(event, runtime.dashboard_url))
        else:
            messages.append(
                build_run_digest(
                    new_events,
                    drop_events,
                    runtime.dashboard_url,
                    runtime.max_listings_per_message,
                )
            )
    for source, streak in block_alerts:
        messages.append(build_block_alert(source, streak))

    if now.hour == runtime.daily_digest_hour:
        messages.append(_heartbeat_message(config, state, now, result.removed_events))

    for subject, text, html in messages:
        _broadcast(notifiers, subject, text, html)


def _heartbeat_message(
    config: AppConfig, state: State, now: datetime, removed_events: list[dict[str, Any]]
):
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    runs_today = state.runs_since(day_start)
    configured = {s for search in config.searches for s in search.sources}
    status: dict[str, str] = {}
    for name, health in sorted(state.data["sources"].items()):
        if name not in configured:
            continue  # source removed from config: old health is stale noise
        if health.get("zero_streak", 0) >= config.runtime.silent_block_threshold:
            last_ok = health.get("last_ok")
            status[name] = f"⚠️ 0 há {health['zero_streak']}h" + (
                f" (último OK {last_ok[11:16]})" if isinstance(last_ok, str) and len(last_ok) > 16 else ""
            )
        else:
            status[name] = "✅"
    events_today = state.events_since(day_start)
    return build_heartbeat(
        now.strftime("%d/%m"),
        len(runs_today),
        status,
        sum(1 for e in events_today if e.get("type") == "new"),
        sum(1 for e in events_today if e.get("type") == "price_drop"),
        config.errors,
        config.runtime.dashboard_url,
        removed_events,
    )


# -- removed-listing detection ------------------------------------------------------


def _url_is_gone(url: str) -> bool | None:
    """True = definitively gone (404/410); False = still live; None = unknown
    (network trouble, anti-bot, weird status) - never mark removed on None."""
    try:
        response = httpx.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "pt-PT,pt;q=0.9",
            },
            timeout=15.0,
            follow_redirects=True,
        )
    except httpx.HTTPError:
        return None
    if response.status_code in (404, 410):
        return True
    if response.status_code == 200:
        return False
    return None


def _check_removals(state: State, now: datetime) -> list[dict[str, Any]]:
    """Once a day, probe detail URLs of tracked listings (oldest-checked
    first, capped) and flag the ones that got sold/pulled."""
    candidates = []
    for key, entry in state.listings.items():
        if "alias_of" in entry or entry.get("removed_at"):
            continue
        if entry.get("source") not in REMOVAL_CHECK_SOURCES:
            continue
        if not entry.get("urls"):
            continue
        candidates.append((str(entry.get("removal_check_at") or ""), key))
    candidates.sort()

    removed: list[dict[str, Any]] = []
    for _, key in candidates[:REMOVAL_CHECK_CAP]:
        entry = state.listings[key]
        entry["removal_check_at"] = now.isoformat()
        gone = _url_is_gone(entry["urls"][0])
        time.sleep(random.uniform(0.5, 1.5))
        if gone is not True:
            continue
        entry["removed_at"] = now.isoformat()
        days = max(0, (now - _parse_iso(entry.get("first_seen"))).days)
        event = {
            "type": "removed",
            "at": now.isoformat(),
            "key": key,
            "title": entry.get("title"),
            "price": entry.get("last_price"),
            "url": entry["urls"][0],
            "source": entry.get("source"),
            "search_name": entry.get("search_name"),
            "location": entry.get("location"),
            "rooms": entry.get("rooms"),
            "area_m2": entry.get("area_m2"),
            "days_on_market": days,
        }
        state.add_event(event)
        removed.append(event)
        log.info("runner: '%s' desapareceu (%d dias no mercado)", entry.get("title"), days)
    return removed


def _broadcast(
    notifiers: list[Notifier], subject: str, text: str, html: str | None
) -> None:
    for notifier in notifiers:
        try:
            notifier.send(subject, text, html)
            log.info("notify: '%s' enviado via %s", subject, notifier.name)
        except NotifyError as exc:  # channel isolation
            log.error("notify: %s", exc)
        except Exception as exc:
            log.exception("notify: falha inesperada no canal %s: %s", notifier.name, exc)


def _generate_dashboard(config: AppConfig, state: State, now: datetime, out_dir: str) -> None:
    try:
        from ..dashboard.generator import generate

        generate(state, config, now, out_dir)
    except Exception as exc:
        log.exception("runner: geração do dashboard falhou: %s", exc)


def _print_dry_run(result: RunResult) -> None:
    print("\n=== DRY RUN — nada foi enviado nem gravado ===")
    print(f"Vistos por fonte: {result.seen_by_source}")
    if result.errors_by_source:
        print(f"Erros por fonte: {result.errors_by_source}")
    if result.baseline_counts:
        print(f"Baseline (registaria): {result.baseline_counts}")
    print(f"Novos ({len(result.new_events)}):")
    for event in result.new_events:
        print(f"  + {event['title']} — {event['price']} — {event['url']}")
    print(f"Baixas de preço ({len(result.drop_events)}):")
    for event in result.drop_events:
        print(f"  ↓ {event['title']} — {event['old_price']} -> {event['price']}")
    if result.block_alerts:
        print(f"Alertas de bloqueio: {result.block_alerts}")


def _safe_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except Exception:
        log.warning("runner: timezone '%s' inválida, a usar Europe/Lisbon", name)
        return ZoneInfo("Europe/Lisbon")
