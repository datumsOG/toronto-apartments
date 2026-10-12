"""GoldSeal Management (goldsealmanagement.com): its own WordPress build.

The property sitemap lists each community page. A page states its address in text after
the heading ("6 Twenty Fourth Street, Etobicoke, ON M8V 3N4") and has one "apply" button
per suite type carrying data-ff-apartment, data-ff-beds and data-ff-price. One listing per
priced button, rent as a floor. The footer carries GoldSeal's own office address (2828
Bathurst Street), which is never a building.
"""
import html
import re
from datetime import datetime, timezone

from collect.adapter import Adapter
from collect.address import split_street_numbers
from collect.model import Listing, ListingError

SITEMAP = "https://goldsealmanagement.com/property-sitemap.xml"
_LOC = re.compile(r"<loc>\s*(https://goldsealmanagement\.com/property/[\w-]+/)\s*</loc>")
_BTN = re.compile(r'data-ff-property="([^"]*)" data-ff-apartment="([^"]*)" data-ff-beds="([^"]*)" data-ff-price="([^"]*)"')
_STRIP = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.S)
_ADDR = re.compile(r"(\d{1,5}(?:\s*[&,/-]\s*\d{1,5})*\s+[A-Za-z][A-Za-z0-9 .'-]{2,40}?),\s*(Toronto|North York|Scarborough|Etobicoke|East York|York),\s*ON\b")
_OFFICE = "2828 bathurst"


def page_addresses(page):
    text = " ".join(html.unescape(_STRIP.sub(" ", page)).split())
    for m in _ADDR.finditer(text):
        if _OFFICE not in m.group(1).lower():
            return [f"{a}, {m.group(2)}" for a in split_street_numbers(m.group(1).strip())]
    return []


class GoldSealAdapter(Adapter):
    id = "gold-seal"
    name = "GoldSeal"
    tier = 1
    hosts = ("goldsealmanagement.com",)
    start_urls = (SITEMAP,)

    def __init__(self, seen=None):
        self.seen = seen

    def fetch(self, fetcher):
        for url in dict.fromkeys(_LOC.findall(fetcher.get(SITEMAP).decode("utf-8", "replace"))):
            yield fetcher.get(url), url

    def sites(self, raw, url):
        return [(a, None, None) for a in page_addresses(raw.decode("utf-8", "replace"))]

    def parse(self, raw, url):
        page = raw.decode("utf-8", "replace")
        many = page_addresses(page)
        if not many:
            return []
        seen = self.seen or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out, done = [], set()
        for _, kind, beds, price in _BTN.findall(page):
            key = (kind, price)
            if key in done or not re.search(r"\d", price) or not re.fullmatch(r"\d+", beds.strip()):
                continue
            done.add(key)
            if "town" in kind.lower():              # townhomes are out of scope
                continue
            try:
                out.append(Listing.from_dict({
                    "source": self.id, "source_url": url, "seen": seen, "address": many[0],
                    "unit_label": html.unescape(kind) or None, "beds": beds.strip(), "rent": price, "rent_is_floor": True,
                }))
            except ListingError:
                continue
        return out
