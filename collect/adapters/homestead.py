"""Homestead Land Holdings (homestead.ca): a Rentsync "website gateway" site.

Read from the landlord's own pages, which the server renders with one row per suite type:
type, "Starting From $X", and an availability word. So this adapter emits one listing per
available suite type per building, with the rent as a floor. It does not know how many
units of a type are free.

The individual units (number, size, exact rent, date) come from
website-gateway.rentsync.com, which answered the VM with a Cloudflare refusal on
2026-10-10. Under the collection rule that host may be tried over the home connection;
when it answers, a second fetch path here can replace the suite-type rows.

Pages: the Toronto city page lists every Toronto building page (31 on 2026-10-10).
"""
import html
import json
import re
from datetime import datetime, timezone
from urllib.parse import urljoin

from collect.adapter import Adapter
from collect.address import split_street_numbers as _streets
from collect.model import Listing, ListingError

CITY_URL = "https://www.homestead.ca/residential/cities/toronto"

_LINK = re.compile(r'href="(/residential/(?!cities/)[^"#?]+)"')
_LDJSON = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_ROW = re.compile(r'<ul class="unit-table-homestead__row[^"]*">(.*?)</ul>', re.S)
_CELL = re.compile(r'<li column-field="([^"]+)">(.*?)</li>', re.S)
_TAG = re.compile(r"<[^>]+>")
_MONEY = re.compile(r"\$\s*([\d,]+)")
_BEDS = re.compile(r"(\d+)\s*bed", re.I)
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _text(fragment):
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def building_links(raw, base=CITY_URL):
    """Every building page linked from the city page, in page order, once each."""
    seen, out = set(), []
    for href in _LINK.findall(raw.decode("utf-8", "replace")):
        url = urljoin(base, href)
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


def _complex(page):
    """The page's schema.org ApartmentComplex block: address and, when given, coordinates."""
    for block in _LDJSON.findall(page):
        try:
            doc = json.loads(block)
        except ValueError:
            continue
        for d in (doc if isinstance(doc, list) else [doc]):
            if isinstance(d, dict) and d.get("@type") == "ApartmentComplex":
                return d
    return None


def _beds(type_name):
    m = _BEDS.search(type_name)
    if m:
        return int(m.group(1))
    return 0 if re.search(r"bachelor|studio", type_name, re.I) else None


class HomesteadAdapter(Adapter):
    id = "homestead"
    name = "Homestead"
    tier = 1
    hosts = ("www.homestead.ca",)
    start_urls = (CITY_URL,)

    def __init__(self, seen=None):
        self.seen = seen

    def fetch(self, fetcher):
        city = fetcher.get(CITY_URL)
        for url in building_links(city):
            yield fetcher.get(url), url

    def sites(self, raw, url):
        """[(address, lat, lon)] for the building(s) this page is about, vacancy or not.
        collect/portfolio.py matches these to say which registry buildings the source covers."""
        cx = _complex(raw.decode("utf-8", "replace"))
        addr = (cx or {}).get("address") or {}
        street = (addr.get("streetAddress") or "").strip()
        if not street:
            return []
        geo = cx.get("geo") or {}
        many = _streets(street)
        # A page for several buildings gives one coordinate; it only helps when there is one.
        lat, lon = (geo.get("latitude"), geo.get("longitude")) if len(many) == 1 else (None, None)
        return [(f"{a}, {addr.get('addressLocality') or 'Toronto'}", lat, lon) for a in many]

    def parse(self, raw, url):
        page = raw.decode("utf-8", "replace")
        cx = _complex(page)
        if not cx:
            return []
        addr = cx.get("address") or {}
        street = (addr.get("streetAddress") or "").strip()
        if not street:
            return []
        geo = cx.get("geo") or {}
        many = _streets(street)
        if len(many) > 1:
            # One page, several buildings, one suite table: the page does not say which
            # building a suite is in. Its vacancies are attached to the first address.
            street, geo = many[0], {}
        seen = self.seen or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for row in _ROW.findall(page):
            cell = {k: _text(v) for k, v in _CELL.findall(row)}
            if not cell.get("availability", "").lower().startswith("available"):
                continue                               # "Waiting List" and the like: nothing to rent
            money = _MONEY.search(cell.get("rate", ""))
            beds = _beds(cell.get("typeName", ""))
            if not money or beds is None:
                continue
            when = _DATE.search(cell["availability"])
            try:
                out.append(Listing.from_dict({
                    "source": self.id, "source_url": url, "seen": seen,
                    "address": f"{street}, {addr.get('addressLocality') or 'Toronto'}",
                    "lat": geo.get("latitude"), "lon": geo.get("longitude"),
                    "unit_label": cell.get("typeName") or None,
                    "beds": beds,
                    "rent": money.group(1),
                    "available": when.group(1) if when else None,
                    "rent_is_floor": "from" in cell.get("rate", "").lower(),
                }))
            except ListingError:
                continue
        return out
