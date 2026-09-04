# Live league refresh on the existing VM

Owner decision, 4 September 2026: run live collection on the existing VM;
do not provision Cloud Scheduler or another Cloud Run collector. API hosting
stays on Cloud Run. Existing GitHub Actions fixture/journal/monitor jobs stay
where they are; this migration replaces only the broken live collector path.

## Evidence and release status

At 08:55 UTC on 4 September the old `fpl-live-league-refresh` scheduler was
enabled but calling the retired `fpl-scheduled-tasks.../tasks/live-refresh`
service and receiving 404. The remaining Cloud Run job last completed on
2 September. This PR prepares a replacement; it does not mean it is deployed.

The new systemd timer preserves `07:00–23:30 UTC`, every 30 minutes (the
old schedule is UTC, not Malaysia time). A oneshot service cannot overlap
itself. Each run has a 20-minute timeout, 50% of one CPU and a 512 MB memory
limit to protect the other workloads on this VM. Output goes to journald.
It collects public FPL picks only and publishes the existing GCS live manifest
contract. It has no FPL session and cannot submit transfers or team changes.

## 1. Review, merge, tag

Require green CI and review first. Deploy from a release tag containing this
change, with a clean checkout matching that tag. Record the actual tag and SHA
in `docs/RUNBOOK.md` after deployment; never describe an untested tag as good.

## 2. Publishing access: maintenance window required

The VM currently uses `733056042866-compute@developer.gserviceaccount.com`
with `devstorage.read_only`. ADC uploads will fail even if IAM allows writing.
The account already has project Editor (observed 4 September); do not add
another broad IAM grant or a service-account key. Changing this shared VM's
identity could break unrelated workloads, so retain its identity and other
scopes and replace only the storage read-only scope with read-write.

This requires a stop/start and interrupts all VM-hosted services. Schedule
that maintenance after review, outside the FPL deadline window. Capture the
current instance configuration first. The following are operator release steps,
not actions performed by this PR:

```bash
gcloud compute instances describe instance-20260412-121200 --project=irfan-374115 --zone=us-central1-f --format=json
gcloud compute instances stop instance-20260412-121200 --project=irfan-374115 --zone=us-central1-f
gcloud compute instances set-service-account instance-20260412-121200 \
  --project=irfan-374115 --zone=us-central1-f \
  --service-account=733056042866-compute@developer.gserviceaccount.com \
  --scopes=https://www.googleapis.com/auth/devstorage.read_write,https://www.googleapis.com/auth/logging.write,https://www.googleapis.com/auth/monitoring.write,https://www.googleapis.com/auth/service.management.readonly,https://www.googleapis.com/auth/servicecontrol,https://www.googleapis.com/auth/trace.append
gcloud compute instances start instance-20260412-121200 --project=irfan-374115 --zone=us-central1-f
```

Recheck the scopes against the captured configuration before applying: do not
overwrite changes made since this audit. The existing Editor role gives this
VM broader storage access than this collector needs; narrowing shared-VM IAM
requires a separate workload inventory. The storage scope above deliberately
does not enable `cloud-platform`. Verify other VM services recover and check
the external IP/DNS after restart, since an ephemeral external IP can change.

## 3. Install and prove one run

On the VM, from the clean tagged checkout:

```bash
bash infra/deploy/install-live-refresh.sh <release-tag>
sudo systemctl start fpl-live-refresh.service
systemctl show fpl-live-refresh.service -p Result -p ExecMainStatus
sudo journalctl -u fpl-live-refresh.service -n 50 --no-pager
```

Require exit 0 and a publication log for **both** leagues, 58005 and 131997.
Check `gs://irfan-374115-fpl-snapshots/live/league<ID>/current.json` for a new
`captured_at`, matching expected/hydrated counts, and a complete referenced
snapshot. Do not enable the timer if either league fails. No card is sent and
no FPL write is made by this collector.

The installer creates an isolated release and Python environment under
`/opt/fpl-live-refresh/releases/<SHA>`; it does not touch the bot/engine code,
their virtual environment, or credentials. It installs units but does not
start or enable the timer. ADC uses the attached VM identity.

## 4. Activate and retire the old resources

After the first successful run, pause the old broken scheduler, then enable
the VM timer. Keep the old job available until one scheduled VM run also passes.

```bash
gcloud scheduler jobs pause fpl-live-league-refresh --project=irfan-374115 --location=us-central1
sudo systemctl enable --now fpl-live-refresh.timer
systemctl list-timers fpl-live-refresh.timer
```

Once the scheduled run has published both leagues, remove the two exact legacy
resources so the scheduler is no longer billed and the Cloud Run job cannot
be launched accidentally:

```bash
gcloud scheduler jobs delete fpl-live-league-refresh --project=irfan-374115 --location=us-central1
gcloud run jobs delete fpl-live-league-refresh --project=irfan-374115 --region=us-central1
```

No replacement cloud scheduler/job is created. This reuses existing VM capacity;
GCS storage/operations and the existing API still have their normal costs.

## 5. API and monitoring rollout

Deploy the tagged API using `infra/cloudbuild.api.yaml`. Verify both
`/v1/leagues/58005/live/status` and `/v1/leagues/131997/live/status` report
`ready: true`. These bounded responses contain no squads. `/live` also exposes
age and `stale` without hiding the last complete snapshot.

The existing GitHub Actions monitor now checks these endpoints. From merge
until API deployment/first collection it will intentionally fail (404, 503,
or stale) instead of reporting this broken path healthy. Deploy promptly after
merge and verify the next monitor run. Twelve hours is the maximum snapshot
age, allowing the intentional overnight pause; it is not a 30-minute live-score
guarantee. Missing, malformed or future timestamps also fail the check.

## Rollback and follow-up

Stop/disable `fpl-live-refresh.timer` and stop its service before switching
`/opt/fpl-live-refresh/current` to a previously verified release directory.
Reinstall that release's reviewed units if their definitions changed, then
prove a manual run before enabling the timer. For a first-install rollback,
leave it disabled and retain the last complete GCS snapshot; do not resume the
known-broken Cloud Scheduler path. Reverting storage scopes requires another
maintenance stop/start using the captured values.

Shared authenticated squad/chip/plan state is a separate PR. This public league
collector does not solve the pre-deadline VM-to-dashboard account-state relay.
