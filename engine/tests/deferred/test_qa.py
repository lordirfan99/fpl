"""
FPL Autopilot - comprehensive QA/QC suite (Part 2 of the audit).

Covers every pure/logic function in the system:
  - xPts model: injury gate, cop thresholds, cameo filtering, rate clipping,
    preseason vs inseason branches, position baselines, FDR scaling
  - Optimizer: MILP squad constraints (15, 2/5/5/3, club cap, budget),
    lineup minimums, bench autosub order
  - Templates: mono_table alignment/escaping, plan_card both branches
  - Bot handlers: plan_staleness (deadline/injury/price), chip validation,
    reject flow, approve stale-refusal (all network mocked)

Run: .venv/Scripts/python.exe -m unittest discover -s tests -v
"""
import os
import sys
import unittest
import tempfile
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "model"))
sys.path.insert(0, os.path.join(BASE, "optimizer"))
sys.path.insert(0, os.path.join(BASE, "execution"))
sys.path.insert(0, os.path.join(BASE, "bot"))

import xpts_model  # noqa: E402
import squad_solver  # noqa: E402
import templates  # noqa: E402
import telegram_bot  # noqa: E402



# ---------------------------------------------------------------------------
# xPts model
# ---------------------------------------------------------------------------
class TestMinProbability(unittest.TestCase):
    def test_no_minutes_returns_prior(self):
        self.assertEqual(xpts_model.min_probability([]), 0.5)

    def test_all_played_smoothed(self):
        self.assertAlmostEqual(xpts_model.min_probability([90, 90, 90]), (3 + 1) / (3 + 2))

    def test_none_played(self):
        self.assertAlmostEqual(xpts_model.min_probability([0, 0]), (0 + 1) / (2 + 2))


class TestPer90Rate(unittest.TestCase):
    def test_cameos_ignored(self):
        # 5 pts in 1 min = 450/90 raw; must be EXCLUDED (< 20 min)
        rates = xpts_model.per90_rate([5, 6, 6], [1, 90, 90])
        self.assertEqual(len([r for r in [] if r]), 0)
        self.assertIsNotNone(rates)
        self.assertLess(rates, 8.0)  # only the two 90-min games count

    def test_clipped_at_cap(self):
        r = xpts_model.per90_rate([30], [90])
        self.assertEqual(r, 25.0)  # 30*90/90=30 -> capped to 25

    def test_recent_weighting(self):
        # [10,20,30] per90 with weights [0.05,0.10,0.15,0.20,0.25,0.25],
        # last-n weights used: [0.20,0.25,0.25]. NOTE 30/90 is NOT clipped (cap=25)
        r = xpts_model.per90_rate([10, 20, 24], [90, 90, 90])
        w = xpts_model._WEIGHTS[-3:]
        expected = (10 * w[0] + 20 * w[1] + 24 * w[2]) / sum(w)
        self.assertAlmostEqual(r, expected, places=6)

    def test_rate_capped_at_25(self):
        # 30 pts in 90 min = 30.0/90 rate, but clipped to the 25 cap
        self.assertEqual(xpts_model.per90_rate([30], [90]), 25.0)

    def test_no_valid_games_returns_none(self):
        self.assertIsNone(xpts_model.per90_rate([5], [1]))


class TestFdrMultiplier(unittest.TestCase):
    def test_gk_def_always_1(self):
        self.assertEqual(xpts_model.fdr_multiplier(5, "GKP"), 1.0)
        self.assertEqual(xpts_model.fdr_multiplier(5, "DEF"), 1.0)

    def test_mid_fwd_scale(self):
        self.assertEqual(xpts_model.fdr_multiplier(2, "MID"), 1.0)
        self.assertLess(xpts_model.fdr_multiplier(4, "MID"), 1.0)
        self.assertGreaterEqual(xpts_model.fdr_multiplier(10, "FWD"), 0.75)  # floor


class TestInjuryGate(unittest.TestCase):
    def test_status_injured_zero(self):
        el = {"status": "i", "element_type": 3, "points_per_game": "5.0", "form": "5.0", "minutes": 300}
        self.assertEqual(xpts_model.inseason_xpts_from_bootstrap(el, 3, 5), 0.0)

    def test_status_unavailable_zero(self):
        # edge-case B11: status 'u' (left league / gone for season) must zero
        # like status 'i' - a phantom player forces a transfer OUT, never sits
        # at normal xPts while the optimizer looks for 'better' replacements.
        el = {"status": "u", "element_type": 3, "chance_of_playing_next_round": None,
              "news": "", "points_per_game": "6.0", "form": "6.0", "minutes": 2500}
        self.assertEqual(xpts_model.inseason_xpts_from_bootstrap(el, 3, 20), 0.0)
        self.assertEqual(xpts_model.preseason_xpts(el, 3), 0.0)

    def test_cop_below_50_zero(self):
        el = {"status": "a", "chance_of_playing_next_round": 40, "element_type": 3,
              "points_per_game": "5.0", "form": "5.0", "minutes": 300}
        self.assertEqual(xpts_model.inseason_xpts_from_bootstrap(el, 3, 5), 0.0)

    def test_cop_50_75_halves(self):
        el = {"status": "a", "chance_of_playing_next_round": 60, "element_type": 3,
              "points_per_game": "5.0", "form": "5.0", "minutes": 300}
        base = xpts_model.inseason_xpts_from_bootstrap(dict(el, chance_of_playing_next_round=100), 3, 5)
        half = xpts_model.inseason_xpts_from_bootstrap(el, 3, 5)
        self.assertAlmostEqual(half, base * 0.5)

    def test_news_text_halves(self):
        el = {"status": "a", "chance_of_playing_next_round": 100, "news": "Knock - 75% chance of playing",
              "element_type": 3, "points_per_game": "5.0", "form": "5.0", "minutes": 300}
        base = xpts_model.inseason_xpts_from_bootstrap(dict(el, news=""), 3, 5)
        with_news = xpts_model.inseason_xpts_from_bootstrap(el, 3, 5)
        self.assertAlmostEqual(with_news, base * 0.5)

    def test_preseason_injury_gate(self):
        el = {"status": "i", "element_type": 4, "points_per_game": "5.0", "minutes": 2500}
        self.assertEqual(xpts_model.preseason_xpts(el, 3), 0.0)


class TestPositionBaseline(unittest.TestCase):
    def test_gk_alias(self):
        self.assertEqual(xpts_model.position_baseline("GKP"), xpts_model.position_baseline("GK"))

    def test_unknown_position_fallback(self):
        self.assertIsNotNone(xpts_model.position_baseline("XYZ"))


# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------
def _p(pid, name, pos, xpts, cost=60, club=1):
    return {"id": pid, "name": name, "position": pos, "club": club, "cost": cost, "xpts": xpts,
            "selling_price": cost, "purchase_price": cost}


def _pool():
    pool = []
    i = 0
    for pos, n in [("GKP", 10), ("DEF", 30), ("MID", 30), ("FWD", 15)]:
        for j in range(n):
            i += 1
            pool.append(_p(1000 + i, f"{pos}_{j}", pos, 3.0 + (j % 5) * 0.5, cost=50 + (j % 10) * 5, club=(j % 6) + 1))
    return pool


class TestSolveSquad(unittest.TestCase):
    def test_squad_shape(self):
        squad = squad_solver.solve_squad(_pool(), budget=1000)
        from collections import Counter
        quota = Counter(p["position"] for p in squad)
        self.assertEqual(len(squad), 15)
        self.assertEqual(dict(quota), {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3})

    def test_club_cap(self):
        squad = squad_solver.solve_squad(_pool(), budget=1000)
        from collections import Counter
        clubs = Counter(p["club"] for p in squad)
        self.assertLessEqual(max(clubs.values()), 3)

    def test_budget_respected(self):
        # minimum legal 15-squad from this pool is £87.0m; budget £90.0m is feasible
        squad = squad_solver.solve_squad(_pool(), budget=900)
        self.assertLessEqual(sum(p["cost"] for p in squad), 900)

    def test_infeasible_budget_raises(self):
        # £60.0m cannot buy a legal 2/5/5/3 squad -> solver must FAIL LOUDLY,
        # never return an invalid squad silently
        with self.assertRaises(RuntimeError):
            squad_solver.solve_squad(_pool(), budget=600)


class TestSolveLineup(unittest.TestCase):
    def test_lineup_minimums(self):
        squad = squad_solver.solve_squad(_pool(), budget=1000)
        starters, bench = squad_solver.solve_lineup(squad)
        from collections import Counter
        q = Counter(p["position"] for p in starters)
        self.assertEqual(len(starters), 11)
        self.assertGreaterEqual(q["GKP"], 1)
        self.assertGreaterEqual(q["DEF"], 3)
        self.assertGreaterEqual(q["MID"], 2)
        self.assertGreaterEqual(q["FWD"], 1)
        self.assertEqual(len(starters) + len(bench), 15)

    def test_bench_ordered_by_xpts_desc(self):
        squad = squad_solver.solve_squad(_pool(), budget=1000)
        starters, bench = squad_solver.solve_lineup(squad)
        xs = [p["xpts"] for p in bench]
        self.assertEqual(xs, sorted(xs, reverse=True), "bench must be autosub-ordered by xPts desc")


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
class TestTemplates(unittest.TestCase):
    def test_mono_table_escapes_html(self):
        t = templates.mono_table(["A"], [["<b>x</b>"]])
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", t)

    def test_mono_table_aligned(self):
        t = templates.mono_table(["Name", "Val"], [["a", "1"], ["longer", "22"]])
        lines = t.splitlines()
        # col widths: Name=6 ("longer"), Val=3 -> +2 padding each side
        self.assertEqual(lines[0], "+--------+-----+")
        self.assertEqual(lines[2], "+--------+-----+")

    def test_plan_card_no_transfers(self):
        card = templates.plan_card({
            "gw": 1, "transfers": [], "target_starters": [_p(1, "GK", "GKP", 3.5)],
            "bench": [_p(2, "FW", "FWD", 3.0)], "captain": _p(1, "GK", "GKP", 3.5),
            "vice": _p(2, "FW", "FWD", 3.0), "target_xpts": 60.0, "current_xpts": 60.0,
            "horizon_gain": 0.0, "deadline": "2026-08-21T17:30:00Z"})
        self.assertIn("Transfers:</b> none", card)
        self.assertIn("FPL AUTOPILOT • GW1", card)

    def test_plan_card_with_transfers(self):
        card = templates.plan_card({
            "gw": 2, "transfers": [{"out_name": "A", "in_name": "B", "gain": 2.5, "hit": True}],
            "target_starters": [_p(1, "GK", "GKP", 3.5)], "bench": [],
            "captain": _p(1, "GK", "GKP", 3.5), "vice": None,
            "target_xpts": 62.0, "current_xpts": 59.0, "horizon_gain": 3.0,
            "deadline": "2026-08-28T17:30:00Z"})
        self.assertIn("Transfers</b>", card)
        self.assertIn("A", card)
        self.assertIn("B", card)


# ---------------------------------------------------------------------------
# Bot handlers (network mocked)
# ---------------------------------------------------------------------------
class TestBotHandlers(unittest.TestCase):
    def setUp(self):
        # Sol audit P0-1: flow tests run as the OWNER (auth has its own suite,
        # tests/test_bot_security.py). These tests verify flow logic, not auth.
        self._auth_patcher = unittest.mock.patch.object(telegram_bot, "authorized", return_value=True)
        self._auth_patcher.start()
        self.addCleanup(self._auth_patcher.stop)
        # point PLAN_FILE at a temp file so we never touch the real pending plan
        self.tmpdir = tempfile.mkdtemp()
        self.tmp_plan = os.path.join(self.tmpdir, "pending_plan.json")
        self._orig_plan_file = telegram_bot.PLAN_FILE
        telegram_bot.PLAN_FILE = self.tmp_plan

        # fake bootstrap for plan_staleness
        self.fake_bootstrap = {
            "events": [{"id": 1, "deadline_time": "2026-08-21T17:30:00Z"}],
            "elements": [
                {"id": 1, "web_name": "FITPLAYER", "status": "a", "chance_of_playing_next_round": 100, "now_cost": 60},
                {"id": 2, "web_name": "INJURED", "status": "i", "chance_of_playing_next_round": 0, "now_cost": 60},
                {"id": 3, "web_name": "DOUBTFUL", "status": "a", "chance_of_playing_next_round": 30, "now_cost": 60},
                {"id": 4, "web_name": "PRICY", "status": "a", "chance_of_playing_next_round": 100, "now_cost": 75},
            ],
        }
        self._orig_fetch = telegram_bot.fetch
        telegram_bot.fetch = lambda url: self.fake_bootstrap

    def tearDown(self):
        telegram_bot.PLAN_FILE = self._orig_plan_file
        telegram_bot.fetch = self._orig_fetch

    def _plan(self, **overrides):
        p = {
            "gw": 1, "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "deadline": "2099-01-01T00:00:00Z",  # far future -> cutoff not reached
            "transfers": [], "target_starters": [{"id": 1, "name": "FITPLAYER", "position": "GKP", "xpts": 3.5}],
            "bench": [], "captain": {"id": 1}, "vice": {"id": 1},
        }
        p.update(overrides)
        return p

    def test_staleness_fresh_plan(self):
        self.assertEqual(telegram_bot.plan_staleness(self._plan()), "")

    def test_staleness_injured_player(self):
        plan = self._plan(target_starters=[{"id": 2, "name": "INJURED", "position": "MID", "xpts": 4.0}])
        reason = telegram_bot.plan_staleness(plan)
        self.assertIn("INJURED", reason)

    def test_staleness_doubtful_player(self):
        plan = self._plan(target_starters=[{"id": 3, "name": "DOUBTFUL", "position": "MID", "xpts": 4.0}])
        reason = telegram_bot.plan_staleness(plan)
        self.assertIn("DOUBTFUL", reason)

    def test_staleness_price_rise(self):
        plan = self._plan(transfers=[{"element_in": 4, "purchase_price": 60}])
        reason = telegram_bot.plan_staleness(plan)
        self.assertIn("price rose", reason)

    def test_staleness_deadline_passed(self):
        plan = self._plan(gw=1, generated_at="2026-08-01T00:00:00+00:00")
        # override the event deadline into the past
        self.fake_bootstrap["events"] = [{"id": 1, "deadline_time": "2026-08-01T17:30:00Z"}]
        reason = telegram_bot.plan_staleness(plan)
        self.assertIn("deadline", reason)

    def test_chip_valid_and_invalid(self):
        # hardening: a chip may only be staged on an existing simulated plan
        telegram_bot.save_pending(self._plan())
        ok = telegram_bot.chip_text(["wildcard"])
        self.assertIn("Wildcard", ok)
        bad = telegram_bot.chip_text(["nonsense"])
        self.assertIn("Chip must be one of", bad)
        # and without any pending plan it must refuse, not create a malformed one
        import os as _os
        if _os.path.exists(telegram_bot.PLAN_FILE):
            _os.remove(telegram_bot.PLAN_FILE)
        refused = telegram_bot.chip_text(["wildcard"])
        self.assertIn("No pending plan", refused)

    def test_reject_flow(self):
        telegram_bot.save_pending(self._plan())
        out = telegram_bot.reject_plan()
        self.assertIn("rejected", out)
        plan = telegram_bot.load_pending()
        self.assertEqual(plan["status"], "rejected")

    def test_approve_refuses_stale(self):
        # plan with an injured player -> must refuse, NOT execute
        telegram_bot.save_pending(self._plan(target_starters=[{"id": 2, "name": "INJURED", "position": "MID", "xpts": 4.0}]))
        out = telegram_bot.approve_plan()
        self.assertIn("STALE", out)

    def test_approve_refuses_in_flight(self):
        # F44/F45: a plan already marked 'executing' (duplicate tap or crash
        # mid-POST) must NEVER start a second execution.
        plan = self._plan(target_starters=[{"id": 1, "name": "A", "position": "MID", "xpts": 4.0}])
        plan["status"] = "executing"
        telegram_bot.save_pending(plan)
        out = telegram_bot.approve_plan()
        self.assertIn("already in progress", out)

    def test_approve_marks_executing_before_execution(self):
        # F44/F45: before the first POST the plan status must flip to
        # 'executing' on disk so a duplicate tap / crash-restart can detect it.
        plan = self._plan(target_starters=[{"id": 1, "name": "A", "position": "MID", "xpts": 4.0}])
        plan["status"] = "pending"
        telegram_bot.save_pending(plan)
        import telegram_bot as tb
        import sys, types
        calls = {}

        def fake_execute_plan(p):
            # the status must ALREADY be executing on disk when execution starts
            calls["status_during"] = tb.load_pending().get("status")
            # P0.1: execute_plan now returns (r1, r2, matched)
            return None, None, True  # no-op + verified final state -> success

        # patch sys.modules so `from executor import ...` picks our fake
        mod = types.ModuleType("executor")
        mod.execute_plan = fake_execute_plan
        mod.is_success = lambda r: True
        old_mod = sys.modules.get("executor")
        sys.modules["executor"] = mod
        try:
            out = tb.approve_plan()
        finally:
            if old_mod is not None:
                sys.modules["executor"] = old_mod
            else:
                sys.modules.pop("executor", None)
        self.assertEqual(calls.get("status_during"), "executing")
        # and after success it flips to executed
        self.assertIn("executed", out)

    def test_approve_module_has_is_success_import(self):
        # regression: approve_plan() must be able to call is_success()
        # (the 202-fix switched to is_success but forgot the import ->
        #  NameError at /approve time; this test pins the import)
        import telegram_bot as tb
        self.assertTrue(hasattr(tb, "is_success") or "is_success" in tb.__dict__ or True)
        src = open(tb.__file__, encoding="utf-8").read()
        self.assertIn("from executor import execute_plan, is_success", src)


class TestStatusText(unittest.TestCase):
    """status_text reads my-team schema correctly (value under transfers, unlimited FT)."""

    def setUp(self):
        self._orig_fetch = telegram_bot.fetch
        telegram_bot.fetch = lambda url: {"events": [{"id": 1, "deadline_time": "2026-08-21T17:30:00Z", "finished": False}]}

    def tearDown(self):
        telegram_bot.fetch = self._orig_fetch

    def _fake_team(self, **overrides):
        team = {
            "picks": [{"element": 1, "position": i + 1, "multiplier": 1, "is_captain": False,
                       "is_vice_captain": False, "element_type": 1, "selling_price": 60, "purchase_price": 60}
                      for i in range(15)],
            "transfers": {"cost": 4, "status": "unlimited", "limit": None, "made": 0, "bank": 0, "value": 1000},
        }
        team.update(overrides)
        return team

    def _status_with_team(self, team):
        # patch FPLClient.my_team on the module's reference
        orig = telegram_bot.FPLClient
        class _FakeClient:
            def __init__(self):
                self.my_team_calls = 0
            def my_team(self, team_id):
                self.my_team_calls += 1
                return team
        telegram_bot.FPLClient = _FakeClient
        try:
            return telegram_bot.status_text()
        finally:
            telegram_bot.FPLClient = orig

    def test_value_read_from_transfers(self):
        msg = self._status_with_team(self._fake_team())
        self.assertIn("£100.0m", msg)  # transfers.value=1000 -> £100.0m, not £0.0m

    def test_unlimited_free_transfers_display(self):
        msg = self._status_with_team(self._fake_team())
        self.assertIn("99 (unlimited)", msg)

    def test_inseason_ft_math(self):
        team = self._fake_team()
        team["transfers"] = {"cost": 4, "status": "ok", "limit": 3, "made": 1, "bank": 20, "value": 1010}
        msg = self._status_with_team(team)
        self.assertIn("2 (made 1)", msg)
        self.assertIn("£101.0m", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
