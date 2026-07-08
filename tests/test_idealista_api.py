"""idealista_api plugin: parse a saved JSON response (no live API calls) and
verify the runner's quota throttle + monthly cap."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

import casa_radar.core.runner as runner_mod
from casa_radar.core.config import AppConfig, RuntimeConfig, SearchConfig
from casa_radar.core.state import State
from casa_radar.sources.idealista_api import IdealistaApiSource
from tests.conftest import load_fixture


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


# -- runner throttle + monthly cap ------------------------------------------


class _StubApi:
    """Metered source stub: counts how many times it's actually called."""

    calls = 0

    def __init__(self):
        type(self).calls += 1  # a call == build_source + search below

    name = "idealista_api"

    def is_enabled(self):
        return True

    def search(self, search, runtime):
        return []


@pytest.fixture(autouse=True)
def _reset_stub():
    _StubApi.calls = 0
    yield


def _env(tmp_path, monkeypatch, *, min_interval=None, cap=140):
    monkeypatch.setattr(runner_mod, "build_source", lambda name: _StubApi())
    monkeypatch.setattr(runner_mod, "build_notifiers", lambda config: [])
    monkeypatch.setattr(runner_mod, "METERED_SOURCES", {"idealista_api"})
    config = AppConfig(
        searches=[SearchConfig(name="Casa", sources=["idealista_api"],
                               idealista_urls=["https://www.idealista.pt/comprar-casas/arouca/"])],
        runtime=RuntimeConfig(
            min_interval_hours={"idealista_api": min_interval} if min_interval else {},
            rapidapi_monthly_cap=cap,
            quiet_hours=(0, 0),
        ),
    )
    state = State(tmp_path / "state.json")
    return config, state


def test_throttle_skips_within_interval(tmp_path, monkeypatch):
    config, state = _env(tmp_path, monkeypatch, min_interval=4)
    runner_mod.run_once(config, state, dashboard_dir=str(tmp_path / "d"))
    assert _StubApi.calls == 1
    # run 1 was called and saw 0 (stub returns []), so streak is 1
    assert state.source_health("idealista_api")["zero_streak"] == 1
    # immediate second run: interval not elapsed -> source is not called
    runner_mod.run_once(config, state, dashboard_dir=str(tmp_path / "d"))
    assert _StubApi.calls == 1
    # a throttled skip must NOT count as another zero-seen (still 1, not 2)
    assert state.source_health("idealista_api")["zero_streak"] == 1


def test_monthly_cap_blocks_further_calls(tmp_path, monkeypatch):
    config, state = _env(tmp_path, monkeypatch, cap=1)
    runner_mod.run_once(config, state, dashboard_dir=str(tmp_path / "d"))
    assert _StubApi.calls == 1
    assert state.data["meta"]["rapidapi_count"] == 1
    # cap=1 already reached -> next run skips
    runner_mod.run_once(config, state, dashboard_dir=str(tmp_path / "d"))
    assert _StubApi.calls == 1


def test_counter_rolls_over_next_month(tmp_path, monkeypatch):
    config, state = _env(tmp_path, monkeypatch, cap=1)
    state.data["meta"]["rapidapi_month"] = "2020-01"
    state.data["meta"]["rapidapi_count"] = 999
    runner_mod.run_once(config, state, dashboard_dir=str(tmp_path / "d"))
    # old month ignored -> fresh count, call goes through
    assert _StubApi.calls == 1
    assert state.data["meta"]["rapidapi_count"] == 1
