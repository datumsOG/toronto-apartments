"""RPMS, Residential Property Management Services (rpmsi.ca): a small WordPress site in the
CAPREIT family, ten property pages.

Each page has the address under the heading, a map point, and one table row per suite
type: name, price and an availability phrase. One listing per row with a price whose
phrase starts with "Available", rent as a floor.
"""
import html
import re
from datetime import datetime, timezone

from collect.adapter import Adapter
from collect.address import split_street_numbers
from collect.model import Listing, ListingError

SITEMAP = "https://www.rpmsi.ca/property-sitemap.xml"
_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>")
_HEAD = re.compile(r'<div class="header-left">\s*<h1>.*?</h1>\s*<p>(.*?)</p>', re.S)
_MAP = re.compile(r'data-latitude="(-?\d+\.\d+)"\s+data-longitude="(-?\d+\.\d+)"')
_TABLE = re.compile(r'<table class="[^"]*property-table[^"]*">(.*?)</table>', re.S)
_ROW = re.compile(r"<tr>\s*<td>\s*<strong>(.*?)</strong>.*?<span class=\"price\">(.*?)</span>\s*"
                  r"<span class=\"availability\">(.*?)</span>", re.S)
_TAG = re.compile(r"<[^>]+>")
_TORONTO = {"toronto", "north york", "scarborough", "etobicoke", "east york", "york"}


def _text(fragment):
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def property_urls(raw):
    seen, out = set(), []
    for u in _LOC.findall(raw.decode("utf-8", "replace")):
        if "/property/" in u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


class RpmsAdapter(Adapter):
    id = "rpms"
    name = "RPMS"
    tier = 1
    hosts = ("www.rpmsi.ca",)
    start_urls = (SITEMAP,)

    def __init__(self, seen=None):
        self.seen = seen

    def fetch(self, fetcher):
        for url in property_urls(fetcher.get(SITEMAP)):
            yield fetcher.get(url), url

    def sites(self, raw, url):
        page = raw.decode("utf-8", "replace")
        m = _HEAD.search(page)
        parts = [p.strip() for p in _text(m.group(1)).split(",")] if m else []
        if len(parts) < 2 or not re.match(r"\d", parts[0]) or parts[1].lower() not in _TORONTO:
            return []
        many = split_street_numbers(parts[0])
        pt = _MAP.search(page) if len(many) == 1 else None
        return [(f"{a}, {parts[1]}", pt.group(1) if pt else None, pt.group(2) if pt else None) for a in many]

    def parse(self, raw, url):
        page = raw.decode("utf-8", "replace")
        sites, table = self.sites(raw, url), _TABLE.search(page)
        if not sites or not table:
            return []
        site = sites[0]
        seen = self.seen or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for name, price, avail in _ROW.findall(table.group(1)):
            name, price, avail = _text(name), _text(price), _text(avail)
            bed = re.search(r"(\d+)\s*bed", name, re.I)
            beds = int(bed.group(1)) if bed else (0 if re.search(r"bachelor|studio", name, re.I) else None)
            if beds is None or not avail.lower().startswith("available") or not re.search(r"\d", price):
                continue
            try:
                out.append(Listing.from_dict({
                    "source": self.id, "source_url": url, "seen": seen,
                    "address": site[0], "lat": site[1], "lon": site[2],
                    "unit_label": name, "beds": beds, "rent": price, "rent_is_floor": True,
                }))
            except ListingError:
                continue
        return out
