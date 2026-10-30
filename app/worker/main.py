"""ARQ worker: ``arq app.worker.main.WorkerSettings``.

* ``schedule_tick`` (cron, every N seconds) enqueues ``check_search`` jobs with a
  deterministic job id, so a search is never queued twice for the same slot.
* ``check_search`` runs one search (Redis lock inside MonitoringService).
* ``deliver_notifications`` drains the notification outbox.
* ``expire_subscriptions`` / ``retention`` run periodically.

All state lives in PostgreSQL (next_check_at, outbox), so a restart loses nothing:
jobs that were in flight are simply picked up again by the next tick.
"""

from __future__ import annotations

import logging

from arq import cron
from arq.connections import RedisSettings

from app.bot.main import make_bot
from app.bot.notifier import TelegramNotifier
from app.config import get_settings
from app.container import build_container
from app.logging_setup import setup_logging
from app.worker import jobs

log = logging.getLogger(__name__)
settings = get_settings()


async def startup(ctx: dict) -> None:
    setup_logging(settings.log_level)
    container = build_container(settings)
    await container.subs.seed_plans()
    bot = make_bot(settings)
    container.attach_notifier(TelegramNotifier(bot))
    ctx["container"] = container
    ctx["bot"] = bot
    log.info("worker started")


async def shutdown(ctx: dict) -> None:
    await ctx["container"].close()
    await ctx["bot"].session.close()


async def check_search(ctx: dict, search_id: int) -> dict:
    return await jobs.run_check(ctx["container"], search_id)


async def schedule_tick(ctx: dict) -> int:
    redis = ctx["redis"]

    async def enqueue(search_id: int) -> None:
        search = await ctx["container"].repo.get_search(search_id)
        slot = int(search.next_check_at.timestamp()) if search and search.next_check_at else 0
        await redis.enqueue_job("check_search", search_id, _job_id=f"check:{search_id}:{slot}")

    return await jobs.schedule_tick(ctx["container"], enqueue)


async def deliver_notifications(ctx: dict) -> int:
    return await jobs.deliver_notifications(ctx["container"])


async def expire_subscriptions(ctx: dict) -> int:
    return await jobs.expire_subscriptions(ctx["container"])


async def retention(ctx: dict) -> dict:
    return await jobs.retention(ctx["container"])


async def daily_digest(ctx: dict) -> int:
    return await jobs.daily_digest(ctx["container"])


async def free_scan(ctx: dict) -> int:
    return await jobs.free_scan(ctx["container"])


def _every(seconds: int) -> set[int]:
    return set(range(0, 60, max(1, min(seconds, 60))))


class WorkerSettings:
    functions = [check_search]
    cron_jobs = [
        cron(schedule_tick, second=_every(settings.scheduler_tick_seconds), run_at_startup=True, timeout=120),
        cron(deliver_notifications, second=_every(10), timeout=120),
        cron(expire_subscriptions, minute=set(range(0, 60, 5)), second=7, timeout=300),
        cron(retention, hour=3, minute=17, second=0, timeout=1800),
        cron(daily_digest, minute={2, 32}, second=11, timeout=300),
        cron(free_scan, minute={7, 22, 37, 52}, second=31, timeout=300),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # personal mode reads OLX politely (one request per ~10 s): few parallel jobs, longer timeout
    max_jobs = 4 if settings.personal_mode else 20
    job_timeout = 300 if settings.personal_mode else 180
    max_tries = 1  # retries are scheduled through searches.next_check_at (exponential backoff)
    keep_result = 60
