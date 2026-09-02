from app.multiweek_optimizer import MultiWeekContext, optimize_multiweek_transfers
from app.projection_types import ExpectedMinutes, PlayerProjection


def player(element: int, team: str, position: str, xpts: float, spread: float = 2) -> PlayerProjection:
    return PlayerProjection(
        element, f"P{element}", team, position, xpts, max(0, xpts - spread), xpts, xpts + spread,
        .3, .1, ExpectedMinutes(.8, .1, 80, 20, 66, .7), {}, "test",
    )


def squad_and_candidates():
    positions = ["GKP", "GKP"] + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 3
    squad = {index + 1: player(index + 1, f"T{(index % 8) + 1}", position, 3) for index, position in enumerate(positions)}
    candidates = {
        20: player(20, "T9", "MID", 8),
        21: player(21, "T10", "FWD", 7),
        22: player(22, "T11", "DEF", 6),
    }
    return squad, candidates


def test_multiweek_optimizer_prices_hits_and_free_transfer_value() -> None:
    squad, candidates = squad_and_candidates()
    players = {**squad, **candidates}
    prices = {element: 5.0 for element in players}
    horizon = [players, players]
    result = optimize_multiweek_transfers(
        squad, players, prices, horizon,
        MultiWeekContext(bank=0, free_transfers=1, weights=(1, .8), max_transfers=2),
    )
    assert result["status"] == "research_only"
    assert result["plans"][0]["net_ev"] > 0
    assert result["plans"][0]["budget_ok"] is True
    two_move = next(plan for plan in result["plans"] if plan["transfer_count"] == 2)
    assert two_move["hit_cost"] == 4
    assert two_move["free_transfer_opportunity_cost"] == 1.5


def test_chip_modes_are_explicit_and_do_not_bypass_transfer_rules() -> None:
    squad, candidates = squad_and_candidates()
    players = {**squad, **candidates}
    for chip in ("freehit", "wildcard"):
        # Add enough legal alternatives for full-squad chip optimization.
        expanded = dict(players)
        next_id = 100
        for position, count in (("GKP", 5), ("DEF", 10), ("MID", 10), ("FWD", 7)):
            for index in range(count):
                expanded[next_id] = player(next_id, f"C{index % 12}", position, 4 + index / 10)
                next_id += 1
        result = optimize_multiweek_transfers(
            squad, expanded, {element: 5 for element in expanded}, [expanded, expanded],
            MultiWeekContext(bank=0, free_transfers=1, weights=(1, .8), active_chip=chip),
        )
        assert result["status"] == "research_only"
        assert result["plans"][0]["plan_type"] == "full_squad_chip"
        assert len(result["plans"][0]["squad"]) == 15
        assert len(result["plans"][0]["lineup"]) == 11
        assert result["plans"][0]["hit_cost"] == 0
    for chip in ("bboost", "3xc"):
        result = optimize_multiweek_transfers(
            squad, players, {element: 5 for element in players}, [players, players],
            MultiWeekContext(bank=0, free_transfers=1, weights=(1, .8), active_chip=chip),
        )
        assert result["status"] == "research_only"
        assert result["plans"]


def test_chip_squad_respects_official_availability_allowlist() -> None:
    squad, candidates = squad_and_candidates()
    players = {**squad, **candidates}
    next_id = 100
    for position, count in (("GKP", 5), ("DEF", 10), ("MID", 10), ("FWD", 7)):
        for index in range(count):
            # The highest-projected midfielder is deliberately unavailable.
            players[next_id] = player(next_id, f"C{index % 12}", position, 50 if next_id == 110 else 4 + index / 10)
            next_id += 1
    eligible = frozenset(element for element in players if element != 110)
    result = optimize_multiweek_transfers(
        squad, players, {element: 5 for element in players}, [players, players],
        MultiWeekContext(bank=0, free_transfers=1, weights=(1, .8), active_chip="wildcard", eligible_player_ids=eligible),
    )
    assert 110 not in result["plans"][0]["squad"]
