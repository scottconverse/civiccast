# REVIEW.md - what a reviewer checks in this repo

This file is the checklist for a code reviewer (a person or an AI) on CivicCast. Read it from `main`, never from the branch under review, so a pull request cannot change its own checklist.

How to use it:
- Read the diff first. Run a rule only when its **Trigger** matches the diff. Skip every other rule.
- Each rule has a **Check** (a question you answer by reading code) and a **Source** (the real incidents behind it).
- Mark each finding with one word: `Important` (breaks air, upgrade, data, security or a stated claim; fix before merge), `Nit` (small, optional), or `Pre-existing` (not caused by this diff; report it, do not block).
- CI guards already catch some things (version identity, ffmpeg wrapper, workflow job timeouts, pip-audit, test-count floors). Do not repeat them. Where a rule overlaps a guard, it says what the guard cannot see.
- Add a rule only when the same cause appears in two or more separate pull requests, commits or issues, and a reviewer can see the trigger in a diff. The owner approves the new rule in a pull request. Prune a rule when its code is gone or a guard catches it.
- Numbers in Source are commit short SHAs or `#` issue / pull request numbers. "(open)" means the pull request was not merged when this file was written.

## R1. Health and state report what was observed
**Trigger:** changes to `/health` or readiness in `civiccast/app.py`, `civiccast/alerting/runtime_status.py`, `civiccast/egress/health.py`, `civiccast/native/supervisor/`, `civiccast/egress/daemon.py` state writes (`FALLBACK_SLATE`, `_STALE_RECONCILE_STATES`), `civiccast/captions/tap_worker.py`, or the installer's wait for `/health`.
**Check:** (1) Does the "ready / healthy / on air" value come from something observed now (live pid, newest segment age, port owner, last input time), not from a row, a config or "a process answered"? (2) Walk three cases: a worker in a restart loop, a channel airing only the slate, and a foreign program on the port. Is the answer right in each, in both directions (no false healthy, no false degraded)? (3) If the state is read from a file another process rewrites, is a failed read tried again before it is reported, so that one failed read does not show as degraded?
**Why:** `/health` said `healthy` while a channel never reached air (restart loop, dead slate, frozen HLS), and said `degraded` while nothing was wrong (programme change, slate without speech, a foreign Ollama counted as ready).
**Source:** ADR 0025; #324; #327; d74a8c31 (#338); adeb26ba (#313); ba8a5846; a3bb99a4; #358 and #359 (open).

## R2. Data saved by an older build still works after upgrade
**Trigger:** a change that removes, renames, narrows or re-defaults a saved field, enum value or accepted config value: models with `extra="forbid"`, `canonical_profile`, station-state, provision or upgrade journals, `PUT .../config` validation, new defaults, Alembic migrations.
**Check:** Is there a read-time or start-time step that handles what a previous release wrote (ignore the old key, repair the old value once, migrate the default)? Is there a fixture of the old on-disk shape in `tests/`? Does a fresh install avoid inheriting leftover state from an older install?
**Why:** Upgraded stations halted at provisioning on a removed field, or stayed dark on a codec the new code refuses; a "clean" install came up as an old station.
**Source:** 7068ac18 (removed fields in 85ffe6c0); 6a547329 (#328, issue #327 part 2); 32d6e89c (the fresh-install case).

## R3. Files shared between processes on Windows
**Trigger:** code that replaces, deletes or reads a file another process also touches: `os.replace`, `Path.replace`, `File.Replace`, `open()` of `active.vtt`, `program-boundary.json`, `runtime-status.json`, `station-state.json`, `transaction.clixml`, caption sidecars, logs.
**Check:** (1) Does the writer retry a sharing denial (`PermissionError`, `IOException`) for longer than the readers retry, and do readers open with delete sharing or retry? (2) If the write still fails, is the content kept for the next write, and does the error stay out of hot paths (reload commit, scan pass)?
**Why:** Windows refuses to rename over an open file. This lost caption updates, stalled a programme hand-over for 15 s, rolled back a good install at Commit, and made health blink.
**Source:** 54eb629d; fa722e8c; adeb26ba (#313); d74a8c31 (#338); #342 and #357 (open, one incident).

## R4. Every wait has a limit shorter than its watcher
**Trigger:** new or changed network calls (`Invoke-WebRequest`, `requests`/`httpx`, Ollama calls), `subprocess` without `timeout=`, installer or uninstaller prompts (NSIS `MessageBox`), startup probes, and build or workflow steps that download.
**Check:** (1) Is there an explicit timeout, and is it shorter than the supervisor's no-progress window (60 s for startup readiness)? (2) Does a silent install or uninstall pass `/SD` and never wait for a person? (3) Does a timeout end in a degraded or failed state, never in a blocked startup or an endless retry? CI's `check_workflow_timeouts.py` covers job ceilings and artifact upload and download steps only, not another download in a step or a runtime call.
**Why:** A silent listener on port 11434 rolled back setup; a download with no data held a candidate build two hours; a silent uninstall waited 30 minutes on a prompt.
**Source:** a3bb99a4; 0b3c8a62; 0d3258e7.

## R5. Child processes and pids: prove who owns them, clean up on exit
**Trigger:** code that spawns, signals, trusts or kills a process, or decides by a pid or a listening port: `civiccast/native/supervisor/`, `civiccast/platform/child_containment.py`, `civiccast/egress/process_identity.py`, `civiccast/egress/relay_reclaim.py`, `civiccast/egress/daemon.py`, `civiccast/egress/hls_relay.py`.
**Check:** (1) Is a saved pid or a port owner checked as ours (age against the state row, parent chain), not only "something holds it"? (2) When the parent dies or is killed, do its children die (own kill-on-close job)?
**Why:** A reused pid kept a dead channel "alive"; a foreign Ollama was treated as ours; orphaned ffmpeg children held UDP ports and kept three channels off air for 11 minutes.
**Source:** 1d4eae14; ba8a5846; 522dad8d and 9d4ff108 (one outage).

## R6. GStreamer playout: reload, abort and API return contracts
**Trigger:** `civiccast/egress/gst/engine.py`, `bridge.py`, `worker.py`, `reload_policy.py`, and the reload code in `civiccast/egress/daemon.py` and `automation.py`.
**Check:** (1) On commit and on abort, does the order of hold-probe release and selector pad switch match the order that the `_commit_reload` docstring in `engine.py` states? A change of that order needs a real-pipeline run as evidence. (2) Does every failure after the incoming leg is staged reach the abort path (including a `False` from `sync_state_with_parent`)? (3) Are element and file names unique per leg and per plan? (4) Is the reload command acked only after the commit, and does every exit (commit, decline, late settle) leave the channel out of `TRANSITIONING`? (5) Does the test use the real gst-python contract (`Bin.add` returns `None` and raises `Gst.AddError`), not a fake that assumes a bool?
**Why:** Reload wedged workers at commit, collided names left a new leg unlinked, a refused state change did not abort, and a "fixed" check broke every first build because a fake modelled the wrong return value.
**Source:** 5978ed5f; 250026b9; bcb3ebe3; 2a880a4e; a5122f08; #344 / #356 (open).

## R7. A test must not depend on another test, a fixed port or the clock
**Trigger:** new or changed tests under `tests/` that start a thread, worker or subprocess, bind a TCP/UDP port, change module or global state, or assert on elapsed seconds.
**Check:** Does the test stop and join what it starts, and take a free port from the OS? Does it wait on an event or a held condition, not on a fixed window that a loaded runner can miss? Would it pass under `pytest -p randomly` with any seed? A leaked PostgreSQL connection is not for this rule: a fixture in `tests/conftest.py` already fails that test.
**Why:** Leaked caption threads failed unrelated tests in random order; fixed ports and short wall-clock windows failed on loaded runners with correct code.
**Source:** #330; 9dfb13e4; 50734fe2; f85ecfef; 13873bdf.

## R8. Claims in prose and comments match the evidence
**Trigger:** an added line in `CHANGELOG.md`, `docs/releases/`, `docs/ops/`, the manuals or `README.md`, or an added code comment or docstring, that states a number with a unit or an absolute word ("never", "cannot", "always", "proven", "guaranteed").
**Check:** Does the text name the evidence (test, run id, receipt) and does that evidence show exactly this, no more? Is a result that was measured on one station worded as that, not as a general rule? Is a "proven" limited to what was proven (output path vs full acceptance)? The claims check (`scripts/policy/check_claims_evidence.py`) covers only claims registered in `docs/claims/claims.yaml`.
**Why:** Repeated corrections of statements the evidence contradicted: an escalation that "never" happened, a mechanism described wrongly, a verdict titled "acceptance proven" that was not.
**Source:** 2a34d0e3; 907df5c0; 9cce9012; 1fe68ae3; b6fd794b; a693767d.

## R9. Flipping the published release updates every surface
**Trigger:** a change to `docs/releases/release-truth.yaml` or `civiccast/_version.py`.
**Check:** Is the old version string gone from the pages that `scripts/policy/check_release_identity.py` does not read: `SUPPORT.md`, `ARCHITECTURE.md`, `INSTALL-WINDOWS.md`, `docs/install-windows.html`, `docs/QUICKSTART-OPERATOR.md`, `docs/install/windows-release-trust.md`, `scripts/download_windows_release_artifacts.ps1`? If the commit message has `[skip ci]`, was the identity check run by hand? CI does not run it for that commit.
**Why:** A publish flip left other files on the old version; the identity check then failed every later pull request.
**Source:** f5f5b5c5; #346 (fixed in #349, open).

## R10. Windows file rights (ACLs) on a directory in use
**Trigger:** `civiccast/native/install_service_identity.py`, `civiccast/native/pgdata_acl.py`, any `icacls` or DACL call on a directory.
**Check:** Does the grant request the rights it needs explicitly, and does it survive a directory that another process holds open (sharing violation)?
**Why:** Clean installs failed on a held directory and on a mapped folder.
**Source:** 9a69db59; e56ba1ae.

## R11. Credentials stay out of responses, journals and diagnostics
**Trigger:** API routes under `civiccast/installer/`, `civiccast/platform/station_router.py`, journal writers in `civiccast/native/upgrade/` and `civiccast/native/provision/`, installer failure-diagnostics copies, log lines that include a URL or token.
**Check:** Can the database URL, a password, a token or a recovery code reach a response body, a journal file, a diagnostics copy or a log? Is the value re-read from config when needed, not from the redacted copy? Is the route authenticated after setup is complete?
**Why:** An unauthenticated setup route returned the live database URL; the upgrade journal and diagnostics kept it in plain text.
**Source:** 184398d9; d74a8c31 (#338).

## R12. AI model choice by memory follows one rule everywhere
**Trigger:** `civiccast/summary/ollama.py` (the RAM check), `civiccast/ai_models/models.py` (`detect_summary_model_default`), `civiccast/ai_models/catalog.py` (`min_ram_gb`), the installer AI self-test in `civiccast/apps/installer/src-tauri/src/main.rs`.
**Check:** Is the RAM and GPU rule the same in the Python default, the RAM check and the Rust self-test? Does it hold for the station shapes in `docs/evidence/rc6-soak-2026-11-15/SOAK-REPORT.md`? Does a deferred tier fall back to a smaller one and say so, and does the installer test only models the station will run?
**Why:** Two rc.6 field failures: summaries always deferred (HTTP 503), and an install stopped by a self-test of a model the station never runs.
**Source:** 843490a3 (#335); 3d9a0f53 (#336); d74a8c31 (#338).

## R13. Wall-clock time uses the station time zone
**Trigger:** code that prints, converts, schedules or stores a time a person reads: on-air clock and CG boards (`civiccast/egress/board_compositor.py`, `bulletin_filler.py`), schedule, recurrence and dayparts, requested air dates, `datetime.now(UTC)` near a display, `"local"` zone handling, `civiccast/timezones.py`.
**Check:** Is the zone for this value chosen on purpose (`CIVICCAST_STATION_TZ`, saved profile, or the machine zone for `local`), and not UTC or the browser zone by accident? If the code maps `local` to UTC, is that the stated design for this path and not a display? Is there a test with a named non-UTC zone, and one for `local`?
**Why:** Dayparts ran on UTC, requested dates lost their zone, and the on-air clock showed 04:04 at 21:04.
**Source:** c0168724; a517d9c0; #347 (fixed in #349, open).

## Tests
The flaky-test rule applies to every diff that touches `tests/` or a `*.test.tsx` file. R7 has the checks for waits and ports.
- Flag any added skip, `time.sleep` used as a wait, or retry-until-pass loop. CI floor guards already reject a skip in the native suites; this rule covers the other suites too.
- A test whose root cause cannot be found is deleted, not skipped or retried. The reason goes in `TEST-REMOVALS.md` at the repo root (test name, date, what was tried, the seed or run link). Make the file with the first entry.
- A bug fix needs a test that fails on the old code. Say so in the pull request, as #337 does.
