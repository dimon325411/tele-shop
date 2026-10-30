"""Telegram handlers (aiogram 3). Thin layer: parse update -> Controller/SearchWizard -> send Reply."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (BufferedInputFile, CallbackQuery, ErrorEvent, KeyboardButton, LabeledPrice,
                           LinkPreviewOptions, Message, PreCheckoutQuery, ReplyKeyboardMarkup)

from app.bot.keyboards import markup
from app.container import Container
from app.controller import Controller, Reply
from app.domain.plans import AccessDenied
from app.i18n import esc, format_interval, t
from app.presentation import TELEGRAM_TEXT_LIMIT, Button, fmt_dt, split_message
from app.services.payments import SuccessfulPaymentData
from app.wizard import SearchWizard

log = logging.getLogger(__name__)
router = Router(name="main")

ADMIN_COMMANDS = ["admin", "admin_sources", "source_off", "source_on", "plans_admin", "plan_set", "payerrors",
                  "suspend", "unsuspend", "grant", "refund", "audit"]


NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


class Wizard(StatesGroup):
    active = State()


class SettingsInput(StatesGroup):
    quiet_hours = State()


async def send(message: Message, reply: Reply) -> None:
    parts = split_message(reply.text)
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        await message.answer(part, reply_markup=markup(reply.buttons) if reply.buttons and last else None,
                             link_preview_options=NO_PREVIEW)


async def edit_or_send(cb: CallbackQuery, reply: Reply) -> None:
    kb = markup(reply.buttons) if reply.buttons else None
    if len(reply.text) > TELEGRAM_TEXT_LIMIT:
        await send(cb.message, reply)
        return
    try:
        await cb.message.edit_text(reply.text, reply_markup=kb, link_preview_options=NO_PREVIEW)
    except TelegramBadRequest:  # message too old / identical / not editable
        await cb.message.answer(reply.text, reply_markup=kb, link_preview_options=NO_PREVIEW)


# ---------------------------------------------------------------- general
@router.message(CommandStart())
async def cmd_start(message: Message, ctl: Controller, state: FSMContext) -> None:
    await state.clear()
    u = message.from_user
    reply = await ctl.start(u.id, u.username, u.first_name)
    await send(message, reply)
    if reply.buttons:  # registered and allowed: persistent quick buttons under the input field
        lang = (await ctl.c.repo.get_user_by_telegram_id(u.id)).language
        await message.answer(t("kb.ready", lang), reply_markup=quick_keyboard(lang))


def quick_keyboard(lang: str | None) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=t("kb.deals", lang)), KeyboardButton(text=t("kb.stats", lang))],
        [KeyboardButton(text=t("kb.searches", lang)), KeyboardButton(text=t("kb.menu", lang))],
    ], resize_keyboard=True, is_persistent=True)


QUICK_BUTTONS = {"kb.deals": "dl", "kb.stats": "st", "kb.searches": "searches", "kb.menu": "menu"}


@router.message(F.text.func(lambda text: any(text == t(k) for k in QUICK_BUTTONS)))
async def quick_button(message: Message, ctl: Controller, user, state: FSMContext, container: Container) -> None:
    """Bottom keyboard: works from any state (also leaves an unfinished wizard)."""
    await state.clear()
    action = next(v for k, v in QUICK_BUTTONS.items() if message.text == t(k, user.language))
    if action in ("dl", "st"):
        await send_nav(message, user, container, action)
    elif action == "searches":
        await send(message, await ctl.list_searches(user))
    else:
        await send(message, ctl.menu(user.language))


@router.message(Command("help"))
async def cmd_help(message: Message, ctl: Controller, user) -> None:
    await send(message, ctl.help(user.language))


@router.message(Command("terms"))
async def cmd_terms(message: Message, ctl: Controller, user) -> None:
    await send(message, ctl.terms(user))


@router.message(Command("privacy"))
async def cmd_privacy(message: Message, ctl: Controller, user) -> None:
    await send(message, ctl.privacy(user.language))


@router.message(Command("support"))
async def cmd_support(message: Message, ctl: Controller, user) -> None:
    await send(message, ctl.support(user))


@router.message(Command("paysupport"))
async def cmd_paysupport(message: Message, ctl: Controller, user) -> None:
    await send(message, ctl.paysupport(user))


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, user) -> None:
    await state.clear()
    await message.answer(t("cancelled", user.language))


@router.callback_query(F.data == "noop:cancel")
async def cb_noop(cb: CallbackQuery, user) -> None:
    await cb.answer()
    await edit_or_send(cb, Reply(t("cancelled", user.language)))


@router.callback_query(F.data.startswith("menu:"))
async def cb_menu(cb: CallbackQuery, ctl: Controller, user, state: FSMContext, container: Container) -> None:
    await cb.answer()
    item = cb.data.split(":", 1)[1]
    if item == "new":
        await start_wizard(cb.message, user, state, container)
    elif item == "searches":
        await send(cb.message, await ctl.list_searches(user))
    elif item == "status":
        await send(cb.message, await ctl.status(user))
    elif item == "plan":
        await send(cb.message, await ctl.plan(user))
    elif item == "help":
        await send(cb.message, ctl.help(user.language))
    elif item == "home":
        await edit_or_send(cb, ctl.menu(user.language))
    elif item == "pack":
        await cmd_pack(cb.message, user, container)


# ------------------------------------------------------------------ wizard
async def start_wizard(message: Message, user, state: FSMContext, container: Container) -> None:
    access = await container.subs.access_for(user)
    try:
        searches = await container.searches.list(user)
        access.check_can_create_search(sum(1 for s in searches if s.is_active))
    except AccessDenied as exc:
        await message.answer(t(f"deny.{exc.code}", user.language, **exc.params))
        return
    wizard = SearchWizard(container.registry, user.language)
    wstate, reply = wizard.start(access.plan)
    await state.set_state(Wizard.active)
    sent = await message.answer(reply.text, reply_markup=markup(reply.buttons))
    await state.update_data(wizard=wstate, wizard_msg_id=sent.message_id)


async def drop_old_buttons(message: Message, data: dict) -> None:
    """Remove the inline keyboard of the previous wizard step so stale buttons can't be pressed."""
    old_id = data.get("wizard_msg_id")
    if not old_id:
        return
    try:
        await message.bot.edit_message_reply_markup(chat_id=message.chat.id, message_id=old_id, reply_markup=None)
    except TelegramBadRequest:  # already edited / too old
        pass


@router.message(Command("newsearch"))
async def cmd_newsearch(message: Message, user, state: FSMContext, container: Container) -> None:
    await start_wizard(message, user, state, container)


@router.message(Wizard.active, F.text & ~F.text.startswith("/"))
async def wizard_text(message: Message, user, state: FSMContext, container: Container) -> None:
    data = await state.get_data()
    wstate = data.get("wizard")
    if not wstate:
        await state.clear()
        return
    wizard = SearchWizard(container.registry, user.language)
    reply = wizard.on_text(wstate, message.text)
    await drop_old_buttons(message, data)
    sent = await message.answer(reply.text, reply_markup=markup(reply.buttons))
    await state.update_data(wizard=wstate, wizard_msg_id=sent.message_id)


@router.callback_query(F.data.startswith("wz:"))
async def wizard_callback(cb: CallbackQuery, user, state: FSMContext, container: Container) -> None:
    data = await state.get_data()
    wstate = data.get("wizard")
    if await state.get_state() != Wizard.active.state or not wstate:
        await cb.answer(t("cancelled", user.language))
        return
    wizard = SearchWizard(container.registry, user.language)
    reply = wizard.on_callback(wstate, cb.data)
    await cb.answer()
    if reply.cancelled:
        await state.clear()
        await edit_or_send(cb, Reply(reply.text))
        return
    if reply.finished:
        try:
            search = await container.searches.create(user, wizard.draft(wstate))
        except AccessDenied as exc:
            await edit_or_send(cb, Reply(t(f"deny.{exc.code}", user.language, **exc.params)))
            await state.clear()
            return
        await state.clear()
        await edit_or_send(cb, Reply(t("wizard.saved", user.language, name=esc(search.name),
                                       interval=format_interval(search.interval_seconds, user.language))))
        return
    await state.update_data(wizard=wstate, wizard_msg_id=cb.message.message_id)
    await edit_or_send(cb, Reply(reply.text, reply.buttons))


# ---------------------------------------------------------------- searches
@router.message(Command("searches"))
async def cmd_searches(message: Message, ctl: Controller, user) -> None:
    await send(message, await ctl.list_searches(user))


@router.message(Command("pause"))
async def cmd_pause(message: Message, ctl: Controller, user) -> None:
    await send(message, await ctl.pick(user, "pause"))


@router.message(Command("resume"))
async def cmd_resume(message: Message, ctl: Controller, user) -> None:
    await send(message, await ctl.pick(user, "resume"))


@router.message(Command("delete"))
async def cmd_delete(message: Message, ctl: Controller, user) -> None:
    await send(message, await ctl.pick(user, "delete"))


@router.callback_query(F.data.regexp(r"^s:(pause|resume|delete|delconfirm|pauseall):\d+$"))
async def cb_search(cb: CallbackQuery, ctl: Controller, user) -> None:
    _, action, sid = cb.data.split(":")
    reply = await ctl.search_action(user, action, int(sid))
    await cb.answer()
    body = (getattr(cb.message, "text", None) or getattr(cb.message, "caption", None) or "")
    if body.startswith(("🆕", "📉", "🔥")):  # a notification (text or photo caption) is never overwritten
        await cb.message.answer(reply.text, reply_markup=markup(reply.buttons) if reply.buttons else None)
    else:
        await edit_or_send(cb, reply)  # notifications are never overwritten


@router.callback_query(F.data.regexp(r"^sl:\d+$"))
async def cb_search_page(cb: CallbackQuery, ctl: Controller, user) -> None:
    await cb.answer()
    await edit_or_send(cb, await ctl.list_searches(user, int(cb.data.split(":")[1])))


# ------------------------------------------------------------- status/plan
@router.message(Command("status"))
async def cmd_status(message: Message, ctl: Controller, user) -> None:
    await send(message, await ctl.status(user))


@router.message(Command("plan"))
async def cmd_plan(message: Message, ctl: Controller, user) -> None:
    await send(message, await ctl.plan(user))


@router.callback_query(F.data == "plan:terms")
async def cb_terms(cb: CallbackQuery, ctl: Controller, user) -> None:
    reply = await ctl.accept_terms(user)
    await cb.answer(reply.text)
    await cb.message.answer(reply.text)


@router.callback_query(F.data.startswith("plan:buy:"))
async def cb_buy(cb: CallbackQuery, ctl: Controller, user, bot: Bot) -> None:
    reply = await ctl.buy(user, cb.data.split(":", 2)[2])
    if reply.invoice is None:
        await cb.answer(reply.text[:190], show_alert=True)
        if reply.buttons:
            await cb.message.answer(reply.text, reply_markup=markup(reply.buttons))
        return
    await cb.answer()
    inv = reply.invoice
    lang = user.language
    await bot.send_invoice(
        chat_id=cb.from_user.id,
        title=t("pay.invoice_title", lang, plan=inv.plan_name),
        description=t("pay.invoice_description", lang, plan=inv.plan_name, days=inv.duration_days),
        payload=inv.payload,
        currency=inv.currency,  # "XTR"; provider_token stays empty for Telegram Stars
        prices=[LabeledPrice(label=inv.plan_name, amount=inv.amount)],
    )


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, container: Container) -> None:
    error = await container.payments.pre_checkout(
        from_user_id=query.from_user.id, currency=query.currency, total_amount=query.total_amount,
        invoice_payload=query.invoice_payload)
    if error is None:
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message=t(f"pay.error.{error}"))


@router.message(F.successful_payment)
async def successful_payment(message: Message, container: Container) -> None:
    sp = message.successful_payment
    result = await container.payments.handle_successful_payment(SuccessfulPaymentData(
        from_user_id=message.from_user.id, currency=sp.currency, total_amount=sp.total_amount,
        invoice_payload=sp.invoice_payload, telegram_payment_charge_id=sp.telegram_payment_charge_id,
        provider_payment_charge_id=sp.provider_payment_charge_id))
    user = await container.repo.get_user_by_telegram_id(message.from_user.id)
    lang = user.language if user else None
    if result.status == "granted":
        plan = await container.repo.get_plan(result.subscription.plan_id)
        await message.answer(t("pay.success", lang, plan=esc(plan.name),
                               until=fmt_dt(result.subscription.expires_at, user.timezone, lang)))
    elif result.status == "duplicate":
        await message.answer(t("pay.duplicate", lang))
    else:
        await message.answer(t("pay.problem", lang))


# ---------------------------------------------------------------- settings
@router.message(Command("settings"))
async def cmd_settings(message: Message, ctl: Controller, user) -> None:
    await send(message, ctl.settings_view(user))


@router.callback_query(F.data.startswith("settings:"))
async def cb_settings(cb: CallbackQuery, ctl: Controller, user, state: FSMContext) -> None:
    action = cb.data.split(":", 1)[1]
    await cb.answer()
    if action == "open":
        await send(cb.message, ctl.settings_view(user))
    elif action == "quiet":
        await state.set_state(SettingsInput.quiet_hours)
        await cb.message.answer(t("settings.ask_quiet", user.language))
    elif action == "pricedrop":
        await edit_or_send(cb, await ctl.toggle_price_drops(user))
    elif action == "newsellers":
        await edit_or_send(cb, await ctl.toggle_new_sellers(user))
    elif action == "area":
        await edit_or_send(cb, ctl.area_cities(user))
    elif action == "delete":
        await edit_or_send(cb, ctl.confirm_delete_account(user))
    elif action == "delete_confirm":
        await state.clear()
        await edit_or_send(cb, await ctl.delete_account(user))


@router.callback_query(F.data.regexp(r"^area:(all|c:[a-z]+|r:[a-z]+:\d+)$"))
async def cb_area(cb: CallbackQuery, ctl: Controller, user) -> None:
    await cb.answer()
    parts = cb.data.split(":")
    if parts[1] == "all":
        reply = await ctl.set_area(user, None, None)
    elif parts[1] == "c":
        reply = ctl.area_radius(user, parts[2])
    else:
        reply = await ctl.set_area(user, parts[2], int(parts[3]))
    await edit_or_send(cb, reply)


@router.message(SettingsInput.quiet_hours, F.text & ~F.text.startswith("/"))
async def quiet_hours_input(message: Message, ctl: Controller, user, state: FSMContext) -> None:
    reply, ok = await ctl.set_quiet_hours(user, message.text)
    if ok:
        await state.clear()
    await send(message, reply)


# ------------------------------------------------------------- AI advisor
async def _ai_answer(message: Message, user, run) -> None:
    """Show a 'thinking' message, run the advisor, replace it with the answer (plain text, no HTML)."""
    from app.services.ai import AIUnavailable

    lang = user.language
    wait = await message.answer(t("ai.thinking", lang))
    try:
        text = await run()
    except AIUnavailable as exc:
        await wait.edit_text(t(f"ai.error.{exc.code}", lang))
        return
    await wait.edit_text(f"{t('ai.answer_header', lang)}\n\n{text}", parse_mode=None,
                         link_preview_options=NO_PREVIEW)


@router.callback_query(F.data.regexp(r"^ai:l:\d+$"))
async def cb_ai_listing(cb: CallbackQuery, user, container: Container) -> None:
    await cb.answer()
    listing_id = int(cb.data.split(":")[2])
    await _ai_answer(cb.message, user, lambda: container.advisor.advise_listing(user, listing_id))


@router.message(Command("compare"))
async def cmd_compare(message: Message, user, container: Container) -> None:
    searches = [s for s in await container.searches.list(user)]
    if not searches:
        await message.answer(t("searches.empty", user.language))
        return
    if len(searches) == 1:
        await _ai_answer(message, user, lambda: _compare_text(user, container, searches[0].id))
        return
    await send_nav(message, user, container, "ai")


@router.callback_query(F.data.regexp(r"^ai:c:\d+$"))
async def cb_ai_compare(cb: CallbackQuery, user, container: Container) -> None:
    await cb.answer()
    search_id = int(cb.data.split(":")[2])
    await _ai_answer(cb.message, user, lambda: _compare_text(user, container, search_id))


async def _compare_text(user, container: Container, search_id: int) -> str:
    from app.domain.timing import utcnow
    from app.presentation import fmt_posted

    answer, listings = await container.advisor.compare_search(user, search_id)
    now = utcnow()
    offers = "\n".join(f"{i + 1}. {l.title} — {l.price if l.price is not None else '?'} {l.currency or ''}"
                       f" · 📅 {fmt_posted(l, now, user.timezone, user.language)}\n{l.url}"
                       for i, l in enumerate(listings))
    return f"{answer}\n\n{t('ai.compare_offers', user.language)}\n{offers}"


@router.message(StateFilter(None), F.reply_to_message, F.text & ~F.text.startswith("/"))
async def reply_question(message: Message, user, container: Container, ctl: Controller) -> None:
    """User replies to a notification with a question -> ask the advisor about that listing."""
    listing_id = await container.repo.find_listing_by_message(user.id, message.reply_to_message.message_id)
    if listing_id is None:
        await send(message, Reply(t("menu.title", user.language), ctl.help(user.language).buttons))
        return
    await _ai_answer(message, user, lambda: container.advisor.advise_listing(user, listing_id, message.text))


# --------------------------------------------------------- reseller tools
async def send_deals(message: Message, user, container: Container, searches, spread=None) -> None:
    from app.analytics import Analytics

    text, buttons = await Analytics(container).deals(user, searches, spread)
    await send(message, Reply(text, buttons))


async def send_chart_options(message: Message, user, container: Container, search) -> None:
    from app.analytics import Analytics

    rows = await Analytics(container).chart_options(user, search)
    if len(rows) == 1:  # no variants detected -> draw right away
        await send_chart(message, user, container, search, "all")
        return
    await message.answer(t("chart.choose_variant", user.language), reply_markup=markup(rows))


async def send_chart(message: Message, user, container: Container, search, variant: str) -> None:
    from app.analytics import Analytics

    await message.bot.send_chat_action(message.chat.id, "upload_photo")
    result = await Analytics(container).chart(user, search, variant)
    if result.png is None:
        await message.answer(result.text)
        return
    await message.answer_photo(BufferedInputFile(result.png, filename="chart.png"), caption=result.caption)


@router.message(Command("deals"))
async def cmd_deals(message: Message, user, container: Container) -> None:
    searches = await container.searches.list(user)
    if not searches:
        await message.answer(t("searches.empty", user.language))
        return
    if len(searches) == 1:
        await send_deals(message, user, container, searches)
        return
    await send_nav(message, user, container, "dl")


@router.callback_query(F.data.regexp(r"^dl:(\d+|all)$"))
async def cb_deals(cb: CallbackQuery, user, container: Container) -> None:
    await cb.answer()
    if cb.data == "dl:all":
        searches = [s for s in await container.searches.list(user) if s.is_active]
        await send_deals(cb.message, user, container, searches)
        return
    try:
        search = await container.searches.get_owned(user, int(cb.data.split(":")[1]))
    except AccessDenied as exc:
        await cb.message.answer(t(f"deny.{exc.code}", user.language))
        return
    await send_deals(cb.message, user, container, [search])


async def send_nav(target: Message, user, container: Container, mode: str, category: str | None = None,
                   cb: CallbackQuery | None = None) -> None:
    from app.analytics import Analytics

    text, rows = await Analytics(container).nav(user, mode, category)
    if cb is not None:
        await edit_or_send(cb, Reply(text, rows))
    else:
        await send(target, Reply(text, rows))


@router.message(Command("stats"))
async def cmd_stats(message: Message, user, container: Container) -> None:
    await send_nav(message, user, container, "st")


@router.callback_query(F.data.regexp(r"^nav:(st|ch|dl|ai)(:[a-z_]+)?$"))
async def cb_nav(cb: CallbackQuery, user, container: Container) -> None:
    await cb.answer()
    parts = cb.data.split(":")
    await send_nav(cb.message, user, container, parts[1], parts[2] if len(parts) > 2 else None, cb=cb)


async def send_stats(message: Message, user, container: Container, search, edit_cb: CallbackQuery | None = None):
    from app.analytics import Analytics

    text, rows = await Analytics(container).stats(user, search)
    if edit_cb is not None:
        await edit_or_send(edit_cb, Reply(text, rows))
    else:
        await send(message, Reply(text, rows))


@router.callback_query(F.data.regexp(r"^st:s:\d+$"))
async def cb_stats(cb: CallbackQuery, user, container: Container) -> None:
    await cb.answer()
    try:
        search = await container.searches.get_owned(user, int(cb.data.split(":")[2]))
    except AccessDenied as exc:
        await cb.message.answer(t(f"deny.{exc.code}", user.language))
        return
    body = (getattr(cb.message, "text", None) or getattr(cb.message, "caption", None) or "")
    from_notification = body.startswith(("🆕", "📉", "🔥")) or cb.message.photo
    await send_stats(cb.message, user, container, search, edit_cb=None if from_notification else cb)


@router.callback_query(F.data.regexp(r"^dlc:[a-z_]+$"))
async def cb_deals_category(cb: CallbackQuery, user, container: Container) -> None:
    await cb.answer()
    category = cb.data.split(":")[1]
    searches = [s for s in await container.searches.list(user)
                if s.is_active and (s.criteria.category or "other") == category]
    await send_deals(cb.message, user, container, searches)


@router.message(Command("pack"))
async def cmd_pack(message: Message, user, container: Container) -> None:
    from app.domain.reseller_pack import DEAL_PERCENT, PACK

    lang = user.language
    items = "\n".join(f"• {esc(i.name)}" for i in PACK)
    ai = t("pack.ai_on", lang) if container.monitoring.screener is not None else ""
    await message.answer(t("pack.intro", lang, count=len(PACK), items=items, pct=DEAL_PERCENT, ai=ai),
                         reply_markup=markup([[Button(t("pack.create", lang), callback="pack:go")]]))


@router.callback_query(F.data == "pack:go")
async def cb_pack(cb: CallbackQuery, user, container: Container) -> None:
    await cb.answer()
    lang = user.language
    created, present, error = await container.searches.create_pack(user)
    await cb.message.edit_reply_markup(reply_markup=None)
    await cb.message.answer(t(
        "pack.done", lang, created=esc(", ".join(created)) or t("pack.none", lang),
        present=t("pack.present", lang, names=esc(", ".join(present))) if present else "",
        stopped=t("pack.stopped", lang, reason=t(f"deny.{error.code}", lang, **error.params)) if error else ""))


@router.message(Command("chart"))
async def cmd_chart(message: Message, user, container: Container) -> None:
    searches = await container.searches.list(user)
    if not searches:
        await message.answer(t("searches.empty", user.language))
        return
    if len(searches) == 1:
        await send_chart_options(message, user, container, searches[0])
        return
    await send_nav(message, user, container, "ch")


@router.callback_query(F.data.regexp(r"^chs:\d+$"))
async def cb_chart_search(cb: CallbackQuery, user, container: Container) -> None:
    await cb.answer()
    try:
        search = await container.searches.get_owned(user, int(cb.data.split(":")[1]))
    except AccessDenied as exc:
        await cb.message.answer(t(f"deny.{exc.code}", user.language))
        return
    await send_chart_options(cb.message, user, container, search)


@router.callback_query(F.data.regexp(r"^ch:\d+:[a-z0-9]+$"))
async def cb_chart(cb: CallbackQuery, user, container: Container) -> None:
    await cb.answer()
    _, sid, variant = cb.data.split(":")
    try:
        search = await container.searches.get_owned(user, int(sid))
    except AccessDenied as exc:
        await cb.message.answer(t(f"deny.{exc.code}", user.language))
        return
    await send_chart(cb.message, user, container, search, variant)


# ------------------------------------------------------------------- admin
@router.message(Command(*ADMIN_COMMANDS))
async def cmd_admin(message: Message, command: CommandObject, ctl: Controller, bot: Bot) -> None:
    async def refund_call(user_telegram_id: int, charge_id: str) -> None:
        await bot.refund_star_payment(user_id=user_telegram_id, telegram_payment_charge_id=charge_id)

    args = (command.args or "").split()
    reply = await ctl.admin(message.from_user.id, command.command, args, refund_call=refund_call)
    await send(message, reply)


# ------------------------------------------------------------------- other
@router.message(F.text & ~F.text.startswith("/"))
async def fallback_text(message: Message, ctl: Controller, user, container: Container) -> None:
    """Free text: "wykres iphone 15 pro", "покажи айфон на котором заработаем 15%"..."""
    from app.intents import detect

    searches = await container.searches.list(user)
    intent, candidates, pct = detect(message.text, searches)
    if intent is None:
        await send(message, Reply(t("menu.title", user.language), ctl.help(user.language).buttons))
        return
    if intent == "deals" and not candidates:  # "на чём заработаем?" without a model -> all searches
        candidates = [s for s in searches if s.is_active]
    if not candidates:
        await message.answer(t("intent.no_search", user.language))
        return
    if intent == "stats":
        for search in candidates[:3]:
            await send_stats(message, user, container, search)
        if len(candidates) > 3:
            rows = [[Button(s.name, callback=f"st:s:{s.id}")] for s in candidates[3:]]
            await send(message, Reply(t("stats.more", user.language), rows))
    elif intent == "deals":
        await send_deals(message, user, container, candidates, pct)
    elif len(candidates) == 1:
        await send_chart_options(message, user, container, candidates[0])
    else:
        rows = [[Button(s.name, callback=f"chs:{s.id}")] for s in candidates]
        await message.answer(t("chart.choose", user.language), reply_markup=markup(rows))


@router.errors()
async def on_error(event: ErrorEvent) -> bool:
    log.exception("handler error: %s", event.exception, exc_info=event.exception)
    update = event.update
    target = update.message or (update.callback_query.message if update.callback_query else None)
    if target is not None:
        try:
            await target.answer(t("error.generic"))
        except Exception:  # pragma: no cover
            pass
    return True
