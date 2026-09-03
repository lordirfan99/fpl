"""squad_watch: hourly, de-duplicated availability alerts for owned players."""
import json

import squad_watch


def _bootstrap(elements):
    return {
        "events": [{"id": 1, "finished": True}, {"id": 2, "finished": True},
                   {"id": 3, "finished": False}],
        "teams": [{"id": 1, "short_name": "ARS"}],
        "elements": elements,
    }


def _el(eid, status="a", cop=None, news=""):
    return {"id": eid, "web_name": f"P{eid}", "team": 1,
            "status": status, "chance_of_playing_next_round": cop, "news": news}


class FakeClient:
    def __init__(self, elements, picks):
        self._els = elements
        self._picks = picks

    def get_json(self, path):
        if path == "bootstrap-static/":
            return _bootstrap(self._els)
        if path.endswith("/picks/"):
            return {"picks": self._picks}
        raise AssertionError(path)

    def my_team(self, _tid):
        return {"picks": self._picks}


def _wire(monkeypatch, tmp_path, elements, picks):
    monkeypatch.setattr(squad_watch, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(squad_watch, "PLAN_FILE", str(tmp_path / "plan.json"))
    monkeypatch.setattr("fpl_client.FPLClient", lambda: FakeClient(elements, picks))


PICKS = [
    {"element": 1, "position": 1, "multiplier": 1, "is_captain": True, "is_vice_captain": False},
    {"element": 2, "position": 2, "multiplier": 1, "is_captain": False, "is_vice_captain": False},
    {"element": 3, "position": 13, "multiplier": 0, "is_captain": False, "is_vice_captain": False},
]


def test_flags_an_injured_owned_player_then_goes_silent(monkeypatch, tmp_path, capsys):
    els = [_el(1, status="i", news="Hamstring"), _el(2), _el(3)]
    _wire(monkeypatch, tmp_path, els, PICKS)

    assert squad_watch.main() == 1
    out = capsys.readouterr().out
    assert "OUT / suspended" in out and "P1 (ARS) (C)" in out and "Hamstring" in out

    # nothing changed -> silent
    assert squad_watch.main() == 0
    assert capsys.readouterr().out == ""


def test_news_change_re_alerts(monkeypatch, tmp_path, capsys):
    _wire(monkeypatch, tmp_path, [_el(1, "d", 50, "Knock"), _el(2), _el(3)], PICKS)
    assert squad_watch.main() == 1
    capsys.readouterr()
    # same player, worse update
    _wire(monkeypatch, tmp_path, [_el(1, "i", 0, "Out 3 weeks"), _el(2), _el(3)], PICKS)
    assert squad_watch.main() == 1
    assert "Out 3 weeks" in capsys.readouterr().out


def test_recovered_player_reports_cleared_once(monkeypatch, tmp_path, capsys):
    _wire(monkeypatch, tmp_path, [_el(1, "i", 0, "Injured"), _el(2), _el(3)], PICKS)
    squad_watch.main()
    capsys.readouterr()
    _wire(monkeypatch, tmp_path, [_el(1), _el(2), _el(3)], PICKS)
    assert squad_watch.main() == 1
    assert "Cleared" in capsys.readouterr().out
    assert squad_watch.main() == 0  # not again


def test_watches_incoming_plan_targets(monkeypatch, tmp_path, capsys):
    els = [_el(1), _el(2), _el(3), _el(9, status="i", news="Doubt")]
    _wire(monkeypatch, tmp_path, els, PICKS)
    (tmp_path / "plan.json").write_text(json.dumps({
        "status": "pending", "transfers": [{"element_in": 9}], "target_starters": []}))
    assert squad_watch.main() == 1
    assert "P9 (ARS) → incoming" in capsys.readouterr().out
