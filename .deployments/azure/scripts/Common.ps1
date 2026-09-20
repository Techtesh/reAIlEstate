Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-ExternalCommand {
    param(
        [Parameter(Mandatory)]
        [string] $Name
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' is not installed or is not on PATH."
    }
}

function Assert-LastExitCode {
    param(
        [Parameter(Mandatory)]
        [string] $Operation
    )

    if ($LASTEXITCODE -ne 0) {
        throw "$Operation failed with exit code $LASTEXITCODE."
    }
}

function Assert-AzureContext {
    param(
        [Parameter(Mandatory)]
        [string] $SubscriptionId,

        [Parameter(Mandatory)]
        [string] $TenantId
    )

    Assert-ExternalCommand -Name "az"
    $accountJson = & az account show --subscription $SubscriptionId --output json
    Assert-LastExitCode -Operation "Reading the Azure CLI account context"
    $account = $accountJson | ConvertFrom-Json

    if ($account.id -ne $SubscriptionId) {
        throw "Azure CLI selected subscription '$($account.id)', expected '$SubscriptionId'."
    }
    if ($account.tenantId -ne $TenantId) {
        throw "Azure CLI selected tenant '$($account.tenantId)', expected '$TenantId'. Run 'az login --tenant $TenantId'."
    }

    Write-Host "Azure context verified: $($account.name) ($SubscriptionId), tenant $TenantId."
}

function Get-RepositoryRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
}

function Get-TerraformOutput {
    param(
        [Parameter(Mandatory)]
        [string] $TerraformDirectory,

        [Parameter(Mandatory)]
        [string] $Name
    )

    $value = & terraform "-chdir=$TerraformDirectory" output -raw $Name
    Assert-LastExitCode -Operation "Reading Terraform output '$Name'"
    return $value.Trim()
}
