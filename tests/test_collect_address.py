"""The address normaliser is where matching errors start; it gets the most cases."""
import csv
import unittest
from pathlib import Path

from collect import address
from collect.address import AddressKey, parse

REG = Path(__file__).resolve().parent.parent / "data" / "registry" / "registration.csv"


def falls_on(listing, registry):
    return address.relation(parse(listing), parse(registry)) is not None


# (landlord spelling, registry spelling) that must match.
MATCH = [
    ("181 Gerrard Street East, Toronto, ON M5A 2E4", "181-183  GERRARD ST E"),
    ("183 Gerrard St. E.", "181-183  GERRARD ST E"),
    ("181-183 Gerrard Street East", "181-183  GERRARD ST E"),
    ("2A Carluke Crescent", "2 A  CARLUKE CRES"),
    ("2 A Carluke Cres.", "2 A  CARLUKE CRES"),
    ("100 High Park Ave.", "100  HIGH PARK AVE "),
    ("100 High Park Avenue, Toronto, ON", "100  HIGH PARK AVE "),
    ("355 St. Clair Avenue West", "355  ST CLAIR AVE W"),
    ("355 Saint Clair Ave W", "355  ST CLAIR AVE W"),
    ("355 St Clair Ave. W., Toronto ON M4V 1S3", "355  ST CLAIR AVE W"),
    ("Suite 1204 - 355 St. Clair Avenue West", "355  ST CLAIR AVE W"),
    ("Unit 5, 77 Davisville Avenue", "77  DAVISVILLE AVE "),
    ("77 Davisville Ave #1204", "77  DAVISVILLE AVE "),
    ("77 Davisville Ave, Apt 3B", "77  DAVISVILLE AVE "),
    ("1204-77 Davisville Ave", "77  DAVISVILLE AVE "),
    ("77 Davisville Avenue, Toronto, Ontario, Canada", "77  DAVISVILLE AVE "),
    ("77 DAVISVILLE AVE.", "77  DAVISVILLE AVE "),
    ("145 St. George Street", "145  ST GEORGE ST "),
    ("25 St Dennis Drive", "25  ST DENNIS DR "),
    ("11 33rd Street", "11  THIRTY THIRD ST "),
    ("100 25th St, Etobicoke", "100  TWENTY FIFTH ST "),
    ("121 4th Street", "121  FOURTH ST "),
    ("20 Fourteenth Street", "20  FOURTEENTH ST "),
    ("484 Avenue Road", "484  AVENUE RD "),
    ("140 The Esplanade", "140  THE ESPLANADE  "),
    ("140 Esplanade", "140  THE ESPLANADE  "),
    ("311 The West Mall", "311  THE WEST MALL  "),
    ("16 The Donway East", "16  THE DONWAY  E"),
    ("4 Sherbourne Street North", "4  SHERBOURNE ST N"),
    ("2663 Lakeshore Blvd. W.", "2663  LAKE SHORE BLVD W"),
    ("1143 O'Connor Drive", "1143  O'CONNOR DR "),
    ("384 Mt. Pleasant Rd", "384  MOUNT PLEASANT RD "),
    ("87 Thorncliffe Park Drive", "85-95  THORNCLIFFE PARK DR "),
    ("3015A Queen Street East", "3015 A  QUEEN ST E"),
    ("235 Bloor St", "235  BLOOR ST E"),  # direction left out: loose
    ("10 Yorkwoods Gate", "10  YORKWOODS GT "),
    ("765 Browns Line", "765  BROWNS LINE "),
    ("150 Dan Leckie Way", "150  DAN LECKIE WAY "),
    ("196 Eglinton Ave E, North York", "196  EGLINTON AVE E"),
    ("806 Kennedy Road, Scarborough, ON M1K 2C9", "806  KENNEDY RD "),
    ("123 D’Arcy St", "123  D'ARCY ST "),
    ("2 Thirty-Third Street", "2  THIRTY THIRD ST "),
]

# Pairs that must NOT match.
NO_MATCH = [
    ("100 High Park Ave", "100  HIGH PARK BLVD "),
    ("25 St Dennis Dr", "25  DENNIS ST "),
    ("25 Dennis St", "25  ST DENNIS DR "),
    ("182 Gerrard St E", "181-183  GERRARD ST E"),   # across the street from an odd range
    ("185 Gerrard St E", "181-183  GERRARD ST E"),
    ("181 Gerrard St W", "181-183  GERRARD ST E"),
    ("2 Carluke Cres", "2 A  CARLUKE CRES"),
    ("2B Carluke Cres", "2 A  CARLUKE CRES"),
    ("3015 Queen St E", "3015 A  QUEEN ST E"),
    ("355 St Clair Ave E", "355  ST CLAIR AVE W"),
    ("355 Clair Ave W", "355  ST CLAIR AVE W"),
    ("145 St George St", "145  GEORGE ST "),
    ("77 Davisville Ave", "78  DAVISVILLE AVE "),
    ("77 Davisville Rd", "77  DAVISVILLE AVE "),
    ("1204-77 Davisville Ave", "1204  DAVISVILLE AVE "),
    ("11 23rd St", "11  THIRTY THIRD ST "),
    ("4 Sherbourne St S", "4  SHERBOURNE ST N"),
    ("4 Sherbourne St N", "4  SHERBOURNE ST "),        # listing states a direction the building lacks
    ("484 Avenue Rd", "484  AVENUE ST "),
    ("10 Yorkwoods Gt", "10  YORKWOODS GDNS "),
]


class Pairs(unittest.TestCase):
    def test_enough_cases(self):
        self.assertGreaterEqual(len(MATCH) + len(NO_MATCH), 40)

    def test_pairs_that_match(self):
        for listing, registry in MATCH:
            with self.subTest(listing=listing, registry=registry):
                self.assertTrue(falls_on(listing, registry), f"{parse(listing)} vs {parse(registry)}")

    def test_pairs_that_must_not_match(self):
        for listing, registry in NO_MATCH:
            with self.subTest(listing=listing, registry=registry):
                self.assertFalse(falls_on(listing, registry), f"{parse(listing)} vs {parse(registry)}")


class Keys(unittest.TestCase):
    def test_structured_key(self):
        self.assertEqual(parse("181 Gerrard Street East, Toronto, ON M5A 2E4"),
                         AddressKey(181, 181, None, "GERRARD", "ST", "E"))
        self.assertEqual(parse("181-183  GERRARD ST E"), AddressKey(181, 183, None, "GERRARD", "ST", "E"))
        self.assertEqual(parse("2 A  CARLUKE CRES"), AddressKey(2, 2, "A", "CARLUKE", "CRES", None))

    def test_saint_is_not_street(self):
        k = parse("355 St. Clair Avenue West")
        self.assertEqual((k.street, k.type, k.direction), ("SAINT CLAIR", "AVE", "W"))
        self.assertEqual(parse("45  ST LAWRENCE ST ").street, "SAINT LAWRENCE")

    def test_avenue_road_keeps_avenue_as_name(self):
        self.assertEqual((parse("484 Avenue Road").street, parse("484 Avenue Road").type), ("AVENUE", "RD"))

    def test_typeless_streets(self):
        self.assertEqual(parse("299  THE KINGSWAY  "), AddressKey(299, 299, None, "KINGSWAY", None, None))
        self.assertEqual(parse("16  THE DONWAY  E").direction, "E")

    def test_ordinals(self):
        self.assertEqual(parse("45  FORTY SECOND ST ").street, "42")
        self.assertEqual(parse("45 42nd Street").street, "42")
        self.assertEqual(parse("12-14  THIRTY NINTH ST ").street, "39")

    def test_unit_forms_are_dropped(self):
        for s in ("Unit 1204, 77 Davisville Ave", "#1204 - 77 Davisville Ave", "77 Davisville Ave Suite 1204",
                  "PH 3 - 77 Davisville Ave", "1204-77 Davisville Ave", "Apt. 3, 77 Davisville Ave"):
            with self.subTest(s=s):
                self.assertEqual(parse(s), AddressKey(77, 77, None, "DAVISVILLE", "AVE", None))

    def test_range_wider_than_the_limit_is_a_unit_number(self):
        self.assertEqual(parse("5-77 Davisville Ave").lo, 77)
        self.assertEqual(parse("182-250  BRENYON WAY ").hi, 250)

    def test_rejects(self):
        for s in ("", "   ", "Davisville Village Residences", "77", "Toronto, ON"):
            with self.subTest(s=s):
                with self.assertRaises(address.AddressError):
                    parse(s)


@unittest.skipUnless(REG.exists(), "registry file not present")
class Registry(unittest.TestCase):
    def test_every_registry_address_parses(self):
        with open(REG, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        failed = []
        for r in rows:
            try:
                k = parse(r["SITE_ADDRESS"])
            except address.AddressError as e:
                failed.append((r["RSN"], r["SITE_ADDRESS"], str(e)))
                continue
            # Each registry key must match its own address string.
            self.assertIsNotNone(address.relation(k, k), r["SITE_ADDRESS"])
        self.assertEqual(failed, [])


if __name__ == "__main__":
    unittest.main()
