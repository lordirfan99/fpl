"""FPL Autopilot - v3 auto-promotion + model version display tests.

Covers:
  - engine_promotion.evaluate_gates: pass / fail / no-data paths
  - engine_promotion.load_shadow_gw parsing
  - engine_promotion.main() end-to-end: pre-season progress (0/3) and
    promotion after 3 evaluated GWs when v3 wins the comparison
  - pre_deadline_run.resolve_engine: v3 if promoted, else v2/v1 by odds
  - templates.plan_card shows model version + v3 captain uncertainty
  - telegram_bot.model_version_line shows live engine + shadow progress

Run: .venv/Scripts/python.exe -m unittest discover -s tests -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "jobs"))
sys.path.insert(0, os.path.join(BASE, "execution"))
sys.path.insert(0, os.path.join(BASE, "bot"))

import engine_promotion  # noqa: E402
import pre_deadline_run  # noqa: E402
import templates  # noqa: E402
import telegram_bot  # noqa: E402


def _shadow_gw(gw, players, captain_id, squad_ids):
    """players: {id: xpts}"""
    return {
        "model": "v3-shadow", "gw": gw,
        "squad": [{"id": i, "xpts": x} for i, x in players.items() if i in squad_ids],
        "top_candidates": [{"id": i, "xpts": x} for i, x in players.items()],
        "captain": {"id": captain_id},
    }


def _plan_gw(gw, captain_id, squad_ids):
    return {"gw": gw, "captain": {"id": captain_id},
            "target_starters": [{"id": i} for i in list(squad_ids)[:11]],
            "bench": [{"id": i} for i in list(squad_ids)[11:]]}


def _residuals(gw, rows):
    """rows: {element: (v2_pred, actual)}"""
    out = []
    for el, (p, a) in rows.items():
        out.append({"gw": gw, "element": el, "name": f"P{el}", "pos": "MID",
                    "predicted": p, "actual": a, "minutes": 90})
    return out


class TestEvaluateGates(unittest.TestCase):
    def test_passes_when_v3_better(self):
        rows = [{"v2_pred": 5.0, "v3_pred": 2.0, "actual": 2.0},
                {"v2_pred": 4.0, "v3_pred": 6.0, "actual": 6.0},
                {"v2_pred": 3.0, "v3_pred": 1.0, "actual": 1.0},
                {"v2_pred": 8.0, "v3_pred": 7.0, "actual": 7.0}]
        passed, report = engine_promotion.evaluate_gates(
            rows, (10, 20), (100, 120), {"v3_mae_tolerance": 0.05})
        self.assertTrue(passed)
        self.assertLess(report["mae_v3"], report["mae_v2"])
        self.assertIn("captain", report["improved"])

    def test_fails_when_v3_mae_materially_worse(self):
        rows = [{"v2_pred": 2.0, "v3_pred": 8.0, "actual": 2.0},
                {"v2_pred": 2.0, "v3_pred": 8.0, "actual": 2.0},
                {"v2_pred": 2.0, "v3_pred": 8.0, "actual": 2.0},
                {"v2_pred": 2.0, "v3_pred": 8.0, "actual": 2.0}]
        passed, report = engine_promotion.evaluate_gates(
            rows, (10, 20), (100, 120), {"v3_mae_tolerance": 0.05})
        self.assertFalse(passed)
        self.assertIn("MAE", report["reason"])

    def test_fails_when_no_improved_metric(self):
        # v3 matches v2 MAE but improves nothing
        rows = [{"v2_pred": 3.0, "v3_pred": 3.0, "actual": 3.0},
                {"v2_pred": 4.0, "v3_pred": 4.0, "actual": 4.0},
                {"v2_pred": 5.0, "v3_pred": 5.0, "actual": 5.0},
                {"v2_pred": 2.0, "v3_pred": 2.0, "actual": 2.0}]
        passed, report = engine_promotion.evaluate_gates(
            rows, (10, 10), (100, 100), {"v3_mae_tolerance": 0.05})
        self.assertFalse(passed)
        self.assertIn("NO improved metric", report["reason"])

    def test_no_rows_fails_safely(self):
        passed, report = engine_promotion.evaluate_gates([], (0, 0), (0, 0), {})
        self.assertFalse(passed)


class TestMainPromotion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fpl_promo_")
        self._orig_proc = engine_promotion.PROCESSED
        self._orig_state = engine_promotion.ENGINE_STATE_FILE
        self._orig_settings = engine_promotion.SETTINGS_FILE
        engine_promotion.PROCESSED = self.tmp
        engine_promotion.ENGINE_STATE_FILE = os.path.join(self.tmp, "engine_state.json")
        sfile = os.path.join(self.tmp, "settings.json")
        with open(sfile, "w") as f:
            json.dump({"v3_auto_promote": True, "v3_promotion_min_gws": 3,
                       "v3_mae_tolerance": 0.05}, f)
        engine_promotion.SETTINGS_FILE = sfile

    def tearDown(self):
        engine_promotion.PROCESSED = self._orig_proc
        engine_promotion.ENGINE_STATE_FILE = self._orig_state
        engine_promotion.SETTINGS_FILE = self._orig_settings
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_gw_data(self, gw, squad, captain_v2, captain_v3, rows):
        # rows: {element: (v2_pred, actual)}; v3 preds come from shadow xpts
        with open(os.path.join(self.tmp, f"plan_gw{gw}.json"), "w") as f:
            json.dump(_plan_gw(gw, captain_v2, set(squad)), f)
        with open(os.path.join(self.tmp, f"v3_shadow_gw{gw}.json"), "w") as f:
            json.dump(_shadow_gw(gw, {i: rows[i][1] for i in rows}, captain_v3, set(squad)), f)
        return _residuals(gw, rows)

    def test_preseason_progress_zero_of_three(self):
        # shadow file exists but no residuals -> not evaluable -> 0/3 progress
        with open(os.path.join(self.tmp, "v3_shadow_gw1.json"), "w") as f:
            json.dump(_shadow_gw(1, {1: 5.0}, 1, {1}), f)
        engine_promotion.main()
        state = json.load(open(engine_promotion.ENGINE_STATE_FILE))
        self.assertFalse(state["promoted"])
        self.assertEqual(state["shadow_evaluated_gws"], 0)

    def test_promotes_after_three_gws_when_v3_wins(self):
        # 3 GWs: v3 predicts actual exactly (MAE 0), v2 is off; v3 captain
        # picks the high scorer (id 100), v2 captain the low scorer (id 1)
        all_rows = []
        for gw in (1, 2, 3):
            rows = {1: (8.0, 2.0), 2: (2.0, 5.0), 100: (2.0, 9.0)}
            all_rows += self._write_gw_data(gw, [1, 2, 100], captain_v2=1,
                                            captain_v3=100, rows=rows)
        with open(os.path.join(self.tmp, "residuals.csv"), "w") as f:
            f.write("gw,element,name,pos,predicted,actual,minutes\n")
            for r in all_rows:
                f.write(f"{r['gw']},{r['element']},{r['name']},{r['pos']},"
                        f"{r['predicted']},{r['actual']},{r['minutes']}\n")
        engine_promotion.main()
        state = json.load(open(engine_promotion.ENGINE_STATE_FILE))
        self.assertTrue(state["promoted"], f"v3 should promote: {state.get('report')}")
        self.assertEqual(state["shadow_evaluated_gws"], 3)
        self.assertEqual(state["report"]["evaluated_gws"], [1, 2, 3])

    def test_does_not_promote_when_v3_worse(self):
        all_rows = []
        for gw in (1, 2, 3):
            rows = {1: (2.0, 2.0), 2: (2.0, 2.0), 100: (2.0, 2.0)}
            all_rows += self._write_gw_data(gw, [1, 2, 100], captain_v2=1,
                                            captain_v3=100, rows=rows)
        with open(os.path.join(self.tmp, "residuals.csv"), "w") as f:
            f.write("gw,element,name,pos,predicted,actual,minutes\n")
            for r in all_rows:
                f.write(f"{r['gw']},{r['element']},{r['name']},{r['pos']},"
                        f"{r['predicted']},{r['actual']},{r['minutes']}\n")
        engine_promotion.main()
        state = json.load(open(engine_promotion.ENGINE_STATE_FILE))
        self.assertFalse(state["promoted"])
        self.assertEqual(state["shadow_evaluated_gws"], 3)


class TestResolveEngine(unittest.TestCase):
    def test_v3_when_promoted(self):
        tmp = tempfile.mkdtemp()
        try:
            orig = pre_deadline_run.ENGINE_STATE_FILE
            pre_deadline_run.ENGINE_STATE_FILE = os.path.join(tmp, "engine_state.json")
            with open(pre_deadline_run.ENGINE_STATE_FILE, "w") as f:
                json.dump({"promoted": True, "shadow_evaluated_gws": 3}, f)
            self.assertEqual(pre_deadline_run.resolve_engine(True)[0], "v3")
            self.assertEqual(pre_deadline_run.resolve_engine(False)[0], "v3")
            pre_deadline_run.ENGINE_STATE_FILE = orig
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_v2_with_odds_v1_without(self):
        tmp = tempfile.mkdtemp()
        try:
            orig_state = pre_deadline_run.ENGINE_STATE_FILE
            pre_deadline_run.ENGINE_STATE_FILE = os.path.join(tmp, "engine_state.json")
            with open(pre_deadline_run.ENGINE_STATE_FILE, "w") as f:
                json.dump({"promoted": False}, f)
            # Sol GW1 directive W3: valid odds alone do NOT promote v2.
            # The owner must approve the pending candidate first.
            from proposal_binding import V2_STATE_FILE
            orig_v2 = V2_STATE_FILE
            try:
                import proposal_binding as pb
                pb.V2_STATE_FILE = os.path.join(tmp, "v2_candidate.json")
                # no candidate -> v1 even with odds
                self.assertEqual(pre_deadline_run.resolve_engine(True)[0], "v1")
                # candidate pending -> still v1 (awaiting owner)
                pb.create_v2_candidate(1, "odds-hash", {"note": "x"})
                self.assertEqual(pre_deadline_run.resolve_engine(True)[0], "v1")
                # owner approves -> v2 with odds, v1 without
                pb.activate_v2(1111111111, {1111111111})
                self.assertEqual(pre_deadline_run.resolve_engine(True)[0], "v2")
                self.assertEqual(pre_deadline_run.resolve_engine(False)[0], "v1")
                pb.V2_STATE_FILE = orig_v2
            finally:
                import proposal_binding as pb
                pb.V2_STATE_FILE = orig_v2
            pre_deadline_run.ENGINE_STATE_FILE = orig_state
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestModelVersionDisplay(unittest.TestCase):
    def test_plan_card_shows_model(self):
        card = templates.plan_card({
            "gw": 1, "model_version": "v1", "engine_note": "v1 (FDR fallback - odds not published)",
            "v3_shadow_progress": "0/3 shadow GWs",
            "transfers": [], "target_starters": [{"id": 1, "position": "GKP", "xpts": 3.5}],
            "bench": [], "captain": {"id": 1}, "vice": {"id": 1},
            "target_xpts": 60.0, "current_xpts": 60.0, "horizon_gain": 0.0,
            "deadline": "2026-08-21T17:30:00Z"})
        self.assertIn("Model", card)
        self.assertIn("v1 (FDR fallback", card)
        self.assertIn("0/3", card)

    def test_plan_card_v3_captain_uncertainty(self):
        card = templates.plan_card({
            "gw": 1, "model_version": "v3", "engine_note": "v3 (Intelligence Engine - auto-promoted)",
            "transfers": [],
            "target_starters": [{"id": 1, "position": "MID", "xpts": 8.5, "p_start": 0.93,
                                 "expected_minutes": 78, "xpts_floor": 4.2, "xpts_upside": 12.1}],
            "bench": [], "captain": {"id": 1}, "vice": {"id": 1},
            "target_xpts": 60.0, "current_xpts": 60.0, "horizon_gain": 0.0,
            "deadline": "2026-08-21T17:30:00Z"})
        self.assertIn("Start 93%", card)
        self.assertIn("Upside 12.1", card)

    def test_status_model_line_v3_live(self):
        tmp = tempfile.mkdtemp()
        try:
            orig = telegram_bot.ENGINE_STATE_FILE
            telegram_bot.ENGINE_STATE_FILE = os.path.join(tmp, "engine_state.json")
            with open(telegram_bot.ENGINE_STATE_FILE, "w") as f:
                json.dump({"promoted": True}, f)
            self.assertEqual(telegram_bot.model_version_line(),
                             "v3 (Intelligence Engine - live)")
            telegram_bot.ENGINE_STATE_FILE = orig
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_status_model_line_shadow_progress(self):
        tmp = tempfile.mkdtemp()
        try:
            orig = telegram_bot.ENGINE_STATE_FILE
            telegram_bot.ENGINE_STATE_FILE = os.path.join(tmp, "engine_state.json")
            with open(telegram_bot.ENGINE_STATE_FILE, "w") as f:
                json.dump({"promoted": False, "shadow_evaluated_gws": 1,
                           "report": {"min_gws_required": 3}}, f)
            # odds CSV is still the HTML placeholder -> deterministic v1 line
            with mock.patch("os.path.getsize", return_value=1271):
                line = telegram_bot.model_version_line()
            self.assertIn("v1 (FDR fallback)", line)
            self.assertIn("1/3", line)
            telegram_bot.ENGINE_STATE_FILE = orig
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
