"""The adapter interface and the polite fetcher every adapter shares.

An adapter turns a landlord's pages into Listings. fetch() gets raw bytes; parse() is pure
(bytes and URL in, listings out) so it can be tested on a saved page with no network.

Tiers: 1 plain fetch from the server, 2 plain fetch through a residential exit (needs a
proxy URL), 3 real browser (not run by this core).
"""
import gzip
import hashlib
import json
import time
import urllib.error
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "collect" / "cache"

USER_AGENT = "apartments-map/0.1 (Toronto rental availability map; +https://github.com/datumsOG/apartments)"
MIN_INTERVAL_S = 2.0      # at most one request per two seconds per host; longer if robots.txt asks
TIMEOUT_S = 30

# Text that marks a bot-challenge page even when it arrives with status 200.
CHALLENGE_MARKERS = (b"cf-challenge", b"challenge-platform", b"<title>just a moment...</title>",
                     b"attention required! | cloudflare", b"px-captcha", b"_incapsula_resource",
                     b"g-recaptcha", b"h-captcha", b"distil_r_captcha", b"are you a robot")


def _decoded(headers, body):
    """Some servers gzip the body whether or not it was asked for."""
    if body[:2] == b"\x1f\x8b" and (headers.get("Content-Encoding") or "").lower() in ("gzip", "x-gzip"):
        try:
            return gzip.decompress(body)
        except OSError:
            return body
    return body


class FetchError(Exception):
    """The fetch failed for a reason that is not a block (network error, 404, 500)."""


class Blocked(FetchError):
    """The site refused us: 403, 429, a challenge page, or robots.txt says no.
    A blocked source is recorded as blocked. The one permitted second try is the same
    plain request over an ordinary home connection (see CLAUDE.md, the collection rule);
    a challenge, a login wall or a robots.txt refusal is never worked around."""


class RobotsDisallowed(Blocked):
    pass


class Broken(Exception):
    """Parsing found nothing (or failed) where it used to find something."""


class Adapter:
    id = ""
    name = ""
    tier = 1
    hosts = ()          # every host this adapter touches
    home_hosts = ()     # the ones among them that are asked over the home route
    start_urls = ()
    demo = False        # demo adapters run only with --demo

    def urls(self):
        return list(self.start_urls)

    def fetch(self, fetcher):
        """Yield (raw bytes, url) for every page to parse. Override for paginated sites."""
        for url in self.urls():
            if urlsplit(url).hostname not in self.hosts:
                raise FetchError(f"{self.id}: {url} is not on a declared host")
            yield fetcher.get(url), url

    def parse(self, raw, url):
        """Pure: raw page bytes and its URL in, list[Listing] out."""
        raise NotImplementedError

    def collect(self, fetcher):
        out = []
        for raw, url in self.fetch(fetcher):
            out.extend(self.parse(raw, url))
        return out


def looks_like_challenge(body):
    head = body[:65536].lower()
    return any(m in head for m in CHALLENGE_MARKERS)


class Fetcher:
    """GET with robots.txt, a per-host rate limit and a raw-response cache on disk.

    max_age: serve from cache without a request when the cached copy is younger than this
    many seconds (None: always fetch, still writing the cache). cache_only: never touch the
    network; a missing cache entry is a FetchError (replaying saved pages)."""

    def __init__(self, cache_dir=CACHE_DIR, proxy=None, min_interval=MIN_INTERVAL_S,
                 user_agent=USER_AGENT, timeout=TIMEOUT_S, max_age=None, cache_only=False,
                 clock=time.monotonic, sleep=time.sleep, home_proxy=None, home_hosts=()):
        self.cache_dir = Path(cache_dir)
        self.min_interval = min_interval
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_age = max_age
        self.cache_only = cache_only
        self.clock, self.sleep = clock, sleep
        self._last = {}       # host -> clock() of the last request
        self._robots = {}     # host -> RobotFileParser
        handlers = []
        if proxy:
            handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        else:
            handlers.append(urllib.request.ProxyHandler({}))
        self.opener = urllib.request.build_opener(*handlers)
        # home_hosts: the hosts that refuse a data-centre address and are asked over the home
        # route instead (home_proxy; see (internal path)). Every
        # other host is asked directly. Without a home_proxy they are asked directly too,
        # and the refusal is recorded as a block.
        self.home_hosts = set(home_hosts) if home_proxy else set()
        self.home_opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": home_proxy, "https": home_proxy})) if home_proxy else None
        self.requests = 0     # network requests made, for tests and the summary

    # cache ---------------------------------------------------------------
    def _cache_paths(self, url):
        host = urlsplit(url).netloc.replace(":", "_") or "_"
        h = hashlib.sha256(url.encode()).hexdigest()[:32]
        d = self.cache_dir / host
        return d / f"{h}.body", d / f"{h}.json"

    def cached(self, url):
        """(body, meta) from the cache, or None."""
        body, meta = self._cache_paths(url)
        if not body.exists() or not meta.exists():
            return None
        return body.read_bytes(), json.loads(meta.read_text())

    def _store(self, url, status, headers, data):
        body, meta = self._cache_paths(url)
        body.parent.mkdir(parents=True, exist_ok=True)
        body.write_bytes(data)
        meta.write_text(json.dumps({
            "url": url, "status": status,
            "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fetched_epoch": time.time(),
            "content_type": headers.get("Content-Type") if headers else None,
        }, indent=1))

    # politeness ------------------------------------------------------------
    def _wait(self, host):
        last = self._last.get(host)
        if last is not None:
            # A Crawl-delay in the host's robots.txt is waited out in full, never shortened.
            rp = self._robots.get(host)
            asked = (rp.crawl_delay(self.user_agent) if rp else None) or 0
            gap = max(self.min_interval, asked) - (self.clock() - last)
            if gap > 0:
                self.sleep(gap)
        self._last[host] = self.clock()

    def _request(self, url):
        """One network GET, rate limited. Returns (status, headers, body); never raises on HTTP status."""
        self._wait(urlsplit(url).netloc)
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
        self.requests += 1
        try:
            opener = self.home_opener if urlsplit(url).hostname in self.home_hosts else self.opener
            with opener.open(req, timeout=self.timeout) as resp:
                return resp.status, resp.headers, _decoded(resp.headers, resp.read())
        except urllib.error.HTTPError as e:
            return e.code, e.headers, _decoded(e.headers, e.read() or b"")
        except (urllib.error.URLError, OSError) as e:
            raise FetchError(f"{url}: {e}") from e

    def allowed(self, url):
        parts = urlsplit(url)
        host = parts.netloc
        if host not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            robots_url = f"{parts.scheme}://{host}/robots.txt"
            status, _, body = self._request(robots_url)
            if status in (401, 403):
                rp.disallow_all = True
            elif status >= 400:
                rp.allow_all = True
            else:
                rp.parse(body.decode("utf-8", "replace").splitlines())
            self._robots[host] = rp
        return self._robots[host].can_fetch(self.user_agent, url)

    def get(self, url):
        hit = self.cached(url)
        if self.cache_only:
            if hit is None:
                raise FetchError(f"{url}: not in cache")
            return hit[0]
        if hit is not None and self.max_age is not None and \
                time.time() - hit[1].get("fetched_epoch", 0) < self.max_age and hit[1]["status"] == 200:
            return hit[0]
        if not self.allowed(url):
            raise RobotsDisallowed(f"{url}: disallowed by robots.txt")
        status, headers, body = self._request(url)
        self._store(url, status, headers, body)
        if status in (403, 429):
            raise Blocked(f"{url}: HTTP {status}")
        if looks_like_challenge(body):
            raise Blocked(f"{url}: challenge page (HTTP {status})")
        if status >= 400:
            raise FetchError(f"{url}: HTTP {status}")
        return body
