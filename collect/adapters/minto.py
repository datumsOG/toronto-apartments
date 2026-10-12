"""Minto Apartments (mintoapartments.com): its own CMS.

The GTA projects page links every community; the Toronto ones sit under
/gta/<Toronto|Downtown-Toronto|North-York|Etobicoke|Scarborough|East-York>-apartment-rentals/.
A community page marks its address and coordinates with schema.org microdata and has one
block per suite type: a heading ("Studio Suites", "One Bedroom Suites"), an availability
label and a price or price range. One listing per block that says "Available", with the
low end of the range as a floor.

robots.txt on this host answers with status 404 and a body that names a sitemap and
disallows nothing (read by hand 2026-10-10).
"""
import html
import re
from datetime import datetime, timezone

from collect.adapter import Adapter
from collect.address import split_street_numbers
from collect.model import Listing, ListingError

PROJECTS = "https://www.mintoapartments.com/gta/apartment-rentals/projects.html"
_LINK = re.compile(r'href="(https://www\.mintoapartments\.com/gta/(?:Downtown-Toronto|Toronto|North-York|Etobicoke|Scarborough|East-York|York)'
                   r'-apartment-rentals/[^"/]+/main\.html)"')
_STREET = re.compile(r'itemprop="streetAddress"[^>]*>(.*?)</span>', re.S)
_CITY = re.compile(r'itemprop="addressLocality"[^>]*>(.*?)</span>', re.S)
_GEO = re.compile(r'itemprop="latitude">\s*(-?\d+\.\d+)\s*</span>\s*<span itemprop="[Ll]ongitude">\s*(-?\d+\.\d+)')
# The heading is plain text and nothing between it and its price may open another
# heading: a suite type that is "Not available" has no price, and must not borrow the next one's.
_BLOCK = re.compile(r'<h4 class="[^"]*h-h3-minto[^"]*">([^<]{1,300})</h4>((?:(?!<h4).){0,900}?)'
                    r'<h5 class="[^"]*h-h3-minto[^"]*">([^<]{1,300})</h5>', re.S)
_TAG = re.compile(r"<[^>]+>")
_MONEY = re.compile(r"\$\s*([\d,]+)")
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}


def _text(fragment):
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def community_urls(raw):
    seen, out = set(), []
    for u in _LINK.findall(raw.decode("utf-8", "replace")):
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def beds_of(title):
    t = title.lower()
    if re.search(r"studio|bachelor", t):
        return 0
    m = re.search(r"(\d+)\s*[- ]?bed", t) or re.search(r"\b(one|two|three|four|five)\b[- ]bed", t)
    if not m:
        return None
    return int(m.group(1)) if m.group(1).isdigit() else _WORDS[m.group(1)]


class MintoAdapter(Adapter):
    id = "minto"
    name = "Minto"
    tier = 1
    hosts = ("www.mintoapartments.com",)
    start_urls = (PROJECTS,)

    def __init__(self, seen=None):
        self.seen = seen

    def fetch(self, fetcher):
        for url in community_urls(fetcher.get(PROJECTS)):
            yield fetcher.get(url), url

    def sites(self, raw, url):
        page = raw.decode("utf-8", "replace")
        st, city = _STREET.search(page), _CITY.search(page)
        if not st or not re.match(r"\d", _text(st.group(1))):
            return []
        # Some pages put the whole line in the street field: "61 Yorkville Ave., Toronto, ON".
        many = split_street_numbers(_text(st.group(1)).split(",")[0].strip())
        geo = _GEO.search(page) if len(many) == 1 else None
        where = _text(city.group(1)).split(",")[0].strip().title() if city else "Toronto"
        return [(f"{a}, {where}", geo.group(1) if geo else None, geo.group(2) if geo else None) for a in many]

    def parse(self, raw, url):
        page = raw.decode("utf-8", "replace")
        sites = self.sites(raw, url)
        if not sites:
            return []
        site = sites[0]
        seen = self.seen or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for title, between, price in _BLOCK.findall(page):
            title, money = _text(title), _MONEY.findall(_text(price))
            beds = beds_of(title)
            label = _text(between).lower()
            if "town" in title.lower():          # townhomes inside a community are out of scope
                continue
            if beds is None or not money or "available" not in label or "not available" in label:
                continue
            try:
                out.append(Listing.from_dict({
                    "source": self.id, "source_url": url, "seen": seen,
                    "address": site[0], "lat": site[1], "lon": site[2],
                    "unit_label": title, "beds": beds, "rent": money[0], "rent_is_floor": True,
                }))
            except ListingError:
                continue
        return out
