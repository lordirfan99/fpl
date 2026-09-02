"""
FPL Autopilot - edge-case scenario tests (Category F: execution & technical).

Covers scenarios from fpl-autopilot-edge-case-scenarios-50.md:
  F41 - concurrent runs / atomic write on pending_plan.json
  F49 - bootstrap schema change fails loudly (no silent None/0)
Run: .venv/Scripts/python.exe -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "jobs"))
sys.path.insert(0, os.path.join(BASE, "bot"))
sys.path.insert(0, os.path.join(BASE, "execution"))

import daily_pull  # noqa: E402
import telegram_bot  # noqa: E402


class TestF49SchemaGuard(unittest.TestCase):
    def test_valid_bootstrap_passes(self):
        d = {"elements": [{"id": 1}], "events": [{"id": 1}],
             "teams": [{"id": 1}], "element_types": [], "total_players": 100}
        daily_pull.validate_bootstrap(d)  # must not raise

    def test_missing_keys_raise(self):
        d = {"elements": [{"id": 1}], "events": [{"id": 1}]}
        with self.assertRaises(ValueError) as ctx:
            daily_pull.validate_bootstrap(d)
        self.assertIn("missing keys", str(ctx.exception))

    def test_empty_elements_raise(self):
        d = {"elements": [], "events": [{"id": 1}],
             "teams": [{"id": 1}], "element_types": [], "total_players": 100}
        with self.assertRaises(ValueError):
            daily_pull.validate_bootstrap(d)


class TestF41AtomicWrite(unittest.TestCase):
    """save_pending() must be atomic (temp + os.replace), never half-written."""

    def setUp(self):
        self.orig = telegram_bot.PLAN_FILE
        self.testdir = tempfile.mkdtemp(prefix="fpl_test_")
        self.path = os.path.join(self.testdir, "pending_plan.json")
        telegram_bot.PLAN_FILE = self.path

    def tearDown(self):
        telegram_bot.PLAN_FILE = self.orig
        import shutil
        shutil.rmtree(self.testdir, ignore_errors=True)

    def test_save_pending_atomic(self):
        import telegram_bot
        telegram_bot.save_pending({"gw": 1, "status": "pending", "data": "x" * 5000})
        with open(self.path, encoding="utf-8") as f:
            plan = json.load(f)
        self.assertEqual(plan["status"], "pending")
        self.assertEqual(len(plan["data"]), 5000)
        # no leftover tmp files in the plan dir
        tmp_left = [f for f in os.listdir(self.testdir) if f.endswith(".tmp")]
        self.assertEqual(tmp_left, [])

    def test_save_then_load_roundtrip(self):
        import telegram_bot
        telegram_bot.save_pending({"status": "executing", "gw": 2})
        self.assertEqual(telegram_bot.load_pending()["status"], "executing")


if __name__ == "__main__":
    unittest.main(verbosity=2)
