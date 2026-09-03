# Scheduled jobs

The five recurring data jobs used to run as **Cloud Run Jobs** triggered by
**Cloud Scheduler**. At ~1 vCPU each and two firing every 30 min, they blew
through the Cloud Run free tier (~180k vCPU-s/month) roughly 3×, costing
~USD 10 / RM 47 a month.

`lordirfan99/fpl` is a **public** repo, so GitHub-hosted Actions minutes are
free and unmetered. Four of the five jobs need no credentials beyond a keyless
GCS write, so they moved back to Actions.

## Now on GitHub Actions

| Workflow | Task | Cron (UTC) | Was |
|---|---|---|---|
| `scheduled-fixtures.yml` | `run_scheduled_task.py fixtures` | `17 * * * *` | `fpl-refresh-fixtures` |
| `scheduled-finalize-gameweek.yml` | `... finalize-gameweek` | `23 * * * *` | `fpl-refresh-gameweek` |
| `scheduled-capture-journal.yml` | `... capture-journal` | `17 * * * *` | `fpl-capture-journal` |
| `scheduled-monitor.yml` | `... monitor` | `7,37 * * * *` | `fpl-monitor` |

GCS auth is **Workload Identity Federation** — no service-account key is stored.
`schedule:` cron on Actions is best-effort (can slip 5-15 min or skip under
load); acceptable for hourly data pulls and a health check. Scheduled workflows
are auto-disabled after 60 days with no repo commits.

### Required repo Actions *variables* (Settings → Secrets and variables → Actions → Variables)

| Variable | Value |
|---|---|
| `GCP_PROJECT_ID` | `irfan-374115` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `projects/733056042866/locations/global/workloadIdentityPools/github-actions/providers/github` |
| `GCP_DEPLOY_SERVICE_ACCOUNT` | `fpl-scheduled-tasks@irfan-374115.iam.gserviceaccount.com` |
| `FPL_SNAPSHOT_BUCKET` | `irfan-374115-fpl-snapshots` |
| `FPL_API_BASE_URL` | `https://fpl-scout-api-bztsnhv3ea-uc.a.run.app` |
| `FPL_SITE_URL` | `https://fpl-scout-intelligence.netlify.app` |

### One-time GCP wiring

```bash
# widen the WIF provider to trust the monorepo (it was bound to the old scout repo)
gcloud iam workload-identity-pools providers update-oidc github \
  --project=irfan-374115 --location=global --workload-identity-pool=github-actions \
  --attribute-condition="assertion.repository=='lordirfan99/fpl-league-58005-scout' || assertion.repository=='lordirfan99/fpl'"

# let the monorepo impersonate the tasks SA (which already has objectAdmin on the bucket)
gcloud iam service-accounts add-iam-policy-binding \
  fpl-scheduled-tasks@irfan-374115.iam.gserviceaccount.com --project=irfan-374115 \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/733056042866/locations/global/workloadIdentityPools/github-actions/attribute.repository/lordirfan99/fpl"
```

## Still on Cloud Run — `fpl-live-league-refresh`

Runs `refresh_live_leagues.py` every 30 min. It needs **GCS write** and imports
`api/app/live_fpl`. The VM's attached service account is
`devstorage.read_only`-scoped, so the VM cannot publish snapshots without either
stopping the instance to widen scopes (disrupts the co-hosted SportMania bots)
or placing a long-lived SA key on disk. Options:

- **Keep on Cloud Run, trimmed** — drop to `--cpu=0.5`, and/or 30-min cadence
  only during live-match windows. ~RM 6-12/month, keyless, exact timing.
- **VM + scoped key** — key for `fpl-live-refresh@` (already `objectAdmin` on the
  bucket) at `/opt/fpl-scout/`, `GOOGLE_APPLICATION_CREDENTIALS`. RM 0, key on disk.
- **VM + wider instance scopes** — stop VM, set access scope to `cloud-platform`,
  grant the compute SA `objectAdmin`. RM 0, keyless, but one SportMania outage
  and a broader VM identity.

## Teardown once Actions runs are verified green

```bash
for j in fpl-refresh-fixtures fpl-refresh-gameweek fpl-capture-journal fpl-monitor; do
  gcloud scheduler jobs delete "$j" --project=irfan-374115 --location=us-central1 --quiet
  gcloud run jobs delete "$j" --project=irfan-374115 --region=us-central1 --quiet
done
# fpl-scheduled-tasks image is then unused; keep fpl-live-refresh until that job moves too
```

`infra/legacy-workflows/*.yml` stay as `workflow_dispatch`-only manual fallbacks.
