"""RPMS, Tricon and Minto adapters against small hand-cut pieces of their real markup."""
import json
import unittest

from collect.adapters.minto import MintoAdapter, beds_of, community_urls
from collect.adapters.rpms import RpmsAdapter
from collect.adapters.tricon import TriconAdapter, page_addresses, slugs

SEEN = "2026-10-10T18:00:00Z"

RPMS = b"""<div class="header-left"> <h1>Huntley Apartments</h1> <p>77 Huntley Street, Toronto, ON, M4Y 2P3</p></div>
<div id="detailMap" data-latitude="43.670364" data-longitude="-79.378719"></div>
<table class="table table-striped property-table"><tbody>
<tr> <td> <strong>Bachelor</strong><br /> <span class="price">$1,580</span> <span class="availability">Available Immediately</span> </td></tr>
<tr> <td> <strong>1 Bedroom</strong><br /> <span class="price">$1,845</span> <span class="availability">Waiting List</span> </td></tr>
</tbody></table>"""

MINTO = b"""<span itemprop="streetAddress" class="address-line">111 Pacific Avenue</span>, <span itemprop="addressLocality" class="address-line">Toronto</span>
<span itemprop="latitude">43.6542110</span><span itemprop="Longitude">-79.4637890</span>
<h4 class="mb-0 font-weight-bold h-h3-minto"> Studio Suites </h4> <p><span class="btn-available-now"> Available now </span></p>
<h5 class="mt-1 font-weight-bold h-h3-minto "> $1,955 - $2,035 </h5>
<h4 class="mb-0 font-weight-bold h-h3-minto"> Two Bedroom Penthouse </h4> <p><span> Not available </span></p>
<h4 class="mb-0 font-weight-bold h-h3-minto"> One Bedroom Suites </h4> <p><span class="btn-available-now"> Available October 20 </span></p>
<h5 class="mt-1 font-weight-bold h-h3-minto "> $2,300 </h5>
<h4 class="mb-0 font-weight-bold h-h3-minto"> Two Bedroom Townhomes </h4> <p><span class="btn-available-now"> Available now </span></p>
<h5 class="mt-1 font-weight-bold h-h3-minto "> $3,900 </h5>"""


class Small(unittest.TestCase):
    def test_rpms(self):
        a = RpmsAdapter(seen=SEEN)
        url = "https://www.rpmsi.ca/property/huntley-apartments-77-huntley/"
        self.assertEqual(a.sites(RPMS, url), [("77 Huntley Street, Toronto", "43.670364", "-79.378719")])
        self.assertEqual([(l.unit_label, l.beds, l.rent) for l in a.parse(RPMS, url)], [("Bachelor", 0, 1580)])
        self.assertEqual(a.sites(RPMS.replace(b"Toronto, ON", b"Ottawa, ON"), url), [])

    def test_minto(self):
        a = MintoAdapter(seen=SEEN)
        url = "https://www.mintoapartments.com/gta/Toronto-apartment-rentals/High-Park-Village/main.html"
        self.assertEqual(a.sites(MINTO, url)[0][0], "111 Pacific Avenue, Toronto")
        # the unavailable penthouse must not borrow the next block's price; townhomes are out
        self.assertEqual([(l.unit_label, l.beds, l.rent, l.rent_is_floor) for l in a.parse(MINTO, url)],
                         [("Studio Suites", 0, 1955, True), ("One Bedroom Suites", 1, 2300, True)])
        self.assertEqual([beds_of(t) for t in ("Studio Suites", "Three Bedroom Suites", "2 Bedroom", "Parking")], [0, 3, 2, None])
        links = b'<a href="https://www.mintoapartments.com/gta/Oakville-apartment-rentals/X/main.html"></a>' \
                b'<a href="https://www.mintoapartments.com/gta/North-York-apartment-rentals/Y/main.html"></a>'
        self.assertEqual(community_urls(links), ["https://www.mintoapartments.com/gta/North-York-apartment-rentals/Y/main.html"])

    def test_tricon(self):
        a = TriconAdapter(seen=SEEN)
        self.assertEqual(slugs(b'<a href="/apartment/the-spoke/">x</a><a href="https://triconliving.com/apartment/the-spoke">y</a>'), ["the-spoke"])
        self.assertEqual(page_addresses(b'<p class="wp-block-paragraph">485 Perth Avenue</p> <p class="wp-block-paragraph">Toronto, Ontario M6N 0B9</p>'),
                         ["485 Perth Avenue, Toronto"])
        self.assertEqual(page_addresses(b'<p>131 Mill St, Toronto, ON</p><p>0026 Mill Street, Toronto, ON</p><p>131 Mill Street<br>Toronto, Ontario</p>'
                                        b'<p>7 St Thomas St #801, Toronto, ON M5S 2B7</p>'), ["131 Mill St, Toronto"])
        raw = json.dumps({"addresses": ["485 Perth Avenue, Toronto"], "units": [
            {"unit_code": "W408", "beds": 1, "baths": "1.0", "sqft": 496, "min_rent": 2414, "max_rent": 2414,
             "availability": {"display": "Available", "date": None}},
            {"unit_code": "E101", "beds": 2, "baths": "2.0", "sqft": 800, "min_rent": 3000, "max_rent": 3200,
             "availability": {"display": "Coming Soon", "date": "2026-11-01T00:00:00"}},
            {"unit_code": "X1", "beds": 1, "min_rent": 2000, "max_rent": 2000, "availability": {"display": "Leased", "date": None}},
        ]}).encode()
        ls = a.parse(raw, "https://triconliving.com/apartment/the-spoke/")
        self.assertEqual([(l.unit_label, l.beds, l.rent, l.rent_is_floor, l.available) for l in ls],
                         [("W408", 1, 2414, False, None), ("E101", 2, 3000, True, "2026-11-01")])
        self.assertEqual(a.parse(json.dumps({"addresses": [], "units": []}).encode(), "https://triconliving.com/apartment/x/"), [])




class Later(unittest.TestCase):
    def test_akelius_units_from_the_state_block(self):
        from collect.adapters.akelius import AkeliusAdapter
        rows = [{"id": "50001a-1", "address": {"city": "Toronto", "streetName": "2875 Yonge Street", "latitude": 43.72, "longitude": -79.40},
                 "keyfacts": {"number-of-bedrooms": 2, "number-of-bathrooms": 1, "total-rent": 2500, "unit-size": 928,
                              "available-from-date": "2026-11-06T00:00:00.000Z", "is-available-from-now-on": False}},
                {"id": "m1", "address": {"city": "Montreal", "streetName": "3460 Peel Street"}, "keyfacts": {"number-of-bedrooms": 1, "total-rent": 1800}}]
        raw = ('<script id="akeliusWebsite-state" type="application/json">' + json.dumps({"1": {"b": rows}, "2": {"b": [{"x": 1}]}}) + "</script>").encode()
        a = AkeliusAdapter(seen=SEEN)
        ls = a.parse(raw, "https://rent.akelius.com/en/search/canada/apartment")
        self.assertEqual([(l.address, l.beds, l.rent, l.sqft, l.available, l.rent_is_floor) for l in ls],
                         [("2875 Yonge Street, Toronto", 2, 2500, 928, "2026-11-06", False)])
        self.assertTrue(ls[0].source_url.endswith("/detail/50001a-1"))
        self.assertEqual(len(a.sites(raw, "x")), 1)
        self.assertEqual(a.parse(b"<html></html>", "x"), [])

    def test_quadreal_cards_and_footer_address(self):
        from collect.adapters.quadreal import QuadrealAdapter, page_addresses
        home = b'<div class="address-sec-footer"> <p><b>Address</b></p> <p>44 Jackes Avenue<br>Toronto, ON M4T 1E5</p></div>'
        self.assertEqual(page_addresses(home), ["44 Jackes Avenue, Toronto"])
        self.assertEqual(page_addresses(b"<p>235 Bloor Street East Toronto, ON</p>"), [])     # not in the footer block
        card = ('<div class="other-info" style="x"> <h3 style="y">A3</h3> <p class="bedrooms"> 1 Bed </p> <p class="bathrooms"> 1 Bath </p>'
                ' <p class="sq-ft-range"> 515 sq.ft. </p> <p class="price-range"> From $2,650 to $2,730 / month </p>')
        raw = json.dumps({"addresses": ["44 Jackes Avenue, Toronto"], "page": card}).encode()
        ls = QuadrealAdapter(seen=SEEN).parse(raw, "https://quadrealresidential.com/toronto/bretton-place/floorplans/")
        self.assertEqual([(l.unit_label, l.beds, l.baths, l.sqft, l.rent, l.rent_is_floor) for l in ls], [("A3", 1, 1.0, 515, 2650, True)])


class Gateway(unittest.TestCase):
    def test_units_are_joined_to_toronto_buildings(self):
        from collect.adapters.gateway import SterlingKaramarAdapter

        class Fake:
            asked = []

            def get(self, url):
                self.asked.append(url)
                if "/properties" in url:
                    return json.dumps({"data": [
                        {"id": 1, "permaLink": "10-a-st", "streetNumber": "10", "streetName": "A Street", "postal": "M4P 1Z2",
                         "latitude": "43.7", "longitude": "-79.4", "buildingOverview": "<p>words we never keep</p>"},
                        {"id": 2, "permaLink": "5-b-rd", "streetNumber": "5", "streetName": "B Road", "postal": "N6H 1W4"}]}).encode()
                return json.dumps({"meta": {"totalPages": 1}, "data": [
                    {"id": 9, "buildingId": 1, "typeName": "1 Bedroom", "number": "804", "bed": 1, "bath": 1, "sqFt": "640",
                     "rate": "1775", "rateMin": 1775, "rateMax": 1775, "available": 1, "availabilityDate": "2026-11-01", "description": "no"},
                    {"id": 10, "buildingId": 1, "typeName": "2 Bedroom", "number": "", "bed": 2, "bath": 1, "sqFt": "0",
                     "rate": "2300", "rateMin": 2300, "rateMax": 2500, "available": 1, "availabilityDate": None},
                    {"id": 11, "buildingId": 2, "typeName": "1 Bedroom", "number": "1", "bed": 1, "rateMin": 1500, "rateMax": 1500, "available": 1}]}).encode()

        a, f = SterlingKaramarAdapter(seen=SEEN), Fake()
        (raw, url), = list(a.fetch(f))
        self.assertEqual(len(f.asked), 2)
        self.assertNotIn("words we never keep", raw.decode())
        self.assertNotIn("description", raw.decode())
        self.assertEqual(a.sites(raw, url), [("10 A Street, Toronto", "43.7", "-79.4")])
        ls = a.parse(raw, url)
        self.assertEqual([(l.unit_label, l.beds, l.rent, l.sqft, l.available, l.rent_is_floor) for l in ls],
                         [("Unit 804", 1, 1775, 640, "2026-11-01", False), ("2 Bedroom", 2, 2300, None, None, True)])
        self.assertTrue(ls[0].source_url.endswith("/apartments-for-rent/10-a-st"))


class Tail(unittest.TestCase):
    def test_medallion_building_level_price(self):
        from collect.adapters.medallion import MedallionAdapter
        rows = [{"title": "1755 Jane Street", "lat": "43.70", "lng": "-79.50", "url": "https://medallioncorp.com/property/1755-jane-street/",
                 "price": '<p class="price"><span class="floor-price-value">$1,755</span></p><p class="price">1,2 Bedrooms </p>'},
                {"title": "1541 Jane Street", "lat": "43.70", "lng": "-79.50", "url": "u", "price": ""},
                {"title": "25 Kitney Dr.", "lat": "43.8426", "lng": "-79.0193", "url": "u", "price": "<p>$1,800</p>"}]     # Ajax: outside the box
        raw = ("var propertiesMapData = " + json.dumps(rows) + ";").encode()
        a = MedallionAdapter(seen=SEEN)
        self.assertEqual([s[0] for s in a.sites(raw, "x")], ["1755 Jane Street, Toronto", "1541 Jane Street, Toronto"])
        ls = a.parse(raw, "x")
        self.assertEqual([(l.address, l.unit_label, l.beds, l.rent, l.rent_is_floor) for l in ls],
                         [("1755 Jane Street, Toronto", "1, 2 bedroom", 1, 1755, True)])

    def test_goldseal_buttons(self):
        from collect.adapters.goldseal import GoldSealAdapter
        page = (b'<h1>Fairfield Towers</h1><p>6 Twenty Fourth Street, Etobicoke, ON M8V 3N4</p>'
                b'<div data-ff-property="Fairfield Towers" data-ff-apartment="1 Bedroom Apartment" data-ff-beds="1" data-ff-price="$2,199"></div>'
                b'<div data-ff-property="Fairfield Towers" data-ff-apartment="2 Bedroom Townhome" data-ff-beds="2" data-ff-price="$2,909"></div>'
                b'<footer>2828 Bathurst Street, Toronto, ON M6B 3A7</footer>')
        a = GoldSealAdapter(seen=SEEN)
        self.assertEqual(a.sites(page, "u"), [("6 Twenty Fourth Street, Etobicoke", None, None)])
        self.assertEqual([(l.unit_label, l.beds, l.rent) for l in a.parse(page, "https://goldsealmanagement.com/property/fairfield-towers/")],
                         [("1 Bedroom Apartment", 1, 2199)])
        self.assertEqual(a.sites(b"<footer>2828 Bathurst Street, Toronto, ON</footer>", "u"), [])

    def test_berkley_cards(self):
        from collect.adapters.berkley import BerkleyAdapter
        page = (b'<a data-latitude="43.75" data-longitude="-79.49" data-x=""></a><div class="content"> <span class="address"> 170 Sentinel Road </span>'
                b' <span class="rent"> rent from <span class="value">$1350</span> </span> <span class="bedrooms"> 1-2 bedrooms </span> </div>'
                b'<a data-latitude="43.7" data-longitude="-79.4"></a><div class="content"> <span class="address"> 44 Walmer Road </span> </div>')
        a = BerkleyAdapter(seen=SEEN)
        url = "https://www.berkleypm.ca/cities/toronto/"
        self.assertEqual([s[0] for s in a.sites(page, url)], ["170 Sentinel Road, Toronto", "44 Walmer Road, Toronto"])
        self.assertEqual([(l.address, l.unit_label, l.beds, l.rent) for l in a.parse(page, url)],
                         [("170 Sentinel Road, Toronto", "1-2 bedrooms", 1, 1350)])

    def test_hrpm_table(self):
        from collect.adapters.hrpm import HrpmAdapter
        page = (b'<div class="address"> 1407 Royal York Road, Etobicoke<br />Ontario, M9P3A6, Canada </div>'
                b'<tr class="row-2"> <td class="column-1">One Bedroom </td><td class="column-2">Waiting List</td></tr>'
                b'<tr class="row-3"> <td class="column-1">Two Bedroom</td><td class="column-2">$2350</td></tr>'
                b'<tr class="row-5"> <td class="column-1">Penthouse</td><td class="column-2">$2890</td></tr>')
        a = HrpmAdapter(seen=SEEN)
        self.assertEqual(a.sites(page, "u"), [("1407 Royal York Road, Etobicoke", None, None)])
        self.assertEqual([(l.unit_label, l.beds, l.rent) for l in a.parse(page, "https://hrpm.ca/properties/1407-royal-york-road/")],
                         [("Two Bedroom", 2, 2350)])
        self.assertEqual(a.sites(page.replace(b"Etobicoke", b"Mississauga"), "u"), [])


class Aggregators(unittest.TestCase):
    def test_rentseeker_building_page(self):
        from collect.adapters.rentseeker import RentseekerAdapter, beds_of
        ld = {"@type": "ApartmentComplex", "address": {"streetAddress": "71 Talara Drive", "addressLocality": "Toronto"},
              "geo": {"latitude": 43.7658, "longitude": -79.3795}, "description": "never kept"}
        rows = [{"slug": "71-talara-drive-1-toronto-a", "title": "71 Talara Drive — 1 Bedroom", "unitNumber": None, "bedrooms": 0,
                 "bathrooms": 1, "sqft": 0, "price": 175000, "availableDate": None},
                {"slug": "71-talara-drive-2-toronto-b", "title": "71 Talara Drive — 2 Bedroom", "unitNumber": "804", "bedrooms": 2,
                 "bathrooms": 1, "sqft": 900, "price": 245000, "availableDate": "2026-11-01T00:00:00Z"},
                {"slug": "x", "title": "71 Talara Drive — Parking", "price": 10000}]
        flight = json.dumps({"units": rows}, ensure_ascii=False, separators=(",", ":"))[1:-1].replace('"', '\\"')
        page = ('<script type="application/ld+json">' + json.dumps(ld) + '</script><script>self.__next_f.push([1,"' + flight + '"])</script>').encode()
        a = RentseekerAdapter(seen=SEEN)
        url = "https://www.rentseeker.ca/building/71-talara-drive-toronto-5155"
        self.assertEqual(a.sites(page, url), [("71 Talara Drive, Toronto", 43.7658, -79.3795)])
        ls = a.parse(page, url)
        self.assertEqual([(l.unit_label, l.beds, l.rent, l.sqft, l.available, l.rent_is_floor) for l in ls],
                         [("1 Bedroom", 1, 1750, None, None, True), ("Unit 804", 2, 2450, 900, "2026-11-01", False)])
        self.assertTrue(ls[0].source_url.endswith("/listing/71-talara-drive-1-toronto-a"))
        self.assertTrue(a.aggregator)
        self.assertEqual([beds_of(t) for t in ("A — Bachelor", "A — 1 Bedroom Plus Den", "A — Three Bedroom", "A — Parking")], [0, 1, 3, None])

    def test_viewit_ads_and_page(self):
        from collect.adapters.viewit import ViewitAdapter, ads
        sm = (b"<loc>https://www.viewit.ca/1704VictoriaParkAve-Toronto-2bdrm-VIT=5732</loc>"
              b"<loc>https://www.viewit.ca/20SamuelWoodWay-Etobicoke-Bach-VIT=243709</loc>"
              b"<loc>https://www.viewit.ca/1MainSt-London-1bdrm-VIT=7</loc><loc>https://www.viewit.ca/9ASt-Toronto-Room-VIT=8</loc>")
        self.assertEqual([(b, v) for _, b, v in ads(sm)], [(2, 5732), (0, 243709)])
        page = (b'<div class="listingHead-price"> <span id="ctl00_ContentMain_spanMoney">$</span>2299\n   <p> And up.&nbsp;Some util. Incl. </p></div>'
                b'<h1 id="ctl00_ContentMain_lblAddress">1704 Victoria Park Ave</h1>'
                b'<input type="hidden" id="ctl00_ContentMain_City" value="Toronto" /><div lat="43.7347471" lng="-79.3067987"></div>')
        a = ViewitAdapter(seen=SEEN)
        url = "https://www.viewit.ca/1704VictoriaParkAve-Toronto-2bdrm-VIT=5732"
        (l,) = a.parse(page, url)
        self.assertEqual((l.address, l.beds, l.rent, l.rent_is_floor, l.source_url), ("1704 Victoria Park Ave, Toronto", 2, 2299, True, url))
        self.assertEqual(a.parse(b"<html></html>", url), [])

    def test_viewit_reuses_a_fresh_page_and_refetches_an_old_one(self):
        from collect.adapters.viewit import ViewitAdapter, DAY

        class Fake:
            def __init__(self, age): self.age, self.got = age, []
            def cached(self, url): return (b"cached", {"status": 200, "fetched_epoch": 1000 * DAY - self.age})
            def get(self, url): self.got.append(url); return b"fresh"

        a = ViewitAdapter(now=lambda: 1000 * DAY)
        self.assertEqual(a._page(Fake(2 * DAY), "u", 5732), b"cached")
        old = Fake(9 * DAY)
        self.assertEqual(a._page(old, "u", 5732), b"fresh")
        self.assertEqual(old.got, ["u"])

    def test_padmapper_reads_until_refused(self):
        from collect.adapter import Blocked
        from collect.adapters.padmapper import PadmapperAdapter
        row = {"address": "950 Lansdowne Ave", "city": "Toronto", "lat": 43.67, "lng": -79.45, "min_price": 2099, "max_price": 3809,
               "min_bedrooms": 1, "max_bedrooms": 3, "min_square_feet": 481, "url": "/apartment-buildings/p1/the-diamond"}
        page = ("<script>window.__PRELOADED_STATE__ = " + json.dumps({"currentSearch": {"listables": {"matching": 2070, "listables": [row]}}}) + ";</script>").encode()

        class Fake:
            asked = []
            def get(self, url):
                self.asked.append(url)
                if url.endswith("page=3"):
                    raise Blocked(url + ": HTTP 429")
                return page

        a, f = PadmapperAdapter(seen=SEEN), Fake()
        got = list(a.fetch(f))
        self.assertEqual(len(got), 2)                       # pages 1 and 2 kept, page 3 refused, no error
        (l,) = a.parse(page, "u")
        self.assertEqual((l.address, l.unit_label, l.beds, l.rent, l.sqft, l.rent_is_floor),
                         ("950 Lansdowne Ave, Toronto", "1 bedroom to 3 bedroom", 1, 2099, 481, True))

        class Shut:
            def get(self, url): raise Blocked("no")
        with self.assertRaises(Blocked):
            list(a.fetch(Shut()))
