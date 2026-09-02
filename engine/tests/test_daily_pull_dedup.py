"""FPL Autopilot - daily_pull dedup signature regression tests.

The 6-Aug bug: the dedup signature contained a RAW deadline countdown
(deadline_hrs), which differs on EVERY run (365->361->357...). The same
6 doubtful players re-printed every 4h -> Telegram spam. The signature must
only contain discrete state changes.

Run: .venv/Scripts/python.exe -m unittest discover -s tests -v
"""
import os
import sys
import unittest
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "jobs"))

import daily_pull  # noqa: E402


def _next_gw(hours_ahead):
    now = datetime.datetime(2026, 8, 6, 12, 0, tzinfo=datetime.timezone.utc)
    dl = now + datetime.timedelta(hours=hours_ahead)
    return ({"id": 1}, dl)


class TestBuildSignature(unittest.TestCase):
    def setUp(self):
        self.flagged = [
            {"name": "J.Timber", "cop": 0, "news": "Groin injury - Expected back 21 Aug"},
            {"name": "Saliba", "cop": 0, "news": "Back injury - Unknown return date"},
        ]
        self.now = datetime.datetime(2026, 8, 6, 12, 0, tzinfo=datetime.timezone.utc)

    def test_time_does_not_change_signature(self):
        """REGRESSION (6-Aug spam): the same doubtful list must produce the
        same signature no matter how many hours pass between runs."""
        s1 = daily_pull.build_signature(self.flagged, 0, 0, _next_gw(365), self.now)
        later = self.now + datetime.timedelta(hours=8)  # two cron ticks later
        s2 = daily_pull.build_signature(self.flagged, 0, 0, _next_gw(357), later)
        self.assertEqual(s1, s2, "raw countdown leaked into the signature -> spam")

    def test_window_entry_flips_signature(self):
        """The ONLY time-based alert: crossing into the <36h window."""
        outside = daily_pull.build_signature(self.flagged, 0, 0, _next_gw(40), self.now)
        inside = daily_pull.build_signature(self.flagged, 0, 0, _next_gw(30), self.now)
        self.assertNotEqual(outside, inside)
        self.assertEqual(outside["deadline_window"], "outside")
        self.assertEqual(inside["deadline_window"], "inside")

    def test_inside_window_stable(self):
        """Inside the window, the countdown must NOT keep re-alerting."""
        s1 = daily_pull.build_signature(self.flagged, 0, 0, _next_gw(30), self.now)
        s2 = daily_pull.build_signature(self.flagged, 0, 0, _next_gw(26), self.now + datetime.timedelta(hours=4))
        self.assertEqual(s1, s2)

    def test_player_change_changes_signature(self):
        changed = [dict(self.flagged[0], cop=20)] + self.flagged[1:]
        s1 = daily_pull.build_signature(self.flagged, 0, 0, _next_gw(365), self.now)
        s2 = daily_pull.build_signature(changed, 0, 0, _next_gw(365), self.now)
        self.assertNotEqual(s1, s2)

    def test_price_change_changes_signature(self):
        s1 = daily_pull.build_signature(self.flagged, 0, 0, _next_gw(365), self.now)
        s2 = daily_pull.build_signature(self.flagged, 3, 1, _next_gw(365), self.now)
        self.assertNotEqual(s1, s2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
