"""price_watch: silent baseline, then speaks only about owned/target moves."""
import json

import price_watch


def _bootstrap(prices):
    return {
        "events": [{"id": 3, "is_current": True, "finished": False}],
        "teams": [{"id": 1, "short_name": "ARS"}, {"id": 2, "short_name": "LIV"}],
        "elements": [
            {"id": eid, "web_name": f"P{eid}", "first_name": "F", "second_name": f"L{eid}",
             "team": 1 if eid % 2 else 2, "now_cost": cost,
             "transfers_in_event": tin, "transfers_out_event": tout,
             "selected_by_percent": "10.0"}
            for eid, (cost, tin, tout) in prices.items()
        ],
    }


class FakeClient:
    def __init__(self, prices, owned):
        self._prices = prices
        self._owned = owned

    def get_json(self, path):
        if path == "bootstrap-static/":
            return _bootstrap(self._prices)
        if path.startswith("entry/") and path.endswith("/picks/"):
            return {"picks": [{"element": e} for e in self._owned]}
        raise AssertionError(path)


def _wire(monkeypatch, tmp_path, prices, owned):
    monkeypatch.setattr(price_watch, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(price_watch, "LEAGUE_STATE_FILE", str(tmp_path / "league.json"))
    monkeypatch.setattr(price_watch, "FPLClient", lambda: FakeClient(prices, owned))


def test_first_run_is_silent_and_writes_baseline(monkeypatch, tmp_path):
    prices = {10: (55, 0, 0), 11: (75, 0, 0), 12: (50, 0, 0)}
    _wire(monkeypatch, tmp_path, prices, owned=[10, 11])
    assert price_watch.build_report(price_watch.FPLClient()) == ""
    saved = json.loads((tmp_path / "state.json").read_text())
    assert saved["prices"]["10"] == 55


def test_owned_price_fall_is_reported(monkeypatch, tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"prices": {"10": 55, "11": 75, "12": 50}}))
    prices = {10: (54, 0, 0), 11: (75, 0, 0), 12: (50, 0, 0)}
    _wire(monkeypatch, tmp_path, prices, owned=[10, 11])
    report = price_watch.build_report(price_watch.FPLClient())
    assert "Your squad" in report and "P10" in report and "🔻" in report
    assert "-0.1" in report


def test_unowned_only_change_stays_brief_and_quiet_when_nothing_moves(monkeypatch, tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"prices": {"10": 55, "11": 75, "12": 50}}))
    prices = {10: (55, 0, 0), 11: (75, 0, 0), 12: (50, 0, 0)}
    _wire(monkeypatch, tmp_path, prices, owned=[10, 11])
    assert price_watch.build_report(price_watch.FPLClient()) == ""


def test_momentum_on_owned_player_warns_before_lock(monkeypatch, tmp_path):
    (tmp_path / "state.json").write_text(json.dumps({"prices": {"10": 55, "11": 75, "12": 50}}))
    prices = {10: (55, 60000, 1000), 11: (75, 0, 0), 12: (50, 0, 0)}
    _wire(monkeypatch, tmp_path, prices, owned=[10, 11])
    report = price_watch.build_report(price_watch.FPLClient())
    assert "Near a change" in report and "P10" in report and "rising" in report
