from datetime import datetime, timedelta, timezone

from casa_radar.dashboard.generator import generate, render
from casa_radar.core.state import State


def _populated_state(tmp_path, now):
    state = State(tmp_path / "state.json")
    state.data["meta"]["last_baseline_at"] = (now - timedelta(days=2)).isoformat()
    state.add_listing(
        "supercasa:1",
        {"first_seen": now.isoformat(), "last_seen": now.isoformat(),
         "last_price": 320_000, "title": "Moradia T3 em Fiães", "location": "Fiães",
         "rooms": 3, "area_m2": 150.0, "source": "supercasa",
         "search_name": "Casa Feira", "image_url": None, "fingerprint": None,
         "urls": ["https://x.pt/1", "https://y.pt/9"]},
    )
    state.add_run(
        {"at": now.isoformat(), "seen": {"supercasa": 30, "idealista": 0},
         "new": 1, "price_drops": 1, "errors": {}}
    )
    state.source_health("supercasa").update({"zero_streak": 0})
    state.source_health("idealista").update({"zero_streak": 5})
    state.add_event(
        {"type": "new", "at": now.isoformat(), "key": "supercasa:1",
         "title": "Moradia T3 em Fiães", "price": 320_000, "url": "https://x.pt/1",
         "image_url": None, "source": "supercasa", "search_name": "Casa Feira",
         "location": "Fiães", "rooms": 3, "area_m2": 150.0}
    )
    state.add_event(
        {"type": "price_drop", "at": now.isoformat(),  # "now": today regardless of UTC midnight
         "key": "supercasa:2", "title": "Apartamento T4 em Lourosa",
         "price": 240_000, "old_price": 255_000, "url": "https://x.pt/2",
         "image_url": None, "source": "supercasa", "search_name": "Casa Feira",
         "location": "Lourosa", "rooms": 4, "area_m2": 130.0}
    )
    return state


def test_render_contains_all_sections(tmp_path, app_config):
    now = datetime.now(timezone.utc)
    html = render(_populated_state(tmp_path, now), app_config, now)
    assert "Casa Radar" in html
    assert "corridas hoje" in html
    assert "Estado das fontes" in html
    assert "supercasa" in html and "idealista" in html
    assert "0 há 5h" in html                       # silent-block warning card
    assert "Apareceu recentemente" in html
    assert "em seguimento" in html
    assert "Moradia T3 em Fiães" in html
    assert "baixa de preço" in html
    assert "320.000 €" in html
    assert "<script" not in html                   # no-JS dashboard


def test_feed_is_event_based_so_baseline_lists_nothing(tmp_path, app_config):
    # A listing with NO event (e.g. registered during baseline) must not show
    # in the feed - only events (new / price drops) appear.
    now = datetime.now(timezone.utc)
    state = State(tmp_path / "state.json")
    state.data["meta"]["last_baseline_at"] = now.isoformat()
    state.add_listing(
        "custojusto:666",
        {"first_seen": now.isoformat(), "last_seen": now.isoformat(),
         "last_price": 19_500, "title": "Baseline House 42", "location": "Fiães",
         "rooms": 3, "area_m2": 150.0, "source": "custojusto",
         "search_name": "Casa Feira", "urls": ["https://x.pt/42"]},
    )
    html = render(state, app_config, now)
    assert "Baseline House 42" not in html          # baseline item never listed
    assert "Nada de novo ainda" in html             # empty feed message
    assert "1</b><span>em seguimento" in html       # but counted as tracked


def test_provider_filter_appears_with_multiple_sources(tmp_path, app_config):
    now = datetime.now(timezone.utc)
    state = State(tmp_path / "state.json")
    state.data["meta"]["last_baseline_at"] = (now - timedelta(days=1)).isoformat()
    for i, src in enumerate(("supercasa", "idealista_api", "imovirtual")):
        state.add_event(
            {"type": "new", "at": now.isoformat(), "key": f"{src}:{i}",
             "title": f"Moradia {i}", "price": 300000, "url": f"https://x.pt/{i}",
             "image_url": None, "source": src, "search_name": "Casa Feira",
             "location": "Fiães", "rooms": 3, "area_m2": 150.0}
        )
    html = render(state, app_config, now)
    assert 'name="prov"' in html                    # pure-CSS filter present
    assert 'id="f-idealista_api"' in html
    assert ">idealista<" in html                     # idealista_api shown as "idealista"
    assert "card src-imovirtual" in html             # cards tagged per provider


def test_sources_removed_from_config_are_hidden(tmp_path):
    from casa_radar.core.config import AppConfig, RuntimeConfig, SearchConfig

    now = datetime.now(timezone.utc)
    config = AppConfig(
        searches=[SearchConfig(name="Casa Feira", sources=["supercasa"])],
        runtime=RuntimeConfig(),
    )
    html = render(_populated_state(tmp_path, now), config, now)
    assert "supercasa" in html
    assert "0 há 5h" not in html


def test_render_empty_state_is_graceful(tmp_path, app_config):
    now = datetime.now(timezone.utc)
    html = render(State(tmp_path / "s.json"), app_config, now)
    assert "primeira corrida" in html
    assert "Nada de novo ainda" in html


def test_generate_writes_pages_files(tmp_path, app_config):
    now = datetime.now(timezone.utc)
    out = generate(_populated_state(tmp_path, now), app_config, now, tmp_path / "docs")
    assert out.exists()
    assert (tmp_path / "docs" / ".nojekyll").exists()
