#!/usr/bin/env python3
"""Read-only, owner-approved FPL hybrid review sidecar.

This module deliberately has no execution client imports. It records advisory
research bound to an exact model plan and evaluates frozen weekly snapshots.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

UTC = dt.timezone.utc
DEFAULT_STATE = Path.home() / ".local" / "state" / "fpl-hybrid-review"
PUBLIC_BASE = "https://fantasy.premierleague.com/api"
_ALLOWED_SUGGESTIONS = {"retain", "recalculate", "request_approval", "hold"}
_ALLOWED_VERDICTS = {"agree", "disagree", "insufficient_evidence"}


class SidecarError(RuntimeError):
    """A visible, safe failure; callers must not fall back to stale state."""


def now() -> str:
    return dt.datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def read_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SidecarError(f"invalid or unreadable JSON: {path}") from exc


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


@contextlib.contextmanager
def locked(state: Path):
    state.mkdir(parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    with (state / ".lock").open("a+", encoding="utf-8") as fh:
        os.chmod(state / ".lock", 0o600)
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield


def compact(value: Any) -> Any:
    """Remove volatile timestamps and secrets before fingerprinting/output."""
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items() if k.lower() not in {"capturedat", "captured_at", "checked_at", "updated_at", "timestamp", "token", "password", "cookie", "authorization"}}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


def _safe_remote_env() -> tuple[str, str, str]:
    host = os.environ.get("FPL_REVIEW_SSH_HOST", "100.72.109.7")
    user = os.environ.get("FPL_REVIEW_SSH_USER", "irfan")
    identity = os.environ.get("FPL_REVIEW_SSH_IDENTITY", str(Path.home() / ".ssh" / "pc_irfan_key"))
    if not re.fullmatch(r"[A-Za-z0-9_.@:-]+", host) or not re.fullmatch(r"[A-Za-z0-9_.-]+", user):
        raise SidecarError("unsafe SSH host/user configuration")
    if not os.path.isabs(identity) or not os.path.exists(identity):
        raise SidecarError("SSH identity is missing or not an absolute local path")
    return host, user, identity


_REMOTE_SCRIPT = r'''import json, os
root = "/opt/fpl-autopilot"
out = {}
for name in ("pending_plan.json", "dashboard_plan.json"):
    path = os.path.join(root, name)
    try:
        with open(path, encoding="utf-8") as f: out[name] = json.load(f)
    except FileNotFoundError: pass
    except Exception as e: out[name] = {"error": type(e).__name__}
try:
    import sys
    sys.path.insert(0, root)
    from engine.execution.fpl_client import FPLClient
    team_id = int(os.environ["FPL_REVIEW_ENTRY"])
    out["account"] = FPLClient().my_team(team_id)
except Exception as e:
    out["account_error"] = type(e).__name__
print(json.dumps(out, separators=(",", ":")))
'''


def remote_probe(entry: int, timeout: int = 25) -> dict[str, Any]:
    host, user, identity = _safe_remote_env()
    # Do not forward the agent's environment (which may contain unrelated secrets).
    env = {"FPL_REVIEW_ENTRY": str(entry), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-i", identity, f"{user}@{host}", "/opt/fpl-autopilot/.venv/bin/python", "-"]
    try:
        result = subprocess.run(command, input=_REMOTE_SCRIPT, text=True, capture_output=True, timeout=timeout, check=True, env=env)
        data = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise SidecarError("read-only SSH probe failed") from exc
    if not isinstance(data, dict) or "account_error" in data:
        raise SidecarError("remote source missing a fresh account snapshot")
    return data


def public_json(path: str, timeout: int = 20) -> Any:
    request = urllib.request.Request(f"{PUBLIC_BASE}/{path.lstrip('/')}", headers={"User-Agent": "fpl-hybrid-review/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise SidecarError(f"official public source failed: {path}") from exc


def plan_from_probe(probe: dict[str, Any], gw: int) -> tuple[dict[str, Any], str]:
    candidates = []
    for name in ("pending_plan.json", "dashboard_plan.json"):
        item = probe.get(name)
        if isinstance(item, dict) and item.get("gameweek") in (gw, str(gw)) and not item.get("error"):
            candidates.append((name, item))
    if not candidates:
        raise SidecarError("no fresh same-gameweek pending/dashboard plan")
    # Pending is preferred because it is the canonical actionable artifact.
    name, plan = next(((n, p) for n, p in candidates if n == "pending_plan.json"), candidates[0])
    if not plan.get("plan_id") and not plan.get("id"):
        raise SidecarError("plan has no exact plan id")
    return plan, "pending_executed" if name == "pending_plan.json" and plan.get("executed") else ("pending" if name == "pending_plan.json" else "dashboard_preview")


def public_snapshot(plan: dict[str, Any], include_summaries: bool = False) -> dict[str, Any]:
    """Capture only model-owned/target players, not the entire public catalogue."""
    bootstrap = public_json("bootstrap-static/")
    if not isinstance(bootstrap, dict):
        raise SidecarError("official bootstrap has invalid shape")
    picks = plan.get("picks") or plan.get("lineup") or []
    ids = {int(p.get("element", p.get("id"))) for p in picks if isinstance(p, dict) and str(p.get("element", p.get("id", ""))).isdigit()}
    elements = [e for e in bootstrap.get("elements", []) if isinstance(e, dict) and e.get("id") in ids]
    fixtures = public_json("fixtures/")
    result: dict[str, Any] = {"elements": elements, "fixtures": fixtures if isinstance(fixtures, list) else []}
    if include_summaries:
        result["element_summaries"] = {str(pid): public_json(f"element-summary/{pid}/") for pid in sorted(ids)}
    return result


def snapshot(probe: dict[str, Any], plan: dict[str, Any], source_kind: str, gw: int, entry: int) -> dict[str, Any]:
    account = probe.get("account")
    if not isinstance(account, dict):
        raise SidecarError("account snapshot is missing")
    return {"gameweek": gw, "entry": entry, "plan_id": plan.get("plan_id", plan.get("id")), "source_kind": source_kind,
            "plan": plan, "account": {k: account[k] for k in ("picks", "bank", "transfers", "transfers_available") if k in account},
            "captured_at": now()}


def event_key(snap: dict[str, Any]) -> str:
    stable = compact(snap)
    return f"gw{snap['gameweek']}:{snap['plan_id']}:{sha(stable)[:16]}"


def changed_events(previous: dict[str, Any] | None, current: dict[str, Any], deadline: dt.datetime | None = None) -> list[str]:
    if previous is None:
        events = ["first_monitor"]
    else:
        old = compact(previous)
        new = compact(current)
        events = []
        if sha(old.get("plan")) != sha(new.get("plan")): events.append("plan_changed")
        if sha(old.get("account")) != sha(new.get("account")): events.append("account_changed")
        if old.get("public_hash") != new.get("public_hash"): events.append("public_data_changed")
        if old.get("gameweek") != new.get("gameweek"): events.append("gameweek_changed")
    if deadline:
        hours = (deadline - dt.datetime.now(UTC)).total_seconds() / 3600
        if 0 <= hours <= 1: events.append("deadline_1h")
        elif 1 < hours <= 2: events.append("deadline_2h")
        elif 2 < hours <= 24: events.append("deadline_24h")
    return sorted(set(events))


def monitor(state: Path, snap: dict[str, Any], public_hash: str = "", deadline: dt.datetime | None = None) -> dict[str, Any]:
    snap = dict(snap, public_hash=public_hash)
    with locked(state):
        last_path = state / "last_snapshot.json"
        previous = read_json(last_path) if last_path.exists() else None
        events = changed_events(previous, snap, deadline)
        record = {"event_key": event_key(snap), "fingerprint": sha(compact(snap)), "events": events, "snapshot_id": sha(compact(snap)), "state_path": str(state)}
        atomic_json(last_path, snap)
        atomic_json(state / "last_monitor.json", record)
    return record


def research_context(source: dict[str, Any], snap: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Return compact, JSON-only material safe for the web-only review job."""
    public = source.get("public", {})
    if not isinstance(public, dict):
        public = {}
    return {"event_key": event["event_key"], "snapshot_id": event["snapshot_id"],
            "gameweek": snap["gameweek"], "entry": snap["entry"], "plan_id": snap["plan_id"],
            "source_kind": snap["source_kind"], "model_plan": compact(snap["plan"]),
            "observed_account": compact(snap["account"]), "public_context": compact(public)}


def validate_review(review: dict[str, Any], expected: dict[str, Any]) -> None:
    required = {"event_key", "snapshot_id", "verdict", "suggested_action", "checked_at", "evidence", "affected_players", "uncertainty"}
    missing = required - review.keys()
    if missing: raise SidecarError(f"review missing fields: {sorted(missing)}")
    if review["event_key"] != expected["event_key"] or review["snapshot_id"] != expected["snapshot_id"]: raise SidecarError("review is not bound to current event")
    if review["verdict"] not in _ALLOWED_VERDICTS or review["suggested_action"] not in _ALLOWED_SUGGESTIONS: raise SidecarError("invalid verdict or suggested action")
    if not isinstance(review["uncertainty"], (int, float)) or not 0 <= review["uncertainty"] <= 1: raise SidecarError("uncertainty must be 0..1")
    if not isinstance(review["evidence"], list) or not review["evidence"]: raise SidecarError("at least one evidence item is required")
    for item in review["evidence"]:
        if not isinstance(item, dict) or not item.get("url") or "published_at" not in item or not item.get("checked_at"): raise SidecarError("evidence needs url, published_at (nullable), checked_at")
    for player in review["affected_players"]:
        if not isinstance(player, dict) or not isinstance(player.get("id"), int): raise SidecarError("affected player needs numeric id")
        allowed = {int(p.get("element", p.get("id"))) for p in (expected["snapshot"].get("plan", {}).get("picks") or expected["snapshot"].get("plan", {}).get("lineup") or []) if isinstance(p, dict) and str(p.get("element", p.get("id", ""))).isdigit()}
        allowed.update(int(pid) for pid in expected["snapshot"].get("plan", {}).get("target_player_ids", []) if str(pid).isdigit())
        if player["id"] not in allowed: raise SidecarError("affected player is not in model or declared shortlist")
    if review.get("alternative"):
        _validate_alternative(review["alternative"], expected["snapshot"].get("plan", {}))


def _validate_alternative(alt: dict[str, Any], plan: dict[str, Any]) -> None:
    model = plan.get("picks") or plan.get("lineup")
    if not isinstance(model, list): raise SidecarError("model plan has no picks for alternative binding")
    model_ids = {int(p.get("element", p.get("id"))) for p in model if isinstance(p, dict) and str(p.get("element", p.get("id", ""))).isdigit()}
    alt_ids = {int(p.get("element", p.get("id"))) for p in alt.get("picks", []) if isinstance(p, dict) and str(p.get("element", p.get("id", ""))).isdigit()}
    if alt_ids != model_ids: raise SidecarError("alternative must reorder the same model 15; transfers are not allowed")
    if len(alt.get("picks", [])) != 15: raise SidecarError("alternative must contain exactly 15 picks")


def record_review(state: Path, review: dict[str, Any]) -> Path:
    with locked(state):
        latest = read_json(state / "last_monitor.json") if (state / "last_monitor.json").exists() else None
        snap = read_json(state / "last_snapshot.json") if (state / "last_snapshot.json").exists() else None
        if not latest or not snap: raise SidecarError("no current monitor snapshot")
        expected = dict(latest, snapshot=snap)
        validate_review(review, expected)
        key = re.sub(r"[^A-Za-z0-9_.:-]", "_", review["event_key"])
        path = state / "reviews" / f"{key}.json"
        if path.exists():
            old = read_json(path)
            if sha(old) != sha(review): raise SidecarError("immutable review already exists with different content")
            return path
        payload = dict(review, recorded_at=now(), input_hash=sha(compact(snap)))
        atomic_json(path, payload)
        return path


def _picks(item: dict[str, Any]) -> list[dict[str, Any]]:
    picks = item.get("picks") or item.get("lineup")
    if not isinstance(picks, list) or len(picks) != 15: raise SidecarError("recommendation must contain 15 picks")
    return [dict(p) for p in picks]


def score_lineup(recommendation: dict[str, Any], players: dict[int, dict[str, Any]]) -> int:
    picks = _picks(recommendation)
    starters = [p for p in picks if int(p.get("position", 0)) <= 11]
    bench = sorted([p for p in picks if int(p.get("position", 0)) > 11], key=lambda p: int(p["position"]))
    if len(starters) != 11 or len(bench) != 4: raise SidecarError("recommendation positions must be 1..15")
    def pdata(p):
        pid = int(p.get("element", p.get("id"))); return pid, players.get(pid, {})
    active = list(starters)
    for starter in starters:
        pid, stat = pdata(starter)
        if stat.get("minutes", 0): continue
        for sub in bench:
            sid, sstat = pdata(sub)
            if sstat.get("minutes", 0) and _legal_formation(active, starter, sub, players):
                active[active.index(starter)] = sub
                break
    total = 0
    captain = next((p for p in picks if p.get("is_captain") or p.get("captain")), None)
    vice = next((p for p in picks if p.get("is_vice_captain") or p.get("vice_captain")), None)
    captain_id = int(captain.get("element", captain.get("id"))) if captain else None
    captain_played = captain_id in {int(p.get("element", p.get("id"))) for p in active} and players.get(captain_id, {}).get("minutes", 0) > 0
    for p in active:
        pid, stat = pdata(p); multiplier = 1
        if pid == captain_id and captain_played: multiplier = 2
        total += int(stat.get("points", 0)) * multiplier
    if not captain_played and vice:
        vid = int(vice.get("element", vice.get("id")))
        if vid in {int(p.get("element", p.get("id"))) for p in active} and players.get(vid, {}).get("minutes", 0) > 0:
            total += int(players[vid].get("points", 0))
    return total


def _legal_formation(active: list[dict[str, Any]], outgoing: dict[str, Any], incoming: dict[str, Any], players: dict[int, dict[str, Any]]) -> bool:
    positions = [players.get(int(p.get("element", p.get("id"))), {}).get("position", p.get("element_type")) for p in active if p != outgoing]
    positions.append(players.get(int(incoming.get("element", incoming.get("id"))), {}).get("position", incoming.get("element_type")))
    # FPL formation: one GK; >=3 DEF, >=2 MID, >=1 FWD.
    return positions.count(1) == 1 and positions.count(2) >= 3 and positions.count(3) >= 2 and positions.count(4) >= 1


def evaluate(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("status") != "finished" or payload.get("data_checked") is not True: raise SidecarError("evaluation requires finished, data_checked outcome")
    players = {int(k): v for k, v in payload.get("players", {}).items()}
    result = {"gameweek": payload.get("gameweek"), "evaluated_at": now(), "scope": "weekly_decision_comparison"}
    for name in ("model", "hybrid"):
        item = payload.get(name)
        if not isinstance(item, dict): raise SidecarError(f"missing {name} recommendation")
        gross = score_lineup(item, players)
        hits = int(item.get("hit_cost", 0))
        result[name] = {"gross_points": gross, "hit_cost": hits, "net_points": gross - hits}
    actual = payload.get("owner_actual")
    if not isinstance(actual, dict) or not actual.get("official"):
        raise SidecarError("owner_actual must be official and explicitly marked")
    result["owner_actual"] = {"net_points": actual.get("net_points"), "source": actual.get("source")}
    return result


def cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("monitor", "collect", "record", "status", "evaluate"))
    parser.add_argument("--state-dir", type=Path, default=Path(os.environ.get("FPL_REVIEW_STATE_DIR", DEFAULT_STATE)))
    parser.add_argument("--input", type=Path, help="captured probe/review/evaluation JSON")
    parser.add_argument("--gameweek", type=int)
    parser.add_argument("--entry", type=int, default=int(os.environ.get("FPL_REVIEW_ENTRY", "2797967")))
    args = parser.parse_args()
    try:
        if args.command in {"monitor", "collect"}:
            if not args.gameweek: raise SidecarError("--gameweek is required")
            offline = bool(args.input)
            source = read_json(args.input) if offline else remote_probe(args.entry)
            plan, kind = plan_from_probe(source, args.gameweek)
            if not offline:
                source["public"] = public_snapshot(plan, include_summaries=args.command == "collect")
            snap = snapshot(source, plan, kind, args.gameweek, args.entry)
            public_hash = sha(source.get("public", {})) if isinstance(source.get("public"), dict) else ""
            result = monitor(args.state_dir, snap, public_hash=public_hash)
            if args.command == "collect": result["research_material"] = research_context(source, snap, result)
            print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        elif args.command == "record":
            if not args.input: raise SidecarError("--input is required")
            print(str(record_review(args.state_dir, read_json(args.input))))
        elif args.command == "status":
            path = args.state_dir / "last_monitor.json"
            print(json.dumps(read_json(path) if path.exists() else {"status": "never_monitored"}, sort_keys=True))
        else:
            if not args.input: raise SidecarError("--input is required")
            print(json.dumps(evaluate(read_json(args.input)), sort_keys=True))
        return 0
    except SidecarError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(cli())
