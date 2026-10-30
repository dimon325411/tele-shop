"""Inline keyboards (aiogram markup built from ``app.presentation.Button`` specs)."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.models import Plan, Search
from app.i18n import t
from app.presentation import Button


def markup(rows: list[list[Button]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=b.text, url=b.url) if b.url else InlineKeyboardButton(text=b.text, callback_data=b.callback)
         for b in row]
        for row in rows
    ])


def main_menu_rows(lang: str | None = None) -> list[list[Button]]:
    return [
        [Button(t("btn.new_search", lang), callback="menu:new")],
        [Button(t("btn.searches", lang), callback="menu:searches"), Button(t("btn.status", lang), callback="menu:status")],
        [Button(t("btn.plan", lang), callback="menu:plan"), Button(t("btn.settings", lang), callback="settings:open")],
        [Button(t("btn.help", lang), callback="menu:help")],
    ]


def main_menu(lang: str | None = None) -> InlineKeyboardMarkup:
    return markup(main_menu_rows(lang))


def search_picker(searches: list[Search], action: str) -> InlineKeyboardMarkup:
    return markup([[Button(s.name, callback=f"s:{action}:{s.id}")] for s in searches])


def searches_overview(searches: list[Search], lang: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[Button]] = []
    for s in searches:
        toggle = (Button(f"⏸ {s.name}", callback=f"s:pause:{s.id}") if s.is_active
                  else Button(f"▶️ {s.name}", callback=f"s:resume:{s.id}"))
        rows.append([toggle, Button("🗑", callback=f"s:delete:{s.id}")])
    rows.append([Button(t("btn.new_search", lang), callback="menu:new")])
    return markup(rows)


def confirm(yes_callback: str, lang: str | None = None) -> InlineKeyboardMarkup:
    return markup([[Button(t("btn.yes", lang), callback=yes_callback), Button(t("btn.no", lang), callback="noop:cancel")]])


def plans_keyboard(plans: list[Plan], terms_accepted: bool, lang: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[Button]] = []
    if not terms_accepted:
        rows.append([Button(t("btn.accept_terms", lang), callback="plan:terms")])
    for p in plans:
        if not p.is_trial and p.price_stars > 0:
            rows.append([Button(t("btn.buy", lang, plan=p.name, price=p.price_stars), callback=f"plan:buy:{p.code}")])
    return markup(rows)


def settings_keyboard(price_drops: bool, lang: str | None = None) -> InlineKeyboardMarkup:
    return markup([
        [Button(t("btn.quiet_hours", lang), callback="settings:quiet")],
        [Button(t("btn.price_drops_on" if price_drops else "btn.price_drops_off", lang), callback="settings:pricedrop")],
        [Button(t("btn.delete_account", lang), callback="settings:delete")],
    ])
