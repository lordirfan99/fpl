# Learned projection candidate — offline research, not a live release

`competitive-v4.4-walkforward-ridge` fits a regularized linear points predictor
from prior gameweeks. It does not change the canonical planner, captain policy,
terminal-cash objective, Telegram approval flags or FPL execution.

## Inputs and chronology

- Finalized official player-event history covering GW1–6: 3,883 rows.
- A player is forecast only after their metadata exists in a prior event.
- Training examples rebuild features using events before their own target GW.
- Predictors: lagged minutes, starts, xG, xA, points, bonus, goals, assists,
  position indicators and team-level lagged points/xG averages.
- Ridge strength is fixed at 12; no search against the reported evaluation tail.
- Aggregate decay is by gameweek, not player row order. Duplicate event/player
  rows are rejected; input-order invariance and future-row isolation are tested.
- Evaluation cohort is selected from prior cumulative minutes >=90. Zero-point
  target outcomes remain in the cohort.

This is a retrospective chronological replay, NOT proof of a pristine untouched
holdout: the same short season was examined during candidate development.
GW3–6 is the evaluated window; GW5–6 is a separately reported evaluation tail.
GW6 outcomes can train the next target, never the same target. GW7 outcomes are
not used. Historical CSV residuals lack independently verified deadline
provenance and are not used to establish superiority over the live model.

## Reproduced evaluation

For the GW5–6 prospective cohort (572 player-gameweek observations):

- Candidate MAE: **2.139801**; RMSE **2.951268**.
- Candidate bias: **-0.052424**; Spearman **0.364711**.
- Lagged player-points EWMA baseline MAE: **2.308351**.
- Lagged position-mean baseline MAE: **2.228107**.

These are comparisons to simple baselines, not evidence of beating the current
production model, elite managers or achieving a top-five-percent league finish.
Only two evaluation-tail events exist; rows are dependent within matches and
players. Do not interpret hundreds of rows as hundreds of independent weeks.

## Run

Use a private finalized JSONL history copy outside tracked `data/`:

```bash
python engine/jobs/evaluate_v44_candidate.py \
  --history /private/path/player_event_history.jsonl \
  --output-dir /private/path/model-evaluation
pytest -q -o addopts='' --disable-warnings
ruff check .
```

The evaluator writes `evaluation.json` and `report.md`. The numeric gate compares
MAE against both baselines and fails closed for missing/non-finite scores; it is
NOT a production promotion permission.

## Required before live adoption

1. Prospectively capture candidate and champion forecasts on identical inputs,
   with deadline, generated-at, source hashes and per-player availability.
2. Add fixture/opponent and injury inputs through a reviewed interface. Neither
   exists in this research predictor; no feasible squad/captain policy is claimed.
3. Run legal-team decision comparisons including transfers, hit costs, bank,
   formation, captain, chips and owner forced-out constraints.
4. Require both forecast and decision-policy evidence before promotion. Keep
   approval/execution safeguards unchanged.
5. Integrate through branch/PR, passing CI, reviewed tag and scoped installer;
   no direct VM edits.

The failed first heuristic candidate was removed from shipping code. Its local
research outputs remain outside the repository for audit.
