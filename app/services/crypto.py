"""Crypto payment provider abstraction — DISABLED.

Telegram's rules for bots require digital goods and services to be sold for Telegram Stars
only; third-party providers and crypto are not allowed for digital goods, including via an
own website (https://core.telegram.org/bots/payments-stars). This module therefore only
defines the interface, webhook validation and a mock used in tests. ``CRYPTO_ENABLED``
must stay false unless a lawyer confirms an allowed scenario (see docs/PAYMENTS.md).

Safety rules for any future provider: verify webhook signatures, amount, currency, network
and status; process each event once; handle under/over-payment and refunds; never store
private keys or seed phrases.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from app.domain.payments import verify_hmac_signature


class CryptoDisabled(Exception):
    pass


@dataclass
class CryptoInvoice:
    external_id: str
    checkout_url: str
    amount: Decimal
    currency: str
    network: str


@dataclass
class CryptoWebhookEvent:
    event_id: str
    invoice_id: str
    status: str  # paid | underpaid | overpaid | expired | refunded
    amount_paid: Decimal
    currency: str
    network: str


class CryptoProvider(Protocol):
    async def create_invoice(self, order_id: str, amount: Decimal, currency: str, network: str) -> CryptoInvoice: ...

    def parse_webhook(self, body: bytes, signature: str) -> CryptoWebhookEvent: ...


class WebhookRejected(Exception):
    pass


def classify_payment(expected: Decimal, paid: Decimal, expected_currency: str, currency: str,
                     expected_network: str, network: str, tolerance: Decimal = Decimal("0")) -> str:
    """Return ok | underpaid | overpaid | wrong_asset."""
    if currency.upper() != expected_currency.upper() or network.lower() != expected_network.lower():
        return "wrong_asset"
    if paid + tolerance < expected:
        return "underpaid"
    if paid - tolerance > expected:
        return "overpaid"
    return "ok"


class MockCryptoProvider:
    """Deterministic provider for tests. Never contacts any network."""

    def __init__(self, secret: str, enabled: bool) -> None:
        self._secret = secret
        self._enabled = enabled

    async def create_invoice(self, order_id: str, amount: Decimal, currency: str, network: str) -> CryptoInvoice:
        if not self._enabled:
            raise CryptoDisabled("crypto payments are disabled")
        return CryptoInvoice(f"mock-{order_id}", f"https://example.invalid/checkout/{order_id}", amount, currency, network)

    def parse_webhook(self, body: bytes, signature: str) -> CryptoWebhookEvent:
        if not self._enabled:
            raise CryptoDisabled("crypto payments are disabled")
        if not verify_hmac_signature(self._secret, body, signature):
            raise WebhookRejected("bad signature")
        try:
            data = json.loads(body)
            return CryptoWebhookEvent(str(data["event_id"]), str(data["invoice_id"]), str(data["status"]),
                                      Decimal(str(data["amount_paid"])), str(data["currency"]), str(data["network"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise WebhookRejected("bad payload") from exc
