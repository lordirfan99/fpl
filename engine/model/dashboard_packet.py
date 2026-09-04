"""Allowlisted, read-only projection of a canonical plan. Never an approval payload."""
import hashlib
import json
import os
from pathlib import Path


def select(row, fields):
    return {key: row.get(key) for key in fields.split()}


def account_state(team):
    picks = team.get("picks") or []
    transfers = team.get("transfers") or {}
    if len(picks) != 15 or len({p.get("element") for p in picks}) != 15:
        raise ValueError("Incomplete account squad")
    if transfers.get("bank") is None or any(p.get("selling_price") is None for p in picks):
        raise ValueError("Account prices unavailable")
    if transfers.get("limit") is None and transfers.get("unlimited") is not True:
        raise ValueError("Free transfers unavailable")
    return {
        "picks": sorted([select(p, "element position selling_price purchase_price is_captain is_vice_captain")
                         for p in picks], key=lambda p: p["element"]),
        "transfers": select(transfers, "bank limit made cost status unlimited"),
        "chips": sorted([select(c, "name status_for_entry played_by_entry") for c in team.get("chips", [])],
                        key=lambda c: c.get("name") or ""),
    }


def account_fingerprint(team):
    return hashlib.sha256(json.dumps(account_state(team), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def make_packet(plan, team, players, bootstrap, fixtures):
    """Build a separate display artifact without modifying plan or plan_id."""
    summary = plan.get("decision_summary") or {}
    manifest = summary.get("source_manifest") or {}
    if not plan.get("plan_id") or manifest.get("status") != "ready":
        raise ValueError("Verified canonical plan unavailable")
    elements = {p["id"]: p for p in bootstrap["elements"]}
    ids = {p["element"] for p in team["picks"]} | {p["id"] for p in plan["target_starters"] + plan["bench"]}
    rows = []
    for p in players:
        if p["id"] not in ids:
            continue
        row = select(p, "id name position club cost xpts xpts_by_gw expected_minutes")
        row["facts"] = select(elements[p["id"]], "minutes starts expected_goals expected_assists expected_goals_conceded defensive_contribution saves status news news_added chance_of_playing_next_round team")
        rows.append(row)
    gw = plan["gw"]
    return {
        "schema_version": 1, "team_id": plan["team_id"], "plan_id": plan["plan_id"],
        "gameweek": gw, "deadline": plan["deadline"], "generated_at": plan["generated_at"],
        "account_fingerprint": account_fingerprint(team), "account": account_state(team),
        "timestamps": {"account": (manifest.get("account") or {}).get("fetched_at"),
                       "reference": (manifest.get("official_fpl") or {}).get("fetched_at"),
                       "league": (manifest.get("league") or {}).get("snapshot_at")},
        "model_version": plan.get("projection_version"), "chip": plan.get("chip"),
        "bank_after": plan.get("bank_after"), "free_transfers_before": plan.get("free_transfers_before"),
        "free_transfers_after": plan.get("free_transfers_after"),
        "starters": [p["id"] for p in plan["target_starters"]], "bench": [p["id"] for p in plan["bench"]],
        "captain": plan["captain"]["id"], "vice": plan["vice"]["id"],
        "transfers": [select(t, "element_out element_in out_name in_name hit gain package_gain") for t in plan["transfers"]],
        "action": summary.get("recommended_action"), "reason": summary.get("reason"),
        "horizon": {"metric": "risk-adjusted utility", "rows": [select(r, "gw weight current proposed gain")
                    for r in (summary.get("horizon") or {}).get("rows", [])]},
        "alternatives": {key: None if not value else {
            **select(value, "horizon_gain net_after_hit projection_starts_gw"),
            "moves": [select(m, "out in hit") for m in value.get("moves", [])],
        } for key, value in (summary.get("alternatives") or {}).items()
                         if key in {"hold", "next_free_transfer", "two_free_transfers", "best_paid_transfer"}},
        "captains": [select(c, "id name xpts expected_minutes eligible selected reason")
                     for c in summary.get("captain_rankings", [])[:3]],
        "players": rows,
        "fixtures": [select(f, "id event team_h team_a team_h_difficulty team_a_difficulty kickoff_time")
                     for f in fixtures if f.get("event") in range(gw, min(39, gw + 3))],
        "teams": [select(t, "id short_name") for t in bootstrap.get("teams", [])],
        "writes_enabled": False,
    }


def private_bucket(base):
    config = Path(base) / "config" / "dashboard.json"
    return json.loads(config.read_text()).get("private_bucket") if config.exists() else None


def publish(base, name, payload):
    bucket_name = private_bucket(base)
    if not bucket_name:
        return False
    if bucket_name == os.getenv("FPL_SNAPSHOT_BUCKET"):
        raise ValueError("Private data cannot use the public snapshot bucket")
    from google.cloud import storage
    bucket = storage.Client().bucket(bucket_name)
    bucket.reload(timeout=15)
    if bucket.iam_configuration.public_access_prevention != "enforced":
        raise ValueError("Private bucket must enforce public access prevention")
    blob = bucket.blob(f"dashboard/{name}.json")
    blob.cache_control = "private, no-store"
    blob.upload_from_string(json.dumps(payload, allow_nan=False), content_type="application/json", timeout=20)
    return True


def export_plan(base, plan, team, players, bootstrap, fixtures):
    if not private_bucket(base):
        return
    packet = make_packet(plan, team, players, bootstrap, fixtures)
    # No raw plan is copied into the public snapshots or the public journal.
    publish(base, "plan", packet)
