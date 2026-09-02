from __future__ import annotations

from collections import Counter
import random

from app.validation import SQUAD_SHAPE, validate_manager_squad


LEGAL_FORMATIONS = ((3, 4, 3), (3, 5, 2), (4, 4, 2), (4, 3, 3),
                    (4, 5, 1), (5, 3, 2), (5, 4, 1), (5, 2, 3))


def manager_for_formation(formation: tuple[int, int, int], *, bench_boost: bool = False) -> dict:
    defenders, midfielders, forwards = formation
    starters = ["GKP"] + ["DEF"] * defenders + ["MID"] * midfielders + ["FWD"] * forwards
    remaining = Counter(SQUAD_SHAPE)
    for position in starters:
        remaining[position] -= 1
    positions = starters + [position for position, count in remaining.items() for _ in range(count)]
    squad = [
        {
            "element": index + 1,
            "position": position,
            "multiplier": 1 if bench_boost or index < 11 else 0,
            "is_captain": index == 1,
            "is_vice_captain": index == 2,
        }
        for index, position in enumerate(positions)
    ]
    return {"active_chip": "bboost" if bench_boost else None, "squad": squad}


def test_every_legal_formation_passes_with_and_without_bench_boost() -> None:
    for formation in LEGAL_FORMATIONS:
        assert validate_manager_squad(manager_for_formation(formation)) == []
        assert validate_manager_squad(manager_for_formation(formation, bench_boost=True)) == []


def test_random_mutations_of_critical_rules_are_rejected() -> None:
    generator = random.Random(58005)
    for _ in range(1_000):
        manager = manager_for_formation(generator.choice(LEGAL_FORMATIONS))
        mutation = generator.choice(("captain", "vice", "lineup", "shape", "scoring"))
        if mutation == "captain":
            manager["squad"][1]["is_captain"] = False
        elif mutation == "vice":
            manager["squad"][2]["is_vice_captain"] = False
        elif mutation == "lineup":
            manager["squad"][0]["position"] = "MID"
        elif mutation == "shape":
            manager["squad"].pop()
        else:
            manager["squad"][10]["multiplier"] = 0
        assert validate_manager_squad(manager), mutation

