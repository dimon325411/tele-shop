import asyncio
import json
import unittest
from datetime import timedelta
from decimal import Decimal

from app.config import PaymentsMode, Settings
from app.domain.models import SubscriptionStatus
from app.domain.payments import InvoicePayload, PaymentRejected
from app.services.admin import AdminError, NotAdmin
from app.services.crypto import CryptoDisabled, MockCryptoProvider, WebhookRejected, classify_payment
from app.services.payments import SuccessfulPaymentData
from tests.unit.helpers import App


class PaymentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await App().setup()
        self.user = await self.app.user(111)
        await self.app.users.accept_terms(self.user)
        self.user = await self.app.repo.get_user(self.user.id)

    async def _invoice(self, plan="basic"):
        return await self.app.payments.create_invoice(self.user, plan)

    def _paid(self, inv, charge="ch_1", **over):
        data = dict(from_user_id=111, currency="XTR", total_amount=inv.amount, invoice_payload=inv.payload,
                    telegram_payment_charge_id=charge)
        data.update(over)
        return SuccessfulPaymentData(**data)

    async def test_invoice_rules(self):
        inv = await self._invoice("pro")
        self.assertEqual((inv.amount, inv.currency, inv.plan_code), (350, "XTR", "pro"))
        with self.assertRaises(PaymentRejected) as ctx:
            await self._invoice("trial")
        self.assertEqual(ctx.exception.code, "plan_unavailable")
        fresh = await self.app.user(222, "Bob")  # did not accept terms
        with self.assertRaises(PaymentRejected) as ctx:
            await self.app.payments.create_invoice(fresh, "basic")
        self.assertEqual(ctx.exception.code, "terms_not_accepted")

    async def test_payments_disabled_by_default(self):
        app = await App(settings=Settings(payments_mode=PaymentsMode.DISABLED)).setup()
        user = await app.user(1)
        await app.users.accept_terms(user)
        with self.assertRaises(PaymentRejected) as ctx:
            await app.payments.create_invoice(await app.repo.get_user(user.id), "basic")
        self.assertEqual(ctx.exception.code, "payments_disabled")
        live_unconfirmed = Settings(payments_mode=PaymentsMode.LIVE)
        self.assertFalse(live_unconfirmed.payments_available)

    async def test_pre_checkout(self):
        inv = await self._invoice()
        ok = await self.app.payments.pre_checkout(from_user_id=111, currency="XTR", total_amount=inv.amount,
                                                  invoice_payload=inv.payload)
        self.assertIsNone(ok)
        bad = await self.app.payments.pre_checkout(from_user_id=111, currency="XTR", total_amount=1,
                                                   invoice_payload=inv.payload)
        self.assertEqual(bad, "amount")
        other = await self.app.payments.pre_checkout(from_user_id=222, currency="XTR", total_amount=inv.amount,
                                                     invoice_payload=inv.payload)
        self.assertEqual(other, "unknown_user")
        await self.app.repo.update_plan("basic", price_stars=200)
        changed = await self.app.payments.pre_checkout(from_user_id=111, currency="XTR", total_amount=inv.amount,
                                                       invoice_payload=inv.payload)
        self.assertEqual(changed, "price_changed")
        await self.app.repo.update_plan("basic", is_active=False)
        self.assertEqual(await self.app.payments.pre_checkout(
            from_user_id=111, currency="XTR", total_amount=200,
            invoice_payload=InvoicePayload("basic", 111, 200, "n").encode()), "plan_unavailable")

    async def test_pre_checkout_does_not_grant_access(self):
        inv = await self._invoice("premium")
        await self.app.payments.pre_checkout(from_user_id=111, currency="XTR", total_amount=inv.amount,
                                             invoice_payload=inv.payload)
        sub = await self.app.repo.get_active_subscription(self.user.id)
        self.assertEqual(sub.origin, "trial")

    async def test_success_grants_and_is_idempotent(self):
        inv = await self._invoice()
        result = await self.app.payments.handle_successful_payment(self._paid(inv))
        self.assertEqual(result.status, "granted")
        sub = await self.app.repo.get_active_subscription(self.user.id)
        plan = await self.app.repo.get_plan(sub.plan_id)
        self.assertEqual((plan.code, sub.origin), ("basic", "stars"))
        self.assertEqual(sub.expires_at, self.app.clock() + timedelta(days=30))
        again = await self.app.payments.handle_successful_payment(self._paid(inv))
        self.assertEqual(again.status, "duplicate")
        self.assertEqual(len(self.app.repo.payments), 1)
        self.assertEqual(len(self.app.repo.payment_events), 1)
        payment = next(iter(self.app.repo.payments.values()))
        self.assertEqual((payment.status, payment.amount, payment.telegram_payment_charge_id),
                         ("paid", Decimal(150), "ch_1"))

    async def test_concurrent_duplicate_webhooks(self):
        inv = await self._invoice()
        results = await asyncio.gather(*(self.app.payments.handle_successful_payment(self._paid(inv))
                                         for _ in range(5)))
        self.assertEqual(sorted(r.status for r in results), ["duplicate"] * 4 + ["granted"])
        self.assertEqual(len(self.app.repo.payments), 1)
        self.assertEqual(sum(1 for s in self.app.repo.subscriptions.values()
                             if s.status is SubscriptionStatus.ACTIVE), 1)

    async def test_forged_success_is_rejected(self):
        inv = await self._invoice()
        bad = await self.app.payments.handle_successful_payment(self._paid(inv, charge="x", total_amount=1))
        self.assertEqual((bad.status, bad.error), ("rejected", "amount"))
        other = await self.app.payments.handle_successful_payment(self._paid(inv, charge="y", currency="USD"))
        self.assertEqual(other.status, "rejected")
        sub = await self.app.repo.get_active_subscription(self.user.id)
        self.assertEqual(sub.origin, "trial")
        errors = await self.app.admin.payment_errors(999)
        self.assertEqual(len(errors), 2)

    async def test_renewal_extends(self):
        inv = await self._invoice()
        await self.app.payments.handle_successful_payment(self._paid(inv, charge="a"))
        self.app.clock.advance(days=10)
        inv2 = await self._invoice()
        await self.app.payments.handle_successful_payment(self._paid(inv2, charge="b"))
        sub = await self.app.repo.get_active_subscription(self.user.id)
        self.assertEqual(sub.expires_at, self.app.clock() + timedelta(days=50))

    async def test_refund_revokes(self):
        inv = await self._invoice()
        await self.app.payments.handle_successful_payment(self._paid(inv))
        calls = []

        async def refund_call(uid, charge):
            calls.append((uid, charge))

        self.assertEqual(await self.app.payments.refund("ch_1", 999, "request", refund_call), "refunded")
        self.assertEqual(calls, [(111, "ch_1")])
        self.assertIsNone(await self.app.repo.get_active_subscription(self.user.id))
        self.assertEqual(await self.app.payments.refund("ch_1", 999, None, refund_call), "already_refunded")
        self.assertEqual(await self.app.payments.refund("nope", 999, None, refund_call), "not_found")

        async def failing(uid, charge):
            raise RuntimeError("CHARGE_ALREADY_REFUNDED")

        inv = await self._invoice()
        await self.app.payments.handle_successful_payment(self._paid(inv, charge="ch_2"))
        self.assertEqual(await self.app.payments.refund("ch_2", 999, None, failing), "failed")
        self.assertIsNotNone(await self.app.repo.get_active_subscription(self.user.id))


class AdminTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = await App().setup()
        self.user = await self.app.user(111)

    async def test_only_admin_ids(self):
        with self.assertRaises(NotAdmin):
            await self.app.admin.stats(111)
        stats = await self.app.admin.stats(999)
        self.assertEqual(stats["users"], 1)
        self.assertEqual(stats["active_subscriptions"], 1)

    async def test_plan_set(self):
        await self.app.admin.set_plan_field(999, "basic", "max_active_searches", "7")
        self.assertEqual((await self.app.repo.get_plan_by_code("basic")).max_active_searches, 7)
        await self.app.admin.set_plan_field(999, "basic", "fuzzy_matching", "tak")
        self.assertTrue((await self.app.repo.get_plan_by_code("basic")).fuzzy_matching)
        for field, value, code in [("id", "1", "bad_field"), ("price_stars", "-1", "bad_value"),
                                   ("min_interval_seconds", "5", "bad_value")]:
            with self.assertRaises(AdminError) as ctx:
                await self.app.admin.set_plan_field(999, "basic", field, value)
            self.assertEqual(ctx.exception.code, code)
        with self.assertRaises(AdminError):
            await self.app.admin.set_plan_field(999, "gold", "name", "x")
        log = await self.app.admin.audit_log(999)
        self.assertEqual(len(log), 2)

    async def test_grant(self):
        await self.app.admin.grant(999, 111, "premium", 7)
        sub = await self.app.repo.get_active_subscription(self.user.id)
        self.assertEqual(sub.origin, "admin")
        self.assertEqual(sub.expires_at, self.app.clock() + timedelta(days=7))
        with self.assertRaises(AdminError):
            await self.app.admin.grant(999, 5555, "premium", 7)


class CryptoTests(unittest.TestCase):
    def test_disabled_by_default(self):
        provider = MockCryptoProvider("s", enabled=False)
        with self.assertRaises(CryptoDisabled):
            asyncio.run(provider.create_invoice("1", Decimal("10"), "USDT", "tron"))
        with self.assertRaises(CryptoDisabled):
            provider.parse_webhook(b"{}", "x")

    def test_webhook_signature(self):
        import hashlib
        import hmac
        provider = MockCryptoProvider("secret", enabled=True)
        body = json.dumps({"event_id": "e1", "invoice_id": "i1", "status": "paid", "amount_paid": "10.0",
                           "currency": "USDT", "network": "tron"}).encode()
        sig = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        self.assertEqual(provider.parse_webhook(body, sig).status, "paid")
        with self.assertRaises(WebhookRejected):
            provider.parse_webhook(body, "0" * 64)

    def test_classify(self):
        self.assertEqual(classify_payment(Decimal(10), Decimal(10), "USDT", "usdt", "tron", "TRON"), "ok")
        self.assertEqual(classify_payment(Decimal(10), Decimal(9), "USDT", "USDT", "tron", "tron"), "underpaid")
        self.assertEqual(classify_payment(Decimal(10), Decimal(11), "USDT", "USDT", "tron", "tron"), "overpaid")
        self.assertEqual(classify_payment(Decimal(10), Decimal(10), "USDT", "USDC", "tron", "tron"), "wrong_asset")


if __name__ == "__main__":
    unittest.main()
