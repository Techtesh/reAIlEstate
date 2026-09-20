#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateSet('admin', 'builder', 'buyer')]
    [string]$Service,

    [ValidateRange(1024, 65535)]
    [int]$Port,

    [string]$VisionProfilesFile
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'runtime_helpers.ps1')

$definition = Get-RealEstateServiceDefinition -Service $Service
if (-not $Port) {
    $Port = $definition.Port
}
$serviceRoot = Join-Path $PSScriptRoot $definition.Directory
$python = Get-RealEstatePython -ApplicationRoot $PSScriptRoot
if ($definition.RequiresFrontend) {
    Assert-RealEstateFrontendBuild -ApplicationRoot $PSScriptRoot -Service $Service
}

Initialize-RealEstateRuntime -ApplicationRoot $PSScriptRoot -VisionProfilesFile $VisionProfilesFile

Write-Host "Starting $Service service at http://127.0.0.1:$Port"
if (-not [string]::IsNullOrWhiteSpace($env:FLOOR_PLAN_DEFAULT_VISION_PROFILE)) {
    Write-Host "AI vision profile: $env:FLOOR_PLAN_DEFAULT_VISION_PROFILE"
}
& $python (Join-Path $serviceRoot 'manage.py') runserver "127.0.0.1:$Port" --noreload "--settings=$($definition.Settings)"
exit $LASTEXITCODE
