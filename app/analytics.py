"""Reseller commands without aiogram: /deals, /chart and free-text questions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta

from app.container import Container
from app.domain.models import Search, User
from app.i18n import esc, t
from app.presentation import Button, fmt_dt, fmt_price, fmt_variant
from app.services.market import ALL_VARIANTS

DEFAULT_SPREAD = 10
MIN_HISTORY_DAYS = 2


@dataclass
class ChartResult:
    png: bytes | None = None
    caption: str = ""
    text: str | None = None  # when there is nothing to draw
    buttons: list[list[Button]] = field(default_factory=list)


class Analytics:
    def __init__(self, c: Container) -> None:
        self.c = c
        self.market = c.monitoring.market

    async def deals(self, user: User, searches: list[Search], spread: int | None = None) -> tuple[str, list]:
        lang = user.language
        deals = []
        for s in searches:
            pct = spread or s.criteria.deal_min_discount or DEFAULT_SPREAD
            deals += [(s, d) for d in await self.market.find_deals(s, pct, limit=10)]
        deals.sort(key=lambda sd: -sd[1].discount)
        deals = deals[:5]
        pct = spread or (searches[0].criteria.deal_min_discount if len(searches) == 1 else None) or DEFAULT_SPREAD
        names = ", ".join(s.name for s in searches)
        if not deals:
            count = 0
            for s in searches:
                count += len(await self.market.current_prices(s.id))
            return t("deals.none", lang, pct=pct, name=esc(names), count=count), []
        lines = [t("deals.header", lang, pct=pct, name=esc(names))]
        buttons = []
        for i, (s, d) in enumerate(deals, 1):
            l = d.listing
            variant = f" · {fmt_variant(d.variant)}" if d.variant else ""
            lines.append(t("deals.item", lang, n=i, title=esc(l.title), price=fmt_price(l.price),
                           currency=esc(l.currency or "PLN"), pct=d.discount, median=fmt_price(d.stats.median),
                           count=d.stats.count, variant=variant, profit=fmt_price(d.potential_profit),
                           posted=fmt_dt(l.posted_at or l.first_seen_at, user.timezone, lang)))
            buttons.append([Button(f"{i}. {l.title[:40]}", url=l.url)])
        lines.append(t("deals.footer", lang))
        return "\n\n".join(lines), buttons

    async def chart_options(self, user: User, search: Search) -> list[list[Button]]:
        variants = await self.market.variants(search.id)
        rows = [[Button(f"{fmt_variant(v)} ({n})", callback=f"ch:{search.id}:{v}")] for v, n in variants[:6]]
        rows.append([Button(t("chart.all_variants", user.language), callback=f"ch:{search.id}:{ALL_VARIANTS}")])
        return rows

    async def chart(self, user: User, search: Search, variant: str = ALL_VARIANTS) -> ChartResult:
        from app.services.charts import distribution_chart, history_chart

        lang = user.language
        label = esc(search.name) + (f" · {fmt_variant(variant)}" if variant != ALL_VARIANTS else "")
        await self.market.take_snapshot(search.id)  # make sure today's point exists
        rows = await self.market.history(search.id, variant)
        if len(rows) >= MIN_HISTORY_DAYS:
            first, last = rows[0]["day"], rows[-1]["day"]
            sub = t("chart.history_sub", lang, start=first.strftime("%d.%m.%Y"), end=last.strftime("%d.%m.%Y"),
                    count=rows[-1]["count"])
            png = await asyncio.to_thread(history_chart, rows, label, sub)
            change = (rows[-1]["median"] - rows[0]["median"]) / rows[0]["median"] * 100
            caption = t("chart.history_caption", lang, name=label, median=fmt_price(rows[-1]["median"]),
                        change=f"{change:+.0f}", days=(last - first).days)
            return ChartResult(png, caption)
        prices = await self.market.current_prices(search.id, variant)
        if len(prices) < 5:
            return ChartResult(text=t("chart.no_data", lang, name=label))
        since = search.created_at or self.c.monitoring.clock()
        png = await asyncio.to_thread(distribution_chart, prices, t("chart.now_title", lang, name=label),
                                      t("chart.now_sub", lang, count=len(prices)))
        caption = t("chart.now_caption", lang, name=label, since=fmt_dt(since, user.timezone, lang))
        return ChartResult(png, caption)
