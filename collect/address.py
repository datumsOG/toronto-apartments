"""Normalise a Toronto street address into a key that registry and landlord spellings share.

Registry:  "181-183  GERRARD ST E", "2 A  CARLUKE CRES", "355  ST CLAIR AVE W"
Landlord:  "181 Gerrard Street East, Toronto, ON M5A 2E4", "2A Carluke Crescent",
           "Suite 1204 - 355 St. Clair Avenue West"

parse() returns an AddressKey; relation() says whether a listing key falls on a building key.
"""
import re
from dataclasses import dataclass

# A listing number pair "a-b" with b > a is read as a range only when it spans at most this
# many street numbers. The widest range in the registry is 182-250 Brenyon Way (68).
# Anything wider, or b <= a ("1204-77 Davisville"), is read as unit-then-street-number.
MAX_RANGE_SPAN = 70

STREET_TYPES = {
    "AVE": "AVE", "AV": "AVE", "AVENUE": "AVE",
    "RD": "RD", "ROAD": "RD",
    "ST": "ST", "STREET": "ST",
    "DR": "DR", "DRIVE": "DR",
    "BLVD": "BLVD", "BLV": "BLVD", "BOULEVARD": "BLVD",
    "CRT": "CRT", "CT": "CRT", "COURT": "CRT",
    "CRES": "CRES", "CRESC": "CRES", "CRESCENT": "CRES",
    "PL": "PL", "PLACE": "PL",
    "PKWY": "PKWY", "PKY": "PKWY", "PARKWAY": "PKWY",
    "WAY": "WAY",
    "HTS": "HTS", "HEIGHTS": "HTS",
    "GDNS": "GDNS", "GARDENS": "GDNS",
    "CRCT": "CRCT", "CIRCUIT": "CRCT",
    "TER": "TER", "TERR": "TER", "TERRACE": "TER",
    "LANE": "LANE", "LN": "LANE",
    "CRCL": "CRCL", "CIR": "CRCL", "CIRCLE": "CRCL",
    "GRV": "GRV", "GROVE": "GRV",
    "GT": "GT", "GATE": "GT",
    "SQ": "SQ", "SQUARE": "SQ",
    "TRL": "TRL", "TRAIL": "TRL",
    "WALK": "WALK",
    "LINE": "LINE",
}

DIRECTIONS = {"E": "E", "EAST": "E", "W": "W", "WEST": "W",
              "N": "N", "NORTH": "N", "S": "S", "SOUTH": "S"}

# Trailing place words a landlord page appends. Stripped from the end only.
PLACE_TAIL = {"TORONTO", "ON", "ONT", "ONTARIO", "CANADA", "CA", "ETOBICOKE",
              "SCARBOROUGH", "NORTH YORK", "EAST YORK", "YORK"}

ORDINAL_UNITS = {"FIRST": 1, "SECOND": 2, "THIRD": 3, "FOURTH": 4, "FIFTH": 5, "SIXTH": 6,
                 "SEVENTH": 7, "EIGHTH": 8, "NINTH": 9, "TENTH": 10, "ELEVENTH": 11,
                 "TWELFTH": 12, "THIRTEENTH": 13, "FOURTEENTH": 14, "FIFTEENTH": 15,
                 "SIXTEENTH": 16, "SEVENTEENTH": 17, "EIGHTEENTH": 18, "NINETEENTH": 19,
                 "TWENTIETH": 20, "THIRTIETH": 30, "FORTIETH": 40, "FIFTIETH": 50}
ORDINAL_TENS = {"TWENTY": 20, "THIRTY": 30, "FORTY": 40, "FIFTY": 50}

POSTAL_RE = re.compile(r"\b[A-Z]\d[A-Z]\s*\d[A-Z]\d\b")
UNIT_RE = re.compile(r"\b(?:UNIT|APT|APARTMENT|SUITE|STE(?=\s*#?\s*\d)|PH(?=\s*#?\s*\d))\s*#?\s*[A-Z]?\d+[A-Z]?\b"
                     r"|#\s*[A-Z]?\d+[A-Z]?\b")
NUMBER_RE = re.compile(r"^(\d+)\s*([A-Z])?(?:\s*-\s*(\d+)\s*([A-Z])?)?(?=\s)")
NUM_ORDINAL_RE = re.compile(r"^(\d+)(?:ST|ND|RD|TH)$")


class AddressError(ValueError):
    pass


@dataclass(frozen=True)
class AddressKey:
    lo: int
    hi: int            # equal to lo for a single number
    suffix: str | None  # "A" in "2A Carluke Cres"
    street: str        # "SAINT CLAIR", "25", "WEST MALL"
    type: str | None   # "AVE"; None when the street has no type ("THE KINGSWAY")
    direction: str | None

    @property
    def street_key(self):
        """The name with spaces dropped, so "LAKE SHORE" and "LAKESHORE" agree."""
        return self.street.replace(" ", "")

    @property
    def is_range(self):
        return self.hi != self.lo

    def __str__(self):
        num = f"{self.lo}-{self.hi}" if self.is_range else f"{self.lo}{self.suffix or ''}"
        return " ".join(p for p in (num, self.street, self.type, self.direction) if p)


def _ordinals(tokens):
    """TWENTY FIFTH -> 25, 25TH -> 25, FIRST -> 1. Both sides of a match get the same treatment."""
    out, i = [], 0
    while i < len(tokens):
        t = tokens[i]
        m = NUM_ORDINAL_RE.match(t)
        if m:
            out.append(m.group(1))
        elif t in ORDINAL_TENS and i + 1 < len(tokens) and tokens[i + 1] in ORDINAL_UNITS \
                and ORDINAL_UNITS[tokens[i + 1]] < 10:
            out.append(str(ORDINAL_TENS[t] + ORDINAL_UNITS[tokens[i + 1]]))
            i += 1
        elif t in ORDINAL_UNITS:
            out.append(str(ORDINAL_UNITS[t]))
        else:
            out.append(t)
        i += 1
    return out


def _street_segment(text):
    """Pick the comma-separated part that holds the street number."""
    for seg in text.split(","):
        seg = seg.strip().lstrip("-").strip()
        if seg[:1].isdigit():
            return seg
    raise AddressError("no street number")


def parse(address):
    """Return the AddressKey for one address string, or raise AddressError."""
    if not address or not address.strip():
        raise AddressError("empty address")
    s = address.upper().replace("’", "").replace("'", "").replace(".", " ")
    s = s.replace("–", "-").replace("—", "-")
    s = POSTAL_RE.sub(" ", s)
    s = UNIT_RE.sub(" ", s)
    s = re.sub(r"[^A-Z0-9,\- ]", " ", s)
    seg = _street_segment(s) + " "

    m = NUMBER_RE.match(seg)
    if not m:
        raise AddressError(f"no street number in {address!r}")
    a, a_suf, b, b_suf = int(m.group(1)), m.group(2), m.group(3), m.group(4)
    if b is None:
        lo = hi = a
        suffix = a_suf
    elif a < int(b) <= a + MAX_RANGE_SPAN and not a_suf and not b_suf:
        lo, hi, suffix = a, int(b), None
    else:  # "1204-77": a unit number, then the street number
        lo = hi = int(b)
        suffix = b_suf
    tokens = seg[m.end():].replace("-", " ").split()

    # "2 A CARLUKE CRES": a lone letter right after a single number is its suffix.
    if lo == hi and suffix is None and len(tokens) >= 2 and re.fullmatch(r"[A-Z]", tokens[0]):
        suffix = tokens.pop(0)

    # Place tail, only from the end and never eating the whole street.
    while len(tokens) > 1:
        if len(tokens) > 2 and " ".join(tokens[-2:]) in PLACE_TAIL:
            tokens = tokens[:-2]
        elif tokens[-1] in PLACE_TAIL:
            tokens = tokens[:-1]
        else:
            break

    direction = None
    if len(tokens) >= 2 and tokens[-1] in DIRECTIONS:
        direction = DIRECTIONS[tokens.pop()]

    stype = None
    # A trailing type word is the type only if a real name is left in front of it:
    # "AVENUE RD" keeps AVENUE as the name, "THE ESPLANADE" has no type.
    if len(tokens) >= 2 and tokens[-1] in STREET_TYPES and tokens[:-1] != ["THE"]:
        stype = STREET_TYPES[tokens.pop()]

    if len(tokens) >= 2 and tokens[0] == "THE":
        tokens = tokens[1:]
    # With the type gone, an ST or MT still in the name is Saint or Mount.
    tokens = ["SAINT" if t == "ST" else "MOUNT" if t == "MT" else t for t in tokens]
    tokens = _ordinals(tokens)
    if not tokens:
        raise AddressError(f"no street name in {address!r}")
    return AddressKey(lo, hi, suffix, " ".join(tokens), stype, direction)


def _same_side(n, lo, hi):
    """A registry range like 181-183 covers one side of the street; 182 is across the road."""
    if (lo - hi) % 2 == 0:
        return (n - lo) % 2 == 0
    return True


def number_relation(listing, building):
    """'exact', 'range' (inside or overlapping a range) or None."""
    if listing.suffix or building.suffix:
        same = (listing.lo, listing.hi, listing.suffix) == (building.lo, building.hi, building.suffix)
        return "exact" if same else None
    if not listing.is_range and not building.is_range:
        return "exact" if listing.lo == building.lo else None
    if (listing.lo, listing.hi) == (building.lo, building.hi):
        return "exact"
    if not listing.is_range:
        ok = building.lo <= listing.lo <= building.hi and _same_side(listing.lo, building.lo, building.hi)
        return "range" if ok else None
    if not building.is_range:
        ok = listing.lo <= building.lo <= listing.hi and _same_side(building.lo, listing.lo, listing.hi)
        return "range" if ok else None
    return "range" if listing.lo <= building.hi and building.lo <= listing.hi else None


def street_relation(listing, building):
    """'strict' when name, type and direction all agree; 'loose' when the listing left out a
    type or direction the building has; None otherwise. A listing that states a type or
    direction the building does not have never matches."""
    if listing.street_key != building.street_key:
        return None
    loose = False
    for mine, theirs in ((listing.type, building.type), (listing.direction, building.direction)):
        if mine == theirs:
            continue
        if mine is None:
            loose = True
            continue
        return None
    return "loose" if loose else "strict"


def relation(listing, building):
    """(number relation, street relation) when the listing key falls on the building key, else None."""
    st = street_relation(listing, building)
    if st is None:
        return None
    num = number_relation(listing, building)
    if num is None:
        return None
    return num, st


def collisions(addresses):
    """{key: [(id, address), ...]} for keys that two or more registry rows share.
    addresses: iterable of (id, address). Those keys are the ambiguous matches waiting to happen."""
    by_key = {}
    for i, a in addresses:
        by_key.setdefault(parse(a), []).append((i, a))
    return {k: v for k, v in by_key.items() if len(v) > 1}


if __name__ == "__main__":
    import csv
    import sys
    from pathlib import Path

    reg = Path(__file__).resolve().parent.parent / "data" / "registry" / "registration.csv"
    with open(reg, encoding="utf-8-sig") as f:
        rows = [(r["RSN"], " ".join(r["SITE_ADDRESS"].split())) for r in csv.DictReader(f)]
    found = collisions(rows)
    print(f"{len(rows)} registry rows, {len({parse(a) for _, a in rows})} distinct keys, "
          f"{len(found)} keys shared by two or more rows ({sum(len(v) for v in found.values())} rows)")
    for k, v in sorted(found.items(), key=lambda kv: str(kv[0]))[:int(sys.argv[1]) if len(sys.argv) > 1 else 20]:
        print(f"  {k}: " + "; ".join(f"{i} {a}" for i, a in v))


_SEVERAL = re.compile(r"\s*(\d+(?:\s*(?:[/&,]|and)\s*\d+)+)\s+(.*)", re.I)
_CHAIN = re.compile(r"\s*(\d+(?:\s*-\s*\d+){2,})\s+(.*)")          # 14-16-20 Carluke Cres
_SPACED_RANGE = re.compile(r"\s*(\d+)\s+-\s+(\d+)\s+(.*)")          # 390 - 400 Woodsworth Road


def split_street_numbers(street):
    """'15/25 Canyon Ave.', '300 & 330 Winnett Ave.', '5,7 & 9 Stag Hill Drive' and
    '14-16-20 Carluke Cres' are several buildings written as one address: one address per
    number. A range ('2029-2055 Victoria Park Avenue') is left as a range, since it does
    not say which numbers exist; '390 - 400 Woodsworth Road' is the same range written
    with spaces and is returned as '390-400 Woodsworth Road'."""
    m = _SEVERAL.match(street or "") or _CHAIN.match(street or "")
    if m:
        return [f"{n} {m.group(2)}" for n in re.findall(r"\d+", m.group(1))]
    r = _SPACED_RANGE.match(street or "")
    if r:
        return [f"{r.group(1)}-{r.group(2)} {r.group(3)}"]
    return [street]
