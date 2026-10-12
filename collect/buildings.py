"""Registry buildings for matching and coverage.

Reads app/data/buildings.json (contract section 1) when the build has written it. Otherwise
reads data/registry/registration.csv directly, taking coordinates from the newest
evaluations.csv row per building when there is one. Either way every building gets an
address key, so matching works from both.
"""
import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from collect import address

ROOT = Path(__file__).resolve().parent.parent
BUILDINGS_JSON = ROOT / "app" / "data" / "buildings.json"
REGISTRY = ROOT / "data" / "registry"

TYPES = {"PRIVATE": "private", "TCHC": "tchc", "SOCIAL HOUSING": "social"}


@dataclass
class Building:
    id: int
    addr: str
    units: int
    type: str | None
    lat: float | None = None
    lon: float | None = None
    key: address.AddressKey | None = field(default=None, repr=False)


def _keyed(b):
    try:
        b.key = address.parse(b.addr)
    except address.AddressError:
        b.key = None
    return b


def _num(s, conv):
    try:
        return conv(s)
    except (TypeError, ValueError):
        return None


def from_registry(registry_dir=REGISTRY):
    coords = {}
    evals = Path(registry_dir) / "evaluations.csv"
    if evals.exists():
        with open(evals, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                lat, lon = _num(r.get("LATITUDE"), float), _num(r.get("LONGITUDE"), float)
                if lat is None or lon is None:
                    continue
                year = _num(r.get("YEAR EVALUATED"), int) or 0
                rsn = _num(r.get("RSN"), int)
                if rsn is not None and year >= coords.get(rsn, (-1,))[0]:
                    coords[rsn] = (year, lat, lon)
    out = []
    with open(Path(registry_dir) / "registration.csv", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rsn = int(r["RSN"])
            c = coords.get(rsn)
            out.append(_keyed(Building(
                id=rsn,
                addr=" ".join(r["SITE_ADDRESS"].split()),
                units=_num(r["CONFIRMED_UNITS"], int) or 0,
                type=TYPES.get(r["PROPERTY_TYPE"].strip()),
                lat=c[1] if c else None,
                lon=c[2] if c else None,
            )))
    return out


def from_json(path=BUILDINGS_JSON):
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    return [_keyed(Building(id=int(b["id"]), addr=b["addr"], units=int(b.get("units") or 0),
                            type=b.get("type"), lat=b.get("lat"), lon=b.get("lon")))
            for b in doc["buildings"]]


def load(buildings_json=BUILDINGS_JSON, registry_dir=REGISTRY):
    """buildings.json when present, plus any registry building it leaves out (the build drops
    unlocated buildings, but they can still match by address and still count for coverage).
    Falls back to the registry alone."""
    reg_csv = Path(registry_dir) / "registration.csv"
    if not Path(buildings_json).exists():
        return from_registry(registry_dir)
    out = from_json(buildings_json)
    if reg_csv.exists():
        have = {b.id for b in out}
        out += [b for b in from_registry(registry_dir) if b.id not in have]
    return out


def private_units(buildings):
    return sum(b.units for b in buildings if b.type == "private")
