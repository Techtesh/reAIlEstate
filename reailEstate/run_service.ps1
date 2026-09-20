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
$ports = @{ admin = 8002; builder = 8003; buyer = 8004 }
$serviceDirectories = @{
    admin = 'adminService'
    builder = 'builderService'
    buyer = 'buyerService'
}
if (-not $Port) {
    $Port = $ports[$Service]
}
$settings = "${Service}_service.settings"
$serviceRoot = Join-Path $PSScriptRoot $serviceDirectories[$Service]
$python = Join-Path $PSScriptRoot 'reailEstate_common\backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python environment is missing: $python"
}
if ($Service -in @('buyer', 'builder') -and
    -not (Test-Path -LiteralPath (Join-Path $PSScriptRoot "reailEstate_common\frontend\dist\$Service\index.html"))) {
    throw "$Service frontend is not built. Run build_service_frontends.ps1 first."
}

. (Join-Path $PSScriptRoot 'configure_runtime.ps1')
Set-RealEstateRuntimeEnvironment -ApplicationRoot $PSScriptRoot -VisionProfilesFile $VisionProfilesFile

Write-Host "Starting $Service service at http://127.0.0.1:$Port"
if (-not [string]::IsNullOrWhiteSpace($env:FLOOR_PLAN_DEFAULT_VISION_PROFILE)) {
    Write-Host "AI vision profile: $env:FLOOR_PLAN_DEFAULT_VISION_PROFILE"
}
& $python (Join-Path $serviceRoot 'manage.py') runserver "127.0.0.1:$Port" --noreload "--settings=$settings"
exit $LASTEXITCODE
