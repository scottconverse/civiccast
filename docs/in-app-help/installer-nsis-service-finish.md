> Historical beta.10 audit snapshot dated 2026-10-03; retained for traceability, not current UI or user guidance.

# Windows setup: service, firewall rule, shortcuts and finish (NSIS details list and dialogs)

Paths relative to `civiccast/apps/installer/src-tauri/`. Hooks = `nsis-hooks-bootstrap.nsh`.

## Where the text lives now
`NSIS_HOOK_POSTINSTALL`, Hooks:1522-1814. The service work is done by `CivicCast Native.exe --civiccast-register-native-service` (`src/native_service_registration.rs`); its exit codes 83 and 84 are mapped to setup exits 126 and 125 at Hooks:1572-1581. The finish page after the last hook is Tauri's template (not in the repository; UNVERIFIED wording). Dialog text is also copied to the details list and the log by `CIVICCAST_ALERT` (Hooks:261-267).

## Current text
| String | Where | Source line |
| --- | --- | --- |
| "Registering the CivicCast (Native) supervisor service (D4)..." | Details list | Hooks:1523 |
| "CivicCast (Native): the service is running but the station is not serving (exit 84) — see the installer log above." | List, failure | Hooks:1573 |
| Dialog 125: "CivicCast (Native) setup started the station's Windows service, but the station did not come up ready to serve. The most common cause is that the database schema is not the one this version needs, which would make the staff pages return errors. Nothing was deleted. Your recordings, database and settings in C:\ProgramData\CivicCast are intact. The exact reason the station reported is in ...install-progress.log, and the upgrade engine's own record is in ...\upgrade\upgrade-engine.log. Resolve the cause and run setup again." | Exit 125 | Hooks:1574 |
| "CivicCast (Native): the service was registered but would not start (exit 83) — see the installer log above." | List, failure | Hooks:1576 |
| Dialog 126: "CivicCast (Native) setup registered the station's Windows service, but Windows could not start it. This is a startup failure, not a registration failure -- the service exists and can be inspected in services.msc. Nothing was deleted. See C:\ProgramData\CivicCast\logs and the Windows Application event log for the exact startup error, then run setup again." | Exit 126 | Hooks:1577 |
| "CivicCast (Native): D4 service registration FAILED (exit N) — see the installer log above." | List, other non-zero | Hooks:1579 |
| Dialog 118: "CivicCast (Native) setup could not register the CivicCast (Native) Windows service. See the installer log for the exact error." | Exit 118 | Hooks:1580 |
| "Registering the CivicCast (Native) portal/API firewall rule (D4)..." | List | Hooks:1583 |
| "CivicCast (Native): D4 firewall rule registration FAILED (exit N) — see the installer log above." | List | Hooks:1588 |
| Dialog 119: "CivicCast (Native) setup could not create the required inbound firewall rule. See the installer log for the exact error." | Exit 119 | Hooks:1589 |
| "CivicCast (Native): recorded InstalledVersion 1.0.0-beta.10 for the next install/upgrade run." | List | Hooks:1663 |
| "CivicCast (Native) bootstrap install complete: required component packs staged and D2-verified, D3 install/upgrade engine run, PostgreSQL provisioned, service and firewall rule registered." | List, last line of a good install | Hooks:1665 |
| Shortcut files "CivicCast Operator Console" (Start menu folder "CivicCast (Native)" and Desktop) and "CivicCast Public Portal" (Start menu) | Windows | Hooks:1794, 1801, 1808 |
| Service display name "CivicCast Native Supervisor" (name `CivicCastSupervisor`) | Services | `civiccast/native/supervisor/config.py:37-38` |
| Firewall rule "CivicCast (Native) Portal/API (TCP 8000)" | Windows Firewall | `native_service_registration.rs:164` |

## What really happens
Setup registers the Windows service `CivicCastSupervisor` (display name "CivicCast Native Supervisor", runs as LocalSystem, starts automatically, restarts itself after 5, 10 and 30 seconds), starts it, waits up to 120 seconds for Windows to report it running and up to 180 seconds for the web server to answer, then adds an inbound firewall rule for TCP port 8000 (`native_service_registration.rs:164, 498, 697, 926, 2774-2790`; manual ch. 10). It then measures the install folder for the "Apps" size, records the installed version, and writes three web shortcuts (Hooks:1621-1663, 1794-1808). A failed shortcut is only logged (non-fatal). After any failure in this part, setup stops the service and sets it to manual start before showing the dialog (Hooks:384-419).

## Mismatches
| ID | What it says | What really happens | Severity |
| --- | --- | --- | --- |
| HELP-92 | Dialog 125: "database schema ... staff pages return errors" and "Resolve the cause" | Names no action and no log to read for the web server. The useful files are `logs\control_plane.log` and `control_plane-app.log` (manual ch. 10) | Moderate |
| HELP-93 | Dialog 126: "the Windows Application event log" | Does not say how to open it (Event Viewer, Windows Logs, Application) | Moderate |
| HELP-94 | Last line (Hooks:1665): "bootstrap install complete: ... D2-verified, D3 ... D4" | Internal words, and nothing says what to do next: open the console shortcut; create the first administrator there; that page only opens on this computer | Moderate |
| HELP-95 | Dialogs 118 and 119: "See the installer log for the exact error" | No path is named, and the log only has "returned N". The child's error text is in the details list only (`ExecToLog`, Hooks:1524, 1584) | Moderate |
| HELP-96 | Rule name appears in no dialog | An IT person who must add the rule by hand cannot learn the name or port from setup | Low |
| HELP-104 | The three shortcuts look like program icons | They are `.url` web links to `http://127.0.0.1:8000/operator/` and `/`. If the service is stopped they show a browser error and nothing says to start the service | Moderate |
| NEW-1 | List lines say "(exit 84)" / "(exit 83)" but the dialog (and Windows) end with 125 / 126 | Two numbers for one failure. 83 and 84 are inner codes of the service step; 125 and 126 are the codes setup exits with (Hooks:1572-1577) | Moderate |

## Proposed text
Details-list lines (now): "Registering the CivicCast Windows service ('CivicCast Native Supervisor'), starting it and waiting for the station to answer. This can take a few minutes." / "Adding the Windows Firewall rule 'CivicCast (Native) Portal/API (TCP 8000)'..." / "Recorded version 1.0.0-beta.10 for the next install or upgrade." Failure lines: replace "(exit 84)" with "(setup error 125)" and "(exit 83)" with "(setup error 126)"; replace "see the installer log above" with "the reason is in the lines just above this one in this list".

Success line (replaces Hooks:1665): "CivicCast (Native) is installed. The station started and answered, and the Windows service and firewall rule are in place. Next: open 'CivicCast Operator Console' from the Desktop or the Start menu folder 'CivicCast (Native)' on this computer and create the first administrator. That page opens only on this computer. The CivicCast (Native) Setup window opens next; the station does not depend on it."

Dialogs (replace the text; each starts "Setup error N." per `installer-failures-and-logs`; Help = "Help: https://github.com/scottconverse/civiccast/issues (remove anything private first)."):
- 125: "Setup error 125. Setup started the CivicCast service, but the station did not answer as ready. Setup set the service to manual start, so the station is not running. Your recordings, database and settings in C:\ProgramData\CivicCast were not deleted. The most common cause is a database that does not match this version. To find the reason, read the last 50 lines of C:\ProgramData\CivicCast\logs\control_plane.log, control_plane-app.log in the same folder, and C:\ProgramData\CivicCast\upgrade\upgrade-engine.log; the step lines are in install-progress.log. Fix the cause, then run setup again. Help line."
- 126: "Setup error 126. Setup registered the CivicCast service ('CivicCast Native Supervisor') but Windows could not start it. Nothing was deleted. To see why: open C:\ProgramData\CivicCast\logs, and open Event Viewer (Windows key, type Event Viewer), choose Windows Logs, then Application, and look for the newest red Error entries from CivicCastSupervisor. You can also press the Windows key and R, type services.msc and look at the service. Fix the cause, then run setup again. Help line."
- 118: "Setup error 118. Setup could not register the CivicCast Windows service. The exact error is in the setup window's details list: before closing, right-click inside it, choose Copy Details To Clipboard, and keep the text. Run setup again as an administrator. Help line. Log: C:\ProgramData\CivicCast\install-progress.log."
- 119: "Setup error 119. Setup could not create the Windows Firewall rule it needs ('CivicCast (Native) Portal/API (TCP 8000)': inbound, TCP port 8000, any network profile). If your security software or your IT department manages the firewall, ask them to allow that rule or let setup create it. The exact error is in the setup window's details list; copy it before closing. Then run setup again. Help line."

Help text for the shortcuts (in-app help, now): "'CivicCast Operator Console' and 'CivicCast Public Portal' open web pages on this computer (http://127.0.0.1:8000/operator/ and http://127.0.0.1:8000/). If the browser shows an error, the CivicCast service is not running: press the Windows key and R, type services.msc, press Enter, find 'CivicCast Native Supervisor' and choose Start."

## Notes for the coder
- The "Setup error N." prefix written at the front of the proposed dialogs is added once by `CIVICCAST_FAIL` (Hooks:420; see `installer-failures-and-logs`). It is shown here so each text reads in full; do not paste it into each string as well. Budget: `NSIS_MAX_STRLEN` is 1,024 and the log wrapper adds about 31 characters, so each edited string (without the prefix) must stay under about 950 characters. Longest proposed text in this file is under that budget unless a note below says otherwise.
- Edit Hooks:1523, 1573, 1574, 1576, 1577, 1579, 1580, 1583, 1588, 1589, 1663, 1665. No language-file edits.
- Test pins: none found for the 118/119/125/126 wording by string; `tests/installer/test_native_service_start.py` and `tests/policy/test_native_installer_identity.py` pin the step order and exit codes (125, 126 mapping); search both for the strings before editing. Keep each dialog under about 990 characters.
- Needs a code fix, not text: capture the service step's output into `install-progress.log` (`ExecToLog` at Hooks:1524, 1584) so HELP-95 and 118/119 can name a real log; make the shortcuts launch something that starts the service or says it is stopped (HELP-104); HELP-102 (the firewall rule opens port 8000 although the station listens on 127.0.0.1 only) needs a coder answer before any help text promises network access (see `installer-install-layout`).
- UNVERIFIED: what Tauri's finish page says; the exact Start menu folder spelling shown by Windows (`${PRODUCTNAME}`).
