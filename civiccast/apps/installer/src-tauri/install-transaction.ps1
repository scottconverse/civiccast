# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
# Installer-specific recovery. Never depends on the candidate Python runtime.
param(
    [ValidateSet('', 'Begin', 'Snapshot', 'Commit', 'Restore', 'Watch', 'Recover')]
    [string]$Action = '',
    [string]$InstallRoot,
    [string]$StateRoot = "$env:ProgramData\CivicCast\install-transaction",
    [string]$UninstallKey,
    [int]$InstallerPid,
    [string]$TransactionId,
    [string]$StartMenuFolder
)
$ErrorActionPreference = 'Stop'
# Setup can be launched from PowerShell 7, whose inherited module path can
# load incompatible modules into Windows PowerShell 5.1. Use Windows' bundled
# CIM, Security and ScheduledTasks modules for this recovery process.
$env:PSModulePath = Join-Path $PSHOME 'Modules'
$script:TaskName = 'CivicCastInstallerRecovery'

function Assert-RecoveryRoot {
    $path = [IO.Path]::GetFullPath($StateRoot)
    while ($path) {
        if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Installer recovery path contains a reparse point.'
        }
        $path = Split-Path -Parent $path
    }
    if (-not (Test-Path -LiteralPath $StateRoot)) { return }
    $trusted = @('S-1-5-18', 'S-1-5-32-544')
    $acl = Get-Acl -LiteralPath $StateRoot
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted) {
        throw 'Installer recovery directory has an untrusted owner; setup stopped without reading its records.'
    }
    $writeRights = [Security.AccessControl.FileSystemRights]::Write -bor [Security.AccessControl.FileSystemRights]::Delete -bor [Security.AccessControl.FileSystemRights]::ChangePermissions -bor [Security.AccessControl.FileSystemRights]::TakeOwnership
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -eq 'Allow' -and ($rule.FileSystemRights -band $writeRights) -and $rule.IdentityReference.Value -notin $trusted) {
            throw 'Installer recovery directory is writable outside administrators/SYSTEM.'
        }
    }
}

function New-RecoveryRoot {
    # Create with the final DACL atomically: never schedule SYSTEM to execute
    # a script in a directory another local account could prepopulate/write.
    $security = [Security.AccessControl.DirectorySecurity]::new()
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($sid), 'FullControl',
            'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $security.AddAccessRule($rule)
    }
    [IO.Directory]::CreateDirectory($StateRoot, $security) | Out-Null
    Assert-RecoveryRoot
}

function Write-Transaction($state) {
    $path = Join-Path $StateRoot 'transaction.clixml'
    $partial = "$path.partial"
    $state | Export-Clixml -LiteralPath $partial -Depth 20
    $stream = [IO.File]::Open($partial, 'Open', 'ReadWrite', 'None')
    try { $stream.Flush($true) } finally { $stream.Dispose() }
    # Another process (the Watch recovery task) may briefly hold the journal
    # open. Retry sharing violations for a bounded time before failing; a
    # failure here would roll back an otherwise good install (R9, 2026-11-13).
    $attempt = 0
    while ($true) {
        $attempt++
        try {
            if (Test-Path -LiteralPath $path) { [IO.File]::Replace($partial, $path, "$path.previous") }
            else { [IO.File]::Move($partial, $path) }
            break
        } catch [IO.IOException] {
            if ($attempt -ge 20) { throw }
            Start-Sleep -Milliseconds 250
        }
    }
}

function Read-Transaction {
    $path = Join-Path $StateRoot 'transaction.clixml'
    if (-not (Test-Path -LiteralPath $path)) { return }
    # Open with FileShare.Delete so a concurrent File.Replace/Move of the
    # journal is never blocked by this reader (Import-Clixml does not share
    # delete access).
    $stream = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite, Delete')
    try {
        $reader = [IO.StreamReader]::new($stream, [Text.Encoding]::UTF8)
        $xml = $reader.ReadToEnd()
    } finally { $stream.Dispose() }
    [Management.Automation.PSSerializer]::Deserialize($xml)
}

function Assert-ChildPath([string]$path, [string]$parent) {
    $full = [IO.Path]::GetFullPath($path).TrimEnd('\')
    $prefix = [IO.Path]::GetFullPath($parent).TrimEnd('\') + '\'
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Recovery path is outside its recorded owner directory.'
    }
}

function Remove-OwnedPath([string]$path, [string]$parent) {
    Assert-ChildPath $path $parent
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
}

function Assert-NoReparsePoints([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return }
    $root = Get-Item -LiteralPath $path -Force
    if ($root.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Recovery cannot safely snapshot a reparse point: $($root.FullName)"
    }
    foreach ($item in Get-ChildItem -LiteralPath $path -Force) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Recovery cannot safely snapshot a reparse point: $($item.FullName)" }
        if ($item.PSIsContainer) { Assert-NoReparsePoints $item.FullName }
    }
}

function Get-RecoveryFiles([string]$path, [string[]]$exclude = @()) {
    if (-not (Test-Path -LiteralPath $path)) { return }
    foreach ($file in Get-ChildItem -LiteralPath $path -File -Recurse -Force | Sort-Object FullName) {
        $skip = $false
        foreach ($excluded in $exclude) {
            if ($file.FullName -match ('(?i)[\\/]' + [regex]::Escape($excluded) + '[\\/]')) { $skip = $true }
        }
        if (-not $skip) { $file }
    }
}

function Get-RecoveryFileId([string]$path, [switch]$LinkCount) {
    if (-not ('CivicCastRecoveryFileId' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
public static class CivicCastRecoveryFileId {
    [StructLayout(LayoutKind.Sequential)]
    private struct Info {
        public uint Attributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME Creation, Access, Write;
        public uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
    }
    [DllImport("kernel32.dll", SetLastError=true)]
    private static extern bool GetFileInformationByHandle(SafeFileHandle handle, out Info info);
    private static Info Inspect(string path) {
        using (var file = new FileStream(path, FileMode.Open, FileAccess.Read,
                FileShare.ReadWrite | FileShare.Delete)) {
            Info info;
            if (!GetFileInformationByHandle(file.SafeFileHandle, out info))
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            return info;
        }
    }
    public static uint Links(string path) { return Inspect(path).Links; }
    public static string Read(string path) {
        var info = Inspect(path);
        return info.Volume.ToString("X8") + ":" + info.IndexHigh.ToString("X8") + info.IndexLow.ToString("X8");
    }
}
'@
    }
    if ($LinkCount) { return [CivicCastRecoveryFileId]::Links($path) }
    return [CivicCastRecoveryFileId]::Read($path)
}

function Write-SnapshotProgress([string]$message) {
    # ExecToStack returns child output only after completion. Write the existing
    # progress log directly so silent installs show the current long-running phase.
    $log = Join-Path (Split-Path -Parent $StateRoot) 'install-progress.log'
    $line = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ') + ' recovery snapshot: ' + $message
    try { [IO.File]::AppendAllText($log, $line + [Environment]::NewLine, [Text.Encoding]::UTF8) }
    catch { Write-Warning 'Recovery snapshot progress could not be written; recovery work continues.' }
}

function Invoke-RecoveryTreeCopy([string[]]$arguments) {
    # Keep robocopy's exact copy/ACL/retry policy and exit-code contract. Wait in
    # bounded intervals solely to report that the snapshot copy is still running.
    $quoted = foreach ($argument in $arguments) {
        $escaped = [regex]::Replace($argument, '(\\*)"', '$1$1\"')
        $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
        '"' + $escaped + '"'
    }
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = "$env:SystemRoot\System32\robocopy.exe"
    $info.Arguments = $quoted -join ' '
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = [Diagnostics.Process]::Start($info)
    try {
        $output = $process.StandardOutput.ReadToEndAsync()
        $errors = $process.StandardError.ReadToEndAsync()
        $elapsed = [Diagnostics.Stopwatch]::StartNew()
        while (-not $process.WaitForExit(15000)) {
            if ($script:SnapshotProgressActive) {
                Write-SnapshotProgress ("copy still running: entry {0}; {1:N0}s elapsed in this copy; copied bytes not measured" -f $script:SnapshotCopyNumber, $elapsed.Elapsed.TotalSeconds)
            }
        }
        if ($process.ExitCode -ge 8) { throw "Recovery copy failed (robocopy exit $($process.ExitCode))." }
    } finally { $process.Dispose() }
}

function Copy-RecoveryTree([string]$source, [string]$target, [string[]]$exclude = @()) {
    if (-not (Test-Path -LiteralPath $source)) { return }
    Assert-NoReparsePoints $source
    Assert-NoReparsePoints $target
    # Preserve data, attributes, times and restrictive DACLs. Copying auditing
    # information would unnecessarily require SeSecurityPrivilege, even for an
    # administrator running this otherwise supported native installer.
    $arguments = @($source, $target, '/MIR', '/COPY:DATS', '/DCOPY:DAT', '/XJ', '/R:1', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
    if ($exclude.Count) { $arguments += '/XD'; $arguments += $exclude }
    # Ollama's components and composed model store share NTFS files. Copy one
    # name per file, then recreate aliases INSIDE the destination tree. Never
    # link a backup to live bytes: in-place writes must not change recovery.
    $first = @{}
    $aliases = @()
    foreach ($file in Get-RecoveryFiles $source $exclude) {
        $id = Get-RecoveryFileId $file.FullName
        $relative = $file.FullName.Substring($source.TrimEnd('\').Length + 1)
        if ($first.ContainsKey($id)) {
            $aliases += @{ Path = $relative; Original = $first[$id]; Source = $file.FullName }
        } else { $first[$id] = $relative }
    }
    if ($aliases.Count) {
        $arguments += '/XF'
        foreach ($alias in $aliases) {
            $arguments += $alias.Source
            $destination = Join-Path $target $alias.Path
            $arguments += $destination
            # These names are recreated below. Remove them before copying so
            # the last old alias cannot retain a second full model's bytes.
            Assert-ChildPath $destination $target
            if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Force }
        }
    }
    # Reproducible packages can have different bytes with identical size/time.
    # Remove destination files before copying so robocopy cannot skip them by
    # metadata. This also detaches hardlinks without writing through aliases
    # outside this tree. The independent source remains intact for retry, and
    # excluded immutable cache files are never returned by Get-RecoveryFiles.
    $destinationFiles = @(Get-RecoveryFiles $target $exclude)
    foreach ($file in $destinationFiles) {
        Assert-ChildPath $file.FullName $target
        Remove-Item -LiteralPath $file.FullName -Force
    }
    Invoke-RecoveryTreeCopy $arguments
    foreach ($alias in $aliases) {
        $destination = Join-Path $target $alias.Path
        $original = Join-Path $target $alias.Original
        Assert-ChildPath $destination $target
        Assert-ChildPath $original $target
        if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Force }
        New-Item -ItemType HardLink -Path $destination -Target $original | Out-Null
    }
}

function Get-TreeBytes([string]$path, [string[]]$exclude = @()) {
    [long]$bytes = 0
    $seen = @{}
    foreach ($file in Get-RecoveryFiles $path $exclude) {
        $id = Get-RecoveryFileId $file.FullName
        if (-not $seen.ContainsKey($id)) { $bytes += $file.Length; $seen[$id] = $true }
    }
    return $bytes
}

function Get-RegistryTree([string]$path) {
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey('LocalMachine', 'Registry64')
    try {
        $key = $base.OpenSubKey($path)
        if (-not $key) { return $null }
        try {
            $values = @()
            foreach ($name in $key.GetValueNames()) {
                $values += @{ Name = $name; Kind = [int]$key.GetValueKind($name); Value = $key.GetValue($name, $null, 'DoNotExpandEnvironmentNames') }
            }
            $children = @{}
            foreach ($name in $key.GetSubKeyNames()) { $children[$name] = Get-RegistryTree "$path\$name" }
            return @{ Values = $values; Children = $children }
        } finally { $key.Dispose() }
    } finally { $base.Dispose() }
}

function Set-RegistryTree([string]$path, $tree) {
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey('LocalMachine', 'Registry64')
    try {
        # Restore only captured product keys; do not remove unrelated parent keys.
        if ($null -eq $tree) { $base.DeleteSubKeyTree($path, $false); return }
        $key = $base.CreateSubKey($path)
        try {
            foreach ($name in $key.GetValueNames()) {
                if ($name -notin @($tree.Values | ForEach-Object { $_.Name })) { $key.DeleteValue($name, $false) }
            }
            foreach ($value in $tree.Values) {
                $kind = [Microsoft.Win32.RegistryValueKind]$value.Kind
                $data = $value.Value
                if ($kind -eq 'Binary' -or $kind -eq 'None') { $data = [byte[]]$data }
                if ($kind -eq 'MultiString') { $data = [string[]]$data }
                if ($kind -eq 'DWord') { $data = [int]$data }
                if ($kind -eq 'QWord') { $data = [long]$data }
                $key.SetValue($value.Name, $data, $kind)
            }
            foreach ($name in $key.GetSubKeyNames()) {
                if (-not $tree.Children.ContainsKey($name)) { $key.DeleteSubKeyTree($name, $false) }
            }
            foreach ($name in $tree.Children.Keys) { Set-RegistryTree "$path\$name" $tree.Children[$name] }
        } finally { $key.Dispose() }
    } finally { $base.Dispose() }
}

function Invoke-ServiceCommand([string[]]$arguments, [int[]]$accepted = @(0)) {
    # Windows PowerShell drops empty native arguments. SCM uses an explicit
    # empty string to clear dependencies/actions, so construct the documented
    # Windows argv quoting here rather than relying on that lossy binder.
    $quoted = foreach ($argument in $arguments) {
        $escaped = [regex]::Replace($argument, '(\\*)"', '$1$1\"')
        $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
        '"' + $escaped + '"'
    }
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = "$env:SystemRoot\System32\sc.exe"
    $info.Arguments = $quoted -join ' '
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = [Diagnostics.Process]::Start($info)
    try {
        $output = $process.StandardOutput.ReadToEndAsync()
        $errors = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(30000)) { $process.Kill(); throw 'Recovery service command timed out.' }
        if ($process.ExitCode -notin $accepted) { throw "Recovery service command $($arguments[0]) failed ($($process.ExitCode))." }
    } finally { $process.Dispose() }
}

function Stop-TransactionWriters($state) {
    $service = Get-Service -Name CivicCastSupervisor -ErrorAction SilentlyContinue
    if ($service) {
        Invoke-ServiceCommand @('config', 'CivicCastSupervisor', 'start=', 'demand')
        if ($service.Status -ne 'Stopped') {
            Invoke-ServiceCommand @('stop', 'CivicCastSupervisor') @(0, 1061, 1062)
            $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(90))
        }
        $service.Dispose()
    }
    $pgdata = Join-Path $state.DataRoot 'data\pgdata'
    $pgctl = Join-Path $state.InstallRoot 'packs\native-server-binaries\payload\bin\pg_ctl.exe'
    if (Test-Path -LiteralPath (Join-Path $pgdata 'postmaster.pid')) {
        if (-not (Test-Path -LiteralPath $pgctl)) { $pgctl = Join-Path $StateRoot 'backup\app\packs\native-server-binaries\payload\bin\pg_ctl.exe' }
        if (-not (Test-Path -LiteralPath $pgctl)) { throw 'Cannot confirm preserved PostgreSQL is stopped; its pg_ctl is missing.' }
        & $pgctl status -D $pgdata 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            & $pgctl stop -D $pgdata -m fast -w -t 90 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) { throw 'Could not stop station PostgreSQL for recovery.' }
        } elseif ($LASTEXITCODE -ne 3) { throw 'Could not determine preserved PostgreSQL state.' }
    }
    # A killed setup may strand activation self-test children. Scope termination
    # to executable paths inside the recorded installation, never process names.
    $prefix = $state.InstallRoot.TrimEnd('\') + '\'
    foreach ($process in Get-CimInstance Win32_Process) {
        if ($process.ExecutablePath -and $process.ExecutablePath.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
        }
    }
}

function Get-ServiceSnapshot {
    $service = Get-CimInstance Win32_Service -Filter "Name='CivicCastSupervisor'"
    if (-not $service) { return $null }
    if ($service.StartName -notin @('LocalSystem', 'NT AUTHORITY\SYSTEM', 'NT SERVICE\CivicCastSupervisor')) {
        throw 'Automatic restoration supports the CivicCast virtual account or legacy LocalSystem account; setup stopped before replacement.'
    }
    if ($service.State -notin @('Running', 'Stopped')) { throw 'Native service is changing state; retry setup after it settles.' }
    $tree = Get-RegistryTree 'SYSTEM\CurrentControlSet\Services\CivicCastSupervisor'
    $failure = @($tree.Values | Where-Object Name -eq 'FailureActions' | ForEach-Object Value)
    # Only the product's restart/none recovery configuration is supported.
    # Reject reboot/program actions before changing anything, not during restore.
    if ($failure.Count) {
        $blob = [byte[]]$failure
        if ($blob.Length -lt 20) { throw 'Invalid prior service failure actions.' }
        $count = [BitConverter]::ToUInt32($blob, 12)
        $offset = [BitConverter]::ToUInt32($blob, 16)
        if ([long]$offset + [long]$count * 8 -gt $blob.Length) { throw 'Invalid prior service failure actions.' }
        for ($i = 0; $i -lt $count; $i++) {
            if ([BitConverter]::ToUInt32($blob, $offset + 8 * $i) -notin @(0, 1)) { throw 'Unsupported custom service failure action; setup stopped before replacement.' }
        }
    }
    return @{ Running = $service.State -eq 'Running'; Tree = $tree }
}

function Restore-ServiceSnapshot($state) {
    $saved = $state.Service
    $service = Get-Service -Name CivicCastSupervisor -ErrorAction SilentlyContinue
    if ($null -eq $saved) {
        if ($service) { $service.Dispose(); Invoke-ServiceCommand @('delete', 'CivicCastSupervisor') @(0, 1060) }
        return
    }
    $values = @{}
    foreach ($item in $saved.Tree.Values) { $values[$item.Name] = $item.Value }
    $savedAccount = [string]$values.ObjectName
    if ($savedAccount -notin @('LocalSystem', 'NT AUTHORITY\SYSTEM', 'NT SERVICE\CivicCastSupervisor')) {
        throw 'Unsupported saved service identity; refusing to substitute LocalSystem.'
    }
    if (-not $service) {
        Invoke-ServiceCommand @('create', 'CivicCastSupervisor', 'binPath=', [string]$values.ImagePath, 'start=', 'demand', 'obj=', $savedAccount)
    }
    if ($service) { $service.Dispose() }
    $start = switch ([int]$values.Start) { 2 { if ($values.DelayedAutoStart) { 'delayed-auto' } else { 'auto' } } 3 { 'demand' } 4 { 'disabled' } default { throw 'Unsupported prior service start mode.' } }
    # Restore raw registry parameters (including PythonClass/PythonPath), then
    # refresh SCM's cached failure-action configuration using its public command.
    Set-RegistryTree 'SYSTEM\CurrentControlSet\Services\CivicCastSupervisor' $saved.Tree
    $effectiveStart = if ($saved.Running) { 'demand' } else { $start }
    Invoke-ServiceCommand @('config', 'CivicCastSupervisor', 'binPath=', [string]$values.ImagePath, 'start=', $effectiveStart, 'obj=', $savedAccount, 'DisplayName=', [string]$values.DisplayName, 'depend=', (@($values.DependOnService) -join '/'))
    if ($values.ContainsKey('FailureActions')) {
        $blob = [byte[]]$values.FailureActions
        $count = [BitConverter]::ToUInt32($blob, 12)
        $offset = [BitConverter]::ToUInt32($blob, 16)
        $actions = @()
        for ($i = 0; $i -lt $count; $i++) {
            $type = [BitConverter]::ToUInt32($blob, $offset + 8 * $i)
            $delay = [BitConverter]::ToUInt32($blob, $offset + 8 * $i + 4)
            $name = switch ($type) { 0 { 'none' } 1 { 'restart' } default { throw 'Unsupported service recovery action in prior installation.' } }
            $actions += "$name/$delay"
        }
        Invoke-ServiceCommand @('failure', 'CivicCastSupervisor', 'reset=', [string][BitConverter]::ToUInt32($blob, 0), 'actions=', ($actions -join '/'))
    } else { Invoke-ServiceCommand @('failure', 'CivicCastSupervisor', 'reset=', '0', 'actions=', '') }
    Invoke-ServiceCommand @('failureflag', 'CivicCastSupervisor', $(if ($values.FailureActionsOnNonCrashFailures) { '1' } else { '0' }))
    if ($saved.Running) {
        Invoke-ServiceCommand @('start', 'CivicCastSupervisor')
        (Get-Service CivicCastSupervisor).WaitForStatus('Running', [TimeSpan]::FromSeconds(90))
        Wait-RestoredStation $state
        Invoke-ServiceCommand @('config', 'CivicCastSupervisor', 'start=', $start)
    }
    # SCM calls above can add default registry values. Put the exact captured
    # product service parameters back after refreshing SCM's cached settings.
    Set-RegistryTree 'SYSTEM\CurrentControlSet\Services\CivicCastSupervisor' $saved.Tree
}

function Wait-RestoredStation($state) {
    $deadline = [DateTime]::UtcNow.AddSeconds(120)
    do {
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 5
            if ($health.schema -eq 'current' -and $health.status -eq 'healthy' -and
                $health.schema_db_revision -and $health.schema_db_revision -eq $health.schema_expected_head -and
                (-not $state.Version -or $health.version -eq $state.Version)) { return }
        } catch { }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'Prior service started but its version/schema health was not confirmed.'
}

function Register-RecoveryTask {
    $ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + (Join-Path $StateRoot 'install-transaction.ps1') + '" -Action Recover -StateRoot "' + $StateRoot + '"'
    $taskAction = New-ScheduledTaskAction -Execute $ps -Argument $arguments
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval ([TimeSpan]::FromMinutes(1))
    Register-ScheduledTask -TaskName $script:TaskName -Action $taskAction -Trigger $trigger -Settings $settings -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
}

function Clear-RecoveryTask {
    if (Get-ScheduledTask -TaskName $script:TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $script:TaskName -Confirm:$false -ErrorAction Stop
    }
}

function Test-InstallerAlive($state) {
    try {
        $process = Get-Process -Id $state.InstallerPid -ErrorAction Stop
        return $process.StartTime.ToUniversalTime().Ticks -eq $state.InstallerStartTicks
    } catch { return $false }
}

function Begin-Transaction {
    $prior = Read-Transaction
    if ($prior -and $prior.Phase -notin @('Committed', 'Restored')) {
        if (Test-InstallerAlive $prior) { throw 'Another CivicCast installer transaction is active.' }
        Restore-Transaction $prior -Abandoned
    }
    if ($prior -and $prior.Phase -eq 'Committed') { Finish-Commit $prior }
    if (-not [IO.Path]::IsPathRooted($InstallRoot) -or [IO.Path]::GetPathRoot($InstallRoot) -eq $InstallRoot.TrimEnd('\') + '\') { throw 'Invalid installation root for recovery.' }
    $dataRoot = Split-Path -Parent $StateRoot
    $service = Get-ServiceSnapshot
    $registry = @{}
    foreach ($key in @('SOFTWARE\CivicCast', 'SOFTWARE\civiccast\CivicCast (Native)', $UninstallKey)) {
        if ($key) { $registry[$key] = Get-RegistryTree $key }
    }
    # The supported native station owns a local cluster at data\pgdata. An
    # external DatabaseUrl cannot be rolled back by copying this directory.
    $native = Get-RegistryTree 'SOFTWARE\CivicCast\Native'
    $url = @($native.Values | Where-Object Name -eq 'DatabaseUrl' | ForEach-Object Value)
    if ($url.Count -and $url[0]) {
        try { $endpoint = [uri]($url[0] -replace '^postgresql\+[^:]+:', 'postgresql:') }
        catch { throw 'The saved database endpoint could not be classified safely.' }
        if ($endpoint.Host -notin @('127.0.0.1', 'localhost', '::1') -or -not (Test-Path -LiteralPath (Join-Path $dataRoot 'data\pgdata\PG_VERSION'))) {
            throw 'Setup cannot automatically restore this external/adopted database; no application files were replaced.'
        }
        $config = Get-Content -LiteralPath (Join-Path $dataRoot 'data\pgdata\postgresql.conf') -Raw
        $ports = [regex]::Matches($config, '(?m)^\s*port\s*=\s*(\d+)\s*(?:#.*)?$')
        $port = if ($ports.Count) { [int]$ports[$ports.Count - 1].Groups[1].Value } else { 5432 }
        $urlPort = if ($endpoint.Port -gt 0) { $endpoint.Port } else { 5432 }
        if ($urlPort -ne $port -or $config -match '(?m)^\s*(?:data_directory|include|include_dir|include_if_exists)\s*=') {
            throw 'Database endpoint/configuration does not identify the supported native cluster; setup stopped before replacement.'
        }
        # A loopback URL alone does not prove ownership: another PostgreSQL
        # instance could be listening there while this preserved cluster is
        # stopped. Bind a live endpoint to this cluster's postmaster PID/data
        # directory before allowing any migration to reach it.
        $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object LocalPort -eq $port)
        if ($listeners.Count) {
            $pidPath = Join-Path $dataRoot 'data\pgdata\postmaster.pid'
            if (-not (Test-Path -LiteralPath $pidPath)) { throw 'The configured database port belongs to a different instance; setup stopped before replacement.' }
            $postmaster = @(Get-Content -LiteralPath $pidPath)
            if ($postmaster.Count -lt 2 -or $postmaster[0] -notmatch '^\d+$' -or
                [IO.Path]::GetFullPath($postmaster[1]).TrimEnd('\') -ne [IO.Path]::GetFullPath((Join-Path $dataRoot 'data\pgdata')).TrimEnd('\') -or
                @($listeners | Where-Object OwningProcess -ne ([int]$postmaster[0])).Count) {
                throw 'The live database endpoint is not the supported station cluster; setup stopped before replacement.'
            }
        }
        $autoConfig = Join-Path $dataRoot 'data\pgdata\postgresql.auto.conf'
        if ((Test-Path -LiteralPath $autoConfig) -and (Get-Content -LiteralPath $autoConfig -Raw) -match '(?m)^\s*(?:port|data_directory|include|include_dir|include_if_exists)\s*=') {
            throw 'Custom PostgreSQL endpoint configuration cannot be safely restored by this installer.'
        }
    }
    $version = @($native.Values | Where-Object Name -eq 'InstalledVersion' | ForEach-Object Value)
    $firewall = New-Object -ComObject HNetCfg.FwPolicy2
    $firewallExisted = @($firewall.Rules | Where-Object Name -eq 'CivicCast (Native) Portal/API (TCP 8000)').Count -gt 0
    $state = @{ Id = [guid]::NewGuid().ToString('N'); Phase = 'Prepared'; InstallRoot = [IO.Path]::GetFullPath($InstallRoot).TrimEnd('\'); DataRoot = $dataRoot; StartMenuFolder = $StartMenuFolder; Service = $service; Registry = $registry; Version = ($version | Select-Object -First 1); FirewallExisted = $firewallExisted; InstallerPid = $InstallerPid; InstallerStartTicks = (Get-Process -Id $InstallerPid).StartTime.ToUniversalTime().Ticks; Entries = @(); SnapshotComplete = $false }
    New-RecoveryRoot
    Copy-Item -LiteralPath $PSCommandPath -Destination (Join-Path $StateRoot 'install-transaction.ps1') -Force
    Write-Transaction $state
    Register-RecoveryTask
    $ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' + (Join-Path $StateRoot 'install-transaction.ps1') + '" -Action Watch -StateRoot "' + $StateRoot + '" -TransactionId ' + $state.Id
    Start-Process -FilePath $ps -ArgumentList $arguments -WindowStyle Hidden | Out-Null
    Write-Output "Recovery prepared: $($state.Id)"
}

function Snapshot-Transaction($state) {
    if ($state.Phase -ne 'Prepared') { throw 'Installer recovery snapshot was not prepared.' }
    Write-SnapshotProgress 'quiescing station writers before recovery copy'
    Stop-TransactionWriters $state
    Write-SnapshotProgress 'measuring recovery size; application and mutable station state only; media and immutable cache excluded'
    $backup = Join-Path $StateRoot 'backup'
    # Only data the installer mutates. Recordings, uploads and other media are
    # excluded; the cold cluster snapshot includes globals, schema and configs.
    $entries = @(@{ Source = $state.InstallRoot; Backup = (Join-Path $backup 'app'); Exclude = @('.station-cache') })
    foreach ($relative in @('data\pgdata', 'provision', 'upgrade')) {
        $entries += @{ Source = (Join-Path $state.DataRoot $relative); Backup = (Join-Path $backup $relative); Exclude = @() }
    }
    $shortcutNumber = 0
    foreach ($shortcut in Get-InstallerShortcutPaths $state) {
        $entries += @{ Source = $shortcut.Path; Backup = (Join-Path $backup "shortcuts\$shortcutNumber"); Exclude = @(); Kind = 'File'; Owner = $shortcut.Owner }
        $shortcutNumber++
    }
    [long]$required = 0
    foreach ($entry in $entries) {
        if ($entry.Kind -eq 'File') {
            if ((Test-Path -LiteralPath $entry.Source) -and ((Get-Item -LiteralPath $entry.Source -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Installer shortcut is a reparse point.' }
        } else { Assert-NoReparsePoints $entry.Source }
        $entry.Existed = Test-Path -LiteralPath $entry.Source
        if ($entry.Kind -eq 'File') {
            if ($entry.Existed) { $required += (Get-Item -LiteralPath $entry.Source).Length }
        } else { $required += Get-TreeBytes $entry.Source $entry.Exclude }
    }
    # Leave headroom for copy metadata and a journal even when the volume is
    # tight; candidate's own staging has its separate free-space check.
    $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($StateRoot))
    Write-SnapshotProgress ("size measured: {0} bytes; required reserve: {1} bytes; available: {2} bytes" -f $required, 256MB, $drive.AvailableFreeSpace)
    if ($drive.AvailableFreeSpace -lt $required + 256MB) { throw "Recovery snapshot needs $required bytes plus 256 MB reserve; setup stopped before replacement." }
    $state.Entries = $entries
    Write-Transaction $state
    Remove-OwnedPath $backup $StateRoot
    $script:SnapshotProgressActive = $true
    $script:SnapshotCopyNumber = 0
    try {
        foreach ($entry in $entries) {
            $script:SnapshotCopyNumber++
            Write-SnapshotProgress ("copying entry {0} of {1}; total recovery size {2} bytes" -f $script:SnapshotCopyNumber, $entries.Count, $required)
            Copy-RecoveryEntry $entry $entry.Source $entry.Backup
            Write-SnapshotProgress ("copied entry {0} of {1}" -f $script:SnapshotCopyNumber, $entries.Count)
        }
    } finally { $script:SnapshotProgressActive = $false }
    $state.SnapshotComplete = $true
    $state.Phase = 'Active'
    Write-Transaction $state
    Write-SnapshotProgress "complete: $required bytes; recovery point recorded before application replacement"
    Write-Output "Recovery snapshot complete: $required bytes"
}

function Hide-UrlPasswords([string]$Path) {
    try {
        $text = [IO.File]::ReadAllText($Path)
        $clean = [regex]::Replace($text, '(?i)([a-z][a-z0-9+.\-]*://[^:/@\s"'']+:)[^@/\s"'']+(@)', '$1***$2')
        $clean = [regex]::Replace($clean, '(?i)([?&](?:password|passwd|pwd)=)[^&\s"'']+', '$1***')
        if ($clean -ne $text) { [IO.File]::WriteAllText($Path, $clean, (New-Object Text.UTF8Encoding($false))) }
    } catch { Remove-Item -LiteralPath $Path -Force -ErrorAction SilentlyContinue }
}

function Restore-Transaction($state, [switch]$Abandoned) {
    if (-not $state -or $state.Phase -in @('Committed', 'Restored')) { return }
    if (-not $Abandoned -and $InstallerPid -and $state.InstallerPid -ne $InstallerPid) { throw 'Recovery transaction belongs to another installer.' }
    Stop-TransactionWriters $state
    # Keep the failed run's diagnostics before restoring the prior journals.
    # They are small and remain inside the same restricted recovery directory.
    $diagnostics = Join-Path $StateRoot 'failure-diagnostics'
    New-Item -ItemType Directory -Path $diagnostics -Force | Out-Null
    foreach ($relative in @('upgrade\upgrade-engine.log', 'upgrade\upgrade-journal.json', 'provision\PROVISION-RECOVERY.md', 'provision\OWNERSHIP-RECOVERY.md')) {
        $source = Join-Path $state.DataRoot $relative
        if (Test-Path -LiteralPath $source -PathType Leaf) {
            $copy = Join-Path $diagnostics ([IO.Path]::GetFileName($source))
            Copy-Item -LiteralPath $source -Destination $copy -Force
            # Defense in depth: never leave a connection-URL password in the
            # diagnostics copy (user:password@host -> user:***@host).
            Hide-UrlPasswords $copy
        }
    }
    $state.Phase = 'Restoring'
    Write-Transaction $state
    if ($state.SnapshotComplete) {
        # Copies are retained throughout restore: retrying after interruption
        # cannot lose the only known-good tree in a partially completed move.
        foreach ($entry in $state.Entries) {
            if ($entry.Existed) { Copy-RecoveryEntry $entry $entry.Backup $entry.Source }
            else {
                $parent = if ($entry.Owner) { $entry.Owner } elseif ($entry.Source -eq $state.InstallRoot) { Split-Path -Parent $state.InstallRoot } else { $state.DataRoot }
                Remove-OwnedPath $entry.Source $parent
            }
        }
    }
    foreach ($key in $state.Registry.Keys) { Set-RegistryTree $key $state.Registry[$key] }
    Restore-TransactionFirewall $state
    Restore-ServiceSnapshot $state
    $state.Phase = 'Restored'
    Write-Transaction $state
    Remove-OwnedPath (Join-Path $StateRoot 'backup') $StateRoot
    Clear-RecoveryTask
    Write-Output 'Prior CivicCast station automatically restored.'
}

function Get-InstallerShortcutPaths($state) {
    $programs = [Environment]::GetFolderPath('CommonPrograms')
    $desktop = [Environment]::GetFolderPath('CommonDesktopDirectory')
    $productFolder = Join-Path $programs 'CivicCast (Native)'
    $paths = @(
        @{ Path = (Join-Path $productFolder 'CivicCast Operator Console.url'); Owner = $programs },
        @{ Path = (Join-Path $productFolder 'CivicCast Public Portal.url'); Owner = $programs },
        @{ Path = (Join-Path $programs 'CivicCast (Native).lnk'); Owner = $programs },
        @{ Path = (Join-Path $desktop 'CivicCast (Native).lnk'); Owner = $desktop },
        @{ Path = (Join-Path $desktop 'CivicCast Operator Console.url'); Owner = $desktop }
    )
    if ($state.StartMenuFolder) {
        $folder = [IO.Path]::GetFullPath((Join-Path $programs $state.StartMenuFolder))
        Assert-ChildPath $folder $programs
        $paths += @{ Path = (Join-Path $folder 'CivicCast (Native).lnk'); Owner = $programs }
    }
    return $paths
}

function Copy-RecoveryEntry($entry, [string]$source, [string]$target) {
    if ($entry.Kind -eq 'File') {
        if (Test-Path -LiteralPath $source) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
            Copy-Item -LiteralPath $source -Destination $target -Force
            Set-Acl -LiteralPath $target -AclObject (Get-Acl -LiteralPath $source)
        }
    } else { Copy-RecoveryTree $source $target $entry.Exclude }
}

function Restore-TransactionFirewall($state) {
    # Registration leaves an existing rule unchanged. Only remove a rule this
    # transaction added; never touch other station firewall configuration.
    if (-not $state.FirewallExisted) {
        $firewall = New-Object -ComObject HNetCfg.FwPolicy2
        if (@($firewall.Rules | Where-Object Name -eq 'CivicCast (Native) Portal/API (TCP 8000)').Count) {
            $firewall.Rules.Remove('CivicCast (Native) Portal/API (TCP 8000)')
        }
    }
}

function Commit-Transaction($state) {
    if (-not $state -or $state.Phase -ne 'Active') { throw 'Installer transaction is not ready to commit.' }
    # The NSIS caller has completed D2/D3/D4, health, registry and shortcuts.
    # Record commit before restoring auto-start; startup recovery finishes that
    # operation if the machine loses power in this final window.
    $state.Phase = 'Committed'
    Write-Transaction $state
    try { Finish-Commit $state }
    catch {
        # The station is already fully installed and health-checked. This is
        # post-commit housekeeping, not a failed upgrade: startup recovery owns
        # finishing auto-start and cleanup, never rolling committed data back.
        Write-Output 'Installation committed; automatic-start/backup cleanup will be retried by the recovery task.'
    }
}

function Finish-Commit($state) {
    # A later setup also checks this journal before its read-only downgrade
    # guard. Never undo an operator's subsequent manual/disabled preference.
    if ($state.Finalized) { Clear-RecoveryTask; return }
    Invoke-ServiceCommand @('config', 'CivicCastSupervisor', 'start=', 'auto')
    Remove-OwnedPath (Join-Path $StateRoot 'backup') $StateRoot
    $state.Finalized = $true
    Write-Transaction $state
    Clear-RecoveryTask
    Write-Output 'CivicCast installer transaction committed.'
}

function Recover-Transaction($state) {
    if ($state -and $state.Phase -eq 'Committed') { Finish-Commit $state }
    elseif ($state -and $state.Phase -eq 'Restored') {
        Remove-OwnedPath (Join-Path $StateRoot 'backup') $StateRoot
        Clear-RecoveryTask
    }
    elseif ($state -and -not (Test-InstallerAlive $state)) { Restore-Transaction $state -Abandoned }
}

# Dot-sourcing exposes the actual filesystem/state operations for focused tests,
# without invoking SCM, registry or scheduled tasks on the development machine.
if ($MyInvocation.InvocationName -ne '.' -and $Action) {
    $mutex = $null
    $held = $false
    try {
        Assert-RecoveryRoot
        if ($Action -ne 'Begin' -and (Test-Path -LiteralPath $StateRoot)) {
            Set-Location -LiteralPath $StateRoot
            [Environment]::CurrentDirectory = $StateRoot
        }
        if ($Action -eq 'Watch') {
            do {
                # The journal can be mid-replace; skip this tick on an IO error.
                try { $state = Read-Transaction }
                catch [IO.IOException] { Start-Sleep -Seconds 1; continue }
                catch [UnauthorizedAccessException] { Start-Sleep -Seconds 1; continue }
                if (-not $state -or $state.Id -ne $TransactionId -or $state.Phase -in @('Committed', 'Restored')) { exit 0 }
                if (-not (Test-InstallerAlive $state)) { break }
                Start-Sleep -Seconds 1
            } while ($true)
        }
        $mutex = [Threading.Mutex]::new($false, 'Global\CivicCastInstallerRecovery')
        # A killed install may leave its snapshot child copying for minutes.
        # Wait for that protected operation rather than abandoning recovery
        # after a short timeout. An exited child releases/abandons this mutex.
        try { $held = $mutex.WaitOne() } catch [Threading.AbandonedMutexException] { $held = $true }
        if (-not $held) { throw 'Another recovery operation is active.' }
        $state = Read-Transaction
        switch ($Action) {
            'Begin' { Begin-Transaction }
            'Snapshot' { Snapshot-Transaction $state }
            'Commit' { Commit-Transaction $state }
            'Restore' { Restore-Transaction $state }
            { $_ -in @('Watch', 'Recover') } { Recover-Transaction $state }
        }
        exit 0
    } catch {
        # The ACL-protected journal/backups and startup task remain for retry.
        # Never put registry values/credentials into the diagnostic log.
        $detail = $_.Exception.Message -replace 'postgresql(?:\+[^: ]+)?://\S+', '[database endpoint redacted]'
        if (Test-Path -LiteralPath $StateRoot -PathType Container) {
            try { Set-Content -LiteralPath (Join-Path $StateRoot 'recovery-error.txt') -Value "Action=$Action`r`n$detail" -Encoding UTF8 }
            catch { }
        }
        Write-Output "CivicCast automatic recovery failed: $detail"
        exit 136
    } finally {
        if ($held) { $mutex.ReleaseMutex() }
        if ($mutex) { $mutex.Dispose() }
    }
}
