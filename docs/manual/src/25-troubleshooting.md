# Troubleshooting matrix {#ch-troubleshooting}

## RC2 corrections from first-install feedback

- **Installation stops while Ollama removes `manifests-v2`:** the restricted service needs modify access to its model data, while installed executables stay read-only. RC2 setup grants that access to the Ollama model store and legacy local-model data only. Do not make all of Program Files writable.
- **A short clip cannot be scheduled:** enter minutes and seconds separately; a 38-second clip uses 0 minutes and 38 seconds. Publish the item separately before expecting it in the resident guide or on air.
- **Watch folder rejected:** read the persistent explanation beside the form. The service account needs read/traverse access to the named folder. Your Windows account being able to browse it is insufficient.
- **Minutes generation refused:** review and approve the recording's caption cues first. The minutes panel displays the reason and an **Open caption review** link. This prerequisite is not bypassed.
- **HLS health disagrees with moving video:** GStreamer now reports measured encoded FPS and output bitrate from its current worker. A missing reading remains unknown; a stalled interval reports zero. This encoder reading alone is not proof of a working resident connection or captions.
- **Captions disappear:** check caption diagnostics for discarded input, timeouts and fallback. A queue depth of zero does not prove no audio was lost. RC2 reduces overlapping Whistle work; verify generated captions in the actual channel output, not only a transcript or sidecar. Bounded development checks are not a sustained-operation guarantee.


This chapter is for the person who looks after the CivicCast station computer: usually the one or two IT staff at a small city. It explains how to identify a problem from a screen, log line or command result and what to do. It is organised by area. Each area is a table with four columns: the **symptom** you see, the **likely cause**, **how to confirm** it, and the **fix**.

## Before you start

- **Who can do what.** Reading logs and running the commands below needs an Administrator account on the station computer; the service runs as the restricted virtual account `NT SERVICE\CivicCastSupervisor`. Console buttons mentioned in the matrix need a role: **Support admin** for the Readiness support bundle; **Setup admin or Support admin** for "Repair GStreamer runtime & restore full egress"; **Setup admin** for settings and emergency-alert feeds; **Meeting operator** to start and stop channels and to retry a recording's finalization; **Publish operator** for the Publish screen. The roles are listed in [Appendix: roles](#app-roles).
- **Open PowerShell as Administrator** before running any command in this chapter. Commands are shown for PowerShell.
- **Times.** Log lines use the station computer's clock and have no zone marker. Schedule, Program Guide and new recording entries use the configured station time zone; older weekly recording schedules can retain their UTC timing. See [Before the meeting](#ch-before-meeting) before comparing a log with a planned time.
- **Changing an environment variable.** Many settings in this chapter are environment variables. Set them in the service's `Environment` registry value as described in [Running it day to day](#ch-operations), then restart the service with `Restart-Service CivicCastSupervisor` between programs and check the log lines named in each row.
- **Restarting the service interrupts the air.** `Restart-Service CivicCastSupervisor` stops every channel. Do it between programs, or on a channel nobody is watching.
- **Local setup, remote staff access.** First setup and first-admin recovery are local-only. Staff can sign in remotely through the configured staff hostname from an approved network. See [Signing in](#ch-signing-in).

## Where things are logged

Uninstall leaves `C:\ProgramData\CivicCast` in place. Setup uses a durable recovery record and a temporary copy in `install-transaction` for the specific files and station state it may change; successful commit or restoration removes that copy. If recovery fails, keep the record and backup in place.

| What you want | File or place | Notes |
|---|---|---|
| Did the Windows service start, restart or give up? | `C:\ProgramData\CivicCast\logs\supervisor.log` | The service itself. Rotates at 10 MiB, keeps 10 old files, and is written to disk on every record. |
| The application's own log (channels, captions, publishing, recording) | `C:\ProgramData\CivicCast\logs\control_plane-app.log` | Most of the log lines quoted in this chapter are here. Old files are named `control_plane-app.log.1` and up. |
| Raw output of the web server process | `C:\ProgramData\CivicCast\logs\control_plane.log` | Crashes before the application logger starts land here. |
| The database server | `C:\ProgramData\CivicCast\logs\postgres.log` | Contains startup and server output; rotates at 10 MiB and keeps 10 older files. |
| Why the supervisor stopped trying to start | `C:\ProgramData\CivicCast\STATION-START-FAILED.md` | Written after 3 failed starts in a row with the same error. Removed after the next successful start. |
| What setup did, step by step | `C:\ProgramData\CivicCast\install-progress.log` | Read it from the bottom. Find the last "begin" with no matching "returned". |
| Automatic setup restoration | `C:\ProgramData\CivicCast\install-transaction\` (`transaction.clixml`, temporary `backup\`, and `recovery-error.txt` when recovery reports an error) | Do not delete these while setup recovery is incomplete. Exit 136 means recovery could not be prepared or completed. |
| Which pack was missing or untrusted | `C:\ProgramData\CivicCast\install-manifest-report-<pid>-<time>.json` | One per setup run. |
| Upgrade, rollback, provisioning | `C:\ProgramData\CivicCast\upgrade\` (`upgrade-engine.log`, `upgrade-journal.json`, `UPGRADE-RECOVERY.md`) and `provision\` (`PROVISION-RECOVERY.md`, `OWNERSHIP-RECOVERY.md`, `ownership-observation.txt`) | |
| One channel's encoder and relays | `C:\ProgramData\CivicCast\data\egress\<channel>\logs\` (`gst-worker.stdout.log`, `gst-worker.stderr.log`, `hls-relay.<sink label>.stderr.log`) | On the default GStreamer engine the worker logs are named `gst-worker.*`. On the FFmpeg fallback engine they are `ffmpeg.stdout.log` and `ffmpeg.stderr.log`. |
| First-run wizard | `%USERPROFILE%\.civiccast\runtime-host.log` and `installer-state.json` | Per Windows user, not per station. |
| Windows' own record of the service | Event Viewer, Windows Logs, Application, source `CivicCastSupervisor` | Setup exit 126 sends you here. |

Two commands you will use constantly:

```powershell
Get-Content C:\ProgramData\CivicCast\logs\control_plane-app.log -Tail 200
Select-String -Path C:\ProgramData\CivicCast\logs\control_plane-app.log* -Pattern "Caption tap"
```

And one to ask the running station how it is:

```powershell
curl.exe http://127.0.0.1:8000/health
```

The answer is a short JSON document with `status` (`healthy` or `degraded`), `version`, `schema` (`current`, `behind`, `not-configured` or `unknown`), `live_captions`, `schema_db_revision`, `schema_expected_head` and `mode`. `status` is `healthy` only when the schema is current and live captions are neither degraded nor unknown. Disabled captions or no active channel do not themselves make readiness degraded. For caption failures, open authenticated System Health for channel details. The HTTP status is 200 whenever the web server process is alive, so read the words in the body, not the number.

## Install and activation (setup exit codes)

Setup returns a number when it fails. Interactive setup shows a dialog with words but not the number; the silent run (`/S`) shows only the number. The full table is in [Reference tables](#reference-tables-ch14). On a failure, setup first puts the service, if it exists, into "stopped, manual start", so a failed install does not leave a half-built station running. Running setup again is the supported recovery.

| Symptom | Likely cause | How to confirm | Fix |
|---|---|---|---|
| Setup stops at exit 110, or a dialog names `.ccpack` files | A required pack is missing or not trusted. A first install needs the `packs` and `station` folders beside `setup.exe`. | `install-progress.log` contains a `step stage-packs: child reported:` line naming the pack; the JSON `install-manifest-report-*.json` has the detail. | Put the full kit (the `packs` and `station` folders, with the files named in the log) beside `setup.exe` and run setup again. |
| Setup stops at exit 111, 112, 121 or 122 | After unpacking, the server tools, the program files, FFmpeg or Ollama do not match their signed list. The copy of the kit is damaged. | The last "begin" in `install-progress.log` is a verify step. | Replace your copy of the kit from the original download and run setup again. |
| Setup stops at exit 123 | Activation failed. The child code says which part: 64 arguments, 65 could not write the report, 66 pack missing or untrusted, 67 activation or self-test failed, 78 the embedded trust key was refused. | `install-progress.log` ends with `step d4-activate-station: returned 67` (or the other child code). | See the next two rows for exit 67. For 66 see exit 110. |
| Exit 123 child 67 on a computer that is short of disk | The same code is used for "not enough free disk space" and for unpacking failures. Activation needs the total size of the packs plus 2 GB of headroom. | The setup details pane shows "Not enough free disk space to activate this station..." | Free disk space and run setup again. |
| Exit 123 child 67 with enough disk | A self-test check failed. The checks include: the Python core imports; PostgreSQL, FFmpeg and Ollama report their pinned versions; TSDuck, if present; a CPU caption test on a sample file (300 second limit); and the three AI models answering (wait limit 300 seconds). | The failing check is named in the setup details pane while setup is open. | Keep the details pane text and the log, then run setup again. If it fails twice for the same reason, collect the items under "What to collect before asking for help". |
| Exit 116 or 117 | PostgreSQL could not be provisioned. | `provision\PROVISION-RECOVERY.md` and the end of `install-progress.log`. | Follow `PROVISION-RECOVERY.md`; run setup again. |
| Exit 127 | Setup cannot tell which CivicCast runtime owns this machine. | `provision\OWNERSHIP-RECOVERY.md`. | If there is no older WSL product, set `HKLM\SOFTWARE\CivicCast\ActiveRuntime` to the text `native`, then run setup again. |
| Exit 135 | The older WSL edition of CivicCast is still registered. | The dialog says so. | Uninstall it, or run its `cutover-to-native`, then run setup again. |
| Exit 118, 119 or 120 | Service registration failed (118), the firewall rule could not be made (119), or the old service could not be stopped before install (120). | `install-progress.log`. The dialog for 118 and 119 names no log path. | Run setup again as Administrator. For 120, stop the service in `services.msc` first. |
| Exit 125 | The service started but the station did not become ready to serve. The dialog names the database schema as the usual cause. | `install-progress.log` and `upgrade\upgrade-engine.log`; then `logs\control_plane.log`. | Fix the cause the logs name, then run setup again. Your data is untouched. |
| Exit 126 | The service is registered but Windows could not start it. | Event Viewer, Application log; `logs\`. | Fix the start-up error it names, then run setup again. |

> **Known issue (beta.11):** The exit-67 dialog says the failing check is named in `install-progress.log`, but the log contains only the activation return code. The child's detailed error appears in the setup details pane. If the setup window has closed, the failing check is lost; capture the details pane before closing it.

> **Known issue (beta.11):** Exit 67 also covers insufficient disk space and extraction errors, although the dialog says "NOT a missing-files problem". Check free disk space and the details pane before you chase a self-test.

> **Known issue (beta.11):** Interactive failure dialogs explain the step but do not show its numeric exit code. Read `install-progress.log` for the code; the installer table in [Chapter 10](#ch-installing) lists the current codes.

> **Historical Beta 10 observation:** A clean-install run on an earlier package observed the first playout packets more than 60 seconds after the first start, while the channel reported its fallback slate as connected. That timing does not predict startup on later packages. Use `/health`, the channel's output and the verification record matching the installed package.

## Sign-in

The **First Setup** screen (`#/setup`) is for creating the first administrator and for that administrator to sign in or recover access. Staff use the separate sign-in page at `/operator/#/login`. When Caddy remote access is configured, staff can sign in through the staff hostname from an approved CIDR; first-admin setup and sign-in remain local to the station.

| Symptom | Likely cause | How to confirm | Fix |
|---|---|---|---|
| "Invalid admin username or password." | Wrong username or password. | The red alert on the card. | Use the username and password from the recovery kit. If the password is lost, use **Use recovery code**: it consumes one of the 8 one-time codes and sets a new password of 12 or more characters. |
| "Invalid staff username or password." | The username or password is wrong, or the account is disabled. | The alert on the Staff sign-in page. | Check the credentials. Ask a setup administrator to confirm the account is enabled or reset its password in **Staff accounts**. |
| "Too many staff sign-in attempts. Wait and try again." | Staff sign-in is rate-limited after repeated failed attempts. | HTTP 429 and the message on the Staff sign-in page; the response includes `Retry-After`. | Wait, then try again with the correct credentials. |
| "Too many sign-in attempts from this station. Wait N seconds, then try again..." | The setup sign-in limiter is cooling down. | The text names the wait. | Wait the number of seconds shown, then use the correct password or a recovery code. |
| "Too many failed attempts to authenticate with the staff API. Wait N seconds, then try again." | 10 bad tokens in 60 seconds from one client address. A request with no token at all does not count. | The text (the console writes it itself), and an HTTP 429 from the API. The API's own `detail` is "Too many failed staff authentication attempts. Wait and retry." with a `Retry-After` header in seconds. | Wait. The limit is set by `CIVICCAST_AUTH_RATE_LIMIT` (default 10) and `CIVICCAST_AUTH_RATE_LIMIT_WINDOW_SECONDS` (default 60). |
| The card "First setup can only be done from the station computer itself" | You opened the console from another computer, or through a remote-viewer that is not running on the station. | The page is HTTP 403. | Sign in on the station computer itself, or in a remote-desktop session on it. The sign-in and recovery routes accept only loopback requests. |
| Notice "You were signed out", or "This browser's console sign-in is no longer valid" | The station rejected the token (HTTP 401). Most often this browser's session was the oldest and was dropped because the station keeps only 20 console sessions. | The notice appears after a screen change. | Sign in again. Nothing is wrong with the station. |
| A screen says "This action requires one of these CivicCast roles: ..." | The token lacks a role (HTTP 403). | The text lists role ids such as `meeting_operator`. | Sign in as someone who holds the role. A Setup admin can assign the required roles in **Setup > Staff accounts**. |
| The browser shows its own error page for `http://127.0.0.1:8000/operator/` | The service is not running, or this is not the station computer. | `Get-Service CivicCastSupervisor` | See "Station will not start". |

> **Password recovery:** If the password and all eight recovery codes are lost, use the offline local-administrator `civiccast admin reset-password` procedure in [Security and privacy](#ch-security). Stop the supervisor first as instructed there. Do not use `CIVICCAST_ALLOW_FIRST_ADMIN_RESET` for password recovery: it reruns first setup and can replace station settings.

## Station will not start

The service is `CivicCastSupervisor` ("CivicCast Native Supervisor"). It runs as the restricted virtual account `NT SERVICE\CivicCastSupervisor`, starts automatically, and Windows restarts it after 5, 10 and 30 seconds if it stops. It starts two programs of its own, PostgreSQL and then the control plane (the web server and station engine), plus an optional Ollama AI engine on `127.0.0.1:11434`. The control plane gets 180 seconds to become ready, PostgreSQL gets 60 seconds, and a stop gives each child 15 seconds before it is ended.

| Symptom | Likely cause | How to confirm | Fix |
|---|---|---|---|
| `Get-Service CivicCastSupervisor` says Stopped, and the console will not load | The supervisor gave up. After 3 failed starts with the same error it writes a marker file. | `C:\ProgramData\CivicCast\STATION-START-FAILED.md` exists. In `supervisor.log`: `station could not start: <ErrorType>: <detail>` and `consecutive failed start count is now N`. | Read the reason in the marker file. Fix it, then `Start-Service CivicCastSupervisor`. The marker is removed after a successful start. |
| The marker file's reason is "Native station activation self-test receipt does not match this distribution" | `station-set.json` or `activation-self-test.json` in the install folder is missing, stale or from another build. The service will not start without them. | The reason text. | Run setup again so activation re-creates them. Do not edit either file by hand. |
| `supervisor.log` says `singleton not acquired (<status>): <detail> -- another supervisor owns the station` | A second copy of the supervisor is running. | The line itself. | Stop the other copy. Check `Get-Service` and Task Manager for a second `CivicCast` process. |
| State is `blocked_wsl_active` or `blocked_probe_unavailable` | The older WSL edition is active, or the check for it could not run. | `supervisor.log`. | Remove or cut over the WSL edition (see exit 135 above). |
| `curl.exe http://127.0.0.1:8000/health` returns `"status":"degraded"` | Database readiness or live-caption processing needs attention; captions can degrade readiness even with a current schema. | Read `schema` and `live_captions`; authenticated System Health shows channel details. | For `behind`, see "Upgrade problems"; for schema `unknown` or `not-configured`, see "Database problems". For caption `unknown` or `degraded`, inspect the affected enabled on-air channel's worker activity and fallback state. This probe does not certify every output or device. |
| `supervisor.log`: `ffmpeg/ffprobe not staged at ... (degraded media handling, ...)` | FFmpeg is missing from the install folder. The station runs, but media preparation is degraded. | The line. | Run setup again to restore the files. |
| `supervisor.log`: `ollama child skipped (degraded AI, service healthy): ...` | The AI engine could not start. Summaries and translation are unavailable; the station is otherwise healthy. | The line. Port 11434 belongs to CivicCast's own Ollama. | Check that another program is not holding `127.0.0.1:11434`. Run setup again if the files are damaged. |
| `supervisor.log` (level ERROR): `... degrading egress to the FFmpeg concat engine (CIVICCAST_EGRESS_ENGINE=ffmpeg-concat) so the channel keeps airing.` | The GStreamer files are damaged. The station tried an in-place self-repair, it did not restore them, and the station switched to the older FFmpeg concat engine. | The line. | On **Readiness**, press **Repair GStreamer runtime & restore full egress** (Setup admin or Support admin). If that fails, run setup again. |

> **Warning:** Do not hand-edit `station-set.json`, `activation-self-test.json` or `STATION-START-FAILED.md`. Re-running setup rebuilds them.

## Channels will not start, or sit on the slate

First, the words. The console shows an egress state in plain words: **Stopped**, **Starting**, **On air**, **Changing source**, **Showing slate**, **Finishing current item**, **Stopping** and **Needs attention**. They correspond to the internal states STOPPED, STARTING, ON_AIR, TRANSITIONING, FALLBACK_SLATE, DRAINING, STOPPING and ERROR. The log line that records every change is:

```
channel <id>: egress state -> <STATE> (source=..., pid=..., last_error=...)
```

The `last_error` part is the reason. Search the application log for it: `Select-String -Path C:\ProgramData\CivicCast\logs\control_plane-app.log* -Pattern "egress state ->"`.

> **Note:** A Beta 15 field run found that some worker labels and process/PID status did not agree with fresh process evidence. The reason was not established; use both the station logs and Windows process state when investigating, and do not treat this as a confirmed Beta 16 defect. See the [Beta 17 deferred follow-up](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/beta17-deferred.md).

A channel that shows **Showing slate** is on air with the "technical difficulties" picture instead of its program. The station chose that rather than dead air. It is a symptom, not a fault in itself. The `last_error` tells you which of these it is:

| `last_error` text (start of it) | What it means | Fix |
|---|---|---|
| "Preparing the scheduled program; airing the fallback slate so the channel stays up while it conforms." | The next program is not yet in a form that can air (it is being conformed: re-encoded to the station's standard picture and sound). A cold file can take minutes. | Wait. If it happens every time, look at "Program change problems". |
| "No valid source plan is available; generated fallback slate." | Nothing is scheduled and nothing else is available to air. | Schedule something, or check **Auto-schedule** and the Program Guide. |
| "Scheduled source plan expired during preparation; aired fallback slate while automation resolves the current schedule." | The preparation took longer than the item's slot. | Automation resolves it on its own. If frequent, see the preparation times below. |
| "Live source failed repeatedly; aired fallback slate instead of an infinite crash-loop." | The real source failed 5 times in a row without staying healthy for 60 seconds. Automation tries the real source again about every 30 seconds. | Look at `gst-worker.stderr.log` for the source's own error. Fix the source (network stream, file, capture device). |
| "caption storage refused: ..." | The channel could not store caption data, usually a full disk or a permissions problem. | See "Disk full". |
| "egress encoder unavailable; aired fallback slate: ..." | No video encoder could start. See "GPU and encoder problems". | See that section. |

| Symptom | Likely cause | How to confirm | Fix |
|---|---|---|---|
| **Needs attention** (ERROR) | The start failed and even the slate could not be encoded, or the configuration is invalid or a secret could not be resolved. With no FFmpeg at all there is no path to air anything. | `egress state -> ERROR ... last_error=...` in the application log. | Fix the named cause, then press **Start** again. |
| The channel stays **Starting** and then restarts | The engine produced no output. The worker quits itself so the daemon can restart it. | `gst-worker.stderr.log`: `CTRL first-output: no output within 45s of PLAYING - quitting for daemon restart`. The limit is `CIVICCAST_GST_FIRST_OUTPUT_TIMEOUT_S`, clamped to between 10 and 120 seconds. | Check the source and encoder rows. If it is only the first start on a new install, wait: see the Known issue above on slow first packets. |
| On air, then silently stops sending | A running worker stopped producing output. | `gst-worker.stderr.log`: `CTRL stall: no output for 10s - quitting for daemon restart`. Application log: `channel <id>: worker exited (exit_code=..., state=..., desired=..., pending_reload=..., deliberate_kill=...)`. | The daemon restarts the worker itself. Look for repeated exits. |
| The picture is frozen but the audio goes on | Video-only freeze. | `gst-worker.stderr.log` lines of the form `CTRL output: <total> buffers (+delta) since PLAYING [mux-in 5.0s: video=+0 audio=+235]`: `video=+0` with audio climbing. | The channel heals the web stream first. After that, see the next row. |
| Log level CRITICAL: `Freeze escalation budget exhausted ... moving to the fallback slate for a bounded cooldown before one program retry` | The HLS stream stayed frozen after relay recovery and the hourly restart budget is spent. | Channel state, recovery slate and the CRITICAL log. | CivicCast requests fallback slate unless an operator override is active, then makes one programme retry after the cooldown ends one hour after the latest budgeted restart. Check output and logs; investigate if recovery cannot proceed or the retry fails. Do not assume the warning itself means a manual restart is required. |

## Program change problems

A program change is the moment a channel moves from one scheduled item to the next. CivicCast tries to do it without a break: it prepares the next item ahead of time and swaps the source at the boundary (a "seamless reload"). If that fails it retries, and then falls back to a restart or to the slate. In the 8-hour run on build C16, 50 program changes happened with no holes. Those numbers come from an earlier internal build, not the published files.

| Symptom | Likely cause | How to confirm | Fix |
|---|---|---|---|
| A change was late by a few seconds, and the log shows a retry | A first-attempt abort at a very short tail boundary. | `Boundary reload for <channel> did not land (...); re-resolving and re-arming (retry N of 2) ...` | None needed. In the lab it healed in about 3 seconds. After the 2 retries are spent: `...retries were spent; arming the fallback slate at the boundary...` |
| The channel restarted at a change instead of swapping | The seamless swap did not land. | `Seamless content-reload for <channel> did not land (...); falling back to restart.` | Look at the `(...)` reason. A restart means a short interruption, not a failure. |
| The slate came up for several minutes at a change | Preparation of the next item was cold and slow. | `Content-reload source preparation for <channel> took N.Ns for N segment(s)`, or `...preparation FAILED ... falling back to restart.` In the lab the longest preparation times were 449 seconds (C15) and 389 seconds (C16). | A 3 to 5 minute slate after a short plan followed by a long item was seen on an earlier internal build, and a look-ahead warm-up was added afterwards. Make sure the next items are in the conform cache (see "Loudness"). |
| The log says the rollover is "past due" | The change should have happened already. | `Channel automation rollover horizon for <channel> is past due by Ns but the seamless reload is still settling; waiting.` or `Channel automation rollover reload for <channel> did not land within Ns (current_proof_event_id unchanged); retrying.` | These are the system retrying. Escalate only if they repeat for many minutes. |
| The channel is pinned at **Changing source** | Reload stall. | `Channel <id>: reload stall -- pinned TRANSITIONING for Ns with nothing in flight ... Re-issuing the rollover now`; if that fails: `reload stall not cleared by the re-issue ... Restarting the channel's egress`. | The watchdog does both steps itself. If it is still stuck afterwards, stop and start the channel. |
| The worker said the commit did not finish | The reload commit did not complete. | `gst-worker.stderr.log`: `CTRL reload: commit did not finish`. | The daemon falls back. |
| A single dropped frame at a join between two pieces | A known single-frame drop. | `CTRL mux diagnostic: dropped future-dated video arrival`. | None. It is one frame. |
| A change aired only the tail of the closing item | The plan was resolved against a short remainder. | `Plan for <channel> resolved to a N.Ns tail of the closing scheduled item`. | Check the schedule's station time zone and the slot's planned start and end. |

> **Historical Beta 15 observation:** An initial Beta 15 candidate showed repeated rollover attempts after a 20-second Welcome clip ended inside a 60-second slot. The corrected Beta 15 package passed both the 20-second and 60-second slot checks and returned to slate. The [Beta 15 verification record](https://github.com/scottconverse/civiccast-native/blob/main/docs/releases/v1.0.0-beta.15-verification.md) preserves both results; they do not establish Beta 16 package behavior.

> **Note:** A Beta 15 field run found scheduled speech windows on three channels showing slate with silent audio and header-only captions. The cause was not established, and the observation does not identify Whistle as the cause or establish Beta 16 behavior. This is a Beta 17 follow-up; see the [deferred field report](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/beta17-deferred.md).

> **Historical Beta 10 observation:** An unexplained rebuild appeared during the eight-hour C16 development run. Its cause was not established; that observation is not evidence about the current package. Keep the logs if you see an unexplained rebuild.

## No audio, low audio, loudness

CivicCast conforms every program to a loudness target. There are three targets: streaming at -16 LUFS, ATSC A/85 at -24 LKFS (the cable headend standard in the United States) and EBU R128 at -23 LUFS. The headend presets use A/85. For speech, a "leveling ride" raises quiet stretches by up to 18 dB over 4-minute windows. The default public-stream target is -16 LUFS plus or minus 1.

| Symptom | Likely cause | How to confirm | Fix |
|---|---|---|---|
| Speech sounds uneven across a long meeting | The leveling ride failed and the item was conformed with the simpler loudnorm filter instead, which does not hold the level across every 4-minute stretch. | `Speech leveling ride failed for '<name>' (window: ...): ... Conforming with loudnorm instead, which does not hold the level across every 4-minute stretch.` | Re-record or re-upload if the source is damaged. Otherwise accept it; the item still aired. |
| A program is noticeably quiet and the log mentions the loudness gate | The source is quieter than the ride can lift. 18 dB of lift reaches a source down to about -35 LUFS; below that, the window is listed in the report as out of reach and is not counted as a failure. | `Speech leveling for '<name>' (window: ...): the kept attempt misses the loudness gate (worst 4-minute stretch N LU, whole program N LU).` | Fix the recording level at the microphone. A check on one public window read -19.4 LUFS and passed on re-check; that is the quiet-source class. |
| A true-peak error in the log | A peak exceeded the hard bound. | An ERROR line from the loudness module. | Collect the log and the asset id. The program still aired. |
| Conform runs again and again for the same file | The conform cache is too small, so files are evicted and rebuilt. The cache size is `CIVICCAST_CONFORM_CACHE_GB`, default 60. The older default of 20 caused thrash: one 4.4-hour asset took about 11 GB. | `Conform-cache warm failed for '<name>' ... retrying after Ns` reports a failed attempt. Retry delay starts at 60 seconds and doubles up to 30 minutes; success resets it. Upcoming-plan work is prioritized over opportunistic warming. `0` turns the cache off. | Keep the setting at its default or raise it, and keep the disk free. In the 8-hour run 46 GB of the 60 GB was used. |
| Silence on air, picture present | The source has no audio track, or the audio leg of the worker failed. | `gst-worker.stderr.log`: in the `CTRL output:` lines, `audio=+0`. | Check the source file or capture device. |

> **Known issue (beta.11):** There is no free-space guard for the 60 GB conform cache. Watch free space. See "Disk full".

## Captions late or missing

There are two separate caption paths. **Live captions** are off by default in beta.11. Enabling **Show live captions on air** in Station Profile starts speech recognition on the next tap-worker scan; stop and start each channel before expecting captions in its video. Turning the switch off stops recognition and drains queued audio on the next scan; caption routing is removed when each channel next starts. The service setting `CIVICCAST_CAPTION_TAP=off` overrides the profile and keeps the tap off. **Recorded captions** run after publication and use the separate review workflow; see the Publishing and Recording sections.

### Live captions and recovery

The native live runtime uses Whistle on CPU by default. It publishes the first recognition without waiting for another reading to agree, and serializes primary inference across channels on the station. A Whistle request has a 30-second deadline. If it fails or times out, Whisper supplies backup captions while CivicCast automatically starts a fresh Whistle process for a recovery attempt. Successful recognition switches that channel back to Whistle. Whisper is also the recorded-caption engine. Supported NVIDIA CUDA acceleration is optional for Whisper; Whistle uses the CPU.

Whisper fallback allows 120 seconds per request by default. `CIVICCAST_WHISPER_FALLBACK_TIMEOUT_SECONDS` accepts finite values from 1 to 600 seconds. This deadline permits slower CPU responses to finish while still containing a stalled process. It does not expand the bounded audio queue. Caption status distinguishes temporary delay from sustained overload and paused recognition.

The audio tap has bounded queues and can discard older working audio or pause caption recognition under sustained overload to keep playout responsive. If captions lag, pause, or show gaps, use the table below to check the logs and reduce competing CPU or disk work.

| Symptom | Likely cause | How to confirm | Fix |
|---|---|---|---|
| No captions on the live picture at all | Live captions are off by default, the service override is `off`, or the current channel has not started since the switch was enabled. | Station Profile checkbox **Show live captions on air**; the **Channels** caption status card; confirm `CIVICCAST_CAPTION_TAP` is not `off`. | Enable the profile switch; recognition resumes on the next worker scan. Then stop and start the channel so its video includes captions. |
| `Whistle failed for <channel>; switching to Whisper` appears | Whistle failed or missed its 30-second deadline; that channel is using backup captioning during recovery. | Check the engine and recovery countdown on **System Health**; search `C:\ProgramData\CivicCast\logs\control_plane-app.log*` for the failure and recovery messages. | CivicCast retries Whistle after 30 seconds, with further failed probes backing off to at most 300 seconds. Successful recognition restores Whistle automatically. A failed Whisper child gets one immediate replay, then a 30-second cooldown after another failure. Bounded failure diagnostics are retained for investigation. |
| Captions have gaps and the log says `Caption tap catch-up ... Discarded` | Recognition fell behind and the bounded tap shed old working audio to preserve playout. | `Select-String -Path C:\ProgramData\CivicCast\logs\control_plane-app.log* -Pattern "Caption tap"`. | Reduce other heavy work on the station and watch the output. An NVIDIA GPU is optional for Whisper and does not accelerate Whistle. Do not treat a gap-free run as established until it has been observed under your workload. |
| Caption status shows delayed, overloaded or paused | Delayed means the queue is temporarily behind; overloaded indicates sustained backlog; paused means the tap has engaged its protective pause. | Check queue depth, queue age and engine recovery on **System Health**, and search the control-plane log for `Caption tap overload`. | Let a temporary delay catch up. For persistent overload, reduce competing CPU/disk work; caption recognition resumes according to its backoff. Restart the channel only if its caption state does not recover. |
| The caption status card says "Not verified" or "Caption proof failed" | The decode-back proof is absent, stale or failed. | Check the card on **Channels** and search the control-plane log around the same time. | Check the live-caption rows above and the channel logs. |
| Captions on H.265 channels | The caption inserter is H.264 only on native Windows. | The log: "HEVC/H.265 cannot embed captions on native Windows -- the caption inserter is H.264-only...". | Use H.264 for channels that must carry captions. |

Ordinary live captioning does not create permanent per-cue review records or evidence WAVs. Temporary working audio is cleaned up as it is processed; bounded live cues are not a substitute for caption review of the original recording. Recorded-caption review continues to use the Review queue.

## Recording failures

Scheduled recording runs inside the station (`CIVICCAST_SCHEDULED_RECORDING`, `inline` or `off`; it looks for new jobs every 10 seconds and plans 600 seconds ahead). Recordings are made by FFmpeg, one process per job.

| Symptom | Likely cause | How to confirm | Fix |
|---|---|---|---|
| The recording job shows "ffmpeg exited before recording started for job ..." | The input could not be opened. | The text in the job's **Failure** column. The channel logs. | Check the input address or capture device and its permissions. |
| "ffmpeg did not create a recording segment for job ..." or "ffmpeg created a zero-byte recording file for job ..." | The input opened but delivered nothing, or the target folder is not writable. | The Failure column. | Check the input is live, and that the folder has space and is not read-only. |
| "No local recording target is configured.", "No production local recording target is configured; only the installer rehearsal target was available." or "No usable local recording target is configured." | No recording folder is set up. | The Failure column. | Configure a local recording target; see [Configuring the station](#ch-configuration). |
| "Recording input preset ... is not available for ... Open Recording and select a detected or configured input preset." | The chosen input is not on this machine. | The Failure column. | Choose a detected input on **Recording**. |
| "Capture file ... does not exist." or "... is empty." | A capture that ended with no media. | The Failure column. | Check the capture source. |
| "Unsupported scheduled recording encoder profile" | The encoder profile name is not one of: copy, default, inherit, h264-1080p, hw-h264-1080p, h264-720p, hw-h264-720p. | The text. | Pick one of those. |
| An amber banner "Scheduled recording runtime is unavailable in this deployment." (HTTP 503) | `CIVICCAST_SCHEDULED_RECORDING` is `off`, or the runtime did not start. | The banner. | Set it to `inline` and restart the service. |
| HTTP 403 on the recording schedule | `CIVICCAST_STATION_ID` does not match (default `civiccast-station`). | The response. | Set the matching value. |
| A recording finished, but the Live room or assets show a failed finalization | The post-recording worker failed. The failure codes are `recording.never_appeared` (waited 1800 seconds), `recording.not_local`, `probe.failed`, `finalize.invalid_trim`, `package.failed`, `cdn.upload_failed`, `worker.interrupted` and `internal.error`. | `GET /api/staff/live/finalizations` or the Live room. | Press **Retry finalization** in the Live room, or call `POST /api/staff/live/sessions/{id}/finalization/retry`. The worker makes 3 attempts by default (`CIVICCAST_FINALIZATION_MAX_ATTEMPTS`), waiting 30 seconds before the second and doubling the wait after each further failure (`CIVICCAST_FINALIZATION_BACKOFF_SECONDS`). The retry call needs the Meeting operator role and answers 409 if the finalization is running or already complete. |
| The schedule ran at the wrong hour | The station time zone or the entry's planned time may be wrong. | Compare the planned time, station zone and computer clock. | Correct the Station Profile zone or schedule entry, then check the next occurrence. |
| A recording alert, `scheduled-recording-failure` or `scheduled-recording-dropout` | A job failed, or the input dropped out during the job. | **Alerts** screen. | See the rows above. |

> **Known issue (beta.11):** **Stop** is also offered on a recording job that is still in the `scheduled` state (not yet started). The station refuses it with HTTP 409 ("Cannot stop job ... only ['arming', 'finalizing', 'recording'] are stoppable"), and the screen does not show the error, so nothing seems to happen. **Stop** does work on a job that is arming, recording or finalizing. To stop a job that has not started from running, the usual way is to untick **Enabled** on its schedule; we did not confirm that this also cancels a job that already exists.

> **Known issue (beta.11):** Creating a recording schedule gives no confirmation, and creating the same one again returns 409 "already exists". Check the list before you try again.

## Publishing problems

The Publish screen records "operator-dashboard" as the operator for every approval. Publish targets are the resident portal, Internet Archive, Local NAS (rsync or ZFS), YouTube Live, YouTube VOD, a podcast episode, subscriber notifications and a cable file package. Podcast and subscriber notifications are optional and show their outcomes when selected.

| Symptom | Likely cause | How to confirm | Fix |
|---|---|---|---|
| Approving fails with "Publish preflight blocked: this asset has no manifest_url. Run the packager or fix ingest before approving publish." (HTTP 409) | The recording has not been packaged for the web yet. | The error text. | Wait for packaging, or retry finalization (see above). |
| "Publish blocked: CivicCast cannot queue this recording's caption job ... Nothing was published." (HTTP 409) | The caption job for this recording could not be queued. | The error text. | Check disk space and the caption pack. Then approve again. |
| A target shows a success message but nothing arrived at YouTube or the archive | Providers default to **mock**. Each of `CIVICCAST_PROVIDER_INTERNET_ARCHIVE`, `_LOCAL_NAS`, `_YOUTUBE`, `_MAIL` and `_WEBHOOK` is `mock` unless set to `real`. | Check the environment variables. | Set the variable to `real` and add the credentials listed in [Cable headend, streaming, CDN, federation, emergency alerts, the API](#ch-integrations). |
| A target you did not tick was reset on a second approval | Approving again rebuilds the whole run and resets unticked targets. | The Publish screen. | Tick every target in one approval. |
| The cable file package row says "not set up (optional)" | `CIVICCAST_CABLE_PACKAGE_OUTPUT_DIR` is not set. | The row. | Set it (see Chapter 15) and approve again. |
| A subscriber did not receive a new-recording notice | The subscriber is not confirmed, the optional surface was not selected, or its provider is mock or failed. | Check the subscriber-notification row and its delivery list. | Confirm the subscription, configure the provider, then retry eligible deliveries. Federation is a separate feature and is off by default. |

> **Known issue (beta.11):** The Publish screen does not always say when a mock provider did the work. An Internet Archive or Local NAS row carries a "Simulated" note, but the card's headline can still read "Archive verified" (HELP-02), and a YouTube row from the mock provider shows a success message with no simulated note (HELP-03). Before you rely on a publish target, send one test and look at the far end.

> **Known issue (beta.11):** Approving a recording can announce it to federation followers as a hidden side effect when ActivityPub is on. See Chapter 15.

> **Known issue (beta.11):** A second approval resets any target you left unticked (HELP-01). Tick everything in a single approval.

## The portal does not show video

Residents can reach the portal through the configured public Caddy hostname. Staff use the separate hostname restricted to approved CIDRs. The application itself remains bound to loopback at `http://127.0.0.1:8000/`; the installer firewall rule does not make that listener directly reachable from another computer. Follow the [network access guide](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/network-access.md) and test from both allowed and denied networks.

| Symptom | Likely cause | How to confirm | Fix |
|---|---|---|---|
| The live page says offline or "standing by" | `curl.exe http://127.0.0.1:8000/api/public/live/current` reports a state: `offline`, `on_air`, `on_air_no_web_output` or `standing_by`. | Read `state` and `reason` in the answer. The reasons are "no HLS output configured", "HLS output configured but not serving yet" and "fallback slate, no program on air". | The first needs an `hls` output on the channel (see Chapter 15). The second means the web stream has not started: see the HLS relay row. The third is the slate: see "Channels will not start". |
| The channel is on air, but the web stream is empty | The HLS relay did not start or died. | Application log: `HLS relay up for <channel>`, `HLS relay could not start for <channel> ... ffmpeg is not available`, `HLS relay for <channel> ... restarting the relay child`. The relay's own log is `data\egress\<channel>\logs\hls-relay.<sink label>.stderr.log`. | Restore FFmpeg by running setup again; stop and start the channel. |
| A recording page has no video | The recording has no `manifest_url`, so it was never packaged, or the web copy failed. | Assets screen status; finalization codes above. | Retry finalization. |
| Recordings play on the station but are slow or missing for the public | The CDN selector is on and uploading failed (`cdn.upload_failed`). | Failure code in the finalization list. | Check the CDN settings in Chapter 15. Review the matching package record's scope, then test one recording and verify it at the CDN before relying on that path. |
| Video is not found at all at `/media/live/<channel>/playlist.m3u8` | The channel has no HLS output, or is not running. | `curl.exe -I http://127.0.0.1:8000/media/live/<channel>/playlist.m3u8` | See above. |

> **Known issue (beta.11):** The `finalization-worker-runbook` in the repository says the local media base address defaults to `http://127.0.0.1:8000`. In the code the default is empty, which means addresses relative to the site. We follow the code.

## Disk full

| Symptom | Likely cause | How to confirm | Fix |
|---|---|---|---|
| Alert `disk-low`: "Low free space: media X GB (min 20)" | Free space on the media or backup drive fell below the limit. The default thresholds are 20 GB for media and 20 GB for backup. | **Readiness** shows "Media space" and "Backup space" in GB free. | Free space. The conform cache (up to 60 GB), old recordings and caption data are the large items. |
| Setup refuses with "Not enough free disk space to activate this station..." | Activation needs the total of the pack sizes plus 2 GB. | The setup details pane. | Free space. |
| Uploads fail with HTTP 507 on the contribute page | The drive is full. | The response. | Free space. |
| Disk fills slowly over weeks | Recordings and logs accumulate, and the conform cache can grow to 60 GB with no check on free space. Ordinary live captions do not create a permanent audio-evidence archive. | Folder sizes under `C:\ProgramData\CivicCast\data`. | Plan the disk size for the long term; see [Planning your station](#ch-planning). |

> **Known issue (beta.11):** The anonymous contributor upload spools a file to disk before any size limit applies, and can fill the drive (audit finding B-001). If the contribute page is open to the public, watch free space closely.

## Database problems (and why there is no NATS)

CivicCast used to have a message broker called NATS. It was removed (decision 0023, owner decision of 2026-08-20). An in-process event bus replaced it. There is no NATS child to start, to check or to repair. If an old note tells you to look for one, ignore it.

PostgreSQL listens on `127.0.0.1` on the first free port of 5432, 5433, 5434, 5435 and 5544, chosen when the station was provisioned. The control plane finds its database address from the registry value `HKLM\SOFTWARE\CivicCast\Native\DatabaseUrl`, readable by SYSTEM, Administrators and `NT SERVICE\CivicCastSupervisor`, unless the environment variable `DATABASE_URL` overrides it.

| Symptom | Likely cause | How to confirm | Fix |
|---|---|---|---|
| **Readiness** machine health row says the database is **Unreachable**, or alert `db-unreachable`: "Database is not reachable from the host." | PostgreSQL stopped or cannot be reached. | `logs\postgres.log`. | Restart the service. If it fails again, read the PostgreSQL log for the cause (disk full, damaged files). |
| Alert `service-down`: "The CivicCast egress service is not running." | The egress engine is not running. | The alert. | Check `supervisor.log`. |
| Log: `DATABASE_URL source: environment override ...` or `... registry (HKLM\SOFTWARE\CivicCast\Native\DatabaseUrl)` | This is an informational line that says which source won. | The line. | If an old `DATABASE_URL` machine variable points at a database that is gone, remove it. |
| A command-line tool says "DATABASE_URL must point at the CivicCast database before running staff token lifecycle commands." | The `civiccast` command line did not find the database address. | The message. | In an Administrator PowerShell, set `$env:DATABASE_URL` from the registry value for that shell only. |
| `/health` shows `schema` as `behind` | The database has not been migrated to this program's version. | `curl.exe http://127.0.0.1:8000/health` | See "Upgrade problems". |

## GPU and encoder problems

The default encoder path is GStreamer. On Windows, a hardware H.264 request is mapped to the Media Foundation encoder (`mfh264enc`), and the default H.264 software encoder is `openh264enc`. HEVC (H.265) needs hardware; there is no software HEVC in this build.

| Symptom | Likely cause | How to confirm | Fix |
|---|---|---|---|
| The channel does not air its program (it shows the slate, or ends in **Needs attention**). The message "No hardware video encoder was found on this machine. To broadcast on the CPU instead (slower), turn on 'Allow software (CPU) encoding fallback' in this channel's settings, then start the channel again. HEVC/H.265 needs hardware and is not available this way." appears in `last_error`, usually after `egress encoder unavailable; aired fallback slate:` | The pre-flight probe found no hardware encoder. | `last_error` on the channel, and the application log. | Either turn on **Allow software (CPU) encoding fallback** in the channel's settings, or install the drivers for the graphics card. |
| "No hardware HEVC/H.265 encoder is available on this machine. HEVC requires a hardware encoder -- there is no software HEVC in this build..." | An H.265 channel on a computer with no hardware HEVC encoder. | The text. | Use H.264. |
| Log warning: "No hardware video encoder was found; 'allow_software_fallback' is on, so this channel is encoding on the CPU (openh264enc). This is slower and may not keep up with live on a weak CPU." | Software fallback is on. | The warning. | This is informational. On a weak CPU, expect trouble in "Program change problems" and "Captions". |
| Log: `egress encoder unavailable (...); falling back to the technical-difficulties slate rather than dead air.` | Neither the GStreamer nor the FFmpeg encoder could start for the program, so the slate is being encoded instead. | The line, and `last_error` "egress encoder unavailable; aired fallback slate: ...". | Fix the reason after the colon. |
| The channel is in ERROR with no slate | FFmpeg is absent from the machine, so even the slate cannot be encoded. | `last_error` is a message about FFmpeg not found. | Run setup again to restore the files. |
| Live GPU captions will not run | The graphics card has less than 8 GB of video memory, is not NVIDIA, or the CUDA pack is missing. | See "Captions". | See "Captions". |

## Upgrade problems

> **Upgrade evidence (Beta 12):** One normal upgrade from the pinned Beta 11 baseline exited 0 and recorded a healthy service/API, current schema 0089, a saved preservation marker, and initial HLS audio/video plus Whistle observations on three channels. The post-upgrade recorder failed during its first JSON save; no five-snapshot PASS was recorded. Fresh-install and repair checks remain unrun, and no sustained-operation result is claimed. Take a separate backup of `C:\ProgramData\CivicCast` before an upgrade. See the [Beta 12 verification record](https://github.com/scottconverse/civiccast-native/blob/main/docs/releases/v1.0.0-beta.12-verification.md).

An upgrade is the same `setup.exe` run over an existing install. The generic **Already Installed** page is suppressed; setup does not uninstall first. The installer first checks versions without stopping the service or replacing files. For an accepted upgrade, setup saves a temporary, size-checked snapshot before replacing files, re-verifies packs, and runs the database upgrade. If a later step fails, the engine rolls back its database work and the enclosing installer transaction attempts to restore the prior application and station state. Exit 136 means recovery could not be prepared or completed; keep `install-transaction` and station data intact and let the recovery task or next setup run retry.

| Symptom | Likely cause | How to confirm | Fix |
|---|---|---|---|
| Setup exit 113 | The engine could not restore its database backup. Setup also attempts to restore the pre-setup application and station snapshot. | `install-progress.log`; when present, failed-run engine logs and journal under `install-transaction\failure-diagnostics`. | If outer restoration succeeds, the prior station state is back. A generated `UPGRADE-RECOVERY.md` may not remain under `upgrade` after that restore. If recovery did not finish, keep the recovery files and station data. |
| Setup exit 114 | This release has a database change that cannot be rolled back, so the automatic upgrade was refused. | The dialog. | No manual path is written in the dialog (HELP-107). Ask for help before you proceed. |
| Setup exit 115 | An unexpected fault in the upgrade step. | `install-progress.log`. | Run setup again; collect the logs if it repeats. |
| Setup exit 124 | The engine rolled back its database work; setup attempts to restore the pre-setup application and station state. | `install-progress.log` and, when present, the failed-run engine log and journal under `install-transaction\failure-diagnostics`. | Read the reason, fix it, then run setup again. If restoration succeeded, the previous service configuration is restored too; keep recovery records if it did not. |
| Setup exit 128 | A terminal failure journal from an earlier upgrade remains; when installer restoration succeeds, it returns the pre-setup state and preserves that earlier journal. | `upgrade\upgrade-journal.json` and any retained copy under `install-transaction\failure-diagnostics`. | Inspect and archive or move the prior terminal journal aside (do not delete it), then run setup again. |
| Setup exit 129 | An older setup was refused. The normal read-only version preflight exits before stopping the service or replacing files; a defensive late check also attempts pre-setup restoration. | The dialog and `install-progress.log`. | Use the setup matching the installed version. If the late recovery did not complete, keep the recovery files and station data. |
| Setup exit 136 | The installer could not prepare, complete or resume automatic restoration. | `install-transaction\recovery-error.txt` and `install-progress.log`. | Keep station data and recovery files. Restart Windows or retry setup to let automatic recovery run; contact support if it repeats. |
| After the upgrade `/health` says `schema` is `behind` | The migration did not run. | `curl.exe http://127.0.0.1:8000/health`. | Run setup again; see exit 125. |
| Uninstall exit 82, 133 or 134 | The service could not be confirmed stopped (82, 133), or teardown is incomplete (134, remove by hand). | The dialog. | Stop the service in `services.msc` or restart Windows, then uninstall again. For 134, remove the leftovers in `services.msc`, Windows Firewall and the registry. |

## What to collect before asking for help

Collect these before you ask anyone for help. Together they answer most questions.

1. **The time and what you saw.** Write down the clock time with its zone, the channel, and the words on screen. Say whether anyone changed a setting just before.
2. **The whole `C:\ProgramData\CivicCast\logs` folder** (`supervisor.log`, `control_plane-app.log`, `control_plane.log` and `postgres.log`, including numbered rotated copies), copied while the problem is fresh. Rotation can overwrite old lines.
3. **`C:\ProgramData\CivicCast\install-progress.log`**, the `upgrade` and `provision` folders, and any `STATION-START-FAILED.md` or `install-manifest-report-*.json`, for install and upgrade problems.
4. **The channel's own logs**: everything in `C:\ProgramData\CivicCast\data\egress\<channel>\logs\`.
5. **The health answer**: the output of `curl.exe http://127.0.0.1:8000/health` and of `Get-Service CivicCastSupervisor`.
6. **The version**: it is in the health answer, and the installed version is recorded in the registry value `InstalledVersion` under `HKLM\SOFTWARE\CivicCast\Native`.
7. **The support bundle**, if the console still loads: on **Readiness**, as a Support admin, create the support bundle. It is a JSON file named `<bundle id>.json` in a `support-bundles` folder next to `station-state.json` (the folder can be moved with `CIVICCAST_SUPPORT_BUNDLE_DIR`), and the console offers it for download. It holds the version, platform, which settings are present (without their values), setup, storage and health information, alerts, recent samples and the tail of some logs. Lines that contain a secret marker are dropped.
8. **Command-line status**, if you can run it: `runtime\python.exe -m civiccast.cli runtime status` and `... doctor`, using the `runtime` folder in the install folder. We have not run these on a station; the code supports them. The command list is in [Appendix: command line](#app-cli).

> **Known issue (beta.11):** The support bundle does not collect the logs most useful for a GStreamer station. It reads `%USERPROFILE%\.civiccast\runtime-host.log` and each channel's `ffmpeg.stdout.log` and `ffmpeg.stderr.log`, while the default GStreamer engine writes `gst-worker.*` logs. `supervisor.log` and `control_plane-app.log` are not included; add them by hand (steps 2 and 4).

> **Note:** Use **Report a beta issue** for the project issue tracker, or follow the no-GitHub option in [Chapter 8](#ch-something-wrong). Do not post a support bundle publicly; wait for a maintainer to request it through a private channel.

## Reference tables {#reference-tables-ch14}

### Setup exit codes

| Code | Step | Meaning |
|---|---|---|
| 82 | Uninstall | The service stop was not confirmed. |
| 110 | Stage packs | A required pack is missing or untrusted (child code 74), or the optional GPU pack is untrusted (child code 75). |
| 111 | Verify | The unpacked server tools do not match their signed list. |
| 112 | Verify | The unpacked program files do not match. |
| 113 | Upgrade | The engine could not restore its database backup; the installer also attempts full pre-setup restoration. See `install-progress.log` and any retained `install-transaction\failure-diagnostics`. |
| 114 | Upgrade | The release has a migration that cannot be rolled back; the automatic upgrade was refused. |
| 115 | Upgrade | An unexpected fault. |
| 116 | Provision | PostgreSQL could not be provisioned. See `PROVISION-RECOVERY.md`. |
| 117 | Provision | An unexpected fault. |
| 118 | Service | The Windows service could not be registered. |
| 119 | Firewall | The inbound rule could not be created. |
| 120 | Pre-install | The existing service could not be stopped, or its state could not be read. |
| 121 | Verify | FFmpeg does not match. |
| 122 | Verify | Ollama does not match. |
| 123 | Activation | Child codes 64, 65, 66, 67 and 78, or another. |
| 124 | Upgrade | The engine rolled back its database work; setup attempts to restore the prior application and station snapshot. |
| 125 | Service | The service started but the station is not serving (child code 84). |
| 126 | Service | The service would not start (child code 83). |
| 127 | Provision | Runtime ownership unknown (child code 85). See `OWNERSHIP-RECOVERY.md`. |
| 128 | Upgrade | An earlier failed upgrade's journal is still on disk. |
| 129 | Upgrade | An older setup was refused; the normal version preflight runs before service stop or file replacement. |
| 130 | Uninstall | You declined the ownership-transfer prompt. |
| 131 | Uninstall | Ownership transfer failed. |
| 132 | Uninstall | Blocked: an active runtime together with a WSL product, or unreadable state. |
| 133 | Uninstall | Service stop unconfirmed; files kept. |
| 134 | Uninstall | Teardown incomplete; remove the leftovers by hand. |
| 135 | Provision | The older WSL edition is registered (child code 87). |
| 136 | Installer recovery | The automatic restoration transaction could not be prepared or completed. Keep `install-transaction` and station data intact. |
| 3010 | Visual C++ | The Visual C++ runtime installed and Windows needs a restart. We could not confirm that setup itself returns this number. |

Child codes seen: 64 arguments, 65 report write, 66 pack missing or untrusted, 67 activation or self-test, 68 verify, 73 and 74 uninstall preflight, 75 provision or pack, 78 embedded trust key refused, 83 service start, 84 not serving, 85 ownership, 87 other product. A child code of 77 appears in the setup code but its meaning is not named in any text we read. A first install with only `setup.exe` and no kit stops at exit 110 (see the first row of the install table).

### Log line index

| Quote this fragment | Where | Section |
|---|---|---|
| `channel <id>: egress state -> <STATE>` | `control_plane-app.log` | Channels |
| `Boundary reload for ... did not land` | `control_plane-app.log` | Program changes |
| `Seamless content-reload for ... did not land` | `control_plane-app.log` | Program changes |
| `reload stall -- pinned TRANSITIONING` | `control_plane-app.log` | Program changes |
| `Freeze escalation budget exhausted` | `control_plane-app.log` | Channels |
| `CTRL first-output: no output within 45s` | `gst-worker.stderr.log` | Channels |
| `Caption tap is behind` | `control_plane-app.log` | Captions |
| `Caption tap catch-up ... Discarded` | `control_plane-app.log` | Captions |
| `Caption tap overload ... PAUSED` | `control_plane-app.log` | Captions |
| `Speech leveling ride failed` | `control_plane-app.log` | Loudness |
| `HLS relay could not start` | `control_plane-app.log` | Portal |
| `TS relay up for` and `TS relay failed to start` | `control_plane-app.log` | Chapter 15 |
| `degrading egress to the FFmpeg concat engine` | `supervisor.log` | Station will not start |
| `station could not start:` | `supervisor.log` | Station will not start |
| `singleton not acquired` | `supervisor.log` | Station will not start |

## Rotation and source-preview checks

- If a channel stops after the last scheduled program, check that its items are published and that **Repeat the published sequence** is saved in **Channels → Channel startup and repeat**. Check the items marked **In repeat sequence** in the published plan. Only the last contiguous group repeats; adjoining entries need second-accurate start times and durations. See [the continuous scheduling recipe](#ch-before-meeting).
- If the trim or summary source preview cannot play, confirm the original file remains available and package the recording for HLS playback. Use the preview's retry or renewal action after correcting the source. A summary citation outside a trimmed copy needs a package covering that passage; it should not be reviewed against a different time range.
- If **Publish** counts items needing action, its **Needs action** filter includes required blocked outputs as well as failed outputs. Open each listed next step to resolve it.

## If it did not work

If nothing here matches, do the following in order. First, run `curl.exe http://127.0.0.1:8000/health` and write down the answer. Second, read the last 200 lines of `control_plane-app.log` and `supervisor.log` and look for lines at WARNING, ERROR or CRITICAL level. Third, if the station is not on air and you are able to restart it, restart the service between programs. Fourth, collect the items in "What to collect before asking for help". Do not delete anything under `C:\ProgramData\CivicCast`; it holds the database and every recording.

## Package status and verification

This chapter describes the published Beta 20 edition. For package-specific results, use the [Beta 20 verification record](https://github.com/scottconverse/civiccast-native/blob/main/docs/releases/v1.0.0-beta.20-verification.md). The [Beta 18 record](https://github.com/scottconverse/civiccast-native/blob/main/docs/releases/v1.0.0-beta.18-verification.md) and [Beta 15 verification record](https://github.com/scottconverse/civiccast-native/blob/main/docs/releases/v1.0.0-beta.15-verification.md) remain historical.

## Related

- [Signing in and finding your way around](#ch-signing-in)
- [Running the meeting](#ch-running-meeting)
- [Publishing, and what residents see](#ch-publishing)
- [When something looks wrong](#ch-something-wrong)
- [Installing, first run, upgrading, uninstalling](#ch-installing)
- [Running it day to day](#ch-operations)
- [Cable headend, streaming, CDN, federation, emergency alerts, the API](#ch-integrations)

<!-- SOURCES: inventory/screens/installer-failures-and-logs.md; installer-install-layout.md; installer-nsis-service-finish.md; installer-nsis-activation-selftest.md; _shell-signin-and-session.md; health.md; channels.md; recording.md; publish.md; eas.md; generated/env-vars.md; docs/releases/v1.0.0-beta.10-verification.md; ops/beta10-oversight/HANDOFF-2026-10-01.md, audits/audit-lite-whole-repo-beta10-2026-10-02.md; civiccast/captions/tap_worker.py:150-410,900-1070,1810-1830,1955-1995; captions/tap_backoff.py; civiccast/egress/daemon.py (_write_state, _start encoder-unavailable path ~2683-2730, reload paths), egress/automation.py, egress/gst/{bridge,encoder_probe}.py, egress/hls_relay.py, egress/loudness_ride.py, egress/preparer.py; civiccast/native/supervisor/{service,children,start_failure_marker,config}.py; civiccast/app.py (health); civiccast/installer/{service,station_state}.py (support bundle ~3330-3560); civiccast/recording/runtime.py; civiccast/live/finalization_worker.py; civiccast/publish/service.py; civiccast/alerting/resource_sampler.py; civiccast/apps/installer/src-tauri/nsis-hooks-bootstrap.nsh:455-588; civiccast/apps/installer/src-tauri/src/main.rs:4782-4798 (self-test wait); civiccast/native/station_runtime.py:1090-1340 (caption tier, FFmpeg degrade); civiccast/auth/middleware.py:100-160 (429 text); civiccast/installer/router.py:1060-1100; civiccast/app.py:2377-2450 (/health); civiccast/egress/router.py (repair-gstreamer); civiccast/live/router.py (finalization retry); civiccast/live/finalization_worker.py:100-145,823; civiccast/app_platform/router.py; ops/beta10-oversight/verdicts/rung-8h-c16-*--VERDICT-NOTE.md -->
