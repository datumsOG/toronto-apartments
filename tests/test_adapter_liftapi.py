"""The Rentsync Lift API adapter against trimmed captures of real answers. Never the internet."""
import unittest
from pathlib import Path

from collect.adapter import FetchError
from collect.adapters.liftapi import GreenwinAdapter, HazelviewAdapter

PAGES = Path(__file__).parent / "fixtures" / "pages" / "liftapi"
SEEN = "2026-10-10T18:00:00Z"
ALL = "https://api.theliftsystem.com/v2/search?client_id=2&auth_token=T&limit=500&offset=0"
BEDS1 = ALL + "&min_bed=1&max_bed=1&only_available_suites=true"


class FakeFetcher:
    def __init__(self, js):
        self.js, self.asked = js, []

    def get(self, url):
        self.asked.append(url)
        if "/scripts/main.js" in url:
            return self.js
        return b'<script src="/scripts/main.js?d=1"></script>' if "v2/search" not in url else b"[]"


class LiftApi(unittest.TestCase):
    def setUp(self):
        self.a = GreenwinAdapter(seen=SEEN)

    def test_sites_are_toronto_apartment_buildings_one_per_street_number(self):
        sites = self.a.sites((PAGES / "all.json").read_bytes(), ALL)
        addrs = [s[0] for s in sites]
        self.assertIn("88 Erskine Ave., Toronto", addrs)
        self.assertEqual(len([a for a in addrs if "Winnett" in a]), 2)      # "300 & 330 Winnett Ave."
        self.assertFalse(any("Kitchener" in a for a in addrs))
        self.assertEqual(len(addrs), 3)                                      # the office is not a site
        self.assertEqual(self.a.sites((PAGES / "beds1.json").read_bytes(), BEDS1), [])

    def test_one_listing_per_building_for_the_bedroom_count_asked(self):
        ls = self.a.parse((PAGES / "beds1.json").read_bytes(), BEDS1)
        e = next(l for l in ls if l.address.startswith("88 Erskine"))
        self.assertEqual((e.beds, e.rent, e.rent_is_floor, e.sqft), (1, 1790, True, 450))
        self.assertTrue(e.source_url.startswith("https://"))
        self.assertFalse(any("Kitchener" in l.address for l in ls))
        self.assertEqual(self.a.parse((PAGES / "all.json").read_bytes(), ALL), [])   # no bedroom count, no listings

    def test_token_is_read_from_either_script_generation(self):
        f = FakeFetcher(b'x=("https://api.theliftsystem.com/v2/search","abc123abc123abc123ab"),y')
        list(self.a.fetch(f))
        self.assertEqual(len(f.asked), 8)                                    # home, script, all, five bedroom counts
        self.assertTrue(all("auth_token=abc123abc123abc123ab" in u for u in f.asked[2:]))
        h = HazelviewAdapter(seen=SEEN)
        f = FakeFetcher(b'p="https://lift-api.rentsync.com/v2/search?auth_token=zz9zz9zz9zz9zz9zz9&"}')
        list(h.fetch(f))
        self.assertTrue(all("auth_token=zz9zz9zz9zz9zz9zz9" in u and "client_id=497" in u for u in f.asked[2:]))
        self.assertTrue(all("lift-api.rentsync.com" in u for u in f.asked[2:]))
        f = FakeFetcher(b'r="https://api.theliftsystem.com/v2/search?locale="+a+"&",o="&auth_token=tok3tok3tok3tok3tok3",')
        list(self.a.fetch(f))
        self.assertTrue(all("auth_token=tok3tok3tok3tok3tok3" in u for u in f.asked[2:]))
        self.assertIn("lift-api.rentsync.com", h.home_hosts)
        self.assertIn("www.hazelviewproperties.com", h.home_hosts)

    def test_no_token_is_an_error_not_a_guess(self):
        with self.assertRaises(FetchError):
            list(self.a.fetch(FakeFetcher(b"nothing here")))


if __name__ == "__main__":
    unittest.main()
