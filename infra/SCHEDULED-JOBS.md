# Scheduled jobs

The five recurring data jobs used to run as **Cloud Run Jobs** triggered by
**Cloud Scheduler**. At ~1 vCPU each and two firing every 30 min they ran ~3×
over the Cloud Run free tier (~180k vCPU-s/month) → ~USD 10 / RM 47 a month.

`lordirfan99/fpl` is a **public** repo, so GitHub-hosted Actions minutes are
free and unmetered. Four of the five jobs need no stored credential (keyless
GCS write via Workload Identity Federation; public FPL endpoints otherwise),
so they moved back to Actions.

## Status

| Job | Runs on | State |
|---|---|---|
| `scheduled-monitor.yml` (`monitor`) | GitHub Actions `7,37 * * * *` | ✅ live + verified |
| `scheduled-fixtures.yml` (`fixtures`) | GitHub Actions `17 * * * *` | ⏳ blocked on WIF wiring |
| `scheduled-capture-journal.yml` (`capture-journal`) | GitHub Actions `17 * * * *` | ⏳ blocked on WIF wiring |
| `scheduled-finalize-gameweek.yml` (`finalize-gameweek`) | GitHub Actions `23 * * * *` | ⏳ blocked on WIF wiring |
| `fpl-live-league-refresh` (`refresh_live_leagues.py`) | Cloud Run, 30 min | see step 4 |

Each workflow just runs `python infra/scripts/run_scheduled_task.py <task>` —
identical to the container `CMD`. `schedule:` cron on Actions is best-effort
(can slip 5–15 min or skip under load); fine for hourly pulls + a health check.
Scheduled workflows auto-disable after 60 days with no repo commits.

Repo Actions **variables** are already set: `GCP_PROJECT_ID`,
`GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_DEPLOY_SERVICE_ACCOUNT`,
`FPL_SNAPSHOT_BUCKET`, `FPL_API_BASE_URL`, `FPL_SITE_URL`.

---

# REMAINING STEPS — run these (agent is classifier-blocked from every `gcloud` mutation below)

## 1. Wire WIF to trust the monorepo

The `github-actions` pool's `github` provider is bound only to the old repo
`lordirfan99/fpl-league-58005-scout`. Widen it and let the monorepo impersonate
the tasks SA (which already holds `roles/storage.objectAdmin` on the bucket):

```bash
gcloud iam workload-identity-pools providers update-oidc github \
  --project=irfan-374115 --location=global --workload-identity-pool=github-actions \
  --attribute-condition="assertion.repository=='lordirfan99/fpl-league-58005-scout' || assertion.repository=='lordirfan99/fpl'" \
  --quiet

gcloud iam service-accounts add-iam-policy-binding \
  fpl-scheduled-tasks@irfan-374115.iam.gserviceaccount.com --project=irfan-374115 \
  --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/733056042866/locations/global/workloadIdentityPools/github-actions/attribute.repository/lordirfan99/fpl"
```

(Console equivalent for the first command: IAM & Admin → Workload Identity
Federation → `github-actions` → provider `github` → edit **Attribute
conditions**.)

## 2. Verify the three GCS workflows

```bash
for w in scheduled-fixtures scheduled-capture-journal scheduled-finalize-gameweek; do
  gh workflow run "$w.yml" --repo lordirfan99/fpl
done
# wait ~2 min, then:
gh run list --repo lordirfan99/fpl --limit 6
```

All three should reach `run_scheduled_task` and exit 0. `finalize-gameweek`
no-ops quickly unless a newly finished + data-checked GW is unpublished.
Confirm freshness:

```bash
gcloud storage cat gs://irfan-374115-fpl-snapshots/snapshots/bootstrap_cache.json \
  | python -c "import json,sys;p=json.load(sys.stdin);print(p['_meta'])"
```

## 3. Tear down the migrated Cloud Run / Scheduler jobs

Only after step 2 is green:

```bash
for j in fpl-refresh-fixtures fpl-refresh-gameweek fpl-capture-journal fpl-monitor; do
  gcloud scheduler jobs delete "$j" --project=irfan-374115 --location=us-central1 --quiet
  gcloud run jobs delete "$j"       --project=irfan-374115 --region=us-central1   --quiet
done
```

The `fpl-scheduled-tasks` image in Artifact Registry is then unused — leave it
until `fpl-live-league-refresh` is also off Cloud Run, then prune the repo.

## 4. `fpl-live-league-refresh` — trim it (default) or move it

It needs GCS **write** + imports `api/app/live_fpl`. The VM's attached SA is
`devstorage.read_only`-scoped, so the VM can't publish without stopping the
instance to widen scopes (a SportMania outage) or a key file on disk.

**Default — keep on Cloud Run, trimmed** (keyless, exact timing, ~RM 6–12/mo):

```bash
# halves the CPU cost on its own
gcloud run jobs update fpl-live-league-refresh \
  --project=irfan-374115 --region=us-central1 \
  --cpu=0.5 --memory=512Mi --max-retries=1

# optional extra saving: drop the overnight-MYT polls (no live PL then)
gcloud scheduler jobs update http fpl-live-league-refresh \
  --project=irfan-374115 --location=us-central1 --schedule="*/30 8-23 * * *"
```

**Alternatives** if you want RM 0: VM + a scoped `fpl-live-refresh@` key at
`/opt/fpl-scout/` with `GOOGLE_APPLICATION_CREDENTIALS`; or stop the VM, set its
access scope to `cloud-platform`, grant the compute SA `objectAdmin`.

---

`infra/legacy-workflows/*.yml` stay as `workflow_dispatch`-only manual fallbacks.
