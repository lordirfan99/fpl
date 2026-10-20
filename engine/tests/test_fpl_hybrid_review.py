import json
from datetime import datetime, timezone, timedelta

import pytest

from automation.fpl_hybrid_review import (
    SidecarError,

    evaluate,
    event_key,
    monitor,
    record_review,
    score_lineup,
)


def picks():
    # 3-4-3, then bench GK/DEF/MID/FWD.  IDs are deliberately synthetic.
    rows = [(1, 1), (2, 2), (3, 2), (4, 2), (5, 3), (6, 3), (7, 3), (8, 3), (9, 4), (10, 4), (11, 4), (12, 1), (13, 2), (14, 3), (15, 4)]
    out = [{"element": i, "position": n} for n, (i, _element_type) in enumerate(rows, 1)]
    out[8]["is_captain"] = True
    out[9]["is_vice_captain"] = True
    return out


def snap():
    return {"gameweek": 8, "entry": 2797967, "plan_id": "p8", "source_kind": "pending", "plan": {"picks": picks()}, "account": {"picks": picks(), "bank": 1}}


def players(minutes=90):
    return {i: {"minutes": minutes, "points": i, "position": pos} for i, pos in [(1, 1), (2, 2), (3, 2), (4, 2), (5, 3), (6, 3), (7, 3), (8, 3), (9, 4), (10, 4), (11, 4), (12, 1), (13, 2), (14, 3), (15, 4)]}


def test_monitor_first_then_unchanged_and_deadline(tmp_path):
    first = monitor(tmp_path, snap())
    assert first["events"] == ["first_monitor"]
    second = monitor(tmp_path, snap())
    assert second["events"] == []
    deadline = datetime.now(timezone.utc) + timedelta(minutes=30)
    third = monitor(tmp_path, snap(), deadline=deadline)
    assert third["events"] == ["deadline_1h"]


def test_monitor_meaningful_changes(tmp_path):
    monitor(tmp_path, snap())
    changed = snap()
    changed["plan"]["picks"][0]["element"] = 15
    result = monitor(tmp_path, changed, public_hash="new")
    assert result["events"] == ["plan_changed", "public_data_changed"]


def test_event_key_ignores_capture_time():
    a, b = snap(), snap()
    a["captured_at"], b["captured_at"] = "old", "new"
    assert event_key(a) == event_key(b)


def test_record_is_bound_and_immutable(tmp_path):
    monitor(tmp_path, snap())
    current = json.loads((tmp_path / "last_monitor.json").read_text())
    review = {"event_key": current["event_key"], "snapshot_id": current["snapshot_id"], "verdict": "agree", "suggested_action": "retain", "checked_at": "2026-10-20T00:00:00Z", "uncertainty": 0.2, "affected_players": [{"id": 9, "assumption": "fit"}], "evidence": [{"url": "https://example.test/news", "published_at": None, "checked_at": "2026-10-20T00:00:00Z"}]}
    path = record_review(tmp_path, review)
    assert path.exists() and json.loads(path.read_text())["input_hash"]
    with pytest.raises(SidecarError, match="immutable"):
        record_review(tmp_path, {**review, "verdict": "disagree"})


def test_bad_review_rejected(tmp_path):
    monitor(tmp_path, snap())
    current = json.loads((tmp_path / "last_monitor.json").read_text())
    with pytest.raises(SidecarError):
        record_review(tmp_path, {"event_key": current["event_key"], "snapshot_id": current["snapshot_id"], "verdict": "agree", "suggested_action": "transfer", "uncertainty": 0, "evidence": [], "affected_players": []})


def test_captain_dnp_uses_vice_and_gk_sub():
    p = players()
    p[9]["minutes"] = 0  # captain DNP; vice is active
    p[1]["minutes"] = 0  # GK DNP; bench GK enters
    assert score_lineup({"picks": picks()}, p) == sum(range(1, 12)) - 9 + 10 + 12 * 2


def test_bench_order_and_formation():
    p = players()
    p[13]["minutes"] = 90
    p[14]["minutes"] = 90
    p[15]["minutes"] = 90
    picks_ = picks()
    p[2]["minutes"] = 0
    # first bench DEF is legal and must be used before later bench players.
    assert score_lineup({"picks": picks_}, p) == sum(range(1, 12)) - 2 + 13 + 9


def test_evaluate_requires_finished_checked_and_official_actual():
    base = {"status": "finished", "data_checked": True, "gameweek": 8, "players": {str(k): v for k, v in players().items()}, "model": {"picks": picks()}, "hybrid": {"picks": picks()}, "owner_actual": {"official": True, "net_points": 50, "source": "entry-history"}}
    result = evaluate(base)
    assert result["model"]["net_points"] == result["hybrid"]["net_points"]
    with pytest.raises(SidecarError):
        evaluate({**base, "status": "live"})
    with pytest.raises(SidecarError):
        evaluate({**base, "owner_actual": {"official": False}})


def test_evaluate_hits_and_no_future_stub():
    base = {"status": "finished", "data_checked": True, "gameweek": 8, "players": {str(k): v for k, v in players().items()}, "model": {"picks": picks(), "hit_cost": 4}, "hybrid": {"picks": picks(), "hit_cost": 0}, "owner_actual": {"official": True, "net_points": 50, "source": "history"}}
    result = evaluate(base)
    assert result["model"]["hit_cost"] == 4 and result["model"]["gross_points"] - result["model"]["hit_cost"] == result["model"]["net_points"]
