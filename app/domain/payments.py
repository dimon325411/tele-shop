"""Telegram Stars invoice payloads and payment validation (pure logic)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

STARS_CURRENCY = "XTR"
PAYLOAD_PREFIX = "sub"


class PaymentRejected(Exception):
    """Validation failure. ``code`` maps to i18n key ``pay.error.<code>``."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class InvoicePayload:
    plan_code: str
    user_telegram_id: int
    price_stars: int
    nonce: str

    def encode(self) -> str:
        # Telegram allows up to 128 bytes of payload.
        value = f"{PAYLOAD_PREFIX}:{self.plan_code}:{self.user_telegram_id}:{self.price_stars}:{self.nonce}"
        if len(value.encode()) > 128:
            raise ValueError("payload too long")
        return value

    @classmethod
    def new(cls, plan_code: str, user_telegram_id: int, price_stars: int) -> "InvoicePayload":
        return cls(plan_code, user_telegram_id, price_stars, secrets.token_hex(6))

    @classmethod
    def decode(cls, raw: str) -> "InvoicePayload":
        parts = (raw or "").split(":")
        if len(parts) != 5 or parts[0] != PAYLOAD_PREFIX:
            raise PaymentRejected("bad_payload")
        try:
            return cls(parts[1], int(parts[2]), int(parts[3]), parts[4])
        except ValueError as exc:
            raise PaymentRejected("bad_payload") from exc


def validate_stars_payment(
    *,
    payload_raw: str,
    currency: str,
    total_amount: int,
    from_user_id: int,
    plan_price_stars: int | None,
    plan_active: bool,
    user_suspended: bool,
) -> InvoicePayload:
    """Checks shared by pre_checkout_query and successful_payment."""
    payload = InvoicePayload.decode(payload_raw)
    if currency != STARS_CURRENCY:
        raise PaymentRejected("currency")
    if payload.user_telegram_id != from_user_id:
        raise PaymentRejected("user_mismatch")
    if user_suspended:
        raise PaymentRejected("suspended")
    if plan_price_stars is None or not plan_active:
        raise PaymentRejected("plan_unavailable")
    if total_amount != payload.price_stars:
        raise PaymentRejected("amount")
    if total_amount != plan_price_stars:
        # Price changed after the invoice was created: refuse, user gets a fresh invoice.
        raise PaymentRejected("price_changed")
    return payload


def verify_hmac_signature(secret: str, body: bytes, signature_hex: str) -> bool:
    """Constant-time HMAC-SHA256 check for provider webhooks (crypto provider, etc.)."""
    if not secret or not signature_hex:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_hex.strip().lower())
