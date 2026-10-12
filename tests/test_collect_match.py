"""The matcher: address first, then coordinates, never a silent guess."""
import json
import tempfile
import unittest
from pathlib import Path

from collect import buildings as B
from collect import match
from collect.model import Listing

REGISTRY = Path(__file__).resolve().parent.parent / "data" / "registry"


def bld(id, addr, lat=None, lon=None, units=100, type="private"):
    return B._keyed(B.Building(id=id, addr=addr, units=units, type=type, lat=lat, lon=lon))


def listing(addr, lat=None, lon=None):
    return Listing.from_dict({"source": "t", "source_url": "https://example.invalid/x",
                              "seen": "2026-10-09T22:00:00Z", "address": addr, "beds": 1, "rent": 2000,
                              "lat": lat, "lon": lon})


# Invented buildings on real street names. 0.0001 deg of latitude is about 11.1 m.
BUILDINGS = [
    bld(1, "181-183  GERRARD ST E", 43.66000, -79.37000),
    bld(2, "183  GERRARD ST E", 43.66010, -79.37000),
    bld(3, "100  HIGH PARK AVE", 43.65000, -79.47000),
    bld(4, "100  HIGH PARK BLVD", 43.64000, -79.46000),
    bld(5, "25  ST DENNIS DR", 43.71000, -79.33000),
    bld(6, "355  ST CLAIR AVE W", 43.68000, -79.41000),
    bld(7, "355  ST CLAIR AVE E", 43.69000, -79.39000),
    bld(8, "40-50  ALEXANDER ST", 43.66500, -79.38000),
    bld(9, "40-50  ALEXANDER ST", 43.66500, -79.38001),
    bld(10, "77  DAVISVILLE AVE", 43.69800, -79.39500),
    bld(11, "2 A  CARLUKE CRES", None, None),
    bld(12, "10  KENDLETON DR", 43.75000, -79.55000),
    bld(13, "10  KENDLETON DR", 43.75200, -79.55000),   # same address, 222 m apart
    bld(14, "50  MAIN ST", 43.70000, -79.30000),
    bld(15, "60  OTHER ST", 43.70005, -79.30000),       # 5.6 m from 14, different street
]


class Address(unittest.TestCase):
    def setUp(self):
        self.m = match.Matcher(BUILDINGS)

    def test_exact_beats_range(self):
        r = self.m.match(listing("183 Gerrard Street East"))
        self.assertEqual((r.building_id, r.method, r.detail), (2, "address", "exact/strict"))
        self.assertEqual(r.confidence, 1.0)

    def test_inside_a_range(self):
        r = self.m.match(listing("181 Gerrard St E, Toronto, ON M5A 2E4"))
        self.assertEqual((r.building_id, r.detail), (1, "range/strict"))
        self.assertEqual(r.confidence, match.CONFIDENCE[("address", "range", "strict")])

    def test_street_type_must_agree(self):
        self.assertEqual(self.m.match(listing("100 High Park Ave.")).building_id, 3)
        self.assertEqual(self.m.match(listing("100 High Park Boulevard")).building_id, 4)

    def test_missing_type_with_two_candidates_is_ambiguous(self):
        r = self.m.match(listing("100 High Park"))
        self.assertEqual((r.method, r.candidates), ("ambiguous", (3, 4)))
        self.assertIsNone(r.building_id)

    def test_missing_direction_is_loose(self):
        r = self.m.match(listing("77 Davisville"))
        self.assertEqual((r.building_id, r.detail), (10, "exact/loose"))
        r = self.m.match(listing("355 St Clair Ave"))
        self.assertEqual(r.method, "ambiguous")

    def test_saints(self):
        self.assertEqual(self.m.match(listing("355 St. Clair Avenue West")).building_id, 6)
        self.assertEqual(self.m.match(listing("355 Saint Clair Ave E")).building_id, 7)
        self.assertEqual(self.m.match(listing("25 Dennis St")).method, "unmatched")

    def test_suffix(self):
        self.assertEqual(self.m.match(listing("2A Carluke Crescent")).building_id, 11)
        self.assertEqual(self.m.match(listing("2 Carluke Crescent")).method, "unmatched")

    def test_duplicate_registry_rows_are_ambiguous(self):
        r = self.m.match(listing("44 Alexander Street"))
        self.assertEqual((r.method, r.candidates), ("ambiguous", (8, 9)))

    def test_coordinates_break_an_address_tie(self):
        r = self.m.match(listing("10 Kendleton Drive", 43.75201, -79.55000))
        self.assertEqual((r.building_id, r.method), (13, "address"))
        self.assertIn("tie broken", r.detail)

    def test_coordinates_too_close_to_break_a_tie(self):
        r = self.m.match(listing("44 Alexander Street", 43.66500, -79.380005))
        self.assertEqual(r.method, "ambiguous")


class Distance(unittest.TestCase):
    def setUp(self):
        self.m = match.Matcher(BUILDINGS)

    def test_same_street_within_40m(self):
        # 99 Davisville is not a building here; 33 m from 77 Davisville on the same street.
        r = self.m.match(listing("99 Davisville Ave", 43.69830, -79.39500))
        self.assertEqual((r.building_id, r.method), (10, "distance"))
        self.assertEqual(r.confidence, match.CONFIDENCE[("distance", "street")])

    def test_same_street_beyond_40m(self):
        r = self.m.match(listing("99 Davisville Ave", 43.69840, -79.39500))   # 44 m
        self.assertEqual(r.method, "unmatched")

    def test_any_street_within_15m(self):
        r = self.m.match(listing("Davisville Village Residences", 43.69810, -79.39500))   # 11 m
        self.assertEqual((r.building_id, r.method, r.confidence), (10, "distance", 0.60))

    def test_any_street_beyond_15m(self):
        r = self.m.match(listing("Davisville Village Residences", 43.69815, -79.39500))   # 17 m
        self.assertEqual(r.method, "unmatched")

    def test_two_buildings_within_tie_margin(self):
        r = self.m.match(listing("The Corner Building", 43.700025, -79.30000))
        self.assertEqual((r.method, r.candidates), ("ambiguous", (14, 15)))

    def test_no_address_no_coordinates(self):
        r = self.m.match(listing("Davisville Village Residences"))
        self.assertEqual(r.method, "unmatched")
        self.assertIn("no coordinates", r.detail)

    def test_distance(self):
        self.assertAlmostEqual(match.distance_m(43.0, -79.0, 43.0001, -79.0), 11.12, places=1)


class Report(unittest.TestCase):
    def test_counts_each_outcome(self):
        m = match.Matcher(BUILDINGS)
        results = [m.match(listing(a, *xy)) for a, *xy in (
            ("183 Gerrard St E",), ("77 Davisville",), ("Somewhere", 43.69810, -79.39500),
            ("1 Nowhere Lane",), ("100 High Park",))]
        self.assertEqual(match.report(results), {"address": 2, "distance": 1, "unmatched": 1, "ambiguous": 1})


class Loading(unittest.TestCase):
    def test_from_buildings_json(self):
        doc = {"buildings": [{"id": 4153437, "addr": "181-183 Gerrard St E", "lat": 43.66123,
                              "lon": -79.37012, "units": 22, "type": "private"}]}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "buildings.json"
            p.write_text(json.dumps(doc))
            bs = B.load(buildings_json=p, registry_dir=Path(d) / "absent")
        self.assertEqual(len(bs), 1)
        r = match.Matcher(bs).match(listing("183 Gerrard Street East"))
        self.assertEqual(r.building_id, 4153437)

    @unittest.skipUnless((REGISTRY / "registration.csv").exists(), "registry file not present")
    def test_from_registry_csv(self):
        bs = B.load(buildings_json=Path("/nonexistent/buildings.json"))
        self.assertEqual(len(bs), 3611)
        self.assertEqual(B.private_units(bs), 260335)
        m = match.Matcher(bs)
        self.assertEqual(m.match(listing("181 Gerrard Street East, Toronto, ON M5A 2E4")).building_id, 4153437)
        self.assertEqual(m.match(listing("Suite 4 - 355 St. Clair Avenue West")).building_id, 4153342)

    @unittest.skipUnless((REGISTRY / "registration.csv").exists(), "registry file not present")
    def test_json_plus_registry_keeps_unlocated_buildings(self):
        doc = {"buildings": [{"id": 4153342, "addr": "355 St Clair Ave W", "lat": 43.68389,
                              "lon": -79.41279, "units": 168, "type": "private"}]}
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "buildings.json"
            p.write_text(json.dumps(doc))
            bs = B.load(buildings_json=p)
        self.assertEqual(len(bs), 3611)
        self.assertEqual(B.private_units(bs), 260335)


if __name__ == "__main__":
    unittest.main()
