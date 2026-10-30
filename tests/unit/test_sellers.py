import json
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from app.domain.models import Listing, StoredListing, User
from app.domain.sellers import is_new_account
from app.presentation import render_listing_notification
from app.services.notifications import OutgoingListing
from app.sources.olx import parse_offers
from tests.unit.helpers import App, T0, draft

UTC = timezone.utc
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "olx_offers_synthetic.json"


class NewAccountTests(unittest.TestCase):
    def test_rules(self):
        now = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
        self.assertTrue(is_new_account(datetime(2026, 10, 1, 8, tzinfo=UTC), now))      # this month
        self.assertTrue(is_new_account(datetime(2026, 9, 30, 8, tzinfo=UTC), now))      # < 30 days
        self.assertFalse(is_new_account(datetime(2026, 9, 20, 8, tzinfo=UTC), now))
        self.assertFalse(is_new_account(datetime(2013, 5, 8, tzinfo=UTC), now))
        self.assertFalse(is_new_account(None, now))
        # calendar month is taken in local time: 31.10 23:30 UTC is already 1.11 in Warsaw
        nov_1_local = datetime(2026, 10, 31, 23, 30, tzinfo=UTC)
        self.assertTrue(is_new_account(datetime(2026, 10, 31, 23, 10, tzinfo=UTC), nov_1_local))
        self.assertFalse(is_new_account(datetime(2026, 9, 1, 12, tzinfo=UTC), nov_1_local))

    def test_olx_parser_reads_seller_age(self):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        data["data"][0]["user"] = {"id": 1, "created": "2013-05-08T20:59:49+02:00", "name": "X"}
        data["data"][0]["business"] = True
        first = parse_offers(data)[0]
        self.assertEqual(first.seller_created_at.isoformat(), "2013-05-08T20:59:49+02:00")
        self.assertTrue(first.seller_is_business)
        self.assertFalse(hasattr(first, "seller_name"))  # the name is never stored


def item(seller_created, business=False, posted=T0 - timedelta(hours=2)):
    listing = StoredListing(source="olx", external_id="1", url="https://www.olx.pl/d/oferta/x.html",
                            title="Rower Kross", price=Decimal(900), id=1, first_seen_at=T0, posted_at=posted,
                            seller_created_at=seller_created, seller_is_business=business)
    return OutgoingListing(User(id=1, telegram_id=10), listing, 3, "Rowery", "new", None)


class RenderTests(unittest.TestCase):
    def test_posted_date_and_old_seller(self):
        text, _ = render_listing_notification(item(datetime(2013, 5, 8, tzinfo=UTC), business=True), T0)
        self.assertIn("📅 opublikowano 24.10.2026 10:00", text)
        self.assertIn("👤 konto sprzedawcy od 05.2013 (firma)", text)
        self.assertNotIn("Nowe konto", text)

    def test_new_seller_warning(self):
        text, _ = render_listing_notification(item(T0 - timedelta(days=3)), T0)
        self.assertIn("⚠️ <b>Nowe konto sprzedawcy</b> (od 21.10.2026)", text)

    def test_unknown_seller_and_date(self):
        text, _ = render_listing_notification(item(None, posted=None), T0)
        self.assertNotIn("konto", text)
        self.assertNotIn("opublikowano", text)


class HideNewSellersTests(unittest.IsolatedAsyncioTestCase):
    async def test_hide_toggle(self):
        app = await App().setup()
        user = await app.user(111)
        search = await app.searches.create(user, draft())
        await app.monitoring.check_search(search.id)
        app.mock.publish(Listing(source="mock", external_id="scam", url="https://example.invalid/test-listing/s",
                                 title="[TEST] Rower tanio", price=Decimal(100), seller_created_at=T0 - timedelta(days=1)))
        app.mock.publish(Listing(source="mock", external_id="ok", url="https://example.invalid/test-listing/o",
                                 title="[TEST] Rower uczciwy", price=Decimal(900),
                                 seller_created_at=datetime(2015, 1, 1, tzinfo=UTC)))
        await app.monitoring.check_search(search.id)
        await app.users.set_hide_new_sellers(user, True)
        report = await app.notifications.deliver_batch()
        self.assertEqual((report.sent, report.hidden_new_seller), (1, 1))
        self.assertEqual(app.notifier.sent[0].listing.external_id, "ok")

    async def test_warn_mode_sends_everything(self):
        app = await App().setup()
        user = await app.user(111)
        search = await app.searches.create(user, draft())
        await app.monitoring.check_search(search.id)
        app.mock.publish(Listing(source="mock", external_id="scam", url="https://example.invalid/test-listing/s",
                                 title="[TEST] Rower tanio", price=Decimal(100), seller_created_at=T0 - timedelta(days=1)))
        await app.monitoring.check_search(search.id)
        self.assertEqual((await app.notifications.deliver_batch()).sent, 1)


if __name__ == "__main__":
    unittest.main()
