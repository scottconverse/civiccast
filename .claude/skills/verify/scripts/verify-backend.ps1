# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
<#
.SYNOPSIS
  Starts the CivicCast backend from this checkout on a free loopback port with
  throwaway state, drives two real user flows through the HTTP API, stops the
  server by its own PID, and writes evidence + a PASS/FAIL report.

.DESCRIPTION
  Flow 1 "first-run": station-state -> prepare managed storage (SQLite +
    Alembic migrations) -> create first admin -> acknowledge recovery kit ->
    sign in with the admin password -> /api/staff/auth/me, plus the negative
    cases (no token -> 401, wrong password -> 401).
  Flow 2 "accounts-durability": admin creates a records-clerk account -> the
    clerk signs in -> clerk identity and roles -> clerk is refused an admin-only
    route (403) -> the server is stopped and started again on the same state ->
    the clerk signs in again and the account list still holds the clerk.

  All state lives in a temp folder that is deleted at the end (use -KeepState
  to keep it). Nothing touches %LOCALAPPDATA%\CivicCast, C:\ProgramData,
  port 8000 or port 5433.

.EXAMPLE
  pwsh -NoProfile -File .claude/skills/verify/scripts/verify-backend.ps1
#>
[CmdletBinding()]
param(
    [int]$Port = 0,
    [string]$EvidenceDir,
    [int]$StartupTimeoutSec = 90,
    [int]$RequestTimeoutSec = 240,
    [switch]$KeepState
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 3.0
Import-Module (Join-Path $PSScriptRoot 'VerifyCommon.psm1') -Force

$total = [System.Diagnostics.Stopwatch]::StartNew()
$repo = Get-RepoRoot -From $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw "No .venv in $repo. Prepare once: uv sync --all-extras --no-extra agenda-js-import --group dev"
}
$evidence = New-EvidenceDir -RepoRoot $repo -Suffix 'backend' -Requested $EvidenceDir
$state = Join-Path ([System.IO.Path]::GetTempPath()) ("civiccast-verify-" + [guid]::NewGuid().ToString('N').Substring(0, 12))
New-Item -ItemType Directory -Force -Path $state | Out-Null

$steps = [System.Collections.Generic.List[object]]::new()
$transcript = Join-Path $evidence 'http-transcript.log'
$secrets = [System.Collections.Generic.List[string]]::new()
$script:server = $null
$script:baseUrl = ''
$failed = $false
$failure = ''

function Write-Transcript([string]$text) {
    foreach ($s in $secrets) { if ($s) { $text = $text.Replace($s, '<redacted>') } }
    Add-Content -Path $transcript -Value $text -Encoding utf8
}

function Invoke-Step {
    param(
        [Parameter(Mandatory)][string]$Flow,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Method,
        [Parameter(Mandatory)][string]$Path,
        [object]$Body,
        [string]$Token,
        [Parameter(Mandatory)][int]$Expect,
        [scriptblock]$Check
    )
    $headers = @{ Accept = 'application/json' }
    if ($Token) { $headers['Authorization'] = "Bearer $Token" }
    $json = if ($null -ne $Body) { $Body | ConvertTo-Json -Depth 8 -Compress } else { $null }
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $params = @{ Uri = "$($script:baseUrl)$Path"; Method = $Method; Headers = $headers; SkipHttpErrorCheck = $true; TimeoutSec = $RequestTimeoutSec; UseBasicParsing = $true }
    if ($json) { $params['Body'] = $json; $params['ContentType'] = 'application/json' }
    $resp = Invoke-WebRequest @params
    $ms = $sw.ElapsedMilliseconds
    $content = [string]$resp.Content
    $data = $null
    try { $data = $content | ConvertFrom-Json -Depth 32 } catch { $data = $null }
    $ok = ($resp.StatusCode -eq $Expect)
    $note = ''
    if ($ok -and $Check) {
        try { $res = & $Check $data; if ($res -is [string]) { $note = $res } }
        catch { $ok = $false; $note = "check failed: $($_.Exception.Message)" }
    } elseif (-not $ok) {
        $note = "expected HTTP $Expect, got $($resp.StatusCode)"
    }
    $short = if ($content.Length -gt 600) { $content.Substring(0, 600) + '...' } else { $content }
    Write-Transcript ("[{0}] {1} {2} {3} -> {4} ({5} ms) {6}`n  request: {7}`n  response: {8}`n" -f `
        $Flow, $Name, $Method, $Path, $resp.StatusCode, $ms, $(if ($ok) { 'PASS' } else { 'FAIL' }), $json, $short)
    $steps.Add([pscustomobject]@{ flow = $Flow; step = $Name; method = $Method; path = $Path; status = $resp.StatusCode; expected = $Expect; ms = $ms; result = $(if ($ok) { 'PASS' } else { 'FAIL' }); note = $note })
    if (-not $ok) { throw "[$Flow] $Name failed: $note" }
    return $data
}

function Start-Backend([string]$label) {
    $p = if ($Port -gt 0) { Assert-PortFree -Port $Port; $Port } else { Get-FreeLoopbackPort }
    $script:baseUrl = "http://127.0.0.1:$p"
    $script:server = Start-HiddenProcess -FilePath $python `
        -ArgumentList @('-m', 'uvicorn', 'civiccast.app:create_app', '--factory', '--host', '127.0.0.1', '--port', "$p") `
        -WorkingDirectory $repo -StdOut (Join-Path $evidence "server-$label.out.log") -StdErr (Join-Path $evidence "server-$label.err.log") `
        -Marker "--port $p"
    Add-Content (Join-Path $evidence 'processes.log') "started $label pid=$($script:server.Pid) start=$($script:server.StartTime.ToString('o')) cmd=$($script:server.CommandLine)"
    $secs = Wait-HttpOk -Url "$($script:baseUrl)/health" -Record $script:server -TimeoutSec $StartupTimeoutSec -Accept 200, 503
    $steps.Add([pscustomobject]@{ flow = 'server'; step = "start-$label"; method = 'GET'; path = '/health'; status = 'up'; expected = 'up'; ms = [int]($secs * 1000); result = 'PASS'; note = "$($script:baseUrl) pid $($script:server.Pid)" })
}

function Stop-Backend([string]$label) {
    if (-not $script:server) { return }
    $line = Stop-OwnedProcess -Record $script:server
    Add-Content (Join-Path $evidence 'processes.log') "stop $label $line"
    $port = ([uri]$script:baseUrl).Port
    $stillListening = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
    $ok = ($line -like '*stopped*' -or $line -like '*already exited*') -and -not $stillListening
    $steps.Add([pscustomobject]@{ flow = 'server'; step = "stop-$label"; method = '-'; path = '-'; status = $(if ($ok) { 'down' } else { 'up' }); expected = 'down'; ms = 0; result = $(if ($ok) { 'PASS' } else { 'FAIL' }); note = $line })
    $script:server = $null
    if (-not $ok) { throw "Could not stop backend ($label): $line" }
}

# ---- Environment: every CivicCast state default goes under $state ----------
# Same variables as tests/support/hermetic_state.py plus the setup paths the
# operator setup-boundary e2e uses. DATABASE_URL is cleared so the app uses
# its managed SQLite store under CIVICCAST_MANAGED_STORAGE_DIR.
$envOverrides = [ordered]@{
    LOCALAPPDATA                        = Join-Path $state 'AppData\Local'
    XDG_DATA_HOME                       = Join-Path $state 'xdg\data'
    XDG_STATE_HOME                      = Join-Path $state 'xdg\state'
    XDG_CONFIG_HOME                     = Join-Path $state 'xdg\config'
    CIVICCAST_CONFIG_DIR                = Join-Path $state 'civiccast-config'
    CIVICCAST_CERT_ROOT                 = Join-Path $state 'civiccast-config\certs'
    CIVICCAST_INSTALLER_BOOTSTRAP_LOG   = Join-Path $state 'civiccast-config\runtime-host.log'
    CIVICCAST_MANAGED_STORAGE_DIR       = Join-Path $state 'managed'
    CIVICCAST_STATION_STATE_PATH        = Join-Path $state 'station-state.json'
    CIVICCAST_SUPPORT_BUNDLE_DIR        = Join-Path $state 'support-bundles'
    CIVICCAST_SUBSCRIBE_SECRETS_FILE    = Join-Path $state 'subscribe-secrets.json'
    CIVICCAST_ACTIVITYPUB_MODE          = 'disabled'
    PYTHONPATH                          = $repo
    PYTHONUNBUFFERED                    = '1'
}
$envClear = @('DATABASE_URL', 'CIVICCAST_UPLOAD_DIR', 'CIVICCAST_ALLOW_EPHEMERAL_STORES',
    'CIVICCAST_ALLOW_DETERMINISTIC_STAFF_TOKEN', 'CIVICCAST_STAFF_TOKENS', 'CIVICCAST_SUPERVISED',
    'CIVICCAST_NATIVE_STATION_ROOT')
$savedEnv = @{}
foreach ($k in @($envOverrides.Keys) + $envClear) { $savedEnv[$k] = [Environment]::GetEnvironmentVariable($k) }
foreach ($k in $envOverrides.Keys) { [Environment]::SetEnvironmentVariable($k, $envOverrides[$k]) }
foreach ($k in $envClear) { [Environment]::SetEnvironmentVariable($k, $null) }

$adminPassword = 'verify-' + [guid]::NewGuid().ToString('N')
$clerkPassword = 'verify-' + [guid]::NewGuid().ToString('N')
$secrets.Add($adminPassword); $secrets.Add($clerkPassword)
$clerkUser = 'clerk' + (Get-Random -Minimum 1000 -Maximum 9999)

try {
    Start-Backend 'first'

    # ---------------- Flow 1: first-run setup and admin sign-in ----------------
    $f = 'first-run'
    Invoke-Step $f 'health' GET '/health' -Expect 200 | Out-Null
    Invoke-Step $f 'fresh station is not set up' GET '/api/setup/station-state' -Expect 200 -Check {
        param($d) if ($d.setup_complete) { throw 'setup_complete is already true on fresh state' } } | Out-Null
    Invoke-Step $f 'prepare managed storage (SQLite + migrations)' POST '/api/setup/storage' -Body @{} -Expect 200 -Check {
        param($d) if ($d.status -ne 'ready') { throw "storage status $($d.status)" }
        if ($d.database_path -notlike "$state*") { throw "database outside throwaway state: $($d.database_path)" }
        "db=$($d.database_path)" } | Out-Null
    $first = Invoke-Step $f 'create first admin' POST '/api/setup/first-admin' -Body @{
        station_name = 'Verify Skill Station'; admin_display_name = 'Verify Admin'; admin_username = 'verifyadmin'
        admin_password = $adminPassword; recovery_kit_destination = 'verify skill throwaway run'
        sample_content_enabled = $false; initial_schedule_enabled = $false; channel_count = 1
    } -Expect 200 -Check { param($d) if ($d.status -ne 'complete') { throw "status $($d.status)" } }
    $secrets.Add([string]$first.operator_console_token)
    Invoke-Step $f 'acknowledge recovery kit' POST '/api/setup/recovery-kit/acknowledge' -Body @{ confirmed = $true } `
        -Token $first.operator_console_token -Expect 200 -Check {
        param($d) if (-not $d.setup_complete) { throw 'setup_complete still false' } } | Out-Null
    Invoke-Step $f 'wrong password is refused' POST '/api/setup/login' -Body @{ admin_username = 'verifyadmin'; admin_password = 'not-the-password' } -Expect 401 | Out-Null
    $login = Invoke-Step $f 'admin signs in with password' POST '/api/setup/login' -Body @{ admin_username = 'verifyadmin'; admin_password = $adminPassword } -Expect 200 -Check {
        param($d) if ($d.status -ne 'authenticated') { throw "status $($d.status)" } }
    $adminToken = [string]$login.operator_console_token
    $secrets.Add($adminToken)
    Invoke-Step $f 'staff route without token is refused' GET '/api/staff/auth/me' -Expect 401 | Out-Null
    Invoke-Step $f 'admin identity' GET '/api/staff/auth/me' -Token $adminToken -Expect 200 -Check {
        param($d) if (@($d.roles) -notcontains 'setup_admin') { throw "roles: $($d.roles -join ',')" }; "operator=$($d.operator_id)" } | Out-Null

    # ---------------- Flow 2: staff account lifecycle + durability -------------
    $f = 'accounts-durability'
    Invoke-Step $f 'admin creates records-clerk account' POST '/api/staff/auth/accounts' -Token $adminToken -Body @{
        username = $clerkUser; display_name = 'Verify Clerk'; password = $clerkPassword; roles = @('records_clerk')
    } -Expect 201 | Out-Null
    $clerk = Invoke-Step $f 'clerk signs in' POST '/api/staff/auth/login' -Body @{ username = $clerkUser; password = $clerkPassword } -Expect 200
    $secrets.Add([string]$clerk.access_token)
    Invoke-Step $f 'clerk identity has only records_clerk' GET '/api/staff/auth/me' -Token $clerk.access_token -Expect 200 -Check {
        param($d) if ((@($d.roles) -join ',') -ne 'records_clerk') { throw "roles: $($d.roles -join ',')" } } | Out-Null
    Invoke-Step $f 'clerk is refused account admin' GET '/api/staff/auth/accounts' -Token $clerk.access_token -Expect 403 | Out-Null

    Stop-Backend 'first'
    Start-Backend 'restart'

    Invoke-Step $f 'after restart: setup still complete' GET '/api/setup/station-state' -Expect 200 -Check {
        param($d) if (-not $d.setup_complete) { throw 'setup_complete lost after restart' } } | Out-Null
    $clerk2 = Invoke-Step $f 'after restart: clerk signs in again' POST '/api/staff/auth/login' -Body @{ username = $clerkUser; password = $clerkPassword } -Expect 200
    $secrets.Add([string]$clerk2.access_token)
    $login2 = Invoke-Step $f 'after restart: admin signs in again' POST '/api/setup/login' -Body @{ admin_username = 'verifyadmin'; admin_password = $adminPassword } -Expect 200
    $secrets.Add([string]$login2.operator_console_token)
    Invoke-Step $f 'after restart: account list still has the clerk' GET '/api/staff/auth/accounts' -Token $login2.operator_console_token -Expect 200 -Check {
        param($d) if (-not (@($d) | Where-Object { $_.username -eq $clerkUser })) { throw "clerk $clerkUser missing" } } | Out-Null
} catch {
    $failed = $true
    $failure = $_.Exception.Message
} finally {
    try { Stop-Backend 'final' } catch { $failed = $true; $failure = ($failure, $_.Exception.Message | Where-Object { $_ }) -join ' | ' }
    foreach ($k in $savedEnv.Keys) { [Environment]::SetEnvironmentVariable($k, $savedEnv[$k]) }
    if ($KeepState) {
        Add-Content (Join-Path $evidence 'processes.log') "state kept at $state"
    } else {
        Remove-Item -Recurse -Force -Path $state -ErrorAction SilentlyContinue
    }
}

# Server errors are evidence too: any Traceback in the server logs fails the run.
$tracebacks = @(Get-ChildItem $evidence -Filter 'server-*.log' | Select-String -Pattern 'Traceback \(most recent call last\)' -SimpleMatch:$false)
if ($tracebacks.Count -gt 0) {
    $failed = $true
    $failure = ($failure, "server log has $($tracebacks.Count) Python traceback(s): $($tracebacks[0].Path)" | Where-Object { $_ }) -join ' | '
}
if (@($steps | Where-Object result -eq 'FAIL').Count -gt 0) { $failed = $true }

$result = if ($failed) { 'FAIL' } else { 'PASS' }
$seconds = [math]::Round($total.Elapsed.TotalSeconds, 1)
$commit = (& git -C $repo rev-parse --short HEAD)
$dirty = [bool](& git -C $repo status --porcelain --untracked-files=no)
$summary = [ordered]@{
    skill = 'verify (backend)'; result = $result; seconds = $seconds; commit = $commit; dirty = $dirty
    evidence = $evidence; failure = $failure; steps = $steps
}
$summary | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $evidence 'summary.json') -Encoding utf8

$lines = @("# Backend verify: $result", '', "commit $commit$(if ($dirty) { ' (+ uncommitted changes)' }) - $seconds s - evidence $evidence", '')
if ($failure) { $lines += "Failure: $failure"; $lines += '' }
$lines += '| Flow | Step | Call | HTTP | Expected | ms | Result |'
$lines += '|---|---|---|---|---|---|---|'
foreach ($s in $steps) { $lines += "| $($s.flow) | $($s.step) | $($s.method) $($s.path) | $($s.status) | $($s.expected) | $($s.ms) | $($s.result) |" }
$lines | Set-Content (Join-Path $evidence 'report.md') -Encoding utf8
$lines | ForEach-Object { Write-Output $_ }
Write-Output ''
Write-Output "RESULT: $result"
Write-Output "EVIDENCE: $evidence"
exit $(if ($failed) { 1 } else { 0 })
