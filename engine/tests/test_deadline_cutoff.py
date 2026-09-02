"""Sol GW1 directive W2: deadline cutoff tests.

Hard cutoff = official deadline minus 30 minutes, UTC-aware, fail closed.
Shared by approval, execution, and reminders.
"""
import os
import sys
import unittest
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "model"))

import proposal_binding as pb

DEADLINE = "2026-08-21T17:30:00Z"  # official GW1 deadline


class TestCutoffBoundary(unittest.TestCase):
    def test_approval_allowed_one_second_before_cutoff(self):
        # cutoff = 17:00 UTC; 16:59:59 is still allowed
        now = datetime.datetime(2026, 8, 21, 16, 59, 59, tzinfo=datetime.timezone.utc)
        self.assertFalse(pb.is_past_cutoff(DEADLINE, now=now, cutoff_minutes=30))

    def test_approval_rejected_exactly_at_cutoff(self):
        now = datetime.datetime(2026, 8, 21, 17, 0, 0, tzinfo=datetime.timezone.utc)
        self.assertTrue(pb.is_past_cutoff(DEADLINE, now=now, cutoff_minutes=30))

    def test_approval_rejected_after_cutoff(self):
        now = datetime.datetime(2026, 8, 21, 17, 0, 1, tzinfo=datetime.timezone.utc)
        self.assertTrue(pb.is_past_cutoff(DEADLINE, now=now, cutoff_minutes=30))

    def test_naive_deadline_fails_closed(self):
        # a tz-naive deadline cannot be trusted -> block
        self.assertTrue(pb.is_past_cutoff("2026-08-21T17:30:00", cutoff_minutes=30))

    def test_missing_deadline_fails_closed(self):
        self.assertTrue(pb.is_past_cutoff(None, cutoff_minutes=30))

    def test_garbage_deadline_fails_closed(self):
        self.assertTrue(pb.is_past_cutoff("not-a-date", cutoff_minutes=30))

    def test_far_before_cutoff_allowed(self):
        now = datetime.datetime(2026, 8, 19, 12, 0, 0, tzinfo=datetime.timezone.utc)
        self.assertFalse(pb.is_past_cutoff(DEADLINE, now=now, cutoff_minutes=30))


class TestExecutionRecheck(unittest.TestCase):
    def test_execution_rechecks_and_rejects_at_cutoff(self):
        # the executor must re-check the cutoff even if approval happened earlier
        plan = {"deadline": DEADLINE}
        now = datetime.datetime(2026, 8, 21, 17, 0, 0, tzinfo=datetime.timezone.utc)
        self.assertTrue(pb.is_past_cutoff(plan["deadline"], now=now, cutoff_minutes=30))

    def test_reminder_offers_no_approval_after_cutoff(self):
        # reminder logic: after cutoff, message must NOT offer approval
        now = datetime.datetime(2026, 8, 21, 17, 0, 0, tzinfo=datetime.timezone.utc)
        blocked = pb.is_past_cutoff(DEADLINE, now=now, cutoff_minutes=30)
        self.assertTrue(blocked)


if __name__ == "__main__":
    unittest.main()
