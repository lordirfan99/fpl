"""FPL Autopilot Telegram Mini App backend.

Run locally:
    .venv/Scripts/python.exe -m uvicorn webapp.server:app --host 0.0.0.0 --port 8787

The app deliberately keeps FPL credentials server-side. Telegram WebApp initData is
validated on every API request. Read-only dashboard access requires valid initData;
write actions additionally require the Telegram user ID to be present in
settings.json -> miniapp.allowed_user_ids.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "execution"))
sys.path.insert(0, str(BASE / "model"))
sys.path.insert(0, str(BASE / "bot"))

from fpl_client import FPLClient  # noqa: E402
from xpts_model import inseason_xpts_from_bootstrap, preseason_xpts  # noqa: E402

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
POS_MAP = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
PLAN_FILE = BASE / "data" / "processed" / "pending_plan.json"
STATIC_DIR = BASE / "webapp" / "static"

app = FastAPI(title="FPL Autopilot Mini App", version="1.0")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def load_settings() -> dict[str, Any]:
    with open(BASE / "config" / "settings.json", encoding="utf-8") as f:
        return json.load(f)


def load_creds() -> dict[str, str]:
    creds: dict[str, str] = {}
    with open(BASE / "config" / "credentials.env", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                creds[key.strip()] = value.strip()
    return creds


def fetch_json(url: str) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def validate_init_data(init_data: str, max_age_seconds: int = 600) -> dict[str, Any]:
    """Validate Telegram WebApp initData using Telegram's documented HMAC flow."""
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing Telegram initData")

    pairs = urllib.parse.parse_qsl(init_data, keep_blank_values=True)
    data = dict(pairs)
    received_hash = data.pop("hash", None)
    if not received_hash:
        raise HTTPException(status_code=401, detail="Telegram initData has no hash")

    auth_date_raw = data.get("auth_date")
    try:
        auth_date = int(auth_date_raw or "0")
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid Telegram auth_date") from exc

    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    if auth_date <= 0 or abs(now - auth_date) > max_age_seconds:
        raise HTTPException(status_code=401, detail="Telegram session expired; reopen the Mini App")

    token = load_creds().get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise HTTPException(status_code=500, detail="Bot token is not configured")

    check_string = "\n".join(f"{key}={data[key]}" for key in sorted(data))
    secret_key = hmac.new(b"WebAppData", token.encode("utf-8"), hashlib.sha256).digest()
    calculated = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calculated, received_hash):
        raise HTTPException(status_code=401, detail="Invalid Telegram signature")

    user_raw = data.get("user")
    try:
        user = json.loads(user_raw) if user_raw else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=401, detail="Invalid Telegram user payload") from exc
    if not user.get("id"):
        raise HTTPException(status_code=401, detail="Telegram user is missing")

    data["user"] = user
    return data


def auth_user(x_telegram_init_data: str | None) -> dict[str, Any]:
    return validate_init_data(x_telegram_init_data or "")["user"]


def require_write_user(user: dict[str, Any]) -> None:
    allowed = {int(x) for x in load_settings().get("miniapp", {}).get("allowed_user_ids", [])}
    if not allowed or int(user["id"]) not in allowed:
        raise HTTPException(
            status_code=403,
            detail="Write actions are locked. Add your Telegram user ID to miniapp.allowed_user_ids.",
        )


def current_gw(bootstrap: dict[str, Any]) -> int:
    for event in bootstrap.get("events", []):
        if not event.get("finished"):
            return int(event["id"])
    return 38


def deadline_info(bootstrap: dict[str, Any], gw: int) -> dict[str, Any]:
    event = next((x for x in bootstrap.get("events", []) if int(x.get("id", 0)) == gw), None)
    if not event:
        return {"deadline": None, "hours": None}
    deadline = dt.datetime.fromisoformat(event["deadline_time"].replace("Z", "+00:00"))
    hours = (deadline - dt.datetime.now(dt.timezone.utc)).total_seconds() / 3600
    return {"deadline": event["deadline_time"], "hours": round(hours, 1)}


def build_dashboard() -> dict[str, Any]:
    settings = load_settings()
    bootstrap = fetch_json("https://fantasy.premierleague.com/api/bootstrap-static/")
    fixtures = fetch_json("https://fantasy.premierleague.com/api/fixtures/")
    gw = current_gw(bootstrap)
    gw_so_far = max(0, gw - 1)
    elements = {int(e["id"]): e for e in bootstrap.get("elements", [])}
    teams = {int(t["id"]): t for t in bootstrap.get("teams", [])}

    fdr: dict[tuple[int, int], int] = {}
    opponents: dict[int, list[str]] = {}
    for fixture in fixtures:
        if fixture.get("event") != gw:
            continue
        home, away = int(fixture["team_h"]), int(fixture["team_a"])
        fdr[(gw, home)] = int(fixture["team_h_difficulty"])
        fdr[(gw, away)] = int(fixture["team_a_difficulty"])
        opponents.setdefault(home, []).append(f"{teams.get(away, {}).get('short_name', '?')} (H)")
        opponents.setdefault(away, []).append(f"{teams.get(home, {}).get('short_name', '?')} (A)")

    client = FPLClient()
    team = client.my_team(settings["team_id"])
    tr = team.get("transfers", {})
    players: list[dict[str, Any]] = []

    for pick in sorted(team.get("picks", []), key=lambda x: x["position"]):
        element = elements.get(int(pick["element"]))
        if not element:
            continue
        difficulty = fdr.get((gw, int(element["team"])), 3)
        xp = (
            preseason_xpts(element, difficulty)
            if gw_so_far == 0
            else inseason_xpts_from_bootstrap(element, difficulty, gw_so_far)
        )
        role = "C" if pick.get("is_captain") else ("VC" if pick.get("is_vice_captain") else "")
        players.append({
            "id": int(element["id"]),
            "name": element["web_name"],
            "position": POS_MAP[int(element["element_type"])],
            "pick": int(pick["position"]),
            "starter": int(pick["position"]) <= 11 and int(pick.get("multiplier", 0)) > 0,
            "role": role,
            "price": round(float(element["now_cost"]) / 10, 1),
            "xpts": round(float(xp), 1),
            "club": teams.get(int(element["team"]), {}).get("short_name", "?"),
            "opponent": " / ".join(opponents.get(int(element["team"]), [])) or "TBC",
            "status": element.get("status", "a"),
            "chance": element.get("chance_of_playing_next_round"),
            "news": element.get("news") or "",
        })

    starters = [p for p in players if p["starter"]]
    captain = next((p for p in starters if p["role"] == "C"), None)
    vice = next((p for p in starters if p["role"] == "VC"), None)
    base_xpts = round(sum(p["xpts"] for p in starters), 1)
    projected = round(base_xpts + (captain["xpts"] if captain else 0), 1)

    formation_counts = {pos: sum(1 for p in starters if p["position"] == pos) for pos in ("DEF", "MID", "FWD")}
    formation = f"{formation_counts['DEF']}-{formation_counts['MID']}-{formation_counts['FWD']}"

    pending = None
    if PLAN_FILE.exists():
        try:
            with open(PLAN_FILE, encoding="utf-8") as f:
                raw = json.load(f)
            if raw.get("status") == "pending":
                pending = {
                    "gw": raw.get("gw"),
                    "transfers": len(raw.get("transfers", [])),
                    "target_xpts": raw.get("target_xpts"),
                    "generated_at": raw.get("generated_at"),
                    "model_version": raw.get("model_version"),
                    "competitive": raw.get("competitive"),
                }
        except (OSError, json.JSONDecodeError):
            pending = None

    return {
        "team_id": settings["team_id"],
        "gw": gw,
        "formation": formation,
        "projected_xpts": projected,
        "base_xpts": base_xpts,
        "captain": captain,
        "vice": vice,
        "team_value": round(float(tr.get("value", 0) or 0) / 10, 1),
        "bank": round(float(tr.get("bank", 0) or 0) / 10, 1),
        "deadline": deadline_info(bootstrap, gw),
        "players": players,
        "pending": pending,
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/dashboard")
def dashboard(x_telegram_init_data: str | None = Header(default=None)) -> dict[str, Any]:
    user = auth_user(x_telegram_init_data)
    return {"user": {"id": user["id"], "first_name": user.get("first_name", "")}, **build_dashboard()}


@app.get("/api/pending")
def pending(x_telegram_init_data: str | None = Header(default=None)) -> dict[str, Any]:
    auth_user(x_telegram_init_data)
    if not PLAN_FILE.exists():
        return {"pending": None}
    try:
        with open(PLAN_FILE, encoding="utf-8") as f:
            plan = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"pending": None}
    return {"pending": plan if plan.get("status") == "pending" else None}


@app.post("/api/approve")
def approve(x_telegram_init_data: str | None = Header(default=None)) -> dict[str, Any]:
    user = auth_user(x_telegram_init_data)
    require_write_user(user)
    from telegram_bot import approve_plan  # imported lazily to avoid startup side effects

    result = approve_plan()
    return {"ok": result.startswith("✅"), "message": result}


@app.post("/api/reject")
def reject(x_telegram_init_data: str | None = Header(default=None)) -> dict[str, Any]:
    user = auth_user(x_telegram_init_data)
    require_write_user(user)
    from telegram_bot import reject_plan

    result = reject_plan()
    return {"ok": result.startswith("❌") or result.startswith("✅"), "message": result}
