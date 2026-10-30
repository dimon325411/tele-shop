"""Creating and managing user searches. All permission checks happen here (server side)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from app.domain.models import Condition, Delivery, Search, SearchCriteria, User
from app.domain.plans import AccessDenied, monitored_search_ids
from app.domain.timing import Clock, utcnow
from app.services.subscriptions import SubscriptionService
from app.sources.registry import SourceRegistry
from app.storage.repo import Repository


@dataclass
class SearchDraft:
    """What the wizard collects (already validated field by field)."""

    name: str = ""
    sources: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    alt_keywords: list[str] = field(default_factory=list)
    excluded_keywords: list[str] = field(default_factory=list)
    category: str | None = None
    price_min: Decimal | None = None
    price_max: Decimal | None = None
    currency: str = "PLN"
    condition: str = "any"
    city: str | None = None
    region: str | None = None
    radius_km: int | None = None
    delivery: str = "any"
    include_unknown: bool = True
    fuzzy: bool = False
    match_description: bool = False
    deal_min_discount: int | None = None
    interval_seconds: int | None = None

    def to_dict(self) -> dict:
        data = dict(self.__dict__)
        for key in ("price_min", "price_max"):
            if data[key] is not None:
                data[key] = str(data[key])
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "SearchDraft":
        data = dict(data)
        for key in ("price_min", "price_max"):
            if data.get(key) is not None:
                data[key] = Decimal(data[key])
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class SearchService:
    def __init__(self, repo: Repository, subscriptions: SubscriptionService, registry: SourceRegistry,
                 clock: Clock = utcnow) -> None:
        self.repo = repo
        self.subs = subscriptions
        self.registry = registry
        self.clock = clock

    async def create(self, user: User, draft: SearchDraft) -> Search:
        if not draft.keywords and not draft.alt_keywords:
            raise AccessDenied("no_keywords")
        if not draft.sources:
            raise AccessDenied("no_sources")
        unknown = [s for s in draft.sources if not self.registry.is_enabled(s)]
        if unknown:
            raise AccessDenied("source_disabled", sources=", ".join(unknown))
        async with self.repo.transaction():
            access = await self.subs.access_for(user)
            existing = await self.repo.list_user_searches(user.id)
            access.check_can_create_search(sum(1 for s in existing if s.is_active))
            access.check_sources(draft.sources)
            access.check_advanced(draft.fuzzy)
            interval = access.clamp_interval(draft.interval_seconds)
            now = self.clock()
            criteria = SearchCriteria(
                keywords=draft.keywords, alt_keywords=draft.alt_keywords, excluded_keywords=draft.excluded_keywords,
                category=draft.category, price_min=draft.price_min, price_max=draft.price_max,
                currency=draft.currency, condition=Condition(draft.condition), city=draft.city,
                region=draft.region, radius_km=draft.radius_km, delivery=Delivery(draft.delivery),
                include_unknown=draft.include_unknown, fuzzy=draft.fuzzy,
                match_description=draft.match_description, deal_min_discount=draft.deal_min_discount,
            )
            search = Search(id=0, user_id=user.id, name=draft.name, sources=list(draft.sources), criteria=criteria,
                            interval_seconds=interval, is_active=True, next_check_at=now, created_at=now)
            return await self.repo.create_search(search)

    async def list(self, user: User) -> list[Search]:
        return await self.repo.list_user_searches(user.id)

    async def get_owned(self, user: User, search_id: int) -> Search:
        """Fetch a search and make sure it belongs to ``user`` (user data isolation)."""
        search = await self.repo.get_search(search_id)
        if search is None or search.user_id != user.id:
            raise AccessDenied("not_found")
        return search

    async def pause(self, user: User, search_id: int) -> Search:
        search = await self.get_owned(user, search_id)
        await self.repo.update_search(search.id, is_active=False, paused_reason="user")
        search.is_active = False
        return search

    async def resume(self, user: User, search_id: int) -> Search:
        async with self.repo.transaction():
            search = await self.get_owned(user, search_id)
            if search.is_active:
                return search
            access = await self.subs.access_for(user)
            existing = await self.repo.list_user_searches(user.id)
            access.check_can_create_search(sum(1 for s in existing if s.is_active))
            interval = access.clamp_interval(search.interval_seconds)
            await self.repo.update_search(search.id, is_active=True, paused_reason=None, next_check_at=self.clock(),
                                          interval_seconds=interval, consecutive_failures=0)
            search.is_active = True
            return search

    async def pause_all(self, user: User) -> int:
        count = 0
        for search in await self.repo.list_user_searches(user.id):
            if search.is_active:
                await self.repo.update_search(search.id, is_active=False, paused_reason="user")
                count += 1
        return count

    async def delete(self, user: User, search_id: int) -> None:
        search = await self.get_owned(user, search_id)
        await self.repo.delete_search(search.id)

    async def monitored_ids(self, user: User) -> set[int]:
        access = await self.subs.access_for(user)
        if not access.active:
            return set()
        return monitored_search_ids(await self.repo.list_user_searches(user.id), access.plan)
