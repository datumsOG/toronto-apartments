"""QuadReal Residential (quadrealresidential.com): WordPress in front of RentCafe data.

The Toronto page links each building. A building's own page gives its address in the
footer block ("address-sec-footer"); its /floorplans/ page has one card per floorplan with
bedrooms, baths, size and a price ("Starting at $X", "From $X to $Y"). The floorplans page
also lists nearby communities with THEIR addresses, so the address is never read from it.
fetch() hands parse() a bundle of the two: {"addresses": [...], "page": "<floorplans html>"}.
One listing per priced card, the low end as a floor.

robots.txt asks for 10 seconds between requests; the fetcher waits it. Nine buildings, two
pages each, so the run takes about three minutes.
"""
import html
import json
import re
from datetime import datetime, timezone

from collect.adapter import Adapter
from collect.address import split_street_numbers
from collect.model import Listing, ListingError

CITY_URL = "https://quadrealresidential.com/toronto/"
_LINK = re.compile(r'href="(https://quadrealresidential\.com/toronto/[\w-]+/)"')
_CARD = re.compile(r'class="other-info"[^>]*>\s*<h3[^>]*>(.*?)</h3>(.*?)<p class="price-range">(.*?)</p>', re.S)
_P = re.compile(r'<p class="(bedrooms|bathrooms|sq-ft-range)">(.*?)</p>', re.S)
_TAG = re.compile(r"<[^>]+>")
_MONEY = re.compile(r"\$\s*([\d,]+)")
_FOOT = re.compile(r'class="address-sec-footer">(.*?)</div>', re.S)
_ADDR = re.compile(r"(\d{1,5}(?:\s*[&,/-]\s*\d{1,5})*\s+[A-Za-z][A-Za-z .'-]{2,40}?)\s*,?\s+(Toronto|North York|Scarborough|Etobicoke|East York),?\s*ON\b")


def _text(fragment):
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def building_urls(raw):
    seen, out = set(), []
    for u in _LINK.findall(raw.decode("utf-8", "replace")):
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def page_addresses(raw):
    """The building's own address(es) from the footer block of its home page."""
    foot = _FOOT.search(raw.decode("utf-8", "replace"))
    m = _ADDR.search(_text(foot.group(1))) if foot else None
    if not m:
        return []
    return [f"{a}, {m.group(2)}" for a in split_street_numbers(m.group(1).strip())]


class QuadrealAdapter(Adapter):
    id = "quadreal"
    name = "QuadReal"
    tier = 1
    hosts = ("quadrealresidential.com",)
    start_urls = (CITY_URL,)

    def __init__(self, seen=None):
        self.seen = seen

    def fetch(self, fetcher):
        for url in building_urls(fetcher.get(CITY_URL)):
            addresses = page_addresses(fetcher.get(url))
            page = fetcher.get(url + "floorplans/").decode("utf-8", "replace")
            yield json.dumps({"addresses": addresses, "page": page}).encode(), url + "floorplans/"

    def sites(self, raw, url):
        return [(a, None, None) for a in json.loads(raw).get("addresses") or []]

    def parse(self, raw, url):
        page = json.loads(raw).get("page") or ""
        sites = self.sites(raw, url)
        if not sites:
            return []
        seen = self.seen or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for name, body, price in _CARD.findall(page):
            cell = {k: _text(v) for k, v in _P.findall(body)}
            money = _MONEY.findall(_text(price))
            bed = re.search(r"(\d+)\s*bed", cell.get("bedrooms", ""), re.I)
            beds = int(bed.group(1)) if bed else (0 if re.search(r"studio|bachelor", cell.get("bedrooms", ""), re.I) else None)
            if beds is None or not money:
                continue
            bath = re.search(r"([\d.]+)", cell.get("bathrooms", ""))
            size = re.search(r"([\d,]+)", cell.get("sq-ft-range", ""))
            try:
                out.append(Listing.from_dict({
                    "source": self.id, "source_url": url, "seen": seen,
                    "address": sites[0][0],
                    "unit_label": _text(name) or None, "beds": beds,
                    "baths": bath.group(1) if bath else None,
                    "sqft": size.group(1) if size else None,
                    "rent": money[0], "rent_is_floor": True,
                }))
            except ListingError:
                continue
        return out
