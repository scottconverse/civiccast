# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
#
# Run-GateA.ps1 -- host orchestrator for Gate A, the automated station-
# acceptance release gate. Runs ON THE HOST (not in Sandbox).
#
# What it does, in order:
#   1. Resolve the candidate kit -- either download it via `gh run download`
#      (-RunId, a single throttled stream -- fine for manual/local use) or
#      use an already-extracted directory (-KitDir, the shape
#      gate-a-station-acceptance.yml hands in after its own parallel,
#      chunked actions/download-artifact@v4 fetch -- see that workflow's
#      header for why the workflow no longer calls -RunId itself).
#      With -KitDir, -SourceSha and -RunId are accepted as optional metadata
#      so the verdict JSON still carries the candidate sha and run id even
#      though this script did not do the resolving itself.
#   2. Validate the kit layout (glob for the installer, don't assume a name;
#      confirm the station bundle is present).
#   3. Point sandbox-lab\kit-download at the resolved kit via an NTFS
#      directory junction (never a copy -- the assembled kit is ~20+ GB;
#      see native-beta-candidate-artifacts.yml's own header for why these
#      artifacts are that large).
#   4. Reset hoststore\ (every gate run is a FRESH install -- a stale
#      persistent install directory would silently skip install evidence).
#   5. Run Host-Launch-Sandbox-Test.ps1, which itself clears output\, writes
#      SOAK_MINUTES.txt, renders the .wsb from its template, launches the
#      Sandbox, polls for the authoritative DONE.json completion signal, and
#      (on the normal-completion path only) drains its own teardown -- waits,
#      bounded, for the VM and its mapped-folder handles to actually release
#      before returning, so a following Checkout step does not hit EBUSY on a
#      still-tearing-down VM. See docs/ops/gate-a.md, "Teardown drain".
#   6. Judge the result with scripts/gate_a_verdict.py (fail-closed).
#   7. Copy output\ to evidence\<source_sha>\<utc-timestamp>\ regardless of
#      verdict -- a FAIL needs its evidence preserved at least as much as a
#      PASS does.
#   8. Print the verdict and exit 0 (PASS), 1 (FAIL), or 2 (not a
#      station-acceptance finding at all -- BUSY because Windows Sandbox
#      stayed occupied by another process and this run never launched, a
#      quiet/wedged mapped output folder mid-run, a plain timeout, no VM, a
#      missing prerequisite, or a bad kit layout). None of those is ever
#      reported as a FAIL: a run whose evidence never reached the host
#      supports no conclusion about the product at all.
#
# Requires on PATH: gh (GitHub CLI, only for -RunId), uv (for the Python
# judge). Must run on a Windows host with the Windows Sandbox feature
# enabled -- see docs/ops/gate-a.md for the runner setup
# (sandbox-lab/runner/Install-GateARunner.ps1).

[CmdletBinding(DefaultParameterSetName = 'ByRunId')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'ByRunId')]
    [Parameter(Mandatory = $false, ParameterSetName = 'ByKitDir')]
    [Nullable[long]]$RunId,

    [Parameter(Mandatory = $true, ParameterSetName = 'ByKitDir')]
    [string]$KitDir,

    # Optional metadata, -KitDir only: the caller (gate-a-station-
    # acceptance.yml) already resolved the candidate sha itself to name the
    # `native-beta-kit-<sha>` artifact for download, so it hands that same
    # sha back here rather than making this script re-derive it. When
    # omitted, the kit's own station\native-station-bundle-report.json is
    # tried next, then the -KitDir leaf directory name, then
    # 'unknown-local'.
    [Parameter(ParameterSetName = 'ByKitDir')]
    [string]$SourceSha,

    [string]$Root = $PSScriptRoot,
    [int]$SoakMinutes = 20,
    # Passed straight through to Host-Launch-Sandbox-Test.ps1. It must stay
    # ABOVE In-Sandbox-Report.ps1's -MaxScriptMinutes (150) so the
    # in-sandbox watchdog always fires first and the host's own bound is the
    # last resort. Raised 120 -> 170 with that default under
    # <gate-a-mapped-folder-stalls>: at 120 the host would have given up
    # BEFORE the watchdog it depends on, turning every long run into an
    # unexplained host timeout. Three numbers, one setting -- 150 (in
    # sandbox) < 170 (host poll), and the host's -QuietShareMinutes ends a
    # dead channel long before either.
    [int]$TimeoutMinutes = 170,
    [string]$Repo = 'scottconverse/civiccast',

    # Passed straight through to Host-Launch-Sandbox-Test.ps1: minutes to
    # wait for Windows Sandbox to become free before giving up. Windows
    # Sandbox is a single-instance-per-machine resource shared with an
    # independent, unrelated build system on this box -- see that script's
    # header and docs/ops/gate-a.md for the shared-sandbox guard. This is a
    # wait BEFORE the run starts and is deliberately not part of the
    # 150/170 budget ordering above, which governs a run already underway.
    [int]$SandboxWaitMinutes = 90,

    # Passed straight through to Host-Launch-Sandbox-Test.ps1: minutes of no
    # change anywhere under output\, with our own VM still alive, before the
    # run is declared a harness error rather than waited out to
    # -TimeoutMinutes. See that script's quiet-share detector and
    # docs/ops/gate-a.md, "Mapped-folder stalls".
    [int]$QuietShareMinutes = 15,

    # Passed straight through to Host-Launch-Sandbox-Test.ps1: bound (seconds)
    # and poll interval (seconds) for its post-teardown drain, which waits
    # for the sandbox VM and its VSMB handles on the mapped folders to
    # actually release before this script's own evidence copy (step 6/7
    # below) and the caller's next Checkout step run. See
    # docs/ops/gate-a.md, "Teardown drain".
    [int]$TeardownDrainSeconds = 300,
    [int]$TeardownDrainPollSeconds = 5,

    # Passed straight through to Host-Launch-Sandbox-Test.ps1: minutes a
    # WindowsSandbox server/client process may sit with no vmmemWindowsSandbox
    # before the pre-launch busy guard classifies it an ORPHAN (a leftover
    # from a prior run's teardown, see -TeardownDrainSeconds above) rather
    # than someone else's live session, and proceeds to launch instead of
    # waiting out the rest of -SandboxWaitMinutes on it. See that script's
    # <gate-a-orphan-guard> header comment and docs/ops/gate-a.md, "Shared
    # Windows Sandbox: the busy guard -- orphan detection".
    [int]$OrphanGraceMinutes = 10,

    # DIRTY-BOX LANE <gate-a-dirty-lane>: run the remnant lane instead of the
    # plain clean-box lane. Passed to Host-Launch-Sandbox-Test.ps1 as
    # -DirtyMode (which writes the DIRTY_MODE.txt guest input) and to
    # scripts/gate_a_verdict.py as --lane dirty (which adds the dirty-lane
    # checks: prep/preservation, operator-data survival, orphaned-tier
    # fallback). A dirty run performs TWO install cycles inside the sandbox,
    # so pass -TimeoutMinutes of at least 230 with this switch (the launcher
    # enforces the floor itself). The clean lane is untouched when this is
    # absent. See docs/ops/gate-a.md, "Dirty lane".
    [switch]$DirtyLane,

    # Optional cross-version dirty-lane inputs. Both are required together;
    # omitting both preserves the legacy uninstall-remnant sub-shape.
    [string]$PreviousKitDir,
    [string]$PreviousSourceSha,

    # DOWNLOAD-ONLY LANE <gate-a-download-only-lane>: proves the download-only
    # install/upgrade path the K1 fix silently broke -- d4-activate-station
    # started requiring a station\ folder beside setup.exe and aborting
    # otherwise, so every OTHER Gate A lane (which installs from the full
    # kit) never caught the regression. Implies -DirtyLane and cross-version
    # -UpgradeMode: phase 1 installs the pinned previous candidate from its
    # full kit (-PreviousKitDir/-PreviousSourceSha, both REQUIRED with this
    # switch), then phase 2 runs the CURRENT candidate's setup.exe from a
    # payload directory this script builds containing ONLY setup.exe and
    # packs\ (the runtime packs, side-loaded because the sandbox has no
    # network) -- never station\ -- so activation must reuse an
    # already-activated station's cached model packs (a parallel change) or
    # fail. Never combined with the dirty lane's own orphaned-tier remnant
    # seed; the judge's --lane download-only never runs dirty_orphaned_tier.
    # See docs/ops/gate-a.md, "Download-only lane".
    [switch]$DownloadOnlyLane,

    # Optional host-side source for the orphaned-caption-tier remnant seed: a
    # directory shaped like <ProgramData>\CivicCast\components\captions-large-v3
    # (i.e. carrying models\faster-whisper-large-v3\ with the REAL, hash-valid
    # model files -- a stub cannot work; see In-Sandbox-Report.ps1's prologue
    # note P6). When present it is staged to hoststore\dirty-seed\ for the
    # sandbox to plant after the phase-1 uninstall. When absent, the dirty
    # lane still runs but reports the orphaned-tier sub-shape as SKIP.
    [string]$DirtySeedLargeV3Dir = 'C:\CivicCastTester\dirty-seed\captions-large-v3',

    # <gate-a-audit-BL-10> The candidate's migration head, computed by the CI
    # job at BUILD time from the checked-out source (NOT from the station
    # under test) and forwarded to the guest. See
    # Host-Launch-Sandbox-Test.ps1's -ExpectedMigrationHead comment and
    # scripts/gate_a_verdict.py's post-upgrade revision check. When empty,
    # this script computes it locally from the repo it is running out of,
    # which is still an origin independent of the guest.
    [string]$ExpectedMigrationHead
)

$ErrorActionPreference = 'Stop'

function Write-Step {
    param([string]$Message)
    Write-Host "[Run-GateA] $Message" -ForegroundColor Cyan
}

function Exit-HarnessError {
    param([string]$Message)
    Write-Error "[Run-GateA] HARNESS ERROR: $Message"
    exit 2
}

Write-Step "Root: $Root"
Write-Step "SoakMinutes=$SoakMinutes TimeoutMinutes=$TimeoutMinutes Repo=$Repo"

$hasPreviousKit = -not [string]::IsNullOrWhiteSpace($PreviousKitDir)
$hasPreviousSha = -not [string]::IsNullOrWhiteSpace($PreviousSourceSha)
if ($hasPreviousKit -xor $hasPreviousSha) {
    Exit-HarnessError "-PreviousKitDir and -PreviousSourceSha must be supplied together"
}
if ($DownloadOnlyLane -and -not ($hasPreviousKit -and $hasPreviousSha)) {
    Exit-HarnessError "-DownloadOnlyLane requires -PreviousKitDir and -PreviousSourceSha together (phase 1 installs the pinned previous candidate from its full kit)"
}
# -DownloadOnlyLane implies the dirty lane's cross-version upgrade shape --
# phase 1 (the pinned previous candidate, from its full kit) is identical to
# -DirtyLane -PreviousKitDir/-PreviousSourceSha. $dirtyLaneActive, not
# $DirtyLane itself, gates every downstream behavior shared between the two
# switches from here on, so a caller only has to pass -DownloadOnlyLane.
$dirtyLaneActive = [bool]$DirtyLane -or [bool]$DownloadOnlyLane
if ($hasPreviousKit -and -not $dirtyLaneActive) {
    Exit-HarnessError "cross-version inputs require -DirtyLane or -DownloadOnlyLane"
}
$upgradeMode = $dirtyLaneActive -and $hasPreviousKit

# The judge's --lane value: 'download-only' takes priority over plain
# 'dirty' (the two are mutually exclusive in practice -- -DownloadOnlyLane
# forces $dirtyLaneActive true on its own), then 'dirty', then 'clean'.
$judgeLaneName = if ($DownloadOnlyLane) { 'download-only' } elseif ($DirtyLane) { 'dirty' } else { 'clean' }

# --------------------------------------------------------------------------
# 1. Resolve the candidate kit.
# --------------------------------------------------------------------------

# PowerShell variable names are case-insensitive, so the working variable
# below (`$sourceSha`) and the `-SourceSha` parameter are THE SAME variable
# -- capture the caller's value under a distinct name before it gets reset,
# or `$sourceSha = $null` two lines down silently wipes out -SourceSha.
$callerSourceSha = $SourceSha

$sourceSha = $null
$kitSourceDir = $null
$resolvedRunId = $null

if ($PSCmdlet.ParameterSetName -eq 'ByRunId') {
    $resolvedRunId = $RunId
    $ghCmd = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $ghCmd) {
        Exit-HarnessError "gh (GitHub CLI) not found on PATH -- required to resolve -RunId $RunId"
    }

    Write-Step "Resolving head SHA for run $RunId in $Repo..."
    $runJson = & gh run view $RunId -R $Repo --json 'headSha,status,conclusion,workflowName' 2>&1
    if ($LASTEXITCODE -ne 0) {
        Exit-HarnessError "gh run view $RunId failed: $runJson"
    }
    $runInfo = $runJson | ConvertFrom-Json
    $sourceSha = $runInfo.headSha
    if (-not $sourceSha) {
        Exit-HarnessError "gh run view $RunId returned no headSha"
    }
    Write-Step "Run $RunId ($($runInfo.workflowName)): status=$($runInfo.status) conclusion=$($runInfo.conclusion) headSha=$sourceSha"
    if ($runInfo.conclusion -and $runInfo.conclusion -ne 'success') {
        Write-Warning "Run $RunId did not conclude 'success' (conclusion=$($runInfo.conclusion)) -- judging it anyway since -RunId was explicit, but its kit may be incomplete or absent."
    }

    $stagingDir = Join-Path $Root "kit-staging\$sourceSha"
    $alreadyStaged = (Test-Path $stagingDir) -and ((Get-ChildItem -Path $stagingDir -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count -gt 0)
    if ($alreadyStaged) {
        Write-Step "Kit already staged at $stagingDir -- skipping download."
    } else {
        $artifactName = "native-beta-kit-$sourceSha"
        Write-Step "Downloading artifact $artifactName from run $RunId into $stagingDir..."
        New-Item -ItemType Directory -Force -Path $stagingDir | Out-Null
        $dl = & gh run download $RunId -R $Repo -n $artifactName -D $stagingDir 2>&1
        if ($LASTEXITCODE -ne 0) {
            Exit-HarnessError "gh run download failed for artifact $artifactName (run $RunId): $dl"
        }
        Write-Step "Download complete."
    }
    $kitSourceDir = $stagingDir
} else {
    if (-not (Test-Path $KitDir)) {
        Exit-HarnessError "-KitDir does not exist: $KitDir"
    }
    $kitSourceDir = (Resolve-Path $KitDir).Path

    if ($RunId) {
        $resolvedRunId = $RunId
    }

    if ($callerSourceSha) {
        # Caller (gate-a-station-acceptance.yml) already resolved this
        # itself to name the artifact it downloaded -- trust it outright.
        $sourceSha = $callerSourceSha
        Write-Step "Using caller-supplied source_sha=$sourceSha"
    } else {
        # No -SourceSha given: try the kit's own build report next (best
        # effort -- as of this writing native-station-bundle-report.json's
        # schema in scripts/build_native_station_bundle.py does not carry a
        # sha-shaped field, so this is forward-looking, not a currently
        # populated path), then fall back to sniffing the -KitDir leaf name
        # against the kit-staging\<sha>\ convention, then give up honestly.
        $reportPath = Get-ChildItem -Path $kitSourceDir -Filter 'native-station-bundle-report.json' -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($reportPath) {
            try {
                $report = Get-Content -Path $reportPath.FullName -Raw | ConvertFrom-Json
                $candidate = $report.source_sha
                if (-not $candidate) { $candidate = $report.git_sha }
                if ($candidate -and ($candidate -match '^[0-9a-f]{7,40}$')) {
                    $sourceSha = $candidate
                    Write-Step "Recovered source_sha=$sourceSha from $($reportPath.FullName)"
                }
            } catch {
                Write-Warning "Could not parse $($reportPath.FullName) for a source_sha: $_"
            }
        }

        if (-not $sourceSha) {
            $leaf = Split-Path -Leaf $kitSourceDir
            if ($leaf -match '^[0-9a-f]{7,40}$') {
                $sourceSha = $leaf
            } else {
                $sourceSha = 'unknown-local'
            }
        }
    }
    Write-Step "Using pre-extracted kit at $kitSourceDir (source_sha=$sourceSha, run_id=$resolvedRunId)"
}

# --------------------------------------------------------------------------
# 2. Validate the kit layout. Glob for the installer -- never a hard-coded
#    filename, since the version string changes every candidate.
# --------------------------------------------------------------------------

$installerExe = Get-ChildItem -Path $kitSourceDir -Filter '*setup.exe' -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $installerExe) {
    Exit-HarnessError "No *setup.exe found anywhere under $kitSourceDir -- bad or incomplete kit"
}
$stationIndex = Get-ChildItem -Path $kitSourceDir -Filter 'station-index.json' -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $stationIndex) {
    Exit-HarnessError "No station-index.json found anywhere under $kitSourceDir -- bad or incomplete kit (K1 activation will fail loud on this)"
}
Write-Step "Kit validated: installer=$($installerExe.Name), station bundle=$($stationIndex.Directory.FullName)"

if ($upgradeMode) {
    if ($PreviousSourceSha -eq $sourceSha) {
        Exit-HarnessError "-PreviousSourceSha equals current source_sha ($sourceSha) -- same-candidate reinstall is not a cross-version upgrade"
    }
    if (-not (Test-Path $PreviousKitDir)) {
        Exit-HarnessError "-PreviousKitDir does not exist: $PreviousKitDir"
    }
    $previousKitSourceDir = (Resolve-Path $PreviousKitDir).Path
    $previousInstaller = Get-ChildItem -Path $previousKitSourceDir -Filter '*setup.exe' -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
    $previousStationIndex = Get-ChildItem -Path $previousKitSourceDir -Filter 'station-index.json' -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $previousInstaller -or -not $previousStationIndex) {
        Exit-HarnessError "Previous full kit is incomplete at $previousKitSourceDir -- setup.exe and station-index.json are both required"
    }
    $previousPhysicalDir = $previousKitSourceDir
    for ($previousHop = 0; $previousHop -lt 8; $previousHop++) {
        $previousProbe = Get-Item -LiteralPath $previousPhysicalDir -Force -ErrorAction SilentlyContinue
        if (-not $previousProbe -or -not $previousProbe.LinkType) { break }
        $previousNext = @($previousProbe.Target) | Select-Object -First 1
        if (-not $previousNext) { break }
        if (-not [System.IO.Path]::IsPathRooted($previousNext)) { $previousNext = Join-Path (Split-Path -Parent $previousPhysicalDir) $previousNext }
        $previousPhysicalDir = $previousNext
    }
    $previousDownload = Join-Path $Root 'previous-kit-download'
    if (Test-Path $previousDownload) {
        $previousItem = Get-Item $previousDownload -Force
        if ($previousItem.LinkType) { $previousItem.Delete() } else { Remove-Item -LiteralPath $previousDownload -Recurse -Force }
    }
    New-Item -ItemType Junction -Path $previousDownload -Target $previousPhysicalDir | Out-Null
    Write-Step "previous-kit-download -> $previousPhysicalDir (source_sha=$PreviousSourceSha)"
}

# Count what is actually in the station bundle directory, not just that a
# station-index.json exists somewhere under the kit <gate-a-run7-findings>.
# Run7's installer failed with "a signed station bundle (station-index.json
# and ITS PACKS) was not found", and this script's only pre-launch assertion
# was the index file's existence -- so the log it left behind cannot answer
# whether the packs beside it were present. It can now.
$stationDir = $stationIndex.Directory.FullName
$stationFiles = @(Get-ChildItem -Path $stationDir -File -ErrorAction SilentlyContinue)
$stationBytes = ($stationFiles | Measure-Object -Property Length -Sum).Sum
if (-not $stationBytes) { $stationBytes = 0 }
Write-Step ("Station bundle inventory: {0} file(s), {1:N1} GB, at {2}" -f $stationFiles.Count, ($stationBytes / 1GB), $stationDir)
Write-Step ("Station bundle names: " + (($stationFiles | Select-Object -First 12 | ForEach-Object { $_.Name }) -join ', '))

# --------------------------------------------------------------------------
# 3. Point kit-download\ at the resolved kit via a directory junction --
#    never a copy. Reset it first so a stale junction from a prior run can
#    never silently point at the wrong kit.
#
#    <gate-a-run7-findings>: point it at the PHYSICAL directory, not at
#    whatever -KitDir happened to be. `Resolve-Path` above does not follow
#    reparse points, and the workflow's "reuse a locally pre-staged kit" step
#    makes sandbox-lab/kit-staging/<sha> a junction to
#    C:\CivicCastTester\kit-staging\<sha> -- so without this, kit-download is
#    a junction whose target is another junction, and that two-hop chain is
#    what gets handed to Windows Sandbox's VSMB. This is hardening, not the
#    proven cause of run7's missing station bundle: run6 passed with the
#    byte-identical two-hop chain. See docs/ops/gate-a.md.
# --------------------------------------------------------------------------

$kitPhysicalDir = $kitSourceDir
$hops = 0
while ($hops -lt 8) {
    $probe = $null
    try { $probe = Get-Item -LiteralPath $kitPhysicalDir -Force -ErrorAction Stop } catch { break }
    if (-not $probe.LinkType) { break }
    $next = @($probe.Target) | Select-Object -First 1
    if (-not $next) { break }
    if (-not [System.IO.Path]::IsPathRooted($next)) { $next = Join-Path (Split-Path -Parent $kitPhysicalDir) $next }
    $kitPhysicalDir = $next
    $hops++
}
if ($kitPhysicalDir -ne $kitSourceDir) {
    Write-Step "Kit path resolved through $hops reparse point(s): $kitSourceDir -> $kitPhysicalDir"
}

$kitDownload = Join-Path $Root 'kit-download'
if (Test-Path $kitDownload) {
    $item = Get-Item $kitDownload -Force
    if ($item.LinkType) {
        # Junction/symlink: remove the link itself, never recurse into the target.
        $item.Delete()
    } else {
        Remove-Item -Path $kitDownload -Recurse -Force -ErrorAction SilentlyContinue
    }
}
New-Item -ItemType Junction -Path $kitDownload -Target $kitPhysicalDir | Out-Null
Write-Step "kit-download -> $kitPhysicalDir (junction, physical target)"

# --------------------------------------------------------------------------
# 3b. DOWNLOAD-ONLY LANE <gate-a-download-only-lane>: repoint kit-download at
#     a FILTERED payload directory containing ONLY setup.exe and a
#     HARD-LINKED packs\ tree -- never station\, never a reparse point at
#     any level -- so phase 2's install (the current candidate, run by the
#     unchanged normal acceptance flow against $PayloadDir =
#     C:\CivicCastPayload, which the .wsb template already maps from
#     kit-download\) must activate the station without a station\ directory
#     beside setup.exe. NEVER modify the source kit itself. Phase 1 (the
#     pinned previous candidate) is untouched by this: it installs from the
#     separately mapped C:\CivicCastPreviousPayload (previous-kit-download\,
#     below), which DOES carry a full station\ -- exactly like the plain
#     -DirtyLane -UpgradeMode cross-version shape.
#
#     <gate-a-download-only-lane-review> BLOCKER 1/2 fixes:
#       - The builder (New-DownloadOnlyPayload, sandbox-lab/scripts/
#         Build-DownloadOnlyPayload.ps1) hard-links every packs\ file instead
#         of junctioning the whole directory -- Host-Launch-Sandbox-Test.ps1
#         resolves the OUTER MappedFolder HostFolder through reparse points
#         precisely because VSMB is not trusted to traverse one; a junction
#         planted INSIDE the mapped tree is exactly that failure mode one
#         level down.
#       - Cleanup (Remove-DownloadOnlyPayload / Restore-DownloadOnlyKitDownload)
#         runs from the try/finally wrapping the REST of this script (steps 4
#         through 8, below), so kit-download-filtered\ is removed and
#         kit-download is restored to the real kit on EVERY exit path --
#         success, FAIL, BUSY/HARNESS_ERROR, or a thrown harness error --
#         never left behind for the next job's `git clean -ffdx` to walk
#         through, the exact failure mode that already cost a 26 GB pinned
#         kit once (see gate-a-station-acceptance.yml's dirty job, "Unlink
#         the pinned previous-kit junction" step).
# --------------------------------------------------------------------------

$filteredPayload = Join-Path $Root 'kit-download-filtered'

# --------------------------------------------------------------------------
# Everything from here through the end of the script -- BUILDING the
# filtered payload (download-only lane only) and steps 4-8 -- runs inside
# one try/finally so the download-only lane's cleanup ALWAYS runs: on a
# clean PASS, a judged FAIL, a BUSY/HARNESS_ERROR non-verdict, a thrown
# harness error (including every Exit-HarnessError call below, which PS
# `exit` unwinds through this finally before the process actually
# terminates), AND a throw from New-DownloadOnlyPayload itself (e.g. a
# missing packs\ directory, or the station\ sanity check) -- the payload
# build runs first specifically so a failure THERE is also covered, not
# just a failure downstream of it. For every other lane this finally is a
# cheap no-op (guarded on $DownloadOnlyLane).
# --------------------------------------------------------------------------
try {

if ($DownloadOnlyLane) {
    . (Join-Path $Root 'scripts\Build-DownloadOnlyPayload.ps1')
    $payloadResult = New-DownloadOnlyPayload -KitPhysicalDir $kitPhysicalDir -InstallerExePath $installerExe.FullName -PayloadDir $filteredPayload
    Write-Step "DownloadOnlyLane: built filtered payload at $filteredPayload (installer=$($payloadResult.InstallerName), packs files=$($payloadResult.FileCount) [hard-linked=$($payloadResult.HardLinkCount), copied=$($payloadResult.CopyFallbackCount)], no station\)"

    if (Test-Path $kitDownload) {
        $kdItem = Get-Item $kitDownload -Force
        if ($kdItem.LinkType) {
            $kdItem.Delete()
        } else {
            Remove-Item -Path $kitDownload -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    New-Item -ItemType Junction -Path $kitDownload -Target $filteredPayload | Out-Null
    Write-Step "DownloadOnlyLane: kit-download -> $filteredPayload (filtered payload: setup.exe + hard-linked packs\ only, no station\, no reparse points)"
}

# --------------------------------------------------------------------------
# 4. Reset hoststore\ -- every gate run is a fresh install.
# --------------------------------------------------------------------------

$hoststore = Join-Path $Root 'hoststore'
if (Test-Path $hoststore) {
    Get-ChildItem -Path $hoststore -Force | Where-Object { $_.Name -ne '.gitkeep' } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
} else {
    New-Item -ItemType Directory -Force -Path $hoststore | Out-Null
}
# <gate-a-audit-MN-19> RE-ENUMERATE. The delete above is -ErrorAction
# SilentlyContinue and the old code printed the "fresh-install guarantee"
# line whether or not anything was actually removed. hoststore\install IS the
# install root the guest writes to (.wsb.template:26-29), and a previous run
# that left handles open (see TEARDOWN-DRAIN-TIMEOUT.txt's
# `vm_gone=False handles_free=False ... remaining_pids=22300`) makes the
# delete a silent no-op -- after which the next run installs over the
# previous run's tree and calls it a clean box. State the post-condition.
$hoststoreRemnants = @(Get-ChildItem -Path $hoststore -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne '.gitkeep' })
if ($hoststoreRemnants.Count -gt 0) {
    Exit-HarnessError ("hoststore reset did NOT complete: " +
        "$($hoststoreRemnants.Count) entr$(if ($hoststoreRemnants.Count -eq 1) { 'y' } else { 'ies' }) remain under $hoststore " +
        "($(($hoststoreRemnants | Select-Object -First 5 | ForEach-Object { $_.Name }) -join ', ')). " +
        "This is the install root the guest writes to, so the fresh-install guarantee is void; " +
        "most often a previous run's processes still hold handles (see TEARDOWN-DRAIN-TIMEOUT.txt). " +
        "Clear them and re-run rather than installing over the previous run's tree.")
}
Write-Step "hoststore reset (fresh-install guarantee) -- verified empty"

# Dirty lane only: stage the optional orphaned-tier seed AFTER the reset so
# it rides into the sandbox via the already-mapped hoststore folder (no .wsb
# template change, no new VSMB share). Copy, not junction: the mapped-folder
# path already resolves reparse points at the TOP level only, and a reparse
# point INSIDE a share is exactly the shape <gate-a-run7-findings> exists to
# avoid handing VSMB.
if ($DirtyLane) {
    if ($DirtySeedLargeV3Dir -and (Test-Path (Join-Path $DirtySeedLargeV3Dir 'models'))) {
        $seedDst = Join-Path $hoststore 'dirty-seed\captions-large-v3'
        New-Item -ItemType Directory -Force -Path $seedDst | Out-Null
        Write-Step "Dirty lane: staging orphaned-tier seed from $DirtySeedLargeV3Dir into $seedDst ..."
        & robocopy.exe $DirtySeedLargeV3Dir $seedDst /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
        if ($LASTEXITCODE -ge 8) {
            Exit-HarnessError "robocopy of the dirty-lane orphaned-tier seed failed (exit $LASTEXITCODE)"
        }
        Write-Step "Dirty lane: orphaned-tier seed staged."
    } else {
        Write-Step "Dirty lane: no orphaned-tier seed at $DirtySeedLargeV3Dir (models\ missing) -- the orphaned-tier sub-shape will be reported SKIP by the judge."
    }
}

# --------------------------------------------------------------------------
# 5. Run the host launcher. It clears output\, writes SOAK_MINUTES.txt,
#    renders the .wsb, launches Sandbox, and polls for DONE.json.
# --------------------------------------------------------------------------

$launcherPath = Join-Path $Root 'Host-Launch-Sandbox-Test.ps1'
if (-not (Test-Path $launcherPath)) {
    Exit-HarnessError "Host-Launch-Sandbox-Test.ps1 not found at $launcherPath"
}

# <gate-a-audit-BL-10> Resolve the BUILD-time migration head. Preference
# order: the caller's explicit value (the CI job computed it from the
# candidate's checked-out source), then a local computation from the repo
# this script lives in. Either way it is computed on the HOST, out of source,
# never read back from the station under test -- that independence is the
# whole point. When neither is available the guest records
# KIT_EXPECTED_HEAD=<not-provided> and the judge FAILs the upgrade lanes.
$resolvedExpectedHead = $ExpectedMigrationHead
if ([string]::IsNullOrWhiteSpace($resolvedExpectedHead)) {
    try {
        $repoRoot = Split-Path $Root -Parent
        # scripts/gate_a_expected_head.py is standard-library only on purpose
        # -- no alembic, no civiccast import, no installed environment -- so
        # this works on a bare runner.
        $headOut = & python (Join-Path $repoRoot 'scripts\gate_a_expected_head.py') --repo-root $repoRoot 2>&1
        if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace($headOut)) {
            $resolvedExpectedHead = ([string]($headOut | Select-Object -Last 1)).Trim()
            Write-Step "Expected migration head computed locally from $repoRoot : $resolvedExpectedHead"
        } else {
            Write-Warning "Could not compute the expected migration head locally (python exit $LASTEXITCODE): $headOut"
        }
    } catch {
        Write-Warning "Could not compute the expected migration head locally: $_"
    }
} else {
    Write-Step "Expected migration head supplied by the caller: $resolvedExpectedHead"
}

Write-Step "Launching Host-Launch-Sandbox-Test.ps1 (TimeoutMinutes=$TimeoutMinutes, SoakMinutes=$SoakMinutes, SandboxWaitMinutes=$SandboxWaitMinutes, QuietShareMinutes=$QuietShareMinutes, TeardownDrainSeconds=$TeardownDrainSeconds, TeardownDrainPollSeconds=$TeardownDrainPollSeconds, OrphanGraceMinutes=$OrphanGraceMinutes, DownloadOnlyLane=$([bool]$DownloadOnlyLane))..."
& $launcherPath -Root $Root -TimeoutMinutes $TimeoutMinutes -SoakMinutes $SoakMinutes -SandboxWaitMinutes $SandboxWaitMinutes -QuietShareMinutes $QuietShareMinutes -TeardownDrainSeconds $TeardownDrainSeconds -TeardownDrainPollSeconds $TeardownDrainPollSeconds -OrphanGraceMinutes $OrphanGraceMinutes -DirtyMode:$dirtyLaneActive -UpgradeMode:$upgradeMode -PreviousSourceSha $PreviousSourceSha -DownloadOnlyMode:$DownloadOnlyLane -ExpectedMigrationHead $resolvedExpectedHead
$launcherExit = $LASTEXITCODE
Write-Step "Host launcher exited with code $launcherExit"

$outputDir = Join-Path $Root 'output'

# --------------------------------------------------------------------------
# 6+7. Judge the run (if there's anything to judge) and always preserve
#      evidence, regardless of verdict.
# --------------------------------------------------------------------------

$utcStamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmssZ')
$evidenceDir = Join-Path $Root "evidence\$sourceSha\$utcStamp"

$hasEvidence = (Test-Path $outputDir) -and ((Get-ChildItem -Path $outputDir -Force -ErrorAction SilentlyContinue | Measure-Object).Count -gt 0)
if ($hasEvidence) {
    New-Item -ItemType Directory -Force -Path (Split-Path $evidenceDir -Parent) | Out-Null
    Copy-Item -Path $outputDir -Destination $evidenceDir -Recurse -Force
    Write-Step "Evidence copied to $evidenceDir"
} else {
    Write-Warning "output\ is empty or missing -- nothing to copy to evidence\, nothing to judge."
}

# <gate-a-download-only-lane-review-2> MAJOR (b): New-DownloadOnlyPayload
# writes its hard-link/copy-fallback summary HOST-SIDE, next to the payload
# directory (sandbox-lab\DOWNLOAD-ONLY-PAYLOAD.txt) -- it is not under
# output\, so the copy above never picks it up. Copy it into this run's
# evidence directory explicitly so the fallback numbers travel with every
# other piece of evidence rather than only existing transiently on the
# runner's own disk (where the try/finally cleanup below never removes it,
# but a future run's New-DownloadOnlyPayload call overwrites it).
if ($DownloadOnlyLane -and $hasEvidence) {
    $downloadOnlyPayloadSummary = Join-Path $Root 'DOWNLOAD-ONLY-PAYLOAD.txt'
    if (Test-Path -LiteralPath $downloadOnlyPayloadSummary) {
        Copy-Item -LiteralPath $downloadOnlyPayloadSummary -Destination (Join-Path $evidenceDir 'DOWNLOAD-ONLY-PAYLOAD.txt') -Force
        Write-Step "DownloadOnlyLane: copied payload build summary into evidence ($evidenceDir\DOWNLOAD-ONLY-PAYLOAD.txt)"
    } else {
        Write-Warning "DownloadOnlyLane: no $downloadOnlyPayloadSummary found -- the payload build summary will be missing from this run's evidence."
    }
}

# --------------------------------------------------------------------------
# 6a. Host-launcher exit 3 = Windows Sandbox stayed busy (owned by another,
#     independent process on this shared box) for the entire
#     -SandboxWaitMinutes wait window, and the launcher backed off without
#     ever touching it. This is a harness-busy outcome, not a station-
#     acceptance FAIL -- write a BUSY verdict document (same shape family as
#     gate-a-verdict.json, so CI can find and read it the same way) and exit
#     2 (harness error), never 1 (product FAIL).
# --------------------------------------------------------------------------

if ($launcherExit -eq 3) {
    Write-Warning "[Run-GateA] Windows Sandbox stayed busy for the entire ${SandboxWaitMinutes}m wait window (owned by another process on this shared box, e.g. a rival build system) -- this is a harness-busy outcome, not a station-acceptance FAIL."

    # Mirror scripts/gate_a_verdict.py's own SANDBOX-BUSY.txt short-circuit
    # shape (including the "detail" field) so a BUSY verdict document looks
    # the same regardless of which of the two writers produced it.
    $busyDetail = "Host-Launch-Sandbox-Test.ps1 exited 3 (Windows Sandbox still busy after waiting ${SandboxWaitMinutes}m)"
    $busyDetailPath = Join-Path $outputDir 'SANDBOX-BUSY.txt'
    if (Test-Path $busyDetailPath) {
        $fileDetail = (Get-Content -Path $busyDetailPath -Raw).Trim()
        if ($fileDetail) { $busyDetail = $fileDetail }
    }

    $busyVerdict = [ordered]@{
        schema_version = 1
        source_sha     = $sourceSha
        run_id         = $resolvedRunId
        verdict        = "BUSY"
        reason         = "sandbox-busy-other-user"
        detail         = $busyDetail
        checks         = @{}
        station_up     = $null
        station_boot_seconds = $null
        station_first_healthy_utc = $null
        evidence_dir   = $(if ($hasEvidence) { $evidenceDir } else { $null })
        judged_utc     = (Get-Date).ToUniversalTime().ToString('o')
    }
    if ($hasEvidence) {
        $busyVerdictDir = $evidenceDir
    } else {
        $busyVerdictDir = $outputDir
    }
    if (-not (Test-Path $busyVerdictDir)) {
        New-Item -ItemType Directory -Force -Path $busyVerdictDir | Out-Null
    }
    $busyVerdictPath = Join-Path $busyVerdictDir 'gate-a-verdict.json'
    ($busyVerdict | ConvertTo-Json -Depth 8) | Set-Content -Path $busyVerdictPath -Encoding UTF8

    Write-Host ""
    Write-Host "=== GATE A VERDICT ===" -ForegroundColor Cyan
    Get-Content -Path $busyVerdictPath -Raw | Write-Host
    Write-Host "Evidence: $busyVerdictDir"
    Write-Host "[Run-GateA] BUSY -- Windows Sandbox occupied by another process; harness did not run" -ForegroundColor Yellow
    exit 2
}

if ($launcherExit -ne 0) {
    # Launcher exit codes: 1 = no VM after launch; 2 = timed out waiting for
    # DONE.json; 3 = sandbox stayed busy, handled above; 4 = the mapped
    # output folder went quiet while our own VM was alive (see
    # Host-Launch-Sandbox-Test.ps1's quiet-share detector, which also drops
    # HOST-QUIET-SHARE.txt into output\ so the judge can name the cause).
    # Every one of them is a harness error, not a station-acceptance FAIL --
    # exit 2 regardless of what the judge says about the partial evidence.
    # Still worth judging that evidence for forensics.
    if ($launcherExit -eq 4) {
        Write-Warning "[Run-GateA] The sandbox's mapped output folder went quiet while the VM was alive -- broken evidence channel, no product conclusion available. See HOST-QUIET-SHARE.txt in the evidence directory."
    }
    if ($hasEvidence) {
        $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
        if ($uvCmd) {
            $forensicRepoRoot = Split-Path $Root -Parent
            $forensicJudgePath = Join-Path $forensicRepoRoot 'scripts\gate_a_verdict.py'
            Write-Step "Running the verdict judge on partial evidence for forensics (harness did not complete cleanly)..."
            $forensicLane = $judgeLaneName
            & uv run --project $forensicRepoRoot python $forensicJudgePath $evidenceDir --source-sha $sourceSha --run-id "$resolvedRunId" --lane $forensicLane --out (Join-Path $evidenceDir 'gate-a-verdict.json') 2>&1 | Write-Host
        }
    }
    Exit-HarnessError "Host-Launch-Sandbox-Test.ps1 did not complete cleanly (exit $launcherExit) -- see $evidenceDir"
}

if (-not $hasEvidence) {
    Exit-HarnessError "Host launcher reported success but output\ is empty -- cannot judge"
}

$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uvCmd) {
    Exit-HarnessError "uv not found on PATH -- required to run scripts/gate_a_verdict.py"
}

$repoRoot = Split-Path $Root -Parent
$judgePath = Join-Path $repoRoot 'scripts\gate_a_verdict.py'
if (-not (Test-Path $judgePath)) {
    Exit-HarnessError "Judge module not found at $judgePath"
}

$verdictPath = Join-Path $evidenceDir 'gate-a-verdict.json'
$judgeLane = $judgeLaneName
Write-Step "Judging $evidenceDir (lane=$judgeLane)..."
& uv run --project $repoRoot python $judgePath $evidenceDir --source-sha $sourceSha --run-id "$resolvedRunId" --lane $judgeLane --out $verdictPath
$judgeExit = $LASTEXITCODE

# --------------------------------------------------------------------------
# 8. Print the verdict and exit with the judge's own code.
# --------------------------------------------------------------------------

if (Test-Path $verdictPath) {
    Write-Host ""
    Write-Host "=== GATE A VERDICT ===" -ForegroundColor Cyan
    Get-Content -Path $verdictPath -Raw | Write-Host
    Write-Host "Evidence: $evidenceDir"
}

if ($judgeExit -eq 0) {
    Write-Host "[Run-GateA] PASS" -ForegroundColor Green
} elseif ($judgeExit -eq 1) {
    Write-Host "[Run-GateA] FAIL" -ForegroundColor Red
} elseif ($judgeExit -eq 2) {
    # One of the judge's two non-verdicts: HARNESS_ERROR (HOST-QUIET-SHARE.txt
    # -- the evidence channel broke mid-run) or BUSY (SANDBOX-BUSY.txt -- the
    # run never started; normally already handled by the launcherExit -eq 3
    # branch above, so reaching it here means the marker was found on
    # otherwise-clean evidence). Either way NO product conclusion can be
    # drawn. Deliberately not printed as FAIL: calling a broken harness a
    # station-acceptance failure is precisely the authored-truth failure
    # mode Gate A exists to eliminate, in the opposite direction.
    Write-Warning "[Run-GateA] HARNESS ERROR -- see gate-a-verdict.json's harness_error field; this is NOT a station-acceptance FAIL"
    exit 2
} else {
    Write-Warning "[Run-GateA] Judge exited with unexpected code $judgeExit (treated as harness error)"
    exit 2
}

exit $judgeExit

} finally {
    # <gate-a-download-only-lane-review> BLOCKER 1: runs on EVERY exit path
    # out of the try block above -- normal completion, every `exit` call
    # inside it (PowerShell unwinds through `finally` before a script-scope
    # `exit` actually terminates the process), and any thrown/unhandled
    # error. Idempotent and never throws itself (both functions catch and
    # warn internally) -- cleanup failing must never mask the real verdict
    # this script already printed above.
    if ($DownloadOnlyLane) {
        Restore-DownloadOnlyKitDownload -KitDownloadPath $kitDownload -KitPhysicalDir $kitPhysicalDir
        Remove-DownloadOnlyPayload -PayloadDir $filteredPayload
    }
}
