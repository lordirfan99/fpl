"""Small invariants for the archived transfer replay."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parents[1] / "jobs"))
from run_transfer_replay import Holding, legal_squad, sale_value  # noqa: E402


def test_sale_value_uses_half_gain_floor():
    assert sale_value(Holding(price=50, purchase=50), 51) == 50
    assert sale_value(Holding(price=50, purchase=50), 53) == 51
    assert sale_value(Holding(price=50, purchase=52), 49) == 52


def test_legal_squad_requires_fpl_shape_and_three_club_limit():
    catalog = {}
    ids = []
    for pos, count in ((1, 2), (2, 5), (3, 5), (4, 3)):
        for _ in range(count):
            i = len(ids) + 1
            ids.append(i)
            catalog[i] = {"element_type": pos, "team": i}
    for i, row in catalog.items():
        row["team"] = 1 if i <= 4 else 2
    assert not legal_squad(ids, catalog)
    for i, row in catalog.items():
        row["team"] = (i - 1) // 3 + 1
    assert legal_squad(ids, catalog)
