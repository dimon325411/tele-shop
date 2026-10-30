"""Logging that never prints secrets: tokens in URLs/text are masked."""

from __future__ import annotations

import logging
import re

_TOKEN = re.compile(r"(?<!\d)\d{6,12}:[A-Za-z0-9_-]{30,}")
_DSN_PASSWORD = re.compile(r"(://[^:/@\s]+:)[^@\s]+@")


def mask_secrets(text: str) -> str:
    text = _TOKEN.sub("<bot-token>", text)
    return _DSN_PASSWORD.sub(r"\1***@", text)


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover
            return True
        masked = mask_secrets(message)
        if masked != message:
            record.msg, record.args = masked, ()
        return True


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler.addFilter(SecretFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("aiogram.event", "httpx"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
