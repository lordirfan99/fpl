#!/usr/bin/env python3
"""
FPL Autopilot - daily data pull (no_agent cron script).

Pulls bootstrap-static + fixtures into data/raw as timestamped snapshots.
Silent-by-default: prints to stdout ONLY when something NOTABLE changed
since the last run (doubtful list changed, price change counts changed,
or a NEW deadline entered the <36h window). Empty stdout = cron delivers
nothing -> no Telegram spam for identical state.

Dedup: state file data/processed/daily_pull_state.json remembers the last
signature sent. Identical state = silent. This kills the "same 6 doubtful
players every 4h" spam while still alerting the moment ANY player's status
changes.

Structured output (Telegram-friendly):
  header line + sections (DOUBTFUL as one bullet per player, PRICE, DEADLINE)

Canonical copy lives in the repo; cron runs the copy in
~/AppData/Local/hermes/scripts/fpl_daily_pull.py
"""
import os
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"

import json
import sys
import datetime
import urllib.request

BASE = "https://fantasy.premierleague.com/api/"
from project_paths import resolve_project_root

ROOT = str(resolve_project_root(__file__))
RAW = os.path.join(ROOT, "data", "raw")
STATE = os.path.join(ROOT, "data", "processed", "daily_pull_state.json")
HDRS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"}
EMOJI_SAFE = os.environ.get("PYTHONUTF8", "") == "1"


def out(s=""):
    """Print line. Emoji only when the cron env is UTF-8; else ASCII fallback."""
    if EMOJI_SAFE:
        print(s)
    else:
        print(s.replace("\U0001F4CA", "[DATA]").replace("\U000026A0", "[!]")
                 .replace("\U0001F4B0", "[PRICE]").replace("\U000023F0", "[TIME]")
                 .replace("\u2022", "-").replace("\u2014", "-"))


def fetch(url, timeout=90):
    req = urllib.request.Request(url, headers=HDRS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


# F49: minimal schema guard - if FPL renames/removes a critical field mid-season,
# fail LOUDLY (cron alerts on non-zero exit) instead of silently feeding None/0
# into the model. A silent schema break is the worst kind: the model keeps
# running on garbage and nobody notices until results are wrong.
REQUIRED_BOOTSTRAP_KEYS = ["elements", "events", "teams", "element_types", "total_players"]


def validate_bootstrap(d):
    missing = [k for k in REQUIRED_BOOTSTRAP_KEYS if k not in d]
    if missing:
        raise ValueError(f"bootstrap-static schema changed: missing keys {missing}")
    if not isinstance(d["elements"], list) or not d["elements"]:
        raise ValueError("bootstrap-static schema changed: elements empty/not a list")
    if not isinstance(d["events"], list) or not d["events"]:
        raise ValueError("bootstrap-static schema changed: events empty/not a list")
    return d


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f)


def build_signature(flagged, rises, falls, next_gw, now):
    """Dedup signature. MUST NOT contain time-varying components.

    A raw deadline countdown (hours-to-deadline) differs EVERY run, so an
    identical doubtful list would re-print every 4h (the 6-Aug bug: same 6
    doubtful players, deadline_hrs 365->361->357 -> spam). Bucket the deadline
    into a discrete window flag that only flips when the deadline actually
    ENTERS the <36h alert window.
    """
    window = "outside"
    if next_gw:
        hrs = (next_gw[1] - now).total_seconds() / 3600
        window = "inside" if hrs < 36 else "outside"
    return {
        "flagged": [[f["name"], f["cop"], f["news"]] for f in flagged],
        "price": [rises, falls],
        "deadline_gw": next_gw[0]["id"] if next_gw else None,
        "deadline_window": window,
    }


def main():
    os.makedirs(RAW, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    stamp = now.strftime("%Y%m%d_%H%M")

    try:
        d = fetch(BASE + "bootstrap-static/")
        validate_bootstrap(d)  # F49: fail loudly on schema changes
    except Exception as e:
        out(f"FPL PULL FAILED: {e}")
        sys.exit(1)

    # Save timestamped snapshot + latest pointer
    snap = os.path.join(RAW, f"bootstrap_{stamp}.json")
    with open(snap, "w", encoding="utf-8") as f:
        json.dump(d, f)
    with open(os.path.join(RAW, "bootstrap-static.json"), "w", encoding="utf-8") as f:
        json.dump(d, f)

    # Fixtures snapshot (cheap, useful for DGW/blank detection later)
    try:
        fx = fetch(BASE + "fixtures/")
        with open(os.path.join(RAW, f"fixtures_{stamp}.json"), "w", encoding="utf-8") as f:
            json.dump(fx, f)
        with open(os.path.join(RAW, "fixtures.json"), "w", encoding="utf-8") as f:
            json.dump(fx, f)
    except Exception:
        pass

    # Load previous snapshot for price diff
    prev = None
    snaps = sorted(f for f in os.listdir(RAW) if f.startswith("bootstrap_") and f.endswith(".json"))
    if len(snaps) >= 2:
        try:
            prev = json.load(open(os.path.join(RAW, snaps[-2]), encoding="utf-8"))
        except Exception:
            prev = None

    rises = falls = 0
    if prev:
        old = {p["id"]: p.get("now_cost", 0) for p in prev["elements"]}
        for p in d["elements"]:
            if p["id"] in old:
                if p.get("now_cost", 0) > old[p["id"]]:
                    rises += 1
                elif p.get("now_cost", 0) < old[p["id"]]:
                    falls += 1

    # Next deadline
    next_gw = None
    for ev in d["events"]:
        dl = datetime.datetime.fromisoformat(ev["deadline_time"].replace("Z", "+00:00"))
        if not ev["finished"] and dl > now:
            next_gw = (ev, dl)
            break

    # Doubtful players (sub-50% chance, with news text) - cap for noise
    flagged = []
    for p in d["elements"]:
        cop = p.get("chance_of_playing_next_round")
        if cop is not None and cop < 50:
            flagged.append({"name": p["web_name"], "cop": cop,
                            "news": (p.get("news") or "")[:60]})
        if len(flagged) >= 6:
            break

    # ---- build signature for dedup ----
    # NOTE: use LISTS (not tuples) - JSON round-trip converts tuples->lists
    # and tuple != list in Python, which would break dedup and spam every run.
    # NOTE 2: no raw countdown in the signature - only the discrete
    # deadline_window flag (build_signature). Same state = silent.
    sig = build_signature(flagged, rises, falls, next_gw, now)
    st = load_state()
    if sig == st.get("last"):
        return 0  # nothing changed -> silent

    # ---- build readable message ----
    lines = []
    lines.append(f"FPL PULL {now.strftime('%d %b %H:%M')} UTC | players={len(d['elements'])} teams={len(d['teams'])}")
    if flagged:
        lines.append("")
        lines.append("DOUBTFUL ({}):".format(len(flagged)))
        for f in flagged:
            line = "  - {} ({}%)".format(f["name"], f["cop"])
            if f["news"]:
                line += " -- " + f["news"]
            lines.append(line)
    if rises or falls:
        lines.append("")
        lines.append("PRICE CHANGES: +{} / -{}".format(rises, falls))
    if next_gw:
        ev, dl = next_gw
        hrs = (dl - now).total_seconds() / 3600
        if hrs < 36:
            lines.append("")
            lines.append("DEADLINE GW{} in {:.1f}d ({})".format(ev["id"], hrs / 24, ev["deadline_time"]))
    for ln in lines:
        out(ln)

    save_state({"last": sig})


if __name__ == "__main__":
    main()
