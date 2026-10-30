import random
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config import PaymentsMode, load_settings
from app.domain.models import Plan, Search, SearchCriteria, Subscription, SubscriptionStatus, User
from app.domain.payments import InvoicePayload, PaymentRejected, validate_stars_payment, verify_hmac_signature
from app.domain.plans import DEFAULT_PLANS, Access, AccessDenied, compute_new_period, monitored_search_ids
from app.domain.timing import TokenBucket, backoff_seconds, in_quiet_hours, local_day_start_utc, quiet_hours_end
from app.domain.validation import (
    InvalidInput, parse_name, parse_phrases, parse_price, parse_price_range, parse_quiet_hours, parse_radius,
    parse_place,
)

UTC = timezone.utc
NOW = datetime(2026, 10, 24, 10, 0, tzinfo=UTC)


def plan(code="basic", price=150, days=30, searches=5, **kw):
    return Plan(code=code, name=code, price_stars=price, duration_days=days, max_active_searches=searches,
                allowed_sources=["mock"], min_interval_seconds=600, max_notifications_per_day=100, id=1, **kw)


def sub(starts, expires, status=SubscriptionStatus.ACTIVE, plan_id=1):
    return Subscription(id=1, user_id=1, plan_id=plan_id, status=status, origin="stars", starts_at=starts,
                        expires_at=expires)


class ValidationTests(unittest.TestCase):
    def test_name(self):
        self.assertEqual(parse_name("  Rower   do  1500 "), "Rower do 1500")
        with self.assertRaises(InvalidInput):
            parse_name("   ")
        with self.assertRaises(InvalidInput):
            parse_name("x" * 61)

    def test_phrases(self):
        self.assertEqual(parse_phrases("iPhone 13, iphone 13; Pro", allow_empty=False), ["iphone 13", "pro"])
        self.assertEqual(parse_phrases("-", allow_empty=True), [])
        with self.assertRaises(InvalidInput):
            parse_phrases("-", allow_empty=False)
        with self.assertRaises(InvalidInput):
            parse_phrases(",".join(str(i) for i in range(11)), allow_empty=False)

    def test_price(self):
        self.assertEqual(parse_price("1 200,50 zł"), Decimal("1200.50"))
        self.assertEqual(parse_price("99"), Decimal("99"))
        self.assertIsNone(parse_price("-"))
        for bad in ["abc", "1.234", "-5", "12,345"]:
            with self.assertRaises(InvalidInput, msg=bad):
                parse_price(bad)

    def test_price_range(self):
        self.assertEqual(parse_price_range("100-500"), (Decimal(100), Decimal(500)))
        self.assertEqual(parse_price_range("-500"), (None, Decimal(500)))
        self.assertEqual(parse_price_range("100-"), (Decimal(100), None))
        self.assertEqual(parse_price_range("-"), (None, None))
        with self.assertRaises(InvalidInput):
            parse_price_range("500-100")
        with self.assertRaises(InvalidInput):
            parse_price_range("500")

    def test_place_radius_quiet(self):
        self.assertEqual(parse_place("Bielsko-Biała"), "Bielsko-Biała")
        self.assertIsNone(parse_place("-"))
        with self.assertRaises(InvalidInput):
            parse_place("<script>")
        self.assertEqual(parse_radius("25 km"), 25)
        with self.assertRaises(InvalidInput):
            parse_radius("1000")
        self.assertEqual(parse_quiet_hours("22-7"), (22, 7))
        self.assertEqual(parse_quiet_hours("-"), (None, None))
        with self.assertRaises(InvalidInput):
            parse_quiet_hours("25-7")


class TimingTests(unittest.TestCase):
    def test_backoff_grows_and_caps(self):
        values = [backoff_seconds(a, base=30, cap=600, jitter=0) for a in range(1, 8)]
        self.assertEqual(values[:5], [30, 60, 120, 240, 480])
        self.assertEqual(values[-1], 600)
        jittered = backoff_seconds(3, base=30, cap=600, jitter=0.2, rng=random.Random(1))
        self.assertTrue(96 <= jittered <= 144)

    def test_quiet_hours_cross_midnight(self):
        tz = "Europe/Warsaw"  # UTC+2 on 2026-10-24
        night = datetime(2026, 10, 24, 21, 30, tzinfo=UTC)  # 23:30 local
        day = datetime(2026, 10, 24, 10, 0, tzinfo=UTC)  # 12:00 local
        self.assertTrue(in_quiet_hours(night, 22, 7, tz))
        self.assertFalse(in_quiet_hours(day, 22, 7, tz))
        self.assertFalse(in_quiet_hours(night, None, None, tz))
        end = quiet_hours_end(night, 22, 7, tz)
        self.assertEqual(end, datetime(2026, 10, 25, 6, 0, tzinfo=UTC))  # 07:00 CET after DST change
        self.assertEqual(quiet_hours_end(day, 22, 7, tz), day)
        self.assertTrue(in_quiet_hours(day, 9, 17, tz))

    def test_local_day_start(self):
        self.assertEqual(local_day_start_utc(NOW, "Europe/Warsaw"), datetime(2026, 10, 23, 22, 0, tzinfo=UTC))

    def test_token_bucket(self):
        now = [0.0]
        bucket = TokenBucket(rate_per_minute=3, clock=lambda: now[0])
        self.assertEqual([bucket.allow(1) for _ in range(4)], [True, True, True, False])
        self.assertTrue(bucket.allow(2))  # other users are independent
        now[0] += 20  # one token refilled
        self.assertTrue(bucket.allow(1))
        self.assertFalse(bucket.allow(1))


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.user = User(id=1, telegram_id=10)

    def test_defaults_are_sane(self):
        codes = [p.code for p in DEFAULT_PLANS]
        self.assertEqual(codes, ["trial", "basic", "pro", "premium"])
        self.assertTrue(all(p.min_interval_seconds >= 60 for p in DEFAULT_PLANS))
        self.assertEqual(sum(p.is_trial for p in DEFAULT_PLANS), 1)

    def test_access_active_and_expired(self):
        p = plan()
        a = Access(self.user, sub(NOW - timedelta(days=1), NOW + timedelta(days=1)), p, NOW)
        self.assertTrue(a.active)
        self.assertEqual(a.clamp_interval(60), 600)
        self.assertEqual(a.clamp_interval(3600), 3600)
        expired = Access(self.user, sub(NOW - timedelta(days=2), NOW), p, NOW)  # expires_at is exclusive
        self.assertFalse(expired.active)
        with self.assertRaises(AccessDenied) as ctx:
            expired.require_active()
        self.assertEqual(ctx.exception.code, "no_subscription")

    def test_suspended(self):
        self.user.is_suspended = True
        a = Access(self.user, sub(NOW - timedelta(days=1), NOW + timedelta(days=1)), plan(), NOW)
        with self.assertRaises(AccessDenied) as ctx:
            a.require_active()
        self.assertEqual(ctx.exception.code, "suspended")

    def test_limits(self):
        a = Access(self.user, sub(NOW - timedelta(days=1), NOW + timedelta(days=1)), plan(searches=2), NOW)
        a.check_can_create_search(1)
        with self.assertRaises(AccessDenied):
            a.check_can_create_search(2)
        with self.assertRaises(AccessDenied):
            a.check_sources(["olx"])
        with self.assertRaises(AccessDenied):
            a.check_advanced(fuzzy=True)

    def test_monitored_ids_after_downgrade(self):
        searches = [Search(id=i, user_id=1, name=str(i), sources=["mock"], criteria=SearchCriteria(keywords=["a"]),
                           interval_seconds=600, created_at=NOW + timedelta(minutes=i)) for i in range(1, 6)]
        searches[1].is_active = False
        self.assertEqual(monitored_search_ids(searches, plan(searches=2)), {1, 3})
        self.assertEqual(monitored_search_ids(searches, None), set())

    def test_period_new_renew_upgrade(self):
        basic, pro = plan("basic", 150, 30), plan("pro", 300, 30)
        start, end, bonus = compute_new_period(NOW, basic, None, None)
        self.assertEqual((start, end, bonus), (NOW, NOW + timedelta(days=30), 0))
        current = sub(NOW - timedelta(days=20), NOW + timedelta(days=10))
        _, end, _ = compute_new_period(NOW, basic, current, basic)
        self.assertEqual(end, NOW + timedelta(days=40))  # renewal appended
        _, end, bonus = compute_new_period(NOW, pro, current, basic)
        self.assertEqual(bonus, 5 * 86400)  # 10 days of basic = 5 days of pro
        self.assertEqual(end, NOW + timedelta(days=35))
        trial = plan("trial", 0, 3)
        _, end, bonus = compute_new_period(NOW, basic, sub(NOW, NOW + timedelta(days=3)), trial)
        self.assertEqual((end, bonus), (NOW + timedelta(days=30), 0))  # no credit for a free trial


class PaymentDomainTests(unittest.TestCase):
    def test_payload_roundtrip(self):
        p = InvoicePayload.new("pro", 123456789, 350)
        decoded = InvoicePayload.decode(p.encode())
        self.assertEqual(decoded, p)
        self.assertLessEqual(len(p.encode().encode()), 128)
        for bad in ["", "x:y", "sub:pro:abc:350:n", "evil:pro:1:350:n"]:
            with self.assertRaises(PaymentRejected):
                InvoicePayload.decode(bad)

    def _validate(self, **over):
        payload = InvoicePayload("pro", 42, 350, "n").encode()
        args = dict(payload_raw=payload, currency="XTR", total_amount=350, from_user_id=42, plan_price_stars=350,
                    plan_active=True, user_suspended=False)
        args.update(over)
        return validate_stars_payment(**args)

    def test_validation(self):
        self.assertEqual(self._validate().plan_code, "pro")
        cases = {
            "currency": dict(currency="USD"), "user_mismatch": dict(from_user_id=43),
            "amount": dict(total_amount=1), "price_changed": dict(plan_price_stars=400),
            "plan_unavailable": dict(plan_active=False), "suspended": dict(user_suspended=True),
        }
        for code, over in cases.items():
            with self.assertRaises(PaymentRejected) as ctx:
                self._validate(**over)
            self.assertEqual(ctx.exception.code, code)

    def test_hmac(self):
        import hashlib
        import hmac
        body = b'{"a":1}'
        sig = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_hmac_signature("s3cret", body, sig))
        self.assertFalse(verify_hmac_signature("s3cret", body + b" ", sig))
        self.assertFalse(verify_hmac_signature("", body, sig))


class ConfigTests(unittest.TestCase):
    def test_env_parsing_and_secrets(self):
        s = load_settings(env={"BOT_TOKEN": "123:abc", "ADMIN_IDS": "1, 2", "ENABLED_SOURCES": "mock,OLX",
                               "PAYMENTS_MODE": "live", "CRYPTO_ENABLED": "false"}, env_file=None)
        self.assertEqual(s.admin_ids, [1, 2])
        self.assertEqual(s.enabled_sources, ["mock", "olx"])
        self.assertTrue(s.is_admin(2))
        self.assertFalse(s.live_payments_allowed)  # live needs explicit confirmation
        self.assertFalse(s.payments_available)
        self.assertEqual(s.safe_dict()["bot_token"], "***")
        confirmed = load_settings(env={"PAYMENTS_MODE": "live", "PAYMENTS_LIVE_CONFIRMED": "yes"}, env_file=None)
        self.assertTrue(confirmed.live_payments_allowed)
        self.assertIs(load_settings(env={}, env_file=None).payments_mode, PaymentsMode.DISABLED)

    def test_env_file(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / ".env"
            path.write_text('# c\nBOT_TOKEN="1:x"\nADMIN_IDS=5\n\nNOISE\n', encoding="utf-8")
            s = load_settings(env={"ADMIN_IDS": "6"}, env_file=path)
            self.assertEqual(s.bot_token, "1:x")
            self.assertEqual(s.admin_ids, [6])  # real env wins over file


if __name__ == "__main__":
    unittest.main()
