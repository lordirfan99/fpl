param(
    [string]$ProjectId = "irfan-374115",
    [string]$Region = "us-central1",
    [string]$Bucket = "irfan-374115-fpl-snapshots"
)

$ErrorActionPreference = "Stop"
$ProjectNumber = gcloud projects describe $ProjectId --format="value(projectNumber)"
$RuntimeServiceAccount = "fpl-live-refresh@$ProjectId.iam.gserviceaccount.com"
$SchedulerServiceAccount = "fpl-live-refresh-scheduler@$ProjectId.iam.gserviceaccount.com"
$JobName = "fpl-live-league-refresh"

gcloud services enable run.googleapis.com cloudscheduler.googleapis.com --project $ProjectId

foreach ($Account in @(
    @{ Email = $RuntimeServiceAccount; Name = "FPL live snapshot collector" },
    @{ Email = $SchedulerServiceAccount; Name = "FPL live refresh scheduler" }
)) {
    $Existing = gcloud iam service-accounts list --project $ProjectId --filter "email:$($Account.Email)" --format="value(email)"
    if (-not $Existing) {
        $Id = $Account.Email.Split('@')[0]
        gcloud iam service-accounts create $Id --display-name $Account.Name --project $ProjectId
    }
}

# The collector can write only the dedicated snapshot bucket. It has no API deploy
# or scheduler permissions.
gcloud storage buckets add-iam-policy-binding "gs://$Bucket" --member "serviceAccount:$RuntimeServiceAccount" --role roles/storage.objectAdmin

# Cloud Build deploys the job on each production API deployment and must be allowed
# to attach the collector runtime identity.
gcloud iam service-accounts add-iam-policy-binding $RuntimeServiceAccount --member "serviceAccount:$ProjectNumber-compute@developer.gserviceaccount.com" --role roles/iam.serviceAccountUser --project $ProjectId

# The Cloud Build deployment creates the job. Run this script again afterwards
# to bind the Scheduler and create its hourly trigger.
$JobExists = gcloud run jobs list --region $Region --project $ProjectId --filter "metadata.name=$JobName" --format="value(metadata.name)"
if (-not $JobExists) {
    Write-Output "Provisioned identities. Deploy the production branch, then run this script once more to create the Scheduler trigger."
    exit 0
}

# Scheduler may start this one job, and the Scheduler service agent may mint the
# OAuth token for that target identity.
gcloud run jobs add-iam-policy-binding $JobName --region $Region --member "serviceAccount:$SchedulerServiceAccount" --role roles/run.invoker --project $ProjectId
gcloud iam service-accounts add-iam-policy-binding $SchedulerServiceAccount --member "serviceAccount:service-$ProjectNumber@gcp-sa-cloudscheduler.iam.gserviceaccount.com" --role roles/iam.serviceAccountTokenCreator --project $ProjectId

$JobUri = "https://run.googleapis.com/v2/projects/$ProjectId/locations/$Region/jobs/${JobName}:run"
$ExistingScheduler = gcloud scheduler jobs list --location $Region --project $ProjectId --filter "name:$JobName" --format="value(name)"
if ($ExistingScheduler) {
    gcloud scheduler jobs update http $JobName --location $Region --schedule "12 * * * *" --time-zone "Etc/UTC" --uri $JobUri --http-method POST --oauth-service-account-email $SchedulerServiceAccount --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform" --attempt-deadline "180s" --max-retry-attempts 3 --project $ProjectId
} else {
    gcloud scheduler jobs create http $JobName --location $Region --schedule "12 * * * *" --time-zone "Etc/UTC" --uri $JobUri --http-method POST --oauth-service-account-email $SchedulerServiceAccount --oauth-token-scope "https://www.googleapis.com/auth/cloud-platform" --attempt-deadline "180s" --max-retry-attempts 3 --project $ProjectId
}

Write-Output "Provisioned $JobName and its hourly Cloud Scheduler trigger."
