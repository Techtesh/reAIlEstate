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

function Assert-AwsContext {
    param(
        [Parameter(Mandatory)]
        [string] $ExpectedAccountId,

        [Parameter(Mandatory)]
        [string] $Region
    )

    Assert-ExternalCommand -Name "aws"
    if ($ExpectedAccountId -notmatch "^[0-9]{12}$") {
        throw "ExpectedAccountId must be the confirmed twelve-digit AWS account ID."
    }

    $accountId = (& aws sts get-caller-identity --query Account --output text).Trim()
    Assert-LastExitCode -Operation "Reading the AWS caller identity"
    if ($accountId -ne $ExpectedAccountId) {
        throw "AWS CLI account '$accountId' does not match expected account '$ExpectedAccountId'."
    }

    $partition = (& aws sts get-caller-identity --query Arn --output text).Trim().Split(":")[1]
    Assert-LastExitCode -Operation "Reading the AWS partition"
    if ($partition -ne "aws") {
        throw "This deployment supports the commercial AWS partition only; current partition is '$partition'."
    }

    Write-Host "AWS context verified: account $accountId, region $Region."
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
