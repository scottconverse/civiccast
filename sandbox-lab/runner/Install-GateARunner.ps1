# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
#
# Install-GateARunner.ps1 -- installs a GitHub Actions self-hosted runner for
# scottconverse/civiccast, labeled for Gate A (sandbox-lab), on this
# Windows box.
#
# IMPORTANT: this runner MUST start as an INTERACTIVE logon scheduled task
# running run.cmd, NOT as a Windows service (`config.cmd ... ` followed by
# `svc install` / `svc start`). Windows Sandbox requires an interactive
# desktop session to launch WindowsSandbox.exe -- it cannot be started from
# a service running in Session 0. This is the same class of constraint the
# existing self-hosted Linux runner note documents for its own reasons (see
# docs/ops/self-hosted-ci.md, "must run as a systemd service, not a
# foreground process") -- the Windows Gate A runner has the OPPOSITE
# requirement for the opposite reason: it must NOT be a Session-0 service,
# because Windows Sandbox cannot be launched from Session 0.
#
# Idempotent: safe to re-run. Re-running with the same -RunnerDir re-registers
# (--replace) rather than erroring on an existing config.
#
# This script CANNOT be tested end-to-end here: registration requires a
# short-lived GitHub registration token the caller must supply (from
# `gh api repos/scottconverse/civiccast/actions/runners/registration-token`
# or the repo's Settings > Actions > Runners > New self-hosted runner page),
# and this environment has none. What IS verified (see docs/ops/gate-a.md):
# this file parses clean under
# [System.Management.Automation.Language.Parser]::ParseFile, and -DryRun
# exercises the download/extract path (skipping config.cmd, the scheduled
# task registration, and anything that touches a real token).
#
# Usage:
#   .\Install-GateARunner.ps1 -Token <registration-token>
#   .\Install-GateARunner.ps1 -Token <registration-token> -RunnerDir D:\actions-runner-gate-a
#   .\Install-GateARunner.ps1 -DryRun   # download/extract only, no registration

param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Register')]
    [string]$Token,

    [string]$RunnerDir = 'C:\actions-runner-gate-a',
    [string]$Repo = 'scottconverse/civiccast',
    [string[]]$Labels = @('self-hosted', 'windows', 'sandbox-lab'),
    [string]$RunnerName = $null,

    [Parameter(Mandatory = $true, ParameterSetName = 'DryRun')]
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

function Write-Step {
    param([string]$Message)
    Write-Host "[Install-GateARunner] $Message" -ForegroundColor Cyan
}

if (-not $RunnerName) {
    $RunnerName = "$($env:COMPUTERNAME)-gate-a"
}

Write-Step "RunnerDir=$RunnerDir Repo=$Repo Labels=$($Labels -join ',') RunnerName=$RunnerName DryRun=$($DryRun.IsPresent)"

# --------------------------------------------------------------------------
# 1. Resolve and download the latest Windows x64 runner release.
# --------------------------------------------------------------------------

New-Item -ItemType Directory -Force -Path $RunnerDir | Out-Null

Write-Step "Resolving latest actions/runner release..."
$release = Invoke-RestMethod -Uri 'https://api.github.com/repos/actions/runner/releases/latest' -Headers @{ 'User-Agent' = 'civiccast-native-gate-a-runner-installer' }
$version = $release.tag_name.TrimStart('v')
$asset = $release.assets | Where-Object { $_.name -match "^actions-runner-win-x64-.*\.zip$" } | Select-Object -First 1
if (-not $asset) {
    throw "Could not find a win-x64 runner asset in the latest actions/runner release ($($release.tag_name))"
}
Write-Step "Latest runner: version=$version asset=$($asset.name) ($('{0:N0}' -f $asset.size) bytes)"

$zipPath = Join-Path $RunnerDir $asset.name
if (-not (Test-Path $zipPath)) {
    Write-Step "Downloading $($asset.browser_download_url) -> $zipPath"
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zipPath
} else {
    Write-Step "Runner zip already present at $zipPath -- skipping download."
}

# --------------------------------------------------------------------------
# 2. Extract (idempotent: only if run.cmd isn't already there).
# --------------------------------------------------------------------------

$runCmd = Join-Path $RunnerDir 'run.cmd'
if (-not (Test-Path $runCmd)) {
    Write-Step "Extracting $zipPath -> $RunnerDir"
    Expand-Archive -Path $zipPath -DestinationPath $RunnerDir -Force
} else {
    Write-Step "Runner already extracted at $RunnerDir (run.cmd present) -- skipping extraction."
}

if (-not (Test-Path $runCmd)) {
    throw "Extraction did not produce $runCmd -- bad archive or unexpected layout"
}

if ($DryRun) {
    Write-Step "DryRun: stopping after download/extract. Not configuring or registering. run.cmd is present at $runCmd."
    exit 0
}

# --------------------------------------------------------------------------
# 3. Configure (register) against the target repo. --replace makes this
#    idempotent across re-runs with the same name.
# --------------------------------------------------------------------------

$configCmd = Join-Path $RunnerDir 'config.cmd'
if (-not (Test-Path $configCmd)) {
    throw "config.cmd not found at $configCmd after extraction"
}

Write-Step "Registering runner '$RunnerName' against https://github.com/$Repo with labels $($Labels -join ',')..."
$configArgs = @(
    '--unattended',
    '--replace',
    '--url', "https://github.com/$Repo",
    '--token', $Token,
    '--name', $RunnerName,
    '--labels', ($Labels -join ',')
)
Push-Location $RunnerDir
try {
    & $configCmd @configArgs
    if ($LASTEXITCODE -ne 0) {
        throw "config.cmd exited $LASTEXITCODE"
    }
} finally {
    Pop-Location
}
Write-Step "Registration complete."

# --------------------------------------------------------------------------
# 4. Register run.cmd to start as an INTERACTIVE LOGON scheduled task, NOT a
#    service. See the header comment: Windows Sandbox cannot launch from
#    Session 0, which rules out `.\svc.cmd install` / `svc start` (the
#    documented, service-based registration path actions/runner ships) for
#    this runner specifically.
# --------------------------------------------------------------------------

$taskName = "CivicCastGateARunner-$RunnerName"
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Step "Scheduled task '$taskName' already exists -- unregistering before re-creating (idempotent re-run)."
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute $runCmd -WorkingDirectory $RunnerDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "CivicCast Gate A self-hosted runner ($RunnerName) -- interactive logon task, NOT a service, because Windows Sandbox cannot launch from Session 0." | Out-Null

Write-Step "Scheduled task '$taskName' registered: runs $runCmd at interactive logon for user $env:USERNAME."
Write-Step "Start it now with: Start-ScheduledTask -TaskName '$taskName'  (or log off/on once to let it start automatically)"
Write-Step "Verify online status with: gh api repos/$Repo/actions/runners"
Write-Step "Done."
