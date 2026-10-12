"""Tricon (triconliving.com): WordPress in front, its own JSON address behind.

The Toronto communities page links each building. A building's page carries the street
address in two paragraphs; https://triconliving.com/api/v1/apartments/<slug> answers with
individual units (code, beds, baths, size, rent, availability). The JSON has no address,
so fetch() hands parse() a small bundle of the two: {"addresses": [...], "units": [...]}.
One listing per unit that is Available or Coming Soon. These are real units, not suite types.
"""
import html
import json
import re
from datetime import datetime, timezone

from collect.adapter import Adapter
from collect.model import Listing, ListingError

CITY_URL = "https://triconliving.com/ca/toronto-communities/"
_SLUG = re.compile(r'href="(?:https://triconliving\.com)?/apartment/([\w-]+)/?"')
# A street line followed by "Toronto, Ontario" or "Toronto, ON": in the next paragraph, after
# a line break, or after a comma. Pages differ, and some name several buildings.
_ADDR = re.compile(r'(\d{1,5}[^<>"]{3,40}?)\s*(?:</p>\s*<p[^>]*>|<br\s*/?>|,)\s*'
                   r'(Toronto|North York|Scarborough|Etobicoke|East York),?\s*(?:Ontario|ON)\b')
_OFFICE = "st thomas"        # Tricon's head office, in every page's footer
_OPEN = {"available", "coming soon"}


def slugs(raw):
    seen, out = set(), []
    for s in _SLUG.findall(raw.decode("utf-8", "replace")):
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def page_addresses(raw):
    """Every building address the page states, in page order, once each."""
    seen, out = set(), []
    for m in _ADDR.finditer(raw.decode("utf-8", "replace")):
        street = " ".join(html.unescape(m.group(1)).split()).rstrip(",. ")
        key = re.sub(r"\b(street|avenue)\b", lambda w: {"street": "st", "avenue": "ave"}[w.group(1)], street.lower())
        if street.startswith("0") or _OFFICE in key or key in seen:
            continue
        seen.add(key)
        out.append(f"{street}, {m.group(2)}")
    return out


class TriconAdapter(Adapter):
    id = "tricon"
    name = "Tricon"
    tier = 1
    hosts = ("triconliving.com",)
    start_urls = (CITY_URL,)

    def __init__(self, seen=None):
        self.seen = seen

    def fetch(self, fetcher):
        for slug in slugs(fetcher.get(CITY_URL)):
            url = f"https://triconliving.com/apartment/{slug}/"
            addresses = page_addresses(fetcher.get(url))
            data = json.loads(fetcher.get(f"https://triconliving.com/api/v1/apartments/{slug}"))
            units = data.get("units") if isinstance(data, dict) else None    # some answers are an empty list
            yield json.dumps({"addresses": addresses, "units": units or []}).encode(), url

    def sites(self, raw, url):
        return [(a, None, None) for a in json.loads(raw).get("addresses") or []]

    def parse(self, raw, url):
        doc = json.loads(raw)
        if not doc.get("addresses"):
            return []
        address = doc["addresses"][0]        # several buildings on one page: the first address
        seen = self.seen or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for u in doc.get("units") or []:
            avail = u.get("availability") or {}
            if (avail.get("display") or "").strip().lower() not in _OPEN:
                continue
            when = (avail.get("date") or "")[:10]
            try:
                out.append(Listing.from_dict({
                    "source": self.id, "source_url": url, "seen": seen,
                    "address": address,
                    "unit_label": u.get("unit_code") or None,
                    "beds": u.get("beds"), "baths": u.get("baths") or None,
                    "sqft": u.get("sqft") or None,
                    "rent": u.get("min_rent"),
                    "available": when if re.fullmatch(r"\d{4}-\d{2}-\d{2}", when) else None,
                    "rent_is_floor": u.get("min_rent") != u.get("max_rent"),
                }))
            except ListingError:
                continue
        return out
