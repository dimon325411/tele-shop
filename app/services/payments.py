"""Telegram Stars payments: invoices, pre-checkout validation, idempotent fulfilment, refunds.

Access is granted only after ``successful_payment`` has been validated and stored;
pressing a button or receiving an unverified message never grants anything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Awaitable, Callable

from app.config import Settings
from app.domain.models import Payment, Subscription, SubscriptionStatus, User
from app.domain.payments import STARS_CURRENCY, InvoicePayload, PaymentRejected, validate_stars_payment
from app.domain.timing import Clock, utcnow
from app.services.subscriptions import SubscriptionService
from app.storage.errors import IntegrityError
from app.storage.repo import Repository

log = logging.getLogger(__name__)
PROVIDER = "telegram_stars"


@dataclass
class InvoiceRequest:
    plan_code: str
    plan_name: str
    duration_days: int
    payload: str
    amount: int
    currency: str = STARS_CURRENCY


@dataclass
class SuccessfulPaymentData:
    from_user_id: int
    currency: str
    total_amount: int
    invoice_payload: str
    telegram_payment_charge_id: str
    provider_payment_charge_id: str | None = None


@dataclass
class FulfilmentResult:
    status: str  # granted | duplicate | rejected
    subscription: Subscription | None = None
    error: str | None = None


class PaymentService:
    def __init__(self, repo: Repository, subscriptions: SubscriptionService, settings: Settings,
                 clock: Clock = utcnow) -> None:
        self.repo = repo
        self.subs = subscriptions
        self.settings = settings
        self.clock = clock

    async def create_invoice(self, user: User, plan_code: str) -> InvoiceRequest:
        if not self.settings.payments_available:
            raise PaymentRejected("payments_disabled")
        if user.is_suspended:
            raise PaymentRejected("suspended")
        if user.terms_accepted_at is None:
            raise PaymentRejected("terms_not_accepted")
        plan = await self.repo.get_plan_by_code(plan_code)
        if plan is None or not plan.is_active or plan.is_trial or plan.price_stars <= 0:
            raise PaymentRejected("plan_unavailable")
        payload = InvoicePayload.new(plan.code, user.telegram_id, plan.price_stars)
        return InvoiceRequest(plan.code, plan.name, plan.duration_days, payload.encode(), plan.price_stars)

    async def pre_checkout(self, *, from_user_id: int, currency: str, total_amount: int,
                           invoice_payload: str) -> str | None:
        """Return None when OK, else an error code (i18n ``pay.error.<code>``). Must be fast (<10 s)."""
        try:
            if not self.settings.payments_available:
                raise PaymentRejected("payments_disabled")
            payload = InvoicePayload.decode(invoice_payload)
            user = await self.repo.get_user_by_telegram_id(from_user_id)
            if user is None or user.deleted_at is not None:
                raise PaymentRejected("unknown_user")
            plan = await self.repo.get_plan_by_code(payload.plan_code)
            validate_stars_payment(
                payload_raw=invoice_payload, currency=currency, total_amount=total_amount,
                from_user_id=from_user_id, plan_price_stars=plan.price_stars if plan else None,
                plan_active=bool(plan and plan.is_active and not plan.is_trial), user_suspended=user.is_suspended,
            )
        except PaymentRejected as exc:
            return exc.code
        return None

    async def handle_successful_payment(self, data: SuccessfulPaymentData) -> FulfilmentResult:
        """Idempotent: the same telegram_payment_charge_id grants access only once."""
        now = self.clock()
        event_id, is_new = await self.repo.record_payment_event(
            PROVIDER, "successful_payment", data.telegram_payment_charge_id,
            {"from_user_id": data.from_user_id, "currency": data.currency, "total_amount": data.total_amount,
             "invoice_payload": data.invoice_payload,
             "provider_payment_charge_id": data.provider_payment_charge_id}, now)
        if not is_new:
            existing = await self.repo.get_payment_by_charge_id(data.telegram_payment_charge_id)
            if existing is not None:
                return FulfilmentResult("duplicate")
            # event stored but never processed (crash) -> process now

        try:
            async with self.repo.transaction():
                if await self.repo.get_payment_by_charge_id(data.telegram_payment_charge_id):
                    return FulfilmentResult("duplicate")
                user = await self.repo.get_user_by_telegram_id(data.from_user_id)
                if user is None:
                    raise PaymentRejected("unknown_user")
                payload = InvoicePayload.decode(data.invoice_payload)
                plan = await self.repo.get_plan_by_code(payload.plan_code)
                # Money was already taken by Telegram: validate amount/currency/user, but do not refuse
                # because of a later price change or plan deactivation — honour the invoice.
                validate_stars_payment(
                    payload_raw=data.invoice_payload, currency=data.currency, total_amount=data.total_amount,
                    from_user_id=data.from_user_id, plan_price_stars=payload.price_stars if plan else None,
                    plan_active=plan is not None, user_suspended=False,
                )
                payment = await self.repo.create_payment(Payment(
                    id=0, user_id=user.id, plan_id=plan.id, provider=PROVIDER, status="paid",
                    amount=Decimal(data.total_amount), currency=data.currency, invoice_payload=data.invoice_payload,
                    telegram_payment_charge_id=data.telegram_payment_charge_id,
                    provider_payment_charge_id=data.provider_payment_charge_id, created_at=now, paid_at=now))
                sub = await self.subs.activate(user.id, plan, "stars", payment.id)
                await self.repo.finish_payment_event(event_id, now, payment.id, None)
                return FulfilmentResult("granted", sub)
        except IntegrityError:
            # A parallel delivery of the same event won the race (unique charge id).
            return FulfilmentResult("duplicate")
        except PaymentRejected as exc:
            # Paid but invalid (should not happen after pre-checkout). Keep the event for the admin.
            await self.repo.finish_payment_event(event_id, now, None, f"rejected: {exc.code}")
            log.error("successful_payment rejected (%s) charge=%s", exc.code, data.telegram_payment_charge_id)
            return FulfilmentResult("rejected", error=exc.code)

    async def refund(self, charge_id: str, admin_telegram_id: int, reason: str | None,
                     refund_call: Callable[[int, str], Awaitable[None]]) -> str:
        """Refund a Stars payment via Bot API ``refundStarPayment`` and revoke its subscription."""
        payment = await self.repo.get_payment_by_charge_id(charge_id)
        if payment is None:
            return "not_found"
        if payment.status == "refunded":
            return "already_refunded"
        user = await self.repo.get_user(payment.user_id)
        now = self.clock()
        try:
            await refund_call(user.telegram_id, charge_id)
        except Exception as exc:
            await self.repo.create_refund(payment.id, payment.amount, reason, "failed", admin_telegram_id,
                                          repr(exc)[:300], now)
            await self.repo.add_audit(admin_telegram_id, "refund_failed", charge_id, {"error": repr(exc)[:300]}, now)
            return "failed"
        async with self.repo.transaction():
            await self.repo.create_refund(payment.id, payment.amount, reason, "completed", admin_telegram_id, None, now)
            await self.repo.update_payment(payment.id, status="refunded", refunded_at=now)
            sub = await self.repo.get_active_subscription(payment.user_id)
            if sub is not None and sub.payment_id == payment.id:
                await self.subs.revoke(sub.id, SubscriptionStatus.REFUNDED)
            await self.repo.add_audit(admin_telegram_id, "refund", charge_id, {"reason": reason}, now)
        return "refunded"
