#requires -Version 7.0
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$frontend = Join-Path $PSScriptRoot 'reailEstate_common\frontend'
$python = Join-Path $PSScriptRoot 'reailEstate_common\backend\.venv\Scripts\python.exe'
npm --prefix $frontend run build:services
if ($LASTEXITCODE -ne 0) {
    throw 'Service frontend build failed.'
}
& $python (Join-Path $PSScriptRoot 'buyerService\manage.py') collectstatic --noinput --settings=buyer_service.settings
if ($LASTEXITCODE -ne 0) {
    throw 'Service static asset collection failed.'
}
Write-Host 'Buyer and builder frontend bundles and static assets are ready.'
