# GW5 formation root cause, elite-gap audit, and open defects

*Investigation: 14 Sep 2026 · owner `lordirfan99` · team `2797967` (KOKDIANG FC)*

Trigger: **"Why is the system still not using the proper formation (3-4-3)? It
keeps suggesting more defenders."**

The complaint was correct. This document records the root cause, the fix, the
supporting evidence, **four claims made during the investigation that were later
disproved**, and the defects that remain open.

---

## 1. Summary

| | |
|---|---|
| Complaint | Optimizer returned **5-4-1** while the elite template was **3-4-3** |
| Root cause | `competitive.template_formation` was computed and *narrated* but never passed to the MILP |
| Fix | Soft formation prior in `engine/optimizer/horizon_milp.py` (PR `feat/elite-template-formation-prior`) |
| Default | `formation_prior_weight = 0.0` → **inert until deliberately enabled** |
| Cost when enabled | ≈ **3.6 XI xPts** on the GW5 squad |
| Bigger finding | Formation was a *symptom*. The real elite gap is **chip strategy**, not projections |

---

## 2. Root cause

`competitive.template_formation` (e.g. `"3-4-3"`) was already produced by the
competitive layer and consumed by:

* `engine/model/decision_explanation.py:169-172` — which **explicitly narrates
  the mismatch**: *"GW5 projections select 5-4-1; the 3-4-3 template…"*
* `engine/model/plan_context.py:167`, `dashboard_packet`, Telegram card, dedup hash

…but it was **never passed to `optimize_horizon()`**. The MILP maximised xPts
under `LINEUP_MAX{DEF: 5}`, so a defender-heavy squad legitimately returned
5-4-1. **The system explained the problem to the owner every week and had no
lever to act on it.**

### Why 5 defenders genuinely won

Live GW5 squad had exactly **one** startable forward:

```
FWD João Pedro 5.70  ← startable
FWD Barry      3.83
FWD Wissa      3.29
DEF Ajayi      5.42  ← 5th defender beats both benched forwards
```

5-4-1 was the correct answer to the wrong question. **Squad shape, not
optimizer logic, produced the formation.**

---

## 3. The fix

`parse_formation()` converts `"D-M-F"` into per-position lineup targets,
rejecting anything whose outfield sum ≠ 10 or that breaks `LINEUP_MIN/MAX`, so
malformed upstream input degrades to "no prior" instead of corrupting the solve.

`optimize_horizon()` gains `template_formation` and `formation_prior_weight`.
Per-week slack variables price `|played − target|` into the objective:

```python
objective.append(-weight * shape_weight * (above + below))
```

The prior is **soft by construction** — deviation is *priced*, never forbidden —
so it cannot render a previously feasible plan infeasible. A `formation_prior`
evidence block (`applied`/`target`/`weight`/`chosen_formation`/`matched`) is
returned so shape decisions are auditable.

### Verified

Replay against **real GW5 data**, baseline reproducing the live card exactly
(5-4-1, `White→De Cuyper` + `Cherki→Groß`, objective 124.505 vs card 124.515):

| weight | shape | XI xPts |
|---|---|---|
| 0.0 | 5-4-1 | 66.54 |
| 0.5 | 5-4-1 | 66.54 |
| **1.0** | **3-4-3** | **62.94** |
| 2.0 | 3-4-3 | 62.94 |

Flip threshold ≈ **1.0**; cost ≈ **−3.6 XI xPts**.

Adversarial probes: `weight=0.0` identical to old code *including* with
`protected`/`excluded` sets; weights 500–1000 across three shapes all stayed
`Optimal`; 8 malformed inputs all degraded safely; no PuLP variable-name
aliasing. 6 unit tests added; full engine suite green; `ruff` clean.

---

## 4. RETRACTIONS — claims made and later disproved

**Read this before trusting any analysis above.** Two were caught only because
the owner pushed back.

### 4.1 "The projection is broken — McBurnie rated above Haaland" — WRONG

Read from a **26-commit-stale checkout of the retired `fpl-autopilot` repo** and
an uninitialised submodule, using a 3-week-old `predictions_gw2.json` tagged
`model_version: v1`. Live `competitive-v4.0` is healthy: Haaland correctly #1 at
6.20, 60 players above 4.0, no baseline clustering.
**Lesson: confirm which code is live before diagnosing it.**

### 4.2 "Haaland was filtered out of the candidate pool" — WRONG

Checked a field name that does not exist (`competitive.template_ids`). Reality:
`candidate_gate.eligible_template_ids` **contains 411 (Haaland)**.
`candidate_pool_size: 25` is **by design** — owned 15 + template 15 (overlap 5) —
because `phase: CATCH` with `differential_allowed: false` restricts buys to the
elite template. Haaland was buyable; the optimizer declined on cost/limits.

### 4.3 "The model over-rates defenders and under-rates attackers" — WRONG

Disproved by measurement against `residuals.csv` (1689 rows, GW1-3), 86 credible
starters (≥60 min, predicted ≥4.0):

| pos | n | pred | actual | bias |
|---|---|---|---|---|
| DEF | 33 | 5.29 | 4.45 | **+0.83** |
| MID | 37 | 5.54 | 4.46 | **+1.08** |
| FWD | 10 | 4.92 | 3.30 | **+1.62** |

**Defenders are the most accurate group; attackers are over-predicted more.**
The real defect is **global over-prediction at the top end (+1.08)**, not a
positional bias. FWD n=10 is far too small to act on.

> This retraction **removes the evidential basis for enabling the formation
> prior at 1.0**. It is deployed at 1.0 on the VM pending an owner decision.

### 4.4 "No chips have been played" — WRONG *(owner corrected)*

```
chips: [{"name": "wildcard", "event": 3}]
GW3 active_chip=wildcard → 28 pts (GW average 51)
```
A "Wildcard-on-misalignment" feature was proposed for a chip **already spent**.
Asserted without reading `/entry/2797967/history/`.

---

## 5. Elite-gap analysis (behaviour, not projections)

```
KOKDIANG FC : 220 pts · overall rank 6,945,181 · league 58005 rank 1091/3813
Top 10% floor: 311   → gap +91
Elite median : 321   → gap +101
```

Versus the **global average** (not the elite):

| GW | you | avg | diff | bench |
|---|---|---|---|---|
| 1 | 43 | 50 | −7 | 12 |
| 2 | 99 | 81 | **+18** | 7 |
| 3 | 28 | 51 | **−23** | 13 |
| 4 | 50 | 63 | −13 | 6 |
| **T** | **220** | **245** | **−25** | **38** |

Top-12 managers in league 58005 (346–370 pts):

```
ELITE MEDIAN : transfers 1.0 · hits 0.0 · bench waste 27.5
OWNER        : transfers 1   · hits 0   · bench waste 38
```

**Transfers and hits are identical. Bench waste is only ~10 worse** (one elite
manager left 46 on the bench and still scored 346). So "transfer more" and "stop
benching points" are both **weak** explanations.

The real difference: **every top-12 manager has played 2-4 chips by GW4**
(`bboost`, `3xc`, `freehit`, `wildcard`) — with **no DGW/BGW available to
anyone**. The owner has played one.

### The wildcard failure

```
GW3 wildcard squad ∩ today's elite template: 3/9
  (Calafiori, Palmer, João Pedro only)
Missing: Haaland, Rogers, De Cuyper, Szoboszlai, Konsa, Raya
```

A wildcard is a free 15-player rebuild — the single best moment to converge on
template — and it landed at **3/9**, scoring **28** against a 51 average. It also
brought in Wissa, Barry, M.Sangaré, Cherki and White: precisely the players the
optimizer now keeps trying to sell, and the direct cause of the single-startable-
forward squad that made 5-4-1 optimal (§2).

**Nothing in the pipeline reconciled this.** No component observed "wildcard
spent, alignment still 37.5%, rebuild failed". The engine kept proposing ±1 xPts
transfers as though the chip had never been played.

---

## 6. OPEN DEFECT — chip advice is DGW/BGW-gated and therefore silent

`engine/model/chip_advisor.py` fires **only** on a double or blank gameweek:

* Triple Captain ← `captain xPts ≥ 7 AND captain's club has a DGW`
* Bench Boost ← `3+ bench ≥ 4 xPts AND a bench club has a DGW`

Fixture audit over all **380 fixtures**:

```
fixtures with no event assigned : 0
gameweeks containing a DGW or BGW: 0   (all 38 GWs are clean 10-fixture weeks)
```

So the advisor prints `"no DGW/BGW opportunity - keep chips"` **every week** and
will keep doing so until cup postponements appear (~GW18+). Meanwhile the elite
played 2-4 chips in ordinary single gameweeks.

### The unmerged `feat/season-chip-roadmap` branch has the same blind spot

`engine/model/chip_roadmap.py` (branch `8e59fb6`, not merged) is also built
entirely on `detect_dgw` / `detect_bgw`. With zero DGW/BGW scheduled it returns,
for every chip:

> *"No double or blank gameweek is visible in the fixture horizon yet."*

**Merging it would not fix the silence.** It needs single-GW triggers first.

### Required (design only — not implemented)

1. **Triple Captain on single-GW ceiling** — use `xpts_upside`/variance, not DGW.
2. **Bench Boost when all four bench players clear a minutes/xPts floor** in any GW.
3. **First-half expiry awareness** — 2026/27 has two chip sets; set 1 expires at
   **GW19**. Unplayed = wasted. Chips remaining: Bench Boost, Triple Captain, Free Hit.
4. **Chip-outcome reconciliation** — record chip spend against alignment/points
   delta so a failed wildcard is visible to later decisions.

---

## 7. Other open items

| # | Item | Priority |
|---|---|---|
| 1 | **Decide `v4_formation_prior_weight`.** Basis for `1.0` was retracted (§4.3); costs ~3.6 xPts/GW. Recommend **0.0** unless shape is wanted for its own sake. | HIGH |
| 2 | **Chip triggers** (§6) — largest measured lever vs elite. | HIGH |
| 3 | **Global over-prediction +1.08** at the top end (§4.3) — real, well-evidenced calibration defect. | HIGH |
| 4 | **`v4_max_starting_defenders` is a dead setting.** Read at `pre_deadline_run.py` into `rebuild_lineup_max`, but only reaches `solve_squad`/`solve_lineup` on the **wildcard/free-hit paths** — it never constrains the normal MILP path. Someone previously tried to cap defenders at 3 and it silently did nothing. Wire it in or remove it deliberately. | MED |
| 5 | Re-run the residual audit after GW6-8 when FWD n ≥ 30 (currently 10). | MED |
| 6 | **Palmer anchor decay** — `ep_next` 6.5 vs model 4.85. `official_weight = max(0.15, 0.75 − 0.10×gw_so_far)` decays to the 0.15 floor by GW7. Given §4.3's global over-prediction it is genuinely unclear which is closer to truth. Needs evidence, not a guess. | LOW |

---

## 8. Process incident — VM hot patch (AGENTS.md rule 3)

During this investigation the fix was applied **directly to `/opt/fpl-autopilot`
on the production VM**, with a `.backup-formation-20260914T105803Z/` directory
left beside the running service. `AGENTS.md` rule 3 forbids exactly this, and
names the 2026-09-02 incident it caused before.

Cause: the retired `lordirfan99/fpl-autopilot` repo was found first and assumed
to be authoritative; this monorepo and its `AGENTS.md` were not discovered until
after deployment. The owner identified the correct repo.

**Current VM state (untracked, ahead of git):**

* `optimizer/horizon_milp.py` — formation prior
* `jobs/pre_deadline_run.py` — caller wiring
* `config/settings.json` — `v4_formation_prior_weight: 1.0` *(gitignored by design)*
* `config/player_prefs.json` — `{"keep": [154]}` protecting Palmer *(gitignored)*

**Reconciliation required:** once this PR merges and deploys from a tag, the VM
must be redeployed from that tag (or reverted from the backup) so `/opt` carries
no untracked diff. `config/player_prefs.json` is a legitimate runtime config
using the supported `load_player_prefs()` mechanism and may stay.

### Why Palmer is protected

`Palmer 4.85 xPts @ £9.7m` vs `Rogers 5.05 @ £7.7m` → the optimizer saw +0.2 xPts
and +£2.0m and proposed selling. But Palmer is **86.9% elite-owned and 80.3%
elite-captained**, and was the card's own largest template gap. Selling the most-
captained player in the game during a CATCH phase is a template-risk error the
optimizer cannot see. Keeping him cost **0.19 xPts** and **saved a transfer**.

---

## 9. Environment notes

| Item | Reality |
|---|---|
| Live VM | `instance-20260412-121200`, zone **`us-central1-a`** |
| Stale doc | The retired repo's README says `us-central1-f`; that VM is **TERMINATED** |
| Runtime | `/opt/fpl-autopilot`, **not a git repo**, venv at `.venv/bin/python` |
| Retired repo | `lordirfan99/fpl-autopilot` is **archived/read-only** on GitHub |
| Local gcloud | Works. Invoke `cd .../google-cloud-sdk/lib && MSYS_NO_PATHCONV=1 python gcloud.py …` |

---

## 10. Method lessons

1. **Confirm which repo and which code is live before diagnosing.** A stale
   checkout produced two false root causes.
2. **`md5sum` local vs VM before editing** — production was 426 diff-lines ahead.
3. **Check that a field name exists** rather than trusting a plausible key.
4. **Measure before theorising.** The defender-bias story survived three messages
   and died instantly to `residuals.csv`.
5. **Read the account's own history** before recommending strategy.
6. **Distrust your own harness.** The first replay defaulted `cost` to 0, which
   would have "proven" every transfer was free.
7. **Sample size gates conclusions.** FWD n=10 over 3 GWs is not evidence.
8. **Read `AGENTS.md` first.**
