[CmdletBinding()]
param(
    [string] $BaseUrl = "",

    [string] $TerraformDirectory = (Join-Path $PSScriptRoot "..\terraform")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
    Assert-ExternalCommand -Name "terraform"
    $terraformPath = (Resolve-Path $TerraformDirectory).Path
    $BaseUrl = Get-TerraformOutput -TerraformDirectory $terraformPath -Name "application_url"
}
if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
    throw "No application URL is available. Deploy the Workload stage or pass -BaseUrl."
}

$base = $BaseUrl.TrimEnd("/")
$checks = @(
    @{ Name = "Gateway liveness"; Url = "$base/health/live" },
    @{ Name = "Database readiness"; Url = "$base/health/ready" },
    @{ Name = "Buyer frontend"; Url = "$base/" },
    @{ Name = "Builder frontend"; Url = "$base/builder" },
    @{ Name = "Admin login"; Url = "$base/admin/login/?next=/admin/" }
)

foreach ($check in $checks) {
    $response = Invoke-WebRequest -Uri $check.Url -Method Get -MaximumRedirection 5 -TimeoutSec 30 -UseBasicParsing
    if ($response.StatusCode -ne 200) {
        throw "$($check.Name) returned HTTP $($response.StatusCode) from $($check.Url)."
    }
    Write-Host "$($check.Name): HTTP $($response.StatusCode) - $($check.Url)"
}

Write-Host ""
Write-Host "Buyer:   $base/"
Write-Host "Builder: $base/builder"
Write-Host "Admin:   $base/admin/"
