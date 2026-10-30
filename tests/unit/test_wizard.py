import json
import unittest
from decimal import Decimal

from app.domain.plans import DEFAULT_PLANS
from app.sources.disabled import AllegroLokalnieSource, OlxSource
from app.sources.mock import MockSource
from app.sources.registry import SourceRegistry
from app.wizard import SearchWizard
from tests.unit.helpers import App

PRO = next(p for p in DEFAULT_PLANS if p.code == "pro")
TRIAL = next(p for p in DEFAULT_PLANS if p.code == "trial")


def registry(enabled=("mock",)):
    return SourceRegistry({"mock": MockSource(), "olx": OlxSource(), "allegro_lokalnie": AllegroLokalnieSource()},
                          list(enabled))


def callbacks(reply):
    return [b.callback for row in reply.buttons for b in row if b.callback]


class WizardTests(unittest.TestCase):
    def setUp(self):
        self.w = SearchWizard(registry(("mock", "olx")))

    def run_happy_path(self, plan=PRO):
        state, r = self.w.start(plan)
        self.assertIn("Krok 1/10", r.text)
        r = self.w.on_text(state, "  Rower   górski ")
        self.assertEqual(state["step"], "sources")
        self.assertIn("wz:src:mock", callbacks(r))
        self.assertIn("wz:src:olx", callbacks(r))  # only enabled sources are offered
        self.assertNotIn("wz:src:allegro_lokalnie", callbacks(r))
        r = self.w.on_callback(state, "wz:src_done")
        self.assertTrue(r.error)  # nothing selected
        self.w.on_callback(state, "wz:src:mock")
        self.w.on_callback(state, "wz:src:olx")
        r = self.w.on_callback(state, "wz:src:olx")  # toggle off
        self.assertIn("✅", r.buttons[0][0].text)
        self.w.on_callback(state, "wz:src_done")
        self.assertEqual(state["step"], "keywords")
        self.w.on_text(state, "rower, 29")
        self.w.on_text(state, "kross; giant")
        r = self.w.on_callback(state, "wz:skip")  # excluded skipped
        self.assertEqual(state["step"], "category")
        self.w.on_callback(state, "wz:cat:sport")
        r = self.w.on_text(state, "500-100")
        self.assertTrue(r.error)
        self.assertIn("minimalna", r.text)
        self.w.on_text(state, "100-2500")
        self.w.on_callback(state, "wz:cond:used")
        self.w.on_text(state, "Kraków")
        r = self.w.on_callback(state, "wz:del:shipping")
        self.assertEqual(state["step"], "summary")
        return state, r

    def test_happy_path_builds_draft(self):
        state, r = self.run_happy_path()
        self.assertIn("Rower górski", r.text)
        self.assertIn("kross / giant", r.text)
        self.assertIn("Źródło testowe nie filtruje sam po", r.text)  # honest about filtering
        json.dumps(state)  # FSM storage needs JSON-serialisable state
        self.w.on_callback(state, "wz:fuzzy")
        self.w.on_callback(state, "wz:unknown")
        self.w.on_callback(state, "wz:interval")
        r = self.w.on_callback(state, "wz:save")
        self.assertTrue(r.finished)
        d = self.w.draft(state)
        self.assertEqual(d.name, "Rower górski")
        self.assertEqual(d.sources, ["mock"])
        self.assertEqual(d.keywords, ["rower", "29"])
        self.assertEqual(d.alt_keywords, ["kross", "giant"])
        self.assertEqual(d.excluded_keywords, [])
        self.assertEqual((d.price_min, d.price_max), (Decimal("100"), Decimal("2500")))
        self.assertEqual((d.category, d.condition, d.city, d.delivery), ("sport", "used", "Kraków", "shipping"))
        self.assertTrue(d.fuzzy)
        self.assertFalse(d.include_unknown)
        self.assertEqual(d.interval_seconds, 900)  # pro min 300 -> next option

    def test_fuzzy_only_when_plan_allows(self):
        state, r = self.run_happy_path(plan=TRIAL)
        self.assertNotIn("wz:fuzzy", callbacks(r))
        self.w.on_callback(state, "wz:fuzzy")
        self.assertFalse(state["draft"]["fuzzy"])

    def test_keywords_required_somewhere(self):
        state, _ = self.w.start(PRO)
        self.w.on_text(state, "X")
        self.w.on_callback(state, "wz:src:mock")
        self.w.on_callback(state, "wz:src_done")
        self.w.on_text(state, "-")  # no mandatory keywords
        r = self.w.on_callback(state, "wz:skip")  # and no alternatives -> refused
        self.assertTrue(r.error)
        r = self.w.on_text(state, "-")
        self.assertTrue(r.error)
        self.w.on_text(state, "sofa, kanapa")
        self.assertEqual(state["step"], "excluded")

    def test_cancel_and_stale_buttons(self):
        state, _ = self.w.start(PRO)
        r = self.w.on_callback(state, "wz:cond:new")  # button from another step
        self.assertTrue(r.error)
        self.assertEqual(state["step"], "name")
        r = self.w.on_callback(state, "wz:skip")  # name cannot be skipped
        self.assertEqual(state["step"], "name")
        self.assertTrue(self.w.on_callback(state, "wz:cancel").cancelled)

    def test_single_source_preselected(self):
        w = SearchWizard(registry(("mock",)))
        state, _ = w.start(PRO)
        self.assertEqual(state["draft"]["sources"], ["mock"])

    def test_interval_options(self):
        self.assertEqual(SearchWizard.interval_options(300), [300, 900, 1800, 3600, 10800])
        self.assertEqual(SearchWizard.interval_options(900), [900, 1800, 3600, 10800])


class WizardToServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_wizard_draft_creates_search_and_matches(self):
        app = await App().setup()
        user = await app.user(1)
        w = SearchWizard(app.registry)
        state, _ = w.start(TRIAL)
        w.on_text(state, "iPhone do 2000")
        w.on_callback(state, "wz:src_done")  # mock preselected
        w.on_text(state, "iphone")
        w.on_text(state, "-")
        w.on_text(state, "uszkodzony")
        w.on_callback(state, "wz:cat:any")
        w.on_text(state, "-2000")
        w.on_callback(state, "wz:cond:any")
        w.on_text(state, "-")
        w.on_callback(state, "wz:del:any")
        self.assertEqual(state["step"], "summary")
        search = await app.searches.create(user, w.draft(state))
        out = await app.monitoring.check_search(search.id)
        titles = sorted(app.repo.listings[lid].title for (sid, lid) in app.repo.matches)
        self.assertEqual(titles, ["[TEST] iPhone 13 128GB czarny"])  # damaged one excluded, price filter
        self.assertEqual(out.matched, 1)


if __name__ == "__main__":
    unittest.main()
