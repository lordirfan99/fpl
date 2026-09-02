"""Sol audit P0-4 regression: odds HTML placeholder can NEVER activate v2.
v2 promotion requires a real CSV (>2000 bytes) with valid hash/size metadata."""
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "jobs"))
sys.path.insert(0, os.path.join(BASE, "model"))

import pre_deadline_run as pdr


class TestOddsGate(unittest.TestCase):
    def test_html_placeholder_rejected(self):
        """The current pre-season artifact is a 1271-byte HTML page. It must
        fail the size gate -> v2 must NOT activate."""
        csv_path = os.path.join(BASE, "data", "historical", "odds", "E0_2026-27.csv")
        if not os.path.exists(csv_path):
            self.skipTest("no odds CSV present")
        usable, note = pdr.odds_file_is_fresh(csv_path)
        self.assertFalse(usable)
        self.assertIn("FDR fallback", note)

    def test_missing_file_rejected(self):
        usable, note = pdr.odds_file_is_fresh(os.path.join(BASE, "nope", "missing.csv"))
        self.assertFalse(usable)

    def test_small_file_rejected(self):
        """A tiny file (e.g. truncated CSV) fails the size gate even if it
        parses as text - fail closed, never trust file existence."""
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("Div,Date\nE0,2026-08-10\n")
            path = f.name
        try:
            usable, _ = pdr.odds_file_is_fresh(path)
            self.assertFalse(usable)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
