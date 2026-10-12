"""CAPREIT (capreit.ca): its own WordPress build.

The property sitemap lists every community page; Toronto ones sit under
/apartments-for-rent/<city>-on/. Each page has one list item per suite type with
data-available, "Starting at $X", the bedroom count and "Up to N Sq Ft". One listing per
available item, rent as a floor.

A community is often several buildings. The page's structured data then carries the
address lines out of place (a second street in addressLocality, and so on), so the lines
are read in page order and every line that starts with a street number is a building.
"""
import html
import json
import re
from datetime import datetime, timezone

from collect.adapter import Adapter
from collect.address import split_street_numbers
from collect.model import Listing, ListingError

SITEMAP = "https://www.capreit.ca/property-sitemap1.xml"
_TORONTO = re.compile(r"/apartments-for-rent/(toronto|north-york|scarborough|etobicoke|east-york|york)-on/[^/]+/?$")
_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>")
_ADDR = re.compile(r'"addressLocality":("(?:[^"\\]|\\.)*"),"streetAddress":("(?:[^"\\]|\\.)*"),'
                   r'"addressRegion":("(?:[^"\\]|\\.)*"),"postalCode":("(?:[^"\\]|\\.)*")')
_MAP = re.compile(r'data-latitude="(-?\d+\.\d+)"\s+data-longitude="(-?\d+\.\d+)"')
_ITEM = re.compile(r'<li class="property-options-list-item"\s+data-available="(\w+)">(.*?)</ul>\s*<ul class="property-options-list-item-cta"', re.S)
_PRICE = re.compile(r'property-options-list-item-price">(.*?)</div>', re.S)
_AVAIL = re.compile(r'property-options-list-item-availability">(.*?)</div>', re.S)
_DETAIL = re.compile(r'<li class="property-options-item">(.*?)</li>', re.S)
_TAG = re.compile(r"<[^>]+>")
_MONEY = re.compile(r"\$\s*([\d,]+)")
_CITY = re.compile(r"^\s*([A-Za-z .'-]+),\s*ON\b")


def _text(fragment):
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def community_urls(raw):
    return [u for u in _LOC.findall(raw.decode("utf-8", "replace")) if _TORONTO.search(u)]


def addresses(page):
    """Every building the page names, as 'number street, city'."""
    m = _ADDR.search(page)
    if not m:
        return []
    locality, street, region, postal = (_text(json.loads(g)) for g in m.groups())
    lines = [street, locality, region, postal]                 # page order
    city = next((c.group(1).strip() for c in map(_CITY.match, lines) if c), None)
    if city is None:
        city = locality if not re.match(r"\d", locality) else "Toronto"
    out = []
    for line in lines:
        if re.match(r"\d+\s*(?:[&,/-]\s*\d+\s*)*[A-Za-z]", line) and not _CITY.match(line):
            out.extend(f"{a}, {city}" for a in split_street_numbers(line))
    return out


class CapreitAdapter(Adapter):
    id = "capreit"
    name = "CAPREIT"
    tier = 1
    hosts = ("www.capreit.ca",)
    start_urls = (SITEMAP,)

    def __init__(self, seen=None):
        self.seen = seen

    def fetch(self, fetcher):
        for url in community_urls(fetcher.get(SITEMAP)):
            yield fetcher.get(url), url

    def sites(self, raw, url):
        page = raw.decode("utf-8", "replace")
        many = addresses(page)
        m = _MAP.search(page) if len(many) == 1 else None      # one coordinate helps one building
        return [(a, m.group(1) if m else None, m.group(2) if m else None) for a in many]

    def parse(self, raw, url):
        page = raw.decode("utf-8", "replace")
        sites = self.sites(raw, url)
        if not sites:
            return []
        site = sites[0]                  # several buildings, one suite list: the first address
        seen = self.seen or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for available, body in _ITEM.findall(page):
            if available != "true":
                continue
            price, details = _PRICE.search(body), [_text(d) for d in _DETAIL.findall(body)]
            money = _MONEY.search(_text(price.group(1))) if price else None
            if not money or not details:
                continue
            kind = details[0]
            bed = re.search(r"(\d+)\s*bed", kind, re.I)
            beds = int(bed.group(1)) if bed else (0 if re.search(r"bachelor|studio", kind, re.I) else None)
            if beds is None:
                continue
            size = next((re.search(r"([\d,]+)\s*sq", d, re.I) for d in details[1:] if re.search(r"sq", d, re.I)), None)
            try:
                out.append(Listing.from_dict({
                    "source": self.id, "source_url": url, "seen": seen,
                    "address": site[0], "lat": site[1], "lon": site[2],
                    "unit_label": kind, "beds": beds, "rent": money.group(1),
                    "sqft": size.group(1) if size else None,
                    "rent_is_floor": True,
                }))
            except ListingError:
                continue
        return out
