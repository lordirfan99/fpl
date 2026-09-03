"""solve_squad must optimise the STARTING XI + captain, not the sum of all 15.

Regression: the old objective (max sum of 15 xPts) would spend real money on a
premium player it then benched - e.g. a £9.0m striker as the 3rd forward in a
5-4-1. The bench only scores via autosub, so it is now weighted at BENCH_WEIGHT.
"""
import pytest

pytest.importorskip("pulp")  # CBC not installed in the lightweight local venv

from squad_solver import solve_lineup, solve_squad  # noqa: E402


def _p(pid, pos, cost, xpts, club=None):
    return {"id": pid, "name": f"{pos}{pid}", "position": pos,
            "cost": cost, "xpts": xpts, "club": club if club is not None else pid}


def _pool():
    players = []
    # keepers
    players += [_p(1, "GKP", 50, 4.0), _p(2, "GKP", 40, 2.5), _p(3, "GKP", 45, 3.0)]
    # five elite cheap defenders (the XI wants all of them) + fodder defenders
    players += [_p(10 + i, "DEF", 50, 7.0) for i in range(5)]
    players += [_p(20 + i, "DEF", 40, 3.0) for i in range(4)]
    # a genuine starter upgrade the surplus *should* go to
    players.append(_p(30, "DEF", 90, 9.0))
    # midfield: five real starters + fodder
    players += [_p(40, "MID", 100, 9.0), _p(41, "MID", 90, 8.0),
                _p(42, "MID", 60, 6.0), _p(43, "MID", 60, 6.0), _p(44, "MID", 60, 6.0)]
    players += [_p(50 + i, "MID", 45, 3.5) for i in range(4)]
    # forwards: one real starter, one "premium bench trap", plenty of fodder
    players.append(_p(60, "FWD", 90, 8.0))          # starts
    players.append(_p(61, "FWD", 90, 4.5))          # TRAP - only ever a bench body
    players += [_p(70 + i, "FWD", 40, 1.0) for i in range(4)]
    return players


TRAP_ID = 61
UPGRADE_ID = 30


def test_does_not_buy_a_premium_player_just_to_bench_it():
    # Budget lets the solver afford the real DEF upgrade but NOT also the trap.
    squad = solve_squad(_pool(), budget=940)
    ids = {p["id"] for p in squad}

    assert len(squad) == 15
    assert TRAP_ID not in ids, "solver spent £9.0m on a forward it will bench"
    assert UPGRADE_ID in ids, "surplus should upgrade a STARTER, not the bench"

    starters, bench = solve_lineup(squad)
    assert len(starters) == 11 and len(bench) == 4
    assert UPGRADE_ID in {p["id"] for p in starters}
    # the two non-starting forwards are cheap fodder, not premium
    bench_fwd = [p for p in bench if p["position"] == "FWD"]
    assert bench_fwd and all(p["cost"] <= 40 for p in bench_fwd)


def test_returns_a_legal_15_and_respects_budget():
    budget = 1000
    squad = solve_squad(_pool(), budget=budget)
    assert len(squad) == 15
    counts = {}
    for p in squad:
        counts[p["position"]] = counts.get(p["position"], 0) + 1
    assert counts == {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
    assert sum(p["cost"] for p in squad) <= budget
    clubs = {}
    for p in squad:
        clubs[p["club"]] = clubs.get(p["club"], 0) + 1
    assert max(clubs.values()) <= 3

    starters, bench = solve_lineup(squad)
    assert len(starters) == 11 and len(bench) == 4
    sc = {}
    for p in starters:
        sc[p["position"]] = sc.get(p["position"], 0) + 1
    assert sc["GKP"] == 1 and sc["DEF"] >= 3 and sc["MID"] >= 2 and sc["FWD"] >= 1


def test_bench_weight_zero_still_produces_a_legal_squad():
    squad = solve_squad(_pool(), budget=1000, bench_weight=0.0)
    assert len(squad) == 15
    starters, bench = solve_lineup(squad)
    assert len(starters) == 11 and len(bench) == 4
