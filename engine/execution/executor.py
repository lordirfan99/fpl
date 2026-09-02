"""
FPL Autopilot - executor: apply the optimizer's squad to the live FPL account.

  --plan    : compute + validate the transfer plan. NO writes to FPL.
  --execute : POST the transfers, then set lineup + captain, then verify.

Approval mode: run --plan, show the card, get explicit user approval,
then run --execute.

Run: .venv/Scripts/python.exe execution/executor.py [--plan|--execute]
"""
import json
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "execution"))
sys.path.insert(0, os.path.join(BASE, "optimizer"))
from fpl_client import FPLClient, refresh_access_token
from plan_validation import InvalidPlanError, validate_live_squad, validate_plan

import time

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
POS_MAP = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
TEAM_ID = 2797967
TARGET_FILE = os.path.join(BASE, "data", "processed", "squad_build_gw1.json")


def fetch_elements():
    import urllib.request
    req = urllib.request.Request("https://fantasy.premierleague.com/api/bootstrap-static/",
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)["elements"]


def build_plan():
    client = FPLClient()
    team = client.my_team(TEAM_ID)
    if not team.get("picks"):
        raise RuntimeError("no current picks found")
    current = team["picks"]
    with open(TARGET_FILE) as f:
        target = json.load(f)
    target_squad = target["squad"]
    target_starters = target["starters"]

    els = {e["id"]: e for e in fetch_elements()}
    cur_ids = {p["element"] for p in current}
    tgt_ids = [p["id"] for p in target_squad]
    tgt_set = set(tgt_ids)

    keep = sorted(cur_ids & tgt_set)
    outs = [eid for eid in cur_ids if eid not in tgt_set]
    ins = [eid for eid in tgt_ids if eid not in cur_ids]

    out_by_pos, in_by_pos = {}, {}
    for eid in outs:
        out_by_pos.setdefault(POS_MAP[els[eid]["element_type"]], []).append(eid)
    for eid in ins:
        in_by_pos.setdefault(POS_MAP[els[eid]["element_type"]], []).append(eid)

    transfers = []
    for pos in ("GKP", "DEF", "MID", "FWD"):
        olist = out_by_pos.get(pos, [])
        ilist = in_by_pos.get(pos, [])
        if len(olist) != len(ilist):
            raise RuntimeError(f"position mismatch {pos}: out {len(olist)} in {len(ilist)}")
        for oeid, ieid in zip(olist, ilist):
            transfers.append({
                "element_in": ieid,
                "element_out": oeid,
                "purchase_price": int(els[ieid]["now_cost"]),
                "selling_price": int(els[oeid]["now_cost"]),
            })

    cost_out = sum(t["selling_price"] for t in transfers)
    cost_in = sum(t["purchase_price"] for t in transfers)
    bank = team.get("transfers", {}).get("bank", 0)
    budget_ok = cost_in - cost_out <= bank
    clubs = {}
    for eid in tgt_ids:
        clubs[els[eid]["team"]] = clubs.get(els[eid]["team"], 0) + 1
    club_ok = all(v <= 3 for v in clubs.values())
    quota = {}
    for eid in tgt_ids:
        quota[POS_MAP[els[eid]["element_type"]]] = quota.get(POS_MAP[els[eid]["element_type"]], 0) + 1
    quota_ok = quota == {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}

    cap = max(target_starters, key=lambda p: p["xpts"])
    vice = max((p for p in target_starters if p["id"] != cap["id"]), key=lambda p: p["xpts"])

    plan = {
        "team_id": TEAM_ID,
        "transfers": transfers,
        "keep": keep,
        "target_starters": target_starters,
        "bench": target.get("bench", []),
        "captain": cap,
        "vice": vice,
        "validation": {
            "transfers": len(transfers),
            "cost_out": cost_out / 10,
            "cost_in": cost_in / 10,
            "budget_ok": budget_ok,
            "club_ok": club_ok,
            "quota_ok": quota_ok,
        },
        "current_xpts": sum(float(p["xpts"]) for p in target.get("current_squad", []) or []),
        "target_xpts": sum(float(p["xpts"]) for p in target_squad),
    }
    return plan, els


def print_plan(plan, els):
    print(f"TEAM {plan['team_id']} | REBUILD PLAN | {len(plan['transfers'])} transfers (pre-season: FREE)")
    print("=" * 66)
    print("OUT (from current) -> IN (to optimized):")
    for t in plan["transfers"]:
        oe = els[t["element_out"]]
        ie = els[t["element_in"]]
        print("  %-16s (%s, %4.1fm)  ->  %-16s (%s, %4.1fm)" % (
            oe["web_name"], POS_MAP[oe["element_type"]], t["selling_price"] / 10,
            ie["web_name"], POS_MAP[ie["element_type"]], t["purchase_price"] / 10))
    if plan["keep"]:
        print(f"KEEP: {', '.join(els[e]['web_name'] for e in plan['keep'])}")
    print("-" * 66)
    print("STARTING XI (from target):")
    for i, p in enumerate(plan["target_starters"], 1):
        star = " (C)" if p["id"] == plan["captain"]["id"] else ""
        print("  %2d. %-4s %-16s xPts %4.1f%s" % (i, p["position"], p["name"], p["xpts"], star))
    print("-" * 66)
    v = plan["validation"]
    print(f"VALIDATION: transfers={v['transfers']} | cost {v['cost_in']:.1f}m in / {v['cost_out']:.1f}m out"
          f" | budget_ok={v['budget_ok']} | club_ok={v['club_ok']} | quota_ok={v['quota_ok']}")
    print(f"PROJECTED: optimized {plan['target_xpts']:.1f} xPts (GW1) | current {plan['current_xpts']:.1f} | delta {plan['target_xpts'] - plan['current_xpts']:+.1f}")
    if not all([v["budget_ok"], v["club_ok"], v["quota_ok"]]):
        print("!! VALIDATION FAILED - do not execute")


def execute_plan(plan):
    errors = validate_plan(plan)
    if errors:
        raise InvalidPlanError(errors)
    team_id = plan.get("team_id", TEAM_ID)
    gw = plan.get("gw", 1)
    client = FPLClient()
    tok = refresh_access_token()
    client.reload()
    sess = client.s
    if tok and tok.get("access_token"):
        sess.headers["Authorization"] = f"Bearer {tok['access_token']}"
    hdrs = {"Content-Type": "application/json", "User-Agent": UA}
    live_errors = validate_live_squad(client.my_team(team_id), plan)
    if live_errors:
        raise InvalidPlanError(live_errors)

    print("POST /api/transfers/ ...")
    chip_code = plan.get("chip")
    import chips as chips_mod
    transfers_chip = chip_code if chip_code and chips_mod.chip_type(chip_code) == "transfer" else None
    if not plan["transfers"] and transfers_chip is None:
        print("  -> no transfers needed (squad already matches target), skipping")
        r = None
    else:
        r = sess.post("https://fantasy.premierleague.com/api/transfers/",
                      json={"transfers": plan["transfers"], "chip": transfers_chip, "event": gw, "entry": team_id},
                      headers=hdrs, timeout=60)
        print("  ->", r.status_code, "|", r.text[:400])
        if r.status_code >= 300:
            print("  !! transfers failed - aborting lineup update")
            return r, None, False

    picks = build_picks(plan)

    print("POST /api/my-team/ (lineup + captain) ...")
    team_chip = chip_code if chip_code and chips_mod.chip_type(chip_code) == "team" else None
    r2 = sess.post(f"https://fantasy.premierleague.com/api/my-team/{team_id}/",
                   json={"picks": picks, "chip": team_chip}, headers=hdrs, timeout=60)
    print("  ->", r2.status_code, "|", r2.text[:400])
    if r2.status_code >= 300:
        print("  !! lineup update failed")
        return r, r2, False

    print("VERIFY: polling my-team until async apply lands ...")
    matched, team = verify_squad_poll(client, team_id, plan)
    now_ids = sorted(p["element"] for p in team.get("picks", [])) if team else []
    print("  squad size:", len(now_ids), "| matches target:", matched)
    # P0.1: return the reconciliation result so the caller can require
    # verified final state before declaring 'executed' (2xx/202 alone means
    # accepted, not proven applied). Merged from PR #2 (VerificationFailure
    # approach) into the 3-tuple contract the bot/tests rely on.
    return r, r2, matched


def verify_squad_poll(client, team_id, plan, attempts=6, delay=5.0):
    """Poll my-team until the async 202 apply lands or attempts run out.

    Returns (matched, team). FPL returns 202 Accepted for lineup/transfer
    POSTs (async apply at the deadline) — a single immediate re-read can
    race the apply and report a false mismatch. Max wait: attempts * delay.
    """
    # P0.1 (7 Aug audit): the target MUST be a single GLOBAL sort of
    # starters+bench. The old expression sorted(starters) + sorted(bench)
    # produced a different ordering than the live ids (sorted(all picks))
    # whenever a bench id sorted inside the starters, so a correct final
    # state could never match and reconciliation always failed (or worse,
    # its result was ignored). PR #2 reached the same fix via an outer
    # sorted() - equivalent result.
    starters, bench = plan.get("target_starters", []), plan.get("bench", [])
    full_reconciliation = (len(starters) == 11 and len(bench) == 4 and
                           (plan.get("captain") or {}).get("id") is not None and
                           (plan.get("vice") or {}).get("id") is not None)
    expected_picks = build_picks(plan) if full_reconciliation else None
    expected_ids = sorted(p.get("id") for p in starters + bench)
    matched = False
    team = None
    for attempt in range(1, attempts + 1):
        try:
            team = client.my_team(team_id)
        except Exception as e:
            print(f"  poll {attempt}/{attempts}: read failed ({repr(e)[:80]}), retrying ...")
        else:
            now_picks = team.get("picks", [])
            reconciled = (lineup_matches(now_picks, expected_picks) if full_reconciliation else
                          sorted(p.get("element") for p in now_picks) == expected_ids)
            if reconciled:
                matched = True
                print(f"  poll {attempt}/{attempts}: full lineup matches target ({len(now_picks)} players)")
                break
            print(f"  poll {attempt}/{attempts}: full lineup not yet matched "
                  f"(size {len(now_picks)}), waiting {delay:.0f}s ...")
        if attempt < attempts:
            time.sleep(delay)
    return matched, team


POS_RANK = {"GKP": 0, "DEF": 1, "MID": 2, "FWD": 3}


def lineup_matches(actual, expected):
    if len(actual) != len(expected):
        return False
    actual_by_slot = {p.get("position"): p for p in actual}
    if len(actual_by_slot) != len(expected):
        return False
    for wanted in expected:
        got = actual_by_slot.get(wanted["position"])
        if not got or any(got.get(key) != wanted[key] for key in
                          ("element", "position", "multiplier", "is_captain", "is_vice_captain")):
            return False
    return True


def is_success(resp):
    """True if a response is None (no-op) or a 2xx status."""
    if resp is None:
        return True
    return 200 <= resp.status_code < 300


def build_picks(plan):
    """Build the /api/my-team/ picks payload from a plan."""
    cap_id = plan["captain"]["id"]
    vice_id = plan["vice"]["id"]
    starters_sorted = sorted(plan["target_starters"], key=lambda p: POS_RANK[p["position"]])
    picks = []
    pos = 1
    for p in starters_sorted:
        picks.append({"element": p["id"], "position": pos,
                      "multiplier": 2 if p["id"] == cap_id else 1,
                      "is_captain": p["id"] == cap_id,
                      "is_vice_captain": p["id"] == vice_id})
        pos += 1
    # The optimizer already ranks the three outfield substitutes by expected
    # autosub value. Only move the reserve goalkeeper into the required first
    # bench slot; preserve the outfield order exactly as planned.
    bench = list(plan.get("bench", []))
    reserve_gk = [p for p in bench if p.get("position") == "GKP"]
    outfield = [p for p in bench if p.get("position") != "GKP"]
    bench_sorted = reserve_gk + outfield
    for b in bench_sorted:
        picks.append({"element": b["id"], "position": pos, "multiplier": 0,
                      "is_captain": False, "is_vice_captain": False})
        pos += 1
    return picks


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--plan"
    plan, els = build_plan()
    if mode == "--plan":
        print_plan(plan, els)
    elif mode == "--execute":
        print_plan(plan, els)
        v = plan["validation"]
        if not all([v["budget_ok"], v["club_ok"], v["quota_ok"]]):
            print("\nABORT: validation failed")
            sys.exit(1)
        print("\nEXECUTING...")
        execute_plan(plan)


if __name__ == "__main__":
    main()
