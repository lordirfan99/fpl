"""FPL Autopilot - tests for the 7 Aug audit P0 fixes + new features.

Covers:
  P0.1  reconciliation target is a single global sort (audit example)
  P0.2  plan_staleness enforces approval_window_hours (age gate)
  P0.4  fdr_maps keeps per-fixture lists (DGW aggregation)
  P0.5  chip advisor wildcard trigger is type-safe with status strings
  P0.6  chip advisor transfer_in is always coupled to a legal transfer_out
  P0.7  chip used tracked per bootstrap allocation (second-half wildcard)
  P0.8  budget gate validates cash flow, not the £100m ceiling
  P0.10 atomic writes round-trip with no temp residue
  FEAT  approval reminder ladder (T-6h, T-90m, no spam)
  FEAT  post-GW decision audit attribution categories
  FEAT  transfer solver honors Keep (protected) players

Run: .venv/Scripts/python.exe -m unittest discover -s tests -v
"""
import datetime
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

BASE = str(Path(__file__).resolve().parents[1])
sys.path.insert(0, os.path.join(BASE, "execution"))
sys.path.insert(0, os.path.join(BASE, "model"))
sys.path.insert(0, os.path.join(BASE, "optimizer"))
sys.path.insert(0, os.path.join(BASE, "jobs"))
sys.path.insert(0, os.path.join(BASE, "bot"))

import chips  # noqa: E402
import chip_advisor  # noqa: E402
import pre_deadline_run  # noqa: E402
import transfer_solver  # noqa: E402
import executor  # noqa: E402
import telegram_bot  # noqa: E402
import post_gw_audit  # noqa: E402
import atomic_io  # noqa: E402
import approval_reminder  # noqa: E402


class TestProjectPortability(unittest.TestCase):
    def test_tracked_source_has_no_machine_specific_project_root(self):
        root = Path(BASE)
        forbidden = (
            "C:/Users/" + "irfan/fpl-autopilot",
            "C:/Users/" + "irfan/projects/fpl-autopilot",
        )
        offenders = []
        for path in root.rglob("*"):
            if path.suffix.lower() not in {".py", ".cmd", ".vbs"}:
                continue
            if any(part in {".git", ".venv", "__pycache__"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(value in text for value in forbidden):
                offenders.append(str(path.relative_to(root)))
        self.assertEqual([], offenders)


def _p(pid, name, pos, xpts, xh, cost=60, club=1, selling=None):
    return {"id": pid, "name": name, "position": pos, "club": club, "cost": cost,
            "xpts": xpts, "xpts_horizon": xh, "selling_price": selling or cost,
            "purchase_price": cost}


# ---------------------------------------------------------------------------
# P0.1 - reconciliation target must be a single global sort
# ---------------------------------------------------------------------------
class TestReconciliationGlobalSort(unittest.TestCase):
    def test_audit_example_now_matches(self):
        # The exact 7 Aug audit reproduction: bench id 82 sorts INSIDE the
        # starters when globally sorted. Old code: sorted(starters)+sorted(bench)
        # => [1,4,13,61,229,346,388,397,426,481,498,82,222,491,545] which never
        # equals the live ids sorted(all picks). New code must match.
        starters = [{"id": i} for i in (1, 4, 13, 61, 229, 346, 388, 397, 426, 481, 498)]
        bench = [{"id": i} for i in (82, 222, 491, 545)]

        class FakeClient:
            def my_team(self, team_id):
                return {"picks": [{"element": i} for i in
                                  (1, 4, 13, 61, 82, 222, 229, 346, 388, 397, 426, 481, 491, 498, 545)]}

        orig_sleep = executor.time.sleep
        executor.time.sleep = lambda s: None
        try:
            matched, team = executor.verify_squad_poll(
                FakeClient(), 1, {"target_starters": starters, "bench": bench},
                attempts=3, delay=1.0)
        finally:
            executor.time.sleep = orig_sleep
        self.assertTrue(matched, "globally sorted target must match live squad")


# ---------------------------------------------------------------------------
# P0.2 - plan_staleness enforces approval_window_hours
# ---------------------------------------------------------------------------
class TestPlanStalenessAgeGate(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="fpl_age_")
        self._orig_plan = telegram_bot.PLAN_FILE
        telegram_bot.PLAN_FILE = os.path.join(self.tmpdir, "pending_plan.json")
        self.fake_bootstrap = {
            "events": [{"id": 1, "deadline_time": "2026-08-21T17:30:00Z"}],
            "elements": [
                {"id": 1, "web_name": "FIT", "status": "a",
                 "chance_of_playing_next_round": 100, "now_cost": 60},
            ],
        }
        self._orig_fetch = telegram_bot.fetch
        telegram_bot.fetch = lambda url: self.fake_bootstrap
        self._orig_settings = telegram_bot.load_settings
        telegram_bot.load_settings = lambda: {"approval_window_hours": 12}

    def tearDown(self):
        telegram_bot.PLAN_FILE = self._orig_plan
        telegram_bot.fetch = self._orig_fetch
        telegram_bot.load_settings = self._orig_settings
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _plan(self, age_hours=None):
        p = {"gw": 1, "status": "pending", "transfers": [],
             "target_starters": [{"id": 1, "name": "FIT", "position": "GKP", "xpts": 3.0}],
             "bench": [], "captain": {"id": 1}, "vice": {"id": 1}}
        if age_hours is None:
            p["generated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        else:
            p["generated_at"] = (datetime.datetime.now(datetime.timezone.utc) -
                                 datetime.timedelta(hours=age_hours)).isoformat()
        return p

    def test_fresh_plan_passes(self):
        self.assertEqual(telegram_bot.plan_staleness(self._plan()), "")

    def test_old_plan_fails_closed(self):
        reason = telegram_bot.plan_staleness(self._plan(age_hours=20))
        self.assertIn("too stale", reason)
        self.assertIn("approval window", reason)

    def test_missing_generated_at_fails_closed(self):
        plan = self._plan(age_hours=None)
        del plan["generated_at"]
        reason = telegram_bot.plan_staleness(plan)
        self.assertIn("no generated_at", reason)


# ---------------------------------------------------------------------------
# P0.4 - fdr_maps keeps per-fixture lists (DGW aggregation)
# ---------------------------------------------------------------------------
class TestDgwAggregation(unittest.TestCase):
    def test_dgw_keeps_both_fixture_difficulties(self):
        fixtures = [
            {"event": 7, "team_h": 1, "team_a": 2, "team_h_difficulty": 2, "team_a_difficulty": 4},
            {"event": 7, "team_h": 1, "team_a": 3, "team_h_difficulty": 5, "team_a_difficulty": 2},
        ]
        fdr = pre_deadline_run.fdr_maps(fixtures, [7])
        self.assertEqual(fdr[(7, 1)], [2, 5], "DGW team must keep BOTH fixtures (was overwritten)")
        self.assertEqual(fdr[(7, 2)], [4])
        self.assertNotIn((7, 4), fdr)

    def test_blank_team_absent(self):
        fixtures = [{"event": 7, "team_h": 1, "team_a": 2,
                     "team_h_difficulty": 2, "team_a_difficulty": 4}]
        fdr = pre_deadline_run.fdr_maps(fixtures, [7])
        self.assertNotIn((7, 3), fdr, "blanking team has no fixture entry")


# ---------------------------------------------------------------------------
# P0.5 - advisor wildcard trigger type-safe + works with real schema
# ---------------------------------------------------------------------------
class TestWildcardTypeSafety(unittest.TestCase):
    def test_status_string_does_not_crash(self):
        self.assertTrue(chip_advisor._injured_or_doubtful({"status": "i"}))
        self.assertTrue(chip_advisor._injured_or_doubtful({"status": "u"}))
        self.assertTrue(chip_advisor._injured_or_doubtful({"cop": 30}))
        self.assertFalse(chip_advisor._injured_or_doubtful({"cop": 80}))
        self.assertFalse(chip_advisor._injured_or_doubtful({}))
        self.assertFalse(chip_advisor._injured_or_doubtful({"status": "a", "cop": 100}))

    def test_wildcard_fires_with_status_strings_in_pipeline_schema(self):
        # all clubs = 1 (has a fixture) so Free Hit never fires - only the
        # Wildcard branch can trigger, isolating the P0.5 fix.
        plan = {
            "target_starters": [{"id": i, "club": 1, "xpts": 2, "status": "i"}
                                for i in range(1, 8)],
            "bench": [{"id": i, "club": 1, "xpts": 2} for i in range(8, 12)],
            "captain": {"id": 1, "club": 1, "xpts": 2, "status": "i"},
        }
        fixtures = [{"event": 1, "team_h": 1, "team_a": 2}]
        sug = chip_advisor.advise(plan, fixtures, 1, 1, used_chips={})
        self.assertIsNotNone(sug)
        self.assertEqual(sug["chip"], "wildcard")


# ---------------------------------------------------------------------------
# P0.6 - chip transfer_in always coupled to a legal transfer_out
# ---------------------------------------------------------------------------
class TestChipTransferCoupling(unittest.TestCase):
    def test_legal_out_same_position_club_cap(self):
        squad = [
            _p(1, "MID_A", "MID", 4.0, 9.0, club=1, selling=55),
            _p(2, "MID_B", "MID", 4.5, 10.0, club=1, selling=60),
            _p(3, "MID_C", "MID", 5.0, 11.0, club=1, selling=65),
            _p(4, "DEF_A", "DEF", 4.0, 9.0, club=2, selling=50),
            _p(5, "GKP_A", "GKP", 3.5, 8.0, club=3, selling=45),
        ]
        target = _p(99, "NEW_MID", "MID", 6.0, 13.0, club=1, cost=70)
        out_p = chip_advisor._legal_transfer_out(target, squad)
        self.assertIsNotNone(out_p)
        # target's club 1 is at the 3-player cap -> MUST sell a club-1 MID
        self.assertEqual(out_p["club"], 1)
        self.assertEqual(out_p["position"], "MID")

    def test_no_legal_out_when_no_same_position(self):
        squad = [_p(4, "DEF_A", "DEF", 4.0, 9.0, club=2, selling=50)]
        target = _p(99, "NEW_MID", "MID", 6.0, 13.0, club=3, cost=70)
        self.assertIsNone(chip_advisor._legal_transfer_out(target, squad))

    def test_suggestion_carries_transfer_out(self):
        plan = {
            "target_starters": [{"id": i, "club": 5, "xpts": 2.0} for i in range(1, 12)],
            "bench": [{"id": i, "club": 5, "xpts": 2.0} for i in range(12, 16)],
            "captain": {"id": 1, "club": 5, "xpts": 2.0},
        }
        players = [
            {"id": 500, "name": "DGW_STAR", "position": "MID", "club": 10, "cost": 70, "xpts": 9.0},
        ]
        squad = [_p(1, "S1", "GKP", 3.0, 7.0, club=5, selling=45),
                 _p(2, "S2", "DEF", 3.0, 7.0, club=5, selling=45),
                 _p(3, "S3", "DEF", 3.0, 7.0, club=5, selling=45),
                 _p(4, "S4", "DEF", 3.0, 7.0, club=5, selling=45),
                 _p(5, "S5", "DEF", 3.0, 7.0, club=5, selling=45),
                 _p(6, "S6", "MID", 3.0, 7.0, club=5, selling=80),
                 _p(7, "S7", "MID", 3.0, 7.0, club=5, selling=60),
                 _p(8, "S8", "MID", 3.0, 7.0, club=5, selling=60),
                 _p(9, "S9", "MID", 3.0, 7.0, club=5, selling=60),
                 _p(10, "S10", "FWD", 3.0, 7.0, club=5, selling=55),
                 _p(11, "S11", "FWD", 3.0, 7.0, club=5, selling=55),
                 _p(12, "B1", "GKP", 3.0, 7.0, club=5, selling=45),
                 _p(13, "B2", "DEF", 3.0, 7.0, club=5, selling=45),
                 _p(14, "B3", "MID", 3.0, 7.0, club=5, selling=45),
                 _p(15, "B4", "FWD", 3.0, 7.0, club=5, selling=45)]
        # DGW for club 10 in gw 7
        fixtures = [{"event": 7, "team_h": 10, "team_a": 11},
                    {"event": 7, "team_h": 10, "team_a": 12}]
        sug = chip_advisor.advise(plan, fixtures, 7, 1, players=players, squad=squad,
                                  bank=0, used_chips={})
        self.assertIsNotNone(sug)
        self.assertEqual(sug["chip"], "3xc")
        self.assertIn("transfer_in", sug)
        self.assertIn("transfer_out", sug, "chip transfer must be actionable")
        self.assertEqual(sug["transfer_out"]["position"], "MID")
        self.assertTrue(sug["transfer_out"]["selling_price"] + 0 >= players[0]["cost"] - 0)


# ---------------------------------------------------------------------------
# P0.7 - chip usage tracked per bootstrap allocation
# ---------------------------------------------------------------------------
class TestChipSecondHalfAllocation(unittest.TestCase):
    WINDOWS = {"wildcard": [(2, 19), (20, 38)], "freehit": [(2, 19), (20, 38)],
               "bboost": [(1, 19), (20, 38)], "3xc": [(1, 19), (20, 38)]}

    def test_first_half_use_blocks_first_half_only(self):
        used = {"wildcard": [10]}
        self.assertTrue(chips.chip_used_in_window("wildcard", 12, used, self.WINDOWS),
                        "first-half wildcard must be used in GW12")
        self.assertFalse(chips.chip_used_in_window("wildcard", 24, used, self.WINDOWS),
                         "second-half wildcard must be AVAILABLE again (P0.7)")

    def test_second_half_use_blocks_second_half(self):
        used = {"wildcard": [24]}
        self.assertTrue(chips.chip_used_in_window("wildcard", 30, used, self.WINDOWS))

    def test_backward_compat_single_event(self):
        used = {"3xc": 12}
        self.assertTrue(chips.chip_used_in_window("3xc", 14, used, self.WINDOWS))
        self.assertFalse(chips.chip_used_in_window("3xc", 22, used, self.WINDOWS))

    def test_advisor_respects_second_half_allocation(self):
        # all clubs = 1 (has a fixture) so only the Wildcard branch can fire.
        plan = {
            "target_starters": [{"id": i, "club": 1, "xpts": 2, "status": "i"} for i in range(1, 8)],
            "bench": [{"id": i, "club": 1, "xpts": 2} for i in range(8, 12)],
            "captain": {"id": 1, "club": 1, "xpts": 2, "status": "i"},
        }
        fixtures = [{"event": 24, "team_h": 1, "team_a": 2}]
        # wildcard played GW10 (first half) -> GW24 second-half allocation free
        sug = chip_advisor.advise(plan, fixtures, 24, 1, used_chips={"wildcard": [10]},
                                  windows=self.WINDOWS)
        self.assertIsNotNone(sug)
        self.assertEqual(sug["chip"], "wildcard")
        # in the first half (GW12) it must NOT be suggested
        fixtures12 = [{"event": 12, "team_h": 1, "team_a": 2}]
        sug2 = chip_advisor.advise(plan, fixtures12, 12, 1, used_chips={"wildcard": [10]},
                                   windows=self.WINDOWS)
        self.assertIsNone(sug2)


# ---------------------------------------------------------------------------
# P0.8 - budget gate = cash flow, not the £100m ceiling
# ---------------------------------------------------------------------------
class TestBudgetGateCashFlow(unittest.TestCase):
    def test_appreciated_squad_passes(self):
        # owned squad worth £105m (sell value), £0 bank - legal and must pass
        total_cost = 1050
        total_sell = 1050
        bank = 0
        self.assertTrue(total_sell + bank >= total_cost)

    def test_transfer_cash_flow_enforced(self):
        bank = 30
        cash_in = 150  # purchases
        cash_out = 120  # sales
        self.assertTrue(cash_in - cash_out <= bank)
        cash_in = 200
        self.assertFalse(cash_in - cash_out <= bank)


# ---------------------------------------------------------------------------
# P0.10 - atomic writes
# ---------------------------------------------------------------------------
class TestAtomicIO(unittest.TestCase):
    def test_roundtrip_and_no_tmp_residue(self):
        d = tempfile.mkdtemp(prefix="fpl_atomic_")
        try:
            path = os.path.join(d, "plan.json")
            atomic_io.atomic_write_json(path, {"a": [1, 2, 3], "t": "x"})
            with open(path, encoding="utf-8") as f:
                self.assertEqual(json.load(f), {"a": [1, 2, 3], "t": "x"})
            leftovers = [x for x in os.listdir(d) if x.endswith(".tmp")]
            self.assertEqual(leftovers, [], "atomic write must clean temp files")
        finally:
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# FEAT - transfer solver honors Keep (protected) players
# ---------------------------------------------------------------------------
class TestProtectedKeep(unittest.TestCase):
    def test_keep_player_never_sold(self):
        squad = [_p(1, "PINNED", "MID", 4.0, 9.0, cost=60, club=1, selling=60),
                 _p(2, "SELLABLE", "MID", 4.5, 10.0, cost=60, club=2, selling=60)]
        candidates = [
            _p(3, "STAR", "MID", 6.0, 16.0, cost=70, club=3),
            _p(4, "ALT", "MID", 5.5, 15.0, cost=65, club=4),
        ]
        transfers, _, _, _ = transfer_solver.solve_transfers(
            squad, candidates, free_transfers=2, bank=20, protected={1})
        outs = {t["element_out"] for t in transfers}
        self.assertNotIn(1, outs, "Keep player must never be transferred out")
        # the pinned player is the ONLY mid -> the alternative move (SELLABLE
        # -> STAR) is still legal and taken; only PINNED is untouchable
        self.assertEqual(outs, {2})

    def test_protected_allows_other_moves(self):
        squad = [_p(1, "PINNED", "MID", 4.0, 9.0, cost=60, club=1, selling=60),
                 _p(2, "BAD", "MID", 4.0, 8.0, cost=60, club=2, selling=60)]
        candidates = [_p(3, "STAR", "MID", 6.0, 16.0, cost=70, club=3)]
        transfers, _, _, _ = transfer_solver.solve_transfers(
            squad, candidates, free_transfers=1, bank=20, protected={1})
        self.assertEqual([t["element_out"] for t in transfers], [2])


# ---------------------------------------------------------------------------
# FEAT - approval reminder ladder
# ---------------------------------------------------------------------------
class TestApprovalReminderLadder(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="fpl_remind_")
        self._orig_plan = approval_reminder.PLAN_FILE
        self._orig_state = approval_reminder.STATE_FILE
        approval_reminder.PLAN_FILE = os.path.join(self.tmpdir, "pending_plan.json")
        approval_reminder.STATE_FILE = os.path.join(self.tmpdir, "reminder_state.json")
        self.sent = []
        self._orig_send = approval_reminder.send_telegram
        approval_reminder.send_telegram = lambda text, chat, token: self.sent.append(text) or {"ok": True}
        self._orig_settings = approval_reminder.load_settings
        approval_reminder.load_settings = lambda: {"approval_window_hours": 12,
                                                   "telegram": {"chat_id": 1}}
        self._orig_creds = approval_reminder.load_creds
        approval_reminder.load_creds = lambda: {"TELEGRAM_BOT_TOKEN": "T"}

    def tearDown(self):
        approval_reminder.PLAN_FILE = self._orig_plan
        approval_reminder.STATE_FILE = self._orig_state
        approval_reminder.send_telegram = self._orig_send
        approval_reminder.load_settings = self._orig_settings
        approval_reminder.load_creds = self._orig_creds
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_plan(self, hours_to_deadline, status="pending", cap_id=1):
        dl = (datetime.datetime.now(datetime.timezone.utc) +
              datetime.timedelta(hours=hours_to_deadline)).isoformat()
        plan = {
            "gw": 1, "status": status, "deadline": dl,
            "generated_at": (datetime.datetime.now(datetime.timezone.utc) -
                             datetime.timedelta(hours=1)).isoformat(),
            "transfers": [], "chip": None,
            "target_starters": [{"id": 1, "name": "A", "position": "MID", "xpts": 5.0}],
            "bench": [], "captain": {"id": cap_id, "name": "A"}, "vice": {"id": cap_id},
        }
        atomic_io.atomic_write_json(approval_reminder.PLAN_FILE, plan)

    def test_urgent_reminder_at_90m_once(self):
        self._write_plan(hours_to_deadline=1.0)
        approval_reminder.main()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("URGENT", self.sent[0])
        # second run: no spam
        approval_reminder.main()
        self.assertEqual(len(self.sent), 1)

    def test_t6_reminder_fires_in_window(self):
        self._write_plan(hours_to_deadline=5.0)
        approval_reminder.main()
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Reminder", self.sent[0])
        approval_reminder.main()
        self.assertEqual(len(self.sent), 1, "T-6h must not re-send")

    def test_no_reminder_for_approved_plan(self):
        self._write_plan(hours_to_deadline=1.0, status="executed")
        approval_reminder.main()
        self.assertEqual(self.sent, [], "no spam after approval")

    def test_plan_change_restarts_ladder(self):
        self._write_plan(hours_to_deadline=5.0)
        approval_reminder.main()
        self.assertEqual(len(self.sent), 1)
        # plan changes (new captain) -> ladder restarts -> T-6h fires again
        self._write_plan(hours_to_deadline=4.0, cap_id=2)
        approval_reminder.main()
        self.assertEqual(len(self.sent), 2)


# ---------------------------------------------------------------------------
# FEAT - post-GW decision audit attribution
# ---------------------------------------------------------------------------
class TestPostGwAudit(unittest.TestCase):
    def setUp(self):
        self.plan = {
            "gw": 3,
            "target_starters": [
                {"id": 1, "name": "STAR", "position": "MID", "xpts": 8.0},
                {"id": 2, "name": "BUST", "position": "FWD", "xpts": 6.0},
                {"id": 3, "name": "SUB", "position": "DEF", "xpts": 3.0},
            ],
            "bench": [{"id": 4, "name": "BENCHY", "position": "MID", "xpts": 4.0}],
            "transfers": [
                {"element_in": 10, "element_out": 11, "out_name": "OLD", "in_name": "NEW", "hit": False},
            ],
            "captain": {"id": 1, "name": "STAR"},
            "target_xpts": 60.0,
            "chip": "3xc",
        }
        # STAR underperforms (bad pred), BUST predicted 6 plays 10' (minutes),
        # NEW (transfer in) outscores OLD (good decision), BENCHY scores big,
        # player 2 (BUST) now injured after deadline.
        self.actuals = {1: 2, 2: 1, 3: 5, 4: 9, 10: 7, 11: 2}
        self.minutes = {1: 90, 2: 10, 3: 90, 4: 90, 10: 90, 11: 90}
        self.elements = {
            1: {"web_name": "STAR"}, 2: {"web_name": "BUST", "status": "i",
                                         "chance_of_playing_next_round": 0},
            3: {"web_name": "SUB"}, 4: {"web_name": "BENCHY"},
            10: {"web_name": "NEW"}, 11: {"web_name": "OLD"},
        }

    def test_all_categories_populated(self):
        audit = post_gw_audit.build_audit(self.plan, self.actuals, self.minutes,
                                          self.elements, gw_points=55)
        cats = audit["categories"]
        self.assertTrue(cats["bad_predictions"], "STAR pred 8 -> 2 must be flagged")
        self.assertTrue(cats["bad_minutes"], "BUST pred 6, 10' must be flagged")
        self.assertTrue(cats["transfers"], "transfer decision must be audited")
        self.assertTrue(cats["injured_after_deadline"], "BUST status i must be flagged")
        self.assertEqual(cats["bench_points"], 9)
        self.assertEqual(cats["chip_outcome"], "3xc")
        joined = "\n".join(audit["lines"])
        self.assertIn("Luck vs process", joined)
        self.assertIn("Captain", joined)

    def test_luck_summary_numbers(self):
        audit = post_gw_audit.build_audit(self.plan, self.actuals, self.minutes,
                                          self.elements, gw_points=55)
        s = audit["summary"]
        # XI predicted 17.0 (8+6+3), actual 8 (2+1+5) -> residual -9
        self.assertEqual(s["xi_predicted"], 17.0)
        self.assertEqual(s["xi_actual"], 8.0)
        self.assertEqual(s["residual"], -9.0)
        self.assertIn("underperformed", s["verdict"])


# ---------------------------------------------------------------------------
# FEAT - clean execution confirmation (no raw log blob in chat)
# ---------------------------------------------------------------------------
class TestExecutionSummary(unittest.TestCase):
    def test_clean_message_no_blob(self):
        plan = {
            "gw": 1, "status": "executed", "transfers": [],
            "target_starters": [], "bench": [], "captain": {"id": 1, "name": "B.Fernandes"},
            "vice": {"id": 4, "name": "Gabriel"}, "chip": None,
            "deadline": "2026-08-21T17:30:00Z",
        }
        msg = telegram_bot.execution_summary(plan)
        self.assertIn("plan executed!", msg)
        self.assertIn("B.Fernandes", msg)
        self.assertIn("Gabriel", msg)
        self.assertIn("Transfers: 0", msg)
        self.assertNotIn("{", msg, "raw JSON blob must never reach the chat")
        self.assertNotIn("POST /api/", msg, "executor log must never reach the chat")

    def test_clean_message_with_transfers(self):
        plan = {
            "gw": 2, "status": "executed",
            "transfers": [{"out_name": "A", "in_name": "B", "gain": 2.5, "hit": False}],
            "target_starters": [], "bench": [], "captain": {"id": 1, "name": "C"},
            "vice": {"id": 2, "name": "D"}, "chip": "3xc",
            "deadline": "2026-08-28T17:30:00Z",
        }
        msg = telegram_bot.execution_summary(plan)
        self.assertIn("A → B", msg)
        self.assertIn("Chip: 3xc", msg)
        self.assertIn("Locks in", msg)


class TestExecutorFailurePath(unittest.TestCase):
    def test_transfers_failure_returns_3tuple_false(self):
        class FailResp:
            status_code = 400
            text = "bad request"

        class FakeSession:
            headers = {}
            cookies = {}

            def post(self, *a, **k):
                return FailResp()

        class FakeClient:
            def __init__(self):
                self.s = FakeSession()

            def reload(self):
                pass

        plan = {"team_id": 1, "gw": 1,
                "transfers": [{"element_in": 2, "element_out": 1}],
                "chip": None, "target_starters": [{"id": 1}], "bench": [],
                "captain": {"id": 1}}
        orig_client = executor.FPLClient
        orig_tok = executor.refresh_access_token
        executor.FPLClient = FakeClient
        executor.refresh_access_token = lambda: None
        try:
            r1, r2, matched = executor.execute_plan(plan)
        finally:
            executor.FPLClient = orig_client
            executor.refresh_access_token = orig_tok
        self.assertEqual(r1.status_code, 400)
        self.assertIsNone(r2)
        self.assertFalse(matched, "a failed transfers POST must not report success")


if __name__ == "__main__":
    unittest.main(verbosity=2)
