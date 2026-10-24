#requires -Version 7.0
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'runtime_helpers.ps1')

$frontend = Join-Path $PSScriptRoot 'reailEstate_common\frontend'
$python = Get-RealEstatePython -ApplicationRoot $PSScriptRoot
npm --prefix $frontend run build:services
if ($LASTEXITCODE -ne 0) {
    throw 'Service frontend build failed.'
}
& $python (Join-Path $PSScriptRoot 'buyerService\manage.py') collectstatic --noinput --settings=buyer_service.settings
if ($LASTEXITCODE -ne 0) {
    throw 'Service static asset collection failed.'
}
& $python (Join-Path $PSScriptRoot 'adminService\manage.py') collectstatic --noinput --settings=admin_service.settings
if ($LASTEXITCODE -ne 0) {
    throw 'Admin project workspace static asset collection failed.'
}
Write-Host 'Buyer, builder and admin project workspace assets are ready.'
