"""Delivery of queued notifications (outbox pattern).

Rows are created by monitoring with a unique (user_id, dedup_key) so a listing matching
several searches of the same user is sent once. Delivery claims rows atomically
(pending -> sending), so parallel workers never send the same row twice; a row stuck in
"sending" (worker crashed) is re-claimed after a timeout.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Protocol

from app.config import Settings
from app.domain.models import Notification, NotificationStatus, StoredListing, User
from app.domain.timing import Clock, backoff_seconds, in_quiet_hours, local_day_start_utc, quiet_hours_end, utcnow
from app.services.subscriptions import SubscriptionService
from app.storage.repo import Repository

log = logging.getLogger(__name__)


class NotifierBlocked(Exception):
    """The user blocked the bot / deleted the chat. Stop sending to them."""


class NotifierRetryLater(Exception):
    def __init__(self, retry_after: float) -> None:
        super().__init__(f"retry after {retry_after}s")
        self.retry_after = retry_after


@dataclass
class OutgoingListing:
    user: User
    listing: StoredListing
    search_id: int | None
    search_name: str | None
    kind: str
    old_price: Decimal | None


class Notifier(Protocol):
    async def send_listing(self, item: OutgoingListing) -> int:
        """Send and return the Telegram message id."""

    async def send_text(self, telegram_id: int, text: str) -> None: ...


@dataclass
class DeliveryReport:
    sent: int = 0
    deferred: int = 0
    limited: int = 0
    failed: int = 0
    retried: int = 0
    cancelled: int = 0


class NotificationService:
    def __init__(self, repo: Repository, subscriptions: SubscriptionService, notifier: Notifier,
                 settings: Settings, clock: Clock = utcnow) -> None:
        self.repo = repo
        self.subs = subscriptions
        self.notifier = notifier
        self.settings = settings
        self.clock = clock

    async def deliver_batch(self) -> DeliveryReport:
        report = DeliveryReport()
        batch = await self.repo.claim_notifications(self.clock(), self.settings.notification_batch_size)
        for item in batch:
            await self._deliver_one(item, report)
        return report

    async def _deliver_one(self, n: Notification, report: DeliveryReport) -> None:
        now = self.clock()
        user = await self.repo.get_user(n.user_id)
        listing = await self.repo.get_listing(n.listing_id)
        if user is None or listing is None or user.deleted_at or user.bot_blocked or user.is_suspended:
            await self.repo.finish_notification(n.id, NotificationStatus.CANCELLED.value, now, "recipient unavailable",
                                                count_attempt=False)
            report.cancelled += 1
            return
        access = await self.subs.access_for(user)
        if not access.active or access.plan is None:
            await self.repo.finish_notification(n.id, NotificationStatus.CANCELLED.value, now, "no subscription",
                                                count_attempt=False)
            report.cancelled += 1
            return
        if in_quiet_hours(now, user.quiet_start_hour, user.quiet_end_hour, user.timezone):
            until = quiet_hours_end(now, user.quiet_start_hour, user.quiet_end_hour, user.timezone)
            await self.repo.finish_notification(n.id, NotificationStatus.PENDING.value, now, None,
                                                next_attempt_at=until, count_attempt=False)
            report.deferred += 1
            return
        sent_today = await self.repo.count_sent_since(user.id, local_day_start_utc(now, user.timezone))
        if sent_today >= access.plan.max_notifications_per_day:
            await self.repo.finish_notification(n.id, NotificationStatus.LIMITED.value, now, "daily limit",
                                                count_attempt=False)
            report.limited += 1
            return

        search_name = None
        if n.search_id is not None:
            search = await self.repo.get_search(n.search_id)
            search_name = search.name if search else None
        old_price = listing.previous_price if n.kind.value == "price_drop" else None
        try:
            message_id = await self.notifier.send_listing(
                OutgoingListing(user, listing, n.search_id, search_name, n.kind.value, old_price))
        except NotifierBlocked:
            await self.repo.update_user(user.id, bot_blocked=True)
            await self.repo.finish_notification(n.id, NotificationStatus.CANCELLED.value, now, "bot blocked by user")
            report.cancelled += 1
        except NotifierRetryLater as exc:
            await self.repo.finish_notification(n.id, NotificationStatus.PENDING.value, now, str(exc),
                                                next_attempt_at=now + timedelta(seconds=exc.retry_after),
                                                count_attempt=False)
            report.retried += 1
        except Exception as exc:  # network errors etc.
            attempts = n.attempts + 1
            if attempts >= self.settings.notification_max_attempts:
                await self.repo.finish_notification(n.id, NotificationStatus.FAILED.value, now, repr(exc)[:300])
                report.failed += 1
            else:
                delay = backoff_seconds(attempts, base=30, cap=1800)
                await self.repo.finish_notification(n.id, NotificationStatus.PENDING.value, now, repr(exc)[:300],
                                                    next_attempt_at=now + timedelta(seconds=delay))
                report.retried += 1
            log.warning("notification %s failed: %r", n.id, exc)
        else:
            await self.repo.finish_notification(n.id, NotificationStatus.SENT.value, now, None, message_id=message_id)
            report.sent += 1
