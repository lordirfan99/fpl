from app.decision_context import goal, ownership, build_context, _cache
from app.repository import SnapshotNotFoundError


def managers(count=21):
    return [{"entry_id": i, "league_rank": i, "total_points": 200 - i,
             "squad": [{"element": e, "name": f"P{e}", "is_captain": e == 1} for e in range(1, 16)]}
            for i in range(1, count + 1)]


def test_top_ten_percent_is_not_top_ten_positions():
    result = goal(managers(), 21, 10)
    assert result["cutoff_rank"] == 3
    assert result["points_gap"] == 7
    assert result["inside_target"] is False


def test_missing_and_duplicate_standings_never_generate_a_target():
    assert goal(managers(20), 21, 10)["available"] is False
    rows = managers()
    rows[2] = rows[0]
    assert goal(rows, 21, 10)["available"] is False
    rows = managers()
    rows[2]["total_points"] = None
    assert goal(rows, 21, 10)["available"] is False
    assert goal(managers(), 21, 100)["available"] is False
    assert goal(managers(), True, 10)["available"] is False
    rows = managers()
    rows[2]["league_rank"] = 2.5
    assert goal(rows, 21, 10)["available"] is False


def test_tied_cutoff_does_not_override_official_rank():
    rows = managers()
    rows[9]["total_points"] = rows[2]["total_points"]
    result = goal(rows, 21, 10)
    assert result["tied_cutoff"] is True
    assert result["points_gap"] == 0
    assert result["inside_target"] is False


def test_hydration_denominator_and_cohort_selected_before_hydration():
    rows = managers()
    rows[0]["squad"] = []
    result = ownership(rows, 21, 10)
    assert result["sample_count"] == 20
    assert result["cohort_count"] == 3
    assert result["cohort_sample"] == 2
    assert result["rows"][0]["league_pct"] == 100
    assert result["rows"][0]["target_captain_pct"] == 100


def test_official_rank_ties_are_included_and_malformed_squads_reduce_coverage():
    rows = managers()
    rows[3]["league_rank"] = 3
    rows[1]["squad"][0] = None
    result = ownership(rows, 21, 10)
    assert result["cohort_rank_threshold"] == 3
    assert result["cohort_count"] == 4
    assert result["cohort_sample"] == 3
    assert result["sample_count"] == 20


def test_missing_history_is_gap_not_zero():
    class Repo:
        def live_league(self, league):
            return {"gameweek": 3, "expected_count": 21, "managers": managers(), "captured_at": "2026-09-04T15:00:00Z"}
        def bootstrap(self):
            return {"events": [{"id": 1, "finished": True, "data_checked": True}, {"id": 2, "finished": True, "data_checked": True}]}
        def league(self, league, gw):
            if gw == 1:
                return {"competitors": managers(), "total_entries": 21, "fetched_at": "2026-08-28T12:00:00Z"}
            raise SnapshotNotFoundError()
    _cache.clear()
    result = build_context(Repo(), 58005, 10)
    assert [r["points_gap"] for r in result["history"]] == [7, None, 7]
    assert "probability" not in result


def test_sampled_archive_and_missing_timestamp_never_create_history():
    class Repo:
        def live_league(self, league):
            return {"gameweek": 2, "expected_count": 21, "managers": managers(), "captured_at": "2026-09-04T15:00:00Z"}
        def bootstrap(self):
            return {"events": [{"id": 1, "finished": True, "data_checked": True}]}
        def league(self, league, gw):
            return {"competitors": managers(5), "total_entries": 5, "population_size": 21,
                    "sampled": True, "fetched_at": None}
    _cache.clear()
    result = build_context(Repo(), 58005, 10)
    assert result["history"] == [
        {"gameweek": 1, "points_gap": None, "snapshot_at": None},
        {"gameweek": 2, "points_gap": 7, "snapshot_at": "2026-09-04T15:00:00Z"},
    ]
