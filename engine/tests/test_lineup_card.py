"""/lineup advisory card: valid formation solve + optimal/suboptimal rendering."""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "bot"))

import telegram_bot


def _squad(xps):
    """xps: 15 floats -> 2 GKP, 5 DEF, 5 MID, 3 FWD."""
    pos = ["GKP"] * 2 + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 3
    return [{"id": i + 1, "name": f"P{i + 1}", "pos": pos[i], "xp": xps[i]} for i in range(15)]


def test_best_xi_returns_a_legal_formation():
    squad = _squad([5, 1, 6, 6, 5, 5, 2, 7, 7, 6, 6, 1, 8, 5, 2])
    starters, bench, formation = telegram_bot._best_xi(squad)
    assert len(starters) == 11 and len(bench) == 4
    counts = {p: sum(1 for s in starters if s["pos"] == p) for p in ("GKP", "DEF", "MID", "FWD")}
    assert counts["GKP"] == 1
    assert 3 <= counts["DEF"] <= 5 and 2 <= counts["MID"] <= 5 and 1 <= counts["FWD"] <= 3
    assert counts["DEF"] + counts["MID"] + counts["FWD"] == 10
    assert formation == f"{counts['DEF']}-{counts['MID']}-{counts['FWD']}"
    assert bench[0]["pos"] == "GKP"                       # reserve GK first
    assert [s["pos"] for s in starters] == sorted(
        (s["pos"] for s in starters), key=["GKP", "DEF", "MID", "FWD"].index)


def test_best_xi_starts_the_stronger_keeper_and_benches_weakest():
    squad = _squad([2.0, 6.0, 5, 5, 5, 5, 1, 6, 6, 6, 1, 1, 6, 6, 1])
    starters, bench, _ = telegram_bot._best_xi(squad)
    start_ids = {s["id"] for s in starters}
    assert 2 in start_ids and 1 not in start_ids               # GK with xp 6.0 starts
    assert bench[0]["id"] == 1


class _FakeClient:
    def __init__(self, picks):
        self._picks = picks

    def my_team(self, _tid):
        return {"picks": self._picks}


def _wire(monkeypatch, xp_by_id, starter_ids, captain_id):
    etype = {1: 1, 2: 1, 3: 2, 4: 2, 5: 2, 6: 2, 7: 2, 8: 3, 9: 3, 10: 3, 11: 3, 12: 3,
             13: 4, 14: 4, 15: 4}
    els = [{"id": i, "element_type": etype[i], "team": 1, "web_name": f"P{i}"} for i in etype]
    bootstrap = {"events": [{"id": 3, "finished": False}], "elements": els,
                 "teams": [{"id": 1, "short_name": "ARS"}]}

    def fake_fetch(url):
        if "bootstrap" in url:
            return bootstrap
        if "fixtures" in url:
            return []
        raise AssertionError(url)

    picks = [{"element": i, "multiplier": (2 if i == captain_id else (1 if i in starter_ids else 0)),
              "is_captain": i == captain_id, "is_vice_captain": False} for i in etype]
    monkeypatch.setattr(telegram_bot, "fetch", fake_fetch)
    monkeypatch.setattr(telegram_bot, "load_settings", lambda: {"team_id": 1})
    monkeypatch.setattr(telegram_bot, "FPLClient", lambda: _FakeClient(picks))
    monkeypatch.setattr(telegram_bot, "preseason_xpts", lambda e, f: xp_by_id[e["id"]])
    monkeypatch.setattr(telegram_bot, "inseason_xpts_from_bootstrap", lambda e, f, g: xp_by_id[e["id"]])


def test_lineup_card_flags_an_improvement(monkeypatch):
    # bench players 12 & 15 are strong; current XI benches them -> suggest change
    xp = {1: 5, 2: 2, 3: 6, 4: 6, 5: 6, 6: 6, 7: 1, 8: 7, 9: 6, 10: 6, 11: 6, 12: 8,
          13: 5, 14: 5, 15: 8}
    _wire(monkeypatch, xp, starter_ids={1, 3, 4, 5, 6, 8, 9, 10, 11, 13, 14}, captain_id=13)
    card = telegram_bot.lineup_text()
    assert "LINEUP — GW3" in card and "<pre>" in card
    assert "Captain:" in card and "Bench:" in card
    assert "Δ XI projection" in card and "▲" in card
    assert len(card) <= 4096


def test_lineup_card_confirms_when_already_optimal(monkeypatch):
    xp = {1: 6, 2: 1, 3: 6, 4: 6, 5: 6, 6: 5, 7: 1, 8: 7, 9: 6, 10: 6, 11: 6, 12: 1,
          13: 6, 14: 5, 15: 1}
    _wire(monkeypatch, xp, starter_ids={1, 3, 4, 5, 6, 8, 9, 10, 11, 13, 14}, captain_id=8)
    card = telegram_bot.lineup_text()
    assert "already match the projection" in card


# --- one-tap apply -----------------------------------------------------------

def _pk(el, slot, mult, c=False, v=False):
    return {"element": el, "position": slot, "multiplier": mult,
            "is_captain": c, "is_vice_captain": v}


def test_lineup_picks_payload_shape():
    starters = [{"id": i, "pos": p, "xp": 10 - i}
                for i, p in enumerate(["GKP", "DEF", "DEF", "DEF", "MID", "MID",
                                       "MID", "MID", "FWD", "FWD", "FWD"], 1)]
    bench = [{"id": 12, "pos": "GKP", "xp": 1}, {"id": 13, "pos": "DEF", "xp": 4},
             {"id": 14, "pos": "MID", "xp": 3}, {"id": 15, "pos": "FWD", "xp": 2}]
    picks = telegram_bot._lineup_picks_payload(starters, bench, cap_id=9, vice_id=5)
    assert len(picks) == 15
    assert [p["position"] for p in picks] == list(range(1, 16))
    assert picks[8]["element"] == 9 and picks[8]["multiplier"] == 2 and picks[8]["is_captain"]
    assert sum(p["is_captain"] for p in picks) == 1
    assert sum(p["is_vice_captain"] for p in picks) == 1
    assert picks[11]["element"] == 12 and picks[11]["multiplier"] == 0   # reserve GK slot 12
    assert all(p["multiplier"] == 0 for p in picks[11:])


def test_lineup_hash_is_order_independent():
    a = [_pk(1, 1, 1), _pk(2, 2, 2, c=True)]
    b = [_pk(2, 2, 2, c=True), _pk(1, 1, 1)]
    assert telegram_bot._lineup_hash(a) == telegram_bot._lineup_hash(b)
    assert telegram_bot._lineup_hash(a) != telegram_bot._lineup_hash([_pk(1, 1, 2), _pk(2, 2, 1)])


class _ApplyClient:
    def __init__(self):
        self.posted = None

    def set_lineup(self, team_id, picks, chip=None):
        self.posted = (team_id, picks, chip)
        raise AssertionError("dry run must not POST")

    def my_team(self, _tid):
        return {"picks": []}


def _pending_lineup(tmp_path, monkeypatch, *, optimal=False, age_s=10):
    import datetime as _dt
    ts = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=age_s)).isoformat()
    picks = telegram_bot._lineup_picks_payload(
        [{"id": i, "pos": p, "xp": 9 - i} for i, p in enumerate(
            ["GKP", "DEF", "DEF", "DEF", "MID", "MID", "MID", "MID", "FWD", "FWD", "FWD"], 1)],
        [{"id": 12, "pos": "GKP", "xp": 1}, {"id": 13, "pos": "DEF", "xp": 2},
         {"id": 14, "pos": "MID", "xp": 2}, {"id": 15, "pos": "FWD", "xp": 2}],
        cap_id=9, vice_id=5)
    plan = {"team_id": 1, "gw": 3, "picks": picks, "captain": {"id": 9, "name": "Cap"},
            "vice": {"id": 5, "name": "Vc"}, "generated_at": ts, "optimal": optimal}
    plan["lineup_id"] = telegram_bot._lineup_hash(picks)
    f = tmp_path / "pending_lineup.json"
    f.write_text(__import__("json").dumps(plan))
    monkeypatch.setattr(telegram_bot, "PENDING_LINEUP_FILE", str(f))
    return plan


def test_apply_lineup_dry_run_does_not_post(monkeypatch, tmp_path):
    plan = _pending_lineup(tmp_path, monkeypatch)
    monkeypatch.setattr(telegram_bot, "authorized", lambda uid: True)
    monkeypatch.setattr(telegram_bot, "execution_enabled", lambda: True)
    monkeypatch.setenv("FPL_TELEGRAM_DRY_RUN", "1")
    monkeypatch.setattr(telegram_bot, "load_pending", lambda: {})
    monkeypatch.setattr(telegram_bot, "next_deadline_info", lambda: (3, None, None))
    monkeypatch.setattr(telegram_bot, "FPLClient", _ApplyClient)
    out = telegram_bot.apply_lineup(uid=1, token=telegram_bot.short_id(plan["lineup_id"]))
    assert "DRY RUN" in out


def test_apply_lineup_refuses_stale_token(monkeypatch, tmp_path):
    _pending_lineup(tmp_path, monkeypatch)
    monkeypatch.setattr(telegram_bot, "authorized", lambda uid: True)
    monkeypatch.setattr(telegram_bot, "execution_enabled", lambda: True)
    assert "stale" in telegram_bot.apply_lineup(uid=1, token="deadbeef").lower()


def test_apply_lineup_refuses_when_chip_plan_pending(monkeypatch, tmp_path):
    plan = _pending_lineup(tmp_path, monkeypatch)
    monkeypatch.setattr(telegram_bot, "authorized", lambda uid: True)
    monkeypatch.setattr(telegram_bot, "execution_enabled", lambda: True)
    monkeypatch.setattr(telegram_bot, "load_pending",
                        lambda: {"status": "pending", "chip": "wildcard"})
    out = telegram_bot.apply_lineup(uid=1, token=telegram_bot.short_id(plan["lineup_id"]))
    assert "wildcard" in out.lower() or "chip" in out.lower()


def test_lineup_apply_confirmation_gates(monkeypatch, tmp_path):
    plan = _pending_lineup(tmp_path, monkeypatch)
    monkeypatch.setattr(telegram_bot, "execution_enabled", lambda: True)
    monkeypatch.setattr(telegram_bot, "load_pending", lambda: {})
    monkeypatch.setattr(telegram_bot, "authorized", lambda uid: False)
    assert telegram_bot.lineup_apply_confirmation(1)[0] is None
    monkeypatch.setattr(telegram_bot, "authorized", lambda uid: True)
    assert telegram_bot.lineup_apply_confirmation(1)[0] == telegram_bot.short_id(plan["lineup_id"])
