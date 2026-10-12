"""PadMapper (padmapper.com, the same company and data as Zumper): a listing aggregator
(see CLAUDE.md, "Aggregators"). Its robots.txt allows the pages read here.

https://www.padmapper.com/apartments/toronto-on?page=N carries its results as JSON in
window.__PRELOADED_STATE__ (currentSearch.listables.listables), 26 a page. About 2,000 match,
but only the first six pages are served to us (see min_interval below): street address, coordinates, the lowest and highest price, the lowest and highest
bedroom count, and a link. That is a building-level range, so each result becomes ONE
listing: the lowest bedroom count, the lowest price as a floor, and the bedroom range as the
label. Floorplan rows exist on each building's own page (about 2,000 more requests) and are
not read.

Results include condos and small landlords. Anything that does not match a registry
building is dropped by the matcher, as everywhere.
"""
import json
import re
from datetime import datetime, timezone

from collect.adapter import Adapter, Blocked
from collect.model import Listing, ListingError

BASE = "https://www.padmapper.com"
SEARCH = BASE + "/apartments/toronto-on"
_STATE = re.compile(r"window\.__PRELOADED_STATE__\s*=\s*")
MAX_PAGES = 120


def results(raw):
    """(rows, total matching) from one search page."""
    page = raw.decode("utf-8", "replace")
    m = _STATE.search(page)
    if not m:
        return [], 0
    try:
        state, _ = json.JSONDecoder().raw_decode(page[m.end():])
        box = state["currentSearch"]["listables"]
        return [r for r in box.get("listables") or [] if isinstance(r, dict)], int(box.get("matching") or 0)
    except (ValueError, KeyError, TypeError):
        return [], 0


class PadmapperAdapter(Adapter):
    id = "padmapper"
    name = "PadMapper"
    tier = 1
    aggregator = True
    hosts = ("www.padmapper.com",)
    start_urls = (SEARCH,)
    # It serves six pages (about 150 results) and answers 429 from the seventh, however
    # slowly we ask (tried at 2 s and after a 100 s pause, 2026-10-11). That is a limit on
    # depth, and we stay inside it: read until refused, keep what was served.
    min_interval = 5.0

    def __init__(self, seen=None):
        self.seen = seen

    def fetch(self, fetcher):
        got = 0
        for n in range(1, MAX_PAGES + 1):
            url = SEARCH if n == 1 else f"{SEARCH}?page={n}"
            try:
                raw = fetcher.get(url)
            except Blocked:
                if n == 1:
                    raise                   # refused outright: the source is blocked
                break                       # refused past the depth it serves: done
            rows, total = results(raw)
            if not rows:
                break
            yield raw, url
            got += len(rows)
            if got >= total:
                break

    @staticmethod
    def _address(r):
        street = " ".join(str(r.get("address") or "").split())
        return f"{street}, {r.get('city') or 'Toronto'}" if re.match(r"\d", street) else None

    def sites(self, raw, url):
        out = []
        for r in results(raw)[0]:
            a = self._address(r)
            if a:
                out.append((a, r.get("lat"), r.get("lng")))
        return out

    def parse(self, raw, url):
        seen = self.seen or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for r in results(raw)[0]:
            a, lo, hi, rent = self._address(r), r.get("min_bedrooms"), r.get("max_bedrooms"), r.get("min_price")
            if not a or not isinstance(lo, int) or not isinstance(rent, (int, float)) or rent < 300:
                continue
            name = lambda n: "bachelor" if n == 0 else f"{n} bedroom"
            label = name(lo) if hi in (None, lo) else f"{name(lo)} to {name(hi)}"
            link = r.get("url") or ""
            size = r.get("min_square_feet")
            try:
                out.append(Listing.from_dict({
                    "source": self.id, "source_url": BASE + link if link.startswith("/") else SEARCH,
                    "seen": seen, "address": a, "lat": r.get("lat"), "lon": r.get("lng"),
                    "unit_label": label, "beds": lo, "rent": rent,
                    "sqft": size if isinstance(size, (int, float)) and size >= 100 else None,
                    "rent_is_floor": True,
                }))
            except ListingError:
                continue
        return out
