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

log = logging.getLogger("dev_run")


async def scheduler_loop(container, tick_seconds: int) -> None:
    running: set[asyncio.Task] = set()

    async def enqueue(search_id: int) -> None:
        task = asyncio.create_task(jobs.run_check(container, search_id))
        running.add(task)
        task.add_done_callback(running.discard)

    ticks = 0
    while True:
        try:
            await jobs.schedule_tick(container, enqueue)
            await jobs.deliver_notifications(container)
            if ticks % 10 == 0:
                await jobs.expire_subscriptions(container)
        except Exception:
            log.exception("scheduler tick failed")
        ticks += 1
        await asyncio.sleep(tick_seconds)


async def main() -> None:
    settings = get_settings()
    settings.enabled_sources = ["mock"]  # demo: never touch real marketplaces
    setup_logging(settings.log_level)
    log.warning("DEMO MODE: in-memory storage, data is lost on restart, only the test source is active")
    container = build_container(settings, repo=MemoryRepository(), use_redis=False)
    await container.subs.seed_plans()
    for plan in await container.repo.list_plans(active_only=False):  # demo: check every minute
        await container.repo.update_plan(plan.code, min_interval_seconds=60)
    bot = make_bot(settings)
    container.attach_notifier(TelegramNotifier(bot))
    dp = Dispatcher(storage=MemoryStorage())
    middleware = AppMiddleware(container)
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    dp.pre_checkout_query.outer_middleware(middleware)
    dp.include_router(router)
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMANDS])
    scheduler = asyncio.create_task(scheduler_loop(container, tick_seconds=15))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        scheduler.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
