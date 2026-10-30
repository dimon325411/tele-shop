"""Time helpers: UTC clock, exponential backoff, quiet hours, token bucket."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone
from typing import Callable
from zoneinfo import ZoneInfo


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


Clock = Callable[[], datetime]


def backoff_seconds(attempt: int, base: float = 30.0, cap: float = 3600.0, jitter: float = 0.2,
                    rng: random.Random | None = None) -> float:
    """Exponential backoff: base * 2**(attempt-1), capped, with +-jitter fraction."""
    attempt = max(1, attempt)
    delay = min(cap, base * (2 ** (attempt - 1)))
    if jitter:
        r = (rng or random).uniform(-jitter, jitter)
        delay = max(0.0, delay * (1 + r))
    return min(delay, cap * (1 + jitter))


def in_quiet_hours(now_utc: datetime, start_hour: int | None, end_hour: int | None, tz: str) -> bool:
    if start_hour is None or end_hour is None or start_hour == end_hour:
        return False
    local = now_utc.astimezone(ZoneInfo(tz)).time()
    start, end = time(start_hour), time(end_hour)
    if start < end:
        return start <= local < end
    return local >= start or local < end  # e.g. 22 -> 7 crosses midnight


def quiet_hours_end(now_utc: datetime, start_hour: int | None, end_hour: int | None, tz: str) -> datetime:
    """UTC moment when the current quiet period ends (``now_utc`` if not in quiet hours)."""
    if not in_quiet_hours(now_utc, start_hour, end_hour, tz):
        return now_utc
    zone = ZoneInfo(tz)
    local = now_utc.astimezone(zone)
    candidate = local.replace(hour=end_hour, minute=0, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def local_day_start_utc(now_utc: datetime, tz: str) -> datetime:
    local = now_utc.astimezone(ZoneInfo(tz))
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


@dataclass
class TokenBucket:
    """In-process token bucket used for per-user command rate limiting."""

    rate_per_minute: int
    clock: Callable[[], float]
    _state: dict[int, tuple[float, float]] = field(default_factory=dict)

    def allow(self, key: int) -> bool:
        capacity = float(self.rate_per_minute)
        refill = capacity / 60.0
        now = self.clock()
        tokens, last = self._state.get(key, (capacity, now))
        tokens = min(capacity, tokens + (now - last) * refill)
        if tokens < 1.0:
            self._state[key] = (tokens, now)
            return False
        self._state[key] = (tokens - 1.0, now)
        if len(self._state) > 50_000:  # bound memory
            self._state.clear()
        return True
