[CmdletBinding()]
param(
    [string] $SubscriptionId = "088476fe-27ee-42b2-a867-268aab19dce3",

    [string] $TenantId = "d68b2372-3268-46b9-8329-75273fb49a72",

    [string] $TerraformDirectory = (Join-Path $PSScriptRoot "..\terraform"),

    [string] $ImageTag = "",

    [string] $ApplicationRepository = "workspace-app",

    [string] $GatewayRepository = "workspace-gateway",

    [switch] $WriteAutoTfvars,

    [switch] $Execute
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

if (-not $Execute) {
    Write-Host "No images were built. Re-run with -Execute after the Foundation stage is applied and reviewed."
    return
}

Assert-ExternalCommand -Name "az"
Assert-ExternalCommand -Name "git"
Assert-ExternalCommand -Name "terraform"
Assert-AzureContext -SubscriptionId $SubscriptionId -TenantId $TenantId

$repositoryRoot = Get-RepositoryRoot
$terraformPath = (Resolve-Path $TerraformDirectory).Path

if ([string]::IsNullOrWhiteSpace($ImageTag)) {
    $ImageTag = (& git -C $repositoryRoot rev-parse --short=12 HEAD).Trim()
    Assert-LastExitCode -Operation "Reading the Git commit for the image tag"
}
if ($ImageTag -notmatch "^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$") {
    throw "'$ImageTag' is not a valid OCI image tag."
}

$registryName = Get-TerraformOutput -TerraformDirectory $terraformPath -Name "container_registry_name"
$loginServer = Get-TerraformOutput -TerraformDirectory $terraformPath -Name "container_registry_login_server"
$applicationImage = "$loginServer/$ApplicationRepository`:$ImageTag"
$gatewayImage = "$loginServer/$GatewayRepository`:$ImageTag"
$applicationContext = Join-Path $repositoryRoot "reailEstate"
$applicationDockerfile = Join-Path $applicationContext "Dockerfile"
$gatewayDockerfile = Join-Path $repositoryRoot ".deployments\azure\gateway\Dockerfile"

& az acr build --subscription $SubscriptionId --registry $registryName --image "$ApplicationRepository`:$ImageTag" --file $applicationDockerfile $applicationContext
Assert-LastExitCode -Operation "Building the application image in Azure Container Registry"

& az acr build --subscription $SubscriptionId --registry $registryName --image "$GatewayRepository`:$ImageTag" --build-arg "APPLICATION_IMAGE=$applicationImage" --file $gatewayDockerfile $repositoryRoot
Assert-LastExitCode -Operation "Building the gateway image in Azure Container Registry"

Write-Host "Built application image: $applicationImage"
Write-Host "Built gateway image: $gatewayImage"

if ($WriteAutoTfvars) {
    $autoTfvarsPath = Join-Path $terraformPath "deployment.auto.tfvars"
    @"
deploy_workload       = true
application_image_tag = "$ImageTag"
gateway_image_tag     = "$ImageTag"
"@ | Set-Content -Path $autoTfvarsPath -Encoding utf8
    Write-Host "Wrote workload image settings to $autoTfvarsPath (ignored by Git)."
}
