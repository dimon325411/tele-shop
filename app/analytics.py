"""Reseller commands without aiogram: /deals, /chart and free-text questions."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta

from app.container import Container
from app.domain.models import Search, User
from app.i18n import esc, t
from app.presentation import Button, fmt_dt, fmt_price, fmt_variant
from app.domain.pricing import price_stats
from app.domain.timing import local_day_start_utc
from app.domain.variants import variant_of
from app.services.market import ALL_VARIANTS, usable

DEFAULT_SPREAD = 10
STATS_CHEAPEST = 3
STATS_MAX_VARIANTS = 4
FLAT_NAV_LIMIT = 8  # up to this many searches: no category step
CATEGORY_ORDER = ["iphone", "konsole", "karty_graficzne", "laptopy", "komputery"]
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
        deals = deals[:5 if len(searches) == 1 else 10]
        pct = spread or (searches[0].criteria.deal_min_discount if len(searches) == 1 else None) or DEFAULT_SPREAD
        names = ", ".join(s.name for s in searches) if len(searches) <= 3 else t("deals.all_name", lang,
                                                                                  count=len(searches))
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
            title = l.title if len(searches) == 1 else f"{s.name}: {l.title}"
            lines.append(t("deals.item", lang, n=i, title=esc(title), price=fmt_price(l.price),
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


    # ------------------------------------------------------------------ /stats
    async def nav(self, user: User, mode: str, category: str | None = None) -> tuple[str, list[list[Button]]]:
        """One navigation for every tool: category -> model. ``mode``: st (stats), ch (chart), dl (deals), ai."""
        lang = user.language
        searches = await self.c.searches.list(user)
        if not searches:
            return t("searches.empty", lang), [[Button(t("btn.m_pack", lang), callback="menu:pack")],
                                               [Button(t("btn.new_search", lang), callback="menu:new")]]
        counts: dict[str, int] = {}
        for s in searches:
            counts[s.criteria.category or "other"] = counts.get(s.criteria.category or "other", 0) + 1
        rows: list[list[Button]] = []
        if category is None and len(searches) > FLAT_NAV_LIMIT and len(counts) > 1:
            if mode == "dl":
                rows.append([Button(t("btn.all_searches", lang), callback="dl:all")])
            order = [c for c in CATEGORY_ORDER if c in counts] + sorted(c for c in counts if c not in CATEGORY_ORDER)
            for i in range(0, len(order), 2):
                rows.append([Button(f"{_cat_label(c, lang)} ({counts[c]})", callback=f"nav:{mode}:{c}")
                             for c in order[i:i + 2]])
            rows.append([Button(t("btn.menu", lang), callback="menu:home")])
            return t(f"nav.title.{mode}", lang, what=t("nav.what.category", lang)), rows
        chosen = [s for s in searches if category is None or (s.criteria.category or "other") == category]
        if mode == "dl" and category is not None:
            rows.append([Button(t("btn.whole_category", lang, category=_cat_label(category, lang)),
                                callback=f"dlc:{category}")])
        elif mode == "dl" and len(chosen) > 1:
            rows.append([Button(t("btn.all_searches", lang), callback="dl:all")])
        leaf = {"st": "st:s:{}", "ch": "chs:{}", "dl": "dl:{}", "ai": "ai:c:{}"}[mode]
        for i in range(0, len(chosen), 2):  # two per row: 16 iPhones fit on one screen
            rows.append([Button(s.name, callback=leaf.format(s.id)) for s in chosen[i:i + 2]])
        back = f"nav:{mode}" if category is not None and len(counts) > 1 and len(searches) > FLAT_NAV_LIMIT \
            else "menu:home"
        rows.append([Button(t("btn.back", lang), callback=back)])
        return t(f"nav.title.{mode}", lang, what=t("nav.what.model", lang)), rows

    async def stats(self, user: User, search: Search) -> tuple[str, list[list[Button]]]:
        """Market card of one search: median, typical range, cheapest per storage variant, trend, cheapest offers."""
        lang = user.language
        now = self.market.clock()
        market = [m for m in await self.market.market(search.id) if usable(m)]
        if search.criteria.condition and getattr(search.criteria.condition, "value", search.criteria.condition) != "any":
            wanted = getattr(search.criteria.condition, "value", search.criteria.condition)
            market = [m for m in market if m.condition in (wanted, None)]
        day_start = local_day_start_utc(now, user.timezone)
        today = sum(1 for m in market if m.first_seen_at and m.first_seen_at >= day_start
                    and (m.posted_at is None or m.posted_at >= day_start))
        lines = [t("stats.title", lang, name=esc(search.name))]
        pause = (Button(t("btn.pause", lang), callback=f"s:pause:{search.id}") if search.is_active
                 else Button(t("btn.resume", lang), callback=f"s:resume:{search.id}"))
        buttons = [[Button(t("btn.stats_chart", lang), callback=f"chs:{search.id}"),
                    Button(t("btn.stats_deals", lang), callback=f"dl:{search.id}")],
                   [Button(t("btn.card_ai", lang), callback=f"ai:c:{search.id}"), pause],
                   [Button(t("btn.back", lang), callback=f"nav:st:{search.criteria.category or 'other'}"),
                    Button(t("btn.menu", lang), callback="menu:home")]]
        if not market:
            lines.append(t("stats.empty", lang))
            return "\n\n".join(lines), buttons
        lines.append(t("stats.summary", lang, count=len(market), today=today))
        cur = esc(market[0].currency or "PLN")
        pct = search.criteria.deal_min_discount or DEFAULT_SPREAD
        groups: dict[str, list] = {}
        for m in market:
            groups.setdefault(variant_of(m.title) or ALL_VARIANTS, []).append(m)
        variants = sorted((v for v in groups if v != ALL_VARIANTS), key=lambda v: -len(groups[v]))[:STATS_MAX_VARIANTS]
        keys = variants if variants else [ALL_VARIANTS]
        shown: dict[str, object] = {}  # variant -> stats shown in the card (cheapest offers compare to these)
        for key in keys:
            items = groups[key] if key != ALL_VARIANTS else market
            label = fmt_variant(key) if key != ALL_VARIANTS else t("stats.all_variants", lang)
            st = price_stats([m.price for m in items])
            if st is None:
                lines.append(t("stats.few", lang, variant=label, count=len(items)))
                continue
            shown[key] = st
            trend = ""
            history = await self.market.history(search.id, key, days=8)
            if len(history) >= 2 and history[0]["median"]:
                old = history[0]["median"]
                change = int(round((float(st.median) - float(old)) / float(old) * 100))
                trend = t("stats.trend", lang, days=(now.date() - _day(history[0]["day"])).days,
                          pct=f"{change:+d}%".replace("-", "−"))
            deal_price = (st.median * (100 - pct) / 100).quantize(st.median.__class__("1"))
            lines.append(t("stats.variant", lang, variant=label, count=st.count, median=fmt_price(st.median.quantize(st.median.__class__("1"))), cur=cur,
                           p25=fmt_price(st.p25.quantize(st.p25.__class__("1"))),
                           p75=fmt_price(st.p75.quantize(st.p75.__class__("1"))),
                           low=fmt_price(min(m.price for m in items)), trend=trend, pct=pct,
                           deal=fmt_price(deal_price)))
        cheapest = sorted(market, key=lambda m: m.price)[:STATS_CHEAPEST]
        if cheapest:
            items = []
            for n, m in enumerate(cheapest, 1):
                st = shown.get(variant_of(m.title) or ALL_VARIANTS) if variants else shown.get(ALL_VARIANTS)
                diff = (t("stats.vs_median", lang, pct=f"{st.diff_percent(m.price):+d}%".replace("-", "−")) if st
                        else t("stats.no_variant", lang) if variants and not variant_of(m.title) else "")
                variant = f" · {fmt_variant(variant_of(m.title))}" if variant_of(m.title) else ""
                items.append(t("stats.cheap_item", lang, n=n, price=fmt_price(m.price), cur=cur, diff=diff,
                               variant=variant, url=esc(m.url), title=esc(m.title[:55])))
            lines.append(t("stats.cheapest", lang) + "\n" + "\n".join(items))
        lines.append(t("stats.footer", lang))
        return "\n\n".join(lines), buttons


def _cat_label(code: str, lang: str | None) -> str:
    return t(f"category.{code}", lang) if code != "other" else t("stats.other", lang)


def _day(value):
    from datetime import date

    return value if isinstance(value, date) else date.fromisoformat(str(value))
