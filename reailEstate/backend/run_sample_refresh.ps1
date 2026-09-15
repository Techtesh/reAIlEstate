param([int]$Workers = 8, [switch]$Resume, [switch]$RetryFailed, [switch]$TrustedSamples)
$ErrorActionPreference = 'Stop'
$workspace = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$env:DATABASE_BACKEND = 'postgresql'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:FLOOR_PLAN_EXTRACTION_MODE = 'ai'
$env:FLOOR_PLAN_VISION_PROFILES = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'vision-profiles.local.json') -Raw
$env:FLOOR_PLAN_DEFAULT_VISION_PROFILE = 'azure-samples'
$env:MAX_UPLOAD_BYTES = '536870912'
$reportRoot = Join-Path $workspace 'output/reports'
New-Item -ItemType Directory -Path $reportRoot -Force | Out-Null
$arguments = @((Join-Path $PSScriptRoot 'manage.py'), 'refresh_sample_corpus', (Join-Path $workspace 'user/Samples'),
    '--import', '--process', '--reprocess', '--username', 'sample-ingestion', '--vision-profile-id', 'azure-samples',
    '--workers', "$Workers", '--report', 'sample-corpus-refresh-20260911.json')
if ($Resume) { $arguments += '--resume' }
if ($RetryFailed) {
    if (-not $Resume) { $arguments += '--resume' }
    $arguments += '--retry-failed'
}
if ($TrustedSamples) { $arguments += '--test-scanner' }
& (Join-Path $PSScriptRoot '.venv/Scripts/python.exe') @arguments 2>&1 |
    Tee-Object -FilePath (Join-Path $reportRoot 'sample-corpus-refresh-20260911.log') -Append
if ($LASTEXITCODE -ne 0) { throw "Sample refresh exited with code $LASTEXITCODE" }