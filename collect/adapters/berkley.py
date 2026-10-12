"""Berkley Property Management (berkleypm.ca): an older Rentsync theme with no data address.

Each city page (/cities/<city>/) lists its buildings as cards: a map point, the street
address, "rent from $X" and a bedroom range ("1-2 bedrooms"). That is a building-level
"from" price, with no suite rows, sizes or dates. A card with a price becomes ONE listing:
the lowest bedroom count in the range, the price as a floor, the range as the label.
"""
import html
import re
from datetime import datetime, timezone

from collect.adapter import Adapter
from collect.address import split_street_numbers
from collect.model import Listing, ListingError

CITIES = {"toronto": "Toronto", "etobicoke": "Etobicoke", "scarborough": "Scarborough", "north-york": "North York"}
_CARD = re.compile(r'data-latitude="(-?[\d.]*)"\s+data-longitude="(-?[\d.]*)"(.*?)<span class="address">(.*?)</span>(.*?)</div>', re.S)
_RENT = re.compile(r'<span class="value">\s*\$?\s*([\d,]+)')
_BEDS = re.compile(r'<span class="bedrooms">\s*(.*?)\s*</span>', re.S)
_TAG = re.compile(r"<[^>]+>")


def _text(fragment):
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def cards(raw, city):
    out = []
    for lat, lon, _, addr, rest in _CARD.findall(raw.decode("utf-8", "replace")):
        street = _text(addr)
        if not re.match(r"\d", street):
            continue
        rent, beds = _RENT.search(rest), _BEDS.search(rest)
        out.append({"street": street, "city": city, "lat": lat or None, "lon": lon or None,
                    "rent": rent.group(1) if rent else None, "beds": _text(beds.group(1)) if beds else ""})
    return out


class BerkleyAdapter(Adapter):
    id = "berkley"
    name = "Berkley"
    tier = 1
    hosts = ("www.berkleypm.ca",)
    start_urls = tuple(f"https://www.berkleypm.ca/cities/{c}/" for c in CITIES)

    def __init__(self, seen=None):
        self.seen = seen

    def fetch(self, fetcher):
        from collect.adapter import FetchError
        for url in self.start_urls:
            try:
                yield fetcher.get(url), url
            except FetchError as e:
                if "HTTP 404" not in str(e):        # a city with no buildings has no page
                    raise

    def _cards(self, raw, url):
        return cards(raw, CITIES.get(url.rstrip("/").rsplit("/", 1)[-1], "Toronto"))

    def sites(self, raw, url):
        out = []
        for c in self._cards(raw, url):
            many = split_street_numbers(c["street"])
            geo = (c["lat"], c["lon"]) if len(many) == 1 else (None, None)
            out.extend((f"{a}, {c['city']}", *geo) for a in many)
        return out

    def parse(self, raw, url):
        seen = self.seen or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for c in self._cards(raw, url):
            if not c["rent"]:
                continue
            many = split_street_numbers(c["street"])
            counts = [int(n) for n in re.findall(r"\d+", c["beds"])]
            low = 0 if re.search(r"bachelor|studio", c["beds"], re.I) else (min(counts) if counts else None)
            if low is None:
                continue
            try:
                out.append(Listing.from_dict({
                    "source": self.id, "source_url": url, "seen": seen,
                    "address": f"{many[0]}, {c['city']}",
                    "lat": c["lat"] if len(many) == 1 else None, "lon": c["lon"] if len(many) == 1 else None,
                    "unit_label": c["beds"] or None, "beds": low, "rent": c["rent"], "rent_is_floor": True,
                }))
            except ListingError:
                continue
        return out
