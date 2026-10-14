import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.static_dataset import LEAGUES, OWNER, build_dataset, canonical, write_dataset


def inputs():
    captured = datetime.now(timezone.utc).isoformat()
    positions = [1, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 1, 2, 2, 3]
    bootstrap = {"elements": [{"id": i, "web_name": f"Player {i}", "team": 1,
                               "element_type": positions[(i - 1) % 15], "now_cost": 50,
                               "minutes": 500, "starts": 6, "form": "3.0", "status": "a"}
                              for i in range(1, 401)],
                 "teams": [{"id": i, "name": f"Club {i}", "short_name": f"C{i}", "code": i} for i in range(1, 21)],
                 "events": [{"id": i, "name": f"GW{i}", "finished": i <= 6, "data_checked": i <= 6,
                             "is_current": i == 6, "is_next": i == 7} for i in range(1, 39)],
                 "element_types": [{"id": i} for i in range(1, 5)]}
    fixtures = [{"id": i, "event": (i - 1) // 10 + 1, "team_h": 1, "team_a": 2,
                 "team_h_difficulty": 2, "team_a_difficulty": 3} for i in range(1, 381)]
    squad = [{"element": i, "name": f"Player {i}", "position": {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}[positions[i - 1]],
              "team": "Club 1", "cost": 5, "multiplier": 2 if i == 1 else 1 if i <= 11 else 0,
              "is_captain": i == 1, "is_vice_captain": i == 2} for i in range(1, 16)]
    managers = [{"entry_id": entry, "entry_name": f"Team {entry}", "player_name": "Recorded Manager",
                 "gw_points": 50, "total_points": 300, "overall_rank": 1000, "league_rank": rank,
                 "squad_cost": 75, "captain": "Player 1", "transfers_made": 0, "squad": deepcopy(squad)}
                for rank, entry in enumerate([OWNER, 123], 1)]
    leagues = [{"league_id": league, "gameweek": 6, "status": "complete", "expected_count": 2,
                "hydrated_count": 2, "captured_at": captured, "managers": deepcopy(managers)} for league in LEAGUES]
    teams = {league: {"entry": {"id": OWNER}, "gameweek": 6, "picks": [{}] * 15,
                      "fetched_at": captured} for league in LEAGUES}
    return dict(bootstrap=bootstrap, fixtures=fixtures, leagues=leagues, teams=teams,
                captured_at=captured, revision="a" * 40)


def test_complete_dataset_is_content_addressed_public_only_and_has_both_archives(tmp_path):
    manifest, files = build_dataset(**inputs())
    assert manifest["writes_enabled"] is False
    assert manifest["gameweeks"] == {str(lid): [6] for lid in LEAGUES}
    assert "private" not in " ".join(files)
    for path, body in files.items():
        assert hashlib.sha256(body).hexdigest() == manifest["files"][path]
    write_dataset(tmp_path, manifest, files)
    assert json.loads((tmp_path / "manifest.json").read_text()) == manifest
    assert len(json.loads(files["catalog.json"])["players"]) == 400


@pytest.mark.parametrize("fault", ["partial", "duplicate", "captain", "squad", "transition", "owner", "secret"])
def test_bad_collection_never_builds_a_publishable_dataset(fault):
    args = inputs()
    if fault == "partial": args["leagues"][1]["hydrated_count"] = 1
    if fault == "duplicate": args["leagues"][0]["managers"][1]["entry_id"] = OWNER
    if fault == "captain": args["leagues"][0]["managers"][0]["squad"][0]["is_captain"] = False
    if fault == "squad": args["leagues"][0]["managers"][0]["squad"].pop()
    if fault == "transition": args["leagues"][0]["gameweek"] = 7
    if fault == "owner": args["teams"][LEAGUES[0]]["entry"]["id"] = 1
    if fault == "secret": args["previous"] = {"private/plan.json": b'{}'}
    with pytest.raises(ValueError): build_dataset(**args)


def test_archived_capture_time_is_not_renewed_by_new_collection():
    args = inputs()
    _, old = build_dataset(**args)
    name = "leagues/58005/gw/6.json"
    archive = json.loads(old[name]); archive["meta"]["snapshot_at"] = "2026-10-01T00:00:00Z"
    old[name] = canonical(archive)
    _, files = build_dataset(**args, previous=old)
    assert files[name] == old[name]


def test_bench_boost_retains_valid_fifteen_scoring_players():
    args = inputs()
    manager = args["leagues"][0]["managers"][0]
    manager["active_chip"] = "bboost"
    for pick in manager["squad"]: pick["multiplier"] = 2 if pick["is_captain"] else 1
    manifest, _ = build_dataset(**args)
    assert manifest["gameweeks"]["58005"] == [6]


def test_official_bench_boost_capture_with_zero_multiplier_bench_is_not_dropped():
    args = inputs()
    args['leagues'][0]['managers'][0]['active_chip'] = 'bboost'
    # Observed public FPL case: active chip, 15 picks, 11 positive multipliers.
    _, files = build_dataset(**args)
    league = json.loads(files['leagues/58005/live.json'])
    refs = league['managers'][0]['squad']
    assert len(refs) == 15
    assert [ref[1] for ref in refs[-4:]] == [0, 0, 0, 0]


def test_public_owner_capture_does_not_copy_unallowlisted_nested_fields():
    args = inputs()
    team = args['teams'][LEAGUES[0]]
    team['private_projection'] = 'must not be public'
    team['entry']['account_fingerprint'] = 'must not be public'
    team['picks'][0] = {'selling_price': 50}
    _, files = build_dataset(**args)
    text = files['leagues/58005/team.json'].decode()
    assert 'private_projection' not in text
    assert 'account_fingerprint' not in text
    assert 'selling_price' not in text
