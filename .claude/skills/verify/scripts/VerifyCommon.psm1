# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
#
# Shared helpers for the CivicCast verify skills (backend and portal).
# Process rules: every child is started hidden, is recorded by PID plus start
# time plus command line, and is stopped ONLY by that PID (process tree) after
# the record is re-checked. Nothing here ever stops a process by image name.

Set-StrictMode -Version 3.0

function Get-RepoRoot {
    param([Parameter(Mandatory)][string]$From)
    $root = (& git -C $From rev-parse --show-toplevel 2>$null)
    if (-not $root) { throw "Not inside a git checkout: $From" }
    return (Resolve-Path $root).Path
}

function Get-FreeLoopbackPort {
    # Ask the OS for a free loopback port. Never 8000 (installed CivicCast)
    # and never 5433 (installed PostgreSQL): ephemeral ports are >= 49152.
    for ($i = 0; $i -lt 20; $i++) {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
        $listener.Start()
        $port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
        $listener.Stop()
        if ($port -notin 8000, 5433) { return $port }
    }
    throw 'Could not allocate a free loopback port.'
}

function Assert-PortFree {
    param([Parameter(Mandatory)][int]$Port)
    if ($Port -in 8000, 5433) {
        throw "Port $Port is reserved for the installed CivicCast station / its PostgreSQL. Pick another port."
    }
    $busy = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
    if ($busy) { throw "Port $Port is already in use (PID $($busy[0].OwningProcess)). Pick another port or pass -Port 0." }
}

function New-EvidenceDir {
    param([Parameter(Mandatory)][string]$RepoRoot, [Parameter(Mandatory)][string]$Suffix, [string]$Requested)
    if ($Requested) {
        $dir = $Requested
    } else {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
        $dir = Join-Path $RepoRoot ".vlite-evidence\$stamp-$Suffix"
    }
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
    return (Resolve-Path $dir).Path
}

function Start-HiddenProcess {
    <#
      Starts a console program hidden, stdout/stderr to files, and returns a
      record { Pid; StartTime; CommandLine; Marker }. Marker is a string that
      must appear in the command line when the record is re-checked on stop.
      The match is a whole word (so '--port 5173' does not match '--port 51730').
      A record may set MarkerMode = 'prefix' to match the marker anywhere.
    #>
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$ArgumentList,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][string]$StdOut,
        [Parameter(Mandatory)][string]$StdErr,
        [Parameter(Mandatory)][string]$Marker
    )
    $proc = Start-Process -FilePath $FilePath -ArgumentList $ArgumentList -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden -RedirectStandardOutput $StdOut -RedirectStandardError $StdErr -PassThru
    $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $($proc.Id)"
    if (-not $cim) { throw "Process $($proc.Id) exited immediately. See $StdErr" }
    return [pscustomobject]@{
        Pid         = $proc.Id
        StartTime   = $cim.CreationDate
        CommandLine = $cim.CommandLine
        Marker      = $Marker
        Process     = $proc
    }
}

function Get-OwnedTree {
    # PIDs of the recorded process and its descendants that were created at or
    # after it (guards against PID reuse by an unrelated older process).
    param([Parameter(Mandatory)]$Record)
    $all = Get-CimInstance Win32_Process
    $result = [System.Collections.Generic.List[int]]::new()
    $queue = [System.Collections.Generic.Queue[int]]::new()
    $queue.Enqueue($Record.Pid)
    while ($queue.Count -gt 0) {
        $parent = $queue.Dequeue()
        $result.Add($parent)
        foreach ($p in $all | Where-Object { $_.ParentProcessId -eq $parent -and $_.CreationDate -ge $Record.StartTime }) {
            if (-not $result.Contains([int]$p.ProcessId)) { $queue.Enqueue([int]$p.ProcessId) }
        }
    }
    return $result
}

function Stop-OwnedProcess {
    <#
      Stops the recorded process tree by PID, only after confirming the PID
      still belongs to us (same creation time, marker in command line).
      Returns a short text line for the evidence log.
    #>
    param([Parameter(Mandatory)]$Record, [int]$WaitSec = 15)
    $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $($Record.Pid)"
    if (-not $cim) { return "pid $($Record.Pid): already exited" }
    $mode = if ($Record.PSObject.Properties['MarkerMode']) { $Record.MarkerMode } else { 'word' }
    $rx = if ($mode -eq 'prefix') { [regex]::Escape($Record.Marker) } else { '(?:^|\s)' + [regex]::Escape($Record.Marker) + '(?:\s|$)' }
    if ($cim.CreationDate -ne $Record.StartTime -or ($cim.CommandLine -notmatch $rx)) {
        return "pid $($Record.Pid): NOT stopped - PID now belongs to another process ($($cim.Name))"
    }
    $tree = Get-OwnedTree -Record $Record
    # taskkill /T walks the tree from OUR verified root PID; /F because a
    # hidden console child cannot receive Ctrl+C from this session.
    # Bounded: a hung taskkill must not hang the run. Hidden, no window.
    $tk = Start-Process -FilePath 'taskkill.exe' -ArgumentList @('/PID', "$($Record.Pid)", '/T', '/F') `
        -WindowStyle Hidden -PassThru
    $tkNote = ''
    if (-not $tk.WaitForExit(15000)) {
        $tkNote = ' (taskkill timed out after 15s)'
        try { $tk.Kill() } catch { }
    }
    $deadline = (Get-Date).AddSeconds($WaitSec)
    do {
        $alive = @($tree | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
        if ($alive.Count -eq 0) { return "pid $($Record.Pid): stopped (tree $($tree -join ','))$tkNote" }
        Start-Sleep -Milliseconds 200
    } while ((Get-Date) -lt $deadline)
    return "pid $($Record.Pid): STILL RUNNING after ${WaitSec}s: $($alive -join ',')$tkNote"
}

function Wait-HttpOk {
    # Polls a URL until it answers with an accepted status, the process dies,
    # or the deadline passes. Returns elapsed seconds.
    param(
        [Parameter(Mandatory)][string]$Url,
        [Parameter(Mandatory)]$Record,
        [int]$TimeoutSec = 90,
        [int[]]$Accept = @(200)
    )
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $last = ''
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
        if ($Record.Process.HasExited) { throw "Process $($Record.Pid) exited with code $($Record.Process.ExitCode) before $Url answered." }
        try {
            $r = Invoke-WebRequest -Uri $Url -TimeoutSec 5 -SkipHttpErrorCheck -UseBasicParsing
            if ($r.StatusCode -in $Accept) { return [math]::Round($sw.Elapsed.TotalSeconds, 2) }
            $last = "HTTP $($r.StatusCode)"
        } catch { $last = $_.Exception.Message }
        Start-Sleep -Milliseconds 250
    }
    throw "$Url did not answer within ${TimeoutSec}s (last: $last)"
}

Export-ModuleMember -Function Get-RepoRoot, Get-FreeLoopbackPort, Assert-PortFree, New-EvidenceDir,
    Start-HiddenProcess, Get-OwnedTree, Stop-OwnedProcess, Wait-HttpOk
