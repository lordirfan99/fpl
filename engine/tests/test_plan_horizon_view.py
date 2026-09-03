"""/plan — render the horizon MILP's 3-GW forward plan."""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "bot"))

import telegram_bot


def _wire(monkeypatch, plan):
    monkeypatch.setattr(telegram_bot, "load_pending", lambda: plan)
    monkeypatch.setattr(telegram_bot, "fetch", lambda url: {"elements": [
        {"id": 1, "web_name": "Haaland"}, {"id": 2, "web_name": "Salah"}]})
    monkeypatch.setattr(telegram_bot, "next_gw_id", lambda: 3)


def _week(offset, moves, captain=1, hits=0, ftb=1, fta=1, bank=5, pts=61.2):
    return {"gw_offset": offset, "formation": "3-4-3", "transfers": moves,
            "transfer_count": len(moves), "hits": hits, "captain_id": captain,
            "free_transfers_before": ftb, "free_transfers_after": fta,
            "bank_after": bank, "robust_points_with_captain": pts,
            "mean_points_with_captain": pts + 3}


def test_renders_three_weeks_with_moves_and_holds(monkeypatch):
    plan = {"gw": 3, "horizon_plan": {"objective": 182.4, "weeks": [
        _week(0, [{"out_name": "OldMid", "in_name": "Salah", "out_pos": "MID"}]),
        _week(1, [], hits=0, ftb=1, fta=2),
        _week(2, [{"out_name": "OldFwd", "in_name": "Haaland"}], hits=1, ftb=1, fta=1),
    ]}}
    _wire(monkeypatch, plan)
    card = telegram_bot.plan_horizon_text()
    assert "3-GW PLAN" in card and "from GW3" in card
    assert "GW3" in card and "GW4" in card and "GW5" in card
    assert "OldMid → Salah" in card
    assert "hold — no transfer" in card
    assert "−4" in card                       # GW5 hit
    assert "(C) Haaland" in card
    assert len(card) <= 4096


def test_empty_weeks_explains_why(monkeypatch):
    _wire(monkeypatch, {"gw": 3, "chip": "wildcard",
                        "horizon_plan": {"objective": 105, "weeks": []}})
    out = telegram_bot.plan_horizon_text()
    assert "No multi-week plan" in out and "wildcard" in out


def test_no_plan_at_all(monkeypatch):
    _wire(monkeypatch, {})
    assert "No multi-week plan" in telegram_bot.plan_horizon_text()
