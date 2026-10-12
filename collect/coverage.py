"""Coverage, exactly as docs/CONTRACT.md section 3 defines it.

A building is covered when at least one source with state "ok" is known to list it. A
source is known to list a building when it has ever produced a matched listing there, or
when collect/sources/portfolio.json says the landlord's site has a page for it (so a full
building with no vacancy today still counts). Share is unit-weighted, private buildings only.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO = ROOT / "collect" / "sources" / "portfolio.json"


def load_portfolio(path=PORTFOLIO):
    """{source_id: set(building_id)} from portfolio.json, or {} when the file is absent."""
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    return {str(src): {int(b) for b in ids} for src, ids in doc.items()}


def known_sources(state_known, portfolio):
    """{building_id: set(source_id)}, the union of both ways a source can know a building."""
    out = {}
    for mapping in (state_known, portfolio):
        for src, ids in mapping.items():
            for bid in ids:
                out.setdefault(bid, set()).add(src)
    return out


def compute(buildings, by_building, source_states):
    """buildings: list of Building. by_building: {building_id: set(source_id)}.
    source_states: {source_id: state}. Returns the contract's coverage object."""
    ok = {s for s, st in source_states.items() if st == "ok"}
    private = [b for b in buildings if b.type == "private"]
    units_private = sum(b.units for b in private)
    covered = [b for b in private if by_building.get(b.id, set()) & ok]
    units_covered = sum(b.units for b in covered)
    return {
        "units_private": units_private,
        "units_covered": units_covered,
        "share": round(units_covered / units_private, 4) if units_private else 0.0,
        "buildings_covered": len(covered),
    }
