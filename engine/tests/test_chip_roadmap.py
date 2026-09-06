from chip_roadmap import season_chip_plan


def _fixtures():
    rows = []
    # GW3: normal (10 fixtures, all 20 teams once)
    for i in range(10):
        rows.append({"event": 3, "team_h": 2 * i + 1, "team_a": 2 * i + 2})
    # GW4: double for teams 1-8 (extra fixtures among themselves)
    for i in range(10):
        rows.append({"event": 4, "team_h": 2 * i + 1, "team_a": 2 * i + 2})
    rows += [{"event": 4, "team_h": 1, "team_a": 3}, {"event": 4, "team_h": 5, "team_a": 7},
             {"event": 4, "team_h": 2, "team_a": 4}, {"event": 4, "team_h": 6, "team_a": 8}]
    # GW5: blank for teams 1-6 (only teams 7-20 play)
    for i in range(7):
        rows.append({"event": 5, "team_h": 7 + 2 * i, "team_a": 8 + 2 * i})
    return rows


def _plan(chips, squad_teams=None):
    return {entry["chip"]: entry for entry in season_chip_plan(
        _fixtures(), 3, available_chips=chips, squad_team_ids=squad_teams, n_teams=20, last_gw=5)}


def test_bench_boost_and_triple_captain_target_the_biggest_double():
    plan = _plan(["bboost", "3xc"])
    assert plan["bboost"]["target_gw"] == 4
    assert plan["3xc"]["target_gw"] == 4
    assert "double gameweek" in plan["bboost"]["reason"]


def test_free_hit_targets_the_biggest_blank():
    plan = _plan(["freehit"])
    assert plan["freehit"]["target_gw"] == 5
    assert "blank" in plan["freehit"]["reason"].lower()


def test_wildcard_points_at_the_deadline_before_the_double():
    plan = _plan(["wildcard"])
    assert plan["wildcard"]["target_gw"] == 3  # GW4 double -> GW3 deadline
    assert plan["wildcard"]["confidence"] == "low"


def test_squad_overlap_raises_confidence_for_bench_boost():
    low = _plan(["bboost"], squad_teams=[19, 20])["bboost"]
    high = _plan(["bboost"], squad_teams=[1, 2, 3, 4, 5])["bboost"]
    assert low["confidence"] == "low"
    assert high["confidence"] == "medium"
    assert "in your squad" in high["reason"]


def test_no_swing_in_horizon_yields_an_honest_hold():
    flat = [{"event": 3, "team_h": 2 * i + 1, "team_a": 2 * i + 2} for i in range(10)]
    plan = {e["chip"]: e for e in season_chip_plan(flat, 3, available_chips=["bboost", "freehit"], last_gw=3)}
    assert plan["bboost"]["target_gw"] is None
    assert plan["freehit"]["target_gw"] is None


def test_entries_are_ordered_by_chip_priority_and_carry_no_extra_keys():
    plan = season_chip_plan(_fixtures(), 3, available_chips=["3xc", "wildcard", "bboost", "freehit"], last_gw=5)
    assert [entry["chip"] for entry in plan] == ["wildcard", "freehit", "bboost", "3xc"]
    for entry in plan:
        assert set(entry) == {"chip", "chip_label", "target_gw", "confidence", "reason"}
