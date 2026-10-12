"""End to end: demo adapter over the invented fixture, then coverage and export."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collect import buildings as B
from collect import coverage, run
from collect.adapter import Adapter, Blocked
from collect.adapters import registered
from collect.adapters.demo import DemoAdapter
from collect.state import State

NOW = datetime(2026, 10, 9, 22, 0, tzinfo=timezone.utc)


def bld(id, addr, units, type="private", lat=None, lon=None):
    return B._keyed(B.Building(id=id, addr=addr, units=units, type=type, lat=lat, lon=lon))


# Invented units on the real addresses the fixture uses, plus buildings it does not list.
BUILDINGS = [
    bld(4153437, "181-183 GERRARD ST E", 22),
    bld(4171428, "77 DAVISVILLE AVE", 483),
    bld(4153342, "355 ST CLAIR AVE W", 168, lat=43.683892, lon=-79.412785),
    bld(4153766, "25 DAVISVILLE AVE", 14, lat=43.698273, lon=-79.395582),
    bld(900001, "1 FULL BUILDING RD", 200),
    bld(900002, "2 SOCIAL HOUSING RD", 300, type="social"),
    bld(900003, "3 NEVER SEEN RD", 1000),
]
UNITS_PRIVATE = 22 + 483 + 168 + 14 + 200 + 1000


class Stub(Adapter):
    """An adapter whose fetch result each test sets."""
    id, name, tier, hosts = "stub", "Stub", 1, ("stub.invalid",)

    def __init__(self, result):
        self.result = result

    def collect(self, fetcher):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class Run(unittest.TestCase):
    def setUp(self):
        # The home route in the caller's environment must not leak into a test (it did:
        # the tier test failed whenever APARTMENTS_PROXY was exported in the shell).
        self._proxy = os.environ.pop("APARTMENTS_PROXY", None)
        self.addCleanup(lambda: self._proxy is not None and os.environ.__setitem__("APARTMENTS_PROXY", self._proxy))
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.db, self.out, self.portfolio = d / "state.db", d / "availability.json", d / "portfolio.json"

    def tearDown(self):
        self.tmp.cleanup()

    def main(self, *argv, adapters=None, now=NOW):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run.main(["--db", str(self.db), "--out", str(self.out), "--portfolio", str(self.portfolio),
                             *argv], buildings=BUILDINGS, adapter_list=adapters, now=now)
        return code, buf.getvalue()

    def demo(self):
        return [DemoAdapter(seen="2026-10-09T22:00:00Z")]

    def test_demo_is_excluded_from_real_runs(self):
        self.assertNotIn("demo", [a.id for a in registered()])
        self.assertEqual([a.id for a in registered(demo=True)], ["demo"])

    def test_end_to_end(self):
        code, text = self.main(adapters=self.demo())
        self.assertEqual(code, 0)
        doc = json.loads(self.out.read_text())
        self.assertEqual(set(doc["buildings"]), {"4153437", "4171428", "4153342", "4153766"})
        gerrard = doc["buildings"]["4153437"]["units"]
        self.assertEqual(len(gerrard), 1)                       # two listings, one unit, two links
        self.assertEqual(len(gerrard[0]["links"]), 2)
        davis = doc["buildings"]["4171428"]["units"]
        self.assertEqual([(u["beds"], u["rent"]) for u in davis], [(0, 1750), (2, 3100)])
        self.assertTrue(davis[0]["rent_is_floor"])
        self.assertEqual(doc["sources"][0]["state"], "ok")
        self.assertEqual((doc["sources"][0]["listings"], doc["sources"][0]["matched"]), (7, 6))
        cov = doc["coverage"]
        self.assertEqual(cov["units_private"], UNITS_PRIVATE)
        self.assertEqual(cov["units_covered"], 22 + 483 + 168 + 14)
        self.assertEqual(cov["buildings_covered"], 4)
        self.assertIn("demo", text)
        self.assertIn(f"We can see 687 of {UNITS_PRIVATE:,} private units (36.4%), in 4 buildings, as of "
                      "2026-10-09 22:00 UTC", text)
        # Nothing stored but facts and links.
        raw = self.out.read_text().lower()
        for word in ("description", "photo", ".jpg", "renovated"):
            self.assertNotIn(word, raw)

    def test_unmatched_listings_are_recorded_for_audit(self):
        self.main(adapters=self.demo())
        st = State(self.db)
        rows = st.db.execute("SELECT method, address FROM misses").fetchall()
        st.close()
        self.assertEqual([tuple(r) for r in rows], [("unmatched", "1 Nowhere Lane, Toronto")])

    def test_dry_run_writes_nothing(self):
        code, text = self.main("--dry-run", adapters=self.demo())
        self.assertEqual(code, 0)
        self.assertFalse(self.out.exists())
        self.assertFalse(self.db.exists())
        self.assertIn("dry run", text)

    def test_portfolio_covers_full_buildings(self):
        self.portfolio.write_text(json.dumps({"demo": [900001, 900002], "nobody": [900003]}))
        self.main(adapters=self.demo())
        doc = json.loads(self.out.read_text())
        self.assertEqual(doc["buildings"]["900001"], {"sources": ["demo"], "units": []})
        self.assertEqual(doc["buildings"]["900002"]["units"], [])    # shown, but social: not in coverage
        self.assertNotIn("900003", doc["buildings"])                  # its source never ran
        self.assertEqual(doc["coverage"]["units_covered"], 687 + 200)
        self.assertEqual(doc["coverage"]["buildings_covered"], 5)

    def test_blocked_source_is_not_covered_and_keeps_its_units(self):
        self.main(adapters=self.demo())
        blocked = DemoAdapter(seen="2026-10-10T22:00:00Z")
        blocked.collect = lambda fetcher: (_ for _ in ()).throw(Blocked("HTTP 403"))
        code, text = self.main(adapters=[blocked], now=NOW + timedelta(days=1))
        self.assertIn("blocked", text)
        doc = json.loads(self.out.read_text())
        self.assertEqual(doc["sources"][0]["state"], "blocked")
        self.assertEqual(doc["coverage"]["units_covered"], 0)
        self.assertEqual(doc["buildings"], {})
        st = State(self.db)
        self.assertEqual(len(st.units("demo")), 5)                   # nothing aged out
        st.close()

    def test_stale_source_still_shown_but_not_covered(self):
        self.main(adapters=self.demo())
        code, text = self.main(adapters=[], now=NOW + timedelta(hours=49))
        doc = json.loads(self.out.read_text())
        self.assertEqual(doc["sources"][0]["state"], "stale")
        self.assertEqual(len(doc["buildings"]), 4)
        self.assertEqual(doc["coverage"]["units_covered"], 0)

    def test_zero_listings_after_a_good_run_is_broken(self):
        stub = Stub([])
        code, text = self.main(adapters=[stub])
        self.assertIn("found nothing on its first run", text)
        stub.result = DemoAdapter(seen="2026-10-09T22:00:00Z").parse(
            DemoAdapter().fixture.read_bytes(), "https://stub.invalid/")
        self.main(adapters=[stub])
        stub.result = []
        code, text = self.main(adapters=[stub])
        self.assertIn("broken (found nothing where the last good run found 7)", text)

    def test_parse_error_is_broken(self):
        code, text = self.main(adapters=[Stub(ValueError("layout changed"))])
        self.assertIn("broken (ValueError: layout changed)", text)

    def test_tiers_without_means_are_skipped(self):
        two, three = Stub([]), Stub([])
        two.id, two.tier, three.id, three.tier = "two", 2, "three", 3
        code, text = self.main(adapters=[two, three])
        self.assertIn("tier 2 needs --proxy", text)
        self.assertIn("tier 3 needs a real browser", text)

    def test_unknown_source(self):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            code, _ = self.main("--source", "nope", adapters=self.demo())
        self.assertEqual(code, 2)


class Coverage(unittest.TestCase):
    def test_contract_definition(self):
        cov = coverage.compute(BUILDINGS, {4153437: {"a"}, 4171428: {"b"}, 900002: {"a"}},
                               {"a": "ok", "b": "blocked"})
        self.assertEqual(cov, {"units_private": UNITS_PRIVATE, "units_covered": 22,
                               "share": round(22 / UNITS_PRIVATE, 4), "buildings_covered": 1})

    def test_portfolio_optional(self):
        self.assertEqual(coverage.load_portfolio("/nonexistent/portfolio.json"), {})

    def test_sentence(self):
        s = run.coverage_sentence({"units_covered": 1234, "units_private": 260335, "share": 0.0047,
                                   "buildings_covered": 9}, NOW)
        self.assertTrue(s.startswith("We can see 1,234 of 260,335 private units (0.5%), in 9 buildings, "
                                     "as of 2026-10-09 22:00 UTC"))


if __name__ == "__main__":
    unittest.main()
