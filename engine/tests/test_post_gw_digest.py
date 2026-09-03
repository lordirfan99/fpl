"""post_gw_digest: fires once per finalized GW with rank + league movement."""
import json

import post_gw_digest


class FakeClient:
    def __init__(self, events, history):
        self._events = events
        self._history = history

    def get_json(self, path):
        if path == "bootstrap-static/":
            return {"events": self._events}
        if path.endswith("/history/"):
            return self._history
        raise AssertionError(path)


def _events(current_gw, avg):
    return [
        {"id": g, "finished": g <= current_gw, "data_checked": g <= current_gw,
         "average_entry_score": avg if g == current_gw else 40}
        for g in (1, 2, 3)
    ]


def _history():
    return {"current": [
        {"event": 2, "points": 55, "total_points": 110, "overall_rank": 900000,
         "points_on_bench": 3, "event_transfers_cost": 0},
        {"event": 3, "points": 62, "total_points": 172, "overall_rank": 720000,
         "points_on_bench": 5, "event_transfers_cost": 4},
    ]}


def _wire(monkeypatch, tmp_path, events, history, league_state=None):
    monkeypatch.setattr(post_gw_digest, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(post_gw_digest, "LEAGUE_STATE", str(tmp_path / "league.json"))
    monkeypatch.setattr(post_gw_digest, "_settings", lambda: {"league_intelligence": {"league_ids": [58005]}})
    if league_state is not None:
        (tmp_path / "league.json").write_text(json.dumps(league_state))
    monkeypatch.setattr("fpl_client.FPLClient", lambda: FakeClient(events, history))


def test_digest_fires_once_and_shows_rank_climb(monkeypatch, tmp_path, capsys):
    _wire(monkeypatch, tmp_path, _events(3, 50), _history())
    assert post_gw_digest.main() == 1
    out = capsys.readouterr().out
    assert "GW3 DIGEST" in out
    assert "62" in out and "avg 50, +12" in out
    assert "−4 hit" in out
    assert "720,000" in out                       # overall rank present
    # second run: same finalized GW -> silent
    assert post_gw_digest.main() == 0
    assert capsys.readouterr().out == ""


def test_overall_rank_arrow_uses_prior_state(monkeypatch, tmp_path, capsys):
    (tmp_path / "state.json").write_text(json.dumps(
        {"last_digest_gw": 2, "overall_rank": 900000, "leagues": {"58005": 780}}))
    league_state = {"our_entry": 2797967, "league_ids": [58005],
                    "standings": [{"entry": 2797967, "league_id": 58005, "rank": 765,
                                   "entry_name": "Us"}]}
    _wire(monkeypatch, tmp_path, _events(3, 50), _history(), league_state)
    assert post_gw_digest.main() == 1
    out = capsys.readouterr().out
    assert "900,000 → 720,000" in out and "🟢 +180,000" in out
    assert "780 → 765" in out and "passed 15" in out


def test_no_finalized_gw_is_silent(monkeypatch, tmp_path, capsys):
    _wire(monkeypatch, tmp_path,
          [{"id": g, "finished": False, "data_checked": False} for g in (1, 2, 3)],
          _history())
    assert post_gw_digest.main() == 0
    assert capsys.readouterr().out == ""
