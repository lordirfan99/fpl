# Full system audit — FPL Scout Intelligence

_Audit date: 6 September 2026. Scope: whole monorepo at `main` tip `727bf70` (#92)._
_Objective it is measured against: maximise the probability of a top‑10 mini‑league
finish (leagues `58005`, `131997`), without ever letting the system submit an FPL
action on its own._

This document is **assessment + roadmap only**. It changes no runtime behaviour.
Each roadmap item below is a **separate** `feat/…` or `fix/…` branch and PR, per
[`AGENTS.md`](../AGENTS.md). It is a hand‑off artefact: Claude produced it; any of
the follow‑up PRs may be picked up by either assistant, but never both on one branch.

---

## 1. What exists today (component map)

| Layer | What it actually does | State |
|---|---|---|
| `engine/jobs/pre_deadline_run.py` | The weekly pipeline: pull bootstrap/fixtures/my‑team → calibrated V4 xPts for every player over a 3‑GW horizon → joint horizon MILP (transfers + banked FTs + legal XI + bench + captain) → hard validation gates → canonical plan hash → `pending_plan.json` → Telegram approval card → private dashboard packet | **Solid, complete** |
| `engine/model/` | Odds‑free `competitive-v4.0` projection with rolling per‑position bias correction, empirical uncertainty scaling, optional replacement‑style bonus layer; `competitive-v4.2` shadow candidate; `projection-v5.0-lab` research lane; `component_xpts`, `minutes_model`, `fixture_engine`, `opponent_intelligence`, `manager_sharpness`, `league_signals` | **Rich, mostly wired** |
| `engine/optimizer/` | `horizon_milp` (production), `transfer_solver`, `squad_solver`, `plan_validation`; `multigw_planner` (beam search) and `horizon_milp` variants | **Production path solid; see §3.2** |
| Competitive template gate | Converge‑to‑template vs hold‑template decision, differential lock, current‑season elite template sourced from `league_intelligence` instead of preseason priors | **Working** |
| Chip handling | `chip_advisor.advise()` flags an imminent DGW/BGW opportunity; strictly advisory, never merged into the executable plan; live Wildcard/Free Hit detected from the account payload triggers a full 15‑player rebuild | **Working but reactive — see §3.1** |
| `api/` (FastAPI, VM behind Caddy, read‑only) | Snapshot‑backed JSON: league, team, elite, projections, fixtures, journal, `/v1/recommendations/current`, `/v1/decision/current`; honest freshness policy (fresh live snapshot → finalized fallback → `safe_hold`/`needs_refresh`) from the #66–#68 work | **Solid** |
| VM live collector (`infra/scripts/refresh_live_leagues.py`) | 30‑min systemd timer; publishes a validated complete‑manifest live league snapshot (every manager hydrated to 15 picks); schema‑v2 now also carries `gw_bank` and real `overall_rank` from `entry_history` at zero extra FPL calls | **Solid** |
| `web/` (Next.js, Netlify) | 16 pages. Clean split between **decision** (`/this-week` = verified private plan, same hash as Telegram) and **research** (`/planner`, `/assistant`, `/elite`, `/transfers`, `/league`, `/analytics`, `/compare`, `/players` = public gameweek data, explicitly labelled "not a personal recommendation"). Freshness chips (Fresh / League research / Stale / Safe hold). Season Journal with immutable per‑GW evidence. | **Good; gaps in §3–4** |
| `bot/` (Telegram `@Fplnaf_bot`) | The **only** path that can write to FPL, and only after the owner approves a specific `plan_id` (canonical hash of the exact plan). No standing automation submits. Asserted in CI. | **Solid** |
| Ops | branch → PR → CI (ruff + pytest + web build + Playwright unit/decision) → tagged release → deploy from tag; last‑known‑good table; incident runbook; secrets never in git | **Solid** |

**Overall:** the plumbing, the safety model and the weekly decision loop are
production‑grade. The gaps are (a) the model has almost no 2026‑27 evidence yet,
and (b) several season‑strategy capabilities are **built but not surfaced**.

---

## 2. Coverage against the top‑10 objective

| Need (owner's list) | Status | Where |
|---|---|---|
| Transfer planning (this week) | ✅ | horizon MILP → Assistant / This Week |
| Starting XI | ✅ | MILP lineup + `solve_lineup` |
| Captaincy | ⚠️ partial | single pick + top‑3 `captain_rankings`; **no captain‑vs‑field / EO view** (§4.3) |
| Bench order | ✅ | `first_week.bench_ids` |
| Chip strategy | ⚠️ partial | imminent DGW/BGW only; **no season chip roadmap** (§3.1) |
| Fixture planning | ✅ | `/planner` 5‑GW FDR matrix |
| Player comparison | ❌ missing | `/compare` is manager‑vs‑manager; `/players` is a filter list (§4.2) |
| Risk management | ⚠️ partial | variance / uncertainty / `p_start` / friction all exist in the engine; **not surfaced as one view** (§4.4) |
| Mini‑league strategy | ✅ | competitive template gate, converge/hold, differential lock |
| Monitoring elite/template managers | ✅ | `/elite`, `elite-manager-monitor`, current‑season template |
| Identifying differentials | ⚠️ partial | edge panels on `/elite`; gate can *lock* differentials but there's no ranked "safe differential" list tied to your squad (§4.5) |
| Avoiding unnecessary transfers | ⚠️ partial | friction + FT‑value + paid‑transfer gate all enforced; **the reason for a hold is not shown** (§3.3) |
| Planning several GWs ahead | ⚠️ partial | MILP plans 3 GWs; **only week 1 is surfaced** — GW+2/GW+3 intent is discarded (§3.2) |

Nine of thirteen are done or nearly done. The four soft spots are all
**surfacing/plumbing**, not model work — which matters, because the model should
stay frozen (see §5).

---

## 3. Findings — decision quality

### 3.1 Chip strategy is reactive, not a season plan
`chip_advisor.advise()` only fires when a DGW/BGW is *imminent*. There is no
season‑long chip roadmap: when to play WC1, when to hold Free Hit for a blank,
whether to Triple‑Captain a premium into a double, Bench Boost timing.
`engine/model/chip_strategy.py` (opportunity‑cost priors, `CHIP_PRIOR`) already
exists **but has no importer** — it is dead code. `decision_summary.roadmap`
already carries a per‑GW action list; nothing populates it with chip intent and
nothing renders it.

### 3.2 The multi‑GW plan is computed and then thrown away
`optimize_horizon()` returns `weeks[0..2]`. `pre_deadline_run.py` uses
`weeks[0]` for the executable plan and **ignores `weeks[1]`/`weeks[2]`**. So the
engine already knows "this week move A→B, next week you likely move C→D, bank the
FT" and never tells the owner. `engine/optimizer/multigw_planner.py` (beam search,
values banked FTs, penalises hits/uncertainty) is only wired into
`pre_deadline_shadow_v3.py` (a shadow eval), not the live plan or the dashboard.
`/planner` is fixture‑FDR research only — it does not show the engine's own
forward transfer roadmap.

### 3.3 "Why hold" is invisible
When no move clears the threshold the Assistant shows "Keep your transfer / No
move clears the model threshold". The *quantitative* reason — package gain vs
`v4_transfer_friction`, the opportunity cost of the saved FT, the paid‑transfer
gate ("disabled until N completed GWs calibrate V4") — is all in the plan packet
(`decision_summary`, `notes`) and is not rendered. The owner cannot tell a
"correctly holding" week from a "data missing" week without opening Telegram.

### 3.4 Elite‑cohort quality can degrade silently
Elite‑template selection depends on the collector getting real `overall_rank` per
manager. When `entry_history` is missing for some managers the payload falls back
to `rank_provenance: classic-league-rank-fallback` (league rank as a proxy).
`monitor_production.py` does **not** alert on that fallback, nor on
`expected_count` vs hydrated‑squad mismatch, so a quietly worse elite template
would pass monitoring.

### 3.5 Two leagues, one objective
The competitive gate reads `competitive_v4.league_id` (58005). League 131997
(2624 teams) is tracked for standings only. If the two leagues' prize boundaries
imply different risk postures, the plan does not reconcile them.
`engine/model/prize_strategy.py` exists but is only referenced from
`league_intelligence.py`, not from the plan.

---

## 4. Findings — dashboard / usability

### 4.1 Retired routes still shipped
`/autopilot`, `/shadow-v3`, `/model-compare` are redirect stubs; `/v5-lab` is a
research lane. Harmless, but the nav is wide (12 "More" links) and mixes decision,
research and dead routes. A pruning/renaming pass would reduce cognitive load.

### 4.2 No player‑vs‑player comparison
The owner asked for it explicitly. Everything needed exists: `/v1/projections/current`
gives per‑player next‑GW + horizon xPts, floor/upside, `p_start`, minutes; bootstrap
gives price/form/ownership; fixtures give the run. There is just no
`/compare?players=…` surface that lays 2–4 players side by side.

### 4.3 Captaincy has no "vs the field" view
`captain_consensus` (what elite managers captain) and `captain_rankings` (your XI
ranked for the armband) are both in the packet. The dashboard shows only your #1.
Missing: template captain vs your captain, elite EO on each, and the rank‑EV swing
of matching vs differing — the single highest‑variance weekly decision.

### 4.4 Risk is scattered
`my-team` shows a "flagged" names string. There is no one place that rolls up:
club concentration (3‑per‑club headroom), price‑fall exposure, injury/rotation
flags across all 15, fixture congestion, captain fragility. The inputs
(`status`, `cop`, `news`, `p_dnp`, `xpts_variance`, `selling_price`) are all
already fetched.

### 4.5 Differentials aren't tied to your squad
`/elite` edge panels show league‑wide "elite favour / elite avoid". There is no
"players not in your squad, low elite ownership, model‑supported, affordable from
your bank" list — i.e. actionable differentials filtered by *your* constraints.

### 4.6 Market intelligence is blank during the live window
`transfer_details` is not captured by the live collector (it would cost one
`entry/{id}/transfers/` call per manager). So the "transfer consensus" panels on
`/transfers` and `/elite` show nothing between deadline and finalization — exactly
the window the owner is deciding in. Either capture it (batched, rate‑limited) or
show an explicit "resumes after GW finalization" state instead of an empty list.

### 4.7 Doc drift
`README.md` says the VM is `us-central1-f`; `ARCHITECTURE.md` and `RUNBOOK.md` say
`us-central1-a` (correct, post zone‑recovery). `settings.example.json` has
`competitive_v4.max_snapshot_age_hours: 168` while the code and API contract
enforce 12h — misleading to a reader.

---

## 5. The constraint that shapes everything: the model has no 2026‑27 evidence yet

- The 2026‑27 dataset the API reads is self‑consistent but **degenerate**:
  `form == ep_next` for the large majority of players, so "form" carries almost no
  independent signal this early.
- `residuals.csv` / `v42_residuals.csv` (the calibration inputs) only accumulate
  rows as GWs finalize. `bias_adjustment` self‑activates at ~100 rows; empirical
  uncertainty at `min_rows=100`.
- Paid transfers are gated off until `v4_paid_transfer_min_gws` (default 3)
  completed GWs. Through roughly GW3–GW5 the engine is, by design, **XI + captain +
  one free transfer** only.

**Implication for this roadmap:** none of the items below change scoring, weights,
`signal()`, or any projection file. They surface, reconcile and explain what the
engine already produces. The model itself should stay frozen until there are
~8–10 finalized GWs of real residuals (≈ GW10). Revisit calibration then, with a
backtest, on its own PR.

---

## 6. Roadmap (each item = one branch → PR → CI → merge → tag if it touches runtime)

### P0 — correctness & honesty, low risk

1. **`fix/doc-drift`** — zone `f`→`a` in README; align `settings.example.json`
   snapshot‑age with the 12h contract; note the freeze rationale here in RUNBOOK.
   _Docs only._
2. **`fix/monitor-cohort-degradation`** — `monitor_production.py`: fail (or warn
   loudly) when `rank_provenance == classic-league-rank-fallback`, when live
   `expected_count` ≠ hydrated squads, or when the newest plan's `gw` ≠ the next
   deadline GW inside T‑minus‑X hours. Add a test.
3. **`feat/why-hold`** — Assistant: when the recommendation is a hold, render the
   number from `decision_summary` / `notes` (package gain vs friction, saved‑FT
   value, paid‑transfer gate state). Distinguishes "correctly holding" from
   "inputs missing". _Web only; data already in the packet._

### P1 — directly serves the top‑10 objective

4. **`feat/season-chip-roadmap`** — import `chip_strategy.py`; add a fixture‑swing
   / DGW‑BGW scan over the remaining season; write chip intent into
   `decision_summary.roadmap`; render an advisory season chip strip on `/planner`
   (WC1 / WC2 / FH / BB / TC target GWs + one‑line rationale). Strictly advisory,
   never merged into the executable plan — same boundary `chip_advisor` already
   respects. Add tests for the boundary.
5. **`feat/forward-transfer-roadmap`** — surface `optimize_horizon` `weeks[1..2]`
   (already computed) on `/planner` as "likely next moves, not committed"; run
   `multigw_planner` beam search as a second opinion alongside it. No change to
   the executable week‑1 plan.
6. **`feat/player-compare`** — `/compare?players=a,b,c,d`: next‑GW + 3‑GW xPts,
   floor/upside, `p_start`, price, ownership, form, 5‑fixture run, minutes risk.
   Reads `/v1/projections/current` + bootstrap + fixtures. New web surface + a
   thin API shape if needed.
7. **`feat/captain-vs-field`** — Assistant: template captain vs your captain,
   elite EO on each, rank‑EV swing of match vs differ. From `captain_consensus` +
   `captain_rankings`. _Web only._

### P2 — depth

8. **`feat/squad-risk-view`** — `/my-team`: club concentration, price‑fall
   exposure, flag/rotation rollup across all 15, fixture congestion, captain
   fragility. Inputs already fetched.
9. **`feat/actionable-differentials`** — ranked list of non‑owned, low‑elite‑EO,
   model‑supported, bank‑affordable players, filtered by your 3‑per‑club headroom.
10. **`feat/live-market-intelligence`** — either capture `transfer_details` on the
    collector (batched, rate‑limited, its own manifest field + `SCHEMA_VERSION`
    bump) or render an explicit "market intelligence resumes after GW
    finalization" state everywhere the consensus panels currently go blank.
11. **`feat/two-league-reconciliation`** — prize‑weighted objective when 58005 and
    131997 imply different risk postures, via `prize_strategy.py`. Advisory
    overlay first; only fold into the gate after a backtest.
12. **`feat/model-accountability-panel`** — `/journal`: FPL vs V4 vs V4.2 vs V5
    actual returns per GW, fed by the journal + `backtest.py`. Sets up the
    ~GW10 calibration decision with evidence.

### Not now
- Any change to `signal()`, projection weights, calibration constants, or the
  MILP objective — frozen until ≈ GW10 + backtest (see §5).
- Reintroducing Cloud Scheduler / a Cloud Run collector — retired on purpose.
- Any edit on the VM or Cloud Run outside branch → PR → tag → deploy.

---

## 7. Honest verdict

The engineering is genuinely strong: the safety model (read‑only API, single
hash‑approved write path, asserted in CI), the decision/research separation on the
dashboard, the freshness honesty work, and the weekly MILP pipeline are all
production‑grade. The system will keep the owner disciplined and will not do
anything reckless.

The edge that wins a mini‑league — season chip timing, captaincy vs the field,
multi‑GW transfer planning, actionable differentials — is **mostly built and not
yet surfaced**. P1 items 4–7 close that gap with plumbing, not model risk. The
projection itself is average and data‑starved right now; that is correctly
handled by the paid‑transfer freeze and should be left alone until ≈ GW10.

Do P0 first (three small PRs, one of them docs‑only). Then P1 in order.
