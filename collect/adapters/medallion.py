"""Medallion (medallioncorp.com): its own WordPress build.

Every page embeds `var propertiesMapData = [...]`: all of Medallion's properties with a
title (the street address), coordinates, a link, and for some a price block such as
"$1,755" and "1,2 Bedrooms". One request gives the lot.

What that is and is not: a building-level "from" price and the bedroom counts on offer.
There are no suite rows, sizes or dates on this site. A building with a price becomes ONE
listing: the lowest bedroom count named, the price as a floor, and the label saying which
bedroom counts are on offer. A building with no price is taken as nothing available.
"""
import html
import json
import re
from datetime import datetime, timezone

from collect.adapter import Adapter
from collect.address import split_street_numbers
from collect.model import Listing, ListingError

INDEX = "https://medallioncorp.com/property/"
_DATA = re.compile(r"var propertiesMapData = (\[.*?\]);", re.S)
_TAG = re.compile(r"<[^>]+>")
_MONEY = re.compile(r"\$\s*([\d,]+)")
# "1,2 Bedrooms": single digits with commas, and never the tail of the price before them.
_BEDS = re.compile(r"(?<![\d,])((?:\d\s*,\s*)*\d)\s*bedroom", re.I)
# Medallion also manages buildings outside Toronto; the city's box keeps the rest out early.
_BOX = (43.58, 43.86, -79.64, -79.11)


def _text(fragment):
    return " ".join(html.unescape(_TAG.sub(" ", fragment or "")).split())


def properties(raw):
    m = _DATA.search(raw.decode("utf-8", "replace"))
    if not m:
        return []
    try:
        rows = json.loads(m.group(1))
    except ValueError:
        return []
    out = []
    for r in rows:
        try:
            lat, lon = float(r.get("lat")), float(r.get("lng"))
        except (TypeError, ValueError):
            continue
        title = _text(r.get("title"))
        if re.match(r"\d", title) and _BOX[0] <= lat <= _BOX[1] and _BOX[2] <= lon <= _BOX[3]:
            out.append({"title": title, "lat": lat, "lon": lon, "url": r.get("url") or INDEX, "price": _text(r.get("price"))})
    return out


class MedallionAdapter(Adapter):
    id = "medallion"
    name = "Medallion"
    tier = 1
    hosts = ("medallioncorp.com",)
    start_urls = (INDEX,)

    def __init__(self, seen=None):
        self.seen = seen

    def sites(self, raw, url):
        out = []
        for p in properties(raw):
            many = split_street_numbers(p["title"].rstrip("."))
            geo = (p["lat"], p["lon"]) if len(many) == 1 else (None, None)
            out.extend((f"{a}, Toronto", *geo) for a in many)
        return out

    def parse(self, raw, url):
        seen = self.seen or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for p in properties(raw):
            money, beds = _MONEY.search(p["price"]), _BEDS.search(p["price"])
            if not money:
                continue
            counts = sorted(int(n) for n in re.findall(r"\d+", beds.group(1))) if beds else []
            label = (", ".join(str(n) for n in counts) + " bedroom") if counts else None
            many = split_street_numbers(p["title"].rstrip("."))
            try:
                out.append(Listing.from_dict({
                    "source": self.id, "source_url": p["url"], "seen": seen,
                    "address": f"{many[0]}, Toronto",
                    "lat": p["lat"] if len(many) == 1 else None, "lon": p["lon"] if len(many) == 1 else None,
                    "unit_label": label, "beds": counts[0] if counts else 1,
                    "rent": money.group(1), "rent_is_floor": True,
                }))
            except ListingError:
                continue
        return out
