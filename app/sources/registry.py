"""Source registry: maps codes to adapters. Only sources listed in ENABLED_SOURCES run."""

from __future__ import annotations

from app.sources.base import SourceAdapter
from app.sources.disabled import AllegroLokalnieSource, OlxSource
from app.sources.mock import MockSource

ALL_SOURCE_CODES = ["olx", "allegro_lokalnie", "mock"]
DISPLAY_NAMES = {"olx": "OLX.pl", "allegro_lokalnie": "Allegro Lokalnie", "mock": "Źródło testowe"}


class SourceRegistry:
    def __init__(self, adapters: dict[str, SourceAdapter], enabled: list[str]) -> None:
        self._adapters = adapters
        self._enabled = [code for code in enabled if code in adapters]

    @classmethod
    def default(cls, enabled: list[str], personal_mode: bool = False,
                min_request_interval: float = 10.0, timeout: float = 15.0) -> "SourceRegistry":
        """Commercial mode: OLX/Allegro are disabled stubs. Personal mode: OLX reads public offers."""
        olx: SourceAdapter = OlxSource()
        if personal_mode:
            from app.sources.http import PoliteFetcher
            from app.sources.olx import OlxPersonalSource

            olx = OlxPersonalSource(PoliteFetcher(min_interval=min_request_interval, timeout=timeout))
            enabled = list(dict.fromkeys([*enabled, "olx"]))
        adapters: dict[str, SourceAdapter] = {
            "olx": olx,
            "allegro_lokalnie": AllegroLokalnieSource(),
            "mock": MockSource(),
        }
        return cls(adapters, enabled)

    def get(self, code: str) -> SourceAdapter | None:
        return self._adapters.get(code)

    def is_enabled(self, code: str) -> bool:
        return code in self._enabled

    @property
    def enabled_codes(self) -> list[str]:
        return list(self._enabled)

    @property
    def selectable_codes(self) -> list[str]:
        """Sources a user may pick in the wizard (enabled ones only)."""
        return list(self._enabled)

    def all_codes(self) -> list[str]:
        return list(self._adapters)
