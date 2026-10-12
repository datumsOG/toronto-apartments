"""What has been seen, where, and when: a small SQLite store (one writer, WAL mode).

units   one row per (source, building, unit) with first_seen / last_seen and a miss count.
known   every (source, building) a source has ever produced a matched listing for.
runs    one row per source run: started, finished, state, counts.
misses  unmatched and ambiguous listings per run, so matching errors can be audited.

Ageing: a successful run marks every unit of its source it did not see as missed once more;
a unit missed by two consecutive successful runs is removed. A blocked or broken run never
ages anything out.
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from collect import units as units_mod

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "collect" / "state.db"

MISSES_TO_REMOVE = 2
STALE_AFTER = timedelta(hours=48)

OK, BLOCKED, BROKEN, STALE, RUNNING = "ok", "blocked", "broken", "stale", "running"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    started TEXT NOT NULL,
    finished TEXT,
    state TEXT NOT NULL,
    listings INTEGER, matched INTEGER, unmatched INTEGER, ambiguous INTEGER,
    note TEXT
);
CREATE INDEX IF NOT EXISTS runs_source ON runs(source, id);
CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    building_id INTEGER NOT NULL,
    beds INTEGER NOT NULL, baths REAL, sqft INTEGER, rent INTEGER NOT NULL,
    rent_is_floor INTEGER NOT NULL DEFAULT 0,
    available TEXT,
    links TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    misses INTEGER NOT NULL DEFAULT 0,
    match_method TEXT, confidence REAL
);
CREATE INDEX IF NOT EXISTS units_source ON units(source, building_id);
CREATE TABLE IF NOT EXISTS known (
    source TEXT NOT NULL,
    building_id INTEGER NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (source, building_id)
);
CREATE TABLE IF NOT EXISTS misses (
    run_id INTEGER NOT NULL,
    method TEXT NOT NULL,
    address TEXT, url TEXT, detail TEXT
);
"""


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class State:
    def __init__(self, path=DB_PATH):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    # runs ------------------------------------------------------------------
    def start_run(self, source, started=None):
        with self.db:
            cur = self.db.execute("INSERT INTO runs (source, started, state) VALUES (?, ?, ?)",
                                  (source, started or now_iso(), RUNNING))
        return cur.lastrowid

    def finish_run(self, run_id, state, listings=0, matched=0, unmatched=0, ambiguous=0,
                   note=None, finished=None):
        with self.db:
            self.db.execute("UPDATE runs SET finished=?, state=?, listings=?, matched=?, unmatched=?, "
                            "ambiguous=?, note=? WHERE id=?",
                            (finished or now_iso(), state, listings, matched, unmatched, ambiguous,
                             note, run_id))

    def last_run(self, source, state=None):
        q = "SELECT * FROM runs WHERE source=? AND state != ?"
        args = [source, RUNNING]
        if state:
            q += " AND state=?"
            args.append(state)
        return self.db.execute(q + " ORDER BY id DESC LIMIT 1", args).fetchone()

    def record_misses(self, run_id, rows):
        """rows: (method, address, url, detail) for each unmatched or ambiguous listing."""
        with self.db:
            self.db.executemany("INSERT INTO misses VALUES (?, ?, ?, ?, ?)",
                                [(run_id,) + tuple(r) for r in rows])

    def sources(self):
        return [r[0] for r in self.db.execute("SELECT DISTINCT source FROM runs ORDER BY source")]

    def source_status(self, source, now=None):
        """{'state', 'last_run', 'last_ok', 'listings', 'matched'} from the run history."""
        last = self.last_run(source)
        if last is None:
            return None
        ok = self.last_run(source, OK)
        state = last["state"]
        if state == OK:
            now = now or datetime.now(timezone.utc)
            if now - _parse_ts(ok["finished"]) > STALE_AFTER:
                state = STALE
        return {"state": state, "last_run": last["finished"],
                "last_ok": ok["finished"] if ok else None,
                "listings": last["listings"] or 0, "matched": last["matched"] or 0}

    # units -----------------------------------------------------------------
    def apply_run(self, source, matched, seen):
        """Record one successful run. matched: list of (building_id, Unit, method, confidence),
        already merged within each building. seen: the run's timestamp (ISO). Updates the
        units the run saw, inserts new ones, ages out the rest, and notes known buildings."""
        day = seen[:10]
        with self.db:
            existing = {}
            for r in self.db.execute("SELECT * FROM units WHERE source=?", (source,)):
                existing.setdefault(r["building_id"], []).append(r)
            touched = set()
            for bid, u, method, conf in matched:
                row = None
                for r in existing.get(bid, ()):
                    if r["id"] not in touched and units_mod.same_unit(_RowUnit(r), u):
                        row = r
                        break
                links = json.dumps(u.links)
                if row is None:
                    cur = self.db.execute(
                        "INSERT INTO units (source, building_id, beds, baths, sqft, rent, rent_is_floor, "
                        "available, links, first_seen, last_seen, misses, match_method, confidence) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                        (source, bid, u.beds, u.baths, u.sqft, u.rent, int(u.rent_is_floor),
                         u.available, links, day, day, method, conf))
                    touched.add(cur.lastrowid)
                else:
                    self.db.execute(
                        "UPDATE units SET baths=?, sqft=?, rent=?, rent_is_floor=?, available=?, links=?, "
                        "last_seen=?, misses=0, match_method=?, confidence=? WHERE id=?",
                        (u.baths, u.sqft, u.rent, int(u.rent_is_floor), u.available, links, day,
                         method, conf, row["id"]))
                    touched.add(row["id"])
                self.db.execute(
                    "INSERT INTO known VALUES (?, ?, ?, ?) ON CONFLICT(source, building_id) "
                    "DO UPDATE SET last_seen=excluded.last_seen", (source, bid, day, day))
            stale = [r["id"] for rows in existing.values() for r in rows if r["id"] not in touched]
            self.db.executemany("UPDATE units SET misses = misses + 1 WHERE id=?", [(i,) for i in stale])
            self.db.execute("DELETE FROM units WHERE source=? AND misses >= ?", (source, MISSES_TO_REMOVE))

    def units(self, source=None):
        q, args = "SELECT * FROM units", ()
        if source:
            q, args = q + " WHERE source=?", (source,)
        return self.db.execute(q + " ORDER BY building_id, id", args).fetchall()

    def known(self):
        """{source: set(building_id)} for every building a source has ever matched a listing to."""
        out = {}
        for r in self.db.execute("SELECT source, building_id FROM known"):
            out.setdefault(r[0], set()).add(r[1])
        return out


class _RowUnit:
    """Lets units.same_unit compare a stored row with a fresh Unit."""

    def __init__(self, r):
        self.beds, self.baths, self.sqft, self.rent = r["beds"], r["baths"], r["sqft"], r["rent"]
