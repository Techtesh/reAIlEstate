[CmdletBinding()]
param(
    [string] $SubscriptionId = "088476fe-27ee-42b2-a867-268aab19dce3",

    [string] $TenantId = "d68b2372-3268-46b9-8329-75273fb49a72",

    [string] $TerraformDirectory = (Join-Path $PSScriptRoot "..\terraform"),

    [int] $TimeoutSeconds = 1800,

    [switch] $Execute
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

if (-not $Execute) {
    Write-Host "No migration was started. Re-run with -Execute after the Workload Terraform plan has been applied."
    return
}

Assert-ExternalCommand -Name "az"
Assert-ExternalCommand -Name "terraform"
Assert-AzureContext -SubscriptionId $SubscriptionId -TenantId $TenantId

$terraformPath = (Resolve-Path $TerraformDirectory).Path
$resourceGroup = Get-TerraformOutput -TerraformDirectory $terraformPath -Name "resource_group_name"
$jobName = Get-TerraformOutput -TerraformDirectory $terraformPath -Name "migration_job_name"

if ([string]::IsNullOrWhiteSpace($jobName)) {
    throw "The migration job is not deployed. Apply the Workload stage first."
}

$executionJson = & az containerapp job start --subscription $SubscriptionId --resource-group $resourceGroup --name $jobName --output json
Assert-LastExitCode -Operation "Starting the migration job"
$executionName = ($executionJson | ConvertFrom-Json).name
if ([string]::IsNullOrWhiteSpace($executionName)) {
    throw "Azure CLI did not return a migration job execution name."
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    Start-Sleep -Seconds 10
    $status = (& az containerapp job execution show --subscription $SubscriptionId --resource-group $resourceGroup --name $jobName --job-execution-name $executionName --query "properties.status" --output tsv).Trim()
    Assert-LastExitCode -Operation "Reading migration job execution '$executionName'"
    Write-Host "Migration execution $executionName status: $status"

    if ($status -eq "Succeeded") {
        Write-Host "Database migration completed successfully."
        return
    }
    if ($status -in @("Failed", "Stopped")) {
        throw "Migration execution '$executionName' ended with status '$status'."
    }
} while ((Get-Date) -lt $deadline)

throw "Migration execution '$executionName' did not complete within $TimeoutSeconds seconds."
