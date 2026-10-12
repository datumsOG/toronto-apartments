"""Registry -> app/data/buildings.json (docs/CONTRACT.md section 1).

    python3 build/buildings.py            # writes app/data/buildings.json and build/out/unlocated.csv
    python3 build/buildings.py --fit-limit 40

One record per row of data/registry/registration.csv. The latest RentSafeTO evaluation
per RSN (data/registry/evaluations.csv) supplies the score and the coordinates.

Coordinates. Most evaluations carry LATITUDE/LONGITUDE; about a hundred buildings have
only X/Y, a local projected grid. No projection is hard-coded: a least-squares polynomial
from X,Y to lat,lon is fitted on the rows that have both, 10% of buildings are held out
to measure the error in metres, and the fit is used for the X/Y-only buildings only if
the held-out 95th-percentile error is under --fit-limit metres (10 by default). A
building with no usable coordinates goes to build/out/unlocated.csv.

Standard library only.
"""
import argparse
import csv
import json
import math
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRATION = ROOT / "data" / "registry" / "registration.csv"
EVALUATIONS = ROOT / "data" / "registry" / "evaluations.csv"
SOURCE_NOTE = ROOT / "data" / "registry" / "SOURCE.md"
ALIASES = ROOT / "data" / "manager_aliases.json"
OUT_JSON = ROOT / "app" / "data" / "buildings.json"
OUT_UNLOCATED = ROOT / "build" / "out" / "unlocated.csv"

FIT_LIMIT_M = 10.0     # held-out p95 error under which the X/Y fit may be used
QUADRATIC_IF_M = 1.0   # affine held-out median above this: try a quadratic, keep it if better
HOLDOUT = 0.10
SEED = 20261009        # the split is fixed so two builds print the same error

TYPES = {"PRIVATE": "private", "TCHC": "tchc", "SOCIAL HOUSING": "social"}
PARKING = {
    "SURFACE PARKING": "surface",
    "UNDERGROUND GARAGE": "underground",
    "GROUND LEVEL GARAGE": "garage",
    "GARAGE ACCESSIBLE THRU BUILDG": "indoor_access",
    "CARPORT": "carport",
    "PARKING DECK": "deck",
}
AC = {"NONE": "none", "INDIVIDUAL UNITS": "units", "CENTRAL AIR": "central"}
SUFFIXES = {"INC", "LTD", "LIMITED", "CORP", "LP", "ULC"}
ORDINAL = re.compile(r"^(\d+)(ST|ND|RD|TH)$")
M_PER_DEG_LAT = 111_320.0


# ---------------------------------------------------------------- reading

def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def cell(row, key):
    return (row.get(key) or "").strip()


def to_int(s):
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def to_float(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def yes_no(s):
    return {"YES": True, "NO": False}.get(s.strip().upper())


def registry_refreshed(note_path=SOURCE_NOTE):
    """The city's last refresh of registration.csv, from the table in SOURCE.md."""
    try:
        text = Path(note_path).read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r"`registration\.csv`[^\n]*?(\d{4}-\d{2}-\d{2})", text)
    return m.group(1) if m else None


# ---------------------------------------------------------------- tidying

def tidy_address(s):
    """`181-183  GERRARD ST E ` -> `181-183 Gerrard St E`; `2ND` -> `2nd`."""
    words = []
    for w in s.split():
        m = ORDINAL.match(w.upper())
        if m:
            words.append(m.group(1) + m.group(2).lower())
        elif w[0].isdigit():
            words.append(w.upper())
        else:
            words.append(w.title())    # O'CONNOR -> O'Connor, ST. -> St.
    return " ".join(words)


def canon_manager(name):
    """Uppercase, punctuation out, trailing company suffixes out, spaces collapsed."""
    s = name.upper().replace("'", "").replace(".", "")
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    words = s.split()
    while words and words[-1] in SUFFIXES:
        words.pop()
    return " ".join(words)


def load_aliases(path=ALIASES):
    """{"CANONICAL": ["registry spelling", ...]} -> {canon(spelling): "CANONICAL"}."""
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    out = {}
    for canonical, spellings in raw.items():
        out[canon_manager(canonical)] = canonical
        for s in spellings:
            out[canon_manager(s)] = canonical
    return out


def manager_of(name, aliases):
    c = canon_manager(name)
    if not c:
        return None
    return aliases.get(c, c)


# ---------------------------------------------------------------- evaluations

def latest_evaluations(rows):
    """RSN -> (latest evaluation, latest one with lat/lon, latest one with X/Y)."""
    by_rsn = {}
    for r in rows:
        by_rsn.setdefault(cell(r, "RSN"), []).append(r)
    out = {}
    for rsn, evs in by_rsn.items():
        evs.sort(key=lambda r: (cell(r, "EVALUATION COMPLETED ON"), to_int(cell(r, "_id")) or 0), reverse=True)
        ll = next((r for r in evs if has_latlon(r)), None)
        xy = next((r for r in evs if has_xy(r)), None)
        out[rsn] = (evs[0], ll, xy)
    return out


def has_latlon(r):
    return to_float(cell(r, "LATITUDE")) is not None and to_float(cell(r, "LONGITUDE")) is not None


def has_xy(r):
    return to_float(cell(r, "X")) is not None and to_float(cell(r, "Y")) is not None


def score_of(ev):
    s = to_float(cell(ev, "CURRENT BUILDING EVAL SCORE"))
    return None if s is None else int(round(s))


def score_year_of(ev):
    return to_int(cell(ev, "EVALUATION COMPLETED ON")[:4])


# ---------------------------------------------------------------- the X/Y fit

def solve(a, b):
    """Gaussian elimination with partial pivoting. a is n x n, b is length n."""
    n = len(a)
    m = [row[:] + [v] for row, v in zip(a, b)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        if m[c][c] == 0:
            raise ValueError("singular system")
        for r in range(n):
            if r != c:
                f = m[r][c] / m[c][c]
                for k in range(c, n + 1):
                    m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] for i in range(n)]


class Fit:
    """lat, lon as a polynomial in X, Y. Degree 1 is affine, 2 is quadratic.

    X and Y are centred and scaled to tens of kilometres first so the normal
    equations stay well conditioned."""

    def __init__(self, points, degree):
        self.degree = degree
        self.x0 = sum(p[0] for p in points) / len(points)
        self.y0 = sum(p[1] for p in points) / len(points)
        t = [self.terms(p[0], p[1]) for p in points]
        n = len(t[0])
        if len(points) < n:
            raise ValueError("too few points for degree %d" % degree)
        ata = [[sum(r[i] * r[j] for r in t) for j in range(n)] for i in range(n)]
        self.lat = solve(ata, [sum(r[i] * p[2] for r, p in zip(t, points)) for i in range(n)])
        self.lon = solve(ata, [sum(r[i] * p[3] for r, p in zip(t, points)) for i in range(n)])

    def terms(self, x, y):
        u, v = (x - self.x0) / 1e4, (y - self.y0) / 1e4
        out = [1.0, u, v]
        if self.degree >= 2:
            out += [u * u, u * v, v * v]
        return out

    def __call__(self, x, y):
        t = self.terms(x, y)
        return (sum(c * k for c, k in zip(self.lat, t)), sum(c * k for c, k in zip(self.lon, t)))


def metres(lat1, lon1, lat2, lon2):
    """Distance in metres; flat-earth is exact enough at city scale."""
    dy = (lat1 - lat2) * M_PER_DEG_LAT
    dx = (lon1 - lon2) * M_PER_DEG_LAT * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def quantile(sorted_vals, q):
    if not sorted_vals:
        return float("nan")
    i = min(len(sorted_vals) - 1, max(0, math.ceil(q * len(sorted_vals)) - 1))
    return sorted_vals[i]


def fit_xy(points_by_rsn, seed=SEED, holdout=HOLDOUT):
    """points_by_rsn: {rsn: (x, y, lat, lon)}. Holds out whole buildings, so a
    building's repeat evaluations cannot leak from training into the test.

    Returns a dict: the chosen Fit, its degree, held-out median and p95 in metres,
    and the same for every degree tried."""
    keys = sorted(points_by_rsn)
    random.Random(seed).shuffle(keys)
    n_test = max(1, int(len(keys) * holdout))
    test = [points_by_rsn[k] for k in keys[:n_test]]
    train = [points_by_rsn[k] for k in keys[n_test:]]

    def held_out(fit):
        errs = sorted(metres(*fit(x, y), lat, lon) for x, y, lat, lon in test)
        return quantile(errs, 0.5), quantile(errs, 0.95)

    # The degree is chosen on the held-out MEDIAN. The tail is dominated by buildings
    # whose X/Y and lat/lon the city geocoded to different points tens of metres
    # apart, which no polynomial can fix; the median is what the fit itself gets right.
    tried = []
    fit = Fit(train, 1)
    med, p95 = held_out(fit)
    tried.append((1, med, p95))
    if med > QUADRATIC_IF_M:
        quad = Fit(train, 2)
        qmed, qp95 = held_out(quad)
        tried.append((2, qmed, qp95))
        if qmed < med:
            fit, med, p95 = quad, qmed, qp95
    return {"fit": fit, "degree": fit.degree, "median_m": med, "p95_m": p95,
            "train": len(train), "test": len(test), "tried": tried}


# ---------------------------------------------------------------- building records

def flags_of(r):
    f = {}
    for key, col in (("balconies", "BALCONIES"), ("pets", "PETS_ALLOWED"),
                     ("laundry", "LAUNDRY_ROOM"), ("nonsmoking", "NON_SMOKING_BUILDING")):
        v = yes_no(cell(r, col))
        if v is not None:
            f[key] = v
    ac = AC.get(cell(r, "AIR_CONDITIONING_TYPE").upper())
    if ac:
        f["ac"] = ac
    el = to_int(cell(r, "NO_OF_ELEVATORS"))
    if el is not None:
        f["elevators"] = el
    vp = cell(r, "VISITOR_PARKING").lower()
    if vp in ("free", "paid", "both", "unavailable"):
        f["visitor_parking"] = vp
    return f


def parking_of(r):
    out = []
    for part in cell(r, "PARKING_TYPE").split(","):
        p = PARKING.get(part.strip().upper())
        if p and p not in out:
            out.append(p)
    return out


def build(reg_rows, eval_rows, aliases=None, fit_limit=FIT_LIMIT_M, now=None, refreshed=None):
    """Returns (document, unlocated rows, summary). Pure: reads nothing, writes nothing."""
    aliases = aliases or {}
    evals = latest_evaluations(eval_rows)

    # Fit on every evaluated building that has both kinds of coordinate.
    pairs = {}
    for rsn, (_, ll, _) in evals.items():
        if ll is not None and has_xy(ll):
            pairs[rsn] = (float(cell(ll, "X")), float(cell(ll, "Y")),
                          float(cell(ll, "LATITUDE")), float(cell(ll, "LONGITUDE")))
    fit = fit_xy(pairs) if len(pairs) >= 20 else None
    use_fit = fit is not None and fit["p95_m"] < fit_limit

    records, unlocated = [], []
    units_by_mgr = {}
    method = {"latlon": 0, "fit": 0}
    counts = {"buildings": 0, "private": 0, "tchc": 0, "social": 0, "located": 0, "unlocated": 0, "units": 0}

    for r in reg_rows:
        rsn = cell(r, "RSN")
        latest, ll, xy = evals.get(rsn, (None, None, None))
        rec = {"id": int(rsn), "addr": tidy_address(cell(r, "SITE_ADDRESS"))}
        btype = TYPES.get(cell(r, "PROPERTY_TYPE").upper())
        if btype is None and latest is not None:
            btype = TYPES.get(cell(latest, "PROPERTY TYPE").upper())
        units = to_int(cell(r, "CONFIRMED_UNITS"))
        mgr = manager_of(cell(r, "PROP_MANAGEMENT_COMPANY_NAME"), aliases)

        counts["buildings"] += 1
        if btype:
            counts[btype] += 1
        counts["units"] += units or 0

        lat = lon = None
        if ll is not None:
            lat, lon = float(cell(ll, "LATITUDE")), float(cell(ll, "LONGITUDE"))
            method["latlon"] += 1
        elif xy is not None and use_fit:
            lat, lon = fit["fit"](float(cell(xy, "X")), float(cell(xy, "Y")))
            method["fit"] += 1
        if lat is None:
            reason = ("never evaluated" if latest is None
                      else "only X/Y, fit not used" if xy is not None
                      else "evaluation has no coordinates")
            unlocated.append({"id": rsn, "addr": rec["addr"], "type": btype or "",
                              "units": units if units is not None else "", "manager": mgr or "",
                              "reason": reason})
            counts["unlocated"] += 1
            continue
        counts["located"] += 1

        rec["lat"], rec["lon"] = round(lat, 5), round(lon, 5)
        if units is not None:
            rec["units"] = units
        storeys = to_int(cell(r, "CONFIRMED_STOREYS"))
        if storeys is not None:
            rec["storeys"] = storeys
        year = to_int(cell(r, "YEAR_BUILT"))
        if year is not None:
            rec["year"] = year
        if btype:
            rec["type"] = btype
        rec["mgr"] = mgr               # replaced by an index below
        rec["score"] = score_of(latest) if latest is not None else None
        if rec["score"] is not None:
            rec["score_year"] = score_year_of(latest)
        ward = to_int(cell(r, "WARD"))
        if ward is not None:
            rec["ward"] = ward
        if cell(r, "PCODE"):
            rec["pcode"] = cell(r, "PCODE").upper()
        parking = parking_of(r)
        if parking:
            rec["parking"] = parking
        flags = flags_of(r)
        if flags:
            rec["flags"] = flags
        if mgr:
            units_by_mgr[mgr] = units_by_mgr.get(mgr, 0) + (units or 0)
        records.append(rec)

    # Biggest managers first, so the common ones get the short indices.
    managers = sorted(units_by_mgr, key=lambda m: (-units_by_mgr[m], m))
    index = {m: i for i, m in enumerate(managers)}
    for rec in records:
        rec["mgr"] = index.get(rec["mgr"])

    now = now or datetime.now(timezone.utc)
    doc = {
        "generated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "registry_refreshed": refreshed,
        "counts": counts,
        "managers": managers,
        "buildings": records,
    }
    summary = {"counts": counts, "method": method, "fit": fit, "fit_used": use_fit,
               "fit_limit": fit_limit, "managers": len(managers),
               "units_located": sum(b.get("units", 0) for b in records)}
    return doc, unlocated, summary


# ---------------------------------------------------------------- writing

def dumps(doc):
    """Compact JSON, one building per line: small, and a diff stays readable."""
    head = {k: v for k, v in doc.items() if k != "buildings"}
    sep = (",", ":")
    lines = [json.dumps(head, separators=sep, ensure_ascii=False)[:-1] + ',"buildings":[']
    rows = [json.dumps(b, separators=sep, ensure_ascii=False) for b in doc["buildings"]]
    lines.append(",\n".join(rows))
    lines.append("]}")
    return "\n".join(lines) + "\n"


def write_unlocated(rows, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "addr", "type", "units", "manager", "reason"])
        w.writeheader()
        w.writerows(rows)


def report(summary, size_bytes, out_path):
    c, m, f = summary["counts"], summary["method"], summary["fit"]
    lines = [
        f"Registry buildings:  {c['buildings']:,} ({c['private']:,} private, {c['tchc']:,} TCHC, "
        f"{c['social']:,} social housing, {c['buildings'] - c['private'] - c['tchc'] - c['social']:,} with no type)",
        f"Located:             {c['located']:,} ({m['latlon']:,} from the evaluation's latitude/longitude, "
        f"{m['fit']:,} from X/Y through the fit)",
        f"Unlocated:           {c['unlocated']:,} (listed in build/out/unlocated.csv)",
        f"Units:               {c['units']:,} in all registry buildings, {summary['units_located']:,} of them in located buildings",
        f"Managers:            {summary['managers']:,} distinct names after canonicalising",
    ]
    if f:
        tried = "; ".join(f"degree {d}: median {med:.1f} m, p95 {p95:.1f} m" for d, med, p95 in f["tried"])
        lines.append(f"X/Y fit:             {f['train']:,} buildings to fit, {f['test']:,} held out. {tried}.")
        verdict = "used" if summary["fit_used"] else "NOT used"
        lines.append(f"                     Chosen degree {f['degree']}: held-out p95 {f['p95_m']:.1f} m against a "
                     f"limit of {summary['fit_limit']:g} m, so the fit is {verdict}.")
    else:
        lines.append("X/Y fit:             too few buildings with both coordinates to fit")
    lines.append(f"Wrote:               {out_path} ({size_bytes / 1024:,.0f} KB before gzip)")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--registration", default=REGISTRATION)
    ap.add_argument("--evaluations", default=EVALUATIONS)
    ap.add_argument("--aliases", default=ALIASES)
    ap.add_argument("--out", default=OUT_JSON)
    ap.add_argument("--unlocated", default=OUT_UNLOCATED)
    ap.add_argument("--fit-limit", type=float, default=FIT_LIMIT_M,
                    help="use the X/Y fit only if its held-out p95 error is under this many metres (default %(default)s)")
    args = ap.parse_args(argv)

    doc, unlocated, summary = build(read_csv(args.registration), read_csv(args.evaluations),
                                    aliases=load_aliases(args.aliases), fit_limit=args.fit_limit,
                                    refreshed=registry_refreshed())
    text = dumps(doc)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    write_unlocated(unlocated, args.unlocated)
    print(report(summary, len(text.encode("utf-8")), out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
