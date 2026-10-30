"""Single-process DEMO mode: ``python -m app.dev_run``.

No PostgreSQL / Redis / Docker needed: in-memory storage, in-process scheduler, mock source.
All data is lost on restart — use it only to try the bot. Production uses docker-compose.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from app.bot.handlers import router
from app.bot.main import COMMANDS, make_bot
from app.bot.middlewares import AppMiddleware
from app.bot.notifier import TelegramNotifier
from app.config import get_settings
from app.container import build_container
from app.logging_setup import setup_logging
from app.storage.memory import MemoryRepository
from app.worker import jobs

STATE_FILE = "data/dev_state.pickle"

log = logging.getLogger("dev_run")


async def persist_loop(repo: MemoryRepository, every_seconds: int = 60) -> None:
    while True:
        await asyncio.sleep(every_seconds)
        try:
            repo.dump(STATE_FILE)
        except Exception:
            log.exception("could not save demo state")


# Checks run one at a time: real sources allow one request per ~10 s anyway, and a check waiting in the
# queue of the polite fetcher must not hit the fetch timeout (many searches = long queue).
PARALLEL_CHECKS = 1


def save_on_signals(repo: MemoryRepository) -> None:
    """Closing the Terminal window (SIGHUP) or ``kill`` (SIGTERM) would end the process without the final save."""
    import os
    import signal

    def save_and_exit(signame: str) -> None:
        try:
            repo.dump(STATE_FILE)
            log.warning("%s: demo state saved to %s", signame, STATE_FILE)
        finally:
            os._exit(0)

    loop = asyncio.get_running_loop()
    for name in ("SIGHUP", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, save_and_exit, name)
        except (NotImplementedError, RuntimeError):  # Windows: the minute-by-minute save covers it
            pass


async def scheduler_loop(container, tick_seconds: int) -> None:
    running: set[asyncio.Task] = set()
    gate = asyncio.Semaphore(PARALLEL_CHECKS)

    async def gated_check(search_id: int) -> None:
        async with gate:
            await jobs.run_check(container, search_id)

    async def enqueue(search_id: int) -> None:
        task = asyncio.create_task(gated_check(search_id))
        running.add(task)
        task.add_done_callback(running.discard)

    ticks = 0
    while True:
        try:
            await jobs.schedule_tick(container, enqueue)
            await jobs.deliver_notifications(container)
            if ticks % 10 == 0:
                await jobs.expire_subscriptions(container)
            if ticks % 20 == 5:  # every ~5 minutes: evening summary when it is due
                await jobs.daily_digest(container)
            if ticks % 5760 == 1:  # about once a day: drop old data so the state file stays small
                await jobs.retention(container)
        except Exception:
            log.exception("scheduler tick failed")
        ticks += 1
        await asyncio.sleep(tick_seconds)


async def warm_up_charts() -> None:
    try:
        from app.services.charts import warm_up
        await asyncio.to_thread(warm_up)
    except Exception:  # charts are optional; never block startup
        logging.getLogger(__name__).warning("chart warm-up failed", exc_info=True)


async def main() -> None:
    settings = get_settings()
    if not settings.personal_mode:
        settings.enabled_sources = ["mock"]  # demo: never touch real marketplaces
    setup_logging(settings.log_level)
    repo = MemoryRepository.load(STATE_FILE)
    log.warning("DEMO MODE: data kept in %s (saved every minute); sources: %s; searches loaded: %s", STATE_FILE,
                "mock + OLX (personal mode)" if settings.personal_mode else "test source only", len(repo.searches))
    container = build_container(settings, repo=repo, use_redis=False)
    await container.subs.seed_plans()
    for plan in await container.repo.list_plans(active_only=False):  # demo: mock checks every minute
        if plan.code != "personal":  # real OLX stays at the personal-mode minimum (10 min)
            await container.repo.update_plan(plan.code, min_interval_seconds=60)
    for telegram_id in settings.admin_ids:  # pack searches follow the current pack definition
        owner = await container.repo.get_user_by_telegram_id(telegram_id)
        if owner is not None:
            updated = await container.searches.sync_pack(owner)
            if updated:
                log.warning("updated filters of %s pack searches", updated)
    bot = make_bot(settings)
    container.attach_notifier(TelegramNotifier(bot))
    dp = Dispatcher(storage=MemoryStorage())
    middleware = AppMiddleware(container)
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    dp.pre_checkout_query.outer_middleware(middleware)
    dp.include_router(router)
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMANDS])
    warm = asyncio.create_task(warm_up_charts())  # noqa: F841 — font cache in background
    save_on_signals(repo)
    scheduler = asyncio.create_task(scheduler_loop(container, tick_seconds=15))
    saver = asyncio.create_task(persist_loop(repo))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.cancel()
        saver.cancel()
        repo.dump(STATE_FILE)
        log.warning("demo state saved to %s", STATE_FILE)
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
