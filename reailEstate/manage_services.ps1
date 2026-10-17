#requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateSet('start', 'stop', 'restart', 'status')]
    [string]$Action = 'status',

    [ValidateSet('all', 'admin', 'builder', 'buyer', 'worker', 'frontend')]
    [string[]]$Service = @('admin', 'builder', 'buyer', 'worker'),

    [switch]$SkipBuild,
    [switch]$SkipMigrations,
    [string]$VisionProfilesFile
)

$ErrorActionPreference = 'Stop'
if ($VisionProfilesFile) {
    $VisionProfilesFile = (Resolve-Path -LiteralPath $VisionProfilesFile -ErrorAction Stop).Path
}
. (Join-Path $PSScriptRoot 'runtime_helpers.ps1')

$ports = @{ admin = 8002; builder = 8003; buyer = 8004; frontend = 5173 }
$defaultServices = @('admin', 'builder', 'buyer', 'worker')
$allServices = @($defaultServices + 'frontend')
$selected = if ($Service -contains 'all') {
    $allServices
} else {
    @($Service | Select-Object -Unique)
}
$runtimeRoot = Join-Path $PSScriptRoot '.runtime'
$logRoot = Join-Path $runtimeRoot 'logs'

function Test-WorkspaceCommand([string]$CommandLine) {
    return $CommandLine -and
        $CommandLine.IndexOf($PSScriptRoot, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Get-ComponentProcesses([string]$Name) {
    $definition = if ($Name -in @('admin', 'builder', 'buyer')) {
        Get-RealEstateServiceDefinition -Service $Name
    } else {
        $null
    }
    return @(Get-CimInstance Win32_Process | Where-Object {
        if (-not (Test-WorkspaceCommand $_.CommandLine)) {
            return $false
        }
        if ($definition) {
            return $_.Name -eq 'python.exe' -and
                $_.CommandLine -like "*$($definition.Directory)\manage.py*" -and
                $_.CommandLine -like '*runserver*'
        }
        if ($Name -eq 'worker') {
            return $_.Name -eq 'python.exe' -and
                $_.CommandLine -like '*adminService\manage.py*' -and
                $_.CommandLine -like '*run_extraction_dispatcher*'
        }
        return $_.Name -eq 'node.exe' -and $_.CommandLine -like '*vite*' -and
            $_.CommandLine -notmatch 'vite\.js"?\s+build\b'
    })
}

function Get-PortOwner([int]$Port) {
    return Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Wait-ServiceReady([string]$Name) {
    if ($Name -eq 'worker') {
        $deadline = (Get-Date).AddSeconds(10)
        do {
            if ((Get-ComponentProcesses $Name).Count -gt 0) {
                return
            }
            Start-Sleep -Milliseconds 250
        } while ((Get-Date) -lt $deadline)
        throw "The worker did not start. Check $logRoot\worker.error.log."
    }

    $port = $ports[$Name]
    $deadline = (Get-Date).AddSeconds(30)
    do {
        try {
            $path = if ($Name -eq 'frontend') { '/' } else { '/health/live' }
            $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port$path" -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                return
            }
        } catch {
            Start-Sleep -Milliseconds 300
        }
    } while ((Get-Date) -lt $deadline)
    throw "$Name did not become healthy on port $port. Check $logRoot\$Name.error.log."
}

function Start-Component([string]$Name) {
    $existing = Get-ComponentProcesses $Name
    if ($existing.Count -gt 0) {
        Write-Host "$Name is already running (PID $($existing.ProcessId -join ', '))."
        return
    }
    if ($Name -ne 'worker') {
        $owner = Get-PortOwner $ports[$Name]
        if ($owner) {
            throw "Port $($ports[$Name]) is already owned by PID $($owner.OwningProcess). Stop that process or choose the canonical service port."
        }
    }

    New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
    $stdout = Join-Path $logRoot "$Name.log"
    $stderr = Join-Path $logRoot "$Name.error.log"
    Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
    if ($Name -eq 'frontend') {
        $frontend = Join-Path $PSScriptRoot 'reailEstate_common\frontend'
        Start-Process -FilePath 'npm.cmd' -ArgumentList @('--prefix', $frontend, 'run', 'dev:buyer') `
            -WorkingDirectory $PSScriptRoot -WindowStyle Hidden `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr | Out-Null
    } elseif ($Name -eq 'worker') {
        $script = Join-Path $PSScriptRoot 'run_worker.ps1'
        $arguments = @('-NoProfile', '-File', $script)
        if ($VisionProfilesFile) {
            $arguments += @('-VisionProfilesFile', $VisionProfilesFile)
        }
        Start-Process -FilePath 'pwsh' -ArgumentList $arguments -WorkingDirectory $PSScriptRoot `
            -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr | Out-Null
    } else {
        $script = Join-Path $PSScriptRoot 'run_service.ps1'
        $arguments = @('-NoProfile', '-File', $script, '-Service', $Name)
        if ($VisionProfilesFile) {
            $arguments += @('-VisionProfilesFile', $VisionProfilesFile)
        }
        Start-Process -FilePath 'pwsh' -ArgumentList $arguments -WorkingDirectory $PSScriptRoot `
            -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr | Out-Null
    }
    Wait-ServiceReady $Name
    Write-Host "Started $Name."
}

function Stop-Component([string]$Name) {
    $processes = Get-ComponentProcesses $Name
    if ($processes.Count -eq 0) {
        Write-Host "$Name is already stopped."
        return
    }
    foreach ($process in $processes | Sort-Object ProcessId -Descending) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
    $deadline = (Get-Date).AddSeconds(10)
    do {
        if ((Get-ComponentProcesses $Name).Count -eq 0) {
            Write-Host "Stopped $Name."
            return
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    throw "$Name did not stop cleanly."
}

function Show-Status {
    $rows = foreach ($name in @('admin', 'builder', 'buyer', 'worker', 'frontend')) {
        $processes = Get-ComponentProcesses $name
        $port = $ports[$name]
        $listening = if ($port) { [bool](Get-PortOwner $port) } else { $false }
        [pscustomobject]@{
            Service = $name
            State = if ($name -eq 'worker') {
                if ($processes.Count) { 'running' } else { 'stopped' }
            } elseif ($listening -and $processes.Count) {
                'running'
            } elseif ($listening) {
                'port occupied'
            } elseif ($processes.Count) {
                'starting'
            } else {
                'stopped'
            }
            Port = if ($port) { $port } else { '-' }
            ProcessIds = $processes.ProcessId -join ','
        }
    }
    $rows | Format-Table -AutoSize
}

function Start-Selected {
    $backendSelected = @($selected | Where-Object { $_ -in $defaultServices }).Count -gt 0
    if ($backendSelected -and -not $SkipMigrations) {
        $python = Get-RealEstatePython -ApplicationRoot $PSScriptRoot
        Initialize-RealEstateRuntime -ApplicationRoot $PSScriptRoot -VisionProfilesFile $VisionProfilesFile
        & $python (Join-Path $PSScriptRoot 'adminService\manage.py') migrate --noinput '--settings=admin_service.settings'
        if ($LASTEXITCODE -ne 0) {
            throw 'Database migration failed.'
        }
    }
    if (-not $SkipBuild -and @($selected | Where-Object { $_ -in @('builder', 'buyer') }).Count -gt 0) {
        & (Join-Path $PSScriptRoot 'build_service_frontends.ps1')
        if ($LASTEXITCODE -ne 0) {
            throw 'Frontend build failed.'
        }
    }
    foreach ($name in $selected) {
        Start-Component $name
    }
    Show-Status
}

switch ($Action) {
    'start' { Start-Selected }
    'stop' {
        foreach ($name in @($selected)[($selected.Count - 1)..0]) {
            Stop-Component $name
        }
        Show-Status
    }
    'restart' {
        foreach ($name in @($selected)[($selected.Count - 1)..0]) {
            Stop-Component $name
        }
        Start-Selected
    }
    'status' { Show-Status }
}
exit 0
