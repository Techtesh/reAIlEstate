param(
    [string]$InputCsv = (Join-Path $PSScriptRoot 'config/mmr-metro-candidates.csv'),
    [string]$Report = (Join-Path $PSScriptRoot '../../output/reports/mmr-metro-candidates.json')
)

$ErrorActionPreference = 'Stop'
$source = (Resolve-Path -LiteralPath $InputCsv).Path
$destination = [IO.Path]::GetFullPath($Report)
if (Test-Path -LiteralPath $destination) { throw 'Report already exists. Choose a new -Report path; existing files are never replaced.' }
$rows = @(Import-Csv -LiteralPath $source)
if ($rows.Count -eq 0) { throw 'The candidate CSV is empty.' }
$columns = @($rows[0].PSObject.Properties.Name)
if (@(Compare-Object @('name', 'line', 'latitude', 'longitude') $columns).Count) {
    throw 'Expected exactly these CSV columns: name,line,latitude,longitude.'
}
$culture = [Globalization.CultureInfo]::InvariantCulture
$candidates = @(
    for ($index = 0; $index -lt $rows.Count; $index++) {
        $row = $rows[$index]
        $sourceRow = $index + 2
        if ([string]::IsNullOrWhiteSpace($row.name) -or [string]::IsNullOrWhiteSpace($row.line)) {
            throw "Station name and line required at CSV row $sourceRow."
        }
        $location = [ordered]@{}
        foreach ($axis in @('latitude', 'longitude')) {
            $value = 0.0
            $limit = if ($axis -eq 'latitude') { 90 } else { 180 }
            if (-not [double]::TryParse($row.$axis, [Globalization.NumberStyles]::Float, $culture, [ref]$value) -or
                -not [double]::IsFinite($value) -or [Math]::Abs($value) -gt $limit) {
                throw "Invalid $axis at CSV row $sourceRow."
            }
            $location[$axis] = $value
        }
        $coordinateText = $location.latitude.ToString('R', $culture) + ',' + $location.longitude.ToString('R', $culture)
        $encoded = [Uri]::EscapeDataString($coordinateText)
        [pscustomobject][ordered]@{
            sourceRow = $sourceRow
            name = $row.name.Trim()
            line = $row.line.Trim()
            location = $location
            mapsUrl = "https://www.google.com/maps/search/?api=1&query=$encoded"
            serviceStatus = 'unverified'
            authoritySourceUrl = $null
            evidenceAsOf = $null
        }
    }
)
$duplicates = @(
    foreach ($group in ($candidates | Group-Object -Property line, name | Where-Object Count -GT 1)) {
        $locations = @($group.Group | ForEach-Object {
            $_.location.latitude.ToString('R', $culture) + ',' + $_.location.longitude.ToString('R', $culture)
        } | Sort-Object -Unique)
        [pscustomobject]@{
            line = $group.Group[0].line
            name = $group.Group[0].name
            sourceRows = @($group.Group.sourceRow)
            issue = if ($locations.Count -gt 1) { 'conflicting_coordinates' } else { 'duplicate_coordinates' }
        }
    }
)
$aliases = @(
    foreach ($group in ($candidates | Group-Object -Property {
        $_.line + '|' + $_.location.latitude.ToString('R', $culture) + ',' + $_.location.longitude.ToString('R', $culture)
    } | Where-Object Count -GT 1)) {
        $names = @($group.Group.name | Sort-Object -Unique)
        if ($names.Count -gt 1) {
            [pscustomobject]@{ line = $group.Group[0].line; names = $names; sourceRows = @($group.Group.sourceRow); issue = 'possible_alias_or_colocation' }
        }
    }
)
$result = [ordered]@{
    schemaVersion = 1
    state = 'needs_review'
    generatedAt = [DateTimeOffset]::UtcNow.ToString('o')
    sourceSha256 = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
    provenance = 'User-supplied station names, lines and coordinates; original Maps URLs replaced by generated coordinate links.'
    caveat = 'Links locate supplied coordinates only. No station identity, entrance, Google place ID or operating status has been verified. Not an active metro catalogue.'
    counts = [ordered]@{
        inputRows = $rows.Count
        candidates = $candidates.Count
        exactNameLineGroups = @($candidates | Group-Object -Property line, name).Count
        conflictingCoordinateGroups = @($duplicates | Where-Object issue -EQ 'conflicting_coordinates').Count
        identicalDuplicateGroups = @($duplicates | Where-Object issue -EQ 'duplicate_coordinates').Count
        possibleAliasGroups = $aliases.Count
    }
    candidates = $candidates
    duplicateGroups = $duplicates
    possibleAliases = $aliases
}
$content = $result | ConvertTo-Json -Depth 10
$null = [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination))
$stream = [IO.File]::Open($destination, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
try {
    $bytes = [Text.UTF8Encoding]::new($false).GetBytes($content)
    $stream.Write($bytes, 0, $bytes.Length)
} finally { $stream.Dispose() }
Write-Host "Candidate report created: $destination"
$result.counts | ConvertTo-Json -Compress