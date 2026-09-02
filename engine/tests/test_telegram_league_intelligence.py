"""Telegram rendering checks for the adaptive league interface."""

import os
import sys
import unittest
from unittest.mock import patch


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "bot"))

import telegram_bot
sys.path.insert(0, os.path.join(BASE, "model"))
from league_alerts import alert_signature, meaningful_league_alerts


class TestLeagueTelegramCard(unittest.TestCase):
    def sample_state(self):
        return {
            "as_of": "2027-04-01T12:00:00+00:00", "event": 30, "exposure_event": 29,
            "mode": {"mode": "Chase", "reason": "Five points to the next band."},
            "cohort_count": 40, "trusted_pick_count": 39,
            "registry": {"status": "final", "finalized_at": "2027-04-01T12:00:00+00:00", "membership_hash": "abc123def456789"},
            "leagues": [{"league_id": 131997, "member_count": 1800, "pages": 36, "complete": True}],
            "prize_status": [{
                "league_id": 131997, "rank": 12,
                "current_prize": {"prize": "RM150"},
                "next_target": {"prize": "RM200"}, "gap_to_next_target": 5,
                "probability": {"available": True, "expected_rank": 8, "p_top_10": 64.2, "p_top_40": 95.0},
                "active_special": [],
            }],
            "monthly_status": [{"league_id": 131997, "rank": 2, "gap_to_first": 4, "prize": {"prize": "RM100 monthly"}}],
            "cohort": [{"team_name": "A < B & C", "live_sharpness": 88, "tier": "S", "activity": {"archetype_live": "Patient Optimizer"}}],
            "transfer_consensus": [{"name": "X & Y", "weighted_in_pct": 60, "weighted_out_pct": 2}],
            "player_exposure": {"1": {"element": 1, "name": "Cap < One", "captain_share": 70, "effective_ownership": 140, "ownership": 90}},
            "live_swing": {"our_live_points": 45, "rivals": [{"live_points": 50, "swing_vs_us": 5}]},
            "market_signals": [{"name": "P < Q", "now_cost": 7.5, "projection": [{"projected_percent": "80", "likelihood": 70}], "net_transfers_event": 1000, "chance_next": 100, "status": "a"}],
        }

    def test_overview_card_is_lean_html_safe_and_bounded(self):
        state = self.sample_state()
        card = telegram_bot.league_text(state)
        self.assertIn("A &lt; B &amp; C", card)          # cohort name HTML-escaped
        self.assertIn("X &amp; Y", card)                 # sharp-money name HTML-escaped
        self.assertIn("P(top10) 64.2%", card)
        self.assertIn("registry FINAL", card)            # only when status == final
        self.assertNotIn("Deep cohort", card)            # internal telemetry removed
        self.assertNotIn("open Market Watch", card)      # teaser removed
        self.assertLess(len(card), 900)                  # genuinely lean now
        self.assertLessEqual(len(card), 4096)

    def test_monthly_placeholder_is_suppressed_until_it_has_data(self):
        state = self.sample_state()
        state["monthly_status"] = [{"league_id": 131997, "rank": None, "prize": {"prize": "RM100 monthly"}}]
        card = telegram_bot.league_text(state)
        self.assertNotIn("awaiting", card)
        self.assertNotIn("RM100 monthly", card)

    def test_remaining_war_room_sections_are_html_safe_and_bounded(self):
        state = self.sample_state()
        for section in ("rivals", "captain", "market"):
            with self.subTest(section=section):
                card = telegram_bot.war_room_text(section, state)
                self.assertLessEqual(len(card), 4096)
                self.assertNotIn("A < B & C", card)
        self.assertIn("A &lt; B &amp; C", telegram_bot.war_room_text("rivals", state))
        self.assertIn("Cap &lt; One", telegram_bot.war_room_text("captain", state))

    def test_retired_sections_fall_back_to_overview(self):
        state = self.sample_state()
        overview = telegram_bot.war_room_text("overview", state)
        for gone in ("prize", "transfers", "registry", "attack", "market_fall"):
            self.assertEqual(telegram_bot.war_room_text(gone, state), overview)

    def test_war_room_callback_contract_is_four_unique_actions(self):
        callbacks = [callback for _, callback in telegram_bot.WAR_ROOM_SECTIONS]
        self.assertEqual(len(callbacks), 4)
        self.assertEqual(len(callbacks), len(set(callbacks)))
        self.assertEqual(set(callbacks), {"war_rivals", "war_captain", "war_market", "war_refresh"})
        self.assertFalse(hasattr(telegram_bot, "MARKET_SECTIONS"))

    def test_market_card_is_bounded_deduplicated_and_squad_aware(self):
        state = self.sample_state()
        state["market_signals"] = [
            {"element": 1, "name": "Owned Faller", "now_cost": 6.5,
             "projection": {"direction": "fall"}, "chance_next": 100, "net_transfers_event": -5000},
            {"element": 2, "name": "Unowned Faller", "now_cost": 5.5,
             "projection": {"direction": "fall"}, "chance_next": 100, "net_transfers_event": -6000},
            {"element": 3, "name": "Doubt", "now_cost": 7.0,
             "projection": {"direction": "fall"}, "chance_next": 50, "net_transfers_event": -1000},
            {"element": 4, "name": "Riser", "now_cost": 8.0,
             "projection": {"direction": "rise"}, "net_transfers_event": 9000},
            {"element": 4, "name": "Riser", "now_cost": 8.0,
             "projection": {"direction": "rise"}, "net_transfers_event": 9000},
        ]
        with patch.object(telegram_bot, "load_pending", return_value={"pre_transfer_squad_ids": [1]}):
            card = telegram_bot.war_room_text("market", state)
        self.assertIn("🏠 owned", card)
        self.assertEqual(card.count("<b>Riser</b>"), 1)
        self.assertLessEqual(len(card), 4096)

    def test_safe_card_never_cuts_html_line(self):
        card = telegram_bot._safe_card(["<b>Header</b>"] + [f"<b>Player {i}</b>" for i in range(500)])
        self.assertLessEqual(len(card), 4096)
        self.assertEqual(card.count("<b>"), card.count("</b>"))

    def test_plan_buttons_fail_closed_without_complete_v4_context(self):
        base = {"status": "pending", "model_version": "competitive-v4.0",
                "plan_id": "abc", "input_fp": "def", "competitive": {}}
        self.assertTrue(telegram_bot._plan_is_executable(base))
        self.assertFalse(telegram_bot._plan_is_executable({**base, "status": "executed"}))
        self.assertFalse(telegram_bot._plan_is_executable({**base, "plan_id": None}))
        self.assertFalse(telegram_bot._plan_is_executable({**base, "competitive": {"fallback": True}}))
        self.assertFalse(telegram_bot._plan_is_executable({**base, "competitive": {"context_status": "pending"}}))

    def test_missing_snapshot_fails_soft(self):
        original = telegram_bot.LEAGUE_STATE_FILE
        telegram_bot.LEAGUE_STATE_FILE = os.path.join(BASE, "does-not-exist.json")
        try:
            self.assertIn("No intelligence snapshot", telegram_bot.war_room_text("rivals"))
        finally:
            telegram_bot.LEAGUE_STATE_FILE = original


class TestLeagueAlerts(unittest.TestCase):
    def test_only_meaningful_transitions_alert_and_signature_is_stable(self):
        previous = {
            "event": 1, "registry": {"status": "provisional"},
            "leagues": [{"member_count": 100}], "mode": {"mode": "Neutral"},
            "cohort_count": 40, "trusted_pick_count": 0, "player_exposure": {},
        }
        current = {
            "event": 1, "registry": {"status": "final"},
            "leagues": [{"member_count": 120}], "mode": {"mode": "Chase", "reason": "gap"},
            "cohort_count": 40, "trusted_pick_count": 35,
            "player_exposure": {"1": {"name": "Salah", "captain_share": 60}},
        }
        alerts = meaningful_league_alerts(previous, current)
        joined = "\n".join(alerts)
        self.assertIn("registry FINAL", joined)
        self.assertIn("Neutral → Chase", joined)
        self.assertIn("rival picks trusted", joined)
        self.assertIn("Salah 60.0%", joined)
        self.assertEqual(alert_signature(alerts), alert_signature(list(alerts)))

    def test_initial_or_unchanged_snapshot_is_silent(self):
        state = {"registry": {"status": "provisional"}, "mode": {"mode": "Neutral"}}
        self.assertEqual(meaningful_league_alerts({}, state), [])
        self.assertEqual(meaningful_league_alerts(state, state), [])


if __name__ == "__main__":
    unittest.main()
