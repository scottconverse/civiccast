> Historical beta.10 audit snapshot dated 2026-10-03; retained for traceability, not current UI or user guidance.

# Setup failures: exit codes and logs (reference; dialogs, `install-progress.log`)

Paths relative to `civiccast/apps/installer/src-tauri/`. Hooks = `nsis-hooks-bootstrap.nsh`. No screen of its own: this page is the in-app help and the shared wording the other installer files use.

## Where the text lives now
- Failure macro and exit codes: Hooks:357-424 (`CIVICCAST_FAIL`), 459-588 (code constants), 261-296 (`CIVICCAST_ALERT`, `CIVICCAST_NOTICE`).
- Log writer: Hooks:201-245 (`CIVICCAST_STEP`; one time-stamped line per step to `C:\ProgramData\CivicCast\install-progress.log`).
- First-run window log button: `src/main.rs:154-160` (`newest_installer_log_path`), `main.rs:138-141`, `main.rs:2954-2970`.
- Per-code dialog text: listed in the other `installer-nsis-*` and `installer-uninstall` files.

## Current text
| String | Where | Source line |
| --- | --- | --- |
| "...contact support with C:\ProgramData\CivicCast\install-progress.log." | Exit-120 dialogs | Hooks:657, 662, 667 |
| "See the installer log at C:\ProgramData\CivicCast\install-progress.log." / "See the installer log for the exact error." / "see the installer log above" | Dialogs and details list | Hooks:751, 760, 1283, 1580, 1589; 862, 1490, 1503, 1573 |
| "postinstall: FAILED, aborting with exit code N" | Last line of the log after any install failure | Hooks:421 |
| "postinstall: FAILURE CONTAINMENT confirmed (service stopped and set to manual start)" / "...NOT confirmed (stop=.. config=..) -- the service may still auto-start onto the new payload" | Log | Hooks:411, 417 |
| "No CivicCast installer log exists yet. Checked: <paths>." | First-run window alert | `main.rs:138-141` |
| "Opened the CivicCast installer log: <path>" | First-run window status | `main.rs:2968` |

## What really happens
Every failure in the install steps goes through one macro: it stops the CivicCast service and sets it to manual start if files were already replaced, shows the dialog (not in a silent run), writes "postinstall: FAILED, aborting with exit code N" and ends setup with that code (Hooks:357-424). The dialog never shows the number; a silent run shows only the number. Uninstall failures (82 and 130 to 134) end with their code but do not write that "FAILED" line (Hooks:1860, 1872, 1882, 2004, 2076, 2084). The log is a plain text file with one line per step start and result; the reasons behind a failure are mostly in the setup window's details list (child programs run with `ExecToLog`), except the missing pack names for exit 110 (Hooks:861). A failure never deletes `C:\ProgramData\CivicCast`. Setup can be run again after any failure: partial installs are rebuilt.

## Mismatches
| ID | What it says | What really happens | Severity |
| --- | --- | --- | --- |
| HELP-105 | Dialogs show text, never the code | About 26 distinct exit codes (82, 110-135) are the only signal in a silent run; no table was available to the operator | SERIOUS |
| HELP-106 | "contact support" | No contact, address or page is named anywhere in setup. The project's only route is its issue page, community run (manual ch. 9) | Moderate |
| HELP-107 | Exit 114: "Use the manual upgrade path with operator acknowledgement" | No such path exists | SERIOUS |
| HELP-108 | Child code 77 is unnamed | `--civiccast-native-uninstall-preflight` returns 77 when uninstall is blocked or the state cannot be read (`main.rs:6240-6242, 6281`); setup shows it as exit 132 (Hooks:1876-1883). Not an install code | Low |
| NEW-1 | "the installer log" means two things | In list lines it means "the list above"; in dialogs it means the file `install-progress.log`. For several failures (67, 66, 118, 119, 115, 117) the file lacks the reason | Moderate |
| NEW-2 | "FAILED, aborting with exit code N" is the way to find the code | The line exists only for install failures, and only if the reader opens the log. Uninstall exits 82 and 130 to 134 leave no code line | Moderate |

## Proposed text
Dialog prefix. Now: add to every dialog, at the front, "Setup error N." (N is the code in the table below). One-line coder change at Hooks:420: `!insertmacro CIVICCAST_ALERT "Setup error ${CODE}.$\r$\n$\r$\n${TEXT}"`; Hooks:262 then writes it to the log too. The uninstall exits call `CIVICCAST_ALERT` directly and need the same prefix at their own call sites.

Support line, one wording everywhere: "Help: https://github.com/scottconverse/civiccast/issues (the project's issue page; community run; remove anything private, such as passwords and recovery codes, before posting)."

In-app help text: how to find the code (now): "Setup shows the error number at the top of the error message. If you ran setup silently, the number is the exit code. For an install, the last lines of C:\ProgramData\CivicCast\install-progress.log also say 'postinstall: FAILED, aborting with exit code N'. To read it: press the Windows key and R, paste C:\ProgramData\CivicCast\install-progress.log and press Enter."

In-app help text: exit codes (now; "log" means the file; "list" means the list in the setup window):
| Code | What happened | What to do |
| --- | --- | --- |
| 0 | Success | Check the install (manual ch. 10) |
| 2 | Setup stopped without its own number | Read the last 20 lines of the log |
| 1602, 1603 and other codes Microsoft gives | The bundled Visual C++ runtime would not install | Read the log, run setup again. 3010 is not a failure: Windows wants a restart |
| 110 | A program pack is missing, damaged, or the GPU pack is damaged | Put the whole kit folder beside setup.exe ('packs' and 'station'). The names are on the log line "step stage-packs: child reported". If it names native-cuda-runtime.ccpack, remove or replace only that file |
| 111, 112, 121, 122 | A copied pack does not match its signature (111 server tools, 112 application, 121 video tools, 122 AI engine) | Copy the kit again from the original source; run setup again |
| 113 | Upgrade failed and its undo failed; service stopped on purpose | Follow C:\ProgramData\CivicCast\upgrade\UPGRADE-RECOVERY.md before starting anything |
| 114 | This release has an irreversible database change; automatic upgrade refused | Do not retry. Report it (no manual procedure exists) |
| 115 | Upgrade tool fault | Read upgrade\upgrade-engine.log and the list |
| 116, 117 | The database could not be created (116), or fault (117) | Read provision\PROVISION-RECOVERY.md and the list |
| 118 | The Windows service could not be registered | Copy the list; run setup as administrator again |
| 119 | The firewall rule could not be created | Allow rule "CivicCast (Native) Portal/API (TCP 8000)" or let setup create it |
| 120 | Setup could not stop an existing CivicCast service, or could not tell if one exists | Stop "CivicCast Native Supervisor" in Services; run setup as administrator. Do not delete C:\ProgramData\CivicCast |
| 123 | Station activation failed. The real cause is in the step code on the log line "step d4-activate-station: returned N": none or 66 pack or index missing, 67 no space or extraction or self-test failed, 78 or 64 or 65 bad setup.exe | Read the list; free space or copy the full kit; or download setup again |
| 124 | The upgrade undid itself; old database intact; station off the air | Read upgrade\upgrade-engine.log; fix; run setup again |
| 125 | Service started but the station is not answering | Read logs\control_plane.log, control_plane-app.log, upgrade\upgrade-engine.log |
| 126 | Windows could not start the service | Read the logs folder and Event Viewer, Windows Logs, Application |
| 127 | Setup cannot tell which CivicCast edition owns this PC | Follow provision\OWNERSHIP-RECOVERY.md |
| 128 | An earlier failed upgrade left a record | Move (do not delete) upgrade\upgrade-journal.json; run setup again |
| 129 | An older setup was run over a newer install | Run the newer setup (the program files were already switched; the service is stopped) |
| 130 | You said No to handing the station to the older CivicCast edition | Nothing was removed |
| 131 | That handover failed | Read the details in the dialog |
| 132 | Uninstall blocked (older edition present, or state unreadable) | Follow the dialog |
| 133 | Service stop not confirmed; program files kept | Stop the service or restart Windows; uninstall again (normally you see 82 instead) |
| 134 | Uninstall incomplete (leftover service, firewall rule or registry entry) | Remove them by hand: Services, Windows Defender Firewall, HKLM\Software\CivicCast\Native |
| 135 | The older CivicCast edition is still installed | Uninstall it in Settings, Apps; run setup again |
| 82 | Uninstall could not stop the service; nothing removed | Stop the service (Services) or restart Windows; uninstall again |

In-app help text: where the logs are (now): setup steps `C:\ProgramData\CivicCast\install-progress.log`; pack report `install-manifest-report-<number>.json` (same folder); upgrade `upgrade\upgrade-engine.log`, `upgrade-journal.json`, `UPGRADE-RECOVERY.md`; database `provision\PROVISION-RECOVERY.md`, `OWNERSHIP-RECOVERY.md`; why the station will not run `logs\supervisor.log`, `control_plane.log`, `control_plane-app.log`, `postgres.log`, `ollama.log`; first-run window `%USERPROFILE%\.civiccast\runtime-host.log` and `installer-state.json` (the only place a download failure's cause is kept). "Which self-test failed" is in the setup window's details list only: right-click inside it, choose Copy Details To Clipboard. Setup never writes the database password to `install-progress.log` (Hooks:1299-1301), but logs can contain folder, user and computer names: remove anything private before posting any log.

After fix: the same table, with "read the list" replaced by the exact log line, once child output is captured into `install-progress.log`.

## Notes for the coder
- Edit `CIVICCAST_FAIL` (Hooks:420) for the prefix; add the code to the uninstall alerts at Hooks:1852, 1869, 1879, 1986, 2067, 2078. Add a "FAILED, aborting with exit code N" breadcrumb to the uninstall paths (Hooks:1860, 1872, 1882, 2004, 2076, 2084).
- Test pins: `tests/policy/test_native_installer_identity.py` pins the failure macro shape (every POSTINSTALL failure must call `CIVICCAST_FAIL`; `CIVICCAST_ALERT` must not appear bare); `tests/installer/test_nsis_bootstrap_hooks.py:1275` measures dialog length against `NSIS_MAX_STRLEN` (1,024) with the 31-character log wrapper; the prefix adds about 20 characters to every dialog, so re-run it and trim any dialog near the limit.
- Needs a code fix, not text: capture child output into the log (`ExecToLog` at Hooks:1137, 1311, 1459, 1467, 1524, 1584); distinct codes for disk space versus self-test (`main.rs:5927-5929`); a real manual-upgrade procedure for 114; a contact address (owner decision).
- UNVERIFIED: that exit 3010 is passed to Windows as a restart prompt rather than ending setup; meaning of child codes 76 and 79 beyond "repair" (`--civiccast-repair`, manual ch. 10: 76 repaired, 79 unrepairable).
