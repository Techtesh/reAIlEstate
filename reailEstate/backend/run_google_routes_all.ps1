param(
    [Parameter(Mandatory = $true)][string]$Reviewer,
    [ValidateSet(5000, 10000, 20000)][int]$Radius = 10000,
    [ValidateRange(1, 5)][int]$BatchSize = 5,
    [switch]$ApproveLocations
)

$ErrorActionPreference = "Stop"
if (-not $env:GOOGLE_MAPS_API_KEY) {
    throw "GOOGLE_MAPS_API_KEY is not configured in this terminal."
}
$workspace = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
if ($env:WORKSPACE_DATA_ROOT -and [IO.Path]::GetFullPath($env:WORKSPACE_DATA_ROOT).TrimEnd('\', '/') -ne $workspace) {
    throw "WORKSPACE_DATA_ROOT must identify this workspace."
}
$env:WORKSPACE_DATA_ROOT = $workspace
$env:DATABASE_BACKEND = "postgresql"
$env:PYTHONUTF8 = "1"
if (-not $env:PROJECT_METRO_CATALOGUE_FILE) {
    $catalogue = Join-Path $PSScriptRoot "config/mmr-upcoming-metro.json"
    if ((Test-Path -LiteralPath $catalogue) -and -not [string]::IsNullOrWhiteSpace(
        [string](Get-Content -LiteralPath $catalogue -Raw))) {
        $env:PROJECT_METRO_CATALOGUE_FILE = $catalogue
    }
}
$report = Join-Path $workspace "output/reports/google-project-routes.json"
$log = Join-Path $workspace "output/google-project-routes.log"
$python = Join-Path $PSScriptRoot ".venv/Scripts/python.exe"
$manage = Join-Path $PSScriptRoot "manage.py"
$generation = [Guid]::NewGuid().ToString("N")
$null = New-Item -ItemType Directory -Path (Split-Path $report) -Force
$arguments = @(
    $manage, "enrich_google_project_routes", "--execute", "--drain",
    "--batch-size", $BatchSize, "--radius", $Radius,
    "--reviewer", $Reviewer, "--retry-failed", "--retry-generation", $generation,
    "--report", $report
)
if (-not $ApproveLocations) { $arguments += "--prefetch-only" }
if (Test-Path -LiteralPath $report) { $arguments += "--resume" }
& $python @arguments | Tee-Object -FilePath $log -Append
if ($LASTEXITCODE -ne 0) { throw "Route enrichment failed. Check $log" }
Write-Host "All active projects accounted for. Route report: $report"