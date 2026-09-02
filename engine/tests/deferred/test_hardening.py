"""FPL Autopilot - QA hardening tests (HERMES_HANDOFF.md).

Covers the approval-to-execution hardening:
  1. chip_text refuses to stage a chip without a valid simulated pending plan
     (running /chip first used to create a malformed plan that crashed
     /approve with KeyError 'captain')
  2. plan_staleness FAILS CLOSED: no gameweek / gw missing from live bootstrap /
     player id missing / status 'u' (left league) / empty bootstrap elements
  3. exclusive approval lock file: concurrent callbacks serialized, stale
     (> 15 min) lock reclaimed
  4. FPLClient.reload() refreshes BOTH bearer credentials and cookies from disk
     after token keepalive / re-login
  5. execution reconciliation polls after async 202 Accepted responses

Run: .venv/Scripts/python.exe -m unittest discover -s tests -v
"""
import datetime
import os
import shutil
import sys
import tempfile
import time
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "model"))
sys.path.insert(0, os.path.join(BASE, "optimizer"))
sys.path.insert(0, os.path.join(BASE, "execution"))
sys.path.insert(0, os.path.join(BASE, "bot"))

import telegram_bot  # noqa: E402
import fpl_client  # noqa: E402
import executor  # noqa: E402


def _plan(**overrides):
    p = {"gw": 1, "status": "pending", "transfers": [],
         "target_starters": [{"id": 1, "name": "FIT", "position": "GKP", "xpts": 3.0}],
         "bench": [], "captain": {"id": 1}, "vice": {"id": 1},
         # P0.2: plan_staleness now enforces approval_window_hours via
         # generated_at - a fresh timestamp is required for a plan to pass.
         "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}
    p.update(overrides)
    return p


# ---------------------------------------------------------------------------
# 1. Chip staging requires an existing simulated pending plan
# ---------------------------------------------------------------------------
class TestChipRequiresPlan(unittest.TestCase):
    def setUp(self):
        # Sol audit P0-1: flow tests run as the OWNER (auth in its own suite).
        self._auth_patcher = unittest.mock.patch.object(telegram_bot, "authorized", return_value=True)
        self._auth_patcher.start()
        self.addCleanup(self._auth_patcher.stop)
        self.tmpdir = tempfile.mkdtemp(prefix="fpl_chip_")
        self._orig_plan = telegram_bot.PLAN_FILE
        telegram_bot.PLAN_FILE = os.path.join(self.tmpdir, "pending_plan.json")
        # deterministic availability check, no network
        self._chips = sys.modules.get("chips")
        import chips
        self._chips = chips
        self._orig_fw = chips.fetch_chip_windows
        self._orig_cp = chips.chip_playable_in
        chips.fetch_chip_windows = lambda: {"wildcard": (2, 38), "freehit": (2, 38),
                                             "bboost": (1, 38), "3xc": (1, 38)}
        chips.chip_playable_in = lambda code, gw, windows: True
        # no network for next_gw_id
        self._orig_fetch = telegram_bot.fetch
        telegram_bot.fetch = lambda url: {"events": [{"id": 1, "finished": False}]}

    def tearDown(self):
        telegram_bot.PLAN_FILE = self._orig_plan
        telegram_bot.fetch = self._orig_fetch
        self._chips.fetch_chip_windows = self._orig_fw
        self._chips.chip_playable_in = self._orig_cp
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_no_plan_refuses_and_creates_nothing(self):
        out = telegram_bot.chip_text(["wildcard"])
        self.assertIn("No pending plan", out)
        self.assertFalse(os.path.exists(telegram_bot.PLAN_FILE),
                         "chip staging must NOT create a plan file")

    def test_malformed_plan_refuses(self):
        telegram_bot.save_pending({"chip_only": True})
        out = telegram_bot.chip_text(["wildcard"])
        self.assertIn("not a valid simulated plan", out)
        plan = telegram_bot.load_pending()
        self.assertNotIn("chip", plan, "chip must not be saved onto a malformed plan")

    def test_executed_plan_refuses(self):
        telegram_bot.save_pending(_plan(status="executed"))
        out = telegram_bot.chip_text(["wildcard"])
        self.assertIn("executed", out)

    def test_rejected_plan_refuses(self):
        telegram_bot.save_pending(_plan(status="rejected"))
        out = telegram_bot.chip_text(["wildcard"])
        self.assertIn("rejected", out)

    def test_valid_pending_plan_stages_chip(self):
        telegram_bot.save_pending(_plan())
        out = telegram_bot.chip_text(["wildcard"])
        self.assertIn("staged", out)
        plan = telegram_bot.load_pending()
        self.assertEqual(plan["chip"], "wildcard")
        self.assertEqual(plan["chip_gw"], 1)


# ---------------------------------------------------------------------------
# 2. plan_staleness fails closed
# ---------------------------------------------------------------------------
class TestPlanStalenessFailClosed(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="fpl_stale_")
        self._orig_plan = telegram_bot.PLAN_FILE
        telegram_bot.PLAN_FILE = os.path.join(self.tmpdir, "pending_plan.json")
        self.fake_bootstrap = {
            "events": [{"id": 1, "deadline_time": "2026-08-21T17:30:00Z"}],
            "elements": [
                {"id": 1, "web_name": "FIT", "status": "a",
                 "chance_of_playing_next_round": 100, "now_cost": 60},
                {"id": 5, "web_name": "GHOST", "status": "u",
                 "chance_of_playing_next_round": None, "now_cost": 60},
            ],
        }
        self._orig_fetch = telegram_bot.fetch
        telegram_bot.fetch = lambda url: self.fake_bootstrap

    def tearDown(self):
        telegram_bot.PLAN_FILE = self._orig_plan
        telegram_bot.fetch = self._orig_fetch
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_fresh_plan_still_passes(self):
        self.assertEqual(telegram_bot.plan_staleness(_plan()), "")

    def test_no_gameweek_fails_closed(self):
        reason = telegram_bot.plan_staleness(_plan(gw=None))
        self.assertIn("no gameweek", reason)

    def test_gw_missing_from_bootstrap_fails_closed(self):
        reason = telegram_bot.plan_staleness(_plan(gw=9))
        self.assertIn("not found in live bootstrap", reason)

    def test_player_missing_fails_closed(self):
        plan = _plan(target_starters=[{"id": 999, "name": "?", "position": "MID", "xpts": 4.0}])
        reason = telegram_bot.plan_staleness(plan)
        self.assertIn("not found in live bootstrap", reason)

    def test_status_u_fails_closed(self):
        # edge-case B11 at the EXECUTION gate: a left-league player in the plan
        # must block approval, not pass because only status 'i' was checked
        plan = _plan(target_starters=[{"id": 5, "name": "GHOST", "position": "MID", "xpts": 4.0}])
        reason = telegram_bot.plan_staleness(plan)
        self.assertIn("UNAVAILABLE", reason)

    def test_empty_elements_fails_closed(self):
        self.fake_bootstrap["elements"] = []
        reason = telegram_bot.plan_staleness(_plan())
        self.assertIn("no elements", reason)


# ---------------------------------------------------------------------------
# 3. Exclusive approval lock
# ---------------------------------------------------------------------------
class TestApproveLock(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="fpl_lock_")
        self._orig_lock = telegram_bot.LOCK_FILE
        telegram_bot.LOCK_FILE = os.path.join(self.tmpdir, "approve.lock")

    def tearDown(self):
        telegram_bot.LOCK_FILE = self._orig_lock
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_acquire_release(self):
        self.assertTrue(telegram_bot.acquire_approve_lock())
        self.assertTrue(os.path.exists(telegram_bot.LOCK_FILE))
        telegram_bot.release_approve_lock()
        self.assertFalse(os.path.exists(telegram_bot.LOCK_FILE))

    def test_second_acquire_refuses_while_held(self):
        self.assertTrue(telegram_bot.acquire_approve_lock())
        self.assertFalse(telegram_bot.acquire_approve_lock(),
                         "concurrent approval must be refused while lock held")
        telegram_bot.release_approve_lock()
        self.assertTrue(telegram_bot.acquire_approve_lock(),
                        "lock must be re-acquirable after release")

    def test_stale_lock_reclaimed(self):
        telegram_bot.acquire_approve_lock()
        old = time.time() - telegram_bot.LOCK_STALE_SECONDS - 60
        os.utime(telegram_bot.LOCK_FILE, (old, old))
        self.assertTrue(telegram_bot.acquire_approve_lock(),
                        "stale lock (>15 min, holder crashed) must be reclaimed")
        telegram_bot.release_approve_lock()

    def test_fresh_lock_not_reclaimed(self):
        telegram_bot.acquire_approve_lock()
        self.assertFalse(telegram_bot.acquire_approve_lock())
        telegram_bot.release_approve_lock()


# ---------------------------------------------------------------------------
# 4. FPLClient.reload() refreshes bearer + cookies
# ---------------------------------------------------------------------------
class TestSessionReload(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="fpl_sess_")
        self._orig = fpl_client.SESSION_FILE
        fpl_client.SESSION_FILE = os.path.join(self.tmpdir, "fpl_session.json")

    def tearDown(self):
        fpl_client.SESSION_FILE = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_reload_refreshes_bearer_and_cookies(self):
        fpl_client.save_session({"access_token": "OLD_TOKEN", "cookies": {"k": "old"}})
        client = fpl_client.FPLClient()
        self.assertEqual(client.s.headers.get("Authorization"), "Bearer OLD_TOKEN")
        # simulate token keepalive / re-login writing a NEW session to disk
        fpl_client.save_session({"access_token": "NEW_TOKEN",
                                 "cookies": {"k": "new", "j": "x"},
                                 "refresh_token": "rt"})
        client.reload()
        self.assertEqual(client.s.headers.get("Authorization"), "Bearer NEW_TOKEN")
        self.assertEqual(client.s.cookies.get("k"), "new")
        self.assertEqual(client.s.cookies.get("j"), "x")

    def test_reload_drops_stale_cookies(self):
        fpl_client.save_session({"access_token": "T", "cookies": {"k": "v"}})
        client = fpl_client.FPLClient()
        fpl_client.save_session({"access_token": "T2"})  # re-login, no cookies
        client.reload()
        self.assertIsNone(client.s.cookies.get("k"))
        self.assertEqual(client.s.headers.get("Authorization"), "Bearer T2")


# ---------------------------------------------------------------------------
# 5. Execution reconciliation polls after 202 Accepted
# ---------------------------------------------------------------------------
class TestReconciliationPoll(unittest.TestCase):
    def test_polls_until_squad_matches(self):
        class FakeResp:
            status_code = 202
            text = "{}"

        class FakeSession:
            def __init__(self):
                self.headers = {}
                self.cookies = {}

            def post(self, *a, **k):
                return FakeResp()

        calls = {"polls": 0}

        class FakeClient:
            def __init__(self):
                self.s = FakeSession()
                # squad fills in over polls: empty -> partial -> full
                self.states = [[], [1], [1, 2]]

            def reload(self):
                pass

            def my_team(self, team_id):
                state = self.states[min(calls["polls"], len(self.states) - 1)]
                calls["polls"] += 1
                return {"picks": [{"element": e} for e in state]}

        plan = {"team_id": 1, "gw": 1, "transfers": [], "chip": None,
                "target_starters": [{"id": 1, "position": "GKP", "xpts": 3.0}],
                "bench": [{"id": 2, "position": "MID", "xpts": 3.0}],
                "captain": {"id": 1}}

        orig_client = executor.FPLClient
        orig_tok = executor.refresh_access_token
        orig_sleep = executor.time.sleep
        executor.FPLClient = FakeClient
        executor.refresh_access_token = lambda: None
        executor.time.sleep = lambda s: None
        try:
            # P0.1: execute_plan returns (r1, r2, matched) - the reconciliation
            # result must be required before declaring success.
            r1, r2, matched = executor.execute_plan(plan)
        finally:
            executor.FPLClient = orig_client
            executor.refresh_access_token = orig_tok
            executor.time.sleep = orig_sleep

        self.assertIsNone(r1, "no transfers -> transfers POST skipped")
        self.assertEqual(r2.status_code, 202)
        self.assertTrue(matched, "final state must verify before success")
        self.assertGreaterEqual(calls["polls"], 3,
                                "reconciliation must poll until the async 202 apply lands")

    def test_verify_squad_poll_matches(self):
        class FakeClient:
            def __init__(self):
                self.polls = 0

            def my_team(self, team_id):
                self.polls += 1
                if self.polls < 3:
                    return {"picks": [{"element": 1}]}
                return {"picks": [{"element": 1}, {"element": 2}]}

        orig_sleep = executor.time.sleep
        executor.time.sleep = lambda s: None
        try:
            matched, team = executor.verify_squad_poll(
                FakeClient(), 1, {"target_starters": [{"id": 1}], "bench": [{"id": 2}]},
                attempts=5, delay=1.0)
        finally:
            executor.time.sleep = orig_sleep
        self.assertTrue(matched)
        self.assertEqual(len(team["picks"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
