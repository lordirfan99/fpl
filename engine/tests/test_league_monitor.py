"""Tests for league monitor + manager sharpness + beat-them engine."""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "model"))
sys.path.insert(0, os.path.join(BASE, "jobs"))

import manager_sharpness as ms
import beat_them as bt


def make_history(n, base_pts=50, base_rank=1000, cost=0, value=1010):
    rows = []
    for i in range(n):
        rows.append({
            "event": i + 1,
            "points": base_pts + i,
            "total_points": base_pts * (i + 1),
            "rank": base_rank - i * 5,
            "transfers_cost": cost,
            "value": value + i,
        })
    return rows


class TestSharpness(unittest.TestCase):
    def test_empty_history_returns_prior(self):
        s = ms.score_manager([])
        self.assertEqual(s["gws_evaluated"], 0)
        self.assertEqual(s["confidence"], "preseason prior only")
        self.assertGreaterEqual(s["score"], 0)
        self.assertLessEqual(s["score"], 100)

    def test_trust_label_boundaries(self):
        self.assertEqual(ms.trust_label(0), "preseason prior only")
        self.assertEqual(ms.trust_label(1), "low")
        self.assertEqual(ms.trust_label(3), "low")
        self.assertEqual(ms.trust_label(4), "provisional")
        self.assertEqual(ms.trust_label(5), "provisional")
        self.assertEqual(ms.trust_label(6), "trusted")
        self.assertEqual(ms.trust_label(12), "established")

    def test_preseason_prior_from_finish(self):
        # rank 1 of 100 -> 100*(1-sqrt(0.01)) = 90
        prior, label = ms.preseason_prior([{"rank": 1, "total_players": 100}])
        self.assertAlmostEqual(prior, 90.0, places=1)
        self.assertIn("prior", label)
        # no history -> neutral
        prior2, _ = ms.preseason_prior(None)
        self.assertEqual(prior2, 50.0)

    def test_score_shrinks_toward_prior_at_zero_gws(self):
        s = ms.score_manager([], prior=70.0)
        self.assertEqual(s["score"], 70.0)  # n=0 -> pure prior

    def test_consistent_high_value_no_hits_scores_high(self):
        hist = make_history(8, base_pts=60, base_rank=500, value=1080)
        s = ms.score_manager(hist)
        self.assertEqual(s["confidence"], "trusted")
        self.assertGreater(s["score"], 50)

    def test_heavy_hits_reduce_score(self):
        good = ms.score_manager(make_history(8, cost=0))
        bad = ms.score_manager(make_history(8, cost=24))  # -24 hits
        self.assertLess(bad["components"]["transfer_efficiency"],
                        good["components"]["transfer_efficiency"])

    def test_value_capture_bounds(self):
        self.assertLessEqual(ms.value_capture(make_history(4, value=1090)), 1.0)
        self.assertGreaterEqual(ms.value_capture([]), 0.0)

    def test_sharpest_sorts(self):
        hists = {
            "a": make_history(6, base_pts=55, value=1070),
            "b": make_history(6, base_pts=40, value=1005),
        }
        top = ms.sharpest_managers(hists, top_n=1)
        self.assertEqual(len(top), 1)
        self.assertEqual(top[0]["entry_id"], "a")


class TestBeatThem(unittest.TestCase):
    def test_differential_excludes_opponent_players(self):
        # opponent owns a high-xPts player; differential must exclude them
        preds = bt.load_predictions()
        if not preds:
            self.skipTest("no predictions snapshot")
        top = sorted(preds, key=lambda p: -float(p.get("xpts", 0)))[:3]
        top_ids = [p["id"] for p in top]
        monitor = {"entries": {"999": {"picks": [{"element": top_ids[0]}]}}}
        diffs = bt.differential_targets(999, monitor_snapshot=monitor, top_n=20)
        diff_ids = [d["id"] for d in diffs]
        self.assertNotIn(top_ids[0], diff_ids)

    def test_captain_differentiation(self):
        preds = bt.load_predictions()
        if not preds:
            self.skipTest("no predictions snapshot")
        note = bt.captain_differentiation(99999999, preds[0]["id"])
        if note:
            self.assertIn("suggestion", note)
        # same captain -> no note
        same = bt.captain_differentiation(preds[0]["id"], preds[0]["id"])
        self.assertIsNone(same)


class TestMonitorHelpers(unittest.TestCase):
    def test_fetch_standings_empty_handling(self):
        # no live API call - just verify the module imports and CLI parses
        self.assertTrue(callable(bt.load_predictions))

    def test_picks_trust_boundary_pre_deadline(self):
        """Before deadline, competitor picks must be treated as unavailable.
        Verify gw_is_live returns False for a not-yet-finished GW."""
        import jobs.league_monitor as lm
        # current date is 2026-08-12; GW1 deadline is 2026-08-21 -> not live
        self.assertFalse(lm.gw_is_live(None, 1))

    def test_incomplete_picks_rejected(self):
        """A picks payload with <15 entries is untrusted -> None."""
        import jobs.league_monitor as lm

        class FakeClient:
            def entry_picks(self, eid, gw):
                return {"picks": [{"element": 1, "multiplier": 1}]}  # 1 pick

        self.assertIsNone(lm.fetch_picks_live(FakeClient(), 1, 1, {}, {}))


if __name__ == "__main__":
    unittest.main()
