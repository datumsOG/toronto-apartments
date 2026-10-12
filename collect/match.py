"""Attach a listing to a registry building, saying how and how sure.

Order: address key (exact number, then inside a range), then coordinates (nearest building
within STREET_RADIUS_M on the same street, then within ANY_RADIUS_M on any street). Two
candidates that cannot be told apart are reported as ambiguous, never picked between.
"""
import math
from collections import Counter, defaultdict
from dataclasses import dataclass

from collect import address

# Every threshold the matcher uses. Change them here; tests pin the behaviour around them.
STREET_RADIUS_M = 40.0   # nearest building on the same street within this distance
ANY_RADIUS_M = 15.0      # nearest building on any street within this distance
TIE_MARGIN_M = 5.0       # two buildings whose distances differ by less than this are a tie

CONFIDENCE = {
    ("address", "exact", "strict"): 1.00,   # same number, street, type, direction
    ("address", "range", "strict"): 0.95,   # number inside the registry's range
    ("address", "exact", "loose"): 0.85,    # listing left out the type or direction
    ("address", "range", "loose"): 0.80,
    ("distance", "street"): 0.75,           # coordinates, same street name
    ("distance", "any"): 0.60,              # coordinates only
}

BY_ADDRESS, BY_DISTANCE, UNMATCHED, AMBIGUOUS = "address", "distance", "unmatched", "ambiguous"


@dataclass
class Match:
    building_id: int | None
    method: str            # address | distance | unmatched | ambiguous
    confidence: float
    detail: str            # e.g. "exact/strict", "street 12.3 m", "2 candidates: 1, 2"
    candidates: tuple = ()

    @property
    def matched(self):
        return self.building_id is not None


def distance_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class Matcher:
    def __init__(self, buildings):
        self.buildings = list(buildings)
        self.by_street = defaultdict(list)
        for b in self.buildings:
            if b.key is not None:
                self.by_street[b.key.street_key].append(b)
        self.located = [b for b in self.buildings if b.lat is not None and b.lon is not None]

    def _by_address(self, key):
        # Best tier wins: exact before range, strict before loose. Within the best tier,
        # one building is a match and more than one is ambiguous.
        tiers = defaultdict(list)
        for b in self.by_street.get(key.street_key, ()):
            rel = address.relation(key, b.key)
            if rel:
                tiers[rel].append(b)
        for rel in (("exact", "strict"), ("range", "strict"), ("exact", "loose"), ("range", "loose")):
            if rel in tiers:
                return rel, tiers[rel]
        return None, []

    def _nearest(self, lat, lon, pool, radius):
        near = sorted(((distance_m(lat, lon, b.lat, b.lon), b) for b in pool
                       if b.lat is not None and b.lon is not None), key=lambda x: x[0])
        near = [(d, b) for d, b in near if d <= radius]
        if not near:
            return None, []
        ties = [b for d, b in near if d - near[0][0] < TIE_MARGIN_M]
        return near[0][0], ties

    def match(self, listing):
        try:
            key = address.parse(listing.address)
        except address.AddressError:
            key = None
        has_xy = listing.lat is not None and listing.lon is not None

        if key is not None:
            rel, cands = self._by_address(key)
            if len(cands) == 1:
                return Match(cands[0].id, BY_ADDRESS, CONFIDENCE[("address",) + rel], "/".join(rel))
            if len(cands) > 1:
                # Coordinates may break the tie among the address candidates, never widen it.
                if has_xy:
                    d, ties = self._nearest(listing.lat, listing.lon, cands, STREET_RADIUS_M)
                    if len(ties) == 1:
                        return Match(ties[0].id, BY_ADDRESS, CONFIDENCE[("address",) + rel] - 0.1,
                                     f"{'/'.join(rel)}, tie broken at {d:.1f} m")
                ids = tuple(sorted(b.id for b in cands))
                return Match(None, AMBIGUOUS, 0.0, f"{len(ids)} address candidates: "
                             + ", ".join(map(str, ids)), ids)

        if has_xy:
            if key is not None:
                pool = self.by_street.get(key.street_key, ())
                d, ties = self._nearest(listing.lat, listing.lon, pool, STREET_RADIUS_M)
                if len(ties) == 1:
                    return Match(ties[0].id, BY_DISTANCE, CONFIDENCE[("distance", "street")],
                                 f"same street, {d:.1f} m")
                if len(ties) > 1:
                    ids = tuple(sorted(b.id for b in ties))
                    return Match(None, AMBIGUOUS, 0.0, f"{len(ids)} same-street buildings within "
                                 f"{TIE_MARGIN_M:g} m of each other: " + ", ".join(map(str, ids)), ids)
            d, ties = self._nearest(listing.lat, listing.lon, self.located, ANY_RADIUS_M)
            if len(ties) == 1:
                return Match(ties[0].id, BY_DISTANCE, CONFIDENCE[("distance", "any")], f"any street, {d:.1f} m")
            if len(ties) > 1:
                ids = tuple(sorted(b.id for b in ties))
                return Match(None, AMBIGUOUS, 0.0, f"{len(ids)} buildings within "
                             f"{TIE_MARGIN_M:g} m of each other: " + ", ".join(map(str, ids)), ids)

        why = "address did not parse" if key is None else f"no building at {key}"
        return Match(None, UNMATCHED, 0.0, why + ("" if has_xy else ", no coordinates"))


def report(matches):
    """Counts of each outcome: {'address': n, 'distance': n, 'unmatched': n, 'ambiguous': n}."""
    c = Counter(m.method for m in matches)
    return {k: c.get(k, 0) for k in (BY_ADDRESS, BY_DISTANCE, UNMATCHED, AMBIGUOUS)}
