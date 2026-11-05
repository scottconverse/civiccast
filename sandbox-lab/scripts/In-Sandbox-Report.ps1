# In-Sandbox-Report.ps1
# Runs INSIDE Windows Sandbox via the .wsb LogonCommand.
# Drives a silent install of the published CivicCast Native beta.1 installer,
# then collects a clean-box baseline: exit code, install tree, station-set.json,
# activation-self-test.json, CivicCastSupervisor service state, installer logs,
# and Event Log errors during the run window.
#
# Evidence path (changed by <gate-a-mapped-folder-stalls>, see the long note
# above the shipper below): every write lands in the LOCAL C:\CivicCastLocalOut
# and a separate shipper process mirrors that directory into the host-visible
# mapped folder C:\CivicCastOutput every few seconds, DONE.json last. The host
# launcher still polls C:\CivicCastOutput\DONE.json exactly as before -- the
# contract with the host is unchanged, only which process writes it is.
#
# HARDENED 2026-08-17 after a run that got all the way through a clean
# postinstall (verified via install-progress.log) but then hung for 6+
# minutes past _AFTER_INSTALL.marker with no summary.json ever written --
# the previous version did two full-tree `Get-ChildItem -Recurse` scans
# (once each for station-set.json / activation-self-test.json) over a
# 10,629-file / 5.4 GB install tree, which is exactly the kind of thing that
# is fast on a real disk and can take many minutes on Windows Sandbox's
# virtualized/differencing storage. Fixes in this version:
#   (a) summary.json is now written INCREMENTALLY after every numbered step
#       below (Save-Summary), so a hang anywhere after step N still leaves
#       steps 1..N's results on disk for the host to read.
#   (b) the two full-tree recursive searches are replaced with TARGETED,
#       bounded path checks (exact known locations + one shallow,
#       non-recursive listing of app\* subfolders) -- see Test-KnownPaths.
#   (c) the CivicCastSupervisor service check remains non-blocking
#       (Get-Service / sc.exe query / sc.exe qc, none of which wait), and a
#       Start-Service attempt (new) is wrapped in a bounded poll (max 60s)
#       instead of any unbounded wait.
#   (d) a DONE.json marker (not just the old DONE.marker text file) is
#       written as the LAST statement in the script -- Host-Launch-
#       Sandbox-Test.ps1 now polls for DONE.json specifically, so a stray
#       earlier marker (e.g. _AFTER_INSTALL.marker) can never be mistaken
#       for real completion again.

# HARDENED <gate-a-station-up-wait-and-log-capture>: bounded script-level
# watchdog. -MaxScriptMinutes defaults to 150 (LogonCommand invokes this
# script with no arguments, so the default always applies in production;
# it exists as a parameter purely so a developer/dry-run can shorten it).
# Deliberately NOT implemented with Start-Job -- service.py's own history
# (see comment near the Get-WinEvent call further down) documents a real
# System.OutOfMemoryException hit loading PSWorkflow/PSScheduledJob module
# type data the moment Start-Job was invoked under this VM's memory
# pressure, so the watchdog is a genuinely separate powershell.exe process
# (Start-Process) instead -- it never touches the job-scheduling subsystem.
#
# HARDENED <gate-a-mapped-folder-stalls>: the main script no longer writes
# ANYTHING to the Windows Sandbox mapped folder. Three independent Gate A
# runs stalled forever on a synchronous write to C:\CivicCastOutput -- the
# host-mapped (VSMB) share -- each at a different statement, each with the
# VM alive and every other process still healthy:
#
#   run3 (8579e66) stalled BETWEEN two consecutive ~30-byte Add-Content
#     appends to the same already-created mapped file (T3T5-RESULT.txt has
#     4 of its 9 expected lines; the 5th append never returned).
#   run4 (8579e66) and run6 (f31618f) both stalled in the 4-statement
#     window between Save-Summary 'station-diag-captured-after-t3t5' and
#     Save-Summary 'install-progress-log-copied' -- a Copy-Item onto an
#     existing mapped file plus a bounded read.
#   run6 is the decisive one: 42 minutes into the stall, the SEPARATE
#     watchdog powershell.exe successfully created two brand-new files in
#     the very same mapped folder. The share was NOT dead. What was dead
#     was this process's own in-flight I/O against it.
#
# So the failure mode is not "sustained I/O kills the share" (a 30-byte
# append wedged run3) and not "the share dies" (run6 disproves it). It is:
# a synchronous, uncancellable, timeout-less file operation issued by THIS
# process against a share this process does not control can wedge that
# thread permanently -- and this script runs the entire gate on one thread.
#
# The architectural answer is to take the share off the critical path
# entirely:
#   * $OutDir is now a LOCAL directory the VM fully owns. Every existing
#     $OutDir reference in this file -- the transcript, summary.json, every
#     T*-RESULT file, every station-diag capture, every redirected child
#     stdout/stderr, DONE.json -- writes there and can no longer wedge.
#   * $ShipDir is the mapped folder. A separate, disposable shipper
#     process mirrors $OutDir into it on a fixed tick; each tick is its own
#     short-lived child process, so a wedged tick costs one tick, never the
#     run (exactly the property run6's watchdog demonstrated).
#   * The watchdog reads and writes $OutDir too, so it can no longer be
#     blocked by the same surface it exists to bound.
#   * Host-Launch-Sandbox-Test.ps1 owns the last line of defence: if the
#     mapped folder goes quiet while the VM is alive, it declares a harness
#     error with its own marker rather than waiting out the full timeout.
param(
    [int]$MaxScriptMinutes = 150,
    [int]$ShipIntervalSeconds = 25,
    # Shipper tick interval WHILE the installer is moving tens of GB across
    # the other mapped folders -- see the QUIESCE note above the shipper.
    # Must stay well under Host-Launch-Sandbox-Test.ps1's -QuietShareMinutes
    # (15) or a healthy quiesced run would look like a dead channel.
    [int]$ShipQuiesceIntervalSeconds = 300
)

$ErrorActionPreference = 'Continue'

# The host-mapped (VSMB) folder. NOTHING on this script's own execution
# path may touch it -- only the shipper child processes below.
$ShipDir = 'C:\CivicCastOutput'
# The local evidence directory every step of this run actually writes to.
$OutDir = 'C:\CivicCastLocalOut'
$PayloadDir = 'C:\CivicCastPayload'
$LocalInstallStage = 'C:\CivicCastInstall'
$RunStart = Get-Date

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
# $ShipDir is deliberately NOT created here. Windows Sandbox creates the
# MappedFolder mount before the LogonCommand runs, and if it somehow did not
# exist, robocopy creates its own destination -- so there is no reason to
# spend an unbounded share call on the main thread to find that out.

# Bounded external-process runner. Used for every operation that has to
# touch $ShipDir from this script (there are exactly two: the one-time
# inbound seed below, and the final flush in the `finally` block). The
# child is killed and reported rather than waited on forever, so even
# these two can never reproduce the stall this change exists to fix.
function Invoke-BoundedProcess {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [int]$TimeoutSeconds = 60
    )
    $result = [ordered]@{ started = $false; completed = $false; exit_code = $null; error = $null }
    try {
        $p = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -PassThru -WindowStyle Hidden -ErrorAction Stop
        $result.started = $true
        try {
            Wait-Process -Id $p.Id -Timeout $TimeoutSeconds -ErrorAction Stop
            $result.completed = $true
            try { $result.exit_code = $p.ExitCode } catch {}
        } catch {
            $result.error = "timed out after ${TimeoutSeconds}s -- killing pid $($p.Id)"
            try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
        }
    } catch {
        $result.error = "$_"
    }
    return $result
}

# Seed the LOCAL dir from the mapped folder ONCE, at entry, while the share
# is known-good (the LogonCommand itself was just read across it). This is
# how host-provided inputs -- SOAK_MINUTES.txt (written by
# Host-Launch-Sandbox-Test.ps1) and the optional SKIP_MODE.txt -- reach the
# reads further down, which now all resolve against $OutDir. Bounded: on
# timeout the run continues with this script's own defaults rather than
# hanging at statement one.
$script:SeedResult = Invoke-BoundedProcess -FilePath 'robocopy.exe' -ArgumentList @(
    $ShipDir, $OutDir, '/E', '/R:0', '/W:0', '/NFL', '/NDL', '/NJH', '/NJS', '/NP'
) -TimeoutSeconds 60

Start-Transcript -Path (Join-Path $OutDir 'sandbox-transcript.log') -Force | Out-Null

function Write-Marker {
    param([string]$Name, [string]$Content = '')
    Set-Content -Path (Join-Path $OutDir $Name) -Value $Content -Encoding UTF8
}

Write-Marker -Name '_STARTED.marker' -Content "Started $RunStart"
# The driver's own PID <gate-a-hoststore-wedge>. Four Gate A runs have now
# ended with "last step written, nothing after, other processes healthy", and
# that signature is IDENTICAL whether the driver's thread blocked or the
# driver's PROCESS died (an unhandled OutOfMemoryException in this VM is not
# hypothetical -- see the Start-Job/PSWorkflow note further down). None of the
# four post-mortems can tell those apart, which means none of them can pick a
# fix with confidence. The watchdog checks this PID when it fires and records
# the answer, so the next occurrence resolves it in one line.
Write-Marker -Name '_DRIVER-PID.txt' -Content "driver_pid=$PID started_utc=$($RunStart.ToUniversalTime().ToString('o'))"
Write-Marker -Name '_LOCALOUT.marker' -Content "local_out_dir=$OutDir ship_dir=$ShipDir seed_completed=$($script:SeedResult.completed) seed_error=$($script:SeedResult.error)"

# DIRTY-BOX LANE <gate-a-dirty-lane>. DIRTY_MODE.txt is a host-provided input
# (written by Host-Launch-Sandbox-Test.ps1 -DirtyMode into the mapped output
# folder, seeded into $OutDir by the robocopy above -- the exact same channel
# SOAK_MINUTES.txt and SKIP_MODE.txt already ride). When present, this run
# executes a REMNANT prologue before the normal acceptance flow: install the
# candidate once, plant operator data (uploads), uninstall it for real via its
# own uninstaller, verify the uninstaller's preservation contract
# (nsis-hooks-bootstrap.nsh: "$COMMONPROGRAMDATA\CivicCast ... is preserved
# across uninstall by design"), optionally seed an orphaned captions-large-v3
# component, and only THEN fall through into the unchanged full acceptance
# flow -- which therefore runs against a machine shaped like every real
# customer upgrade box (leftover pgdata, uploads, logs, journals) instead of
# the pristine sandbox that has let remnant-only failures ship for weeks
# (field instance: DESKTOP-2BR3SJR 2026-08-30, #18 supervisor crash-loop on a
# preserved-but-receiptless caption tier -- PR #80).
#
# The mode flag must be resolved HERE, before the shipper and watchdog are
# spawned below: a dirty run performs TWO full install cycles, so its overall
# bound is raised to 210 minutes (contract: 210 in-sandbox < the host's 230
# poll -- the dirty-lane analogue of the clean lane's 150 < 170 ordering,
# asserted by tests/gate_a/test_gate_a_harness_contract.py). The clean lane's
# numbers are untouched: without DIRTY_MODE.txt nothing in this block changes
# any value.
$script:DirtyMode = Test-Path (Join-Path $OutDir 'DIRTY_MODE.txt')
$script:UpgradeMode = Test-Path (Join-Path $OutDir 'UPGRADE_MODE.txt')
if ($script:UpgradeMode -and -not $script:DirtyMode) {
    Write-Marker -Name '_INVALID-UPGRADE-MODE.marker' -Content 'UPGRADE_MODE.txt requires DIRTY_MODE.txt'
    throw 'UPGRADE_MODE.txt requires DIRTY_MODE.txt'
}
if ($script:DirtyMode) {
    if ($MaxScriptMinutes -lt 210) { $MaxScriptMinutes = 210 }
    Write-Marker -Name '_DIRTY-MODE.marker' -Content "dirty_mode=1 max_script_minutes=$MaxScriptMinutes detected_utc=$((Get-Date).ToUniversalTime().ToString('o'))"
}

# DOWNLOAD-ONLY LANE <gate-a-download-only-lane>. DOWNLOAD_ONLY_MODE.txt is
# the same host-to-guest input channel as DIRTY_MODE.txt/UPGRADE_MODE.txt
# (Host-Launch-Sandbox-Test.ps1 -DownloadOnlyMode). It rides the existing
# dirty-lane cross-version prologue unchanged -- phase 1 installs the pinned
# previous candidate from its full kit (C:\CivicCastPreviousPayload, which
# DOES carry station\) exactly as UPGRADE_MODE=1 already does -- so it
# requires both DIRTY_MODE.txt and UPGRADE_MODE.txt to be present too. What
# is unique to this lane is phase 2: Run-GateA.ps1 -DownloadOnlyLane points
# C:\CivicCastPayload (the normal install flow's $PayloadDir, read below) at
# a FILTERED payload directory containing only setup.exe and packs\ -- never
# station\ -- so the current candidate's install must activate the station by
# reusing an already-activated station's cached model packs instead of
# reading a fresh station bundle. See docs/ops/gate-a.md, "Download-only
# lane" for the field failure this exists to catch: since the K1 fix,
# d4-activate-station required a station\ folder beside setup.exe and
# aborted otherwise, so a download-only install/upgrade silently stopped
# working and no existing Gate A lane could catch it (every other lane
# installs from the full kit).
$script:DownloadOnlyMode = Test-Path (Join-Path $OutDir 'DOWNLOAD_ONLY_MODE.txt')
if ($script:DownloadOnlyMode -and -not ($script:DirtyMode -and $script:UpgradeMode)) {
    Write-Marker -Name '_INVALID-DOWNLOAD-ONLY-MODE.marker' -Content 'DOWNLOAD_ONLY_MODE.txt requires DIRTY_MODE.txt and UPGRADE_MODE.txt'
    throw 'DOWNLOAD_ONLY_MODE.txt requires DIRTY_MODE.txt and UPGRADE_MODE.txt'
}
if ($script:DownloadOnlyMode) {
    Write-Marker -Name '_DOWNLOAD-ONLY-MODE.marker' -Content "download_only_mode=1 detected_utc=$((Get-Date).ToUniversalTime().ToString('o'))"
}

# Quiesce control <gate-a-run7-findings>. Raised around the installer so the
# shipper stops competing with it for the shared VSMB transport; see the
# QUIESCE note above the shipper for the measured cost of not doing this.
# Both helpers are LOCAL-only writes and neither can throw past its boundary
# -- failing to quiesce must never be able to fail a run.
function Enter-ShipperQuiesce {
    param([string]$Reason, [int]$MaxMinutes = 90)
    try {
        $until = (Get-Date).ToUniversalTime().AddMinutes([Math]::Max(1, $MaxMinutes)).ToString('o')
        Write-Marker -Name '_SHIPPER-QUIESCE.marker' -Content "quiesce_until_utc=$until reason=$Reason raised_utc=$((Get-Date).ToUniversalTime().ToString('o'))"
    } catch {}
}
function Exit-ShipperQuiesce {
    try {
        $p = Join-Path $OutDir '_SHIPPER-QUIESCE.marker'
        if (Test-Path $p) { Remove-Item -Path $p -Force -ErrorAction SilentlyContinue }
    } catch {}
}

# Transcript flushing <gate-a-run7-findings>. Windows PowerShell 5.1's
# transcript writer buffers in user space and does NOT flush as it goes:
# measured on this host, a child that logged 100+ caught terminating errors
# still had a 689-byte header-only transcript on disk, and it was STILL
# 689 bytes after the process was killed without reaching Stop-Transcript.
# That is exactly run7's 686-byte transcript. Every Gate A run that ends via
# the watchdog (which force-completes while the main script is still
# running, after which the host tears the VM down) therefore loses its
# entire transcript body. Stop/Start -Append at a few checkpoints forces the
# buffer out without the cost of flushing on every write.
function Sync-Transcript {
    param([string]$Checkpoint)
    try {
        Stop-Transcript -ErrorAction SilentlyContinue | Out-Null
        Start-Transcript -Path (Join-Path $OutDir 'sandbox-transcript.log') -Append -ErrorAction SilentlyContinue | Out-Null
        Add-Content -Path (Join-Path $OutDir 'sandbox-transcript.log') -Value "# transcript flushed at checkpoint: $Checkpoint $((Get-Date).ToUniversalTime().ToString('o'))" -ErrorAction SilentlyContinue
    } catch {}
}

# --------------------------------------------------------------------------
# The SHIPPER: the only thing in this run that writes to the mapped folder.
#
# A supervisor process spawns ONE short-lived tick child every
# $ShipIntervalSeconds. Each tick is a fresh powershell.exe with fresh
# handles -- the exact shape that provably kept working in run6 while the
# main script's own I/O was wedged -- so a tick that hangs on the share
# costs that tick and nothing else.
#
# Guard: the supervisor SKIPS a tick while the previous child is still
# running, but only up to 3 intervals. Past that the child is presumed
# wedged on the share, force-killed, and replaced -- a strict
# skip-forever guard would let one wedged tick stop shipping for the rest
# of the run, which is the failure this whole change exists to remove.
#
# Each tick writes _SHIPPER-HEARTBEAT.txt locally BEFORE mirroring, so a
# healthy tick always advances a timestamp on the host side even when the
# run is inside a quiet stretch (the T5 soak only advances a step every
# 5 minutes). Host-Launch-Sandbox-Test.ps1's quiet-share detector reads
# exactly that liveness.
#
# QUIESCE <gate-a-run7-findings>. Every mapped folder in this VM --
# C:\CivicCastPayload (the ~21 GB kit the installer reads),
# C:\CivicCastHostStore (the install target it writes), and
# C:\CivicCastOutput (this shipper's destination) -- rides the same Windows
# Sandbox VSMB transport. Run7 measured what a 25-second tick costs the
# other two while the installer is moving tens of GB across them. Against
# three pre-shipper runs whose CPU/local-disk-bound steps are flat to the
# second (vc-redist 4m04/4m04/4m04 -> 4m05; d4-provision 25s/25s/28s ->
# 28s), every VSMB-crossing installer step in run7 slowed down:
#
#   stage-packs                6m39 / 6m47 / 7m21  ->  11m26   (1.6x)
#   d2-verify-server-binaries     6s /    5s /  5s  ->     21s   (4.2x)
#   d2-verify-app-payload      1m09 / 1m14 / 1m19  ->   3m16   (2.5x)
#   d4-activate-station       14m13 /14m37 /15m44  ->  35m09   (2.2x, then FAILED)
#
# So while the installer runs, the shipper drops to $QuiesceIntervalSeconds
# (default 300). The driver writes _SHIPPER-QUIESCE.marker before launching
# the installer and removes it after. The marker carries its own expiry so a
# lost removal degrades to "back to the fast tick", never to "shipping
# silently stopped for the rest of the run". 300s still sits far inside
# Host-Launch-Sandbox-Test.ps1's 15-minute quiet-share bound, so liveness is
# preserved throughout -- and the install phase produces almost no evidence
# to ship anyway (markers and INSTALL-RESULT.txt, nothing more).
#
# The mirror is ADDITIVE (robocopy /E, never /MIR): the host owns files in
# that folder too (.gitkeep, _HOST_LAUNCHED.marker, SOAK_MINUTES.txt, and
# the launcher's own HOST-QUIET-SHARE.txt), and a purge would delete them.
# The one retraction the harness genuinely needs -- a watchdog timeout file
# that a genuine completion later supersedes -- is handled by an explicit,
# named retraction list instead.
# --------------------------------------------------------------------------
$script:ShipTickPath = Join-Path $env:TEMP 'civiccast-gate-a-ship-tick.ps1'
try {
    $shipTickScript = @'
param([string]$LocalDir, [string]$ShipDir)

# Liveness first: this file is what tells the host the guest is still
# shipping even when no evidence file changed this tick.
try {
    "shipper_tick_utc=$((Get-Date).ToUniversalTime().ToString('o')) local_dir=$LocalDir" |
        Set-Content -Path (Join-Path $LocalDir '_SHIPPER-HEARTBEAT.txt') -Encoding UTF8
} catch {}

# Additive mirror, DONE.json deliberately EXCLUDED. /R:0 /W:0 -- never
# retry, never wait; a failed tick is retried by the NEXT tick's fresh
# process, not by this one.
#
# The exclusion preserves the harness's oldest contract across the new
# channel: DONE.json is the LAST thing to appear, so its presence on the
# host means everything else already arrived. Host-Launch-Sandbox-Test.ps1
# polls for it every 10s and tears the VM down the moment it sees it -- and
# robocopy does not copy in write order, so without this it could hand the
# host a DONE.json mid-tick, before the evidence files that sort after it.
# That is the same shape as the documented Watch-Run.ps1 race that cost the
# Aug-19 reference run its own DONE.json, just moved one layer down.
& robocopy.exe $LocalDir $ShipDir /E /XF DONE.json /R:0 /W:0 /NFL /NDL /NJH /NJS /NP | Out-Null

# Explicit retraction list: names the harness itself withdraws when a run
# genuinely completes after a watchdog already fired. Anything not on this
# list is never deleted from the mapped folder.
# _SHIPPER-QUIESCE.marker joins the list <gate-a-hoststore-wedge>: the mirror
# is additive, so a marker shipped during the quiesce window stayed on the
# host after the driver removed it locally, and the preserved evidence for
# the run that prompted this change shows one -- telling a reader the run was
# still quiesced when it was not. Evidence that misreports harness state is
# worse than absent evidence.
foreach ($name in @('WATCHDOG-TIMEOUT.txt', 'STALL-TIMEOUT.txt', '_SHIPPER-QUIESCE.marker')) {
    try {
        $localCopy = Join-Path $LocalDir $name
        $shipCopy = Join-Path $ShipDir $name
        if ((-not (Test-Path $localCopy)) -and (Test-Path $shipCopy)) {
            Remove-Item -Path $shipCopy -Force -ErrorAction SilentlyContinue
        }
    } catch {}
}

# DONE.json goes across LAST, on its own, after every other file in this
# tick has landed -- see the /XF note above.
try {
    $localDone = Join-Path $LocalDir 'DONE.json'
    if (Test-Path $localDone) {
        Copy-Item -LiteralPath $localDone -Destination (Join-Path $ShipDir 'DONE.json') -Force -ErrorAction SilentlyContinue
    }
} catch {}

# Local receipt, written LAST: proof to the supervisor that a full tick --
# robocopy included -- returned. The supervisor uses this instead of probing
# the share itself, which would reintroduce the very unbounded call this
# whole design removes. A tick that wedged on the share never gets here, so
# its receipt timestamp never advances.
try {
    "shipper_tick_completed_utc=$((Get-Date).ToUniversalTime().ToString('o'))" |
        Set-Content -Path (Join-Path $LocalDir '_SHIPPER-LASTOK.txt') -Encoding UTF8
} catch {}
'@
    Set-Content -Path $script:ShipTickPath -Value $shipTickScript -Encoding UTF8

    $shipperScript = @'
param([string]$LocalDir, [string]$ShipDir, [string]$TickScript, [int]$IntervalSeconds, [int]$MaxMinutes, [int]$QuiesceIntervalSeconds = 300)

$donePath = Join-Path $LocalDir 'DONE.json'
$quiescePath = Join-Path $LocalDir '_SHIPPER-QUIESCE.marker'

function Get-EffectiveInterval {
    param([string]$QuiescePath, [int]$Fast, [int]$Slow)
    # Quiesce ONLY while the marker exists AND its own stated expiry is still
    # in the future. A marker the driver failed to remove (crash, wedge, kill)
    # therefore stops mattering on its own -- the failure mode is "shipping
    # speeds back up", never "shipping stays throttled for the rest of the run".
    try {
        if (-not (Test-Path $QuiescePath)) { return $Fast }
        $raw = Get-Content -Path $QuiescePath -Raw -Encoding UTF8 -ErrorAction Stop
        $m = [regex]::Match($raw, 'quiesce_until_utc=(\S+)')
        if (-not $m.Success) { return $Fast }
        $until = [datetime]::Parse($m.Groups[1].Value, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind)
        if ((Get-Date).ToUniversalTime() -lt $until.ToUniversalTime()) { return $Slow }
    } catch {}
    return $Fast
}
# Written LOCALLY by each tick child once its robocopy has returned. The
# supervisor reads only local paths -- it never touches $ShipDir itself.
$receiptPath = Join-Path $LocalDir '_SHIPPER-LASTOK.txt'
# Outlive the watchdog: the shipper must still be running to carry the
# watchdog's own placeholder DONE.json out to the host.
$deadline = (Get-Date).AddSeconds([Math]::Max(120, ($MaxMinutes + 10) * 60))
$child = $null
$childStart = $null
$ticksSinceDone = 0

function Start-Tick {
    param([string]$LocalDir, [string]$ShipDir, [string]$TickScript)
    return (Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$TickScript`"",
        '-LocalDir', "`"$LocalDir`"", '-ShipDir', "`"$ShipDir`""
    ) -PassThru -WindowStyle Hidden)
}

while ((Get-Date) -lt $deadline) {
    # Re-read every loop: the driver raises and clears the quiesce marker
    # while this supervisor is running.
    $effectiveInterval = Get-EffectiveInterval -QuiescePath $quiescePath -Fast $IntervalSeconds -Slow $QuiesceIntervalSeconds

    $skip = $false
    if ($child -ne $null) {
        $running = $false
        try { $running = -not $child.HasExited } catch { $running = $false }
        if ($running) {
            $ageSeconds = ((Get-Date) - $childStart).TotalSeconds
            # Stale-child bound stays keyed to the FAST interval even while
            # quiesced: a wedged tick must be replaced on the same schedule
            # regardless of how often new ticks are being started.
            if ($ageSeconds -ge ($IntervalSeconds * 3)) {
                # Presumed wedged on the share. Kill it and replace it --
                # a fresh process gets fresh handles.
                try { Stop-Process -Id $child.Id -Force -ErrorAction SilentlyContinue } catch {}
                $child = $null
            } else {
                $skip = $true
            }
        }
    }

    if (-not $skip) {
        try {
            $child = Start-Tick -LocalDir $LocalDir -ShipDir $ShipDir -TickScript $TickScript
            $childStart = Get-Date
        } catch {
            $child = $null
        }
    }

    # Stop once a tick has demonstrably COMPLETED after DONE.json appeared --
    # i.e. a robocopy that necessarily included it returned. This is read
    # from the tick's own local receipt, never by probing $ShipDir: a
    # Test-Path against the share from THIS process is exactly the kind of
    # unbounded call the supervisor exists to avoid making.
    #
    # The iteration cap is the backstop for a share that stays wedged: keep
    # spawning fresh ticks for a while, then stop and let
    # Host-Launch-Sandbox-Test.ps1's quiet-share detector be the thing that
    # declares the harness error.
    if (Test-Path $donePath) {
        $ticksSinceDone++
        $delivered = $false
        try {
            if (Test-Path $receiptPath) {
                $delivered = (Get-Item $receiptPath).LastWriteTimeUtc -gt (Get-Item $donePath).LastWriteTimeUtc
            }
        } catch { $delivered = $false }
        if ($delivered -or $ticksSinceDone -ge 8) { break }
    }

    # Sleep the FAST interval regardless, but only start a tick once the
    # effective interval has elapsed. Polling the quiesce marker on the fast
    # cadence is what lets the driver un-quiesce promptly the moment the
    # installer returns, instead of the shipper staying slow for up to
    # another full 5 minutes.
    Start-Sleep -Seconds $IntervalSeconds
    if ($effectiveInterval -gt $IntervalSeconds) {
        $waited = $IntervalSeconds
        while ($waited -lt $effectiveInterval) {
            if ((Get-EffectiveInterval -QuiescePath $quiescePath -Fast $IntervalSeconds -Slow $QuiesceIntervalSeconds) -le $IntervalSeconds) { break }
            if (Test-Path $donePath) { break }
            Start-Sleep -Seconds $IntervalSeconds
            $waited += $IntervalSeconds
        }
    }
}
'@
    $shipperPath = Join-Path $env:TEMP 'civiccast-gate-a-shipper.ps1'
    Set-Content -Path $shipperPath -Value $shipperScript -Encoding UTF8
    Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$shipperPath`"",
        '-LocalDir', "`"$OutDir`"", '-ShipDir', "`"$ShipDir`"",
        '-TickScript', "`"$($script:ShipTickPath)`"",
        '-IntervalSeconds', $ShipIntervalSeconds, '-MaxMinutes', $MaxScriptMinutes,
        '-QuiesceIntervalSeconds', $ShipQuiesceIntervalSeconds
    ) -WindowStyle Hidden | Out-Null
    Write-Marker -Name '_SHIPPER_SPAWNED.marker' -Content "ship_dir=$ShipDir interval_seconds=$ShipIntervalSeconds quiesce_interval_seconds=$ShipQuiesceIntervalSeconds started_utc=$((Get-Date).ToUniversalTime().ToString('o'))"
} catch {
    Add-Content -Path (Join-Path $OutDir 'summary-write-errors.log') -Value "shipper spawn failed (FATAL to evidence delivery, but the run continues so the local evidence still exists in the VM): $_"
}

# Spawn the watchdog now, as early as possible, so it bounds the ENTIRE run
# (installer through T5 soak) -- not just the parts of the script written
# after it. Two independent triggers, both handled by this ONE separate
# process (still not Start-Job -- see below):
#   (1) OVERALL bound: if DONE.json still does not exist $MaxScriptMinutes
#       minutes from now, write WATCHDOG-TIMEOUT.txt + a placeholder
#       DONE.json so Host-Launch-Sandbox-Test.ps1's poll loop can never
#       wait on a zombie in-sandbox script forever.
#   (2) STALENESS bound, added after the 8579e66-run4 evidence: run #4
#       reached 'station-diag-captured-after-t3t5' at 11:52:11Z and then
#       never advanced -- 'install-progress-log-copied' (the very next
#       step, block 6 below) never arrived in 6+ minutes, with no
#       DONE.json, and a coarse whole-script deadline was far too blunt a
#       bound to catch that kind of late-stage stall promptly.
#
# ARMING FIX <gate-a-mapped-folder-stalls>. The first version of trigger
# (2) armed by string-matching the CURRENT value of
# summary.json.last_completed_step against three names ('runtime-check-*',
# 't3t5-skipped-station-down', 't5-soak-complete') while polling every 30s.
# On run6 that never armed, so only the coarse overall bound fired -- 47
# minutes late. The reason is a race that was guaranteed to lose, not bad
# luck: every one of those three names is a MOMENTARY value. Run6's
# 'runtime-check-*' steps occupied summary.json from 22:29:57 to 22:29:58
# (~1s -- RUNTIME-RESULT.txt records all three surfaces answering on poll
# #1), and 't5-soak-complete' was written at ~22:54:46 and superseded by
# 'station-diag-captured-after-t3t5' at 22:54:48 (~2s). Sampling a ~3s
# total window with a 30s poll misses it roughly 9 times in 10.
#
# Two structural fixes, both applied below:
#   * ARM ON A STICKY FILE, not a transient value. The main script writes
#     _VERDICT-STAGE.marker once, at the station-up verdict, and never
#     removes it. Test-Path cannot be raced by a coarse poller. The step-
#     name predicate is kept as a redundant second arming path (and
#     widened to every post-verdict step name), never as the only one.
#   * STALL ON A MONOTONIC COUNTER, not name equality. summary.json now
#     carries step_seq, incremented on every Save-Summary. Two different
#     steps can share a name; step_seq cannot repeat, so "progress
#     stopped" is now an unambiguous observation rather than an inference
#     from string equality.
#
# Both this watchdog's reads (summary.json) and its writes (STALL-TIMEOUT
# .txt, WATCHDOG-TIMEOUT.txt, DONE.json) are against the LOCAL $OutDir. In
# run6 this watchdog wrote successfully to the mapped folder while the main
# script was wedged on it, which is lucky, not designed: had its own
# summary.json read wedged instead, nothing at all would have fired.
#
# Deliberately NOT implemented with Start-Job -- service.py's own history
# (see comment near the Get-WinEvent call further down) documents a real
# System.OutOfMemoryException hit loading PSWorkflow/PSScheduledJob module
# type data the moment Start-Job was invoked under this VM's memory
# pressure, so the watchdog is a genuinely separate powershell.exe process
# (Start-Process) instead -- it never touches the job-scheduling subsystem.
try {
    $watchdogScript = @'
param([string]$OutDir, [int]$Minutes, [int]$StallMinutes = 8, [int]$DriverPid = 0)

function Get-DriverForensics {
    <#
      Called ONLY when the driver is still alive at the moment the watchdog
      fires <gate-a-summary-json-explosion>. "Alive and CPU-hot" is the answer
      the previous change's liveness line gave, and it was enough to find the
      ConvertTo-Json explosion -- but only because the suspect list was short.
      This narrows the next one without a debugger, using nothing that
      Windows PowerShell 5.1 lacks:

        1. A CPU DELTA over a fixed interval. One cumulative CPU number cannot
           distinguish "spinning right now" from "burned CPU earlier and is
           now blocked". Two samples can, and that is the single most useful
           bit for choosing where to look.
        2. A bounded MiniDump via rundll32 comsvcs.dll, written to $env:TEMP
           and NEVER to the shipped evidence directory -- a full dump of the
           8.3 GB process this was written for would be 8.3 GB. Only the path
           and size are recorded. Guarded by a working-set cap so it is
           skipped exactly when it would be ruinous, and by its own timeout.

      Returns a single line. Never throws.
    #>
    param([int]$DriverPid, [int]$SampleSeconds = 5, [int]$DumpMaxWorkingSetMb = 1536)
    $parts = New-Object System.Collections.Generic.List[string]
    try {
        $p1 = Get-Process -Id $DriverPid -ErrorAction Stop
        $cpu1 = $p1.TotalProcessorTime.TotalSeconds
        Start-Sleep -Seconds $SampleSeconds
        $p2 = Get-Process -Id $DriverPid -ErrorAction Stop
        $cpu2 = $p2.TotalProcessorTime.TotalSeconds
        $delta = [Math]::Round($cpu2 - $cpu1, 2)
        $busy = [Math]::Round(100.0 * ($cpu2 - $cpu1) / [Math]::Max(1, $SampleSeconds), 1)
        $wsMb = [Math]::Round($p2.WorkingSet64 / 1MB, 1)
        $parts.Add("driver_cpu_delta_seconds=$delta over_${SampleSeconds}s driver_busy_percent=$busy")
        $parts.Add("driver_verdict=$(if ($busy -ge 50) { 'SPINNING (compute-bound right now)' } else { 'NOT-SPINNING (idle or blocked right now)' })")

        if ($wsMb -gt $DumpMaxWorkingSetMb) {
            $parts.Add("driver_dump=skipped (working set ${wsMb}MB exceeds ${DumpMaxWorkingSetMb}MB cap -- a full dump would not fit)")
        } else {
            $dumpPath = Join-Path $env:TEMP ("civiccast-driver-$DriverPid-" + (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss') + '.dmp')
            $dp = Start-Process -FilePath 'rundll32.exe' `
                -ArgumentList @('C:\Windows\System32\comsvcs.dll,MiniDump', "$DriverPid", "`"$dumpPath`"", 'full') `
                -PassThru -WindowStyle Hidden -ErrorAction Stop
            try {
                Wait-Process -Id $dp.Id -Timeout 120 -ErrorAction Stop
                if (Test-Path $dumpPath) {
                    $dumpMb = [Math]::Round((Get-Item $dumpPath).Length / 1MB, 1)
                    $parts.Add("driver_dump=$dumpPath (${dumpMb}MB, NOT shipped -- read it inside the VM before teardown)")
                } else {
                    $parts.Add('driver_dump=failed (rundll32 completed but wrote no file -- likely a privilege refusal)')
                }
            } catch {
                try { Stop-Process -Id $dp.Id -Force -ErrorAction SilentlyContinue } catch {}
                $parts.Add('driver_dump=timed-out after 120s')
            }
        }
    } catch {
        $parts.Add("driver_forensics_failed=$($_.Exception.Message)")
    }
    return ($parts -join ' ')
}

function Get-DriverLiveness {
    # "The driver stopped advancing" has two very different causes that leave
    # an identical trail: its thread blocked, or its process died. This is the
    # one cheap observation that separates them, and it must be made AT THE
    # MOMENT the watchdog fires -- afterwards the VM is torn down and the
    # answer is gone forever. Returns a string, never throws.
    param([int]$DriverPid)
    if ($DriverPid -le 0) { return 'driver_pid_unknown' }
    try {
        $p = Get-Process -Id $DriverPid -ErrorAction Stop
        $cpu = $null
        try { $cpu = [Math]::Round($p.TotalProcessorTime.TotalSeconds, 1) } catch {}
        $ws = $null
        try { $ws = [Math]::Round($p.WorkingSet64 / 1MB, 1) } catch {}
        return "driver_process_alive=true driver_pid=$DriverPid driver_cpu_seconds=$cpu driver_working_set_mb=$ws"
    } catch {
        return "driver_process_alive=false driver_pid=$DriverPid (process is gone -- the driver DIED rather than blocked)"
    }
}

$donePath = Join-Path $OutDir 'DONE.json'
$summaryPath = Join-Path $OutDir 'summary.json'
$verdictStageMarker = Join-Path $OutDir '_VERDICT-STAGE.marker'
$deadline = (Get-Date).AddSeconds([Math]::Max(60, $Minutes * 60))
$pollIntervalSeconds = 30
$stallThresholdSeconds = [Math]::Max(60, $StallMinutes * 60)

$staleTrackingStarted = $false
$lastSeenProgress = $null
$lastSeenStep = $null
$lastChangeTime = Get-Date

# Redundant, widened second arming path. The sticky marker is the primary
# one; this exists so a marker that somehow failed to write still cannot
# leave the staleness bound disarmed for the whole run.
function Test-PostRuntimeVerdictStep {
    param([string]$Step)
    if (-not $Step) { return $false }
    if ($Step -like 'runtime-check-*') { return $true }
    if ($Step -like 't2-*') { return $true }
    if ($Step -like 't3*') { return $true }
    if ($Step -like 't4-*') { return $true }
    if ($Step -like 't5-*') { return $true }
    if ($Step -like 'station-diag-captured-*') { return $true }
    if ($Step -eq 'station-up-wait') { return $true }
    if ($Step -eq 'runtime-ui-captured') { return $true }
    if ($Step -like 'install-progress*') { return $true }
    if ($Step -like 'transcript-flushed*') { return $true }
    if ($Step -like 'event-log-*') { return $true }
    if ($Step -like 'final-diag-*') { return $true }
    if ($Step -eq 'finally-block') { return $true }
    return $false
}

while ((Get-Date) -lt $deadline) {
    if (Test-Path $donePath) { break }

    $step = $null
    $seq = $null
    if (Test-Path $summaryPath) {
        try {
            $raw = Get-Content -Path $summaryPath -Raw -Encoding UTF8 -ErrorAction Stop
            $obj = $raw | ConvertFrom-Json -ErrorAction Stop
            $step = $obj.last_completed_step
            $seq = $obj.step_seq
        } catch {
            $step = $null  # mid-write or transiently malformed -- try again next poll
            $seq = $null
        }
    }

    # Progress identity: step_seq when present (monotonic, cannot repeat),
    # the step name only as a fallback for an older summary shape.
    $progress = $null
    if ($seq -ne $null) { $progress = "seq:$seq" } elseif ($step) { $progress = "step:$step" }

    if ($progress) {
        if (-not $staleTrackingStarted) {
            if ((Test-Path $verdictStageMarker) -or (Test-PostRuntimeVerdictStep -Step $step)) {
                $staleTrackingStarted = $true
                $lastSeenProgress = $progress
                $lastSeenStep = $step
                $lastChangeTime = Get-Date
            }
        } elseif ($progress -ne $lastSeenProgress) {
            $lastSeenProgress = $progress
            $lastSeenStep = $step
            $lastChangeTime = Get-Date
        } else {
            $stalledSeconds = ((Get-Date) - $lastChangeTime).TotalSeconds
            if ($stalledSeconds -ge $stallThresholdSeconds -and -not (Test-Path $donePath)) {
                $ts = (Get-Date).ToUniversalTime().ToString('o')
                $stuckSinceIso = $lastChangeTime.ToUniversalTime().ToString('o')
                $liveness = Get-DriverLiveness -DriverPid $DriverPid
                # Only pay for forensics when the driver is ALIVE -- a dead
                # driver has nothing left to sample or dump.
                if ($liveness -like '*driver_process_alive=true*') {
                    $liveness = $liveness + ' ' + (Get-DriverForensics -DriverPid $DriverPid)
                }
                "stall_detected_utc=$ts stuck_step=$lastSeenStep stuck_progress=$lastSeenProgress stuck_since_utc=$stuckSinceIso stalled_seconds=$([Math]::Round($stalledSeconds, 1)) threshold_seconds=$stallThresholdSeconds $liveness" |
                    Set-Content -Path (Join-Path $OutDir 'STALL-TIMEOUT.txt') -Encoding UTF8
                if (-not (Test-Path $donePath)) {
                    $doneObj = [ordered]@{
                        done_utc             = $ts
                        last_completed_step  = $lastSeenStep
                        installer_exit_code  = $null
                        harness_completed    = $false
                        watchdog_timeout     = $false
                        stall_timeout        = $true
                        driver_liveness      = $liveness
                    }
                    ($doneObj | ConvertTo-Json -Depth 3) | Set-Content -Path $donePath -Encoding UTF8
                }
                break
            }
        }
    }

    Start-Sleep -Seconds $pollIntervalSeconds
}

if (-not (Test-Path $donePath)) {
    $ts = (Get-Date).ToUniversalTime().ToString('o')
    $liveness = Get-DriverLiveness -DriverPid $DriverPid
    if ($liveness -like '*driver_process_alive=true*') {
        $liveness = $liveness + ' ' + (Get-DriverForensics -DriverPid $DriverPid)
    }
    "watchdog_fired_utc=$ts max_script_minutes=$Minutes reason=DONE.json not present after the bounded deadline -- main script presumed hung or zombied $liveness" |
        Set-Content -Path (Join-Path $OutDir 'WATCHDOG-TIMEOUT.txt') -Encoding UTF8
    if (-not (Test-Path $donePath)) {
        $doneObj = [ordered]@{
            done_utc             = $ts
            last_completed_step  = 'watchdog-timeout'
            installer_exit_code  = $null
            harness_completed    = $false
            watchdog_timeout     = $true
            driver_liveness      = $liveness
        }
        ($doneObj | ConvertTo-Json -Depth 3) | Set-Content -Path $donePath -Encoding UTF8
    }
}
'@
    $watchdogPath = Join-Path $env:TEMP 'civiccast-gate-a-watchdog.ps1'
    Set-Content -Path $watchdogPath -Value $watchdogScript -Encoding UTF8
    Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$watchdogPath`"",
        '-OutDir', "`"$OutDir`"", '-Minutes', $MaxScriptMinutes, '-DriverPid', $PID
    ) -WindowStyle Hidden | Out-Null
    Write-Marker -Name '_WATCHDOG_SPAWNED.marker' -Content "MaxScriptMinutes=$MaxScriptMinutes stall_threshold_minutes=8 out_dir=$OutDir driver_pid=$PID started_utc=$((Get-Date).ToUniversalTime().ToString('o'))"
} catch {
    Add-Content -Path (Join-Path $OutDir 'summary-write-errors.log') -Value "watchdog spawn failed (non-fatal, continuing without a watchdog): $_"
}

$summary = [ordered]@{
    run_start_utc          = $RunStart.ToUniversalTime().ToString('o')
    sandbox_has_gpu        = $false
    networking              = 'disabled'
    installer_source        = $null
    installer_sha256        = $null
    silent_flag_used        = $null
    installer_exit_code     = $null
    installer_launch_error  = $null
    install_dir_found       = $null
    install_dir_candidates  = @()
    install_tree_top_levels = @()
    station_set_json_found  = @()
    activation_self_test_json_found = @()
    marker_search_method     = 'targeted (non-recursive) -- see HARDENED note at top of script'
    arp_entries              = @()
    service_get_service       = $null
    service_sc_query_raw      = $null
    service_sc_qc_raw         = $null
    service_start_attempt     = $null
    install_progress_log_found = $false
    install_progress_log_bytes = $null
    # <gate-a-audit-MA-23> the append-only log's line count at the instant
    # before this phase's setup.exe ran, and the number of lines it wrote.
    install_progress_phase_mark  = 0
    install_progress_phase_lines = $null
    install_progress_log_tail  = @()
    installer_nsis_log_found   = $false
    event_log_errors           = @()
    errors                     = @()
    last_completed_step        = 'init'
    # step_seq / step_utc <gate-a-mapped-folder-stalls>: a MONOTONIC progress
    # identity for the staleness watchdog. last_completed_step alone is a
    # name, and names can legitimately repeat; step_seq cannot, so "the run
    # stopped progressing" becomes an observation instead of an inference
    # from string equality. step_utc records when that step landed so a
    # post-mortem can measure the stall without cross-referencing file
    # mtimes.
    step_seq                   = 0
    step_utc                   = $null
    run_end_utc                = $null
    # harness_completed / top_level_error <gate-a-audit-BL-09>. These start
    # FALSE and are only promoted by the LAST statement of the main try block.
    # The previous shape set harness_completed=$true unconditionally in the
    # `finally`, including after the catch that swallows any throw from the
    # 1500-line try -- so a lane that crashed internally (e.g. "No *setup.exe
    # found in mapped payload") still shipped DONE.json.harness_completed=true
    # and the judge read it as a genuine completion. A crashed run must be
    # able to SAY it crashed; that is what top_level_error carries.
    harness_completed          = $false
    top_level_error            = $null
}

# --------------------------------------------------------------------------
# SUMMARY SERIALIZATION SAFETY <gate-a-summary-json-explosion>
#
# THE BUG THIS EXISTS FOR. Five Gate A runs (4, 6, 7, and both candidate-#11
# runs) stopped advancing at the first Save-Summary after
# install_progress_log_tail was assigned. The liveness instrument added in the
# previous change finally answered what was happening:
#
#   driver_process_alive=true driver_cpu_seconds=449.5 driver_working_set_mb=8318.2
#
# Alive, CPU-hot, 8.3 GB resident in a 16 GB VM. Not blocked I/O -- a
# serializer explosion.
#
# Get-Content does not emit plain strings. It emits strings DECORATED with
# NoteProperties: PSPath, PSParentPath, PSChildName, PSDrive, PSProvider,
# ReadCount. PSProvider is a ProviderInfo whose .Drives is a collection of
# PSDriveInfo, and each PSDriveInfo has a .Provider back-reference to that same
# ProviderInfo -- a cycle. ConvertTo-Json walks NoteProperties, so -Depth N
# walks that cycle N levels deep and expands combinatorially.
#
# Measured on this host, ONE Get-Content line inside a hashtable:
#
#   -Depth 3 ->      1,889 json chars
#   -Depth 4 ->     32,936
#   -Depth 5 ->    447,193
#   -Depth 6 ->  3,852,872
#   -Depth 7 -> 98,197,802  (11.2 seconds)
#   -Depth 8 -> never completed; killed at 180s having reached 4 GB / 178s CPU
#
# The driver serialized EIGHTY such lines at -Depth 8. 8.3 GB and 449.5s of
# CPU is exactly what that costs.
#
# The same 80 lines as PLAIN strings at the same -Depth 8: 5,314 chars, 30 ms.
#
# Two independent defences, because either alone would have been enough to
# prevent this and neither alone is enough to prevent the next one:
#   (1) Sanitize at the boundary -- ConvertTo-PlainForSummary below strips
#       PSObject decoration off everything before it is serialized, so a
#       decorated value can no longer reach ConvertTo-Json at all.
#   (2) Serialize at the depth the data actually needs (6), not 8. The
#       deepest real member is install_tree_top_levels: summary -> array ->
#       entry -> children -> string = 5. Depth is a blast-radius multiplier
#       for exactly this class of bug.
# --------------------------------------------------------------------------

#: What Save-Summary serializes at. See the note above -- this is a bound on
#: blast radius, not just a formatting preference.
$script:SummaryJsonDepth = 6

function ConvertTo-PlainForSummary {
    <#
      Return a value built only from plain types (string / number / bool /
      null / array / hashtable), with every PSObject adapted-member wrapper
      discarded. Terminates by construction: it recurses only into arrays and
      dictionaries, caps its own depth, and renders anything else via
      ToString() rather than walking its object graph -- which is precisely
      what ConvertTo-Json does NOT do, and why this exists.
    #>
    param($Value, [int]$Depth = 0)
    if ($null -eq $Value) { return $null }
    if ($Depth -ge 12) { return [string]$Value }

    # Unwrap PSObject first: a Get-Content line is a PSObject whose BaseObject
    # is a plain System.String. Taking the BaseObject drops PSProvider/PSDrive
    # and the cycle with them.
    if ($Value -is [System.Management.Automation.PSObject]) {
        $Value = $Value.BaseObject
        if ($null -eq $Value) { return $null }
    }

    if ($Value -is [string]) { return [string]$Value }
    if ($Value -is [bool] -or $Value -is [int] -or $Value -is [long] -or
        $Value -is [double] -or $Value -is [decimal]) { return $Value }
    if ($Value -is [datetime]) { return $Value.ToUniversalTime().ToString('o') }

    if ($Value -is [System.Collections.IDictionary]) {
        $out = [ordered]@{}
        foreach ($key in @($Value.Keys)) {
            $out[[string]$key] = ConvertTo-PlainForSummary -Value $Value[$key] -Depth ($Depth + 1)
        }
        return $out
    }

    if ($Value -is [System.Collections.IEnumerable]) {
        # ArrayList, not List[object]: in Windows PowerShell 5.1 `@($list)`
        # over a System.Collections.Generic.List[object] throws "Argument
        # types do not match", which silently degraded every array member of
        # the summary into one space-joined string. Caught by the host-side
        # sanitizer test, not by reading the code.
        #
        # The leading comma on the return is also load-bearing: without it
        # PowerShell unrolls the array, and a single-element list would reach
        # ConvertTo-Json as a scalar -- changing summary.json's shape for
        # exactly the fields the judge counts.
        $out = New-Object System.Collections.ArrayList
        foreach ($item in $Value) {
            [void]$out.Add((ConvertTo-PlainForSummary -Value $item -Depth ($Depth + 1)))
        }
        return , ($out.ToArray())
    }

    # Anything else (ProviderInfo, PSDriveInfo, Process, ...) is rendered, not
    # walked. This single line is what makes the explosion impossible.
    return [string]$Value
}

# (a) Incremental writer: called after EVERY step below so a hang later in
# the script can never swallow earlier results. Cheap (summary is small
# JSON, never the multi-GB install tree itself). Writes to the LOCAL
# $OutDir -- the shipper carries it to the host, so a wedged share can no
# longer stop the run's own bookkeeping.
function Save-Summary {
    param([string]$Step)
    $summary.last_completed_step = $Step
    $summary.step_seq = [int]$summary.step_seq + 1
    $summary.step_utc = (Get-Date).ToUniversalTime().ToString('o')
    try {
        $summaryPath = Join-Path $OutDir 'summary.json'
        $plain = ConvertTo-PlainForSummary -Value $summary
        ($plain | ConvertTo-Json -Depth $script:SummaryJsonDepth) | Set-Content -Path $summaryPath -Encoding UTF8
    } catch {
        # Writing the summary itself must never throw and abort the run.
        try { Add-Content -Path (Join-Path $OutDir 'summary-write-errors.log') -Value "step=$Step : $_" } catch {}
    }
}

# Installer-breadcrumb capture <gate-a-run7-findings>.
#
# This used to be four bare statements in the finalization block, between
# Save-Summary 'station-diag-captured-after-t3t5' and Save-Summary
# 'install-progress-log-copied'. Runs 4, 6 and 7 all stopped advancing
# inside exactly that window, and because the two Save-Summary calls were
# the only instrumentation, all three post-mortems can localise the stall to
# a WINDOW and none of them can name the statement. Run7 narrows it further
# but still not to one op: the complete 6844-byte copy reached the host, so
# the Copy-Item's handle closed, which leaves the tail read and the step
# write -- and on this host, against run7's own file, both measure in single
# -digit milliseconds.
#
# Two changes, both of which stand regardless of which op it turns out to be:
#   (1) RELOCATE: the primary call site is now right after the installer
#       returns, out of the finalization path entirely.
#   (2) INSTRUMENT: every statement gets its own step, so the next
#       occurrence names the operation instead of a window. That is the
#       instrument the three previous post-mortems did not have.
#
# Also: ONE forward read replaces the old copy-then-re-read-with-Tail. The
# old shape wrote the file and immediately read it back on the Sandbox's
# virtualized/differencing C:, which this file's own 2026-08-17 note already
# flags as a place where ordinary operations can take minutes. Reading the
# source once into memory, writing it out, and slicing the tail in memory
# removes the read-after-write entirely.
$script:InstallProgressCaptured = $false
$script:D3Route = 'MISSING'
$script:D3EngineExit = 'MISSING'
# <gate-a-audit-MA-23> install-progress.log is APPEND-ONLY across both install
# phases. The old capture walked the WHOLE file forward keeping the last
# match, so a phase-2 installer that died before emitting its d3-engine line
# left $script:D3Route / $script:D3EngineExit silently holding PHASE 1's
# values -- and check_download_only_no_station_dir, which gates only on
# D3_ENGINE_EXIT=0, then passed on stale data. Evidence:
# gate-a-cross-version-33681670855/install-progress.log:163 is phase 1's
# `route=FRESH_INSTALL engine_exit=11`. This mark is the line count of the log
# at the instant before the phase-2 setup.exe launches; the capture scans only
# lines after it, so a phase 2 that emitted nothing reports MISSING (a FAIL)
# rather than inheriting phase 1's success.
$script:InstallProgressPhase2Mark = 0
# <gate-a-audit-MA-24/MA-25> The d4/postinstall breadcrumbs no lane read.
$script:D4ActivateExit = 'MISSING'
$script:PostinstallOutcome = 'MISSING'
$script:PostinstallAlerts = 0
$script:PostinstallNonZeroSteps = @()
function Invoke-InstallProgressCapture {
    param([string]$Phase)
    if ($script:InstallProgressCaptured) {
        Save-Summary -Step "install-progress-already-captured-$Phase"
        return
    }
    $progressLog = Join-Path $env:ProgramData 'CivicCast\install-progress.log'
    $progressLogCopy = Join-Path $OutDir 'install-progress.log'

    Save-Summary -Step "install-progress-probe-begin-$Phase"
    $present = $false
    try { $present = Test-Path $progressLog } catch { $summary.errors += "install-progress probe failed ($Phase): $_" }
    Save-Summary -Step "install-progress-probed-$Phase"
    if (-not $present) { return }

    $summary.install_progress_log_found = $true

    # Size guard: this log is ~7 KB in every run observed so far, but a
    # runaway installer must not turn a diagnostic read into an unbounded
    # one. Above the cap, record the fact and skip rather than read.
    $sizeBytes = -1
    try { $sizeBytes = (Get-Item -LiteralPath $progressLog -Force).Length } catch {}
    $summary.install_progress_log_bytes = $sizeBytes
    Save-Summary -Step "install-progress-sized-$Phase"
    if ($sizeBytes -gt 16MB) {
        $summary.errors += "install-progress.log is $sizeBytes bytes (> 16MB cap) -- not read into summary ($Phase)"
        return
    }

    # [string[]] is load-bearing, not tidiness <gate-a-summary-json-explosion>.
    # Get-Content emits PSObject-wrapped strings carrying PSProvider/PSDrive
    # note properties whose object graph contains a cycle; casting to a plain
    # string array drops the wrapper at the source. Without it, these lines end
    # up in $summary and ConvertTo-Json spends 8 GB and 450s of CPU expanding
    # that cycle. See the note above Save-Summary for the measurements.
    $lines = [string[]]@()
    try {
        $lines = [string[]]@(Get-Content -LiteralPath $progressLog -ErrorAction Stop)
    } catch {
        $summary.errors += "install-progress read failed ($Phase): $_"
        Save-Summary -Step "install-progress-read-failed-$Phase"
        return
    }
    Save-Summary -Step "install-progress-read-$Phase"

    # <gate-a-audit-MA-23/MA-24/MA-25> Scan ONLY the tail this installer
    # invocation wrote (everything after $script:InstallProgressPhase2Mark),
    # and reset first, so an installer that emitted no breadcrumb of a given
    # kind reports MISSING rather than inheriting the previous phase's value.
    $script:D3Route = 'MISSING'
    $script:D3EngineExit = 'MISSING'
    $script:D4ActivateExit = 'MISSING'
    $script:PostinstallOutcome = 'MISSING'
    $script:PostinstallAlerts = 0
    $script:PostinstallNonZeroSteps = @()
    $phaseLines = @($lines | Select-Object -Skip $script:InstallProgressPhase2Mark)
    $summary.install_progress_phase_lines = $phaseLines.Count
    foreach ($line in $phaseLines) {
        $d3Match = [regex]::Match($line, 'step d3-engine: evidence route=(UPGRADE|FRESH_INSTALL|SAME_VERSION_NO_OP) engine_exit=(-?\d+)')
        if ($d3Match.Success) {
            $script:D3Route = $d3Match.Groups[1].Value
            $script:D3EngineExit = $d3Match.Groups[2].Value
        }
        $d4Match = [regex]::Match($line, 'step d4-activate-station: returned (-?\d+)')
        if ($d4Match.Success) { $script:D4ActivateExit = $d4Match.Groups[1].Value }
        $poMatch = [regex]::Match($line, 'postinstall: (SUCCESS|FAILED|COMPLETED WITH A D3 ROLLBACK)')
        if ($poMatch.Success) { $script:PostinstallOutcome = $poMatch.Groups[1].Value }
        if ($line -match 'ALERT:') { $script:PostinstallAlerts++ }
        # Any d2/d3/d4 step that returned nonzero. `step vc-redist` is
        # deliberately excluded: the hook itself treats a nonzero there as
        # non-fatal when a runtime is already present (nsh:495-542).
        $stepMatch = [regex]::Match($line, 'step (d[234]-[a-z0-9-]+): returned (-?\d+)')
        if ($stepMatch.Success -and [int]$stepMatch.Groups[2].Value -ne 0) {
            $script:PostinstallNonZeroSteps += "$($stepMatch.Groups[1].Value)=$($stepMatch.Groups[2].Value)"
        }
    }

    # <gate-a-audit-MA-25> The clean lane has no D3/postinstall check at all:
    # check_install asserts installer_exit_code == 0 and a station-set.json
    # PATH STRING, and the pre-#143 installer exited 0 on a rolled-back
    # upgrade. Ship these breadcrumbs as a file the judge grades in EVERY
    # lane. (This log is already copied into every evidence directory and is
    # currently asserted on by nothing.)
    $ipResult = Join-Path $OutDir 'INSTALL-PROGRESS-RESULT.txt'
    "checked_utc=$((Get-Date).ToUniversalTime().ToString('o'))" | Set-Content -Path $ipResult -Encoding UTF8
    "PHASE=$Phase" | Add-Content -Path $ipResult -Encoding UTF8
    "PHASE_LINE_COUNT=$($phaseLines.Count)" | Add-Content -Path $ipResult -Encoding UTF8
    "D3_ROUTE=$($script:D3Route)" | Add-Content -Path $ipResult -Encoding UTF8
    "D3_ENGINE_EXIT=$($script:D3EngineExit)" | Add-Content -Path $ipResult -Encoding UTF8
    "D4_ACTIVATE_EXIT=$($script:D4ActivateExit)" | Add-Content -Path $ipResult -Encoding UTF8
    "POSTINSTALL_OUTCOME=$($script:PostinstallOutcome)" | Add-Content -Path $ipResult -Encoding UTF8
    "POSTINSTALL_ALERTS=$($script:PostinstallAlerts)" | Add-Content -Path $ipResult -Encoding UTF8
    "POSTINSTALL_NONZERO_STEPS=$(if ($script:PostinstallNonZeroSteps.Count -gt 0) { $script:PostinstallNonZeroSteps -join ',' } else { 'none' })" | Add-Content -Path $ipResult -Encoding UTF8

    try {
        Set-Content -Path $progressLogCopy -Value $lines -Encoding UTF8
    } catch {
        $summary.errors += "install-progress copy failed ($Phase): $_"
    }
    Save-Summary -Step "install-progress-copied-$Phase"

    $summary.install_progress_log_tail = [string[]]@($lines | Select-Object -Last 80)
    $script:InstallProgressCaptured = $true
    Save-Summary -Step "install-progress-captured-$Phase"
}

function Invoke-UpgradeEngineEvidenceCapture {
    # D3's durable log is already database-URL-redacted by the engine. The
    # journal is not: its context contains the live credential, so never copy
    # that file verbatim into the mapped evidence directory / CI artifact.
    $stateRoot = Join-Path $env:ProgramData 'CivicCast\upgrade'
    $destRoot = Join-Path $OutDir 'upgrade-engine'
    try {
        New-Item -ItemType Directory -Force -Path $destRoot | Out-Null
        $engineLog = Join-Path $stateRoot 'upgrade-engine.log'
        if (Test-Path -LiteralPath $engineLog) {
            $size = (Get-Item -LiteralPath $engineLog -Force).Length
            if ($size -le 16MB) {
                Copy-Item -LiteralPath $engineLog -Destination (Join-Path $destRoot 'upgrade-engine.log') -Force
            } else {
                "upgrade-engine.log omitted: $size bytes exceeds 16 MB cap" |
                    Set-Content -LiteralPath (Join-Path $destRoot 'upgrade-engine-log-omitted.txt') -Encoding UTF8
            }
        }

        $journalPath = Join-Path $stateRoot 'upgrade-journal.json'
        if (Test-Path -LiteralPath $journalPath) {
            $journal = Get-Content -LiteralPath $journalPath -Raw -ErrorAction Stop | ConvertFrom-Json
            if ($null -ne $journal.context -and $journal.context.PSObject.Properties.Name -contains 'database_url') {
                $journal.context.database_url = '<database-url-redacted>'
            }
            $journal | ConvertTo-Json -Depth 20 |
                Set-Content -LiteralPath (Join-Path $destRoot 'upgrade-journal.redacted.json') -Encoding UTF8
        }
    } catch {
        "upgrade engine evidence capture failed: $_" |
            Set-Content -LiteralPath (Join-Path $OutDir 'upgrade-engine-capture-error.txt') -Encoding UTF8
    }
    Save-Summary -Step 'upgrade-engine-evidence-captured'
}

# --------------------------------------------------------------------------
# BOUNDED PROBE <gate-a-hoststore-wedge>
#
# `Invoke-BoundedProcess` above bounds an external command. This bounds a
# piece of OUR OWN logic: it ships the script text to a throwaway
# powershell.exe, hands it arguments and a result path as files (no quoting
# games), waits with a timeout, and reads the JSON back. On timeout the child
# is killed and the caller gets $null plus a recorded note.
#
# Why this exists rather than just calling the code inline: `C:\CivicCastHostStore`
# is a read-write mapped folder AND the install target -- 10,683 files and
# 1,264 directories live there after a successful install, and the installer's
# own final step spent three minutes merely measuring that tree. Every
# remaining synchronous read of it from the driver's single thread is an
# unbounded call against a share the driver does not control, which is the
# exact shape of failure this harness has hit four times.
#
# Rejected alternative, for the record: moving the install target to a LOCAL
# directory (C:\CivicCastLocalInstall) would remove the dependency outright
# and was the tidier-sounding option. It is not viable, and the reason is
# already documented in this file -- the `/D=C:\CivicCastHostStore\install`
# comment records that staging the packs locally "blew past the Sandbox's
# virtual disk (os error 112 'not enough space') during station-pack cache",
# because the install is ~12 GB and activation stages ~40 GB of models on top
# of a ~40 GB virtual C:. That same comment also records that activation
# "REFUSES junction/symlink install-roots", so the obvious dodge is closed
# too. Run-GateA.ps1's fresh-install guarantee (it resets hoststore\ before
# every run) and gate_a_verdict.py's install/activation checks both read that
# tree from the host as well. Bounding the accesses is the option that
# survives all three constraints.
# --------------------------------------------------------------------------
function Invoke-BoundedProbe {
    param(
        [string]$Name,
        [string]$ScriptText,
        [hashtable]$Arguments = @{},
        [int]$TimeoutSeconds = 90
    )
    $stamp = [guid]::NewGuid().ToString('N').Substring(0, 8)
    $scriptPath = Join-Path $env:TEMP "civiccast-probe-$stamp.ps1"
    $argsPath = Join-Path $env:TEMP "civiccast-probe-$stamp.args.json"
    $resultPath = Join-Path $env:TEMP "civiccast-probe-$stamp.result.json"
    try {
        # The child reads $ProbeArgs (hashtable) and must write its result to
        # $ResultPath. Both are injected by this preamble, so the caller's
        # script text stays plain PowerShell.
        $preamble = @'
param([string]$ArgsPath, [string]$ResultPath)
$ProbeArgs = @{}
try {
    if (Test-Path $ArgsPath) {
        $raw = Get-Content -Path $ArgsPath -Raw -Encoding UTF8
        $obj = $raw | ConvertFrom-Json
        foreach ($prop in $obj.PSObject.Properties) { $ProbeArgs[$prop.Name] = $prop.Value }
    }
} catch {}
'@
        Set-Content -Path $scriptPath -Value ($preamble + [Environment]::NewLine + $ScriptText) -Encoding UTF8
        ($Arguments | ConvertTo-Json -Depth 5) | Set-Content -Path $argsPath -Encoding UTF8

        $run = Invoke-BoundedProcess -FilePath 'powershell.exe' -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$scriptPath`"",
            '-ArgsPath', "`"$argsPath`"", '-ResultPath', "`"$resultPath`""
        ) -TimeoutSeconds $TimeoutSeconds

        if (-not $run.completed) {
            $summary.errors += "bounded probe '$Name' did not complete within ${TimeoutSeconds}s: $($run.error)"
            return $null
        }
        if (-not (Test-Path $resultPath)) {
            $summary.errors += "bounded probe '$Name' completed (exit $($run.exit_code)) but wrote no result"
            return $null
        }
        return ((Get-Content -Path $resultPath -Raw -Encoding UTF8) | ConvertFrom-Json)
    } catch {
        $summary.errors += "bounded probe '$Name' threw: $_"
        return $null
    } finally {
        foreach ($tmp in @($scriptPath, $argsPath, $resultPath)) {
            try { if (Test-Path $tmp) { Remove-Item -Path $tmp -Force -ErrorAction SilentlyContinue } } catch {}
        }
    }
}

# (b) Bounded, targeted marker-file lookup -- replaces the old
# `Get-ChildItem -Recurse` full-tree scans. Checks only the exact locations
# the coordinator specified, plus a SHALLOW (one-level, non-recursive)
# listing of immediate subfolders under <installDir>\app, never a deep walk.
#
# <gate-a-hoststore-wedge>: every one of those checks reads
# C:\CivicCastHostStore, a mapped folder. Targeted and non-recursive is not
# the same as bounded -- a single Test-Path against a wedged share blocks
# forever. The probes now run in a disposable child with a hard timeout.
function Test-KnownPaths {
    param([string]$InstallDir, [string]$FileName)

    $probe = Invoke-BoundedProbe -Name "known-paths:$FileName" -TimeoutSeconds 90 -Arguments @{
        InstallDir = $InstallDir
        FileName   = $FileName
        ProgramData = $env:ProgramData
    } -ScriptText @'
$hits = New-Object System.Collections.Generic.List[string]
$InstallDir = $ProbeArgs['InstallDir']
$FileName = $ProbeArgs['FileName']
try {
    if ($InstallDir) {
        $p = Join-Path $InstallDir $FileName
        if (Test-Path $p) { $hits.Add($p) }
        $appDir = Join-Path $InstallDir 'app'
        $p = Join-Path $appDir $FileName
        if (Test-Path $p) { $hits.Add($p) }
        if (Test-Path $appDir) {
            Get-ChildItem -Path $appDir -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object {
                $candidate = Join-Path $_.FullName $FileName
                if (Test-Path $candidate) { $hits.Add($candidate) }
            }
        }
    }
    $pd = Join-Path $ProbeArgs['ProgramData'] 'CivicCast'
    $p = Join-Path $pd $FileName
    if (Test-Path $p) { $hits.Add($p) }
} catch {}
# Named envelope, NOT a unary-comma array. Windows PowerShell 5.1's
# ConvertTo-Json turns `(, @(...))` into {"value":[...],"Count":n} -- an
# object, not a JSON array -- so every caller would silently receive one
# wrapper instead of its list. Caught by the host-side probe smoke test.
@{ items = @($hits | Select-Object -Unique) } | ConvertTo-Json -Depth 3 | Set-Content -Path $ResultPath -Encoding UTF8
'@

    if ($null -eq $probe) { return @() }
    return @($probe.items)
}

# Kept for reference and for any caller that genuinely wants the unbounded
# form. Nothing in this script calls it; Test-KnownPaths above is the bounded
# replacement.
function Test-KnownPathsUnbounded {
    param([string]$InstallDir, [string]$FileName)
    $hits = New-Object System.Collections.Generic.List[string]

    # <installDir>\<file>
    $p = Join-Path $InstallDir $FileName
    if (Test-Path $p) { $hits.Add($p) }

    # <installDir>\app\<file>
    $appDir = Join-Path $InstallDir 'app'
    $p = Join-Path $appDir $FileName
    if (Test-Path $p) { $hits.Add($p) }

    # <installDir>\app\*\<file>  -- one shallow, non-recursive enumeration of
    # app's immediate subdirectories only (bounded by however many top-level
    # app subfolders exist, typically a handful -- not a tree walk).
    if (Test-Path $appDir) {
        Get-ChildItem -Path $appDir -Directory -Force -ErrorAction SilentlyContinue | ForEach-Object {
            $candidate = Join-Path $_.FullName $FileName
            if (Test-Path $candidate) { $hits.Add($candidate) }
        }
    }

    # %ProgramData%\CivicCast\<file>
    $pd = Join-Path $env:ProgramData 'CivicCast'
    $p = Join-Path $pd $FileName
    if (Test-Path $p) { $hits.Add($p) }

    return ($hits | Select-Object -Unique)
}

# ============================================================================
# HARDENED <gate-a-station-up-wait-and-log-capture> -- station diagnostics
# capture. The 8579e66-run3 evidence proved the harness had NO way to see
# why the station never listened on :8000: postgres.log / nats.log /
# control_plane.log / supervisor.log all live under
# %ProgramData%\CivicCast\logs INSIDE the sandbox, which resets every
# session, and nothing copied them out before the VM was torn down. This
# function is called twice -- once right after the station-up-wait
# concludes (pass OR fail) and once, unconditionally, from the top-level
# `finally` block -- so a diagnosis is preserved even if something later in
# the run hangs or throws.
#
# Every operation here is BOUNDED and TARGETED, matching this file's
# existing "no recursive scans over the 10k-file install tree" discipline:
# robocopy of two small, known ProgramData subdirectories (never the
# install tree), three non-blocking process/service queries, and a
# `-MaxEvents`-capped Get-WinEvent call. Paths are taken from
# civiccast/native/supervisor/install_layout.py's `resolve_install_layout`
# (the single source of truth for the installed layout) rather than
# re-guessed here: log_root = <ProgramData>\CivicCast\logs (every child's
# <name>.log plus the rotating supervisor.log all land there in one place),
# config lives at <ProgramData>\CivicCast\config (nats-server.conf etc --
# explicitly NOT the sibling data\pgdata / data\nats-store directories,
# which are excluded from the robocopy).
function Invoke-StationDiagCapture {
    param([string]$OutDir, [string]$InstallDir, [string]$Label, [datetime]$RunStart)
    $diagDir = Join-Path $OutDir "station-diag\$Label"
    New-Item -ItemType Directory -Force -Path $diagDir | Out-Null
    $note = Join-Path $diagDir '_capture-note.txt'
    "captured_utc=$((Get-Date).ToUniversalTime().ToString('o')) label=$Label" | Set-Content -Path $note -Encoding UTF8

    $pdCivicCast = Join-Path $env:ProgramData 'CivicCast'

    # Whole logs dir -- it is small (text logs, not media/model data), per
    # the coordinator's note. Covers postgres.log, nats.log,
    # control_plane.log, ollama.log, and the rotating supervisor.log in one
    # bounded copy (see install_layout.default_log_root / service.py's
    # child_log_path -- all children log to <log_root>\<name>.log).
    $logsSrc = Join-Path $pdCivicCast 'logs'
    if (Test-Path $logsSrc) {
        try {
            & robocopy.exe $logsSrc (Join-Path $diagDir 'logs') /E /R:1 /W:1 /NFL /NDL /NJH /NJS 2>&1 |
                Out-File -FilePath (Join-Path $diagDir 'robocopy-logs.log') -Encoding UTF8
        } catch { "robocopy logs failed: $_" | Add-Content -Path $note -Encoding UTF8 }
    } else {
        "logs dir not present at $logsSrc (station never got far enough to log, or ProgramData path differs)" | Add-Content -Path $note -Encoding UTF8
    }

    # Per-channel egress worker logs (Gate A T4, 2026-09). The GStreamer playout
    # worker's own stdout/stderr -- its traceback, its "CTRL stall" line, the
    # encoder's stderr -- go to <egress work_dir>\<channel>\logs\gst-worker.*.log
    # (civiccast/egress/gst/strategy.py start(): channel_dir/'logs'). The
    # supervisor pins CIVICCAST_EGRESS_WORK_DIR into ProgramData\CivicCast\data\egress
    # (civiccast/native/supervisor/children.py default_egress_work_dir), which is
    # OUTSIDE the ProgramData\CivicCast\logs tree copied above -- so a run whose
    # worker died at startup previously captured the daemon saying a child exited
    # and nothing at all about why. Copy ONLY the per-channel logs dirs (/S with a
    # *.log filter under each channel's 'logs'), never the sibling media/segment
    # artifacts in the same work dir.
    $egressSrc = Join-Path $pdCivicCast 'data\egress'
    if (Test-Path $egressSrc) {
        try {
            $egressDst = Join-Path $diagDir 'egress-worker-logs'
            New-Item -ItemType Directory -Force -Path $egressDst | Out-Null
            $logDirs = @(Get-ChildItem -LiteralPath $egressSrc -Directory -ErrorAction SilentlyContinue |
                ForEach-Object { Join-Path $_.FullName 'logs' } |
                Where-Object { Test-Path $_ })
            if ($logDirs.Count -eq 0) {
                "no per-channel egress logs dir under $egressSrc (no channel ever started an encoder)" |
                    Add-Content -Path $note -Encoding UTF8
            }
            foreach ($ld in $logDirs) {
                $channelName = Split-Path (Split-Path $ld -Parent) -Leaf
                & robocopy.exe $ld (Join-Path $egressDst $channelName) '*.log' /S /R:1 /W:1 /NFL /NDL /NJH /NJS 2>&1 |
                    Out-File -FilePath (Join-Path $diagDir 'robocopy-egress-worker-logs.log') -Append -Encoding UTF8
            }
        } catch { "robocopy egress worker logs failed: $_" | Add-Content -Path $note -Encoding UTF8 }
    } else {
        "egress work dir not present at $egressSrc" | Add-Content -Path $note -Encoding UTF8
    }

    # Config only -- /XD excludes 'data' and 'pgdata' so a misplaced
    # co-located data directory can never turn this into an unbounded copy
    # of the postgres cluster or NATS JetStream store.
    $configSrc = Join-Path $pdCivicCast 'config'
    if (Test-Path $configSrc) {
        try {
            & robocopy.exe $configSrc (Join-Path $diagDir 'config') /E /XD 'data' 'pgdata' 'nats-store' /R:1 /W:1 /NFL /NDL /NJH /NJS 2>&1 |
                Out-File -FilePath (Join-Path $diagDir 'robocopy-config.log') -Encoding UTF8
        } catch { "robocopy config failed: $_" | Add-Content -Path $note -Encoding UTF8 }
    } else {
        "config dir not present at $configSrc" | Add-Content -Path $note -Encoding UTF8
    }

    try { (& sc.exe qc CivicCastSupervisor 2>&1 | Out-String) | Set-Content -Path (Join-Path $diagDir 'sc-qc.txt') -Encoding UTF8 } catch { "sc qc capture failed: $_" | Add-Content -Path $note -Encoding UTF8 }
    try { (& sc.exe query CivicCastSupervisor 2>&1 | Out-String) | Set-Content -Path (Join-Path $diagDir 'sc-query.txt') -Encoding UTF8 } catch { "sc query capture failed: $_" | Add-Content -Path $note -Encoding UTF8 }
    try {
        $ns = (& netstat.exe -ano 2>&1)
        ($ns | Select-String -Pattern 'LISTENING') | Out-String | Set-Content -Path (Join-Path $diagDir 'netstat-listening.txt') -Encoding UTF8
    } catch { "netstat capture failed: $_" | Add-Content -Path $note -Encoding UTF8 }
    try {
        $taskPattern = 'python|pythonservice|civiccast|postgres|nats|ollama'
        $rows = (& tasklist.exe /v /fo csv 2>&1) | ConvertFrom-Csv -ErrorAction SilentlyContinue |
            Where-Object { $_.'Image Name' -match $taskPattern }
        $rows | Format-Table -AutoSize | Out-String -Width 300 | Set-Content -Path (Join-Path $diagDir 'tasklist-filtered.txt') -Encoding UTF8
    } catch { "tasklist capture failed: $_" | Add-Content -Path $note -Encoding UTF8 }

    try {
        $winEventStart = $RunStart
        $startedMarker = Join-Path $OutDir '_STARTED.marker'
        if (Test-Path $startedMarker) { $winEventStart = (Get-Item $startedMarker).CreationTime }
        $events = Get-WinEvent -FilterHashtable @{
            LogName = 'Application', 'System'; Level = 1, 2, 3; StartTime = $winEventStart
        } -MaxEvents 200 -ErrorAction SilentlyContinue
        $eventRows = @($events | ForEach-Object {
            [ordered]@{
                TimeCreated  = $_.TimeCreated.ToString('o')
                LogName      = $_.LogName
                LevelDisplay = $_.LevelDisplayName
                ProviderName = $_.ProviderName
                Id           = $_.Id
                Message      = ($_.Message -split "`n" | Select-Object -First 5) -join ' | '
            }
        })
        # (,$eventRows) forces ConvertTo-Json to keep serializing a
        # single-element (or zero-element) collection as a JSON array --
        # without the unary comma, PowerShell's pipeline unrolls a
        # one-item array before ConvertTo-Json ever sees it, silently
        # producing a bare JSON object instead of a one-element array (a
        # real gap found reviewing 8579e66-run4's own captured evidence,
        # which had exactly one Windows Event Log entry).
        ((, $eventRows) | ConvertTo-Json -Depth 4) | Set-Content -Path (Join-Path $diagDir 'winevent-app-system.json') -Encoding UTF8
    } catch { "winevent capture failed: $_" | Add-Content -Path $note -Encoding UTF8 }

    # These two reads are against C:\CivicCastHostStore (a mapped folder), and
    # this capture runs up to three times per run -- including from the
    # top-level `finally`, where a block would cost the run its DONE.json.
    # Bounded <gate-a-hoststore-wedge>.
    if ($InstallDir) {
        $copied = Invoke-BoundedProbe -Name "diag-station-markers:$Label" -TimeoutSeconds 60 -Arguments @{
            InstallDir = $InstallDir
            DiagDir    = $diagDir
        } -ScriptText @'
$done = @()
try {
    foreach ($f in @('activation-self-test.json', 'station-set.json')) {
        $src = Join-Path $ProbeArgs['InstallDir'] $f
        if (Test-Path $src) {
            Copy-Item -LiteralPath $src -Destination (Join-Path $ProbeArgs['DiagDir'] $f) -Force -ErrorAction SilentlyContinue
            $done += $f
        }
    }
} catch {}
@{ items = @($done) } | ConvertTo-Json -Depth 3 | Set-Content -Path $ResultPath -Encoding UTF8
'@
        if ($null -eq $copied) {
            "station marker copy ($Label) did not complete within its bound -- see summary.errors" |
                Add-Content -Path $note -Encoding UTF8
        }
    }
}

# ============================================================================
# HARDENED 2026-08-18 -- helpers added for T2 (render assert), T3 (real
# authenticated content loop), and T4 (product-engine egress) upgrades.
# audit findings this section addresses:
#   QA-F1: the old T2 pass criterion was "200 status + >0 bytes", which is
#          true for the served SPA shell whether or not the SPA ever mounts.
#          Test-RenderAssert below drives real headless Edge and requires
#          the DOM to grow far past the raw shell AND contain real UI copy.
#   QA-F2: the old T4 "egress" proof piped raw ffmpeg straight at a UDP
#          port -- it proved ffmpeg works, not that CivicCast's own egress
#          engine (config -> daemon -> sink) works. The T4 section now
#          attempts to drive that engine through its real staff API first
#          and only falls back to the synthetic-ffmpeg proof, labeled
#          PASS_FFMPEG_FALLBACK (never plain PASS), if the API path is
#          genuinely blocked.
#   T3:    a real authenticated content loop -- loopback first-admin -> staff
#          bearer token -> generate/upload/package/publish an asset ->
#          prove it on the public surface -> prove offline captions land.
#
# All HTTP calls in these helpers go through Invoke-CivicCastApi so every
# step logs method/url/status, and any >=400 response has its body
# captured -- consistent with this file's existing philosophy of leaving a
# forensic trail even when a step fails, rather than just failing silently.
# ============================================================================

Add-Type -AssemblyName System.Net.Http

# Generic JSON-capable HTTP helper. Never throws past its own boundary --
# failures (including non-2xx responses) are captured into the returned
# object so callers can make bounded, fail-honest decisions instead of
# relying on try/catch control flow for expected-failure paths (e.g. a 404
# probing for an endpoint that may not exist on this build).
function Invoke-CivicCastApi {
    param(
        [string]$Method,
        [string]$Url,
        [string]$LogFile,
        [object]$BodyObj = $null,
        [string]$BearerToken = $null,
        [string]$SetupNonce = $null,
        [int]$TimeoutSec = 30
    )
    $result = [ordered]@{
        method = $Method; url = $Url; status = $null; ok = $false
        body_raw = $null; body_json = $null; error = $null
    }
    try {
        $headers = @{}
        if ($BearerToken) { $headers['Authorization'] = "Bearer $BearerToken" }
        if ($SetupNonce) { $headers['X-CivicCast-Setup-Nonce'] = $SetupNonce }
        $params = @{
            Uri = $Url; Method = $Method; Headers = $headers; UseBasicParsing = $true
            TimeoutSec = $TimeoutSec; ErrorAction = 'Stop'
        }
        if ($null -ne $BodyObj) {
            $params['Body'] = ($BodyObj | ConvertTo-Json -Depth 10)
            $params['ContentType'] = 'application/json'
        }
        $resp = Invoke-WebRequest @params
        $result.status = [int]$resp.StatusCode
        $result.body_raw = [string]$resp.Content
        $result.ok = $true
    } catch {
        $we = $_.Exception
        if ($we.Response) {
            try {
                $result.status = [int]$we.Response.StatusCode
                $stream = $we.Response.GetResponseStream()
                $reader = New-Object System.IO.StreamReader($stream)
                $result.body_raw = $reader.ReadToEnd()
            } catch {}
        }
        $result.error = "$_"
    }
    if ($result.body_raw) {
        try { $result.body_json = $result.body_raw | ConvertFrom-Json -ErrorAction Stop } catch {}
    }
    try {
        "$Method $Url -> status:$($result.status) ok:$($result.ok) err:$($result.error)" | Add-Content -Path $LogFile -Encoding UTF8
        if ((-not $result.ok) -or ($result.status -ge 400)) {
            "  BODY: $($result.body_raw)" | Add-Content -Path $LogFile -Encoding UTF8
        }
    } catch {}
    return $result
}

# Multipart asset upload -- Windows PowerShell 5.1's Invoke-WebRequest has
# no -Form parameter (that is PowerShell 6+ only), so multipart/form-data
# is built by hand via System.Net.Http.MultipartFormDataContent.
function Invoke-AssetUpload {
    param(
        [string]$BaseUrl, [string]$Token, [string]$AssetId, [string]$Title,
        [string]$FilePath, [string]$LogFile, [int]$TimeoutSec = 180
    )
    $url = "$BaseUrl/api/staff/assets/upload"
    $result = [ordered]@{ method = 'POST'; url = $url; status = $null; ok = $false; body_raw = $null; body_json = $null; error = $null }
    try {
        $handler = New-Object System.Net.Http.HttpClientHandler
        $client = New-Object System.Net.Http.HttpClient($handler)
        $client.Timeout = [TimeSpan]::FromSeconds($TimeoutSec)
        $client.DefaultRequestHeaders.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue('Bearer', $Token)
        $content = New-Object System.Net.Http.MultipartFormDataContent
        $content.Add((New-Object System.Net.Http.StringContent($AssetId)), 'asset_id')
        $content.Add((New-Object System.Net.Http.StringContent($Title)), 'title')
        $fileBytes = [System.IO.File]::ReadAllBytes($FilePath)
        $fileContent = New-Object System.Net.Http.ByteArrayContent(,$fileBytes)
        try { $fileContent.Headers.ContentType = [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('video/mp4') } catch {}
        $content.Add($fileContent, 'file', [System.IO.Path]::GetFileName($FilePath))
        $resp = $client.PostAsync($url, $content).Result
        $result.status = [int]$resp.StatusCode
        $result.body_raw = $resp.Content.ReadAsStringAsync().Result
        $result.ok = $resp.IsSuccessStatusCode
        try { $client.Dispose() } catch {}
    } catch {
        $result.error = "$_"
    }
    if ($result.body_raw) {
        try { $result.body_json = $result.body_raw | ConvertFrom-Json -ErrorAction Stop } catch {}
    }
    try {
        "POST $url -> status:$($result.status) ok:$($result.ok) err:$($result.error)" | Add-Content -Path $LogFile -Encoding UTF8
        if ((-not $result.ok) -or ($result.status -ge 400)) {
            "  BODY: $($result.body_raw)" | Add-Content -Path $LogFile -Encoding UTF8
        }
    } catch {}
    return $result
}

# Probe the shipped ffmpeg's h264 encoder before trusting it for real work.
# The shipped pack is the LGPL/v3 build (no GPL libx264) -- resolve_h264_
# encoder in the native stream code falls back to libopenh264 on a CPU-only
# box, so that is what this harness must probe and use too.
function Test-Libopenh264 {
    param([string]$FfmpegExe)
    if (-not $FfmpegExe -or -not (Test-Path $FfmpegExe)) { return $false }
    try {
        $tmp = Join-Path $env:TEMP ("probe-" + [Guid]::NewGuid().ToString('N') + '.mp4')
        $probeArgs = @('-y','-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc=size=320x240:rate=25','-t','1','-c:v','libopenh264', $tmp)
        $p = Start-Process -FilePath $FfmpegExe -ArgumentList $probeArgs -PassThru -NoNewWindow -Wait
        $ok = ($p.ExitCode -eq 0) -and (Test-Path $tmp) -and ((Get-Item $tmp -ErrorAction SilentlyContinue).Length -gt 0)
        Remove-Item -Path $tmp -Force -ErrorAction SilentlyContinue
        return [bool]$ok
    } catch { return $false }
}

# Locate msedge.exe -- verify the standard path at runtime, fall back to
# where.exe, exactly per the coordinator's environment notes.
function Get-EdgePath {
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
        (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe')
    )
    foreach ($c in $candidates) {
        if ($c -and (Test-Path $c)) { return $c }
    }
    try {
        $found = & where.exe msedge.exe 2>$null | Select-Object -First 1
        if ($found -and (Test-Path $found)) { return $found }
    } catch {}
    return $null
}

# Bounded headless-Edge dump-dom. Uses a per-call --user-data-dir (a shared
# profile can attach to / hang on another running Edge session -- observed
# on the host during dry-run) and a HARD kill via $p.WaitForExit(ms) rather
# than -Wait, so a stuck render can never hang the whole harness run.
function Invoke-HeadlessDumpDom {
    param([string]$EdgeExe, [string]$Url, [string]$OutFile, [int]$TimeoutSec = 75, [int]$VirtualTimeBudgetMs = 10000)
    $result = [ordered]@{ url = $Url; ok = $false; timed_out = $false; exit_code = $null; error = $null }
    $udd = Join-Path $env:TEMP ("edge-udd-" + [Guid]::NewGuid().ToString('N'))
    try {
        New-Item -ItemType Directory -Force -Path $udd | Out-Null
        $edgeArgs = @(
            '--headless=new','--disable-gpu','--hide-scrollbars','--no-first-run',
            '--disable-extensions','--disable-sync','--disable-background-networking',
            '--disable-default-apps','--disable-component-update',
            '--disable-client-side-phishing-detection',
            "--user-data-dir=$udd",
            "--virtual-time-budget=$VirtualTimeBudgetMs",
            '--dump-dom', $Url
        )
        $stderrFile = "$OutFile.stderr.log"
        $p = Start-Process -FilePath $EdgeExe -ArgumentList $edgeArgs -PassThru -NoNewWindow `
            -RedirectStandardOutput $OutFile -RedirectStandardError $stderrFile
        $p.Handle | Out-Null   # forces handle creation so WaitForExit is reliable (known PS quirk)
        $finished = $p.WaitForExit($TimeoutSec * 1000)
        if (-not $finished) {
            $result.timed_out = $true
            try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
        } else {
            $result.exit_code = $p.ExitCode
            $result.ok = $true
        }
    } catch {
        $result.error = "$_"
    } finally {
        try { Remove-Item -Recurse -Force -Path $udd -ErrorAction SilentlyContinue } catch {}
    }
    return $result
}

# T2 render assert (QA-F1 fix). The raw HTTP capture proves the server
# returned *a* response; it says nothing about whether the SPA actually
# mounted. PASS requires the dumped DOM to be both a large multiple of the
# raw shell's size AND to contain real UI copy that provably is not in the
# raw shell -- so a large-but-broken response (e.g. a stack trace page)
# can't fake a pass just by being bigger than the shell.
function Test-RenderAssert {
    param([string]$Label, [string]$Url, [string[]]$KnownStrings, [string]$EdgeExe, [string]$DomOutFile)
    $r = [ordered]@{
        label = $Label; url = $Url; raw_bytes = 0; dumped_bytes = 0; ratio = 0.0
        known_string_matched = $null; known_string_in_raw = $false
        edge_ok = $false; timed_out = $false; error = $null; result = 'FAIL'
    }
    try {
        $rawResp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 15 -ErrorAction Stop
        $rawBody = [string]$rawResp.Content
    } catch {
        $r.error = "raw fetch failed: $_"
        return $r
    }
    $r.raw_bytes = $rawBody.Length
    foreach ($ks in $KnownStrings) {
        if ($rawBody.Contains($ks)) { $r.known_string_in_raw = $true }
    }
    if (-not $EdgeExe) {
        $r.error = 'msedge.exe not found on this box (checked standard path + where.exe)'
        return $r
    }
    $dump = Invoke-HeadlessDumpDom -EdgeExe $EdgeExe -Url $Url -OutFile $DomOutFile
    $r.edge_ok = $dump.ok
    $r.timed_out = $dump.timed_out
    if ($dump.error -and -not $r.error) { $r.error = $dump.error }
    if ($r.timed_out) {
        if (-not $r.error) { $r.error = 'headless Edge dump-dom timed out' }
        return $r
    }
    if (-not (Test-Path $DomOutFile)) {
        $r.error = 'no DOM output file produced'
        return $r
    }
    $dumpedBody = Get-Content -Path $DomOutFile -Raw -ErrorAction SilentlyContinue
    if (-not $dumpedBody) { $dumpedBody = '' }
    $r.dumped_bytes = $dumpedBody.Length
    if ($r.raw_bytes -gt 0) { $r.ratio = [Math]::Round(([double]$r.dumped_bytes / [double]$r.raw_bytes), 2) }
    foreach ($ks in $KnownStrings) {
        if ($dumpedBody.Contains($ks)) { $r.known_string_matched = $ks; break }
    }
    $pass = ($r.dumped_bytes -gt (3 * $r.raw_bytes)) -and ($r.dumped_bytes -gt 3000) -and ($null -ne $r.known_string_matched) -and (-not $r.known_string_in_raw)
    if ($pass) { $r.result = 'PASS' } else { $r.result = 'FAIL' }
    return $r
}

# THE station-readiness predicate <gate-a-audit-MA-26>. ONE function, used by
# the 20-minute station-up wait, the T3 `health-200` surface probe, and every
# T5 soak beat. PR #143 taught the station-up wait to read the /health BODY
# (civiccast/app.py's own docstring: 200 is LIVENESS ONLY; readiness is the
# body's `status`, and migration currency is `schema`) but left T3 and T5
# reading `.StatusCode` alone. Gate A run 33681670855's evidence shows exactly
# what that costs: `T5_RESULT=PASS beats=2 unhealthy=0` and `T3 health-200 ->
# 200 ok` recorded against a station whose body read
# {"status":"degraded","schema":"behind",...}. Three call sites, one
# predicate, so a future fix cannot land on one and miss the others.
function Invoke-CivicCastHealthProbe {
    param([string]$Url, [int]$TimeoutSec = 10)
    $probe = [ordered]@{
        url = $Url; status = -1; ok = $false; bytes = 0; snippet = $null; error = $null
        body_status = $null; body_schema = $null; db_revision = $null; expected_head = $null
        body_parse_error = $null
    }
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec -ErrorAction Stop
        $probe.status = [int]$r.StatusCode
        $body = [string]$r.Content
        $probe.bytes = $body.Length
        if ($probe.bytes -gt 0) {
            $probe.snippet = ($body.Substring(0, [Math]::Min(300, $body.Length))) -replace '\s+', ' '
        }
        $parsed = $null
        if ($probe.bytes -gt 0) {
            try { $parsed = $body | ConvertFrom-Json -ErrorAction Stop }
            catch { $probe.body_parse_error = "$($_.Exception.Message)" }
        }
        if ($parsed) {
            $probe.body_status = [string]$parsed.status
            $probe.body_schema = [string]$parsed.schema
            if ($parsed.PSObject.Properties.Name -contains 'schema_db_revision') {
                $probe.db_revision = [string]$parsed.schema_db_revision
            }
            if ($parsed.PSObject.Properties.Name -contains 'schema_expected_head') {
                $probe.expected_head = [string]$parsed.schema_expected_head
            }
            $probe.ok = ($probe.status -eq 200) -and ($probe.body_status -eq 'healthy') -and
                ($probe.body_schema -eq 'current')
        }
    } catch {
        $probe.error = "$($_.Exception.Message)"
        try { $probe.status = [int]$_.Exception.Response.StatusCode.value__ } catch { $probe.status = -1 }
    }
    return $probe
}

# INDEPENDENT post-upgrade schema proof <gate-a-audit-BL-10>.
#
# THE BUG THIS EXISTS FOR. PR #143 added
# POST_UPGRADE_DB_REVISION_MATCHES_HEAD to both upgrade lanes on the stated
# ground that "a healthy station-up body and D3_ENGINE_EXIT=0 are no longer
# treated as proof by themselves." It was computed as
#   $healthRes.ok -and $rev -ne '<unavailable>' -and ($rev -eq $expectedHead)
# where $healthRes.ok requires body_schema=='current', civiccast/app.py
# sources BOTH schema_db_revision and schema_expected_head from ONE
# SchemaStatus, and civiccast/schema_check.py returns state=="current" IF AND
# ONLY IF db_revision == expected_head. So MATCHES_HEAD was 1 whenever ok was
# 1 -- ALWAYS. The guard written to stop trusting a label was itself the
# label, and both sides of its comparison were one process's self-report.
#
# THE FIX, in two halves that must come from genuinely different origins:
#   (a) the DATABASE's own alembic_version row, read here with psql straight
#       out of the station's Postgres -- not through /health, not through the
#       running control plane, not through any code path that also computes
#       the expected head; and
#   (b) the expected head string, recorded by the CI job at BUILD time from
#       the candidate's own source tree and handed to this run as
#       EXPECTED-MIGRATION-HEAD.txt (see Run-GateA.ps1 -ExpectedMigrationHead
#       and .github/workflows/gate-a-station-acceptance.yml).
# scripts/gate_a_verdict.py re-derives the match from those two values and
# FAILs on evidence that disagrees with the harness's own flag.
#
# Fails LOUD, never silently: every failure path returns a '<...>' sentinel
# the judge treats as a FAIL, and none of them ever emits the credential.
function Get-CivicCastDbRevisionViaPsql {
    param([string]$InstallDir, [string]$LogFile)

    $probe = [ordered]@{
        revision = '<unavailable>'; method = 'psql'; psql_path = $null
        database_url_found = $false; exit_code = $null; error = $null
    }
    $psql = $null
    foreach ($candidate in @(
        (Join-Path $InstallDir 'packs\native-server-binaries\payload\bin\psql.exe'),
        (Join-Path $InstallDir 'runtime\bin\psql.exe')
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { $psql = $candidate; break }
    }
    if (-not $psql) {
        $probe.revision = '<no-psql>'
        $probe.error = 'psql.exe not found under the install tree'
        if ($LogFile) { "POST_UPGRADE_DB_REVISION_SOURCE=psql result=NOT-FOUND" | Add-Content -Path $LogFile -Encoding UTF8 }
        return $probe
    }
    $probe.psql_path = $psql

    $dbUrl = $null
    try {
        # <gate-a-dburl-registry-key> The product records the URL under the
        # \Native subkey (nsis-hooks-bootstrap.nsh: ReadRegStr ... "Software\CivicCast\Native" "DatabaseUrl";
        # civiccast/native/provision write_database_url). Reading the parent key made
        # this proof '<no-database-url>' on every upgrade run that got this far
        # (Gate A 33857982657, kit c27c6e7, 2026-09-04).
        $dbUrl = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\CivicCast\Native' -Name 'DatabaseUrl' -ErrorAction Stop).DatabaseUrl
    } catch {}
    if ([string]::IsNullOrWhiteSpace($dbUrl)) {
        $probe.revision = '<no-database-url>'
        $probe.error = 'HKLM\SOFTWARE\CivicCast\Native\DatabaseUrl is absent or empty'
        if ($LogFile) { "POST_UPGRADE_DB_REVISION_SOURCE=psql result=NO-DATABASE-URL" | Add-Content -Path $LogFile -Encoding UTF8 }
        return $probe
    }
    $probe.database_url_found = $true

    # SQLAlchemy's driver suffix ('postgresql+psycopg://') is not a scheme
    # libpq understands; strip it before handing the URL to psql. The
    # credential lives only in this local variable and psql's own argv/env --
    # nothing below ever writes $dbUrl or $conn.UserInfo to evidence.
    $libpqUrl = $dbUrl -replace '^postgresql\+[a-z0-9_]+://', 'postgresql://'
    try {
        $conn = [uri]$libpqUrl
    } catch {
        $probe.revision = '<unparseable-database-url>'
        $probe.error = 'DatabaseUrl is not a parseable URI'
        return $probe
    }

    # Same two-namespace search order civiccast/schema_check.py:173 uses.
    # <gate-a-psql-schema> A UNION over both namespaces is one statement: when
    # public.alembic_version does not exist (the product keeps it in the civiccast
    # schema, civiccast/alembic/env.py version_table_schema) PostgreSQL rejects
    # the WHOLE statement ('relation "public.alembic_version" does not exist',
    # Gate A 33885550628). Try each namespace as its own statement, same order
    # civiccast/schema_check.py:173 uses.
    $sqlCandidates = @(
        'SELECT version_num FROM civiccast.alembic_version LIMIT 1',
        'SELECT version_num FROM public.alembic_version LIMIT 1'
    )
    $userInfo = $conn.UserInfo -split ':', 2
    $env:PGPASSWORD = if ($userInfo.Count -gt 1) { [uri]::UnescapeDataString($userInfo[1]) } else { '' }
    $stdout = Join-Path $OutDir 'post-upgrade-db-revision.psql.stdout.log'
    $stderr = Join-Path $OutDir 'post-upgrade-db-revision.psql.stderr.log'
    try {
      foreach ($sql in $sqlCandidates) {
        $argv = @(
            '--host', $conn.Host, '--port', "$($conn.Port)",
            '--username', [uri]::UnescapeDataString($userInfo[0]),
            '--dbname', $conn.AbsolutePath.TrimStart('/'),
            '--no-password', '--tuples-only', '--no-align',
            # <gate-a-psql-quote> Start-Process joins -ArgumentList with spaces and
            # does NOT quote elements, so the SQL arrived as nine separate arguments:
            # psql ran a bare `SELECT`, warned 'extra command-line argument ... ignored'
            # for the rest, exited 0 with no rows, and the proof judged
            # '<no-alembic-version-row>' (Gate A 33870994702, kit c27c6e7). Quote it.
            '--set', 'ON_ERROR_STOP=1', '-c', ('"' + $sql + '"')
        )
        $p = Start-Process -FilePath $psql -ArgumentList $argv -PassThru -NoNewWindow -Wait `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        $probe.exit_code = $p.ExitCode
        $errText = (Get-Content -LiteralPath $stderr -Raw -ErrorAction SilentlyContinue)
        if ($p.ExitCode -eq 0 -and $errText -match 'extra command-line argument') {
            # The SQL did not reach the server intact; never read an exit-0 result as a proof.
            $probe.exit_code = $p.ExitCode
            $probe.revision = '<psql-args-split>'
            $probe.error = 'psql ignored extra command-line arguments -- the SQL was split'
        } elseif ($p.ExitCode -eq 0) {
            $out = (Get-Content -LiteralPath $stdout -Raw -ErrorAction SilentlyContinue)
            if ($null -ne $out) { $out = $out.Trim() }
            if (-not [string]::IsNullOrWhiteSpace($out)) {
                $probe.revision = ($out -split "`n" | Select-Object -First 1).Trim()
                break
            } else {
                $probe.revision = '<no-alembic-version-row>'
            }
        } else {
            $probe.revision = '<psql-failed>'
            $probe.error = "psql exited $($p.ExitCode)"
            if ($errText -match 'does not exist') { continue }  # try the next namespace
        }
      }
    } catch {
        $probe.revision = '<psql-threw>'
        $probe.error = "$_"
    } finally {
        Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
    }
    if ($LogFile) {
        "POST_UPGRADE_DB_REVISION_SOURCE=psql exit=$($probe.exit_code) revision=$($probe.revision)" |
            Add-Content -Path $LogFile -Encoding UTF8
    }
    return $probe
}

# The BUILD-time expected migration head <gate-a-audit-BL-10>, dropped into
# the mapped output directory by the host (Run-GateA.ps1 -ExpectedMigrationHead
# writes it the same way SOAK_MINUTES.txt / DIRTY_MODE.txt already ride in).
# It is computed on the RUNNER from the candidate's own checked-out source, so
# it does not come from the station under test.
function Get-CivicCastKitExpectedHead {
    $path = Join-Path $OutDir 'EXPECTED-MIGRATION-HEAD.txt'
    if (-not (Test-Path -LiteralPath $path)) { return '<not-provided>' }
    try {
        $value = (Get-Content -LiteralPath $path -ErrorAction Stop | Select-Object -First 1)
        if ($null -ne $value) { $value = $value.Trim() }
        if ([string]::IsNullOrWhiteSpace($value)) { return '<empty>' }
        return $value
    } catch {
        return '<read-error>'
    }
}

# Windows Firewall pre-authorization <gate-a-batch-item-1>.
#
# THE BUG THIS EXISTS FOR. On every install test the operator saw a Windows
# Security dialog INSIDE the sandbox -- "Do you want to allow public and
# private networks to access this app? TSDuck (tsp.exe)" -- raised by the
# Windows Defender Firewall the first time `tsp -I ip <port>` opened its
# listening socket for the egress verify. The prompt is modal, it is on the
# guest desktop nobody is watching, and it makes every Gate A run look like
# it needs a human. This harness already runs ELEVATED in the sandbox, so it
# can simply author the allow rules itself before the first bind.
#
# `netsh advfirewall firewall add rule` is idempotent in the sense that
# matters here (a duplicate rule name adds a second identical rule rather
# than failing), but we delete by name first anyway so a re-run does not
# accumulate. Both directions are authored: `dir=in` is the one that raises
# the prompt for a listening socket, `dir=out` is authored for symmetry so a
# future outbound-blocking profile cannot reintroduce the dialog. Failure to
# author a rule is recorded and NEVER fatal -- the firewall is a convenience
# here, not part of the acceptance contract.
function Add-CivicCastFirewallAllowRule {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$ProgramPath,
        [string]$ResultFile
    )
    $note = ''
    if ([string]::IsNullOrWhiteSpace($ProgramPath) -or -not (Test-Path -LiteralPath $ProgramPath)) {
        $note = "firewall_rule name=$Name program=$ProgramPath result=SKIPPED(program-not-found)"
        if ($ResultFile) { $note | Add-Content -Path $ResultFile -Encoding UTF8 }
        return $false
    }
    $resolved = (Resolve-Path -LiteralPath $ProgramPath).ProviderPath
    $ok = $true
    try {
        & netsh.exe advfirewall firewall delete rule name="$Name" 2>&1 | Out-Null
        foreach ($direction in @('in', 'out')) {
            $out = & netsh.exe advfirewall firewall add rule name="$Name" dir=$direction action=allow program="$resolved" enable=yes profile=any 2>&1
            if ($LASTEXITCODE -ne 0) {
                $ok = $false
                $note += "firewall_rule name=$Name dir=$direction result=FAILED exit=$LASTEXITCODE detail=$($out -join ' ')`n"
            }
        }
    } catch {
        $ok = $false
        $note += "firewall_rule name=$Name result=THREW detail=$_`n"
    }
    if ($ok) { $note = "firewall_rule name=$Name program=$resolved dir=in+out result=ALLOWED" }
    if ($ResultFile) { $note.TrimEnd() | Add-Content -Path $ResultFile -Encoding UTF8 }
    return $ok
}

# TSDuck tsp verification -- same command pattern as the kit's own
# verify-egress.ps1 (-I ip <port> -P until --seconds N -P analyze --json),
# reimplemented standalone here because verify-egress.ps1 hardcodes a fixed
# 3-channel/3-port set (public/education/government @ 9001-9003) and T4's
# product-engine attempt targets one channel on a dedicated port so it can
# never collide with the synthetic-ffmpeg fallback's fixed ports.
function Test-TsProof {
    param([string]$TspExe, [int]$Port, [int]$Seconds, [string]$OutDir, [string]$Label)
    $result = [ordered]@{
        label = $Label; port = $Port; tsp_found = $false; ran = $false; timed_out = $false
        exit_code = $null; report_found = $false; report_bytes = $null; packets_total = $null
        invalid_syncs = $null; transport_errors = $null; discontinuities = $null
        verdict = 'not-run'
    }
    if (-not $TspExe -or -not (Test-Path $TspExe)) {
        $result.verdict = 'not-run: tsp.exe not found'
        return $result
    }
    $result.tsp_found = $true
    $report = Join-Path $OutDir "tsduck-$Label-report.json"
    $stdout = Join-Path $OutDir "tsduck-$Label.stdout.log"
    $stderr = Join-Path $OutDir "tsduck-$Label.stderr.log"
    $tspArgs = @('-I','ip',"$Port",'--buffer-size','16777216','-P','until','--seconds',"$Seconds",'-P','analyze','--json','--output-file',$report,'-O','drop')
    try {
        $proc = Start-Process -FilePath $TspExe -ArgumentList $tspArgs -PassThru -NoNewWindow -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        # <gate-a-tsp-exit-code> Windows PowerShell 5.1 returns $null for
        # $proc.ExitCode unless the process HANDLE was cached before the
        # process exited (Start-Process -PassThru returns a lazy object;
        # Wait-Process -Id does not cache it). Gate A run 33826665417
        # (kit 4b30c99, 2026-09-04) captured a full 1229-packet report from
        # the engine and still recorded exit_code=null -> verdict
        # 'fail-exit-' -> the report was never read. Touch the handle and
        # wait on the object itself so the exit code is always real.
        $null = $proc.Handle
        $timeoutSec = $Seconds + 20
        $exited = $proc.WaitForExit($timeoutSec * 1000)
        if (-not $exited) {
            $result.timed_out = $true
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            $null = $proc.WaitForExit(5000)
        }
        $result.ran = $true
        if (-not $result.timed_out) { $result.exit_code = $proc.ExitCode }
    } catch {
        $result.verdict = "error: $_"
        return $result
    }
    # <gate-a-audit-MA-27> A tsp run that TIMED OUT or exited nonzero analysed
    # nothing, whatever it left on disk. Judge the RUN before the report.
    if ($result.timed_out) {
        $result.verdict = 'fail-timed-out'
        return $result
    }
    if ($null -eq $result.exit_code -or [int]$result.exit_code -ne 0) {
        $result.verdict = "fail-exit-$($result.exit_code)"
        return $result
    }
    if (-not (Test-Path $report)) {
        $result.verdict = 'fail-no-report'
        return $result
    }
    $result.report_found = $true
    # <gate-a-audit-MA-27> `Get-Content -Raw` on a 0-byte file returns $null,
    # and PowerShell's pipeline DROPS $null -- so `... | ConvertFrom-Json`
    # never ran, never threw, $j stayed $null, the three counters stayed at
    # their initialised 0, and the verdict was 'pass'. Gate A run 33681670855
    # shipped exactly that: a 0-byte tsduck-engine-government-report.json,
    # `configuration file 'dtv' not found` on stderr, verdict=pass, and zero
    # transport stream analysed. Read into a variable first and fail on empty.
    $raw = $null
    try { $raw = Get-Content -Raw -LiteralPath $report -ErrorAction Stop } catch {
        $result.verdict = "report-read-error: $_"
        return $result
    }
    $result.report_bytes = if ($null -eq $raw) { 0 } else { $raw.Length }
    if ([string]::IsNullOrWhiteSpace($raw)) {
        $result.verdict = 'fail-empty-report'
        return $result
    }
    try {
        $j = $raw | ConvertFrom-Json -ErrorAction Stop
    } catch {
        $result.verdict = "report-parse-error: $_"
        return $result
    }
    if (-not $j -or -not $j.ts -or -not $j.ts.packets) {
        $result.verdict = 'fail-no-ts-packets-node'
        return $result
    }
    # A ts.packets node with no positive total count is a report about
    # nothing. `total` is TSDuck's own analyze field name; fall back to
    # summing the per-PID counts when a future schema drops it.
    $totalPackets = 0
    if ($j.ts.packets.PSObject.Properties.Name -contains 'total') {
        $totalPackets = [int]$j.ts.packets.total
    } else {
        foreach ($pidRow in @($j.pids)) {
            if ($pidRow.packets -and ($pidRow.packets.PSObject.Properties.Name -contains 'total')) {
                $totalPackets += [int]$pidRow.packets.total
            }
        }
    }
    $result.packets_total = $totalPackets
    if ($totalPackets -le 0) {
        $result.verdict = 'fail-zero-packets'
        return $result
    }
    $invalidSyncs = 0
    $transportErrors = 0
    if ($j.ts.packets.PSObject.Properties.Name -contains 'invalid-syncs') { $invalidSyncs = [int]$j.ts.packets.'invalid-syncs' }
    if ($j.ts.packets.PSObject.Properties.Name -contains 'transport-errors') { $transportErrors = [int]$j.ts.packets.'transport-errors' }
    $disc = 0
    foreach ($pidRow in @($j.pids)) {
        if ($pidRow.packets -and ($pidRow.packets.PSObject.Properties.Name -contains 'discontinuities')) { $disc += [int]$pidRow.packets.discontinuities }
    }
    $result.invalid_syncs = $invalidSyncs
    $result.transport_errors = $transportErrors
    $result.discontinuities = $disc
    if ($invalidSyncs -eq 0 -and $transportErrors -eq 0 -and $disc -eq 0) { $result.verdict = 'pass' } else { $result.verdict = 'fail' }
    return $result
}

# ============================================================================
# DIRTY-BOX REMNANT PROLOGUE <gate-a-dirty-lane>. Runs ONLY when the host
# opted this run into the dirty lane (see the DIRTY_MODE.txt block near the
# top of this file). Everything here happens BEFORE the normal acceptance
# flow, which then runs unchanged against the remnant-carrying box:
#
#   P1. Install either the pinned previous candidate (upgrade mode) or the
#       current candidate (legacy remnant mode), using the same silent
#       command as the normal flow.
#       The install-time provision creates a REAL PostgreSQL cluster at
#       %ProgramData%\CivicCast\data\pgdata -- not a hand-faked seed.
#   P2. Plant operator data: two media files into
#       %ProgramData%\CivicCast\data\uploads (install_layout.upload_dir),
#       recording their SHA-256 so phase 2 can prove byte survival.
#   P3. Record the pgdata cluster identity (PG_VERSION content + the
#       directory's CreationTimeUtc) so phase 2 can prove the SAME cluster
#       survived rather than a re-provisioned lookalike.
#   P4. Stop the CivicCastSupervisor service (bounded) and run the REAL
#       uninstaller silently. `_?=` keeps NSIS running in place so -Wait and
#       the exit code are meaningful; its one side effect -- the uninstaller
#       cannot delete its own exe -- is cleaned up afterwards (bounded),
#       because on a customer machine the copy-to-%TEMP% flow removes it.
#   P5. Verify the uninstaller's preservation contract
#       (nsis-hooks-bootstrap.nsh: "$COMMONPROGRAMDATA\CivicCast ...
#       preserved across uninstall by design"): pgdata and the planted
#       uploads must still be there; the install tree must be gone.
#   P6. OPTIONAL orphaned-caption-tier seed: if the host staged a
#       hash-valid large-v3 model at C:\CivicCastHostStore\dirty-seed\
#       captions-large-v3, mirror it to %ProgramData%\CivicCast\components\
#       captions-large-v3 WITHOUT any ProgramData activation receipt --
#       byte-for-byte the DESKTOP-2BR3SJR #17/#18 remnant shape whose
#       receipt-path crash PR #80 fixed. A stub is NOT seeded when the real
#       model is absent: `_resolve_caption_tier` validates the selected
#       tier's pinned SHA-256 set BEFORE the receipt is read, so an
#       incomplete model dir reproduces a deliberate fail-closed crash, not
#       the orphaned-receipt degrade path this lane exists to prove.
#       DIRTY-PREP-RESULT.txt records seeded=0 and the judge reports that
#       sub-shape as SKIP (loudly), never as a silent pass.
#
# Every hoststore (mapped-folder) access is bounded via Invoke-BoundedProbe /
# Invoke-BoundedProcess, per this file's <gate-a-hoststore-wedge> discipline.
# ProgramData paths are the sandbox's own local disk and are safe to touch
# directly. Failures are recorded, never thrown: a broken prologue must still
# leave a judgeable evidence trail (the judge fails the dirty lane on the
# recorded lines, and the normal flow still runs for forensics).
# ============================================================================
$script:DirtyPgdataCreationUtc = $null
$script:DirtyPgVersion = $null
$script:DirtyUploadHashes = @{}
function Invoke-DirtyRemnantPrologue {
    $prep = Join-Path $OutDir 'DIRTY-PREP-RESULT.txt'
    "dirty_prep_started_utc=$((Get-Date).ToUniversalTime().ToString('o'))" | Set-Content -Path $prep -Encoding UTF8
    $pd = Join-Path $env:ProgramData 'CivicCast'
    $installRoot = 'C:\CivicCastHostStore\install'

    # P1. Phase-1 install. Cross-version mode uses the separately mapped,
    # immutable previous full kit; legacy remnant mode keeps the historical
    # same-candidate install/uninstall shape.
    $phase1Payload = if ($script:UpgradeMode) { 'C:\CivicCastPreviousPayload' } else { $PayloadDir }
    $dirtyExe = Get-ChildItem -Path $phase1Payload -Filter '*setup.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $dirtyExe) {
        "PHASE1_INSTALL_EXIT=-999 (no *setup.exe found in $phase1Payload)" | Add-Content -Path $prep -Encoding UTF8
        Save-Summary -Step 'dirty-prep-no-installer'
        return
    }
    if ($script:UpgradeMode) {
        $currentUpgradeExe = Get-ChildItem -Path $PayloadDir -Filter '*setup.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
        "UPGRADE_MODE=1" | Add-Content -Path $prep -Encoding UTF8
        "UPGRADE_OVER_LIVE_REQUESTED=1" | Add-Content -Path $prep -Encoding UTF8
        if (-not $currentUpgradeExe) {
            "CURRENT_INSTALLER_SHA256=<missing>" | Add-Content -Path $prep -Encoding UTF8
            "PHASE1_INSTALL_EXIT=-999 (no current *setup.exe found in $PayloadDir)" | Add-Content -Path $prep -Encoding UTF8
            return
        }
        try { $previousInstallerHash = (Get-FileHash -LiteralPath $dirtyExe.FullName -Algorithm SHA256 -ErrorAction Stop).Hash.ToLower() } catch { $previousInstallerHash = "<hash-error: $_>" }
        try { $currentInstallerHash = (Get-FileHash -LiteralPath $currentUpgradeExe.FullName -Algorithm SHA256 -ErrorAction Stop).Hash.ToLower() } catch { $currentInstallerHash = "<hash-error: $_>" }
        try { $currentProductVersion = ([System.Diagnostics.FileVersionInfo]::GetVersionInfo($currentUpgradeExe.FullName).ProductVersion).Trim() } catch { $currentProductVersion = "<version-error: $_>" }
        "PREVIOUS_INSTALLER_SHA256=$previousInstallerHash" | Add-Content -Path $prep -Encoding UTF8
        "CURRENT_INSTALLER_SHA256=$currentInstallerHash" | Add-Content -Path $prep -Encoding UTF8
        "CURRENT_PRODUCT_VERSION=$currentProductVersion" | Add-Content -Path $prep -Encoding UTF8
        if ($previousInstallerHash -eq $currentInstallerHash) {
            "PHASE1_INSTALL_EXIT=-996 (previous and current installer identities are identical)" | Add-Content -Path $prep -Encoding UTF8
            return
        }
    }
    Enter-ShipperQuiesce -Reason 'dirty-lane-phase1-install' -MaxMinutes 120
    Save-Summary -Step 'dirty-prep-phase1-install-begin'
    $phase1Exit = $null
    try {
        $p1 = Start-Process -FilePath $dirtyExe.FullName -ArgumentList '/S /D=C:\CivicCastHostStore\install' -PassThru -Wait -WindowStyle Hidden
        $phase1Exit = $p1.ExitCode
    } catch {
        "phase1_install_launch_error=$_" | Add-Content -Path $prep -Encoding UTF8
    }
    Exit-ShipperQuiesce
    "PHASE1_INSTALL_EXIT=$(if ($null -ne $phase1Exit) { $phase1Exit } else { -998 })" | Add-Content -Path $prep -Encoding UTF8
    Save-Summary -Step 'dirty-prep-phase1-install-done'
    if ($phase1Exit -ne 0) {
        "DIRTY_PREP=FAIL (phase-1 install did not exit 0 -- continuing into the normal flow for forensics only)" | Add-Content -Path $prep -Encoding UTF8
        return
    }

    if ($script:UpgradeMode) {
        try {
            $previousProductVersion = (Get-ItemPropertyValue -LiteralPath 'HKLM:\Software\CivicCast\Native' -Name 'InstalledVersion' -ErrorAction Stop).Trim()
        } catch {
            $previousProductVersion = "<registry-error: $_>"
        }
        "PREVIOUS_PRODUCT_VERSION=$previousProductVersion" | Add-Content -Path $prep -Encoding UTF8
        if ($previousProductVersion -eq $currentProductVersion) {
            "DIRTY_PREP=FAIL (previous and current InstalledVersion identities are identical; D3 would route SAME_VERSION_NO_OP)" | Add-Content -Path $prep -Encoding UTF8
            return
        }
    }

    # P2. Plant operator uploads (install_layout.upload_dir =
    # <ProgramData>\CivicCast\data\uploads). Source media is the mapped
    # scripts folder's real sample clip -- never a synthetic file.
    $uploads = Join-Path $pd 'data\uploads'
    try { New-Item -ItemType Directory -Force -Path $uploads | Out-Null } catch {}
    $sample = 'C:\CivicCastScripts\lpm-sample-short.mp4'
    foreach ($name in @('dirty-remnant-recording-1.mp4', 'dirty-remnant-recording-2.mp4')) {
        try {
            Copy-Item -LiteralPath $sample -Destination (Join-Path $uploads $name) -Force -ErrorAction Stop
            $h = (Get-FileHash -Path (Join-Path $uploads $name) -Algorithm SHA256).Hash.ToLower()
            $script:DirtyUploadHashes[$name] = $h
            "UPLOAD_PLANTED name=$name sha256=$h" | Add-Content -Path $prep -Encoding UTF8
        } catch {
            "UPLOAD_PLANT_FAILED name=$name error=$_" | Add-Content -Path $prep -Encoding UTF8
        }
    }
    Save-Summary -Step 'dirty-prep-uploads-planted'

    # P3. Record the pgdata cluster identity.
    $pgdata = Join-Path $pd 'data\pgdata'
    if (Test-Path $pgdata) {
        try { $script:DirtyPgdataCreationUtc = (Get-Item -LiteralPath $pgdata -Force).CreationTimeUtc.ToString('o') } catch {}
        try { $script:DirtyPgVersion = (Get-Content -LiteralPath (Join-Path $pgdata 'PG_VERSION') -Raw -ErrorAction Stop).Trim() } catch {}
        "PGDATA_PRESENT_AFTER_PHASE1=1 creation_utc=$($script:DirtyPgdataCreationUtc) pg_version=$($script:DirtyPgVersion)" | Add-Content -Path $prep -Encoding UTF8
    } else {
        "PGDATA_PRESENT_AFTER_PHASE1=0 (no cluster at $pgdata after a phase-1 install that exited 0)" | Add-Content -Path $prep -Encoding UTF8
    }
    Save-Summary -Step 'dirty-prep-pgdata-recorded'

    if ($script:UpgradeMode) {
        # Leave the previous candidate installed and running. The normal
        # acceptance flow below now invokes the current setup directly over
        # this live install; the installer owns graceful quiescence/migration.
        "dirty_prep_finished_utc=$((Get-Date).ToUniversalTime().ToString('o'))" | Add-Content -Path $prep -Encoding UTF8
        Save-Summary -Step 'dirty-prep-cross-version-live'
        return
    }

    if (-not $script:UpgradeMode) {
        Write-Marker -Name '_DIRTY-LEGACY-REMNANT.marker' -Content 'legacy uninstall-remnant path selected'
    }

    # P4. Stop the service (bounded poll), then run the real uninstaller.
    try { & sc.exe stop CivicCastSupervisor 2>&1 | Out-Null } catch {}
    $stopDeadline = (Get-Date).AddSeconds(120)
    $svcState = 'unknown'
    while ((Get-Date) -lt $stopDeadline) {
        $q = (& sc.exe query CivicCastSupervisor 2>&1 | Out-String)
        if ($q -match 'STOPPED') { $svcState = 'stopped'; break }
        if ($q -match '1060') { $svcState = 'not-installed'; break }
        Start-Sleep -Seconds 5
    }
    "SERVICE_STATE_BEFORE_UNINSTALL=$svcState" | Add-Content -Path $prep -Encoding UTF8
    Save-Summary -Step 'dirty-prep-service-stopped'

    $uninstProbe = Invoke-BoundedProbe -Name 'dirty-uninstaller-present' -TimeoutSeconds 60 -Arguments @{ Path = (Join-Path $installRoot 'uninstall.exe') } -ScriptText @'
@{ items = @([bool](Test-Path $ProbeArgs['Path'])) } | ConvertTo-Json -Depth 3 | Set-Content -Path $ResultPath -Encoding UTF8
'@
    $uninstPresent = ($null -ne $uninstProbe) -and (@($uninstProbe.items)[0] -eq $true)
    if (-not $uninstPresent) {
        "UNINSTALL_EXIT=-999 (uninstall.exe not found under $installRoot)" | Add-Content -Path $prep -Encoding UTF8
        Save-Summary -Step 'dirty-prep-no-uninstaller'
        return
    }
    # 45-minute bound: the uninstaller's RMDir /r walks the ~12 GB, 10k-file
    # install tree ACROSS VSMB (the same transport whose per-file cost run7
    # measured at 1.6-4.2x) -- a bound sized for a local disk would kill a
    # healthy uninstall mid-walk and report a false remnant-contract FAIL.
    Enter-ShipperQuiesce -Reason 'dirty-lane-uninstall' -MaxMinutes 50
    $uninstallRun = Invoke-BoundedProcess -FilePath (Join-Path $installRoot 'uninstall.exe') -ArgumentList @('/S', "_?=$installRoot") -TimeoutSeconds 2700
    Exit-ShipperQuiesce
    $uninstallExit = if ($uninstallRun.completed) { $uninstallRun.exit_code } else { -997 }
    "UNINSTALL_EXIT=$uninstallExit uninstall_completed=$($uninstallRun.completed) uninstall_error=$($uninstallRun.error)" | Add-Content -Path $prep -Encoding UTF8
    Save-Summary -Step 'dirty-prep-uninstall-done'

    # `_?=` means the uninstaller could not delete its own exe (it schedules
    # a delete-on-reboot that never happens in a sandbox). On a customer
    # machine the copy-to-%TEMP% flow leaves no such file, so remove this
    # harness-induced artifact -- and ONLY it -- before phase 2, or the
    # phase-2 installer would see a tree shape no real machine has.
    $cleanupProbe = Invoke-BoundedProbe -Name 'dirty-uninstaller-cleanup' -TimeoutSeconds 60 -Arguments @{ Root = $installRoot } -ScriptText @'
$removed = @()
try {
    $u = Join-Path $ProbeArgs['Root'] 'uninstall.exe'
    if (Test-Path $u) { Remove-Item -LiteralPath $u -Force -ErrorAction Stop; $removed += 'uninstall.exe' }
    $items = @(Get-ChildItem -LiteralPath $ProbeArgs['Root'] -Force -ErrorAction SilentlyContinue)
    if ($items.Count -eq 0) { Remove-Item -LiteralPath $ProbeArgs['Root'] -Force -ErrorAction SilentlyContinue; $removed += '<empty install root>' }
    $removed += "leftover_entries=$((@(Get-ChildItem -LiteralPath $ProbeArgs['Root'] -Force -ErrorAction SilentlyContinue) | Select-Object -ExpandProperty Name) -join ';')"
} catch { $removed += "cleanup_error=$_" }
@{ items = @($removed) } | ConvertTo-Json -Depth 3 | Set-Content -Path $ResultPath -Encoding UTF8
'@
    "UNINSTALLER_SELF_CLEANUP=$(if ($cleanupProbe) { @($cleanupProbe.items) -join ' ' } else { '<probe did not complete>' })" | Add-Content -Path $prep -Encoding UTF8

    # P5. Preservation contract after uninstall.
    $pgPreserved = [int](Test-Path $pgdata)
    $uploadsPreserved = 1
    foreach ($name in $script:DirtyUploadHashes.Keys) {
        if (-not (Test-Path (Join-Path $uploads $name))) { $uploadsPreserved = 0 }
    }
    "PGDATA_PRESERVED_AFTER_UNINSTALL=$pgPreserved" | Add-Content -Path $prep -Encoding UTF8
    "UPLOADS_PRESERVED_AFTER_UNINSTALL=$uploadsPreserved" | Add-Content -Path $prep -Encoding UTF8
    $treeProbe = Invoke-BoundedProbe -Name 'dirty-installtree-gone' -TimeoutSeconds 60 -Arguments @{ Root = $installRoot } -ScriptText @'
$gone = 1
foreach ($sub in @('runtime', 'packs')) {
    if (Test-Path (Join-Path $ProbeArgs['Root'] $sub)) { $gone = 0 }
}
@{ items = @($gone) } | ConvertTo-Json -Depth 3 | Set-Content -Path $ResultPath -Encoding UTF8
'@
    $treeGone = if ($treeProbe) { @($treeProbe.items)[0] } else { '<probe did not complete>' }
    "INSTALL_TREE_REMOVED_AFTER_UNINSTALL=$treeGone" | Add-Content -Path $prep -Encoding UTF8
    Save-Summary -Step 'dirty-prep-preservation-checked'

    # P6. Optional orphaned large-v3 seed.
    $orphanSeeded = 0
    $seedProbe = Invoke-BoundedProbe -Name 'dirty-orphan-seed-present' -TimeoutSeconds 60 -Arguments @{ Path = 'C:\CivicCastHostStore\dirty-seed\captions-large-v3\models' } -ScriptText @'
@{ items = @([bool](Test-Path $ProbeArgs['Path'])) } | ConvertTo-Json -Depth 3 | Set-Content -Path $ResultPath -Encoding UTF8
'@
    if (($null -ne $seedProbe) -and (@($seedProbe.items)[0] -eq $true)) {
        $seedDst = Join-Path $pd 'components\captions-large-v3'
        $seedCopy = Invoke-BoundedProcess -FilePath 'robocopy.exe' -ArgumentList @(
            'C:\CivicCastHostStore\dirty-seed\captions-large-v3', $seedDst, '/E', '/R:0', '/W:0', '/NFL', '/NDL', '/NJH', '/NJS', '/NP'
        ) -TimeoutSeconds 1800
        # Robocopy exit codes < 8 are success family.
        if ($seedCopy.completed -and ($seedCopy.exit_code -lt 8)) { $orphanSeeded = 1 }
        "ORPHAN_SEED_COPY completed=$($seedCopy.completed) exit=$($seedCopy.exit_code) error=$($seedCopy.error)" | Add-Content -Path $prep -Encoding UTF8
    } else {
        "ORPHAN_SEED_SOURCE_ABSENT=1 (no hash-valid large-v3 model staged by the host -- orphaned-tier remnant shape NOT covered this run; see docs/ops/gate-a.md, 'Dirty lane')" | Add-Content -Path $prep -Encoding UTF8
    }
    # The orphan shape requires the ProgramData receipt to be ABSENT. It is
    # never deleted here -- if a phase-1 install legitimately wrote one, that
    # is recorded and the judge surfaces the discrepancy instead of the
    # harness authoring the shape it wants.
    "RECEIPT_PRESENT_IN_PROGRAMDATA=$([int](Test-Path (Join-Path $pd 'activation-self-test.json')))" | Add-Content -Path $prep -Encoding UTF8
    "DIRTY_ORPHAN_SEEDED=$orphanSeeded" | Add-Content -Path $prep -Encoding UTF8
    "dirty_prep_finished_utc=$((Get-Date).ToUniversalTime().ToString('o'))" | Add-Content -Path $prep -Encoding UTF8
    Save-Summary -Step 'dirty-prep-complete'
}

try {
    if ($script:DirtyMode) {
        Invoke-DirtyRemnantPrologue
        Sync-Transcript -Checkpoint 'dirty-prologue'
    }

    # 0. SKIP-REINSTALL MODE -- EXPERIMENTAL, OPT-IN ONLY, GATED (2026-08-19).
    #    A live run proved this mode's core premise wrong: station_up stayed
    #    False because CivicCastSupervisor never existed to answer on 8000.
    #    Root-caused afterward (static discovery, no further launches): the
    #    Windows service registration, HKLM\SOFTWARE\CivicCast\Native\
    #    DatabaseUrl / SetupNonce, AND the PostgreSQL/NATS data directories
    #    themselves ALL live under %ProgramData%\CivicCast and the registry --
    #    both on the Sandbox's own OS disk, which resets EVERY session. Only
    #    C:\CivicCastHostStore\install (a MappedFolder) survives. So
    #    station-set.json existing there proves activation/model-staging
    #    finished once; it does NOT prove postgres/service state is
    #    recoverable this session -- restoring that would need the real
    #    installer's provisioning step, which itself needs an installer-only
    #    Ed25519 pack public key this harness has no safe way to obtain (see
    #    PREFLIGHT.md next to this script for the full writeup). Given that,
    #    and per the coordinator's explicit "stop discovering serially, gate
    #    it" decision: this path is now OFF BY DEFAULT. The proven, working
    #    full-install path (installer + activation, every run) is what
    #    actually runs unless BOTH an explicit opt-in marker AND the
    #    persistence signal are present -- so an ordinary run can never be
    #    silently short-circuited by a stray leftover station-set.json.
    $PersistentInstallDir = 'C:\CivicCastHostStore\install'
    $SkipModeOptInFile = Join-Path $OutDir 'SKIP_MODE.txt'
    $skipModeOptedIn = Test-Path $SkipModeOptInFile
    $stationSetPresent = $false
    try {
        if (Test-Path (Join-Path $PersistentInstallDir 'station-set.json')) { $stationSetPresent = $true }
    } catch {}
    $skipReinstall = $skipModeOptedIn -and $stationSetPresent
    $summary.skip_mode_opted_in = $skipModeOptedIn
    $summary.skip_mode_station_set_present = $stationSetPresent
    $summary.skip_reinstall = $skipReinstall
    $installResultFile = Join-Path $OutDir 'INSTALL-RESULT.txt'

    if ($skipReinstall) {
        "INSTALL=SKIPPED_PERSISTENT_EXPERIMENTAL install_dir=$PersistentInstallDir checked_utc=$((Get-Date).ToUniversalTime().ToString('o'))" | Set-Content -Path $installResultFile -Encoding UTF8
        "opt_in_file=$SkipModeOptInFile station_set_present=$stationSetPresent" | Add-Content -Path $installResultFile -Encoding UTF8
        Write-Marker -Name '_SKIP_REINSTALL.marker' -Content (Get-Date).ToString('o')
        $summary.installer_source = "SKIPPED (SKIP_MODE.txt opt-in + persistent station-set.json found at $PersistentInstallDir)"
        $summary.silent_flag_used = 'SKIPPED_PERSISTENT_EXPERIMENTAL'
        $summary.installer_exit_code = 'SKIPPED'
        # No fresh install ran this session -- there is no real "just
        # installed" boot to time. Use "now" so station_boot_seconds still
        # reports a bounded, non-null number (time from re-registration
        # attempt to health) instead of silently going null in this mode.
        $script:AfterInstallTime = Get-Date
        Save-Summary -Step 'install-skipped-persistent'

        # EXPERIMENTAL, best-effort, NON-BLOCKING: attempt to re-register the
        # CivicCastSupervisor Windows service against the persistent install
        # via its real, module-level SCM entry point (confirmed by reading
        # supervisor\service_host.py: `win32serviceutil.HandleCommandLine`,
        # the exact same mechanism pythonservice.exe uses). This ONLY
        # recreates the service object -- it does nothing about the missing
        # DatabaseUrl/postgres-data problem above, so it is expected to leave
        # the service unable to actually start in most sandbox sessions; it
        # is attempted anyway (cheap, ~seconds) in case a future run's
        # environment differs, and its result is informational only. Any
        # failure here is caught and logged, never thrown -- this step must
        # never block reaching the existing (unconditional, already-bounded)
        # service-state/start-attempt logic below.
        $expLog = Join-Path $OutDir 'SKIP-MODE-SERVICE-REREGISTER.txt'
        "attempted_utc=$((Get-Date).ToUniversalTime().ToString('o'))" | Set-Content -Path $expLog -Encoding UTF8
        try {
            $runtimePython = Join-Path $PersistentInstallDir 'runtime\python.exe'
            "runtime_python=$runtimePython present=$([bool](Test-Path $runtimePython))" | Add-Content -Path $expLog -Encoding UTF8
            if (Test-Path $runtimePython) {
                $regOut = Join-Path $OutDir 'SKIP-MODE-SERVICE-REREGISTER.stdout.log'
                $regErr = Join-Path $OutDir 'SKIP-MODE-SERVICE-REREGISTER.stderr.log'
                $regArgs = @('-m', 'civiccast.native.supervisor.service_host', 'install')
                $regProc = Start-Process -FilePath $runtimePython -ArgumentList $regArgs -PassThru -NoNewWindow -Wait `
                    -RedirectStandardOutput $regOut -RedirectStandardError $regErr -WorkingDirectory $PersistentInstallDir
                "service_host_install_exit_code=$($regProc.ExitCode)" | Add-Content -Path $expLog -Encoding UTF8
            } else {
                "SKIPPED: runtime python.exe not found -- cannot attempt registration" | Add-Content -Path $expLog -Encoding UTF8
            }
        } catch {
            "EXPERIMENTAL_REGISTRATION_ERROR (non-fatal, continuing): $_" | Add-Content -Path $expLog -Encoding UTF8
        }
        "note=this step is informational only; DatabaseUrl/postgres-data are still expected to be missing this session -- see PREFLIGHT.md" | Add-Content -Path $expLog -Encoding UTF8
        Save-Summary -Step 'skip-mode-experimental-service-reregister'
    } else {
        $skipGateNote = if ($skipModeOptedIn -and -not $stationSetPresent) {
            ' (SKIP_MODE.txt present but no persistent station-set.json found -- opt-in condition not met)'
        } elseif (-not $skipModeOptedIn -and $stationSetPresent) {
            ' (persistent station-set.json found but SKIP_MODE.txt opt-in not present -- full install runs by default)'
        } else {
            ''
        }
        "INSTALL=RAN_FRESH checked_utc=$((Get-Date).ToUniversalTime().ToString('o'))$skipGateNote" | Set-Content -Path $installResultFile -Encoding UTF8

        # 1. Run the installer DIRECTLY from the read-only host-mapped payload
        #    ($PayloadDir = C:\CivicCastPayload). The old harness copied the whole
        #    payload to a local writable stage first, but this kit carries ~22.9GB
        #    of station models -- copying it in, then letting the install stage the
        #    packs AND activation cache the station bundle, blew past the Sandbox's
        #    virtual disk (os error 112 "not enough space" during station-pack
        #    cache). The installer only READS $EXEDIR\packs and $EXEDIR\station and
        #    writes solely to $INSTDIR, so a read-only $EXEDIR is fine and saves the
        #    entire 22.9GB copy.
        $LocalInstallStage = $PayloadDir
        $exe = Get-ChildItem -Path $PayloadDir -Filter '*setup.exe' | Select-Object -First 1
        if (-not $exe) {
            throw "No *setup.exe found in mapped payload at $PayloadDir"
        }
        $summary.installer_source = $exe.FullName
        try {
            $summary.installer_sha256 = (Get-FileHash -Path $exe.FullName -Algorithm SHA256).Hash.ToLower()
        } catch { $summary.errors += "hash of staged exe failed: $_" }

        $packsDir = Join-Path $PayloadDir 'packs'
        $summary.errors += "packs dir present: $(Test-Path $packsDir); contents: $((Get-ChildItem $packsDir -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name) -join ', ')"

        # DOWNLOAD-ONLY LANE <gate-a-download-only-lane>: record BEFORE this
        # (phase-2, current-candidate) install runs that the payload really
        # carries no station\ -- Run-GateA.ps1 -DownloadOnlyLane is what
        # repoints $PayloadDir at the filtered directory, but the evidence
        # this lane's judge check reads must prove it from inside the
        # sandbox, not merely trust the host's own claim.
        if ($script:DownloadOnlyMode) {
            $dlOnlyResult = Join-Path $OutDir 'DOWNLOAD-ONLY-RESULT.txt'
            $stationDirPresent = [int](Test-Path (Join-Path $PayloadDir 'station'))
            "checked_utc=$((Get-Date).ToUniversalTime().ToString('o'))" | Set-Content -Path $dlOnlyResult -Encoding UTF8
            "PAYLOAD_DIR=$PayloadDir" | Add-Content -Path $dlOnlyResult -Encoding UTF8
            "STATION_DIR_PRESENT=$stationDirPresent" | Add-Content -Path $dlOnlyResult -Encoding UTF8
            Save-Summary -Step 'download-only-payload-checked'
        }
        Save-Summary -Step 'staged-payload'

        # 2. Run the installer silently. Tauri/NSIS convention is uppercase /S for
        #    silent mode (no UI, no prompts, default install dir). Capture the raw
        #    process exit code -- nsis-hooks-bootstrap.nsh's CIVICCAST_FAIL macro
        #    (SetErrorLevel + Abort) makes the process exit code a real, meaningful
        #    signal of which postinstall step failed (110=pack delivery,
        #    111/112/121/122=D2 verify, 116-119=D4 provision/service/firewall,
        #    120=upgrade quiesce failure, 123=D4 activation [K1], etc).
        # /D= sets the NSIS install directory to the WRITABLE host-mapped folder
        # (C:\CivicCastHostStore\install, backed by the host's 1.2TB) so the WHOLE
        # install -- stage-packs, provision (which creates the dependencies/
        # junctions in place), and activation (which stages ~40GB of models and
        # REFUSES junction/symlink install-roots) -- runs natively on real disk
        # instead of the Sandbox's ~40GB virtual C:. /D must be the LAST argument
        # and unquoted (NSIS quirk); the path has no spaces so this is clean.
        $summary.silent_flag_used = '/S /D=C:\CivicCastHostStore\install'
        # <gate-a-audit-MA-23> Mark the append-only install-progress.log HERE,
        # before this phase's setup.exe runs, so the capture below can never
        # read a previous phase's d3-engine/d4-activate/postinstall lines as
        # this one's. Absent log => mark 0, which is correct (nothing to skip).
        $script:InstallProgressPhase2Mark = 0
        try {
            $preLog = Join-Path $env:ProgramData 'CivicCast\install-progress.log'
            if (Test-Path -LiteralPath $preLog) {
                $script:InstallProgressPhase2Mark = @(Get-Content -LiteralPath $preLog -ErrorAction Stop).Count
            }
        } catch {
            $summary.errors += "install-progress phase mark failed: $_"
        }
        $summary.install_progress_phase_mark = $script:InstallProgressPhase2Mark
        Write-Marker -Name '_BEFORE_INSTALL.marker' -Content (Get-Date).ToString('o')
        # QUIESCE the shipper for the duration of the install. The installer
        # reads ~21 GB from C:\CivicCastPayload and writes ~12 GB to
        # C:\CivicCastHostStore, both over the same VSMB transport the
        # shipper's destination rides; run7 measured 1.6-4.2x slowdowns on
        # exactly those steps with a 25s tick running underneath. Nothing of
        # value is written locally during the install anyway.
        # Covers the installer AND the post-install discovery that follows it
        # <gate-a-hoststore-wedge>. 120 minutes rather than 90 because the
        # window is longer now; the marker's own expiry remains the backstop
        # against a lift that never happens, and the station-up wait lifts it
        # explicitly long before then on every real run.
        Enter-ShipperQuiesce -Reason 'installer-and-post-install-discovery' -MaxMinutes 120
        try {
            $proc = Start-Process -FilePath $exe.FullName -ArgumentList '/S /D=C:\CivicCastHostStore\install' -PassThru -Wait -WindowStyle Hidden
            $summary.installer_exit_code = $proc.ExitCode
        } catch {
            $summary.installer_launch_error = "$_"
            $summary.errors += "installer launch/wait failed: $_"
        }
        # NOTE <gate-a-hoststore-wedge>: the quiesce is deliberately NOT lifted
        # here any more. It used to be, in a `finally` attached to the install
        # itself, which put the 25s tick back underneath the post-install
        # phase -- and that phase is at least as VSMB-heavy as the install:
        # install-dir discovery, the install-tree listing, Test-KnownPaths, the
        # station-set.json / activation-self-test.json reads, and the service
        # checks all read C:\CivicCastHostStore, where 10,683 files now live.
        # The installer's own last step spent 3 minutes just MEASURING that
        # tree (see "EstimatedSize corrected" in install-progress.log). The
        # quiesce is now lifted at the station-up wait instead -- see
        # Exit-ShipperQuiesce there -- because that wait is HTTP-bound, is the
        # first phase that genuinely wants prompt evidence shipping, and gives
        # the run a natural point where nothing is reading the share.
        Write-Marker -Name '_AFTER_INSTALL.marker' -Content (Get-Date).ToString('o')
        # station_boot_seconds (station-up-wait, below) is measured from this
        # timestamp -- capture it as a real DateTime now rather than
        # re-parsing the marker file back off disk later.
        $script:AfterInstallTime = Get-Date
        Save-Summary -Step 'installer-ran'

        # CAPTURE THE INSTALLER BREADCRUMB HERE <gate-a-run7-findings>, not in
        # the finalization block at the end of the run. Runs 4, 6 and 7 all
        # went dark in the few statements after
        # 'station-diag-captured-after-t3t5', which is where this capture used
        # to live; run7 got as far as writing the copy (the complete 6844-byte
        # file reached the host, so its handle closed) and never recorded the
        # step after it. Doing the capture now -- while the file is freshly
        # written, the run is not in its finalization path, and the shipper is
        # still quiesced -- removes it from that window entirely. The
        # finalization block keeps a guarded second attempt for the case where
        # the installer wrote the log after this point.
        Invoke-InstallProgressCapture -Phase 'post-install'
        Invoke-UpgradeEngineEvidenceCapture
        Sync-Transcript -Checkpoint 'post-install'

        # Give any late-writing child processes (service self-registration, log
        # flush) a short, BOUNDED grace window before we start reading state.
        Start-Sleep -Seconds 15
        Save-Summary -Step 'post-install-grace-sleep'
    }

    # 3. Locate the install directory. SKIP-REINSTALL MODE: no installer ran
    #    this session, so there is no fresh ARP registration to find (ARP is
    #    registry/OS state and does not persist across Sandbox sessions) --
    #    use the known, fixed /D= target directly instead. Otherwise, don't
    #    assume a path -- check ARP (Add/Remove Programs) registry entries
    #    first (authoritative, InstallLocation), then fall back to a
    #    directory-name scan of the two Program Files roots.
    if ($skipReinstall) {
        $installDir = $null
        if (Test-Path $PersistentInstallDir) { $installDir = $PersistentInstallDir }
        $summary.install_dir_candidates = @($PersistentInstallDir)
        $summary.install_dir_found = $installDir
        Save-Summary -Step 'install-dir-located'
    } else {
        $arpRoots = @(
            'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
            'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
        )
        $arpHits = foreach ($root in $arpRoots) {
            Get-ItemProperty -Path $root -ErrorAction SilentlyContinue |
                Where-Object { $_.DisplayName -like '*CivicCast*' }
        }
        foreach ($hit in $arpHits) {
            $summary.arp_entries += [ordered]@{
                DisplayName     = $hit.DisplayName
                DisplayVersion  = $hit.DisplayVersion
                InstallLocation = $hit.InstallLocation
                UninstallString = $hit.UninstallString
                EstimatedSize   = $hit.EstimatedSize
            }
        }

        $candidates = New-Object System.Collections.Generic.List[string]
        foreach ($hit in $arpHits) {
            if ($hit.InstallLocation) { $candidates.Add($hit.InstallLocation) }
        }
        foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
            if ($root -and (Test-Path $root)) {
                Get-ChildItem -Path $root -Directory -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -like '*Civic*' } |
                    ForEach-Object { $candidates.Add($_.FullName) }
            }
        }
        $candidates = $candidates | Select-Object -Unique
        $summary.install_dir_candidates = $candidates

        $installDir = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
        $summary.install_dir_found = $installDir
        Save-Summary -Step 'install-dir-located'
    }

    if ($installDir) {
        # Top-level + one-level-deep tree listing. "Bounded" used to mean
        # bounded in BREADTH (immediate children, first 50 grandchildren);
        # <gate-a-hoststore-wedge> makes it bounded in TIME as well, because
        # $installDir is C:\CivicCastHostStore\install -- a mapped folder
        # holding 10,683 files across 1,264 directories after a successful
        # install, and enumerating it is precisely the kind of call that has
        # no timeout of its own.
        Save-Summary -Step 'install-tree-listing-begin'
        $treeProbe = Invoke-BoundedProbe -Name 'install-tree-top-levels' -TimeoutSeconds 120 -Arguments @{
            InstallDir = $installDir
        } -ScriptText @'
$out = @()
try {
    $out = @(Get-ChildItem -Path $ProbeArgs['InstallDir'] -Force -ErrorAction SilentlyContinue |
        ForEach-Object {
            $entry = [ordered]@{ name = $_.Name; type = $(if ($_.PSIsContainer) { 'dir' } else { 'file' }) }
            if ($_.PSIsContainer) {
                $entry.children = @(Get-ChildItem -Path $_.FullName -Force -ErrorAction SilentlyContinue |
                    Select-Object -First 50 -ExpandProperty Name)
            }
            $entry
        })
} catch {}
@{ items = @($out) } | ConvertTo-Json -Depth 8 | Set-Content -Path $ResultPath -Encoding UTF8
'@
        if ($null -ne $treeProbe) { $summary.install_tree_top_levels = @($treeProbe.items) }
        Save-Summary -Step 'install-tree-top-levels'

        # 4. TARGETED (non-recursive) lookup for the two activation-related
        #    marker files -- see Test-KnownPaths above. Checks:
        #      <installDir>\station-set.json
        #      <installDir>\app\station-set.json
        #      <installDir>\app\*\station-set.json  (shallow, one level)
        #      %ProgramData%\CivicCast\station-set.json
        #    and the same four shapes for activation-self-test.json.
        $summary.station_set_json_found = Test-KnownPaths -InstallDir $installDir -FileName 'station-set.json'
        Save-Summary -Step 'station-set-json-check'
        $summary.activation_self_test_json_found = Test-KnownPaths -InstallDir $installDir -FileName 'activation-self-test.json'
        Save-Summary -Step 'activation-self-test-json-check'
    } else {
        # No install dir found via ARP/Program Files scan -- still check the
        # ProgramData-only shape of Test-KnownPaths so we don't silently skip
        # the one location that doesn't depend on $installDir. Pass a
        # nonexistent dummy install dir so only the ProgramData branch fires.
        $summary.station_set_json_found = Test-KnownPaths -InstallDir 'C:\__no-install-dir-found__' -FileName 'station-set.json'
        $summary.activation_self_test_json_found = Test-KnownPaths -InstallDir 'C:\__no-install-dir-found__' -FileName 'activation-self-test.json'
        Save-Summary -Step 'marker-check-no-install-dir'
    }

    # 4b. If activation did NOT write station-set.json, re-run the activation
    #     CLI directly against the (now fully provisioned) install with full
    #     stdout+stderr captured, so we get the EXACT underlying error the NSIS
    #     ExecToLog swallowed (the installer only logs a generic "bundle not
    #     found" template + exit 123). This reproduces the same activation the
    #     installer ran, against the same install dir, bundle, and runtimes.
    # Bulletproof plain-text capture (NO ConvertTo-Json, OS-level stream
    # redirect so the activation stderr lands on disk even if this script
    # later dies): write activation-rerun.log + a flat result file.
    $rr = Join-Path $OutDir 'ACTIVATION-RESULT.txt'
    "installer_exit_code=$($summary.installer_exit_code)" | Set-Content -Path $rr -Encoding UTF8
    "install_dir=$installDir" | Add-Content -Path $rr -Encoding UTF8
    $stationHit = ($summary.station_set_json_found | Measure-Object).Count
    "station_set_json_found_after_install=$stationHit" | Add-Content -Path $rr -Encoding UTF8
    if ($stationHit -eq 0 -and $installDir) {
        # With /D=C:\CivicCastHostStore\install the install already lives on the
        # host-backed folder (real disk, junctions intact). If the installer's
        # OWN activation still didn't write station-set.json, re-run activation
        # directly against that same install root (NO copy -- copying breaks the
        # provision-created dependency junctions), with cache on host disk too.
        $hostStore   = 'C:\CivicCastHostStore'
        $hostInstall = $installDir
        $cache       = Join-Path $hostStore 'cache'
        $bundle      = 'C:\CivicCastPayload\station\station-index.json'
        "hoststore_present=$([bool](Test-Path $hostStore))" | Add-Content -Path $rr -Encoding UTF8
        "hostinstall=$hostInstall" | Add-Content -Path $rr -Encoding UTF8
        $exe = Join-Path $hostInstall 'CivicCast Native.exe'
        "rerun_exe=$exe present=$([bool](Test-Path $exe))" | Add-Content -Path $rr -Encoding UTF8
        "rerun_bundle=$bundle present=$([bool](Test-Path $bundle))" | Add-Content -Path $rr -Encoding UTF8
        if ((Test-Path $exe) -and (Test-Path $bundle)) {
            New-Item -ItemType Directory -Force -Path $cache | Out-Null
            $outLog = Join-Path $OutDir 'activation-rerun.stdout.log'
            $errLog = Join-Path $OutDir 'activation-rerun.stderr.log'
            "(started $(Get-Date -Format o))" | Set-Content -Path $errLog -Encoding UTF8
            # GUI-subsystem Tauri app: use Start-Process -Wait (the call operator
            # does not wait for GUI apps); explicit-quoted arg string so the
            # space+parens in the install path survive Windows PowerShell 5.1.
            $argStr = '--civiccast-activate-station --install-root "' + $hostInstall + '"' +
                      ' --civiccast-import-station "' + $bundle + '"' +
                      ' --cache-root "' + $cache + '"'
            $proc = Start-Process -FilePath $exe -ArgumentList $argStr -Wait -PassThru `
                -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog
            "rerun_exit_code=$($proc.ExitCode)" | Add-Content -Path $rr -Encoding UTF8
            # station-set.json / activation-self-test.json now land in the
            # host-mapped install root. Copy any proof out to $OutDir.
            $ssPath = Join-Path $hostInstall 'station-set.json'
            $stPath = Join-Path $hostInstall 'activation-self-test.json'
            "station_set_json_found_after_rerun=$([int](Test-Path $ssPath))" | Add-Content -Path $rr -Encoding UTF8
            "station_set_path=$ssPath" | Add-Content -Path $rr -Encoding UTF8
            if (Test-Path $ssPath) {
                Copy-Item -LiteralPath $ssPath -Destination (Join-Path $OutDir 'PROVEN-station-set.json') -Force -ErrorAction SilentlyContinue
            }
            if (Test-Path $stPath) {
                Copy-Item -LiteralPath $stPath -Destination (Join-Path $OutDir 'PROVEN-activation-self-test.json') -Force -ErrorAction SilentlyContinue
            }
        }
    }

    # 5. CivicCastSupervisor service state -- Get-Service (typed) and raw
    #    sc.exe query / qc (matches exactly what the installer's own
    #    PREINSTALL upgrade classification does: "sc query CivicCastSupervisor",
    #    exit 0 = registered, 1060 = ERROR_SERVICE_DOES_NOT_EXIST = not
    #    registered). None of these three calls block/wait.
    $svc = Get-Service -Name 'CivicCastSupervisor' -ErrorAction SilentlyContinue
    if ($svc) {
        $summary.service_get_service = [ordered]@{
            Name              = $svc.Name
            Status            = $svc.Status.ToString()
            StartType         = $svc.StartType.ToString()
        }
    } else {
        $summary.service_get_service = $null
    }
    $summary.service_sc_query_raw = (& sc.exe query CivicCastSupervisor 2>&1 | Out-String)
    $summary.service_sc_qc_raw    = (& sc.exe qc CivicCastSupervisor 2>&1 | Out-String)
    Save-Summary -Step 'service-state-query'

    # (c) OPTIONAL bounded start attempt -- only if the service is actually
    # registered and not already running, so we learn whether it CAN run
    # (vs. dead-ending pre-activation) rather than just its at-rest status.
    # Start-Service itself does not block; the wait below is capped at 60s
    # and polls Status rather than doing any unbounded wait.
    if ($svc -and $svc.Status -ne 'Running') {
        $attempt = [ordered]@{
            attempted       = $true
            start_error     = $null
            status_after    = $null
            reached_running = $false
            poll_seconds    = 0
        }
        try {
            Start-Service -Name 'CivicCastSupervisor' -ErrorAction Stop
        } catch {
            $attempt.start_error = "$_"
        }
        $deadline = (Get-Date).AddSeconds(60)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 3
            $attempt.poll_seconds += 3
            $cur = Get-Service -Name 'CivicCastSupervisor' -ErrorAction SilentlyContinue
            $attempt.status_after = if ($cur) { $cur.Status.ToString() } else { 'unknown' }
            if ($cur -and $cur.Status -eq 'Running') { $attempt.reached_running = $true; break }
            if ($cur -and $cur.Status -in @('Stopped','StopPending') -and $attempt.start_error) { break }
        }
        $summary.service_start_attempt = $attempt
    } elseif ($svc) {
        $summary.service_start_attempt = [ordered]@{ attempted = $false; reason = "already $($svc.Status)" }
    } else {
        $summary.service_start_attempt = [ordered]@{ attempted = $false; reason = 'service not registered' }
    }
    Save-Summary -Step 'service-start-attempt'

    # 5b. STATION-UP WAIT (HARDENED <gate-a-station-up-wait-and-log-capture>
    #     after 8579e66-run3: the old version polled 3 endpoints
    #     SEQUENTIALLY at up to 180s EACH -- ~9.5 minutes total -- then gave
    #     up. The Aug-19 reference run only worked because ~25 minutes of
    #     unrelated activation work happened to sit between install and this
    #     check, giving the station time to cold-boot postgres+nats+
    #     control-plane; 8579e66-run3 had no such gap and the station simply
    #     never got there within the old bound. This version polls ONLY
    #     /api/health on a single bounded 20-minute deadline (6s interval),
    #     logs every poll's timestamp + outcome/error to
    #     STATION-UP-WAIT.txt, and only probes /operator/ and / (short,
    #     60s-bounded each) AFTER health itself has answered 200 --
    #     matching the coordinator's exact spec. station_boot_seconds is
    #     measured from _AFTER_INSTALL.marker (informational only: Gate A's
    #     judge records it but does not fail on duration -- Gate B owns
    #     timing).
    # Lift the shipper quiesce here <gate-a-hoststore-wedge>. Everything from
    # this point on is HTTP-bound (a 6-second poll against 127.0.0.1) rather
    # than VSMB-bound, so the 25s tick costs nothing -- and this is the first
    # phase that genuinely wants prompt evidence shipping, because a station
    # that never comes up is a 20-minute wait the host should be able to watch.
    Exit-ShipperQuiesce
    Save-Summary -Step 'shipper-unquiesced-at-station-up-wait'

    $rt = Join-Path $OutDir 'RUNTIME-RESULT.txt'
    $stationWaitLog = Join-Path $OutDir 'STATION-UP-WAIT.txt'
    "checked_utc=$((Get-Date).ToUniversalTime().ToString('o'))" | Set-Content -Path $rt -Encoding UTF8
    $afterInstallIso = if ($script:AfterInstallTime) { $script:AfterInstallTime.ToUniversalTime().ToString('o') } else { '<not-set>' }
    "wait_started_utc=$((Get-Date).ToUniversalTime().ToString('o')) after_install_marker_utc=$afterInstallIso deadline_minutes=20 poll_interval_seconds=6" |
        Set-Content -Path $stationWaitLog -Encoding UTF8

    $endpoints = [ordered]@{
        health           = 'http://127.0.0.1:8000/api/health'
        operator_console = 'http://127.0.0.1:8000/operator/'
        resident_portal  = 'http://127.0.0.1:8000/'
    }
    $summary.runtime_checks = [ordered]@{}

    # ---- health: the ONLY endpoint with the long (20-minute) bound ----
    # Gate A run 33681670855 fix: civiccast/app.py's /health docstring is
    # explicit that HTTP 200 is LIVENESS ONLY ("always 200 while the process
    # answers, in every schema state") and READINESS is the JSON body's
    # `status` field (healthy/degraded) plus `schema` (current/behind/...).
    # Gating on status-code-200-and-nonempty-body alone (the previous
    # behavior here) reported STATION HEALTHY for a beta.3 station serving
    # 500s over an unmigrated beta.2 database, whose body read
    # {"status":"degraded","schema":"behind",...}. STATION HEALTHY now
    # requires the body itself to say status=="healthy" and schema=="current".
    $healthRes = [ordered]@{
        url = $endpoints.health; status = $null; ok = $false; bytes = 0; snippet = $null; error = $null; polls = 0
        body_status = $null; body_schema = $null; db_revision = $null; expected_head = $null
    }
    $stationFirstHealthyTime = $null
    $healthDeadline = (Get-Date).AddMinutes(20)
    while ((Get-Date) -lt $healthDeadline) {
        $healthRes.polls++
        $pollTs = (Get-Date).ToUniversalTime().ToString('o')
        # <gate-a-audit-MA-26> ONE predicate, shared with T3 and T5 below.
        $probe = Invoke-CivicCastHealthProbe -Url $endpoints.health
        $healthRes.status = $probe.status
        $healthRes.bytes = $probe.bytes
        if ($probe.snippet) { $healthRes.snippet = $probe.snippet }
        $healthRes.body_status = $probe.body_status
        $healthRes.body_schema = $probe.body_schema
        $healthRes.db_revision = $probe.db_revision
        $healthRes.expected_head = $probe.expected_head
        if ($probe.error) {
            $healthRes.error = $probe.error
            "poll #$($healthRes.polls) $pollTs -> ERROR: $($healthRes.error)" | Add-Content -Path $stationWaitLog -Encoding UTF8
        } elseif ($probe.ok) {
            $healthRes.ok = $true
            $stationFirstHealthyTime = Get-Date
            "poll #$($healthRes.polls) $pollTs -> status:$($healthRes.status) body_status:$($healthRes.body_status) body_schema:$($healthRes.body_schema) STATION HEALTHY" | Add-Content -Path $stationWaitLog -Encoding UTF8
            break
        } elseif ($probe.body_parse_error) {
            "poll #$($healthRes.polls) $pollTs -> status:$($healthRes.status) ok:false bytes:$($healthRes.bytes) body_parse_error:$($probe.body_parse_error)" | Add-Content -Path $stationWaitLog -Encoding UTF8
        } else {
            "poll #$($healthRes.polls) $pollTs -> status:$($healthRes.status) ok:false body_status:$($healthRes.body_status) body_schema:$($healthRes.body_schema) db_revision:$($healthRes.db_revision) expected_head:$($healthRes.expected_head) DEGRADED (readiness body did not report healthy/current)" | Add-Content -Path $stationWaitLog -Encoding UTF8
        }
        Start-Sleep -Seconds 6
    }
    if (-not $healthRes.ok) {
        "wait_ended_utc=$((Get-Date).ToUniversalTime().ToString('o')) result=TIMEOUT total_polls=$($healthRes.polls) (20-minute bounded deadline reached, station never reported healthy/current at /api/health)" |
            Add-Content -Path $stationWaitLog -Encoding UTF8
    }
    $summary.runtime_checks['health'] = $healthRes
    "health=status:$($healthRes.status) ok:$($healthRes.ok) body_status:$($healthRes.body_status) body_schema:$($healthRes.body_schema) db_revision:$($healthRes.db_revision) expected_head:$($healthRes.expected_head) bytes:$($healthRes.bytes) polls:$($healthRes.polls) err:$($healthRes.error)" | Add-Content -Path $rt -Encoding UTF8

    $summary.station_up = [bool]$healthRes.ok
    $summary.station_first_healthy_utc = if ($stationFirstHealthyTime) { $stationFirstHealthyTime.ToUniversalTime().ToString('o') } else { $null }
    if ($stationFirstHealthyTime -and $script:AfterInstallTime) {
        $summary.station_boot_seconds = [Math]::Round((New-TimeSpan -Start $script:AfterInstallTime -End $stationFirstHealthyTime).TotalSeconds, 1)
    } else {
        $summary.station_boot_seconds = $null
    }
    "station_up=$($summary.station_up) station_first_healthy_utc=$($summary.station_first_healthy_utc) station_boot_seconds=$($summary.station_boot_seconds)" |
        Add-Content -Path $stationWaitLog -Encoding UTF8
    Save-Summary -Step 'station-up-wait'

    # ARM the staleness watchdog <gate-a-mapped-folder-stalls>. Everything
    # from here to DONE.json is bounded work -- the only long waits left are
    # the T5 soak's own 300s beats, each of which advances step_seq -- so
    # from this point on, 8 minutes with no step_seq change means the run is
    # stuck, not busy. The marker is a FILE and is never removed precisely
    # because the previous arming test read a momentary in-file value and a
    # 30s poller could not reliably see it (run6 armed on nothing at all and
    # the staleness bound never fired). Written AFTER the station-up verdict
    # so it covers both the station-up and station-down paths.
    Write-Marker -Name '_VERDICT-STAGE.marker' -Content "armed_utc=$((Get-Date).ToUniversalTime().ToString('o')) station_up=$($summary.station_up) step_seq=$($summary.step_seq)"

    # Flush the transcript here <gate-a-run7-findings>: the 20-minute
    # station-up wait is the single biggest producer of transcript content on
    # a failing run (one caught terminating error per 6s poll), and a run that
    # ends via the watchdog never reaches Stop-Transcript to flush it. run7
    # lost all 150 polls' worth this way.
    Sync-Transcript -Checkpoint 'station-up-verdict'

    # ---- operator_console / resident_portal: ONLY after health=200, short 60s bound each ----
    foreach ($name in @('operator_console', 'resident_portal')) {
        $url = $endpoints[$name]
        $res = [ordered]@{ url = $url; status = $null; ok = $false; bytes = 0; snippet = $null; error = $null; polls = 0 }
        if ($healthRes.ok) {
            $deadline = (Get-Date).AddSeconds(60)
            while ((Get-Date) -lt $deadline) {
                $res.polls++
                try {
                    $r = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
                    $res.status = [int]$r.StatusCode
                    $body = [string]$r.Content
                    $res.bytes = $body.Length
                    $res.snippet = ($body.Substring(0, [Math]::Min(300, $body.Length))) -replace '\s+', ' '
                    if ($res.status -eq 200 -and $res.bytes -gt 0) { $res.ok = $true }
                    break
                } catch {
                    $res.error = "$($_.Exception.Message)"
                    Start-Sleep -Seconds 6
                }
            }
        } else {
            $res.error = 'skipped: /api/health did not answer 200 within the 20-minute station-up-wait deadline'
        }
        $summary.runtime_checks[$name] = $res
        "$name=status:$($res.status) ok:$($res.ok) bytes:$($res.bytes) polls:$($res.polls) err:$($res.error)" | Add-Content -Path $rt -Encoding UTF8
        Save-Summary -Step "runtime-check-$name"
    }
    # Capture the operator console + portal HTML to files for eyeball/browser proof.
    foreach ($name in @('operator_console','resident_portal','health')) {
        try {
            $u = $endpoints[$name]
            $r2 = Invoke-WebRequest -Uri $u -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
            [string]$r2.Content | Set-Content -Path (Join-Path $OutDir "ui-$name.html") -Encoding UTF8
        } catch {}
    }

    # Station diagnostics -- FIRST capture, right after the station-up
    # decision and BEFORE the runtime verdict downstream (Gate A's own
    # judge, and this script's T2/T3/T4/T5 gating below) consumes it.
    # Captured unconditionally (pass or fail) so a station that never came
    # up still leaves behind whatever the supervisor/postgres/nats/control-
    # plane children managed to log before giving up.
    try {
        Invoke-StationDiagCapture -OutDir $OutDir -InstallDir $installDir -Label 'after-station-up-wait' -RunStart $RunStart
    } catch {
        "station diag capture (after-station-up-wait) failed: $_" | Add-Content -Path (Join-Path $OutDir 'summary-write-errors.log') -Encoding UTF8
    }
    Save-Summary -Step 'station-diag-captured-after-wait'
    Save-Summary -Step 'runtime-ui-captured'

    # 5b-dirty. DIRTY-LANE SURVIVAL VERIFY <gate-a-dirty-lane>. Only in dirty
    # mode, right after the station-up verdict: prove the operator data the
    # prologue planted before the upgrade or legacy uninstall/reinstall cycle
    # survived it, and
    # (when the orphaned large-v3 remnant was seeded) that PR #80's
    # orphaned-tier fallback actually FIRED -- the supervisor log must carry
    # its WARNING, proving the remnant was detected and degraded rather than
    # nothing having been exercised at all. All paths here are the sandbox's
    # own local disk (ProgramData); no mapped-folder access.
    if ($script:DirtyMode) {
        $dirtyRes = Join-Path $OutDir 'DIRTY-RESULT.txt'
        "checked_utc=$((Get-Date).ToUniversalTime().ToString('o'))" | Set-Content -Path $dirtyRes -Encoding UTF8
        if ($script:UpgradeMode) {
            "UPGRADE_MODE=1" | Add-Content -Path $dirtyRes -Encoding UTF8
            "UPGRADE_CURRENT_INSTALL_EXIT=$($summary.installer_exit_code)" | Add-Content -Path $dirtyRes -Encoding UTF8
            "D3_ROUTE=$($script:D3Route)" | Add-Content -Path $dirtyRes -Encoding UTF8
            "D3_ENGINE_EXIT=$($script:D3EngineExit)" | Add-Content -Path $dirtyRes -Encoding UTF8
            "D4_ACTIVATE_EXIT=$($script:D4ActivateExit)" | Add-Content -Path $dirtyRes -Encoding UTF8
            "POSTINSTALL_OUTCOME=$($script:PostinstallOutcome)" | Add-Content -Path $dirtyRes -Encoding UTF8

            # Gate A run 33681670855 fix: the station-up health check proved
            # /health said healthy/current, but D3 rehoming's real defect was
            # a pre-upgrade DRILL that compared the wrong revision -- the
            # actual proof an upgrade committed is that the LIVE database's
            # revision equals the running code's migration head, both read
            # straight from the same /health poll that declared the station
            # up (civiccast/app.py now returns schema_db_revision/
            # schema_expected_head unconditionally, not just when "behind").
            # POST_UPGRADE_DB_REVISION_MATCHES_HEAD=0 fails this lane even if
            # station_up=1, because "the process answered 200" was exactly
            # the weaker claim Gate A run 33681670855 shipped on.
            $postUpgradeRevision = if ($healthRes.db_revision) { $healthRes.db_revision } else { '<unavailable>' }
            $postUpgradeExpectedHead = if ($healthRes.expected_head) { $healthRes.expected_head } else { '<unavailable>' }
            $postUpgradeMatches = [int](
                $healthRes.ok -and $postUpgradeRevision -ne '<unavailable>' -and
                ($postUpgradeRevision -eq $postUpgradeExpectedHead)
            )
            "POST_UPGRADE_DB_REVISION=$postUpgradeRevision" | Add-Content -Path $dirtyRes -Encoding UTF8
            "EXPECTED_HEAD=$postUpgradeExpectedHead" | Add-Content -Path $dirtyRes -Encoding UTF8
            "POST_UPGRADE_DB_REVISION_MATCHES_HEAD=$postUpgradeMatches" | Add-Content -Path $dirtyRes -Encoding UTF8

            # <gate-a-audit-BL-10> The two INDEPENDENT operands. The three
            # lines above all descend from one SchemaStatus in one process
            # and can therefore never disagree; these two cannot agree by
            # construction. See Get-CivicCastDbRevisionViaPsql's header.
            $psqlProbe = Get-CivicCastDbRevisionViaPsql -InstallDir $installDir -LogFile $dirtyRes
            "POST_UPGRADE_DB_REVISION_PSQL=$($psqlProbe.revision)" | Add-Content -Path $dirtyRes -Encoding UTF8
            "KIT_EXPECTED_HEAD=$(Get-CivicCastKitExpectedHead)" | Add-Content -Path $dirtyRes -Encoding UTF8

            # Post-upgrade app-payload identity, prompted by the 2026-09-05
            # real-tester install-over regression: D3_ENGINE_EXIT=0 and a
            # healthy /health body proved setup finished -- they never proved
            # the installed APPLICATION PAYLOAD is actually the one this kit
            # shipped. Hash the pack CURRENTLY STAGED on the station against
            # the pack THIS KIT actually shipped in $PayloadDir\packs, and
            # record both so gate_a_verdict.py's check_dirty_survival can
            # fail the run when they diverge.
            #
            # HONEST SCOPE NOTE: this lane's baseline is always an OLDER
            # product_version than the candidate, so these two digests will
            # always match here by construction -- this does NOT reproduce
            # the regression's actual trigger (two kits declaring the SAME
            # product_version with different content). See
            # gate_a_verdict.py's check_dirty_survival for the full note;
            # that exact same-version scenario is covered by
            # native_pack_staging.rs's own Rust tests, not by this lane.
            $postUpgradeAppPayloadPack = Join-Path $installDir 'packs\native-app-payload.ccpack'
            $postUpgradeAppPayloadDigest = 'unavailable'
            if (Test-Path -LiteralPath $postUpgradeAppPayloadPack) {
                try {
                    $postUpgradeAppPayloadDigest = (Get-FileHash -LiteralPath $postUpgradeAppPayloadPack -Algorithm SHA256).Hash.ToLower()
                } catch {
                    $postUpgradeAppPayloadDigest = "error:$_"
                }
            }
            "POST_UPGRADE_APP_PAYLOAD_DIGEST=$postUpgradeAppPayloadDigest" | Add-Content -Path $dirtyRes -Encoding UTF8

            $kitAppPayloadPack = Join-Path $PayloadDir 'packs\native-app-payload.ccpack'
            $kitAppPayloadDigest = 'unavailable'
            if (Test-Path -LiteralPath $kitAppPayloadPack) {
                try {
                    $kitAppPayloadDigest = (Get-FileHash -LiteralPath $kitAppPayloadPack -Algorithm SHA256).Hash.ToLower()
                } catch {
                    $kitAppPayloadDigest = "error:$_"
                }
            }
            "KIT_APP_PAYLOAD_DIGEST=$kitAppPayloadDigest" | Add-Content -Path $dirtyRes -Encoding UTF8
        }
        $pdRoot = Join-Path $env:ProgramData 'CivicCast'

        # pgdata: the SAME cluster, not a re-provisioned lookalike.
        $pgdataNow = Join-Path $pdRoot 'data\pgdata'
        $pgPreserved = 0
        $pgDetail = 'pgdata missing'
        if (Test-Path $pgdataNow) {
            $creationNow = $null
            $pgVersionNow = $null
            try { $creationNow = (Get-Item -LiteralPath $pgdataNow -Force).CreationTimeUtc.ToString('o') } catch {}
            try { $pgVersionNow = (Get-Content -LiteralPath (Join-Path $pgdataNow 'PG_VERSION') -Raw -ErrorAction Stop).Trim() } catch {}
            if ($script:DirtyPgdataCreationUtc -and ($creationNow -eq $script:DirtyPgdataCreationUtc) -and ($pgVersionNow -eq $script:DirtyPgVersion)) {
                $pgPreserved = 1
                $pgDetail = "same cluster (creation_utc=$creationNow pg_version=$pgVersionNow)"
            } else {
                $pgDetail = "identity mismatch: phase1 creation_utc=$($script:DirtyPgdataCreationUtc) now=$creationNow phase1 pg_version=$($script:DirtyPgVersion) now=$pgVersionNow"
            }
        }
        "DIRTY_PGDATA_PRESERVED=$pgPreserved detail=$pgDetail" | Add-Content -Path $dirtyRes -Encoding UTF8

        # uploads: byte-identical survival of the planted recordings.
        $uploadsNow = Join-Path $pdRoot 'data\uploads'
        $uploadsPreserved = 1
        if ($script:DirtyUploadHashes.Count -eq 0) { $uploadsPreserved = 0 }
        foreach ($name in $script:DirtyUploadHashes.Keys) {
            $f = Join-Path $uploadsNow $name
            $hNow = $null
            try { if (Test-Path $f) { $hNow = (Get-FileHash -Path $f -Algorithm SHA256).Hash.ToLower() } } catch {}
            $match = [int]($hNow -eq $script:DirtyUploadHashes[$name])
            if ($match -ne 1) { $uploadsPreserved = 0 }
            "DIRTY_UPLOAD name=$name survived=$match sha256_now=$hNow" | Add-Content -Path $dirtyRes -Encoding UTF8
        }
        "DIRTY_UPLOADS_PRESERVED=$uploadsPreserved" | Add-Content -Path $dirtyRes -Encoding UTF8

        # Orphaned-tier warning (PR #80). Echo the seed decision from the
        # prologue's own file, then grep every log in the station's log root
        # (size-guarded) for the fallback's WARNING text.
        $orphanSeeded = 'unknown'
        try {
            $prepRaw = Get-Content -LiteralPath (Join-Path $OutDir 'DIRTY-PREP-RESULT.txt') -Raw -ErrorAction Stop
            $m = [regex]::Match($prepRaw, 'DIRTY_ORPHAN_SEEDED=(\d)')
            if ($m.Success) { $orphanSeeded = $m.Groups[1].Value }
        } catch {}
        "DIRTY_ORPHAN_SEEDED=$orphanSeeded" | Add-Content -Path $dirtyRes -Encoding UTF8
        if ($orphanSeeded -eq '1') {
            $warningFound = 0
            $logRoot = Join-Path $pdRoot 'logs'
            try {
                foreach ($logFile in @(Get-ChildItem -Path $logRoot -Filter '*.log*' -File -ErrorAction SilentlyContinue)) {
                    if ($logFile.Length -gt 32MB) {
                        "DIRTY_ORPHAN_LOG_SKIPPED name=$($logFile.Name) bytes=$($logFile.Length) (over 32MB cap)" | Add-Content -Path $dirtyRes -Encoding UTF8
                        continue
                    }
                    $hit = Select-String -LiteralPath $logFile.FullName -Pattern 'staged but has no valid activation\s*self-test receipt' -ErrorAction SilentlyContinue | Select-Object -First 1
                    if ($hit) {
                        $warningFound = 1
                        "DIRTY_ORPHAN_WARNING_SOURCE=$($logFile.Name):$($hit.LineNumber)" | Add-Content -Path $dirtyRes -Encoding UTF8
                        break
                    }
                }
            } catch {
                "DIRTY_ORPHAN_LOG_SCAN_ERROR=$_" | Add-Content -Path $dirtyRes -Encoding UTF8
            }
            "DIRTY_ORPHAN_WARNING=$warningFound" | Add-Content -Path $dirtyRes -Encoding UTF8
        } else {
            "DIRTY_ORPHAN_WARNING=NA (remnant not seeded this run -- orphaned-tier shape not covered)" | Add-Content -Path $dirtyRes -Encoding UTF8
        }
        Save-Summary -Step 'dirty-survival-verified'
    }

    # 5b-download-only. DOWNLOAD-ONLY LANE RESULT
    # <gate-a-download-only-lane>: finish DOWNLOAD-ONLY-RESULT.txt (started
    # above, before phase 2's install, with STATION_DIR_PRESENT) with the
    # phase-2 install/activation outcome and the resulting station-set.json's
    # product version, so the judge's download_only_no_station_dir check can
    # prove activation succeeded with no station\ present by reusing an
    # already-activated station's cached model packs -- not by silently
    # falling back to a stale or mismatched receipt.
    if ($script:DownloadOnlyMode) {
        $dlOnlyResult = Join-Path $OutDir 'DOWNLOAD-ONLY-RESULT.txt'
        "PHASE2_INSTALL_EXIT=$($summary.installer_exit_code)" | Add-Content -Path $dlOnlyResult -Encoding UTF8
        "D3_ROUTE=$($script:D3Route)" | Add-Content -Path $dlOnlyResult -Encoding UTF8
        "D3_ENGINE_EXIT=$($script:D3EngineExit)" | Add-Content -Path $dlOnlyResult -Encoding UTF8
        # <gate-a-audit-MA-24> The judge's download-only check claimed to
        # verify "the D4 activation step exited 0" while reading D3's number.
        # Today's evidence proves the two diverge:
        # gate-a-download-only-33623737236/install-progress.log:346-353 shows
        # `route=UPGRADE engine_exit=0` immediately followed by
        # `step d4-activate-station: returned 66` and `postinstall: FAILED`.
        "D4_ACTIVATE_EXIT=$($script:D4ActivateExit)" | Add-Content -Path $dlOnlyResult -Encoding UTF8
        "POSTINSTALL_OUTCOME=$($script:PostinstallOutcome)" | Add-Content -Path $dlOnlyResult -Encoding UTF8

        # Gate A run 33681670855 fix: same proof as the dirty lane above --
        # station_up=1 (a 200 with a healthy/current body) is not itself
        # proof the running database is at the running code's migration
        # head; read both revisions straight from the same station-up health
        # poll and judge them explicitly.
        $postUpgradeRevisionDl = if ($healthRes.db_revision) { $healthRes.db_revision } else { '<unavailable>' }
        $postUpgradeExpectedHeadDl = if ($healthRes.expected_head) { $healthRes.expected_head } else { '<unavailable>' }
        $postUpgradeMatchesDl = [int](
            $healthRes.ok -and $postUpgradeRevisionDl -ne '<unavailable>' -and
            ($postUpgradeRevisionDl -eq $postUpgradeExpectedHeadDl)
        )
        "POST_UPGRADE_DB_REVISION=$postUpgradeRevisionDl" | Add-Content -Path $dlOnlyResult -Encoding UTF8
        "EXPECTED_HEAD=$postUpgradeExpectedHeadDl" | Add-Content -Path $dlOnlyResult -Encoding UTF8
        "POST_UPGRADE_DB_REVISION_MATCHES_HEAD=$postUpgradeMatchesDl" | Add-Content -Path $dlOnlyResult -Encoding UTF8

        # <gate-a-audit-BL-10> Same two independent operands as the dirty
        # lane above: the database's own alembic_version row read with psql,
        # and the build-time head the CI job recorded from source.
        $psqlProbeDl = Get-CivicCastDbRevisionViaPsql -InstallDir $installDir -LogFile $dlOnlyResult
        "POST_UPGRADE_DB_REVISION_PSQL=$($psqlProbeDl.revision)" | Add-Content -Path $dlOnlyResult -Encoding UTF8
        "KIT_EXPECTED_HEAD=$(Get-CivicCastKitExpectedHead)" | Add-Content -Path $dlOnlyResult -Encoding UTF8

        $stationSetHits = @($summary.station_set_json_found)
        $stationSetProductVersion = '<missing>'
        if ($stationSetHits.Count -gt 0) {
            try {
                $ssContent = Get-Content -LiteralPath $stationSetHits[0] -Raw -ErrorAction Stop | ConvertFrom-Json
                if ($ssContent.product_version) { $stationSetProductVersion = [string]$ssContent.product_version }
            } catch {
                $stationSetProductVersion = "<read-error: $_>"
            }
        }
        "STATION_SET_PRODUCT_VERSION=$stationSetProductVersion" | Add-Content -Path $dlOnlyResult -Encoding UTF8

        # CURRENT_PRODUCT_VERSION was already captured into
        # DIRTY-PREP-RESULT.txt by the shared upgrade prologue (P1, above) --
        # echo it here too so the judge can compare both fields from this one
        # file without cross-referencing DIRTY-PREP-RESULT.txt itself.
        $currentProductVersion = '<missing>'
        try {
            $prepRawForDlOnly = Get-Content -LiteralPath (Join-Path $OutDir 'DIRTY-PREP-RESULT.txt') -Raw -ErrorAction Stop
            $currentVersionMatch = [regex]::Match($prepRawForDlOnly, 'CURRENT_PRODUCT_VERSION=(\S+)')
            if ($currentVersionMatch.Success) { $currentProductVersion = $currentVersionMatch.Groups[1].Value }
        } catch {}
        "CURRENT_PRODUCT_VERSION=$currentProductVersion" | Add-Content -Path $dlOnlyResult -Encoding UTF8
        Save-Summary -Step 'download-only-result-recorded'
    }

    # 5c. T2 RENDER ASSERT (QA-F1 fix). The T1 checks above only prove the
    #     server answered with 200 + nonzero bytes -- true for the bare SPA
    #     shell whether or not the SPA ever mounts. Drive headless Edge
    #     against both shells with a virtual-time budget so React's async
    #     data fetch (portal: assets/schedule; operator: setup/auth state)
    #     has time to resolve before dump-dom captures the DOM -- a bare
    #     --dump-dom with no time budget under-captures a still-loading SPA
    #     (confirmed on the host dry-run: 2.7KB without a time budget vs.
    #     11.9KB with one, on the SAME page). PASS requires the dumped DOM
    #     to be a large multiple of the raw shell AND contain real UI copy
    #     that provably is not present in the raw shell.
    $edgeExe = Get-EdgePath
    $t2 = Join-Path $OutDir 'T2-RENDER-RESULT.txt'
    "checked_utc=$((Get-Date).ToUniversalTime().ToString('o'))" | Set-Content -Path $t2 -Encoding UTF8
    "edge_exe=$edgeExe" | Add-Content -Path $t2 -Encoding UTF8
    $renderTargets = @(
        @{ label = 'portal';   url = $endpoints.resident_portal;  known = @('No live broadcast is on air.','Latest recordings','Coming up','Broadcast status'); out = (Join-Path $OutDir 'ui-dom-portal.html') },
        @{ label = 'operator'; url = $endpoints.operator_console; known = @('No live meeting broadcast','Open navigation','Setup First setup'); out = (Join-Path $OutDir 'ui-dom-operator.html') }
    )
    $summary.t2_render_checks = [ordered]@{}
    $t2AllPass = $true
    foreach ($t in $renderTargets) {
        $res = Test-RenderAssert -Label $t.label -Url $t.url -KnownStrings $t.known -EdgeExe $edgeExe -DomOutFile $t.out
        $summary.t2_render_checks[$t.label] = $res
        if ($res.result -ne 'PASS') { $t2AllPass = $false }
        "T2_$($t.label) raw=$($res.raw_bytes) dumped=$($res.dumped_bytes) ratio=$($res.ratio) known_matched=$($res.known_string_matched) known_in_raw=$($res.known_string_in_raw) edge_ok=$($res.edge_ok) timed_out=$($res.timed_out) error=$($res.error) result=$($res.result)" | Add-Content -Path $t2 -Encoding UTF8
    }
    "T2_RENDER=$(if($t2AllPass){'PASS'}else{'FAIL'})" | Add-Content -Path $t2 -Encoding UTF8
    Save-Summary -Step 't2-render-assert'

    # ================= T3 (loop/surfaces) · T4 (egress) · T5 (soak) =================
    $t35 = Join-Path $OutDir 'T3T5-RESULT.txt'
    "checked_utc=$((Get-Date).ToUniversalTime().ToString('o'))" | Set-Content -Path $t35 -Encoding UTF8
    $stationUp = $false
    try { $stationUp = ($summary.station_up -eq $true) } catch {}
    "station_up=$stationUp station_boot_seconds=$($summary.station_boot_seconds)" | Add-Content -Path $t35 -Encoding UTF8
    $BASE = 'http://127.0.0.1:8000'
    $soakDir = 'C:\CivicCastSoak'
    $runRoot = 'C:\CivicCastSoakRun'
    New-Item -ItemType Directory -Force -Path $runRoot | Out-Null
    if ($installDir) {
        $ffmpeg = Join-Path $installDir 'dependencies\ffmpeg\bin\ffmpeg.exe'
        $tsdukBin = Join-Path $installDir 'packs\native-server-binaries\payload\tsduck\bin'
        $env:CIVICCAST_FFMPEG = $ffmpeg
        $env:FFMPEG = $ffmpeg                                  # start-encoders.ps1 reads $env:FFMPEG only
        $env:CIVICCAST_TSDUCK_PATH = $tsdukBin
        $env:TSP = (Join-Path $tsdukBin 'tsp.exe')             # verify-egress.ps1 prefers $env:TSP
        $env:BASE_URL = $BASE
        $env:RUN_ROOT = $runRoot
        "ffmpeg_present=$([bool](Test-Path $ffmpeg)) tsp_present=$([bool](Test-Path (Join-Path $tsdukBin 'tsp.exe')))" | Add-Content -Path $t35 -Encoding UTF8

        # <gate-a-batch-item-1> Author the firewall allow rules BEFORE the
        # first tsp/ffmpeg bind, so the guest's Windows Security prompt never
        # appears. Resolved from the same TSDuck path the verify itself uses,
        # so the rule can never name a different binary than the one that
        # runs. See Add-CivicCastFirewallAllowRule for why this is safe and
        # non-fatal.
        Add-CivicCastFirewallAllowRule -Name 'CivicCast Gate A - TSDuck tsp' -ProgramPath (Join-Path $tsdukBin 'tsp.exe') -ResultFile $t35 | Out-Null
        Add-CivicCastFirewallAllowRule -Name 'CivicCast Gate A - ffmpeg' -ProgramPath $ffmpeg -ResultFile $t35 | Out-Null
        Add-CivicCastFirewallAllowRule -Name 'CivicCast Gate A - ffprobe' -ProgramPath (Join-Path $installDir 'dependencies\ffmpeg\bin\ffprobe.exe') -ResultFile $t35 | Out-Null
        Save-Summary -Step 'firewall-rules-authored'
    }

    if ($stationUp) {
        # --- T3: surfaces respond + staff auth enforced (ported from synthetic-probes) ---
        function Test-Endpoint($label, $url, $expect) {
            $st = -1
            try { $st = [int](Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop).StatusCode }
            catch { try { $st = [int]$_.Exception.Response.StatusCode.value__ } catch { $st = -1 } }
            $ok = ($st -eq $expect)
            "T3 $label -> $st (expect $expect) $(if($ok){'ok'}else{'FAIL'})" | Add-Content -Path $t35 -Encoding UTF8
            return $ok
        }
        # <gate-a-audit-MA-26> The health surface is graded on its BODY, not
        # its status line -- see Invoke-CivicCastHealthProbe. The other three
        # T3 surfaces are genuine status-code contracts (a 200 shell, a 401
        # auth refusal) and stay on Test-Endpoint.
        $t3 = $true
        $t3HealthProbe = Invoke-CivicCastHealthProbe -Url "$BASE/api/health"
        "T3 health-body -> status:$($t3HealthProbe.status) body_status:$($t3HealthProbe.body_status) body_schema:$($t3HealthProbe.body_schema) db_revision:$($t3HealthProbe.db_revision) expected_head:$($t3HealthProbe.expected_head) err:$($t3HealthProbe.error) $(if($t3HealthProbe.ok){'ok'}else{'FAIL'})" | Add-Content -Path $t35 -Encoding UTF8
        $t3 = ([bool]$t3HealthProbe.ok) -and $t3
        $t3 = (Test-Endpoint 'operator-console-200'  "$BASE/operator/"         200) -and $t3
        $t3 = (Test-Endpoint 'resident-portal-200'   "$BASE/"                  200) -and $t3
        $t3 = (Test-Endpoint 'staff-agendas-401'     "$BASE/api/staff/agendas" 401) -and $t3
        "T3_RESULT=$(if($t3){'PASS'}else{'FAIL'})" | Add-Content -Path $t35 -Encoding UTF8
        Save-Summary -Step 't3-surfaces'

        # --- T3 REAL LOOP: authenticated content loop, end to end -----------------
        # POST first-admin (loopback-admitted) -> bearer token -> 30s test
        # MP4 with the bundled ffmpeg -> upload -> package -> approve/publish ->
        # assert on /api/public/assets -> poll for offline captions. Bounded and
        # fail-honest: every HTTP step logs method/url/status to T3-LOOP.txt, and
        # any >=400 response has its body captured (via Invoke-CivicCastApi).
        $t3loop = Join-Path $OutDir 'T3-LOOP.txt'
        "checked_utc=$((Get-Date).ToUniversalTime().ToString('o'))" | Set-Content -Path $t3loop -Encoding UTF8
        $t3LoopState = 'FAIL(unstarted)'
        $token = $null
        $loopAssetId = $null
        if (-not $installDir) {
            $t3LoopState = 'SKIPPED(no-install-dir)'
            "SKIPPED: no install dir located, cannot resolve ffmpeg for asset generation" | Add-Content -Path $t3loop -Encoding UTF8
        } else {
            try {
                if ($skipReinstall) {
                    # SKIP-REINSTALL MODE: this admin was already created by a
                    # PRIOR run against the same persistent host-backed install.
                    # The installer's setup nonce lives in HKLM, which is
                    # OS/registry state and does NOT persist across Sandbox
                    # sessions (only MappedFolders do) -- so first-admin cannot
                    # be, and does not need to be, re-run. Reuse the credentials
                    # that prior run wrote to T3-CREDENTIALS.txt and sign in via
                    # /api/setup/login instead (no nonce required for login).
                    $credFile = Join-Path $OutDir 'T3-CREDENTIALS.txt'
                    if (-not (Test-Path $credFile)) { $t3LoopState = 'PARTIAL(reuse-credentials-missing)'; throw "SKIP-REINSTALL mode but $credFile not found -- cannot reuse admin credentials" }
                    $creds = @{}
                    foreach ($line in (Get-Content -Path $credFile -ErrorAction Stop)) {
                        $eqIdx = $line.IndexOf('=')
                        if ($eqIdx -gt 0) { $creds[$line.Substring(0, $eqIdx)] = $line.Substring($eqIdx + 1) }
                    }
                    if (-not $creds.ContainsKey('admin_username') -or -not $creds.ContainsKey('admin_password')) {
                        $t3LoopState = 'PARTIAL(reuse-credentials-malformed)'; throw "$credFile is missing admin_username/admin_password"
                    }
                    "reused_credentials_from=$credFile admin_username=$($creds['admin_username'])" | Add-Content -Path $t3loop -Encoding UTF8
                    $loginBody = [ordered]@{ admin_username = $creds['admin_username']; admin_password = $creds['admin_password'] }
                    $rLogin = Invoke-CivicCastApi -Method 'Post' -Url "$BASE/api/setup/login" -LogFile $t3loop -BodyObj $loginBody
                    if ($rLogin.status -ne 200 -or -not $rLogin.body_json) { $t3LoopState = 'PARTIAL(login)'; throw "setup/login failed: status=$($rLogin.status)" }
                    $token = $rLogin.body_json.operator_console_token
                    if (-not $token) { $t3LoopState = 'PARTIAL(login-token)'; throw "no operator_console_token in setup/login response" }
                    "token_acquired=true (via reused-credential login) token_length=$($token.Length)" | Add-Content -Path $t3loop -Encoding UTF8
                } else {
                    # Step 1: nothing to fetch. The installer-handoff setup nonce was
                    # RETIRED (PR #60, 2026-08-29): first setup is admitted by loopback peer
                    # address alone, and this harness runs ON the station, so it already is
                    # the trusted caller. Reading the retired registry value here is exactly
                    # what broke T3/captions/T4 on candidate #17: the key no longer exists,
                    # so the harness could never authenticate and every downstream content
                    # check failed while the product itself was healthy.
                    "nonce_not_required=true (loopback-admitted first setup, PR #60)" | Add-Content -Path $t3loop -Encoding UTF8

                    # Step 2: POST first-admin -> bearer token. Password is generated
                    # here (never hardcoded) and written to T3-CREDENTIALS.txt only --
                    # this is a disposable sandbox station, torn down with the VM.
                    $genPw = -join (((48..57) + (65..90) + (97..122)) | Get-Random -Count 20 | ForEach-Object { [char]$_ })
                    $genPw = $genPw + 'Aa1!'
                    $credFile = Join-Path $OutDir 'T3-CREDENTIALS.txt'
                    "station_name=Sandbox Proof Station" | Set-Content -Path $credFile -Encoding UTF8
                    "admin_username=sandboxproof" | Add-Content -Path $credFile -Encoding UTF8
                    "admin_password=$genPw" | Add-Content -Path $credFile -Encoding UTF8

                    $firstAdminBody = [ordered]@{
                        station_name             = 'Sandbox Proof Station'
                        admin_display_name       = 'Sandbox Proof Admin'
                        admin_username           = 'sandboxproof'
                        admin_password           = $genPw
                        recovery_kit_destination = 'sandbox automated test run -- not physically stored'
                    }
                    $r1 = Invoke-CivicCastApi -Method 'Post' -Url "$BASE/api/setup/first-admin" -LogFile $t3loop -BodyObj $firstAdminBody
                    if ($r1.status -ne 200 -or -not $r1.body_json) { $t3LoopState = 'PARTIAL(first-admin)'; throw "first-admin failed: status=$($r1.status)" }
                    $token = $r1.body_json.operator_console_token
                    if (-not $token) { $t3LoopState = 'PARTIAL(first-admin-token)'; throw "no operator_console_token in first-admin response" }
                    "token_acquired=true token_length=$($token.Length)" | Add-Content -Path $t3loop -Encoding UTF8
                }

                # Step 3: pick the T3 upload asset. Prefer the REAL LPM clip staged in
                # the mapped scripts folder (real speech -> whisper produces real caption
                # cues; the synthetic sine-tone clip gives whisper nothing to transcribe,
                # which makes a captions FAIL ambiguous). Fall back to generating the
                # synthetic clip only when no real clip is present, and say so in the log.
                $mp4Path = Join-Path $runRoot 't3-test-asset.mp4'
                $lpmClip = 'C:\CivicCastScripts\lpm-sample-short.mp4'
                if (Test-Path $lpmClip) {
                    Copy-Item -Path $lpmClip -Destination $mp4Path -Force
                    "t3_asset_source=real-lpm-clip ($lpmClip)" | Add-Content -Path $t3loop -Encoding UTF8
                } else {
                    if (-not (Test-Libopenh264 -FfmpegExe $ffmpeg)) { $t3LoopState = 'PARTIAL(ffmpeg-probe)'; throw "libopenh264 probe failed on $ffmpeg" }
                    "libopenh264_probe=ok" | Add-Content -Path $t3loop -Encoding UTF8
                    $genArgs = @(
                        '-y','-hide_banner','-loglevel','warning',
                        '-f','lavfi','-i','testsrc=size=1280x720:rate=30','-t','30',
                        '-f','lavfi','-i','sine=frequency=1000:sample_rate=48000','-t','30',
                        '-map','0:v:0','-map','1:a:0',
                        '-c:v','libopenh264','-b:v','1500k','-g','60',
                        '-c:a','aac','-b:a','96k',
                        '-movflags','+faststart',
                        $mp4Path
                    )
                    $genProc = Start-Process -FilePath $ffmpeg -ArgumentList $genArgs -PassThru -NoNewWindow -Wait -RedirectStandardError (Join-Path $OutDir 't3-generate-mp4.stderr.log')
                    "t3_asset_source=synthetic-sine-tone (captions may legitimately be empty) generate_mp4_exit=$($genProc.ExitCode)" | Add-Content -Path $t3loop -Encoding UTF8
                }
                $mp4Ok = (Test-Path $mp4Path) -and ((Get-Item $mp4Path -ErrorAction SilentlyContinue).Length -gt 0)
                "t3_asset path=$mp4Path exists=$mp4Ok bytes=$((Get-Item $mp4Path -ErrorAction SilentlyContinue).Length)" | Add-Content -Path $t3loop -Encoding UTF8
                if (-not $mp4Ok) { $t3LoopState = 'PARTIAL(generate-mp4)'; throw "T3 asset staging failed" }

                # Step 4: upload. Asset id/title are always freshly generated
                # (timestamp + random suffix) so a SKIP-REINSTALL run against an
                # install that already has a previously-published asset is still
                # self-contained -- it can never collide with (or be mistaken
                # for) a prior run's asset.
                $loopAssetSuffix = -join ((97..122) | Get-Random -Count 4 | ForEach-Object { [char]$_ })
                $loopAssetId = 'sandbox-proof-' + (Get-Date -Format 'yyMMddHHmmss') + '-' + $loopAssetSuffix
                $loopAssetTitle = 'Sandbox Proof Asset ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
                $up = Invoke-AssetUpload -BaseUrl $BASE -Token $token -AssetId $loopAssetId -Title $loopAssetTitle -FilePath $mp4Path -LogFile $t3loop
                if ($up.status -ne 201) { $t3LoopState = 'PARTIAL(upload)'; throw "upload failed: status=$($up.status)" }
                "uploaded_asset_id=$loopAssetId" | Add-Content -Path $t3loop -Encoding UTF8

                # Step 5: package.
                $pkg = Invoke-CivicCastApi -Method 'Post' -Url "$BASE/api/staff/assets/$loopAssetId/package" -LogFile $t3loop -BearerToken $token
                if ($pkg.status -ne 200) { $t3LoopState = 'PARTIAL(package)'; throw "package failed: status=$($pkg.status)" }

                # Step 6: approve/publish (portal surface).
                $approveBody = [ordered]@{ operator_id = 'sandboxproof'; operator_display_name = 'Sandbox Proof Admin' }
                $appr = Invoke-CivicCastApi -Method 'Post' -Url "$BASE/api/staff/publish/assets/$loopAssetId/approve" -LogFile $t3loop -BodyObj $approveBody -BearerToken $token
                if ($appr.status -ne 200) { $t3LoopState = 'PARTIAL(approve)'; throw "approve failed: status=$($appr.status)" }

                # Step 7: assert it appears on the PUBLIC surface (bounded poll, 60s).
                $pubDeadline = (Get-Date).AddSeconds(60)
                $foundPublic = $false
                while ((Get-Date) -lt $pubDeadline -and -not $foundPublic) {
                    $pub = Invoke-CivicCastApi -Method 'Get' -Url "$BASE/api/public/assets" -LogFile $t3loop
                    if ($pub.status -eq 200 -and $pub.body_json) {
                        $hit = $pub.body_json | Where-Object { $_.asset_id -eq $loopAssetId -or $_.id -eq $loopAssetId }
                        if ($hit) { $foundPublic = $true; break }
                    }
                    Start-Sleep -Seconds 5
                }
                "asset_found_public=$foundPublic asset_id=$loopAssetId" | Add-Content -Path $t3loop -Encoding UTF8
                if (-not $foundPublic) { $t3LoopState = 'PARTIAL(public-listing)'; throw "asset $loopAssetId not found on /api/public/assets within 60s" }
                Save-Summary -Step 't3-loop-published'

                # Step 8: poll for offline captions (bounded 20 min -- CPU whisper is
                # slow). Endpoint discovery is dynamic rather than hardcoded: this
                # kit's build can differ from what was last probed, so fetch
                # openapi.json in-sandbox and look for a captions/offline-jobs-shaped
                # staff route before falling back to the documented default path.
                # Do not hard-fail on schema drift -- capture raw responses either way.
                #
                # EVIDENCE FIX: earlier version only logged status:200 per poll, which
                # cannot distinguish "no job was ever enqueued" (audit finding K3-1:
                # publish-time enqueue swallows exceptions) from "job is still running
                # on CPU whisper". Now: (a) every poll's FULL body is captured to
                # T3-CAPTIONS.txt for the first poll, any poll whose body differs from
                # the last logged one, and the final poll; (b) if the job list is empty
                # for this asset right after publish, attempt a manual enqueue
                # (POST route discovered from the same openapi.json fetch) and log
                # that request+response, then resume polling as normal.
                $capEndpoint = $null
                $capEnqueueEndpoint = $null
                $capSpecPaths = $null
                try {
                    $spec = Invoke-CivicCastApi -Method 'Get' -Url "$BASE/openapi.json" -LogFile $t3loop -BearerToken $token
                    if ($spec.status -eq 200 -and $spec.body_json -and $spec.body_json.paths) {
                        $capSpecPaths = $spec.body_json.paths
                        $allPaths = $capSpecPaths.PSObject.Properties.Name
                        $capPaths = $allPaths | Where-Object { $_ -like '*caption*offline*job*' -or $_ -like '*offline*caption*job*' }
                        if (-not $capPaths) { $capPaths = $allPaths | Where-Object { $_ -like '*caption*job*' } }
                        if ($capPaths) { $capEndpoint = ($capPaths | Select-Object -First 1) }
                    }
                } catch { "caption endpoint discovery error: $_" | Add-Content -Path $t3loop -Encoding UTF8 }
                if (-not $capEndpoint) { $capEndpoint = '/api/staff/captions/offline-jobs' }
                "caption_endpoint=$capEndpoint" | Add-Content -Path $t3loop -Encoding UTF8

                # Manual-enqueue route discovery: prefer a POST method on the SAME
                # collection path we poll (the common REST shape); else any other
                # caption/job-shaped path that declares a POST.
                if ($capSpecPaths) {
                    $capEndpointNode = $capSpecPaths.PSObject.Properties | Where-Object { $_.Name -eq $capEndpoint } | Select-Object -First 1
                    if ($capEndpointNode -and ($capEndpointNode.Value.PSObject.Properties.Name -contains 'post')) {
                        $capEnqueueEndpoint = $capEndpoint
                    } else {
                        $postCapPaths = $capSpecPaths.PSObject.Properties | Where-Object {
                            ($_.Name -like '*caption*job*') -and ($_.Value.PSObject.Properties.Name -contains 'post')
                        }
                        if ($postCapPaths) { $capEnqueueEndpoint = ($postCapPaths | Select-Object -First 1).Name }
                    }
                }
                "caption_enqueue_endpoint=$capEnqueueEndpoint" | Add-Content -Path $t3loop -Encoding UTF8

                $capLog = Join-Path $OutDir 'T3-CAPTIONS.txt'
                "asset_id=$loopAssetId" | Set-Content -Path $capLog -Encoding UTF8
                "caption_endpoint=$capEndpoint" | Add-Content -Path $capLog -Encoding UTF8
                "caption_enqueue_endpoint=$capEnqueueEndpoint" | Add-Content -Path $capLog -Encoding UTF8

                $vttFound = $false
                $vttInfo = $null
                $capDeadline = (Get-Date).AddMinutes(20)
                $capBeat = 0
                $lastCapBodyRaw = $null
                $everSawJobForAsset = $false
                $capRouteMissing = $false
                $manualEnqueueAttempted = $false
                $manualEnqueueSucceeded = $false
                $manualEnqueueDetail = $null
                while ((Get-Date) -lt $capDeadline -and -not $vttFound) {
                    $capBeat++
                    $isFirstPoll = ($capBeat -eq 1)
                    $cj = Invoke-CivicCastApi -Method 'Get' -Url "$BASE$capEndpoint" -LogFile $t3loop -BearerToken $token
                    if ($cj.status -eq 404) {
                        "caption_endpoint_404 -- not present on this build; aborting caption poll" | Add-Content -Path $t3loop -Encoding UTF8
                        "---- poll #$capBeat (FINAL, 404) $((Get-Date).ToUniversalTime().ToString('o')) ----" | Add-Content -Path $capLog -Encoding UTF8
                        "$($cj.body_raw)" | Add-Content -Path $capLog -Encoding UTF8
                        $capRouteMissing = $true
                        break
                    }

                    $bodyChanged = ($cj.body_raw -ne $lastCapBodyRaw)
                    if ($isFirstPoll -or $bodyChanged) {
                        "---- poll #$capBeat $((Get-Date).ToUniversalTime().ToString('o')) status=$($cj.status) (first=$isFirstPoll changed=$bodyChanged) ----" | Add-Content -Path $capLog -Encoding UTF8
                        "$($cj.body_raw)" | Add-Content -Path $capLog -Encoding UTF8
                    }
                    $lastCapBodyRaw = $cj.body_raw

                    $jobsForAsset = @()
                    if ($cj.status -eq 200 -and $cj.body_json) {
                        $jobs = $cj.body_json
                        if ($jobs.PSObject -and ($jobs.PSObject.Properties.Name -contains 'jobs')) { $jobs = $jobs.jobs }
                        $jobsForAsset = @($jobs) | Where-Object {
                            ($_.asset_id -eq $loopAssetId) -or ($_.assetId -eq $loopAssetId) -or ($_.target_asset_id -eq $loopAssetId)
                        }
                    }
                    if ($jobsForAsset.Count -gt 0) { $everSawJobForAsset = $true }

                    foreach ($j in $jobsForAsset) {
                        $cueCount = $null
                        foreach ($f in @('cue_count','cueCount')) {
                            if ($j.PSObject.Properties.Name -contains $f) { $cueCount = $j.$f }
                        }
                        if ($cueCount -and [int]$cueCount -gt 0) { $vttFound = $true; $vttInfo = $j; break }
                    }

                    # K3-1 check + manual enqueue, attempted ONCE on the first poll only
                    # if nothing is queued for this asset yet. Publish's own enqueue is
                    # expected to be near-immediate, so an empty list this early either
                    # means K3-1 (swallowed exception, nothing was ever queued) or a
                    # genuinely broken/undiscoverable API -- attempting immediately
                    # (rather than waiting out the full 20-minute bound) keeps this
                    # bounded and leaves the rest of the window to observe completion.
                    if ($isFirstPoll -and -not $everSawJobForAsset -and -not $manualEnqueueAttempted) {
                        $manualEnqueueAttempted = $true
                        if (-not $capEnqueueEndpoint) {
                            $manualEnqueueDetail = 'no POST enqueue route discovered in openapi.json'
                            "manual_enqueue_attempted=true route=none detail=$manualEnqueueDetail" | Add-Content -Path $capLog -Encoding UTF8
                        } else {
                            $enqueueUrl = "$BASE$capEnqueueEndpoint"
                            # The batch route takes a LIST (RC4 Gate A 2026-11-04 sent asset_id
                            # and got 422 "body.asset_ids Field required"); single routes take one id.
                            if ($capEnqueueEndpoint -like '*/batch') {
                                $enqueueBody = [ordered]@{ asset_ids = @($loopAssetId) }
                            } else {
                                $enqueueBody = [ordered]@{ asset_id = $loopAssetId }
                            }
                            "manual_enqueue_request: POST $enqueueUrl body=$(($enqueueBody | ConvertTo-Json -Compress))" | Add-Content -Path $capLog -Encoding UTF8
                            $enq = Invoke-CivicCastApi -Method 'Post' -Url $enqueueUrl -LogFile $t3loop -BodyObj $enqueueBody -BearerToken $token
                            "manual_enqueue_response: status=$($enq.status) body=$($enq.body_raw)" | Add-Content -Path $capLog -Encoding UTF8
                            if ($enq.status -ge 200 -and $enq.status -lt 300) {
                                $manualEnqueueSucceeded = $true
                                $manualEnqueueDetail = "enqueue POST succeeded (status=$($enq.status))"
                            } else {
                                $manualEnqueueDetail = "enqueue POST failed (status=$($enq.status))"
                            }
                        }
                    }

                    Save-Summary -Step "t3-loop-caption-poll-$capBeat"
                    if (-not $vttFound) { Start-Sleep -Seconds 30 }
                }
                if (-not $capRouteMissing) {
                    "---- poll #$capBeat (FINAL) $((Get-Date).ToUniversalTime().ToString('o')) ----" | Add-Content -Path $capLog -Encoding UTF8
                    "$lastCapBodyRaw" | Add-Content -Path $capLog -Encoding UTF8
                }

                if ($vttFound) {
                    ($vttInfo | ConvertTo-Json -Depth 6) | Set-Content -Path (Join-Path $OutDir 'T3-caption-artifact.json') -Encoding UTF8
                    "captions_result=PASS cue_count_found=true" | Add-Content -Path $t3loop -Encoding UTF8
                    "CAPTIONS=PASS" | Add-Content -Path $capLog -Encoding UTF8
                    "CAPTIONS=PASS" | Add-Content -Path $t3loop -Encoding UTF8
                    $t3LoopState = 'PASS'
                } elseif ($everSawJobForAsset) {
                    # A job for the asset was OBSERVED, so auto-enqueue demonstrably
                    # worked -- this must outrank any "no enqueue route" conclusion
                    # (the 2026-08-19 run mislabeled exactly this: completed job in
                    # the poll bodies, verdict said FAIL_NO_ENQUEUE_ROUTE because the
                    # manual-enqueue POST probe 404'd).
                    $completeZeroCues = ($lastCapBodyRaw -match '"state"\s*:\s*"complete"') -and
                                        ($lastCapBodyRaw -match '"cue_count"\s*:\s*0') -and
                                        ($lastCapBodyRaw -match '"last_error"\s*:\s*""')
                    if ($completeZeroCues) {
                        # Pipeline ran to completion with no error and produced zero
                        # cues -- the honest outcome for a no-speech (sine-tone) clip.
                        # Mechanism proven; content proof needs a real-speech upload.
                        "captions_result=COMPLETE_ZERO_CUES (job completed clean; zero cues -- expected for a no-speech source)" | Add-Content -Path $t3loop -Encoding UTF8
                        "CAPTIONS=COMPLETE_ZERO_CUES" | Add-Content -Path $capLog -Encoding UTF8
                        "CAPTIONS=COMPLETE_ZERO_CUES" | Add-Content -Path $t3loop -Encoding UTF8
                        $t3LoopState = 'PARTIAL(captions-zero-cues)'
                    } else {
                        # Job existed but none completed with cue_count>0 inside the
                        # bounded window -- most likely still running (CPU whisper is
                        # slow), but PASS requires evidence.
                        "captions_result=FAIL_ENQUEUED_NO_COMPLETE (polled $capBeat times over up to 20 min)" | Add-Content -Path $t3loop -Encoding UTF8
                        "CAPTIONS=FAIL_ENQUEUED_NO_COMPLETE" | Add-Content -Path $capLog -Encoding UTF8
                        "CAPTIONS=FAIL_ENQUEUED_NO_COMPLETE" | Add-Content -Path $t3loop -Encoding UTF8
                        $t3LoopState = 'PARTIAL(captions)'
                    }
                } elseif ($capRouteMissing -or (-not $capEndpoint)) {
                    "captions_result=FAIL_NO_ENQUEUE_ROUTE (caption endpoint not present on this build)" | Add-Content -Path $t3loop -Encoding UTF8
                    "CAPTIONS=FAIL_NO_ENQUEUE_ROUTE" | Add-Content -Path $capLog -Encoding UTF8
                    "CAPTIONS=FAIL_NO_ENQUEUE_ROUTE" | Add-Content -Path $t3loop -Encoding UTF8
                    $t3LoopState = 'PARTIAL(captions)'
                } elseif ($manualEnqueueAttempted -and $manualEnqueueSucceeded) {
                    # Publish's own auto-enqueue never produced a job for this asset
                    # (K3-1: publish enqueue swallows exceptions), but the SAME
                    # endpoint accepted a manually-triggered enqueue -- confirms the
                    # caption pipeline itself works and isolates the bug to the
                    # publish-time enqueue call specifically.
                    "captions_result=FAIL_NEVER_ENQUEUED (K3-1 confirmed: publish never enqueued a job for $loopAssetId; manual enqueue via $capEnqueueEndpoint succeeded -- $manualEnqueueDetail)" | Add-Content -Path $t3loop -Encoding UTF8
                    "CAPTIONS=FAIL_NEVER_ENQUEUED (K3-1 confirmed, manual enqueue worked)" | Add-Content -Path $capLog -Encoding UTF8
                    "CAPTIONS=FAIL_NEVER_ENQUEUED (K3-1 confirmed, manual enqueue worked)" | Add-Content -Path $t3loop -Encoding UTF8
                    $t3LoopState = 'PARTIAL(captions)'
                } else {
                    # Never saw a job for this asset, and either no enqueue route was
                    # discoverable or the manual enqueue attempt itself failed --
                    # cannot distinguish K3-1 from a broken/undiscoverable API here.
                    "captions_result=FAIL_NO_ENQUEUE_ROUTE (manual_enqueue_attempted=$manualEnqueueAttempted detail=$manualEnqueueDetail)" | Add-Content -Path $t3loop -Encoding UTF8
                    "CAPTIONS=FAIL_NO_ENQUEUE_ROUTE" | Add-Content -Path $capLog -Encoding UTF8
                    "CAPTIONS=FAIL_NO_ENQUEUE_ROUTE" | Add-Content -Path $t3loop -Encoding UTF8
                    $t3LoopState = 'PARTIAL(captions)'
                }
            } catch {
                "T3_LOOP_ERROR: $_" | Add-Content -Path $t3loop -Encoding UTF8
            }
        }
        "T3_LOOP=$t3LoopState" | Add-Content -Path $t3loop -Encoding UTF8
        Save-Summary -Step 't3-real-loop'

        # --- T4: PRODUCT-ENGINE EGRESS (QA-F2 fix) ---
        # QA-F2: the prior proof drove raw ffmpeg straight at a bare UDP port --
        # that proves ffmpeg works, not that CivicCast's own egress engine
        # (staff config -> daemon -> sink) works. Attempt to drive that real
        # engine through its staff API first (discover routes from the SERVED
        # openapi.json, PUT a udp-ts sink config, POST a start command, verify
        # with tsp using the same command pattern as the kit's verify-egress.ps1).
        # Only if that path is genuinely blocked does this fall back to the
        # synthetic-ffmpeg proof -- and that result is labeled
        # PASS_FFMPEG_FALLBACK, never plain PASS, so a fallback can never be
        # mistaken for proof the product's own engine works. No silent
        # substitution -- that is the whole point of this audit finding.
        $t4notes = Join-Path $OutDir 'T4-ENGINE-NOTES.txt'
        "checked_utc=$((Get-Date).ToUniversalTime().ToString('o'))" | Set-Content -Path $t4notes -Encoding UTF8
        $t4EnginePath = $false
        $t4EngineBlockReason = $null
        $engineToken = $token   # reuse the T3 REAL LOOP bearer token if we have one
        if (-not $engineToken) {
            $t4EngineBlockReason = 'no-bearer-token (T3 REAL LOOP did not reach a staff token)'
            "$t4EngineBlockReason -- egress config/commands routes require staff auth, so the product-engine path cannot be attempted without T3 having authenticated first" | Add-Content -Path $t4notes -Encoding UTF8
        } else {
            try {
                # (a) Discovery via the SERVED openapi.json, not a recursive
                # site-packages grep. This harness was hardened once already
                # (see file header) against unbounded recursive filesystem
                # scans over Sandbox's slow virtualized/differencing disk --
                # deliberately not reintroducing that class of scan here when
                # the served spec already gives an authoritative, cheap answer.
                $specR = Invoke-CivicCastApi -Method 'Get' -Url "$BASE/openapi.json" -LogFile $t4notes -BearerToken $engineToken
                $egressPaths = @()
                if ($specR.status -eq 200 -and $specR.body_json -and $specR.body_json.paths) {
                    $egressPaths = @($specR.body_json.paths.PSObject.Properties.Name | Where-Object { $_ -like '*egress*' } | Sort-Object)
                }
                "discovered_egress_paths_count=$($egressPaths.Count)" | Add-Content -Path $t4notes -Encoding UTF8
                $egressPaths | ForEach-Object { "  $_" | Add-Content -Path $t4notes -Encoding UTF8 }

                $configPath = $egressPaths | Where-Object { $_ -like '*channels/{channel_id}/config' } | Select-Object -First 1
                $commandsPath = $egressPaths | Where-Object { $_ -like '*channels/{channel_id}/commands' } | Select-Object -First 1
                $statePath = $egressPaths | Where-Object { $_ -like '*channels/{channel_id}/state' } | Select-Object -First 1
                $healthPath = $egressPaths | Where-Object { $_ -like '*channels/{channel_id}/health' } | Select-Object -First 1

                if (-not $configPath -or -not $commandsPath) {
                    $t4EngineBlockReason = 'no config+commands egress route discovered in openapi.json'
                    "$t4EngineBlockReason" | Add-Content -Path $t4notes -Encoding UTF8
                } else {
                    # Dedicated port (19003), distinct from the fallback's fixed
                    # 9001-9003, so the two paths can never collide even if a
                    # stop/teardown races a later fallback run.
                    $engineChannel = 'government'
                    $enginePort = 19003
                    # Bug fix (sandbox run evidence, T4-ENGINE-NOTES.txt): the bare
                    # "$enginePort?pkt_size=1316" form sent a mangled uri ("udp://
                    # 127.0.0.1:=1316" per the 422's echoed input) -- braced ${}
                    # interpolation removes any ambiguity at the "?". Also: unlike
                    # the raw-ffmpeg fallback below (which needs ?pkt_size for
                    # ffmpeg's own UDP muxer), the EgressConfig sink schema takes a
                    # plain udp://host:port uri with NO query string -- packetization/
                    # latency are separate typed fields (latency_ms, below), not URI
                    # params, so the query string is dropped entirely here rather
                    # than just re-escaped.
                    $engineUri = "udp://127.0.0.1:${enginePort}"
                    $configUrl = "$BASE" + ($configPath -replace '\{channel_id\}', $engineChannel)
                    $commandsUrl = "$BASE" + ($commandsPath -replace '\{channel_id\}', $engineChannel)
                    "config_url=$configUrl" | Add-Content -Path $t4notes -Encoding UTF8
                    "commands_url=$commandsUrl" | Add-Content -Path $t4notes -Encoding UTF8

                    $cfgBody = [ordered]@{
                        channel_id              = $engineChannel
                        enabled                 = $true
                        auto_start              = $false
                        allow_software_fallback = $true
                        fill_policy             = 'slate'
                        slate_message           = 'Sandbox proof run -- product-engine egress test, no live source configured.'
                        sinks                   = @(
                            [ordered]@{
                                kind                    = 'udp-ts'
                                label                   = 'sandbox-proof-engine'
                                uri                     = $engineUri
                                latency_ms              = 2000
                                loudness_regime         = 'inherit'
                                eas_tone_strip_enabled  = $true
                            }
                        )
                    }
                    $cfgR = Invoke-CivicCastApi -Method 'Put' -Url $configUrl -LogFile $t4notes -BodyObj $cfgBody -BearerToken $engineToken
                    if ($cfgR.status -ne 200) {
                        $t4EngineBlockReason = "config PUT failed: status=$($cfgR.status)"
                        "$t4EngineBlockReason" | Add-Content -Path $t4notes -Encoding UTF8
                    } else {
                        $startR = Invoke-CivicCastApi -Method 'Post' -Url $commandsUrl -LogFile $t4notes -BodyObj (@{ action = 'start' }) -BearerToken $engineToken
                        if ($startR.status -ne 202) {
                            $t4EngineBlockReason = "start command failed: status=$($startR.status)"
                            "$t4EngineBlockReason" | Add-Content -Path $t4notes -Encoding UTF8
                        } else {
                            # Gate A T4 diagnosis fix (2026-09): a FIXED 20s sleep gave
                            # the daemon no more than an arbitrary head start and then
                            # opened the tsp capture window regardless of whether the
                            # engine's sink had actually connected -- both beta.3 and
                            # beta.4 were found at engine_state=FALLBACK_SLATE with the
                            # capture racing a channel that was never on air, and the
                            # probe recorded only $stR.body_json.state (no last_error,
                            # no sink map), so there was no trail explaining why. Replace
                            # the fixed sleep with a BOUNDED poll (5s interval, 60s
                            # budget total -- same order of magnitude as the old fixed
                            # wait, but adaptive) of BOTH /state (engine_state +
                            # last_error) and /health (the sink_connected map): open the
                            # tsp capture window as soon as EITHER report shows a
                            # connected sink, so the capture never races a cold start on
                            # a healthy box and never hangs past the budget on a broken
                            # one. Every poll tick, and the full final state/health
                            # bodies, are written to T4-ENGINE-NOTES.txt (and two sibling
                            # JSON files) so a FALLBACK_SLATE this run hits has an actual
                            # diagnostic trail instead of a bare state string.
                            # Test-TsProof's own verdict logic is UNCHANGED below.
                            $stateUrl = if ($statePath) { "$BASE" + ($statePath -replace '\{channel_id\}', $engineChannel) } else { $null }
                            $healthUrl = if ($healthPath) { "$BASE" + ($healthPath -replace '\{channel_id\}', $engineChannel) } else { $null }
                            "state_url=$stateUrl" | Add-Content -Path $t4notes -Encoding UTF8
                            "health_url=$healthUrl" | Add-Content -Path $t4notes -Encoding UTF8

                            $pollBudgetSeconds = 60
                            $pollIntervalSeconds = 5
                            $pollElapsed = 0
                            $sinkConnected = $false
                            $lastStateBody = $null
                            $lastHealthBody = $null
                            while ($true) {
                                if ($stateUrl) {
                                    $stR = Invoke-CivicCastApi -Method 'Get' -Url $stateUrl -LogFile $t4notes -BearerToken $engineToken
                                    if ($stR.body_json) {
                                        $lastStateBody = $stR.body_json
                                        "poll t=${pollElapsed}s engine_state=$($lastStateBody.state) last_error=$($lastStateBody.last_error)" | Add-Content -Path $t4notes -Encoding UTF8
                                    }
                                }
                                if ($healthUrl) {
                                    $hR = Invoke-CivicCastApi -Method 'Get' -Url "${healthUrl}?limit=1" -LogFile $t4notes -BearerToken $engineToken
                                    if ($hR.body_json -and @($hR.body_json).Count -gt 0) {
                                        $lastHealthBody = @($hR.body_json)[0]
                                        $sinkMap = $lastHealthBody.sink_connected
                                        $sinkJson = if ($sinkMap) { ($sinkMap | ConvertTo-Json -Compress -Depth 4) } else { '{}' }
                                        "poll t=${pollElapsed}s health_state=$($lastHealthBody.state) sink_connected=$sinkJson" | Add-Content -Path $t4notes -Encoding UTF8
                                        if ($sinkMap) {
                                            $sinkConnected = @($sinkMap.PSObject.Properties | Where-Object { $_.Value -eq $true }).Count -gt 0
                                        }
                                    }
                                }
                                if ($sinkConnected) {
                                    "poll t=${pollElapsed}s: sink reported connected -- opening tsp capture window now" | Add-Content -Path $t4notes -Encoding UTF8
                                    break
                                }
                                if ($pollElapsed -ge $pollBudgetSeconds) {
                                    "poll budget exhausted (${pollBudgetSeconds}s) without a connected sink -- opening tsp capture window anyway (best-effort, same posture as the prior fixed-sleep behavior)" | Add-Content -Path $t4notes -Encoding UTF8
                                    break
                                }
                                Start-Sleep -Seconds $pollIntervalSeconds
                                $pollElapsed += $pollIntervalSeconds
                            }
                            "poll_timeline_seconds=$pollElapsed sink_connected_before_capture=$sinkConnected" | Add-Content -Path $t4notes -Encoding UTF8
                            if ($lastStateBody) {
                                "engine_state=$($lastStateBody.state)" | Add-Content -Path $t4notes -Encoding UTF8
                                "engine_last_error=$($lastStateBody.last_error)" | Add-Content -Path $t4notes -Encoding UTF8
                                ($lastStateBody | ConvertTo-Json -Depth 8) | Set-Content -Path (Join-Path $OutDir 'T4-ENGINE-STATE-BODY.json') -Encoding UTF8
                            }
                            if ($lastHealthBody) {
                                ($lastHealthBody | ConvertTo-Json -Depth 8) | Set-Content -Path (Join-Path $OutDir 'T4-ENGINE-HEALTH-BODY.json') -Encoding UTF8
                            }
                            $tspExe = Join-Path $tsdukBin 'tsp.exe'
                            $tsProof = Test-TsProof -TspExe $tspExe -Port $enginePort -Seconds 8 -OutDir $OutDir -Label 'engine-government'
                            ($tsProof | ConvertTo-Json -Depth 6) | Set-Content -Path (Join-Path $OutDir 'egress-verify-engine.json') -Encoding UTF8
                            "engine_tsp_verdict=$($tsProof.verdict) invalid_syncs=$($tsProof.invalid_syncs) transport_errors=$($tsProof.transport_errors) discontinuities=$($tsProof.discontinuities)" | Add-Content -Path $t4notes -Encoding UTF8
                            # Bounded, best-effort stop of the daemon we started.
                            try { Invoke-CivicCastApi -Method 'Post' -Url $commandsUrl -LogFile $t4notes -BodyObj (@{ action = 'stop' }) -BearerToken $engineToken | Out-Null } catch {}
                            if ($tsProof.verdict -eq 'pass') {
                                $t4EnginePath = $true
                            } else {
                                $t4EngineBlockReason = "engine started but tsp verification did not pass (verdict=$($tsProof.verdict))"
                                "$t4EngineBlockReason" | Add-Content -Path $t4notes -Encoding UTF8
                            }
                        }
                    }
                }
            } catch {
                $t4EngineBlockReason = "exception during product-engine attempt: $_"
                "$t4EngineBlockReason" | Add-Content -Path $t4notes -Encoding UTF8
            }
        }
        "t4_engine_path_succeeded=$t4EnginePath" | Add-Content -Path $t4notes -Encoding UTF8
        Save-Summary -Step 't4-engine-attempt'

        if ($t4EnginePath) {
            "T4_RESULT=PASS_PRODUCT_ENGINE" | Add-Content -Path $t35 -Encoding UTF8
            Save-Summary -Step 't4-egress-product-engine'
        } else {
            "T4_engine_path_blocked_reason=$t4EngineBlockReason (see T4-ENGINE-NOTES.txt)" | Add-Content -Path $t35 -Encoding UTF8
            # --- T4 fallback: synthetic encoders + TSDuck verify ---
            # The shipped ffmpeg is the LGPL/version3 pack build: it carries libopenh264 +
            # h264_nvenc but NOT GPL libx264/libx265 (by design). The native stream code
            # (_ffmpeg.py resolve_h264_encoder: nvenc->mf->libopenh264->libx264) resolves to
            # libopenh264 on a CPU box, so the egress proof must use that encoder. The kit's
            # start-encoders.ps1 hardcodes libx264 (assumes a tester-supplied GPL ffmpeg), so
            # we spawn the encoders inline here with the shipped encoder and leave the kit's
            # verify-egress.ps1 (the actual TSDuck assertion) untouched.
            try {
                $t4chs = @(@{c='public';p=9001}, @{c='education';p=9002}, @{c='government';p=9003})
                $t4procs = @()
                $t4logs = Join-Path $runRoot 'logs'
                New-Item -ItemType Directory -Force -Path $t4logs | Out-Null
                foreach ($e in $t4chs) {
                    $a = @(
                        '-y','-hide_banner','-loglevel','warning',
                        '-f','lavfi','-re','-i','color=size=1280x720:rate=30:color=#1A1A1A,format=yuv420p',
                        '-f','lavfi','-re','-i','sine=frequency=1000:sample_rate=48000',
                        '-map','0:v:0','-map','1:a:0',
                        '-c:v','libopenh264','-b:v','1500k','-g','60',
                        '-c:a','aac','-b:a','96k',
                        '-f','mpegts','-fflags','+genpts','-flush_packets','1',
                        "udp://127.0.0.1:$($e.p)?pkt_size=1316"
                    )
                    $t4procs += (Start-Process -FilePath $ffmpeg -ArgumentList $a -PassThru -NoNewWindow -RedirectStandardError (Join-Path $t4logs "ffmpeg-$($e.c).log"))
                }
                ("T4 encoders started: " + (($t4procs | ForEach-Object { $_.Id }) -join ',')) | Add-Content -Path (Join-Path $OutDir 'start-encoders.log')
                Start-Sleep -Seconds 20
                & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $soakDir 'scripts\verify-egress.ps1') -HeartbeatIndex 1 -Seconds 8 *> (Join-Path $OutDir 'verify-egress.log')
                foreach ($p in $t4procs) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
                $ev = Get-ChildItem (Join-Path $runRoot 'egress-verify') -Filter '*.json' -ErrorAction SilentlyContinue | Select-Object -Last 1
                if ($ev) {
                    Copy-Item $ev.FullName (Join-Path $OutDir 'egress-verify.json') -Force
                    $body = Get-Content $ev.FullName -Raw
                    $pass = ($body -match '"invalid_syncs"\s*:\s*0') -and ($body -match '"transport_errors"\s*:\s*0') -and ($body -match '"discontinuities"\s*:\s*0')
                    "T4_RESULT=$(if($pass){'PASS_FFMPEG_FALLBACK'}else{'CHECK egress-verify.json'})" | Add-Content -Path $t35 -Encoding UTF8
                } else {
                    "T4_RESULT=NO_ARTIFACT (see start-encoders.log / verify-egress.log)" | Add-Content -Path $t35 -Encoding UTF8
                }
            } catch { "T4_ERROR=$_" | Add-Content -Path $t35 -Encoding UTF8 }
            Save-Summary -Step 't4-egress-ffmpeg-fallback'
        }

        # --- T5: bounded soak (default 20 min; SOAK_MINUTES.txt overrides for full 4h) ---
        $soakMin = 20
        try { if (Test-Path (Join-Path $OutDir 'SOAK_MINUTES.txt')) { $soakMin = [int]((Get-Content (Join-Path $OutDir 'SOAK_MINUTES.txt') | Select-Object -First 1).Trim()) } } catch {}
        "T5_soak_minutes=$soakMin" | Add-Content -Path $t35 -Encoding UTF8
        $soakStart = Get-Date
        $soakEnd = $soakStart.AddMinutes($soakMin)
        $beat = 0; $soakFail = 0
        # <gate-a-audit-MA-26> Every beat is graded on the /health BODY, and
        # each beat's body_status/body_schema are recorded, so a soak over a
        # degraded/schema-behind station can never record unhealthy=0 again.
        # The first beat runs BEFORE the deadline test so a zero-minute soak
        # still produces one real observation rather than beats=0 (MN-20).
        do {
            $beat++
            $hp = Invoke-CivicCastHealthProbe -Url "$BASE/api/health"
            if (-not $hp.ok) { $soakFail++ }
            "T5 beat=$beat health=$($hp.status) body_status=$($hp.body_status) body_schema=$($hp.body_schema) ok=$($hp.ok) $((Get-Date).ToString('o'))" | Add-Content -Path $t35 -Encoding UTF8
            Save-Summary -Step "t5-beat-$beat"
            if ((Get-Date) -ge $soakEnd) { break }
            Start-Sleep -Seconds 300
        } while ((Get-Date) -lt $soakEnd)
        $soakElapsed = [Math]::Round((New-TimeSpan -Start $soakStart -End (Get-Date)).TotalSeconds, 1)
        "T5_elapsed_seconds=$soakElapsed" | Add-Content -Path $t35 -Encoding UTF8
        "T5_RESULT=$(if($soakFail -eq 0){'PASS'}else{'FAIL'}) beats=$beat unhealthy=$soakFail" | Add-Content -Path $t35 -Encoding UTF8
        Save-Summary -Step 't5-soak-complete'
    } else {
        # HARDENED <gate-a-station-up-wait-and-log-capture>: EXPLICITLY skip
        # T3/T4/T5 -- write each of their own result files with a real
        # SKIPPED verdict line (never leave them absent/ambiguous, and never
        # attempt T4's product-engine call, ffmpeg-fallback encoder run, or
        # T5's 20-minute soak against a station that never came up; that is
        # what left the 8579e66-run3 script hung with no forward progress
        # for 30+ minutes after t2-render-assert). scripts/gate_a_verdict.py
        # already fails closed on a SKIPPED value for every one of these
        # (only an explicit PASS / PASS_PRODUCT_ENGINE line passes), so this
        # is purely about leaving an honest, bounded, non-hanging trail --
        # not a verdict-judge contract change.
        "T3T5_SKIPPED=station health check did not pass" | Add-Content -Path $t35 -Encoding UTF8
        "T3_RESULT=SKIPPED(station-down)" | Add-Content -Path $t35 -Encoding UTF8
        "T4_RESULT=SKIPPED(station-down)" | Add-Content -Path $t35 -Encoding UTF8
        "T5_soak_minutes=0 (skipped)" | Add-Content -Path $t35 -Encoding UTF8
        "T5_RESULT=SKIPPED beats=0 unhealthy=0" | Add-Content -Path $t35 -Encoding UTF8

        $t3loopSkip = Join-Path $OutDir 'T3-LOOP.txt'
        "checked_utc=$((Get-Date).ToUniversalTime().ToString('o'))" | Set-Content -Path $t3loopSkip -Encoding UTF8
        "SKIPPED: station health check did not pass within the 20-minute station-up-wait deadline -- see STATION-UP-WAIT.txt and station-diag\after-station-up-wait\ for why" | Add-Content -Path $t3loopSkip -Encoding UTF8
        "T3_LOOP=SKIPPED(station-down)" | Add-Content -Path $t3loopSkip -Encoding UTF8
        "CAPTIONS=SKIPPED(station-down)" | Add-Content -Path $t3loopSkip -Encoding UTF8

        $t4notesSkip = Join-Path $OutDir 'T4-ENGINE-NOTES.txt'
        "checked_utc=$((Get-Date).ToUniversalTime().ToString('o'))" | Set-Content -Path $t4notesSkip -Encoding UTF8
        "SKIPPED: station health check did not pass -- the product-engine attempt requires a staff bearer token from T3, which never ran" | Add-Content -Path $t4notesSkip -Encoding UTF8
        "t4_engine_path_succeeded=false" | Add-Content -Path $t4notesSkip -Encoding UTF8

        Save-Summary -Step 't3t5-skipped-station-down'
    }

    # Station diagnostics -- SECOND capture point, still inside the try
    # block right after the T3/T4/T5 decision (pass-through path OR the
    # skip path above), before the installer-log/event-log tail steps.
    # This is IN ADDITION TO the unconditional final capture in the
    # `finally` block below (the task's "before ... AND again at the end"),
    # so a soak-loop hang or an exception in steps 6/7 still leaves a
    # post-T3/T4/T5 diagnostic snapshot on disk even if the true final
    # capture never runs.
    try {
        Invoke-StationDiagCapture -OutDir $OutDir -InstallDir $installDir -Label 'after-t3t5' -RunStart $RunStart
    } catch {
        "station diag capture (after-t3t5) failed: $_" | Add-Content -Path (Join-Path $OutDir 'summary-write-errors.log') -Encoding UTF8
    }
    Save-Summary -Step 'station-diag-captured-after-t3t5'
    # Last flush before the finalization path -- the exact path all three
    # observed stalls died in. Whatever the run recorded up to here survives
    # even if nothing below this line ever completes.
    Sync-Transcript -Checkpoint 'pre-finalization'
    Save-Summary -Step 'transcript-flushed-pre-finalization'

    # 6. Installer logs: install-progress.log (the nsh's own breadcrumb log,
    #    always at $COMMONPROGRAMDATA\CivicCast\install-progress.log).
    #    RELOCATED <gate-a-run7-findings>: the primary capture now happens
    #    right after the installer returns (see Invoke-InstallProgressCapture
    #    and its call site up there). Runs 4, 6 and 7 all went dark in the
    #    handful of statements that used to live HERE, and the harness had no
    #    step between them to say which one. This call is now only a guarded
    #    second attempt -- it no-ops immediately if the post-install capture
    #    already succeeded, which on a healthy run it always has -- and every
    #    statement inside it records its own step, so a repeat names the
    #    operation instead of a window.
    Invoke-InstallProgressCapture -Phase 'finalization'
    Save-Summary -Step 'install-progress-log-copied'

    # 7. Windows Event Log errors/criticals during the run window (Application
    #    + System). Best-effort; a from-scratch Sandbox may have very little
    #    in these logs. Bounded via -MaxEvents (stops the query itself early,
    #    unlike Select-Object -First which only trims AFTER full retrieval).
    #    NOT wrapped in Start-Job: a 2026-08-16 run under memory pressure hit
    #    System.OutOfMemoryException loading PSWorkflow/PSScheduledJob module
    #    type data the moment Start-Job was invoked -- job infrastructure has
    #    real memory cost this VM does not reliably have to spare on top of
    #    the already-running PostgreSQL/NATS/pythonservice.exe stack. A direct
    #    call costs nothing extra and -MaxEvents keeps it from running long.
    Save-Summary -Step 'event-log-query-begin'
    try {
        $events = Get-WinEvent -FilterHashtable @{
            LogName   = 'Application', 'System'
            Level     = 1, 2, 3
            StartTime = $RunStart
        } -MaxEvents 25 -ErrorAction SilentlyContinue
        $summary.event_log_errors = @($events | ForEach-Object {
            [ordered]@{
                TimeCreated  = $_.TimeCreated.ToString('o')
                LogName      = $_.LogName
                LevelDisplay = $_.LevelDisplayName
                ProviderName = $_.ProviderName
                Id           = $_.Id
                Message      = ($_.Message -split "`n" | Select-Object -First 3) -join ' | '
            }
        })
    } catch {
        $summary.errors += "event log query failed: $_"
    }
    Save-Summary -Step 'event-log-checked'

    # <gate-a-audit-BL-09> THE completion promotion, and the only one. Every
    # statement of the acceptance flow above ran without throwing. Anything
    # that reaches the catch below leaves this $false.
    $summary.harness_completed = $true
    Save-Summary -Step 'harness-completed'

} catch {
    $summary.errors += "top-level failure: $_"
    $summary.harness_completed = $false
    $summary.top_level_error = "$_"
    try { Save-Summary -Step 'top-level-failure' } catch {}
} finally {
    # Station diagnostics -- THIRD and final capture point, unconditional,
    # regardless of pass/fail/exception. This is the one capture guaranteed
    # to run even if something above this `finally` threw before reaching
    # either of the two earlier capture points.
    # Instrumented <gate-a-run7-findings>: a stall inside the final capture
    # used to be indistinguishable from a stall in the statements before it.
    try { Save-Summary -Step 'final-diag-begin' } catch {}
    try {
        Invoke-StationDiagCapture -OutDir $OutDir -InstallDir $installDir -Label 'final' -RunStart $RunStart
    } catch {
        try { "station diag capture (final) failed: $_" | Add-Content -Path (Join-Path $OutDir 'summary-write-errors.log') -Encoding UTF8 } catch {}
    }
    try { Save-Summary -Step 'final-diag-captured' } catch {}

    $summary.run_end_utc = (Get-Date).ToUniversalTime().ToString('o')
    # <gate-a-audit-BL-09> harness_completed is NOT set here. It was promoted
    # by the last statement of the try, or it is still $false because the
    # catch fired. The `finally` records the end of the run; it does not get
    # to decide whether the run succeeded.
    Save-Summary -Step 'finally-block'

    try { Stop-Transcript | Out-Null } catch {}

    # (d) DONE.json -- the unambiguous, authoritative completion signal. This
    # is the LAST thing the script does, after every other write. The old
    # DONE.marker text file is kept too (harmless, backward compatible) but
    # Host-Launch-Sandbox-Test.ps1 now polls for DONE.json specifically.
    #
    # `harness_completed=true` / `watchdog_timeout=false` are the
    # authoritative completion signal gate_a_verdict.py's check_completion
    # actually gates on now -- NOT a specific `last_completed_step` string.
    # (The prior contract required last_completed_step=='t5-soak-complete',
    # but steps 6/7 below T5 -- install-progress-log-copied,
    # event-log-checked -- and this very finally block ALWAYS run after
    # T5 and legitimately advance last_completed_step past it, so that
    # contract could never actually pass on a real completed run. Fixed as
    # part of this change; see scripts/gate_a_verdict.py and its tests.)
    # If the separate watchdog process (spawned at script entry) already
    # wrote WATCHDOG-TIMEOUT.txt / a placeholder DONE.json before reaching
    # here, this is the REAL completion and safely overwrites both.
    Write-Marker -Name 'DONE.marker' -Content (Get-Date).ToString('o')
    $doneObj = [ordered]@{
        done_utc              = (Get-Date).ToUniversalTime().ToString('o')
        last_completed_step   = $summary.last_completed_step
        step_seq              = $summary.step_seq
        run_end_utc           = $summary.run_end_utc
        installer_exit_code   = $summary.installer_exit_code
        harness_completed     = [bool]$summary.harness_completed
        top_level_error       = $summary.top_level_error
        watchdog_timeout       = $false
        station_up             = $summary.station_up
        station_first_healthy_utc = $summary.station_first_healthy_utc
        station_boot_seconds   = $summary.station_boot_seconds
    }
    # The main script reached its real end -- remove the watchdog's own
    # WATCHDOG-TIMEOUT.txt if the watchdog raced a slow-but-successful
    # finish (extremely unlikely given the 150-minute default vs this
    # script's own bounded steps, but the file's mere presence is what
    # gate_a_verdict.py's completion check fails closed on, so a stale one
    # from a genuinely-completed run must not linger). Done BEFORE DONE.json
    # so the same ship tick that carries DONE.json out also carries the
    # retraction; the shipper's retraction list removes any copy that
    # already reached the host.
    #
    # <gate-a-audit-BL-09> The retraction is now GATED on the run having
    # actually completed. Previously it ran unconditionally, so a run the
    # watchdog had already declared dead could retroactively erase that
    # finding merely by limping as far as this `finally`.
    if ($summary.harness_completed -eq $true) {
        try {
            $wd = Join-Path $OutDir 'WATCHDOG-TIMEOUT.txt'
            if (Test-Path $wd) { Remove-Item -Path $wd -Force -ErrorAction SilentlyContinue }
        } catch {}
    }

    ($doneObj | ConvertTo-Json -Depth 3) | Set-Content -Path (Join-Path $OutDir 'DONE.json') -Encoding UTF8

    # FINAL FLUSH <gate-a-mapped-folder-stalls>. DONE.json is written
    # locally, last, exactly as before -- but the host polls for it on the
    # MAPPED side, so one tick has to carry it across before this script
    # exits. Run it here rather than waiting up to a full interval for the
    # shipper's own next tick, and run it BOUNDED: if this last mapped
    # write is the one that wedges, the child is killed and the shipper
    # (still running, still spawning fresh children) gets further chances.
    # Nothing below this depends on it.
    try {
        $flush = Invoke-BoundedProcess -FilePath 'powershell.exe' -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$($script:ShipTickPath)`"",
            '-LocalDir', "`"$OutDir`"", '-ShipDir', "`"$ShipDir`""
        ) -TimeoutSeconds 120
        if (-not $flush.completed) {
            Add-Content -Path (Join-Path $OutDir 'summary-write-errors.log') -Value "final ship flush did not complete: $($flush.error)"
        }
    } catch {
        try { Add-Content -Path (Join-Path $OutDir 'summary-write-errors.log') -Value "final ship flush threw: $_" } catch {}
    }
}
