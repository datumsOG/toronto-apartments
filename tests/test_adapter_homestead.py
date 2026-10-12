"""The Homestead adapter against trimmed captures of real pages. Never the internet."""
import unittest
from pathlib import Path

from collect.adapters.homestead import HomesteadAdapter, building_links, _streets, _beds

PAGES = Path(__file__).parent / "fixtures" / "pages" / "homestead"
SEEN = "2026-10-10T16:00:00Z"


class Homestead(unittest.TestCase):
    def setUp(self):
        self.a = HomesteadAdapter(seen=SEEN)

    def page(self, name):
        return (PAGES / f"{name}.html").read_bytes()

    def test_city_page_gives_building_pages_only(self):
        links = building_links(self.page("city-toronto"))
        self.assertEqual(len(links), 31)
        self.assertTrue(all("/residential/" in u and "/cities/" not in u for u in links))
        self.assertIn("https://www.homestead.ca/residential/140-erskine-avenue-toronto", links)

    def test_one_listing_per_available_suite_type(self):
        url = "https://www.homestead.ca/residential/140-erskine-avenue-toronto"
        ls = self.a.parse(self.page("140-erskine"), url)
        self.assertEqual([(l.unit_label, l.beds, l.rent) for l in ls],
                         [("Bachelor", 0, 1605), ("1 Bedroom", 1, 1615), ("2 Bedroom", 2, 2405)])
        for l in ls:
            self.assertEqual(l.address, "140 Erskine Avenue, Toronto")
            self.assertTrue(l.rent_is_floor)
            self.assertEqual((l.source, l.source_url, l.seen), ("homestead", url, SEEN))
            self.assertAlmostEqual(l.lat, 43.71, places=1)

    def test_unavailable_rows_are_not_listings(self):
        raw = self.page("140-erskine").replace(b">Available<", b">Unavailable<")
        self.assertEqual(self.a.parse(raw, "https://www.homestead.ca/x"), [])

    def test_a_page_for_several_buildings(self):
        url = "https://www.homestead.ca/residential/canyon-apartments"
        self.assertEqual([s[0] for s in self.a.sites(self.page("canyon"), url)],
                         ["15 Canyon Ave., Toronto", "25 Canyon Ave., Toronto"])
        ls = self.a.parse(self.page("canyon"), url)
        self.assertTrue(ls)
        self.assertTrue(all(l.address == "15 Canyon Ave., Toronto" and l.lat is None for l in ls))

    def test_small_parsers(self):
        self.assertEqual(_streets("263/273/283 Pharmacy Avenue"),
                         ["263 Pharmacy Avenue", "273 Pharmacy Avenue", "283 Pharmacy Avenue"])
        self.assertEqual(_streets("140 Erskine Avenue"), ["140 Erskine Avenue"])
        self.assertEqual([_beds(t) for t in ("Bachelor", "1 Bedroom + Den", "3 Bedroom Penthouse", "Parking")],
                         [0, 1, 3, None])

    def test_no_address_block_no_listings(self):
        self.assertEqual(self.a.parse(b"<html><body>nothing</body></html>", "https://www.homestead.ca/x"), [])


if __name__ == "__main__":
    unittest.main()
