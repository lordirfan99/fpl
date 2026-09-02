"""telegram_notify must never raise; a delivery failure returns None/False."""
import urllib.error

import telegram_notify


def test_missing_token_or_chat_returns_none():
    assert telegram_notify.send_message(None, 123, "x") is None
    assert telegram_notify.send_message("tok", None, "x") is None


def test_network_error_is_swallowed(monkeypatch):
    def boom(*_a, **_k):
        raise ConnectionResetError("reset")

    monkeypatch.setattr(telegram_notify.urllib.request, "urlopen", boom)
    assert telegram_notify.send_message("tok", 1, "x", retries=1) is None


def test_4xx_does_not_retry(monkeypatch):
    calls = {"n": 0}

    def http_401(*_a, **_k):
        calls["n"] += 1
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

    monkeypatch.setattr(telegram_notify.urllib.request, "urlopen", http_401)
    assert telegram_notify.send_message("tok", 1, "x", retries=3) is None
    assert calls["n"] == 1  # no retry on a client error


def test_5xx_retries_then_gives_up(monkeypatch):
    calls = {"n": 0}

    def http_503(*_a, **_k):
        calls["n"] += 1
        raise urllib.error.HTTPError("u", 503, "busy", {}, None)

    monkeypatch.setattr(telegram_notify.urllib.request, "urlopen", http_503)
    monkeypatch.setattr(telegram_notify.time, "sleep", lambda _s: None)
    assert telegram_notify.send_message("tok", 1, "x", retries=2) is None
    assert calls["n"] == 3  # initial + 2 retries


def test_ok_response_is_returned(monkeypatch):
    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b'{"ok": true, "result": {"message_id": 7}}'

    monkeypatch.setattr(telegram_notify.urllib.request, "urlopen", lambda *_a, **_k: FakeResp())
    out = telegram_notify.send_message("tok", 1, "hello", parse_mode="HTML")
    assert out["ok"] is True and out["result"]["message_id"] == 7


def test_send_long_message_splits(monkeypatch):
    sent = []
    monkeypatch.setattr(
        telegram_notify, "send_message",
        lambda _t, _c, text, **_k: sent.append(text) or {"ok": True},
    )
    body = "\n".join(f"line {i}" for i in range(2000))
    assert telegram_notify.send_long_message("tok", 1, body, chunk_limit=500) is True
    assert len(sent) > 1
