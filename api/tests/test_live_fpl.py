from app import live_fpl


def _team_fixture_get(active_chip, chips):
    def fake_get(path: str, ttl: int = 30) -> dict:
        if path == "bootstrap-static/":
            return {
                "elements": [{"id": 9, "web_name": "P", "element_type": 3, "team": 1, "now_cost": 50, "event_points": 4}],
                "teams": [{"id": 1, "name": "Team"}],
                "events": [{"id": 3, "finished": False}],
            }
        if path == "entry/77/":
            return {"name": "T", "player_first_name": "A", "player_last_name": "B", "leagues": {"classic": []}}
        if path == "entry/77/event/3/picks/":
            return {"active_chip": active_chip, "picks": [
                {"element": 9, "position": 1, "multiplier": 2, "is_captain": True, "is_vice_captain": False},
            ]}
        if path == "entry/77/history/":
            return {"current": [{"event": 3, "event_total": 8, "total_points": 20, "overall_rank": 1}], "chips": chips}
        if path == "fixtures/?event=3":
            return []
        raise AssertionError(path)
    return fake_get


def test_team_surfaces_the_active_chip_and_played_chips(monkeypatch) -> None:
    monkeypatch.setattr(live_fpl, "_get", _team_fixture_get(
        "wildcard", [{"name": "wildcard", "time": "t", "event": 3}]))
    result = live_fpl.team(77, 3)
    assert result["active_chip"] == "wildcard"
    assert result["chips_played"] == [{"name": "wildcard", "event": 3}]


def test_team_active_chip_is_none_before_the_deadline(monkeypatch) -> None:
    monkeypatch.setattr(live_fpl, "_get", _team_fixture_get(None, []))
    result = live_fpl.team(77, 3)
    assert result["active_chip"] is None
    assert result["chips_played"] == []


def test_league_standings_fetches_every_official_page_and_deduplicates(monkeypatch) -> None:
    pages = {
        1: {"standings": {"has_next": True, "results": [{"entry": 1}, {"entry": 2}]}},
        2: {"standings": {"has_next": True, "results": [{"entry": 2}, {"entry": 3}]}},
        3: {"standings": {"has_next": False, "results": [{"entry": 4}]}},
    }

    def fake_get(path: str, ttl: int = 30) -> dict:
        assert ttl == 60
        page = int(path.split("page_standings=")[1].split("&", 1)[0])
        return pages[page]

    monkeypatch.setattr(live_fpl, "_get", fake_get)
    result = live_fpl.league_standings(58005)

    assert result["count"] == 4
    assert result["pages_fetched"] == 3
    assert [row["entry"] for row in result["managers"]] == [1, 2, 3, 4]


def test_hydrated_squad_uses_pick_selection_order_for_the_starting_xi(monkeypatch) -> None:
    rows = [{"entry": 1}]

    def fake_get(path: str, ttl: int = 30) -> dict:
        if path == "bootstrap-static/":
            return {"elements": [{"id": 9, "web_name": "Player", "element_type": 3, "team": 1, "now_cost": 50}], "teams": [{"id": 1, "name": "Team"}]}
        assert path == "entry/1/event/2/picks/"
        return {
            "picks": [{"element": 9, "position": position, "multiplier": 1, "is_captain": position == 1, "is_vice_captain": position == 2} for position in range(1, 16)],
            "entry_history": {"bank": 7, "overall_rank": 812345, "event_transfers": 2},
        }

    monkeypatch.setattr(live_fpl, "_get", fake_get)

    assert live_fpl.hydrate_manager_squads(rows, 2, 1) == 1
    squad = rows[0]["_live_squad"]
    assert sum(pick["multiplier"] > 0 for pick in squad) == 11
    assert squad[0]["multiplier"] == 2
    assert all(pick["multiplier"] == 0 for pick in squad[11:])
    # The same picks payload also yields bank / official overall rank / GW transfers.
    assert rows[0]["_live_bank"] == 7
    assert rows[0]["_live_overall_rank"] == 812345
    assert rows[0]["_live_event_transfers"] == 2


def test_hydrate_tolerates_a_picks_payload_with_no_entry_history(monkeypatch) -> None:
    rows = [{"entry": 1}]

    def fake_get(path: str, ttl: int = 30) -> dict:
        if path == "bootstrap-static/":
            return {"elements": [{"id": 9, "web_name": "P", "element_type": 3, "team": 1, "now_cost": 50}], "teams": [{"id": 1, "name": "T"}]}
        return {"picks": [{"element": 9, "position": p, "multiplier": 1, "is_captain": p == 1, "is_vice_captain": p == 2} for p in range(1, 16)]}

    monkeypatch.setattr(live_fpl, "_get", fake_get)
    assert live_fpl.hydrate_manager_squads(rows, 2, 1) == 1
    assert rows[0]["_live_bank"] is None
    assert rows[0]["_live_overall_rank"] is None
