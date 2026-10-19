# Offline model tournament (GW3–6)

This is research code only. It does not change the canonical planner, bot,
approval path, FPL writes, or deployment files.

## Candidate tournament

The prespecified manifest is saved at
`/home/ubuntu/.hermes/cache/scratch/fpl-revamp/tournament/manifest.json`.
The new competitor is a two-stage model: Bayesian minutes-state probabilities
(DNP/cameo/starter) followed by conditional points with positional shrinkage.
The three fixed configurations were declared before execution; none was tuned
against GW3–6. DNP rows remain in the training data as zero-point outcomes.
Every target GW uses only rows with `gw < target_gw`, and the player universe is
limited to players with prior metadata.

The decision replay keeps the historical 15-player squads, transfer/chip state,
and transfer costs. It selects a legal XI and captain inside those squads. This
is not a transfer-policy backtest.

| Model | GW3 | GW4 | GW5 | GW6 | Total | Gain vs actual |
|---|---:|---:|---:|---:|---:|---:|
| Actual historical team | 28 | 50 | 47 | 73 | 198 | — |
| two_stage_bayes | 28 | 53 | 59 | 79 | 219 | **+21** |
| two_stage_stable | 28 | 47 | 64 | 79 | 218 | **+20** |
| ridge_two_stage_ensemble | 25 | 46 | 64 | 79 | 214 | **+16** |
| v4.4 ridge reference | 27 | 52 | 64 | 69 | 212 | **+14** |

The strongest fixed-squad replay is therefore `two_stage_bayes`, with exact
captains: B. Fernandes (GW3), B. Fernandes (GW4), Groß (GW5), Groß (GW6).
Its forecast MAE by target GW was 1.766875, 1.780690, 1.656743, and 1.554077
on cohorts of 626, 654, 659, and 667 player-GW rows respectively. These are
retrospective descriptive metrics, not an untouched holdout or a promotion
recommendation.

## Ceiling and interpretation

The cached mathematical hindsight ceiling for the same squads is 264 points
versus 198 actual: only **+66** is feasible in this scope. Therefore a +100
claim is impossible for a fixed-squad GW3–6 replay, regardless of model quality.
The new candidate improves the replay by 7 points over v4.4 (+21 versus +14),
but it does not meet +100 and does not establish that transfers would improve.

Historical pre-deadline bootstrap archives were reported as available on the VM
by the parent task. This run deliberately did not copy or use them: the agreed
priority was a small, runnable, leakage-safe fixed-squad tournament. A future
full dynamic replay must verify each archive's capture code/timestamp and
content against actual totals, then reconstruct prices, availability, bank,
free-transfer state, legal squad constraints, and transfer costs. It must be a
separate tournament, not mixed into this fixed-squad result.

Artifacts:

- `engine/model/competitive_two_stage_candidate.py`
- `engine/jobs/run_model_tournament.py`
- `engine/tests/test_competitive_two_stage_candidate.py`
- `/home/ubuntu/.hermes/cache/scratch/fpl-revamp/tournament/manifest.json`
- `/home/ubuntu/.hermes/cache/scratch/fpl-revamp/tournament/tournament.json`
