Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')
Assert-ExternalCommand -Name docker
$ec2 = (Resolve-Path (Join-Path $PSScriptRoot '../ec2')).Path
# Some Compose versions still require env_file paths to exist with
# --no-env-resolution. Validate an isolated fixture, never the operator's secrets.
$fixture = [IO.Directory]::CreateTempSubdirectory('realestate-compose-check-').FullName
try {
    foreach ($name in @('compose.yml', 'compose.preview.yml', '.env.example')) {
        Copy-Item -LiteralPath (Join-Path $ec2 $name) -Destination (Join-Path $fixture $name)
    }
    Copy-Item -LiteralPath (Join-Path $ec2 'app.env.example') -Destination (Join-Path $fixture 'app.env')
    $stderr = Join-Path $fixture 'compose.stderr'
    $json = & docker compose --profile "*" --env-file (Join-Path $fixture '.env.example') -f (Join-Path $fixture 'compose.yml') config --no-env-resolution --format json 2> $stderr
    if ($LASTEXITCODE -ne 0) {
        $detail = Get-Content -LiteralPath $stderr -Raw
        throw "Parsing EC2 Compose configuration failed with exit code $LASTEXITCODE. Docker Compose: $detail"
    }
    $config = ($json -join "`n") | ConvertFrom-Json -AsHashtable
    $previewJson = & docker compose --profile "*" --env-file (Join-Path $fixture '.env.example') -f (Join-Path $fixture 'compose.yml') -f (Join-Path $fixture 'compose.preview.yml') config --no-env-resolution --format json 2> $stderr
    if ($LASTEXITCODE -ne 0) { throw 'Parsing private preview configuration failed.' }
    $preview = ($previewJson -join "`n") | ConvertFrom-Json -AsHashtable
}
finally {
    foreach ($name in @('compose.yml', 'compose.preview.yml', '.env.example', 'app.env', 'compose.stderr')) {
        $file = Join-Path $fixture $name
        if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file -Force }
    }
    [IO.Directory]::Delete($fixture)
}
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
foreach ($name in @('admin', 'builder', 'buyer')) {
    $service = $preview.services[$name]
    Assert-Configuration (($service.command -join ' ') -match '--bind=127\.0\.0\.1:800[234]') "Preview $name listener must be private"
    Assert-Configuration ($service.environment.SECURE_SSL_REDIRECT -eq 'false') 'SSH preview must not redirect to missing public TLS'
    Assert-Configuration ($service.environment.USE_X_FORWARDED_PROTO -eq 'false') 'Private HTTP preview must use its actual protocol'
    Assert-Configuration ($service.environment.CSRF_TRUSTED_ORIGINS -eq 'http://localhost:18080,http://127.0.0.1:18080') 'Preview CSRF origins must be restricted to the tunnel'
}
Assert-Configuration (-not $preview.services.worker.environment.ContainsKey('SECURE_SSL_REDIRECT')) 'Preview settings must not change worker configuration'
Assert-Configuration ($config.services.worker.profiles -contains 'processing') 'Worker must be opt-in'
Assert-Configuration ($config.services.migrate.profiles -contains 'maintenance') 'Migrations must be opt-in'
Assert-Configuration (-not $config.services.ContainsKey('postgres')) 'Must reuse RDS'
Assert-ExternalCommand -Name node
& node (Join-Path $ec2 'test-load-test.cjs')
Assert-LastExitCode -Operation 'Load scenario checks'
Write-Host 'EC2 configuration checks passed. No AWS calls or load test performed.'
