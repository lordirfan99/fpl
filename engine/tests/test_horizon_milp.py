import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "optimizer"))

from horizon_milp import optimize_horizon  # noqa: E402


def player(pid, position, club, cost, values, start=0.9, minutes=80):
    return {
        "id": pid, "name": f"P{pid}", "position": position, "club": club,
        "cost": cost, "selling_price": cost, "xpts": values[0],
        "xpts_by_gw": list(values), "variance_by_gw": [1.0, 1.0, 1.0],
        "p_start": start, "expected_minutes": minutes,
    }


def legal_squad():
    positions = ["GKP"] * 2 + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 3
    return [player(index + 1, position, (index % 8) + 1, 50, [3.0, 3.0, 3.0])
            for index, position in enumerate(positions)]


class HorizonMilpTests(unittest.TestCase):
    def test_rolls_to_two_free_transfers_and_keeps_legal_shapes(self):
        squad = legal_squad()
        result = optimize_horizon(squad, squad, bank=0, free_transfers=1)
        self.assertEqual(result["optimizer_version"], "v4.1")
        self.assertEqual(result["weeks"][0]["transfer_count"], 0)
        self.assertEqual(result["weeks"][0]["free_transfers_after"], 2)
        self.assertEqual(len(result["weeks"][0]["lineup_ids"]), 11)
        self.assertIn(result["weeks"][0]["formation"], {
            "3-4-3", "3-5-2", "4-4-2", "4-3-3", "4-5-1", "5-3-2", "5-4-1", "5-2-3",
        })

    def test_funding_pair_is_jointly_selected(self):
        squad = legal_squad()
        squad[2].update(cost=80, selling_price=80, xpts_by_gw=[2, 2, 2])
        squad[7].update(cost=60, selling_price=60, xpts_by_gw=[1, 1, 1])
        value_def = player(101, "DEF", 10, 50, [3, 3, 3])
        star_mid = player(102, "MID", 11, 90, [8, 8, 8])
        result = optimize_horizon(squad, squad + [value_def, star_mid], bank=0,
                                  free_transfers=2, transfer_friction=0)
        first_ins = {move["element_in"] for move in result["weeks"][0]["transfers"]}
        self.assertEqual(first_ins, {101, 102})
        self.assertEqual(result["weeks"][0]["bank_after"], 0)

    def test_paid_transfer_lock_is_enforced(self):
        squad = legal_squad()
        squad[7].update(xpts_by_gw=[0, 0, 0])
        star = player(103, "MID", 12, 50, [10, 10, 10])
        result = optimize_horizon(squad, squad + [star], bank=0, free_transfers=0,
                                  paid_transfers_allowed=False)
        self.assertEqual(result["weeks"][0]["transfer_count"], 0)
        self.assertEqual(result["weeks"][0]["hits"], 0)

    def test_captain_minutes_gate_blocks_risky_high_mean(self):
        squad = legal_squad()
        risky = squad[7]
        risky.update(xpts_by_gw=[12, 12, 12], p_start=0.6, expected_minutes=55)
        result = optimize_horizon(squad, squad, bank=0, free_transfers=0,
                                  paid_transfers_allowed=False)
        self.assertNotEqual(result["weeks"][0]["captain_id"], risky["id"])

    def test_captain_uses_same_ceiling_and_role_logic_as_card(self):
        squad = legal_squad()
        goalkeeper = next(player for player in squad if player["position"] == "GKP")
        midfielder = next(player for player in squad if player["position"] == "MID")
        goalkeeper.update(xpts_by_gw=[5.0, 5.0, 5.0], variance_by_gw=[1.0] * 3)
        midfielder.update(xpts_by_gw=[4.9, 4.9, 4.9], variance_by_gw=[9.0] * 3)
        result = optimize_horizon(squad, squad, bank=0, free_transfers=0,
                                  paid_transfers_allowed=False)
        self.assertEqual(result["weeks"][0]["captain_id"], midfielder["id"])


def defender_heavy_squad():
    """A squad whose best XI on pure xPts is 5-4-1."""
    squad = legal_squad()
    for player_row in squad:
        if player_row["position"] == "DEF":
            player_row.update(xpts_by_gw=[6.0, 6.0, 6.0])
        elif player_row["position"] == "MID":
            player_row.update(xpts_by_gw=[5.0, 5.0, 5.0])
    forwards = [p for p in squad if p["position"] == "FWD"]
    forwards[0].update(xpts_by_gw=[5.5, 5.5, 5.5])
    forwards[1].update(xpts_by_gw=[1.0, 1.0, 1.0])
    forwards[2].update(xpts_by_gw=[1.0, 1.0, 1.0])
    return squad


class LineupMaxTests(unittest.TestCase):
    def test_default_allows_five_defenders(self):
        squad = defender_heavy_squad()
        result = optimize_horizon(squad, squad, bank=0, free_transfers=0,
                                  paid_transfers_allowed=False)
        self.assertEqual(result["weeks"][0]["formation"], "5-4-1")

    def test_cap_limits_starting_defenders_on_the_normal_path(self):
        """The regression: v4_max_starting_defenders never reached the MILP."""
        squad = defender_heavy_squad()
        result = optimize_horizon(squad, squad, bank=0, free_transfers=0,
                                  paid_transfers_allowed=False,
                                  lineup_max={"DEF": 3})
        defenders = sum(1 for pid in result["weeks"][0]["lineup_ids"]
                        if next(p for p in squad if p["id"] == pid)["position"] == "DEF")
        self.assertEqual(defenders, 3)
        self.assertEqual(len(result["weeks"][0]["lineup_ids"]), 11)

    def test_cap_is_clamped_into_the_legal_band(self):
        """Out-of-range caps must clamp, not produce an illegal or infeasible XI."""
        squad = defender_heavy_squad()
        for requested in (0, 1, 9):
            with self.subTest(requested=requested):
                result = optimize_horizon(squad, squad, bank=0, free_transfers=0,
                                          paid_transfers_allowed=False,
                                          lineup_max={"DEF": requested})
                self.assertIn(result["status"], {"Optimal", "Integer Feasible"})
                defenders = sum(
                    1 for pid in result["weeks"][0]["lineup_ids"]
                    if next(p for p in squad if p["id"] == pid)["position"] == "DEF")
                self.assertGreaterEqual(defenders, 3)
                self.assertLessEqual(defenders, 5)

    def test_unknown_position_keys_are_ignored(self):
        squad = defender_heavy_squad()
        result = optimize_horizon(squad, squad, bank=0, free_transfers=0,
                                  paid_transfers_allowed=False,
                                  lineup_max={"BENCH": 2, "DEF": 4})
        self.assertIn(result["status"], {"Optimal", "Integer Feasible"})
        self.assertEqual(len(result["weeks"][0]["lineup_ids"]), 11)


if __name__ == "__main__":
    unittest.main()
