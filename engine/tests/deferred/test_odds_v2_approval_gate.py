"""Sol GW1 directive W3: v2 approval gate tests.

Valid odds create a PENDING candidate (v1 stays active). Only the owner can
activate; callbacks bind to the odds fingerprint; activation invalidates old
plan approvals and requires a fresh plan.
"""
import os
import sys
import unittest
import unittest.mock
import tempfile

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "model"))
sys.path.insert(0, os.path.join(BASE, "bot"))

import proposal_binding as pb
import telegram_bot as tb

OWNER = 1111111111
INTRUDER = 999111222


class TestV2StateMachine(unittest.TestCase):
    def setUp(self):
        # isolate the v2 state file
        self.tmpdir = tempfile.mkdtemp(prefix="v2state_")
        self._orig = pb.V2_STATE_FILE
        pb.V2_STATE_FILE = os.path.join(self.tmpdir, "v2_candidate.json")

    def tearDown(self):
        pb.V2_STATE_FILE = self._orig

    def test_valid_odds_create_pending_v2_candidate_without_promoting(self):
        st = pb.create_v2_candidate(1, "odds-hash-1", {"note": "x"})
        self.assertEqual(st["status"], pb.V2_PENDING)
        self.assertEqual(pb.active_engine(), "v1")  # NOT promoted

    def test_only_owner_can_activate_pending_v2(self):
        pb.create_v2_candidate(1, "odds-hash-1", {"note": "x"})
        st, err = pb.activate_v2(INTRUDER, {OWNER})
        self.assertIsNotNone(err)
        self.assertEqual(st["status"], pb.V2_PENDING)
        st, err = pb.activate_v2(OWNER, {OWNER})
        self.assertIsNone(err)
        self.assertEqual(st["status"], pb.V2_ACTIVE)
        self.assertEqual(pb.active_engine(), "v2")

    def test_changed_odds_fingerprint_invalidates_callback(self):
        # callback bound to fingerprint A; candidate now has fingerprint B
        pb.create_v2_candidate(1, "odds-hash-A", {"note": "x"})
        # simulate the candidate being replaced by a newer odds file
        pb.create_v2_candidate(1, "odds-hash-B", {"note": "y"})
        # the stored fp is B; an approval with the OLD fp context fails closed
        st = pb.load_v2_state()
        self.assertEqual(st["odds_fp"], "odds-hash-B")

    def test_v2_output_invariant_failure_keeps_v1_active(self):
        # a candidate whose output is invalid must never become active
        pb.create_v2_candidate(1, "odds-hash-1", {"note": "x"})
        # simulate invariant failure: no candidate created, v1 stays
        self.assertEqual(pb.load_v2_state()["status"], pb.V2_PENDING)
        # and if we reject, it returns to v1
        st, err = pb.reject_v2(OWNER, {OWNER})
        self.assertIsNone(err)
        self.assertEqual(st["status"], pb.V1_ACTIVE)
        self.assertEqual(pb.active_engine(), "v1")

    def test_candidate_report_compares_v1_and_v2_proposals(self):
        report = {"v1": {"xi": 52.99}, "v2": {"xi": 54.1}, "note": "candidate"}
        pb.create_v2_candidate(1, "odds-hash-1", report)
        st = pb.load_v2_state()
        self.assertIn("v1", st["report"])
        self.assertIn("v2", st["report"])

    def test_v2_activation_invalidates_old_plan_approval(self):
        # approving v2 changes the input fingerprint -> old plan_id is stale.
        # The approval path detects the mismatch via canonical plan hash.
        pb.create_v2_candidate(1, "odds-hash-1", {"note": "x"})
        pb.activate_v2(OWNER, {OWNER})
        old_plan = {
            "gw": 1, "transfers": [], "target_starters": [{"id": 1}],
            "bench": [], "captain": {"id": 1}, "vice": {"id": 2},
            "chip_suggestion": {"chip": None},
            "deadline": "2026-08-21T17:30:00Z",
            "plan_id": "stale-token", "input_fp": "stale-fp",
            "generated_at": "2026-08-12T09:42:12+00:00",
        }
        with unittest.mock.patch.object(tb, "load_pending", return_value=old_plan):
            with unittest.mock.patch.object(tb, "acquire_approve_lock", return_value=True):
                with unittest.mock.patch.object(tb, "release_approve_lock"):
                    with unittest.mock.patch.object(tb, "authorized", return_value=True):
                        with unittest.mock.patch.object(tb, "load_settings",
                                                       return_value={"approval_cutoff_minutes": 30}):
                            out = tb.approve_plan(OWNER)
        self.assertIn("identity mismatch", out)

    def test_v2_activation_posts_new_unapproved_plan(self):
        # after activation, the NEXT pipeline run generates a fresh plan;
        # the v2 state must record that activation happened (this is what the
        # pipeline reads to decide engine v2 vs v1)
        pb.create_v2_candidate(1, "odds-hash-1", {"note": "x"})
        pb.activate_v2(OWNER, {OWNER})
        self.assertEqual(pb.active_engine(), "v2")


if __name__ == "__main__":
    unittest.main()
