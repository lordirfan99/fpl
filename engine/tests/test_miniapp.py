import hashlib
import hmac
import json
import os
import sys
import time
import unittest
import urllib.parse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from fastapi import HTTPException  # noqa: E402
from webapp import server  # noqa: E402


def signed_init_data(token, user_id=123, auth_date=None):
    data = {
        "auth_date": str(auth_date or int(time.time())),
        "query_id": "AAE-test",
        "user": json.dumps({"id": user_id, "first_name": "Test"}, separators=(",", ":")),
    }
    check = "\n".join(f"{k}={data[k]}" for k in sorted(data))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(data)


class TestTelegramInitData(unittest.TestCase):
    def setUp(self):
        self.orig_creds = server.load_creds
        self.orig_settings = server.load_settings
        server.load_creds = lambda: {"TELEGRAM_BOT_TOKEN": "123:abc"}

    def tearDown(self):
        server.load_creds = self.orig_creds
        server.load_settings = self.orig_settings

    def test_valid_signature(self):
        result = server.validate_init_data(signed_init_data("123:abc"))
        self.assertEqual(result["user"]["id"], 123)

    def test_bad_signature_rejected(self):
        raw = signed_init_data("123:abc").replace("hash=", "hash=0")
        with self.assertRaises(HTTPException) as ctx:
            server.validate_init_data(raw)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_expired_session_rejected(self):
        raw = signed_init_data("123:abc", auth_date=int(time.time()) - 9999)
        with self.assertRaises(HTTPException) as ctx:
            server.validate_init_data(raw)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_write_requires_allowlist(self):
        server.load_settings = lambda: {"miniapp": {"allowed_user_ids": []}}
        with self.assertRaises(HTTPException) as ctx:
            server.require_write_user({"id": 123})
        self.assertEqual(ctx.exception.status_code, 403)

    def test_write_allows_configured_user(self):
        server.load_settings = lambda: {"miniapp": {"allowed_user_ids": [123]}}
        server.require_write_user({"id": 123})


if __name__ == "__main__":
    unittest.main()
