"""The listing shape every adapter emits (docs/CONTRACT.md section 2)."""
import re
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime

REQUIRED = ("source", "source_url", "seen", "address", "beds", "rent")

# Facts and links only. A listing that carries any of these is rejected outright, so an
# adapter that starts scraping them fails its tests instead of quietly storing them.
FORBIDDEN = {"description", "desc", "body", "text", "details", "photo", "photos", "image",
             "images", "img", "picture", "pictures", "gallery", "thumbnail", "contact",
             "contact_name", "agent", "phone", "email"}

BACHELOR_WORDS = {"bachelor", "studio", "bach", "jr", "junior"}


class ListingError(ValueError):
    pass


def _int(value, name):
    if isinstance(value, bool):
        raise ListingError(f"{name}: not a number: {value!r}")
    if isinstance(value, (int, float)):
        return int(round(value))
    s = re.sub(r"[$,\s]", "", str(value))
    s = re.sub(r"(?i)(/mo(nth)?|sq\.?ft\.?|sf|ft2)$", "", s)
    try:
        return int(round(float(s)))
    except ValueError:
        raise ListingError(f"{name}: not a number: {value!r}") from None


def _float(value, name):
    if isinstance(value, bool):
        raise ListingError(f"{name}: not a number: {value!r}")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ListingError(f"{name}: not a number: {value!r}") from None


def _beds(value):
    if isinstance(value, str) and value.strip().lower() in BACHELOR_WORDS:
        return 0
    n = _int(value, "beds")
    if not 0 <= n <= 10:
        raise ListingError(f"beds out of range: {value!r}")
    return n


def _seen(value):
    s = str(value)
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise ListingError(f"seen: not an ISO timestamp: {value!r}") from None
    return s


def _available(value):
    s = str(value)[:10]
    try:
        date.fromisoformat(s)
    except ValueError:
        raise ListingError(f"available: not an ISO date: {value!r}") from None
    return s


# A source's bad cell must not travel: one landlord's page carried a size too large for
# the state store (2026-10-11). Outside these bounds a rent rejects the listing and a size
# is dropped.
MAX_RENT = 50000
MAX_SQFT = 20000


def _sqft(value):
    n = _int(value, "sqft")
    return n if 0 < n <= MAX_SQFT else None


@dataclass
class Listing:
    source: str
    source_url: str
    seen: str
    address: str
    beds: int
    rent: int
    lat: float | None = None
    lon: float | None = None
    unit_label: str | None = None
    baths: float | None = None
    sqft: int | None = None
    available: str | None = None
    rent_is_floor: bool = False

    @classmethod
    def from_dict(cls, d):
        bad = FORBIDDEN & {k.lower() for k in d}
        if bad:
            raise ListingError(f"listing carries forbidden field(s): {sorted(bad)}")
        known = {f.name for f in fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise ListingError(f"unknown field(s): {sorted(unknown)}")
        for k in REQUIRED:
            if d.get(k) in (None, ""):
                raise ListingError(f"missing required field: {k}")

        def opt(k, conv):
            v = d.get(k)
            return None if v in (None, "") else conv(v)

        url = str(d["source_url"])
        if not url.startswith(("http://", "https://")):
            raise ListingError(f"source_url is not a web link: {url!r}")
        rent = _int(d["rent"], "rent")
        if rent <= 0:
            raise ListingError(f"rent must be positive: {d['rent']!r}")
        if rent > MAX_RENT:
            raise ListingError(f"rent is not a monthly rent: {d['rent']!r}")
        return cls(
            source=str(d["source"]),
            source_url=url,
            seen=_seen(d["seen"]),
            address=str(d["address"]).strip(),
            beds=_beds(d["beds"]),
            rent=rent,
            lat=opt("lat", lambda v: _float(v, "lat")),
            lon=opt("lon", lambda v: _float(v, "lon")),
            unit_label=opt("unit_label", lambda v: str(v).strip()),
            baths=opt("baths", lambda v: _float(v, "baths")),
            sqft=opt("sqft", _sqft),
            available=opt("available", _available),
            rent_is_floor=bool(d.get("rent_is_floor") or False),
        )

    def to_dict(self):
        d = asdict(self)
        if not self.rent_is_floor:
            del d["rent_is_floor"]
        return d
