"""End-to-end runner tests with a fake source and a capturing notifier -
baseline mode, new-listing alerts, price drops, cross-source grouping and
silent-block detection, all without network."""

from __future__ import annotations

import copy
from datetime import datetime

import pytest

import casa_radar.core.runner as runner_mod
from casa_radar.core.config import AppConfig, RuntimeConfig, SearchConfig
from casa_radar.core.models import Listing
from casa_radar.core.runner import run_once
from casa_radar.core.state import State


def make_listing(n: int, source: str = "supercasa", **overrides) -> Listing:
    defaults = dict(
        id="",
        source=source,
        search_name="Casa Feira",
        title=f"Moradia T3 número {n}",
        price=300_000 + n * 1000,
        location="Fiães",
        area_m2=150.0 + n,
        rooms=3,
        url=f"https://{source}.pt/venda-moradia-{n}-9876543{n}",
    )
    defaults.update(overrides)
    return Listing(**defaults)


class FakeScraper:
    """Stands in for any portal; serves whatever the test programmed."""

    def __init__(self, name: str, catalog: dict[str, list[Listing]]):
        self.name = name
        self.catalog = catalog

    def is_enabled(self) -> bool:
        return True

    def search(self, search, runtime) -> list[Listing]:
        from casa_radar.core.dedup import canonical_url, hash_key

        results = []
        for listing in copy.deepcopy(self.catalog.get(self.name, [])):
            listing.search_name = search.name
            listing.url = canonical_url(listing.url)
            if not listing.id:
                listing.id = hash_key(listing.source, listing.url)
            results.append(listing)
        return results


class CapturingNotifier:
    name = "capture"

    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def is_enabled(self) -> bool:
        return True

    def send(self, subject: str, text: str, html=None) -> None:
        self.sent.append((subject, text))


@pytest.fixture
def env(tmp_path, monkeypatch):
    catalog: dict[str, list[Listing]] = {"supercasa": [], "custojusto": []}
    notifier = CapturingNotifier()
    monkeypatch.setattr(runner_mod, "build_source", lambda name: FakeScraper(name, catalog))
    monkeypatch.setattr(runner_mod, "build_notifiers", lambda config: [notifier])

    off_hour = (datetime.now().hour + 2) % 24  # keep heartbeat out of the way
    config = AppConfig(
        searches=[
            SearchConfig(
                name="Casa Feira",
                operation="buy",
                locations=["Fiães"],
                price_max=350_000,
                typologies=["T3"],
                sources=["supercasa", "custojusto"],
            )
        ],
        runtime=RuntimeConfig(
            daily_digest_hour=off_hour,
            silent_block_threshold=3,
            quiet_hours=(0, 0),  # disabled by default; specific tests re-enable
        ),
    )
    state = State(tmp_path / "state.json")
    docs = tmp_path / "docs"

    def run(**kwargs):
        return run_once(config, state, dashboard_dir=str(docs), **kwargs)

    return type(
        "Env", (), dict(catalog=catalog, notifier=notifier, config=config,
                        state=state, docs=docs, run=staticmethod(run))
    )


def test_first_run_is_baseline_with_single_confirmation(env):
    env.catalog["supercasa"] = [make_listing(1), make_listing(2)]
    result = env.run()

    assert result.new_events == []
    assert result.baseline_counts == {"Casa Feira": 2}
    assert len(env.notifier.sent) == 1
    subject, text = env.notifier.sent[0]
    assert "baseline criado" in text
    assert "2 anúncios" in text
    assert env.state.is_baselined("Casa Feira")
    assert (env.docs / "index.html").exists()


def test_second_run_alerts_only_new_listings(env):
    env.catalog["supercasa"] = [make_listing(1), make_listing(2)]
    env.run()
    env.notifier.sent.clear()

    env.catalog["supercasa"] = [make_listing(1), make_listing(2), make_listing(3)]
    result = env.run()

    assert len(result.new_events) == 1
    assert result.new_events[0]["title"] == "Moradia T3 número 3"
    # <= 3 new -> individual message, tagged with the search name
    assert len(env.notifier.sent) == 1
    subject, text = env.notifier.sent[0]
    assert "Casa Feira" in subject
    assert "Moradia T3 número 3" in text


def test_price_drop_is_notified_and_not_new(env):
    env.catalog["supercasa"] = [make_listing(1)]
    env.run()
    env.notifier.sent.clear()

    env.catalog["supercasa"] = [make_listing(1, price=290_000)]
    result = env.run()

    assert result.new_events == []
    assert len(result.drop_events) == 1
    event = result.drop_events[0]
    assert event["old_price"] == 301_000 and event["price"] == 290_000
    assert any("baixa" in s.lower() or "baixa" in t.lower() for s, t in env.notifier.sent)


def test_cross_source_duplicate_is_grouped_not_renotified(env):
    env.catalog["supercasa"] = [make_listing(1)]
    env.run()
    env.notifier.sent.clear()

    # Same physical house shows up on custojusto (slightly different numbers)
    env.catalog["custojusto"] = [
        make_listing(
            1,
            source="custojusto",
            url="https://custojusto.pt/aveiro/moradia-t3-11223344",
            price=302_000,   # +0.3%
            area_m2=152.0,   # +1 m2
        )
    ]
    result = env.run()

    assert result.new_events == []
    assert env.notifier.sent == []
    entries = [e for e in env.state.listings.values() if "alias_of" not in e]
    assert len(entries) == 1
    assert len(entries[0]["urls"]) == 2


def test_silent_block_alert_fires_once_at_threshold(env):
    env.catalog["supercasa"] = [make_listing(1)]
    env.catalog["custojusto"] = [make_listing(9, source="custojusto",
                                              url="https://custojusto.pt/x-99887766")]
    env.run()
    env.notifier.sent.clear()

    env.catalog["custojusto"] = []  # custojusto goes silent (block or broken parser)
    alerts = []
    for _ in range(4):
        result = env.run()
        alerts.extend(result.block_alerts)
    # threshold=3 -> exactly one alert, on the 3rd consecutive zero
    assert alerts == [("custojusto", 3)]
    assert any("custojusto" in s for s, _ in env.notifier.sent)


def test_heartbeat_ignores_sources_removed_from_config(env):
    env.state.source_health("casasapo")["zero_streak"] = 99  # stale leftover
    env.state.source_health("supercasa")["zero_streak"] = 0
    now = datetime.now().astimezone()
    _, text, _ = runner_mod._heartbeat_message(env.config, env.state, now, [])
    assert "casasapo" not in text
    assert "supercasa" in text


def test_dry_run_writes_nothing(env, tmp_path):
    env.catalog["supercasa"] = [make_listing(1)]
    env.run(dry_run=True)
    assert not (tmp_path / "state.json").exists()
    assert not env.docs.exists()
    assert env.notifier.sent == []


def test_small_price_drop_is_silent(env):
    env.catalog["supercasa"] = [make_listing(1, price=300_000)]
    env.run()
    env.notifier.sent.clear()

    env.catalog["supercasa"] = [make_listing(1, price=298_000)]  # -0.67% < 1%
    result = env.run()

    assert result.drop_events == []
    assert env.notifier.sent == []
    # price still updated silently, so the next drop is measured from 298k
    entry = next(e for e in env.state.listings.values() if "alias_of" not in e)
    assert entry["last_price"] == 298_000


def test_quiet_hours_queue_and_morning_flush(env):
    env.catalog["supercasa"] = [make_listing(1)]
    # keep custojusto healthy so no block alert muddies the assertions
    env.catalog["custojusto"] = [make_listing(7, source="custojusto",
                                              url="https://custojusto.pt/x-77665544")]
    env.run()
    env.notifier.sent.clear()

    # Force the quiet window over the current hour: new listing gets queued
    now_hour = datetime.now().hour
    env.config.runtime.quiet_hours = (now_hour, (now_hour + 1) % 24)
    env.catalog["supercasa"] = [make_listing(1), make_listing(2)]
    result = env.run()

    assert len(result.new_events) == 1
    assert env.notifier.sent == []
    assert len(env.state.pending["new"]) == 1

    # Morning: quiet window no longer covers the current hour -> flush
    env.config.runtime.quiet_hours = ((now_hour + 1) % 24, (now_hour + 2) % 24)
    env.run()

    assert len(env.notifier.sent) == 1
    _, text = env.notifier.sent[0]
    assert "Moradia T3 número 2" in text
    assert env.state.pending["new"] == []


def test_removal_check_flags_gone_listings(env, monkeypatch):
    env.catalog["supercasa"] = [make_listing(1), make_listing(2)]
    env.run()

    monkeypatch.setattr(
        runner_mod, "_url_is_gone", lambda url: True if "moradia-1" in url else False
    )
    monkeypatch.setattr(runner_mod.time, "sleep", lambda s: None)
    now = datetime.now().astimezone()
    removed = runner_mod._check_removals(env.state, now)

    assert len(removed) == 1
    assert removed[0]["title"] == "Moradia T3 número 1"
    assert removed[0]["days_on_market"] == 0
    entry = env.state.resolve(removed[0]["key"])[1]
    assert entry["removed_at"]
    # second day: already flagged, not probed again
    assert runner_mod._check_removals(env.state, now) == []


def test_block_alert_cooldown_suppresses_oscillation(env):
    env.catalog["supercasa"] = [make_listing(1)]
    env.catalog["custojusto"] = [make_listing(9, source="custojusto",
                                              url="https://custojusto.pt/x-99887766")]
    env.run()

    env.catalog["custojusto"] = []
    alerts = []
    for _ in range(3):
        alerts.extend(env.run().block_alerts)
    assert alerts == [("custojusto", 3)]

    # source recovers, then goes silent again within the 24h cooldown
    env.catalog["custojusto"] = [make_listing(9, source="custojusto",
                                              url="https://custojusto.pt/x-99887766")]
    env.run()
    env.catalog["custojusto"] = []
    alerts = []
    for _ in range(3):
        alerts.extend(env.run().block_alerts)
    assert alerts == []  # suppressed: only the daily digest carries the status


def test_baseline_flag_rebuilds_without_alert_flood(env):
    env.catalog["supercasa"] = [make_listing(1)]
    env.run()
    env.catalog["supercasa"] = [make_listing(1), make_listing(2)]
    env.run()  # listing 2 -> a "new" event lands in the dashboard history
    assert len(env.state.data["events"]) == 1
    env.notifier.sent.clear()

    env.catalog["supercasa"] = [make_listing(1), make_listing(2), make_listing(5)]
    result = env.run(force_baseline=True)
    assert result.new_events == []
    assert result.baseline_counts["Casa Feira"] == 1  # only the unseen one registered
    assert env.state.data["events"] == []  # fresh slate: dashboard junk wiped
