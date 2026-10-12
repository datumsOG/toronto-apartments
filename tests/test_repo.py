"""The committed registry files are the denominator; if they change shape, say so loudly."""
import csv
import unittest
from pathlib import Path

REG = Path(__file__).resolve().parent.parent / "data" / "registry"


class RegistryFiles(unittest.TestCase):
    def test_registration_has_join_key_and_units(self):
        with open(REG / "registration.csv", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        self.assertGreater(len(rows), 3000)
        self.assertEqual(len({r["RSN"] for r in rows}), len(rows))
        for col in ("SITE_ADDRESS", "CONFIRMED_UNITS", "PROPERTY_TYPE", "PROP_MANAGEMENT_COMPANY_NAME"):
            self.assertIn(col, rows[0])

    def test_evaluations_carry_coordinates(self):
        with open(REG / "evaluations.csv", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        located = sum(1 for r in rows if r["LATITUDE"].strip() and r["LONGITUDE"].strip())
        self.assertGreater(located / len(rows), 0.9)


if __name__ == "__main__":
    unittest.main()
