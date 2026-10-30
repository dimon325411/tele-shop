"""aiogram middlewares: dependency injection, user loading, rate limiting, suspension."""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery, TelegramObject

from app.container import Container
from app.controller import Controller
from app.domain.timing import TokenBucket
from app.i18n import t

log = logging.getLogger(__name__)

# Commands a suspended user can still use.
ALWAYS_ALLOWED = {"/support", "/paysupport", "/terms", "/privacy", "/start", "/help"}


class AppMiddleware(BaseMiddleware):
    """Injects ``container``, ``ctl`` and ``user`` into handlers; enforces rate limit & suspension."""

    def __init__(self, container: Container) -> None:
        self.container = container
        self.controller = Controller(container)
        self.bucket = TokenBucket(container.settings.command_rate_per_minute, time.monotonic)

    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        data["container"] = self.container
        data["ctl"] = self.controller
        from_user = getattr(event, "from_user", None)
        if from_user is None or from_user.is_bot:
            return None  # channel posts, other bots: ignored

        # Payments must always reach their handlers quickly (10 s deadline for pre-checkout).
        if isinstance(event, PreCheckoutQuery) or (isinstance(event, Message) and event.successful_payment):
            return await handler(event, data)

        if not self.bucket.allow(from_user.id):
            if isinstance(event, CallbackQuery):
                await event.answer(t("error.rate_limited"), show_alert=False)
            elif isinstance(event, Message):
                await event.answer(t("error.rate_limited"))
            return None

        text = event.text if isinstance(event, Message) and event.text else ""
        command = text.split()[0].split("@")[0] if text.startswith("/") else ""
        users = self.container.users
        user = await self.container.repo.get_user_by_telegram_id(from_user.id)
        if user is None or user.deleted_at is not None:
            if command == "/start":
                # /start registers the user itself (and shows the trial welcome)
                return await handler(event, data)
            user, _ = await users.register(from_user.id, from_user.username, from_user.first_name)
        data["user"] = user

        if user.is_suspended:
            if command not in ALWAYS_ALLOWED:
                if isinstance(event, CallbackQuery):
                    await event.answer(t("error.suspended", user.language), show_alert=True)
                elif isinstance(event, Message):
                    await event.answer(t("error.suspended", user.language))
                return None
        return await handler(event, data)
