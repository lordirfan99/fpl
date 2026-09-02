import pytest
from fastapi.testclient import TestClient
from fastapi import HTTPException

from app import main
from app.main import app
from app.recommendations import _phase, calibration_weights
from app.validation import validate_manager_squad


client = TestClient(app)


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["competitive_model"] == "competitive-v4.0"
    assert response.json()["execution_authority"] == "manual_fpl"
    assert response.json()["dashboard_writes_enabled"] is False
    assert response.json()["writes_enabled"] is False


def test_my_team_has_valid_squad() -> None:
    response = client.get("/v1/me/team?league_id=58005&gw=1")
    assert response.status_code == 200
    payload = response.json()
    assert payload["manager"]["entry_id"] == 2797967
    assert len(payload["manager"]["squad"]) == 15
    assert sum(1 for pick in payload["manager"]["squad"] if pick["multiplier"] > 0) == 11


def test_current_gameweek_is_discovered() -> None:
    identity = client.get("/v1/me").json()
    response = client.get("/v1/me/team?league_id=58005")
    assert response.status_code == 200
    assert response.json()["gameweek"] == identity["current_gameweek"]


def test_current_gameweek_falls_back_from_provisional_snapshot(monkeypatch) -> None:
    finalized = main.repository.league(58005, 1)

    class Repository:
        @staticmethod
        def bootstrap() -> dict:
            return {"events": [{"id": 2, "is_current": True}]}

        @staticmethod
        def league(league_id: int, gameweek: int) -> dict:
            assert league_id == 58005
            if gameweek == 2:
                return {"gw": 2, "fetched_at": "2026-09-01T00:00:00Z", "competitors": []}
            return finalized

    monkeypatch.setattr(main, "repository", Repository())
    assert main._current_gameweek() == 1


def test_elite_and_recommendation_contracts() -> None:
    league = client.get("/v1/leagues/58005?gw=1")
    elite = client.get("/v1/elite/1?league_id=58005")
    recommendations = client.get("/v1/recommendations/current?league_id=58005&gw=1")
    assert elite.status_code == 200
    assert league.json()["hydration_percent"] == 100.0
    expected_count = max(1, (league.json()["count"] * 5 + 99) // 100)
    assert elite.json()["count"] == expected_count
    assert elite.json()["ownership"]
    assert recommendations.status_code == 200
    payload = recommendations.json()
    assert payload["elite_count"] == expected_count
    assert payload["captains"]
    assert payload["competitive"]["model_version"] == "competitive-v4.0"
    assert payload["competitive"]["phase"] in {"CATCH", "MATCH", "ATTACK", "CHASE"}
    assert 0 <= payload["competitive"]["alignment"] <= 100
    assert payload["competitive"]["target_alignment"] == 82
    assert payload["competitive"]["weights"] == {
        "elite_consensus": 0.45,
        "projection": 0.45,
        "current_season_evidence": 0.10,
    }
    assert payload["competitive"]["writes_enabled"] is False
    assert payload["competitive"]["template_formation"]
    assert payload["competitive"]["elite_template"]
    assert payload["competitive"]["template_gate"]["decision"] in {
        "CONVERGE_TO_TEMPLATE", "CONTROLLED_DIFFERENTIAL"
    }
    assert payload["competitive"]["execution_authority"] == "manual_fpl"
    assert payload["meta"]["quality_status"] == "valid"
    for transfer in payload["transfers"]:
        assert transfer["incoming"]["position"] == transfer["outgoing"]["position"]
        assert transfer["incoming"]["cost"] <= transfer["outgoing"]["cost"] + transfer["legal_checks"]["bank"]
        assert transfer["legal_checks"]["club_limit"] is True
        assert transfer["net_ev_status"] == "not_calculated"
        assert "gross" in transfer["gain_basis"]

    # V4 must apply the published weights, not merely return them as metadata.
    candidate = next(player for player in payload["captains"] if not player["risk"])
    components = candidate["components"]
    weights = payload["competitive"]["weights"]
    calculated = 100 * sum(weights[name] * components[name] for name in weights)
    assert candidate["score"] == pytest.approx(calculated, abs=0.02)


def test_bench_boost_validation_is_chip_aware() -> None:
    payload = client.get("/v1/me/team?league_id=58005&gw=1").json()["manager"]
    payload["active_chip"] = "bboost"
    for pick in payload["squad"]:
        pick["multiplier"] = max(1, pick["multiplier"])
    assert validate_manager_squad(payload) == []


def test_canonical_decision_packet_contract() -> None:
    response = client.get("/v1/decision/current?league_id=58005&gw=1")
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["decision_id"]) == 64
    assert payload["model_version"] == "competitive-v4.0"
    assert payload["competitive"]["model_version"] == "competitive-v4.0"
    assert payload["execution_authority"] == "manual_fpl"
    assert payload["writes_enabled"] is False
    assert payload["executable"] is False
    assert payload["plan"] is None
    assert payload["packet_status"] == "advisory"


def test_decision_packet_is_local_only_safe_hold_without_snapshot(monkeypatch) -> None:
    """With no finalized snapshot the packet is a plainly labelled safe hold.

    There is no bridge fallback: the API can only ever return locally derived
    read-only decision support.
    """
    monkeypatch.setattr(
        main, "recommendations",
        lambda **_: (_ for _ in ()).throw(HTTPException(status_code=404)),
    )
    payload = client.get("/v1/decision/current?league_id=58005&gw=2").json()
    assert payload["packet_status"] == "safe_hold"
    assert payload["executable"] is False
    assert payload["plan"] is None
    assert payload["execution_authority"] == "manual_fpl"
    assert payload["writes_enabled"] is False
    assert payload["meta"]["source"] == "snapshot"


def test_api_has_no_autopilot_or_telegram_surface() -> None:
    """No bridge/Telegram endpoint, setting or response field survives."""
    from app.settings import settings as live_settings

    for attribute in ("autopilot_base_url", "autopilot_token", "telegram_configured", "telegram_bot_name"):
        assert not hasattr(live_settings, attribute), attribute
    for path in ("/v1/autopilot/status", "/v1/autopilot/control-centre", "/v1/integration/status"):
        assert client.get(path).status_code == 404, path
    assert client.post("/internal/v1/snapshots/bootstrap_cache.json", content=b"{}").status_code == 404
    for path in ("/health", "/v1/recommendations/current?league_id=58005&gw=1", "/v1/decision/current?league_id=58005&gw=1"):
        body = client.get(path).text.lower()
        assert "telegram" not in body, path
        assert "autopilot" not in body, path


def test_v4_calibration_and_chase_are_deterministic() -> None:
    for gameweek in (1, 3, 6, 12):
        assert sum(calibration_weights(gameweek).values()) == pytest.approx(1.0)
    manager = {"total_points": 1800}
    phase, _, inputs = _phase(manager, [{"total_points": 1900}], 30, alignment=90, target=72)
    assert phase == "CHASE"
    assert inputs["leader_gap"] == 100
    assert inputs["chase_trigger"] == 40


def test_catalog_exposes_official_fpl_entities() -> None:
    response = client.get("/v1/catalog")
    assert response.status_code == 200
    assert response.json()["players"]
    assert response.json()["teams"]
    assert response.json()["meta"]["snapshot_at"]
    assert response.json()["meta"]["quality_status"] == "valid"


def test_live_team_endpoint_is_separate_from_snapshots(monkeypatch) -> None:
    monkeypatch.setattr(main.live_fpl, "current_gameweek", lambda: 2)
    monkeypatch.setattr(main.live_fpl, "team", lambda entry_id, gameweek, league_id: {
        "source": "official-fpl-live", "status": "live", "gameweek": gameweek,
        "entry": {"id": entry_id}, "league": {"id": league_id}, "picks": [], "provisional": True,
    })
    response = client.get("/v1/live/team")
    assert response.status_code == 200
    assert response.json()["source"] == "official-fpl-live"
    assert response.json()["status"] == "live"
    assert response.json()["gameweek"] == 2
    assert response.json()["league"]["id"] == 58005


def test_live_league_reads_only_a_complete_background_snapshot(monkeypatch) -> None:
    snapshot = {
        "gameweek": 2,
        "expected_count": 2,
        "hydrated_count": 2,
        "captured_at": "2026-09-01T12:00:00+00:00",
        "pages_fetched": 1,
        "managers": [
            {"entry_id": 1, "entry_name": "One", "player_name": "Manager One", "gw_points": 40, "total_points": 100, "overall_rank": 1, "league_rank": 1, "squad_cost": 100.0, "captain": "Player", "transfers_made": 0, "squad": [{"element": 1}] * 15},
            {"entry_id": 2, "entry_name": "Two", "player_name": "Manager Two", "gw_points": 30, "total_points": 90, "overall_rank": 2, "league_rank": 2, "squad_cost": 99.0, "captain": "Player", "transfers_made": 0, "squad": [{"element": 2}] * 15},
        ],
    }
    monkeypatch.setattr(main.repository, "live_league", lambda league_id: snapshot)

    response = client.get("/v1/leagues/58005/live")

    assert response.status_code == 200
    assert response.json()["hydration_percent"] == 100.0
    assert response.json()["count"] == 2
    assert response.json()["meta"]["source"] == "official-fpl-live-snapshot"


def test_live_team_calculates_current_score_and_league_rank_from_official_payload(monkeypatch) -> None:
    payloads = {
        "bootstrap-static/": {
            "events": [{"id": 2, "finished": False}],
            "elements": [{"id": 10, "web_name": "Captain", "team": 1, "event_points": 7, "now_cost": 80}],
        },
        "entry/99/": {
            "name": "Test FC", "player_first_name": "Test", "player_last_name": "Manager",
            "summary_overall_rank": 1234, "summary_overall_points": 88,
            "leagues": {"classic": [{"id": 58005, "name": "Test League", "entry_rank": 12, "entry_last_rank": 20, "rank_count": 100}]},
        },
            "entry/99/event/2/picks/": {"picks": [{"element": 10, "position": 1, "multiplier": 2, "is_captain": True, "is_vice_captain": False}]},
            "entry/99/history/": {"current": []},
            "fixtures/?event=2": [{"team_h": 1, "team_a": 2, "started": False, "finished": False, "kickoff_time": "2026-09-02T12:00:00Z"}],
    }
    monkeypatch.setattr(main.live_fpl, "_get", lambda path, ttl=30: payloads[path])
    result = main.live_fpl.team(99, 2, 58005)
    assert result["points"] == 14
    assert result["points_source"] == "official-live-picks"
    assert result["league"]["entry_rank"] == 12
    assert result["fixtures"] == [{"team_h": 1, "team_a": 2, "started": False, "finished": False, "kickoff_time": "2026-09-02T12:00:00Z"}]


def test_fixture_horizon_is_populated() -> None:
    response = client.get("/v1/fixtures?from_gw=2&to_gw=6")
    assert response.status_code == 200
    payload = response.json()["gameweeks"]
    assert set(payload) == {"2", "3", "4", "5", "6"}
    assert all(payload[str(gw)] for gw in range(2, 7))


def test_missing_snapshot_is_404() -> None:
    response = client.get("/v1/leagues/58005?gw=38")
    assert response.status_code == 404


# --- Regression: live/in-progress GW snapshots must not 500 ---
# The FPL API can return null picks, non-numeric multipliers, bare-string
# squad entries, or even null competitor rows for a gameweek still in
# progress. snapshot_quality historically crashed on these with an uncaught
# exception -> HTTP 500, which poisoned /v1/me/team and /v1/leagues/* and
# silently rolled the whole dashboard back to GW1. These tests pin that the
# validation layer now fails soft (returns issues, never raises).

def _malformed_snapshot(competitors):
    return {
        "gw": 2, "league_id": 58005, "fetched_at": "2026-08-30T00:00:00Z",
        "total_entries": len(competitors), "errors": 0, "competitors": competitors,
    }


def _valid_competitor(entry_id: int, squad) -> dict:
    return {
        "entry_id": entry_id, "entry_name": "Test FC", "player_name": "T",
        "league_rank": 1, "gw_points": 50, "total_points": 50, "rank": 1000,
        "squad": squad, "captain": "P2", "vice_captain": "P3",
        "squad_composition": {"DEF": 5}, "squad_teams": {"ARS": 3},
        "squad_cost": 80.0, "active_players_count": 11, "injured_count": 0,
        "transfers_made": 0, "chips_used": [],
    }


def _full_squad() -> list[dict]:
    # Full squad shape 2-5-5-3 (15 picks). First 11 forms a legal XI:
    # 1 GKP, 4 DEF, 4 MID, 2 FWD; the 4 bench complete 2-5-5-3 (GKP/DEF/MID/FWD).
    xi = ["GKP"] + ["DEF"] * 4 + ["MID"] * 4 + ["FWD"] * 2          # 11
    bench = ["GKP"] + ["DEF"] + ["MID"] + ["FWD"]                    # 4
    positions = xi + bench
    squad = []
    for i in range(1, 16):
        # XI (first 11) have positive multiplier; bench (last 4) have 0.
        mult = 2 if i == 1 else (1 if i <= 11 else 0)
        squad.append({
            "element": i, "name": f"P{i}",
            "position": positions[i - 1],
            "team": "ARS", "cost": 5.0, "is_captain": False, "is_vice_captain": False,
            "multiplier": mult, "position_order": i, "selected_by": 10, "form": 0,
            "total_points": 0, "points_per_game": 0, "status": "a",
            "chance_of_playing": 100, "news": "", "minutes": 0, "starts": 0,
            "expected_goals": 0, "expected_assists": 0, "expected_goals_per_90": 0,
            "expected_assists_per_90": 0,
        })
    squad[0]["is_captain"] = True
    squad[1]["is_vice_captain"] = True
    return squad


def test_null_pick_in_live_gw_snapshot_does_not_crash() -> None:
    c = _valid_competitor(1, [None] + _full_squad()[1:])
    quality, issues = main.snapshot_quality(_malformed_snapshot([c]))
    assert quality == "invalid"
    assert any("squad_non_dict_picks" in issue for issue in issues)


def test_non_numeric_multiplier_does_not_crash() -> None:
    squad = _full_squad()
    squad[1]["multiplier"] = "not-an-int"
    c = _valid_competitor(2, squad)
    quality, issues = main.snapshot_quality(_malformed_snapshot([c]))
    assert quality == "invalid"
    assert any("scoring_players" in issue for issue in issues)


def test_null_competitor_row_does_not_crash() -> None:
    quality, issues = main.snapshot_quality(_malformed_snapshot([None]))
    assert quality == "invalid"
    assert "competitor_malformed" in issues


def test_bare_string_squad_entry_does_not_crash() -> None:
    squad = ["GKP"] + _full_squad()[1:]
    c = _valid_competitor(3, squad)
    quality, issues = main.snapshot_quality(_malformed_snapshot([c]))
    assert quality == "invalid"


def test_non_numeric_total_entries_does_not_crash() -> None:
    snapshot = _malformed_snapshot([_valid_competitor(1, _full_squad())])
    snapshot["total_entries"] = "one-thousand"
    quality, issues = main.snapshot_quality(snapshot)
    assert quality == "invalid"
    assert any("total_entries_non_numeric" in issue for issue in issues)


def test_valid_snapshot_still_reports_valid() -> None:
    c = _valid_competitor(1, _full_squad())
    snapshot = _malformed_snapshot([c])
    snapshot["errors"] = 0
    quality, _ = main.snapshot_quality(snapshot)
    assert quality == "valid"


def test_provisional_league_snapshot_returns_conflict_not_500(monkeypatch) -> None:
    incomplete = _valid_competitor(1, _full_squad())
    del incomplete["squad_cost"]
    monkeypatch.setattr(main.repository, "league", lambda league_id, gameweek: _malformed_snapshot([incomplete]))

    response = client.get("/v1/leagues/58005?gw=2")

    assert response.status_code == 409
    payload = response.json()["detail"]
    assert payload["code"] == "snapshot_not_finalized"
    assert payload["gameweek"] == 2
    assert payload["quality_status"] == "invalid"
    assert any("squad_cost" in issue for issue in payload["quality_issues"])
