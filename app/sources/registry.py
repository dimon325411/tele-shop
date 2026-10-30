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
    def default(cls, enabled: list[str]) -> "SourceRegistry":
        adapters: dict[str, SourceAdapter] = {
            "olx": OlxSource(),
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
