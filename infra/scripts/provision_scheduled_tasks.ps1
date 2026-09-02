param(
    [string]$ProjectId = "irfan-374115",
    [string]$Region = "us-central1",
    [string]$Bucket = "irfan-374115-fpl-snapshots"
)

# Provisions the GCP side of the scheduled tasks that replaced the GitHub
# Actions crons (refresh-fixtures, capture-journal, refresh-gameweek,
# monitor-production). The Cloud Run Jobs themselves are created by the
# cloudbuild.api.yaml deployment; run this script once before that deploy to
# create identities, and again afterwards to bind the Cloud Scheduler triggers.

$ErrorActionPreference = "Stop"
$ProjectNumber = gcloud projects describe $ProjectId --format="value(projectNumber)"
$RuntimeServiceAccount = "fpl-scheduled-tasks@$ProjectId.iam.gserviceaccount.com"
$SchedulerServiceAccount = "fpl-scheduled-tasks-scheduler@$ProjectId.iam.gserviceaccount.com"

# job name -> cron schedule (UTC)
$Jobs = [ordered]@{
    "fpl-refresh-fixtures" = "17 * * * *"
    "fpl-capture-journal"  = "17 * * * *"
    "fpl-refresh-gameweek" = "23 * * * *"
    "fpl-monitor"          = "*/30 * * * *"
}

gcloud services enable run.googleapis.com cloudscheduler.googleapis.com --project $ProjectId

foreach ($Account in @(
    @{ Email = $RuntimeServiceAccount; Name = "FPL scheduled tasks runtime" },
    @{ Email = $SchedulerServiceAccount; Name = "FPL scheduled tasks scheduler" }
)) {
    $Existing = gcloud iam service-accounts list --project $ProjectId --filter "email:$($Account.Email)" --format="value(email)"
    if (-not $Existing) {
        $Id = $Account.Email.Split('@')[0]
        gcloud iam service-accounts create $Id --display-name $Account.Name --project $ProjectId
    }
}

# The tasks read and write only the shared snapshot bucket.
gcloud storage buckets add-iam-policy-binding "gs://$Bucket" --member "serviceAccount:$RuntimeServiceAccount" --role roles/storage.objectAdmin

# Cloud Build deploys the jobs on each production API deployment and must be
# allowed to attach the runtime identity.
gcloud iam service-accounts add-iam-policy-binding $RuntimeServiceAccount --member "serviceAccount:$ProjectNumber-compute@developer.gserviceaccount.com" --role roles/iam.serviceAccountUser --project $ProjectId

# The Scheduler service agent may mint OAuth tokens for the scheduler identity.
gcloud iam service-accounts add-iam-policy-binding $SchedulerServiceAccount --member "serviceAccount:service-$ProjectNumber@gcp-sa-cloudscheduler.iam.gserviceaccount.com" --role roles/iam.serviceAccountTokenCreator --project $ProjectId

$AnyMissing = $false
foreach ($JobName in $Jobs.Keys) {
    $JobExists = gcloud run jobs list --region $Region --project $ProjectId --filter "metadata.name=$JobName" --format="value(metadata.name)"
    if (-not $JobExists) {
        Write-Output "Cloud Run Job '$JobName' not deployed yet."
        $AnyMissing = $true
        continue
    }

    # Scheduler may start this job.
    gcloud run jobs add-iam-policy-binding $JobName --region $Region --member "serviceAccount:$SchedulerServiceAccount" --role roles/run.invoker --project $ProjectId

    $JobUri = "https://run.googleapis.com/v2/projects/$ProjectId/locations/$Region/jobs/${JobName}:run"
    $Schedule = $Jobs[$JobName]
    $Existing = gcloud scheduler jobs list --location $Region --project $ProjectId --filter "name:$JobName" --format="value(name)"
    if ($Existing) {
        gcloud scheduler jobs update http $JobName --location $Region --schedule "$Schedule" --time-zone "Etc/UTC" --uri $JobUri --http-method POST --oauth-service-account-email $SchedulerServiceAccount --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform" --attempt-deadline "1800s" --max-retry-attempts 2 --project $ProjectId
    } else {
        gcloud scheduler jobs create http $JobName --location $Region --schedule "$Schedule" --time-zone "Etc/UTC" --uri $JobUri --http-method POST --oauth-service-account-email $SchedulerServiceAccount --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform" --attempt-deadline "1800s" --max-retry-attempts 2 --project $ProjectId
    }
    Write-Output "Provisioned Cloud Scheduler trigger '$JobName' ($Schedule UTC)."
}

if ($AnyMissing) {
    Write-Output ""
    Write-Output "Deploy the production branch (cloudbuild.api.yaml creates the jobs), then run this script once more to bind the Scheduler triggers."
    exit 0
}

Write-Output ""
Write-Output "All scheduled-task jobs and Cloud Scheduler triggers are provisioned."
Write-Output "The matching GitHub Actions workflows remain available as manual (workflow_dispatch) fallbacks."
