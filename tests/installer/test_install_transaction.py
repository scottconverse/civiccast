# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Run the installer recovery's real copy/journal logic in temporary directories.

SCM, machine registry, firewall and tasks are replaced at their narrow boundaries;
these tests never install, stop or modify a service on the development machine.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "civiccast/apps/installer/src-tauri/install-transaction.ps1"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows installer filesystem recovery")

SETUP = r"""
param([string]$Coordinator, [string]$Fixture)
. $Coordinator -StateRoot (Join-Path $Fixture 'station\install-transaction')
$script:restoreNativeService = ${function:Restore-ServiceSnapshot}
New-Item -ItemType Directory -Path $StateRoot -Force | Out-Null
$app = Join-Path $Fixture 'app'
$data = Join-Path $Fixture 'station'
New-Item -ItemType Directory -Path "$app\runtime", "$app\models", "$app\packs\.station-cache", "$data\data\pgdata", "$data\recordings" -Force | Out-Null
Set-Content -LiteralPath "$app\runtime\python.exe" -Value 'old-runtime'
Set-Content -LiteralPath "$app\station-set.json" -Value 'old-activation'
Set-Content -LiteralPath "$app\models\model.bin" -Value 'old-model'
Set-Content -LiteralPath "$app\packs\.station-cache\pack.bin" -Value 'immutable-cache'
Set-Content -LiteralPath "$data\data\pgdata\database.bin" -Value 'old-db'
Set-Content -LiteralPath "$data\data\pgdata\postgresql.conf" -Value 'old-config'
Set-Content -LiteralPath "$data\recordings\meeting.mp4" -Value 'station-recording'
$script:events = [Collections.Generic.List[string]]::new()
function Stop-TransactionWriters($state) { $script:events.Add('stop') }
function Set-RegistryTree($path, $tree) { $script:events.Add('registry:' + $tree.Version) }
function Restore-TransactionFirewall($state) { $script:events.Add('firewall') }
function Clear-RecoveryTask { $script:events.Add('clear-task') }
function Get-InstallerShortcutPaths($state) { return @() }
function Restore-ServiceSnapshot($state) {
    Assert ((Get-Content "$app\runtime\python.exe") -eq 'old-runtime') 'service sees candidate runtime'
    Assert ((Get-Content "$app\station-set.json") -eq 'old-activation') 'service sees candidate activation'
    Assert ((Get-Content "$data\data\pgdata\database.bin") -eq 'old-db') 'service sees candidate database'
    $script:events.Add('restart-checked')
}
function Invoke-ServiceCommand($arguments, $accepted) { $script:events.Add('service-config') }
function Assert($condition, [string]$message) { if (-not $condition) { throw $message } }
$state = @{ Id='test'; Phase='Prepared'; InstallRoot=$app; DataRoot=$data; Registry=@{ Product=@{Version='old'} }; InstallerPid=0; Entries=@(); SnapshotComplete=$false }
Write-Transaction $state
"""


def run_recovery(tmp_path: Path, body: str) -> None:
    wrapper = tmp_path / "check.ps1"
    wrapper.write_text(SETUP + "\n" + body, encoding="utf-8")
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
            "-Coordinator",
            str(SCRIPT),
            "-Fixture",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_restores_files_activation_database_after_late_failure_and_preserves_media(
    tmp_path: Path,
) -> None:
    run_recovery(
        tmp_path,
        r"""
Snapshot-Transaction $state
Assert (-not (Test-Path "$StateRoot\backup\app\packs\.station-cache")) 'cache duplicated'
Set-Content "$app\runtime\python.exe" 'candidate-runtime'
Set-Content "$app\station-set.json" 'candidate-activation'
Set-Content "$data\data\pgdata\database.bin" 'migrated-db'
Set-Content "$data\data\pgdata\postgresql.conf" 'candidate-config'
Set-Content "$app\candidate-only.txt" 'new'
Set-Content "$app\packs\.station-cache\new-pack.bin" 'new-immutable-cache'
Set-Content "$data\recordings\meeting.mp4" 'recording-written-outside-installer'
Restore-Transaction (Read-Transaction)
Assert ((Read-Transaction).Phase -eq 'Restored') 'restoration not recorded'
Assert ((Get-Content "$data\data\pgdata\postgresql.conf") -eq 'old-config') 'PG config not restored'
Assert (-not (Test-Path "$app\candidate-only.txt")) 'candidate extra file retained'
Assert (Test-Path "$app\packs\.station-cache\new-pack.bin") 'immutable cache removed'
Assert ((Get-Content "$data\recordings\meeting.mp4") -eq 'recording-written-outside-installer') 'media overwritten'
Assert ($script:events.IndexOf('registry:old') -lt $script:events.IndexOf('restart-checked')) 'service started before registry'
Assert (-not (Test-Path "$StateRoot\backup")) 'successful restoration retained large backup'
""",
    )


def test_interrupted_restore_keeps_recovery_point_and_retries_idempotently(tmp_path: Path) -> None:
    run_recovery(
        tmp_path,
        r"""
$main = "$app\CivicCast Native.exe"
$stamp = [DateTime]::new(2024, 1, 1, 0, 0, 0, [DateTimeKind]::Utc)
[IO.File]::WriteAllText($main, 'old-main-exe')
[IO.File]::SetLastWriteTimeUtc($main, $stamp)
$priorAttributes = [IO.File]::GetAttributes($main)
Snapshot-Transaction $state
[IO.File]::WriteAllText($main, 'new-main-exe')
[IO.File]::SetLastWriteTimeUtc($main, $stamp)
Set-Content "$app\runtime\python.exe" 'candidate-runtime'
Set-Content "$data\data\pgdata\database.bin" 'partly-migrated-db'
$script:copyTree = ${function:Copy-RecoveryTree}
$script:failOnce = $true
function Copy-RecoveryTree($source, $target, $exclude) {
    if ($script:failOnce -and $source -like '*backup*pgdata') {
        $script:failOnce = $false
        throw 'controlled restore interruption'
    }
    & $script:copyTree $source $target $exclude
}
try { Restore-Transaction (Read-Transaction); throw 'expected interruption' }
catch { Assert ($_.Exception.Message -eq 'controlled restore interruption') 'wrong restore failure' }
Assert ((Read-Transaction).Phase -eq 'Restoring') 'retry state lost'
Assert (Test-Path "$StateRoot\backup\app\runtime\python.exe") 'recovery point consumed during restore'
Assert (-not $script:events.Contains('restart-checked')) 'service started on partial restore'
Assert ((Get-Content $main) -eq 'old-main-exe') 'same-size same-time candidate executable not restored'
[IO.File]::WriteAllText($main, 'new-main-exe')
[IO.File]::SetLastWriteTimeUtc($main, $stamp)
[IO.File]::SetAttributes($main, $priorAttributes -bor [IO.FileAttributes]::Hidden)
Restore-Transaction (Read-Transaction)
Assert ((Get-Content $main) -eq 'old-main-exe') 'same-size same-time executable not restored on retry'
Assert ([IO.File]::GetAttributes($main) -eq $priorAttributes) 'prior executable attributes not restored'
Assert ((Read-Transaction).Phase -eq 'Restored') 'retry failed'
""",
    )


def test_dead_installer_recovery_restores_but_live_installer_is_untouched(tmp_path: Path) -> None:
    run_recovery(
        tmp_path,
        r"""
Snapshot-Transaction $state
Set-Content "$app\runtime\python.exe" 'candidate-runtime'
function Test-InstallerAlive($state) { return $true }
Recover-Transaction (Read-Transaction)
Assert ((Get-Content "$app\runtime\python.exe") -eq 'candidate-runtime') 'live installer was undone'
function Test-InstallerAlive($state) { return $false }
# A newly launched installer has a different PID from the abandoned owner.
$InstallerPid = $PID
Recover-Transaction (Read-Transaction)
Assert ((Read-Transaction).Phase -eq 'Restored') 'dead installer was not recovered'
""",
    )


def test_commit_keeps_candidate_and_recovery_cannot_roll_it_back(tmp_path: Path) -> None:
    run_recovery(
        tmp_path,
        r"""
Snapshot-Transaction $state
Set-Content "$app\runtime\python.exe" 'candidate-runtime'
Commit-Transaction (Read-Transaction)
Assert ((Read-Transaction).Phase -eq 'Committed') 'commit not durable'
Assert (-not (Test-Path "$StateRoot\backup")) 'commit retained large snapshot'
$configCount = @($script:events | Where-Object { $_ -eq 'service-config' }).Count
Recover-Transaction (Read-Transaction)
Assert (@($script:events | Where-Object { $_ -eq 'service-config' }).Count -eq $configCount) 'later setup overwrote operator start-mode preference'
Assert ((Get-Content "$app\runtime\python.exe") -eq 'candidate-runtime') 'committed candidate rolled back'
Assert (-not $script:events.Contains('restart-checked')) 'prior station restarted after commit'
""",
    )


def test_commit_survives_reader_holding_the_journal_open(tmp_path: Path) -> None:
    """R9 2026-11-13: a Watch reader held transaction.clixml during Replace (exit 136)."""
    run_recovery(
        tmp_path,
        r"""
$journal = Join-Path $StateRoot 'transaction.clixml'
# A hostile reader with no delete sharing blocks File.Replace until released.
$blocker = [IO.File]::Open($journal, 'Open', 'Read', 'Read')
$release = [powershell]::Create().AddScript({ param($h) Start-Sleep -Milliseconds 1500; $h.Dispose() }).AddArgument($blocker)
$null = $release.BeginInvoke()
try {
    $state.Phase = 'Active'
    Write-Transaction $state
} finally { $blocker.Dispose(); $release.Dispose() }
Assert ((Read-Transaction).Phase -eq 'Active') 'write lost after reader released'
""",
    )


def test_read_transaction_does_not_block_replace(tmp_path: Path) -> None:
    run_recovery(
        tmp_path,
        r"""
$journal = Join-Path $StateRoot 'transaction.clixml'
# A reader that shares like Read-Transaction must not block File.Replace.
$shared = [IO.File]::Open($journal, 'Open', 'Read', 'ReadWrite, Delete')
try {
    $state.Phase = 'Active'
    $sw = [Diagnostics.Stopwatch]::StartNew()
    Write-Transaction $state
    Assert ($sw.ElapsedMilliseconds -lt 1000) 'Write-Transaction waited on a shared reader'
} finally { $shared.Dispose() }
Assert ((Read-Transaction).Phase -eq 'Active') 'write lost'
""",
    )


def test_restores_existing_shortcut_and_removes_only_new_installer_shortcut(tmp_path: Path) -> None:
    run_recovery(
        tmp_path,
        r"""
$links = Join-Path $Fixture 'links'
New-Item -ItemType Directory -Path $links -Force | Out-Null
Set-Content "$links\console.url" 'old-console'
Set-Content "$links\unrelated.url" 'unrelated'
function Get-InstallerShortcutPaths($state) {
    return @(@{ Path="$links\console.url"; Owner=$links }, @{ Path="$links\portal.url"; Owner=$links })
}
Snapshot-Transaction $state
Set-Content "$links\console.url" 'candidate-console'
Set-Content "$links\portal.url" 'candidate-portal'
Restore-Transaction (Read-Transaction)
Assert ((Get-Content "$links\console.url") -eq 'old-console') 'old shortcut not restored'
Assert (-not (Test-Path "$links\portal.url")) 'new shortcut retained'
Assert ((Get-Content "$links\unrelated.url") -eq 'unrelated') 'unrelated shortcut changed'
""",
    )


def test_insufficient_snapshot_space_refuses_before_replacement(tmp_path: Path) -> None:
    run_recovery(
        tmp_path,
        r"""
function Get-TreeBytes($path, $exclude) { return [long]([IO.DriveInfo]::new('C:\').AvailableFreeSpace + 1GB) }
try { Snapshot-Transaction $state; throw 'expected space refusal' }
catch { Assert ($_.Exception.Message -like 'Recovery snapshot needs*') 'wrong preflight failure' }
Assert (-not (Read-Transaction).SnapshotComplete) 'incomplete recovery point trusted'
Restore-Transaction (Read-Transaction)
Assert ((Get-Content "$app\runtime\python.exe") -eq 'old-runtime') 'preflight replaced runtime'
Assert ((Read-Transaction).Phase -eq 'Restored') 'preflight did not restore prior running state'
""",
    )


def test_snapshot_and_restore_keep_hardlinks_without_sharing_live_bytes(tmp_path: Path) -> None:
    run_recovery(
        tmp_path,
        r"""
New-Item -ItemType Directory -Path "$app\components\model" -Force | Out-Null
[IO.File]::WriteAllText("$app\components\model\blob", 'old-model-bytes')
New-Item -ItemType HardLink -Path "$app\models\alias-one" -Target "$app\components\model\blob" | Out-Null
New-Item -ItemType HardLink -Path "$app\models\alias-two" -Target "$app\components\model\blob" | Out-Null
New-Item -ItemType HardLink -Path "$app\packs\.station-cache\excluded-alias" -Target "$app\components\model\blob" | Out-Null
# Equal contents are not evidence of a hardlink; this file stays independent.
[IO.File]::WriteAllText("$app\models\separate-copy", 'old-model-bytes')
$logical = (Get-ChildItem -LiteralPath $app -Recurse -File | Where-Object { $_.FullName -notlike '*\.station-cache\*' } | Measure-Object -Property Length -Sum).Sum
$unique = $logical - 2 * (Get-Item "$app\components\model\blob").Length
Assert ((Get-TreeBytes $app @('.station-cache')) -eq $unique) 'snapshot counts hardlink aliases twice'
Snapshot-Transaction $state
Assert (-not (Test-Path "$StateRoot\backup\app\packs\.station-cache")) 'hardlink cache copied'
Remove-Item -LiteralPath "$app\packs\.station-cache\excluded-alias"
[IO.File]::WriteAllText("$app\models\alias-one", 'candidate-model-bytes')
Assert ((Get-Content "$StateRoot\backup\app\components\model\blob") -eq 'old-model-bytes') 'live writes changed backup'
$script:nativeCopy = ${function:Invoke-RecoveryTreeCopy}
$script:appCopyCount = 0
function Invoke-RecoveryTreeCopy($arguments) {
    if ($arguments[0] -eq "$StateRoot\backup\app") {
        Assert (-not (Test-Path "$app\models\alias-one")) 'old model alias retained when byte copy starts'
        Assert (-not (Test-Path "$app\models\alias-two")) 'last old model alias retains large bytes during copy'
        if ($script:appCopyCount -gt 0) {
            Assert (-not (Test-Path "$app\models\separate-copy")) 'changed candidate alias retains large bytes during retry'
        }
        $script:appCopyCount++
    }
    & $script:nativeCopy $arguments
}
# Fail after restoring the app: backup topology and independence must survive retry.
$script:copyTree = ${function:Copy-RecoveryTree}
$script:failOnce = $true
function Copy-RecoveryTree($source, $target, $exclude) {
    if ($script:failOnce -and $source -like '*backup*pgdata') {
        $script:failOnce = $false
        throw 'controlled hardlink restore interruption'
    }
    & $script:copyTree $source $target $exclude
}
try { Restore-Transaction (Read-Transaction); throw 'expected interruption' }
catch { if ($_.Exception.Message -ne 'controlled hardlink restore interruption') { throw } }
# Candidate topology can differ: an independent prior file is now an alias.
Remove-Item -LiteralPath "$app\models\separate-copy"
New-Item -ItemType HardLink -Path "$app\models\separate-copy" -Target "$app\components\model\blob" | Out-Null
[IO.File]::WriteAllText("$app\models\alias-two", 'interrupted-live-write')
Assert ((Get-Content "$StateRoot\backup\app\models\alias-one") -eq 'old-model-bytes') 'restore linked live bytes to backup'
# Check backup topology by changing one alias, then put the recovery point back.
[IO.File]::WriteAllText("$StateRoot\backup\app\models\alias-one", 'backup-link-check')
Assert ((Get-Content "$StateRoot\backup\app\models\alias-two") -eq 'backup-link-check') 'backup hardlinks expanded into copies'
Assert ((Get-Content "$StateRoot\backup\app\models\separate-copy") -eq 'old-model-bytes') 'equal contents incorrectly deduplicated'
[IO.File]::WriteAllText("$StateRoot\backup\app\models\alias-one", 'old-model-bytes')
Restore-Transaction (Read-Transaction)
Assert ($script:appCopyCount -eq 2) 'native copy boundary not checked on restore and retry'
Assert ((Get-Content "$app\components\model\blob") -eq 'old-model-bytes') 'retry did not restore model'
[IO.File]::WriteAllText("$app\models\alias-one", 'restored-link-check')
Assert ((Get-Content "$app\models\alias-two") -eq 'restored-link-check') 'restored hardlinks expanded into copies'
Assert ((Get-Content "$app\components\model\blob") -eq 'restored-link-check') 'restored component disconnected from models'
Assert ((Get-Content "$app\models\separate-copy") -eq 'old-model-bytes') 'independent file was linked'
""",
    )


@pytest.mark.parametrize(
    "account", ["LocalSystem", r"NT AUTHORITY\SYSTEM", r"NT SERVICE\CivicCastSupervisor"]
)
def test_service_restore_preserves_start_mode_and_prior_running_state(
    tmp_path: Path, account: str
) -> None:
    run_recovery(
        tmp_path,
        r"""
$script:fakeService = [pscustomobject]@{}
$script:fakeService | Add-Member -MemberType ScriptMethod -Name Dispose -Value { }
$script:fakeService | Add-Member -MemberType ScriptMethod -Name WaitForStatus -Value { param($status, $timeout) }
function Get-Service($Name) { return $script:fakeService }
function Wait-RestoredStation($state) { $script:events.Add('health') }
$script:commands = [Collections.Generic.List[string]]::new()
function Invoke-ServiceCommand($arguments, $accepted) { $script:commands.Add(($arguments -join ' ')) }
foreach ($mode in @(2, 3, 4)) {
    foreach ($running in @($false, $true)) {
        $script:commands.Clear()
        $state.Service = @{ Running=$running; Tree=@{ Values=@(
            @{Name='Start'; Value=$mode}, @{Name='ImagePath'; Value='old-pythonservice.exe'},
            @{Name='DisplayName'; Value='CivicCast'}, @{Name='DependOnService'; Value=@()},
            @{Name='ObjectName'; Value='SAVED_ACCOUNT'}
        ); Children=@{} } }
        & $script:restoreNativeService $state
        Assert ($script:commands[0] -like '*obj= SAVED_ACCOUNT *') 'prior service identity changed'
        $startName = switch ($mode) { 2 { 'auto' } 3 { 'demand' } 4 { 'disabled' } }
        Assert ($script:commands.Contains('start CivicCastSupervisor') -eq $running) 'prior running state changed'
        if ($running) {
            Assert ($script:commands[0] -like '*start= demand*') 'running disabled service cannot be started'
            Assert ($script:commands[$script:commands.Count - 1] -eq "config CivicCastSupervisor start= $startName") 'prior mode not restored after health'
        } else {
            Assert ($script:commands[0] -like "*start= $startName*") 'stopped service mode changed'
        }
    }
}
""".replace("SAVED_ACCOUNT", account),
    )


@pytest.mark.parametrize("account", [None, r".\operator"], ids=["missing", "unsupported"])
def test_service_restore_rejects_missing_or_unsupported_identity(
    tmp_path: Path, account: str | None
) -> None:
    run_recovery(
        tmp_path,
        r"""
function Get-Service($Name) { return $null }
$state.Service = @{ Running=$false; Tree=@{ Values=@(
    @{Name='Start'; Value=2}, @{Name='ImagePath'; Value='old-pythonservice.exe'}
    SAVED_ACCOUNT_ENTRY
); Children=@{} } }
try {
    & $script:restoreNativeService $state
    throw 'unsupported identity was accepted'
} catch {
    Assert ($_.Exception.Message -eq 'Unsupported saved service identity; refusing to substitute LocalSystem.') 'unexpected restore failure'
}
Assert ($script:events.Count -eq 0) 'unsupported identity changed service or registry'
""".replace(
            "SAVED_ACCOUNT_ENTRY",
            "" if account is None else ", @{Name='ObjectName'; Value='" + account + "'}",
        ),
    )


def test_snapshot_progress_identifies_size_copy_and_completion(tmp_path: Path) -> None:
    run_recovery(
        tmp_path,
        r"""
$script:realCopy = ${function:Copy-RecoveryEntry}
function Copy-RecoveryEntry($entry, $source, $target) {
    $current = Get-Content -LiteralPath "$data\install-progress.log" -Raw
    Assert ($current -match 'size measured: \d+ bytes; required reserve: 268435456 bytes; available: \d+ bytes') 'size was not reported before copy'
    Assert ($current -match 'copying entry') 'copy phase was not reported before copy'
    Assert ($current -notmatch 'recovery snapshot: complete:') 'completion reported before copy'
    & $script:realCopy $entry $source $target
}
Snapshot-Transaction $state
$log = Get-Content -LiteralPath "$data\install-progress.log" -Raw
Assert ($log -match 'recovery snapshot: quiescing') 'quiesce phase missing'
Assert ($log -match 'recovery snapshot: measuring recovery size') 'size phase missing'
Assert ($log -match 'recovery snapshot: copied entry 4 of 4') 'entry completion missing'
Assert ($log -match 'recovery snapshot: complete: \d+ bytes; recovery point recorded') 'durable completion missing'
Assert ((Read-Transaction).SnapshotComplete) 'progress changed recovery completion'
Assert (-not $script:SnapshotProgressActive) 'copy heartbeat left active'
""",
    )
