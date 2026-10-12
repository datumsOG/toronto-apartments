"""Write app/data/availability.json (docs/CONTRACT.md section 3) from the state store."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from collect import coverage, units as units_mod

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "app" / "data" / "availability.json"

# Units from these source states are shown. A blocked or broken source keeps its units in
# the store (they are not aged out) but they are not shown until it recovers.
VISIBLE = ("ok", "stale")


def _merge_rows(rows):
    """Merge one building's stored units across sources into contract units."""
    merged = []
    for r in rows:
        links = json.loads(r["links"])
        for m in merged:
            if units_mod.same_unit(m["_u"], r_unit(r)):
                for link in links:
                    if link not in m["links"]:
                        m["links"].append(link)
                m["first_seen"] = min(m["first_seen"], r["first_seen"])
                m["last_seen"] = max(m["last_seen"], r["last_seen"])
                if r["available"] and (m.get("available") is None or r["available"] < m["available"]):
                    m["available"] = r["available"]
                break
        else:
            merged.append({"_u": r_unit(r), "beds": r["beds"], "baths": r["baths"], "sqft": r["sqft"],
                           "rent": r["rent"], "rent_is_floor": bool(r["rent_is_floor"]),
                           "available": r["available"], "first_seen": r["first_seen"],
                           "last_seen": r["last_seen"], "links": list(links)})
    out = []
    for m in merged:
        m.pop("_u")
        if not m["rent_is_floor"]:
            m.pop("rent_is_floor")
        if isinstance(m["baths"], float) and m["baths"].is_integer():
            m["baths"] = int(m["baths"])
        out.append({k: v for k, v in m.items() if v is not None})
    out.sort(key=lambda u: (u["beds"], u["rent"]))
    return out


def r_unit(r):
    return units_mod.Unit(beds=r["beds"], rent=r["rent"], baths=r["baths"], sqft=r["sqft"])


def build(buildings, state, meta, portfolio, now=None):
    """buildings: list of Building. state: State. meta: {source_id: {"name", "tier"}}.
    portfolio: {source_id: set(building_id)}. Returns the availability document."""
    now = now or datetime.now(timezone.utc)
    statuses = {s: state.source_status(s, now) for s in state.sources()}
    statuses = {s: st for s, st in statuses.items() if st}
    states = {s: st["state"] for s, st in statuses.items()}

    by_building = coverage.known_sources(state.known(), portfolio)
    visible = {s for s, st in states.items() if st in VISIBLE}
    ids = {b.id for b in buildings}

    rows_by_building = {}
    for r in state.units():
        if r["source"] in visible:
            rows_by_building.setdefault(r["building_id"], []).append(r)

    out_buildings = {}
    for bid in sorted(by_building):
        srcs = by_building[bid] & visible
        if not srcs or bid not in ids:
            continue
        out_buildings[str(bid)] = {"sources": sorted(srcs),
                                   "units": _merge_rows(rows_by_building.get(bid, []))}

    sources = []
    for s in sorted(statuses):
        st = statuses[s]
        m = meta.get(s, {})
        sources.append({"id": s, "name": m.get("name", s), "tier": m.get("tier"),
                        "last_ok": st["last_ok"], "last_run": st["last_run"],
                        "listings": st["listings"], "matched": st["matched"], "state": st["state"]})

    return {
        "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "coverage": coverage.compute(buildings, by_building, states),
        "sources": sources,
        "buildings": out_buildings,
    }


def write(doc, path=OUT):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))
    os.replace(tmp, path)
    return path
