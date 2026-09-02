# Production deployment and automation

For the current verified state (including live GW2 data, snapshot availability and
deployment evidence), see [`docs/CURRENT_STATUS.md`](docs/CURRENT_STATUS.md).

## Live services

| Component | Production target |
|---|---|
| Next.js dashboard | Netlify site `fpl-scout-intelligence` |
| Read API | Cloud Run service `fpl-scout-api`, region `us-central1` |
| Autopilot | Compute Engine VM `instance-20260412-121200`, zone `us-central1-f` |
| Bridge | `fpl-dashboard-bridge.service`, local port `8787` |
| Public bridge path | `https://sportmania.duckdns.org/fpl-autopilot/` |
| Bridge token | Secret Manager `fpl-dashboard-read-token` |

## Frontend: automatic Netlify deployment

Netlify is connected to `lordirfan99/fpl-league-58005-scout`, branch `master`.
The root `netlify.toml` sets `web-next` as the base and runs `npm run build`.

The site must retain this production environment variable:

```text
FPL_API_BASE_URL=https://fpl-scout-api-bztsnhv3ea-uc.a.run.app
```

After that, pushing a reviewed commit to `master` deploys automatically. Pull requests
receive validation and can receive Netlify previews without a production deploy.

## GCP: one-time keyless GitHub authentication

The deployment workflow uses GitHub OIDC and Google Workload Identity Federation. Do
not create or store a service-account JSON key.

Create a deployment service account and grant only the ability to submit Cloud Builds:

```powershell
$project = 'irfan-374115'
$projectNumber = gcloud projects describe $project --format='value(projectNumber)'
$serviceAccount = "fpl-github-deployer@$project.iam.gserviceaccount.com"
$buildAccount = gcloud builds get-default-service-account --project $project

gcloud iam service-accounts create fpl-github-deployer --project $project --display-name 'FPL GitHub deployer'
gcloud projects add-iam-policy-binding $project --member "serviceAccount:$serviceAccount" --role roles/cloudbuild.builds.editor
gcloud projects add-iam-policy-binding $project --member "serviceAccount:$serviceAccount" --role roles/serviceusage.serviceUsageConsumer
gcloud iam service-accounts add-iam-policy-binding $buildAccount --project $project --member "serviceAccount:$serviceAccount" --role roles/iam.serviceAccountUser
gcloud storage buckets create "gs://${project}-fpl-github-build-source" --project $project --location us-central1 --uniform-bucket-level-access
gcloud storage buckets add-iam-policy-binding "gs://${project}-fpl-github-build-source" --member "serviceAccount:$serviceAccount" --role roles/storage.admin

gcloud iam workload-identity-pools create github-actions --project $project --location global --display-name 'GitHub Actions'
gcloud iam workload-identity-pools providers create-oidc github `
  --project $project `
  --location global `
  --workload-identity-pool github-actions `
  --display-name 'lordirfan99/fpl-league-58005-scout' `
  --issuer-uri 'https://token.actions.githubusercontent.com' `
  --attribute-mapping 'google.subject=assertion.sub,attribute.repository=assertion.repository' `
  --attribute-condition "assertion.repository=='lordirfan99/fpl-league-58005-scout'"

$principal = "principalSet://iam.googleapis.com/projects/$projectNumber/locations/global/workloadIdentityPools/github-actions/attribute.repository/lordirfan99/fpl-league-58005-scout"
gcloud iam service-accounts add-iam-policy-binding $serviceAccount `
  --project $project `
  --role roles/iam.workloadIdentityUser `
  --member $principal
```

Set these GitHub repository variables:

```powershell
gh variable set GCP_PROJECT_ID --body 'irfan-374115'
gh variable set GCP_DEPLOY_SERVICE_ACCOUNT --body 'fpl-github-deployer@irfan-374115.iam.gserviceaccount.com'
gh variable set GCP_WORKLOAD_IDENTITY_PROVIDER --body "projects/$projectNumber/locations/global/workloadIdentityPools/github-actions/providers/github"
```

`deploy-api.yml` then submits `cloudbuild.api.yaml` automatically whenever API code,
fixture data or completed-GW snapshots change.

## VM bridge: automatic reviewed-code sync

Install the auto-update service once:

```bash
sudo install -o root -g root -m 0755 integration/gcp-bot/sync_dashboard_bridge.sh /usr/local/sbin/fpl-dashboard-bridge-sync
sudo install -o root -g root -m 0644 integration/gcp-bot/fpl-dashboard-bridge-sync.service /etc/systemd/system/
sudo install -o root -g root -m 0644 integration/gcp-bot/fpl-dashboard-bridge-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fpl-dashboard-bridge-sync.timer
```

Every five minutes it downloads the reviewed `master` bridge, compiles it, compares it
with the installed version, restarts only when changed, verifies `/health`, and rolls back
if the new process is unhealthy.

## Completed-gameweek data

`refresh-gameweek.yml` runs every four hours but performs work only when FPL exposes a
new gameweek with both `finished=true` and `data_checked=true`. It uses the corrected
multi-league collector, validates both league snapshots and commits atomically. That
commit automatically triggers Netlify and Cloud Run deployment.

The workflow can be manually rerun with a GW override for recovery. Normal weekly
operation requires no intervention.

## Health checks

```powershell
Invoke-RestMethod https://fpl-scout-api-bztsnhv3ea-uc.a.run.app/health
Invoke-RestMethod https://fpl-scout-api-bztsnhv3ea-uc.a.run.app/v1/live/team
Invoke-RestMethod https://sportmania.duckdns.org/fpl-autopilot/health
```

On the VM:

```bash
systemctl is-active fpl-telegram.service
systemctl is-active fpl-dashboard-bridge.service
systemctl is-active fpl-dashboard-bridge-sync.timer
systemctl list-timers --all | grep fpl
journalctl -u fpl-dashboard-bridge-sync.service -n 50 --no-pager
```

The control-centre payload must report `writes_enabled: false` and
`execution_authority: telegram`.

## Rollback

- Netlify: publish the previous known-good deploy from the Netlify deployment history.
- Cloud Run: route traffic back to the prior revision.
- Bridge: restore `/opt/fpl-autopilot/webapp/dashboard_bridge.py.auto-rollback` and restart
  `fpl-dashboard-bridge.service`.
- Data: revert the automated `data: finalize GW...` commit; CI will redeploy the last
  validated snapshots.
