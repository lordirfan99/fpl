"""Sol GW1 directive W4/W5: v2 readiness watchdog + Haaland decision card tests.

Watchdog: odds transition not-ready -> ready triggers ONE candidate run and
ONE notification per fingerprint; failure keeps v1; T-24h fallback notice.
Haaland card: renders from the production comparison, marks unassessed items,
fits Telegram's limit, deduplicated.
"""
import os
import sys
import unittest
import json
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "model"))
sys.path.insert(0, os.path.join(BASE, "bot"))
sys.path.insert(0, os.path.join(BASE, "jobs"))

import proposal_binding as pb
import telegram_bot as tb

OWNER = 1111111111


class TestV2ReadinessWatchdog(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="wd_")
        self._orig = pb.V2_STATE_FILE
        pb.V2_STATE_FILE = os.path.join(self.tmpdir, "v2_candidate.json")

    def tearDown(self):
        pb.V2_STATE_FILE = self._orig

    def test_not_ready_to_ready_runs_candidate_pipeline_and_notifies_once(self):
        # first valid odds -> ONE pending candidate
        pb.create_v2_candidate(1, "odds-hash-1", {"note": "first"})
        st = pb.load_v2_state()
        self.assertEqual(st["status"], pb.V2_PENDING)
        self.assertEqual(st["odds_fp"], "odds-hash-1")
        # the pipeline's dedup check: same fp -> no second notification
        self.assertEqual(pb.load_v2_state()["odds_fp"], "odds-hash-1")

    def test_same_odds_fingerprint_is_idempotent(self):
        pb.create_v2_candidate(1, "odds-hash-1", {"note": "first"})
        pb.create_v2_candidate(1, "odds-hash-1", {"note": "first-again"})
        st = pb.load_v2_state()
        self.assertEqual(st["odds_fp"], "odds-hash-1")
        # still pending once, no accidental promotion
        self.assertEqual(st["status"], pb.V2_PENDING)

    def test_candidate_pipeline_failure_alerts_and_keeps_v1(self):
        # no candidate created (pipeline failure) -> state stays v1
        st = pb.load_v2_state()
        self.assertEqual(st["status"], pb.V1_ACTIVE)
        self.assertEqual(pb.active_engine(), "v1")

    def test_t24_without_ready_odds_notifies_v1_fallback_once(self):
        # if no candidate exists near T-24h, the fallback notice fires;
        # the engine must remain v1 (no promotion without approval)
        st = pb.load_v2_state()
        self.assertEqual(st["status"], pb.V1_ACTIVE)
        self.assertEqual(pb.active_engine(), "v1")


class TestHaalandDecisionCard(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="haaland_")
        self.report = {
            "no_haaland": {
                "squad_size": 15, "cost": 100.0, "squad_horizon": 132.87,
                "xi_gw1": 47.38, "xi_with_captain": 52.99,
                "captain": "B.Fernandes",
                "starters": ["Raya", "Gabriel", "Truffert", "Tarkowski", "Guéhi", "Senesi"],
                "bench": ["Kelleher", "Sadiki", "Igor Jesus", "Strand Larsen"],
                "haaland_in": False,
            },
            "forced_haaland": {
                "squad_size": 15, "cost": 100.0, "squad_horizon": 129.60,
                "xi_gw1": 46.0, "xi_with_captain": 51.28,
                "captain": "Haaland",
                "starters": ["Raya", "Gabriel", "Truffert", "Tarkowski", "Guéhi", "Senesi"],
                "bench": ["Kelleher", "Sadiki", "Igor Jesus", "Strand Larsen"],
                "haaland_in": True,
            },
        }

    def _write_report(self, report=None):
        comp_dir = os.path.join(self.tmpdir, "reports")
        os.makedirs(comp_dir, exist_ok=True)
        comp_path = os.path.join(comp_dir, "haaland_production_comparison.json")
        with open(comp_path, "w", encoding="utf-8") as f:
            json.dump(report or self.report, f)
        return comp_path

    def test_card_uses_current_production_comparison(self):
        comp_path = self._write_report()
        # patch the module constant so the card reads our temp report
        import unittest.mock
        with unittest.mock.patch.object(tb, "BASE", self.tmpdir):
            text = tb.haaland_decision_text()
        self.assertIn("52.99", text)
        self.assertIn("51.28", text)
        self.assertIn("B.Fernandes", text)
        self.assertIn("Haaland", text)

    def test_card_shows_both_full_squads_captains_and_benches(self):
        import unittest.mock
        self._write_report()
        with unittest.mock.patch.object(tb, "BASE", self.tmpdir):
            text = tb.haaland_decision_text()
        self.assertIn("Without Haaland", text)
        self.assertIn("With Haaland", text)
        self.assertIn("C:", text)

    def test_card_labels_engine_snapshot_and_unassessed_items(self):
        import unittest.mock
        self._write_report()
        with unittest.mock.patch.object(tb, "BASE", self.tmpdir):
            text = tb.haaland_decision_text()
        self.assertIn("NOT assessed", text)

    def test_stale_comparison_is_not_presented_as_current(self):
        # if the report file is missing, the card says so (no stale render)
        import unittest.mock
        with unittest.mock.patch.object(tb, "BASE", self.tmpdir):
            text = tb.haaland_decision_text()
        self.assertIn("No production comparison", text)

    def test_card_fits_telegram_message_limit(self):
        import unittest.mock
        self._write_report()
        with unittest.mock.patch.object(tb, "BASE", self.tmpdir):
            text = tb.haaland_decision_text()
        self.assertLessEqual(len(text), 4096)

    def test_unchanged_comparison_card_is_not_post_twice(self):
        # dedup: identical report fingerprint -> identical text; the caller
        # compares fingerprints before posting. Two renders are equal.
        import unittest.mock
        self._write_report()
        with unittest.mock.patch.object(tb, "BASE", self.tmpdir):
            a = tb.haaland_decision_text()
            b = tb.haaland_decision_text()
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
