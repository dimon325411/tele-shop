"""Shared fixtures for the dependency-free unit tests (stdlib unittest + memory repository)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.config import PaymentsMode, Settings
from app.services.admin import AdminService
from app.services.locks import MemoryLocker
from app.services.monitoring import MonitoringService
from app.services.notifications import NotificationService, OutgoingListing
from app.services.payments import PaymentService
from app.services.searches import SearchDraft, SearchService
from app.services.subscriptions import SubscriptionService
from app.services.users import UserService
from app.sources.disabled import AllegroLokalnieSource, OlxSource
from app.sources.mock import MockSource
from app.sources.registry import SourceRegistry
from app.storage.memory import MemoryRepository

T0 = datetime(2026, 10, 24, 10, 0, tzinfo=timezone.utc)  # 12:00 in Warsaw


class FakeClock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> datetime:
        self.now += timedelta(**kwargs)
        return self.now


@dataclass
class FakeNotifier:
    sent: list[OutgoingListing] = field(default_factory=list)
    texts: list[tuple[int, str]] = field(default_factory=list)
    fail_with: Exception | None = None

    async def send_listing(self, item: OutgoingListing) -> int:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append(item)
        return 1000 + len(self.sent)

    async def send_text(self, telegram_id: int, text: str) -> None:
        self.texts.append((telegram_id, text))


class App:
    """Wires all services exactly like production, but with memory storage and fakes."""

    def __init__(self, settings: Settings | None = None, mock: MockSource | None = None,
                 enabled: list[str] | None = None, repo=None, locker=None) -> None:
        self.settings = settings or Settings(admin_ids=[999], payments_mode=PaymentsMode.TEST,
                                             enabled_sources=enabled or ["mock"])
        self.clock = FakeClock()
        self.repo = repo if repo is not None else MemoryRepository()
        self.mock = mock or MockSource(grow=False)
        self.registry = SourceRegistry(
            {"mock": self.mock, "olx": OlxSource(), "allegro_lokalnie": AllegroLokalnieSource()},
            self.settings.enabled_sources,
        )
        self.locker = locker if locker is not None else MemoryLocker()
        self.notifier = FakeNotifier()
        self.subs = SubscriptionService(self.repo, self.clock)
        self.users = UserService(self.repo, self.subs, self.settings, self.clock)
        self.searches = SearchService(self.repo, self.subs, self.registry, self.clock)
        self.monitoring = MonitoringService(self.repo, self.subs, self.registry, self.locker, self.settings, self.clock)
        self.notifications = NotificationService(self.repo, self.subs, self.notifier, self.settings, self.clock)
        self.payments = PaymentService(self.repo, self.subs, self.settings, self.clock)
        self.admin = AdminService(self.repo, self.subs, self.settings, self.clock)

    async def setup(self) -> "App":
        await self.subs.seed_plans()
        return self

    async def user(self, telegram_id: int = 111, name: str = "Ala"):
        user, _ = await self.users.register(telegram_id, name.lower(), name)
        return user


def draft(**kwargs) -> SearchDraft:
    base = dict(name="Rowery", sources=["mock"], keywords=["rower"])
    base.update(kwargs)
    return SearchDraft(**base)
