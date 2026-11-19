# PREFLIGHT — In-Sandbox-Report.ps1

Static, no-launch verification pass (2026-08-19) after: T4 port-interpolation fix,
captions evidence-capture rewrite, and gating SKIP-REINSTALL mode behind an explicit
opt-in. No sandbox was launched to produce this document — everything below is either
a parse check, a host-side demonstration of pure string/JSON construction, a read of
already-collected evidence from the two prior sandbox runs, or a line-by-line reading
of the script against documented PowerShell 5.1 semantics.

## (a) Parse result

```
[System.Management.Automation.Language.Parser]::ParseFile(
  'sandbox-lab/scripts/In-Sandbox-Report.ps1', ...)
```

**PARSE CLEAN: 0 errors.** Re-run after every edit in this pass; last run immediately
before writing this document.

Also re-verified: zero occurrences of PowerShell-7-only syntax anywhere in the file —
no `??` (null-coalescing), no bare `$var?` (the exact shape of the T4 port bug), no
`?:` ternary. Grep patterns used: `\$[A-Za-z_][A-Za-z0-9_]*\?` and `??` over the whole
file; the only remaining `?` hit is inside a comment quoting the OLD buggy string as
history, not live code.

## (b) T4 sink URI — sample construction, proven sane

Ran the exact fixed line from the script (`$engineUri = "udp://127.0.0.1:${enginePort}"`)
with the same sample port the script uses (19003), then round-tripped it through the
same `ConvertTo-Json` call the real `EgressConfig` PUT body goes through:

```
engineUri=udp://127.0.0.1:19003
type=String length=21
{
  "kind": "udp-ts",
  "label": "sandbox-proof-engine",
  "uri": "udp://127.0.0.1:19003",
  "latency_ms": 2000,
  "loudness_regime": "inherit",
  "eas_tone_strip_enabled": true
}
```

Confirms: no `?pkt_size` query string (the `EgressConfig` sink schema takes a plain
`udp://host:port` URI — packetization/latency are the separate `latency_ms` field,
already present), the port digits are present and correctly interpolated (the original
bug produced `udp://127.0.0.1:=1316` — port missing entirely), and the value survives
JSON serialization unchanged.

Grepped the whole file for the bug's exact shape (`\$[A-Za-z_][A-Za-z0-9_]*\?`) — the
only historical occurrence was the one fixed line; the fallback block's
`"udp://127.0.0.1:$($e.p)?pkt_size=1316"` (raw ffmpeg's own UDP muxer option, a
different target with a real use for that query string) already used safe
subexpression syntax `$($e.p)` and needed no change.

## (c) Captions block — PS 5.1 semantics walk-through

Full block: `In-Sandbox-Report.ps1` lines ~3438–4109 (Step 8, offline-caption
discovery/poll/manual-enqueue/verdict). Walked every statement against Windows
PowerShell 5.1 (not pwsh 7) semantics:

| Area | What it does | Risk found |
|---|---|---|
| `ConvertFrom-Json` (inside `Invoke-CivicCastApi`, used for every `$spec`/`$cj`/`$enq`) | No `-Depth` parameter passed | **None** — Windows PowerShell 5.1's `ConvertFrom-Json` has no `-Depth` parameter at all (added in PS7); the script never passes one anywhere. Not a PS7-ism, nothing to fix. |
| Same, on the ~920 KB `/openapi.json` body | Parses a large, moderately nested JSON document | **PROVEN, not just theoretical** — this exact code path (`Invoke-CivicCastApi` extracted verbatim from this script) was run against the real, live, 920 KB `openapi.json` on the host during this session's discovery work and succeeded (22 egress paths correctly enumerated). |
| `$capEndpointNode.Value.PSObject.Properties.Name -contains 'post'` | Checks whether a discovered OpenAPI path node declares a `post` method | **NEW** (this exact sub-pattern, in a captions context, has not run in-sandbox) but structurally identical to the `.PSObject.Properties.Name -contains 'jobs'` idiom already used two lines above it and proven working against live `openapi.json` this session. Low risk. |
| `$jobs.PSObject -and (...)` | Guards before checking for a `.jobs` wrapper property | Minor imprecision, not a bug: `.PSObject` is non-null for any object PowerShell holds (even primitives), so this condition is always true in practice — it doesn't do anything the following `-contains` check doesn't already do safely on its own. Cosmetic only; left as-is (harmless, already reads clearly). |
| `$jobsForAsset = @($jobs) \| Where-Object {...}` | Wraps the possibly-single-object JSON result in `@()` before filtering | **Correct, deliberate defense** against the classic PowerShell single-element-JSON-array pitfall (`ConvertFrom-Json` returns a bare scalar, not a 1-element array, when the source JSON array has exactly one item — `.Count` on a bare scalar is `$null`, not 1). The `@(...)` wrap makes `.Count` reliable regardless of 0/1/N elements. |
| `$j.$f` (`$f` holding a property-name string) | Dynamic property lookup by variable | Valid, well-supported PS 5.1 syntax (`$obj.$propertyNameVariable`); not a PS7-ism. |
| `$cueCount -and [int]$cueCount -gt 0` | Truthiness + numeric check | Verified operator precedence: `-gt` binds tighter than `-and`, so this reads as `$cueCount -and ([int]$cueCount -gt 0)` — the intended grouping. One PowerShell quirk worth flagging (not a bug here): a non-empty **string** `"0"` is truthy in PowerShell (only `""` is falsy), so if an API ever returned `cue_count` as the string `"0"` the left operand would be `$true` — but the right operand `[int]"0" -gt 0` is still correctly `$false`, so the overall result is unaffected. No fix needed; documented so a future reader doesn't "fix" it into a bug. |
| `$null -eq 404`, `$null -ge 200`, `$cj.body_raw -ne $lastCapBodyRaw` when both are `$null` | Comparisons against a possibly-null `.status`/`.body_raw` (e.g. after a connection exception with no HTTP response) | Verified safe: PowerShell's comparison operators against `$null` behave predictably here (`$null -eq 404` → `$false`, `$null -ge 200` → `$false`, `$null -ne $null` → `$false`) and never throw. Matches the pattern already exercised successfully in this session's T2 negative-control dry run. |
| `[ordered]@{ asset_id = $loopAssetId }` and other `[ordered]` hashtable literals | Ordered-dict construction for JSON bodies | PS 5.1 has supported `[ordered]` on hashtable literals since PS3.0; `ConvertTo-Json` preserves the order. No pitfall. |
| Nested string interpolation `"...body=$(($enqueueBody \| ConvertTo-Json -Compress))"` | Subexpression inside a double-quoted string | Standard, already used elsewhere in this file (e.g. the `$(if(...){'PASS'}else{'FAIL'})` idiom at multiple `*_RESULT=` lines) — safe, consistent with house style. |
| The 5-way `if / elseif / elseif / elseif / elseif / else` verdict cascade | Produces exactly one of `CAPTIONS=PASS / FAIL_NO_ENQUEUE_ROUTE / FAIL_ENQUEUED_NO_COMPLETE / FAIL_NEVER_ENQUEUED (...) / FAIL_NO_ENQUEUE_ROUTE` | Pure boolean control flow, no PS7-isms. Every branch writes to both `T3-CAPTIONS.txt` and `T3-LOOP.txt` before falling through to `Save-Summary`, so a mid-loop crash still leaves partial evidence on disk (consistent with the rest of the file's incremental-write philosophy). |

**Conclusion for (c): no PS7-isms, no ConvertFrom-Json depth issue (the cmdlet doesn't
have that parameter in 5.1, and nothing in the script assumes it does), no ordered-dict
pitfall. The one real "gotcha" class (single-element JSON array unwrapping) is already
defended against correctly.** The only residual risk is *semantic*, not syntactic: the
manual-enqueue body shape (`{"asset_id": ...}`) and the discovered GET/POST route
shapes are assumptions about the K3 build's actual API, unverified because that build
was never reachable for a live dry run (see NEW items below) — but every one of those
calls already goes through `Invoke-CivicCastApi`, which never throws and always
degrades to a named `CAPTIONS=FAIL_*` bucket instead of crashing the run.

## (d) Assumption inventory — PROVEN vs NEW

**PROVEN** (held in the two prior sandbox runs — the original full end-to-end run,
archived at `output\prev-run1\`, and the coordinator's own status report from the run
immediately before this session's fixes):

- The installer runs successfully via `/S /D=C:\CivicCastHostStore\install` end to end
  (`install-progress.log`: 22:56:18 → 23:22:25, exit path SUCCESS, `InstalledVersion`
  recorded, `QuietUninstallString`/`InstallLocation` written).
- After a full fresh install, the CivicCastSupervisor service registers, starts, and
  the station answers on `127.0.0.1:8000` (health/operator/portal all 200).
- **T2_RENDER=PASS for both UIs** — confirmed in-sandbox by the coordinator directly
  ("Your harness ran end-to-end in the sandbox... T2_RENDER=PASS (both UIs, great)").
  Also independently re-verified this session on the host against the same served SPA
  shells (headless Edge + `--virtual-time-budget`, ratios 20.2x / 6.7x over raw).
- **T3 REAL LOOP (nonce → first-admin → token → generate MP4 → upload → package →
  approve/publish → public-listing assert) — PASS**, confirmed in-sandbox by the
  coordinator. This proves `Invoke-CivicCastApi`, the hand-rolled
  `System.Net.Http.MultipartFormDataContent` upload, and the whole authenticated
  HTTP-call machinery all work for real, mutating, sandboxed calls — not just reads.
- The captions poll loop itself is stable and bounded in-sandbox: the prior (pre-fix)
  version ran the full up-to-20-minute window without crashing, landing on
  `PARTIAL(captions)` — proves the loop/timeout/`Start-Sleep` structure is sound; this
  session's fix only changes what evidence is captured along the way, not the loop
  shape.
- T4's synthetic-ffmpeg fallback path (start 3 libopenh264 encoders, invoke the kit's
  `verify-egress.ps1`, parse `egress-verify.json`) — PASS in-sandbox before this
  session's changes, and the underlying mechanics are untouched by the port fix (only
  the NEW product-engine attempt sits in front of it, itself wrapped in try/catch).
- `ffmpeg_present=True tsp_present=True` — confirmed directly from the (failed)
  skip-mode run's own `T3T5-RESULT.txt`, so path resolution to the bundled ffmpeg/tsp
  binaries works regardless of which code path set `$installDir`.
- `openapi.json` is servable and parses correctly at its real ~920 KB size via
  `Invoke-WebRequest` + `ConvertFrom-Json` (host dry-run this session, same code).

**NEW** (never exercised end-to-end against a live station; each already has a
defensive fallback or bounded failure path, noted below):

- **T4 product-engine calls** (`PUT .../egress/channels/{id}/config`,
  `POST .../commands`, `GET .../state`) — never run against a live sandboxed station
  with a real bearer token; only the *discovery* GET of `/openapi.json` was dry-run
  (on the host, read-only). *Fallback:* the whole attempt is inside one `try/catch`;
  any non-2xx response or exception sets `$t4EngineBlockReason` and falls straight
  into the PROVEN ffmpeg fallback, labeled `PASS_FFMPEG_FALLBACK` (never plain
  `PASS`) — no crash path, no silent substitution.
- **Manual caption enqueue POST** — body shape (`{"asset_id": ...}`) and the
  discovered route are assumptions about the K3 build (07caa156f), which was never
  reachable for a live call (only the older beta.1 host build was reachable, and it
  doesn't even have this endpoint). *Fallback:* routed through `Invoke-CivicCastApi`,
  which never throws; a wrong body shape or missing route lands cleanly in
  `CAPTIONS=FAIL_NO_ENQUEUE_ROUTE` rather than crashing the run.
- **Caption GET route on the K3 build specifically** — confirmed ABSENT on the older
  beta.1 build reachable this session; presence/shape on 07caa156f is unverified.
  *Fallback:* dynamic discovery via `openapi.json` with a documented-default fallback
  path, plus explicit 404 handling that ends the poll loop cleanly and reports
  `CAPTIONS=FAIL_NO_ENQUEUE_ROUTE` rather than hanging or throwing.
- **SKIP-REINSTALL mode (gated, off by default)** — excluded from this "full-mode"
  risk assessment entirely: it only runs when `C:\CivicCastOutput\SKIP_MODE.txt` is
  deliberately created AND `station-set.json` is present. Root-caused this session
  (static discovery only, no further launches): `HKLM\SOFTWARE\CivicCast\Native\
  DatabaseUrl`/`SetupNonce`, and — more fundamentally — the PostgreSQL/NATS data
  directories themselves, all live under `%ProgramData%\CivicCast` and the registry,
  neither of which is a Sandbox `MappedFolder`; both reset every session. Only
  `C:\CivicCastHostStore\install` persists. So `station-set.json` being present
  proves activation/model-staging finished once — it does **not** prove the database
  is recoverable this session. Restoring it for real would require the installer's
  own provisioning step, which needs an installer-internal Ed25519 pack public key
  (`--pack-public-key-base64`) that has no discoverable copy anywhere in the deployed
  Python tree or the accessible `civiccast` source checkout (confirmed by reading
  `civiccast.native.provision.__main__.decode_pack_public_key` and its only caller —
  the key is generated/embedded on the Rust/build side, never shipped to the deployed
  Python runtime). Given that, the gated branch now does only one experimental,
  strictly informational, non-blocking thing: attempt to re-register the
  `CivicCastSupervisor` Windows service object itself (confirmed via
  `supervisor\service_host.py`'s module-level `win32serviceutil.HandleCommandLine` —
  the same mechanism `pythonservice.exe` uses) via
  `<installDir>\runtime\python.exe -m civiccast.native.supervisor.service_host
  install`. This is wrapped in its own `try/catch`, writes its result to
  `SKIP-MODE-SERVICE-REREGISTER.txt`, and — whatever it does — the existing,
  already-proven, unconditional `Get-Service`/bounded `Start-Service` logic
  immediately below it runs exactly as before. The service is still expected to fail
  to start in this state (`ensure_database_url_env()` will raise
  `DatabaseUrlUnavailableError` for lack of a registry value), which is fine: that is
  `station_up=False`, the SAME fail-honest signal as before, not a hang or a crash.

## Gated-skip-mode diff summary

- New gate: `$skipReinstall = $skipModeOptedIn -and $stationSetPresent`, where
  `$skipModeOptedIn = Test-Path (Join-Path $OutDir 'SKIP_MODE.txt')`. Previously it was
  `station-set.json` presence alone — an ordinary run with a stale leftover
  `station-set.json` could have silently short-circuited into skip mode. Now it can't:
  skip mode requires an operator to deliberately drop `SKIP_MODE.txt` into
  `C:\CivicCastOutput` before launch.
- `INSTALL-RESULT.txt` now always states which of the four combinations
  (opted-in × station-set-present) applied, including the two "opted in/out but the
  other condition wasn't met" cases, so a run's log is self-explanatory without cross
  referencing `SKIP_MODE.txt`'s existence separately.
- Inside the gated branch only: added the experimental, non-blocking service
  re-registration attempt described above (`SKIP-MODE-SERVICE-REREGISTER.txt` +
  `.stdout.log`/`.stderr.log`), explicitly labeled experimental in its own comments and
  in `$summary.installer_source`/`$summary.silent_flag_used`
  (`SKIPPED_PERSISTENT_EXPERIMENTAL`).
- Everything else inside the gated branch (direct `$installDir` assignment instead of
  ARP/Program-Files scan, and the T3-loop's saved-credential `/api/setup/login` path)
  is unchanged from the prior session's implementation — still present, still only
  reachable when both gate conditions hold.
- The default (non-opted-in) path is byte-for-byte the original, PROVEN full-install
  flow: run the installer, run activation-rerun-if-needed, locate `$installDir` via
  ARP/Program-Files scan, run first-admin via the HKLM nonce. Nothing in this path
  changed this session except the two items explicitly requested as staying in
  (captions evidence capture, T4 port fix), which apply identically in both modes.

---

# PREFLIGHT ADDENDUM — mapped-folder stall fix (2026-08-24)

Second static, no-launch verification pass, for the
`<gate-a-mapped-folder-stalls>` change (local `$OutDir` + shipper process,
staleness-watchdog arming fix, `step_seq`, host quiet-share fallback, budget
`100 -> 150` / `120 -> 170`). No Windows Sandbox was launched to produce this
addendum. The section above remains the 2026-08-19 record and is not restated.

## (a) Parse result — the file AND its three embedded sub-scripts

`In-Sandbox-Report.ps1` embeds the watchdog, the shipper supervisor, and the
shipper tick as single-quoted here-strings. An outer parse says nothing about
them: to the outer parser they are opaque string literals. All four were
parsed separately with
`[System.Management.Automation.Language.Parser]::ParseInput` under **Windows
PowerShell 5.1** (`5.1.26100.x`), alongside every other `.ps1` under
`sandbox-lab/`:

```
PARSE OK  : sandbox-lab\Host-Launch-Sandbox-Test.ps1
PARSE OK  : sandbox-lab\Run-GateA.ps1
PARSE OK  : sandbox-lab\runner\Install-GateARunner.ps1
PARSE OK  : sandbox-lab\scripts\In-Sandbox-Report.ps1
PARSE OK  : sandbox-lab\scripts\Watch-Run.ps1
PARSE OK  : sandbox-lab\soak-4h\scripts\heartbeat.ps1
PARSE OK  : sandbox-lab\soak-4h\scripts\start-encoders.ps1
PARSE OK  : sandbox-lab\soak-4h\scripts\verify-egress.ps1
PARSE OK  : In-Sandbox-Report.ps1 embedded here-string #1  (shipper tick)
PARSE OK  : In-Sandbox-Report.ps1 embedded here-string #2  (shipper supervisor)
PARSE OK  : In-Sandbox-Report.ps1 embedded here-string #3  (watchdog)
ALL CLEAN (PS 5.1 AST parse + PS7-ism scan)
```

`Watch-Run.ps1` did NOT parse before this pass, and had not since it was
added: a single U+2014 em dash in a double-quoted string decodes under 5.1's
default ANSI codepage as `a<euro>"`, whose embedded double quote terminates
the string early -- five cascading errors from one character. Replaced with
`--`. Nothing else in that file changed; it remains the legacy interactive
monitor Gate A never invokes.

PS7-ism scan over the same set (`??`, `?.`, `&&`, `||`,
`ForEach-Object -Parallel`, `ConvertFrom-Json -AsHashtable`, `Get-Error`,
`Test-Json`, `Join-String`): zero hits.

## (b) Runtime demonstration of the two new mechanisms

Not a parse check and not a sandbox run: the three sub-scripts were extracted
from the here-strings and exercised against ordinary local directories on the
host, which is sufficient because neither mechanism depends on Windows
Sandbox to be exercised -- only to be stressed.

Shipper (interval 3s, two temp dirs standing in for local/mapped):

- evidence file and a nested `station-diag\after-t3t5\` subtree both mirrored;
- `_SHIPPER-HEARTBEAT.txt` present and its timestamp advancing tick over tick
  (00:00:12 -> 00:00:18) with no other file changing -- this is exactly the
  liveness the host's quiet-share detector reads during the T5 soak;
- a host-owned `.gitkeep` placed only on the destination side survived, which
  is the property `/MIR` would have destroyed;
- `WATCHDOG-TIMEOUT.txt` removed on the source side was retracted from the
  destination on the next tick (the explicit retraction list);
- `DONE.json` reached the destination and the supervisor then exited on its
  own rather than being killed.

Watchdog arming, replaying run6's actual observed state (`summary.json` stuck
at `station-diag-captured-after-t3t5`, `step_seq` frozen):

- OLD predicate on that step name: **False** -- it never armed, which is why
  only the coarse whole-script bound fired, 47 minutes late;
- NEW watchdog (`-StallMinutes 1` for the demonstration): `STALL-TIMEOUT.txt`
  written at 60.0s stalled, `stuck_progress=seq:41`, plus a placeholder
  `DONE.json` carrying `stall_timeout: true` and `harness_completed: false`
  -- the shape `scripts/gate_a_verdict.py` fails closed on;
- no false positive: with the step NAME held constant at `t5-beat-1` and only
  `step_seq` advancing every 20s for 100s, no `STALL-TIMEOUT.txt` was
  written. The old name-equality test would have called that a stall.

## (c) What this addendum does not cover

The quiet-share detector in `Host-Launch-Sandbox-Test.ps1` was verified by
parse and by static contract test (`tests/gate_a/test_gate_a_harness_contract.py`),
not by execution -- running it launches Windows Sandbox, which this pass
deliberately does not do. No end-to-end Gate A run has been performed against
this change; the next real candidate run is its first execution.
