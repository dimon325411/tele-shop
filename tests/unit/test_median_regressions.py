import unittest
from decimal import Decimal as D

from app.domain.models import StoredListing
from app.domain.variants import variant_of
from app.services.market import stats_for, usable
from app.presentation import fmt_variant
from app.analytics import Analytics
from tests.unit.helpers import App, draft
from tests.unit.test_controller_jobs import container


def item(i, title, price=1000, **kw):
    kw.setdefault('condition', 'used')
    return StoredListing(source='mock', external_id=str(i), id=i, title=title,
                         url='https://example.invalid/x', price=D(price), **kw)


class ConfigurationTests(unittest.TestCase):
    def test_gpu_median_never_mixes_vram(self):
        target = item(1, 'RTX 5060 Ti 16GB')
        pool = [item(i+2, 'RTX 5060 Ti 16GB', 2000) for i in range(5)]
        pool += [item(i+10, 'RTX 5060 Ti 8GB', 1000) for i in range(8)]
        result = stats_for(target, pool)
        self.assertIsNotNone(result)
        self.assertEqual((result.median, result.count), (D(2000), 5))

    def test_desktop_ram_and_disk_are_separate_dimensions(self):
        a = variant_of('Komputer i5-12400F RTX 3060 12GB 32GB RAM SSD 1TB')
        b = variant_of('Komputer i5-12400F RTX 3060 12GB SSD 1TB 32GB RAM')
        c = variant_of('Komputer i5-12400F RTX 3060 12GB 16GB RAM SSD 1TB')
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        label = fmt_variant(a)
        self.assertIn('32 GB RAM', label)
        self.assertIn('1 TB', label)
        self.assertEqual(a, variant_of('Komputer 32GB RAM i5-12400F RTX 3060 12GB SSD 1TB'))

    def test_laptop_ram_is_not_storage(self):
        self.assertNotEqual(variant_of('MacBook Air M1 8GB RAM 256GB SSD'),
                            variant_of('MacBook Air M1 16GB RAM 256GB SSD'))

    def test_pc_gpu_vram_and_model_are_part_of_configuration(self):
        a = variant_of('Komputer i5-12400F RTX 5060 Ti 8GB 32GB RAM SSD 1TB')
        b = variant_of('Komputer i5-12400F RTX 5060 Ti 16GB 32GB RAM SSD 1TB')
        c = variant_of('Komputer i5-12400F RTX 4060 Ti 8GB 32GB RAM SSD 1TB')
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)

    def test_laptop_chip_and_compact_specs_are_recognized(self):
        a = variant_of('MacBook Air M1 8/256')
        b = variant_of('MacBook Air M1 8GB RAM 256GB SSD')
        c = variant_of('MacBook Air M2 8GB RAM 256GB SSD')
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_unknown_condition_and_currency_do_not_get_verdicts(self):
        peers = [item(i+2, 'iPhone 15 Pro 128GB', 3000, condition='new') for i in range(5)]
        self.assertIsNone(stats_for(item(1, 'iPhone 15 Pro 128GB', condition=None), peers))
        for p in peers:
            p.currency = None
        self.assertIsNone(stats_for(item(1, 'iPhone 15 Pro 128GB', condition='new', currency=None), peers))

    def test_unknown_phone_configuration_has_no_deal_verdict(self):
        target = item(1, 'iPhone 15 Pro')
        pool = [item(i+2, 'iPhone 15 Pro 512GB', 3000) for i in range(5)]
        self.assertIsNone(stats_for(target, pool))

    def test_damaged_description_is_excluded_but_negation_is_allowed(self):
        self.assertFalse(usable(item(1, 'iPhone 15 Pro 128GB', description='Telefon uszkodzony, nie działa.')))
        self.assertTrue(usable(item(2, 'iPhone 15 Pro 128GB', description='Bez blokady iCloud. Wszystko działa.')))

    def test_different_known_conditions_are_never_pooled(self):
        target = item(1, 'iPhone 15 Pro 128GB', condition='used')
        pool = [item(i+2, 'iPhone 15 Pro 128GB', 3000, condition='new') for i in range(5)]
        self.assertIsNone(stats_for(target, pool))


class CardTests(unittest.IsolatedAsyncioTestCase):
    async def test_chart_prices_use_the_same_outlier_filter_as_cards(self):
        app = await App().setup()
        user = await app.user()
        search = await app.searches.create(user, draft(name='iPhone', keywords=['iphone']))
        for i, price in enumerate([1000, 1100, 1200, 1300, 1400, 9999]):
            saved = (await app.repo.upsert_listing(item(i+1, 'iPhone 15 Pro 128GB', price), app.clock())).listing
            await app.repo.add_match(search.id, saved.id, user.id, [])
        prices = await app.monitoring.market.current_prices(search.id, '128gb')
        self.assertEqual(prices, [D(1000), D(1100), D(1200), D(1300), D(1400)])

    async def test_mixed_conditions_get_separate_card_groups(self):
        app = await App().setup()
        user = await app.user()
        search = await app.searches.create(user, draft(name='iPhone', keywords=['iphone']))
        for i in range(10):
            obj = item(i+1, 'iPhone 15 Pro 128GB', 1000 if i < 5 else 4000,
                       condition='used' if i < 5 else 'new')
            saved = (await app.repo.upsert_listing(obj, app.clock())).listing
            await app.repo.add_match(search.id, saved.id, user.id, [])
        a = Analytics(container(app))
        text, _ = await a.stats(user, search)
        self.assertIn('mediana <b>1\xa0000 PLN</b>', text)
        self.assertIn('mediana <b>4\xa0000 PLN</b>', text)
        self.assertNotIn('2\xa0500', text)
        self.assertEqual(await a.market.current_prices(search.id), [])

    async def test_discount_threshold_is_not_rounded_up(self):
        app = await App().setup()
        user = await app.user()
        search = await app.searches.create(user, draft(name='iPhone', keywords=['iphone']))
        for i in range(6):
            saved = (await app.repo.upsert_listing(item(i+1, 'iPhone 15 Pro 128GB', 2000), app.clock())).listing
            await app.repo.add_match(search.id, saved.id, user.id, [])
        saved = (await app.repo.upsert_listing(item(20, 'iPhone 15 Pro 128GB', 1709), app.clock())).listing
        await app.repo.add_match(search.id, saved.id, user.id, [])
        self.assertIsNone(await app.monitoring.market.evaluate_deal(search.id, saved, 15))

    async def test_used_card_snapshot_and_chart_ignore_unknown_condition_and_currency(self):
        app = await App().setup()
        user = await app.user()
        search = await app.searches.create(user, draft(name='iPhone', keywords=['iphone'], condition='used'))
        for i in range(15):
            obj = item(i+1, 'iPhone 15 Pro 128GB', 1000 if i < 5 else 4000,
                       condition='used' if i < 5 or i >= 10 else None,
                       currency=None if i >= 10 else 'PLN')
            saved = (await app.repo.upsert_listing(obj, app.clock())).listing
            await app.repo.add_match(search.id, saved.id, user.id, [])
        a = Analytics(container(app))
        text, _ = await a.stats(user, search)
        self.assertIn('mediana <b>1\xa0000 PLN</b>', text)
        await a.market.take_snapshot(search.id)
        rows = await a.market.history(search.id, '128gb')
        self.assertEqual(rows[-1]['median'], D(1000))
        self.assertEqual(await a.market.current_prices(search.id, '128gb'), [D(1000)] * 5)

    async def test_unknown_gpu_entry_does_not_hide_known_vram_median(self):
        app = await App().setup()
        user = await app.user()
        search = await app.searches.create(user, draft(name='RTX 3070', keywords=['rtx 3070']))
        for i in range(6):
            obj = item(i+1, 'RTX 3070 8GB', 1200, last_seen_at=app.clock())
            saved = (await app.repo.upsert_listing(obj, app.clock())).listing
            await app.repo.add_match(search.id, saved.id, user.id, [])
        saved = (await app.repo.upsert_listing(item(20, 'RTX 3070 zestaw SSD 1TB'), app.clock())).listing
        await app.repo.add_match(search.id, saved.id, user.id, [])
        text, _ = await Analytics(container(app)).stats(user, search)
        self.assertIn('mediana', text)
        self.assertIn('1\xa0200', text)
        self.assertIn('To ceny ofert, nie sprzedaży.', text)
