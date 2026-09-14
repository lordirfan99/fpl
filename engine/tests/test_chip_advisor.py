"""
FPL Autopilot - unit tests for chip advisor (model/chip_advisor.py).

Covers:
  - DGW/BGW detection from fixtures
  - TC suggestion: captain >= 7 xPts + captain's club has DGW
  - BB suggestion: 3+ bench >= 4 xPts + bench club has DGW
  - FH suggestion: 4+ squad players blanking
  - WC suggestion: 4+ injured/doubtful
  - no suggestion when no opportunity
  - priority ordering (3xc beats bboost when both fire)
Run: .venv/Scripts/python.exe -m unittest discover -s tests -v
"""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "model"))

import chip_advisor  # noqa: E402


def _fx(gw, home_team, away_team):
    """One fixture dict."""
    return {"event": gw, "team_h": home_team, "team_a": away_team}


def _p(pid, club, xpts, pos="MID", status=100):
    return {"id": pid, "name": f"P{pid}", "position": pos, "club": club, "xpts": xpts, "status": status}


class TestDetect(unittest.TestCase):
    def test_dgw_detection(self):
        # team 1 plays twice in GW5 (vs 2 and vs 3)
        fx = [_fx(5, 1, 2), _fx(5, 3, 1), _fx(5, 4, 5)]
        self.assertEqual(chip_advisor.detect_dgw(fx, 5), {1})
        self.assertEqual(chip_advisor.detect_dgw(fx, 4), set())

    def test_bgw_detection(self):
        # team 1 blank in GW5, 20-team league
        fx = [_fx(5, 2, 3), _fx(5, 4, 5)]
        bgw = chip_advisor.detect_bgw(fx, 5)
        self.assertIn(1, bgw)
        self.assertNotIn(2, bgw)
        self.assertEqual(len(bgw), 16)  # 20 teams - 4 playing (2,3,4,5)


class TestAdvise(unittest.TestCase):
    def test_triple_captain_dgw(self):
        # captain (id 100, club 1) has 8.0 xPts, club 1 has DGW in GW5
        fx = [_fx(5, 1, 2), _fx(5, 3, 1), _fx(5, 4, 5)]
        plan = {"target_starters": [_p(100, 1, 8.0)],
                "bench": [_p(200, 4, 2.0)],
                "captain": _p(100, 1, 8.0)}
        sug = chip_advisor.advise(plan, fx, 5, 1)
        self.assertIsNotNone(sug)
        self.assertEqual(sug["chip"], "3xc")

    def test_no_tc_without_dgw_unless_captain_is_clear_of_the_field(self):
        # BEHAVIOUR CHANGE: a high-xPts captain with no DGW used to be silent.
        # A season can schedule no DGW at all, so the chip would simply expire.
        # TC may now fire on a single gameweek, but only when the captain is
        # clearly ahead of the next-best starter - here he is not.
        fx = [_fx(5, 1, 2), _fx(5, 4, 5)]
        plan = {"target_starters": [_p(100, 1, 8.0), _p(101, 4, 7.6)],
                "bench": [_p(200, 4, 2.0)],
                "captain": _p(100, 1, 8.0)}
        self.assertIsNone(chip_advisor.advise(plan, fx, 5, 1))

    def test_bench_boost_dgw(self):
        # 3 bench players >= 4 xPts, one with DGW club
        fx = [_fx(5, 1, 2), _fx(5, 3, 1), _fx(5, 4, 5)]
        plan = {"target_starters": [_p(100, 6, 3.0)],
                "bench": [_p(1, 1, 5.0), _p(2, 3, 4.5), _p(3, 4, 4.2)],
                "captain": _p(100, 6, 3.0)}
        sug = chip_advisor.advise(plan, fx, 5, 1)
        self.assertEqual(sug["chip"], "bboost")

    def test_free_hit_bgw(self):
        # 4 squad players blank in GW5
        fx = [_fx(5, 2, 3), _fx(5, 4, 5)]
        squad = [_p(1, 1, 3.0), _p(2, 1, 3.0), _p(3, 1, 3.0), _p(4, 1, 3.0), _p(5, 2, 3.0)]
        plan = {"target_starters": squad[:4], "bench": squad[4:], "captain": squad[0]}
        sug = chip_advisor.advise(plan, fx, 5, 1)
        self.assertEqual(sug["chip"], "freehit")

    def test_wildcard_injuries(self):
        fx = [_fx(5, 1, 2)]
        squad = [_p(1, 1, 3.0, status=20), _p(2, 1, 3.0, status=30),
                 _p(3, 1, 3.0, status=40), _p(4, 1, 3.0, status=10),
                 _p(5, 1, 3.0, status=100)]
        plan = {"target_starters": squad[:4], "bench": squad[4:], "captain": squad[0]}
        sug = chip_advisor.advise(plan, fx, 5, 1)
        self.assertEqual(sug["chip"], "wildcard")

    def test_no_suggestion(self):
        fx = [_fx(5, 1, 2)]
        squad = [_p(1, 1, 3.0), _p(2, 1, 3.0), _p(3, 1, 3.0), _p(4, 1, 3.0)]
        plan = {"target_starters": squad[:4], "bench": [], "captain": squad[0]}
        self.assertIsNone(chip_advisor.advise(plan, fx, 5, 1))

    def test_priority_3xc_over_bboost(self):
        # both TC and BB fire -> 3xc wins (SUGGEST_PRIORITY)
        fx = [_fx(5, 1, 2), _fx(5, 3, 1)]
        plan = {"target_starters": [_p(100, 1, 8.0)],
                "bench": [_p(1, 1, 5.0), _p(2, 3, 4.5), _p(3, 4, 4.2)],
                "captain": _p(100, 1, 8.0)}
        sug = chip_advisor.advise(plan, fx, 5, 1)
        self.assertEqual(sug["chip"], "3xc")


class TestMarketWideSuggestions(unittest.TestCase):
    """The user's question: does the advisor suggest TRANSFER IN a strong DGW
    player who is NOT in our squad, then use the chip?"""

    def _dgw_fx(self):
        # club 16 (e.g. Man Utd) has a DGW in GW5; everyone else plays once
        fx = [_fx(5, 16, 2), _fx(5, 3, 16)]
        for h, a in [(4, 5), (6, 7), (8, 9), (10, 11), (12, 13), (14, 15), (17, 18), (19, 20)]:
            fx.append(_fx(5, h, a))
        return fx

    def _market(self):
        # best DGW player = Haaland (id 555, club 16, 10.2 xPts, 150 cost)
        return [
            {"id": 555, "name": "Haaland", "position": "FWD", "club": 16, "cost": 150, "xpts": 10.2},
            {"id": 100, "name": "CurrentCap", "position": "MID", "club": 6, "cost": 100, "xpts": 5.0},
            {"id": 101, "name": "CheapDGW", "position": "MID", "club": 16, "cost": 50, "xpts": 4.8},
        ]

    def _market_squad(self):
        # P0.6: squad dicts must carry position (same-position sell required);
        # id 200 is the FWD used to fund Haaland, id 100 the MID for CheapDGW.
        return [
            {"id": 100, "position": "MID", "club": 6, "selling_price": 100, "xpts_horizon": 10.0},
            {"id": 200, "position": "FWD", "club": 4, "selling_price": 100, "xpts_horizon": 8.0},
        ]

    def test_tc_suggests_transfer_in_when_captain_not_dgw(self):
        # captain (club 6) has NO DGW, but Haaland (club 16) has DGW and is
        # NOT in squad -> advisor must suggest transfer in + TC
        fx = self._dgw_fx()
        plan = {"target_starters": [_p(100, 6, 5.0)],
                "bench": [_p(200, 4, 3.0)],
                "captain": _p(100, 6, 5.0)}
        squad = self._market_squad()
        sug = chip_advisor.advise(plan, fx, 5, 1, players=self._market(), squad=squad, bank=60)
        self.assertIsNotNone(sug)
        self.assertEqual(sug["chip"], "3xc")
        self.assertIn("Transfer IN", sug["detail"])
        self.assertEqual(sug["transfer_in"]["id"], 555)  # Haaland
        self.assertEqual(sug["transfer_out"]["position"], "FWD")

    def test_tc_not_suggested_when_unaffordable(self):
        # Haaland costs 150; bank 0 + best FWD sell 100 = 100 < 150 -> unaffordable.
        # TC is NOT suggested (unaffordable = noise); the advisor falls through
        # to the actionable alternative (BB with cheap DGW bench option).
        fx = self._dgw_fx()
        plan = {"target_starters": [_p(100, 6, 5.0)],
                "bench": [_p(200, 4, 3.0), _p(201, 5, 3.0), _p(202, 7, 3.0)],
                "captain": _p(100, 6, 5.0)}
        squad = self._market_squad()
        sug = chip_advisor.advise(plan, fx, 5, 1, players=self._market(), squad=squad, bank=0)
        self.assertIsNotNone(sug)
        self.assertNotEqual(sug["chip"], "3xc")
        self.assertEqual(sug["chip"], "bboost")
        self.assertIn("transfer IN", sug["detail"])
        self.assertEqual(sug["transfer_in"]["id"], 101)  # CheapDGW

    def test_tc_suggests_transfer_in_when_affordable(self):
        # bank 60 + sell 100 = 160 >= 150 -> affordable
        fx = self._dgw_fx()
        plan = {"target_starters": [_p(100, 6, 5.0)],
                "bench": [_p(200, 4, 3.0)],
                "captain": _p(100, 6, 5.0)}
        squad = self._market_squad()
        sug = chip_advisor.advise(plan, fx, 5, 1, players=self._market(), squad=squad, bank=60)
        self.assertEqual(sug["chip"], "3xc")
        self.assertIn("Transfer IN", sug["detail"])
        self.assertEqual(sug["transfer_in"]["id"], 555)

    def test_bb_suggests_cheap_dgw_bench(self):
        # bench weak (no DGW) -> suggest cheap DGW bench option
        fx = self._dgw_fx()
        plan = {"target_starters": [_p(100, 6, 5.0)],
                "bench": [_p(200, 4, 3.0), _p(201, 5, 3.0), _p(202, 7, 3.0)],
                "captain": _p(100, 6, 5.0)}
        squad = self._market_squad()
        sug = chip_advisor.advise(plan, fx, 5, 1, players=self._market(), squad=squad, bank=0)
        self.assertEqual(sug["chip"], "bboost")
        self.assertIn("transfer IN", sug["detail"])
        self.assertEqual(sug["transfer_in"]["id"], 101)  # CheapDGW
        self.assertEqual(sug["transfer_out"]["position"], "MID")

    def test_used_chip_never_suggested(self):
        # D26: TC already played this season -> advisor must NOT suggest 3xc
        # even though captain has a DGW and high xPts.
        fx = [_fx(5, 16, 2), _fx(5, 3, 16)]
        plan = {"target_starters": [_p(100, 16, 8.0)],
                "bench": [_p(200, 4, 3.0)],
                "captain": _p(100, 16, 8.0)}
        sug = chip_advisor.advise(plan, fx, 5, 1, used_chips={"3xc": 2})
        self.assertIsNone(sug)

    def test_unused_chip_still_suggested(self):
        # D26 sanity: with no used chips the same setup suggests TC
        fx = [_fx(5, 16, 2), _fx(5, 3, 16)]
        plan = {"target_starters": [_p(100, 16, 8.0)],
                "bench": [_p(200, 4, 3.0)],
                "captain": _p(100, 16, 8.0)}
        sug = chip_advisor.advise(plan, fx, 5, 1, used_chips={})
        self.assertEqual(sug["chip"], "3xc")


class TestSingleGameweekTriggers(unittest.TestCase):
    """A season can schedule no DGW/BGW at all (2026/27 as of GW5 has none).

    A DGW-only advisor is silent for the whole first half while the chips
    expire unused, which is the regression these tests pin down.
    """

    def test_tc_fires_on_standout_captain_without_any_dgw(self):
        fx = [_fx(5, 1, 2), _fx(5, 3, 4)]  # clean single gameweek
        self.assertEqual(chip_advisor.detect_dgw(fx, 5), set())
        starters = [_p(100, 1, 8.2), _p(2, 2, 5.0), _p(3, 3, 4.4)]
        plan = {"target_starters": starters, "bench": [], "captain": starters[0]}
        sug = chip_advisor.advise(plan, fx, 5, 1)
        self.assertIsNotNone(sug)
        self.assertEqual(sug["chip"], "3xc")
        self.assertTrue(sug["single_gw"])

    def test_tc_stays_silent_when_captain_is_not_clear_of_the_field(self):
        """High xPts alone is not enough - the alternative is just captaining
        the next-best player, so a thin margin makes the chip near-worthless."""
        fx = [_fx(5, 1, 2), _fx(5, 3, 4)]
        starters = [_p(100, 1, 8.2), _p(2, 2, 7.8)]
        plan = {"target_starters": starters, "bench": [], "captain": starters[0]}
        self.assertIsNone(chip_advisor.advise(plan, fx, 5, 1))

    def test_bb_fires_when_all_four_bench_players_are_nailed(self):
        fx = [_fx(5, 1, 2), _fx(5, 3, 4)]
        bench = [_p(1, 1, 4.5), _p(2, 2, 4.2), _p(3, 3, 4.0), _p(4, 4, 3.8)]
        plan = {"target_starters": [_p(100, 1, 5.0)], "bench": bench,
                "captain": _p(100, 1, 5.0)}
        sug = chip_advisor.advise(plan, fx, 5, 1, players=[_p(900, 1, 6.0)])
        self.assertIsNotNone(sug)
        self.assertEqual(sug["chip"], "bboost")
        self.assertTrue(sug["single_gw"])

    def test_bb_stays_silent_when_one_bench_player_is_a_blank(self):
        fx = [_fx(5, 1, 2), _fx(5, 3, 4)]
        bench = [_p(1, 1, 5.5), _p(2, 2, 5.2), _p(3, 3, 5.0), _p(4, 4, 0.5)]
        plan = {"target_starters": [_p(100, 1, 5.0)], "bench": bench,
                "captain": _p(100, 1, 5.0)}
        self.assertIsNone(chip_advisor.advise(plan, fx, 5, 1, players=[_p(900, 1, 6.0)]))

    def test_dgw_evidence_still_wins_over_the_single_gw_fallback(self):
        """The fallback must not mask a real double gameweek."""
        fx = [_fx(5, 1, 2), _fx(5, 3, 1)]  # team 1 doubles
        starters = [_p(100, 1, 8.2), _p(2, 2, 5.0)]
        plan = {"target_starters": starters, "bench": [], "captain": starters[0]}
        sug = chip_advisor.advise(plan, fx, 5, 1)
        self.assertEqual(sug["chip"], "3xc")
        self.assertNotIn("single_gw", sug)
        self.assertIn("DGW", sug["detail"])

    def test_expiry_warning_appears_near_the_window_deadline(self):
        fx = [_fx(17, 1, 2), _fx(17, 3, 4)]
        starters = [_p(100, 1, 8.2), _p(2, 2, 5.0)]
        plan = {"target_starters": starters, "bench": [], "captain": starters[0]}
        windows = {"3xc": [(1, 19), (20, 38)]}
        sug = chip_advisor.advise(plan, fx, 17, 1, windows=windows)
        self.assertEqual(sug["expires_gw"], 19)
        self.assertIn("expires after GW19", sug["detail"])

    def test_no_expiry_warning_early_in_the_window(self):
        fx = [_fx(5, 1, 2), _fx(5, 3, 4)]
        starters = [_p(100, 1, 8.2), _p(2, 2, 5.0)]
        plan = {"target_starters": starters, "bench": [], "captain": starters[0]}
        windows = {"3xc": [(1, 19), (20, 38)]}
        sug = chip_advisor.advise(plan, fx, 5, 1, windows=windows)
        self.assertNotIn("expires after", sug["detail"])

    def test_chip_deadline_resolves_the_active_window(self):
        windows = {"bboost": [(1, 19), (20, 38)]}
        self.assertEqual(chip_advisor.chip_deadline("bboost", 5, windows), 19)
        self.assertEqual(chip_advisor.chip_deadline("bboost", 25, windows), 38)
        self.assertIsNone(chip_advisor.chip_deadline("bboost", 5, None))
        self.assertIsNone(chip_advisor.chip_deadline("freehit", 5, windows))

    def test_already_used_chip_is_never_suggested(self):
        fx = [_fx(5, 1, 2), _fx(5, 3, 4)]
        starters = [_p(100, 1, 8.2), _p(2, 2, 5.0)]
        plan = {"target_starters": starters, "bench": [], "captain": starters[0]}
        self.assertIsNone(chip_advisor.advise(
            plan, fx, 5, 1, used_chips={"3xc": [3]}, windows={"3xc": [(1, 19)]}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
