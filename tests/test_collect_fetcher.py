"""The polite fetcher, against a local http.server. Never the internet."""
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from collect.adapter import Adapter, Blocked, FetchError, Fetcher, RobotsDisallowed

ROBOTS = b"User-agent: *\nDisallow: /private/\n"
PAGES = {
    "/robots.txt": (200, ROBOTS),
    "/ok": (200, b"<html>suites</html>"),
    "/private/x": (200, b"secret"),
    "/forbidden": (403, b"no"),
    "/slow-down": (429, b"too many"),
    "/challenge": (200, b"<html><head><title>Just a moment...</title></head><div id='cf-challenge'></div>"),
    "/missing": (404, b"not here"),
}


class Handler(BaseHTTPRequestHandler):
    log = []

    def do_GET(self):
        Handler.log.append(self.path)
        status, body = PAGES.get(self.path, (404, b""))
        if self.path.startswith("http://"):          # a proxied request carries the full URL
            status, body = 200, b"via proxy"
        self.send_response(status)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


class FetcherTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Handler.log.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.clock = FakeClock()
        self.f = Fetcher(cache_dir=self.tmp.name, clock=self.clock, sleep=self.clock.sleep)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ok_and_cached(self):
        self.assertEqual(self.f.get(self.base + "/ok"), b"<html>suites</html>")
        body, meta = self.f.cached(self.base + "/ok")
        self.assertEqual((body, meta["status"]), (b"<html>suites</html>", 200))

    def test_robots_checked_once_and_obeyed(self):
        self.f.get(self.base + "/ok")
        with self.assertRaises(RobotsDisallowed):
            self.f.get(self.base + "/private/x")
        self.assertEqual(Handler.log.count("/robots.txt"), 1)
        self.assertNotIn("/private/x", Handler.log)

    def test_one_request_per_interval_per_host(self):
        self.f.get(self.base + "/ok")      # robots.txt, then /ok: one wait
        self.f.get(self.base + "/ok")      # one more wait
        self.assertEqual(self.clock.slept, [2.0, 2.0])

    def test_crawl_delay_in_robots_is_waited_in_full(self):
        PAGES["/robots.txt"] = (200, ROBOTS + b"Crawl-delay: 10\n")
        try:
            self.f.get(self.base + "/ok")      # robots.txt (delay not known yet), then /ok
            self.f.get(self.base + "/ok")
            self.assertEqual(self.clock.slept, [10.0, 10.0])
        finally:
            PAGES["/robots.txt"] = (200, ROBOTS)

    def test_rate_limit_counts_elapsed_time(self):
        self.f.get(self.base + "/ok")
        self.clock.t += 1.5
        self.f.get(self.base + "/ok")
        self.assertEqual(self.clock.slept, [2.0, 0.5])

    def test_real_clock_spacing(self):
        import time
        f = Fetcher(cache_dir=self.tmp.name, min_interval=0.3)
        t0 = time.monotonic()
        f.get(self.base + "/ok")
        f.get(self.base + "/ok")
        self.assertGreaterEqual(time.monotonic() - t0, 0.6)   # robots, ok, ok: two gaps

    def test_blocked_is_not_broken(self):
        for path in ("/forbidden", "/slow-down", "/challenge"):
            with self.subTest(path=path):
                with self.assertRaises(Blocked):
                    self.f.get(self.base + path)
        with self.assertRaises(FetchError) as cm:
            self.f.get(self.base + "/missing")
        self.assertNotIsInstance(cm.exception, Blocked)

    def test_blocked_response_is_still_cached(self):
        with self.assertRaises(Blocked):
            self.f.get(self.base + "/forbidden")
        self.assertEqual(self.f.cached(self.base + "/forbidden")[1]["status"], 403)

    def test_cache_only_replays_without_network(self):
        self.f.get(self.base + "/ok")
        n = len(Handler.log)
        replay = Fetcher(cache_dir=self.tmp.name, cache_only=True)
        self.assertEqual(replay.get(self.base + "/ok"), b"<html>suites</html>")
        self.assertEqual(len(Handler.log), n)
        with self.assertRaises(FetchError):
            replay.get(self.base + "/never-fetched")

    def test_max_age_serves_fresh_cache(self):
        self.f.get(self.base + "/ok")
        n = len(Handler.log)
        f2 = Fetcher(cache_dir=self.tmp.name, max_age=3600)
        f2.get(self.base + "/ok")
        self.assertEqual(len(Handler.log), n)

    def test_network_error(self):
        with self.assertRaises(FetchError):
            Fetcher(cache_dir=self.tmp.name, timeout=2).get("http://127.0.0.1:9/ok")

    def test_proxy(self):
        # The local server stands in for a residential exit: it receives the full URL.
        f = Fetcher(cache_dir=self.tmp.name, proxy=self.base, min_interval=0)
        self.assertEqual(f.get("http://listings.example.invalid/page"), b"via proxy")
        self.assertIn("http://listings.example.invalid/page", Handler.log)


class AdapterInterface(unittest.TestCase):
    def test_fetch_refuses_undeclared_hosts(self):
        class A(Adapter):
            id, hosts, start_urls = "a", ("good.invalid",), ("https://other.invalid/x",)

        with self.assertRaises(FetchError):
            list(A().fetch(None))

    def test_parse_is_required(self):
        with self.assertRaises(NotImplementedError):
            Adapter().parse(b"", "https://x.invalid/")


if __name__ == "__main__":
    unittest.main()
