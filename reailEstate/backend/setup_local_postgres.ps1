$ErrorActionPreference = 'Stop'
$root = Join-Path $PSScriptRoot '.local'
$configPath = Join-Path $root 'postgres.json'
$container = 'realestate-postgres'

if (-not (Test-Path $configPath)) {
    # Do not adopt or reset credentials on an unrelated existing container.
    $existing = docker ps -a --filter "name=^/$container`$" --format '{{.Names}}'
    if ($LASTEXITCODE -ne 0) { throw 'Docker is unavailable.' }
    if ($existing) { throw 'Container exists but the local connection file is missing; recover its credentials first.' }
    New-Item -ItemType Directory -Path $root -Force | Out-Null
    $account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls $root /inheritance:r /grant:r "${account}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Unable to restrict local credential directory permissions.' }
    $bytes = [System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)
    $config = @{ HOST='127.0.0.1'; PORT='55432'; NAME='realestate'; USER='realestate'; PASSWORD=[Convert]::ToBase64String($bytes) }
    $config | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding utf8
}
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$existing = docker ps -a --filter "name=^/$container`$" --format '{{.Names}}'
if ($LASTEXITCODE -ne 0) { throw 'Docker is unavailable.' }
if (-not $existing) {
    # Secret value is passed through the environment, never as a command argument or log.
    $previous = $env:POSTGRES_PASSWORD
    try {
        $env:POSTGRES_PASSWORD = $config.PASSWORD
        docker run -d --name $container --restart unless-stopped `
            -p '127.0.0.1:55432:5432' -v 'realestate-postgres-data:/var/lib/postgresql/data' `
            -e POSTGRES_PASSWORD -e POSTGRES_USER=realestate -e POSTGRES_DB=realestate postgres:17 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Unable to create PostgreSQL container.' }
    } finally { $env:POSTGRES_PASSWORD = $previous }
} else {
    docker start $container | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Unable to start PostgreSQL.' }
}
for ($attempt=0; $attempt -lt 60; $attempt++) {
    docker exec $container pg_isready -U realestate -d realestate 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host 'PostgreSQL ready at 127.0.0.1:55432. Data lives in the realestate-postgres-data volume.'
        return
    }
    Start-Sleep -Seconds 2
}
throw 'PostgreSQL readiness timed out.'