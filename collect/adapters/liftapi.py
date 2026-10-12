"""Rentsync "Lift API" sites (the older Rentsync generation): Greenwin, Hazelview.

The landlord's search page asks <api host>/v2/search for a JSON list of its buildings. Each
row has the address, coordinates, and statistics over the suites that match the query. The
API has no per-suite rows, so this adapter asks once per bedroom count with
only_available_suites: the answer is then, per building, the lowest rent, smallest size,
count and earliest date among the AVAILABLE suites with that many bedrooms. One listing per
building per bedroom count, rent as a floor.

The request carries a client id and a token. Both are public: the landlord's own page
script hands them to every visitor. They are read from that script at run time and are
never written into this repository.

The API hosts refuse a data-centre address (Cloudflare 403, seen 2026-10-10) and answer an
ordinary home connection, so they are home_hosts (see collect/adapter.py, Fetcher).

Many buildings have their own website; the row's permalink is the link we give.
"""
import html
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urljoin, urlsplit

from collect.adapter import Adapter, FetchError
from collect.address import split_street_numbers
from collect.model import Listing, ListingError

TORONTO = {"toronto", "north york", "scarborough", "etobicoke", "east york", "york"}
BEDS = (0, 1, 2, 3, 4)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def in_scope(row):
    """A Toronto apartment building. Offices, land, construction sites and townhouse
    communities are in the same list and are not."""
    addr = row.get("address") or {}
    return (addr.get("city") or "").strip().lower() in TORONTO \
        and "apartment" in (row.get("property_type") or "").lower()


def _addresses(row):
    """One address per building the row stands for ('300 & 330 Winnett Ave.' is two)."""
    addr = row.get("address") or {}
    return [f"{a}, {addr.get('city') or 'Toronto'}"
            for a in split_street_numbers(" ".join(html.unescape(addr.get("address") or "").split()))]


def _link(row, fallback):
    url = (row.get("permalink") or "").strip()
    if url.startswith("http://"):
        url = "https://" + url[7:]
    return url if url.startswith("https://") else fallback


API_HOSTS = ("api.theliftsystem.com", "lift-api.rentsync.com")
# The page script names the production address and, right beside it, the token. Three
# shapes seen: ("…/v2/search","TOKEN"), "…/v2/search?auth_token=TOKEN&", and
# "…/v2/search?locale="+a+"&","&auth_token=TOKEN".
_TOKEN = re.compile(r'https://(' + "|".join(re.escape(h) for h in API_HOSTS) + r')/v2/search'
                    r'[^;]{0,40}?(?:auth_token=|",")(\w{16,})')
_SCRIPT = re.compile(r'<script[^>]*src="([^"]*/scripts/main\.js[^"]*)"')


class LiftApiAdapter(Adapter):
    tier = 1
    client_id = None
    home_url = ""          # the landlord's home page; its script names the API host and carries the token
    site_url = ""          # the fallback link for a row with no permalink

    def __init__(self, seen=None):
        self.seen = seen
        self.hosts = (urlsplit(self.home_url).hostname,) + API_HOSTS
        self.home_hosts = API_HOSTS + tuple(getattr(self, "extra_home_hosts", ()))

    def _api(self, fetcher):
        """(api host, token), both read from the landlord's own page script. The script's
        address carries a version stamp, so it is found on the home page first."""
        home = fetcher.get(self.home_url).decode("utf-8", "replace")
        src = _SCRIPT.search(home)
        if not src:
            raise FetchError(f"{self.id}: no page script on {self.home_url}")
        js = fetcher.get(urljoin(self.home_url, src.group(1))).decode("utf-8", "replace")
        m = _TOKEN.search(js)
        if not m:
            raise FetchError(f"{self.id}: no Lift API address and token in the page script")
        return m.group(1), m.group(2)

    def _url(self, api, beds=None):
        host, token = api
        q = f"client_id={self.client_id}&auth_token={token}&limit=500&offset=0"
        if beds is not None:
            q += f"&min_bed={beds}&max_bed={beds}&only_available_suites=true"
        return f"https://{host}/v2/search?{q}"

    def fetch(self, fetcher):
        api = self._api(fetcher)
        url = self._url(api)                        # every building: the portfolio
        yield fetcher.get(url), url
        for beds in BEDS:
            url = self._url(api, beds)
            yield fetcher.get(url), url

    def sites(self, raw, url):
        if "min_bed" in url:
            return []
        out = []
        for row in json.loads(raw):
            if in_scope(row):
                geo, many = row.get("geocode") or {}, _addresses(row)
                # A row for several buildings has one coordinate; it only helps for one.
                lat, lon = (geo.get("latitude"), geo.get("longitude")) if len(many) == 1 else (None, None)
                out.extend((a, lat, lon) for a in many)
        return out

    def parse(self, raw, url):
        q = parse_qs(urlsplit(url).query)
        if "min_bed" not in q:
            return []
        beds = int(q["min_bed"][0])
        seen = self.seen or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for row in json.loads(raw):
            if not in_scope(row) or not (row.get("availability_count") or 0) > 0:
                continue
            suites = (row.get("statistics") or {}).get("suites") or {}
            rent = _num((suites.get("rates") or {}).get("min"))
            if rent < 100:                           # 0 and 1 are "call us" placeholders
                continue
            many = _addresses(row)
            # Several buildings in one row: the vacancy is attached to the first address.
            geo = (row.get("geocode") or {}) if len(many) == 1 else {}
            sqft = _num((suites.get("square_feet") or {}).get("min"))
            when = (row.get("min_availability_date") or "")[:10]
            names = row.get("matched_suite_names") or []
            try:
                out.append(Listing.from_dict({
                    "source": self.id, "source_url": _link(row, self.site_url), "seen": seen,
                    "address": many[0],
                    "lat": geo.get("latitude"), "lon": geo.get("longitude"),
                    "unit_label": ", ".join(names)[:60] or None,
                    "beds": beds, "rent": rent,
                    "sqft": sqft if sqft >= 100 else None,
                    "available": when if re.fullmatch(r"\d{4}-\d{2}-\d{2}", when) else None,
                    "rent_is_floor": True,
                }))
            except ListingError:
                continue
        return out


def _site(id_, name_, client, site, extra_home=()):
    return type(f"{id_.title().replace('-', '')}Adapter", (LiftApiAdapter,), {
        "id": id_, "name": name_, "client_id": client,
        "home_url": site.rstrip("/") + "/", "site_url": site,
        "extra_home_hosts": tuple(extra_home)})


GreenwinAdapter = _site("greenwin", "Greenwin", 2, "https://www.greenwin.ca")
# Hazelview's own site also refuses a data-centre address (403 from the VM, 2026-10-09).
HazelviewAdapter = _site("hazelview", "Hazelview", 497, "https://www.hazelviewproperties.com",
                         extra_home=("www.hazelviewproperties.com",))
BriarlaneAdapter = _site("briarlane", "Briarlane", 470, "https://www.briarlane.ca")
ParkPropertyAdapter = _site("park-property", "Park Property", 65, "https://www.parkproperty.ca")
SignetAdapter = _site("signet", "Signet Group", 84, "https://www.signetgroup.ca")
DmsAdapter = _site("dms", "DMS (Tribe)", 122, "https://www.dmsproperty.com")
RaneeAdapter = _site("ranee", "Ranee", 223, "https://www.ranee.ca")
# Centurion's own site shows a challenge page to a data-centre address and loads normally at home.
CenturionAdapter = _site("centurion", "Centurion", 21, "https://www.cpliving.com", extra_home=("www.cpliving.com",))
LIFT = [GreenwinAdapter, HazelviewAdapter, BriarlaneAdapter, ParkPropertyAdapter, SignetAdapter, DmsAdapter, RaneeAdapter,
        CenturionAdapter]
