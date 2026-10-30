"""Background job bodies, independent of ARQ (unit-testable).

``app.worker.main`` wraps them into ARQ functions / cron jobs.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from app.container import Container
from app.i18n import esc, t
from app.services.notifications import NotifierBlocked

log = logging.getLogger(__name__)

Enqueue = Callable[[int], Awaitable[None]]


async def schedule_tick(c: Container, enqueue: Enqueue) -> int:
    """Pick due searches (fair across users) and enqueue one check job per search."""
    ids = await c.monitoring.plan_due_checks()
    for search_id in ids:
        await enqueue(search_id)
    return len(ids)


async def run_check(c: Container, search_id: int) -> dict:
    outcome = await c.monitoring.check_search(search_id)
    if outcome.baseline and outcome.status in ("ok", "partial") and c.notifications is not None:
        search = await c.repo.get_search(search_id)
        user = await c.repo.get_user(search.user_id) if search else None
        if user is not None and not user.bot_blocked:
            try:
                await c.notifications.notifier.send_text(
                    user.telegram_id, t("notify.baseline", user.language, name=esc(search.name), count=outcome.matched))
            except NotifierBlocked:
                await c.repo.update_user(user.id, bot_blocked=True)
            except Exception:  # informational message only
                log.warning("baseline message failed for search %s", search_id, exc_info=True)
    return {"status": outcome.status, "new": outcome.new_listings, "matched": outcome.matched,
            "notifications": outcome.notifications, "reason": outcome.reason}


async def deliver_notifications(c: Container, max_batches: int = 5) -> int:
    if c.notifications is None:
        return 0
    total = 0
    for _ in range(max_batches):
        report = await c.notifications.deliver_batch()
        handled = report.sent + report.deferred + report.limited + report.failed + report.retried + report.cancelled
        total += report.sent
        if handled < c.settings.notification_batch_size:
            break
    return total


async def expire_subscriptions(c: Container) -> int:
    expired = await c.subs.expire_due()
    notices = await c.subs.pop_expiry_notices()
    if c.notifications is not None:
        for sub in notices:
            user = await c.repo.get_user(sub.user_id)
            if user is None or user.bot_blocked or user.deleted_at:
                continue
            try:
                await c.notifications.notifier.send_text(user.telegram_id, t("plan.expired", user.language))
            except NotifierBlocked:
                await c.repo.update_user(user.id, bot_blocked=True)
            except Exception:
                log.warning("expiry notice failed for user %s", user.id, exc_info=True)
    return len(expired)


async def retention(c: Container) -> dict:
    result = await c.retention.purge()
    log.info("retention purge: %s", result)
    return result
