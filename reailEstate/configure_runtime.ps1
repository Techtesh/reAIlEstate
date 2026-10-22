#requires -Version 7.0

function Set-RealEstateRuntimeEnvironment {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$ApplicationRoot,

        [string]$VisionProfilesFile
    )

    if (-not $VisionProfilesFile) {
        $VisionProfilesFile = $env:FLOOR_PLAN_VISION_PROFILES_FILE
    }
    if (-not $VisionProfilesFile) {
        $VisionProfilesFile = Join-Path $ApplicationRoot 'reailEstate_common\backend\vision-profiles.json'
    }
    $env:FLOOR_PLAN_VISION_PROFILES_FILE = [IO.Path]::GetFullPath($VisionProfilesFile)
    if ((Test-Path -LiteralPath $env:FLOOR_PLAN_VISION_PROFILES_FILE -PathType Leaf) -and
        [string]::IsNullOrWhiteSpace($env:FLOOR_PLAN_EXTRACTION_MODE)) {
        $env:FLOOR_PLAN_EXTRACTION_MODE = 'ai'
    }
}
