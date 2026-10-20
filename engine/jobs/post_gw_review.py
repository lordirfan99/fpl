"""
FPL Autopilot - post-GW review (after a gameweek closes).

1. Pull event/{gw}/live/ actuals
2. Score predictions (MAE, Spearman) - the model's report card
3. Captain/transfer performance review
4. Rank + points from entry history
5. Append residuals for retraining, print Telegram report

Run: .venv/Scripts/python.exe jobs/post_gw_review.py [gw]
"""
import argparse
import json
import os
import sys
import datetime
import urllib.request

from project_paths import resolve_project_root

BASE = str(resolve_project_root(__file__))
sys.path.insert(0, os.path.join(BASE, "execution"))
sys.path.insert(0, os.path.join(BASE, "model"))
from fpl_client import FPLClient
from feature_store_v42 import event_rows, write_event_rows
from recent_form import VERSION as FORM_VERSION

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def load_settings():
    with open(os.path.join(BASE, "config", "settings.json")) as f:
        return json.load(f)


def residual_target(projection_features):
    """Keep learned-v5 calibration rows isolated from legacy residual streams."""
    if projection_features == "learned-v5.0":
        return "residuals_learned_v5.csv", True
    return ("residuals_recency_v1.csv" if projection_features == FORM_VERSION else "residuals.csv"), False


def merge_residual_rows(path, new_rows, deduplicate=False):
    import csv
    if not new_rows:
        return 0
    existing = []
    fieldnames = list(new_rows[0].keys())
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as source:
            reader = csv.DictReader(source)
            existing = list(reader)
            fieldnames = list(dict.fromkeys((reader.fieldnames or []) + fieldnames))
    if deduplicate:
        merged = {(int(row["gw"]), int(row["element"])): row for row in existing}
        merged.update({(int(row["gw"]), int(row["element"])): row for row in new_rows})
        rows = [merged[key] for key in sorted(merged)]
    else:
        rows = existing + new_rows
    with open(path, "w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(new_rows)


def history_rows(payload):
    rows = payload.get("current", payload.get("history", []))
    return rows if isinstance(rows, list) else []


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gw", nargs="?", type=int)
    parser.add_argument("--report-only", action="store_true",
                        help="Read official results and print audit without changing history, residuals or forecasts")
    args = parser.parse_args()
    settings = load_settings()
    team_id = settings["team_id"]
    client = FPLClient()

    bootstrap = fetch("https://fantasy.premierleague.com/api/bootstrap-static/")
    els = {e["id"]: e for e in bootstrap["elements"]}

    # find the last finished GW
    finished = [ev for ev in bootstrap["events"]
                if ev.get("finished") and ev.get("data_checked")]
    if not finished:
        print("No finished and data-checked gameweeks yet.")
        return
    gw = max(ev["id"] for ev in finished)
    if args.gw is not None:
        gw = args.gw
    if gw not in {int(event["id"]) for event in finished}:
        raise ValueError(f"GW{gw} is not finished and data-checked")
    print(f"=== POST-GW REVIEW: GW{gw} ===")

    live = fetch(f"https://fantasy.premierleague.com/api/event/{gw}/live/")
    actual = {}
    minutes = {}
    for el in live.get("elements", []):
        actual[el["id"]] = el.get("stats", {}).get("total_points", 0)
        minutes[el["id"]] = el.get("stats", {}).get("minutes", 0)

    # V4.2 feature history is written only after FPL marks the event finished
    # and data-checked. The merge is idempotent by (gw, element).
    captured_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    history_file = os.path.join(BASE, "data", "processed", "player_event_history.jsonl")
    if not args.report_only:
        write_event_rows(history_file, event_rows(gw, live, bootstrap["elements"], captured_at))
        print(f"V4.2 history: official data-checked GW{gw} persisted")

    # predictions
    pred_file = os.path.join(BASE, "data", "processed", f"predictions_gw{gw}.json")
    if not os.path.exists(pred_file):
        print("No predictions file for GW%d - model didn't predict this GW." % gw)
        preds = []
        prediction_features = None
    else:
        with open(pred_file) as f:
            prediction_snapshot = json.load(f)
        preds = prediction_snapshot.get("players", [])
        prediction_features = prediction_snapshot.get("projection_features")
        print(f"Historical forecast: {prediction_snapshot.get('projection_version', 'unknown')} | features {prediction_features} | captured {prediction_snapshot.get('generated_at', 'unknown')}")

    # metrics
    rows = [(p["xpts"], actual.get(p["id"], 0)) for p in preds if p["id"] in actual]
    if rows:
        xs = [r[0] for r in rows]
        ys = [r[1] for r in rows]
        mae = sum(abs(a - b) for a, b in rows) / len(rows)
        try:
            import pandas as pd
            sp = pd.Series(xs).corr(pd.Series(ys), method="spearman")
        except Exception:
            sp = None
        print(f"predicted {len(rows)} player-GWs | MAE {mae:.2f} | Spearman {sp if sp is None else round(sp, 3)}")

    # team performance
    team_history = client.entry_history(team_id)
    hist = history_rows(team_history)
    cur = next((h for h in hist if h.get("event") == gw), None)
    if cur:
        previous = max((h for h in hist if int(h.get("event", 0)) < gw),
                       key=lambda h: int(h["event"]), default={})
        delta = (int(cur["overall_rank"]) - int(previous["overall_rank"])
                 if cur.get("overall_rank") is not None and previous.get("overall_rank") is not None else None)
        hit_cost = int(cur.get("event_transfers_cost") or 0)
        print(f"GW{gw} official points: {cur.get('points')} | hits: {hit_cost} | net: {int(cur['points'])-hit_cost} | total: {cur.get('total_points')} | overall rank: {cur.get('overall_rank')}")
        if delta is not None:
            print(f"Overall rank change: {delta:+d} (positive = dropped)")
    else:
        print("GW%d not in entry history yet" % gw)

    # Historical official picks, not today's team or the frozen proposal, own
    # the actual chip, autosub and effective-captain multipliers.
    picks = None
    try:
        picks = client.entry_picks(team_id, gw)
        if len(picks.get("picks") or []) != 15:
            raise ValueError("official picks are incomplete")
        from post_gw_audit import captain_context
        context = captain_context({}, official_picks=picks)
        cap_el = context["id"]
        if cap_el in actual:
            cap_pts = actual[cap_el]
            print(f"captain: {els.get(cap_el, {}).get('web_name', cap_el)} scored {cap_pts} x {context['multiplier']} = {context['multiplier'] * cap_pts} pts | official chip: {context['chip'] or 'none'}")
        if not cur and isinstance(picks.get("entry_history"), dict):
            cur = picks["entry_history"]
            print(f"Official picks entry-history fallback: {cur.get('points')} points")
    except Exception as e:
        picks = None
        print("Official captain/chip check unavailable:", type(e).__name__)

    # --- decision audit (attribution, 7 Aug feature) ---
    # Separates bad prediction / bad minutes / captain variance / transfer
    # decision / injury after deadline / bench points / chip outcome /
    # luck vs process so "retraining" is not just accumulating residuals.
    plan_file = os.path.join(BASE, "data", "processed", f"plan_gw{gw}.json")
    if os.path.exists(plan_file):
        try:
            sys.path.insert(0, os.path.join(BASE, "jobs"))
            import post_gw_audit
            with open(plan_file, encoding="utf-8") as f:
                plan = json.load(f)
            audit = post_gw_audit.build_audit(
                plan, actual, minutes, els,
                gw_points=cur.get("points") if cur else None, official_picks=picks)
            print("\n--- DECISION AUDIT (attribution) ---")
            for ln in audit["lines"]:
                print("  " + ln)
        except Exception as e:
            print("decision audit skipped:", repr(e)[:120])
    else:
        print("(no plan_gw%d.json snapshot - decision audit skipped)" % gw)

    if args.report_only:
        print("Report-only: no history, residual, shadow or plan files changed; no FPL writes.")
        return

    # residuals for retraining
    residual_name, learned_residuals = residual_target(prediction_features)
    res_file = os.path.join(BASE, "data", "processed", residual_name)
    new_rows = []
    for p in preds:
        a = actual.get(p["id"])
        if a is None:
            continue
        new_rows.append({"gw": gw, "element": p["id"], "name": p["name"],
                         "pos": p["pos"], "predicted": p["xpts"], "actual": a,
                         "minutes": minutes.get(p["id"], 0),
                         "floor": p.get("floor"), "upside": p.get("upside"),
                         "p_start": p.get("p_start"),
                         "expected_minutes": p.get("expected_minutes")})
    if new_rows:
        merge_residual_rows(res_file, new_rows, deduplicate=learned_residuals)
        print(f"appended {len(new_rows)} residuals -> {residual_name}")

    # Score the candidate independently. This cannot alter the live champion.
    shadow_file = os.path.join(BASE, "data", "processed", f"v42_shadow_gw{gw}.json")
    if os.path.exists(shadow_file):
        with open(shadow_file, encoding="utf-8") as handle:
            shadow = json.load(handle)
        candidate_rows = []
        for player in shadow.get("players", []):
            element = int(player.get("id") or 0)
            if element not in actual:
                continue
            candidate_rows.append({
                "gw": gw, "element": element, "name": player.get("name"),
                "pos": player.get("position"), "predicted": player.get("xpts", 0),
                "actual": actual[element], "minutes": minutes.get(element, 0),
                "p_dnp": player.get("p_dnp", 0), "p_1_59": player.get("p_1_59", 0),
                "p_60_plus": player.get("p_60_plus", 0),
                "floor": player.get("xpts_floor"), "upside": player.get("xpts_upside"),
                "expected_minutes": player.get("expected_minutes"),
                "feature_as_of": shadow.get("feature_as_of"),
                "source_fingerprint": shadow.get("source_fingerprint"),
            })
        if candidate_rows:
            import csv
            candidate_name = ("v42_residuals_recency_v1.csv"
                              if shadow.get("projection_features") == FORM_VERSION else "v42_residuals.csv")
            candidate_file = os.path.join(BASE, "data", "processed", candidate_name)
            existing = []
            fieldnames = list(candidate_rows[0])
            if os.path.exists(candidate_file):
                with open(candidate_file, newline="", encoding="utf-8") as source:
                    reader = csv.DictReader(source)
                    existing = list(reader)
                    fieldnames = list(dict.fromkeys((reader.fieldnames or []) + fieldnames))
            merged = {(int(r["gw"]), int(r["element"])): r for r in existing}
            merged.update({(int(r["gw"]), int(r["element"])): r for r in candidate_rows})
            with open(candidate_file, "w", newline="", encoding="utf-8") as target:
                writer = csv.DictWriter(target, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(merged[key] for key in sorted(merged))
            print(f"V4.2 shadow: scored {len(candidate_rows)} player-GWs")


if __name__ == "__main__":
    main()
