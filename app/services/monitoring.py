"""Periodic checking of searches: fetch -> store -> match -> enqueue notifications."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from app.config import Settings
from app.domain.matcher import match
from app.domain.models import NotificationKind, Search, SourceState
from app.domain.plans import monitored_search_ids
from app.domain.area import in_area
from app.domain.sellers import is_new_account
from app.domain.timing import Clock, backoff_seconds, utcnow
from app.services.locks import Locker
from app.services.market import MarketService
from app.services.subscriptions import SubscriptionService
from app.sources.base import SourceBlocked, SourceDisabled, SourceError, SourceLayoutChanged, SourceUnavailable
from app.sources.registry import SourceRegistry
from app.storage.repo import Repository

log = logging.getLogger(__name__)

PRICE_DROP_MIN_FRACTION = Decimal("0.05")  # notify only about drops of at least 5 %
FETCH_TIMEOUT_SECONDS = 120  # a search may need several polite requests (one per OLX category, >=10 s apart)
LOCK_TTL_SECONDS = 300
DEFAULT_BLOCK_SECONDS = 3600
# A listing published before the search existed is "old" even if the source shows it to us only now
# (sources may return a different subset of results on every request).
OLD_LISTING_SLACK = timedelta(minutes=30)
MAX_NEW_NOTIFICATIONS_PER_CHECK = 10
# Hot-deal mode (search.criteria.deal_min_discount set): besides the discount, a candidate must be fresh
# and come from a seller whose account is not brand new; then the AI reads the description (if configured).
HOT_MAX_AGE = timedelta(hours=6)


@dataclass
class CheckOutcome:
    search_id: int
    status: str  # ok | partial | failed | skipped
    reason: str | None = None
    fetched: int = 0
    new_listings: int = 0
    matched: int = 0
    notifications: int = 0
    baseline: bool = False
    suppressed_old: int = 0  # matched, but published before the search was created
    suppressed_cap: int = 0  # matched and new, but over the per-check notification cap
    suppressed_not_deal: int = 0  # reseller mode: matched and new, but not cheap enough
    suppressed_not_hot: int = 0  # deal, but stale or from a brand-new seller account
    suppressed_ai: int = 0  # deal, but the AI found a red flag in the description
    suppressed_area: int = 0  # deal, but outside the owner's pick-up area (still counts for statistics)
    retry_after: float | None = None
    source_status: dict[str, str] = field(default_factory=dict)


class MonitoringService:
    def __init__(self, repo: Repository, subscriptions: SubscriptionService, registry: SourceRegistry,
                 locker: Locker, settings: Settings, clock: Clock = utcnow) -> None:
        self.repo = repo
        self.subs = subscriptions
        self.registry = registry
        self.locker = locker
        self.settings = settings
        self.clock = clock
        self.market = MarketService(repo, clock)
        self.screener = None  # AdvisorService (AI pre-check of deal candidates), attached by the container

    # ------------------------------------------------------------ scheduling
    async def plan_due_checks(self) -> list[int]:
        """Pick searches to check now, fairly across users, and reserve their next slot."""
        now = self.clock()
        due = await self.repo.list_due_searches(now, self.settings.max_checks_per_tick * 3)
        by_user: dict[int, list[Search]] = defaultdict(list)
        for search in due:
            by_user[search.user_id].append(search)

        selected: list[Search] = []
        for user_id, searches in by_user.items():
            user = await self.repo.get_user(user_id)
            if user is None:
                continue
            access = await self.subs.access_for(user)
            if not access.active:
                # no access: look again in an hour instead of every tick
                for s in searches:
                    await self.repo.update_search(s.id, next_check_at=now + timedelta(hours=1))
                continue
            allowed = monitored_search_ids(await self.repo.list_user_searches(user_id), access.plan)
            eligible = [s for s in searches if s.id in allowed]
            for s in searches:
                if s.id not in allowed:
                    await self.repo.update_search(s.id, next_check_at=now + timedelta(hours=1))
            selected.extend(eligible[: self.settings.max_checks_per_user_per_tick])

        # round-robin across users so one user cannot fill the tick
        selected.sort(key=lambda s: (s.next_check_at or now, s.id))
        selected = selected[: self.settings.max_checks_per_tick]
        for s in selected:
            await self.repo.update_search(s.id, next_check_at=now + timedelta(seconds=s.interval_seconds))
        return [s.id for s in selected]

    # ---------------------------------------------------------------- checks
    async def check_search(self, search_id: int) -> CheckOutcome:
        async with self.locker.hold(f"search:{search_id}", LOCK_TTL_SECONDS) as acquired:
            if not acquired:
                return CheckOutcome(search_id, "skipped", "locked")
            return await self._check_locked(search_id)

    async def _check_locked(self, search_id: int) -> CheckOutcome:
        now = self.clock()
        search = await self.repo.get_search(search_id)
        if search is None or not search.is_active:
            return CheckOutcome(search_id, "skipped", "inactive")
        user = await self.repo.get_user(search.user_id)
        if user is None:
            return CheckOutcome(search_id, "skipped", "no_user")
        access = await self.subs.access_for(user)
        if not access.active or access.plan is None:
            return CheckOutcome(search_id, "skipped", "no_access")
        plan = access.plan
        if search.id not in monitored_search_ids(await self.repo.list_user_searches(user.id), plan):
            return CheckOutcome(search_id, "skipped", "over_plan_limit")

        outcome = CheckOutcome(search_id, "ok", baseline=not search.baseline_done)
        notify_new = search.baseline_done or self.settings.notify_on_first_run
        price_drops = plan.price_drop_alerts and user.notify_price_drops
        successes = temporary_failures = 0
        deal_mode = bool(search.criteria.deal_min_discount)
        deal_candidates = []  # hot-deal mode: judged after all sources are stored (the market is complete)

        for source in search.sources:
            if source not in plan.allowed_sources:
                outcome.source_status[source] = "not_in_plan"
                continue
            started = self.clock()
            status, error = await self._source_gate(source, started)
            fetched = new = matched = 0
            if status is None:
                adapter = self.registry.get(source)
                try:
                    listings = await asyncio.wait_for(adapter.fetch(search.criteria), FETCH_TIMEOUT_SECONDS)
                except SourceDisabled as exc:
                    status, error = "disabled", str(exc)
                except SourceBlocked as exc:
                    status, error = "blocked", str(exc)
                    await self._mark_source(source, "blocked", started, str(exc),
                                            block_seconds=exc.retry_after_seconds or DEFAULT_BLOCK_SECONDS)
                except SourceLayoutChanged as exc:
                    status, error = "error", f"layout: {exc}"
                    await self._mark_source(source, "degraded", started, error)
                except (SourceUnavailable, asyncio.TimeoutError) as exc:
                    status, error = "error", str(exc) or "timeout"
                    temporary_failures += 1
                    await self._mark_source(source, "degraded", started, error)
                except SourceError as exc:
                    status, error = "error", str(exc)
                    temporary_failures += 1
                else:
                    status = "ok"
                    successes += 1
                    fetched = len(listings)
                    await self._mark_source(source, "ok", started, None)
                    for listing in listings:
                        if adapter.is_test_source:
                            listing.is_test = True  # test data is always flagged
                        result = await self.repo.upsert_listing(listing, self.clock())
                        new += int(result.is_new)
                        verdict = match(result.listing, search.criteria)
                        if not verdict.matched:
                            continue
                        reasons = [[key, params] for key, params in verdict.reasons]
                        inserted = await self.repo.add_match(search.id, result.listing.id, user.id, reasons)
                        if inserted:
                            matched += 1
                            if deal_mode:
                                # freshness (not "seen after the search was created") decides: a 3-hour-old
                                # bargain found by the very first check is still worth a message
                                deal_candidates.append((result.listing, NotificationKind.NEW.value, f"new:{result.listing.id}"))
                                continue
                            if not notify_new:
                                continue
                            posted = result.listing.posted_at
                            if posted is not None and search.created_at is not None \
                                    and posted < search.created_at - OLD_LISTING_SLACK:
                                outcome.suppressed_old += 1
                                continue
                            if outcome.notifications >= MAX_NEW_NOTIFICATIONS_PER_CHECK:
                                outcome.suppressed_cap += 1
                                continue
                            outcome.notifications += int(await self.repo.enqueue_notification(
                                user.id, result.listing.id, search.id, NotificationKind.NEW.value,
                                f"new:{result.listing.id}", result.listing.price, self.clock()))
                        elif price_drops and self._is_price_drop(result.old_price, result.listing.price):
                            if deal_mode:
                                deal_candidates.append((result.listing, NotificationKind.PRICE_DROP.value,
                                                        f"price_drop:{result.listing.id}:{result.listing.price}"))
                                continue
                            outcome.notifications += int(await self.repo.enqueue_notification(
                                user.id, result.listing.id, search.id, NotificationKind.PRICE_DROP.value,
                                f"price_drop:{result.listing.id}:{result.listing.price}", result.listing.price,
                                self.clock()))
            outcome.source_status[source] = status
            outcome.fetched += fetched
            outcome.new_listings += new
            outcome.matched += matched
            await self.repo.add_run(search.id, source, started, self.clock(), status, fetched, new, matched,
                                    (error or None) and str(error)[:500])

        for listing, kind, dedup_key in deal_candidates:
            if not self._is_hot(listing):
                outcome.suppressed_not_hot += 1
                continue
            deal = await self.market.evaluate_deal(search.id, listing, search.criteria.deal_min_discount)
            if deal is None:
                outcome.suppressed_not_deal += 1
                continue
            if not in_area(user, listing):
                outcome.suppressed_area += 1
                continue
            if outcome.notifications >= MAX_NEW_NOTIFICATIONS_PER_CHECK:
                outcome.suppressed_cap += 1
                continue
            if not await self._screen(deal, plan):
                outcome.suppressed_ai += 1
                continue
            outcome.notifications += int(await self.repo.enqueue_notification(
                user.id, listing.id, search.id, kind, dedup_key, listing.price,
                self.clock()))

        finished = self.clock()
        fields: dict = {"last_checked_at": finished}
        if successes:
            fields.update(last_success_at=finished, baseline_done=True, consecutive_failures=0,
                          next_check_at=finished + timedelta(seconds=search.interval_seconds))
            outcome.status = "ok" if not temporary_failures else "partial"
        elif temporary_failures:
            failures = search.consecutive_failures + 1
            delay = max(search.interval_seconds, backoff_seconds(failures, base=60, cap=3600))
            fields.update(consecutive_failures=failures, next_check_at=finished + timedelta(seconds=delay))
            outcome.status, outcome.retry_after = "failed", delay
        else:
            fields.update(next_check_at=finished + timedelta(seconds=search.interval_seconds))
            outcome.status, outcome.reason = "skipped", "no_usable_source"
        await self.repo.update_search(search.id, **fields)
        return outcome

    def _is_hot(self, listing) -> bool:
        now = self.clock()
        posted = listing.posted_at or listing.first_seen_at
        if posted is None or now - posted > HOT_MAX_AGE:
            return False  # refreshed/bumped old listing: somebody had hours to buy it already
        return not is_new_account(listing.seller_created_at, now)

    async def _screen(self, deal, plan) -> bool:
        """AI pre-check of a deal candidate. False = drop it. No AI / no verdict = keep it."""
        if self.screener is None or not plan.advanced_filters:
            return True
        result = await self.screener.screen_deal(deal.listing, deal.stats.median, deal.discount, deal.stats.count)
        if result is None:
            return True
        icon = {"ok": "✅", "risk": "⚠️", "reject": "⛔"}[result.decision]
        await self.repo.set_listing_ai_note(deal.listing.id, f"{icon} {result.reason}".strip())
        if result.decision == "reject":
            log.info("deal %s rejected by AI: %s", deal.listing.id, result.reason)
            return False
        return True

    @staticmethod
    def _is_price_drop(old: Decimal | None, new: Decimal | None) -> bool:
        if old is None or new is None or old <= 0:
            return False
        return new < old and (old - new) / old >= PRICE_DROP_MIN_FRACTION

    async def _source_gate(self, source: str, now: datetime) -> tuple[str | None, str | None]:
        """Return (status, reason) when the source must not be called now, else (None, None)."""
        if not self.registry.is_enabled(source) or self.registry.get(source) is None:
            return "disabled", "not enabled in configuration"
        state = await self.repo.get_source_state(source)
        if state is not None:
            if not state.enabled:
                return "disabled", state.disabled_reason or "disabled by admin"
            if state.blocked_until is not None and state.blocked_until > now:
                return "blocked", f"cool-down until {state.blocked_until.isoformat()}"
        return None, None

    async def _mark_source(self, source: str, status: str, now: datetime, error: str | None,
                           block_seconds: int | None = None) -> None:
        state = await self.repo.get_source_state(source) or SourceState(source=source)
        state.status = status
        if status == "ok":
            state.last_success_at = now
            state.blocked_until = None
        else:
            state.last_error_at = now
            state.last_error = (error or "")[:500]
        if block_seconds:
            state.blocked_until = now + timedelta(seconds=block_seconds)
            log.warning("source %s blocked us, pausing for %ss: %s", source, block_seconds, error)
        await self.repo.save_source_state(state, now)
