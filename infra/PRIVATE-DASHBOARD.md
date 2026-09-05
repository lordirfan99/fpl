# Private dashboard release

This is an optional, read-only display of the canonical Telegram plan, not a
new optimizer or execution channel. Config absent means disabled. Personal
packets must never enter `data/` build contexts, public snapshots or journal exports.

## Prerequisites (deployment is blocked until these are verified)

- Owner Google OAuth web client; production callback:
  `https://fpl-scout-intelligence.netlify.app/api/auth/callback/google`.
- Netlify server-only `AUTH_GOOGLE_ID`, `AUTH_GOOGLE_SECRET`, `AUTH_SECRET`,
  `AUTH_URL=https://fpl-scout-intelligence.netlify.app`, `AUTH_TRUST_HOST=true`,
  `FPL_OWNER_EMAIL=azwariirfan@gmail.com`, `FPL_DASHBOARD_READ_TOKEN` (random >=32 chars).
  Production secrets must not be made available to untrusted deploy previews.
- Separate regional private GCS bucket with uniform bucket-level access and
  public access prevention ENFORCED. Never reuse `FPL_SNAPSHOT_BUCKET`.
- VM service identity: bucket-scoped object user + bucket metadata read;
  API service identity: bucket-scoped object viewer + bucket metadata read.
  Neither anonymous users nor Netlify receives direct bucket permissions.
- API server-only `FPL_PRIVATE_DASHBOARD_BUCKET` and the same read token, stored
  through Secret Manager. Token authorizes only GET `/v1/private/dashboard/current`.
  The standard API build uses `--update-env-vars`, so later tagged deployments
  preserve these separately managed bindings instead of clearing them.
- VM `config/dashboard.json`, owner fpl, mode 0600, contains only
  `{"private_bucket":"<private-bucket-name>","public_snapshot_bucket":"<existing-public-bucket-name>"}`.
  Both identities are required and must differ; obtain the public identity from
  the deployed collector configuration, not an unset planner environment variable.
  No FPL credentials leave the VM.

## Reviewed release installation

Use `infra/deploy/install-private-dashboard.sh <tag>` from a clean tagged clone
on the active VM in us-central1-a. Stop the existing auto-runner timer first and
wait for any planner to finish. The installer requires pre-provisioned configuration,
checks dependencies and backs up every affected runtime file. It does not restart
the bot or change execution settings. Restore the prior auto-runner timer state
after verification. The new account-check timer must be enabled only after the
private bucket and API unauthorized checks pass.

The existing planner publishes an allowlisted packet only after validating and
saving the canonical plan. The 15-minute checker independently reads `my-team`
and the pending plan id; it never optimizes, sends a card or writes FPL actions.
An old plan, changed account, superseded plan, missing check, failed check,
expired deadline or stale source returns `packet: null`, never a hold advice.
Publication errors do not interrupt the Telegram workflow. A failed upload cannot
extend freshness: the API rejects checks older than 20 minutes.

## Acceptance and rollback

Require unauthorized API/Next.js requests to return 401 with `private, no-store`;
verify bucket anonymous access denied and owner Google sign-in accepted while
another account is denied. Check cache isolation with two browser sessions.
Compare dashboard plan id, lineup and captain with the VM pending plan; confirm
zero FPL writes and no notification from the verification job. Test failed and
changed account checks before releasing the UI.

Rollback: stop/disable the new account-check timer, stop auto-runner and wait
for the planner, then run the tagged installer with `--rollback`. Redeploy the
previous API/web tag, restore the prior auto-runner state, and retain private
artifacts for diagnosis (never copy them into public storage). Record actual
release, checks and rollback backup path in RUNBOOK; code merged is not deployed.
