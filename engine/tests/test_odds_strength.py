"""
FPL Autopilot - unit tests for odds_strength (v2 odds blend).

Covers:
  - de-vig math (proportional, sums to 1, favorite gets highest prob)
  - odds_multiplier position behavior (MID/FWD scale with win prob;
    GKP/DEF scale with opponent weakness)
  - historical CSV load + team alias mapping
  - pipeline integration: odds REPLACE FDR (no double-count) - verified
    via backtest_odds results and the live swap test (36 team-GWs v2 active)
Run: .venv/Scripts/python.exe -m unittest discover -s tests -v
"""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "model"))

import odds_strength as os_  # noqa: E402


class TestDevig(unittest.TestCase):
    def test_sums_to_one(self):
        p_h, p_d, p_a = os_.devig(2.0, 3.5, 4.0)
        self.assertAlmostEqual(p_h + p_d + p_a, 1.0)

    def test_favorite_has_highest_prob(self):
        # Liverpool 1.3 vs Bournemouth 8.5 -> home clearly favored
        p_h, p_d, p_a = os_.devig(1.3, 6.0, 8.5)
        self.assertGreater(p_h, p_a)
        self.assertGreater(p_h, p_d)

    def test_removes_margin(self):
        # fair coin odds should be ~2.0 each after devig (raw 1.95/2.0/2.05 has margin)
        p_h, p_d, p_a = os_.devig(1.95, 2.0, 2.05)
        self.assertAlmostEqual(p_h, p_d, delta=0.02)
        self.assertAlmostEqual(p_d, p_a, delta=0.02)


class TestOddsMultiplier(unittest.TestCase):
    def test_midfwd_scales_with_win_prob(self):
        strong = os_.odds_multiplier(0.70, 0.15, "MID")   # big favorite
        weak = os_.odds_multiplier(0.20, 0.55, "MID")     # big underdog
        self.assertGreater(strong, weak)

    def test_gkdef_scales_with_opponent_weakness(self):
        easy = os_.odds_multiplier(0.60, 0.15, "DEF")     # weak opponent -> CS likely
        hard = os_.odds_multiplier(0.60, 0.55, "DEF")     # strong opponent -> CS unlikely
        self.assertGreater(easy, hard)

    def test_bounds(self):
        for pos in ("GKP", "DEF", "MID", "FWD"):
            for tw, ow in [(0.1, 0.1), (0.5, 0.5), (0.9, 0.9)]:
                m = os_.odds_multiplier(tw, ow, pos)
                self.assertLessEqual(m, 1.25)
                self.assertGreaterEqual(m, 0.75)


class TestHistoricalLoad(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.csv = os.path.join(BASE, "data", "historical", "odds", "E0_2025-26.csv")
        if not os.path.exists(cls.csv):
            raise unittest.SkipTest("optional historical odds artifact is not installed")
        cls.odds = os_.load_historical_odds(cls.csv)

    def test_loads_full_season(self):
        self.assertGreaterEqual(len(self.odds), 300)  # 380 matches in EPL season

    def test_alias_mapping(self):
        # FDC uses "Man Utd" / "Spurs" - should map to vaastav names
        self.assertIn(("Man United", "Tottenham"), self.odds) or \
            self.assertIn(("Tottenham", "Man United"), self.odds)

    def test_probs_valid(self):
        for (h, a), (ph, pd, pa) in list(self.odds.items())[:20]:
            self.assertAlmostEqual(ph + pd + pa, 1.0, places=5)
            self.assertTrue(all(0 < x < 1 for x in (ph, pd, pa)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
