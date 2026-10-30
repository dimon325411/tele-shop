"""Rendering of bot messages (pure functions, no aiogram) so they can be unit-tested.

Buttons are returned as ``Button`` specs; ``app.bot.keyboards`` turns them into aiogram markup.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.domain.models import Plan, Search
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


def fmt_dt(value: datetime | None, tz: str, lang: str | None = None) -> str:
    if value is None:
        return t("never", lang)
    return value.astimezone(ZoneInfo(tz)).strftime("%d.%m.%Y %H:%M")


def render_listing_notification(item: OutgoingListing, now: datetime) -> tuple[str, list[list[Button]]]:
    user, listing = item.user, item.listing
    lang = user.language
    lines = [t("notify.price_drop" if item.kind == "price_drop" else "notify.new", lang, title=esc(listing.title))]
    currency = esc(listing.currency or "PLN")
    if item.kind == "price_drop" and item.old_price is not None and listing.price is not None:
        lines.append(t("notify.price_drop_line", lang, old=fmt_price(item.old_price), price=fmt_price(listing.price),
                       currency=currency))
    elif listing.price is not None:
        lines.append(t("notify.price", lang, price=fmt_price(listing.price), currency=currency))
    else:
        lines.append(t("notify.price_unknown", lang))
    lines.append(t("notify.source", lang, source=esc(DISPLAY_NAMES.get(listing.source, listing.source))))
    location = ", ".join(x for x in (listing.city, listing.region) if x)
    if location:
        lines.append(t("notify.location", lang, location=esc(location)))
    if listing.condition:
        lines.append(t("notify.condition", lang, condition=t(f"condition.{listing.condition}", lang)))
    lines.append(t("notify.found_at", lang, time=fmt_dt(listing.first_seen_at or now, user.timezone, lang)))
    if item.search_name:
        lines.append(t("notify.search", lang, name=esc(item.search_name)))
    if listing.is_test:
        lines.append(t("notify.test_marker", lang))
    buttons = [[Button(t("btn.open_listing", lang), url=listing.url)]]
    row = []
    if item.search_id is not None:
        row.append(Button(t("btn.pause_search", lang), callback=f"s:pause:{item.search_id}"))
    row.append(Button(t("btn.notify_settings", lang), callback="settings:open"))
    buttons.append(row)
    return "\n".join(lines), buttons


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
    return t("plan.item", lang, name=esc(plan.name), price=plan.price_stars, days=plan.duration_days,
             searches=plan.max_active_searches, interval=format_interval(plan.min_interval_seconds, lang),
             daily=plan.max_notifications_per_day, extras="\n".join(extras)).rstrip()
