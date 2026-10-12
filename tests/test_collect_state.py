"""The state store: ageing, failed runs, known buildings, source states."""
import json
import unittest
from datetime import datetime, timedelta, timezone

from collect import units
from collect.state import State


def U(beds=1, rent=2000, url="https://example.invalid/1", **kw):
    return units.Unit(beds=beds, rent=rent, links=[{"source": "s", "url": url}], **kw)


def ok_run(st, matched, day):
    rid = st.start_run("s", f"{day}T10:00:00Z")
    st.apply_run("s", [(bid, u, "address", 1.0) for bid, u in matched], seen=f"{day}T10:00:00Z")
    st.finish_run(rid, "ok", len(matched), len(matched), finished=f"{day}T10:01:00Z")


def failed_run(st, state, day):
    rid = st.start_run("s", f"{day}T10:00:00Z")
    st.finish_run(rid, state, note="x", finished=f"{day}T10:01:00Z")


class Ageing(unittest.TestCase):
    def setUp(self):
        self.st = State(":memory:")

    def tearDown(self):
        self.st.close()

    def test_first_and_last_seen(self):
        ok_run(self.st, [(1, U())], "2026-10-01")
        ok_run(self.st, [(1, U(rent=2010))], "2026-10-02")
        [row] = self.st.units()
        self.assertEqual((row["first_seen"], row["last_seen"], row["rent"]), ("2026-10-01", "2026-10-02", 2010))

    def test_removed_after_two_successful_misses(self):
        ok_run(self.st, [(1, U()), (1, U(beds=2, rent=3000))], "2026-10-01")
        ok_run(self.st, [(1, U())], "2026-10-02")
        self.assertEqual(len(self.st.units()), 2)          # one miss: still there
        ok_run(self.st, [(1, U())], "2026-10-03")
        self.assertEqual([r["beds"] for r in self.st.units()], [1])

    def test_seen_again_resets_misses(self):
        ok_run(self.st, [(1, U())], "2026-10-01")
        ok_run(self.st, [], "2026-10-02")
        ok_run(self.st, [(1, U())], "2026-10-03")
        ok_run(self.st, [], "2026-10-04")
        self.assertEqual(len(self.st.units()), 1)

    def test_failed_runs_never_age(self):
        ok_run(self.st, [(1, U())], "2026-10-01")
        ok_run(self.st, [], "2026-10-02")
        for i, s in enumerate(("blocked", "broken", "blocked"), 3):
            failed_run(self.st, s, f"2026-10-0{i}")
        self.assertEqual(len(self.st.units()), 1)
        ok_run(self.st, [], "2026-10-06")                     # second successful miss
        self.assertEqual(len(self.st.units()), 0)

    def test_known_outlives_units(self):
        ok_run(self.st, [(1, U())], "2026-10-01")
        ok_run(self.st, [], "2026-10-02")
        ok_run(self.st, [], "2026-10-03")
        self.assertEqual(self.st.units(), [])
        self.assertEqual(self.st.known(), {"s": {1}})

    def test_two_like_units_in_one_building_stay_two(self):
        ok_run(self.st, [(1, U()), (1, U(url="https://example.invalid/2"))], "2026-10-01")
        ok_run(self.st, [(1, U()), (1, U(url="https://example.invalid/2"))], "2026-10-02")
        self.assertEqual(len(self.st.units()), 2)

    def test_links_stored(self):
        ok_run(self.st, [(7, U())], "2026-10-01")
        self.assertEqual(json.loads(self.st.units()[0]["links"]), [{"source": "s", "url": "https://example.invalid/1"}])


class SourceStatus(unittest.TestCase):
    def setUp(self):
        self.st = State(":memory:")

    def tearDown(self):
        self.st.close()

    def test_states(self):
        now = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
        self.assertIsNone(self.st.source_status("s", now))
        ok_run(self.st, [(1, U())], "2026-10-09")
        self.assertEqual(self.st.source_status("s", now)["state"], "ok")
        self.assertEqual(self.st.source_status("s", now + timedelta(hours=49))["state"], "stale")
        failed_run(self.st, "blocked", "2026-10-09")
        st = self.st.source_status("s", now)
        self.assertEqual((st["state"], st["last_ok"]), ("blocked", "2026-10-09T10:01:00Z"))

    def test_wal(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            s = State(os.path.join(d, "state.db"))
            mode = s.db.execute("PRAGMA journal_mode").fetchone()[0]
            s.close()
        self.assertEqual(mode, "wal")


if __name__ == "__main__":
    unittest.main()
