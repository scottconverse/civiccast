> Historical beta.10 audit snapshot dated 2026-10-03; retained for traceability, not current UI or user guidance.

# Windows setup: start, folder page, pre-install checks (NSIS wizard, setup.exe)

Paths below are relative to `civiccast/apps/installer/src-tauri/`. Hooks = `nsis-hooks-bootstrap.nsh`; Lang = `nsis-lang-native-english.nsh`. Authority for behavior: manual chapter `docs/manual/src/21-installing.md`.

## Where the text lives now
- Folder-page sentence: Hooks:199 (`MUI_DIRECTORYPAGE_TEXT_TOP`); space figure declared at Hooks:189 and applied at Hooks:631 (`AddSize 5400000`).
- Maintenance, already-running and WebView2 strings: Lang:32-58 (Tauri stock text, `{{product_name}}` expanded by Tauri).
- Pre-install progress lines and the three refusal dialogs (exit 120): Hooks:651, 657, 662, 667, 693, 697.
- Window/product names: `tauri.native.conf.json:3,5,10`. Page order, "Choose Install Location" and the finish page come from Tauri's NSIS template, which is not in the repository (UNVERIFIED wording).
- Every dialog is also copied into the details list and the log by `CIVICCAST_ALERT` (Hooks:261-267).

## Current text
| String | Where | Source line |
| --- | --- | --- |
| "CivicCast (Native) needs the space shown below on this drive for the program itself. After Setup finishes, the CivicCast setup wizard downloads additional components (captions and AI models) separately -- their sizes are shown individually, before anything downloads, on that wizard's own screen." | Folder page, above the browse box | Hooks:199 |
| "Already Installed" / "CivicCast (Native) 1.0.0-beta.10 is already installed. Select the operation you want to perform and click Next to continue." | Maintenance page | Lang:33-34 |
| "Add/Reinstall components" / "Uninstall CivicCast (Native)" | Maintenance choices | Lang:32, 49 |
| "A newer version of CivicCast (Native) is already installed! It is not recommended that you install an older version. ..." | Maintenance page, newer found | Lang:44 |
| "An older version of CivicCast (Native) is installed on your system. It's recommended that you uninstall the current version before installing. ..." / "Uninstall before installing" / "Do not uninstall" | Maintenance page, older found | Lang:46, 50, 40 |
| "Downgrades are disabled for this installer, can't proceed with the silent installer, please use the graphical interface installer instead." | Silent run over newer install | Lang:47 |
| "CivicCast (Native) is running! Please close it first then try again." / "...is running! Click OK to kill it" / "Failed to kill CivicCast (Native). Please close it first then try again" | Running-app check | Lang:35, 36, 42 |
| "Installing WebView2..." / "WebView2 installed successfully" / "Failed to install WebView2! The app can't run without it. Try restarting the installer." | Details list, only if WebView2 is missing | Lang:43, 57, 52 |
| "Preparing the existing CivicCast (Native) installation for a data-preserving upgrade..." | Details list, upgrade | Hooks:651 |
| "Stopping the existing CivicCast Native bootstrap before installation..." / "No prior CivicCast Native bootstrap process to stop (fresh install)." | Details list | Hooks:693, 697 |
| "CivicCast (Native) setup found an existing installation but could not safely stop its native service (service-stop exit N). Setup stopped before replacing application files. Its service registration, upgrade identity, recordings, database, and settings were not deleted. Retry after resolving the service error; if it persists, contact support with C:\ProgramData\CivicCast\install-progress.log." | Dialog, exit 120 | Hooks:657 |
| "CivicCast (Native) setup found the CivicCastSupervisor service, but the existing CivicCast Native bootstrap is missing from <folder>. ... Do not delete C:\ProgramData\CivicCast ... Repair or remove the broken application installation without deleting application data, then retry, or contact support with ...install-progress.log." | Dialog, exit 120 | Hooks:662 |
| "CivicCast (Native) setup could not safely determine whether the CivicCastSupervisor service exists (service-query exit N). Only Windows service error 1060 definitively means the service is absent ... Retry from an administrator account ..., then contact support with ...install-progress.log if the error persists." | Dialog, exit 120 | Hooks:667 |
| "CivicCast (Native) Setup" | Window title of the first-run window that opens afterwards | `tauri.native.conf.json:10` |

## What really happens
Setup runs as administrator (perMachine, `tauri.native.conf.json:33`). Before replacing any file it checks whether the CivicCast service already exists, stops it gracefully if so, and ends the old program. If the service cannot be stopped, or its state cannot be read, setup stops with exit 120 before changing anything (Hooks:647-668). Everything else the wizard does (packs, database, activation, service) comes from the hooks described in the other installer files, and none of it downloads anything: the first install needs the full kit (`packs` and `station` folders beside `setup.exe`). The declared "Space required" is only the program files plus 5,400,000 KB (Hooks:189); the page does not refuse a drive that is too small, setup refuses later at activation (manual ch. 10).

## Mismatches
| ID | What it says | What really happens | Severity |
| --- | --- | --- | --- |
| HELP-73 (promise) | "After Setup finishes, the CivicCast setup wizard downloads additional components (captions and AI models)" (Hooks:199) | The models are not downloaded by setup. Setup needs `station\` and `packs\` beside `setup.exe`; with `setup.exe` alone it stops at exit 110 (Hooks:850-867), and with `packs\` but no `station\` on a never-installed PC at exit 123 (Hooks:1453-1494). The first-run window's later downloads are a second, separate list | SERIOUS |
| HELP-73 (size) | "needs the space shown below ... for the program itself" | The figure is the installer's own files plus 5,400,000 KB (about 5.1 GiB). Real need is roughly 55 GB (manual ch. 9, derived estimate: two copies of about 21 GB of model packs plus runtime packs plus 2 GB, `native_activation.rs:841`). No check happens on this page | SERIOUS |
| HELP-74 | "contact support with C:\ProgramData\CivicCast\install-progress.log" (Hooks:657, 662, 667) | No contact is given anywhere. The only route is the project's issue page, community run (manual ch. 9) | Moderate |
| HELP-75 | "bootstrap", "upgrade identity", "CivicCastSupervisor service", "service-stop exit N" | Plain meaning: setup could not stop the running CivicCast service, so it changed nothing. A clerk cannot act on the jargon | Moderate |
| HELP-76 | No page says setup is long | Gate A took about 33 minutes from launch to a healthy station in a sandbox (manual ch. 10); the details list can sit still for minutes | Moderate |
| NEW-1 | Exit-120 dialogs say "Retry after resolving the service error" | No dialog says how: open Services (Windows key + R, `services.msc`), stop "CivicCast Native Supervisor", run setup again as administrator | Moderate |
| NEW-2 | "Click OK to kill it" (Lang:36) | Wording is alarming. It force-ends CivicCast (Native), including the first-run window, so the reader should know to save nothing there | Low |

## Proposed text
Folder page (`MUI_DIRECTORYPAGE_TEXT_TOP`; keep under 1,000 characters; a double quote inside must be written `$\"`, a line break `$\r$\n`):

Honest text for now:
> CivicCast (Native) installs from the full CivicCast kit. Run setup.exe from the folder that also holds the 'packs' folder and the 'station' folder. Setup does not download the speech and AI models; it copies them from the kit's 'station' folder. If either folder is missing, setup stops with error 110 (no 'packs' folder) or error 123 (no 'station' folder, and no earlier install on this computer). Plan for about 55 GB of free space on this drive: the 'Space required' figure below counts only the program files. Setup can take longer than half an hour and the list may stay still for several minutes. Do not close it. After it finishes, the CivicCast (Native) Setup window opens. Your recordings, database and settings live in C:\ProgramData\CivicCast and are not changed by the folder you pick here.

After fix (only if setup learns to download the model packs itself): "CivicCast (Native) needs the space shown below on this drive for the program itself. Setup then fetches the speech and AI model packs (about 21 GB) from the internet unless they are in the 'station' folder next to setup.exe. Plan for about 55 GB free. This can take longer than half an hour; do not close setup." Do not use this text until a download path really exists (Hooks:773-785 states none does).

Exit-120 dialogs (replace the first sentence, keep the rest of each text; add the exit number at the front as in `installer-failures-and-logs`):
> 120a: "Setup could not stop the CivicCast service that is already running on this computer, so it has changed nothing. To continue: press the Windows key and R, type services.msc, press Enter, find 'CivicCast Native Supervisor', choose Stop, then run setup again as an administrator. Your recordings, database and settings were not deleted. Help: https://github.com/scottconverse/civiccast/issues (remove anything private first). Log: C:\ProgramData\CivicCast\install-progress.log (service-stop exit N)."
> 120b: "Setup found the CivicCast service but not the CivicCast program that should control it, so it cannot upgrade safely and has changed nothing. Do not delete C:\ProgramData\CivicCast: it holds the station's recordings, database and settings. Remove or repair the old CivicCast (Native) entry in Settings, Apps without deleting that folder, then run setup again. Help and log as above."
> 120c: "Setup could not find out whether the CivicCast service exists (Windows error N), so it has changed nothing. Run setup again from a Windows administrator account. If it still stops, send the last 20 lines of C:\ProgramData\CivicCast\install-progress.log to the project's issue page."

WebView2 and running-app strings: keep; replace "kill" with "close" if the Lang file is edited: "CivicCast (Native) is running. Click OK to close it and continue (anything open in its window will be lost)."

## Notes for the coder
- The "Setup error N." prefix written at the front of the proposed dialogs is added once by `CIVICCAST_FAIL` (Hooks:420; see `installer-failures-and-logs`). It is shown here so each text reads in full; do not paste it into each string as well. Budget: `NSIS_MAX_STRLEN` is 1,024 and the log wrapper adds about 31 characters, so each edited string (without the prefix) must stay under about 950 characters. Longest proposed text in this file is under that budget unless a note below says otherwise.
- Edit Hooks:199 and the three `CIVICCAST_FAIL ${CIVICCAST_EXIT_UPGRADE_QUIESCE}` texts (Hooks:657, 662, 667). `Lang:36` edit needs a Lang-file change (Tauri `customLanguageFiles` replaces the whole file; keep every LangString).
- Test pin: `tests/policy/test_native_installer_identity.py:2957-2977` requires the folder text to contain "download" and "after" (any case). The proposed text keeps both words only as "does not download" / "After it finishes"; update the test docstring and assertion if the meaning changes. NSIS string limit is 1,024 characters; dialog texts are also wrapped by `CIVICCAST_ALERT` (about 31 extra characters in the log write), so keep each under about 990.
- The real fix for HELP-73 is a product decision (host and pin the model packs for a download path, or keep kit-only). Text cannot make `setup.exe` alone work. The pre-flight idea: when `$EXEDIR\packs` is absent, fail at the very first step with a short dialog before copying files, instead of after the long Windows copy.
- Not fixed by text: a free-space check on the folder page that compares against the real need (needs a custom page or `.onVerifyInstDir`).
- The "55 GB" figure is a derived estimate from chapter 9, not a measurement; confirm before shipping.
