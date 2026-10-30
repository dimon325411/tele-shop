"""Polite HTTP fetcher for *authorised* source access.

It is not used by any enabled source today (see docs/SOURCES.md). It exists so that, once
legal access is confirmed, an adapter can be written without re-implementing safety rules:

* minimum interval between requests per host (shared by all searches in this process);
* timeouts; retries with exponential backoff for 5xx/timeouts only;
* 403/429/CAPTCHA => ``SourceBlocked`` and a cool-down; we never retry around a block,
  rotate identities, solve CAPTCHAs or log in;
* identifies itself with an honest User-Agent.

The transport is injectable, so the logic is tested without network access.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urlparse

from app.domain.timing import backoff_seconds
from app.sources.base import SourceBlocked, SourceUnavailable

USER_AGENT = "telegram-market-monitor/0.1 (+contact: see bot /support)"
CAPTCHA_MARKERS = ("captcha", "cf-challenge", "are you a robot", "jesteś robotem", "px-captcha")


@dataclass
class HttpResponse:
    status: int
    text: str
    headers: dict[str, str]


Transport = Callable[[str, dict[str, str], float], Awaitable[HttpResponse]]


async def httpx_transport(url: str, headers: dict[str, str], timeout: float) -> HttpResponse:  # pragma: no cover
    import httpx  # imported lazily: optional at runtime

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url, headers=headers)
        return HttpResponse(resp.status_code, resp.text, {k.lower(): v for k, v in resp.headers.items()})


class PoliteFetcher:
    def __init__(
        self,
        transport: Transport = httpx_transport,
        min_interval: float = 10.0,
        timeout: float = 15.0,
        max_retries: int = 2,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._transport = transport
        self._min_interval = min_interval
        self._timeout = timeout
        self._max_retries = max_retries
        self._monotonic = monotonic
        self._sleep = sleep
        self._last_request: dict[str, float] = {}
        self._blocked_until: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def blocked_for(self, host: str) -> float:
        return max(0.0, self._blocked_until.get(host, 0.0) - self._monotonic())

    async def get(self, url: str) -> str:
        host = urlparse(url).netloc
        if self.blocked_for(host) > 0:
            raise SourceBlocked(f"{host} in cool-down", int(self.blocked_for(host)))
        lock = self._locks.setdefault(host, asyncio.Lock())
        attempt = 0
        while True:
            attempt += 1
            async with lock:
                wait = self._min_interval - (self._monotonic() - self._last_request.get(host, -1e9))
                if wait > 0:
                    await self._sleep(wait)
                self._last_request[host] = self._monotonic()
                try:
                    resp = await self._transport(url, {"User-Agent": USER_AGENT, "Accept-Language": "pl"}, self._timeout)
                except (asyncio.TimeoutError, OSError) as exc:
                    resp = None
                    error: Exception | None = exc
                else:
                    error = None
            if resp is not None:
                if resp.status in (403, 429) or self._looks_like_captcha(resp):
                    retry_after = self._retry_after(resp) or 3600
                    self._blocked_until[host] = self._monotonic() + retry_after
                    raise SourceBlocked(f"{host} answered {resp.status}", retry_after)
                if 200 <= resp.status < 300:
                    return resp.text
                if resp.status < 500:
                    raise SourceUnavailable(f"{host} answered {resp.status}")
                error = SourceUnavailable(f"{host} answered {resp.status}")
            if attempt > self._max_retries:
                raise SourceUnavailable(str(error))
            await self._sleep(backoff_seconds(attempt, base=2.0, cap=30.0, jitter=0.0))

    @staticmethod
    def _looks_like_captcha(resp: HttpResponse) -> bool:
        head = resp.text[:5000].lower()
        return any(marker in head for marker in CAPTCHA_MARKERS)

    @staticmethod
    def _retry_after(resp: HttpResponse) -> int | None:
        value = resp.headers.get("retry-after")
        return int(value) if value and value.isdigit() else None
