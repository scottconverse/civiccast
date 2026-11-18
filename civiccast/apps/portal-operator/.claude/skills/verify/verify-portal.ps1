# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
#
# Operator-portal verify: build, start `vite preview` hidden on a free port,
# drive real operator flows headless with Playwright against real CivicCast
# API processes, save trace and screenshot evidence, and stop the preview by
# the PID this script started. Never stops anything by process name.
[CmdletBinding()]
param(
    [int]$Port = 0,
    [string]$EvidenceDir,
    [ValidateSet('publish', 'setup')][string[]]$Flows = @('publish'),   # add 'setup' when the disk is quiet (see SKILL.md)
    [switch]$Install,      # force `npm ci` even when node_modules exists
    [switch]$SkipBuild,    # reuse the existing dist folders
    [int]$TestTimeoutSec = 900,
    [int]$PhaseTimeoutSec = 900   # limit for each install and build phase
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0

$appDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$publicDir = (Resolve-Path (Join-Path $appDir '..\portal-public')).Path
Import-Module (Join-Path $appDir '..\..\..\.claude\skills\verify\scripts\VerifyCommon.psm1') -Force
$repo = Get-RepoRoot -From $appDir
$evidence = New-EvidenceDir -RepoRoot $repo -Suffix 'portal' -Requested $EvidenceDir
$procLog = Join-Path $evidence 'processes.log'
$python = Join-Path $repo '.venv\Scripts\python.exe'
$node = (Get-Command node -ErrorAction Stop).Source
$npm = (Get-Command npm.cmd -ErrorAction Stop).Source
$specs = @{
    publish = 'e2e/full-stack-publish.spec.ts'
    setup   = 'e2e/setup-real-boundary.spec.ts'
}
$phases = [System.Collections.Generic.List[object]]::new()
$failures = [System.Collections.Generic.List[string]]::new()
$runStart = Get-Date

function Write-ProcLog([string]$line) { Add-Content -Path $procLog -Value ("{0:HH:mm:ss.fff} {1}" -f (Get-Date), $line) }

function Invoke-Phase {
    # Runs one phase hidden with a time limit. On timeout the phase tree is
    # stopped by its recorded PID (never by name) and the phase fails.
    param([string]$Name, [string]$Dir, [string]$Exe, [string[]]$Arguments, [int]$TimeoutSec = $PhaseTimeoutSec)
    $log = Join-Path $evidence "$Name.log"
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $rec = Start-HiddenProcess -FilePath $Exe -ArgumentList $Arguments -WorkingDirectory $Dir `
        -StdOut $log -StdErr (Join-Path $evidence "$Name.err.log") -Marker $Arguments[0]
    $rec | Add-Member -NotePropertyName MarkerMode -NotePropertyValue 'prefix'
    $null = $rec.Process.Handle   # keep the handle so ExitCode is readable after exit
    Write-ProcLog "started $Name pid $($rec.Pid) limit ${TimeoutSec}s cmd: $($rec.CommandLine)"
    if ($rec.Process.WaitForExit($TimeoutSec * 1000)) {
        $rec.Process.WaitForExit()
        $code = $rec.Process.ExitCode
    } else {
        Write-ProcLog ("$Name timed out after ${TimeoutSec}s: " + (Stop-OwnedProcess -Record $rec))
        $code = 124
    }
    $sec = [math]::Round($sw.Elapsed.TotalSeconds, 1)
    $phases.Add([pscustomobject]@{ phase = $Name; seconds = $sec; exit = $code; log = (Split-Path $log -Leaf) })
    Write-Output ("{0,-22} {1,7} s  exit {2}" -f $Name, $sec, $code)
    if ($code -ne 0) { $failures.Add("$Name failed (exit $code), see $log"); throw "$Name failed (exit $code). See $log" }
}

if (-not (Test-Path $python)) { throw "Missing $python. Run once at the repo root: uv sync --all-extras --no-extra agenda-js-import --group dev" }

$preview = $null
$result = 'FAIL'
try {
    # 1. Install (only when missing or forced; lockfile installs).
    if ($Install -or -not (Test-Path (Join-Path $appDir 'node_modules\.package-lock.json'))) {
        Invoke-Phase 'install-operator' $appDir $npm @('ci', '--no-audit', '--no-fund')
    }
    if ('setup' -in $Flows -and ($Install -or -not (Test-Path (Join-Path $publicDir 'node_modules\.package-lock.json')))) {
        Invoke-Phase 'install-public' $publicDir $npm @('ci', '--no-audit', '--no-fund')
    }
    # 2. Build what the preview and the setup flow serve.
    if (-not $SkipBuild) {
        Invoke-Phase 'build-operator' $appDir $npm @('run', 'build')
        if ('setup' -in $Flows) { Invoke-Phase 'build-public' $publicDir $npm @('run', 'build') }
    }
    if (-not (Test-Path (Join-Path $appDir 'dist\index.html'))) { throw 'portal-operator dist is missing; run without -SkipBuild.' }

    # 3. Start the preview hidden on a free loopback port and record its PID.
    if ($Port -eq 0) { $Port = Get-FreeLoopbackPort } else { Assert-PortFree -Port $Port }
    $baseUrl = "http://127.0.0.1:$Port"
    $vite = Join-Path $appDir 'node_modules\vite\bin\vite.js'
    $preview = Start-HiddenProcess -FilePath $node -WorkingDirectory $appDir `
        -ArgumentList @("`"$vite`"", 'preview', '--host', '127.0.0.1', '--port', "$Port", '--strictPort') `
        -StdOut (Join-Path $evidence 'preview.out.log') -StdErr (Join-Path $evidence 'preview.err.log') `
        -Marker "--port $Port"
    Write-ProcLog "started preview pid $($preview.Pid) created $($preview.StartTime) cmd: $($preview.CommandLine)"
    $up = Wait-HttpOk -Url "$baseUrl/" -Record $preview -TimeoutSec 60
    $phases.Add([pscustomobject]@{ phase = 'preview-up'; seconds = $up; exit = 0; log = 'preview.out.log' })
    Write-Output ("{0,-22} {1,7} s  {2} pid {3}" -f 'preview-up', $up, $baseUrl, $preview.Pid)

    # 4. Pick the headless browser. Use the revision this Playwright pins when it
    #    is installed. If not, use the closest NEWER installed headless shell
    #    through the app's CIVICCAST_PLAYWRIGHT_EXECUTABLE hook, and say so in the
    #    report. Never download a browser here.
    $browserNote = 'pinned'
    if (-not $env:CIVICCAST_PLAYWRIGHT_EXECUTABLE) {
        $pins = Get-Content (Join-Path $appDir 'node_modules\playwright-core\browsers.json') -Raw | ConvertFrom-Json
        $want = [int](($pins.browsers | Where-Object name -eq 'chromium-headless-shell').revision)
        $msp = if ($env:PLAYWRIGHT_BROWSERS_PATH) { $env:PLAYWRIGHT_BROWSERS_PATH } else { Join-Path $env:LOCALAPPDATA 'ms-playwright' }
        $shells = @(Get-ChildItem $msp -Directory -Filter 'chromium_headless_shell-*' -ErrorAction SilentlyContinue | ForEach-Object {
                $exe = Join-Path $_.FullName 'chrome-headless-shell-win64\chrome-headless-shell.exe'
                if (Test-Path $exe) { [pscustomobject]@{ Rev = [int]($_.Name -replace '.*-', ''); Exe = $exe } }
            } | Sort-Object Rev)
        if (-not ($shells | Where-Object Rev -eq $want)) {
            $pick = $shells | Where-Object Rev -gt $want | Select-Object -First 1
            if (-not $pick) {
                throw "Headless Chromium revision $want is not installed and no newer one is. Ask the owner, then run: npx playwright install chromium-headless-shell"
            }
            $env:CIVICCAST_PLAYWRIGHT_EXECUTABLE = $pick.Exe
            $browserNote = "fallback: pinned revision $want missing, used installed revision $($pick.Rev)"
        }
    } else { $browserNote = "CIVICCAST_PLAYWRIGHT_EXECUTABLE=$env:CIVICCAST_PLAYWRIGHT_EXECUTABLE (set by caller)" }
    Write-Output "browser: $browserNote"

    # 5. Run the real operator flows headless.
    $env:VERIFY_PORTAL_BASE_URL = $baseUrl
    $env:VERIFY_PORTAL_EVIDENCE_DIR = $evidence
    $env:CIVICCAST_E2E_PREVIEW_ORIGIN = $baseUrl
    $env:PYTHON = $python
    $cli = Join-Path $appDir 'node_modules\@playwright\test\cli.js'
    $pwArgs = @($cli, 'test', '--config', '.claude/skills/verify/playwright.verify.config.ts',
        '--timeout', "$($TestTimeoutSec * 1000)") + @($Flows | ForEach-Object { $specs[$_] })
    try {
        Invoke-Phase 'playwright' $appDir $node $pwArgs -TimeoutSec ($TestTimeoutSec * $Flows.Count + 300)
    } catch {
        Write-Output "playwright reported failures (details below)"
    } finally {
        Remove-Item Env:VERIFY_PORTAL_BASE_URL, Env:VERIFY_PORTAL_EVIDENCE_DIR, Env:CIVICCAST_E2E_PREVIEW_ORIGIN, Env:PYTHON -ErrorAction SilentlyContinue
    }
} catch {
    if ($failures.Count -eq 0) { $failures.Add($_.Exception.Message) }
} finally {
    if ($preview) {
        $stop = Stop-OwnedProcess -Record $preview
        Write-ProcLog "stop preview: $stop"
        if ($stop -notlike '*: stopped (tree*' -and $stop -notlike '*: already exited*') {
            Start-Sleep -Seconds 2
            $stop = Stop-OwnedProcess -Record $preview
            Write-ProcLog "stop preview (retry): $stop"
            if ($stop -notlike '*: stopped (tree*' -and $stop -notlike '*: already exited*') {
                $failures.Add("preview pid $($preview.Pid) was not stopped: $stop")
            }
        }
    }
    # The specs stop the API processes they spawn. Report (and stop, by PID)
    # any that outlived them: only ones started by this run from this repo.
    $leftover = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object {
        $_.CreationDate -ge $runStart -and $_.CommandLine -like "*$repo*" -and $_.CommandLine -like '*tests.integration.operator_*'
    }
    foreach ($p in @($leftover)) {
        $rec = [pscustomobject]@{ Pid = [int]$p.ProcessId; StartTime = $p.CreationDate; Marker = 'tests.integration.operator_'; MarkerMode = 'prefix' }
        $failures.Add("fixture API pid $($p.ProcessId) outlived its spec")
        Write-ProcLog ("leftover fixture: " + (Stop-OwnedProcess -Record $rec))
    }
}

# 5. Read the Playwright results and write the report.
$tests = @()
$jsonPath = Join-Path $evidence 'playwright-results.json'
if (Test-Path $jsonPath) {
    $json = Get-Content $jsonPath -Raw | ConvertFrom-Json
    function Get-Specs($suite) {
        $names = $suite.PSObject.Properties.Name
        if ('specs' -in $names) { foreach ($s in @($suite.specs)) { if ($s) { $s } } }
        if ('suites' -in $names) { foreach ($c in @($suite.suites)) { if ($c) { Get-Specs $c } } }
    }
    foreach ($suite in $json.suites) {
        foreach ($spec in Get-Specs $suite) {
            foreach ($t in $spec.tests) {
                $r = $t.results | Select-Object -Last 1
                $shots = @($r.attachments | Where-Object { $_.name -eq 'screenshot' } | ForEach-Object { $_.path })
                $trace = @($r.attachments | Where-Object { $_.name -eq 'trace' } | ForEach-Object { $_.path })
                $tests += [pscustomobject]@{
                    file = $spec.file; title = $spec.title; status = $r.status; seconds = [math]::Round($r.duration / 1000, 1)
                    screenshot = ($shots -join '; '); trace = ($trace -join '; ')
                }
            }
        }
    }
    foreach ($t in $tests) { if ($t.status -ne 'passed') { $failures.Add("$($t.file): '$($t.title)' $($t.status)") } }
    if ($tests.Count -eq 0) { $failures.Add('Playwright ran no tests') }
} elseif ($failures.Count -eq 0) {
    $failures.Add('playwright-results.json was not written')
}
if ($failures.Count -eq 0) { $result = 'PASS' }

if (-not (Get-Variable browserNote -ErrorAction SilentlyContinue)) { $browserNote = 'not reached' }
$lines = @("# Operator portal verify: $result", '', "Evidence: $evidence", "Browser: $browserNote", '', '| Phase | Seconds | Exit | Log |', '|---|---|---|---|')
$lines += $phases | ForEach-Object { "| $($_.phase) | $($_.seconds) | $($_.exit) | $($_.log) |" }
$lines += @('', '| Spec | Test | Status | Seconds | Screenshot | Trace |', '|---|---|---|---|---|---|')
$lines += $tests | ForEach-Object { "| $($_.file) | $($_.title) | $($_.status) | $($_.seconds) | $($_.screenshot) | $($_.trace) |" }
if ($failures.Count) { $lines += @('', '## Failures', '') + ($failures | ForEach-Object { "- $_" }) }
$lines += @('', "RESULT: $result")
$lines | Set-Content -Path (Join-Path $evidence 'report.md') -Encoding utf8
[pscustomobject]@{ result = $result; evidence = $evidence; browser = $browserNote; phases = $phases; tests = $tests; failures = $failures } |
    ConvertTo-Json -Depth 5 | Set-Content -Path (Join-Path $evidence 'summary.json') -Encoding utf8

$tests | ForEach-Object { Write-Output ("{0,-40} {1,-8} {2,6} s" -f $_.file, $_.status, $_.seconds) }
$failures | ForEach-Object { Write-Output "FAIL: $_" }
Write-Output "RESULT: $result"
Write-Output "EVIDENCE: $evidence"
exit ([int]($result -ne 'PASS'))
