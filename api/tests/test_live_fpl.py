from app import live_fpl


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
        return {"picks": [{"element": 9, "position": position, "multiplier": 1, "is_captain": position == 1, "is_vice_captain": position == 2} for position in range(1, 16)]}

    monkeypatch.setattr(live_fpl, "_get", fake_get)

    assert live_fpl.hydrate_manager_squads(rows, 2, 1) == 1
    squad = rows[0]["_live_squad"]
    assert sum(pick["multiplier"] > 0 for pick in squad) == 11
    assert squad[0]["multiplier"] == 2
    assert all(pick["multiplier"] == 0 for pick in squad[11:])
