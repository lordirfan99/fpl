# Leakage-safe FPL evaluation

The evaluator keeps forecast evidence separate from mutable operational plans.
Each canonical file is keyed by `GW + model_version + projection_features`:

`data/processed/forecast_capture_gwN_<model>_<features>.json`

A capture is accepted only when `generated_at` and `feature_as_of` are strictly
before the official deadline, timestamps carry a timezone, and a source
fingerprint plus explicit model/feature versions are present. A same-GW rerun
preserves the first capture; it cannot replace it with a later or post-deadline
snapshot. Actual points are never written into a forecast capture.

Each capture includes two same-time baselines:

- `official_ep_next`: the official FPL estimate in the same pre-deadline
  bootstrap snapshot;
- `lagged_position`: prior-season position baseline scaled by forecast minutes.

`engine/jobs/evaluate_frozen_forecasts.py` scores the captures after official actuals
are available. It accepts an explicit JSON map of player id to points and writes
one version-separated evaluation report, preserving an existing report on rerun.

full population and a **prospective** decision cohort (`predicted >= 4` and
`expected_minutes >= 60`). It does not select rows using actual minutes or
actual points. Decision metrics separately report recommended XI/captain,
bench points and transfer net-of-hit. Executed-team results remain
`unavailable` unless a verified executed-picks artifact is supplied; a stored
recommendation is not treated as an execution.

`competitive-v4.2-shadow` uses its own immutable capture key and remains a
shadow comparison. This change does not alter production coefficients,
selection, promotion or the FPL write/approval boundary.
