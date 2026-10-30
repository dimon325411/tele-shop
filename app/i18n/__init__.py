"""Centralised translations. ``t(key, lang, **params)`` falls back to Polish, then to the key."""

from __future__ import annotations

import html
import logging

from app.i18n import pl

log = logging.getLogger(__name__)

LANGUAGES: dict[str, dict[str, str]] = {"pl": pl.MESSAGES}
DEFAULT_LANGUAGE = "pl"


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def t(key: str, lang: str | None = None, **params: object) -> str:
    table = LANGUAGES.get(lang or DEFAULT_LANGUAGE, LANGUAGES[DEFAULT_LANGUAGE])
    template = table.get(key) or LANGUAGES[DEFAULT_LANGUAGE].get(key)
    if template is None:
        log.warning("missing translation key %s", key)
        return key
    return template.format_map(_SafeDict(params))


def esc(value: object) -> str:
    """Escape user/listing supplied text for Telegram HTML."""
    return html.escape("" if value is None else str(value), quote=False)


def format_interval(seconds: int, lang: str | None = None) -> str:
    if seconds % 3600 == 0:
        return t("unit.hours", lang, n=seconds // 3600)
    return t("unit.minutes", lang, n=max(1, round(seconds / 60)))
