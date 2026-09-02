"""deadline_check: self-gating window + red-flag surfacing, fires once per GW."""
import datetime
import json

import deadline_check

_TYPE = {1: [1, 2], 2: [3, 4, 5, 6, 7], 3: [8, 9, 10, 11, 12], 4: [13, 14, 15]}
_START_SLOTS = {1, 3, 4, 5, 6, 8, 9, 10, 11, 13, 14}  # 1 GK, 4 DEF, 4 MID, 2 FWD


def _elements(overrides=None):
    overrides = overrides or {}
    out = []
    for etype, ids in _TYPE.items():
        for eid in ids:
            e = {"id": eid, "element_type": etype, "team": 1, "web_name": f"P{eid}",
                 "status": "a", "chance_of_playing_next_round": None, "news": "",
                 "ep_next": "3.0"}
            e.update(overrides.get(eid, {}))
            out.append(e)
    return out


def _bootstrap(mins_to_deadline, elements=None):
    dl = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=mins_to_deadline)
    return {
        "events": [{"id": 3, "finished": False, "deadline_time": dl.isoformat().replace("+00:00", "Z")}],
        "teams": [{"id": 1, "short_name": "ARS"}],
        "elements": elements or _elements(),
    }


class FakeClient:
    def __init__(self, bootstrap):
        self._bs = bootstrap
        slot = 0
        self._picks = []
        for etype, ids in _TYPE.items():
            for eid in ids:
                slot += 1
                starter = slot in _START_SLOTS
                self._picks.append({
                    "element": eid, "position": slot,
                    "multiplier": 2 if eid == 8 else (1 if starter else 0),
                    "is_captain": eid == 8, "is_vice_captain": eid == 9,
                })

    def get_json(self, path):
        assert path == "bootstrap-static/"
        return self._bs

    def my_team(self, _tid):
        return {"picks": self._picks, "transfers": {"status": "unlimited"}}


def _wire(monkeypatch, tmp_path, bootstrap):
    monkeypatch.setattr(deadline_check, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(deadline_check, "PLAN_FILE", str(tmp_path / "plan.json"))
    monkeypatch.setattr(deadline_check, "FPLClient", lambda: FakeClient(bootstrap))


def test_silent_outside_the_warning_window(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, _bootstrap(mins_to_deadline=300))
    assert deadline_check.build_report(deadline_check.FPLClient()) == (None, None)


def test_fires_inside_window_with_xi_and_countdown(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, _bootstrap(mins_to_deadline=100))
    report, event = deadline_check.build_report(deadline_check.FPLClient())
    assert event == 3
    assert "DEADLINE CHECK · GW3" in report
    assert "Starting XI:" in report and "Bench:" in report


def test_injured_starter_is_flagged(monkeypatch, tmp_path):
    bs = _bootstrap(mins_to_deadline=90, elements=_elements({8: {"status": "i", "news": "hamstring"}}))
    _wire(monkeypatch, tmp_path, bs)
    report, _ = deadline_check.build_report(deadline_check.FPLClient())
    assert "Red flags:" in report and "P8" in report


def test_does_not_fire_twice_for_the_same_gw(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, _bootstrap(mins_to_deadline=90))
    (tmp_path / "state.json").write_text(json.dumps({"fired_event": 3}))
    assert deadline_check.build_report(deadline_check.FPLClient()) == (None, None)
