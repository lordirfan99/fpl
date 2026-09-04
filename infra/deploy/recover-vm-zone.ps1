param(
    [ValidateSet('us-central1-a', 'us-central1-b', 'us-central1-c')]
    [string]$TargetZone = 'us-central1-a',
    [switch]$Apply
)

$ErrorActionPreference = 'Stop'
$Project = 'irfan-374115'
$VmName = 'instance-20260412-121200'
$SourceZone = 'us-central1-f'
$ImageName = 'fpl-zone-recovery-20260904'
$PublicIp = '34.60.216.122'

function Invoke-Gcloud {
    param([string[]]$Arguments)
    $result = & gcloud @Arguments
    if ($LASTEXITCODE -ne 0) { throw "gcloud failed; migration stopped ($LASTEXITCODE)" }
    return $result
}

$SourceVm = Invoke-Gcloud @('compute', 'instances', 'describe', $VmName, "--project=$Project", "--zone=$SourceZone", '--format=json') | ConvertFrom-Json
if ($SourceVm.status -ne 'TERMINATED') { throw 'Source must remain stopped to prevent duplicate bots.' }
if ($SourceVm.machineType -notlike '*/e2-micro' -or $SourceVm.disks.Count -ne 1 -or -not $SourceVm.disks[0].boot) {
    throw 'Unexpected source machine/disk layout; review before proceeding.'
}
$Targets = @(Invoke-Gcloud @('compute', 'instances', 'list', "--project=$Project", "--zones=$TargetZone", "--filter=name=$VmName", '--format=json') | ConvertFrom-Json)
if ($Targets.Count -gt 0) { throw 'Target VM already exists; inspect it instead of overwriting.' }
$Address = Invoke-Gcloud @('compute', 'addresses', 'describe', 'fpl-shared-vm-ip', "--project=$Project", '--region=us-central1', '--format=json') | ConvertFrom-Json
if ($Address.address -ne $PublicIp) { throw 'Reserved public address does not match the reviewed recovery plan.' }
if (@($Address.users | Where-Object { $_ -ne $SourceVm.selfLink }).Count -gt 0) { throw 'Public address is attached to another resource.' }

Write-Output "Plan: preserve stopped $SourceZone VM; create private machine image; restore e2-micro with pd-standard in $TargetZone; retain $PublicIp."
if (-not $Apply) { Write-Output 'Read-only plan. Use -Apply only from the reviewed release.'; return }

$Images = @(Invoke-Gcloud @('compute', 'machine-images', 'list', "--project=$Project", "--filter=name=$ImageName", '--format=json') | ConvertFrom-Json)
if ($Images.Count -eq 0) {
    Invoke-Gcloud @('compute', 'machine-images', 'create', $ImageName, "--project=$Project", "--source-instance=$VmName", "--source-instance-zone=$SourceZone", '--storage-location=us-central1', '--quiet')
}
$Image = Invoke-Gcloud @('compute', 'machine-images', 'describe', $ImageName, "--project=$Project", '--format=json') | ConvertFrom-Json
if ($Image.status -ne 'READY' -or $Image.sourceInstance -ne $SourceVm.selfLink) { throw 'Recovery image is not ready or belongs to another VM.' }

# Machine images preserve the disk, SSH metadata, labels, service account,
# scopes and service configuration without copying credentials into the repo.
if ($SourceVm.networkInterfaces[0].accessConfigs.Count -gt 0) {
    if ($SourceVm.networkInterfaces[0].accessConfigs[0].natIP -ne $PublicIp) { throw 'Unexpected source external address.' }
    Invoke-Gcloud @('compute', 'instances', 'delete-access-config', $VmName, "--project=$Project", "--zone=$SourceZone", '--access-config-name=External NAT', '--quiet')
}
$DeviceName = $SourceVm.disks[0].deviceName
Invoke-Gcloud @('compute', 'instances', 'create', $VmName, "--project=$Project", "--zone=$TargetZone",
    "--source-machine-image=$ImageName", '--machine-type=e2-micro', '--subnet=default', "--address=$PublicIp",
    "--create-disk=boot=yes,auto-delete=no,device-name=$DeviceName,type=pd-standard", '--quiet')
# The machine-image restore observed on 2026-09-04 retained the source disk
# settings despite create-disk overrides. Enforce retention explicitly and
# report the actual type instead of claiming the requested free disk allowance.
$Restored = Invoke-Gcloud @('compute', 'instances', 'describe', $VmName, "--project=$Project", "--zone=$TargetZone", '--format=json') | ConvertFrom-Json
$DiskName = ($Restored.disks[0].source -split '/')[-1]
Invoke-Gcloud @('compute', 'instances', 'set-disk-auto-delete', $VmName, "--project=$Project", "--zone=$TargetZone", "--disk=$DiskName", '--no-auto-delete', '--quiet')
$Disk = Invoke-Gcloud @('compute', 'disks', 'describe', $DiskName, "--project=$Project", "--zone=$TargetZone", '--format=json') | ConvertFrom-Json
if ($Disk.type -notlike '*/pd-standard') { Write-Warning "Recovered boot disk is $($Disk.type); storage charges may apply. Do not stop the recovered VM just to change disk type." }
Write-Output "Created replacement. Verify all services and scopes before declaring recovery. Original VM/disk/image are retained."
