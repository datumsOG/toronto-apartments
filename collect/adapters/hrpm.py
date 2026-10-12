"""H&R Property Management (hrpm.ca): its own WordPress build.

The search results page links every building page. A building page gives the address
("1407 Royal York Road, Etobicoke") and a TablePress table with one row per suite type:
"Suite Type" and "Rent From", where the rent cell is a price or "Waiting List". One listing
per row with a price, rent as a floor. A row that is not a bedroom count ("Penthouse") is
skipped, since the page does not say how many bedrooms it has.
"""
import html
import re
from datetime import datetime, timezone

from collect.adapter import Adapter
from collect.address import split_street_numbers
from collect.model import Listing, ListingError

INDEX = "https://hrpm.ca/search-results/"
_LINK = re.compile(r'href="(https://hrpm\.ca/properties/(?!agents/)[\w.-]+/)"')
_ADDR = re.compile(r'<div class="address">\s*(.*?)\s*<br', re.S)
_ROW = re.compile(r'<tr class="row-\d+">\s*<td class="column-1">(.*?)</td>\s*<td class="column-2">(.*?)</td>', re.S)
_TAG = re.compile(r"<[^>]+>")
_MONEY = re.compile(r"\$\s*([\d,]+)")
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4}
_TORONTO = {"toronto", "north york", "scarborough", "etobicoke", "east york", "york"}


def _text(fragment):
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def beds_of(kind):
    k = kind.lower()
    if re.search(r"bachelor|studio", k):
        return 0
    m = re.search(r"\b(one|two|three|four|\d)\b[\s-]*bed", k)
    if not m:
        return None
    return int(m.group(1)) if m.group(1).isdigit() else _WORDS[m.group(1)]


class HrpmAdapter(Adapter):
    id = "hr-property"
    name = "H&R"
    tier = 1
    hosts = ("hrpm.ca",)
    start_urls = (INDEX,)

    def __init__(self, seen=None):
        self.seen = seen

    def fetch(self, fetcher):
        for url in dict.fromkeys(_LINK.findall(fetcher.get(INDEX).decode("utf-8", "replace"))):
            yield fetcher.get(url), url

    def sites(self, raw, url):
        m = _ADDR.search(raw.decode("utf-8", "replace"))
        parts = [p.strip() for p in _text(m.group(1)).split(",")] if m else []
        if len(parts) < 2 or not re.match(r"\d", parts[0]) or parts[-1].lower() not in _TORONTO:
            return []
        return [(f"{a}, {parts[-1]}", None, None) for a in split_street_numbers(parts[0])]

    def parse(self, raw, url):
        sites = self.sites(raw, url)
        if not sites:
            return []
        seen = self.seen or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for kind, rent in _ROW.findall(raw.decode("utf-8", "replace")):
            kind, money, beds = _text(kind), _MONEY.search(_text(rent)), beds_of(_text(kind))
            if beds is None or not money:
                continue
            try:
                out.append(Listing.from_dict({
                    "source": self.id, "source_url": url, "seen": seen, "address": sites[0][0],
                    "unit_label": kind, "beds": beds, "rent": money.group(1), "rent_is_floor": True,
                }))
            except ListingError:
                continue
        return out
