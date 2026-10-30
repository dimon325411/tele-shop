"""Telegram handlers (aiogram 3). Thin layer: parse update -> Controller/SearchWizard -> send Reply."""

from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, ErrorEvent, LabeledPrice, LinkPreviewOptions, Message, PreCheckoutQuery

from app.bot.keyboards import markup
from app.container import Container
from app.controller import Controller, Reply
from app.domain.plans import AccessDenied
from app.i18n import esc, format_interval, t
from app.presentation import fmt_dt
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
    await message.answer(reply.text, reply_markup=markup(reply.buttons) if reply.buttons else None,
                         link_preview_options=NO_PREVIEW)


async def edit_or_send(cb: CallbackQuery, reply: Reply) -> None:
    kb = markup(reply.buttons) if reply.buttons else None
    try:
        await cb.message.edit_text(reply.text, reply_markup=kb, link_preview_options=NO_PREVIEW)
    except TelegramBadRequest:  # message too old / identical / not editable
        await cb.message.answer(reply.text, reply_markup=kb, link_preview_options=NO_PREVIEW)


# ---------------------------------------------------------------- general
@router.message(CommandStart())
async def cmd_start(message: Message, ctl: Controller, state: FSMContext) -> None:
    await state.clear()
    u = message.from_user
    await send(message, await ctl.start(u.id, u.username, u.first_name))


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
    if cb.message.text and cb.message.text.startswith(("🆕", "📉")):
        await cb.message.answer(reply.text, reply_markup=markup(reply.buttons) if reply.buttons else None)
    else:
        await edit_or_send(cb, reply)  # notifications are never overwritten


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
    elif action == "delete":
        await edit_or_send(cb, ctl.confirm_delete_account(user))
    elif action == "delete_confirm":
        await state.clear()
        await edit_or_send(cb, await ctl.delete_account(user))


@router.message(SettingsInput.quiet_hours, F.text & ~F.text.startswith("/"))
async def quiet_hours_input(message: Message, ctl: Controller, user, state: FSMContext) -> None:
    reply, ok = await ctl.set_quiet_hours(user, message.text)
    if ok:
        await state.clear()
    await send(message, reply)


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
async def fallback_text(message: Message, ctl: Controller, user) -> None:
    await send(message, Reply(t("menu.title", user.language), ctl.help(user.language).buttons))


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
