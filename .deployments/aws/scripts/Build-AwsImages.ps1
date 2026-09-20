[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string] $ExpectedAccountId,

    [string] $Region = "ap-south-1",

    [string] $TerraformDirectory = (Join-Path $PSScriptRoot "..\terraform"),

    [string] $ImageTag = "",

    [string] $PipIndexUrl = "",

    [switch] $WriteAutoTfvars,

    [switch] $Execute
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

if (-not $Execute) {
    Write-Host "No images were built or pushed. Re-run with -Execute after the Foundation stage is applied and reviewed."
    return
}

Assert-ExternalCommand -Name "aws"
Assert-ExternalCommand -Name "docker"
Assert-ExternalCommand -Name "git"
Assert-ExternalCommand -Name "terraform"
Assert-AwsContext -ExpectedAccountId $ExpectedAccountId -Region $Region

$repositoryRoot = Get-RepositoryRoot
$terraformPath = (Resolve-Path $TerraformDirectory).Path

if ([string]::IsNullOrWhiteSpace($ImageTag)) {
    $ImageTag = (& git -C $repositoryRoot rev-parse --short=12 HEAD).Trim()
    Assert-LastExitCode -Operation "Reading the Git commit for the image tag"
}
if ($ImageTag -notmatch "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$") {
    throw "'$ImageTag' is not a valid OCI image tag."
}

$applicationRepository = Get-TerraformOutput -TerraformDirectory $terraformPath -Name "application_repository_url"
$gatewayRepository = Get-TerraformOutput -TerraformDirectory $terraformPath -Name "gateway_repository_url"
$registryHost = $applicationRepository.Split("/")[0]

& aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $registryHost
Assert-LastExitCode -Operation "Authenticating Docker to Amazon ECR"

$applicationImage = "$applicationRepository`:$ImageTag"
$gatewayImage = "$gatewayRepository`:$ImageTag"
$applicationContext = Join-Path $repositoryRoot "reailEstate"
$applicationDockerfile = Join-Path $applicationContext "Dockerfile"
$gatewayDockerfile = Join-Path $repositoryRoot ".deployments\aws\gateway\Dockerfile"

$applicationBuildArgs = @(
    "build",
    "--platform", "linux/amd64",
    "--tag", $applicationImage,
    "--file", $applicationDockerfile
)
if (-not [string]::IsNullOrWhiteSpace($PipIndexUrl)) {
    $applicationBuildArgs += @("--build-arg", "PIP_INDEX_URL=$PipIndexUrl")
}
$applicationBuildArgs += $applicationContext

& docker @applicationBuildArgs
Assert-LastExitCode -Operation "Building the application image"
& docker push $applicationImage
Assert-LastExitCode -Operation "Pushing the application image"

& docker build --platform linux/amd64 --build-arg "APPLICATION_IMAGE=$applicationImage" --tag $gatewayImage --file $gatewayDockerfile $repositoryRoot
Assert-LastExitCode -Operation "Building the gateway image"
& docker push $gatewayImage
Assert-LastExitCode -Operation "Pushing the gateway image"

Write-Host "Built and pushed application image: $applicationImage"
Write-Host "Built and pushed gateway image: $gatewayImage"

if ($WriteAutoTfvars) {
    $autoTfvarsPath = Join-Path $terraformPath "deployment.auto.tfvars"
    if (Test-Path -LiteralPath $autoTfvarsPath) {
        throw "Refusing to overwrite existing '$autoTfvarsPath'. Update it explicitly."
    }

    @"
deploy_workload       = true
application_image_tag = "$ImageTag"
gateway_image_tag     = "$ImageTag"
"@ | Set-Content -Path $autoTfvarsPath -Encoding utf8
    Write-Host "Wrote workload image settings to $autoTfvarsPath (ignored by Git)."
}
