"""'Oddam za darmo': recognising free offers, estimating resale value, alerting only nearby and >= 200 zł."""

import json
import unittest
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from app.domain.freebies import comparable, is_free_junk, is_free_offer, value_query
from app.domain.models import User
from app.services.freebies import FreebieService
from app.sources.http import HttpResponse, PoliteFetcher
from app.sources.olx import OlxPersonalSource
from app.sources.olx_categories import OlxCategories
from app.sources.registry import SourceRegistry
from app.storage.memory import MemoryRepository
from tests.unit.helpers import T0, FakeClock, FakeNotifier

KRK = {"lat": 50.06, "lon": 19.94}
WAW = {"lat": 52.23, "lon": 21.01}


def offer(oid, title, price=None, free=False, posted=T0, where=KRK, city="Kraków", description=""):
    price_param = ({"key": "price", "value": {"value": 0, "type": "free", "label": "Za darmo", "currency": "PLN"}}
                   if free else {"key": "price", "value": {"value": price, "currency": "PLN", "label": f"{price} zł"}})
    return {"id": oid, "url": f"https://www.olx.pl/d/oferta/x-{oid}.html", "title": title, "description": description,
            "created_time": posted.isoformat(), "params": [price_param], "map": where,
            "location": {"city": {"name": city}}, "category": {"id": 1}}


class DomainTests(unittest.TestCase):
    def test_free_detection(self):
        self.assertTrue(is_free_offer(offer(1, "Pralka Bosch", free=True)))
        self.assertTrue(is_free_offer({"title": "Oddam fotel", "params": []}))
        self.assertFalse(is_free_offer(offer(2, "Pralka Bosch", price=400)))
        self.assertFalse(is_free_offer(offer(3, "Oddam iPhone 13 za 900", price=900)))  # a sale, despite "Oddam"

    def test_junk_and_query(self):
        for title in ["Oddam kotka w dobre ręce", "Przyjmę meble za darmo", "Oddam gruz", "Oddam pralkę zepsutą",
                      "Dwie Suczki do oddania.", "Oddam gupiki", "Szczenięta do adopcji"]:
            self.assertTrue(is_free_junk(title), title)
        self.assertFalse(is_free_junk("Oddam pralkę Bosch"))
        self.assertEqual(value_query("Oddam za darmo pralkę Bosch Serie 4, sprawna"), "pralke bosch serie")
        self.assertEqual(value_query("Oddam PS4 slim"), "ps4 slim")
        self.assertIsNone(value_query("Oddam za darmo - pilne"))
        self.assertTrue(comparable("Pralka BOSCH Serie 6 8kg", "pralke bosch serie"))
        self.assertFalse(comparable("Pralka Samsung", "pralke bosch"))


class ScanTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = []
        self.payloads = {}

        async def transport(url, headers, timeout):
            query = parse_qs(urlparse(url).query)["query"][0]
            self.calls.append(query)
            return HttpResponse(200, json.dumps({"data": self.payloads.get(query, [])}), {})

        async def no_sleep(_):
            return None

        source = OlxPersonalSource(PoliteFetcher(transport, min_interval=0, sleep=no_sleep), OlxCategories({}))
        self.repo = MemoryRepository()
        self.clock = FakeClock(T0 + timedelta(hours=1))
        self.service = FreebieService(self.repo, SourceRegistry({"olx": source}, ["olx"]), self.clock)
        self.notifier = FakeNotifier()
        self.owner = User(id=1, telegram_id=10, deal_city="krakow", deal_radius_km=30, free_alerts=True)

    async def test_only_valuable_fresh_nearby_items_are_sent(self):
        self.payloads["oddam"] = [
            offer(1, "Oddam pralkę Bosch Serie 4", free=True),                       # valuable, nearby -> sent
            offer(2, "Oddam regał IKEA Billy", free=True),                            # nearby, worth ~80 zł
            offer(3, "Oddam pralkę Bosch Serie 4", free=True, where=WAW, city="Warszawa"),  # far away
            offer(4, "Oddam kotka", free=True),                                       # never resold
            offer(5, "Oddam lodówkę Samsung", free=True, posted=T0 - timedelta(hours=8)),  # gone by now
            offer(6, "Pralka Bosch Serie 4 sprzedam", price=500),                     # not free
        ]
        self.payloads["pralke bosch serie"] = [offer(100 + i, f"Pralka Bosch Serie 4 {i}", price=p)
                                              for i, p in enumerate([400, 450, 500, 420, 480, 470])]
        self.payloads["regal ikea billy"] = [offer(200 + i, "Regał IKEA Billy biały", price=p)
                                             for i, p in enumerate([60, 80, 90, 70, 100])]
        out = await self.service.scan([self.owner], self.notifier)
        self.assertEqual((out.in_area, out.priced, out.sent, out.too_cheap), (2, 2, 1, 1))
        sent = self.notifier.sent[0]
        self.assertEqual((sent.kind, sent.listing.external_id), ("free", "1"))
        self.assertEqual(int(sent.price_stats.median), 460)  # (450 + 470) / 2
        # nothing is examined twice (each offer costs one polite request at most once)
        self.calls.clear()
        out = await self.service.scan([self.owner], self.notifier)
        self.assertEqual((out.priced, out.sent), (0, 0))
        self.assertEqual(self.calls, ["oddam", "za darmo"])

    async def test_needs_area_and_option(self):
        self.payloads["oddam"] = [offer(1, "Oddam pralkę Bosch Serie 4", free=True)]
        no_area = User(id=2, telegram_id=20, free_alerts=True)
        off = User(id=3, telegram_id=30, deal_city="krakow", deal_radius_km=30)
        out = await self.service.scan([no_area, off], self.notifier)
        self.assertEqual((out.scanned, self.calls), (0, []))  # no request at all

    async def test_too_few_comparables_no_alert(self):
        self.payloads["oddam"] = [offer(1, "Oddam piękną komodę retro", free=True)]
        self.payloads["piekna komode retro"] = [offer(9, "Komoda retro", price=300)]
        out = await self.service.scan([self.owner], self.notifier)
        self.assertEqual((out.no_estimate, out.sent), (1, 0))

    def test_render(self):
        from decimal import Decimal as D

        from app.domain.pricing import PriceStats
        from app.presentation import render_listing_notification
        from app.services.notifications import OutgoingListing
        from app.domain.models import StoredListing

        listing = StoredListing(source="olx", external_id="1", url="https://www.olx.pl/d/oferta/x-1.html",
                                title="Oddam pralkę Bosch", posted_at=T0, city="Wieliczka", latitude=49.987,
                                longitude=20.065, description="Działa, odbiór dziś.", id=1)
        text, buttons = render_listing_notification(
            OutgoingListing(self.owner, listing, None, None, "free", None, PriceStats(D(465), D(440), D(490), 6)),
            T0 + timedelta(hours=1))
        text = text.replace("\xa0", " ")
        self.assertIn("🎁 <b>Za darmo:</b> Oddam pralkę Bosch", text)
        self.assertIn("wartość ~<b>465 zł</b> (mediana 6 płatnych ofert", text)
        self.assertIn("📍 Wieliczka (12 km)", text)
        self.assertIn("Działa, odbiór dziś.", text)
        self.assertEqual(buttons[0][0].url, "https://www.olx.pl/d/oferta/x-1.html")


if __name__ == "__main__":
    unittest.main()


class TransientNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeouts_become_retryable_then_unavailable(self):
        from app.sources.base import SourceUnavailable
        from app.sources.http import TransientNetworkError

        calls = []

        async def transport(url, headers, timeout):
            calls.append(url)
            raise TransientNetworkError("ReadTimeout")

        async def no_sleep(_):
            return None

        fetcher = PoliteFetcher(transport, min_interval=0, sleep=no_sleep, max_retries=2)
        with self.assertRaises(SourceUnavailable):  # a clean, handled error (monitoring backs off)
            await fetcher.get("https://www.olx.pl/api/v1/offers/?query=x")
        self.assertEqual(len(calls), 3)
