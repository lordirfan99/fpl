"""
FPL Autopilot - unit tests (run with repo venv python).

Run: .venv/Scripts/python.exe -m unittest discover -s tests -v

Covers the two silent-regression traps:
  1. Lineup picks ordering (GKP->DEF->MID->FWD) - API rejects any other order.
  2. Transfer solver hit-threshold discipline (-4 only when net gain > threshold).

NOTE: in the picks payload, "position" is the numeric slot (1-15); the
element type lives on the plan's player dicts ("position": "GKP"/"DEF"/...).
"""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "execution"))
sys.path.insert(0, os.path.join(BASE, "optimizer"))

from executor import build_picks, POS_RANK, is_success  # noqa: E402
from transfer_solver import solve_transfers  # noqa: E402


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class TestResponseHandling(unittest.TestCase):
    """FPL returns 202 Accepted for lineup/transfer POSTs (async apply)."""

    def test_202_is_success(self):
        self.assertTrue(is_success(FakeResponse(202)))
        self.assertTrue(is_success(FakeResponse(200)))
        self.assertTrue(is_success(None))  # no-op (no transfers needed)

    def test_3xx_4xx_5xx_are_failure(self):
        self.assertFalse(is_success(FakeResponse(300)))
        self.assertFalse(is_success(FakeResponse(400)))
        self.assertFalse(is_success(FakeResponse(500)))


def _p(pid, name, pos, xpts, xh, cost=60, club=1):
    return {"id": pid, "name": name, "position": pos, "club": club, "cost": cost,
            "xpts": xpts, "xpts_horizon": xh, "selling_price": cost, "purchase_price": cost}


class TestLineupOrdering(unittest.TestCase):
    """The API rejects picks unless ordered ascending element_type (GKP->DEF->MID->FWD)."""

    def _make_plan(self, starters, bench=None):
        captain = max(starters, key=lambda p: p["xpts"])
        vice = max((p for p in starters if p["id"] != captain["id"]),
                   key=lambda p: p["xpts"])
        return {
            "target_starters": starters,
            "bench": bench or [],
            "captain": captain,
            "vice": vice,
        }

    def _element_types_in_pick_order(self, plan, picks):
        players = {p["id"]: p for p in plan["target_starters"] + plan.get("bench", [])}
        return [players[pk["element"]]["position"] for pk in picks]

    def test_picks_are_ordered_gkp_def_mid_fwd(self):
        # deliberately shuffled input
        starters = [
            _p(11, "FWD1", "FWD", 4.0, 9.0),
            _p(1, "GK1", "GKP", 3.5, 8.0),
            _p(6, "MID1", "MID", 5.0, 11.0),
            _p(3, "DEF1", "DEF", 4.2, 9.5),
        ]
        plan = self._make_plan(starters)
        picks = build_picks(plan)
        types = self._element_types_in_pick_order(plan, picks)
        self.assertEqual(types[:4], ["GKP", "DEF", "MID", "FWD"])
        self.assertEqual([POS_RANK[t] for t in types], sorted(POS_RANK[t] for t in types))

    def test_captain_multiplier_and_bench_multiplier_zero(self):
        starters = [
            _p(1, "GK1", "GKP", 3.5, 8.0),
            _p(6, "MID1", "MID", 5.0, 11.0),  # captain (highest xPts)
        ]
        bench = [_p(20, "GK2", "GKP", 3.0, 7.0), _p(21, "FWD2", "FWD", 3.2, 7.5)]
        plan = self._make_plan(starters, bench)
        picks = build_picks(plan)
        cap_picks = [p for p in picks if p["is_captain"]]
        self.assertEqual(len(cap_picks), 1)
        self.assertEqual(cap_picks[0]["multiplier"], 2)
        bench_picks = [p for p in picks if p["position"] > len(starters)]
        self.assertTrue(all(p["multiplier"] == 0 for p in bench_picks))
        self.assertEqual(len(picks), 4)  # 2 starters + 2 bench

    def test_full_15_squad_shape(self):
        starters = (
            [_p(1, "GK1", "GKP", 3.5, 8.0)] +
            [_p(10 + i, f"DEF{i}", "DEF", 4.0, 9.0) for i in range(5)] +
            [_p(20 + i, f"MID{i}", "MID", 4.5, 10.0) for i in range(4)] +
            [_p(30, "FWD0", "FWD", 4.0, 9.5)]
        )
        bench = [_p(50, "GK2", "GKP", 3.0, 7.0),
                 _p(51, "MID9", "MID", 4.0, 9.0),
                 _p(52, "FWD4", "FWD", 3.5, 8.0),
                 _p(53, "FWD5", "FWD", 3.4, 7.8)]
        plan = self._make_plan(starters, bench)
        picks = build_picks(plan)
        self.assertEqual(len(picks), 15)
        self.assertEqual([p["position"] for p in picks], list(range(1, 16)))
        types = self._element_types_in_pick_order(plan, picks)
        self.assertEqual(types[:11], ["GKP"] + ["DEF"] * 5 + ["MID"] * 4 + ["FWD"])
        self.assertEqual(types[11:], ["GKP", "MID", "FWD", "FWD"])

    def test_outfield_bench_priority_is_not_resorted_by_position(self):
        starters = (
            [_p(1, "GK1", "GKP", 3.5, 8.0)] +
            [_p(10 + i, f"DEF{i}", "DEF", 4.0, 9.0) for i in range(4)] +
            [_p(20 + i, f"MID{i}", "MID", 4.5, 10.0) for i in range(4)] +
            [_p(30 + i, f"FWD{i}", "FWD", 4.0, 9.5) for i in range(2)]
        )
        bench = [_p(50, "GK2", "GKP", 3.0, 7.0),
                 _p(51, "FWD first", "FWD", 4.5, 9.0),
                 _p(52, "DEF second", "DEF", 4.0, 8.0),
                 _p(53, "MID third", "MID", 3.5, 7.0)]
        plan = self._make_plan(starters, bench)
        picks = build_picks(plan)
        self.assertEqual([p["element"] for p in picks[11:]], [50, 51, 52, 53])


class TestTransferHitDiscipline(unittest.TestCase):
    """Hits (-4) only taken when horizon gain exceeds hit_threshold; free moves need min_gain."""

    def test_hit_requires_gain_above_threshold(self):
        squad = [_p(1, "OLD", "MID", 4.0, 9.0, cost=60, club=1)]
        candidates = [
            _p(2, "NEW_SMALL", "MID", 4.1, 9.2, cost=61, club=2),   # gain 0.2 -> too small for a hit
            _p(3, "NEW_BIG", "MID", 5.0, 15.0, cost=70, club=3),    # gain 6.0 -> passes 5.0 threshold
        ]
        # no free transfers left -> every move is a hit; bank covers both candidates
        transfers, _, ft_left, notes = solve_transfers(squad, candidates, free_transfers=0, bank=15,
                                                       hit_threshold=5.0, min_gain=1.5)
        self.assertEqual(len(transfers), 1)
        self.assertEqual(transfers[0]["in_name"], "NEW_BIG")
        self.assertTrue(transfers[0]["hit"])

    def test_free_transfer_min_gain_blocks_noise_churn(self):
        squad = [_p(1, "OLD", "MID", 4.0, 9.0, cost=60, club=1)]
        candidates = [_p(2, "NOISE", "MID", 4.1, 9.1, cost=60, club=2)]  # +0.1 horizon, below min_gain 1.5
        transfers, _, _, _ = solve_transfers(squad, candidates, free_transfers=1, bank=0,
                                             hit_threshold=5.0, min_gain=1.5)
        self.assertEqual(len(transfers), 0, "sub-min_gain move must be blocked (no transfer churn)")

    def test_joint_pair_can_use_funding_move(self):
        squad = [
            _p(1, "PREMIUM_DEF", "DEF", 4.0, 10.0, cost=80, club=1),
            _p(2, "WEAK_MID", "MID", 2.0, 5.0, cost=60, club=2),
        ]
        candidates = [
            _p(3, "VALUE_DEF", "DEF", 3.5, 8.0, cost=50, club=3),
            _p(4, "STAR_MID", "MID", 6.0, 14.0, cost=90, club=4),
        ]
        transfers, bank, ft_left, notes = solve_transfers(
            squad, candidates, free_transfers=2, bank=0, min_gain=1.5)
        self.assertEqual({t["element_in"] for t in transfers}, {3, 4})
        self.assertEqual(bank, 0)
        self.assertEqual(ft_left, 0)
        self.assertTrue(any("joint 2-move" in note for note in notes))

    def test_two_paid_transfers_are_blocked_by_default(self):
        squad = [
            _p(1, "BAD_DEF", "DEF", 1.0, 1.0, cost=50, club=1),
            _p(2, "BAD_MID", "MID", 1.0, 1.0, cost=50, club=2),
        ]
        candidates = [
            _p(3, "STAR_DEF", "DEF", 8.0, 12.0, cost=50, club=3),
            _p(4, "STAR_MID", "MID", 8.0, 12.0, cost=50, club=4),
        ]
        transfers, _, _, _ = solve_transfers(
            squad, candidates, free_transfers=0, bank=0, hit_threshold=5.0)
        self.assertEqual(len(transfers), 1)

    def test_five_banked_free_transfers_remain_available(self):
        squad = [_p(i, f"OLD{i}", "MID", 2.0, 5.0, cost=60, club=i)
                 for i in range(1, 6)]
        candidates = [_p(100 + i, f"NEW{i}", "MID", 6.0, 12.0, cost=50, club=20 + i)
                      for i in range(1, 6)]
        transfers, _, ft_left, _ = solve_transfers(
            squad, candidates, free_transfers=5, bank=0)
        self.assertEqual(len(transfers), 5)
        self.assertEqual(ft_left, 0)

    def test_zero_paid_cap_reports_excluded_hits(self):
        squad = [
            _p(1, "BAD_DEF", "DEF", 1.0, 1.0, cost=50, club=1),
            _p(2, "BAD_MID", "MID", 1.0, 1.0, cost=50, club=2),
        ]
        candidates = [
            _p(3, "STAR_DEF", "DEF", 8.0, 12.0, cost=50, club=3),
            _p(4, "STAR_MID", "MID", 8.0, 12.0, cost=50, club=4),
        ]
        transfers, _, _, notes = solve_transfers(
            squad, candidates, free_transfers=1, bank=0,
            hit_threshold=5.0, max_paid_transfers=0)
        self.assertEqual(len(transfers), 1)
        self.assertTrue(any("cap 0 reached" in note for note in notes))


if __name__ == "__main__":
    unittest.main(verbosity=2)
