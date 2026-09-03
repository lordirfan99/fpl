"""/compare head-to-head player card."""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "bot"))

import telegram_bot


def _el(eid, web, team, etype, tp=0, first="", second="", **extra):
    row = {"id": eid, "web_name": web, "team": team, "element_type": etype,
           "total_points": tp, "first_name": first, "second_name": second or web,
           "now_cost": 75, "selected_by_percent": "10.0", "form": "5.0",
           "points_per_game": "5.0", "status": "a",
           "chance_of_playing_next_round": None,
           "expected_goal_involvements_per_90": "0.5",
           "penalties_order": None, "corners_and_indirect_freekicks_order": None,
           "direct_freekicks_order": None}
    row.update(extra)
    return row


ELS = [
    _el(1, "Salah", 10, 3, tp=30, penalties_order=1),
    _el(2, "Palmer", 5, 3, tp=20, penalties_order=1),
    _el(3, "M.Salah", 11, 4, tp=2),       # weaker "salah" substring match
    _el(4, "Haaland", 4, 4, tp=25),
]
TEAMS = [{"id": t, "short_name": s} for t, s in
         [(10, "LIV"), (5, "CHE"), (11, "IPS"), (4, "MCI")]]
BOOT = {"elements": ELS, "teams": TEAMS, "events": [{"id": 3, "finished": False}]}
FIX = [{"event": 3, "team_h": 10, "team_a": 5, "team_h_difficulty": 3, "team_a_difficulty": 4}]


def _wire(monkeypatch):
    monkeypatch.setattr(telegram_bot, "fetch",
                        lambda url: BOOT if "bootstrap" in url else FIX)


def test_resolve_exact_and_missing():
    assert telegram_bot._resolve_player("salah", ELS)["id"] == 1        # exact web_name
    assert telegram_bot._resolve_player("haaland", ELS)["id"] == 4
    assert telegram_bot._resolve_player("nobody-here", ELS) is None


def test_resolve_prefers_more_prominent_on_substring_tie():
    # "sal" is a substring of both "Salah" and "M.Salah"; pick the higher scorer
    assert telegram_bot._resolve_player("sal", ELS)["id"] == 1


def test_compare_card_renders_both_players(monkeypatch):
    _wire(monkeypatch)
    card = telegram_bot.compare_text("salah vs palmer")
    assert "COMPARE" in card and "<pre>" in card
    assert "Salah" in card and "Palmer" in card
    assert "LIV" in card and "CHE" in card
    assert len(card) <= 4096


def test_compare_usage_and_not_found(monkeypatch):
    _wire(monkeypatch)
    assert "Usage" in telegram_bot.compare_text("only-one")
    assert "Couldn't find" in telegram_bot.compare_text("salah vs zzzznope")


def test_compare_accepts_comma_and_v_separators(monkeypatch):
    _wire(monkeypatch)
    assert "<pre>" in telegram_bot.compare_text("haaland, palmer")
    assert "<pre>" in telegram_bot.compare_text("haaland v palmer")
