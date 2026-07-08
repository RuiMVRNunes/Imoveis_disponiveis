"""idealista_api plugin: parse a saved JSON response (no live API calls) and
verify the runner's schedule + one-token-per-concelho + per-token cap."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import casa_radar.core.runner as runner_mod
from casa_radar.core.config import AppConfig, RuntimeConfig, SearchConfig
from casa_radar.core.state import State
from casa_radar.sources.idealista_api import IdealistaApiSource
from tests.conftest import load_fixture

LISBON = ZoneInfo("Europe/Lisbon")


def test_parse_element_maps_all_fields(search):
    source = IdealistaApiSource()
    listings = source.parse_page(load_fixture("idealista_api.json"), search)
    assert len(listings) == 2

    house = source._finalize(listings[0], search)
    assert house.id == "idealista_api:33445566"
    assert house.price == 315_000
    assert house.rooms == 3
    assert house.area_m2 == 142.0
    assert house.location == "Rua das Flores, Fiães"
    assert house.image_url.endswith("33445566.jpg")   # images confirmed
    assert house.url == "https://www.idealista.pt/imovel/33445566/"
    assert house.raw["operation"] == "buy"
    assert house.raw["property_type"] == "moradia"     # chalet -> moradia
    assert house.raw["lat"] == 40.99

    flat = listings[1]
    assert flat.price == 289_000                        # falls back to priceInfo
    assert flat.raw["property_type"] == "apartamento"   # flat -> apartamento
    assert flat.image_url is None                        # no photo, still kept


def test_property_type_filter_drops_the_flat(search):
    from casa_radar.core.filters import passes_filters

    search.property_types = ["moradia"]
    source = IdealistaApiSource()
    listings = [source._finalize(l, search) for l in
                source.parse_page(load_fixture("idealista_api.json"), search)]
    kept = [l for l in listings if passes_filters(l, search)]
    assert [l.raw["property_type"] for l in kept] == ["moradia"]


# -- runner: scheduling, per-token rotation, per-token cap ------------------


class _StubApi:
    """Metered source stub: records the URL subset the runner injects."""

    calls = 0
    seen_urls: list[list[str]] = []
    run_urls: list[str] = []

    def __init__(self):
        type(self).calls += 1

    name = "idealista_api"

    def is_enabled(self):
        return True

    def search(self, search, runtime):
        type(self).seen_urls.append(list(self.run_urls))
        return []


@pytest.fixture(autouse=True)
def _reset_stub():
    _StubApi.calls = 0
    _StubApi.seen_urls = []
    _StubApi.run_urls = []
    yield


def _env(tmp_path, monkeypatch, *, urls, url_keys=None, run_hours=None, cap=95, baselined=True):
    monkeypatch.setattr(runner_mod, "build_source", lambda name: _StubApi())
    monkeypatch.setattr(runner_mod, "build_notifiers", lambda config: [])
    monkeypatch.setattr(runner_mod, "METERED_SOURCES", {"idealista_api"})
    now_hour = datetime.now(LISBON).hour
    config = AppConfig(
        searches=[SearchConfig(name="Casa", sources=["idealista_api"],
                               idealista_urls=urls, idealista_url_keys=url_keys or {})],
        runtime=RuntimeConfig(
            idealista_run_hours=[now_hour] if run_hours is None else run_hours,
            rapidapi_monthly_cap=cap,
            quiet_hours=(0, 0),
        ),
    )
    state = State(tmp_path / "state.json")
    if baselined:
        state.mark_baselined("Casa")  # exercise the normal scheduled path
    return config, state


def _run(config, state, tmp_path):
    return runner_mod.run_once(config, state, dashboard_dir=str(tmp_path / "d"))


def test_one_token_per_concelho_runs_all_each_window(tmp_path, monkeypatch):
    urls = ["uFeira", "uOvar"]
    keys = {"uFeira": "K1", "uOvar": "K2"}
    config, state = _env(tmp_path, monkeypatch, urls=urls, url_keys=keys)
    _run(config, state, tmp_path)
    assert set(_StubApi.seen_urls[0]) == {"uFeira", "uOvar"}  # both, one per token
    counts = state.data["meta"]["rapidapi_count"]
    assert counts["K1"] == 1 and counts["K2"] == 1


def test_shared_token_rotates_one_per_window(tmp_path, monkeypatch):
    urls = ["u0", "u1", "u2"]  # no per-url keys -> all share RAPIDAPI_KEY
    config, state = _env(tmp_path, monkeypatch, urls=urls)
    for _ in range(3):
        state.source_health("idealista_api")["last_attempt_at"] = None  # bypass anti-double
        _run(config, state, tmp_path)
    assert _StubApi.seen_urls == [["u0"], ["u1"], ["u2"]]
    assert state.data["meta"]["rapidapi_count"]["RAPIDAPI_KEY"] == 3


def test_off_window_hours_skip_without_calling(tmp_path, monkeypatch):
    off_hour = (datetime.now(LISBON).hour + 3) % 24
    config, state = _env(tmp_path, monkeypatch, urls=["u0"], run_hours=[off_hour])
    _run(config, state, tmp_path)
    assert _StubApi.calls == 0            # source never built/called
    assert _StubApi.seen_urls == []
    # a scheduled skip is not a silent-block
    assert state.source_health("idealista_api").get("zero_streak", 0) == 0


def test_per_token_cap_skips_only_that_token(tmp_path, monkeypatch):
    urls = ["uFeira", "uOvar"]
    keys = {"uFeira": "K1", "uOvar": "K2"}
    config, state = _env(tmp_path, monkeypatch, urls=urls, url_keys=keys, cap=5)
    now = datetime.now(LISBON)
    state.data["meta"]["rapidapi_month"] = now.strftime("%Y-%m")
    state.data["meta"]["rapidapi_count"] = {"K1": 5}  # K1 already at cap
    _run(config, state, tmp_path)
    assert _StubApi.seen_urls == [["uOvar"]]           # only K2's concelho runs
    assert state.data["meta"]["rapidapi_count"]["K2"] == 1


def test_anti_double_within_same_window(tmp_path, monkeypatch):
    urls = ["uFeira", "uOvar"]
    keys = {"uFeira": "K1", "uOvar": "K2"}
    config, state = _env(tmp_path, monkeypatch, urls=urls, url_keys=keys)
    _run(config, state, tmp_path)
    _run(config, state, tmp_path)  # immediate second run -> same window, skipped
    assert len(_StubApi.seen_urls) == 1


def test_baseline_captures_all_concelhos_off_window(tmp_path, monkeypatch):
    # A baseline must register every concelho regardless of the schedule, so
    # none floods as "new" later. Force an off-window hour to prove the bypass.
    off_hour = (datetime.now(LISBON).hour + 5) % 24
    urls = ["uFeira", "uOvar", "uEspinho"]
    keys = {"uFeira": "K1", "uOvar": "K2", "uEspinho": "K3"}
    config, state = _env(tmp_path, monkeypatch, urls=urls, url_keys=keys,
                         run_hours=[off_hour], baselined=False)
    _run(config, state, tmp_path)  # first run == baseline
    assert set(_StubApi.seen_urls[0]) == {"uFeira", "uOvar", "uEspinho"}
    counts = state.data["meta"]["rapidapi_count"]
    assert counts == {"K1": 1, "K2": 1, "K3": 1}


def test_counter_rolls_over_next_month(tmp_path, monkeypatch):
    config, state = _env(tmp_path, monkeypatch, urls=["u0"], url_keys={"u0": "K1"})
    state.data["meta"]["rapidapi_month"] = "2020-01"
    state.data["meta"]["rapidapi_count"] = {"K1": 999}
    _run(config, state, tmp_path)
    assert _StubApi.seen_urls == [["u0"]]              # old month ignored
    assert state.data["meta"]["rapidapi_count"]["K1"] == 1
