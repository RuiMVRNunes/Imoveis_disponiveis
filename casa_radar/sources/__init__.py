"""Source registry: each portal is an independent plugin implementing
SourceScraper. A failing source must never break the others (the runner
wraps every scraper call in try/except)."""

from __future__ import annotations

from .base import BaseSource
from .casasapo import CasaSapoSource
from .custojusto import CustoJustoSource
from .idealista import IdealistaSource
from .imovirtual import ImovirtualSource
from .supercasa import SupercasaSource

SOURCES: dict[str, type[BaseSource]] = {
    "idealista": IdealistaSource,
    "imovirtual": ImovirtualSource,
    "supercasa": SupercasaSource,
    "custojusto": CustoJustoSource,
    "casasapo": CasaSapoSource,
}


def build_source(name: str) -> BaseSource:
    try:
        return SOURCES[name]()
    except KeyError:
        raise ValueError(f"unknown source '{name}'") from None
