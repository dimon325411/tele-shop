"""Bot process entry point: ``python -m app.bot.main`` (long polling)."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from app.bot.handlers import router
from app.bot.middlewares import AppMiddleware
from app.config import PaymentsMode, Settings, get_settings
from app.container import build_container
from app.logging_setup import setup_logging

log = logging.getLogger(__name__)

# Telegram test environment (needed for test Stars payments; requires a bot created in the test DC).
TEST_SERVER = TelegramAPIServer(
    base="https://api.telegram.org/bot{token}/test/{method}",
    file="https://api.telegram.org/file/bot{token}/test/{path}",
)

COMMANDS = [
    ("start", "Menu główne"), ("newsearch", "Nowe wyszukiwanie"), ("searches", "Moje wyszukiwania"),
    ("status", "Status usługi"), ("plan", "Subskrypcja i limity"), ("pause", "Wstrzymaj monitoring"),
    ("resume", "Wznów monitoring"), ("delete", "Usuń wyszukiwanie"), ("compare", "Porównaj oferty (AI)"), ("settings", "Ustawienia"),
    ("help", "Pomoc"), ("terms", "Regulamin"), ("privacy", "Prywatność"), ("support", "Pomoc techniczna"),
    ("paysupport", "Pomoc w sprawie płatności"),
]


def make_bot(settings: Settings) -> Bot:
    if not settings.bot_token:
        raise SystemExit("BOT_TOKEN is not set (see .env.example)")
    session = AiohttpSession(api=TEST_SERVER) if settings.payments_mode is PaymentsMode.TEST else None
    return Bot(settings.bot_token, session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def make_fsm_storage(settings: Settings):
    try:
        from aiogram.fsm.storage.redis import RedisStorage

        return RedisStorage.from_url(settings.redis_url, state_ttl=86400, data_ttl=86400)
    except Exception:  # redis package missing -> in-memory (dev only)
        from aiogram.fsm.storage.memory import MemoryStorage

        log.warning("using in-memory FSM storage")
        return MemoryStorage()


async def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)
    log.info("starting bot with settings %s", settings.safe_dict())
    container = build_container(settings)
    await container.subs.seed_plans()
    bot = make_bot(settings)
    dp = Dispatcher(storage=make_fsm_storage(settings))
    middleware = AppMiddleware(container)
    dp.message.outer_middleware(middleware)
    dp.callback_query.outer_middleware(middleware)
    dp.pre_checkout_query.outer_middleware(middleware)
    dp.include_router(router)
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMANDS])
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await container.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
