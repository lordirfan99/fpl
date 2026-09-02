"""
FPL Autopilot - post-GW review (after a gameweek closes).

1. Pull event/{gw}/live/ actuals
2. Score predictions (MAE, Spearman) - the model's report card
3. Captain/transfer performance review
4. Rank + points from entry history
5. Append residuals for retraining, print Telegram report

Run: .venv/Scripts/python.exe jobs/post_gw_review.py [gw]
"""
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

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def load_settings():
    with open(os.path.join(BASE, "config", "settings.json")) as f:
        return json.load(f)


def main():
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
    if len(sys.argv) > 1:
        gw = int(sys.argv[1])
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
    write_event_rows(history_file, event_rows(gw, live, bootstrap["elements"], captured_at))
    print(f"V4.2 history: official data-checked GW{gw} persisted")

    # predictions
    pred_file = os.path.join(BASE, "data", "processed", f"predictions_gw{gw}.json")
    if not os.path.exists(pred_file):
        print("No predictions file for GW%d - model didn't predict this GW." % gw)
        preds = []
    else:
        with open(pred_file) as f:
            preds = json.load(f).get("players", [])

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
    hist = team_history.get("history", [])
    cur = next((h for h in hist if h.get("event") == gw), None)
    if cur:
        print(f"GW{gw} points: {cur.get('points')} | total: {cur.get('total_points')} | rank: {cur.get('rank')} | rank delta: {cur.get('rank_sort')}")
    else:
        print("GW%d not in entry history yet" % gw)

    # captain performance from live picks
    try:
        picks = client.entry_picks(team_id, gw)
        pick_map = {p["element"]: p for p in picks.get("picks", [])}
        cap_el = next((e for e, p in pick_map.items() if p.get("is_captain")), None)
        if cap_el:
            cap_pts = actual.get(cap_el, 0)
            print(f"captain: {els.get(cap_el, {}).get('web_name', cap_el)} scored {cap_pts} -> {2 * cap_pts} pts with armband")
    except Exception as e:
        print("captain check skipped:", repr(e)[:100])

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
                gw_points=cur.get("points") if cur else None)
            print("\n--- DECISION AUDIT (attribution) ---")
            for ln in audit["lines"]:
                print("  " + ln)
        except Exception as e:
            print("decision audit skipped:", repr(e)[:120])
    else:
        print("(no plan_gw%d.json snapshot - decision audit skipped)" % gw)

    # residuals for retraining
    res_file = os.path.join(BASE, "data", "processed", "residuals.csv")
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
        import csv
        existing = []
        fieldnames = list(new_rows[0].keys())
        if os.path.exists(res_file):
            with open(res_file, newline="", encoding="utf-8") as source:
                reader = csv.DictReader(source)
                existing = list(reader)
                fieldnames = list(dict.fromkeys((reader.fieldnames or []) + fieldnames))
        with open(res_file, "w", newline="", encoding="utf-8") as target:
            writer = csv.DictWriter(target, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(existing + new_rows)
        print(f"appended {len(new_rows)} residuals -> residuals.csv")

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
            candidate_file = os.path.join(BASE, "data", "processed", "v42_residuals.csv")
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
