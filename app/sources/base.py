"""Common interface for listing sources (one adapter per marketplace)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.domain.models import Listing, SearchCriteria


class SourceError(Exception):
    """Base error for source adapters."""


class SourceUnavailable(SourceError):
    """Temporary failure (timeout, 5xx). The check is retried with backoff."""


class SourceBlocked(SourceError):
    """The source refused us (403/429/CAPTCHA). Collection pauses; never try to bypass."""

    def __init__(self, message: str, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class SourceDisabled(SourceError):
    """The source is intentionally disabled (no confirmed legal access, or admin switch)."""


class SourceLayoutChanged(SourceError):
    """The page structure changed and could not be parsed; needs a developer."""


@dataclass
class SourceCapabilities:
    """Which filters the source can apply itself; the rest is done by our matcher."""

    keywords: bool = True
    price: bool = False
    category: bool = False
    condition: bool = False
    location: bool = False
    radius: bool = False
    delivery: bool = False
    sort_newest: bool = False
    # Whether listings from this source carry a category we can filter on locally.
    listing_category: bool = True
    notes: list[str] = field(default_factory=list)


class SourceAdapter(ABC):
    code: str = ""
    display_name: str = ""
    is_test_source: bool = False

    @abstractmethod
    def capabilities(self) -> SourceCapabilities: ...

    @abstractmethod
    async def fetch(self, criteria: SearchCriteria, limit: int = 50) -> list[Listing]:
        """Return the newest listings for ``criteria``. Raise SourceError subclasses on failure."""

    async def close(self) -> None:  # pragma: no cover - optional
        return None
