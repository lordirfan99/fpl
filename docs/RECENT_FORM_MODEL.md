# Recent attacking form (`recency-v1`)

The season-only V4 projection could retain an expensive player whose early haul
continued to dominate cumulative goals, assists and expected attacking returns.
The pre-deadline planner now reads the existing official player-event history
for both the champion and V4.2 candidate projection paths.

## Forecast behavior

- Blend 30% of the observed season per-90 attacking rate with 70% of the last
  four GWs. Within the recent window, observations have a three-GW half-life.
  Apply this separately to goals, assists, xG and xA, before the existing
  positional-prior shrinkage, fixture factors and official FPL anchor.
- Preserve the existing minutes, availability, defensive components, captain
  eligibility and legal transfer optimizer. No player names, IDs, prices or
  clubs are used as special-case forecast rules.
- Require at least three historical GWs, continuous recent coverage and 180
  minutes both overall and in the last three GWs. Missing expected stats, stale
  history, or non-finite values retain the season forecast and report an explicit
  fallback status. Never convert an unknown expected-stat field into a blank.
- Filter all attacking observations to GWs strictly before the first forecast
  GW. Reuse that same information set for the whole three-GW horizon.
- Report `single_haul_dependency` when at least five observed GWs show one old
  match contributing at least 60% of positive points, and the latest three-GW
  xGI/90 is below 60% of the season rate. This flag is explanatory: it does not
  subtract points or force a sale. Healthy underlying numbers can support a hold
  despite consecutive FPL blanks.

The existing horizon MILP already prices selling value, bank, free transfers and
hits jointly. It can choose a cheaper replacement with a slightly lower forecast
when the released money funds a larger improvement elsewhere. The change fixes
the forecasts feeding that decision rather than adding a points-per-price sell
rule. FPL autosubs still require non-appearance; poor actual points cannot trigger
a retrospective substitution.

## Evidence and evaluation

Bruno Fernandes (official element 426, Man Utd) had GW1–6 points of
`2, 23, 2, 2, 2, 1`. Using the retrieved GW1–6 history and published GW7–9
fixtures, the uncalibrated V4 GW7 mean changes from 4.568 to 3.913. Expected
minutes remain 82.6. The dependency flag is true; latest-three-GW xGI/90 is 0.330
against a season 0.673. These are an upcoming forecast comparison, **not** an
out-of-sample performance gain or an instruction to sell this player.

Regression tests cover a stale haul versus identical season totals with improving
recent form, healthy underlying numbers despite blanks, future-row exclusion,
missing/stale/invalid history, DGW minute exposure, both projection paths, and a
legal premium downgrade funding a second upgrade without hits. The sanitized
private packet carries the evidence; the canonical decision summary records it
for each owned player alongside whether that player was transferred out.

New predictions record `projection_features: recency-v1`. Calibration and V4.2
promotion evidence start separate `residuals_recency_v1.csv` and
`v42_residuals_recency_v1.csv` streams; old observations remain available for
audit. The existing minimum-GW, policy-outcome and explicit-owner-approval gates
still apply to V4.2 promotion. No automatic FPL execution is introduced.

The four-GW window, three-GW half-life and 70/30 blend are conservative initial
parameters, not tuned or proven optimal on a historical holdout. Subsequent
evaluation must use frozen deadline-time inputs and account for transfer hits,
turnover, and mistaken sales of players whose underlying numbers remained good.

## Tagged deployment and rollback

After merging green CI, dispatch `deploy-recency-model.yml` on `main` with a
reviewed `vYYYY.MM.DD-model-recency...` tag and mode `install`. The workflow checks
main ancestry and the newest required CI runs before creating or selecting the
tag. Its GITHUB_TOKEN-created tag does not deploy the website.

The scoped installer backs up the allowlisted modules, pauses/restores the
runner timer, refuses to replace files while the planner/reviewer runs, and
checks installed bytes and imports. Backfill uses only public, finished and
data-checked FPL events. A private `--dashboard-only --notifications-disabled`
refresh verifies recent-form evidence without replacing `pending_plan.json` or
submitting account changes. Private preview logs stay on the VM; Actions output
contains only release version and aggregate verification information.

If install/backfill/preview verification fails after module installation, restore
the saved modules automatically. Dispatch the same tag with mode `rollback` for
an explicit later rollback. The pre-release pending-plan fingerprint and timer
state must be preserved. Updating code alone does not mean production is active:
record the completed deployment run and verification in `docs/RUNBOOK.md`.
