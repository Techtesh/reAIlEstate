param(
    [string]$Container = 'realestate-postgres',
    [string]$Volume = 'realestate-postgres-data',
    [ValidateRange(1024, 65535)][int]$Port = 55432
)
$ErrorActionPreference = 'Stop'
$root = Join-Path $PSScriptRoot '.local'
$configPath = Join-Path $root 'postgres.json'

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
    $config = @{ HOST='127.0.0.1'; PORT="$Port"; NAME='realestate'; USER='realestate'; PASSWORD=[Convert]::ToBase64String($bytes) }
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
            -p "127.0.0.1:$($config.PORT):5432" -v "${Volume}:/var/lib/postgresql/data" `
            -e POSTGRES_PASSWORD -e POSTGRES_USER=realestate -e POSTGRES_DB=realestate postgres:17 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Unable to create PostgreSQL container.' }
    } finally { $env:POSTGRES_PASSWORD = $previous }
} else {
    docker start $container | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Unable to start PostgreSQL.' }
}
Write-Host "PostgreSQL container started at 127.0.0.1:$($config.PORT). Data lives in the $Volume volume."
Write-Host "Check readiness with: docker exec $Container pg_isready -U realestate -d realestate"