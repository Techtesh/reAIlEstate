#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$DestinationRoot = 'D:\',
    [string]$Container = 'realestate-workspace-publish-postgres',
    [switch]$WritersStopped,
    [switch]$DatabaseOnly,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$workspace = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$destination = (Resolve-Path -LiteralPath $DestinationRoot).Path
if ($destination.TrimEnd('\') -eq $workspace -or
    $destination.StartsWith($workspace + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'Snapshots contain private data. Choose a destination outside the repository.'
}
if ($env:WORKSPACE_DATA_ROOT -and
    [System.IO.Path]::GetFullPath($env:WORKSPACE_DATA_ROOT).TrimEnd('\') -ne $workspace.TrimEnd('\')) {
    throw 'WORKSPACE_DATA_ROOT differs from this checkout. Run from the correct workspace configuration.'
}
$config = Get-Content -LiteralPath (Join-Path $PSScriptRoot '.local\postgres.json') -Raw | ConvertFrom-Json
$database = [string]$config.NAME
$databaseUser = [string]$config.USER
foreach ($name in @('HOST', 'PORT', 'DATABASE', 'USER')) {
    $configured = switch ($name) {
        'DATABASE' { $database }
        'USER' { $databaseUser }
        default { [string]$config.$name }
    }
    $override = [Environment]::GetEnvironmentVariable("PG$name")
    if ($override -and $override -ne $configured) {
        throw "PG$name overrides the local database configuration. Clear it before exporting."
    }
}
if ($config.HOST -notin @('localhost', '127.0.0.1')) {
    throw 'The local configuration does not point to a loopback PostgreSQL container.'
}

function Invoke-Docker {
    param([string[]]$Arguments)
    $result = & docker @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker operation '$($Arguments[0])' failed (exit $LASTEXITCODE)." }
    return $result
}

function Read-Database {
    param([string]$Query, [string]$DatabaseName = $database)
    return Invoke-Docker -Arguments @(
        'exec', $Container, 'psql', '-X', '-U', $databaseUser, '-d', $DatabaseName,
        '-v', 'ON_ERROR_STOP=1', '-Atc', $Query
    )
}

function Assert-NoWriters {
    $processes = @(Get-CimInstance Win32_Process | Where-Object {
        $_.Name -match '^(?:python|py)(?:[\d.]*)?\.exe$' -and $_.CommandLine -and
        $_.CommandLine.Replace('/', '\').IndexOf($PSScriptRoot, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    })
    if ($processes.Count) {
        throw "Stop this workspace's Python servers/workers first (PIDs: $($processes.ProcessId -join ', '))."
    }
    $connections = [int](Read-Database "SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend';")
    if ($connections) { throw "The database still has $connections client connection(s). Stop all writers before exporting." }
}

function Get-MediaInventory {
    foreach ($relative in @('user\media', 'output\media', 'reailEstate\reailEstate_common\backend\media')) {
        $root = Join-Path $workspace $relative
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $rootItem = Get-Item -LiteralPath $root -Force
        if ($rootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "Media root '$relative' is a link. Resolve its storage location before exporting."
        }
        foreach ($item in Get-ChildItem -LiteralPath $root -Recurse -Force) {
            if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                throw 'Media contains a symbolic link or junction. Resolve it before exporting.'
            }
            if (-not $item.PSIsContainer) {
                [pscustomobject]@{
                    Path = [System.IO.Path]::GetRelativePath($workspace, $item.FullName)
                    Source = $item.FullName
                    Bytes = [long]$item.Length
                    Modified = $item.LastWriteTimeUtc.Ticks
                }
            }
        }
    }
}

$ports = @(Invoke-Docker -Arguments @('port', $Container, '5432/tcp'))
if (-not ($ports | Where-Object { $_ -eq "127.0.0.1:$($config.PORT)" })) {
    throw 'Container port mapping does not match this workspace. Refusing to back up another database.'
}
$version = [int](Read-Database 'SHOW server_version_num;')
$databaseBytes = [long](Read-Database 'SELECT pg_database_size(current_database());')
$files = @(if (-not $DatabaseOnly) { Get-MediaInventory })
$mediaBytes = if ($files.Count) { [long](($files | Measure-Object -Property Bytes -Sum).Sum) } else { [long]0 }
$drive = [System.IO.DriveInfo]::new([System.IO.Path]::GetPathRoot($destination))
if ($drive.DriveFormat -eq 'FAT32' -and
    (($files | Where-Object { $_.Bytes -ge 4GB }) -or $databaseBytes -ge 4GB)) {
    throw 'A file or the estimated database dump exceeds the FAT32 limit. Use an NTFS/exFAT destination.'
}
$requiredBytes = $mediaBytes + $databaseBytes + 256MB
if ($drive.AvailableFreeSpace -lt $requiredBytes) {
    throw "Not enough free space. Allow at least $([math]::Ceiling($requiredBytes / 1GB)) GiB."
}
Write-Host ("Preflight: {0} media files, {1:N2} GiB media, {2:N1} MiB database, {3:N2} GiB free ({4})." -f
    $files.Count, ($mediaBytes / 1GB), ($databaseBytes / 1MB), ($drive.AvailableFreeSpace / 1GB), $drive.DriveFormat)
if ($CheckOnly) { return }
if (-not $WritersStopped) {
    throw 'Stop all backend/worker processes and uploads, then rerun with -WritersStopped. This script never kills services.'
}
Assert-NoWriters

$commit = & git -C $workspace rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw 'Unable to identify the source Git commit.' }
$changes = @(& git -C $workspace status --porcelain)
if ($LASTEXITCODE -ne 0) { throw 'Unable to record the source working-tree status.' }
if ($changes.Count) { Write-Warning 'The working tree has changes. Its status will be recorded; source code is not bundled.' }

$id = 'workspace-demo-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$bundle = Join-Path $destination $id
New-Item -ItemType Directory -Path $bundle | Out-Null
$incomplete = Join-Path $bundle 'INCOMPLETE.txt'
'Export in progress. Do not restore this folder until COMPLETE.txt exists.' |
    Set-Content -LiteralPath $incomplete -Encoding utf8
Write-Host "Snapshot destination: $bundle"
Write-Warning 'This snapshot contains private project evidence. Original accounts and authentication data are excluded; evidence itself is not anonymized.'

$temporaryDump = "/tmp/$id.dump"
$originalDump = "/tmp/$id-source.dump"
$sanitizerPath = "/tmp/$id-sanitize.sql"
$temporaryDatabase = 'demo_export_' + [guid]::NewGuid().ToString('N')
$verificationDatabase = 'demo_verify_' + [guid]::NewGuid().ToString('N')
$temporaryCreated = $false
$verificationCreated = $false
$countQuery = @'
SELECT json_build_object(
  'projects', (SELECT count(*) FROM platform_api_project),
  'organizations', (SELECT count(*) FROM platform_api_organization),
  'documents', (SELECT count(*) FROM platform_api_evidencedocument),
  'floorPlansAndLayouts', (SELECT count(*) FROM platform_api_floorplan),
  'floorPlanAssets', (SELECT count(*) FROM platform_api_floorplanasset),
  'cropDrafts', (SELECT count(*) FROM platform_api_layoutcropdraft),
  'ingestionRuns', (SELECT count(*) FROM platform_api_ingestionrun),
  'evaluationRuns', (SELECT count(*) FROM platform_api_agentworkflowrun),
  'evaluationSteps', (SELECT count(*) FROM platform_api_agentworkflowstep),
  'reviewTasks', (SELECT count(*) FROM platform_api_reviewtask));
'@
$manifestFiles = [System.Collections.Generic.List[object]]::new()
$started = [DateTime]::UtcNow.ToString('o')
try {
    Write-Host 'Exporting PostgreSQL (no changes to the source database)...'
    $sourceCounts = Read-Database $countQuery
    Invoke-Docker -Arguments @('exec', $Container, 'pg_dump', '-U', $databaseUser, '-d', $database,
        '--format=custom', '--no-owner', '--no-privileges', "--file=$originalDump") | Out-Null
    Invoke-Docker -Arguments @('exec', $Container, 'createdb', '-U', $databaseUser, '-T', 'template0', $temporaryDatabase) | Out-Null
    $temporaryCreated = $true
    Invoke-Docker -Arguments @('exec', $Container, 'pg_restore', '-U', $databaseUser, '-d', $temporaryDatabase,
        '--single-transaction', '--no-owner', '--no-privileges', $originalDump) | Out-Null
    Invoke-Docker -Arguments @('cp', (Join-Path $PSScriptRoot 'sanitize_demo_snapshot.sql'), "${Container}:$sanitizerPath") | Out-Null
    Write-Host 'Removing original accounts and personal records in an isolated temporary database...'
    Invoke-Docker -Arguments @('exec', $Container, 'psql', '-X', '-q', '-U', $databaseUser, '-d', $temporaryDatabase,
        '-v', 'ON_ERROR_STOP=1', '-f', $sanitizerPath) | Out-Null
    $counts = Read-Database $countQuery $temporaryDatabase
    if ("$counts" -ne "$sourceCounts") { throw 'Sanitization changed retained project/evaluation record counts.' }
    Invoke-Docker -Arguments @('exec', $Container, 'pg_dump', '-U', $databaseUser, '-d', $temporaryDatabase,
        '--format=custom', '--no-owner', '--no-privileges', "--file=$temporaryDump") | Out-Null
    Write-Host 'Test-restoring the sanitized archive and checking account exclusions...'
    Invoke-Docker -Arguments @('exec', $Container, 'createdb', '-U', $databaseUser, '-T', 'template0', $verificationDatabase) | Out-Null
    $verificationCreated = $true
    Invoke-Docker -Arguments @('exec', $Container, 'pg_restore', '-U', $databaseUser, '-d', $verificationDatabase,
        '--single-transaction', '--no-owner', '--no-privileges', $temporaryDump) | Out-Null
    if ("$(Read-Database $countQuery $verificationDatabase)" -ne "$sourceCounts") {
        throw 'Test restore changed retained project/evaluation record counts.'
    }
    $accountCheck = Read-Database @'
SELECT (SELECT count(*) FROM auth_user)=1
AND EXISTS (SELECT 1 FROM auth_user WHERE username='demo-evidence-placeholder'
    AND password='!' AND NOT is_active AND NOT is_staff AND NOT is_superuser
    AND email='' AND first_name='' AND last_name='' AND last_login IS NULL)
AND NOT EXISTS (SELECT 1 FROM django_session)
AND NOT EXISTS (SELECT 1 FROM auth_user_groups)
AND NOT EXISTS (SELECT 1 FROM auth_user_user_permissions)
AND NOT EXISTS (SELECT 1 FROM platform_api_organizationmembership)
AND NOT EXISTS (SELECT 1 FROM platform_api_idempotencyrecord);
'@ $verificationDatabase
    if ("$accountCheck".Trim() -ne 't') { throw 'Restored snapshot contains unexpected account/access data.' }
    Write-Host 'Sanitized database restored successfully; project/evaluation counts preserved and no original accounts retained.'
    $catalogue = @(Invoke-Docker -Arguments @('exec', $Container, 'pg_restore', '--list', $temporaryDump))
    if (-not ($catalogue | Where-Object { $_ -match 'TABLE DATA public platform_api_project ' })) {
        throw 'The archive does not contain the expected project table.'
    }
    Invoke-Docker -Arguments @('cp', "${Container}:$temporaryDump", (Join-Path $bundle 'database.dump')) | Out-Null
    $sourceDigest = ((Invoke-Docker -Arguments @('exec', $Container, 'sha256sum', $temporaryDump)) -split '\s+')[0]
    $dump = Get-Item -LiteralPath (Join-Path $bundle 'database.dump')
    $dumpDigest = (Get-FileHash -LiteralPath $dump.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($dumpDigest -ne $sourceDigest) { throw 'Database archive checksum mismatch.' }
    $manifestFiles.Add([ordered]@{ path = 'database.dump'; bytes = $dump.Length; sha256 = $dumpDigest })

    $done = 0
    $copiedBytes = [long]0
    foreach ($file in $files) {
        $target = Join-Path $bundle $file.Path
        [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($target)) | Out-Null
        $sourceHash = (Get-FileHash -LiteralPath $file.Source -Algorithm SHA256).Hash
        [System.IO.File]::Copy($file.Source, $target, $false)
        $targetHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash
        if ($sourceHash -ne $targetHash -or (Get-Item -LiteralPath $target).Length -ne $file.Bytes) {
            throw "Media copy verification failed for '$($file.Path)'."
        }
        $manifestFiles.Add([ordered]@{ path = $file.Path; bytes = $file.Bytes; sha256 = $targetHash.ToLowerInvariant() })
        $done++
        $copiedBytes += $file.Bytes
        if ($done % 100 -eq 0 -or $done -eq $files.Count) {
            Write-Host ("Copied and verified {0}/{1} files ({2:N2}/{3:N2} GiB)." -f
                $done, $files.Count, ($copiedBytes / 1GB), ($mediaBytes / 1GB))
        }
    }
    Assert-NoWriters
    $after = @(if (-not $DatabaseOnly) { Get-MediaInventory })
    if ($files.Count -ne $after.Count -or
        ($files.Count -gt 0 -and @(Compare-Object $files $after -Property Path, Bytes, Modified).Count)) {
        throw 'Source media changed during export. Keep writers stopped and create a new snapshot.'
    }
    [ordered]@{
        formatVersion = 2
        accountPolicy = 'Original accounts removed; one inactive placeholder with unusable password; no sessions or memberships'
        databaseRestoreVerified = $true
        scope = $(if ($DatabaseOnly) { 'database-only' } else { 'database-and-media' })
        mediaIncluded = -not [bool]$DatabaseOnly
        startedAtUtc = $started
        completedAtUtc = [DateTime]::UtcNow.ToString('o')
        gitCommit = "$commit".Trim()
        gitWorkingTreeChanges = $changes
        database = @{ engine = 'postgresql'; serverVersionNumber = $version; name = $database; rowCounts = ($counts | ConvertFrom-Json) }
        mediaFileCount = $files.Count
        mediaBytes = $mediaBytes
        files = $manifestFiles.ToArray()
    } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $bundle 'manifest.json') -Encoding utf8
    @"
PRIVATE DEMO SNAPSHOT
Database only: $([bool]$DatabaseOnly). Database engine: PostgreSQL.
This folder includes private project records. Source free text is NOT anonymized.
When database-only is true, media files are intentionally omitted; images/PDFs need a separate authorized copy.
Original users, password hashes, sessions, memberships and personal preferences are excluded.
Buyer requirements, recommendations, shares, shortlists, travel caches and operational queues are omitted.
Audit metadata and recognized identity keys in JSON are cleared; free text and files are not anonymized.
One inactive anonymous placeholder with an unusable password preserves required uploader/owner links.
Local configuration, environment files, node_modules and virtual environments are not included.
Review private project content separately before sharing.

RESTORE ON A DIFFERENT MACHINE
1. Check out Git commit $("$commit".Trim()). Check manifest.json for uncommitted source changes.
2. Install the backend/frontend dependencies using their README instructions.
3. Provision a NEW EMPTY PostgreSQL 17 database with the workspace setup script:
   .\reailEstate\reailEstate_common\backend\setup_local_postgres.ps1 -Container realestate-workspace-publish-postgres -Volume realestate-workspace-publish-postgres-data -Port 15432
   Do not copy the source machine's .local\postgres.json. The setup creates fresh credentials.
4. From this snapshot folder:
   docker cp .\database.dump realestate-workspace-publish-postgres:/tmp/demo.dump
   docker exec realestate-workspace-publish-postgres pg_restore -U realestate -d realestate --single-transaction --no-owner --no-privileges /tmp/demo.dump
   Check exit codes. Do not use --clean or restore over an existing working database.
5. Copy user\media, output\media, and reailEstate\reailEstate_common\backend\media (when present) into the same
   relative locations in the NEW checkout. Do not copy only the database: images need these files.
6. Set WORKSPACE_DATA_ROOT to the NEW checkout root.
7. Create NEW demo accounts with manage.py createsuperuser and Django admin.
   Assign new builder users to the required organizations. No source account can log in.
   Start the frontend and API; verify a project image, a layout, and its evaluation.
   Configure provider credentials separately if needed.
8. Leave the extraction dispatcher STOPPED for a browse-only demo. Copied pending jobs can incur costs.

INTEGRITY
manifest.json records SHA-256 and byte length for database.dump and every media file.
Before restore, compare them using Get-FileHash -Algorithm SHA256.
The archive catalogue and every copied file were checked at export.
A restore into a separate temporary database was verified, including record counts and account exclusions.
"@ | Set-Content -LiteralPath (Join-Path $bundle 'RESTORE.txt') -Encoding utf8
} finally {
    # Cleanup only the uniquely named databases/files created by this invocation.
    try {
        if ($verificationCreated) { Invoke-Docker -Arguments @('exec', $Container, 'dropdb', '-U', $databaseUser, $verificationDatabase) | Out-Null }
    } finally {
        try {
            if ($temporaryCreated) { Invoke-Docker -Arguments @('exec', $Container, 'dropdb', '-U', $databaseUser, $temporaryDatabase) | Out-Null }
        } finally {
            Invoke-Docker -Arguments @('exec', $Container, 'rm', '-f', '--', $temporaryDump, $originalDump, $sanitizerPath) | Out-Null
        }
    }
}
Remove-Item -LiteralPath $incomplete
'Export completed; database archive and all media copies verified. See manifest.json and RESTORE.txt.' |
    Set-Content -LiteralPath (Join-Path $bundle 'COMPLETE.txt') -Encoding utf8
Write-Host "Verified snapshot ready: $bundle"
