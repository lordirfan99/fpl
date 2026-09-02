"""
FPL Autopilot - unit tests for chips wiring (execution/chips.py).

Covers:
  - display name -> API code mapping (Bench Boost -> bboost, NOT "benchboost")
  - chip_type routing (wildcard/freehit = transfer POST; bboost/3xc = team POST)
  - availability windows from bootstrap (wildcard/freehit start GW2, NOT GW1)
  - endpoint construction
Run: .venv/Scripts/python.exe -m unittest discover -s tests -v
"""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "execution"))

import chips  # noqa: E402


class TestChipMapping(unittest.TestCase):
    def test_display_to_api_code(self):
        self.assertEqual(chips.CHIP_API["Wildcard"], "wildcard")
        self.assertEqual(chips.CHIP_API["Free Hit"], "freehit")
        self.assertEqual(chips.CHIP_API["Bench Boost"], "bboost")
        self.assertEqual(chips.CHIP_API["Triple Captain"], "3xc")
        # the API does NOT accept "benchboost"/"triplecaptain" as codes
        self.assertNotIn("benchboost", set(chips.CHIP_API.values()))
        self.assertNotIn("triplecaptain", set(chips.CHIP_API.values()))

    def test_chip_api_code(self):
        self.assertEqual(chips.chip_api_code("Bench Boost"), "bboost")
        self.assertIsNone(chips.chip_api_code("Unknown"))


class TestChipType(unittest.TestCase):
    def test_transfer_type_chips(self):
        self.assertEqual(chips.chip_type("wildcard"), "transfer")
        self.assertEqual(chips.chip_type("freehit"), "transfer")

    def test_team_type_chips(self):
        self.assertEqual(chips.chip_type("bboost"), "team")
        self.assertEqual(chips.chip_type("3xc"), "team")


class TestChipEndpoint(unittest.TestCase):
    def test_transfer_chip_endpoint(self):
        self.assertEqual(chips.chip_endpoint("wildcard"), "/api/transfers/")
        self.assertEqual(chips.chip_endpoint("freehit"), "/api/transfers/")

    def test_team_chip_endpoint(self):
        self.assertEqual(chips.chip_endpoint("bboost", 2797967),
                         "/api/my-team/2797967/")
        self.assertEqual(chips.chip_endpoint("3xc", 2797967),
                         "/api/my-team/2797967/")


class TestChipAvailability(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.windows = chips.fetch_chip_windows()
            cls.live = True
        except Exception:
            cls.windows = {"wildcard": (2, 19), "freehit": (2, 19),
                           "bboost": (1, 19), "3xc": (1, 19)}
            cls.live = False

    def test_live_fetch(self):
        # if we have live data, all 4 chips must be present
        if self.live:
            for code in ("wildcard", "freehit", "bboost", "3xc"):
                self.assertIn(code, self.windows)

    def test_gw1_wildcard_not_playable(self):
        # verified 2026-08-05: wildcard starts GW2
        self.assertFalse(chips.chip_playable_in("wildcard", 1, self.windows))

    def test_gw1_freehit_not_playable(self):
        self.assertFalse(chips.chip_playable_in("freehit", 1, self.windows))

    def test_gw1_bboost_playable(self):
        self.assertTrue(chips.chip_playable_in("bboost", 1, self.windows))

    def test_gw1_3xc_playable(self):
        self.assertTrue(chips.chip_playable_in("3xc", 1, self.windows))

    def test_gw2_wildcard_playable(self):
        self.assertTrue(chips.chip_playable_in("wildcard", 2, self.windows))


if __name__ == "__main__":
    unittest.main(verbosity=2)
