"""Rendering of bot messages (pure functions, no aiogram) so they can be unit-tested.

Buttons are returned as ``Button`` specs; ``app.bot.keyboards`` turns them into aiogram markup.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.domain.models import Plan, Search
from app.domain.pricing import verdict_key
from app.domain.sellers import is_new_account
from app.i18n import esc, format_interval, t
from app.services.notifications import OutgoingListing
from app.sources.registry import DISPLAY_NAMES


@dataclass(frozen=True)
class Button:
    text: str
    url: str | None = None
    callback: str | None = None


def fmt_price(value: Decimal | None) -> str:
    if value is None:
        return ""
    q = value.quantize(Decimal("0.01"))
    whole, _, frac = f"{q:.2f}".partition(".")
    whole_spaced = f"{int(whole):,}".replace(",", " ")
    return whole_spaced if frac == "00" else f"{whole_spaced},{frac}"


def fmt_posted(listing, now: datetime, tz: str, lang: str | None = None) -> str:
    """When the listing was put up: '24.10 14:48 (2 godz. temu)'. Falls back to when we first saw it."""
    value = listing.posted_at or listing.first_seen_at
    if value is None:
        return t("posted.unknown", lang)
    local = value.astimezone(ZoneInfo(tz)).strftime("%d.%m %H:%M")
    minutes = max(0, int((now - value).total_seconds() // 60))
    if minutes < 60:
        age = t("age.minutes", lang, n=minutes)
    elif minutes < 48 * 60:
        age = t("age.hours", lang, n=minutes // 60)
    else:
        age = t("age.days", lang, n=minutes // 1440)
    return t("posted.value", lang, time=local, age=age, seen="" if listing.posted_at else t("posted.seen", lang))


def fmt_dt(value: datetime | None, tz: str, lang: str | None = None) -> str:
    if value is None:
        return t("never", lang)
    return value.astimezone(ZoneInfo(tz)).strftime("%d.%m.%Y %H:%M")


def render_listing_notification(item: OutgoingListing, now: datetime) -> tuple[str, list[list[Button]]]:
    user, listing = item.user, item.listing
    lang = user.language
    lines = [t("notify.price_drop" if item.kind == "price_drop" else "notify.new", lang, title=esc(listing.title))]
    currency = esc(listing.currency or "PLN")
    stats = getattr(item, "price_stats", None)
    compare = ""
    diff = None
    if stats is not None and listing.price is not None:
        diff = stats.diff_percent(listing.price)
        scope = [fmt_variant(stats.variant)] if getattr(stats, "variant", None) else []
        if stats.same_condition and listing.condition:
            scope.append(t(f"condition.{listing.condition}", lang).lower())
        compare = t("notify.vs_median", lang, diff=_signed_pct(diff), median=fmt_price(stats.median.quantize(Decimal("1"))),
                    count=stats.count, scope=(", " + ", ".join(scope)) if scope else "")
    if item.kind == "price_drop" and item.old_price is not None and listing.price is not None:
        lines.append(t("notify.price_drop_line", lang, old=fmt_price(item.old_price), price=fmt_price(listing.price),
                       currency=currency) + compare)
    elif listing.price is not None:
        lines.append(t("notify.price", lang, price=fmt_price(listing.price), currency=currency) + compare)
    else:
        lines.append(t("notify.price_unknown", lang))
    if diff is not None and diff <= -5:
        lines.append(t("notify.profit", lang, profit=fmt_price(stats.median - listing.price), currency=currency))
    if diff is not None and diff <= -HOT_MARK_PERCENT and item.kind != "price_drop":
        lines[0] = t("notify.hot", lang, title=esc(listing.title))
    if diff is not None and diff <= -SUSPICIOUS_DISCOUNT:
        lines.append(t("notify.suspicious", lang))
    facts = [t("notify.posted_at", lang, time=fmt_posted(listing, now, user.timezone, lang))]
    location = ", ".join(x for x in (listing.city, listing.region) if x)
    if location:
        from app.domain.area import distance_km

        dist = distance_km(user, listing)
        facts.append(t("notify.location", lang, location=esc(location)) + (f" ({dist:.0f} km)" if dist is not None else ""))
    if listing.condition:
        facts.append(t(f"condition.{listing.condition}", lang))
    lines.append(" · ".join(facts))
    if listing.seller_created_at is not None:
        since = listing.seller_created_at.astimezone(ZoneInfo(user.timezone))
        if is_new_account(listing.seller_created_at, now, user.timezone):
            lines.append(t("notify.seller_new", lang, date=since.strftime("%d.%m.%Y")))
        else:
            lines.append(t("notify.seller_since", lang, date=since.strftime("%m.%Y"),
                           business=t("notify.seller_business", lang) if listing.seller_is_business else ""))
    if listing.ai_note:
        lines.append(t("notify.ai_note", lang, note=esc(listing.ai_note)))
    footer = []
    if item.search_name:
        footer.append(t("notify.search", lang, name=esc(item.search_name)))
    if listing.source != "olx":
        footer.append(t("notify.source", lang, source=esc(DISPLAY_NAMES.get(listing.source, listing.source))))
    if footer:
        lines.append(" · ".join(footer))
    if listing.is_test:
        lines.append(t("notify.test_marker", lang))
    buttons = [[Button(t("btn.open_listing", lang), url=listing.url)]]
    if item.search_id is not None:
        buttons[0].append(Button(t("btn.card_stats", lang), callback=f"st:s:{item.search_id}"))
    if getattr(item, "ai_enabled", False):
        buttons.append([Button(t("btn.ask_ai", lang), callback=f"ai:l:{listing.id}")])

    row = []
    if item.search_id is not None:
        row.append(Button(t("btn.pause_search", lang), callback=f"s:pause:{item.search_id}"))
    row.append(Button(t("btn.notify_settings", lang), callback="settings:open"))
    buttons.append(row)
    return "\n".join(lines), buttons


def _signed_pct(diff: int) -> str:
    return f"−{-diff}%" if diff < 0 else f"+{diff}%"


def fmt_variant(variant: str | None) -> str:
    """'256gb' -> '256 GB', '1tb' -> '1 TB'."""
    if not variant:
        return ""
    return variant[:-2] + " " + variant[-2:].upper()


TELEGRAM_TEXT_LIMIT = 4096


def split_message(text: str, limit: int = TELEGRAM_TEXT_LIMIT - 96) -> list[str]:
    """Split a long HTML message at line breaks (our tags never span lines) into Telegram-sized parts."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single huge line (should not happen): hard cut
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            parts.append(current)
            current = line
        else:
            current = candidate
    if current.strip():
        parts.append(current)
    return [p for p in parts if p.strip()]


HOT_MARK_PERCENT = 10
SUSPICIOUS_DISCOUNT = 40  # this far below the median: often a scam, a broken item or a wrong model  # "🔥 Okazja" header when the price is at least this much below the median
CAPTION_LIMIT = 1024  # Telegram limit for photo captions


def photo_for(item: OutgoingListing, text: str) -> str | None:
    """Photo URL to send with the notification, or None to send plain text."""
    url = item.listing.photo_url
    if not url or not url.startswith("https://") or item.listing.is_test or len(text) > CAPTION_LIMIT:
        return None
    return url


def describe_criteria(search_or_draft, lang: str | None = None) -> str:
    """Compact human description of search criteria (works for Search and SearchDraft)."""
    c = getattr(search_or_draft, "criteria", search_or_draft)
    parts: list[str] = []
    if c.keywords:
        parts.append("🔑 " + esc(", ".join(c.keywords)))
    if c.alt_keywords:
        parts.append("🔀 " + esc(" / ".join(c.alt_keywords)))
    if c.excluded_keywords:
        parts.append("🚫 " + esc(", ".join(c.excluded_keywords)))
    if c.price_min is not None or c.price_max is not None:
        low = fmt_price(c.price_min) if c.price_min is not None else "0"
        high = fmt_price(c.price_max) if c.price_max is not None else "∞"
        parts.append(f"💰 {low}–{high} {esc(c.currency)}")
    condition = getattr(c.condition, "value", c.condition)
    if condition != "any":
        parts.append("📦 " + t(f"condition.{condition}", lang))
    if c.city:
        parts.append("📍 " + esc(c.city) + (f" +{c.radius_km} km" if c.radius_km else ""))
    delivery = getattr(c.delivery, "value", c.delivery)
    if delivery != "any":
        parts.append("🚚 " + t(f"delivery.{delivery}", lang))
    deal = getattr(c, "deal_min_discount", None)
    if deal:
        parts.append(t("criteria.deal", lang, pct=deal))
    return " · ".join(parts) if parts else "—"


def render_search_list(searches: list[Search], monitored: set[int], tz: str, lang: str | None = None) -> str:
    if not searches:
        return t("searches.empty", lang)
    lines = [t("searches.title", lang)]
    for s in searches:
        icon = "▶️" if s.is_active else "⏸"
        sources = ", ".join(DISPLAY_NAMES.get(code, code) for code in s.sources)
        lines.append(t("searches.item", lang, icon=icon, name=esc(s.name), sources=esc(sources),
                       criteria=describe_criteria(s, lang), last=fmt_dt(s.last_checked_at, tz, lang)))
        if s.is_active and s.id not in monitored:
            lines.append("   " + t("searches.over_limit", lang))
    return "\n\n".join(lines)


def render_plan(plan: Plan, lang: str | None = None) -> str:
    extras = []
    if plan.fuzzy_matching:
        extras.append(t("plan.extra.fuzzy", lang))
    if plan.price_drop_alerts:
        extras.append(t("plan.extra.price_drop", lang))
    if plan.advanced_filters:
        extras.append(t("plan.extra.price_compare", lang))
    return t("plan.item", lang, name=esc(plan.name), price=plan.price_stars, days=plan.duration_days,
             searches=plan.max_active_searches, interval=format_interval(plan.min_interval_seconds, lang),
             daily=plan.max_notifications_per_day, extras="\n".join(extras)).rstrip()
