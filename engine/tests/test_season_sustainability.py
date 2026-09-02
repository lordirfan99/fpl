import os
import subprocess
import sys
import unittest
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "jobs"))

import fpl_auto  # noqa: E402


class SeasonSustainabilityTests(unittest.TestCase):
    def test_only_data_checked_gameweeks_enter_calibration(self):
        bootstrap = {"events": [
            {"id": 7, "finished": True, "data_checked": True},
            {"id": 8, "finished": True, "data_checked": False},
            {"id": 9, "finished": False, "data_checked": False},
        ]}
        self.assertEqual(fpl_auto.latest_reviewable_event(bootstrap), 7)

    def test_optional_job_timeout_returns_failure_instead_of_aborting_runner(self):
        timeout = subprocess.TimeoutExpired(["python", "job.py"], 600,
                                            output="partial", stderr="stalled")
        with mock.patch.object(fpl_auto.subprocess, "run", side_effect=timeout):
            rc, stdout, stderr = fpl_auto.run("league_intelligence.py")
        self.assertEqual(rc, 124)
        self.assertEqual(stdout, "partial")
        self.assertIn("timed out", stderr)


if __name__ == "__main__":
    unittest.main()
