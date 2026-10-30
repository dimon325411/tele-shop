import unittest
from datetime import timedelta
from decimal import Decimal

from app.domain.models import Listing
from app.domain.pricing import MIN_SAMPLE, price_stats, remove_outliers, verdict_key
from app.presentation import render_listing_notification
from tests.unit.helpers import App, draft

D = Decimal


class PricingTests(unittest.TestCase):
    def test_median_and_diff(self):
        s = price_stats([D(x) for x in (800, 900, 1000, 1100, 1200)])
        self.assertEqual((s.median, s.count), (D(1000), 5))
        self.assertEqual(s.diff_percent(D(820)), -18)
        self.assertEqual(s.diff_percent(D(1150)), 15)
        self.assertEqual(s.diff_percent(D(1000)), 0)

    def test_outliers_removed(self):
        prices = [D(x) for x in (900, 950, 1000, 1000, 1050, 1100, 9999, 1)]
        self.assertNotIn(D(9999), remove_outliers(prices))
        self.assertNotIn(D(1), remove_outliers(prices))
        self.assertEqual(price_stats(prices).median, D(1000))

    def test_small_sample_gives_no_verdict(self):
        self.assertIsNone(price_stats([D(100)] * (MIN_SAMPLE - 1)))
        self.assertIsNone(price_stats([D(0), D(0), D(0), D(0), D(0)]))  # zero/"free" prices ignored

    def test_verdicts(self):
        self.assertEqual(verdict_key(-18), "price.cheaper")
        self.assertEqual(verdict_key(3), "price.typical")
        self.assertEqual(verdict_key(25), "price.pricier")


def L(ext, price, condition="used", title="Rower"):
    return Listing(source="mock", external_id=ext, url=f"https://example.invalid/test-listing/{ext}",
                   title=f"[TEST] {title} {ext}", price=D(price), condition=condition)


class PriceCompareFlowTests(unittest.IsolatedAsyncioTestCase):
    async def _flow(self, plan_code):
        app = await App().setup()
        user = await app.user(111)
        await app.admin.grant(999, 111, plan_code, 30)
        search = await app.searches.create(user, draft(keywords=["rower"]))
        for i, p in enumerate((900, 950, 1000, 1050, 1100, 1000)):
            app.mock.publish(L(f"b{i}", p))
        app.mock.publish(L("n1", 999, condition="new"))  # different condition, not enough of them
        await app.monitoring.check_search(search.id)  # baseline = sample of similar listings
        app.mock.publish(L("x", 820, title="Rower okazja"))
        await app.monitoring.check_search(search.id)
        await app.notifications.deliver_batch()
        return app

    async def test_notification_shows_comparison_for_pro(self):
        app = await self._flow("pro")
        item = app.notifier.sent[-1]
        # 6 published + the seed "Rower górski Kross" (1200, used) of the mock catalogue
        self.assertEqual((item.price_stats.median, item.price_stats.count), (D(1000), 7))
        self.assertTrue(item.price_stats.same_condition)
        text, _ = render_listing_notification(item, app.clock())
        self.assertIn("Podobne w tym samym stanie (7): mediana 1 000 PLN → ta oferta 18% taniej", text)

    async def test_not_shown_without_plan_feature(self):
        app = await self._flow("basic")
        self.assertIsNone(app.notifier.sent[-1].price_stats)
        text, _ = render_listing_notification(app.notifier.sent[-1], app.clock())
        self.assertNotIn("mediana", text)

    async def test_old_listings_leave_the_sample(self):
        app = await App().setup()
        user = await app.user(111)
        await app.admin.grant(999, 111, "pro", 60)
        search = await app.searches.create(user, draft(keywords=["rower"]))
        for i in range(6):
            app.mock.publish(L(f"o{i}", 1000))
        await app.monitoring.check_search(search.id)
        app.clock.advance(days=31)
        app.mock._listings.clear()
        app.mock.publish(L("y", 500))
        await app.monitoring.check_search(search.id)
        listing = next(l for l in app.repo.listings.values() if l.external_id == "y")
        self.assertIsNone(await app.notifications.comparable_stats(search.id, listing, app.clock()))


if __name__ == "__main__":
    unittest.main()
