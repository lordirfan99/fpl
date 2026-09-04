#!/usr/bin/env bash
# Finish the "scheduled jobs -> GitHub Actions" migration (PR #26 / #27).
#
# Everything the agent could do is merged: the 4 workflows, the 6 repo Actions
# variables, and `scheduled-monitor` is verified green. What is left needs GCP
# owner credentials (IAM / Cloud Run / Cloud Scheduler mutations), so it lives
# here as one script instead of four manual steps.
#
# Run it from a machine authenticated as a project owner:
#     gcloud auth login            # if needed
#     bash infra/scripts/finish_scheduled_migration.sh
#
# Idempotent: safe to re-run. Nothing is deleted until the 3 GCS workflows pass.

set -euo pipefail

PROJECT="irfan-374115"
PROJECT_NUMBER="733056042866"
REGION="us-central1"
POOL="github-actions"
PROVIDER="github"
TASKS_SA="fpl-scheduled-tasks@${PROJECT}.iam.gserviceaccount.com"
REPO="lordirfan99/fpl"
PRINCIPAL="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}"
MIGRATED_JOBS=(fpl-refresh-fixtures fpl-refresh-gameweek fpl-capture-journal fpl-monitor)

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

say "1/5  Widen the WIF provider to trust ${REPO}"
gcloud iam workload-identity-pools providers update-oidc "${PROVIDER}" \
  --project="${PROJECT}" --location=global --workload-identity-pool="${POOL}" \
  --attribute-condition="assertion.repository=='lordirfan99/fpl-league-58005-scout' || assertion.repository=='${REPO}'" \
  --quiet

say "2/5  Let ${REPO} impersonate ${TASKS_SA}"
gcloud iam service-accounts add-iam-policy-binding "${TASKS_SA}" \
  --project="${PROJECT}" --role=roles/iam.workloadIdentityUser \
  --member="${PRINCIPAL}"

say "3/5  Trigger + wait for the three GCS workflows"
for w in scheduled-fixtures scheduled-capture-journal scheduled-finalize-gameweek; do
  gh workflow run "${w}.yml" --repo "${REPO}"
done
echo "waiting 150s for runs to complete ..."
sleep 150
gh run list --repo "${REPO}" --limit 6
FAILED=$(gh run list --repo "${REPO}" --limit 6 --json workflowName,conclusion,event \
  --jq '[.[] | select(.event=="workflow_dispatch" and (.workflowName|startswith("scheduled")) and .conclusion!="success")] | length')
if [[ "${FAILED}" != "0" ]]; then
  echo "!! Some scheduled workflows did not succeed — inspect with 'gh run view <id> --log-failed'."
  echo "   Skipping teardown. Re-run this script once they are green."
  exit 1
fi

say "4/5  Delete the migrated Cloud Scheduler + Cloud Run jobs"
for j in "${MIGRATED_JOBS[@]}"; do
  gcloud scheduler jobs delete "${j}" --project="${PROJECT}" --location="${REGION}" --quiet || true
  gcloud run jobs delete "${j}"       --project="${PROJECT}" --region="${REGION}"   --quiet || true
done

say "5/5  Live collection uses the existing VM"
echo "Follow infra/LIVE-REFRESH-VM.md for the tagged collector install and verification."
echo "Do not create/update/resume a Cloud Run collector or Cloud Scheduler trigger."
echo "Retire the old live resources only after the VM has published both leagues."
