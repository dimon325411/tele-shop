"""Telegram implementation of the ``Notifier`` protocol."""

from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramNotFound, TelegramRetryAfter

from app.bot.keyboards import markup
from app.domain.timing import utcnow
from app.presentation import render_listing_notification
from app.services.notifications import NotifierBlocked, NotifierRetryLater, OutgoingListing


class TelegramNotifier:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def send_listing(self, item: OutgoingListing) -> int:
        text, buttons = render_listing_notification(item, utcnow())
        try:
            msg = await self.bot.send_message(item.user.telegram_id, text, reply_markup=markup(buttons))
        except TelegramRetryAfter as exc:
            raise NotifierRetryLater(exc.retry_after) from exc
        except (TelegramForbiddenError, TelegramNotFound) as exc:
            raise NotifierBlocked() from exc
        return msg.message_id

    async def send_text(self, telegram_id: int, text: str) -> None:
        try:
            await self.bot.send_message(telegram_id, text)
        except TelegramRetryAfter as exc:
            raise NotifierRetryLater(exc.retry_after) from exc
        except (TelegramForbiddenError, TelegramNotFound) as exc:
            raise NotifierBlocked() from exc
