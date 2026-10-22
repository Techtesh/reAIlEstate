Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')
Assert-ExternalCommand -Name docker
$ec2 = (Resolve-Path (Join-Path $PSScriptRoot '../ec2')).Path
$json = & docker compose --profile "*" --env-file (Join-Path $ec2 '.env.example') -f (Join-Path $ec2 'compose.yml') config --no-env-resolution --format json
Assert-LastExitCode -Operation 'Parsing EC2 Compose configuration'
$config = ($json -join "`n") | ConvertFrom-Json -AsHashtable
function Assert-Configuration([bool] $Condition, [string] $Message) {
    if (-not $Condition) { throw $Message }
}
Assert-Configuration ($config.services.Count -eq 7) 'Unexpected service count'
foreach ($name in @('admin', 'builder', 'buyer', 'worker', 'migrate')) {
    $service = $config.services[$name]
    Assert-Configuration ($service.network_mode -eq 'host') "$name must use host networking"
    Assert-Configuration ($service.user -eq '1000:1000') "$name must use the EFS owner"
    Assert-Configuration ($service.env_file[0].format -eq 'raw') "$name must preserve literal passwords"
    Assert-Configuration ($service.volumes[0].bind.create_host_path -eq $false) 'Missing EFS path must fail closed'
}
foreach ($name in @('admin', 'builder', 'buyer')) {
    Assert-Configuration (($config.services[$name].command -join ' ') -match '--bind=127\.0\.0\.1:800[234]') "$name listener must be private"
}
Assert-Configuration ($config.services.worker.profiles -contains 'processing') 'Worker must be opt-in'
Assert-Configuration ($config.services.migrate.profiles -contains 'maintenance') 'Migrations must be opt-in'
Assert-Configuration (-not $config.services.ContainsKey('postgres')) 'Must reuse RDS'
Assert-ExternalCommand -Name node
& node (Join-Path $ec2 'test-load-test.cjs')
Assert-LastExitCode -Operation 'Load scenario checks'
Write-Host 'EC2 configuration checks passed. No AWS calls or load test performed.'
