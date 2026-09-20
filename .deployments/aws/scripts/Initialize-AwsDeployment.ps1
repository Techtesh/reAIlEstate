[CmdletBinding(SupportsShouldProcess, ConfirmImpact = "High")]
param(
    [ValidateSet("Validate", "Foundation", "Workload")]
    [string] $Stage = "Validate",

    [string] $ExpectedAccountId = "",

    [string] $Region = "ap-south-1",

    [string] $BackendConfigPath = "",

    [string] $TerraformDirectory = (Join-Path $PSScriptRoot "..\terraform"),

    [switch] $Apply,

    [string] $ApproveAccountId = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

Assert-ExternalCommand -Name "terraform"
$terraformPath = (Resolve-Path $TerraformDirectory).Path

& terraform "-chdir=$terraformPath" fmt -check -recursive
Assert-LastExitCode -Operation "Terraform formatting check"

if ($Stage -eq "Validate") {
    if ($Apply) {
        throw "-Apply cannot be used with the Validate stage."
    }

    & terraform "-chdir=$terraformPath" init -backend=false -input=false
    Assert-LastExitCode -Operation "Offline Terraform initialization"
    & terraform "-chdir=$terraformPath" validate
    Assert-LastExitCode -Operation "Terraform validation"
    Write-Host "Offline AWS Terraform validation completed. No AWS resources were changed."
    return
}

if ([string]::IsNullOrWhiteSpace($ExpectedAccountId)) {
    throw "Foundation and Workload stages require -ExpectedAccountId."
}
if ([string]::IsNullOrWhiteSpace($BackendConfigPath)) {
    throw "Foundation and Workload stages require -BackendConfigPath for protected remote state."
}

Assert-AwsContext -ExpectedAccountId $ExpectedAccountId -Region $Region
$backendPath = (Resolve-Path $BackendConfigPath).Path

& terraform "-chdir=$terraformPath" init -reconfigure -input=false "-backend-config=$backendPath"
Assert-LastExitCode -Operation "Terraform backend initialization"
& terraform "-chdir=$terraformPath" validate
Assert-LastExitCode -Operation "Terraform validation"

$deployWorkload = if ($Stage -eq "Workload") { "true" } else { "false" }
$planPath = Join-Path $terraformPath "$($Stage.ToLowerInvariant()).tfplan"
& terraform "-chdir=$terraformPath" plan -input=false "-out=$planPath" "-var=aws_region=$Region" "-var=expected_aws_account_id=$ExpectedAccountId" "-var=deploy_workload=$deployWorkload"
Assert-LastExitCode -Operation "Terraform $Stage plan"

Write-Host "Terraform plan created at $planPath. Review it before applying."

if (-not $Apply) {
    return
}
if ($ApproveAccountId -ne $ExpectedAccountId) {
    throw "-ApproveAccountId must exactly match '$ExpectedAccountId' before apply."
}
if (-not $PSCmdlet.ShouldProcess("$Stage resources in AWS account $ExpectedAccountId", "Apply reviewed Terraform plan")) {
    return
}

& terraform "-chdir=$terraformPath" apply -input=false $planPath
Assert-LastExitCode -Operation "Terraform $Stage apply"
