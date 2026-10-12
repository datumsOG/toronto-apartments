"""build/buildings.py: the registry -> buildings.json step, on an invented fixture and on
the real committed registry."""
import csv
import io
import json
import math
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "build"))
import buildings as B  # noqa: E402

# Invented buildings. Only the columns the build reads, plus one it ignores.
REG = """RSN,SITE_ADDRESS,CONFIRMED_UNITS,CONFIRMED_STOREYS,YEAR_BUILT,PROPERTY_TYPE,PROP_MANAGEMENT_COMPANY_NAME,WARD,PCODE,PARKING_TYPE,BALCONIES,PETS_ALLOWED,LAUNDRY_ROOM,NON_SMOKING_BUILDING,AIR_CONDITIONING_TYPE,NO_OF_ELEVATORS,VISITOR_PARKING,INTERCOM
9000001,"181-183  GERRARD ST E ",22,4,1930,PRIVATE,"Maple Leaf Rentals Inc.",13,M5A,"Carport , Surface Parking",YES,YES,NO,YES,NONE,0,PAID,YES
9000002,"10  O'CONNOR DR  ",120,12,1965,PRIVATE,"MAPLE LEAF RENTALS, LTD",14,m4k,"Underground Garage , Garage accessible thru buildg",NO,NO,YES,NO,CENTRAL AIR,2,FREE,YES
9000003,"5  2ND ST ",40,3,,TCHC,"TCH",3,,,,,,,,,,
9000004,"7  LOST LANE ",15,3,1950,SOCIAL HOUSING,,5,M6K,,YES,,,,,,,
9000005,"9  GRID AVE ",30,5,1970,,"Birch Property Corp",6,M6M,Surface Parking,YES,YES,YES,YES,INDIVIDUAL UNITS,1,BOTH,YES
9000006,"11  NOWHERE CRES ",12,3,1960,PRIVATE,,7,M3H,,,,,,,,,
"""

EVAL_HEAD = ("_id,RSN,EVALUATION COMPLETED ON,CURRENT BUILDING EVAL SCORE,PROPERTY TYPE,"
             "LATITUDE,LONGITUDE,X,Y\n")
EVAL = EVAL_HEAD + """1,9000001,2023-06-05,71,PRIVATE,43.66123,-79.37012,,
2,9000001,2025-07-01,88.6,PRIVATE,43.66124,-79.37013,,
3,9000002,2024-08-01,93,PRIVATE,43.69,-79.33,,
4,9000003,2026-01-10,80,TCHC,,,,
5,9000005,2025-02-02,77,PRIVATE,,,315000.0,4840000.0
"""


def rows(text):
    return list(csv.DictReader(io.StringIO(text)))


NOW = datetime(2026, 10, 9, 22, 0, tzinfo=timezone.utc)


class Tidy(unittest.TestCase):
    def test_address(self):
        self.assertEqual(B.tidy_address("181-183  GERRARD ST E "), "181-183 Gerrard St E")
        self.assertEqual(B.tidy_address("10  O'CONNOR DR  "), "10 O'Connor Dr")
        self.assertEqual(B.tidy_address("5  2ND ST "), "5 2nd St")
        self.assertEqual(B.tidy_address("12A  MAIN ST W"), "12A Main St W")

    def test_manager_canon(self):
        self.assertEqual(B.canon_manager("Greenwin Corp."), "GREENWIN")
        self.assertEqual(B.canon_manager("MAPLE LEAF RENTALS, LTD"), "MAPLE LEAF RENTALS")
        self.assertEqual(B.canon_manager("  H & R  Property   Management Inc. "), "H R PROPERTY MANAGEMENT")
        self.assertEqual(B.canon_manager("Acme Holdings LP ULC"), "ACME HOLDINGS")
        self.assertEqual(B.canon_manager("D'Arcy Rentals"), "DARCY RENTALS")
        self.assertEqual(B.canon_manager("INC REALTY INC"), "INC REALTY")   # only trailing suffixes go
        self.assertEqual(B.canon_manager(""), "")

    def test_aliases(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "aliases.json"
            p.write_text(json.dumps({"TORONTO COMMUNITY HOUSING": ["TCH", "Toronto Community Housing Corp."]}))
            a = B.load_aliases(p)
            self.assertEqual(B.manager_of("tch", a), "TORONTO COMMUNITY HOUSING")
            self.assertEqual(B.manager_of("Toronto Community Housing Corp", a), "TORONTO COMMUNITY HOUSING")
            self.assertEqual(B.manager_of("Someone Else Ltd", a), "SOMEONE ELSE")
            self.assertEqual(B.load_aliases(Path(d) / "missing.json"), {})


class Fit(unittest.TestCase):
    def grid(self, f, n=12):
        pts = {}
        for i in range(n):
            for j in range(n):
                x, y = 295000 + i * 3000, 4827000 + j * 2200
                pts[str(i * n + j)] = (x, y) + f(x, y)
        return pts

    def test_recovers_affine_map_exactly(self):
        f = lambda x, y: (43.0 + (y - 4.8e6) * 9e-6 + x * 1e-8, -80.0 + x * 1.2e-5 - y * 2e-8)
        r = B.fit_xy(self.grid(f))
        self.assertEqual(r["degree"], 1)
        self.assertLess(r["p95_m"], 0.01)
        lat, lon = r["fit"](310000, 4840000)
        exp = f(310000, 4840000)
        self.assertLess(B.metres(lat, lon, *exp), 0.01)

    def test_goes_quadratic_when_the_map_bends(self):
        f = lambda x, y: (43.0 + (y - 4.8e6) * 9e-6 + ((x - 3.1e5) / 1e4) ** 2 * 2e-4, -80.0 + x * 1.2e-5)
        r = B.fit_xy(self.grid(f))
        self.assertEqual(r["degree"], 2)
        self.assertEqual([d for d, _, _ in r["tried"]], [1, 2])
        self.assertLess(r["p95_m"], 0.01)

    def test_metres(self):
        self.assertAlmostEqual(B.metres(43.0, -79.0, 43.001, -79.0), 111.32, places=1)
        self.assertAlmostEqual(B.metres(43.0, -79.0, 43.0, -79.001), 111.32 * math.cos(math.radians(43)), places=1)


class BuildFixture(unittest.TestCase):
    def setUp(self):
        self.doc, self.unlocated, self.summary = B.build(rows(REG), rows(EVAL), now=NOW, refreshed="2026-07-05")
        self.by_id = {b["id"]: b for b in self.doc["buildings"]}

    def test_header(self):
        self.assertEqual(self.doc["generated"], "2026-10-09T22:00:00Z")
        self.assertEqual(self.doc["registry_refreshed"], "2026-07-05")
        c = self.doc["counts"]
        self.assertEqual(c["buildings"], 6)
        self.assertEqual(c["located"] + c["unlocated"], c["buildings"])
        # 9000005 has no type in the registry; its evaluation says PRIVATE
        self.assertEqual((c["private"], c["tchc"], c["social"]), (4, 1, 1))
        self.assertEqual(c["units"], 22 + 120 + 40 + 15 + 30 + 12)

    def test_latest_evaluation_wins(self):
        b = self.by_id[9000001]
        self.assertEqual(b["score"], 89)          # 88.6, from 2025, not the 2023 71
        self.assertEqual(b["score_year"], 2025)
        self.assertEqual((b["lat"], b["lon"]), (43.66124, -79.37013))

    def test_record_shape(self):
        b = self.by_id[9000001]
        self.assertEqual(b["addr"], "181-183 Gerrard St E")
        self.assertEqual((b["units"], b["storeys"], b["year"], b["type"]), (22, 4, 1930, "private"))
        self.assertEqual((b["ward"], b["pcode"]), (13, "M5A"))
        self.assertEqual(b["parking"], ["carport", "surface"])
        self.assertEqual(b["flags"], {"balconies": True, "pets": True, "laundry": False, "nonsmoking": True,
                                      "ac": "none", "elevators": 0, "visitor_parking": "paid"})
        b2 = self.by_id[9000002]
        self.assertEqual(b2["pcode"], "M4K")
        self.assertEqual(b2["parking"], ["underground", "indoor_access"])
        self.assertEqual(b2["flags"]["ac"], "central")

    def test_one_manager_after_canon(self):
        m = self.doc["managers"]
        self.assertEqual(m[self.by_id[9000001]["mgr"]], "MAPLE LEAF RENTALS")
        self.assertEqual(self.by_id[9000001]["mgr"], self.by_id[9000002]["mgr"])
        self.assertEqual(m[0], "MAPLE LEAF RENTALS")    # most units first

    def test_unknowns_are_omitted_but_mgr_and_score_stay(self):
        for b in self.doc["buildings"]:
            self.assertIn("mgr", b)
            self.assertIn("score", b)
            self.assertNotIn(None, [v for k, v in b.items() if k not in ("mgr", "score")])
        self.assertTrue(all("score_year" not in b for b in self.doc["buildings"] if b["score"] is None))

    def test_unlocated(self):
        reasons = {r["id"]: r["reason"] for r in self.unlocated}
        self.assertEqual(reasons["9000003"], "evaluation has no coordinates")
        self.assertEqual(reasons["9000004"], "never evaluated")
        self.assertEqual(reasons["9000005"], "only X/Y, fit not used")   # too few points to fit
        self.assertEqual(reasons["9000006"], "never evaluated")
        self.assertEqual(self.doc["counts"]["unlocated"], 4)

    def test_dumps_round_trips(self):
        self.assertEqual(json.loads(B.dumps(self.doc)), self.doc)


class RealBuild(unittest.TestCase):
    """Runs the build on the committed registry into a temporary folder."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        out, unl = Path(cls.tmp.name) / "b.json", Path(cls.tmp.name) / "u.csv"
        cls.printed = io.StringIO()
        stdout, sys.stdout = sys.stdout, cls.printed
        try:
            B.main(["--out", str(out), "--unlocated", str(unl)])
        finally:
            sys.stdout = stdout
        cls.doc = json.loads(out.read_text(encoding="utf-8"))
        with open(unl, encoding="utf-8") as f:
            cls.unl = list(csv.DictReader(f))
        with open(B.REGISTRATION, encoding="utf-8-sig") as f:
            cls.reg = list(csv.DictReader(f))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_counts_add_up(self):
        c, bl = self.doc["counts"], self.doc["buildings"]
        self.assertEqual(c["buildings"], len(self.reg))
        self.assertEqual(c["located"], len(bl))
        self.assertEqual(c["unlocated"], len(self.unl))
        self.assertEqual(c["located"] + c["unlocated"], c["buildings"])
        self.assertLessEqual(c["private"] + c["tchc"] + c["social"], c["buildings"])
        self.assertEqual(c["units"], sum(int(r["CONFIRMED_UNITS"]) for r in self.reg))
        ids = [b["id"] for b in bl] + [int(r["id"]) for r in self.unl]
        self.assertEqual(sorted(ids), sorted(int(r["RSN"]) for r in self.reg))

    def test_every_building_is_in_toronto(self):
        for b in self.doc["buildings"]:
            self.assertTrue(43.55 < b["lat"] < 43.87 and -79.66 < b["lon"] < -79.10, b)
            self.assertIn(b["type"], ("private", "tchc", "social"))
            self.assertTrue(b["mgr"] is None or 0 <= b["mgr"] < len(self.doc["managers"]))
            self.assertTrue(b["score"] is None or 0 <= b["score"] <= 100)

    def test_summary_is_printed(self):
        text = self.printed.getvalue()
        for word in ("Registry buildings", "Located", "Unlocated", "Units", "X/Y fit", "KB"):
            self.assertIn(word, text)


if __name__ == "__main__":
    unittest.main()
