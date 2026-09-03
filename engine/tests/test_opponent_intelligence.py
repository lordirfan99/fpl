"""Adaptive multi-league intelligence and prize strategy tests."""

import datetime as dt
import os
import sys
import tempfile
import unittest
from unittest import mock


BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "model"))
sys.path.insert(0, os.path.join(BASE, "jobs"))
sys.path.insert(0, os.path.join(BASE, "execution"))

import opponent_intelligence as oi
import prize_strategy as ps
import league_signals as ls


def locked_picks(captain=1):
    return {
        "entry_history": {"event": 5},
        "picks": [
            {
                "element": i,
                "position": i,
                "multiplier": 2 if i == captain else (0 if i > 11 else 1),
                "is_captain": i == captain,
                "is_vice_captain": i == 2,
            }
            for i in range(1, 16)
        ],
    }


class TestCohort(unittest.TestCase):
    def test_deduplicates_cross_league_entry(self):
        rows = [
            {"entry": 10, "league_id": 1, "rank": 1, "total": 100},
            {"entry": 10, "league_id": 2, "rank": 2, "total": 100},
            {"entry": 11, "league_id": 1, "rank": 2, "total": 99},
            {"entry": 99, "league_id": 1, "rank": 3, "total": 98},
        ]
        priors = {
            10: {"entry": 10, "historical_score": 90, "tier": "S", "seasons": 8},
            11: {"entry": 11, "historical_score": 70, "tier": "A", "seasons": 5},
        }
        cohort = oi.select_deep_cohort(rows, priors, 99, max_size=10, top_per_league=2)
        self.assertEqual([c["entry"] for c in cohort].count(10), 1)
        shared = next(c for c in cohort if c["entry"] == 10)
        self.assertEqual(shared["leagues"], [1, 2])

    def test_cohort_respects_hard_cap(self):
        rows = [{"entry": i, "league_id": 1, "rank": i, "total": 200 - i} for i in range(1, 30)]
        cohort = oi.select_deep_cohort(rows, {}, 99, max_size=7, top_per_league=20)
        self.assertEqual(len(cohort), 7)


class TestLockedPicks(unittest.TestCase):
    def test_valid_locked_picks(self):
        self.assertTrue(oi.validate_locked_picks(locked_picks(), 5))

    def test_rejects_duplicate_or_incomplete_picks(self):
        payload = locked_picks()
        payload["picks"][1]["element"] = 1
        self.assertFalse(oi.validate_locked_picks(payload, 5))
        self.assertFalse(oi.validate_locked_picks({"picks": payload["picks"][:3]}, 5))

    def test_weighted_exposure(self):
        cohort = [
            {"entry": 10, "historical_score": 100},
            {"entry": 11, "historical_score": 0},
        ]
        exposure = oi.exposure_from_picks(cohort, {10: locked_picks(1), 11: locked_picks(2)})
        self.assertGreater(exposure["1"]["captain_share"], exposure["2"]["captain_share"])
        self.assertGreater(exposure["1"]["effective_ownership"], 100)

    def test_current_season_elite_template_ignores_preseason_prior(self):
        # Two managers: entry 10 sits TOP of the table this season but has a
        # weak preseason score; entry 11 is bottom but preseason-elite.
        cohort = [
            {"entry": 10, "historical_score": 5},
            {"entry": 11, "historical_score": 99},
        ]
        picks = {
            10: {"picks": [{"element": i, "is_captain": i == 1} for i in range(1, 16)]},
            11: {"picks": [{"element": i, "is_captain": i == 90} for i in range(80, 95)]},
        }
        standings = [
            {"entry": 10, "league_id": 1, "rank": 1, "total": 180},
            {"entry": 11, "league_id": 1, "rank": 40, "total": 90},
        ]
        tmpl = oi.elite_template_current_season(
            cohort, picks, standings, top_fraction=0.5, min_managers=1,
            ownership_floor=100.0)
        ids = {p["element"] for p in tmpl["players"]}
        self.assertEqual(tmpl["manager_count"], 1)          # top 50% of 2 -> 1
        self.assertIn(1, ids)                               # entry 10's squad
        self.assertNotIn(80, ids)                           # entry 11 excluded
        self.assertEqual(tmpl["source"], "current_season")

    def test_current_season_elite_template_ownership_floor(self):
        cohort = [{"entry": e, "historical_score": 50} for e in (1, 2, 3, 4)]
        # element 5 in every squad; element 7 in only one
        picks = {
            e: {"picks": [{"element": 5, "is_captain": False}]
                + ([{"element": 7, "is_captain": False}] if e == 1 else [])}
            for e in (1, 2, 3, 4)
        }
        standings = [{"entry": e, "league_id": 1, "rank": e, "total": 200 - e} for e in (1, 2, 3, 4)]
        tmpl = oi.elite_template_current_season(
            cohort, picks, standings, top_fraction=1.0, min_managers=1, ownership_floor=75.0)
        ids = {p["element"] for p in tmpl["players"]}
        self.assertIn(5, ids)
        self.assertNotIn(7, ids)

    def test_explicit_anonymous_client_does_not_load_cached_session(self):
        from fpl_client import FPLClient
        with mock.patch("fpl_client.load_session", return_value={"access_token": "stale"}):
            client = FPLClient(session_data={})
        self.assertFalse(client.authenticated)
        self.assertNotIn("Authorization", client.s.headers)


class TestAdaptiveCaptain(unittest.TestCase):
    def plan(self):
        starters = [
            {"id": 1, "name": "Model Best", "xpts": 8.0},
            {"id": 2, "name": "Template", "xpts": 7.6},
            {"id": 3, "name": "Differential", "xpts": 7.1},
        ]
        return {"gw": 30, "target_starters": starters, "captain": starters[0], "vice": starters[1]}

    def state(self, mode):
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        return {
            "as_of": now,
            "event": 30,
            "mode": {"mode": mode, "reason": "test"},
            "player_exposure": {
                "1": {"captain_share": 10, "effective_ownership": 70},
                "2": {"captain_share": 70, "effective_ownership": 150},
                "3": {"captain_share": 1, "effective_ownership": 5},
            },
        }

    def test_neutral_never_changes_captain(self):
        result = oi.refine_plan_captain(self.plan(), self.state("Neutral"))
        self.assertEqual(result["captain"]["id"], 1)
        self.assertFalse(result["league_intelligence"]["applied"])

    def test_protect_matches_template_inside_half_point(self):
        result = oi.refine_plan_captain(self.plan(), self.state("Protect"))
        self.assertEqual(result["captain"]["id"], 2)
        self.assertLessEqual(result["league_intelligence"]["xpts_cost"], 0.5)

    def test_chase_uses_low_ownership_inside_one_point(self):
        result = oi.refine_plan_captain(self.plan(), self.state("Chase"))
        self.assertEqual(result["captain"]["id"], 3)
        self.assertLessEqual(result["league_intelligence"]["xpts_cost"], 1.0)

    def test_stale_or_wrong_event_fails_soft(self):
        state = self.state("Protect")
        state["event"] = 10
        result = oi.refine_plan_captain(self.plan(), state)
        self.assertEqual(result["captain"]["id"], 1)


class TestPrizeStrategy(unittest.TestCase):
    def test_prize_boundary_gap_and_current_band(self):
        config = {
            "league_id": 1,
            "priority": 1,
            "overall": [
                {"rank_from": 1, "rank_to": 1, "prize": "RM100", "cash_rm": 100},
                {"rank_from": 2, "rank_to": 5, "prize": "RM50", "cash_rm": 50},
            ],
        }
        rows = [
            {"entry": 10, "league_id": 1, "rank": 1, "total": 100},
            {"entry": 99, "league_id": 1, "rank": 3, "total": 90},
            {"entry": 11, "league_id": 1, "rank": 6, "total": 85},
        ]
        status = ps.calculate_prize_status(rows, 99, config, completed_gws=30)
        self.assertEqual(status["current_prize"]["prize"], "RM50")
        self.assertEqual(status["next_target"]["rank_to"], 1)
        self.assertEqual(status["gap_to_next_target"], 11)
        self.assertEqual(status["drop_buffer"], 5)

    def test_early_season_always_neutral(self):
        status = {"priority": 1, "rank": 100, "gap_to_next_target": 50, "current_prize": None}
        self.assertEqual(ps.prize_mode([status], 3)["mode"], "Neutral")

    def test_late_large_gap_chase(self):
        status = {"priority": 1, "rank": 100, "gap_to_next_target": 20, "current_prize": None}
        self.assertEqual(ps.prize_mode([status], 30)["mode"], "Chase")


class TestPagination(unittest.TestCase):
    def test_fetches_past_ten_pages(self):
        import league_intelligence as job

        class Client:
            def get_json(self, path):
                page = int(path.split("page_standings=")[1].split("&")[0])
                return {
                    "league": {"name": "Large"},
                    "standings": {
                        "has_next": page < 12,
                        "results": [{"entry": page, "entry_name": str(page), "rank": page, "last_rank": page, "total": 100}],
                    },
                    "new_entries": {"has_next": False, "results": []},
                }

        result = job.fetch_league(Client(), 1, max_pages=20)
        self.assertTrue(result["complete"])
        self.assertEqual(result["pages"], 12)
        self.assertEqual(len(result["members"]), 12)

    def test_registry_accepts_late_entries_then_freezes_after_deadline(self):
        import league_intelligence as job

        deadline = dt.datetime(2026, 8, 21, 17, 30, tzinfo=dt.timezone.utc)
        league = {"league_id": 1, "league_name": "Prize", "members": [{"entry": 1}, {"entry": 2}]}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(job, "REGISTRY_DIR", tmp):
            _, pre = job.apply_deadline_registry([league], 1, deadline - dt.timedelta(minutes=1), deadline)
            self.assertEqual(pre["status"], "provisional")

            _, final = job.apply_deadline_registry([league], 1, deadline + dt.timedelta(minutes=5), deadline)
            self.assertEqual(final["status"], "final")

            changed = dict(league)
            changed["members"] = league["members"] + [{"entry": 3}]
            filtered, still_final = job.apply_deadline_registry([changed], 1, deadline + dt.timedelta(minutes=15), deadline)
            self.assertEqual([row["entry"] for row in filtered[0]["members"]], [1, 2])
            self.assertEqual(still_final["membership_hash"], final["membership_hash"])


class TestLeagueSignals(unittest.TestCase):
    def test_transfer_consensus_is_sharpness_weighted(self):
        cohort = [{"entry": 1, "historical_score": 100}, {"entry": 2, "historical_score": 0}]
        transfers = {
            1: [{"event": 4, "element_in": 10, "element_out": 20}],
            2: [{"event": 4, "element_in": 11, "element_out": 21}],
        }
        result = ls.transfer_consensus(cohort, transfers, 4)
        self.assertEqual(result[0]["element"], 10)
        self.assertGreater(result[0]["weighted_in_pct"], result[1]["weighted_in_pct"])

    def test_manager_activity_tracks_chips_hits_and_early_moves(self):
        deadline = dt.datetime(2026, 8, 21, 17, 30, tzinfo=dt.timezone.utc)
        history = {
            "current": [{"event": 1, "transfers_cost": 8, "value": 1012, "bank": 5}],
            "chips": [{"name": "bboost", "event": 1}],
        }
        transfers = [{"event": 1, "time": "2026-08-19T10:00:00Z", "element_in": 1, "element_out": 2}]
        result = ls.manager_activity(history, transfers, {1: deadline})
        self.assertEqual(result["early_transfer_count"], 1)
        self.assertEqual(result["hits_paid"], 8)
        self.assertIn("bboost", result["chips_used"])

    def test_api_set_piece_and_market_signals(self):
        elements = [{
            "id": 1, "web_name": "A", "team": 2, "now_cost": 75,
            "penalties_order": 1, "direct_freekicks_order": None,
            "corners_and_indirect_freekicks_order": 2,
            "price_change_hourly_rate": 3.5, "price_change_projections": "+0.1",
            "transfers_in_event": 100, "transfers_out_event": 10, "status": "a",
        }]
        self.assertEqual(ls.set_piece_signals(elements)[0]["roles"]["penalties"], 1)
        self.assertEqual(ls.market_signals(elements)[0]["net_transfers_event"], 90)

    def test_live_swing_uses_pick_multipliers(self):
        ours = locked_picks(1)
        rival = locked_picks(2)
        live = {"elements": [{"id": i, "stats": {"total_points": 10 if i == 1 else 1}} for i in range(1, 16)]}
        result = ls.cohort_live_swing(ours, {5: rival}, live)
        self.assertEqual(result["our_live_points"], 30)
        self.assertEqual(result["rivals"][0]["live_points"], 21)

    def test_monthly_ledger_aggregates_events(self):
        ledger = {"2026-08": {"1": {"1": {"99": 60, "2": 70}, "2": {"99": 65, "2": 50}}}}
        result = ls.monthly_totals([], ledger, "2026-08", 1, 99, {"prize": "RM100"})
        self.assertEqual(result["rank"], 1)
        self.assertEqual(result["points"], 125)

    def test_prize_simulation_is_deterministic_and_bounded(self):
        rows = [{"entry": i, "rank": i, "total": 101 - i} for i in range(1, 51)]
        bands = [{"rank_from": 1, "rank_to": 10, "prize": "Top 10"}]
        one = ls.simulate_prize_probabilities(rows, 10, bands, 5, simulations=200, seed=7)
        two = ls.simulate_prize_probabilities(rows, 10, bands, 5, simulations=200, seed=7)
        self.assertEqual(one, two)
        self.assertGreaterEqual(one["p_top_10"], 0)
        self.assertLessEqual(one["p_top_10"], 100)


if __name__ == "__main__":
    unittest.main()
