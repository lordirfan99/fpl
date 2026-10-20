# Post-gameweek reporting: official attribution

This patch changes reporting only. The live projection, pending approval plan,
execution flags, credentials and actual FPL team are outside its write scope.

## Corrections

- Read the official entry-history `current` array; retain the legacy `history`
  wrapper as a compatibility fallback. Report actual overall-rank change rather
  than incorrectly calling `rank_sort` a rank delta.
- Read historical `entry/{entry}/event/{gw}/picks/` and use its actual chip and
  effective captain multiplier, including vice fallback. A frozen proposal with
  `chip:null` cannot override an official Triple Captain execution. Report the
  mismatch without inventing its activation path.
- Keep planned-XI raw points, captain extras, official weighted-pick totals and
  entry totals distinct. Captain hindsight gaps use only the extra multiplier
  (normal captain1 extra, Triple Captain2), not the full player multiplier.
- Do not compare the full15 `target_xpts` with an entry's counting score as if they
  measured the same quantity. Triple Captain's marginal realized benefit over a
  normal captain is shown separately from the total captain contribution.
- Describe forecast residuals as observations. A residual alone cannot identify
  luck, bad forecasts or a sound process. Current injury flags also do not prove
  when an injury started. Bench raw totals are not recoverable lineup gains.
- Show the historical projection version/features, so a legacy forecast is not
  misattributed to a newer active model.

## Safe replay

```sh
cd /opt/fpl-autopilot
sudo -u fpl env HOME=/home/fpl FPL_AUTOPILOT_HOME=/opt/fpl-autopilot \
  .venv/bin/python jobs/post_gw_review.py 7 --report-only
```

`--report-only` performs official read requests and prints the corrected report.
It does not write feature history, residual CSVs, shadow scores, forecasts or
approval plans, and contains no FPL squad/chip write path. Normal scheduled
post-GW persistence remains unchanged, including version-specific residual
routing. This patch does not silently rewrite old calibration datasets.

## Deployment

Use a clean reviewed tag and `infra/deploy/install-postgw-report.sh <tag>`.
Only `jobs/post_gw_review.py` and `jobs/post_gw_audit.py` are allowlisted. The
installer backs up their exact previous bytes, serializes against an active
report/auto-runner, preserves the pending-plan/settings hashes and restores only
the original auto-runner timer state. `--rollback` restores those two files.
There is no bot restart, model activation or dependency upgrade.

Verify a real report-only replay with hashes of settings, pending plan, historical
forecast/plan and calibration files before/after. Compare the official weighted
score with entry history. Treat chip provenance as unknown unless actual execution
logs/approval receipts identify the path; temporal proximity alone is not proof.
