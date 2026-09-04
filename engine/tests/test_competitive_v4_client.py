import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "model"))
import competitive_v4_client as client  # noqa: E402


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class CompetitiveV4ClientTests(unittest.TestCase):
    def payload(self, model="competitive-v4.0", quality="valid", stale=False):
        return {
            "meta": {"quality_status": quality, "stale": stale, "freshness_hours": 1.2,
                     "snapshot_at": datetime.now(timezone.utc).isoformat(), "snapshot_gameweek": 1},
            "freshness": {"stale": stale, "status": "provisional"},
            "packet_status": "valid",
            "executable": True,
            "plan": {"gw": 1, "status": "pending"},
            "elite_count": 10,
            "elite_overlap": 4,
            "competitive": {
                "model_version": model,
                "phase": "CATCH",
                "alignment": 40,
                "target_alignment": 82,
                "core_owned": 2,
                "core_size": 5,
                "core_template": [],
                "critical_missing": [{"element": 1, "name": "Player", "position": "MID"}],
            },
        }

    @mock.patch.object(client.urllib.request, "urlopen")
    def test_returns_compact_validated_context(self, urlopen):
        urlopen.return_value = _Response(self.payload())
        result = client.fetch_competitive_v4(58005, 1)
        self.assertEqual(result["model_version"], "competitive-v4.0")
        self.assertEqual(result["phase"], "CATCH")
        self.assertEqual(result["critical_missing"][0]["name"], "Player")

    @mock.patch.object(client.urllib.request, "urlopen")
    def test_accepts_applied_packet_as_read_only_context(self, urlopen):
        payload = self.payload()
        payload.update({"packet_status": "applied", "executable": False})
        payload["plan"]["status"] = "executed"
        urlopen.return_value = _Response(payload)
        result = client.fetch_competitive_v4(58005, 1)
        self.assertEqual(result["packet_status"], "applied")

    @mock.patch.object(client.urllib.request, "urlopen")
    def test_rejects_safe_hold(self, urlopen):
        payload = self.payload()
        payload.update({"packet_status": "safe_hold", "executable": False, "plan": None})
        urlopen.return_value = _Response(payload)
        with self.assertRaises(client.CompetitiveV4Error):
            client.fetch_competitive_v4(58005, 1)

    @mock.patch.object(client.urllib.request, "urlopen")
    def test_planner_rejects_safe_hold_even_with_valid_structure(self, urlopen):
        payload = self.payload()
        payload.update({"packet_status": "safe_hold", "executable": False, "plan": None})
        urlopen.return_value = _Response(payload)
        with self.assertRaises(client.CompetitiveV4Error):
            client.fetch_competitive_v4(58005, 1, require_executable_plan=False)

    @mock.patch.object(client.urllib.request, "urlopen")
    def test_planner_accepts_advisory_finalized_gw_context(self, urlopen):
        # The API returns packet_status "advisory" for a finalized-GW decision
        # packet — competitor-aware, not executable. Planning must accept it.
        payload = self.payload()
        payload.update({"packet_status": "advisory", "executable": False, "plan": None})
        payload["meta"]["snapshot_gameweek"] = 2
        urlopen.return_value = _Response(payload)
        result = client.fetch_competitive_v4(58005, 2, require_executable_plan=False)
        self.assertEqual(result["packet_status"], "advisory")
        self.assertEqual(result["context_status"], "ready")
        self.assertTrue(result["context_only"])
        query = urlopen.call_args.args[0].full_url
        self.assertNotIn("gw=", query)
        self.assertIn("league_id=58005", query)

    @mock.patch.object(client.urllib.request, "urlopen")
    def test_planner_rejects_old_missing_future_and_wrong_gw_evidence(self, urlopen):
        for timestamp, gw in [
            (None, 1), ("bad", 1), ("2026-09-04T12:00:00", 1),
            ((datetime.now(timezone.utc) - timedelta(hours=73)).isoformat(), 1),
            ((datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(), 1),
            (datetime.now(timezone.utc).isoformat(), 2),
        ]:
            payload = self.payload()
            payload["meta"].update(snapshot_at=timestamp, snapshot_gameweek=gw)
            urlopen.return_value = _Response(payload)
            with self.assertRaises(client.CompetitiveV4Error):
                client.fetch_competitive_v4(58005, 1, require_executable_plan=False)

    @mock.patch.object(client.urllib.request, "urlopen")
    def test_planner_rejects_stale_flag_with_recent_timestamp(self, urlopen):
        urlopen.return_value = _Response(self.payload(stale=True))
        with self.assertRaises(client.CompetitiveV4Error):
            client.fetch_competitive_v4(58005, 1, require_executable_plan=False)

    def test_alignment_uses_current_account_instead_of_public_previous_squad(self):
        context = {"core_template": [{"element": 1}, {"element": 2}],
                   "alignment": 0, "target_alignment": 82,
                   "critical_missing": [{"element": 1}, {"element": 2}],
                   "model_edges": [], "template_gate": {"decision": "CONVERGE_TO_TEMPLATE"}}
        client.align_current_squad(context, {1, 2, 3})
        self.assertEqual(context["alignment"], 100)
        self.assertEqual(context["critical_missing"], [])
        self.assertEqual(context["template_gate"]["decision"], "HOLD_TEMPLATE")
        self.assertEqual(context["alignment_source"], "authenticated_current_squad")

    @mock.patch.object(client.urllib.request, "urlopen")
    def test_planner_rejects_incomplete_safe_hold_context(self, urlopen):
        payload = self.payload()
        payload.update({"packet_status": "safe_hold", "executable": False, "plan": None})
        payload["competitive"]["alignment"] = None
        urlopen.return_value = _Response(payload)
        with self.assertRaises(client.CompetitiveV4Error):
            client.fetch_competitive_v4(
                58005, 1, require_executable_plan=False)

    @mock.patch.object(client.urllib.request, "urlopen")
    def test_rejects_non_v4(self, urlopen):
        urlopen.return_value = _Response(self.payload(model="v3"))
        with self.assertRaises(client.CompetitiveV4Error):
            client.fetch_competitive_v4(58005, 1)

    @mock.patch.object(client.urllib.request, "urlopen")
    def test_rejects_invalid_quality(self, urlopen):
        urlopen.return_value = _Response(self.payload(quality="invalid"))
        with self.assertRaises(client.CompetitiveV4Error):
            client.fetch_competitive_v4(58005, 1)


if __name__ == "__main__":
    unittest.main()
