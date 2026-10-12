"""MetCap and CAPREIT adapters against trimmed captures of real pages. Never the internet."""
import unittest
from pathlib import Path

from collect.adapters.capreit import CapreitAdapter, community_urls
from collect.adapters.metcap import MetcapAdapter, _addresses, property_urls

PAGES = Path(__file__).parent / "fixtures" / "pages"
SEEN = "2026-10-10T18:00:00Z"


class Metcap(unittest.TestCase):
    URL = "https://www.metcap.com/property/15-dundonald-street-toronto-on-m4y-1k4/"

    def test_available_rows_become_listings(self):
        raw = (PAGES / "metcap" / "15-dundonald.html").read_bytes()
        a = MetcapAdapter(seen=SEEN)
        self.assertEqual([s[0] for s in a.sites(raw, self.URL)], ["15 Dundonald Street, Toronto"])
        ls = a.parse(raw, self.URL)
        self.assertTrue(ls)
        first = ls[0]
        self.assertEqual((first.address, first.beds, first.rent, first.sqft, first.rent_is_floor),
                         ("15 Dundonald Street, Toronto", 1, 1863, 626, True))
        self.assertAlmostEqual(first.lat, 43.67, places=2)
        rented = raw.replace(b'data-title="Status">Available<', b'data-title="Status">Rented<')
        self.assertEqual(a.parse(rented, self.URL), [])

    def test_titles_and_sitemap(self):
        page = "<title>2700 - 2702 Lawrence Avenue, East, Scarborough, ON M1P 2S5 - MetCap Living</title>"
        self.assertEqual(_addresses(page), ["2700 Lawrence Avenue East, Scarborough", "2702 Lawrence Avenue East, Scarborough"])
        sm = (b"<loc>https://www.metcap.com/property/1-a-street-toronto-on-m4y-1k4/</loc>"
              b"<loc>https://www.metcap.com/property/2-b-street-ottawa-on-k1a-0a1/</loc>")
        self.assertEqual(property_urls(sm), ["https://www.metcap.com/property/1-a-street-toronto-on-m4y-1k4/"])


class Capreit(unittest.TestCase):
    URL = "https://www.capreit.ca/apartments-for-rent/etobicoke-on/west-park-village-apartments/"

    def test_a_community_of_several_buildings(self):
        raw = (PAGES / "capreit" / "west-park-village.html").read_bytes()
        a = CapreitAdapter(seen=SEEN)
        addrs = [s[0] for s in a.sites(raw, self.URL)]
        self.assertEqual(addrs, ["10 Blackfriar Ave., Etobicoke", "186 Kingsview Blvd., Etobicoke",
                                 "15 Bridesburg Dr., Etobicoke", "55 Bridesburg Dr., Etobicoke"])
        self.assertTrue(all(s[1] is None for s in a.sites(raw, self.URL)))   # one map point, four buildings
        ls = a.parse(raw, self.URL)
        self.assertEqual([(l.unit_label, l.beds, l.rent, l.sqft) for l in ls][:2],
                         [("Bachelor", 0, 1750, 430), ("1 Bedroom", 1, 1895, 737)])
        self.assertTrue(all(l.address == addrs[0] and l.rent_is_floor for l in ls))
        self.assertEqual(a.parse(raw.replace(b'data-available="true"', b'data-available="false"'), self.URL), [])

    def test_sitemap_keeps_toronto_communities(self):
        sm = (b"<loc>https://www.capreit.ca/apartments-for-rent/toronto-on/x-apartments/</loc>"
              b"<loc>https://www.capreit.ca/apartments-for-rent/london-on/y/</loc>"
              b"<loc>https://www.capreit.ca/appartements-a-louer/toronto-on/x/</loc>")
        self.assertEqual(community_urls(sm), ["https://www.capreit.ca/apartments-for-rent/toronto-on/x-apartments/"])


if __name__ == "__main__":
    unittest.main()
