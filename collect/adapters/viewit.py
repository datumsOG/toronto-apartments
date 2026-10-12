"""Viewit.ca: a Toronto listing aggregator (see CLAUDE.md, "Aggregators").

Its robots.txt allows listing pages, so this adapter runs under the ordinary rules. The
sitemap lists every active ad, one per suite type per building, as
https://www.viewit.ca/<address>-<City>-<Bach|1bdrm|2bdrm|3bdrm>-VIT=<n>. Being in the
sitemap is what says the ad is live. The ad's page gives the street address, city,
coordinates and the rent ("$2299 And up").

There are about 1,800 Toronto ads. A page is only needed for its rent, which moves slowly,
so a cached copy is reused for 5 to 8 days (spread by the ad number so they do not all
expire together) and a normal day fetches the new ads and that day's share of the old
ones. The first run takes about an hour at one request per two seconds.

An ad whose page fails that day is skipped; a refusal (403, challenge) stops the source.
"""
import html
import re
import time
from datetime import datetime, timezone

from collect.address import split_street_numbers
from collect.adapter import Adapter, Blocked, FetchError
from collect.model import Listing, ListingError

SITEMAP = "https://www.viewit.ca/sitemap.xml"
_LOC = re.compile(r"<loc>\s*(https://www\.viewit\.ca/[^<\s]*-(Toronto|NorthYork|Scarborough|Etobicoke|EastYork|York)-(Bach|\dbdrm)-VIT=(\d+))\s*</loc>")
_ADDR = re.compile(r'id="ctl00_ContentMain_lblAddress"[^>]*>\s*(.*?)\s*<', re.S)
_CITY = re.compile(r'id="ctl00_ContentMain_City" value="([^"]*)"')
_GEO = re.compile(r'lat="(-?\d+\.\d+)" lng="(-?\d+\.\d+)"')
_RENT = re.compile(r'id="ctl00_ContentMain_spanMoney">\s*\$\s*</span>\s*([\d,]+)(.{0,300})', re.S)
DAY = 86400


def ads(raw):
    """[(url, beds, ad number)] for the Toronto ads in the sitemap."""
    out = []
    for url, _, kind, vit in _LOC.findall(raw.decode("utf-8", "replace")):
        out.append((url, 0 if kind == "Bach" else int(kind[0]), int(vit)))
    return out


class ViewitAdapter(Adapter):
    id = "viewit"
    name = "Viewit"
    tier = 1
    aggregator = True
    hosts = ("www.viewit.ca",)
    start_urls = (SITEMAP,)

    def __init__(self, seen=None, now=time.time):
        self.seen, self.now = seen, now

    def _page(self, fetcher, url, vit):
        hit = fetcher.cached(url)
        keep = (5 + vit % 4) * DAY
        if hit and hit[1].get("status") == 200 and self.now() - hit[1].get("fetched_epoch", 0) < keep:
            return hit[0]
        return fetcher.get(url)

    def fetch(self, fetcher):
        for url, beds, vit in ads(fetcher.get(SITEMAP)):
            try:
                yield self._page(fetcher, url, vit), url
            except Blocked:
                raise
            except FetchError:
                continue

    def _site(self, page):
        a, c = _ADDR.search(page), _CITY.search(page)
        street = " ".join(html.unescape(a.group(1)).split()) if a else ""
        if not re.match(r"\d", street):
            return None
        g, city = _GEO.search(page), (html.unescape(c.group(1)).title() if c else "Toronto")
        return [(f"{a}, {city}", g.group(1) if g else None, g.group(2) if g else None) for a in split_street_numbers(street)]

    def sites(self, raw, url):
        many = self._site(raw.decode("utf-8", "replace")) or []
        # One map point for several buildings only helps when there is one.
        return many if len(many) == 1 else [(a, None, None) for a, _, _ in many]

    def parse(self, raw, url):
        page = raw.decode("utf-8", "replace")
        many, rent = self.sites(raw, url), _RENT.search(page)
        site = many[0] if many else None        # an ad for several buildings: the first address
        kind = re.search(r"-(Bach|\dbdrm)-VIT=\d+$", url)
        if not site or not rent or not kind:
            return []
        beds = 0 if kind.group(1) == "Bach" else int(kind.group(1)[0])
        try:
            return [Listing.from_dict({
                "source": self.id, "source_url": url,
                "seen": self.seen or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "address": site[0], "lat": site[1], "lon": site[2],
                "unit_label": "Bachelor" if beds == 0 else f"{beds} bedroom",
                "beds": beds, "rent": rent.group(1),
                "rent_is_floor": "and up" in html.unescape(rent.group(2)).lower(),
            })]
        except ListingError:
            return []
