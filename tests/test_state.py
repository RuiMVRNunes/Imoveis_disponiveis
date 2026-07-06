from datetime import datetime, timedelta, timezone

from casa_radar.core.state import State


def test_roundtrip(tmp_path):
    path = tmp_path / "state.json"
    state = State(path)
    state.add_listing("supercasa:1", {"first_seen": "2026-07-05T10:00:00+01:00", "urls": []})
    state.mark_baselined("Casa Feira")
    state.save()

    reloaded = State(path)
    assert "supercasa:1" in reloaded.listings
    assert reloaded.is_baselined("Casa Feira")
    assert not reloaded.is_baselined("Outra")


def test_alias_resolution(tmp_path):
    state = State(tmp_path / "state.json")
    state.add_listing("supercasa:1", {"urls": ["https://supercasa.pt/x-1"]})
    state.add_alias("idealista:9", "supercasa:1")
    key, entry = state.resolve("idealista:9")
    assert key == "supercasa:1"
    assert entry["urls"] == ["https://supercasa.pt/x-1"]
    assert state.resolve("desconhecida:0") is None


def test_corrupt_state_starts_clean(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    state = State(path)
    assert state.listings == {}
    assert path.with_suffix(".corrupt.json").exists()


def test_prune_drops_old_events_and_stale_listings(tmp_path):
    now = datetime.now(timezone.utc)
    state = State(tmp_path / "state.json")
    state.add_event({"type": "new", "at": (now - timedelta(days=10)).isoformat()})
    state.add_event({"type": "new", "at": now.isoformat()})
    state.add_listing("a:1", {"last_seen": (now - timedelta(days=120)).isoformat()})
    state.add_listing("a:2", {"last_seen": now.isoformat()})
    state.add_alias("b:9", "a:1")

    state.prune(now, history_days=7)

    assert len(state.data["events"]) == 1
    assert "a:1" not in state.listings
    assert "b:9" not in state.listings  # alias of pruned entry goes too
    assert "a:2" in state.listings


def test_runs_since(tmp_path):
    now = datetime.now(timezone.utc)
    state = State(tmp_path / "state.json")
    state.add_run({"at": (now - timedelta(hours=30)).isoformat(), "seen": {"x": 1}})
    state.add_run({"at": now.isoformat(), "seen": {"x": 2}})
    assert len(state.runs_since(now - timedelta(hours=24))) == 1
