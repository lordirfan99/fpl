import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "model"))
sys.path.insert(0, os.path.join(BASE, "optimizer"))

from fixture_engine import fixtures_by_team_gw, fixture_count, is_bgw, is_dgw
from minutes_model import forecast_minutes
from component_xpts import gameweek_xpts
from multigw_planner import plan_sequences
from chip_strategy import opportunity_cost_decision
from calibration import mae, bias, uncertainty_scale


class FixtureEngineTests(unittest.TestCase):
    def test_dgw_preserves_both_fixtures(self):
        fx = [
            {"id": 1, "event": 5, "team_h": 1, "team_a": 2, "team_h_difficulty": 2, "team_a_difficulty": 4},
            {"id": 2, "event": 5, "team_h": 3, "team_a": 1, "team_h_difficulty": 3, "team_a_difficulty": 3},
        ]
        m = fixtures_by_team_gw(fx, [5])
        self.assertEqual(fixture_count(m, 5, 1), 2)
        self.assertTrue(is_dgw(m, 5, 1))
        self.assertTrue(is_bgw(m, 5, 4))


class MinutesTests(unittest.TestCase):
    def test_injured_is_zero(self):
        f = forecast_minutes({"status": "i", "minutes": 900, "starts": 10}, 10)
        self.assertEqual(f.expected_minutes, 0)
        self.assertEqual(f.p_start, 0)

    def test_regular_starter_has_high_probability(self):
        f = forecast_minutes({"status": "a", "minutes": 810, "starts": 9}, 10)
        self.assertGreater(f.p_start, 0.7)
        self.assertGreater(f.expected_minutes, 55)


class ComponentTests(unittest.TestCase):
    def player(self):
        return {"element_type": 3, "status": "a", "minutes": 900, "starts": 10,
                "goals_scored": 5, "assists": 4, "bonus": 10,
                "expected_goals": "4.5", "expected_assists": "3.5",
                "yellow_cards": 1, "red_cards": 0, "clean_sheets": 3}

    def test_dgw_xpts_exceeds_single_fixture(self):
        fx = [{"fdr": 3}, {"fdr": 3}]
        single = gameweek_xpts(self.player(), fx[:1], 10)
        double = gameweek_xpts(self.player(), fx, 10)
        self.assertGreater(double.mean, single.mean * 1.8)
        self.assertGreater(double.upside, double.mean)

    def test_bgw_zero(self):
        self.assertEqual(gameweek_xpts(self.player(), [], 10).mean, 0)


class PlannerTests(unittest.TestCase):
    def test_planner_can_prefer_transfer(self):
        out = {"id": 1, "name": "A", "position": "MID", "club": 1, "cost": 50,
               "selling_price": 50, "xpts_by_gw": [2, 2], "variance_by_gw": [1, 1]}
        inc = {"id": 2, "name": "B", "position": "MID", "club": 2, "cost": 50,
               "xpts_by_gw": [7, 7], "variance_by_gw": [1, 1]}
        squad = [out]
        plan = plan_sequences(squad, [inc], 0, 1, horizon=2, beam_width=5)
        self.assertEqual(plan["first_action"]["action"], "transfer")


class ChipAndCalibrationTests(unittest.TestCase):
    def test_tc_holds_when_future_benchmark_better(self):
        plan = {"captain": {"xpts": 7.2}, "target_starters": [], "bench": []}
        d = opportunity_cost_decision("3xc", plan, {}, 5, [12.0])
        self.assertFalse(d["play"])

    def test_metrics(self):
        rows = [{"predicted": 4, "actual": 2}, {"predicted": 3, "actual": 4}]
        self.assertAlmostEqual(mae(rows), 1.5)
        self.assertAlmostEqual(bias(rows), 0.5)

    def test_uncertainty_scale_waits_for_enough_real_rows(self):
        self.assertEqual(uncertainty_scale({"n": 99, "rmse": 4.5}), 1.0)
        self.assertEqual(uncertainty_scale({"n": 100, "rmse": 4.5}), 1.5)


if __name__ == "__main__":
    unittest.main()
