"""Tests for timestamped, tamper-evident live odds metadata."""
import datetime
import io
import os
import sys
import tempfile
import unittest
import urllib.error
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "jobs"))

import odds_feed  # noqa: E402
import fetch_odds  # noqa: E402
import fpl_auto  # noqa: E402
import pre_deadline_run  # noqa: E402


class TestOddsFeedMetadata(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.csv = os.path.join(self.tmp.name, "E0.csv")
        with open(self.csv, "wb") as f:
            f.write(b"Div,Date,HomeTeam,AwayTeam,PSH,PSD,PSA\nE0,21/08/2026,A,B,2,3,4\n")
        self.now = datetime.datetime(2026, 8, 20, 12, tzinfo=datetime.timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fresh_matching_metadata_is_accepted(self):
        odds_feed.write_metadata(self.csv, "https://example.test/E0.csv", fetched_at=self.now)
        meta, reason = odds_feed.load_fresh_metadata(
            self.csv, now=self.now + datetime.timedelta(hours=6), max_age_hours=12)
        self.assertIsNone(reason)
        self.assertEqual(meta["sha256"], odds_feed.sha256_file(self.csv))

    def test_stale_metadata_is_rejected(self):
        odds_feed.write_metadata(self.csv, "https://example.test/E0.csv", fetched_at=self.now)
        meta, reason = odds_feed.load_fresh_metadata(
            self.csv, now=self.now + datetime.timedelta(hours=13), max_age_hours=12)
        self.assertIsNone(meta)
        self.assertIn("stale", reason.lower())

    def test_modified_csv_is_rejected(self):
        odds_feed.write_metadata(self.csv, "https://example.test/E0.csv", fetched_at=self.now)
        with open(self.csv, "ab") as f:
            f.write(b"tampered")
        meta, reason = odds_feed.load_fresh_metadata(self.csv, now=self.now, max_age_hours=12)
        self.assertIsNone(meta)
        self.assertIn("mismatch", reason.lower())


class TestOddsDrivenRegeneration(unittest.TestCase):
    def test_same_gw_regenerates_when_fresh_odds_hash_changes(self):
        state = {"plan_gw": 1, "plan_odds_signature": "old-hash"}
        self.assertTrue(fpl_auto.should_generate_plan(state, 1, 12, "new-hash"))

    def test_same_gw_does_not_regenerate_when_odds_are_unchanged(self):
        state = {"plan_gw": 1, "plan_odds_signature": "same-hash"}
        self.assertFalse(fpl_auto.should_generate_plan(state, 1, 12, "same-hash"))

    def test_never_generates_outside_predeadline_window(self):
        self.assertFalse(fpl_auto.should_generate_plan({}, 1, 30, "hash"))


class TestFetcherMetadataIntegration(unittest.TestCase):
    def test_preseason_http_300_is_a_silent_non_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "E0.csv")
            error = urllib.error.HTTPError(fetch_odds.URL, 300, "Multiple Choices", {}, None)
            with mock.patch.object(fetch_odds, "ODDS_DIR", tmp), \
                 mock.patch.object(fetch_odds, "TARGET", target), \
                 mock.patch.object(fetch_odds.urllib.request, "urlopen", side_effect=error):
                self.assertEqual(fetch_odds.main(), 0)
            self.assertFalse(os.path.exists(target))

    def test_successful_real_fetch_writes_matching_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "E0.csv")
            payload = (b"Div,Date,HomeTeam,AwayTeam,PSH,PSD,PSA\n" +
                       b"E0,21/08/2026,A,B,2,3,4\n" * 100)
            with mock.patch.object(fetch_odds, "ODDS_DIR", tmp), \
                 mock.patch.object(fetch_odds, "TARGET", target), \
                 mock.patch.object(fetch_odds.urllib.request, "urlopen", return_value=io.BytesIO(payload)):
                self.assertEqual(fetch_odds.main(), 0)
            meta, reason = odds_feed.load_fresh_metadata(target)
            self.assertIsNone(reason)
            self.assertEqual(meta["sha256"], odds_feed.sha256_file(target))


class TestLivePipelineFreshnessGate(unittest.TestCase):
    def test_pipeline_accepts_fresh_verified_odds(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = os.path.join(tmp, "E0.csv")
            with open(csv_path, "wb") as f:
                f.write(b"x" * 3000)
            now = datetime.datetime(2026, 8, 20, 12, tzinfo=datetime.timezone.utc)
            odds_feed.write_metadata(csv_path, "https://example.test/E0.csv", fetched_at=now)
            usable, note = pre_deadline_run.odds_file_is_fresh(
                csv_path, max_age_hours=12, now=now)
            self.assertTrue(usable)
            self.assertIsNone(note)

    def test_pipeline_falls_back_when_odds_are_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = os.path.join(tmp, "E0.csv")
            with open(csv_path, "wb") as f:
                f.write(b"x" * 3000)
            fetched = datetime.datetime(2026, 8, 20, 0, tzinfo=datetime.timezone.utc)
            odds_feed.write_metadata(csv_path, "https://example.test/E0.csv", fetched_at=fetched)
            usable, note = pre_deadline_run.odds_file_is_fresh(
                csv_path, max_age_hours=6,
                now=fetched + datetime.timedelta(hours=7))
            self.assertFalse(usable)
            self.assertIn("stale", note.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
