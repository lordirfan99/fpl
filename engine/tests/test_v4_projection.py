import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "model"))

from v4_projection import captain_score, project_player


def player(**overrides):
    row = {
        "id": 1, "team": 1, "element_type": 3, "minutes": 900,
        "starts": 10, "status": "a", "chance_of_playing_next_round": 100,
        "goals_scored": 3, "assists": 4, "expected_goals": "3.2",
        "expected_assists": "3.8", "clean_sheets": 3, "bonus": 8,
        "yellow_cards": 1, "red_cards": 0, "saves": 0,
    }
    row.update(overrides)
    return row


class V4ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.fixtures = {
            (2, 1): [{"fdr": 2}], (3, 1): [{"fdr": 3}],
            (4, 1): [{"fdr": 4}],
        }

    def test_returns_three_week_uncertainty_and_components(self):
        out = project_player(player(), self.fixtures, 1, [2, 3, 4])
        self.assertEqual(len(out["xpts_by_gw"]), 3)
        self.assertEqual(len(out["variance_by_gw"]), 3)
        self.assertIn("goals", out["components"])
        self.assertGreater(out["expected_horizon"], out["xpts_horizon"])

    def test_availability_reduces_confidence_and_projection(self):
        fit = project_player(player(), self.fixtures, 1, [2, 3, 4])
        doubt = project_player(
            player(status="d", chance_of_playing_next_round=25, news="Knock"),
            self.fixtures, 1, [2, 3, 4])
        self.assertLess(doubt["confidence"], fit["confidence"])
        self.assertLess(doubt["xpts"], fit["xpts"])

    def test_blank_gameweek_is_zero(self):
        out = project_player(player(), {}, 1, [2])
        self.assertEqual(out["xpts"], 0)
        self.assertEqual(out["xpts_horizon"], 0)

    def test_official_next_gw_prior_stabilizes_early_projection(self):
        without_prior = project_player(player(ep_next=None), self.fixtures, 1, [2, 3, 4])
        with_prior = project_player(player(ep_next="6.0"), self.fixtures, 1, [2, 3, 4])
        expected = 0.65 * 6.0 + 0.35 * without_prior["xpts"]
        self.assertAlmostEqual(with_prior["xpts"], expected, places=3)

    def test_captain_tiebreak_prefers_attacking_ceiling(self):
        keeper = {"position": "GKP", "xpts": 4.0, "xpts_upside": 6.0}
        midfielder = {"position": "MID", "xpts": 3.9, "xpts_upside": 7.0}
        self.assertGreater(captain_score(midfielder), captain_score(keeper))

    def test_captain_tiebreak_does_not_override_large_mean_gap(self):
        defender = {"position": "DEF", "xpts": 6.0, "xpts_upside": 8.0}
        midfielder = {"position": "MID", "xpts": 4.0, "xpts_upside": 8.0}
        self.assertGreater(captain_score(defender), captain_score(midfielder))

    def test_empirical_uncertainty_scales_variance_and_risk_horizon(self):
        base = project_player(player(), self.fixtures, 1, [2, 3, 4])
        wider = project_player(
            player(), self.fixtures, 1, [2, 3, 4], uncertainty_multiplier=1.5)
        self.assertAlmostEqual(wider["xpts_variance"], base["xpts_variance"] * 2.25)
        self.assertLess(wider["xpts_horizon"], base["xpts_horizon"])
        self.assertEqual(wider["uncertainty_multiplier"], 1.5)


if __name__ == "__main__":
    unittest.main()
