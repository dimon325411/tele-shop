"""Bot command logic without aiogram: every method returns ``Reply`` objects.

``app.bot.handlers`` maps Telegram updates to these methods and sends the replies.
All permission checks are delegated to services (server side).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.config import Settings
from app.container import Container
from app.domain.models import User
from app.domain.payments import PaymentRejected
from app.domain.plans import AccessDenied
from app.domain.timing import local_day_start_utc
from app.domain.validation import InvalidInput, parse_quiet_hours
from app.i18n import esc, t
from app.presentation import Button, fmt_dt, render_plan, render_search_list
from app.services.admin import AdminError, NotAdmin
from app.services.payments import InvoiceRequest
from app.sources.registry import DISPLAY_NAMES

log = logging.getLogger(__name__)


@dataclass
class Reply:
    text: str
    buttons: list[list[Button]] = field(default_factory=list)
    invoice: InvoiceRequest | None = None
    alert: bool = False  # for callback answers


def menu_rows(lang: str | None) -> list[list[Button]]:
    return [
        [Button(t("btn.new_search", lang), callback="menu:new")],
        [Button(t("btn.searches", lang), callback="menu:searches"), Button(t("btn.status", lang), callback="menu:status")],
        [Button(t("btn.plan", lang), callback="menu:plan"), Button(t("btn.settings", lang), callback="settings:open")],
        [Button(t("btn.help", lang), callback="menu:help")],
    ]


def deny(exc: AccessDenied, lang: str | None) -> Reply:
    return Reply(t(f"deny.{exc.code}", lang, **exc.params))


class Controller:
    def __init__(self, c: Container) -> None:
        self.c = c

    @property
    def settings(self) -> Settings:
        return self.c.settings

    # ------------------------------------------------------------- general
    async def start(self, telegram_id: int, username: str | None, first_name: str | None) -> Reply:
        user, created = await self.c.users.register(telegram_id, username, first_name)
        lang = user.language
        if user.is_suspended:
            return Reply(t("error.suspended", lang))
        name = esc(first_name or username or "")
        access = await self.c.subs.access_for(user)
        # Newcomers get the full welcome even if their first message was not /start
        # (any message registers the user silently).
        newcomer = created or not await self.c.searches.list(user)
        if access.active and self.settings.personal_mode:
            text = t("start.welcome_personal", lang, name=name)
        elif newcomer and access.active:
            text = t("start.welcome_new", lang, name=name, plan=esc(access.plan.name),
                     until=fmt_dt(access.subscription.expires_at, user.timezone, lang))
        else:
            text = t("start.welcome_back", lang, name=name)
        return Reply(text, menu_rows(lang))

    def help(self, lang: str | None = None) -> Reply:
        return Reply(t("help", lang), menu_rows(lang))

    def terms(self, user: User) -> Reply:
        buttons = [] if user.terms_accepted_at else [[Button(t("btn.accept_terms", user.language), callback="plan:terms")]]
        return Reply(t("terms", user.language), buttons)

    def privacy(self, lang: str | None = None) -> Reply:
        return Reply(t("privacy", lang, listings_days=self.settings.retention_listings_days,
                       notifications_days=self.settings.retention_notifications_days))

    def support(self, user: User) -> Reply:
        return Reply(t("support", user.language, contact=esc(self.settings.support_contact), tg_id=user.telegram_id))

    def paysupport(self, user: User) -> Reply:
        return Reply(t("paysupport", user.language, contact=esc(self.settings.support_contact), tg_id=user.telegram_id))

    # ------------------------------------------------------------ searches
    async def list_searches(self, user: User) -> Reply:
        lang = user.language
        searches = await self.c.searches.list(user)
        monitored = await self.c.searches.monitored_ids(user)
        text = render_search_list(searches, monitored, user.timezone, lang)
        rows: list[list[Button]] = []
        for s in searches:
            toggle = (Button(f"⏸ {s.name}", callback=f"s:pause:{s.id}") if s.is_active
                      else Button(f"▶️ {s.name}", callback=f"s:resume:{s.id}"))
            rows.append([toggle, Button("🗑", callback=f"s:delete:{s.id}")])
        rows.append([Button(t("btn.new_search", lang), callback="menu:new")])
        return Reply(text, rows)

    async def pick(self, user: User, action: str) -> Reply:
        lang = user.language
        searches = await self.c.searches.list(user)
        if action == "pause":
            items = [s for s in searches if s.is_active]
            empty, title = "searches.none_to_pause", "searches.choose_pause"
        elif action == "resume":
            items = [s for s in searches if not s.is_active]
            empty, title = "searches.none_to_resume", "searches.choose_resume"
        else:
            items = searches
            empty, title = "searches.empty", "searches.choose_delete"
        if not items:
            return Reply(t(empty, lang))
        rows = [[Button(s.name, callback=f"s:{action}:{s.id}")] for s in items]
        if action == "pause" and len(items) > 1:
            rows.append([Button(t("btn.pause_all", lang), callback="s:pauseall:0")])
        return Reply(t(title, lang), rows)

    async def search_action(self, user: User, action: str, search_id: int) -> Reply:
        lang = user.language
        try:
            if action == "pauseall":
                return await self.pause_all(user)
            if action == "pause":
                s = await self.c.searches.pause(user, search_id)
                return Reply(t("searches.paused", lang, name=esc(s.name)))
            if action == "resume":
                s = await self.c.searches.resume(user, search_id)
                return Reply(t("searches.resumed", lang, name=esc(s.name)))
            if action == "delete":
                s = await self.c.searches.get_owned(user, search_id)
                return Reply(t("searches.confirm_delete", lang, name=esc(s.name)),
                             [[Button(t("btn.yes", lang), callback=f"s:delconfirm:{s.id}"),
                               Button(t("btn.no", lang), callback="noop:cancel")]])
            if action == "delconfirm":
                s = await self.c.searches.get_owned(user, search_id)
                await self.c.searches.delete(user, search_id)
                return Reply(t("searches.deleted", lang, name=esc(s.name)))
        except AccessDenied as exc:
            return deny(exc, lang)
        return Reply(t("error.generic", lang))

    async def pause_all(self, user: User) -> Reply:
        count = await self.c.searches.pause_all(user)
        if not count:
            return Reply(t("searches.none_to_pause", user.language))
        return Reply(t("searches.paused_all", user.language, count=count))

    # -------------------------------------------------------------- status
    async def status(self, user: User) -> Reply:
        lang = user.language
        access = await self.c.subs.access_for(user)
        searches = await self.c.searches.list(user)
        active = sum(1 for s in searches if s.is_active)
        if access.active:
            sub_line = t("plan.current", lang, plan=esc(access.plan.name),
                         until=fmt_dt(access.subscription.expires_at, user.timezone, lang))
            limit, daily = access.plan.max_active_searches, access.plan.max_notifications_per_day
        else:
            sub_line, limit, daily = t("plan.none", lang), 0, 0
        sent_today = await self.c.repo.count_sent_since(user.id, local_day_start_utc(access.now, user.timezone))
        states = {s.source: s for s in await self.c.repo.list_source_states()}
        lines = []
        for code in self.c.registry.all_codes():
            adapter = self.c.registry.get(code)
            state = states.get(code)
            if not self.c.registry.is_enabled(code) or (state and not state.enabled):
                key, icon = "disabled", "⚪"
            elif state and state.status == "blocked":
                key, icon = "blocked", "🟠"
            elif state and state.status == "degraded":
                key, icon = "degraded", "🟡"
            else:
                key, icon = ("test" if adapter.is_test_source else "ok"), "🟢"
            lines.append(t("status.source_line", lang, icon=icon, name=esc(DISPLAY_NAMES.get(code, code)),
                           state=t(f"status.source.{key}", lang)))
        return Reply(t("status", lang, subscription=sub_line, active=active, limit=limit, sent_today=sent_today,
                       daily_limit=daily, sources="\n".join(lines)))

    # --------------------------------------------------------------- plans
    async def plan(self, user: User) -> Reply:
        lang = user.language
        access = await self.c.subs.access_for(user)
        head = (t("plan.current", lang, plan=esc(access.plan.name),
                  until=fmt_dt(access.subscription.expires_at, user.timezone, lang))
                if access.active else t("plan.none", lang))
        plans = [p for p in await self.c.repo.list_plans() if not p.is_trial]
        body = "\n\n".join(render_plan(p, lang) for p in plans)
        parts = [head, t("plan.list_title", lang), body, t("plan.change_note", lang)]
        rows: list[list[Button]] = []
        if not self.settings.payments_available:
            parts.append(t("plan.payments_disabled", lang))
        else:
            if user.terms_accepted_at is None:
                rows.append([Button(t("btn.accept_terms", lang), callback="plan:terms")])
            rows += [[Button(t("btn.buy", lang, plan=p.name, price=p.price_stars), callback=f"plan:buy:{p.code}")]
                     for p in plans if p.price_stars > 0]
        return Reply("\n\n".join(parts), rows)

    async def accept_terms(self, user: User) -> Reply:
        await self.c.users.accept_terms(user)
        return Reply(t("terms.accepted", user.language))

    async def buy(self, user: User, plan_code: str) -> Reply:
        lang = user.language
        try:
            invoice = await self.c.payments.create_invoice(user, plan_code)
        except PaymentRejected as exc:
            buttons = []
            if exc.code == "terms_not_accepted":
                buttons = [[Button(t("btn.accept_terms", lang), callback="plan:terms")]]
            return Reply(t(f"pay.error.{exc.code}", lang), buttons, alert=True)
        return Reply("", invoice=invoice)

    # ------------------------------------------------------------ settings
    def settings_view(self, user: User) -> Reply:
        lang = user.language
        quiet = (f"{user.quiet_start_hour}:00–{user.quiet_end_hour}:00"
                 if user.quiet_start_hour is not None else t("settings.quiet_none", lang))
        rows = [
            [Button(t("btn.quiet_hours", lang), callback="settings:quiet")],
            [Button(t("btn.price_drops_on" if user.notify_price_drops else "btn.price_drops_off", lang),
                    callback="settings:pricedrop")],
            [Button(t("btn.new_sellers_hide" if user.hide_new_sellers else "btn.new_sellers_warn", lang),
                    callback="settings:newsellers")],
            [Button(t("btn.delete_account", lang), callback="settings:delete")],
        ]
        return Reply(t("settings.title", lang, quiet=quiet, tz=esc(user.timezone)), rows)

    async def set_quiet_hours(self, user: User, raw: str) -> tuple[Reply, bool]:
        lang = user.language
        try:
            start, end = parse_quiet_hours(raw)
        except InvalidInput as exc:
            return Reply(t(f"invalid.{exc.code}", lang) + "\n" + t("settings.ask_quiet", lang)), False
        await self.c.users.set_quiet_hours(user, start, end)
        quiet = f"{start}:00–{end}:00" if start is not None else t("settings.quiet_none", lang)
        return Reply(t("settings.quiet_saved", lang, quiet=quiet)), True

    async def toggle_price_drops(self, user: User) -> Reply:
        await self.c.users.set_price_drop_alerts(user, not user.notify_price_drops)
        user.notify_price_drops = not user.notify_price_drops
        return self.settings_view(user)

    async def toggle_new_sellers(self, user: User) -> Reply:
        await self.c.users.set_hide_new_sellers(user, not user.hide_new_sellers)
        user.hide_new_sellers = not user.hide_new_sellers
        return self.settings_view(user)

    def confirm_delete_account(self, user: User) -> Reply:
        lang = user.language
        return Reply(t("settings.confirm_delete_account", lang),
                     [[Button(t("btn.yes", lang), callback="settings:delete_confirm"),
                       Button(t("btn.no", lang), callback="noop:cancel")]])

    async def delete_account(self, user: User) -> Reply:
        await self.c.users.delete_account(user)
        return Reply(t("settings.account_deleted", user.language))

    # --------------------------------------------------------------- admin
    async def admin(self, admin_id: int, command: str, args: list[str],
                    refund_call=None) -> Reply:
        """Admin commands. ``command`` without slash, e.g. 'source_off'."""
        a = self.c.admin
        try:
            if command == "admin":
                stats = await a.stats(admin_id)
                return Reply(t("admin.stats", **stats) + "\n\n" + t("admin.help"))
            if command == "admin_sources":
                a.ensure_admin(admin_id)
                states = {s.source: s for s in await a.sources(admin_id)}
                lines = []
                for code in self.c.registry.all_codes():
                    s = states.get(code)
                    enabled_cfg = self.c.registry.is_enabled(code)
                    lines.append(
                        f"<b>{code}</b>: config={'on' if enabled_cfg else 'off'}, "
                        f"admin={'on' if (s is None or s.enabled) else 'off'}, status={s.status if s else 'n/a'}, "
                        f"ok={fmt_dt(s.last_success_at, 'UTC') if s else '-'}, "
                        f"err={esc((s.last_error or '-')[:120]) if s else '-'}")
                return Reply("\n".join(lines))
            if command in {"source_off", "source_on"}:
                if not args:
                    return Reply(t("admin.usage", usage=f"/{command} &lt;kod&gt;"))
                await a.set_source_enabled(admin_id, args[0], command == "source_on", " ".join(args[1:]) or None)
                return Reply(t("admin.ok"))
            if command == "plans_admin":
                a.ensure_admin(admin_id)
                plans = await self.c.repo.list_plans(active_only=False)
                return Reply("\n".join(
                    f"<b>{p.code}</b>: {p.price_stars}⭐/{p.duration_days}d, searches={p.max_active_searches}, "
                    f"interval={p.min_interval_seconds}s, daily={p.max_notifications_per_day}, "
                    f"sources={','.join(p.allowed_sources)}, fuzzy={p.fuzzy_matching}, "
                    f"price_drop={p.price_drop_alerts}, active={p.is_active}" for p in plans))
            if command == "plan_set":
                if len(args) < 3:
                    return Reply(t("admin.usage", usage="/plan_set &lt;plan&gt; &lt;pole&gt; &lt;wartość&gt;"))
                await a.set_plan_field(admin_id, args[0], args[1], " ".join(args[2:]))
                return Reply(t("admin.ok"))
            if command == "payerrors":
                errors = await a.payment_errors(admin_id)
                return Reply("\n".join(f"#{e['id']} {e['event_type']}: {esc(e['error'])}" for e in errors)
                             or t("admin.empty"))
            if command in {"suspend", "unsuspend"}:
                if not args or not args[0].isdigit():
                    return Reply(t("admin.usage", usage=f"/{command} &lt;tg_id&gt; [powód]"))
                await a.set_suspended(admin_id, int(args[0]), command == "suspend", " ".join(args[1:]) or None)
                return Reply(t("admin.ok"))
            if command == "grant":
                if len(args) != 3 or not args[0].isdigit() or not args[2].isdigit():
                    return Reply(t("admin.usage", usage="/grant &lt;tg_id&gt; &lt;plan&gt; &lt;dni&gt;"))
                await a.grant(admin_id, int(args[0]), args[1], int(args[2]))
                return Reply(t("admin.ok"))
            if command == "refund":
                a.ensure_admin(admin_id)
                if not args or refund_call is None:
                    return Reply(t("admin.usage", usage="/refund &lt;charge_id&gt; [powód]"))
                result = await self.c.payments.refund(args[0], admin_id, " ".join(args[1:]) or None, refund_call)
                return Reply(t(f"admin.refund.{result}"))
            if command == "audit":
                rows = await a.audit_log(admin_id)
                return Reply("\n".join(
                    f"{fmt_dt(r['created_at'], 'UTC')} {r['admin_telegram_id']} {esc(r['action'])} {esc(r['target'] or '')}"
                    for r in rows) or t("admin.empty"))
        except NotAdmin:
            return Reply(t("error.not_admin"))
        except AdminError as exc:
            return Reply(t(f"admin.error.{exc.code}", **exc.params))
        return Reply(t("admin.help"))
