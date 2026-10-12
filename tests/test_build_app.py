"""The static app: the preview server serves app/ and only app/, the page loads the
pinned kit and a pinned MapLibre, and the kit copy is unedited."""
import hashlib
import json
import re
import sys
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
sys.path.insert(0, str(APP))
import serve  # noqa: E402


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = serve.make_server(0, quiet=True)   # any free port, 127.0.0.1
        cls.base = "http://127.0.0.1:%d/" % cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, r.headers, r.read()

    def test_binds_localhost_by_default(self):
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")

    def test_serves_the_page_and_modules(self):
        status, headers, body = self.get("")
        self.assertEqual(status, 200)
        self.assertIn(b'id="map"', body)
        status, headers, _ = self.get("js/logic.js")
        self.assertEqual(headers["Content-Type"].split(";")[0], "text/javascript")
        self.assertEqual(headers["Cache-Control"], "no-cache")
        status, headers, body = self.get("data/buildings.json")
        self.assertEqual(json.loads(body)["counts"]["buildings"], json.loads(
            (APP / "data" / "buildings.json").read_text(encoding="utf-8"))["counts"]["buildings"])

    def test_nothing_above_app(self):
        for path in ("../CLAUDE.md", "%2e%2e/CLAUDE.md", "../data/registry/SOURCE.md"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get(path)
            self.assertEqual(cm.exception.code, 404, path)

    def test_missing_availability_is_a_404_not_a_crash(self):
        if (APP / "data" / "availability.json").exists():
            self.skipTest("an availability.json is present in this checkout")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("data/availability.json")
        self.assertEqual(cm.exception.code, 404)


class Page(unittest.TestCase):
    html = (APP / "index.html").read_text(encoding="utf-8")

    def test_kit_is_loaded_at_its_pinned_version(self):
        version = (APP / "kit" / "KIT-VERSION").read_text().strip()
        self.assertIn('kit/ember-ui.css?v=%s' % version, self.html)
        self.assertIn('kit/ember-ui.js?v=%s' % version, self.html)
        app_js = (APP / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("kit/glass/host-maplibre.js?v=%s" % version, app_js)

    def test_maplibre_is_pinned_to_one_exact_version(self):
        versions = set(re.findall(r"maplibre-gl@([0-9][^/\"']*)/", self.html))
        self.assertEqual(len(versions), 1, versions)
        self.assertRegex(versions.pop(), r"^\d+\.\d+\.\d+$")

    def test_glass_layer_is_added_last(self):
        app_js = (APP / "js" / "app.js").read_text(encoding="utf-8")
        adds = [m.start() for m in re.finditer(r"map\.addLayer\(", app_js)]
        self.assertEqual(adds[-1], app_js.index("map.addLayer(glassLayer)"))

    def test_logic_module_has_no_dom_or_map(self):
        src = (APP / "js" / "logic.js").read_text(encoding="utf-8")
        self.assertNotRegex(src, r"\b(document|window|maplibregl)\b")
        self.assertNotRegex(src, r"^import ", "logic.js imports nothing")


class Kit(unittest.TestCase):
    def test_kit_copy_is_unedited(self):
        manifest = json.loads((APP / "kit" / "MANIFEST.json").read_text())
        for name, digest in manifest["files"].items():
            got = hashlib.sha256((APP / "kit" / name).read_bytes()).hexdigest()
            self.assertEqual(got, digest, "app/kit/%s differs from its release" % name)


if __name__ == "__main__":
    unittest.main()
