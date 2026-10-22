#requires -Version 7.0

function Get-RealEstatePython {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$ApplicationRoot)

    $python = Join-Path $ApplicationRoot 'reailEstate_common\backend\.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw "Python environment is missing: $python"
    }
    return $python
}

function Get-RealEstateServiceDefinition {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [ValidateSet('admin', 'builder', 'buyer')]
        [string]$Service
    )

    $definitions = @{
        admin = @{ Directory = 'adminService'; Port = 8002; Settings = 'admin_service.settings'; Frontend = $false }
        builder = @{ Directory = 'builderService'; Port = 8003; Settings = 'builder_service.settings'; Frontend = $true }
        buyer = @{ Directory = 'buyerService'; Port = 8004; Settings = 'buyer_service.settings'; Frontend = $true }
    }
    return [pscustomobject]@{
        Name = $Service
        Directory = $definitions[$Service].Directory
        Port = $definitions[$Service].Port
        Settings = $definitions[$Service].Settings
        RequiresFrontend = $definitions[$Service].Frontend
    }
}

function Initialize-RealEstateRuntime {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$ApplicationRoot,
        [string]$VisionProfilesFile
    )

    . (Join-Path $ApplicationRoot 'configure_runtime.ps1')
    Set-RealEstateRuntimeEnvironment -ApplicationRoot $ApplicationRoot -VisionProfilesFile $VisionProfilesFile
}

function Assert-RealEstateFrontendBuild {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$ApplicationRoot,
        [Parameter(Mandatory)][ValidateSet('builder', 'buyer')][string]$Service
    )

    $index = Join-Path $ApplicationRoot "reailEstate_common\frontend\dist\$Service\index.html"
    if (-not (Test-Path -LiteralPath $index -PathType Leaf)) {
        throw "$Service frontend is not built. Run build_service_frontends.ps1 first."
    }
}
