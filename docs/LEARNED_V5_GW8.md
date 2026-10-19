# GW8 learned-v5 release

This release adds an explicit production projection mode, `learned-v5.0`, backed by
one cached two-stage fit: a fixed `RandomForestClassifier` (128 trees, depth 5,
leaf 8, `n_jobs=1`) estimates DNP/cameo/starter state and per-state Ridge models
(alpha 10) estimate points. The fit is shared across the catalog and cached by a
source fingerprint; it is not refit per player.

## Data contract

- Training joins pre-deadline snapshots to finalized `player_event_history.jsonl`.
- Only current-season GW2 through the target's prior GW are eligible. GW1 is
  excluded because its aggregate is prior-season data.
- Activation fails closed if fewer than 40 eligible finalized rows, a snapshot is
  missing/invalid, or the pinned Python dependencies cannot import.
- The plan exposes `learned_model.active`, activation source, training gameweeks,
  training row count, fallback status, and `projection_features=learned-v5.0`.
- The three-GW forecast uses one current-cutoff catalog and fixture snapshot.
  Future fixture difficulty is a bounded fixed adjustment and preserves doubles;
  no future capture or outcome is copied into features.
- Residual standard deviation and state probabilities are exposed. `confidence` is
  intentionally `null`; the release does not fabricate confidence.

## Planning and approval safeguards

The existing canonical plan hash, input fingerprint, account/authentication gates,
lineup legality, cash-flow validation, transfer limits and horizon MILP remain the
execution boundary. In learned-v5 mode the MILP captain term is mean-only (no
fixed adversarial DEF ban); lineup eligibility and legality gates remain unchanged.
The learned model changes projection values only; it does not
submit FPL writes or activate chips. A chip can enter the rebuild path only when
official FPL reports it already active for the target GW.

`config/player_prefs.json` may contain `manual_out` plus `manual_out_gw`. For GW8,
Bruno Fernandes (`426`) is a scoped forced-out id and Palmer (`154`) remains in
`keep`; the solver must produce the forced outgoing move and fails closed if it
cannot. The preference is not auto-cleared by this release: cleanup must happen
only after a verified external execution readback or when its scoped GW expires.

No claim is made that this reaches a top-five or 100-point outcome. Six finalized
GWs are a small retrospective training base, not a prospective performance proof.

## Tagged install / rollback

From a clean checkout of the tagged release:

```bash
bash infra/deploy/install-learned-v5-model.sh v2026.10.19-gw8-learned-v5
bash infra/deploy/install-learned-v5-model.sh v2026.10.19-gw8-learned-v5 --rollback
```

The installer is allowlisted to `engine/model/learned_v5_projection.py` and
`engine/jobs/pre_deadline_run.py`, backs them up under
`/var/backups/fpl-learned-v5/<commit>`, verifies the tag and source hashes, stops
and restores the auto-runner timer, and checks that `pending_plan.json` and
`settings.json` hashes are unchanged. It never writes FPL or replaces private
settings/state. Dependency install is a separate pinned wheel-only step for
`scikit-learn`, `joblib`, and `threadpoolctl`; rollback restores code but does not
uninstall those packages.

The preflight requires at least 256 MiB `MemAvailable` and 300 MiB free disk,
reuses the installed NumPy/SciPy, sets BLAS/threadpool caps to one, and does not
upgrade NumPy/SciPy or install browser dependencies. Parent deployment must
verify the real VM's higher measured headroom before running the tagged installer.

## Verification

CI should run `pytest`, `ruff check .`, `python -m py_compile` on changed Python,
`bash -n infra/deploy/install-learned-v5-model.sh`, JSON validation, and
`git diff --check`. The learned-v5 regression module covers prior-only training,
GW1 exclusion, target-outcome mutation, double-gameweek adjustment, explicit
activation, fail-closed missing training, canonical approval-hash preservation,
and the Bruno-out/Palmer-keep preference contract.
