> Historical beta.10 audit snapshot dated 2026-10-03; retained for traceability, not current UI or user guidance.

# Downloading / Setting Up (Tauri window "CivicCast (Native) Setup", first-run screen 3)

Paths relative to `civiccast/apps/installer/`. AF = `src/AcquisitionFlow.tsx`; AP = `src/acquisition-progress.ts`; API = `src/api.ts`; main = `src-tauri/src/main.rs`.

## Where the text lives now
Screen and rows: AF:654-1047. Failure lines and stall rule: AP:144-239. Alerts and refusals: API:869-871, 898-900, 944. Messages from the program: main:3743-3746, 3766, 3786-3793, 138-141, 2968-2971. The same screen is reachable again from the status window's "More options, Download AI models and captions" (`src/App.tsx:618-622, 803-805`).

## Current text
| String | Where | Source line |
| --- | --- | --- |
| "Downloading" / "Setting Up" | Heading (Setting Up when every row is found locally or still waiting behind such rows) | AF:764, 688-691 |
| "Keep CivicCast Installer open. If a download is interrupted, use Resume download." | Lead while downloading | AF:768 |
| "Everything CivicCast needs was found on this computer or your USB kit — nothing is being downloaded. Keep this window open while it finishes." | Lead when all local | AF:767 |
| "<done> of <total>"; "Done"; "Time left: Estimating…"; "<time> left" | Overall progress | AF:786-793; AP:373 |
| "Total updated to X. The download was announced as Y from the published sizes; every file's real size is now known. This figure does not change again during this download." | Note, at most once | AP:322-324 |
| "Found locally — verified ✓" / "Verified ✓ — checked against its signature" | Row done | AF:959 |
| "Waiting" + "Not started yet"; "Downloading"; "Verifying"; "Stalled — retrying"; "<rate>/s — <time> left" or "Measuring speed" | Row in progress | AF:1013-1018, 1032-1036 |
| "What is this?" | Current row only | AF:1041 |
| "Stopped. X of Y is already downloaded and kept." + **Resume download** | Row stopped | AF:973-978 |
| Failure lines (8, listed in the proposed text below) + **Resume download** or **Retry download** | Row failed | AP:146-175, 198-199; AF:998-1004 |
| **Stop downloading** (while anything is in flight); "CivicCast stopped downloading. Nothing already downloaded was lost." | Bottom bar | AF:891; main:3766 |
| **Open installer log** (until all done); "Opened the CivicCast installer log: <path>"; "No CivicCast installer log exists yet. Checked: <paths>." | Bottom bar | AF:924; main:2968, 138-141 |
| "No files have started downloading yet. CivicCast is still waiting for the first byte. If this does not change, use Open installer log below and send that log to support." | Alert after 30 s of nothing | AF:756-757; AP:239 |
| "CivicCast could not start downloading its components. Nothing is being downloaded right now. Use Open installer log below and send that log to support." | Alert if start refused | API:869-871 |
| "CivicCast could not stop the download. It is still running. Close this window to stop it; anything already downloaded is kept and setup will pick up where it left off." | Alert if stop refused | API:898-900 |
| "Retrying <name>." then "Retrying <component id>." (for example "Retrying captions_large.") | Status line after a retry | AF:850; main:3786 |
| "Retry is queued. CivicCast will pick this file back up on the next check." | If no driver config yet | main:3792; API:944 |

## What really happens
When the screen opens it starts one background download driver (main:3740-3747). The driver goes through the six downloadable components in a fixed order (application, database tools, Medium captions, Large captions, GPU library, AI model), one at a time. For each it first checks the copy already on disk against its signature and size, and marks it "Found locally" without touching the network; otherwise it downloads over HTTPS (30 second connect limit, 6 hour limit per transfer, a `.partial` file and HTTP Range for resuming) and checks the result (`component_acquisition.rs:544-545`; manual ch. 10). The screen reads progress from `%USERPROFILE%\.civiccast\installer-state.json` every 0.5 s while something runs, otherwise every 2 s (AP:128-131). There is no automatic retry loop; a row that fails or stops waits for the reader. **Stop downloading** marks every unfinished row stopped (main:3762-3767); **Resume download** or **Retry download** re-runs one row and returns only when it has finished (main:3776-3795). When every shown row is done the window moves to the status panel. Closing the window ends the program at once and keeps partial files (main:6459-6463).

## Mismatches
| ID | What it says | What really happens | Severity |
| --- | --- | --- | --- |
| HELP-56 | "Stalled — retrying" (AF:1015; AP:224-227) | Nothing retries. A row that gets no new byte for more than 10 seconds shows this text and can sit there up to the 6 hour limit. The real remedy is Stop downloading, then Resume download | SERIOUS |
| HELP-57 | "use Open installer log ... send that log to support" | The button opens whichever is newer of `C:\ProgramData\CivicCast\install-progress.log` and `%USERPROFILE%\.civiccast\runtime-host.log` (main:154-160). Neither records a download failure. The cause is kept only in `installer-state.json` (`acquisition_state.rs:60-67`: the engine detail is never shown) | SERIOUS |
| HELP-58 | "Keep CivicCast Installer open" | The window is titled "CivicCast (Native) Setup" (`tauri.native.conf.json:10`) | Low |
| HELP-59 | "This is our problem, not yours." with only Retry | A file missing at the download address cannot be fixed by retrying; no contact route is given | Moderate |
| HELP-60 | (silent) | The default download source is the release tag `native-beta-1.0.0-beta.1-rc1`, not 1.0.0-beta.10, while packs are checked against 1.0.0-beta.10; the GPU pack may fail with "didn't match its signature" (`acquisition_catalog.rs:221, 327-338`; UNVERIFIED which assets exist) | Moderate |
| HELP-61 | "MB/s", "Estimating…" | Units and the end of the estimate are unexplained | Low |
| NEW-1 | "Retrying captions_large." | After a retry finishes, the status line shows the raw component id (main:3786) in place of the friendly name set at AF:850 | Low |
| NEW-2 | **Stop downloading**, "Nothing already downloaded was lost" | True, but every stopped row then needs its own **Resume download** click; nothing resumes them all | Low |
| NEW-3 | Screen offers no way to leave except finishing | A reader who stops on a metered link can only close the window (the program exits). The station itself is already installed and does not need this window (manual ch. 10 Tip) | Low |

## Proposed text
Lead (replaces AF:768; keep the words "If a download is interrupted" which `src/cancel-retry.test.ts:376` pins): "Keep CivicCast (Native) Setup open. If a download is interrupted, use Resume download. Everything already installed by Windows setup shows 'Found locally' and downloads nothing."

Stalled (replaces AF:1015): "No data for 10 seconds. If this continues, choose Stop downloading, then Resume download." (`src/stalled-row-escape.test.ts:177` pins the old text; change it with this.)

Failure lines (honest now; the first five stay as AP:146-162 with the notes below):
- connection dropped: "The connection dropped. Nothing is damaged. Choose Resume download." 
- signature mismatch: "The downloaded file did not match its signature and was discarded. Choose Retry download; it starts this file again."
- server did not have the file: "The download server did not have this file. Retrying will not fix this. Please report it at https://github.com/scottconverse/civiccast/issues and say which row failed." 
- resume not possible: "The paused download could not pick up where it left off, so this file starts over. Choose Retry download."
- disk full: keep AP:162 and add the drive: "...Free up some space on the drive that holds C:\ProgramData\CivicCast, then choose Retry download."
- permission denied and write failed: keep AP:171, 175.
- unexplained (AP:198-199): "This download stopped and CivicCast did not get a reason it can explain. Nothing on this computer is damaged. Choose Retry download. If it stops again, open %USERPROFILE%\.civiccast\installer-state.json in Notepad, find this row's 'error' entry, and post that text at https://github.com/scottconverse/civiccast/issues (remove anything private first)."

Alerts (keep the words "installer log" and "no files have started downloading", pinned at `src/cancel-retry.test.ts:287-288`): "No files have started downloading yet. CivicCast is still waiting for the first byte. Check this computer's internet connection. If this does not change, use Open installer log below (it shows setup's steps, not download errors) and also send %USERPROFILE%\.civiccast\installer-state.json to support." Start-refused alert: add the same two files. Support line: https://github.com/scottconverse/civiccast/issues (community run).

Help text, now: "'Found locally — verified' means CivicCast checked the copy already on this computer against its signature, and nothing was downloaded. 'Verified — checked against its signature' means it was downloaded and then checked. MB/s means megabytes per second. Closing this window stops any download at once and keeps partial files; the station keeps running because it is a Windows service."

After fix: replace "Stalled — retrying" only if an automatic retry is added; change "Open installer log" to open `installer-state.json` or a new download log that records failures.

## Notes for the coder
- Edit AF:756-757, 768, 850, 1015; AP:154-175, 198-199; API:871; `main.rs:3786` (use the friendly name). Pinned tests: `src/stalled-row-escape.test.ts:177`; `src/cancel-retry.test.ts:192-195, 241-248, 287-288, 341-359, 376-385` (stopped text, "Resume download", "Retrying", the two alert regexes, "Opened the CivicCast installer log", "Setting Up" and "If a download is interrupted"); `src/api.test.ts` for the API strings.
- Needs a code fix, not text: write the engine's failure detail to a log the "Open installer log" button opens (HELP-57); resume all stopped rows at once; fix the release tag (HELP-60); an automatic retry if "retrying" is to stay.
- UNVERIFIED: what extracts a downloaded `.ccpack` after the GUI verifies it (no extraction call in this path); whether Stop leaves an unticked-but-running component going (see `installer-gui-download-plan`, HELP-52).
