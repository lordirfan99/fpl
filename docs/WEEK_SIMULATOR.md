# This Week packet and transfer simulator

Inspired by the compact weekly decision page and what-if flow in
[fantaszy-szentre](https://github.com/mrsyafiqzamri-lgtm/fantaszy-szentre), inspected
at `863143d44087e27fee4bce82c14b7f2016e3a628`. No code or model parameters copied.

The browser reads `/api/week?league=58005` (or `131997`) once. Its NDJSON stream
contains independent public league and private plan sections. The combined
response is always private/no-store and varies on Cookie. Public league reads
retain the existing five-minute upstream cache. Owner authentication and the
backend's fingerprint, timestamp and deadline checks still gate the private
section. Anonymous users receive a signed-out section with no packet. Periodic
verification sends only plan identity and check timestamp to the browser.

The private exporter adds a slim allowlisted projection catalog captured in the
same run as the plan. There are no simulator FPL calls or on-demand model runs.
Old packets remain compatible, offering only players present in their evidence.
Transfers use actual owned selling prices and captured purchase prices in integer
tenths. Packages must respect position quotas, budget, duplicate and club limits.
Free transfers use account limit minus already-made transfers, preserving zero;
unlimited status requires an active Wildcard or Free Hit. Further transfers cost
four points each, deducted once across the horizon.

Both the hold and draft squad select their best legal XI and captain separately
each GW, assuming the owner sets that lineup. This is a raw points comparison,
not risk-adjusted planner utility. Missing projections block claims of gain.
Bench Boost and Triple Captain apply only in the first GW. Free Hit stops after
one GW because the permanent returning squad is not available here. The tool
does not estimate future transfers, autosubs or future prices and stores no draft
or account details in browser storage. Telegram approval remains separate.

## Release and rollback

After green PR CI and merge, tag `v2026.10.07-week-simulator`. Web deployment now
accepts release tags, including manual dispatch on a tag, matching AGENTS.md.
From a clean checkout of that tag, run
`bash infra/deploy/install-dashboard-simulator.sh v2026.10.07-week-simulator`.
This stops the runner timer, refuses an in-flight planner, backs up and atomically
installs only `model/dashboard_packet.py`, then restores the timer's prior state.
The API and executable pending plan are untouched.

To populate a current full catalog, use the existing non-executable job as user
fpl with `FPL_AUTOPILOT_HOME=/opt/fpl-autopilot`:
`/opt/fpl-autopilot/.venv/bin/python /opt/fpl-autopilot/jobs/pre_deadline_run.py
--dashboard-only --notifications-disabled`, followed by
`/opt/fpl-autopilot/.venv/bin/python /opt/fpl-autopilot/jobs/dashboard_account_check.py`.
Stop the auto-runner timer and wait for active jobs before manual preview, then
restore its prior state. A failed or stale capture must stay unavailable.

Rollback the exporter from the same release checkout by adding `--rollback`;
the previous file is under `/var/backups/fpl-dashboard-simulator/<release SHA>`.
Redeploy the preceding known-good web tag `v2026.10.07-journal-recovery` (its web tree matches the previously published
`40afcc6d40f551e208082a5a5cb9e4caffb30f04`). The exporter
addition is backward compatible with the earlier dashboard.

## Verification

Python packet tests assert allowlisting, no mutation and unchanged plan identity.
Simulator unit cases cover joint budgets, actual selling prices, captain and XI
changes, zero FT, one-time hits, missing projections, club/position/duplicate
violations, and chip horizon handling. Browser tests cover owner/non-owner
access, mobile/desktop rendering and accessibility, independent section arrival,
the single initial request, package edits without network calls, and compact
heartbeat identity. Production release evidence is recorded in the runbook.
