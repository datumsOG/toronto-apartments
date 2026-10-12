"""Rentsync "website gateway" sites (the newer Rentsync generation): Sterling Karamar.

The landlord's pages are a JavaScript app that asks
https://website-gateway.rentsync.com/v1/<site>/properties and /units for JSON. Properties
carry street number, street name, postal code and coordinates; units carry bedrooms, baths,
size, rent, an available flag and sometimes a unit number and a date. No token is involved.

Two requests a run (more if the units run past one page of 2,000):
  properties?limit=500                         every building; Toronto ones have an M postal code
  units?where=available~in:1,status~in:enabled every available unit, kept by building afterwards
The filter syntax is the gateway's own, read from the "self" link in its answers.

The host refuses a data-centre address and answers an ordinary home connection, so it is a
home_host. fetch() hands parse() a bundle trimmed to the fields used:
{"properties": [...], "units": [...]}. Descriptions and photos in the answers are dropped
there and never stored.

Homestead is on the same gateway (site id homestead_rebuild) but is read from its own
pages (collect/adapters/homestead.py), which works without the home route.
"""
import json
import re
from datetime import datetime, timezone

from collect.adapter import Adapter, FetchError
from collect.model import Listing, ListingError

HOST = "website-gateway.rentsync.com"
_P_KEEP = ("id", "permaLink", "streetNumber", "streetName", "postal", "latitude", "longitude")
_U_KEEP = ("id", "buildingId", "typeName", "number", "bed", "bath", "sqFt", "rate", "rateMin", "rateMax",
           "available", "availabilityDate", "hideRateWebsites")
MAX_PAGES = 6


def _trim(row, keep):
    return {k: row.get(k) for k in keep}


class GatewayAdapter(Adapter):
    tier = 1
    hosts = (HOST,)
    home_hosts = (HOST,)
    site = ""              # the gateway's id for the landlord
    link = ""              # the landlord's page for a building is link + permaLink

    def __init__(self, seen=None):
        self.seen = seen

    def _get(self, fetcher, path):
        doc = json.loads(fetcher.get(f"https://{HOST}/v1/{self.site}/{path}"))
        if not isinstance(doc, dict) or not isinstance(doc.get("data"), list):
            raise FetchError(f"{self.id}: unexpected answer for {path.split('?')[0]}")
        return doc

    def fetch(self, fetcher):
        props = [_trim(p, _P_KEEP) for p in self._get(fetcher, "properties?limit=500")["data"]
                 if (p.get("postal") or "").strip().upper().startswith("M") and p.get("streetNumber") and p.get("streetName")]
        units = []
        # Every available unit the landlord has, all cities, then kept by building here. The
        # gateway answers 500 to a list of building ids (a comma also separates its
        # conditions), so the request stays inside the grammar its own links use.
        mine = {p["id"] for p in props}
        for page in range(1, MAX_PAGES + 1):
            doc = self._get(fetcher, f"units?limit=2000&page={page}&where=available~in:1,status~in:enabled")
            units += [_trim(u, _U_KEEP) for u in doc["data"] if u.get("buildingId") in mine]
            if page >= ((doc.get("meta") or {}).get("totalPages") or 1):
                break
        yield json.dumps({"properties": props, "units": units}).encode(), self.link

    @staticmethod
    def _address(p):
        return f"{str(p['streetNumber']).strip()} {' '.join(str(p['streetName']).split())}, Toronto"

    def sites(self, raw, url):
        return [(self._address(p), p.get("latitude"), p.get("longitude")) for p in json.loads(raw).get("properties") or []]

    def parse(self, raw, url):
        doc = json.loads(raw)
        by_id = {p["id"]: p for p in doc.get("properties") or []}
        seen = self.seen or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for u in doc.get("units") or []:
            p = by_id.get(u.get("buildingId"))
            if not p or not u.get("available") or u.get("hideRateWebsites"):
                continue
            rent = u.get("rateMin") or u.get("rate")
            when = (u.get("availabilityDate") or "")[:10]
            number = (u.get("number") or "").strip()
            try:
                size = float(u.get("sqFt") or 0)
            except (TypeError, ValueError):
                size = 0
            try:
                out.append(Listing.from_dict({
                    "source": self.id, "source_url": self.link + (p.get("permaLink") or ""), "seen": seen,
                    "address": self._address(p), "lat": p.get("latitude"), "lon": p.get("longitude"),
                    "unit_label": (f"Unit {number}" if number else (u.get("typeName") or None)),
                    "beds": u.get("bed"), "baths": u.get("bath") or None,
                    "sqft": size if size >= 100 else None,
                    "rent": rent,
                    "available": when if re.fullmatch(r"\d{4}-\d{2}-\d{2}", when) else None,
                    # a numbered unit has its own rent; a suite type's rent is the lowest of its kind
                    "rent_is_floor": not number or u.get("rateMin") != u.get("rateMax"),
                }))
            except ListingError:
                continue
        return out


class SterlingKaramarAdapter(GatewayAdapter):
    id = "sterling-karamar"
    name = "Sterling Karamar"
    site = "sterlingkaramar"
    link = "https://www.sterlingkaramar.com/apartments-for-rent/"
