"""config.yaml loading and validation.

Design goals (per spec):
- Config is data, not code: re-read at the start of every run.
- A broken search must NOT kill the run: report the search/field with the
  problem and continue with the valid searches. Never fail silently.
- Secrets never live in the YAML - only in environment variables.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger("casa_radar.config")

VALID_OPERATIONS = {"buy", "rent"}
KNOWN_SOURCES = {"idealista", "imovirtual", "supercasa", "custojusto", "casasapo"}
KNOWN_SEARCH_FIELDS = {
    "name",
    "operation",
    "locations",
    "price_min",
    "price_max",
    "typologies",
    "min_area_m2",
    "keywords_exclude",
    "sources",
    "start_urls",
}
_URL_PLACEHOLDER_TOKENS = ("COLA_AQUI", "PASTE_HERE")


@dataclass
class SearchConfig:
    name: str
    operation: str = "buy"
    locations: list[str] = field(default_factory=list)
    price_min: int | None = None
    price_max: int | None = None
    typologies: list[str] = field(default_factory=list)
    min_area_m2: float | None = None
    keywords_exclude: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    start_urls: dict[str, str] = field(default_factory=dict)

    @property
    def wanted_rooms(self) -> set[int]:
        rooms: set[int] = set()
        for t in self.typologies:
            digits = "".join(ch for ch in str(t) if ch.isdigit())
            if digits:
                rooms.add(int(digits))
        return rooms


@dataclass
class ChannelConfig:
    enabled: bool = False
    to: str = ""
    provider: str = ""


@dataclass
class NotificationsConfig:
    email: ChannelConfig = field(default_factory=ChannelConfig)
    whatsapp: ChannelConfig = field(default_factory=ChannelConfig)
    telegram: ChannelConfig = field(default_factory=ChannelConfig)


@dataclass
class RuntimeConfig:
    max_pages_per_source: int = 2
    request_delay_seconds: tuple[float, float] = (2.0, 6.0)
    silent_block_threshold: int = 3
    daily_digest_hour: int = 22
    timezone: str = "Europe/Lisbon"
    notify_price_drops: bool = True
    min_price_drop_pct: float = 1.0  # drops below this % update state silently
    history_days: int = 30
    max_listings_per_message: int = 20
    dashboard_url: str = ""
    quiet_hours: tuple[int, int] = (0, 7)  # [start, end): queue alerts, deliver in the morning; [0,0] disables


@dataclass
class AppConfig:
    searches: list[SearchConfig] = field(default_factory=list)
    notifications: NotificationsConfig = field(default_factory=NotificationsConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    errors: list[str] = field(default_factory=list)  # human-readable, PT


def _parse_search(raw: Any, index: int, errors: list[str]) -> SearchConfig | None:
    label = f"pesquisa #{index + 1}"
    if not isinstance(raw, dict):
        errors.append(f"{label}: devia ser um mapa de campos, encontrei {type(raw).__name__}.")
        return None
    name = raw.get("name")
    if not name or not isinstance(name, str):
        errors.append(f"{label}: falta o campo obrigatório 'name'.")
        return None
    label = f"pesquisa '{name}'"

    for key in raw:
        if key not in KNOWN_SEARCH_FIELDS:
            errors.append(f"{label}: campo desconhecido '{key}' (ignorado — typo?).")

    operation = str(raw.get("operation", "buy")).lower()
    if operation not in VALID_OPERATIONS:
        errors.append(
            f"{label}: operation '{operation}' inválida (usa 'buy' ou 'rent'); a assumir 'buy'."
        )
        operation = "buy"

    def _as_list(key: str) -> list[str]:
        value = raw.get(key) or []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            errors.append(f"{label}: '{key}' devia ser uma lista; ignorado.")
            return []
        return [str(v) for v in value]

    def _as_number(key: str) -> float | None:
        value = raw.get(key)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            errors.append(f"{label}: '{key}' devia ser um número; ignorado.")
            return None

    sources = [s.lower() for s in _as_list("sources")]
    unknown = [s for s in sources if s not in KNOWN_SOURCES]
    for s in unknown:
        errors.append(f"{label}: fonte desconhecida '{s}' (ignorada).")
    sources = [s for s in sources if s in KNOWN_SOURCES]
    if not sources:
        errors.append(f"{label}: sem fontes válidas — pesquisa ignorada.")
        return None

    start_urls_raw = raw.get("start_urls") or {}
    start_urls: dict[str, str] = {}
    if not isinstance(start_urls_raw, dict):
        errors.append(f"{label}: 'start_urls' devia ser um mapa fonte->URL; ignorado.")
    else:
        for src, url in start_urls_raw.items():
            url = str(url or "").strip()
            if not url or any(tok in url for tok in _URL_PLACEHOLDER_TOKENS):
                continue  # placeholder still in place - just use structured filters
            if not url.startswith("http"):
                errors.append(f"{label}: start_url de '{src}' não parece um URL; ignorado.")
                continue
            start_urls[str(src).lower()] = url

    price_max = _as_number("price_max")
    price_min = _as_number("price_min")
    return SearchConfig(
        name=name,
        operation=operation,
        locations=_as_list("locations"),
        price_min=int(price_min) if price_min is not None else None,
        price_max=int(price_max) if price_max is not None else None,
        typologies=_as_list("typologies"),
        min_area_m2=_as_number("min_area_m2"),
        keywords_exclude=_as_list("keywords_exclude"),
        sources=sources,
        start_urls=start_urls,
    )


def _parse_notifications(raw: Any, errors: list[str]) -> NotificationsConfig:
    cfg = NotificationsConfig()
    if raw is None:
        return cfg
    if not isinstance(raw, dict):
        errors.append("notifications: formato inválido; a usar defaults (tudo desligado).")
        return cfg
    for channel in ("email", "whatsapp", "telegram"):
        entry = raw.get(channel)
        if entry is None:
            continue
        if not isinstance(entry, dict):
            errors.append(f"notifications.{channel}: formato inválido; canal desligado.")
            continue
        setattr(
            cfg,
            channel,
            ChannelConfig(
                enabled=bool(entry.get("enabled", False)),
                to=str(entry.get("to", "") or ""),
                provider=str(entry.get("provider", "") or ""),
            ),
        )
    return cfg


def _parse_runtime(raw: Any, errors: list[str]) -> RuntimeConfig:
    cfg = RuntimeConfig()
    if raw is None:
        return cfg
    if not isinstance(raw, dict):
        errors.append("runtime: formato inválido; a usar defaults.")
        return cfg
    for key, caster in (
        ("max_pages_per_source", int),
        ("silent_block_threshold", int),
        ("daily_digest_hour", int),
        ("history_days", int),
        ("max_listings_per_message", int),
        ("timezone", str),
        ("dashboard_url", str),
        ("notify_price_drops", bool),
        ("min_price_drop_pct", float),
    ):
        if key in raw:
            try:
                setattr(cfg, key, caster(raw[key]))
            except (TypeError, ValueError):
                errors.append(f"runtime.{key}: valor inválido '{raw[key]}'; a usar default.")
    delay = raw.get("request_delay_seconds")
    if delay is not None:
        try:
            low, high = float(delay[0]), float(delay[1])
            cfg.request_delay_seconds = (min(low, high), max(low, high))
        except (TypeError, ValueError, IndexError):
            errors.append("runtime.request_delay_seconds: devia ser [min, max]; a usar default.")
    quiet = raw.get("quiet_hours")
    if quiet is not None:
        try:
            start, end = int(quiet[0]), int(quiet[1])
            if not (0 <= start <= 23 and 0 <= end <= 23):
                raise ValueError
            cfg.quiet_hours = (start, end)
        except (TypeError, ValueError, IndexError):
            errors.append("runtime.quiet_hours: devia ser [inicio, fim] entre 0-23; a usar default.")
    if not 0 <= cfg.daily_digest_hour <= 23:
        errors.append("runtime.daily_digest_hour: fora de 0-23; a usar 22.")
        cfg.daily_digest_hour = 22
    return cfg


def load_config(path: str | Path = "config.yaml") -> AppConfig:
    """Load and validate the YAML config. Never raises: problems land in
    ``AppConfig.errors`` (and in the logs) and valid searches keep working."""
    errors: list[str] = []
    path = Path(path)
    if not path.exists():
        errors.append(f"config: ficheiro '{path}' não encontrado.")
        return AppConfig(errors=errors)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        errors.append(f"config: YAML inválido (indentação?): {exc}")
        return AppConfig(errors=errors)
    if not isinstance(data, dict):
        errors.append("config: o topo do ficheiro devia ser um mapa (searches/notifications/runtime).")
        return AppConfig(errors=errors)

    searches: list[SearchConfig] = []
    raw_searches = data.get("searches") or []
    if not isinstance(raw_searches, list):
        errors.append("config: 'searches' devia ser uma lista.")
        raw_searches = []
    seen_names: set[str] = set()
    for i, raw in enumerate(raw_searches):
        search = _parse_search(raw, i, errors)
        if search is None:
            continue
        if search.name in seen_names:
            errors.append(f"pesquisa '{search.name}': nome duplicado — a segunda foi ignorada.")
            continue
        seen_names.add(search.name)
        searches.append(search)
    if not searches:
        errors.append("config: nenhuma pesquisa válida — nada para monitorizar.")

    config = AppConfig(
        searches=searches,
        notifications=_parse_notifications(data.get("notifications"), errors),
        runtime=_parse_runtime(data.get("runtime"), errors),
        errors=errors,
    )
    for err in errors:
        log.warning("config: %s", err)
    return config
