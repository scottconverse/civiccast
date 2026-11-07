# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
<#
.SYNOPSIS
Download and verify CivicCast Windows release artifacts.

.DESCRIPTION
Two asset-set families are supported:

  NativeCandidate (default when -Repository is scottconverse/civiccast-native):
    the native beta-candidate release shape published by
    scripts/release/publish_beta_candidate.py -- SHA256SUMS.txt, setup.exe,
    setup.exe.sidecar.json, and (with -IncludePacks) every *.ccpack runtime
    pack in packs/ and the complete station-models.json multipart ZIP bundle
    extracted into station/. setup.exe and every runtime pack are verified against SHA256SUMS.txt; the
    sidecar is verified by its own sha256 field matching SHA256SUMS.txt's
    setup.exe line. The asset names here are pinned against the publisher's
    constants by tests/policy/test_windows_release_downloader.py so the two
    cannot drift.

The defaults target the published RC5 release, whose station models are downloaded as the multipart station-models.zip bundle.
For a historical release, specify `-Tag`; `-Version`, if supplied, must match for that release.

  ProofKit / TesterPackage / All (the retired WSL2 rc line's shape): fetch the
    release-artifacts manifest plus the tester package and/or proof kit,
    verified against the manifest's SHA-256 values (falling back to the
    GitHub asset digest for files the manifest does not list).
#>

[CmdletBinding()]
param(
  [string]$Repository = "scottconverse/civiccast-native",
  [string]$Tag = "v1.0.0-rc.5",
  [string]$Version = "",
  [ValidateSet("", "NativeCandidate", "ProofKit", "TesterPackage", "All")]
  [string]$AssetSet = "",
  [switch]$IncludePacks,
  [switch]$Install,
  [string]$Destination = "C:\CivicCastProof",
  [switch]$Force
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
if ([string]::IsNullOrWhiteSpace($Version)) { $Version = $Tag -replace '^v', '' }
if ($Tag -ne "v$Version") { throw 'Tag and Version must identify the same release.' }
if ($Install) { $IncludePacks = $true }

# Asset naming contract shared with scripts/release/publish_beta_candidate.py
# (SETUP_ASSET_NAME / SHA256SUMS_ASSET_NAME / SIDECAR_SUFFIX / PACK_SUFFIX).
$NativeSetupName = "setup.exe"
$NativeSumsName = "SHA256SUMS.txt"
$NativeSidecarSuffix = ".sidecar.json"
$NativePackSuffix = ".ccpack"

if ([string]::IsNullOrWhiteSpace($AssetSet)) {
  if ($Repository -eq "scottconverse/civiccast-native") {
    $AssetSet = "NativeCandidate"
  } else {
    $AssetSet = "ProofKit"
  }
}

function Write-Step {
  param([string]$Message)
  Write-Host "[CivicCast] $Message"
}

function Assert-DownloadSpace {
  param([string]$Root, [long]$RequiredBytes, [string]$Purpose)
  $driveRoot = [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($Root))
  if ($driveRoot.StartsWith('\\')) {
    Write-Warning "Check free space on the network share: $RequiredBytes additional bytes for $Purpose."
    return
  }
  $drive = New-Object IO.DriveInfo($driveRoot)
  Write-Step ("{0}: need {1:N2} GiB additional; {2:N2} GiB available. Installed data needs separate space." -f $Purpose, ($RequiredBytes / 1GB), ($drive.AvailableFreeSpace / 1GB))
  if ($drive.AvailableFreeSpace -lt $RequiredBytes) {
    throw "Insufficient space for $Purpose. Choose a larger local destination; existing downloads are preserved."
  }
}

function Get-Release {
  param([string]$Repository, [string]$Tag)

  $uri = "https://api.github.com/repos/$Repository/releases/tags/$Tag"
  try {
    return Invoke-RestMethod -Uri $uri -Headers @{
      "Accept" = "application/vnd.github+json"
      "User-Agent" = "CivicCast-Windows-Release-Downloader"
    }
  } catch {
    throw "Could not load GitHub Release $Repository $Tag. Publish the release/tag/assets first, then retry. GitHub error: $($_.Exception.Message)"
  }
}

function Get-ReleaseAsset {
  param(
    [object]$Release,
    [string]$Name
  )

  $asset = @($Release.assets | Where-Object { $_.name -eq $Name }) | Select-Object -First 1
  if (-not $asset) {
    $available = @($Release.assets | ForEach-Object { $_.name }) -join ", "
    throw "GitHub Release '$($Release.tag_name)' does not contain required asset '$Name'. Available assets: $available"
  }
  return $asset
}

function Save-ReleaseAsset {
  param(
    [object]$Asset,
    [string]$Target
  )

  if ((Test-Path -LiteralPath $Target) -and -not $Force -and
      ((Get-Item -LiteralPath $Target).Length -eq [long]$Asset.size -or -not $Asset.size)) {
    Write-Step "Using existing $Target"
    return
  }

  $partial = $Target + '.partial'
  if ((Test-Path -LiteralPath $Target) -and -not $Force -and -not (Test-Path -LiteralPath $partial)) {
    [IO.File]::Move($Target, $partial)
  }
  $offset = 0L
  if ((Test-Path -LiteralPath $partial) -and -not $Force) {
    $offset = (Get-Item -LiteralPath $partial).Length
  }
  if ($Asset.size -and $offset -gt [long]$Asset.size) {
    throw "Partial download is larger than $($Asset.name); use -Force to download it again."
  }
  if ($Asset.size -and $offset -eq [long]$Asset.size) {
    if (Test-Path -LiteralPath $Target) { [IO.File]::Replace($partial, $Target, $null) }
    else { [IO.File]::Move($partial, $Target) }
    return # The caller still verifies the complete file's SHA-256.
  }
  Add-Type -AssemblyName System.Net.Http
  $client = [Net.Http.HttpClient]::new()
  $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Get, $Asset.browser_download_url)
  $response = $null
  $stream = $null
  $output = $null
  try {
    $request.Headers.Add('Accept', 'application/octet-stream')
    $request.Headers.Add('User-Agent', 'CivicCast-Windows-Release-Downloader')
    if ($offset -gt 0) {
      $request.Headers.Range = [Net.Http.Headers.RangeHeaderValue]::new($offset, $null)
      Write-Step "Resuming $($Asset.name) at byte $offset"
    } else { Write-Step "Downloading $($Asset.name)" }
    $response = $client.SendAsync($request, [Net.Http.HttpCompletionOption]::ResponseHeadersRead).GetAwaiter().GetResult()
    $null = $response.EnsureSuccessStatusCode()
    $mode = [IO.FileMode]::Create
    if ([int]$response.StatusCode -eq 206) {
      $range = $response.Content.Headers.ContentRange
      if (-not $range -or $range.Unit -ne 'bytes' -or $range.From -ne $offset -or
          ($Asset.size -and $range.Length -ne [long]$Asset.size)) {
        throw 'Download server returned an inconsistent byte range; partial file preserved.'
      }
      $mode = [IO.FileMode]::Append
    } elseif ([int]$response.StatusCode -ne 200) {
      throw "Unexpected download status $([int]$response.StatusCode)."
    }
    # A server ignoring Range returns 200: replace the partial, never append it.
    $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
    $output = [IO.File]::Open($partial, $mode, [IO.FileAccess]::Write, [IO.FileShare]::None)
    $downloaded = if ($mode -eq [IO.FileMode]::Append) { $offset } else { 0L }
    $buffer = New-Object byte[] (1024 * 1024)
    $progressClock = [Diagnostics.Stopwatch]::StartNew()
    while (($count = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
      $output.Write($buffer, 0, $count)
      $downloaded += $count
      if ($progressClock.Elapsed.TotalSeconds -ge 5) {
        Write-Step "$($Asset.name): $downloaded of $($Asset.size) bytes downloaded"
        $progressClock.Restart()
      }
    }
    $output.Flush()
    Write-Step "$($Asset.name): $downloaded bytes downloaded; verifying file"
  } finally {
    if ($output) { $output.Dispose() }
    if ($stream) { $stream.Dispose() }
    if ($response) { $response.Dispose() }
    $request.Dispose()
    $client.Dispose()
  }
  if ($Asset.size -and (Get-Item -LiteralPath $partial).Length -ne [long]$Asset.size) {
    throw "Incomplete download for $($Asset.name); rerun the same command to resume."
  }
  if ($Asset.digest -match '^sha256:([0-9a-fA-F]{64})$') {
    Assert-Sha256 -Path $partial -ExpectedHash $Matches[1]
  }
  if (Test-Path -LiteralPath $Target) { [IO.File]::Replace($partial, $Target, $null) }
  else { [IO.File]::Move($partial, $Target) }
}

function Get-ManifestEntry {
  param(
    [object]$Manifest,
    [string]$FileName
  )

  return @($Manifest.artifacts | Where-Object { $_.filename -eq $FileName }) | Select-Object -First 1
}

function Get-ExpectedSha256 {
  # The release-artifacts manifest is built by one CI job (Linux/portable
  # outputs) and does not cover artifacts a separate job uploads directly
  # (the Windows installer, proof kit, and tester package). When the
  # manifest has no entry for a file, fall back to the GitHub Release
  # asset's own server-computed digest -- the same value a tester's local
  # `Get-FileHash` will produce, and independent of which job built it.
  param(
    [object]$Manifest,
    [object]$Asset,
    [string]$FileName
  )

  $entry = Get-ManifestEntry -Manifest $Manifest -FileName $FileName
  if ($entry) {
    Write-Step "$FileName checksum source: release-artifacts manifest"
    return $entry.sha256
  }
  if ($Asset.digest -and $Asset.digest -match '^sha256:([0-9a-fA-F]{64})$') {
    Write-Step "$FileName checksum source: GitHub Release asset digest (not listed in the release-artifacts manifest, which only covers the Linux build job's outputs)"
    return $Matches[1]
  }
  throw "No checksum available for '$FileName': not in the release manifest and the GitHub Release asset has no usable digest."
}

function Assert-Sha256 {
  param(
    [string]$Path,
    [string]$ExpectedHash
  )

  if ($ExpectedHash -notmatch '^[0-9a-fA-F]{64}$') { throw "Invalid SHA-256 for $Path" }
  $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
  if ($actual -ne $ExpectedHash.ToLowerInvariant()) {
    throw "SHA-256 mismatch for $Path. Expected $ExpectedHash but got $actual."
  }
  Write-Step "Verified SHA-256 for $Path"
}

function Read-Sha256Sums {
  # Parse the standard `<hash>  <filename>` lines publish_beta_candidate.py
  # writes into a filename -> lowercase-hash table.
  param([string]$Path)

  $table = @{}
  foreach ($line in Get-Content -LiteralPath $Path) {
    if ($line -match '^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$') {
      $table[$Matches[2]] = $Matches[1].ToLowerInvariant()
    }
  }
  if ($table.Count -eq 0) {
    throw "$Path contains no parsable '<sha256>  <filename>' lines."
  }
  return $table
}

function Assert-AssetDigest {
  param([object]$Asset, [string]$Path, [switch]$Required)
  if ($Asset.digest -and $Asset.digest -match '^sha256:([0-9a-fA-F]{64})$') {
    Assert-Sha256 -Path $Path -ExpectedHash $Matches[1]
  } elseif ($Required) { throw "GitHub asset '$($Asset.name)' has no usable SHA-256 digest." }
}

function Get-StationEntries {
  param([object]$Manifest)
  if ($Manifest.schema_version -ne 1 -or $Manifest.archive_format -ne 'zip' -or $Manifest.archive_name -ne 'station-models.zip') {
    throw 'Unsupported station-models.json schema or archive format.'
  }
  $entries = @{}
  foreach ($entry in $Manifest.unpacked_files) {
    # Only flat, known station filenames are allowed. This also excludes ADS,
    # rooted paths, dot segments and Windows path aliases before extraction.
    if ($entry.path -notmatch '^station/(station-index\.json|SHA256SUMS\.txt|[a-z0-9][a-z0-9-]*\.ccpack)$' -or $entries.ContainsKey($entry.path)) {
      throw "Unsafe or duplicate station path: $($entry.path)"
    }
    if ($entry.sha256 -notmatch '^[0-9a-fA-F]{64}$' -or [long]$entry.size_bytes -lt 0) { throw 'Invalid station file metadata.' }
    $entries[$entry.path] = $entry
  }
  $required = @('station-index.json', 'SHA256SUMS.txt', 'core.ccpack', 'captions-floor.ccpack', 'captions-whistle.ccpack',
    'summary-gemma4-12b.ccpack', 'summary-gemma4-e4b.ccpack', 'translation-translategemma-4b.ccpack')
  if ($entries.Count -ne $required.Count) { throw 'Station bundle must contain the complete eight-file station set.' }
  foreach ($name in $required) { if (-not $entries.ContainsKey("station/$name")) { throw "Station bundle is missing $name" } }
  return $entries
}

function Assert-StationDirectory {
  param([string]$Root, [object]$Manifest, [hashtable]$Entries, [string]$ExpectedVersion = $Version)
  foreach ($key in $Entries.Keys) {
    $file = Join-Path $Root $key
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing station file: $file" }
    if ((Get-Item -LiteralPath $file).Length -ne [long]$Entries[$key].size_bytes) { throw "Station file size mismatch: $file" }
    Assert-Sha256 -Path $file -ExpectedHash $Entries[$key].sha256
  }
  $indexPath = Join-Path $Root 'station/station-index.json'
  Assert-Sha256 -Path $indexPath -ExpectedHash $Manifest.station_index_sha256
  $index = (Get-Content -LiteralPath $indexPath -Raw | ConvertFrom-Json).manifest
  if ($index.product_version -ne $ExpectedVersion -or @($index.packs).Count -ne 6) { throw 'Station index does not match the requested version and six-pack station set.' }
  $stationSums = Read-Sha256Sums -Path (Join-Path $Root 'station/SHA256SUMS.txt')
  if ($stationSums['station-index.json'] -ne $Manifest.station_index_sha256) { throw 'Station checksum list disagrees with station index hash.' }
  $seen = @{}
  foreach ($pack in $index.packs) {
    $key = "station/$($pack.filename)"
    if (-not $Entries.ContainsKey($key) -or $pack.filename -notlike '*.ccpack' -or $seen.ContainsKey($key) -or
        $pack.required -ne $true -or $pack.sha256 -ne $Entries[$key].sha256 -or
        [long]$pack.bytes -ne [long]$Entries[$key].size_bytes -or $stationSums[$pack.filename] -ne $pack.sha256) {
      throw "Station index/checksum list disagrees with bundle: $($pack.filename)"
    }
    $seen[$key] = $true
  }
}

function Install-StationArchive {
  param([string]$ArchivePath, [string]$Root, [object]$Manifest, [string]$ExpectedVersion = $Version, [switch]$ModelsOnly)
  $entries = Get-StationEntries -Manifest $Manifest
  # Refuse reparse-point directories so validated relative paths stay in Root.
  foreach ($directory in @($Root, (Join-Path $Root 'station'))) {
    if ((Test-Path -LiteralPath $directory) -and ((Get-Item -LiteralPath $directory).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
      throw "Refusing reparse-point destination: $directory"
    }
  }
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $stage = Join-Path $Root ('.station-download-' + [guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $stage | Out-Null
  try {
    $zip = [IO.Compression.ZipFile]::OpenRead($ArchivePath)
    try {
      $seen = @{}
      foreach ($entry in $zip.Entries) {
        if ($entry.FullName -eq 'station/' -and $entry.Length -eq 0) { continue }
        if (-not $entries.ContainsKey($entry.FullName) -or $seen.ContainsKey($entry.FullName) -or
            $entry.Length -ne [long]$entries[$entry.FullName].size_bytes) { throw "Unexpected, duplicate or unsafe ZIP entry: $($entry.FullName)" }
        $seen[$entry.FullName] = $true
      }
      if ($seen.Count -ne $entries.Count) { throw 'Station ZIP is missing required files.' }
    } finally { $zip.Dispose() }
    # .NET extraction streams ZIP64 members and works in Windows PowerShell 5.1.
    [IO.Compression.ZipFile]::ExtractToDirectory($ArchivePath, $stage)
    Assert-StationDirectory -Root $stage -Manifest $Manifest -Entries $entries -ExpectedVersion $ExpectedVersion
    # Check every existing destination before adding any file; never overwrite
    # station data, including with -Force.
    foreach ($key in $entries.Keys) {
      if ($ModelsOnly -and ($key -notlike '*.ccpack' -or $key -eq 'station/core.ccpack')) { continue }
      $target = Join-Path $Root $key
      if (Test-Path -LiteralPath $target) {
        if ((Get-Item -LiteralPath $target).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing reparse-point file: $target" }
        Assert-Sha256 -Path $target -ExpectedHash $entries[$key].sha256
      }
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $Root 'station') | Out-Null
    foreach ($key in $entries.Keys) {
      if ($ModelsOnly -and ($key -notlike '*.ccpack' -or $key -eq 'station/core.ccpack')) { continue }
      $target = Join-Path $Root $key
      if (-not (Test-Path -LiteralPath $target)) { [IO.File]::Move((Join-Path $stage $key), $target) }
    }
  } finally {
    # Stage is an internally generated child of Root, never an archive path.
    Remove-Item -LiteralPath $stage -Recurse -Force
  }
}

function Save-StationBundle {
  param([object]$Release, [string]$Root, [string]$ExpectedVersion = $Version, [switch]$ModelsOnly)
  $manifestAsset = Get-ReleaseAsset -Release $Release -Name 'station-models.json'
  $manifestPath = Join-Path $Root 'station-models.json'
  Save-ReleaseAsset -Asset $manifestAsset -Target $manifestPath
  Assert-AssetDigest -Asset $manifestAsset -Path $manifestPath
  $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
  $entries = Get-StationEntries -Manifest $manifest
  if ($ModelsOnly) {
    $completeModels = $true
    foreach ($key in $entries.Keys) {
      if ($key -notlike '*.ccpack' -or $key -eq 'station/core.ccpack') { continue }
      $existing = Join-Path $Root $key
      if (-not (Test-Path -LiteralPath $existing -PathType Leaf)) { $completeModels = $false; break }
      if ((Get-FileHash -Algorithm SHA256 -LiteralPath $existing).Hash.ToLowerInvariant() -ne $entries[$key].sha256) { $completeModels = $false; break }
    }
    if ($completeModels) { Write-Step 'Reused all five hash-verified model packs already in this kit.'; return }
  }
  if (-not $ModelsOnly) {
    $completeStation = $true
    foreach ($key in $entries.Keys) {
      $existing = Join-Path $Root $key
      if (-not (Test-Path -LiteralPath $existing -PathType Leaf) -or
          (Get-Item -LiteralPath $existing).Length -ne [long]$entries[$key].size_bytes -or
          (Get-FileHash -Algorithm SHA256 -LiteralPath $existing).Hash.ToLowerInvariant() -ne $entries[$key].sha256) {
        $completeStation = $false; break
      }
    }
    if ($completeStation) {
      Assert-StationDirectory -Root $Root -Manifest $manifest -Entries $entries -ExpectedVersion $ExpectedVersion
      Write-Step 'Reused the complete hash-verified station folder; no model archive transfer or extraction needed.'
      return
    }
  }
  if (@($manifest.parts).Count -eq 0) { throw 'Station bundle manifest contains no archive parts.' }
  [long]$archiveBytes = ($manifest.parts | Measure-Object -Property size_bytes -Sum).Sum
  [long]$unpackedBytes = ($manifest.unpacked_files | Measure-Object -Property size_bytes -Sum).Sum
  [long]$remainingParts = 0
  foreach ($part in $manifest.parts) {
    $existing = Join-Path $Root $part.filename
    [long]$have = 0
    if (Test-Path -LiteralPath $existing -PathType Leaf) { $have = (Get-Item -LiteralPath $existing).Length }
    elseif (Test-Path -LiteralPath ($existing + '.partial') -PathType Leaf) { $have = (Get-Item -LiteralPath ($existing + '.partial')).Length }
    $remainingParts += [Math]::Max(0, [long]$part.size_bytes - $have)
  }
  [long]$assemblyBytes = $archiveBytes
  if (Test-Path -LiteralPath (Join-Path $Root 'station-models.zip') -PathType Leaf) { $assemblyBytes = 0 }
  Write-Step ("Model bundle: {0:N2} GiB remaining transfer, {1:N2} GiB additional archive assembly, {2:N2} GiB temporary extraction. Runtime downloads, installed files and station media are separate." -f ($remainingParts / 1GB), ($assemblyBytes / 1GB), ($unpackedBytes / 1GB))
  Assert-DownloadSpace -Root $Root -RequiredBytes ($remainingParts + $assemblyBytes + $unpackedBytes) -Purpose 'model download, archive assembly and extraction'
  $partPaths = @()
  $number = 1
  foreach ($part in $manifest.parts) {
    $expectedName = 'station-models.zip.{0:D3}' -f $number
    if ($part.filename -cne $expectedName -or [long]$part.size_bytes -le 0 -or [long]$part.size_bytes -gt 1900000000) {
      throw "Invalid station archive part: $($part.filename); expected $expectedName"
    }
    $asset = Get-ReleaseAsset -Release $Release -Name $part.filename
    $path = Join-Path $Root $part.filename
    Save-ReleaseAsset -Asset $asset -Target $path
    if ((Get-Item -LiteralPath $path).Length -ne [long]$part.size_bytes) { throw "Station archive part size mismatch: $path" }
    Assert-AssetDigest -Asset $asset -Path $path -Required
    Assert-Sha256 -Path $path -ExpectedHash $part.sha256
    $partPaths += $path
    $number++
  }
  $archivePath = Join-Path $Root 'station-models.zip'
  $createdArchive = $false
  if (-not (Test-Path -LiteralPath $archivePath)) {
    $partial = Join-Path $Root ('.station-archive-' + [guid]::NewGuid().ToString('N'))
    try {
      $output = [IO.File]::Create($partial)
      try {
        foreach ($path in $partPaths) {
          $inputStream = [IO.File]::OpenRead($path)
          try { $inputStream.CopyTo($output) } finally { $inputStream.Dispose() }
        }
      } finally { $output.Dispose() }
      Assert-Sha256 -Path $partial -ExpectedHash $manifest.archive_sha256
      [IO.File]::Move($partial, $archivePath)
      $createdArchive = $true
    } finally { if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force } }
  } else { Assert-Sha256 -Path $archivePath -ExpectedHash $manifest.archive_sha256 }
  Install-StationArchive -ArchivePath $archivePath -Root $Root -Manifest $manifest -ExpectedVersion $ExpectedVersion -ModelsOnly:$ModelsOnly
  if ($createdArchive) { Remove-Item -LiteralPath $archivePath -Force }
}

New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$Destination = (Get-Item -LiteralPath $Destination).FullName

$release = Get-Release -Repository $Repository -Tag $Tag

if ($AssetSet -eq "NativeCandidate") {
  # 1. SHA256SUMS.txt first: it is the hash authority for everything else.
  #    It is itself verified against the GitHub asset digest when one is
  #    available (there is no higher local authority for it).
  $sumsAsset = Get-ReleaseAsset -Release $release -Name $NativeSumsName
  $sumsPath = Join-Path $Destination $NativeSumsName
  Save-ReleaseAsset -Asset $sumsAsset -Target $sumsPath
  if ($sumsAsset.digest -and $sumsAsset.digest -match '^sha256:([0-9a-fA-F]{64})$') {
    Assert-Sha256 -Path $sumsPath -ExpectedHash $Matches[1]
  } else {
    Write-Step "$NativeSumsName has no GitHub asset digest to check against; trusting the downloaded copy as the hash authority"
  }
  $sums = Read-Sha256Sums -Path $sumsPath

  # 2. setup.exe, verified against SHA256SUMS.txt.
  if (-not $sums.ContainsKey($NativeSetupName)) {
    throw "$NativeSumsName does not list $NativeSetupName; refusing to trust the installer."
  }
  $setupAsset = Get-ReleaseAsset -Release $release -Name $NativeSetupName
  $setupPath = Join-Path $Destination $NativeSetupName
  Save-ReleaseAsset -Asset $setupAsset -Target $setupPath
  Assert-Sha256 -Path $setupPath -ExpectedHash $sums[$NativeSetupName]
  Assert-AssetDigest -Asset $setupAsset -Path $setupPath
  $signature = Get-AuthenticodeSignature -LiteralPath $setupPath
  if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|,\s*)CN=Scott Converse(,|$)') {
    throw 'setup.exe must have a valid Authenticode signature from Scott Converse.'
  }

  # 3. The sidecar: its sha256 field must equal SHA256SUMS.txt's setup.exe
  #    line (the same bytes described two independent ways).
  $sidecarName = "$NativeSetupName$NativeSidecarSuffix"
  $sidecarAsset = Get-ReleaseAsset -Release $release -Name $sidecarName
  $sidecarPath = Join-Path $Destination $sidecarName
  Save-ReleaseAsset -Asset $sidecarAsset -Target $sidecarPath
  Assert-AssetDigest -Asset $sidecarAsset -Path $sidecarPath
  $sidecar = Get-Content -LiteralPath $sidecarPath -Raw | ConvertFrom-Json
  if ([string]$sidecar.sha256 -ne $sums[$NativeSetupName]) {
    throw "$sidecarName sha256 '$($sidecar.sha256)' does not match $NativeSumsName's $NativeSetupName entry '$($sums[$NativeSetupName])'."
  }
  Write-Step "Verified $sidecarName agrees with $NativeSumsName"

  # 4. Optionally every *.ccpack runtime pack, each verified against SHA256SUMS.txt.
  if ($IncludePacks) {
    $runtimeNames = @('native-app-payload.ccpack', 'native-cuda-runtime.ccpack', 'native-ffmpeg-runtime.ccpack', 'native-ollama-runtime.ccpack', 'native-server-binaries.ccpack')
    $packRoot = Join-Path $Destination 'packs'
    New-Item -ItemType Directory -Force -Path $packRoot | Out-Null
    foreach ($name in $runtimeNames) {
      if (-not $sums.ContainsKey($name)) { throw "Missing runtime checksum: $name" }
      $packPath = Join-Path $packRoot $name
      if ((Test-Path -LiteralPath $packPath) -and $name -eq 'native-ollama-runtime.ccpack' -and (Get-Item -LiteralPath $packPath).Length -eq 2249822889) {
        Assert-Sha256 -Path $packPath -ExpectedHash $sums[$name]
        continue
      }
      if ($name -eq 'native-ollama-runtime.ccpack' -and @($release.assets | Where-Object { $_.name -eq $name }).Count -eq 0) {
        $asset = Get-ReleaseAsset -Release $release -Name ($name + '.zip')
        $zipPath = Join-Path $packRoot ($name + '.zip')
        Save-ReleaseAsset -Asset $asset -Target $zipPath
        Assert-Sha256 -Path $zipPath -ExpectedHash $sums[$asset.name]
        Assert-AssetDigest -Asset $asset -Path $zipPath
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip = [IO.Compression.ZipFile]::OpenRead($zipPath)
        try {
          if ($zip.Entries.Count -ne 1 -or $zip.Entries[0].FullName -cne $name -or $zip.Entries[0].Length -ne 2249822889) { throw 'Unexpected Ollama archive contents.' }
          $extracted = $packPath + '.extracting'
          try {
            [IO.Compression.ZipFileExtensions]::ExtractToFile($zip.Entries[0], $extracted, $true)
            Assert-Sha256 -Path $extracted -ExpectedHash $sums[$name]
            if (Test-Path -LiteralPath $packPath) { [IO.File]::Replace($extracted, $packPath, $null) }
            else { [IO.File]::Move($extracted, $packPath) }
          } finally { if (Test-Path -LiteralPath $extracted) { Remove-Item -LiteralPath $extracted } }
        } finally { $zip.Dispose() }
      } else {
        $asset = Get-ReleaseAsset -Release $release -Name $name
        Save-ReleaseAsset -Asset $asset -Target $packPath
        Assert-AssetDigest -Asset $asset -Path $packPath
      }
      Assert-Sha256 -Path $packPath -ExpectedHash $sums[$name]
    }

    $overlayPrefix = if ($Version -match '^1\.0\.0-rc\.(\d+)$') { 'rc' + $Matches[1] } else { '' }
    $hasOverlay = $overlayPrefix -and @($release.assets | Where-Object { $_.name -eq ($overlayPrefix + '-station-index.json') }).Count -eq 1
    if (-not $hasOverlay) {
      Save-StationBundle -Release $release -Root $Destination
    } else {
    # RC1 reuses the exact five already-published signed model packs from Beta20.
    # Only the signed station index, checksum list, and tiny core pack differ.
    $overlay = @{
      ($overlayPrefix + '-station-index.json') = 'station-index.json'
      ($overlayPrefix + '-station-SHA256SUMS.txt') = 'SHA256SUMS.txt'
      ($overlayPrefix + '-station-core.ccpack') = 'core.ccpack'
    }
    $stationRoot = Join-Path $Destination 'station'
    $alreadyRc1 = Test-Path -LiteralPath (Join-Path $stationRoot 'SHA256SUMS.txt')
    if ($alreadyRc1) {
      $alreadyRc1 = ((Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $stationRoot 'SHA256SUMS.txt')).Hash.ToLowerInvariant() -eq $sums[($overlayPrefix + '-station-SHA256SUMS.txt')])
    }
    if (-not $alreadyRc1) {
      Write-Step 'Reusing unchanged public Beta20 model downloads; no Git LFS.'
      $modelRelease = Get-Release -Repository $Repository -Tag 'v1.0.0-beta.20'
      $modelManifest = Get-ReleaseAsset -Release $modelRelease -Name 'station-models.json'
      if ($modelManifest.digest -ne 'sha256:a53474f57e81d54dc7b3b4c2f832371c9a045a74fa514689d3351994b1d385b6') { throw 'The pinned Beta20 model manifest differs.' }
      Save-StationBundle -Release $modelRelease -Root $Destination -ExpectedVersion '1.0.0-beta.20' -ModelsOnly
      foreach ($assetName in $overlay.Keys) {
        $asset = Get-ReleaseAsset -Release $release -Name $assetName
        $download = Join-Path $Destination $assetName
        Save-ReleaseAsset -Asset $asset -Target $download
        Assert-Sha256 -Path $download -ExpectedHash $sums[$assetName]
        Assert-AssetDigest -Asset $asset -Path $download
        [IO.File]::Copy($download, (Join-Path $stationRoot $overlay[$assetName]), $true)
      }
    }
    $finalIndex = (Get-Content -LiteralPath (Join-Path $stationRoot 'station-index.json') -Raw | ConvertFrom-Json).manifest
    if ($finalIndex.product_version -ne $Version -or @($finalIndex.packs).Count -ne 6) { throw 'Final station index does not match the requested version and six-pack station set.' }
    $stationSums = Read-Sha256Sums -Path (Join-Path $stationRoot 'SHA256SUMS.txt')
    foreach ($name in $stationSums.Keys) {
      if ($name -notmatch '^[a-zA-Z0-9][a-zA-Z0-9._-]*$') { throw 'Unsafe station checksum filename.' }
      Assert-Sha256 -Path (Join-Path $stationRoot $name) -ExpectedHash $stationSums[$name]
    }
  }
    }

} else {
  $manifestName = "civiccast-$Version-release-artifacts-manifest.json"
  $proofKitName = "civiccast-$Version-clean-windows-proof-kit.zip"
  $testerPackageName = "civiccast-$Version-windows-tester-package.zip"

  $manifestAsset = Get-ReleaseAsset -Release $release -Name $manifestName
  $manifestPath = Join-Path $Destination $manifestName
  Save-ReleaseAsset -Asset $manifestAsset -Target $manifestPath
  $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json

  $requestedAssets = @()
  if ($AssetSet -in @("ProofKit", "All")) {
    $requestedAssets += $proofKitName
  }
  if ($AssetSet -in @("TesterPackage", "All")) {
    $requestedAssets += $testerPackageName
  }

  foreach ($name in $requestedAssets) {
    $asset = Get-ReleaseAsset -Release $release -Name $name
    $target = Join-Path $Destination $name
    Save-ReleaseAsset -Asset $asset -Target $target
    $expectedHash = Get-ExpectedSha256 -Manifest $manifest -Asset $asset -FileName $name
    Assert-Sha256 -Path $target -ExpectedHash $expectedHash

    if ($name -eq $proofKitName) {
      Write-Step "Extracting clean Windows proof kit to $Destination"
      Expand-Archive -LiteralPath $target -DestinationPath $Destination -Force
    }
  }
}

if ($Install) {
  if ($AssetSet -ne 'NativeCandidate') { throw '-Install requires NativeCandidate assets.' }
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  $start = @{ FilePath = $setupPath; ArgumentList = '/S'; Wait = $true; PassThru = $true; WindowStyle = 'Hidden' }
  if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { $start.Verb = 'RunAs' }
  Write-Step 'Installing the verified complete kit. Please wait for setup to finish; installation progress is recorded by setup.'
  $process = Start-Process @start
  if ($process.ExitCode -ne 0) { throw "CivicCast setup exited with code $($process.ExitCode)." }
}
Write-Step "Done."
if ($AssetSet -eq 'NativeCandidate') {
  if ($Install) {
    Write-Step 'Setup completed successfully. Open CivicCast Operator Console from the Start menu to sign in or create the first administrator.'
  } elseif ($IncludePacks) {
    Write-Step 'Verified installer, runtime packs and complete station bundle. Setup will verify signed station metadata during installation.'
    Write-Host "Next command (Windows x64, administrator): & `"$setupPath`" /S"
    Write-Host 'Keep setup.exe, packs\ and station\ together. Full installation requires sufficient disk space for the downloaded kit and installed models.'
  } else { Write-Step 'Installer only. Run this downloader with -IncludePacks before installing to acquire the complete required kit.' }
}
Write-Host ""
Write-Host "Downloaded artifacts:"
Get-ChildItem -LiteralPath $Destination -File | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize

if (Test-Path -LiteralPath (Join-Path $Destination "VERIFY-AND-LAUNCH.ps1")) {
  Write-Host ""
  Write-Host "Next command:"
  Write-Host "  powershell -ExecutionPolicy Bypass -File `"$Destination\VERIFY-AND-LAUNCH.ps1`""
}
