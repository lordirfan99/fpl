"""Safe publication diagnostics and durable failure episodes (no network)."""
import datetime
import hashlib
import json
import re


CODES = {
    "source_not_ready", "plan_invalid", "account_invalid", "configuration_invalid",
    "not_configured", "privacy_guard_failed", "packet_invalid", "export_failed",
    "storage_client_failed", "storage_metadata_failed", "storage_upload_failed",
    "local_binding_failed", "runner_failed",
}
STAGES = {"validation", "export", "client", "metadata", "upload", "local_binding", "runner"}


class PacketValidationError(ValueError):
    def __init__(self, code, message):
        self.code = code if code in CODES else "packet_invalid"
        super().__init__(message)


class PublicationError(RuntimeError):
    def __init__(self, code, stage, error_class):
        self.code = code if code in CODES else "export_failed"
        self.stage = stage if stage in STAGES else "export"
        self.error_class = error_class if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,63}", error_class) else "Error"
        super().__init__(f"Dashboard preview was not published [code={self.code};stage={self.stage};error={self.error_class}]")


def publication_failure(error, stage):
    if isinstance(error, PublicationError):
        return error
    code = (error.code if isinstance(error, PacketValidationError) else
            "local_binding_failed" if stage == "local_binding" else "export_failed")
    return PublicationError(code, stage, type(error).__name__)


def storage_action(stage, action):
    try:
        return action()
    except Exception as error:
        raise PublicationError(f"storage_{stage}_failed", stage, type(error).__name__) from None


def diagnostic_from_stderr(stderr):
    """Never forward an arbitrary upstream exception message to Telegram."""
    match = re.search(r"\[code=([a-z_]+);stage=([a-z_]+);error=([A-Za-z_0-9]+)\]", stderr or "")
    if match and match[1] in CODES and match[2] in STAGES:
        return {"code": match[1], "stage": match[2], "error_class": match[3][:64]}
    classes = re.findall(r"^([A-Za-z_][A-Za-z_0-9.]{0,127}):", stderr or "", re.MULTILINE)
    error_class = classes[-1].split(".")[-1] if classes else "UnclassifiedError"
    return {"code": "runner_failed", "stage": "runner", "error_class": error_class}


def publication_health(plan, failure=None):
    manifest = (plan.get("decision_summary") or {}).get("source_manifest") or {}
    status = manifest.get("status")
    return {
        "schema_version": 1,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "status": "failed" if failure else "published",
        "gameweek": plan.get("gw") if isinstance(plan.get("gw"), int) else None,
        "run_id_hash": hashlib.sha256(str(plan.get("run_id") or "").encode()).hexdigest(),
        "source_status": status if status in {"ready", "lineup_only_safe"} else "unknown",
        "refresh_failure_count": len(manifest.get("refresh_failures") or []),
        "code": failure.code if failure else None,
        "stage": failure.stage if failure else None,
        "error_class": failure.error_class if failure else None,
    }


def failure_episode(state, key, diagnostic, now, *, gameweek, mode, rc):
    """Alert on first failure/change and after 24h, independent of retry ticks."""
    identity = {**diagnostic, "gameweek": gameweek, "mode": mode, "rc": rc}
    signature = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    previous = state.get(key) or {}
    changed = previous.get("signature") != signature
    last_alert = previous.get("alerted_at")
    try:
        elapsed = (now - datetime.datetime.fromisoformat(last_alert)).total_seconds()
    except (ValueError, TypeError):
        elapsed = float("inf")
    notify = changed or elapsed >= 86400
    state[key] = {**identity, "signature": signature,
                  "first_seen_at": now.isoformat() if changed else previous.get("first_seen_at"),
                  "last_seen_at": now.isoformat(),
                  "count": 1 if changed else int(previous.get("count", 0)) + 1,
                  "alerted_at": now.isoformat() if notify else last_alert}
    return notify
