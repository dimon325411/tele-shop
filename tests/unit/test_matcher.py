import unittest
from decimal import Decimal

from app.domain.matcher import match, phrase_in_tokens, token_matches
from app.domain.models import Condition, Delivery, Listing, SearchCriteria
from app.domain.text import clean_keyword_list, normalize, split_user_list, tokens


def L(title, **kw):
    kw.setdefault("price", Decimal("100"))
    return Listing(source="mock", external_id="1", url="https://example.invalid/1", title=title, **kw)


class TextTests(unittest.TestCase):
    def test_normalize_polish(self):
        self.assertEqual(normalize('Rower GÓRSKI, Kross — 29"'), "rower gorski kross 29")
        self.assertEqual(normalize("Łódź żółć"), "lodz zolc")
        self.assertEqual(normalize(None), "")

    def test_tokens_and_lists(self):
        self.assertEqual(tokens("iPhone-13 Pro!"), ["iphone", "13", "pro"])
        self.assertEqual(split_user_list("a, b; c\nd,,"), ["a", "b", "c", "d"])
        self.assertEqual(clean_keyword_list(["Rower", "rower", " ", "ŁÓDŹ"]), ["rower", "lodz"])


class TokenMatchTests(unittest.TestCase):
    def test_inflection(self):
        for tok in ["rower", "rowery", "rowerem", "roweru"]:
            self.assertTrue(token_matches("rower", tok), tok)
        self.assertTrue(token_matches("kurtka", "kurtki"))
        self.assertTrue(token_matches("rowery", "rower"))

    def test_numbers_exact(self):
        self.assertTrue(token_matches("13", "13"))
        self.assertFalse(token_matches("13", "130"))
        self.assertFalse(token_matches("iphone13", "iphone130"))

    def test_unrelated(self):
        self.assertFalse(token_matches("rower", "rowerek" + "xxxxx"))
        self.assertFalse(token_matches("sofa", "stol"))
        self.assertFalse(token_matches("kot", "kotlet"))  # short keywords are exact

    def test_fuzzy_typos(self):
        self.assertFalse(token_matches("iphone", "ipohne"))
        self.assertTrue(token_matches("iphone", "ipohne", fuzzy=True))
        self.assertFalse(token_matches("iphone", "samsung", fuzzy=True))

    def test_phrase_consecutive(self):
        toks = tokens("Sprzedam iPhone 13 mini")
        self.assertTrue(phrase_in_tokens("iphone 13", toks))
        self.assertFalse(phrase_in_tokens("iphone mini", toks))
        self.assertFalse(phrase_in_tokens("", toks))


class MatchTests(unittest.TestCase):
    def test_keywords_all_required(self):
        c = SearchCriteria(keywords=["rower", "kross"])
        self.assertTrue(match(L("Rower Kross Hexagon"), c).matched)
        r = match(L("Rower Giant"), c)
        self.assertFalse(r.matched)
        self.assertEqual(r.rejected_by, "keywords")

    def test_alt_keywords_any(self):
        c = SearchCriteria(keywords=["rower"], alt_keywords=["kross", "giant"])
        self.assertTrue(match(L("Rower Giant"), c).matched)
        self.assertEqual(match(L("Rower Trek"), c).rejected_by, "alt_keywords")
        only_alt = SearchCriteria(alt_keywords=["sofa", "kanapa"])
        self.assertTrue(match(L("Kanapa rozkładana"), only_alt).matched)

    def test_excluded(self):
        c = SearchCriteria(keywords=["iphone"], excluded_keywords=["uszkodzony", "na czesci"])
        self.assertTrue(match(L("iPhone 13"), c).matched)
        self.assertEqual(match(L("iPhone 14 uszkodzony ekran"), c).rejected_by, "excluded")
        self.assertEqual(match(L("iPhone na części"), c).rejected_by, "excluded")

    def test_description_is_searched(self):
        c = SearchCriteria(keywords=["kross"])
        self.assertTrue(match(L("Rower górski", description="Marka Kross, stan dobry"), c).matched)

    def test_price_bounds(self):
        c = SearchCriteria(keywords=["rower"], price_min=Decimal("100"), price_max=Decimal("500"))
        self.assertTrue(match(L("rower", price=Decimal("100")), c).matched)
        self.assertTrue(match(L("rower", price=Decimal("500")), c).matched)
        self.assertEqual(match(L("rower", price=Decimal("99.99")), c).rejected_by, "price_min")
        self.assertEqual(match(L("rower", price=Decimal("500.01")), c).rejected_by, "price_max")
        self.assertEqual(match(L("rower", price=None), c).rejected_by, "price_unknown")
        self.assertTrue(match(L("rower", price=None), SearchCriteria(keywords=["rower"])).matched)

    def test_currency(self):
        c = SearchCriteria(keywords=["rower"], currency="PLN")
        self.assertEqual(match(L("rower", currency="EUR"), c).rejected_by, "currency")
        self.assertTrue(match(L("rower", currency=None), c).matched)

    def test_condition_and_unknown(self):
        c = SearchCriteria(keywords=["rower"], condition=Condition.NEW)
        self.assertTrue(match(L("rower", condition="new"), c).matched)
        self.assertEqual(match(L("rower", condition="used"), c).rejected_by, "condition")
        r = match(L("rower", condition=None), c)
        self.assertTrue(r.matched)
        self.assertIn("reason.unknown", r.reason_keys())
        strict = SearchCriteria(keywords=["rower"], condition=Condition.NEW, include_unknown=False)
        self.assertEqual(match(L("rower", condition=None), strict).rejected_by, "condition_unknown")

    def test_city_region(self):
        c = SearchCriteria(keywords=["rower"], city="Kraków")
        self.assertTrue(match(L("rower", city="KRAKOW"), c).matched)
        self.assertEqual(match(L("rower", city="Gdańsk"), c).rejected_by, "city")
        c2 = SearchCriteria(keywords=["rower"], region="małopolskie", include_unknown=False)
        self.assertEqual(match(L("rower", region=None), c2).rejected_by, "region_unknown")

    def test_radius(self):
        krakow = (50.0647, 19.9450)
        c = SearchCriteria(keywords=["rower"], radius_km=20, center_lat=krakow[0], center_lon=krakow[1])
        self.assertTrue(match(L("rower", latitude=50.0, longitude=20.0), c).matched)
        self.assertEqual(match(L("rower", latitude=52.23, longitude=21.01), c).rejected_by, "radius")  # Warszawa

    def test_delivery(self):
        c = SearchCriteria(keywords=["rower"], delivery=Delivery.SHIPPING)
        self.assertTrue(match(L("rower", delivery_available=True), c).matched)
        self.assertEqual(match(L("rower", delivery_available=False), c).rejected_by, "delivery")
        self.assertTrue(match(L("rower"), SearchCriteria(keywords=["rower"], delivery=Delivery.PICKUP)).matched)

    def test_category(self):
        c = SearchCriteria(keywords=["rower"], category="Sport")
        self.assertTrue(match(L("rower", category="sport"), c).matched)
        self.assertEqual(match(L("rower", category="dom"), c).rejected_by, "category")

    def test_reasons_explain(self):
        c = SearchCriteria(keywords=["rower"], alt_keywords=["kross"], price_max=Decimal("2000"))
        r = match(L("Rower Kross", price=Decimal("1200")), c)
        self.assertEqual(r.reason_keys(), ["reason.keyword", "reason.alt_keyword", "reason.price"])

    def test_no_keywords_never_matches(self):
        self.assertFalse(match(L("cokolwiek"), SearchCriteria()).matched)


if __name__ == "__main__":
    unittest.main()
