from __future__ import annotations

from pathlib import Path

import pytest

from casa_radar.core.config import AppConfig, ChannelConfig, RuntimeConfig, SearchConfig

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def search():
    return SearchConfig(
        name="Casa Feira",
        operation="buy",
        locations=["Santa Maria da Feira", "Fiães", "Lourosa"],
        price_max=350_000,
        typologies=["T3", "T4"],
        min_area_m2=100,
        keywords_exclude=["trespasse", "leilão", "penhora"],
        sources=["idealista", "imovirtual", "supercasa", "custojusto"],
    )


@pytest.fixture
def app_config(search):
    return AppConfig(searches=[search], runtime=RuntimeConfig())
