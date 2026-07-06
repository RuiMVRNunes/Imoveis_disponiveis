from casa_radar.core.config import load_config

VALID_YAML = """
searches:
  - name: "Casa Feira"
    operation: buy
    locations: ["Santa Maria da Feira"]
    price_max: 350000
    typologies: ["T3", "T4"]
    min_area_m2: 100
    keywords_exclude: ["leilão"]
    sources: ["imovirtual", "supercasa"]
    start_urls:
      idealista: "COLA_AQUI_O_URL_JA_FILTRADO"
      supercasa: "https://supercasa.pt/comprar-casas/santa-maria-da-feira"

notifications:
  email: { enabled: true, to: "eu@exemplo.com" }

runtime:
  max_pages_per_source: 3
  request_delay_seconds: [1, 2]
  daily_digest_hour: 21
  min_price_drop_pct: 2.5
  quiet_hours: [23, 8]
"""


def test_valid_config_loads(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(VALID_YAML, encoding="utf-8")
    config = load_config(path)
    assert len(config.searches) == 1
    search = config.searches[0]
    assert search.wanted_rooms == {3, 4}
    # placeholder start_url is dropped; the real one is kept
    assert "idealista" not in search.start_urls
    assert "supercasa" in search.start_urls
    assert config.runtime.max_pages_per_source == 3
    assert config.runtime.daily_digest_hour == 21
    assert config.runtime.min_price_drop_pct == 2.5
    assert config.runtime.quiet_hours == (23, 8)
    assert config.notifications.email.enabled is True
    assert config.errors == []


def test_broken_search_is_skipped_but_valid_one_survives(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        """
searches:
  - operation: buy
    sources: ["imovirtual"]
  - name: "Boa"
    operation: comprar
    price_max: "trezentos"
    typologie: ["T3"]
    sources: ["imovirtual", "portalinventado"]
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert [s.name for s in config.searches] == ["Boa"]
    joined = " ".join(config.errors)
    assert "name" in joined            # missing name reported
    assert "typologie" in joined       # typo reported
    assert "portalinventado" in joined # unknown source reported
    assert "price_max" in joined       # bad number reported
    assert config.searches[0].operation == "buy"  # bad operation -> default


def test_invalid_yaml_reports_and_never_raises(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("searches:\n  - name: 'x'\n   bad_indent: 1\n", encoding="utf-8")
    config = load_config(path)
    assert config.searches == []
    assert any("YAML" in e for e in config.errors)


def test_missing_file(tmp_path):
    config = load_config(tmp_path / "nope.yaml")
    assert config.searches == []
    assert config.errors
