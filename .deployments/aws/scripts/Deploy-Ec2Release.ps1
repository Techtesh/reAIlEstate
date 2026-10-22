[CmdletBinding()]
param(
    [Parameter(Mandatory)][string] $ReleaseArchive,
    [Parameter(Mandatory)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]*$')][string] $HostName,
    [Parameter(Mandatory)][string] $KeyPath
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Common.ps1')
$archive = (Resolve-Path -LiteralPath $ReleaseArchive).Path
$key = (Resolve-Path -LiteralPath $KeyPath).Path
$leaf = Split-Path -Leaf $archive
if ($leaf -notmatch '^realestate-([a-f0-9]{40})\.tar\.gz$') {
    throw 'Select the realestate-FULL_COMMIT_SHA.tar.gz release archive.'
}
$sha = $Matches[1]
$checksum = (Get-Content -LiteralPath "$archive.sha256" -Raw).Trim().Split(' ')[0].ToLowerInvariant()
if ($checksum -notmatch '^[a-f0-9]{64}$' -or
    (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $checksum) {
    throw 'Release checksum mismatch.'
}
foreach ($command in @('ssh', 'scp')) { Assert-ExternalCommand -Name $command }
$destination = "ec2-user@$HostName"
# Require the host key verified during initial SSH setup.
& ssh -i $key -o StrictHostKeyChecking=yes $destination 'true'
Assert-LastExitCode -Operation 'Verifying SSH connection and known host key'
& scp -i $key -o StrictHostKeyChecking=yes $archive "${destination}:/home/ec2-user/$leaf"
Assert-LastExitCode -Operation 'Transferring release'
$deploy = (Resolve-Path (Join-Path $PSScriptRoot '../ec2/deploy-release.sh')).Path
& scp -i $key -o StrictHostKeyChecking=yes $deploy "${destination}:/home/ec2-user/deploy-release.sh"
Assert-LastExitCode -Operation 'Transferring deployment script'
& ssh -i $key -o StrictHostKeyChecking=yes $destination "bash /home/ec2-user/deploy-release.sh $sha $checksum"
Assert-LastExitCode -Operation 'Deploying EC2 release and verifying readiness'
