#requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateRange(0.1, 60)]
    [double]$PollSeconds = 2,

    [string]$VisionProfilesFile
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'configure_runtime.ps1')

$python = Join-Path $PSScriptRoot 'reailEstate_common\backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Python environment is missing: $python"
}

Set-RealEstateRuntimeEnvironment -ApplicationRoot $PSScriptRoot -VisionProfilesFile $VisionProfilesFile

Write-Host 'Starting shared extraction, media-evaluation, and recommendation worker'
if (-not [string]::IsNullOrWhiteSpace($env:FLOOR_PLAN_DEFAULT_VISION_PROFILE)) {
    Write-Host "AI vision profile: $env:FLOOR_PLAN_DEFAULT_VISION_PROFILE"
}
& $python (Join-Path $PSScriptRoot 'adminService\manage.py') run_extraction_dispatcher `
    '--settings=admin_service.settings' "--poll-seconds=$PollSeconds"
exit $LASTEXITCODE
