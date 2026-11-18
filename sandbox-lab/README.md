# sandbox-lab/

This page describes existing developer lab tools. Their historical Beta 5
verdict criteria are tool behavior, not automatic Beta 12 release requirements.
Scott chooses the authorized check sequence and release acceptance. For the
Beta 12 results (historical), see [verification](../docs/releases/v1.0.0-beta.12-verification.md).

Gate A — the automated station-acceptance release gate. Full documentation
(what it proves, verdict criteria, how to run it, runner setup, promotion
rule): **[`docs/ops/gate-a.md`](../docs/ops/gate-a.md)**.

Quick start:

```powershell
pwsh -File sandbox-lab/Run-GateA.ps1 -RunId <native-beta-candidate-artifacts run id>
```

`output/`, `hoststore/`, `kit-download/`, `kit-staging/`, and `evidence/` are
per-run state (gitignored, kept as empty tracked directories via
`.gitkeep`) — `Run-GateA.ps1` regenerates them on every run.

## Local soak

A fast, LOCAL pre-check that finds bugs on HALO in ~15 SOAK minutes instead
of on a tester box in hours. It drives a real silent install + station
start + sample-content playout inside a disposable Windows Sandbox VM,
using a kit already verified on disk (no download). It is **not** a
replacement for Gate A — it's a quick sanity pass you run before pushing
something out to the tester fleet, reusing Gate A's own proven
install/health/channel-start code paths (see `sandbox-lab/scripts/In-Sandbox-Soak.ps1`'s
header for exactly which lines came from where) at a much shorter default
duration.

```powershell
pwsh -File sandbox-lab/Run-SandboxSoak.ps1 -Sha <full sha> [-Minutes 15] [-KitRoot C:\CivicCastTester\kit-safe] [-SeamlessReload] [-CaptionsOff] [-WorkerEnv "NAME=VALUE;NAME2=VALUE2"]
```

Add `-DryRun` to verify the kit, render the `.wsb`, run the
`System.Net.Http.HttpClientHandler` self-check (the guest's own Windows
PowerShell 5.1 engine, not `pwsh`), and parse-check both in-sandbox scripts
without ever launching Windows Sandbox — a dry run is exempt from the busy
guard below since it never launches anything.

Beta.5 enables seamless content reload by default when
`CIVICCAST_EGRESS_SEAMLESS_RELOAD` is absent. An ordinary invocation therefore
leaves the environment unset and still applies the strict seamless verdict:
zero planned worker restarts, zero reload aborts, and no armed reload left
without a commit. `-SeamlessReload` remains as a compatibility/reproduction
override that explicitly exports `CIVICCAST_EGRESS_SEAMLESS_RELOAD=1` at
MACHINE scope before service start. Evidence records the override request and
effective expectation separately as `seamless_reload_override_requested` and
`seamless_reload_expected`; the legacy `seamless_reload` field retains its
original explicit-override meaning for older evidence readers. The
`seamless_reload_verified` field applies only to that explicit environment
override because neither `Get-Process` nor `Win32_Process` can read another
process's real environment block from outside it.

`-CaptionsOff` PUTs `{"live_captions_enabled": false}` to
`/api/staff/station/profile` right after first-admin succeeds, using the
operator's own first-admin token, then GETs the profile back to confirm
the read-back value is really `false` -- it never touches the
`CIVICCAST_CAPTION_TAP` env var (the caption-tap machinery itself stays
untouched; this only flips the operator-facing switch). A failed PUT or a
read-back that is not `false` is `HARNESS_ERROR` (see
`sandbox-lab/scripts/CaptionsOffCheck.ps1`'s `Get-CaptionsOffVerification`, the same
never-an-unconfirmed-premise principle as `-SeamlessReload`'s own
verification). Recorded as `captions_off_requested`/`captions_enabled`/
`captions_off_verified` in `SOAK-START.json` and `VERDICT.json`. `captions_enabled`
is a MEASURED value on every run, not only `-CaptionsOff` runs: right after
first-admin, this lane always does one GET
`/api/staff/station/profile` (the resolved, in-effect value —
`civiccast/platform/station_router.py`'s `get_station_profile`) and records
the real read-back (conservatively `true` only when the GET itself
couldn't be confirmed) — never a hardcoded assumption.

`-WorkerEnv "NAME=VALUE;NAME2=VALUE2"` (or `-WorkerEnv @('A=1','B=2')` —
both shapes parse identically, see `sandbox-lab/scripts/WorkerEnv.ps1`'s header)
injects arbitrary environment variables into the CivicCastSupervisor
service's own per-service `Environment` `REG_MULTI_SZ`, and therefore into
every GStreamer egress worker, which inherits the daemon's process
environment wholesale (`civiccast/egress/gst/strategy.py`'s
`_default_worker_launcher` builds `env = {key: value for key, value in
os.environ.items() if key not in ("SWAPS", "INTERVAL")}` and passes that
straight to `subprocess.Popen`). Entries are deduped by name (later wins)
and merged into the **same** registry write and the **same**
Stop-Service/Start-Service cycle as `-SeamlessReload` — one restart total,
never two, even when both are passed together. An empty value (`NAME=`)
is the explicit **unset/remove** form: it deletes that name from the
service's registry entry entirely rather than writing an empty string.
This matters because the product does not treat "empty" and "absent" the
same way for every variable that could be worth injecting —
`civiccast/captions/tap.py:67-73`'s `build_audio_tap_plan` happens to treat
`CIVICCAST_CAPTION_TAP_DIR=""` the same as unset (`.strip()` then `if not
root: return None`), but this lane does not assume every future experiment
variable shares that fallback, so `NAME=` is defined once, uniformly, as a
real removal. `<`, `>`, `&`, `|`, `^`, `%`, and a literal `"` are rejected
outright in any name or value (a parse error, not a best-effort escape):
the `.wsb` `<LogonCommand>` runs through `cmd.exe`, whose
redirection/pipe/escape-metacharacter scan runs before (and independently
of) the quote-aware argv tokenizing `powershell.exe` performs on its own
`-File` arguments — `<`/`>` are real redirection operators to `cmd.exe`
even inside a double-quoted token, and `cmd.exe` expands `%NAME%` tokens
inside a quoted argument too (measured directly: a value of
`C:\%USERNAME%\d.log` is delivered to the guest as `C:\scott\d.log`, not
the literal text). A value ending in a single trailing backslash (`\`) is
also rejected: it collides with the closing quote this lane appends,
under the Win32 argv-tokenizer's backslash-then-quote escaping rule
(measured: `C:\CivicCastSoak\` is delivered as `C:\CivicCastSoak"`,
silently swallowing everything after it into the same argument) — name a
file inside a directory rather than the bare directory path.

`-DryRun` renders the `.wsb` and the LogonCommand exactly as a real run
would and round-trips the rendered `-WorkerEnv "..."` token by actually
**executing** it through a real `cmd.exe` → `powershell.exe -File` parse
(`Test-RenderedWorkerEnvRoundTrip`, substituting a tiny throwaway capture
script for the real `-File` target so nothing else about the command is
touched) — not a regex over the rendered text. A regex only ever proves
what was *written*; the two bugs above (`%NAME%` expansion, the trailing-
backslash/quote collision) both round-tripped as false PASSes under an
earlier regex-only version of this check, because the regex had no way to
observe what a real parse actually *delivers*. This function catches
either bug — or any future cmd.exe/PowerShell quoting quirk this lane
hasn't thought of yet — before a quoting regression ever reaches a live
sandbox.

Verified the same way `-SeamlessReload` already is — reading another
process's real environment block from outside it is not something this
harness can do (`Win32_Process` carries no environment-adjacent property at
all), so "verified" means the per-service registry value matches what was
requested for that entry (present with the exact value for a set entry;
genuinely absent for an unset/removal entry) **and** a live post-restart
control-plane process exists. Recorded per-entry as
`worker_env_requested`/`worker_env_verified` in `SOAK-START.json` and
`VERDICT.json`; any entry that cannot be confirmed is `HARNESS_ERROR`, same
principle as `-SeamlessReload`/`-CaptionsOff` — never an unconfirmed
premise of a PASS/FAIL verdict for a flag the operator explicitly asked to
test.

If the injected env sets `GST_DEBUG_FILE=<path>`, the guest creates that
path's containing directory before the service restart (GStreamer does not
create intermediate directories for a log-file path itself), and
`Copy-StationLogs` copies that file — plus any rotated/sibling files
matching the same base name or a `*.gstdebug` extension in the same
directory — into `logs\checkpoint-cycleN\gst-debug\` and `logs\final\
gst-debug\`, alongside the rest of that checkpoint's evidence. Each copy
takes a **one-time snapshot** of the source's length the moment it opens
the file: it copies exactly `min(snapshot length, 200 MB)` bytes, and
anything the source gains **after** that snapshot is simply not part of
that capture — the next checkpoint's own fresh snapshot picks up whatever
grew in the meantime, the same way every other piece of evidence this lane
gathers is a point-in-time snapshot, not a live tail. A file whose snapshot
length is still **within** the 200 MB per-file bound is copied
**verbatim** — no banner, no rename — since nothing was dropped. Only a
file whose snapshot length actually **exceeds** the bound is truncated:
its **last** ~200 MB is kept (streamed, never loaded whole into memory),
written under the name `<original-name>.tailNNN(kb|mb)` (the unit matches
whatever the bound actually was — never overwriting/being confused with a
complete copy) with a one-line banner prepended to the bytes themselves so
a reader of the file knows content was dropped — an earlier version wrote
that banner and rename even when nothing had been dropped at all, which
was actively misleading for the common case where `GST_DEBUG` never grows
past the bound. A separate, purely defensive 30-second wall-clock ceiling
on the copy itself (never a growth-detection mechanism — the snapshot
already settles that question) guards against a genuinely slow/contended
disk; on the rare occasion it fires, the result is marked partial and
renamed to `<name>.partial` so an incomplete capture can never be mistaken
for a complete one.

Capture is **gated and volume-capped**, not run on every single checkpoint,
using **two independent budgets** so one can never starve the other:
periodic rollup checkpoints (every ~3 minutes) capture only the **first**
one and every **10th** thereafter, and draw only against a **400 MB**
periodic budget; `final` (and an early-failure label like the
ON_AIR-poll-timeout path) is always attempted and draws only against its
own separate **200 MB** reserve — a periodic checkpoint can never exhaust
the reserve `final` needs, and vice versa. A THIRD, independent cap bounds
a single checkpoint's own total across every candidate file it copies
(the primary `GST_DEBUG_FILE` plus any rotated/`*.gstdebug` siblings) at
**200 MB** — without it, one checkpoint with two or more large candidates
could otherwise drain the entire 400 MB periodic budget by itself, well
before the every-10th gate ever had a chance to space things out. Each
per-file copy's own 200 MB bound is additionally clamped to whatever
remains of the tightest of these three budgets, so the combined ceiling
(400 MB + 200 MB = 600 MB per run) is a **hard**
one, never overshoot by "one file's worth" the way a simple pre-write check
would allow — true because the 200 MB (or smaller, clamped) per-copy bound
is enforced with a counted read loop in **every** path Copy-GstDebugTail
can take, including the common case where the source looked small enough
to copy verbatim: that path used to read straight to the source's real
end-of-file with no bound of its own, so a file still being actively
written could grow far past the bound for the whole duration of a single
copy (measured: 1119 MB copied under a 120 MB bound) before this was
fixed. Without any of this, an earlier version captured on every
single checkpoint with no budget at all, measured to project to roughly
8 GB shipped over a 2-hour soak — and a single shared 600 MB cap (an
earlier fix) still let periodic checkpoints consume the whole thing before
`final` ever ran.

**Caption-tap controls in Beta 12.** The native environment builder honors
`CIVICCAST_CAPTION_TAP=off` and clears `CIVICCAST_CAPTION_TAP_DIR` in the child
environment, including an inherited directory value. This disables the tap
worker and audio tee. `CIVICCAST_EGRESS_EMBED_CAPTIONS` is still set by the
native runtime; a registry write alone is not proof that every downstream
setting changed. Use the operator's live-caption switch for normal station
operation. See `civiccast/native/station_runtime.py` for the effective launch
configuration.

**The experiment this follow-up exists to run** (stall-timeout override
and a targeted `GST_DEBUG` scope with its own debug-log file — the two
caption/embed entries from an earlier draft of this experiment are
deliberately absent; see the caveat immediately above for why):

```powershell
pwsh -File sandbox-lab/Run-SandboxSoak.ps1 -Sha <full sha> -WorkerEnv "CIVICCAST_STALL_TIMEOUT_S=60;GST_DEBUG=concat:4,tee:4,appsink:4,mpegtsmux:4;GST_DEBUG_FILE=C:\CivicCastSoak\gst-debug.log"
```

`-Minutes` is **SOAK minutes**, not wall-clock minutes from launch: the
clock starts only once the station reports healthy AND all three channels
are configured/started/confirmed `ON_AIR` (`soak_start_utc`, recorded in
every rollup and in `VERDICT.json`) — not from process launch. Installing
alone measured 13m05s on the first real run, so a launch-relative deadline
would declare that a stall before the install even finished.

What it checks, end to end: silent install (`/S /D=...`) exits 0 within the
installer bound, the station reports `status=="healthy"` AND
`schema=="current"` at `/health` within the health bound (HTTP 200 alone is
liveness only — a degraded station on an unmigrated DB still answers 200),
firewall allow rules are authored for tsp/ffmpeg/ffprobe (Defender's
first-bind modal, same fix as Gate A's), first-admin setup succeeds, the
kit's `samples\*.mp4` clips upload as assets (pinned to a 30-second
schedule slot each — the deliberate rollover instrument, not a discovered
duration; the schedule is sized for the ON_AIR poll's own worst case, from
the actual COMMITTED item count, not the target), all three channels are
configured/started and polled until **every** channel (not just one) is
confirmed `ON_AIR` within a 12-minute bound before the soak clock starts —
a bound expiring while the remaining channel(s) are still visibly
progressing (a non-null state, never silent) is reported as `HARNESS_ERROR`
(a lane-sizing gap), never a product FAIL; only a channel that never
returns a single state row at all is a genuine FAIL. Then it polls for
`-Minutes` SOAK minutes: a full ~20-25s tsp probe per channel, run 3
channels per ~60-75s cycle, with an all-channel state sample taken
immediately before each channel's own probe (so state/pid is sampled
roughly 3 times per cycle, not once) into a 12-sample/~3-minute ring per
channel — close enough in practice to catch a restart shorter than the
worst-case ~75s cycle period, though it is NOT a literal independent 15s
timer.

**PASS** requires: every cycle after a 3-minute warm-up grace has all three
channels `ON_AIR` on an OS-process-verified GStreamer engine (never a
software fallback); a passing TSDuck (`tsp.exe`) egress probe on every
channel every cycle (no restart-window exception — a genuinely seamless
reload should not drop packets either; a tsp result of `not-run` or
`error:...` — the TOOL is missing or failed to launch — is `HARNESS_ERROR`
instead, since a broken probe proves nothing about the product); **zero
unplanned relaunches; zero planned restarts; zero reload aborts; and no
armed reload without a commit**. A worker pid change is classified
`planned_restart` if the channel's own sample ring shows `TRANSITIONING`
in the preceding 3 minutes and
`unplanned_relaunch` otherwise (a crash); while a planned restart is in
flight, the channel is temporarily excused from the ON_AIR check for
max(60s, 2x the measured cycle period) — a separate, more generous number
used only to avoid a false per-cycle outage while classification settles;
the final beta.5 verdict still fails any planned restart. `VERDICT.json`
reports `unplanned_relaunch_count`,
`planned_restart_count`, `max_restart_gap_seconds`, and the full
`restart_events` list; otherwise **FAIL**, naming the first failing
cycle/event and why. This classification logic lives in
`sandbox-lab/scripts/RestartClassifier.ps1` (dot-sourced by the in-sandbox driver and
its own unit tests, `Test-RestartClassifier.ps1`) — extracted the same way
`SoakVerdict.ps1`/`HostLiveness.ps1` already were, after an inline version
had a parameter accidentally named `$Pid` (PowerShell's read-only `$PID`
automatic variable), which silently no-opped every ring write with no
crash and no product/host visible signal until this review caught it.

TSDuck's current JSON stores packet totals and sync/transport errors under
`ts.packets`; the sampler rejects missing or malformed nested counters rather
than treating them as zero. It reports discontinuities as the explicit sum of
each PID row's `packets.discontinuities` counter, not as an invented
TS-level or PCR-only field. A timed-out `tsp` probe remains a failed probe;
this parsing rule does not turn missing analysis into transport success.

Refuses to start (exit 3) if Windows Sandbox is already running (it's a
single-instance-per-machine resource shared with Gate A and other agents on
this box — `-DryRun` is exempt) and refuses (exit 2) if the kit's
`SHA256SUMS.txt` is missing or any listed file fails verification. Separate
stall bounds apply per phase — a boot bound (default 5 min: absence of any
main-thread file before this is normal, not staleness), installer (default
20 min from launch, `-InstallBoundMinutes`), station-healthy (default
10 min after install, `-HealthBoundMinutes`),
rollup-stall (default 6 min once the soak clock has started) — plus a
generic 15-minute main-thread quiet-liveness backstop (newest mtime among
`soak-log.txt`/`summary.json`/phase markers, classified via the shipper's
own heartbeat) that covers every phase, including setup (first-admin/
asset-upload/schedule/channel-start) which has no dedicated bound of its
own. A stall whose sandbox process(es) belong to this run (by recorded PID,
never by bare name) is killed (exit 4) and evidence preserved; one that
can't be positively attributed to this run touches nothing (exit 5,
`FOREIGN-SANDBOX-SESSION.txt`); a stale main-thread signal alongside a
stale/missing shipper heartbeat is `HOST-QUIET-SHARE.txt` (exit 6,
HARNESS_ERROR — no product conclusion, e.g. the guest itself also reports
HARNESS_ERROR when its own schedule-coverage sizing check fails).
`vmmemWindowsSandbox` cannot be stopped from an unelevated host (confirmed:
`Access is denied`) — the host waits up to 3 minutes for it to exit on its
own and reports it as lingering rather than retrying a kill that can only
fail.

`-InstallBoundMinutes` and `-HealthBoundMinutes` are positive bounded host
parameters (defaults 20 and 10, respectively). The host writes both into the
rendered Windows Sandbox LogonCommand; the guest receives the same values for
its independent watchdog and records the effective values in
`SOAK-START.json` and `VERDICT.json`. Thus a widened host install budget is
not silently narrowed back to the guest default.

Evidence lands under `soak-output/soak-<shortsha>-<UTC stamp>/` — a
separate per-run root from Gate A's own `output/` above (which
`Host-Launch-Sandbox-Test.ps1` wipes at the start of every Gate A run, so
sharing it would risk a soak run's evidence being deleted mid-run by a
concurrent Gate A run). Every run writes `summary.json`, per-cycle JSON
under `cycles/` (each channel row carries its own up-to-12-sample
`sample_ring`, the cycle's `measured_cycle_period_seconds`, and now the
per-channel `reload_committed_count`/`reload_aborted_count_worker`/
`reload_aborted_reasons_worker`/`worker_stall_count`/`worker_stall_stderr_count`
parsed from that channel's own `gst-worker.stdout.log`/`gst-worker.stderr.log`
(`worker_stall_count` counts the worker's own
`WORKER_RESULT {'error': ('stall', ...` exit record on stdout — **not**
unconditional: `engine.py`'s `stop(force_exit_on_hang=True)` teardown
backstop calls `os._exit(70)` directly when teardown itself hangs, which
skips the `WORKER_RESULT` print entirely, so a stall whose own teardown
hangs is invisible to this count. `worker_stall_stderr_count` covers that
blind spot: it counts the stall watchdog's own `CTRL stall: ...` stderr
line, written unconditionally before teardown is even attempted — see
`sandbox-lab/scripts/WorkerStdoutParser.ps1`'s header and its
`ConvertFrom-WorkerStderrLines`/`Update-WorkerStderrCounters`), plus a
cycle-wide `processes` array (per-pid `cpu_seconds_delta`/`working_set_mb`/
`role` for every python.exe/pythonw.exe/pythonservice.exe/ffmpeg.exe
process — `role` is one of `gst-worker:<channel_id>`, `gst-worker:unknown`,
`ffmpeg-fallback:<channel_id>` (a channel running the ffmpeg-fallback
engine, resolved via the same per-channel pid map a gst-worker pid uses —
fixed to actually resolve outside the gst-worker branch, since it
previously always fell through to `other` even when the channel mapping
was already in scope), `supervisor` (the `pythonservice.exe` Windows
service host), `control-plane` (the control-plane child), or `other`,
labeled from pid facts this lane already holds, never an extra process
query — see `sandbox-lab/scripts/CpuSampler.ps1`'s `Get-ProcessRoleLabel`) and `cpu_total_percent`;
`SOAK-START.json` additionally records the guest's `cpu_count` once),
`restart-events.json`, a rollup every 3 minutes under `rollups/` (each
carrying a `worker_stdout_cumulative_by_channel` snapshot, plus
`harness_notes_count` and `harness_notes_recent` — the last 3 entries only;
re-embedding the full, potentially-20-entry `harness_notes` list in every
single rollup was needless repetition of the same notes over a long soak,
so the full (deduped, capped) list lives only in the final `VERDICT.json`,
below), per-channel egress worker logs (`logs/<label>/egress-per-channel/
<channel>/`, plus a `prepared/` directory LISTING, never a copy) alongside
the daemon-level logs at each checkpoint and on every FAIL, and a final
`VERDICT.json` / `VERDICT.txt` (verdict one of `PASS`, `FAIL`,
`HARNESS_ERROR`) — on every beta.5 run, a channel the daemon log confirmed
armed a seamless content-reload for but whose worker stdout
never logged a commit for the whole soak is reported `FAIL` ("seamless
reload never committed"), a product finding, not a harness note (see
`sandbox-lab/scripts/WorkerStdoutParser.ps1` and `sandbox-lab/scripts/DaemonLogPatterns.ps1`'s
`$DaemonReloadArmedRegex`/`Get-ReloadArmedNeverCommittedChannels`/
`Invoke-FinalWorkerStdoutDrainAndComputeArmedNeverCommitted`). This check
(and `VERDICT.json`'s own `worker_stdout_by_channel` snapshot) reads from
one FINAL per-channel drain of every worker's stdout counters, taken right
after the poll loop exits — a reload committed in the last cycle's own
write window is always counted before this check runs, never missed by up
to one stale cycle; the drain-then-compute order is itself a tested
contract (`Invoke-FinalWorkerStdoutDrainAndComputeArmedNeverCommitted`,
exercised directly by `Test-RestartClassifier.ps1`'s scenario27e-g), not
just an ordering claim in a comment.

`VERDICT.json` also carries the full, deduped (one entry per distinct
message, capped at 20 via `Add-HarnessNote`) `harness_notes` array, and
`Run-SandboxSoak.ps1`'s own final console block prints those same notes
and appends them to the host's own copy of `VERDICT.txt` — on every
verdict-read path (whichever of `VERDICT.txt`-direct or the
`VERDICT.json`-fallback path actually produced the verdict), not only the
fallback one.

Three read paths (`RestartClassifier.ps1`'s planned-restart walk-back
exclusion, `SoakVerdict.ps1`'s ON_AIR-check exclusion and 3-consecutive-
failure escalation) used to each keep their own hand-typed
`-like 'state read failed*'` copy of the same "a channel row's own state
read failed" contract. `sandbox-lab/scripts/DaemonLogPatterns.ps1` now holds the one
shared predicate, `Test-IsReadFailureMarker` — the consumer side of
`New-StateReadFailureLastError`'s producer-side formula — and all three
call sites, plus `Test-SoakVerdict.ps1`'s own fixtures, use it instead of
re-typing the literal.

`sandbox-lab/scripts/ServiceStartFailureCheck.ps1`: when `Get-Service` itself throws
(rather than returning a normal not-running/stopped result), the event-log
crash check now falls back to the well-known constant service display
name (`CivicCast Native Supervisor`) instead of skipping the check
entirely — a genuine crash should still be caught even when `Get-Service`
happens to fail. That constant is a hand-duplicated copy of
`civiccast/native/supervisor/config.py`'s own `DISPLAY_NAME` (no import
path exists from PowerShell into that Python module); rather than leave a
comment telling readers to re-verify it by hand, `Test-ServiceStartFailure.ps1`
now reads `config.py`'s own `DISPLAY_NAME` line via regex and asserts it
matches the PowerShell constant, so a rename in either file without the
other fails the lane's unit tests instead of silently drifting.

`Run-SandboxSoak.ps1`'s `awaiting-soak-start` phase no longer flips to
`running` on `SOAK-START.json`'s mere existence: `In-Sandbox-Soak.ps1`'s
own harness-error path writes a *backstop* `SOAK-START.json`
(`harness_error_before_soak_start: true`, `soak_start_utc: null`) on a run
that failed BEFORE the real soak clock ever started, so downstream tooling
still finds a `SOAK-START.json` on every run. The host parses the
marker's own content — a backstop marker (or a still-partial write) is
reported as the harness error it actually is instead of arming a
rollup-stall bound on a run that will never produce a rollup. Round-
follow-up-C finding: the backstop marker and `VERDICT.txt`/`.json` ride the
SAME ~15s in-sandbox shipper tick, and `SOAK-START.json` sorts before
`VERDICT.txt` alphabetically in a robocopy tick — so a tick that lands
mid-write-sequence could ship the marker without yet shipping the verdict,
and killing the VM the instant the marker was seen could beat `VERDICT.txt`
to the share by seconds, leaving the operator with only
`HOST-QUIET-SHARE.txt` even though the real verdict had already been
written in the guest. `sandbox-lab/scripts/BackstopMarkerGrace.ps1`'s
`Wait-ForVerdictAfterBackstopMarker` now gives `VERDICT.txt` a bounded
grace window (default 45s = 3 shipper ticks, polled every 5s) before
falling back to the quiet-share exit; if the verdict arrives during the
grace window, the run takes the normal verdict path instead.

The verify/verdict logic lives in `sandbox-lab/scripts/SoakVerdict.ps1` (the per-cycle
PASS/FAIL/HARNESS_ERROR judgment), `sandbox-lab/scripts/RestartClassifier.ps1` (planned-
vs-unplanned restart classification and ring sampling),
`sandbox-lab/scripts/HostLiveness.ps1` (the host's stall/quiet-share classification),
and `sandbox-lab/scripts/BackstopMarkerGrace.ps1` (the host's backstop-marker grace-wait
decision) — each dot-sourced by both its real caller and its own
unit-test file, so the same code judges a real run and a synthetic
unit-test tuple. Run the unit tests with:

```powershell
pwsh -File sandbox-lab/scripts/Test-SoakVerdict.ps1
pwsh -File sandbox-lab/scripts/Test-RestartClassifier.ps1
pwsh -File sandbox-lab/scripts/Test-HostLiveness.ps1
pwsh -File sandbox-lab/scripts/Test-BackstopMarkerGrace.ps1
pwsh -File sandbox-lab/scripts/Test-ServiceStartFailure.ps1
pwsh -File sandbox-lab/scripts/Test-CaptionsOffCheck.ps1
pwsh -File sandbox-lab/scripts/Test-WorkerStdoutParser.ps1
pwsh -File sandbox-lab/scripts/Test-CpuSampler.ps1
pwsh -File sandbox-lab/scripts/Test-WorkerEnv.ps1
```

Or run every `Test-*.ps1` suite in one step with
`sandbox-lab/scripts/Invoke-LaneUnitTests.ps1` (discovers every `Test-*.ps1` directly
in `scripts/` dynamically, sorted by name, so a newly-added suite is
picked up automatically; runs each as a child `powershell.exe` process —
Windows PowerShell 5.1, the actual guest engine, not `pwsh` — and exits
non-zero if any suite fails):

```powershell
powershell.exe -NoProfile -File sandbox-lab/scripts/Invoke-LaneUnitTests.ps1
```

CI (`.github/workflows/ci-sandbox-lab.yml`, `windows-latest`, triggered on
pull requests touching `sandbox-lab/**`) runs `Invoke-LaneUnitTests.ps1`
under `powershell.exe -NoProfile` and parse-checks every `.ps1` file under
`sandbox-lab/` (`[System.Management.Automation.PSParser]::Tokenize`, no
execution) — fails the job on any suite failure or parse error.

`sandbox-lab/scripts/CaptionsOffCheck.ps1` (item 1's -CaptionsOff PUT/GET verification
judgment), `sandbox-lab/scripts/WorkerStdoutParser.ps1` (item 2's per-line matcher for
each channel's `gst-worker.stdout.log`/`gst-worker.stderr.log`), and
`sandbox-lab/scripts/CpuSampler.ps1` (item 3's per-pid CPU-delta/working-set math)
follow the same dot-sourced-and-unit-tested extraction pattern.

`sandbox-lab/scripts/WorkerEnv.ps1` (lane follow-up D's `-WorkerEnv` feature: string/
array parsing into NAME=VALUE entries, dedupe-by-name-later-wins, the
empty-value unset/removal semantic, merging entries into a service's
existing `Environment` `REG_MULTI_SZ`, and the `.wsb` LogonCommand quoting
round trip) follows the same pattern — dot-sourced by both
`Run-SandboxSoak.ps1` (host-side parse/render/round-trip) and
`In-Sandbox-Soak.ps1` (guest-side parse/merge/verify), unit-tested in
`Test-WorkerEnv.ps1`.

`sandbox-lab/scripts/CpuSampler.ps1`'s `Get-ProcessRoleLabel` checked the generic
python/pythonw catch-all (`'control-plane'`) BEFORE resolving the pid to a
channel via `PidToChannelId` — so a python-named process that DID resolve
to a channel (e.g. an ffmpeg-fallback engine launched via a python-named
process) was mislabeled `control-plane`, contradicting the function's own
docstring, which defines `control-plane` as a python process *not*
resolved to a channel. Round-follow-up-C finding, fixed by checking
channel resolution before the catch-all; `Test-CpuSampler.ps1` scenario
13c is the exact regression guard (`-ProcessName python -ProcessId 9001
-PidToChannelId @{9001='public'}` now returns `ffmpeg-fallback:public`
instead of `control-plane`).
