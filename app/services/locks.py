"""Distributed locks: one check per search at a time, even with several workers."""

from __future__ import annotations

import asyncio
import secrets
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Protocol


class Locker(Protocol):
    def hold(self, key: str, ttl_seconds: int) -> "AsyncLockContext": ...


class AsyncLockContext(Protocol):
    async def __aenter__(self) -> bool: ...
    async def __aexit__(self, *exc: object) -> None: ...


class MemoryLocker:
    """Process-local lock with TTL semantics (tests and single-process runs)."""

    def __init__(self, monotonic=time.monotonic) -> None:
        self._held: dict[str, float] = {}
        self._monotonic = monotonic

    @asynccontextmanager
    async def hold(self, key: str, ttl_seconds: int) -> AsyncIterator[bool]:
        await asyncio.sleep(0)
        now = self._monotonic()
        expires = self._held.get(key)
        if expires is not None and expires > now:
            yield False
            return
        self._held[key] = now + ttl_seconds
        try:
            yield True
        finally:
            self._held.pop(key, None)


_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""


class RedisLocker:
    """SET NX PX lock with a random token; released only by its owner."""

    def __init__(self, redis) -> None:  # redis.asyncio.Redis
        self._redis = redis

    @asynccontextmanager
    async def hold(self, key: str, ttl_seconds: int) -> AsyncIterator[bool]:
        token = secrets.token_hex(16)
        full_key = f"lock:{key}"
        acquired = bool(await self._redis.set(full_key, token, nx=True, px=ttl_seconds * 1000))
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            await self._redis.eval(_RELEASE_LUA, 1, full_key, token)
