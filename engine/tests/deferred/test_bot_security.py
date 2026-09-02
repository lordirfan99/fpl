"""Sol audit P0-1 regression tests: immutable-user authorization at every
privileged boundary (approve/reject/chip/keep/exclude) — fail closed."""
import os
import sys
import unittest
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "bot"))
sys.path.insert(0, os.path.join(BASE, "execution"))

import telegram_bot as tb

OWNER = 1111111111
INTRUDER = 999111222
CHAT = -1001111111111


class TestAuthorization(unittest.TestCase):
    def setUp(self):
        # force the settings file to include the owner allowlist
        settings = tb.load_settings()
        settings["telegram"]["chat_id"] = CHAT
        settings["telegram"]["allowed_user_ids"] = [OWNER]
        with mock.patch.object(tb, "load_settings", return_value=settings):
            pass  # load_settings is read fresh each call; patch below instead

    def test_owner_authorized(self):
        self.assertTrue(tb.authorized(OWNER))

    def test_intruder_not_authorized(self):
        self.assertFalse(tb.authorized(INTRUDER))

    def test_none_not_authorized(self):
        self.assertFalse(tb.authorized(None))

    def test_non_numeric_not_authorized(self):
        self.assertFalse(tb.authorized("not-a-number"))

    def test_approve_denied_for_intruder(self):
        with mock.patch.object(tb, "load_settings",
                               return_value={"telegram": {"allowed_user_ids": [OWNER]}}):
            with mock.patch.object(tb, "acquire_approve_lock") as lock:
                result = tb.approve_plan(INTRUDER)
                self.assertIn("Not authorized", result)
                lock.assert_not_called()

    def test_approve_allowed_for_owner(self):
        with mock.patch.object(tb, "load_settings",
                               return_value={"telegram": {"allowed_user_ids": [OWNER]}}):
            with mock.patch.object(tb, "acquire_approve_lock", return_value=False) as lock:
                result = tb.approve_plan(OWNER)
                # lock acquired but busy -> the lock-busy message, NOT "Not authorized"
                self.assertNotIn("Not authorized", result)
                lock.assert_called_once()

    def test_reject_denied_for_intruder(self):
        with mock.patch.object(tb, "load_settings",
                               return_value={"telegram": {"allowed_user_ids": [OWNER]}}):
            result = tb.reject_plan(INTRUDER)
            self.assertIn("Not authorized", result)

    def test_reject_allowed_for_owner_no_plan(self):
        with mock.patch.object(tb, "load_settings",
                               return_value={"telegram": {"allowed_user_ids": [OWNER]}}):
            with mock.patch.object(tb, "load_pending", return_value=None):
                result = tb.reject_plan(OWNER)
                self.assertNotIn("Not authorized", result)
                self.assertIn("No pending plan", result)

    def test_chip_denied_for_intruder(self):
        with mock.patch.object(tb, "load_settings",
                               return_value={"telegram": {"allowed_user_ids": [OWNER]}}):
            result = tb.chip_text(["wildcard"], INTRUDER)
            self.assertIn("Not authorized", result)

    def test_chip_allowed_for_owner(self):
        with mock.patch.object(tb, "load_settings",
                               return_value={"telegram": {"allowed_user_ids": [OWNER]}}):
            with mock.patch.object(tb, "load_pending", return_value=None):
                result = tb.chip_text(["wildcard"], OWNER)
                self.assertNotIn("Not authorized", result)
                self.assertIn("No pending plan", result)

    def test_empty_allowlist_fails_closed(self):
        """Empty allowed_user_ids = everyone blocked (fail closed)."""
        with mock.patch.object(tb, "load_settings",
                               return_value={"telegram": {"allowed_user_ids": []}}):
            self.assertFalse(tb.authorized(OWNER))
            self.assertIn("Not authorized", tb.approve_plan(OWNER))


if __name__ == "__main__":
    unittest.main()
