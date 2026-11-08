# Gate A — Automated Station-Acceptance Release Gate

## Why this exists

This project's historical failure mode is builder-authored "it works" claims
outrunning reality (see `CLAUDE.md`'s Mandatory CivicCast Cross-Agent Audit
Protocol). Gate A replaces prose with a machine verdict: a clean Windows
Sandbox install of the real candidate kit, K1 activation, both UIs rendered,
the clerk loop (upload → publish → captions) exercised, the product egress
engine verified with TSDuck, and a bounded soak — judged by code, fail-closed,
never from prose.

It is built from a standalone harness (`Host-Launch-Sandbox-Test.ps1` +
`In-Sandbox-Report.ps1`) that was proven manually against real candidate
builds before this repository absorbed it. See "Provenance" below.

## What Gate A proves, and what it does not

**Proves** (the checks in `scripts/gate_a_verdict.py`, each fail-closed):

| Check | What it proves | Evidence file(s) |
|---|---|---|
| `install` | The signed installer runs silently to exit 0 and station-set.json exists after install | `summary.json` |
| `activation` | The K1 mandatory activation hook ran and staged the station | `ACTIVATION-RESULT.txt`, `summary.json` |
| `runtime` | The station answers `/api/health`, the operator console, and the resident portal, all HTTP 200 | `summary.json.runtime_checks` |
| `t2_render` | Both UIs actually render their SPA shell (not just serve a stub document — checked via a headless-Edge DOM dump ratio) | `T2-RENDER-RESULT.txt` |
| `t3_loop` | The clerk loop completes: nonce → first-admin → token → upload → package → approve/publish → public listing | `T3T5-RESULT.txt`, `T3-LOOP.txt` |
| `captions` | The offline caption pipeline produced real cues from a real speech clip (not a synthetic sine tone) | `T3-LOOP.txt`, `T3-caption-artifact.json` |
| `t4_engine` | The product egress engine (GStreamer) started and passed TSDuck transport-stream verification — `PASS_FFMPEG_FALLBACK` is a FAIL, see below | `T3T5-RESULT.txt` |
| `t5_soak` | The bounded soak stayed healthy for its whole window (`unhealthy=0`) | `T3T5-RESULT.txt` |
| `completion` | The harness itself reached its own authoritative completion signal (`DONE.json.harness_completed == true`, no `WATCHDOG-TIMEOUT.txt`/`STALL-TIMEOUT.txt`/`HOST-QUIET-SHARE.txt`) | `DONE.json`, `summary.json` |

**Two verdicts, and two non-verdicts.** `gate-a-verdict.json` carries `PASS`
or `FAIL` when the gate actually observed the candidate, and one of two other
values when it did not:

| Value | Meaning | Marker | Checks in the document |
|---|---|---|---|
| `PASS` / `FAIL` | A real station-acceptance finding | — | All computed, and they decide the verdict |
| `BUSY` | The run never started — Windows Sandbox was occupied by another process on this shared box (see "Shared Windows Sandbox: the busy guard") | `SANDBOX-BUSY.txt` | Empty: no evidence was ever produced |
| `HARNESS_ERROR` | The run started, then lost its evidence channel (see "Mapped-folder stalls") | `HOST-QUIET-SHARE.txt` | All computed and recorded as forensics, but they do not decide the verdict |

Neither non-verdict is reported as a FAIL. Calling a broken harness a product
failure is the same authored-truth mistake Gate A exists to eliminate,
pointed the other way. Exit codes: `0` PASS, `1` FAIL, `2` for `BUSY`,
`HARNESS_ERROR`, or a missing output directory — all of which mean "no
observation", never "bad candidate".

Each row cites the 3.0 MASTER spec's station-acceptance gate
(`docs/spec/3.0/civiccast-3.0-station-in-a-box-MASTER.md` §12, "Station
acceptance"): *"operator installs+commissions without terminal work ... runs
the three PEG channels ... publishes VOD from a recorded meeting..."* — Gate A
exercises the subset of that ladder a disposable, offline, no-SDI Windows
Sandbox VM can prove in under two hours, not the whole ladder.

**Does NOT prove** — these remain out of scope by design, not by oversight:

- The 24h/72h real-hardware unattended soaks with kill+restart+reboot (§12,
  §5 unified proof/certification ladder). That is **Gate B**, a separate,
  real-hardware job — not built here.
- Physical SDI proof (rung 3 of the proof ladder). Windows Sandbox has no
  hardware pass-through.
- The commissioning-wizard UI walkthrough, force matrix, schedule commit,
  OTT-app presence, and support-bundle export — the existing
  Playwright/manual acceptance work covers these; Gate A does not replace
  them.
- Networking is disabled inside the Sandbox VM (see `CivicCastSandboxTest.wsb.template`),
  so YouTube Live / Internet Archive / real syndication targets are never
  exercised — only the portal-tier clerk loop and local egress.

### `t4_engine` policy: `PASS_FFMPEG_FALLBACK` is a FAIL

`In-Sandbox-Report.ps1`'s T4 step first attempts the real product egress
engine (GStreamer, driven through `/api/staff/egress/channels/.../commands`);
if that path is blocked for any reason it falls back to spawning synthetic
ffmpeg encoders and re-using the soak kit's `verify-egress.ps1` TSDuck check,
recording `T4_RESULT=PASS_FFMPEG_FALLBACK`. That fallback predates
CHANGELOG's "Egress default engine flipped to GStreamer (S15)". Now that
GStreamer is the shipped default engine, a candidate that only proves the
fallback path has not proven what a real station actually runs — so Gate A's
`t4_engine` check treats `PASS_FFMPEG_FALLBACK` as a named FAIL, not a
degraded pass, and only accepts `T4_RESULT=PASS_PRODUCT_ENGINE`.

### Known limitation: `Test-TsProof` null-pipeline bug let a false T4 PASS through (fixed in #145)

**GStreamer engine egress is NOT yet proven in Gate A.** A PowerShell
null-pipeline bug in `sandbox-lab/scripts/In-Sandbox-Report.ps1`'s
`Test-TsProof` graded the beta.3 Gate A run's `t4_engine` check as PASS
(`T4_RESULT=PASS_PRODUCT_ENGINE`) from a TSDuck capture that actually timed
out at 0 bytes on `udp/19003` -- an empty capture read as passing rather
than as the absence of evidence it was. The bug is fixed in #145. Once
fixed, re-running the same check against both the beta.3 and the beta.4
kits found the product engine's own state at `engine_state=FALLBACK_SLATE`
for the `government` channel, with no diagnostic trail explaining why: the
control-plane child process (the `uvicorn civiccast.app:create_app`
process the supervisor spawns) had no configured log handler above
WARNING, so the egress daemon's own INFO-level state-transition and
`last_error` records were silently dropped rather than written anywhere.
Both halves are now fixed: the control-plane child gets the same
package-level INFO file logging the supervisor host process already had
(`civiccast.native.supervisor.service.configure_control_plane_logging`,
`%ProgramData%\CivicCast\logs\control_plane-app.log`), and T4's probe now
polls `/state` and `/health` (writing every tick, the full final state and
health bodies, and `last_error` to `T4-ENGINE-NOTES.txt`) instead of a
fixed sleep before a single unlabelled `state` read. **The ffmpeg fallback
path (`T4_RESULT=PASS_FFMPEG_FALLBACK`) is proven** -- it is graded a named
FAIL by policy (not "unproven"; see above) precisely because it is not the
shipped default engine, not because it doesn't work. Until a Gate A run
records a genuine `T4_RESULT=PASS_PRODUCT_ENGINE` under the fixed grader,
no release note, README claim, or verification doc should say the
GStreamer engine's egress was proven by Gate A for beta.3 or beta.4.

## Shared Windows Sandbox: the busy guard

Windows Sandbox is **single-instance per machine** — only one VM can run at a
time, system-wide, regardless of who launches it. The Gate A runner box is
**shared**: an independent, unrelated build system (not part of this
project) also launches Windows Sandbox on the same machine at unpredictable
times. Gate A's harness must never launch into that other party's sandbox,
wait on it ambiguously, or kill it — and the other party is symmetrically
unaware of Gate A. Neither side can coordinate with the other, so the guard
is entirely observational: check for a running sandbox before launching, and
never touch a sandbox process this run didn't itself start.

**`Host-Launch-Sandbox-Test.ps1` — pre-launch guard.** Immediately after
stamping a clean `output\` (step 1) and before rendering/launching the `.wsb`
(step 2), it checks `Get-Process` for any of `WindowsSandboxClient`,
`WindowsSandboxRemoteSession`, `WindowsSandboxServer`, `vmmemWindowsSandbox`.
If any are running:

- It does **not** launch. It polls every 30s, for up to `-SandboxWaitMinutes`
  (default `90`), logging one line every ~2 minutes to
  `output\SANDBOX-WAIT.txt` (timestamp + the observed process names/PIDs).
- If the sandbox frees up within the window, it proceeds to launch normally
  (with one more log line noting when it became free).
- If it is **still busy at the deadline**, it writes `output\SANDBOX-BUSY.txt`
  (same detail line as the final `SANDBOX-WAIT.txt` entry) and exits with
  code **`3`** — a distinct, harness-specific code, never touching the
  foreign sandbox.

**`Host-Launch-Sandbox-Test.ps1` — teardown guard.** The script records the
PIDs of its own sandbox processes right after its own `Start-Process` call
(the busy guard above already proved none were running immediately before
that point, and the single-instance property means every sandbox process
that exists from then on belongs to this run). At teardown it stops **only**
those recorded PIDs — never a blanket `Stop-Process` by name. If the
recorded PID list is empty, or none of those PIDs are still alive under a
sandbox process name, it logs that and leaves everything alive rather than
guessing (see the "NEVER kill by image name" lesson this guard was written
to avoid repeating).

**`Run-GateA.ps1`.** Accepts and passes through `-SandboxWaitMinutes`
(default `90`) to `Host-Launch-Sandbox-Test.ps1`. A launcher exit code of
`3` is treated as a **harness-busy** outcome, not a station-acceptance
FAIL: it writes a `gate-a-verdict.json` with `"verdict": "BUSY"` and
`"reason": "sandbox-busy-other-user"` (empty `checks`, since no evidence was
ever produced) into the evidence directory alongside whatever
`SANDBOX-WAIT.txt`/`SANDBOX-BUSY.txt` output exists, and exits **`2`**
(harness error) — never `1` (product FAIL).

**`scripts/gate_a_verdict.py`.** If `SANDBOX-BUSY.txt` is present in the
evidence directory being judged, the judge short-circuits to the same
`"verdict": "BUSY"` document *before* running any of the required checks,
rather than letting every check fail closed on evidence files that were
never going to exist (no sandbox ever launched). `main()` returns exit code
`2` for a `BUSY` verdict, matching the "harness error, not a product
finding" contract already used for a missing output directory. This applies
whether the judge is invoked by `Run-GateA.ps1` or run standalone against a
busy evidence directory.

**`gate-a-station-acceptance.yml`.** The `Fail the job on a non-PASS
verdict or harness error` step reads `gate-a-verdict.json`'s `verdict`
field when the run didn't PASS. A `BUSY` verdict gets a distinctly worded,
clearly-re-runnable failure message (Windows Sandbox was occupied by the
other party for the whole wait window — not a product regression) instead
of the generic non-PASS message. There is no auto-retry beyond the
launcher's own `-SandboxWaitMinutes` window; re-running the workflow is a
human/on-call action. The job's `concurrency: gate-a` group (unchanged by
this guard) only ever serializes **our own** Gate A runs against each
other — the other party's sandbox usage is invisible to GitHub Actions and
cannot be coordinated through workflow concurrency.

### Orphan detection: telling a live sandbox from an orphaned server process

**What happened.** Run `32930110802` (2026-08-26) declared `BUSY` and burned
its entire 90-minute `-SandboxWaitMinutes` window on the strength of a
single process: `WindowsSandboxServer`, PID `17548`, 81 MB working set,
started hours earlier during a prior run's slow teardown (see "Teardown
drain" below — PR #48's drain reduces how often a run leaves this kind of
leftover behind, but cannot make it impossible; the drain is itself
best-effort and bounded). There was **no** `vmmemWindowsSandbox` process
(the actual VM — multi-GB working set when a session is real), **no**
`WindowsSandboxClient` window, and `wsb list` reported zero sessions for the
entire wait. The pre-2026-08-26 guard checked only `$SandboxProcessNames`
membership — any of `WindowsSandboxClient`, `WindowsSandboxRemoteSession`,
`WindowsSandboxServer`, `vmmemWindowsSandbox` — so it could not tell an
orphaned leftover shell from another party's real, in-progress session and
had no way to do anything but wait out the full window on dead weight.

**The fix (`Host-Launch-Sandbox-Test.ps1`'s pre-launch guard, `-OrphanGraceMinutes`,
default `10`).** The guard is now evidence-based, not name-based.
`Get-SandboxBusyEvidence` splits every observed `$SandboxProcessNames`
process into `vmmemWindowsSandbox` (`Vmmem`) versus everything else
(`Other` — the server/client/remote-session shells), and
`Format-SandboxProcessEvidence` records pid, process name, working-set
size, and start time for each. Every poll of the wait loop re-reads this
evidence and classifies it:

- **`vmmemWindowsSandbox` present, at any point** → genuinely busy. Behavior
  is unchanged from before this hardening: keep polling up to
  `-SandboxWaitMinutes`, write `SANDBOX-BUSY.txt` and exit `3` if still busy
  at the deadline.
- **Only `Other` processes present, with no `vmmemWindowsSandbox`, for
  `-OrphanGraceMinutes` minutes** (default `10` — a real launch spawns
  `vmmemWindowsSandbox` within seconds to a couple of minutes of the server
  process, so this many minutes of continuous absence is not something a
  live launch produces) → classified **ORPHAN**. The guard writes
  `output\SANDBOX-ORPHAN.txt` with the full evidence (pids, names,
  working-set sizes, start times, how long it was orphaned, the grace
  threshold) and **proceeds to launch** — it does not wait out the rest of
  `-SandboxWaitMinutes`, and it does not write `SANDBOX-BUSY.txt`.

  The orphan clock is seeded from the **oldest `Other` process's own
  `StartTime`**, not from "now" — run `32930110802`'s orphan was already
  hours old by the time the guard first looked at it, and seeding from
  `StartTime` lets an already-stale process classify on the very first
  evidence read instead of forcing every future run to sit through a fresh
  10-minute wait on top of a process that was never going anywhere. The
  clock resets to unset the instant `vmmemWindowsSandbox` is observed, so a
  genuine in-flight launch — the server process becomes visible a moment
  before its own VM spawns — is never misclassified as an orphan.

- **Nothing present** → unchanged: proceed to launch immediately.

**Proceed-not-kill, always.** A stale leftover server process does not hold
the machine-wide single-instance Windows Sandbox slot the way a real VM
does, so it does not need to be removed to unblock a new launch — the guard
only ever *waits less*, never touches the process. The busy/orphan guard
contains **no `Stop-Process` call** against anything it merely observes,
orphan or not; the only `Stop-Process` in this script is step 5's teardown,
far below, and that is scoped exclusively to the PIDs *this run itself*
recorded immediately after its own `Start-Process` call (see the
2026-08-24 hardening above and the "NEVER kill by image name" lesson it was
written not to repeat). If an orphan classification turns out to be wrong —
some evidence this guard cannot see means the slot really is taken — the
subsequent launch simply fails the existing "no Windows Sandbox VM process
found running a few seconds after launch" check (exit `1`) and reports that
honestly; nothing about the orphan path overrides or short-circuits it.

**`Run-GateA.ps1`.** Accepts and passes `-OrphanGraceMinutes` (default `10`)
straight through to `Host-Launch-Sandbox-Test.ps1`, the same way it already
threads `-SandboxWaitMinutes` and the teardown-drain settings.

**`scripts/gate_a_verdict.py`.** Unchanged by this hardening. An orphan
classification never writes `SANDBOX-BUSY.txt` — the run proceeds to a real
launch attempt instead — so the judge's existing `SANDBOX-BUSY.txt`
short-circuit to a `BUSY` verdict is untouched, and it stays ignorant of
`SANDBOX-ORPHAN.txt` entirely. An orphaned run either produces real
evidence (judged normally, exactly like any other run) or fails post-launch
through the pre-existing, honest "no VM" path above.

## Teardown drain

**What happened.** Run `32926056071` completed with job **SUCCESS** at
~04:17Z, but `vmmemWindowsSandbox` (15.6 GB) kept running for several more
minutes afterward, still holding VSMB handles on the run's own mapped
folders (`sandbox-lab/hoststore/...`, `sandbox-lab/scripts`). A second Gate A
run (`32929704614`), dispatched one minute later, failed at **Checkout**:
`git` could not clean the workspace (`EBUSY: resource busy or locked, rmdir
...sandbox-lab/scripts`), plus permission warnings under
`hoststore\install\dependencies\ollama` and `runtime\python312.zip`. The VM
finished exiting on its own a few minutes later, on no schedule tied to
either job. Windows Sandbox's `Stop-Process` (the teardown guard above) only
**requests** the VM stop — it returns as soon as the request is accepted,
not once the VM and the VSMB handles it holds on every `MappedFolder` are
actually released. Back-to-back Gate A runs — which the 3-consecutive-green
promotion rule below requires — keep hitting this.

**The fix, host side (`Host-Launch-Sandbox-Test.ps1`, step 6).** After the
teardown guard's `Stop-Process` (step 5), and **only on the
normal-completion path** (never on the busy-guard's exit `3`, the
quiet-share detector's exit `4`, or the plain-timeout exit `2` — all three
return before step 6, so this drain can only run on an invocation that
itself confirmed launching a sandbox, `$launchedPids.Count -gt 0`), the
script polls every `-TeardownDrainPollSeconds` (default `5`), bounded by
`-TeardownDrainSeconds` (default `300` = 5 minutes), until **both**:

- no process with any of the recorded launched PIDs remains, and
- every one of this run's mapped host folders (read back from the rendered
  `.wsb`, so it is the exact list VSMB shared into this VM — normally
  `kit-download`, `output`, `scripts`, `hoststore`, `soak-4h`) round-trips a
  `Test-DirectoryHandlesFree` probe: rename the directory away and back.
  This renames the *directory itself* rather than writing a file inside it,
  because that is the exact operation (`rmdir`/rename) Checkout's workspace
  clean performs and the exact operation that failed with EBUSY — and
  because a write-inside-the-folder probe would never catch a handle held on
  a **read-only** mapped folder like `scripts`, where VSMB still opens a
  handle on the host directory even though the guest cannot write to it.

If the bound is reached without both conditions holding, the script writes
`TEARDOWN-DRAIN-TIMEOUT.txt` into `output\` (carried into
`evidence\<source_sha>\<utc-timestamp>\` for free by `Run-GateA.ps1`'s
existing unconditional evidence copy) and emits a warning. **The verdict is
never changed** — by the time the drain runs, the product verdict is already
decided; this is runner hygiene, not a station-acceptance finding.

**The fix, workflow side (`gate-a-station-acceptance.yml`).**
Belt-and-suspenders on top of the host-side drain: a `Wait for prior run's
workspace to be clean` step runs **before** `Checkout`. If `sandbox-lab/`
already exists in the workspace (a self-hosted runner's workspace persists
across jobs), it runs the same rename-probe loop (bounded 300s, 5s poll)
against `sandbox-lab/{scripts,hoststore,output,soak-4h}` and logs what it's
still waiting on. It never fails the job on timeout — it logs a warning and
lets `Checkout` proceed, so a real, still-attributable EBUSY is at least now
distinguishable from a cold start rather than the job failing silently on
this step instead.

## Station-up wait, diagnostics capture, and the script-level watchdog

Added after the 8579e66-run3 evidence showed the station's own process
never listened on `:8000` and the harness had no way to see why (its
child-process logs live under `%ProgramData%\CivicCast\logs`, ephemeral
inside Windows Sandbox, and nothing copied them out before the VM was
torn down):

- **Station-up wait.** `In-Sandbox-Report.ps1` polls **only**
  `/api/health` on a single bounded 20-minute deadline (6s interval),
  logging every poll's timestamp and outcome to `STATION-UP-WAIT.txt`.
  `/operator/` and `/` are only probed (60s bound each) once health has
  answered 200. `summary.json` records `station_up`,
  `station_first_healthy_utc`, and `station_boot_seconds` (measured from
  `_AFTER_INSTALL.marker`) — `station_boot_seconds` is **informational
  only** in `gate-a-verdict.json`; Gate A does not fail on boot duration
  (Gate B owns timing).
- **Station diagnostics capture.** `Invoke-StationDiagCapture` runs at
  three points — right after the station-up wait concludes (pass or
  fail), right after the T3/T4/T5 decision, and unconditionally in the
  top-level `finally` block — writing bounded snapshots to
  `output/station-diag/<after-station-up-wait|after-t3t5|final>/`: a
  `robocopy` of `%ProgramData%\CivicCast\logs` (every child's `<name>.log`
  plus the rotating `supervisor.log`, per
  `civiccast/native/supervisor/install_layout.py`), a `robocopy` of
  `%ProgramData%\CivicCast\config` (excluding `data`/`pgdata`/`nats-store`),
  `sc qc`/`sc query` output, `netstat -ano` LISTENING lines, a filtered
  `tasklist /v`, and up to 200 Application/System Windows Event Log
  errors/warnings since `_STARTED.marker`. None of these operations scan
  the multi-GB install tree.
- **No hangs when the station never comes up.** If `station_up` is
  `False`, T3/T4/T5 are explicitly skipped (never attempted) — each
  writes its own result file with a `SKIPPED(station-down)` verdict line
  rather than being left absent or hanging in an unbounded loop.
  `scripts/gate_a_verdict.py` already fails closed on any non-`PASS`
  value for these checks, so this changes the evidence trail, not the
  judge's pass/fail contract for `t3_loop`/`captions`/`t4_engine`/`t5_soak`.
- **Script-level watchdog — two triggers, one process.** A genuinely
  separate `powershell.exe` process (`Start-Process`, deliberately **not**
  `Start-Job` — see the code comment for the documented `Start-Job`/
  PSWorkflow out-of-memory history on this VM) is spawned at script entry
  and polls every 30s:
  - **Overall bound.** If `DONE.json` still does not exist
    `-MaxScriptMinutes` minutes later (default 100), it writes
    `WATCHDOG-TIMEOUT.txt` and a placeholder `DONE.json` so
    `Host-Launch-Sandbox-Test.ps1`'s poll loop can never wait on a zombie
    in-sandbox script forever.
  - **Staleness bound**, added after `8579e66-run4` stalled 6+ minutes
    past `station-diag-captured-after-t3t5` (block 6's install-progress-log
    copy) with no forward progress and no `DONE.json` — `-MaxScriptMinutes`
    alone was far too coarse to catch that promptly. Once
    `summary.json.last_completed_step` first reaches a step at or after the
    runtime verdict (`runtime-check-*`, `t3t5-skipped-station-down`, or
    `t5-soak-complete`), the watchdog tracks it; if it stops changing for 8
    minutes, the watchdog writes `STALL-TIMEOUT.txt` and a placeholder
    `DONE.json` (`stall_timeout: true`).

  Either `WATCHDOG-TIMEOUT.txt` or `STALL-TIMEOUT.txt` is a named FAIL in
  the `completion` check regardless of what else in `DONE.json` looks
  complete.

  > **Superseded in part.** The staleness bound's *arming* rule described
  > above (match `last_completed_step` against three names) is what failed
  > on run6 and has been replaced — see "Mapped-folder stalls" below for the
  > arming fix, the `step_seq` change, and the new budget numbers
  > (`-MaxScriptMinutes` is 150, not 100). The two triggers and their two
  > marker files are otherwise unchanged.

## Mapped-folder stalls, the shipper, and the quiet-share fallback

Three Gate A runs hung late, each at a different statement, each with the
sandbox VM alive and `In-Sandbox-Report.ps1` writing nothing further:

| Run | Last `summary.json` step | What actually hung |
|---|---|---|
| run3 `8579e66` | `t2-render-assert` | The 5th of a run of consecutive ~30-byte `Add-Content` appends to `T3T5-RESULT.txt` — the file has 4 of its 9 expected lines |
| run4 `8579e66` | `station-diag-captured-after-t3t5` | The four-statement window to `install-progress-log-copied`, which includes a `Copy-Item` onto an existing mapped file |
| run6 `f31618f` | `station-diag-captured-after-t3t5` | Same window. Every product check had already **passed** (`T3_LOOP=PASS`, `CAPTIONS=PASS`, `T4_RESULT=PASS_PRODUCT_ENGINE`, `T5_RESULT=PASS beats=4 unhealthy=0`) before the run was failed closed 47 minutes later |

**What the failure mode is not.** "Sustained/large I/O exhausts the share"
does not survive run3, where a 30-byte append to an already-created file
wedged. "The share dies" does not survive run6: 42 minutes into that stall
the *separate* watchdog `powershell.exe` created `WATCHDOG-TIMEOUT.txt` and
`DONE.json` in the very same mapped folder, and both reached the host. The
share was fine.

**What it is.** A synchronous, uncancellable, timeout-less file operation
issued against a VSMB/9P share the guest does not control can wedge the
*issuing thread* permanently, while the share keeps serving other file
objects and other processes normally. `In-Sandbox-Report.ps1` ran the entire
gate on one thread and wrote every artifact straight to the share, so any
single wedged I/O ended the run silently. Whether the wedge is a stuck file
object or a stuck IRP on that connection cannot be distinguished from
post-mortem timestamps, and it does not matter: both have the same fix.

The fix takes the share off the critical path:

- **Local evidence directory.** `$OutDir` inside the sandbox is now
  `C:\CivicCastLocalOut`, on storage the VM owns. The transcript, every
  `T*-RESULT` file, every `station-diag` capture, every redirected child
  stdout/stderr, `summary.json`, and `DONE.json` all land there.
- **A shipper process.** A supervisor spawns one short-lived tick child every
  ~25s; each tick writes `_SHIPPER-HEARTBEAT.txt` locally and then
  `robocopy /E` mirrors the local directory into `C:\CivicCastOutput`. A tick
  that wedges on the share costs that tick — the supervisor skips while a
  child is still running, and force-replaces one older than three intervals,
  because a fresh process gets fresh handles (exactly the property run6's
  watchdog demonstrated). The mirror is **additive, never `/MIR`**: the host
  owns files in that folder too (`.gitkeep`, `_HOST_LAUNCHED.marker`,
  `SOAK_MINUTES.txt`, `HOST-QUIET-SHARE.txt`) and a purge would delete them.
  The one retraction the harness genuinely needs — a `WATCHDOG-TIMEOUT.txt`
  that a genuine completion later supersedes — is an explicit named list.
  `DONE.json` is excluded from the bulk mirror (`/XF`) and copied on its own
  after it returns, so the harness's oldest contract survives the new
  channel: DONE.json appearing on the host still means everything else
  already arrived. robocopy does not copy in write order, and the host tears
  the VM down within 10s of seeing that file.
- **Two bounded exceptions.** The driver itself touches the share exactly
  twice: a one-time inbound seed at entry (so host-provided
  `SOAK_MINUTES.txt` / `SKIP_MODE.txt` reach the reads further down) and a
  final flush after `DONE.json`. Both go through `Invoke-BoundedProcess`,
  which kills the child on timeout instead of waiting on it.
- **The watchdog moved too.** It now polls `summary.json` from, and writes
  its markers to, the local directory. In run6 it happened to write across
  the share successfully; had its *read* wedged instead, nothing at all would
  have fired.

### Why the staleness watchdog missed run6

It armed by matching the *current* value of `summary.json.last_completed_step`
against `runtime-check-*`, `t3t5-skipped-station-down`, or `t5-soak-complete`,
polling every 30 seconds. Every one of those is a momentary value. Run6's
`runtime-check-*` steps occupied `summary.json` from 22:29:57 to 22:29:58
(all three surfaces answered on poll #1 — see `RUNTIME-RESULT.txt`), and
`t5-soak-complete` was written at ~22:54:46 and superseded by
`station-diag-captured-after-t3t5` at 22:54:48. Roughly a 3-second target
sampled at 30-second intervals: the watchdog never armed, the run wedged at
22:54:48, and only the coarse 100-minute bound fired, at 23:36:50.

Two structural changes, both in `In-Sandbox-Report.ps1`:

- **Arm on a sticky file, not a transient value.** The driver writes
  `_VERDICT-STAGE.marker` once, at the station-up verdict (covering both the
  station-up and station-down paths), and never removes it. `Test-Path`
  cannot be raced by a coarse poller. The step-name predicate is kept —
  widened to every post-verdict step — as a redundant second arming path,
  never as the only one.
- **Stall on a monotonic counter, not name equality.** `summary.json` now
  carries `step_seq` (incremented on every `Save-Summary`) and `step_utc`.
  Step names can legitimately repeat; `step_seq` cannot, so "progress
  stopped" is an observation rather than an inference from string equality.

### The host's quiet-share fallback

On a healthy run the shipper heartbeat means *something* under `output\`
changes at least every ~25 seconds, right through the T5 soak's otherwise
silent 5-minute beats. `Host-Launch-Sandbox-Test.ps1` now watches for that:
no change anywhere under `output\` for `-QuietShareMinutes` (default 15)
while **our own** VM is still alive (checked by the PIDs the launcher
recorded right after its own launch, per the shared-sandbox ownership rule
above) means the guest-to-host channel — or the guest itself — is wedged and
no further waiting can produce evidence. It writes `HOST-QUIET-SHARE.txt` (on
the host's own disk, the one write in this system the wedged share cannot
affect) and exits **4** — distinct from the plain timeout's `2` and from the
busy guard's `3`, because "never started" and "started and went dark" are
different conditions. `scripts/gate_a_verdict.py` turns that marker into
`verdict: "HARNESS_ERROR"`.

### Budget ordering

| Bound | Where | Value |
|---|---|---|
| In-sandbox script watchdog | `In-Sandbox-Report.ps1 -MaxScriptMinutes` | 150 |
| Host poll deadline | `Host-Launch-Sandbox-Test.ps1 -TimeoutMinutes` | 170 |
| Same, passed through | `Run-GateA.ps1 -TimeoutMinutes` | 170 |
| Same again, **explicit override** | `.github/workflows/gate-a-station-acceptance.yml` | 170 |
| Host quiet-share bound | `Host-Launch-Sandbox-Test.ps1 -QuietShareMinutes` | 15 |
| In-sandbox staleness bound | watchdog `-StallMinutes` | 8 |
| Shipper tick | `In-Sandbox-Report.ps1 -ShipIntervalSeconds` | 25 |
| Shipper tick while the installer runs | `In-Sandbox-Report.ps1 -ShipQuiesceIntervalSeconds` | 300 |

The poll deadline is **one setting written in three places**, and the CI
workflow's explicit argument is the one that actually governs every gate run
— a script default fixed on its own would have looked correct and changed
nothing. The watchdog must fire before that deadline or every long run
degrades into an unexplained host timeout with no watchdog evidence, which is
exactly what a 150-minute watchdog under the previous 120/150-minute host
deadlines would have produced.
`tests/gate_a/test_gate_a_harness_contract.py` reads all four literals and
fails the build if the ordering drifts.

`-SandboxWaitMinutes` (90) is deliberately **not** part of this ordering: it
bounds a wait *before* the run starts, not a run already underway.

### Residual risk this change does NOT remove

`C:\CivicCastHostStore` is also a read-write mapped folder, and it is the
**install target** — the product installs into `C:\CivicCastHostStore\install`
and the station runs from there, by design, so the install tree stays visible
to the host. The driver still reads that share synchronously on its own
thread (`Test-KnownPaths`, the `station-set.json` /
`activation-self-test.json` copies in `Invoke-StationDiagCapture`, the
install-tree listing). None of the three observed stalls were on that share,
and moving the install target would destroy the install evidence Gate A
exists to collect — so this is a named, accepted residual, not a solved
problem. What has changed is the blast radius: a wedge there is now bounded
by the staleness watchdog (8 minutes) and the host quiet-share detector (15
minutes) instead of running silently to the whole-script deadline, and it
reports as a stall or a harness error rather than as 47 minutes of nothing.

## Run 7: what the shipper cost the installer

Run 7 (`5ac447c`, the first Gate A run on the shipper architecture above)
failed at `d4-activate-station` with *"a signed station bundle
(station-index.json and its packs) was not found at
`C:\CivicCastPayload\station`"* — after the same step had succeeded on the
same staged kit in run 6.

> **Dialog text note (2026-09-02).** The sentence quoted above is what run 7
> actually said and is preserved verbatim as evidence. It is no longer the
> text `d4-activate-station` emits: the step now resolves the index from
> `$EXEDIR\station\station-index.json` first and falls back to an embedded
> `$INSTDIR\station\station-index.json`, and only fails when neither exists.
> Run 7's failure mode — the kit's `station\` directory unreadable across the
> VSMB transport — still fails the install, just through the second dialog.

### The measurement

Every mapped folder in the VM — `C:\CivicCastPayload` (the ~21 GB kit the
installer reads), `C:\CivicCastHostStore` (the ~12 GB it writes), and
`C:\CivicCastOutput` (the shipper's destination) — rides **one** Windows
Sandbox VSMB transport. Comparing the installer's own `install-progress.log`
across four runs, three of them pre-shipper:

| Installer step | run3 | run4 | run6 | run7 (shipper) |
|---|---|---|---|---|
| `vc-redist` (CPU/local-disk bound) | 4m04 | 4m04 | 4m04 | **4m05** |
| `d4-provision` (local) | 25s | 25s | 28s | **28s** |
| `stage-packs` (reads Payload) | 6m39 | 6m47 | 7m21 | **11m26** |
| `d2-verify-server-binaries` | 6s | 5s | 5s | **21s** |
| `d2-verify-app-payload` | 1m09 | 1m14 | 1m19 | **3m16** |
| `d4-activate-station` | 14m13 ✓ | 14m37 ✓ | 15m44 ✓ | **35m09 ✗ (67)** |

The two steps that do not cross VSMB are flat to the second across all four
runs. Every step that does is 1.6–4.2× slower in run 7 alone. The one thing
running underneath run 7 that was not running underneath the other three is
the shipper: a `robocopy` of the whole in-VM evidence tree into
`C:\CivicCastOutput` every 25 seconds, for the entire 85-minute run.

Confounds checked and excluded: the only self-hosted job on the box in that
window was Gate A itself (the overlapping `ci-test`, `ci-a11y`,
`deterministic-detectors` and `native-app-reproducibility` runs are all
`ubuntu-latest` / `windows-latest`), and the run's own workflow log confirms
the kit resolution was byte-identical to run 6's.

**Stated honestly:** the correlation is strong and the controls are clean,
but the mechanism — why ~40 small files of metadata traffic every 25 s
should cost a bulk read stream on the same transport that much — is *not*
proven here. What is proven is that the slowdown is real, is confined to
VSMB-crossing work, and appeared with the shipper.

### The fix: quiesce

`In-Sandbox-Report.ps1` writes `_SHIPPER-QUIESCE.marker` before launching the
installer and removes it in a `finally` afterwards. While that marker is
live the shipper ticks every `-ShipQuiesceIntervalSeconds` (default 300)
instead of 25. The marker carries its own `quiesce_until_utc` expiry, so a
removal the driver never gets to perform degrades to *"shipping speeds back
up"*, never to *"shipping silently stopped"*. 300 s stays far inside the
host's 15-minute quiet-share bound — two full quiesced ticks fit with room
to spare, which
`tests/gate_a/test_gate_a_harness_contract.py` asserts — and the install
phase produces almost no evidence to ship anyway.

### What was NOT the cause

The kit reaches the VM through two chained junctions
(`kit-download` → `kit-staging/<sha>` → `C:\CivicCastTester\kit-staging\<sha>`),
which is an obvious suspect and is **not** the answer: run 6 passed with the
byte-identical two-hop chain, and both runs' workflow logs show the same
`Reusing locally staged kit` and `kit-download -> …kit-staging\<sha>
(junction)` lines. `git clean -ffdx` recursing through that junction and
deleting the shared kit is likewise refuted — measured on this host, `git
clean` removes the link and leaves every file behind the junction intact.

Both `Host-Launch-Sandbox-Test.ps1` (every `<HostFolder>` in the rendered
`.wsb`) and `Run-GateA.ps1` (the `kit-download` junction target) now resolve
through reparse points to the physical directory regardless. That is
hardening — one fewer hop for VSMB to traverse, and a `.wsb` that says what
is actually being shared — not a fix for a proven defect. `Run-GateA.ps1`
also now logs the station bundle's **file count and total bytes** before
launching, because run 7's installer failed on *"station-index.json and its
packs"* and the harness had only ever asserted that the index file existed.

### The third finding: the finalization path, again

Run 7 also stalled at `station-diag-captured-after-t3t5` — the same window as
runs 4 and 6 — for the full 8-minute staleness bound. The watchdog fixed in
the previous change caught it cleanly this time (`STALL-TIMEOUT.txt`,
`stuck_progress=seq:15`, a clean fail-closed `DONE.json`), which is the
first time that window has been bounded rather than silent.

The blocking statement is still **not identified**, and it is worth being
precise about why. Run 7 narrows it: the complete 6844-byte
`install-progress.log` reached the host, so the `Copy-Item`'s handle closed
and that statement completed. That leaves the `-Tail` read of the just-copied
file and the `Save-Summary` after it — and measured on this host against run
7's own file, `Get-Content -Tail 80` takes **8 ms**, and `Save-Summary` had
already succeeded fifteen times. Also note that 8 minutes is the watchdog's
*floor*: run 7 proves "≥8 min", where run 6 proved "≥47 min". They may not be
the same failure.

So rather than guess, this change does the two things that hold either way:

- **Relocate.** The installer-breadcrumb capture now runs immediately after
  the installer returns, out of the finalization path entirely. The
  finalization call site remains only as a guarded second attempt that
  no-ops when the first succeeded. The copy-then-re-read-with-`-Tail` shape
  is gone: the source is read once, forward, into memory (with a 16 MB cap),
  written out from memory, and the tail sliced in memory.
- **Instrument.** Every statement in that path now records its own step
  (`install-progress-probe-begin-*`, `-probed-*`, `-sized-*`, `-read-*`,
  `-copied-*`, `-captured-*`, plus `event-log-query-begin`,
  `final-diag-begin`, `final-diag-captured`). Three post-mortems in a row
  have been unable to name the operation because there was no step between
  the statements. The next one will name it.

### Transcript recovery

Run 7's `sandbox-transcript.log` reached the host as 686 bytes — the
transcript header and nothing else — despite 150 failed station-up polls that
should have logged a terminating error each.

Reproduced on this host: a Windows PowerShell 5.1 child that logged 100+
caught terminating errors still had a **689-byte header-only** transcript on
disk, and it was *still* 689 bytes after the process was killed without
reaching `Stop-Transcript`. The transcript writer buffers in user space. Any
Gate A run that ends via the watchdog — which force-completes while the main
script is still running, after which the host tears the VM down — therefore
loses its entire transcript body.

`Sync-Transcript` (`Stop-Transcript` + `Start-Transcript -Append`) now runs at
three checkpoints: after the install, at the station-up verdict, and
immediately before the finalization path. That forces the buffer out without
paying a flush on every write.

**For the record on the rest of that report:** run 7's evidence was *not*
otherwise missing. The uploaded artifact carries 41 files including
`summary.json` (6475 bytes), both `station-diag/after-station-up-wait/` and
`station-diag/after-t3t5/` trees, and every `T*-RESULT` file. The absent
`station-diag/*/logs/` subtrees are correct behaviour, not a shipper gap —
both `_capture-note.txt` files record *"logs dir not present at
C:\ProgramData\CivicCast\logs"*, because the install failed before the
station ever logged anything.

## The hoststore wedge: `C:\CivicCastHostStore` is a mapped folder *and* the install target

The candidate-#11 run (`831f3df`, run id `32871499307`) is the first Gate A run
where the install **succeeded end to end** — `installer_exit_code: 0`,
`d4-activate-station: returned 0`. The station-bundle failure from run 7 is
resolved, and the shipper quiesce measurably helped
(`d4-activate-station` 35m09 ✗ → 31m13 ✓; `stage-packs` 11m26 → 9m09). Read
those deltas with care, though: candidate #11 is a **different kit** from the
one runs 6 and 7 used (different installer SHA, 1,264 dirs vs 968), so this is
not a controlled comparison of the quiesce alone.

The run then stalled, and for the first time the harness **named the step**.

### What the instrumentation caught

```
stuck_step=install-progress-copied-post-install  stuck_progress=seq:7
stuck_since_utc=2026-08-25T17:20:05Z  stalled_seconds=509  threshold_seconds=480
```

`summary.json` confirms it: `step_seq: 7`,
`last_completed_step: install-progress-copied-post-install`,
`install_progress_log_found: true`, `install_progress_log_bytes: 7170`, and
`install_progress_log_tail: []` — the tail assignment never ran.

That pins the wedge to the three statements after that step:

```powershell
Save-Summary -Step "install-progress-copied-$Phase"          # seq 7, 17:20:03.379Z ✓
$summary.install_progress_log_tail = @($lines | Select-Object -Last 80)   # in-memory
$script:InstallProgressCaptured = $true                                   # in-memory
Save-Summary -Step "install-progress-captured-$Phase"        # never arrived
```

**This rules out the obvious suspect rather than confirming it.** The natural
reading — that the wedge is in the hoststore reads (install-dir discovery,
`station-set.json` / `activation-self-test.json`, ARP, service checks) — is
excluded by the instrumentation: every one of those is a separately recorded
step further down (`post-install-grace-sleep`, `install-dir-located`,
`install-tree-top-levels`, `service-state-query`), and none appeared.

The data being processed is unremarkable too: the log is 178 lines, longest
line 138 characters, 3,466 characters across the whole 80-line tail. Two
in-memory assignments and a ~2 KB local JSON write is not work that takes
eight and a half minutes.

### What is still unresolved, and the instrument added for it

Four runs have now ended with the same signature: **last step written, nothing
after, every other process in the VM healthy.** That signature is identical
whether the driver's *thread blocked* or the driver's *process died* — and an
unhandled `OutOfMemoryException` in this VM is not hypothetical; this script's
own `Start-Job`/PSWorkflow comment records one. No post-mortem so far can tell
those apart, which means none of them can pick a fix with confidence.

So the driver now records its PID (`_DRIVER-PID.txt`), the watchdog is given
it, and **both** watchdog triggers call `Get-DriverLiveness` at the moment they
fire, writing the answer into `STALL-TIMEOUT.txt` / `WATCHDOG-TIMEOUT.txt` and
into the placeholder `DONE.json`:

```
driver_process_alive=true  driver_pid=6160 driver_cpu_seconds=41.2 driver_working_set_mb=118.4
driver_process_alive=false driver_pid=6160 (process is gone -- the driver DIED rather than blocked)
```

The observation is only available while the VM still exists, so it has to be
made there and then. This is the same shape of fix as `step_seq` was for the
arming race: the missing instrument, not a guess at the mechanism.

### Bounding the remaining hoststore reads

Independently of the named wedge, the coordinator is right that the
post-install phase still read a mapped folder synchronously.
`C:\CivicCastHostStore\install` holds **10,683 files across 1,264
directories** after a successful install, and the installer's own last step
spent three minutes merely measuring it (`EstimatedSize corrected` in
`install-progress.log`). Three readers remained:

| Reader | What it touches | Now |
|---|---|---|
| `Test-KnownPaths` | up to 4 probes × 2 marker files | bounded probe, 90 s |
| install-tree listing | `Get-ChildItem` over the install root | bounded probe, 120 s |
| `Invoke-StationDiagCapture` marker copies | 2 files, runs up to 3× per run | bounded probe, 60 s |

`Invoke-BoundedProbe` ships a piece of the driver's own logic to a throwaway
`powershell.exe` with an arguments file and a result file, waits with a hard
timeout, kills the child if it overruns, and returns `$null` plus a recorded
error rather than blocking. "Targeted and non-recursive" was never the same as
"bounded": a single `Test-Path` against a wedged share blocks forever.

The quiesce window also **stops being lifted when the installer returns**. It
used to be, in a `finally` on the install itself, which put the 25-second tick
straight back underneath this same hoststore-heavy phase. It is now lifted at
the station-up wait (`shipper-unquiesced-at-station-up-wait`), which is
HTTP-bound and is the first phase that genuinely wants prompt shipping.

### Why the install target was NOT moved off the mapped folder

Making the in-sandbox install target a local `C:\CivicCastLocalInstall` would
remove the dependency outright and is the tidier-sounding option. It is not
viable, for reasons already recorded in `In-Sandbox-Report.ps1`:

- The `/D=C:\CivicCastHostStore\install` comment documents that staging locally
  **"blew past the Sandbox's virtual disk (os error 112 'not enough space')
  during station-pack cache"** — the install is ~12 GB and activation stages
  ~40 GB of models, against a ~40 GB virtual C:.
- The same comment records that activation **"REFUSES junction/symlink
  install-roots"**, closing the obvious dodge of installing locally and linking.
- `Run-GateA.ps1`'s fresh-install guarantee resets `hoststore\` before every
  run, and `gate_a_verdict.py`'s `install` and `activation` checks read that
  tree from the host side. Moving it would mean changing all three plus the
  evidence contract.

Bounding the accesses is the option that survives all three constraints. The
mapped install target is therefore a **standing** architectural constraint of
this harness, not a defect awaiting cleanup.

### One evidence-integrity fix

`_SHIPPER-QUIESCE.marker` joins the shipper's retraction list. The mirror is
additive, so a marker shipped during the quiesce window survived on the host
after the driver removed it locally — the preserved evidence for this very run
contains one, which tells a reader the run was still quiesced when it was not.
Evidence that misreports harness state is worse than absent evidence.

## The cause of the stalls: `ConvertTo-Json` walking a `Get-Content` cycle

This is the answer to five runs' worth of stalls — 4, 6, 7, and both
candidate-#11 runs. The liveness instrument added one change earlier is what
produced it, on the very next run:

```
driver_process_alive=true driver_pid=6636 driver_cpu_seconds=449.5 driver_working_set_mb=8318.2
```

Alive, CPU-hot, **8.3 GB resident in a 16 GB VM**. Not blocked I/O — a
serializer explosion. That single line eliminated every I/O hypothesis at once.

### The mechanism

`Get-Content` does not emit plain strings. Every line is a `PSObject` carrying
six note properties: `PSPath`, `PSParentPath`, `PSChildName`, `PSDrive`,
`PSProvider`, `ReadCount`. `PSProvider` is a `ProviderInfo`; its `.Drives` is a
collection of `PSDriveInfo`; each `PSDriveInfo` has a `.Provider`
back-reference to that same `ProviderInfo`. **That is a cycle.**
`ConvertTo-Json` serializes note properties, so `-Depth N` walks that cycle
`N` levels deep and expands combinatorially.

Measured on this host — **one** `Get-Content` line inside a hashtable:

| `-Depth` | JSON produced | Time |
|---|---|---|
| 3 | 1,889 chars | 0.8 ms |
| 4 | 32,936 chars | 107 ms |
| 5 | 447,193 chars | 105 ms |
| 6 | 3,852,872 chars | 620 ms |
| 7 | **98,197,802 chars** | 11.2 s |
| 8 | never completed | killed at 180 s, having reached 4 GB / 178 s CPU |

The driver serialized **eighty** such lines at `-Depth 8`. 8.3 GB and 449.5 s
of CPU is precisely what that costs. The same 80 lines as plain strings at the
same depth 8: **5,314 chars in 30 ms**.

### Why it always looked like an I/O stall

`install_progress_log_tail` was assigned from `Get-Content` output, and the
*next* statement was a `Save-Summary`. So the run always died at the first
serialization after that assignment — which is why runs 4, 6 and 7 all stalled
just past `station-diag-captured-after-t3t5` (where the capture used to live),
and why both #11 runs stalled at `install-progress-copied-post-install` after
the capture was relocated. **The stall followed the code.** Relocating it moved
the explosion earlier in the run rather than removing it.

(Run 3, which stalled at `t2-render-assert` in a run of `Add-Content` calls, is
*not* explained by this and is not claimed to be.)

### The fix — two independent defences

Either alone would have prevented this; neither alone prevents the next one.

1. **Sanitize at the boundary.** `ConvertTo-PlainForSummary` rebuilds the
   summary out of plain types before serialization. It recurses only into
   arrays and dictionaries, caps its own depth, and renders anything else via
   `ToString()` rather than walking its object graph — which is exactly what
   `ConvertTo-Json` does not do. A cyclic `ProviderInfo` now serializes to 59
   characters instead of expanding forever.
2. **Serialize at the depth the data needs (6), not 8.** The deepest real
   member is `install_tree_top_levels`: summary → array → entry → `children` →
   string = 5. Depth is a blast-radius multiplier for this whole bug class.

Plus a `[string[]]` cast at the source, which strips the decoration before it
can reach `$summary` at all.

> **A PS 5.1 trap found while fixing this.** The sanitizer first used
> `System.Collections.Generic.List[object]` and `return @($out)`. In Windows
> PowerShell 5.1 that combination throws *"Argument types do not match"*,
> which silently degraded **every array member of the summary** into one
> space-joined string. It uses `ArrayList` and `return , ($out.ToArray())`
> now — the leading comma also keeps a single-element array an array, which
> matters because the judge counts those fields. Caught by the host-side
> sanitizer test, not by reading the code.

### Forensics for the next unknown

When the watchdog fires **and the driver is still alive**, it now also
collects:

- a **CPU delta over a fixed interval**, with a verdict. One cumulative CPU
  number cannot separate "spinning right now" from "burned CPU earlier and is
  now blocked"; two samples can. Verified against a real spinning process
  (`driver_busy_percent=100.8 driver_verdict=SPINNING`) and a real sleeping one
  (`driver_busy_percent=0 driver_verdict=NOT-SPINNING`).
- a **bounded MiniDump** via `rundll32 comsvcs.dll` — skipped when the working
  set exceeds `DumpMaxWorkingSetMb` (a full dump of the 8.3 GB process this was
  written for would be 8.3 GB), time-bounded at 120 s, and written to
  `$env:TEMP` and **never** to the shipped evidence directory. Only its path
  and size are recorded, so it must be read inside the VM before teardown.

## Known harness quirk: the Aug-19 reference run's `completion` check

The PASS fixture used in `tests/gate_a/fixtures/pass-2026-08-19/` is a
verbatim copy of a real Windows Sandbox run's output directory from
2026-08-19 that exercised every step successfully — `T2_RENDER=PASS`,
`T3_RESULT=PASS`, `T3_LOOP=PASS`, `CAPTIONS=PASS` (cue_count 20),
`T4_RESULT=PASS_PRODUCT_ENGINE`, `T5_RESULT=PASS beats=2 unhealthy=0`. **It
does not contain `DONE.json`.** Judging it with `scripts/gate_a_verdict.py`
produces an overall verdict of **FAIL** — every check passes except
`completion`.

This is a real, documented property of that historical run, not a bug in the
fixture or the judge, and this module deliberately does not special-case it
to force a PASS — doing that would be exactly the "authored truth" failure
mode Gate A exists to eliminate. Root cause: `In-Sandbox-Report.ps1` writes
`DONE.json` as the very last thing it does, inside a `finally` block, after
stopping its own PowerShell transcript and querying the Windows Event Log.
The Aug-19 run (and an earlier run captured in
`sandbox-lab/evidence/run2-summary.json`, whose own `harness_note` field
independently documents the same pattern) was monitored by the old
host-side `sandbox-lab/scripts/Watch-Run.ps1` script, which declares "done"
the instant `T3T5-RESULT.txt` contains a `T5_RESULT=` line — racing ahead of
the script's own `finally` block by several minutes. The operator's habit of
closing the Sandbox VM as soon as the watcher printed `DONE` pre-empted that
tail end of the script before it could write `DONE.json`.

`Run-GateA.ps1` does not use `Watch-Run.ps1`. It uses
`Host-Launch-Sandbox-Test.ps1`'s own poll loop, which waits for the real
`DONE.json` file (up to `-TimeoutMinutes`) before it will touch the VM. A
real Gate A run is therefore expected to produce a genuine `DONE.json` when
the run truly completes; `Watch-Run.ps1` is kept in `sandbox-lab/scripts/`
only for interactive manual debugging, and Gate A's own orchestration never
invokes it.

## Running Gate A in CI vs. locally

The workflow (`.github/workflows/gate-a-station-acceptance.yml`) does **not**
call `Run-GateA.ps1 -RunId` any more. `gh run download`'s single stream was
measured at ~1.3 GB/10min against the ~21 GB `native-beta-kit-<sha>`
artifact — ~2.5h per gate just to fetch the kit. Instead the workflow
resolves the candidate sha itself, prunes `kit-staging/` down to that sha
(21 GB/run adds up fast on a runner with finite disk), fetches the artifact
with `actions/download-artifact@v4` (parallel, chunked — a few minutes
instead of hours), and calls `Run-GateA.ps1 -KitDir sandbox-lab/kit-staging/<sha>
-SourceSha <sha> -RunId <id>` — the sha and run id are passed through as
metadata so `gate-a-verdict.json` still carries them even though the script
itself didn't do the resolving.

`-RunId` alone (the single-stream `gh run download` path) still works and is
the right choice for a one-off local run against a completed
`native-beta-candidate-artifacts` build, where standing up the parallel-
download step isn't worth it for a single kit fetch.

Prerequisites: Windows with the Windows Sandbox feature enabled, `gh`
(GitHub CLI, authenticated) if using `-RunId`, `uv` on PATH.

```powershell
# From a completed native-beta-candidate-artifacts workflow run:
pwsh -File sandbox-lab/Run-GateA.ps1 -RunId 32444504123

# Against an already-extracted kit directory (skip the gh download) --
# the shape CI itself uses after its own parallel download:
pwsh -File sandbox-lab/Run-GateA.ps1 -KitDir C:\path\to\extracted-kit -SourceSha abc1234 -RunId 32444504123

# Shorter soak window for a quick local check:
pwsh -File sandbox-lab/Run-GateA.ps1 -RunId 32444504123 -SoakMinutes 5 -TimeoutMinutes 60
```

`-SourceSha` and `-RunId` are optional with `-KitDir`. Without `-SourceSha`,
the script tries the kit's own `station\native-station-bundle-report.json`
next (forward-looking — that report does not carry a sha-shaped field as of
this writing), then the `-KitDir` leaf directory name if it looks like a sha
(the `kit-staging\<sha>\` convention), then falls back to `unknown-local`.

Exit codes: `0` = PASS, `1` = FAIL (a real station-acceptance finding —
see `gate-a-verdict.json`), `2` = anything that is not a station-acceptance
finding at all: a `BUSY` verdict (Windows Sandbox was occupied by the other,
independent process on this box for the whole `-SandboxWaitMinutes` wait
window — see "Shared Windows Sandbox: the busy guard" above), a
`HARNESS_ERROR` verdict (the mapped output folder went quiet while the VM
was alive — see "Mapped-folder stalls" below), a timeout waiting for
`DONE.json`, no Sandbox VM, a bad/incomplete kit layout, or missing
`gh`/`uv`. None of those is ever a statement about the candidate.

`Host-Launch-Sandbox-Test.ps1` itself uses finer codes, all of which
`Run-GateA.ps1` collapses into its own `2` after recording which one
happened in the evidence directory — so callers only ever need to handle
`Run-GateA.ps1`'s three exit codes (`0`/`1`/`2`):

| Launcher exit | Meaning |
|---|---|
| `1` | No Windows Sandbox VM process a few seconds after launch |
| `2` | Timed out waiting for `DONE.json` |
| `3` | Gave up waiting for Windows Sandbox to become free; never launched (`SANDBOX-BUSY.txt`) |
| `4` | Launched, then the mapped output folder went quiet while the VM was alive (`HOST-QUIET-SHARE.txt`) |

Evidence for every run — pass or fail — lands at
`sandbox-lab/evidence/<source_sha>/<utc-timestamp>/`, a full copy of
`sandbox-lab/output/` plus `gate-a-verdict.json`.

## Cutting the download further: self-hosted candidate builds

Even at ~1-2 MB/s the download above is one leg of a two-leg transfer
problem: `native-beta-candidate-artifacts.yml` normally builds on a hosted
`windows-latest` runner and uploads the same ~21 GB kit (plus an ~18.6 GB
station bundle and ~3.4 GB of candidate binaries — the signed installer and
every `.ccpack`) before Gate A ever starts pulling it back down onto this
box — full round trip, ~2.5-3h before the Windows Sandbox even launches.

`native-beta-candidate-artifacts.yml` accepts a `build_target` input on
manual dispatch. `hosted` (the default, and every `push`-triggered build on
the release branch) is unchanged from the description above. `self-hosted`
runs the build on THIS SAME `[self-hosted, windows, sandbox-lab]` box Gate A
runs on. It keeps intermediate mirrors under
`C:\CivicCastTester\candidates\<sha>\` and writes the FINAL kit in the flat
layout Gate A expects — `setup.exe`, `packs\`, `station\` directly under the
directory — straight to `C:\CivicCastTester\kit-staging\<sha>\`, instead of
only uploading it.

By default it also skips every large upload: the station bundle and kit
(gated by `upload_large_artifacts`, as before) *and*, as of the 2026-09-09
shared-storage-cap fix, the ~3.4 GB candidate binaries too (gated by the
newer `upload_candidate_binaries` input). Gate A never consumed that
binaries artifact on the self-hosted lane in the first place — its
`assemble-native-beta-kit` job reads the signed installer and packs from
the local mirror `native-beta-candidate-artifacts.yml` writes under
`C:\CivicCastTester\candidates\<sha>\candidate\` on that same box, never
from a download — so leaving it uploading unconditionally was pure dead
weight against the 10 GB/month Actions artifact-storage cap this repo
shares with five other projects. Only the small evidence set (`*-report.json`,
`SHA256SUMS.txt`, `candidate-receipt.json`, uploaded as the
`native-beta-candidate-<sha>` artifact) and the tiny installer-embed
artifact (`native-station-embed-<sha>`) still upload unconditionally on
every lane. Pass `upload_large_artifacts: true` and/or
`upload_candidate_binaries: true` on the dispatch to force the corresponding
uploads anyway (e.g. to let a different machine inspect that candidate
without disk access to this box). Hosted builds always upload everything —
both flags are ignored there, since a hosted runner has no persistent local
box for anything to read from.

`gate-a-station-acceptance.yml` owns the consumer side of this contract in
its own "Reuse a locally pre-staged kit" step: it checks
`C:\CivicCastTester\kit-staging\<sha>\` directly (a `station\` subdirectory
and a `*setup.exe` at the root) and, when present, junctions it into
`sandbox-lab/kit-staging/<sha>` and skips the ~21 GB download entirely. Any
candidate that step doesn't find this way — an older run predating this
contract, a hosted build, or a self-hosted build whose local path isn't
present on the box handling this particular Gate A run — falls back to the
`actions/download-artifact@v4` fetch exactly as described above. Producer
(this workflow) and consumer (that step) are deliberately two separate,
independently mergeable changes that agree only on the path/layout contract
above, not on a shared artifact or workflow-to-workflow signal.

Because a self-hosted build and Gate A now share the one physical box, both
workflows' top-level `concurrency:` block uses the same `sandbox-lab` group
when a self-hosted build is in play, so the two never execute at once — the
later one queues (`cancel-in-progress: false`) rather than contending for
the box's disk, CPU, and the Windows Sandbox feature itself. Only dispatch a
self-hosted candidate build when Gate A is not currently mid-run; queueing
behind it is safe but still a wait.

The self-hosted lane also makes the compiled PyAV wheel's byte-exact
reproducibility check (inside `scripts/build_native_pyav_wheel.py`)
advisory rather than a hard failure — see
`docs/process/pyav-wheel-reproducibility.md` for why running the identical
pinned toolchain on a different physical machine can legitimately produce
non-byte-identical (but still correct — the runtime probe and license gate
both still run unconditionally) output. Every pinned *download* in that
build stays a hard failure on every lane; only the final compiled wheel's
identity assertion is affected, and only on `self-hosted`.

Local disk under both `C:\CivicCastTester\candidates\` and
`C:\CivicCastTester\kit-staging\` is pruned to the current sha at the start
of `build-native-beta` on the self-hosted lane, the same pattern
`gate-a-station-acceptance.yml` already used for its own
`sandbox-lab/kit-staging\` — without it, every self-hosted dispatch would
add another ~20-40 GB across the two roots that never gets reclaimed.

You can also run the judge directly against any evidence directory without
launching Sandbox at all:

```powershell
uv run python scripts/gate_a_verdict.py sandbox-lab/evidence/<sha>/<stamp> --source-sha <sha> --run-id <id>
```

## Directory layout (`sandbox-lab/`)

```text
sandbox-lab/
├── CivicCastSandboxTest.wsb.template   # Windows Sandbox config template (rendered per-run)
├── Host-Launch-Sandbox-Test.ps1        # Host: renders the .wsb, launches Sandbox, polls DONE.json
├── Run-GateA.ps1                       # Host: kit resolution + orchestration + judging + evidence
├── scripts/
│   ├── In-Sandbox-Report.ps1           # Runs INSIDE the Sandbox VM (LogonCommand) — the driver
│   #  (writes to the VM-local C:\CivicCastLocalOut; a shipper child process
│   #   mirrors that into the mapped C:\CivicCastOutput, i.e. output\ below)
│   ├── Watch-Run.ps1                   # Legacy interactive monitor — NOT used by Run-GateA.ps1
│   ├── PREFLIGHT.md                    # Static parse/semantics verification record
│   └── lpm-sample-short.mp4            # Real speech clip for a meaningful captions proof
├── soak-4h/                            # Copied verbatim from the v3.0 tester-handoff kit (read-only source)
├── runner/
│   └── Install-GateARunner.ps1         # Self-hosted Windows runner registration (interactive logon task)
├── output/        (gitignored)         # Live run output — wiped at the start of every run
├── hoststore/      (gitignored)        # Persistent install dir — reset at the start of every run
├── kit-download/   (gitignored)        # Junction to the resolved kit — never a copy
│   # (no .gitkeep here -- Run-GateA.ps1 deletes and replaces this whole
│   #  directory with an NTFS junction on every run, so a tracked placeholder
│   #  file inside it can never survive a run; kit-staging/ and evidence/
│   #  keep theirs because those directories are only written INTO, never
│   #  replaced wholesale)
├── kit-staging/    (gitignored)        # Downloaded candidate artifacts, keyed by source SHA
│   #  (in CI: pruned to only the current candidate's sha before every
│   #   download -- kits run ~21 GB each and the runner's disk is finite)
└── evidence/       (gitignored)        # Every run's preserved output, keyed by <sha>/<timestamp>
```

## Runner setup

Gate A needs an interactive Windows desktop session — Windows Sandbox cannot
launch from a Session-0 service. `sandbox-lab/runner/Install-GateARunner.ps1`
registers the self-hosted runner as an **interactive logon scheduled task**,
not a Windows service (contrast with the existing Linux self-hosted runner in
`docs/ops/self-hosted-ci.md`, which correctly must be a systemd service for
its own, opposite reason). See that script's header comment for the full
rationale.

```powershell
# Get a registration token first:
gh api repos/scottconverse/civiccast/actions/runners/registration-token --method POST

pwsh -File sandbox-lab/runner/Install-GateARunner.ps1 -Token <token>
```

This installs to `C:\actions-runner-gate-a` by default, registers with labels
`self-hosted,windows,sandbox-lab`, and creates a scheduled task that starts
`run.cmd` at interactive logon for the current user. The task was not
launched by this install (no token available in this environment to actually
register against GitHub) — after registering, either log off/on once or run
`Start-ScheduledTask -TaskName 'CivicCastGateARunner-<name>'` to start it,
then confirm with `gh api repos/scottconverse/civiccast/actions/runners`.

## Dirty lane: the cross-version install-over-existing gate

### Why it exists

Gate A's clean sandbox has no history. A real station does: it has a live old
version, a PostgreSQL cluster, recordings, settings, journals, and registered
native service state. For weeks the absence of that shape produced a repeating
failure family: installers that passed the pristine sandbox and then died on
machines carrying prior-version state. One field instance
(DESKTOP-2BR3SJR, 2026-08-30, upgrade #17 → #18): the preserved
`ProgramData` carried `data\pgdata`, `data\uploads`, and a
`components\captions-large-v3` model **without** any ProgramData
`activation-self-test.json` receipt — the new install resolved large-v3 as
the highest staged tier, read the receipt from the tier's own root, found
none, and the supervisor crash-looped on
`NativeStationConfigurationError`. PR #80 fixed that instance (degrade to
the proven floor tier with a WARNING); the dirty lane exists so the *family*
finally has a CI gate instead of another memory note — per the standing
owner rule that a twice-documented failure pattern gets a gate that fails
the build.

### What it does

`gate-a-station-acceptance.yml` runs a second job,
`station-acceptance-dirty`, after the clean lane (same runner, same
`sandbox-lab` concurrency group, same informational status). It runs only
when the clean lane succeeded: a candidate that cannot pass a pristine box
tells the dirty lane nothing new, and Windows Sandbox on this runner is a
shared resource that a doomed 3-hour second cycle should not occupy. It invokes
`Run-GateA.ps1 -DirtyLane` with the pinned previous-kit directory and source
SHA. The previous identity lives in `sandbox-lab/upgrade-baseline.json`; the
workflow verifies its exact candidate-build run, source SHA, workflow name,
successful conclusion, installer SHA-256, signed station-index SHA-256, and
product version. Candidate #22 (`1.0.0-rc18`) is the pinned older product
version; its complete kit is retained in
`C:\CivicCastTester\kit-staging\<sha>`. The lane fails closed if those exact
local bytes are absent or mixed. The signed station index in turn pins every
station pack hash. It never substitutes newest/latest or a partial artifact.

`Run-GateA.ps1` threads `-DirtyMode` and `-UpgradeMode` into
`Host-Launch-Sandbox-Test.ps1` (writes `DIRTY_MODE.txt` into `output\`, the
same host-to-guest input channel as `SOAK_MINUTES.txt`, plus
`UPGRADE_MODE.txt`) and `--lane dirty` into the judge. The `.wsb` maps the
pinned previous kit separately at `C:\CivicCastPreviousPayload`. Inside the
sandbox, `In-Sandbox-Report.ps1` runs an upgrade **prologue** before the
unchanged acceptance flow:

1. **Previous-candidate install** — install the pinned previous setup; its
   provision step creates a real PostgreSQL cluster at
   `%ProgramData%\CivicCast\data\pgdata`.
2. **Plant operator data** — two real media files into
   `%ProgramData%\CivicCast\data\uploads`, SHA-256s recorded.
3. **Record cluster identity** — `PG_VERSION` content + the pgdata
   directory's creation time, so phase 2 can prove the *same* cluster
   survived rather than a re-provisioned lookalike.
4. **Prove version separation** -- hash both setup executables, read the old
   install's authoritative `InstalledVersion`, read the current setup product
   version, and fail if either byte identities or product versions are missing,
   malformed, or equal. Equal versions would route D3 to `SAME_VERSION_NO_OP`
   and therefore cannot count as upgrade proof.
5. **Leave the old version live** — no harness-authored uninstall or manual
   service stop. This is the condition the current installer must own.

Then the **full normal acceptance flow runs the current setup directly over
that live previous install** (upgrade quiescence/migration → activation →
station-up → T2/T3/T4 → a 10-minute soak).
After the station-up verdict the harness writes `DIRTY-RESULT.txt`: the last
machine-parseable D3 breadcrumb from the append-only installer log is captured
as `D3_ROUTE` and `D3_ENGINE_EXIT`, pgdata identity is re-checked, upload hashes
are re-checked, and (when seeded) the supervisor log is grepped for PR #80's
orphaned-tier WARNING. The last-match rule binds the evidence to the current
installer, not the previous-candidate install earlier in the same sandbox run.

In upgrade mode (`UPGRADE_MODE=1`), `DIRTY-RESULT.txt` also carries
`POST_UPGRADE_DB_REVISION`, `EXPECTED_HEAD`, and
`POST_UPGRADE_DB_REVISION_MATCHES_HEAD` — read straight from the same
station-up `/health` poll that declared the station up
(`civiccast/app.py`'s `/health` now returns `schema_db_revision` /
`schema_expected_head` unconditionally, not just when `schema == "behind"`).
Gate A run 33681670855 (kit 7971815, beta.2 → beta.3) shipped with
`D3_ENGINE_EXIT=0` and a healthy station-up body while the live database was
still at the OLD version's revision — a pre-upgrade backup-verification
false-negative had rolled the D3 engine back, but the flat installer layout
let setup continue anyway and start a service that happily answered
`/health` 200 over the unmigrated schema. `D3_ENGINE_EXIT=0` and a healthy
body are therefore not proof by themselves; the judge compares the two
revisions explicitly.

The judge (`scripts/gate_a_verdict.py --lane dirty`) adds three checks on
top of the unchanged clean set:

| check | PASS means |
| --- | --- |
| `dirty_prep` | previous install exit 0, the live-upgrade request is explicit, and previous/current installer SHA-256 identities are valid and distinct |
| `dirty_survival` | current install exit 0, D3 explicitly reported route `UPGRADE` with engine exit 0, `POST_UPGRADE_DB_REVISION_MATCHES_HEAD=1` (in upgrade mode), and the SAME pgdata cluster plus byte-identical uploads survived install-over-existing and station-up; successful `FRESH_INSTALL` and `SAME_VERSION_NO_OP` routes fail this check |
| `dirty_orphaned_tier` | `SKIP` with a loud not-covered reason in automated cross-version mode; this uninstall-only remnant shape is not authored during a live upgrade |

The dirty job posts its full per-check verdict table to the workflow run
summary, so the verdict — including any `SKIP` — is on the run's front page.

### Legacy uninstall-remnant sub-shape (manual opt-in)

`Run-GateA.ps1 -DirtyLane` without `-PreviousKitDir` and
`-PreviousSourceSha` retains the earlier same-candidate install → seed → real
uninstall → reinstall harness. That manual mode verifies the uninstaller's
ProgramData preservation contract and can exercise the orphaned-caption-tier
fallback described below. The automated workflow does not use this shape and
does not combine its evidence with the cross-version result.

### The orphaned-tier remnant needs the real model

`_resolve_caption_tier` (`civiccast/native/station_runtime.py`) verifies the
selected tier's pinned SHA-256 file set **before** the activation receipt is
ever read. A stub `components\captions-large-v3` therefore reproduces a
deliberate fail-closed crash ("model file is missing/tampered"), *not* the
orphaned-receipt degrade path — the field machine's remnant was a complete,
valid model whose *receipt* was missing. Since the candidate kit ships
floor-only (no large-v3 pack) and the sandbox has no network, the harness
can only plant this remnant from a host-staged copy of the real ~2.9 GB
model. To enable it once, permanently, on the Gate A runner:

```
# Shape: C:\CivicCastTester\dirty-seed\captions-large-v3\models\faster-whisper-large-v3\<model files>
# (the same relative layout as %ProgramData%\CivicCast\components\captions-large-v3)
```

The legacy `Run-GateA.ps1 -DirtyLane` sub-shape picks it up from
`C:\CivicCastTester\dirty-seed\captions-large-v3` (override with
`-DirtySeedLargeV3Dir`) and stages it into `hoststore\dirty-seed\` for the
sandbox. Until then, `dirty_orphaned_tier` reports `SKIP` with a
NOT-covered detail — visible in the run summary — and the lane's other
remnant shapes still gate.

### Remnant shapes covered vs not

Covered by the automated lane: a pinned previous candidate left live, current
setup invoked over it, exact installer identity separation, live pgdata reuse,
upload survival, service quiescence/migration, and the complete normal Gate A
acceptance set. Covered only by the legacy manual sub-shape: the product's
real uninstall preservation contract and — when seeded — the
preserved-model/missing-receipt caption orphan. **Not covered** by the
automated lane: uninstall-only caption remnants, remnants of a *crashed*
(not uninstalled) install,
partial/corrupt model directories (deliberately: the product fails closed on
those by design), leftover registry state a failed uninstall retains
(`InstallDirRegKey` paths), Program Files leftovers from third-party
interference, and multi-version remnant stacks. Each of those needs its own
seed design; add them here when they earn a gate the same way this one did.

### Timing contract

Two install cycles need a bigger budget: the in-sandbox watchdog raises
itself to **210** minutes when `DIRTY_MODE.txt` is present, the host poll
must exceed it (**230**, enforced as a floor by
`Host-Launch-Sandbox-Test.ps1 -DirtyMode` and passed explicitly by the
workflow), and the dirty job's `timeout-minutes: 340` outlasts 230 plus the
up-to-90-minute shared-sandbox wait. The clean lane's 150 < 170 ordering is
untouched. All of it is asserted by
`tests/gate_a/test_gate_a_harness_contract.py`.

## Download-only lane

### Why it exists

Owner decision, 2026-09-02. From the K1 fix until the two changes named
below, the installer's `d4-activate-station` step *required* a `station\`
folder (the ~21 GB signed model bundle) beside `setup.exe` and aborted
otherwise -- so a download-only install or upgrade (the shape a real
deployment uses when it fetches `setup.exe` and the small runtime `packs\`
over the network but reuses an already-activated station's cached model packs
instead of re-downloading the whole station bundle) silently stopped working.
No existing Gate A lane caught it: the clean lane and the dirty lane above
both install from the **full** kit (`setup.exe` + `packs\` + `station\`), so
neither ever exercises a payload with `station\` absent. The owner ruled that
a download-only lane is required for every release from now on.

**What closed the gap, and why this lane still exists.** Two changes landed
after this lane was written, and together they are what it now grades:

1. `acquire_station_distribution` serves a pack that is absent from the
   index's media directory from the station's own per-SHA cache instead of
   failing on the missing file (`native_distribution.rs::
   copy_station_pack_to_cache`), and the model packs carry an identity that
   is stable across candidates so a previous install's cache still matches.
2. `setup.exe` embeds the signed `station-index.json` and the tiny `core`
   pack as Tauri `bundle.resources`, so `d4-activate-station` has an index to
   import even with no `station\` folder beside it: it resolves
   `$EXEDIR\station\station-index.json` first (the full-kit path both lanes
   above still take, unchanged) and falls back to
   `$INSTDIR\station\station-index.json`. It still aborts when NEITHER
   exists.

So this lane is no longer proving a known-failing shape -- it is the required
proof that the replacement actually works end to end on a real machine. Note
what it therefore does **not** cover, and what "download-only" means here: a
download-only *upgrade* completes because the previous install populated
`<install root>\packs\.station-cache`; a download-only *first* install on a
machine that has never held the model packs still fails closed, correctly.
Phase 1 of this lane installs the pinned previous candidate from its full kit
precisely so the cache exists before phase 2 runs.

### What it does

`gate-a-station-acceptance.yml` runs a third job,
`station-acceptance-download-only`, after the dirty lane (`needs:
station-acceptance-dirty`, same runner, same shared-Sandbox discipline).
Unlike the clean and dirty lanes, this job is **REQUIRED**, not
informational -- its own `Fail the job on a non-PASS verdict or harness
error` step fails the job like any other CI check.

It invokes `Run-GateA.ps1 -DownloadOnlyLane` with the same pinned
previous-kit directory and source SHA the dirty lane uses (one immutable
upgrade baseline, `sandbox-lab/upgrade-baseline.json`, shared by both
cross-version lanes). `-DownloadOnlyLane` implies the dirty lane's
`-DirtyLane`/cross-version `-UpgradeMode` shape -- phase 1 is byte-identical
to the dirty lane's own upgrade prologue:

1. **Phase 1 -- install the pinned previous candidate from its FULL kit**
   (`-PreviousKitDir`/`-PreviousSourceSha`, hash-verified exactly as the
   dirty lane verifies it), plant operator data, leave it live. This reuses
   `Invoke-DirtyRemnantPrologue`'s existing `UpgradeMode` branch in
   `In-Sandbox-Report.ps1` unchanged.
2. **Phase 2 -- run the CURRENT candidate's `setup.exe` from a FILTERED
   payload directory containing ONLY `setup.exe` and `packs\`** (the runtime
   packs, side-loaded because the sandbox has no network) **and NO
   `station\` directory.** `Run-GateA.ps1` builds this filtered directory
   itself, on the host, before rendering the `.wsb`: it copies the small
   `setup.exe` and NTFS-junctions `packs\` from the resolved current kit into
   a fresh `sandbox-lab\kit-download-filtered\` directory, refuses to
   proceed if that directory somehow ends up with a `station\` subdirectory,
   and then repoints the existing `sandbox-lab\kit-download` junction (which
   the `.wsb` template already maps to `C:\CivicCastPayload` read-only) at
   the filtered directory instead of the full kit -- the kit on disk is
   never modified. The full acceptance flow inside the sandbox is otherwise
   unchanged; it reads `$PayloadDir` (`C:\CivicCastPayload`) exactly as the
   clean and dirty lanes do, so it now sees the filtered shape without any
   code path needing to know it is in a special mode, except for the small
   evidence-recording addition below.
3. In-sandbox, `In-Sandbox-Report.ps1` records the download-only evidence to
   `DOWNLOAD-ONLY-RESULT.txt` in two passes: **before** phase 2's install
   runs, `STATION_DIR_PRESENT` (0/1, checked against
   `C:\CivicCastPayload\station`) and `PAYLOAD_DIR`; **after** the station-up
   verdict, `PHASE2_INSTALL_EXIT`, `D3_ROUTE`/`D3_ENGINE_EXIT` (the same D3
   breadcrumb the dirty lane's `DIRTY-RESULT.txt` already captures),
   `POST_UPGRADE_DB_REVISION`/`EXPECTED_HEAD`/
   `POST_UPGRADE_DB_REVISION_MATCHES_HEAD` (the same station-up `/health`
   revision proof the dirty lane's upgrade mode records -- see the dirty-lane
   section above for the Gate A run 33681670855 failure this closes),
   `STATION_SET_PRODUCT_VERSION` (read directly from the install's own
   `station-set.json`), and `CURRENT_PRODUCT_VERSION` (echoed from the
   shared upgrade prologue's own `DIRTY-PREP-RESULT.txt`).

The judge (`scripts/gate_a_verdict.py --lane download-only`) runs the
unchanged clean-lane checks plus `dirty_prep` and `dirty_survival` (the same
cross-version upgrade evidence the dirty lane's checks already grade) plus a
new `download_only_no_station_dir` check, which FAILS unless
`DOWNLOAD-ONLY-RESULT.txt` proves all of: the phase-2 payload had no
`station\` (`STATION_DIR_PRESENT=0`), the phase-2 install and its D4
activation step both exited 0 (`PHASE2_INSTALL_EXIT=0`,
`D3_ENGINE_EXIT=0`), and `station-set.json` names the CURRENT candidate's
product version (`STATION_SET_PRODUCT_VERSION == CURRENT_PRODUCT_VERSION`) --
proving activation succeeded by reusing an already-activated station's
cached model packs, not by silently falling back to a stale or mismatched
receipt. This lane deliberately does **not** run `dirty_orphaned_tier` -- that
remnant sub-shape belongs only to the dirty lane's own legacy uninstall-only
path. Fail-closed on any missing evidence file, exactly like every other
Gate A check.

### What it does NOT prove

- **Runtime-pack download from `civiccast-releases` is not exercised.** The
  filtered payload directory's `packs\` is side-loaded from the already
  fetched/staged current kit (junctioned, never copied) because the sandbox
  has no network (see `docs/ops/gate-a.md`'s clean-lane boundary statement
  above -- `Networking: Disable` in the `.wsb`). This lane proves the
  installer's OWN behavior when `station\` is absent and packs are already
  present beside `setup.exe`; it does not prove the separate download step a
  real download-only deployment performs to fetch those packs over the
  network in the first place.
- Everything the clean lane's own boundary statement already excludes (the
  24h/72h real-hardware soaks, physical SDI proof, unattended-reboot
  survival, the commissioning-wizard UI walkthrough, OTT-app checks).
- The dirty lane's legacy uninstall-only remnant shapes (real uninstall
  preservation, the orphaned-caption-tier fallback) -- this lane never runs
  that sub-shape.

### Diagnostic reruns

`workflow_dispatch`'s `lane` input gained a third option,
`download-only-only`, alongside the existing `full` and `cross-version-only`
-- it skips both the clean and dirty lanes (their own `if:` guards exclude
it) and runs only `station-acceptance-download-only`, using its own
`needs: station-acceptance-dirty` bypass exactly the way `cross-version-only`
already bypasses the dirty lane's own `needs: station-acceptance`.

### Timing contract

Identical two-install-cycle budget to the dirty lane, reused rather than
duplicated: `-DownloadOnlyLane` sets `-DirtyMode` on
`Host-Launch-Sandbox-Test.ps1` exactly as plain `-DirtyLane` does, so the
in-sandbox watchdog raises itself to 210 minutes, the host poll floor is 230,
and the job's own `timeout-minutes: 340` outlasts 230 plus the up-to-90-minute
shared-sandbox wait -- the same numbers `tests/gate_a/
test_gate_a_harness_contract.py` already asserts for the dirty lane, since
they are the identical settings.

### "Required" here is an owner decision, not (yet) a GitHub branch-protection setting

This lane's job fails the CI run on a non-PASS verdict, unlike the clean and
dirty lanes -- that is what "required for every release" (owner decision,
2026-09-02) means in this file. It is **not** the same thing as GitHub
**required status checks** on branch protection: a check that a repository's
branch-protection rules do not name as required can be *skipped entirely*
(the whole `gate-a-station-acceptance` workflow only triggers after a
successful `native-beta-candidate-artifacts` run, or on manual dispatch) and
GitHub will still allow the PR to merge, because a skipped check is not a
failing check from branch protection's point of view. Per this repo's
`CLAUDE.md`, "Owner gates" section, only Scott can add a check to branch
protection's required-status-checks list, and no agent does this
automatically. Until that step happens, a PR CAN merge without this lane
having run at all -- its "required" status is enforced by the job failing
loudly when it does run and by human process, not by a GitHub-mechanical
gate. Add `station-acceptance-download-only` (and, per the existing
Promotion rule below, the clean and dirty lanes once each earns it) to
branch protection's required-status-checks list once it has been proven
against real candidate builds.

## Promotion rule

Gate A's workflow (`.github/workflows/gate-a-station-acceptance.yml`) is
**informational only**. It runs automatically after every successful
`native-beta-candidate-artifacts` build and reports its verdict, but it does
not block merges or releases. Per this repo's CLAUDE.md "Owner gates"
section, only Scott can promote it to a required check (branch protection)
and only Scott flips that setting — no agent does this automatically. The
agreed bar: promote after **3 consecutive green (PASS) runs** against real
candidate builds.

## Provenance

Gate A's harness scripts were imported from a standalone proof harness at
`C:\Users\scott\Desktop\Code\sandbox-lab\` (outside this repository, not a
git repo) that was manually proven against real candidate builds before this
import. `In-Sandbox-Report.ps1` needed no host-path changes — every path it
touches is a fixed Sandbox-internal path, either a mapped-folder mount
(`C:\CivicCastPayload`, `C:\CivicCastOutput`, `C:\CivicCastScripts`,
`C:\CivicCastHostStore`, `C:\CivicCastSoak`) or, since the mapped-folder-stall
fix, the VM-local `C:\CivicCastLocalOut` — never a host path — so it was
imported byte-for-byte. Only `Host-Launch-Sandbox-Test.ps1` needed
parameterization (it previously hardcoded an absolute host `$Root`), and the
`.wsb` became a template rendered per-run instead of a static file, because
Windows Sandbox's `MappedFolder` entries require absolute host paths that
cannot be relative to the `.wsb` file's own location.
