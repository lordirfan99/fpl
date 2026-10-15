import json
import tempfile
import unittest

from evaluation_capture import (
    build_capture,
    capture_path,
    decision_metrics,
    evaluate_capture,
    freeze_capture,
    validate_provenance,
)


BEFORE = "2026-10-15T16:00:00+00:00"
DEADLINE = "2026-10-15T17:00:00+00:00"


class ImmutableCaptureTests(unittest.TestCase):
    def players(self):
        return [
            {"id": 1, "name": "A", "pos": "MID", "xpts": 6,
             "ep_next": 5, "expected_minutes": 80},
            {"id": 2, "name": "B", "pos": "DEF", "xpts": 3,
             "ep_next": 4, "expected_minutes": 70},
        ]

    def capture(self, generated=BEFORE):
        return build_capture(
            gw=7, deadline=DEADLINE, generated_at=generated,
            feature_as_of=generated, model_version="competitive-v4.0",
            feature_version="recency-v1", source_payload={"snapshot": 1},
            players=self.players(), position_baselines={"MID": 4, "DEF": 3},
        )

    def test_pre_deadline_provenance_and_same_time_baselines(self):
        value = self.capture()
        self.assertTrue(value["provenance_valid"])
        self.assertEqual(value["rows"][0]["official_ep_next"], 5)
        self.assertEqual(value["rows"][0]["lagged_position"], round(4 * 80 / 90, 3))

    def test_post_deadline_and_missing_version_fail_closed(self):
        with self.assertRaises(ValueError):
            self.capture(generated=DEADLINE)
        value = self.capture()
        value["projection_features"] = None
        with self.assertRaises(ValueError):
            validate_provenance(value)

    def test_same_gw_rerun_preserves_canonical_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = capture_path(tmp, 7, "competitive-v4.0", "recency-v1")
            first, created = freeze_capture(path, self.capture())
            second, created_again = freeze_capture(
                path, self.capture("2026-10-15T16:30:00+00:00"))
            self.assertTrue(created)
            self.assertFalse(created_again)
            self.assertEqual(first["generated_at"], second["generated_at"])
            with open(path, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["generated_at"], BEFORE)


class EvaluationTests(unittest.TestCase):
    def test_prospective_cohort_and_baselines_are_separate(self):
        capture = build_capture(
            gw=7, deadline=DEADLINE, generated_at=BEFORE, feature_as_of=BEFORE,
            model_version="competitive-v4.2-shadow", feature_version="recency-v1",
            source_payload={"snapshot": 2},
            players=[
                {"id": 1, "pos": "MID", "xpts": 6, "ep_next": 5, "expected_minutes": 80},
                {"id": 2, "pos": "DEF", "xpts": 3, "ep_next": 4, "expected_minutes": 70},
            ], position_baselines={"MID": 4, "DEF": 3},
        )
        report = evaluate_capture(capture, {1: 2, 2: 8})
        cohort = report["forecast"]["champion"]["prospective_decision_cohort"]
        self.assertEqual(cohort["n"], 1)
        self.assertEqual(report["forecast"]["official_ep_next"]["all"]["n"], 2)

    def test_decision_audit_does_not_claim_executed_team(self):
        plan = {
            "target_starters": [{"id": 1}], "bench": [{"id": 2}],
            "captain": {"id": 1}, "vice": {"id": 2},
            "transfers": [{"element_in": 1, "element_out": 2, "hit": True}],
        }
        result = decision_metrics(plan, {1: 6, 2: 2})
        self.assertEqual(result["executed_state"], "unavailable")
        self.assertEqual(result["recommended"]["captain_delta_to_best"], 0)
        self.assertEqual(result["recommended"]["transfers"][0]["net"], 0)


if __name__ == "__main__":
    unittest.main()
