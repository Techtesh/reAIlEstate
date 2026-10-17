#requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateRange(0.1, 60)]
    [double]$PollSeconds = 2,

    [string]$VisionProfilesFile
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'runtime_helpers.ps1')

$python = Get-RealEstatePython -ApplicationRoot $PSScriptRoot
Initialize-RealEstateRuntime -ApplicationRoot $PSScriptRoot -VisionProfilesFile $VisionProfilesFile

Write-Host 'Starting shared extraction, media-evaluation, and recommendation worker'
if (-not [string]::IsNullOrWhiteSpace($env:FLOOR_PLAN_VISION_PROFILES_FILE)) {
    Write-Host "AI vision configuration: $env:FLOOR_PLAN_VISION_PROFILES_FILE"
}
& $python (Join-Path $PSScriptRoot 'adminService\manage.py') run_extraction_dispatcher `
    '--settings=admin_service.settings' "--poll-seconds=$PollSeconds"
exit $LASTEXITCODE
