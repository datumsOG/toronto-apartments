"""Run the registered adapters, match, update state, export, and print a summary.

    python3 -m collect.run [--source ID] [--dry-run] [--demo] [--cache-only]

--dry-run uses a throwaway in-memory store and writes nothing (no state, no export).
--demo adds the demo adapter over an invented page; it is never part of a real run. Unless
--db and --out are given, a demo run keeps its state in memory and writes its export to
collect/cache/demo-availability.json, so invented data never reaches the real store or the app.
Tier 2 adapters need a proxy URL (--proxy or APARTMENTS_PROXY); tier 3 adapters need a
real browser and are skipped by this core.
"""
import argparse
import fcntl
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from collect import adapters as adapters_mod
from collect import buildings as buildings_mod
from collect import coverage, export, match, units
from collect.adapter import Blocked, Fetcher
from collect.state import BLOCKED, BROKEN, DB_PATH, OK, State

try:
    from zoneinfo import ZoneInfo
    TORONTO = ZoneInfo("America/Toronto")
except Exception:  # no tz database on this machine: print UTC only
    TORONTO = None

DEMO_OUT = Path(__file__).resolve().parent / "cache" / "demo-availability.json"


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def run_source(adapter, fetcher, matcher, state, now=None):
    """One run of one adapter. Returns a summary dict; records the run in state."""
    started = _iso(now or datetime.now(timezone.utc))
    run_id = state.start_run(adapter.id, started)
    out = {"id": adapter.id, "name": adapter.name, "listings": 0, "matched": 0,
           "unmatched": 0, "ambiguous": 0, "state": None, "note": None}
    try:
        listings = adapter.collect(fetcher)
    except Blocked as e:
        out.update(state=BLOCKED, note=str(e))
    except Exception as e:  # any other failure, parse errors included, is "broken"
        out.update(state=BROKEN, note=f"{type(e).__name__}: {e}")
    else:
        if not listings:
            prev = state.last_run(adapter.id, OK)
            if prev is None:
                out.update(state=BROKEN, note="found nothing on its first run")
            elif prev["listings"]:
                out.update(state=BROKEN, note=f"found nothing where the last good run found {prev['listings']}")
    if out["state"]:
        state.finish_run(run_id, out["state"], note=out["note"], finished=_iso(now) if now else None)
        return out

    results = [(li, matcher.match(li)) for li in listings]
    rep = match.report(m for _, m in results)
    by_building = {}
    for li, m in results:
        if m.matched:
            by_building.setdefault(m.building_id, []).append((li, m))
    matched = []
    for bid, pairs in by_building.items():
        weakest = min((m for _, m in pairs), key=lambda m: m.confidence)
        for u in units.merge([li for li, _ in pairs]):
            matched.append((bid, u, f"{weakest.method}: {weakest.detail}", weakest.confidence))
    state.apply_run(adapter.id, matched, seen=started)
    state.record_misses(run_id, [(m.method, li.address, li.source_url, m.detail)
                                 for li, m in results if not m.matched])
    out.update(state=OK, listings=len(listings),
               matched=rep[match.BY_ADDRESS] + rep[match.BY_DISTANCE],
               unmatched=rep[match.UNMATCHED], ambiguous=rep[match.AMBIGUOUS],
               by_address=rep[match.BY_ADDRESS], by_distance=rep[match.BY_DISTANCE])
    state.finish_run(run_id, OK, out["listings"], out["matched"], out["unmatched"], out["ambiguous"],
                     finished=_iso(now) if now else None)
    return out


def coverage_sentence(cov, when):
    stamp = when.strftime("%Y-%m-%d %H:%M UTC")
    if TORONTO is not None:
        stamp += " / " + when.astimezone(TORONTO).strftime("%H:%M Toronto")
    return (f"We can see {cov['units_covered']:,} of {cov['units_private']:,} private units "
            f"({cov['share'] * 100:.1f}%), in {cov['buildings_covered']:,} buildings, as of {stamp}.")


def summary_table(results):
    lines = [f"{'source':<16}{'listings':>9}{'matched':>9}{'unmatched':>10}{'ambiguous':>10}  state"]
    for r in results:
        lines.append(f"{r['id']:<16}{r['listings']:>9}{r['matched']:>9}{r['unmatched']:>10}"
                     f"{r['ambiguous']:>10}  {r['state']}" + (f" ({r['note']})" if r.get("note") else ""))
    return "\n".join(lines)


def main(argv=None, buildings=None, adapter_list=None, now=None):
    ap = argparse.ArgumentParser(prog="python3 -m collect.run", description=__doc__.split("\n")[0])
    ap.add_argument("--source", help="run only this source id")
    ap.add_argument("--dry-run", action="store_true", help="in-memory store, write nothing")
    ap.add_argument("--demo", action="store_true", help="run only the demo adapter (invented data, no network)")
    ap.add_argument("--cache-only", action="store_true", help="replay cached pages, no network")
    ap.add_argument("--proxy", default=os.environ.get("APARTMENTS_PROXY"), help="the home route: proxy URL for tier 2 adapters and for any adapter's home_hosts")
    ap.add_argument("--db", default=None, help=f"state store (default {DB_PATH})")
    ap.add_argument("--out", default=None, help=f"export path (default {export.OUT})")
    ap.add_argument("--portfolio", default=str(coverage.PORTFOLIO))
    args = ap.parse_args(argv)

    if args.db is None:
        args.db = ":memory:" if args.demo else str(DB_PATH)
    if args.out is None:
        args.out = str(DEMO_OUT if args.demo else export.OUT)
    in_memory = args.dry_run or args.db == ":memory:"

    now = now or datetime.now(timezone.utc)
    adapters = adapter_list if adapter_list is not None else adapters_mod.registered(demo=args.demo)
    if args.source:
        adapters = [a for a in adapters if a.id == args.source]
        if not adapters:
            print(f"no adapter with id {args.source!r}"
                  + ("" if args.demo else " (the demo adapter needs --demo)"), file=sys.stderr)
            return 2

    lock = None
    if not in_memory:
        Path(args.db).parent.mkdir(parents=True, exist_ok=True)
        lock = open(args.db + ".lock", "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another collect.run holds the state store; exiting", file=sys.stderr)
            return 3

    blds = buildings if buildings is not None else buildings_mod.load()
    matcher = match.Matcher(blds)
    state = State(":memory:" if in_memory else args.db)
    try:
        results = []
        for a in adapters:
            if a.tier == 3:
                results.append({"id": a.id, "listings": 0, "matched": 0, "unmatched": 0, "ambiguous": 0,
                                "state": "skipped", "note": "tier 3 needs a real browser"})
                continue
            if a.tier == 2 and not args.proxy:
                results.append({"id": a.id, "listings": 0, "matched": 0, "unmatched": 0, "ambiguous": 0,
                                "state": "skipped", "note": "tier 2 needs --proxy"})
                continue
            fetcher = Fetcher(proxy=args.proxy if a.tier == 2 else None, cache_only=args.cache_only,
                              home_proxy=args.proxy, home_hosts=getattr(a, "home_hosts", ()),
                              **({"min_interval": a.min_interval} if getattr(a, "min_interval", None) else {}))
            results.append(run_source(a, fetcher, matcher, state, now))

        meta = {a.id: {"name": a.name, "tier": a.tier} for a in adapters_mod.registered(demo=True)}
        meta.update({a.id: {"name": a.name, "tier": a.tier} for a in adapters})
        doc = export.build(blds, state, meta, coverage.load_portfolio(args.portfolio), now)
        print(summary_table(results) if results else "no adapters registered")
        print()
        print(coverage_sentence(doc["coverage"], now))
        if args.dry_run:
            print("(dry run: nothing written)")
        else:
            path = export.write(doc, args.out)
            print(f"wrote {path}")
    finally:
        state.close()
        if lock:
            lock.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
