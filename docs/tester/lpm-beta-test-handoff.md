# CivicCast Beta Test Handoff For Longmont Public Media

> **Archived Beta 10 handoff.** This document records the Beta 10 tester
> packet and is not a current release guide. Beta 20 is the current release;
> use the [current User Manual](../USER-MANUAL.md), [Windows install
> guide](../../INSTALL-WINDOWS.md), [tester start page](START-HERE.md), and
> [Beta 20 verification record](../releases/v1.0.0-beta.20-verification.md).

## Historical Beta 10 Release Record

At the time this handoff was prepared, `v1.0.0-beta.10` was the current
published release (published 2026-10-02); do not treat a tester handoff or USB
kit as a public release. For that historical beta.10 release,
`setup.exe`, the five per-pack runtime `.ccpack` assets, a
`SHA256SUMS.txt` checksum file, and `setup.exe.sidecar.json` metadata are
attached to the
[`v1.0.0-beta.10` GitHub Release](https://github.com/scottconverse/civiccast-native/releases/tag/v1.0.0-beta.10)
as a **pre-release** (a "Beta Candidate", not a production release) -- watch
<https://github.com/scottconverse/civiccast-native/releases> for Sergio's
next check, not `scottconverse/civiccast-wsl-legacy` (the retired, separate WSL2-line
repository) and not any `v1.0.0-rcNN` tag, which belongs to that other
repository. See
[`docs/releases/release-truth.yaml`](../releases/release-truth.yaml) for the
authored release-state record -- it is the single source of truth for which
tag is current.

`v1.0.0-beta.7`, `v1.0.0-beta.5`, `v1.0.0-beta.4` and `v1.0.0-beta.3` (the first
downloadable release) are now superseded but still run; see "Upgrading from
`v1.0.0-beta.1`" below and "Release Build To Use" for the download-only path.
Beta.7 had exact-package upgrade proof from beta.5; that evidence does not
certify beta.10. Upgrade paths were not run for beta.10.

Beta 10 was a beta candidate. Gate A (automated station
acceptance): the clean-install lane passed, 10 of 10 criteria, run locally in
Windows Sandbox on 2026-10-02 against exactly this build; the cross-version
(upgrade) lane and the download-only lane were not run (waived by the owner for
this publication). The human/station acceptance pass is not done. See
[`docs/releases/v1.0.0-beta.10-verification.md`](../releases/v1.0.0-beta.10-verification.md).

If Sergio/LPM is still on a USB-delivered `v1.0.0-beta.1` station, that
install is also superseded but still runs; see "Upgrading from
`v1.0.0-beta.1`" below.

`v1.0.0-beta.2` was **never published**: it exists only as an internal Gate A
upgrade-baseline kit and is never sent to Sergio/LPM.

Use the current Beta 20 manual and install guide linked above instead of following this
archived package procedure for a current installation.

The installer itself is small; the large AI components (caption engine, local
summary and translation model, and on capable hardware an optional
higher-quality caption engine and GPU acceleration) are not inside it. During
install the CivicCast Installer window explains each large component, uses a
copy already on the computer (**Found locally - verified**, for example from a
USB/LAN kit's `station\` folder or an earlier install), and downloads the rest
with a progress display and a **Stop downloading** button. The complete signed
USB/LAN kit (about 21 GB with its `station\` model bundle) is the offline
alternative. A first install with no kit and no earlier install is not yet
proven for beta.10: the setup step that activates the station fails closed
when it cannot find the model packs, so use the kit your handoff names. An
upgrade of an
already-installed `v1.0.0-beta.3`-or-later station
is download-only (`setup.exe` plus the runtime packs, no `station\` folder
needed) and keeps the station's existing recordings, database, and AI
models. Beta.7's verification record documents the tested beta.5-to-beta.7
upgrade (history; not run for beta.10).

**Upgrading from `v1.0.0-beta.1`:** copy the whole `beta.3` kit -- `setup.exe`
plus the `station\` folder beside it (USB or a LAN copy) -- to the station
and run `setup.exe` over the existing `beta.1` install. Your recordings,
settings, database, and AI models are kept and the schema migrates. Do not
run `setup.exe` alone from a `beta.1` install: the `beta.2` internal
baseline kit changed the signed identity of every AI model pack so later
upgrades can reuse them, and a `beta.1` station's already-downloaded models
won't match that new identity unless the full kit is present. From `beta.3`
on, every later step (`beta.3` to `beta.4` and beyond) is a normal
download-only upgrade in place: your recordings, settings, and AI models are
kept. Details:
[`docs/releases/2026-09-02-beta1-to-beta2-fresh-install-only.md`](../releases/2026-09-02-beta1-to-beta2-fresh-install-only.md).

Last updated: 2026-10-02.

Audience: Longmont Public Media beta testers, station operators, technical
staff, and anyone observing the first real station-side CivicCast runs.

## Plain-English Summary

Use only the release-matched installer and manifest for the package you were
actually handed. For a GitHub release, also use its sidecar and checksum; a
USB/LAN kit may have only its own hash-pinned delivery manifest and need not contain
the GitHub sidecar. This handoff was written for the historical Beta 10 GitHub
pre-release (`v1.0.0-beta.2`, `v1.0.0-beta.8` and `v1.0.0-beta.9` were never
published and are never handed to a tester).
Preserve all logs and report any failure.

## What This Beta Is Meant To Exercise

The run is meant to answer:

- Can LPM install CivicCast on a real Windows station machine?
- Can staff complete first setup without developer help?
- Can the operator console run through normal station workflows?
- Can staff run a private rehearsal that copies and validates the exact
  recorded sample they selected, finalizes a private recording, and loads its
  resident preview without publishing it?
- Can staff create or upload recorded media, package it privately, explicitly
  approve Portal publication, and play it in the resident portal?
- What copy, controls, or workflow details confuse real operators?
- What equipment-specific issues appear only at the station?

## Release Build To Use

**Use the exact package named in your active handoff.** A USB/LAN field kit is
verified against its own hash-pinned delivery manifest. A GitHub package is used
only after its exact beta tag is published and is verified against that
release's sidecar and checksum assets. This handoff's release was
[`v1.0.0-beta.10`](https://github.com/scottconverse/civiccast-native/releases/tag/v1.0.0-beta.10)
(a GitHub pre-release, published 2026-10-02); its `setup.exe` is 242,367,640
bytes, to be checked against that release's `SHA256SUMS.txt` and sidecar.

Expected SHA-256 and byte size must match the applicable package manifest.

LPM release candidate artifact details (once a downloadable candidate is
published; a USB-delivered station has no GitHub artifacts to check):

| Field | Value |
| --- | --- |
| Release tag | the exact `v1.0.0-beta.N` tag named in your active handoff |
| Candidate asset source | [GitHub Releases page](https://github.com/scottconverse/civiccast-native/releases) |
| Windows installer | `setup.exe` |
| Checksum file | `SHA256SUMS.txt` |
| Installer sidecar | `setup.exe.sidecar.json` |
| Runtime packs (if applicable) | `*.ccpack` |
| Installer size / SHA-256 | Verify against `SHA256SUMS.txt` and the sidecar for GitHub, or the hash-pinned delivery manifest for USB/LAN; never reuse a copied hash |
| Signature | Valid Authenticode signature from Scott Converse (Azure Trusted Signing; this release chain carries no Sigstore step) |

Optional reference files (proof kit, manifest, PDF, DOCX) must come from that
same release. Do not reuse old hashes.

## Before The Test

Use a Windows 11 machine that LPM can safely use for beta work. Do not run this
on a mission-critical live playout machine unless LPM has explicitly decided
that risk is acceptable.

Before starting:

- Use a local Windows account with administrator rights.
- Keep the Windows session logged in while setup or a test is running.
- Plug the machine into reliable power.
- Disable sleep during the acceptance run.
- Make sure outbound HTTPS is allowed to GitHub.
- Plan disk space for the large AI components (from the USB/LAN kit or
  downloaded by the installer) and the installed runtime/model copy they
  produce, plus the short sample media, recordings, and backups used in the
  test. The installer verifies the signed model packs and composes the local
  Ollama model store; it does not run a further background model download after
  setup.
- Have a place to save the recovery kit that is not a public folder.
- Decide who is allowed to know the beta admin password.
- Decide how LPM will send reports privately to Scott.

Do not paste passwords, recovery codes, provider credentials, private meeting
content, or resident data into bug reports.

## Verify The Installer

A downloadable GitHub release includes a `setup.exe.sidecar.json` file next to
`setup.exe`, and a `SHA256SUMS.txt` listing every asset's hash. A USB/LAN kit
uses its own hash-pinned delivery manifest instead and need not contain the GitHub
sidecar. Always verify against the manifest belonging to the exact package you
received; do not trust a hash copied from anywhere else.

1. For a GitHub download, open `setup.exe.sidecar.json` and note its `sha256`
   value. For USB/LAN, use the kit's hash-pinned delivery manifest.
2. In PowerShell, in the package folder, hash the actual installer. For a
   GitHub download, run:

   ```powershell
   Get-FileHash .\setup.exe -Algorithm SHA256
   ```

   Historical filename example for a beta.5 USB/LAN kit (substitute the exact
   filename from your current handoff, not a guessed beta.10 filename):

   ```powershell
   Get-FileHash '.\CivicCast (Native)_1.0.0-beta.5_x64-setup.exe' -Algorithm SHA256
   ```

3. Compare against the trusted handoff and that exact filename's line in the
   package's `SHA256SUMS.txt`. For GitHub, also compare the sidecar's `sha256`.
   All must match exactly. If they do not, do not
   run the installer. Quarantine the package and ask Scott for a replacement
   proof bundle.

Do not assume the candidate is signed. The active handoff must state the actual
Authenticode status for the exact bytes. If it says `Valid`, confirm the named
publisher. If it says `NotSigned` or the value differs from the handoff, stop;
a local engineering build with `NotSigned` status is not approved for public distribution.
See [SMARTSCREEN-WALKTHROUGH.md](SMARTSCREEN-WALKTHROUGH.md) for the conditional
signature checks.

## Install And First Setup

1. Run the exact installer filename named in the active LPM handoff after
   completing its hash and signature checks: GitHub downloads use `setup.exe`;
   USB/LAN kits use the filename in their own delivery manifest. Do not use
   the historical beta.5 example above to identify a different candidate.
2. Approve expected Windows prompts.
3. Let the installer prepare CivicCast. This step can take several minutes,
   but the screen must keep showing the current phase, step, elapsed time,
   and a regularly updating heartbeat. It must explicitly say if a restart is
   required. Missing or stale feedback is itself a defect; capture it
   immediately. (Report a permanent freeze or crash if the window never
   recovers.)
4. If Windows requires a reboot, record the exact prompt and reboot reason.
   After reboot, log back into the same Windows account and continue.
5. When the installer provides the operator console URL, open that URL exactly.
6. Create the first local admin.
7. Save or print the recovery kit.
8. Store the recovery kit somewhere LPM controls.
9. Confirm the operator console opens.
10. Open System Health. Install, storage, service, and database blockers must
    not be red. In the stock build, **Source preview unavailable** and disabled live
    start are expected because no production media probe is bundled.
11. After setup says it is done, record whether the setup window closed normally.
    If a CivicCast setup window remains open or Windows still shows a CivicCast
    setup process after the operator console is healthy, capture a screenshot and
    report it with the test notes.

Record:

- install start time;
- install finish time;
- whether a reboot happened;
- CivicCast version shown in the UI or health page;
- whether first admin setup succeeded;
- whether the recovery kit was saved;
- whether the setup window closed normally;
- any warning or error text.

Expected version: the exact tag named in your active handoff (`v1.0.0-beta.1`
for a USB-delivered station, or the exact `v1.0.0-beta.N` tag for a
downloaded candidate).

## First-Day Smoke Test

Run this before any long soak.

### Setup And Health

- Confirm the operator console opens after closing and reopening the browser.
- Sign out and sign back in with the admin account.
- Confirm System Health opens.
- Create a support bundle, download the JSON file to Windows, and record its
  displayed SHA-256.
- Verify backup destination if LPM has one ready.
- Run restore rehearsal if the UI offers it.

### Source Or Test Media

Use an uploaded test clip or bundled/sample media. The stock build has no production
server-side media probe, so do not treat camera, NDI, SDI, encoder, preview, or
live-start behavior as part of this acceptance run. Record the expected
**Source preview unavailable** state and confirm live start remains disabled.

### Recording

- Create or upload sample recorded media.
- Run **Private rehearsal** and confirm the result identifies the exact sample,
  a finalized private recording, and a loaded resident preview.
- Keep it short. Packaging has no durable return-later job progress, so
  production-length recordings are outside this first acceptance run.
- Validate it and choose **Package for playback** as an authorized publish
  operator or setup administrator.
- Confirm the recording remains private before approval.
- Approve only the Portal surface.
- Confirm resident playback works after approval.

### Resident View

- Open the resident/public view in another browser.
- Confirm the station name and current state look right.
- Confirm the approved recording plays; confirm unapproved media remains private.

## Recommended LPM Beta Runs

### Run 1: Clean Install And First Setup

Goal: prove LPM can install and set up CivicCast without developer intervention.

Pass if:

- installer hash matched;
- installer completed;
- operator console opened;
- first admin was created;
- recovery kit was saved;
- System Health opens;
- no blocking setup error remains.

### Run 2: Operator Workflow Rehearsal

Goal: walk through normal staff behavior.

Use a staff member who did not build the product if possible.

Cover:

- sign in;
- station setup review;
- confirm the expected source-preview-unavailable state and disabled live start;
- create or upload a short sample recording;
- run **Private rehearsal** and confirm the result names that exact sample, a
  finalized private recording, and a loaded resident preview;
- package and review the recording while it is still private;
- approve only the Portal surface and verify resident playback;
- resident preview;
- support bundle creation;
- notes on confusing wording.

Long soaks, SDI/device proof, live source work, and downstream station-output
testing are deliberately deferred. They require a later candidate with a real
server-side source probe and separate station/integrator acceptance.

## What To Capture In Every Report

Use this shape for each report:

```text
Tester:
Date/time:
Machine:
Windows version:
CivicCast version:
Installer filename:
Installer SHA-256 checked: yes/no
Run type: install / smoke / recording / soak / real equipment
What we tried:
What worked:
What failed:
Exact error text:
Screenshots attached: yes/no
Downloaded support bundle filename:
Support bundle SHA-256:
Secrets checked/redacted: yes/no
Would this block another beta run: yes/no
```

## What Counts As A Blocking Issue

Stop and report immediately if:

- the installer hash does not match;
- the installer cannot complete;
- the operator console cannot open;
- first admin setup fails;
- login fails after first admin setup;
- the recovery kit cannot be saved;
- System Health cannot open;
- CivicCast reports the wrong version;
- a recording cannot be created or finalized;
- the app loses health during a soak;
- the UI freezes or crashes;
- a support bundle exposes secrets;
- a reboot happens during a soak without being planned and recorded.

## What Is Useful Feedback Even If It Is Not Blocking

Please report:

- unclear wording;
- buttons or screens that feel out of order;
- missing station-specific terms;
- workflows that require too many clicks;
- places where staff do not know what to do next;
- logs or support-bundle steps that feel too technical;
- places where LPM would need a checklist, label, or printed note.

Those are valuable beta findings even when the software technically works.

## Privacy And Secret Handling

Never send:

- admin passwords;
- recovery codes;
- provider credentials;
- private keys;
- resident information;
- private meeting content;
- unredacted support bundles in public channels.

Screenshots are fine only after checking that they do not show secrets or
private content.

## If Something Goes Wrong

1. Stop the test at the safest point.
2. Write down the exact on-screen wording.
3. Take a screenshot if it does not expose secrets.
4. Create a support bundle from System Health if possible, then use **Download
   support bundle** to save its JSON file on Windows.
5. Fill out the report shape above.
6. Send it through the private beta channel Scott provided.

Do not try to repair by installing developer tools, running from source, editing
runtime files, or downloading a different release unless Scott explicitly asks
for that.

## Reference Docs

- Tester start page: `docs/tester/START-HERE.md`
- First broadcast checklist: `docs/tester/first-broadcast-checklist.md`
- Support bundle instructions: `docs/tester/support-bundle-instructions.md`
- Bug report template: `docs/tester/bug-report-template.md`
- User manual: `docs/USER-MANUAL.md`
- Release-state record: `docs/releases/release-truth.yaml`
- Windows release trust: `docs/install/windows-release-trust.md`

## Current Beta Boundary

Use only the exact release named in your active handoff, verified against its
own checksum and sidecar files before running it. The stock build must show
**Source preview unavailable** and keep live start disabled without an
integrator-provided media probe. The bounded acceptance path is recorded
media -> exact-sample private rehearsal -> private package -> Portal approval
-> resident playback. The recovery drill covers the database only, not media,
configuration, or credentials.

Treat the beta as controlled evaluation software until the LPM station-device
evidence is complete.

---

## Historical: retired WSL2-line (rc13-rc18) handoff

Everything below describes the retired public WSL2 line
(`v1.0.0-rc18` and earlier, repository `scottconverse/civiccast-wsl-legacy`) that this
repository does not carry. It is preserved as historical evidence only, not
as current tester guidance. The verification document it used to cite
(`docs/releases/v1.0.0-rc17-verification.md`,
`docs/releases/v1.0.0-rc18-verification.md`) is not present in this
repository -- it is omitted below rather than linked, because it does not
exist on `main`.

<details>
<summary>Expand: retired WSL2-line (rc13-rc18) handoff notes</summary>

`v1.0.0-rc18` was the published controlled beta on that other line; do not
install the withdrawn `v1.0.0-rc13`. The earlier LPM clean install failed,
and a later genuine clean-Windows run reproduced bootstrap and user-feedback
defects. That line carried rc15's clean-Windows repairs (which passed
exact-public validation), rc16's published UI/UX repairs, the six audited
rc17 beta-blocker fixes, and rc18's sixteen stage-gate remediations.

The exact rc18 installer passed a clean-host install, launch, reinstall,
uninstall and rc17-to-rc18 upgrade on a pristine Windows 11 guest, plus an
interactive installer walkthrough. The full product path on a clean host was
last proven against rc17's exact bytes on 2026-07-20.

LPM release candidate artifact details on that line:

| Field | Value |
| --- | --- |
| Release tag | `v1.0.0-rc18` |
| Windows installer | `civiccast-1.0.0-rc18-windows-setup.exe` |
| Release manifest | `civiccast-1.0.0-rc18-release-artifacts-manifest.json` |
| Signature | Valid Authenticode signature from Scott Converse |

Known beta diagnostic note: the older R19 release (an earlier internal release
tag, predating the rc numbering) could show `schema=unknown` in packaged
`/health` output. The rc18 candidate included the packaged schema lookup fix.

Known rc15-era installer issue, fixed from rc17 onward: a still-open rc15
installer could briefly show a stale restart screen after setup was already
healthy; closing and reopening it retained saved progress. The published
rc15 assets remain immutable history.

</details>
