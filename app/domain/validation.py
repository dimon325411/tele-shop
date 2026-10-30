"""Validation of values typed by users in the search wizard.

Every function raises ``InvalidInput(code)`` where ``code`` maps to i18n key ``invalid.<code>``.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.domain.text import clean_keyword_list, split_user_list

MAX_NAME_LEN = 60
MAX_PHRASES = 10
MAX_PHRASE_LEN = 60
MAX_PRICE = Decimal("10000000")
SUPPORTED_CURRENCIES = {"PLN", "EUR", "USD"}


class InvalidInput(ValueError):
    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


def parse_name(raw: str) -> str:
    name = " ".join((raw or "").split())
    if not name:
        raise InvalidInput("empty")
    if len(name) > MAX_NAME_LEN:
        raise InvalidInput("too_long", max=MAX_NAME_LEN)
    return name


def parse_phrases(raw: str, *, allow_empty: bool) -> list[str]:
    if raw is None or raw.strip() in {"", "-"}:
        if allow_empty:
            return []
        raise InvalidInput("empty")
    phrases = split_user_list(raw)
    if any(len(p) > MAX_PHRASE_LEN for p in phrases):
        raise InvalidInput("too_long", max=MAX_PHRASE_LEN)
    cleaned = clean_keyword_list(phrases)
    if not cleaned and not allow_empty:
        raise InvalidInput("empty")
    if len(cleaned) > MAX_PHRASES:
        raise InvalidInput("too_many", max=MAX_PHRASES)
    return cleaned


_PRICE_RE = re.compile(r"^\d+(?:[.,]\d{1,2})?$")


def parse_price(raw: str) -> Decimal | None:
    """'1 200,50 zł' -> Decimal('1200.50'); '-' -> None."""
    value = (raw or "").strip().lower()
    if value in {"", "-"}:
        return None
    for token in ("zł", "zl", "pln"):
        value = value.replace(token, "")
    value = value.replace(" ", "").replace(" ", "")
    if not _PRICE_RE.match(value):
        raise InvalidInput("price")
    try:
        price = Decimal(value.replace(",", "."))
    except InvalidOperation as exc:  # pragma: no cover - regex guards this
        raise InvalidInput("price") from exc
    if price > MAX_PRICE:
        raise InvalidInput("price_too_high")
    return price


def parse_price_range(raw: str) -> tuple[Decimal | None, Decimal | None]:
    """'100-500', '100-', '-500', '-' (none)."""
    value = (raw or "").strip()
    if value in {"", "-"}:
        return None, None
    if "-" not in value:
        raise InvalidInput("price_range")
    low_raw, _, high_raw = value.partition("-")
    low = parse_price(low_raw) if low_raw.strip() else None
    high = parse_price(high_raw) if high_raw.strip() else None
    if low is not None and high is not None and low > high:
        raise InvalidInput("price_order")
    return low, high


def parse_currency(raw: str) -> str:
    value = (raw or "").strip().upper()
    if value not in SUPPORTED_CURRENCIES:
        raise InvalidInput("currency")
    return value


def parse_place(raw: str) -> str | None:
    value = " ".join((raw or "").split())
    if value in {"", "-"}:
        return None
    if len(value) > 60 or not re.match(r"^[\w\s\-.'ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+$", value):
        raise InvalidInput("place")
    return value


def parse_radius(raw: str) -> int | None:
    value = (raw or "").strip().lower().replace("km", "").strip()
    if value in {"", "-"}:
        return None
    if not value.isdigit():
        raise InvalidInput("radius")
    radius = int(value)
    if not 0 <= radius <= 500:
        raise InvalidInput("radius")
    return radius


def parse_hour(raw: str) -> int:
    value = (raw or "").strip()
    if not value.isdigit() or not 0 <= int(value) <= 23:
        raise InvalidInput("hour")
    return int(value)


def parse_quiet_hours(raw: str) -> tuple[int | None, int | None]:
    """'22-7' -> (22, 7); '-' -> (None, None)."""
    value = (raw or "").strip()
    if value in {"", "-"}:
        return None, None
    m = re.match(r"^(\d{1,2})\s*-\s*(\d{1,2})$", value)
    if not m:
        raise InvalidInput("quiet_hours")
    start, end = parse_hour(m.group(1)), parse_hour(m.group(2))
    if start == end:
        raise InvalidInput("quiet_hours")
    return start, end
