"""/live in-gameweek tracker."""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "bot"))

import telegram_bot

# elements 1-11 = XI (1 GK / 4 DEF / 4 MID / 2 FWD), 12-15 = bench
_TYPES = {1: 1, **{i: 2 for i in range(2, 6)}, **{i: 3 for i in range(6, 10)},
          10: 4, 11: 4, 12: 1, 13: 2, 14: 3, 15: 4}
_TEAM = {i: (1 if i % 2 else 2) for i in range(1, 16)}


def _bootstrap(current=True, avg=40):
    ev = {"id": 3, "is_current": current, "is_next": not current,
          "average_entry_score": avg, "deadline_time": "2026-09-04T17:30:00Z"}
    return {"events": [ev],
            "elements": [{"id": i, "web_name": f"P{i}", "team": _TEAM[i],
                          "element_type": _TYPES[i]} for i in range(1, 16)]}


def _live(minutes, pts=None, bps=None):
    pts = pts or {}
    bps = bps or {}
    return {"elements": [{"id": i, "stats": {
        "minutes": minutes.get(i, 90), "total_points": pts.get(i, 2),
        "bps": bps.get(i, 0)}} for i in range(1, 16)]}


def _picks(active_chip=None, captain=6):
    picks = []
    for i in range(1, 16):
        picks.append({"element": i, "position": i,
                      "multiplier": (2 if i == captain else (1 if i <= 11 else 0)),
                      "is_captain": i == captain, "is_vice_captain": i == 7})
    return {"picks": picks, "entry_history": {"overall_rank": 1100000},
            "active_chip": active_chip}


class _Client:
    def __init__(self, picks):
        self._picks = picks

    def get_json(self, path):
        assert "picks" in path
        return self._picks


def _wire(monkeypatch, bootstrap, live, fixtures, picks):
    def fake_fetch(url):
        if "bootstrap" in url:
            return bootstrap
        if "/live/" in url:
            return live
        if "fixtures" in url:
            return fixtures
        raise AssertionError(url)
    monkeypatch.setattr(telegram_bot, "fetch", fake_fetch)
    monkeypatch.setattr(telegram_bot, "load_settings", lambda: {"team_id": 1})
    monkeypatch.setattr(telegram_bot, "FPLClient", lambda: _Client(picks))


def test_no_live_gameweek(monkeypatch):
    _wire(monkeypatch, _bootstrap(current=False), _live({}), [], _picks())
    assert "No gameweek is live" in telegram_bot.live_text()


def test_mid_gameweek_points_and_yet_to_play(monkeypatch):
    # team 1 fixture live, team 2 upcoming; odd ids (team 1) played, even not
    fixtures = [{"team_h": 1, "team_a": 9, "started": True, "finished": False},
                {"team_h": 2, "team_a": 8, "started": False, "finished": False}]
    minutes = {i: (60 if _TEAM[i] == 1 else 0) for i in range(1, 16)}
    live = _live(minutes, pts={6: 5}, bps={2: 25, 6: 20})
    _wire(monkeypatch, _bootstrap(avg=30), live, fixtures, _picks(captain=6))
    card = telegram_bot.live_text()
    assert "LIVE — GW3" in card
    assert "(C) P6" in card and "5 → <b>10</b>" in card         # captain doubled
    assert "Yet to play" in card                               # team-2 XI players
    assert "avg 30" in card
    assert "Your BPS: P2 25" in card and "provisional" in card
    assert len(card) <= 4096


def test_bench_boost_counts_bench(monkeypatch):
    fixtures = [{"team_h": 1, "team_a": 2, "started": True, "finished": False}]
    _wire(monkeypatch, _bootstrap(), _live({i: 90 for i in range(1, 16)}, pts={12: 3, 13: 4}),
          fixtures, _picks(active_chip="bboost"))
    card = telegram_bot.live_text()
    assert "bboost" in card and "boosted, counting" in card


def test_autosub_when_starter_blank_and_fixture_done(monkeypatch):
    # element 2 (DEF, team 2) played 0 and its fixture is finished; bench DEF 13 played
    fixtures = [{"team_h": 1, "team_a": 3, "started": True, "finished": True},
                {"team_h": 2, "team_a": 4, "started": True, "finished": True}]
    minutes = {i: 90 for i in range(1, 16)}
    minutes[2] = 0
    live = _live(minutes, pts={2: 0, 13: 7})
    _wire(monkeypatch, _bootstrap(), live, fixtures, _picks(captain=6))
    card = telegram_bot.live_text()
    assert "Autosubs:" in card and "P13 ↑ for P2" in card
