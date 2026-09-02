"""The one Telegram sendMessage path for every job and the bot.

`send_message()` retries once on a transient failure and **never raises** — a
notification failure must not crash the caller or fail its systemd unit. It
returns the Telegram response dict on success, or None on any failure (logged).

Replaces the previously divergent senders in deliver_stdout / fpl_auto /
pre_deadline_run / approval_reminder / post_miniapp_button.
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

_ENDPOINT = "https://api.telegram.org/bot{token}/sendMessage"
_UA = "fpl-autopilot/1.0"
_TELEGRAM_HARD_LIMIT = 4096


def _log(prefix, msg):
    print(f"{prefix}telegram_notify: {msg}", flush=True)


def send_message(token, chat_id, text, *, parse_mode=None, reply_markup=None,
                 disable_web_page_preview=True, timeout=30, retries=1, log_prefix=""):
    """POST one message. Returns the response dict, or None on any failure."""
    if not token or not chat_id:
        _log(log_prefix, "skipped (missing token or chat_id)")
        return None

    payload = {
        "chat_id": chat_id,
        "text": text[:_TELEGRAM_HARD_LIMIT],
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = (
            reply_markup if isinstance(reply_markup, str) else json.dumps(reply_markup)
        )
    data = urllib.parse.urlencode(payload).encode()

    last_error = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                _ENDPOINT.format(token=token), data=data, headers={"User-Agent": _UA}
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = json.load(response)
            if body.get("ok"):
                return body
            last_error = f"API not ok: {body.get('description')!r}"
            break  # an API-level rejection will not fix itself on retry
        except urllib.error.HTTPError as error:
            last_error = f"HTTP {error.code}"
            if error.code < 500:
                break  # 4xx (bad token, bad chat) — retrying is pointless
        except Exception as error:  # noqa: BLE001 - this path must never raise
            last_error = repr(error)[:150]
        if attempt < retries:
            time.sleep(2)

    _log(log_prefix, f"send failed ({last_error})")
    return None


def send_long_message(token, chat_id, text, *, chunk_limit=3900, **kwargs):
    """Split on line boundaries and send each chunk. Returns True if all sent."""
    chunks, current = [], ""
    for line in text.splitlines():
        if current and len(current) + len(line) + 1 > chunk_limit:
            chunks.append(current)
            current = ""
        current += line + "\n"
        if len(current) >= chunk_limit:
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)

    ok = True
    for chunk in chunks or [text]:
        if send_message(token, chat_id, chunk, **kwargs) is None:
            ok = False
    return ok
