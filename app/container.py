"""Wiring of production services (used by the bot, the worker and the HTTP API)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.services.admin import AdminService, RetentionService
from app.services.locks import Locker, MemoryLocker, RedisLocker
from app.services.monitoring import MonitoringService
from app.services.notifications import NotificationService, Notifier
from app.services.payments import PaymentService
from app.services.searches import SearchService
from app.services.subscriptions import SubscriptionService
from app.services.users import UserService
from app.sources.registry import SourceRegistry

log = logging.getLogger(__name__)


@dataclass
class Container:
    settings: Settings
    repo: Any
    registry: SourceRegistry
    locker: Locker
    subs: SubscriptionService
    users: UserService
    searches: SearchService
    monitoring: MonitoringService
    payments: PaymentService
    admin: AdminService
    retention: RetentionService
    redis: Any = None
    notifications: NotificationService | None = None

    def attach_notifier(self, notifier: Notifier) -> None:
        self.notifications = NotificationService(self.repo, self.subs, notifier, self.settings)

    async def close(self) -> None:
        engine = getattr(self.repo, "engine", None)
        if engine is not None:
            await engine.dispose()
        if self.redis is not None:
            await self.redis.aclose()


def build_container(settings: Settings, repo: Any = None, redis: Any = None, use_redis: bool = True) -> Container:
    if repo is None:
        from app.storage.sql import SqlRepository, make_engine

        repo = SqlRepository(make_engine(settings.database_url))
    if redis is None and use_redis and settings.redis_url:
        import redis.asyncio as aioredis

        redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    locker: Locker = RedisLocker(redis) if redis is not None else MemoryLocker()
    registry = SourceRegistry.default(settings.enabled_sources, settings.personal_mode,
                                      settings.source_min_request_interval_seconds, settings.source_timeout_seconds)
    subs = SubscriptionService(repo)
    container = Container(
        settings=settings, repo=repo, registry=registry, locker=locker, subs=subs,
        users=UserService(repo, subs, settings),
        searches=SearchService(repo, subs, registry),
        monitoring=MonitoringService(repo, subs, registry, locker, settings),
        payments=PaymentService(repo, subs, settings),
        admin=AdminService(repo, subs, settings),
        retention=RetentionService(repo, settings),
        redis=redis,
    )
    log.info("services ready: sources=%s payments=%s personal_mode=%s", registry.enabled_codes,
             settings.payments_mode.value, settings.personal_mode)
    return container
