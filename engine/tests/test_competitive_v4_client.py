import json
import os
import sys
import unittest
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
            "meta": {"quality_status": quality, "stale": stale, "freshness_hours": 1.2},
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
    def test_planner_accepts_valid_safe_hold_context(self, urlopen):
        payload = self.payload()
        payload.update({"packet_status": "safe_hold", "executable": False, "plan": None})
        urlopen.return_value = _Response(payload)
        result = client.fetch_competitive_v4(
            58005, 1, require_executable_plan=False)
        self.assertEqual(result["context_status"], "ready")
        self.assertEqual(result["phase"], "CATCH")
        self.assertEqual(result["alignment"], 40)
        self.assertTrue(result["context_only"])

    @mock.patch.object(client.urllib.request, "urlopen")
    def test_planner_accepts_advisory_finalized_gw_context(self, urlopen):
        # The API returns packet_status "advisory" for a finalized-GW decision
        # packet — competitor-aware, not executable. Planning must accept it.
        payload = self.payload()
        payload.update({"packet_status": "advisory", "executable": False, "plan": None})
        urlopen.return_value = _Response(payload)
        result = client.fetch_competitive_v4(58005, 2, require_executable_plan=False)
        self.assertEqual(result["packet_status"], "advisory")
        self.assertEqual(result["context_status"], "ready")
        self.assertTrue(result["context_only"])

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
