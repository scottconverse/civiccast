# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
#
# F10 (RC4 field run, 2026-11-04): warn when a program other than CivicCast
# already listens on the station's AI engine port. Advisory only: the station
# installs and runs without AI (the supervisor treats a foreign listener as
# "degraded AI, service healthy"). Exit 10 = held by another program (stdout
# names it); exit 0 = free, CivicCast's own, or the check could not run.
# Windows PowerShell 5.1 compatible.
param(
    [int]$Port = 11434,
    [string]$OwnInstallRoot = ''
)
$ErrorActionPreference = 'Stop'
try {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -in @('127.0.0.1', '0.0.0.0', '::', '::1') })
    if ($listeners.Count -eq 0) {
        Write-Output "CivicCast AI port preflight: port $Port is free"
        exit 0
    }
    $owner = $listeners[0].OwningProcess
    $name = 'unknown program'
    $path = ''
    try {
        $proc = Get-Process -Id $owner -ErrorAction Stop
        $name = $proc.ProcessName
        if ($proc.Path) { $path = $proc.Path }
    } catch {}
    if ($OwnInstallRoot -and $path) {
        $root = $OwnInstallRoot.TrimEnd('\') + '\'
        if ($path.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
            Write-Output "CivicCast AI port preflight: port $Port is held by CivicCast's own $name (pid $owner)"
            exit 0
        }
    }
    $where = if ($path) { " at $path" } else { '' }
    Write-Output "CivicCast AI port preflight: port $Port is held by $name (pid $owner)$where"
    exit 10
} catch {
    Write-Output "CivicCast AI port preflight: could not check port ${Port}: $($_.Exception.Message)"
    exit 0
}
