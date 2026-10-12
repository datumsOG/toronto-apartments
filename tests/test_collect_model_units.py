"""The listing shape and the same-unit rule."""
import unittest

from collect import units
from collect.model import Listing, ListingError

BASE = {"source": "t", "source_url": "https://example.invalid/u/1", "seen": "2026-10-09T22:00:00Z",
        "address": "77 Davisville Ave, Toronto", "beds": 1, "rent": 2350}


def L(**kw):
    return Listing.from_dict({**BASE, **kw})


class Model(unittest.TestCase):
    def test_minimal(self):
        li = L()
        self.assertEqual((li.beds, li.rent, li.baths, li.available), (1, 2350, None, None))

    def test_each_required_field(self):
        for k in ("source", "source_url", "seen", "address", "beds", "rent"):
            with self.subTest(k=k):
                d = dict(BASE)
                del d[k]
                with self.assertRaises(ListingError):
                    Listing.from_dict(d)
                with self.assertRaises(ListingError):
                    Listing.from_dict({**BASE, k: ""})

    def test_coerces(self):
        li = L(beds="2", rent="$2,350.00", baths="1.5", sqft="620", lat="43.7", lon="-79.4",
               available="2026-11-01T00:00:00")
        self.assertEqual((li.beds, li.rent, li.baths, li.sqft, li.lat, li.available),
                         (2, 2350, 1.5, 620, 43.7, "2026-11-01"))
        self.assertEqual(L(beds="Bachelor").beds, 0)
        self.assertEqual(L(beds="studio").beds, 0)

    def test_rejects_description_and_photos(self):
        for k in ("description", "photos", "image", "Description", "contact_name"):
            with self.subTest(k=k):
                with self.assertRaises(ListingError):
                    L(**{k: "x"})

    def test_rejects_unknown_and_bad_values(self):
        for kw in ({"colour": "blue"}, {"beds": "lots"}, {"rent": 0}, {"rent": True},
                   {"available": "soon"}, {"seen": "yesterday"}, {"source_url": "javascript:x"},
                   {"beds": 40}):
            with self.subTest(kw=kw):
                with self.assertRaises(ListingError):
                    L(**kw)

    def test_round_trip(self):
        li = L(rent_is_floor=True, baths=1)
        self.assertEqual(Listing.from_dict(li.to_dict()), li)
        self.assertNotIn("rent_is_floor", L().to_dict())


class SameUnit(unittest.TestCase):
    def test_rules(self):
        a = L(baths=1, sqft=600, rent=2000)
        self.assertTrue(units.same_unit(a, L(baths=1, sqft=617, rent=2040)))     # 2.8%, 1.96%
        self.assertTrue(units.same_unit(a, L(baths=None, sqft=None, rent=2000)))  # unknowns agree
        self.assertFalse(units.same_unit(a, L(beds=2, baths=1, sqft=600, rent=2000)))
        self.assertFalse(units.same_unit(a, L(baths=2, sqft=600, rent=2000)))
        self.assertFalse(units.same_unit(a, L(baths=1, sqft=620, rent=2000)))    # 3.2%
        self.assertFalse(units.same_unit(a, L(baths=1, sqft=600, rent=2045)))    # 2.2%

    def test_merge_keeps_links_and_fills_gaps(self):
        out = units.merge([
            L(source_url="https://example.invalid/a", sqft=None, available="2026-12-01"),
            L(source_url="https://example.invalid/b", sqft=600, available="2026-11-01"),
            L(source_url="https://example.invalid/c", beds=2, rent=3000),
        ])
        self.assertEqual(len(out), 2)
        one = out[0]
        self.assertEqual([x["url"] for x in one.links], ["https://example.invalid/a", "https://example.invalid/b"])
        self.assertEqual((one.sqft, one.available), (600, "2026-11-01"))

    def test_merge_compares_with_the_first_listing_only(self):
        # 2000 -> 2039 -> 2078: each step is under 2%, the ends are not. No drift.
        out = units.merge([L(rent=2000), L(rent=2039), L(rent=2078)])
        self.assertEqual([u.rent for u in out], [2000, 2078])

    def test_identical_suites_are_merged_on_purpose(self):
        out = units.merge([L(source_url="https://example.invalid/fl3"), L(source_url="https://example.invalid/fl9")])
        self.assertEqual(len(out), 1)
        self.assertEqual(len(out[0].links), 2)


if __name__ == "__main__":
    unittest.main()
