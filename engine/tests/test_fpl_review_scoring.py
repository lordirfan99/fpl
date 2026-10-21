import pytest

from automation.fpl_review_scoring import ScoringError, evaluate, score_lineup, validate_picks


POSITIONS = [1, 2, 2, 2, 3, 3, 3, 3, 4, 4, 4, 1, 2, 3, 2]
CLUBS = [i // 3 + 1 for i in range(15)]


def picks():
    rows = []
    for slot in range(1, 16):
        row = {"element": slot, "position": slot}
        if slot == 9:
            row["is_captain"] = True
        if slot == 10:
            row["is_vice_captain"] = True
        rows.append(row)
    return rows


def players(minutes=90, points=None):
    points = points or {i: i for i in range(1, 16)}
    return {
        i: {"position": POSITIONS[i - 1], "club": CLUBS[i - 1], "minutes": minutes, "points": points[i]}
        for i in range(1, 16)
    }


def recommendation(**kwargs):
    return {"picks": picks(), **kwargs}


def test_validate_picks_enforces_squad_clubs_captain_and_bench_slots():
    assert len(validate_picks(picks(), players())) == 15
    bad = picks()
    bad[14]["position"] = 12
    with pytest.raises(ScoringError):
        validate_picks(bad, players())
    bad = picks()
    bad[0]["element"] = bad[1]["element"]
    with pytest.raises(ScoringError, match="duplicate"):
        validate_picks(bad, players())
    bad = picks()
    bad[8]["is_captain"] = False
    with pytest.raises(ScoringError):
        validate_picks(bad, players())
    bad_players = players()
    bad_players[4]["club"] = 1
    with pytest.raises(ScoringError, match="three players"):
        validate_picks(picks(), bad_players)
    bad = picks()
    bad[1]['position'], bad[13]['position'] = bad[13]['position'], bad[1]['position']
    with pytest.raises(ScoringError, match="formation"):
        validate_picks(bad, players())


def test_duplicate_and_missing_outcome_rows_fail_closed():
    bad = players()
    bad.pop(15)
    with pytest.raises(ScoringError, match="missing metadata"):
        score_lineup(recommendation(), bad)
    bad = players()
    bad[1].pop("points")
    with pytest.raises(ScoringError, match="points"):
        score_lineup(recommendation(), bad)


def test_gk_sub_and_captain_dnp_vice_doubles_once():
    data = players()
    data[1]["minutes"] = 0
    data[9]["minutes"] = 0
    data[9]["points"] = 0
    for player_id in (13, 14, 15):
        data[player_id]["minutes"] = 0
    # Bench GK 12 enters; vice 10 gets the captain extra.  No bench row is reused.
    expected = sum(data[i]["points"] for i in range(2, 12)) + data[12]["points"] + data[10]["points"]
    assert score_lineup(recommendation(), data) == expected


def test_bench_order_and_each_substitute_used_once_with_many_dnps():
    data = players(points={i: 100 + i for i in range(1, 16)})
    # DNP starting DEF and MID. Bench DEF then MID can both enter legally.
    data[2]["minutes"] = 0
    data[5]["minutes"] = 0
    result = score_lineup(recommendation(), data)
    # The first bench DEF is used for the DEF DNP, then bench MID for the MID DNP.
    expected_active = [1, 3, 4, 13, 6, 7, 8, 14, 9, 10, 11]
    expected = sum(data[i]["points"] for i in expected_active) + data[9]["points"]
    assert result == expected


def test_no_hindsight_selects_first_legal_bench_and_preserves_formation():
    data = players(points={i: 1 for i in range(1, 16)})
    data[2]["minutes"] = 0
    data[3]["minutes"] = 0
    data[5]["minutes"] = 0
    data[13]["points"] = 50
    data[14]["points"] = 70
    # DEF13 is first and legal for one DEF DNP; it must not be reused for the second.
    assert score_lineup(recommendation(), data) == 9 + 50 + 70 + 1


def test_blank_official_minutes_and_points_are_valid_zeroes():
    data = players(minutes=0, points={i: 0 for i in range(1, 16)})
    assert score_lineup(recommendation(), data) == 0


def test_negative_points_and_hit_cost_rules():
    data = players(points={i: -i for i in range(1, 16)})
    assert score_lineup(recommendation(hit_cost=4), data) < 0
    for cost in (-4, 2, 64):
        with pytest.raises(ScoringError):
            score_lineup(recommendation(hit_cost=cost), players())
    with pytest.raises(ScoringError, match="chips"):
        score_lineup(recommendation(chip="3xc"), players())


def test_evaluate_requires_frozen_valid_gw_and_official_owner_url():
    payload = {
        "status": "finished",
        "data_checked": True,
        "gameweek": 8,
        "players": players(),
        "model": recommendation(hit_cost=4),
        "hybrid": recommendation(),
        "owner_actual": {
            "official": True,
            "net_points": 50,
            "source_url": "https://fantasy.premierleague.com/entry/123/history",
        },
    }
    result = evaluate(payload)
    assert result["model"]["net_points"] == result["model"]["gross_points"] - 4
    for changed in ({"status": "live"}, {"gameweek": 39}, {"data_checked": False}):
        with pytest.raises(ScoringError):
            evaluate({**payload, **changed})
    with pytest.raises(ScoringError):
        evaluate({**payload, "owner_actual": {"official": True, "net_points": 50, "source_url": "https://example.test/x"}})
