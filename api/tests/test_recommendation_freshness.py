"""`/v1/recommendations/current` must use the freshest *safe* data.

Covers the freshness policy in `app.recommendation_inputs`: fresh live inputs,
a stale finalized snapshot, an incomplete live snapshot, mixed-source data,
future/malformed timestamps, and the guarantees that nothing here can write to
FPL and that only a bound executable plan satisfies the engine's execution path.
"""
from __future__ import annotations

import ast
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.recommendation_inputs import resolve_recommendation_inputs
from app.repository import LiveSnapshotNotFoundError, SnapshotNotFoundError

NOW = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
TEAM_ID = main.settings.my_team_id
CLIENT = TestClient(main.app)

POS_TYPE = {"GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}
XI = ["GKP", "DEF", "DEF", "DEF", "MID", "MID", "MID", "FWD", "FWD", "FWD", "MID"]
BENCH = ["GKP", "DEF", "MID", "FWD"]
TEAMS = [{"id": 1, "name": "Alpha"}, {"id": 2, "name": "Beta"}, {"id": 3, "name": "Gamma"}]
TEAM_BY_NAME = {t["name"]: t["id"] for t in TEAMS}


def _bootstrap(current_gw: int = 3):
    elements = []
    for element_id in range(1, 60):
        elements.append({
            "id": element_id, "web_name": f"P{element_id}",
            "team": (element_id % 3) + 1, "element_type": ((element_id % 4) + 1),
            "now_cost": 50, "status": "a", "ep_next": "3.0", "form": "3.0",
            "points_per_game": "3.0", "chance_of_playing_next_round": None, "news": "",
        })
    # 55 == the elite "star" midfielder: high projection -> high score.
    elements.append({
        "id": 55, "web_name": "Star", "team": 1, "element_type": 3, "now_cost": 50,
        "status": "a", "ep_next": "9.0", "form": "8.0", "points_per_game": "8.0",
        "chance_of_playing_next_round": None, "news": "",
    })
    events = [
        {"id": gw, "is_current": gw == current_gw, "is_next": gw == current_gw + 1,
         "finished": gw < current_gw, "data_checked": gw < current_gw,
         "deadline_time": f"2026-09-{gw:02d}T17:30:00Z"}
        for gw in range(1, 11)
    ]
    return {
        "_meta": {"fetched_at": NOW.isoformat(), "content_sha256": "deadbeef"},
        "events": events, "teams": TEAMS, "elements": elements,
    }


def _fixtures(_gw):
    return [
        {"team_h": "Alpha", "team_a": "Beta", "team_h_difficulty": 2, "team_a_difficulty": 4},
        {"team_h": "Gamma", "team_a": "Alpha", "team_h_difficulty": 3, "team_a_difficulty": 3},
    ]


def _pick(element, position, *, is_captain=False, is_vice=False, bench=False):
    return {
        "element": element, "name": f"P{element}", "position": position,
        "team": TEAMS[element % 3]["name"], "cost": 5.0,
        "multiplier": 0 if bench else (2 if is_captain else 1),
        "is_captain": is_captain, "is_vice_captain": is_vice,
    }


def _squad(*, weak_mid_element=5, own_star=False):
    squad = []
    for index, position in enumerate(XI + BENCH):
        element = 10 + index
        if position == "MID" and index == 4:  # the sell candidate
            element = weak_mid_element
        squad.append(_pick(element, position,
                           is_captain=(index == 0), is_vice=(index == 1),
                           bench=index >= 11))
    if own_star:
        squad[4] = _pick(55, "MID", bench=False)
    return squad


def _manager(entry_id, rank, *, with_bank=True, own_star=False):
    m = {
        "entry_id": entry_id, "entry_name": f"E{entry_id}", "player_name": f"M{entry_id}",
        "overall_rank": rank, "league_rank": rank, "total_points": 100, "gw_points": 40,
        "squad_cost": 100.0, "captain": "P10", "transfers_made": 0,
        "squad": _squad(own_star=own_star),
    }
    if with_bank:
        m["gw_bank"] = 15  # £1.5m in FPL tenths
    return m


def _cohort(*, with_bank=True):
    # user + 5 rivals; one rival (rank 1) owns the star so it becomes elite core.
    return [
        _manager(TEAM_ID, 40, with_bank=with_bank),
        _manager(2, 1, with_bank=with_bank, own_star=True),
        _manager(3, 2, with_bank=with_bank, own_star=True),
        _manager(4, 3, with_bank=with_bank),
        _manager(5, 4, with_bank=with_bank),
        _manager(6, 5, with_bank=with_bank),
    ]


def _live_snapshot(*, captured_at, gameweek=3, with_bank=True, rank_provenance="official-entry-history"):
    managers = _cohort(with_bank=with_bank)
    return {
        "schema_version": 2, "status": "complete", "source": "official-fpl-live",
        "captured_at": captured_at, "league_id": 58005, "gameweek": gameweek,
        "expected_count": len(managers), "hydrated_count": len(managers),
        "rank_provenance": rank_provenance, "managers": managers,
    }


def _finalized_snapshot(*, fetched_at, gw=2):
    return {
        "fetched_at": fetched_at, "gw": gw, "total_entries": 6,
        "population_size": 6, "competitors": _cohort(with_bank=True),
    }


def _wire(monkeypatch, *, live=None, finalized=None, current_gw=3, finalized_gw=2):
    monkeypatch.setattr(main.repository, "bootstrap", lambda: _bootstrap(current_gw))
    monkeypatch.setattr(main.repository, "fixtures", _fixtures)
    monkeypatch.setattr(main, "_current_gameweek", lambda: finalized_gw)

    def live_league(_league_id):
        if live is None:
            raise LiveSnapshotNotFoundError("no live snapshot")
        return live
    monkeypatch.setattr(main.repository, "live_league", live_league)

    def league(_league_id, _gw):
        if finalized is None:
            raise SnapshotNotFoundError("no finalized snapshot")
        return dict(finalized)
    monkeypatch.setattr(main.repository, "league", league)


# --------------------------------------------------------------------------- #

def test_fresh_live_inputs(monkeypatch):
    _wire(monkeypatch, live=_live_snapshot(captured_at=(NOW - timedelta(minutes=20)).isoformat()))
    body = CLIENT.get("/v1/recommendations/current?league_id=58005").json()

    assert body["packet_status"] == "advisory"
    fr = body["freshness"]
    assert fr["source"] == "official-fpl-live" and fr["status"] == "fresh"
    assert fr["stale"] is False and fr["bank_known"] is True
    assert body["meta"]["data_source"] == "official-fpl-live"
    assert body["meta"]["missing_fields"] == []
    assert body["gameweek"] == 3
    assert isinstance(body["transfers"], list)
    assert body["inputs"]["bank_known"] is True


def test_stale_finalized_snapshot_only_predating_live_gw_is_safe_hold(monkeypatch):
    _wire(
        monkeypatch, live=None,
        finalized=_finalized_snapshot(fetched_at=(NOW - timedelta(hours=69)).isoformat(), gw=2),
        current_gw=3, finalized_gw=2,
    )
    body = CLIENT.get("/v1/recommendations/current?league_id=58005").json()

    assert body["packet_status"] == "safe_hold"
    assert body["transfers"] == []
    assert body["meta"]["stale"] is True
    fr = body["freshness"]
    assert fr["status"] == "safe_hold"
    assert "predates_live_gw" in fr["reason"]
    # competitive block still parseable by the engine client (phase + alignment).
    assert body["competitive"]["phase"] and body["competitive"]["alignment"] is not None


def test_stale_finalized_same_gw_is_labelled_stale_not_hidden(monkeypatch):
    _wire(
        monkeypatch, live=None,
        finalized=_finalized_snapshot(fetched_at=(NOW - timedelta(hours=30)).isoformat(), gw=3),
        current_gw=3, finalized_gw=3,
    )
    body = CLIENT.get("/v1/recommendations/current?league_id=58005").json()
    assert body["packet_status"] == "advisory"
    assert body["freshness"]["status"] == "stale" and body["meta"]["stale"] is True


def test_incomplete_live_snapshot_is_provisional_with_bank_unconfirmed(monkeypatch):
    _wire(monkeypatch, live=_live_snapshot(
        captured_at=(NOW - timedelta(minutes=15)).isoformat(), with_bank=False))
    body = CLIENT.get("/v1/recommendations/current?league_id=58005").json()

    # packet_status stays "advisory" (still actionable); the "provisional"
    # nuance lives in freshness.status so older consumers are unaffected.
    assert body["packet_status"] == "advisory"
    assert body["freshness"]["status"] == "provisional"
    assert body["freshness"]["bank_known"] is False
    assert body["meta"]["missing_fields"] == ["gw_bank"]
    assert body["inputs"]["bank_known"] is False
    for transfer in body["transfers"]:
        assert transfer["legal_checks"]["bank_known"] is False


def test_mixed_source_live_league_plus_fresh_catalogue(monkeypatch):
    _wire(monkeypatch, live=_live_snapshot(captured_at=(NOW - timedelta(hours=1)).isoformat()))
    body = CLIENT.get("/v1/recommendations/current?league_id=58005").json()
    # league context is live; catalogue-derived numbers still resolve.
    assert body["freshness"]["source"] == "official-fpl-live"
    assert body["captains"], "captain ranking needs catalogue ep_next/form"
    assert all("fixture" in c for c in body["captains"])


@pytest.mark.parametrize("captured_at", [
    "2020-01-01T00:00:00Z",                       # ancient
    None,                                          # missing
    "2026-09-04T12:00:00",                         # naive (no tz)
    (NOW + timedelta(days=1)).isoformat(),         # future
])
def test_future_or_malformed_live_timestamp_is_rejected(monkeypatch, captured_at):
    _wire(
        monkeypatch,
        live=_live_snapshot(captured_at=captured_at),
        finalized=_finalized_snapshot(fetched_at=(NOW - timedelta(hours=2)).isoformat(), gw=3),
        current_gw=3, finalized_gw=3,
    )
    body = CLIENT.get("/v1/recommendations/current?league_id=58005").json()
    assert body["freshness"]["source"] == "finalized-snapshot"
    assert "live_snapshot" in body["freshness"]["reason"]


def test_no_valid_source_anywhere_is_needs_refresh(monkeypatch):
    _wire(monkeypatch, live=None, finalized=None, current_gw=3, finalized_gw=3)
    body = CLIENT.get("/v1/recommendations/current?league_id=58005").json()
    assert body["packet_status"] == "needs_refresh"
    assert body["transfers"] == []
    assert body["freshness"]["status"] == "needs_refresh"


def test_decision_current_propagates_the_recommendation_status(monkeypatch):
    _wire(
        monkeypatch, live=None,
        finalized=_finalized_snapshot(fetched_at=(NOW - timedelta(hours=69)).isoformat(), gw=2),
        current_gw=3, finalized_gw=2,
    )
    body = CLIENT.get("/v1/decision/current?league_id=58005").json()
    assert body["packet_status"] == "safe_hold"
    assert body["executable"] is False
    assert body["freshness"]["status"] == "safe_hold"


def test_no_automatic_fpl_writes_in_the_response_or_the_module(monkeypatch):
    _wire(monkeypatch, live=_live_snapshot(captured_at=(NOW - timedelta(minutes=10)).isoformat()))
    body = CLIENT.get("/v1/recommendations/current?league_id=58005").json()
    assert body["competitive"]["execution_authority"] == "manual_fpl"
    assert body["competitive"]["writes_enabled"] is False

    # Static guarantee: the resolver imports no execution / write client.
    src = Path(main.__file__).with_name("recommendation_inputs.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    execution_imports = [n for n in imported if "fpl_client" in n or "execution" in n]
    assert not execution_imports, f"resolver must not import an execution client: {execution_imports}"
    for bad in ("set_lineup", "submit_transfers", "make_transfers", "requests.post", "httpx.post"):
        assert bad not in src


def test_telegram_approval_remains_the_only_execution_authority(monkeypatch):
    """A provisional / safe_hold packet must never satisfy require_executable_plan."""
    spec = importlib.util.spec_from_file_location(
        "competitive_v4_client",
        Path(main.__file__).resolve().parents[2] / "engine" / "model" / "competitive_v4_client.py",
    )
    client = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(client)

    def _payload(packet_status, quality="valid"):
        return {
            "competitive": {"model_version": "competitive-v4.0", "phase": "MATCH", "alignment": 30},
            "meta": {"quality_status": quality},
            "packet_status": packet_status, "executable": False, "plan": None,
        }

    class _Resp:
        def __init__(self, data): self._data = data
        def read(self): import json; return json.dumps(self._data).encode()
        def __enter__(self): return self
        def __exit__(self, *_): return False

    for status in ("provisional", "safe_hold", "advisory"):
        monkeypatch.setattr(client.urllib.request, "urlopen", lambda *_a, **_k: _Resp(_payload(status)))
        with pytest.raises(client.CompetitiveV4Error):
            client.fetch_competitive_v4(58005, 3, require_executable_plan=True)


def test_resolver_unit_prefers_fresh_live_over_stale_finalized(monkeypatch):
    class Repo:
        def bootstrap(self): return _bootstrap(3)
        def fixtures(self, _gw): return _fixtures(_gw)
        def live_league(self, _lid):
            return _live_snapshot(captured_at=(NOW - timedelta(minutes=5)).isoformat())
        def league(self, _lid, _gw):
            return _finalized_snapshot(fetched_at=(NOW - timedelta(hours=90)).isoformat())

    resolved = resolve_recommendation_inputs(Repo(), 58005, TEAM_ID, 2, now=NOW)
    assert resolved.source == "official-fpl-live"
    assert resolved.status == "fresh" and resolved.usable is True
    assert resolved.gameweek == 3
