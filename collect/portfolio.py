"""Which registry buildings each source has a page for -> collect/sources/portfolio.json.

    python3 -m collect.portfolio               # fetch (politely, cached) and write
    python3 -m collect.portfolio --cache-only  # replay cached pages

A building with no vacancy today produces no listing, so the run alone cannot tell that
the source covers it. An adapter that defines sites(raw, url) -> [(address, lat, lon)]
says which buildings its pages are about; each is matched to the registry here. Coverage
(collect/coverage.py) reads the file this writes.
"""
import argparse
import json
import os
import re
import sys
from types import SimpleNamespace

from collect import adapters as adapters_mod
from collect import buildings as buildings_mod
from collect import coverage, match
from collect.adapter import FetchError, Fetcher


_RANGE = re.compile(r"\s*\d+\s*-\s*\d+\s")


def sites_for(adapter, fetcher):
    out = []
    for raw, url in adapter.fetch(fetcher):
        out.extend((a, lat, lon, url) for a, lat, lon in adapter.sites(raw, url))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m collect.portfolio", description=__doc__.split("\n")[0])
    ap.add_argument("--cache-only", action="store_true")
    ap.add_argument("--max-age", type=float, default=86400, help="reuse a cached page younger than this many seconds")
    ap.add_argument("--out", default=str(coverage.PORTFOLIO))
    ap.add_argument("--proxy", default=os.environ.get("APARTMENTS_PROXY"), help="the home route, for an adapter's home_hosts")
    args = ap.parse_args(argv)

    blds = buildings_mod.load()
    by_id = {b.id: b for b in blds}
    matcher = match.Matcher(blds)
    previous = coverage.load_portfolio(args.out)
    doc = {}
    for a in adapters_mod.registered():
        if not hasattr(a, "sites"):
            continue
        fetcher = Fetcher(cache_only=args.cache_only, max_age=args.max_age, home_proxy=args.proxy,
                          home_hosts=getattr(a, "home_hosts", ()),
                          **({"min_interval": a.min_interval} if getattr(a, "min_interval", None) else {}))
        ids, missed = set(), []
        try:
            sites = sites_for(a, fetcher)
        except FetchError as e:
            # A source that cannot be read today keeps the buildings it was known to cover.
            doc[a.id] = sorted(previous.get(a.id, ()))
            print(f"{a.id}: not read ({str(e)[-80:]}); keeping {len(doc[a.id])} buildings from before")
            continue
        for addr, lat, lon, url in sites:
            m = matcher.match(SimpleNamespace(address=addr, lat=float(lat) if lat else None,
                                              lon=float(lon) if lon else None))
            if m.matched:
                ids.add(m.building_id)
            elif m.candidates and "address candidates" in m.detail and _RANGE.match(addr):
                # "32-51 Brookwell Drive" names every registry building in that range: for
                # the question "which buildings does this page cover", all of them count.
                ids.update(m.candidates)
            else:
                missed.append((addr, url, m.detail))
        doc[a.id] = sorted(ids)
        units = sum(by_id[i].units for i in ids)
        print(f"{a.id}: {len(sites)} addresses on its pages, {len(ids)} registry buildings matched "
              f"({units:,} units), {len(missed)} not matched")
        for addr, url, why in missed:
            print(f"  not matched: {addr}  ({why})  {url}")
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
