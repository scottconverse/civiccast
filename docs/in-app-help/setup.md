## Operator guidance — RC2 snapshot, October 30, 2026 (historical)

A clean station creates its first administrator and recovery kit. An upgrade or reinstall preserves accounts, including a recovered older service profile. Admin sign-in means setup already exists; use that station’s recovery kit. Reinstalling is not a password reset. Station naming and per-channel branding are separate.

The shipped built-in Help is generated from `docs/manual/src/`; the audit below is historical.

> Historical beta.10 audit snapshot dated 2026-10-03; retained for traceability, not current UI or user guidance.

# First Setup (nav id: setup)

Sidebar: Setup > **First Setup**. Page H1 reads "First setup". The same page is the station's only sign-in page (see `_shell-signin-and-session.md`). Verified against the code at HEAD 0b35aef6 (2026-10-03); manual authority: `docs/manual/src/11-signing-in.md`, `22-configuration.md`.

## Where the help text lives now
All strings are in `civiccast/apps/portal-operator/src/screens/SetupScreen.tsx` (2084 lines) unless the table names `SourceUploadWizard.tsx` (`components/setup/`). Server-supplied text (provider cards, `next_step`, errors) comes from `civiccast/installer/service.py` and `router.py`. Line numbers below were confirmed by opening the file, not copied from the inventory.

## Current text
| String | Where | Source line |
| --- | --- | --- |
| "First setup" / "Create the station identity, first local admin, and recovery kit before a public meeting." | H1 and intro, always shown | SetupScreen.tsx:1647, 1649 |
| "Checking setup state..." / "Could not read setup state. <server text>" | loading / generic failure | 1655, 1720 |
| "Durable storage"; "CivicCast has a local database for meeting records, captions, summaries, and subscriptions." / "Prepare the local database before creating the first admin."; buttons "Prepare storage", "Preparing..."; badge "Storage ready"; "Storage setup failed." | storage card | 591, 594-595, 618, 605, 629 |
| "Create the first admin after storage is ready" / "CivicCast will show the station setup form as soon as the local database is prepared." | placeholder | 1942-1944 |
| Field helps: "The name residents will recognize." / "The person responsible for setup and recovery." / "A local sign-in name for the first admin." / "Use at least 12 characters (n/12)." / "Retype the password above to confirm it." / "A note for your records (e.g. 'printed, stored in the clerk safe') — not a file path. After you submit, you'll get one chance to save or print the recovery kit." / "Optional for local rehearsal; set before public launch." | first-admin form | 1970, 1981, 1995, 2005, 2020, 2031, 2045 |
| "Create first admin"; "First setup failed." | submit / error | 2059, 2064 |
| "Recovery kit ready"; "This screen and the saved/printed copy are the only place your admin password and these codes are ever shown together. ... Save or print now, before doing anything else." | kit panel | 306, 308-310 |
| "Routine sign-in — use every time"; "Print kit"; "Save kit"; "Save kit downloads a plain-text file (including the admin password above) to this browser's Downloads folder. ..." | kit panel | 315, 359, 367, 371-374 |
| "I have saved or printed this kit — the admin password and the recovery codes — and stored it away from this computer." / "Use Print kit or Save kit first."; "Continue to the console" / "Recording confirmation..." | confirm box and button | 384-385, 388, 404 |
| "Setup complete" / "Signed in" / "Account recovered"; "CivicCast saved a fresh console token in this browser for <name>."; "Next step: open System Health and confirm readiness before the meeting." | signed-in card | 551, 555, 558 |
| "First-run defaults"; "Generated station settings recorded during setup."; "Timezone", "Mode" ("Test mode" / "On-air mode"), "Dashboard commissioning", "Sample content: enabled", "Initial schedule: enabled" | defaults card | 468, 470, 483, 489, 495, 436-437, 532-533 |
| "Setup complete"; "<station> already has a first admin and recovery kit."; "Sign in with the local admin password if this browser lost its console token." | signed-out card | 1805, 1807, 1810 |
| "Admin sign-in"; "Routine sign-in — use this every time, with the username and password from your printed or saved recovery kit. Creates a fresh console token for this browser without touching any other browser or device already signed in."; "Sign-in failed." | sign-in card | 1593, 1595-1597, 1635 |
| "Use recovery code"; "Emergency only — if you have the admin password, use Admin sign-in instead. This permanently consumes one of your 8 printed codes ..."; "Recover account" / "Confirm — consume recovery code"; "This permanently consumes one recovery code — only 8 exist for this station. Click Recover account again to confirm."; "Recovery failed." | recovery card | 1834, 1836-1839, 1926, 1910-1911, 1930 |
| "You were signed out" + "This browser's console session is no longer valid — most often because it was the oldest session ..." | notice | 1764, 1766-1770 |
| "Recovery kit never confirmed" + "CivicCast has no record that this station's recovery codes were saved or printed ... If both the password and codes are lost, your local Windows administrator can recover access without wiping the station; see Administrator password reset in the Security and privacy manual chapter."; "I found the kit — it is stored safely" | warning | SetupScreen.tsx recovery-kit warning |
| "This browser's console sign-in is no longer valid" + "The station rejected the console token ... Sign in again with the admin username and password from your recovery kit."; "Sign in again" | stale-token card | 1668, 1670-1672, 1681 |
| "The station is cooling down after too many requests"; "Try again" | rate-limit card | 1696, 1711 |
| "First setup can only be done from the station computer itself"; "This page must be opened in a browser running on the station itself — not in a remote desktop viewer's own separate computer, and not from another computer on the network." | 403 card | 1730, 1732-1734 |
| "Backup destination"; "Choose the folder or drive CivicCast should verify before meetings."; "Verify backup"; "Backup setup failed." | backup card | 682, 684, 720, 725 |
| "CDN concierge (recommended)"; "Your station's internet can serve about 200 viewers directly. For big meetings, CivicCast rents overflow capacity from Cloudflare -- free until the night everyone shows up. ..."; "Cloudflare API token"; "Provision for me" | R2 card | 757, 760-763, 782, 804 |
| "Provider setup"; "Required items protect the local tester path. Optional providers can wait."; "Setup admin role required to save provider details or proof. Readiness remains visible." | provider panel | 920, 922, 931 |
| "Checking provider setup..."; "Provider readiness could not load." | states | 936, 941 |
| "Save provider details" / "Or enter R2 details manually"; "CivicCast stores these locally and never shows secret values again."; "Save details"; "Details saved. Run live proof before marking this provider ready."; "Test connection"; "Save details first, then test that these credentials reach the CDN." | credential form | 1098, 1102, 1137, 1141, 1159, 1162 |
| "Record redacted proof"; "Save only a file path, URL, or release evidence reference after secrets have been removed."; "Evidence reference"; "I reviewed the proof and removed tokens, passwords, private keys, and resident data." | proof box | 1037, 1039, 1043, 1069 |
| "Storage and viewing estimate"; "Hours per meeting", "Meetings per month", "Average viewers"; "Varies by provider"; "...Cloudflare R2 ... charges $0 for egress — a real, current, published price"; "Read more in the manual" | estimate card | 1237, 1244, 1257, 1270, 1293, 1299-1306 |
| "Camera or test media"; "Choose the equipment in the room, or upload a short clip for a no-camera rehearsal."; "Read the full walkthrough in the manual"; badge "not set up yet" | SourceUploadWizard.tsx | 211, 212-213, 225 |
| "Do not paste camera passwords here. Store credentials separately."; "Save meeting source"; "Create sample media"; "Upload test media" | SourceUploadWizard.tsx | 39, 291, 308, 362 |
| "Ready: <id> ... Next step. Open System Health and select Check broadcast readiness, then confirm the resident preview." | SourceUploadWizard.tsx | 116-118, 108 |

## What the screen really does
On a brand-new station it prepares the local database, then creates the single admin account and shows a one-time recovery kit; the console stays locked until the kit is confirmed. On a configured station the same page is the sign-in page (admin sign-in, or a one-time recovery code). Sign-in, recovery and first setup only work from a browser on the station computer itself (other computers get HTTP 403). Once signed in it also holds four tools: camera or test media, a backup-folder write check, a browser-side storage estimate, and provider (cloud, archive, notices) credentials. The new station starts in "Test mode" with three channels, a sample video and a starter schedule item; the label does not change behavior and the console has no switch for it (`22-configuration.md`).

## Mismatches
| ID | What the text says | What really happens | Severity |
| --- | --- | --- | --- |
| HELP-01 / SHELL-07 | H1 "First setup" and "Create the station identity ... before a public meeting." on a station set up long ago | The page is the sign-in page; the sidebar has no "Sign in" entry (SetupScreen.tsx:1647-1649; Sidebar.tsx:100) | blocks work (a returning user must scroll past "Setup complete" to find Admin sign-in) |
| HELP-02 | "CivicCast saved a fresh console token in this browser" (555, 1670, 1596) | "Console token" is jargon; it means "you are signed in" | misleading |
| HELP-03 | "open System Health" (558, 108) | Nav item is "Readiness", page H1 is "Safe to broadcast" | misleading |
| HELP-04 | "Choose the folder or drive CivicCast should verify" / "Verify backup" (684, 720) | Writes, reads and deletes a small test file and remembers the path (service.py:1083). Nothing copies station data there; `last_backup_at` is never written. A green card does not mean a backup exists | misleading (a clerk will think backups are running) |
| HELP-05 | "Run live proof before marking this provider ready." (1141); "Record redacted proof" (1037) | Only Cloudflare R2 and BunnyCDN have a Test connection button. Other providers have only a typed note, stored as typed (service.py:1202-1249); no live test runs | misleading |
| HELP-06 | Fastly/Akamai setup steps say "Run Test connection" (service.py:2253, 2273) | Those cards have no such button (SetupScreen.tsx:53) | misleading |
| HELP-07 | "can serve about 200 viewers directly ... free until the night everyone shows up" (760-763) | Hard-coded number and cost claim with no source in code | misleading |
| HELP-08 | "local tester path" (922) | Internal beta wording | cosmetic |
| HELP-09 | Form never says the station starts in Test mode with sample content and a starter schedule (models.py:349-356) | Test mode changes no behavior and has no switch; sample video and schedule item appear in the public portal unless deleted | misleading |
| HELP-10 | "Do not paste camera passwords here" with an `rtmp://` placeholder (SourceUploadWizard.tsx:39) | Never explains what a stream address is or where to get it | cosmetic |
| HELP-11 | Kit text says "stored it away from this computer" (385) | The saved file holds the admin password in plain text (258, 371) and the text never says not to email it or leave it in a synced Downloads folder | misleading (security) |
| NEW-1 | "Resident portal URL ... set before public launch" (2045) | The value is stored but nothing reads it; the Readiness "Resident portal" row uses `CIVICCAST_RESIDENT_PORTAL_URL` (service.py:3607-3632; manual ch. 11) | misleading |
| NEW-2 | "Camera or test media" next step: "select Check broadcast readiness" (108) | Sample source passes pre-flight with no camera (RESUME gap list); the readiness button creates a private test recording (ch. 8) | misleading |
| NEW-3 | "Read the full walkthrough in the manual" (212) links `your-first-beta-workflow` | That anchor does not exist in the new manual (see `help.md` anchor map) | blocks work (link goes nowhere) |

## Proposed text
Describe beta.10 as it is. "After fix:" lines apply once the code is changed.

**Header (station not set up):** H1 "First setup". Intro: "Create the station's first admin account and recovery kit. Do this once, on the station computer, before the first public meeting. Who can use this: anyone at the station computer, before an admin exists." Add under the form button: "A new station starts in Test mode with three channels (public, education, government), a sample video and a starter schedule item. In this version the Test mode label does not change how the station behaves. Delete the sample video in Assets and the starter schedule item in Schedule before residents see them."
**Header (station already set up):** H1 "Sign in to CivicCast". Intro: "Sign in with the admin username and password from your recovery kit. This only works in a browser on the station computer (or inside a remote session on it). Setup tools are below once you are signed in."
- After fix: separate `/sign-in` page titled "Sign in" and a sidebar entry "Sign in"; First Setup shows only while no admin exists.
- "CivicCast saved a fresh console token in this browser for <name>." -> "You are signed in on this browser as <name>."
- "Next step: open System Health and confirm readiness before the meeting." -> "Next step: open Readiness (under System Health) and check that the station is ready before the meeting."
- Stale-token card: "The station no longer accepts this browser's saved sign-in (<detail>). Nothing is wrong with the station. Click Sign in again, then use Admin sign-in with the username and password from your recovery kit."
- Signed-out notice: "Your sign-in on this browser ended. This usually means newer sign-ins pushed it out (the station keeps 20), or the station's sign-in state was reset. Nothing is wrong with the station. Sign in below."
- Admin sign-in card: "Use this every time. Enter the username and password from your recovery kit. Signing in here does not sign out any other browser. Closing the browser does not sign you out: on a shared computer, click Sign out before you leave."
- 403 card heading: "Sign-in and setup only work on the station computer"; body: "Open CivicCast in a browser that runs on the station computer itself, or inside a remote session on it. Another computer on the network cannot sign in. If you are on the station computer and still see this, report it."
- Recovery card: "Use only if the admin password is lost. Each of the 8 codes works once and sets a new password at once. Other browsers stay signed in. Write the new password down; your printed kit still lists the old one."
- Kit panel: add "The saved file holds the admin password in plain text. Keep it off this computer, do not email it, and do not leave it in a folder that syncs to the internet."
- Recovery kit never confirmed: add "Pressing the button records your word only; it does not check the kit. A Setup admin can make a new set from Station Profile > Security while signed in."
- Resident portal URL help: "Optional. Saved with the station record but not used by anything in this version. Residents' links come from the service setting `CIVICCAST_RESIDENT_PORTAL_URL`; ask your IT person."
- Backup card: "Enter a folder or drive CivicCast can write to. Verify backup writes, reads and deletes a small test file there and remembers the path. It does not copy any station data, and this version makes no backups by itself; see the manual chapter 'Running it day to day'. Use a local path or a share the computer account can write to; a drive letter mapped only in your own session does not exist for the service." After fix: "Backup destination: last backup <time>" once a backup job exists.
- Provider panel intro: "Required items must be ready before a meeting. Optional providers can wait."
- Proof box: heading "Note where your evidence is kept"; text "CivicCast does not run a test here. Type the path or web address of your evidence (a screenshot or a log) after removing passwords and keys. CivicCast saves the text as typed and does not check it." Checkbox: "I removed passwords, keys and personal details from the evidence." Success text: "Details saved. Test the connection if the card has a Test connection button; otherwise record where your evidence is kept." After fix: a real Test connection for Fastly, Akamai and the archive providers, or hide the proof box.
- CDN concierge: "If many residents watch live at once, a content delivery network (CDN) carries the load so the station computer does not. This tool sets one up on Cloudflare R2. You need a free Cloudflare account and one API token (steps below). CivicCast does not store the token. Check Cloudflare's current prices before you start." (Remove the 200-viewer and "free" claims until verified.)
- Stream address help (live sources): "A stream address is a web address your camera app or encoder shows on its own screen, for example rtmp://... . Copy it exactly. Never put a password in this box."
- Sample/test result next step: "Next step: open Readiness (under System Health). Press Check broadcast readiness to run a private test; it saves a test recording."
- Manual link: "Read the walkthrough in the manual" -> `/help#signing-in-first-run` (see `help.md`).

## Notes for the coder
- Edit `SetupScreen.tsx` (header 1641-1651; cards above), `SourceUploadWizard.tsx` (39, 108, 212), `status-language`/`models.py` only for server `next_step` strings (`installer/service.py` provider steps 2096-2312, 2253, 2273).
- Pins: `SetupScreen.test.tsx` (Setup complete, console token, Admin sign-in, Use recovery code, Routine sign-in, You were signed out, Emergency only, Test mode, First-run defaults, Print kit, Save kit, Verify backup); e2e heading `First setup` in `a11y.spec.ts` 101/162/253, `contrast.spec.ts` 273/283/302/314, `channel-app-config.spec.ts` 600, `operator-first-mile.spec.ts` 672, `setup-loopback-only.spec.ts` 74/101, `setup-real-boundary.spec.ts` 198; `SetupScreenLoopbackDenied.test.tsx` 145/156 (403 heading); `operator-first-mile.spec.ts` pins "Run live proof" and the "Open System Health and select" next step; server tests `tests/installer/test_installer_api.py` pin "First setup can only be done..." and "Verify backup".
- Needs a code fix, not a text fix: separate sign-in route and sidebar entry (HELP-01); Fastly/Akamai Test connection (HELP-06); a real backup job or relabel (HELP-04); a Test mode switch or removal of the label (HELP-09); `Resident portal URL` to be read by the Readiness row (NEW-1); dangling anchor `your-first-beta-workflow` (NEW-3, see `help.md`).
- Gate A pin: `sandbox-lab/scripts/In-Sandbox-Report.ps1:3226` (the operator render check) requires the page text to contain "Setup First setup" (sidebar label plus H1), "Open navigation" and "No live meeting broadcast". Renaming the "Setup" nav label or the "First setup" H1 fails the sandbox render check; update that script in the same change.
