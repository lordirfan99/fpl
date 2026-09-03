import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "model"))
sys.path.insert(0, os.path.join(ROOT, "jobs"))
sys.path.insert(0, os.path.join(ROOT, "bot"))

from feature_store_v42 import (event_rows, history_by_player, load_event_history,
                               player_rates, team_rotation_rate, team_strengths,
                               write_event_rows)
from minutes_v42 import forecast_minutes_v42
from v42_projection import _logistic, project_player_v42
from evaluate_v42_candidate import evaluate
from templates import plan_card


def player(**overrides):
    value = {"id": 10, "web_name": "Player", "element_type": 3, "team": 1,
             "status": "a", "chance_of_playing_next_round": None,
             "news": "", "ep_next": "4.0"}
    value.update(overrides)
    return value


class RateShrinkageTests(unittest.TestCase):
    PRIOR = {"goals_scored": 0.30}

    def _rate(self, gws, minutes_each):
        rows = [{"minutes": minutes_each, "goals_scored": 1.0} for _ in range(gws)]
        return player_rates(rows, self.PRIOR)

    def test_effective_prior_decays_with_gameweeks(self):
        self.assertEqual(self._rate(2, 90)["effective_prior_minutes"], 780.0)   # 900 - 60*2
        self.assertEqual(self._rate(10, 90)["effective_prior_minutes"], 300.0)
        self.assertEqual(self._rate(20, 90)["effective_prior_minutes"], 180.0)  # floor

    def test_current_season_signal_wins_faster_late_season(self):
        early = self._rate(2, 90)["goals_scored"]     # heavy shrink toward 0.30
        late = self._rate(12, 90)["goals_scored"]     # sample should dominate
        self.assertLess(early, late)
        self.assertGreater(late, 0.7)                  # observed ~1.0/90 comes through


class DefensiveContributionTests(unittest.TestCase):
    def test_logistic_is_half_at_threshold_and_monotone(self):
        self.assertAlmostEqual(_logistic(10.0, 10.0, 0.35), 0.5, places=6)
        self.assertLess(_logistic(6.0, 10.0, 0.35), 0.5)
        self.assertGreater(_logistic(14.0, 10.0, 0.35), 0.5)
        self.assertLess(_logistic(10.0, 10.0, 0.35), 1.0)   # never a certainty


class MinutesTests(unittest.TestCase):
    def test_probabilities_are_bounded_and_sum_to_one(self):
        history = [{"minutes": m} for m in (90, 82, 0, 35, 90)]
        result = forecast_minutes_v42(player(), history)
        self.assertAlmostEqual(result.p_dnp + result.p_1_59 + result.p_60_plus, 1.0, places=4)
        self.assertTrue(all(0 <= value <= 1 for value in
                            (result.p_dnp, result.p_1_59, result.p_60_plus)))
        self.assertGreater(result.expected_minutes, 0)

    def test_unavailable_player_is_certain_dnp(self):
        result = forecast_minutes_v42(player(status="i"), [{"minutes": 90}])
        self.assertEqual(result.p_dnp, 1.0)
        self.assertEqual(result.expected_minutes, 0.0)

    def test_congestion_reduces_sixty_plus_probability(self):
        history = [{"minutes": 90}] * 6
        normal = forecast_minutes_v42(player(), history)
        congested = forecast_minutes_v42(player(), history, congestion=True)
        self.assertLess(congested.p_60_plus, normal.p_60_plus)


class FeatureStoreTests(unittest.TestCase):
    def test_history_is_idempotent_and_strictly_lagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "history.jsonl")
            rows = [{"gw": 1, "element": 10, "minutes": 90},
                    {"gw": 2, "element": 10, "minutes": 30}]
            write_event_rows(path, rows)
            write_event_rows(path, rows)
            loaded = load_event_history(path)
            self.assertEqual(len(loaded), 2)
            self.assertEqual(len(history_by_player(loaded, 2)[10]), 1)

    def test_event_normalization_uses_official_live_stats(self):
        live = {"elements": [{"id": 10, "stats": {"minutes": 90,
                                                     "expected_goals": "0.4",
                                                     "total_points": 7}}]}
        rows = event_rows(1, live, [{"id": 10, "team": 1, "element_type": 3}], "now")
        self.assertEqual(rows[0]["minutes"], 90)
        self.assertEqual(rows[0]["expected_goals"], "0.4")

    def test_team_strength_excludes_target_gameweek(self):
        fixtures = [
            {"event": 1, "finished": True, "team_h": 1, "team_a": 2,
             "team_h_score": 3, "team_a_score": 0},
            {"event": 2, "finished": True, "team_h": 1, "team_a": 2,
             "team_h_score": 0, "team_a_score": 8},
        ]
        before = team_strengths(fixtures, 2)
        self.assertGreater(before[1]["attack"], before[2]["attack"])

    def test_team_rotation_uses_lagged_start_turnover(self):
        history = {
            1: [{"gw": 1, "element": 1, "team": 1, "starts": 1, "minutes": 90}],
            2: [{"gw": 2, "element": 2, "team": 1, "starts": 1, "minutes": 90}],
        }
        self.assertGreater(team_rotation_rate(history, 1), 0.0)


class ProjectionTests(unittest.TestCase):
    def test_blank_gameweek_is_zero(self):
        result = project_player_v42(player(), {}, [2], {10: [{"gw": 1, "minutes": 90}]}, {}, {})
        self.assertEqual(result["mean"], 0.0)

    def test_double_gameweek_aggregates_fixtures(self):
        fixtures = {(2, 1): [
            {"opponent": 2, "home": True, "fdr": 2},
            {"opponent": 3, "home": False, "fdr": 3},
        ]}
        history = {10: [{"gw": 1, "minutes": 90, "expected_goals": 0.3,
                         "expected_assists": 0.2}]}
        double = project_player_v42(player(), fixtures, [2], history, {}, {})
        single = project_player_v42(player(), {(2, 1): fixtures[(2, 1)][:1]},
                                      [2], history, {}, {})
        self.assertGreater(double["mean"], single["mean"])
        self.assertEqual(double["model_version"], "competitive-v4.2-shadow")

    def test_fixture_congestion_reduces_minutes(self):
        fixtures = {
            (2, 1): [{"opponent": 2, "home": True, "fdr": 2,
                      "kickoff_time": "2026-08-28T17:30:00Z"}],
            (3, 1): [{"opponent": 3, "home": False, "fdr": 3,
                      "kickoff_time": "2026-08-31T17:30:00Z"}],
        }
        history = {10: [{"gw": n, "minutes": 90} for n in range(1, 7)]}
        congested = project_player_v42(player(), fixtures, [2], history, {}, {})
        normal = project_player_v42(player(), {(2, 1): fixtures[(2, 1)]},
                                      [2], history, {}, {})
        self.assertLess(congested["p_60_plus"], normal["p_60_plus"])

    def test_official_set_piece_role_is_a_bounded_candidate_component(self):
        fixtures = {(2, 1): [{"opponent": 2, "home": True, "fdr": 2}]}
        base = project_player_v42(player(), fixtures, [2], {}, {}, {})
        taker = project_player_v42(player(penalties_order=1,
                                           direct_freekicks_order=1,
                                           corners_and_indirect_freekicks_order=1),
                                     fixtures, [2], {}, {}, {})
        self.assertGreater(taker["mean"], base["mean"])
        self.assertIn("set_piece_role", taker["components"])


class PromotionTests(unittest.TestCase):
    def _row(self, gw, element, champion, candidate, actual, pos="MID"):
        covered = element % 5 != 0
        return ({"gw": gw, "element": element, "predicted": champion,
                 "actual": actual, "minutes": 90, "p_start": 0.8,
                 "expected_minutes": 72, "pos": pos},
                {"gw": gw, "element": element, "predicted": candidate,
                 "actual": actual, "minutes": 90, "p_dnp": 0.02,
                 "p_1_59": 0.03, "p_60_plus": 0.95, "pos": pos,
                 "floor": actual - 2 if covered else actual + 1,
                 "upside": actual + 2})

    def test_candidate_cannot_qualify_before_six_gameweeks(self):
        pairs = [self._row(gw, i, (i % 6) + 4, (i % 6) + 3, (i % 6) + 3)
                 for gw in range(1, 6) for i in range(120)]
        report = evaluate([a for a, _ in pairs], [b for _, b in pairs],
                          {"min_live_gws": 6, "min_rows": 500},
                          {"champion_total": 100, "candidate_total": 101,
                           "evaluated_gws": list(range(1, 6))})
        self.assertFalse(report["eligible_for_owner_approval"])
        self.assertFalse(report["checks"]["live_gws"])

    def test_candidate_still_only_becomes_eligible(self):
        pairs = [self._row(gw, i, (i % 6) + 4, (i % 6) + 3, (i % 6) + 3)
                 for gw in range(1, 7) for i in range(100)]
        report = evaluate([a for a, _ in pairs], [b for _, b in pairs],
                          {"min_live_gws": 6, "min_rows": 500},
                          {"champion_total": 100, "candidate_total": 101,
                           "evaluated_gws": list(range(1, 7))})
        self.assertTrue(report["eligible_for_owner_approval"])
        self.assertNotIn("active_projection", report)

    def test_telegram_card_labels_candidate_non_authoritative(self):
        card = plan_card({
            "gw": 2, "model_version": "competitive-v4.0",
            "engine_note": "competitive-v4.0 projection + horizon MILP v4.1",
            "model_candidate": {"version": "competitive-v4.2-shadow",
                                "status": "awaiting_eligibility",
                                "evaluated_gws": [1], "rows": 500,
                                "eligible_for_owner_approval": False},
            "target_starters": [], "bench": [], "transfers": [],
            "captain": {}, "vice": {}, "deadline": "2026-08-28T17:30:00Z",
        })
        # Not eligible -> the shadow model is NOT shown on the lean card (dashboard only).
        self.assertNotIn("V4.2 candidate", card)

        ready = plan_card({
            "gw": 2, "model_version": "competitive-v4.0",
            "model_candidate": {"version": "competitive-v4.2-shadow",
                                "evaluated_gws": [1, 2, 3, 4, 5, 6], "rows": 600,
                                "eligible_for_owner_approval": True},
            "target_starters": [], "bench": [], "transfers": [],
            "captain": {}, "vice": {}, "deadline": "2026-08-28T17:30:00Z",
        })
        self.assertIn("V4.2 candidate ready for approval", ready)


if __name__ == "__main__":
    unittest.main()
