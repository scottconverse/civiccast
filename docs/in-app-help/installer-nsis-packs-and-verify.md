> Historical beta.10 audit snapshot dated 2026-10-03; retained for traceability, not current UI or user guidance.

# Windows setup: C++ runtime, pack staging and re-check (NSIS details list and dialogs)

Paths relative to `civiccast/apps/installer/src-tauri/`. Hooks = `nsis-hooks-bootstrap.nsh`.

## Where the text lives now
All strings are in the `NSIS_HOOK_POSTINSTALL` macro (Hooks:702-1025). Dialog text goes through `CIVICCAST_FAIL` (Hooks:357-424), which also copies it to the details list and `install-progress.log`. The child programs' own words are in `src/native_pack_staging.rs:354-362` (abort message) and `src/main.rs:4469-4600` (stage-packs exit codes 64, 65, 74, 75).

## Current text
| String | Where | Source line |
| --- | --- | --- |
| "Installing the offline Microsoft Visual C++ runtime prerequisite..." | Details list | Hooks:711 |
| "Microsoft Visual C++ runtime installation completed." / "...completed; a Windows restart is required." | Details list (exit 0 / 3010) | Hooks:715, 717 |
| "Microsoft Visual C++ runtime already present, same or newer — prerequisite satisfied; setup did not reinstall it." | Details list (exit 1638 and registry confirms) | Hooks:747 |
| "CivicCast (Native) setup could not install the required Microsoft Visual C++ runtime (exit code N). See the installer log at C:\ProgramData\CivicCast\install-progress.log." | Dialog, setup exits with the runtime's own code N | Hooks:760 (1638 without registry proof: Hooks:751) |
| "Staging required native component packs from the 'packs' folder next to this installer..." | Details list | Hooks:851 |
| "CivicCast (Native): required native component pack delivery FAILED (exit N) — see the installer log above for the exact missing component(s)." | Details list on failure | Hooks:862 |
| "CivicCast (Native) setup could not obtain a required native component pack. The component pack file(s) are published alongside this installer -- on the same release page, or on the same distribution medium you got setup from. To retry: 1. Obtain the required .ccpack file(s) and put them in a 'packs' folder next to the installer (the same folder this setup .exe is in). 2. Run setup again. Setup safely prepares the partial installation before retrying. Your recordings, database, and settings in C:\ProgramData\CivicCast were not deleted. See the installer log at C:\ProgramData\CivicCast\install-progress.log for the exact missing component(s)." | Dialog, exit 110 | Hooks:867 |
| "Required native component packs staged and verified. Full detail: C:\ProgramData\CivicCast\install-progress.log" | Details list on success | Hooks:889 |
| "Re-verifying the extracted native-server-binaries / native-app-payload / native-ffmpeg-runtime / native-ollama-runtime component pack against its signed pack file (D2)..." | Details list | Hooks:922, 962, 994, 1012 |
| "CivicCast (Native): D2 install-time verification of the extracted <pack> pack FAILED (exit N)." then "The tree at <folder> does not match its signed pack — see the installer log above for the exact mismatched path(s)." | Details list on failure | Hooks:938-939, 974-975, 1001-1002, 1019-1020 |
| "CivicCast (Native) setup could not verify a required native component pack it just extracted against its signed manifest. This usually means disk corruption or an interrupted copy. Re-download the installer/pack and try again; if this persists, contact support with the installer log." | Dialog, exits 111, 112, 121 (same words each time) | Hooks:940, 976, 1003 |
| "CivicCast (Native) setup could not verify the required local-AI runtime pack it just extracted against its signed manifest. Re-download the installer/pack and try again; if this persists, contact support with the installer log." | Dialog, exit 122 | Hooks:1021 |
| "<Pack> component pack verified against its signed pack file (D2). Full detail: C:\ProgramData\CivicCast\install-progress.log" | Details list on success | Hooks:947, 983, 1006, 1024 |
| Log line "step stage-packs: child reported: <text>" | `install-progress.log` | Hooks:861 |

## What really happens
Setup installs the bundled Microsoft Visual C++ runtime, then runs the program's own `--civiccast-stage-packs` step against the folder that holds `setup.exe` (`$EXEDIR\packs`). It needs four signed packs there (server binaries, application, ffmpeg, Ollama) and uses the optional GPU pack if present; each is checked, copied into the install folder and extracted (Hooks:852; `native_pack_staging.rs:99-154`). Nothing is downloaded: no channel address is passed, so a missing pack is a hard stop (Hooks:773-785). Setup then re-checks each extracted folder against its signed pack (steps D2). Any failure stops the CivicCast service, sets it to manual start, shows the dialog and exits with the code. The missing pack names are written to the log line "step stage-packs: child reported:" (Hooks:861), so the dialog's "installer log" claim is true for exit 110. Extraction of tens of gigabytes writes nothing to the list; the log shows a 107 second gap in one run.

## Mismatches
| ID | What it says | What really happens | Severity |
| --- | --- | --- | --- |
| HELP-77 | Dialog 110 plus the folder-page promise (see `installer-nsis-setup-wizard`) imply setup.exe alone can finish | Setup.exe alone stops here with exit 110 every time; there is no download path (Hooks:773-785) | SERIOUS |
| HELP-78 | "for the exact missing component(s)" in a log file | The dialog never names the pack or the folder it searched (`$EXEDIR\packs`). The names are in the log line (Hooks:861) and in the details list | Moderate |
| HELP-79 | "(D2)" and "bootstrap" in list lines | Internal step names | Low |
| HELP-80 | "Re-download the installer/pack" | No address. Manual ch. 10 says the five runtime packs are named on the release page; the roughly 21 GB `station` folder is not a release asset (manual ch. 9), and the kit is handed out by USB or network copy | Moderate |
| HELP-81 | Nothing between "Staging required native component packs..." and the result | Copy plus extraction can be quiet for minutes (log shows a 107 s gap) | Moderate |
| NEW-1 | Dialogs 111, 112 and 121 are word for word identical | The dialog cannot say which pack failed. Only the exit code and the details-list lines (Hooks:938-939 etc.) differ | Moderate |
| NEW-2 | Dialog 110 says getting the `.ccpack` files fixes it | Fixing `packs\` alone on a PC with no earlier install leads to exit 123 after another long wait, because `station\` is also needed (Hooks:1453-1494) | SERIOUS |
| NEW-3 | Dialog 110 says "could not obtain a required native component pack" | Setup uses the same dialog for four causes: required pack missing or untrusted (child 74), optional GPU pack present but untrusted (75), embedded signing key problem (64), report file could not be written (65) (`main.rs:4469-4600`; any non-zero maps to 110, Hooks:856). For 75 the remedy is to replace or remove `native-cuda-runtime.ccpack`; for 64 it is a bad setup.exe | Moderate |

## Proposed text
Details-list lines (now): "Copying and checking the program packs from the 'packs' folder next to setup.exe. This can take several minutes with no change on screen." / "Program packs copied and checked. Details: C:\ProgramData\CivicCast\install-progress.log" / "Re-checking the copied native-server-binaries pack against its signed pack file..." (same pattern for the other three; drop "(D2)").

Dialog 110. Honest text for now (replace Hooks:867; keep under about 990 characters):
> Setup error 110. Setup could not find or trust a program pack it needs. A first install needs the full CivicCast kit: the 'packs' and 'station' folders must sit in the same folder as setup.exe. Setup does not download them. The pack names are on the line 'step stage-packs: child reported' in C:\ProgramData\CivicCast\install-progress.log. To retry: 1. Copy the whole kit folder to this computer (or put the named .ccpack files in a 'packs' folder beside setup.exe, and the 'station' folder too). 2. Run setup again. If the log names native-cuda-runtime.ccpack, delete or replace only that file. Your recordings, database and settings in C:\ProgramData\CivicCast were not deleted. Help: https://github.com/scottconverse/civiccast/issues (remove anything private first).

After fix: add the missing pack names to the dialog itself ("Missing: <names>"): `$1` already holds them (Hooks:854, 861); and if a download path is ever added, replace "Setup does not download them" with the real source.

Dialogs 111, 112, 121, 122 (replace Hooks:940, 976, 1003, 1021, one string each, with the pack named):
> Setup error 111 (122 for the AI engine pack, 121 for the video tools pack, 112 for the application pack). Setup copied the <server tools / application / video tools / AI engine> pack but the copy does not match its signature. This usually means a damaged kit copy, a failing disk or an interrupted copy. Copy the kit folder to this computer again from the original source, check that the drive has free space, then run setup again. Your recordings, database and settings were not deleted. If it fails the same way twice, post the last 20 lines of C:\ProgramData\CivicCast\install-progress.log at https://github.com/scottconverse/civiccast/issues (remove anything private first).

Visual C++ dialog (Hooks:760): keep the text and add what to do, using only what manual ch. 10 supports: "The code N is Microsoft's own code for the runtime installer. Read the last lines of the log, then run setup again. (Code 3010 is not a failure: it only means Windows wants a restart.)"

## Notes for the coder
- The "Setup error N." prefix written at the front of the proposed dialogs is added once by `CIVICCAST_FAIL` (Hooks:420; see `installer-failures-and-logs`). It is shown here so each text reads in full; do not paste it into each string as well. Budget: `NSIS_MAX_STRLEN` is 1,024 and the log wrapper adds about 31 characters, so each edited string (without the prefix) must stay under about 950 characters. Longest proposed text in this file is under that budget unless a note below says otherwise.
- Edit Hooks:762-1025 strings. Test pins in `tests/installer/test_nsis_bootstrap_hooks.py` around lines 240-330 (child output must reach `install-progress.log`; dialog must point at the log), 572-720 (no JSON dump in the pane; success lines must name `install-progress.log`). Keep the words "installer log" in every dialog if the pins are not edited.
- NEW-3 and the free-text pack names need `$1` (already popped) added to the 110 dialog text; mind the 1,024 character NSIS limit (`$1` is already truncated to 1,024).
- NEW-1: use three distinct texts for 111, 112, 121 (a short per-pack noun is enough). Each is its own `CIVICCAST_FAIL` call.
- Needs a code fix, not a text fix: a progress line during extraction (stage-packs is run with `ExecToStack`, so the pane shows nothing until it exits; `native_pack_staging.rs` emits no output by design, Hooks:813-820), and an early "is there a `packs` folder?" check before the long Windows copy.
- Not language-file text: all strings here are in the hooks file.
