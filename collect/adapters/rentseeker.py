"""RentSeeker (rentseeker.ca): a listing aggregator (see CLAUDE.md, "Aggregators").

Its robots.txt allows every public page, so this adapter runs like any other. The
properties sitemap lists a page per building; Toronto ones end "-toronto-<id>" (also
north-york, scarborough, etobicoke, east-york). A building page has a schema.org
ApartmentComplex block (street, city, coordinates) and, in the page's embedded data, a
"units" list: title ("71 Talara Drive — 1 Bedroom"), unit number, baths, size, price in
cents, available date and a slug for the unit's own page.

The "bedrooms" number in that list is unreliable (0 for a "1 Bedroom" title), so bedrooms
are read from the title. A unit with a number is one unit; without, a suite type and its
price is a floor.

Many of these buildings are also read from their landlord's own site. The export merges
units per building across sources, so a building keeps one set of rows.
"""
import json
import re
from datetime import datetime, timezone

from collect.address import split_street_numbers
from collect.adapter import Adapter
from collect.model import Listing, ListingError

SITEMAP = "https://www.rentseeker.ca/sitemap/properties-1.xml"
aggregator_host = "www.rentseeker.ca"
_LOC = re.compile(r"<loc>\s*(https://www\.rentseeker\.ca/building/[\w-]+-(?:toronto|north-york|scarborough|etobicoke|east-york)-\d+)\s*</loc>")
_LD = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_UNITS = re.compile(r'\\"units\\":(\[)')
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4}


def building(page):
    for blk in _LD.findall(page):
        try:
            d = json.loads(blk)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("@type") == "ApartmentComplex":
            return d
    return None


def units(page):
    """The embedded unit list. It sits inside a JavaScript string, so its quotes are escaped."""
    m = _UNITS.search(page)
    if not m:
        return []
    text = page[m.start(1):m.start(1) + 400000].replace('\\"', '"').replace("\\\\", "\\")
    try:
        rows, _ = json.JSONDecoder().raw_decode(text)
    except ValueError:
        return []
    return [r for r in rows if isinstance(r, dict)]


def beds_of(title):
    t = title.lower().split("—")[-1]
    if re.search(r"bachelor|studio", t):
        return 0
    m = re.search(r"\b(one|two|three|four|\d)\b[\s-]*bed", t)
    if not m:
        return None
    return int(m.group(1)) if m.group(1).isdigit() else _WORDS[m.group(1)]


class RentseekerAdapter(Adapter):
    id = "rentseeker"
    name = "RentSeeker"
    tier = 1
    aggregator = True
    hosts = (aggregator_host,)
    start_urls = (SITEMAP,)

    def __init__(self, seen=None):
        self.seen = seen

    def fetch(self, fetcher):
        for url in dict.fromkeys(_LOC.findall(fetcher.get(SITEMAP).decode("utf-8", "replace"))):
            yield fetcher.get(url), url

    def _site(self, page):
        b = building(page)
        addr = (b or {}).get("address") or {}
        street = (addr.get("streetAddress") or "").strip()
        if not street or not re.match(r"\d", street):
            return None
        geo = b.get("geo") or {}
        many = split_street_numbers(" ".join(street.split()))
        lat, lon = (geo.get("latitude"), geo.get("longitude")) if len(many) == 1 else (None, None)
        return [(f"{a}, {addr.get('addressLocality') or 'Toronto'}", lat, lon) for a in many]

    def sites(self, raw, url):
        return self._site(raw.decode("utf-8", "replace")) or []

    def parse(self, raw, url):
        page = raw.decode("utf-8", "replace")
        many = self._site(page)
        if not many:
            return []
        site = many[0]                          # a page for several buildings: the first address
        seen = self.seen or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for u in units(page):
            title = str(u.get("title") or "")
            beds, cents = beds_of(title), u.get("price")
            if beds is None or not isinstance(cents, (int, float)) or cents < 10000:
                continue
            number = str(u.get("unitNumber") or "").strip()
            when = str(u.get("availableDate") or "")[:10]
            kind = title.split("—")[-1].strip()
            try:
                out.append(Listing.from_dict({
                    "source": self.id,
                    "source_url": f"https://{aggregator_host}/listing/{u['slug']}" if u.get("slug") else url,
                    "seen": seen, "address": site[0], "lat": site[1], "lon": site[2],
                    "unit_label": f"Unit {number}" if number else (kind or None),
                    "beds": beds, "baths": u.get("bathrooms") or None,
                    "sqft": u.get("sqft") if isinstance(u.get("sqft"), (int, float)) and u.get("sqft") >= 100 else None,
                    "rent": cents / 100,
                    "available": when if re.fullmatch(r"\d{4}-\d{2}-\d{2}", when) else None,
                    "rent_is_floor": not number,
                }))
            except ListingError:
                continue
        return out
