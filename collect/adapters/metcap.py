"""MetCap Living (metcap.com): its own WordPress build.

The property sitemap lists every building page; the page address carries the street, city
and postal code. Each page has one table with a row per suite type: name, status, beds,
baths, size and "Rent From". One listing per row whose status is Available, rent as a floor.
"""
import html
import re
from datetime import datetime, timezone

from collect.adapter import Adapter
from collect.model import Listing, ListingError

SITEMAP = "https://www.metcap.com/property-sitemap.xml"
_TORONTO = re.compile(r"/property/[^/]*-(toronto|north-york|scarborough|etobicoke|east-york|york)-on-[^/]*/?$")
_LOC = re.compile(r"<loc>\s*(.*?)\s*</loc>")
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_TABLE = re.compile(r'<table class="table-listing[^"]*">(.*?)</table>', re.S)
_TR = re.compile(r"<tr>(.*?)</tr>", re.S)
_TD = re.compile(r'<td data-title="([^"]+)">(.*?)</td>', re.S)
_TAG = re.compile(r"<[^>]+>")
_LATLON = re.compile(r'"(?:latitude|lat)"\s*:\s*"?(-?\d+\.\d+)"?.{0,80}?"(?:longitude|lng|lon)"\s*:\s*"?(-?\d+\.\d+)', re.S)


def _text(fragment):
    return " ".join(html.unescape(_TAG.sub(" ", fragment)).split())


def property_urls(raw):
    return [u for u in _LOC.findall(raw.decode("utf-8", "replace")) if _TORONTO.search(u)]


def _addresses(page):
    """'15 Dundonald Street, Toronto, ON M4Y 1K4 - MetCap Living' -> ['15 Dundonald Street, Toronto'].
    '2700 - 2702 Lawrence Avenue, East, Scarborough, ON …' is two buildings: both ends of the
    range, since MetCap writes a pair of neighbouring buildings this way."""
    m = _TITLE.search(page)
    if not m:
        return []
    parts = [p.strip() for p in _text(m.group(1)).rsplit(" - ", 1)[0].split(",")]
    if len(parts) < 3 or not re.match(r"\d", parts[0]):
        return []
    street, city = " ".join(parts[:-2]), parts[-2]
    rng = re.match(r"(\d+)\s*-\s*(\d+)\s+(.*)", street)
    streets = [f"{rng.group(1)} {rng.group(3)}", f"{rng.group(2)} {rng.group(3)}"] if rng else [street]
    return [f"{st}, {city}" for st in streets]


class MetcapAdapter(Adapter):
    id = "metcap"
    name = "MetCap Living"
    tier = 1
    hosts = ("www.metcap.com",)
    start_urls = (SITEMAP,)

    def __init__(self, seen=None):
        self.seen = seen

    def fetch(self, fetcher):
        for url in property_urls(fetcher.get(SITEMAP)):
            yield fetcher.get(url), url

    def sites(self, raw, url):
        page = raw.decode("utf-8", "replace")
        many = _addresses(page)
        m = _LATLON.search(page) if len(many) == 1 else None      # one coordinate helps one building
        return [(a, m.group(1) if m else None, m.group(2) if m else None) for a in many]

    def parse(self, raw, url):
        page = raw.decode("utf-8", "replace")
        sites = self.sites(raw, url)
        table = _TABLE.search(page)
        if not sites or not table:
            return []
        site = sites[0]                  # a page for two buildings: vacancies go to the first
        seen = self.seen or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for tr in _TR.findall(table.group(1)):
            cell = {k: _text(v) for k, v in _TD.findall(tr)}
            if cell.get("Status", "").lower() != "available":
                continue
            beds = cell.get("Beds", "")
            if re.search(r"bach|studio", beds + " " + cell.get("Unit", ""), re.I) and not re.match(r"\d", beds):
                beds = "0"
            try:
                out.append(Listing.from_dict({
                    "source": self.id, "source_url": url, "seen": seen,
                    "address": site[0], "lat": site[1], "lon": site[2],
                    "unit_label": cell.get("Unit") or None,
                    "beds": beds, "baths": cell.get("Baths") or None,
                    "sqft": cell.get("Sq. Ft") if re.match(r"\d", cell.get("Sq. Ft", "")) else None,
                    "rent": cell.get("Rent From", ""),
                    "rent_is_floor": True,
                }))
            except ListingError:
                continue
        return out
