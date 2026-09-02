import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "optimizer"))
sys.path.insert(0, os.path.join(BASE, "model"))
sys.path.insert(0, os.path.join(BASE, "bot"))

from decision_explanation import build_decision_summary, formation  # noqa: E402
from squad_solver import solve_lineup  # noqa: E402
from templates import plan_card  # noqa: E402


def player(pid, position, value, cost=50):
    return {
        "id": pid, "name": f"P{pid}", "position": position, "club": (pid % 8) + 1,
        "cost": cost, "selling_price": cost, "xpts": value,
        "xpts_horizon": value * 2.2,
        "xpts_by_gw": [value, value * 0.9, value * 0.8],
        "variance_by_gw": [1.0, 1.0, 1.0], "xpts_variance": 1.0,
    }


class DecisionExplanationTests(unittest.TestCase):
    def setUp(self):
        positions = ["GKP"] * 2 + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 3
        self.squad = [player(i + 1, pos, 5.0 - i * 0.1) for i, pos in enumerate(positions)]
        self.starters, self.bench = solve_lineup(self.squad)
        self.plan = {
            "gw": 2, "transfers": [], "target_starters": self.starters,
            "bench": self.bench, "captain": self.starters[0], "vice": self.starters[1],
            "current_xpts": 60, "target_xpts": 60, "horizon_gain": 0,
            "deadline": "2026-08-28T17:30:00Z", "model_version": "competitive-v4.0",
            "competitive": {
                "context_status": "ready", "template_formation": "3-4-3",
                "elite_template": [
                    {"element": self.squad[0]["id"], "name": self.squad[0]["name"],
                     "position": "GKP", "cost": 5.0, "elite_percentage": 80},
                    {"element": 99, "name": "Elite FWD", "position": "FWD",
                     "cost": 6.0, "elite_percentage": 75},
                ],
                "meta": {"freshness_hours": 0.5},
            },
        }

    def test_hold_reason_is_legal_constraint_not_false_optimality(self):
        candidates = self.squad + [player(99, "FWD", 6.5, cost=60)]
        summary = build_decision_summary(
            self.plan, squad=self.squad, final_squad=self.squad,
            candidates=candidates, starters=self.starters, gw_ids=[2, 3, 4],
            bank=10, free_transfers=0, paid_transfers_calibrated=False,
            paid_transfer_min_gws=3, calibration={"n": 561, "rmse": 2.7},
            generated_at="2026-08-27T05:00:00+00:00",
            deadline="2026-08-28T17:30:00Z",
            solver_settings={"risk_penalty": 0.25, "bench_weight": 0.08,
                             "approval_cutoff_minutes": 30},
        )
        self.assertEqual(summary["recommended_action"], "HOLD")
        self.assertIn("0 free transfers", summary["reason"])
        self.assertNotIn("optimal", summary["reason"].lower())
        self.assertEqual(len(summary["horizon"]["rows"]), 3)
        self.assertIn("no transfer", summary["approval_scope"].lower())
        self.assertEqual(summary["data_health"]["deadline_safety"], "open")
        self.assertEqual(summary["template_comparison"]["missing"][0]["name"], "Elite FWD")

    def test_formation_is_calculated_from_selected_xi(self):
        counts = {pos: sum(p["position"] == pos for p in self.starters)
                  for pos in ("DEF", "MID", "FWD")}
        self.assertEqual(formation(self.starters), f"{counts['DEF']}-{counts['MID']}-{counts['FWD']}")

    def test_plan_card_exposes_scope_and_never_exceeds_telegram_limit(self):
        self.plan["decision_summary"] = {
            "recommended_action": "HOLD",
            "reason": "You have 0 free transfers; paid moves are locked.",
            "approval_scope": "Approval changes only the XI, captain and vice-captain; no transfer will be made.",
            "formation": {"selected": "5-4-1", "template": "3-4-3",
                          "explanation": "Immediate xPts and template destination differ."},
            "horizon": {"rows": [
                {"gw": 2, "current": 40, "proposed": 40, "gain": 0},
                {"gw": 3, "current": 42, "proposed": 42, "gain": 0},
                {"gw": 4, "current": 44, "proposed": 44, "gain": 0},
            ]},
            "uncertainty": {"mean_with_captain": 45, "outcome_low": 32,
                            "outcome_high": 58, "calibration": {"n": 561}},
            "roadmap": [{"gw": 2, "action": "HOLD", "route": None, "status": "recommended"}],
            "data_health": {"account_squad_synced": True, "free_transfers_synced": True,
                            "free_transfers": 0, "league_snapshot_age_hours": 0.5},
        }
        card = plan_card(self.plan)
        self.assertIn("RECOMMENDED ACTION: HOLD", card)
        self.assertNotIn("squad already optimal", card)
        self.assertIn("Approval changes only", card)
        self.assertLessEqual(len(card), 4096)

    def test_bench_has_goalkeeper_then_outfield_projection_order(self):
        _, bench = solve_lineup(self.squad)
        self.assertEqual(bench[0]["position"], "GKP")
        outfield = bench[1:]
        self.assertEqual(outfield, sorted(outfield, key=lambda p: -p["xpts"]))


if __name__ == "__main__":
    unittest.main()
