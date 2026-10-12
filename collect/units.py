"""Decide when two listings in one building are the same unit, and merge their links.

Same unit: same beds; baths equal or either unknown; sqft within SQFT_TOLERANCE or either
unknown; rent within RENT_TOLERANCE. Nothing else is compared (not the unit label, not the
availability date), because the same suite is labelled and dated differently across pages.

What this wrongly merges: two genuinely different suites with the same bed and bath count,
similar size and the same rent, for example two identical 1-bedrooms on different floors at
$2,350. They become one unit with both links. That is acceptable here: the map answers
"is something like this available in this building", and both links stay attached, so
nobody loses a way to reach either suite. The count of available units in a building is a
floor, not an exact figure.
"""
from dataclasses import dataclass, field

SQFT_TOLERANCE = 0.03   # 3%
RENT_TOLERANCE = 0.02   # 2%


def _close(a, b, tol):
    return abs(a - b) <= tol * max(abs(a), abs(b))


def same_unit(a, b):
    """a and b are anything with beds, baths, sqft, rent (Listing or Unit)."""
    if a.beds != b.beds:
        return False
    if a.baths is not None and b.baths is not None and a.baths != b.baths:
        return False
    if a.sqft is not None and b.sqft is not None and not _close(a.sqft, b.sqft, SQFT_TOLERANCE):
        return False
    return _close(a.rent, b.rent, RENT_TOLERANCE)


@dataclass
class Unit:
    beds: int
    rent: int
    baths: float | None = None
    sqft: int | None = None
    available: str | None = None
    rent_is_floor: bool = False
    links: list = field(default_factory=list)   # [{"source": id, "url": url}]

    def absorb(self, other_links, baths=None, sqft=None, available=None):
        for link in other_links:
            if link not in self.links:
                self.links.append(link)
        self.baths = self.baths if self.baths is not None else baths
        self.sqft = self.sqft if self.sqft is not None else sqft
        if available and (self.available is None or available < self.available):
            self.available = available


def merge(listings):
    """Collapse the listings of one building into units. Each listing is compared with the
    first listing of every unit found so far, in order, so the result does not depend on
    chains of near-matches drifting apart."""
    units, firsts = [], []
    for li in listings:
        link = {"source": li.source, "url": li.source_url}
        for u, first in zip(units, firsts):
            if same_unit(first, li):
                u.absorb([link], li.baths, li.sqft, li.available)
                break
        else:
            units.append(Unit(beds=li.beds, rent=li.rent, baths=li.baths, sqft=li.sqft,
                              available=li.available, rent_is_floor=li.rent_is_floor, links=[link]))
            firsts.append(li)
    return units
