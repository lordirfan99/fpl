# Phase D cutover — runbook

Run these in order from a **clean monorepo checkout of `main`** at the tag you cut.
Each step is reversible until the archive. The frozen legacy system is the safety net.

> The coding agent is gated from executing production deploys (Cloud Build / Cloud
> Run / scheduler) and cannot log in to Netlify. These are for the owner to run.

## 0. Pre-flight

```bash
git clone https://github.com/lordirfan99/fpl && cd fpl
# stage the snapshot fallback into the (gitignored) build context:
git -C /path/to/fpl-league-58005-scout archive origin/master data | tar -x
git tag v2026.09.03-cutover && git push origin v2026.09.03-cutover
```

Rollback target for the API (record before deploying):
`gcloud run services describe fpl-scout-api --region=us-central1 --project=irfan-374115 --format='value(status.traffic[0].revisionName)'`
→ was **`fpl-scout-api-00070-8kb`** at freeze.

## 1. API → Cloud Run

```bash
SHA=$(git rev-parse HEAD)
gcloud builds submit --project=irfan-374115 \
  --config=infra/cloudbuild.api.yaml \
  --substitutions=_GIT_SHA=$SHA \
  --gcs-source-staging-dir=gs://irfan-374115-fpl-github-build-source/source .
```

Verify:
```bash
curl -s https://fpl-scout-api-bztsnhv3ea-uc.a.run.app/health | python -m json.tool
# expect: status ok, revision == $SHA, execution_authority manual_fpl, writes_enabled false
curl -s https://fpl-scout-api-bztsnhv3ea-uc.a.run.app/ready   # expect ready: true
```
Rollback: `gcloud run services update-traffic fpl-scout-api --region=us-central1 --project=irfan-374115 --to-revisions=fpl-scout-api-00070-8kb=100`

## 2. Dashboard → Netlify (UI)

Netlify → site `fpl-scout-intelligence` → Site configuration → Build & deploy →
**Link to a different repository** → `lordirfan99/fpl`, base directory `web`.
Env vars (`FPL_API_BASE_URL`, `FPL_DATA_BASE_URL`) carry over. Trigger a deploy,
then load the site and click through a couple of pages.
Rollback: relink to `fpl-league-58005-scout`, base `web-next`.

## 3. VM → engine + bot

```bash
bash infra/deploy/vm-sync.sh v2026.09.03-cutover
```
The script backs up `model/optimizer/execution/jobs/bot` on the VM, syncs the
monorepo code, syntax-gates, restarts `fpl-telegram.service`, and does a
`pre_deadline_run.py --notifications-disabled` dry run (must exit 0).
Rollback: restore `/opt/fpl-autopilot/.monorepo-sync-backup-*` and
`systemctl restart fpl-telegram.service`.

## 4. Un-freeze — one at a time

```bash
# VM write-timers
gcloud compute ssh instance-20260412-121200 --zone us-central1-f --project irfan-374115 \
  --command 'sudo systemctl enable --now fpl-auto-runner.timer'
# then, after watching one cycle each:
#   fpl-daily-pull.timer   fpl-league-finalizer.timer

# Cloud Scheduler — resume, watch one run, then the next
for j in fpl-refresh-fixtures fpl-refresh-gameweek fpl-capture-journal fpl-monitor \
         fpl-live-league-refresh fpl-decision-refresh fpl-decision-final-window; do
  gcloud scheduler jobs resume "$j" --location=us-central1 --project=irfan-374115
done
```

## 5. Cleanup (after 24–48h of green)

```bash
gcloud run services delete fpl-scout-dashboard --region=us-central1 --project=irfan-374115
gh repo archive lordirfan99/fpl-league-58005-scout -y
gh repo archive lordirfan99/fpl-autopilot -y
```
Then update `docs/RUNBOOK.md` *Last known-good*.

## Follow-ups (not blocking)

- GitHub Actions deploy workflow for the API (needs WIF trust for `lordirfan99/fpl`
  added to `projects/733056042866/.../workloadIdentityPools/github-actions`).
- Rebuild the `fpl-live-refresh` / `fpl-scheduled-tasks` job images from the monorepo
  (currently still the scout-built images; fine while paused).
