"""DEMO ONLY. An adapter over an invented page (tests/fixtures/demo_landlord.html) that
proves the whole path: parse, match, state, coverage, export. It never touches the network
and is excluded from real runs unless `python3 -m collect.run --demo` is passed."""
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from collect.adapter import Adapter
from collect.model import Listing

FIXTURE = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "demo_landlord.html"
FIELDS = {"addr", "label", "beds", "baths", "sqft", "rent", "avail"}


def _number(text):
    """The first number in "540 sq.ft." or "From $1,750/month", without commas; "" if none."""
    m = re.search(r"\d[\d,]*(?:\.\d+)?", text or "")
    return m.group(0).replace(",", "") if m else ""


class _SuiteParser(HTMLParser):
    """Collects the text of the known spans inside each <article class="suite">. Everything
    else on the page (description paragraphs, images) is ignored, never read into a field."""

    def __init__(self):
        super().__init__()
        self.suites, self._suite, self._field = [], None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "article" and "suite" in classes:
            self._suite = {"url": a.get("data-url"), "lat": a.get("data-lat"), "lon": a.get("data-lon")}
        elif self._suite is not None and tag == "span":
            self._field = next((c for c in classes if c in FIELDS), None)
            if self._field:
                self._suite.setdefault(self._field, "")

    def handle_endtag(self, tag):
        if tag == "span":
            self._field = None
        elif tag == "article" and self._suite is not None:
            self.suites.append(self._suite)
            self._suite = None

    def handle_data(self, data):
        if self._suite is not None and self._field:
            self._suite[self._field] += data


class DemoAdapter(Adapter):
    id = "demo"
    name = "Demo landlord (invented)"
    tier = 1
    hosts = ("demo.invalid",)
    start_urls = ("https://demo.invalid/toronto",)
    demo = True

    def __init__(self, fixture=FIXTURE, seen=None):
        self.fixture = Path(fixture)
        self.seen = seen

    def fetch(self, fetcher):
        yield self.fixture.read_bytes(), self.start_urls[0]

    def parse(self, raw, url):
        p = _SuiteParser()
        p.feed(raw.decode("utf-8"))
        seen = self.seen or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = []
        for s in p.suites:
            rent = s.get("rent", "").strip()
            floor = rent.lower().startswith("from")
            out.append(Listing.from_dict({
                "source": self.id,
                "source_url": s["url"] or url,
                "seen": seen,
                "address": " ".join(s.get("addr", "").split()),
                "lat": s["lat"], "lon": s["lon"],
                "unit_label": s.get("label", "").strip() or None,
                "beds": s.get("beds", "").strip(),
                "baths": s.get("baths", "").strip(),
                "sqft": _number(s.get("sqft")),
                "rent": _number(rent),
                "available": s.get("avail", "").strip(),
                "rent_is_floor": floor,
            }))
        return out
