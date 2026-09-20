#requires -Version 7.0

function Set-RealEstateRuntimeEnvironment {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$ApplicationRoot,

        [string]$VisionProfilesFile
    )

    $defaultVisionProfilesFile = Join-Path $ApplicationRoot 'reailEstate_common\backend\vision-profiles.local.json'
    if (-not $VisionProfilesFile -and
        [string]::IsNullOrWhiteSpace($env:FLOOR_PLAN_VISION_PROFILES) -and
        (Test-Path -LiteralPath $defaultVisionProfilesFile -PathType Leaf)) {
        $VisionProfilesFile = $defaultVisionProfilesFile
    }
    if (-not $VisionProfilesFile) {
        return
    }

    $resolvedVisionProfilesFile = (Resolve-Path -LiteralPath $VisionProfilesFile -ErrorAction Stop).Path
    $visionProfilesJson = Get-Content -LiteralPath $resolvedVisionProfilesFile -Raw
    try {
        $visionProfiles = ConvertFrom-Json -InputObject $visionProfilesJson -AsHashtable -ErrorAction Stop
    }
    catch {
        throw "Vision profile file is not valid JSON: $resolvedVisionProfilesFile"
    }
    if (-not $visionProfiles -or $visionProfiles.Count -eq 0) {
        throw "Vision profile file must contain at least one profile: $resolvedVisionProfilesFile"
    }

    $env:FLOOR_PLAN_VISION_PROFILES = $visionProfilesJson
    if ([string]::IsNullOrWhiteSpace($env:FLOOR_PLAN_DEFAULT_VISION_PROFILE)) {
        if ($visionProfiles.Count -ne 1) {
            throw 'Set FLOOR_PLAN_DEFAULT_VISION_PROFILE when the vision profile file contains multiple profiles.'
        }
        $env:FLOOR_PLAN_DEFAULT_VISION_PROFILE = @($visionProfiles.Keys)[0]
    }
    if ([string]::IsNullOrWhiteSpace($env:FLOOR_PLAN_EXTRACTION_MODE)) {
        $env:FLOOR_PLAN_EXTRACTION_MODE = 'ai'
    }
}
