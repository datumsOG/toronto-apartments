"""Akelius (rent.akelius.com): its own Angular app, server-rendered.

One page, https://rent.akelius.com/en/search/canada/apartment, carries every unit Akelius
has on offer in Canada as JSON inside <script id="akeliusWebsite-state">: street, city,
coordinates, bedrooms, baths, size, total rent, available-from date and a unit id. One
request, individual units, exact rents. Each unit links to its own detail page.
"""
import json
import re
from datetime import datetime, timezone

from collect.adapter import Adapter
from collect.model import Listing, ListingError

SEARCH = "https://rent.akelius.com/en/search/canada/apartment"
DETAIL = "https://rent.akelius.com/en/search/canada/detail/"
_STATE = re.compile(r'<script id="akeliusWebsite-state"[^>]*>(.*?)</script>', re.S)
_TORONTO = {"toronto", "north york", "scarborough", "etobicoke", "east york", "york"}


def units(raw):
    """The unit records in the page's state block: the one long list of dicts with an address."""
    m = _STATE.search(raw.decode("utf-8", "replace"))
    if not m:
        return []
    try:
        state = json.loads(m.group(1))
    except ValueError:
        return []
    best = []
    for v in state.values():
        rows = v.get("b") if isinstance(v, dict) else None
        if isinstance(rows, list) and rows and isinstance(rows[0], dict) and "address" in rows[0] and len(rows) > len(best):
            best = rows
    return [u for u in best if ((u.get("address") or {}).get("city") or "").strip().lower() in _TORONTO]


class AkeliusAdapter(Adapter):
    id = "akelius"
    name = "Akelius"
    tier = 1
    hosts = ("rent.akelius.com",)
    start_urls = (SEARCH,)

    def __init__(self, seen=None):
        self.seen = seen

    def sites(self, raw, url):
        seen, out = set(), []
        for u in units(raw):
            a = u["address"]
            key = (a.get("streetName"), a.get("city"))
            if a.get("streetName") and key not in seen:
                seen.add(key)
                out.append((f"{a['streetName']}, {a['city']}", a.get("latitude"), a.get("longitude")))
        return out

    def parse(self, raw, url):
        seen = self.seen or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for u in units(raw):
            a, k = u.get("address") or {}, u.get("keyfacts") or {}
            when = (k.get("available-from-date") or "")[:10]
            try:
                out.append(Listing.from_dict({
                    "source": self.id, "source_url": DETAIL + str(u.get("id")), "seen": seen,
                    "address": f"{a.get('streetName')}, {a.get('city')}",
                    "lat": a.get("latitude"), "lon": a.get("longitude"),
                    "unit_label": str(u.get("id")) if u.get("id") else None,
                    "beds": k.get("number-of-bedrooms"), "baths": k.get("number-of-bathrooms") or None,
                    "sqft": k.get("unit-size") or None, "rent": k.get("total-rent"),
                    "available": when if re.fullmatch(r"\d{4}-\d{2}-\d{2}", when) and not k.get("is-available-from-now-on") else None,
                    "rent_is_floor": False,
                }))
            except ListingError:
                continue
        return out
