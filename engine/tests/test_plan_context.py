"""Display-only plan context enrichments — must NEVER change xPts/ranking.

Covers: effective ownership, news age, set-piece context lines, and the
fail-soft contract (missing data -> None, cards render without context).
"""
import os
import sys
import unittest
from unittest.mock import patch

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "model"))
sys.path.insert(0, os.path.join(BASE, "bot"))

from plan_context import (
    effective_ownership,
    news_age_hours,
    set_piece_line,
    plan_context_lines,
    league_intelligence_lines,
    haaland_eo_line,
)


def _fake_bootstrap():
    """Return a bootstrap dict with a minimal elements list."""
    return {
        "elements": [
            {"id": 1, "web_name": "B.Fernandes", "team": 16,
             "selected_by_percent": "48.2", "news": "", "news_added": None},
            {"id": 2, "web_name": "Haaland", "team": 4,
             "selected_by_percent": "74.0", "news": "Knock", "news_added": "2026-08-13T08:00:00Z"},
            {"id": 3, "web_name": "Gabriel", "team": 1,
             "selected_by_percent": "25.0", "news": "", "news_added": None},
        ],
        "teams": [{"id": 16, "short_name": "MUN"}, {"id": 4, "short_name": "MCI"},
                  {"id": 1, "short_name": "ARS"}],
    }


class TestPlanContext(unittest.TestCase):
    def setUp(self):
        self.boot = _fake_bootstrap()
        self.patcher = patch("plan_context._load_bootstrap", return_value=(
            {e["id"]: e for e in self.boot["elements"]},
            {t["id"]: t["short_name"] for t in self.boot["teams"]},
        ))
        self.mock_load = self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_effective_ownership_returns_pair(self):
        raw, eff = effective_ownership(1)
        self.assertAlmostEqual(raw, 48.2, places=1)
        self.assertGreater(eff, raw)  # captain share boosts EO

    def test_effective_ownership_high_owner_has_big_eo(self):
        raw, eff = effective_ownership(2)
        self.assertAlmostEqual(raw, 74.0, places=1)
        self.assertGreater(eff, 100.0)  # ~118% with captain share

    def test_effective_ownership_missing_player_returns_none(self):
        self.assertIsNone(effective_ownership(9999))

    def test_news_age_hours_with_timestamp(self):
        age = news_age_hours(2)
        self.assertIsNotNone(age)
        self.assertGreaterEqual(age, 0)

    def test_news_age_hours_no_news_returns_none(self):
        self.assertIsNone(news_age_hours(1))

    def test_set_piece_line_matches(self):
        self.assertIsNotNone(set_piece_line("B.Fernandes"))
        self.assertIsNone(set_piece_line("Unknown Player"))

    def test_plan_context_lines_include_captain_eo(self):
        plan = {"captain": {"id": 1, "name": "B.Fernandes"},
                "target_starters": [{"id": 3, "name": "Gabriel", "xpts": 5.0}]}
        lines = plan_context_lines(plan)
        joined = "\n".join(lines)
        self.assertIn("Captain EO", joined)

    def test_plan_context_lines_empty_plan(self):
        self.assertEqual(plan_context_lines({}), [])

    def test_haaland_eo_line(self):
        line = haaland_eo_line()
        self.assertIsNotNone(line)
        self.assertIn("Haaland", line)

    def test_fail_soft_on_missing_bootstrap(self):
        with patch("plan_context._load_bootstrap", return_value=(None, None)):
            self.assertIsNone(effective_ownership(1))
            self.assertIsNone(news_age_hours(2))
            self.assertIsNone(haaland_eo_line())

    def test_league_prize_context(self):
        plan = {"league_intelligence": {"mode": "Chase", "applied": False, "reason": "Prize gap"}}
        state = {
            "mode": {
                "mode": "Chase",
                "target": {
                    "league_id": 131997,
                    "rank": 70,
                    "current_prize": None,
                    "next_target": {"prize": "Free slot next tour"},
                    "gap_to_next_target": 8,
                },
            },
            "cohort_count": 40,
            "trusted_pick_count": 38,
        }
        text = "\n".join(league_intelligence_lines(plan, state))
        self.assertIn("Chase", text)
        self.assertIn("131997", text)
        self.assertIn("40 monitored", text)


class TestCardIntegration(unittest.TestCase):
    """The cards must render WITH context but never crash when context absent."""

    def test_plan_card_renders_with_context(self):
        from templates import plan_card
        plan = {
            "gw": 1, "model_version": "v1", "target_xpts": 58.4,
            "current_xpts": 58.4, "horizon_gain": 0.0, "transfers": [],
            "target_starters": [{"id": 1, "name": "B.Fernandes", "position": "MID", "xpts": 5.56}],
            "bench": [], "captain": {"id": 1, "name": "B.Fernandes"},
            "vice": {"id": 3}, "chip_suggestion": None,
            "deadline": "2026-08-21T17:30:00Z",
        }
        card = plan_card(plan)
        self.assertIn("CONTEXT", card)
        self.assertIn("B.Fernandes", card)

    def test_plan_card_without_context_data_does_not_crash(self):
        with patch("plan_context._load_bootstrap", return_value=(None, None)):
            from templates import plan_card
            plan = {"gw": 1, "target_starters": [], "bench": [],
                    "captain": {"id": 1, "name": "X"}, "deadline": "2026-08-21T17:30:00Z"}
            card = plan_card(plan)
            self.assertIn("Decision required", card)

    def test_plan_card_explains_elite_template_gate(self):
        from templates import plan_card
        plan = {
            "gw": 2, "model_version": "competitive-v4.0", "target_xpts": 55,
            "current_xpts": 53, "horizon_gain": 2, "transfers": [],
            "target_starters": [{"id": 1, "name": "B.Fernandes", "position": "MID", "xpts": 5.5}],
            "bench": [], "captain": {"id": 1, "name": "B.Fernandes"},
            "vice": {"id": 3}, "deadline": "2026-08-28T17:30:00Z",
            "competitive": {
                "template_formation": "3-4-3",
                "template_gate": {"alignment": 70, "alignment_threshold": 82,
                                   "decision": "CONVERGE_TO_TEMPLATE", "differential_allowed": False},
                "elite_template": [{"element": 2, "name": "Haaland", "elite_percentage": 90}],
                "captain_consensus": [{"name": "Haaland", "percentage": 75}],
            },
        }
        card = plan_card(plan)
        self.assertIn("CONVERGE TO TEMPLATE", card)
        self.assertIn("Top template gaps", card)
        self.assertIn("Elite captain", card)
        self.assertIn("Official FPL + statistical V4", card)


if __name__ == "__main__":
    unittest.main()
