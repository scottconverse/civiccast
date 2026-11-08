# Known Limitations For Early-Adopter Builds

> **Historical Beta 10 limitation record.** This page records evidence and
> limits for Beta 10 only. Beta 20 is the latest published release. For Beta 20
> operating guidance, use the
> [current User Manual](../USER-MANUAL.md), [Windows install guide](../../INSTALL-WINDOWS.md),
> [tester start page](START-HERE.md), and [Beta 20 verification
> record](../releases/v1.0.0-beta.20-verification.md). The Beta 10 results below
> remain historical and do not establish Beta 20 behavior.

## Release State

`v1.0.0-beta.10` was the published release on 2026-10-02:
`setup.exe` + `.ccpack` runtime packs + `SHA256SUMS.txt`, published as a GitHub
pre-release (a "Beta Candidate", not a production release) at
<https://github.com/scottconverse/civiccast/releases/tag/v1.0.0-beta.10>.
`v1.0.0-beta.7` is superseded history (as are beta.5, beta.4, and
`v1.0.0-beta.3`, the first downloadable release); `v1.0.0-beta.8` and
`v1.0.0-beta.9` were never published.
`v1.0.0-beta.1` (USB-delivered, no GitHub download) is also superseded.
`v1.0.0-beta.2` was never published -- it exists only as an internal Gate A
upgrade-baseline kit.
See [`docs/releases/release-truth.yaml`](../releases/release-truth.yaml) for
the authored release-state record.

The limits below describe Beta 10 and its earlier evidence only.

## Historical Beta 10 Evidence

At the time, `v1.0.0-beta.10` was the current published release (`v1.0.0-beta.8` and
`v1.0.0-beta.9` were never published, and their work is included in it). It is
a GitHub pre-release, not a production release. Its lab evidence is an
eight-hour watched run on a three-channel lab station; see
[`docs/releases/v1.0.0-beta.10-verification.md`](../releases/v1.0.0-beta.10-verification.md).
Gate A (automated station acceptance) for beta.10: the clean-install lane
passed, 10 of 10 criteria, run locally in Windows Sandbox on 2026-10-02 against
exactly this build. The cross-version (upgrade) lane and the download-only lane
were not run; the owner waived them for this publication. The human/station
acceptance pass has not been done, and no human field tester has signed off on
it.

Known limits of beta.10:

- **Two program-change first attempts aborted and corrected themselves** in
  about three seconds on the government channel, with no on-air effect.
- **One known single-frame (0.033 second) video drop** at a part-to-part join
  inside a program.
- **A stretch of a source that is too quiet** for the speech leveling to reach
  -16 LUFS is reported, not leveled.
- **Carried over from beta.9 and not re-tested in the beta.10 run:** a channel
  whose schedule runs out can stop and need a manual start; `/api/health` can
  report healthy while a channel cannot air; the built-in decode-back check
  failed on certain cue timings in beta.9.
- **Not proven:** runs longer than eight hours, operation at a real station, SDI
  hardware and a cable headend.

## Installer Trust

Verify the exact release's SHA-256 and actual Authenticode status (`setup.exe`
against `SHA256SUMS.txt` and `setup.exe.sidecar.json`) against its matching
handoff before installing. Never assume a candidate is signed from its
filename or from a SmartScreen page.

Do not install from repository source ZIPs, tester handoff files, Git LFS-backed
files, email attachments, chat uploads, or issue-comment downloads unless your
organization independently verifies the hash and provenance.

For the Longmont Public Media beta, use only the exact installer and filename
named in the active LPM handoff, and verify its SHA-256 against the matching
release sidecar. Older GitHub prerelease tags remain
historical evidence; they are not automatically the current beta candidate after
a later GauntletGate rebuild.

## WSL Public-Beta Line - Windows Runtime

> **Historical: describes the retired public WSL2 line, not this
> repository.** `civiccast` (formerly `civiccast-native`) ships one product, the native Windows
> station (session-0 Windows service, no WSL) -- see
> [BRANCHES.md](../../BRANCHES.md). This section's `rc15`/WSL2 setup
> behavior belongs to the separate, private `scottconverse/civiccast-wsl-legacy`
> repository and does not apply here. Kept as historical reference pending
> a native-line known-limitations section.

This section applied to the retired rc-numbered WSL product line. The
native Windows product line this repository ships was developed under
ADR 0021 and follows its own exact tester directives and evidence
boundaries.

WSL-line Windows testers run CivicCast services inside WSL2 Ubuntu 24.04. The setup app hides
that layer, but the host still needs Windows 11 support for WSL2 and may need a
reboot during WSL setup.

The stock target is a CivicCast administrator-consent prompt and no visible
helper console. Record any extra PowerShell/WSL console, interactive prompt,
or Welcome window as an installer defect instead of treating it as normal.

> **rc17 and later:** do not expect exactly one prompt. If Windows requires a
> restart mid-setup, the resumed step re-elevates as a fresh process and shows
> the Windows prompt again — that second prompt is expected, not a defect.

One environment limitation remains:
- On **debloated or IT-locked-down Windows images** (Microsoft Store or its
  MSI path removed/broken), Windows cannot install the WSL runtime through
  Windows Update at all — helper setup stops with a message naming the fix:
  IT installs the Windows Subsystem for Linux package directly from
  Microsoft's WSL releases page (github.com/microsoft/WSL), then retries.
  Retrying without that install cannot succeed on such an image.

The first time the operator console opens on a brand-new machine, Microsoft
Edge shows its own welcome screens first (sign-in offers and data-import
questions). Answer or skip them however your organization prefers; the
CivicCast console loads in a tab behind them. If the console tab opened before
you finished Edge's welcome screens and says it could not read setup state,
go back to CivicCast Installer and press "Open operator console" again to get
a fresh tab.

## External Providers

Internet Archive, YouTube, Cloudflare R2, subscriber notifications, local NAS
archive copies, podcast distribution, NDI planning, cable handoff planning,
reference CTV feeds, and ActivityPub are
optional unless station policy requires them. A provider is not ready just
because credentials are saved in Setup; CivicCast requires live or controlled
proof before claiming provider readiness.

## Packaging Duration

The stock acceptance path uses short sample recordings. Packaging is intentionally
limited to one operation at a time and does not yet preserve a durable job or
return-later progress record. Do not use production-length recordings for the
first acceptance run; validate the bounded recorded-media path first.

## Restore, Update, And Rollback

The System Health restore action is a real isolated **database** drill. It
does not restore or verify media, configuration, or credentials. Update
preflight, rollback rehearsal, controlled failed-update proof, and
post-update safe-to-broadcast proof are also part of the software-owned
resilience path. None substitutes for a station's full disaster-recovery
exercise with real archived meetings. (A prior full clean-host database-drill
walkthrough exists for the retired WSL2 line's rc17 bytes -- see
Clean-Machine Proof below -- and does not describe this repository's native
line.)

## Roles And Permissions

The console is organized by Setup, Run Meeting, Review Records, Publish,
Channels, and System Health. Local roles cover common station responsibilities.
Enterprise SSO remains outside the current early-adopter scope.

## On-Air Approval (Commit-to-Air)

CivicCast enforces a Commit-to-Air gate: a schedule item airs only after it is
**committed** (published), not merely scheduled. This is new behavior — if a
program you expected did not air, check that it was committed first.

- **Manually-added schedule items** land in a *scheduled* (draft) state and
  **will not air until you explicitly Commit them to Air** in Channel Ops. A
  scheduled-but-uncommitted item falls to slate at its slot; this is expected,
  not a defect.
- **Auto-schedule rule items** are approved automatically when you compile the
  rule (compiling the rule *is* the approval step), so they air without a
  separate per-item commit.
- The public "Coming Up" schedule shows only committed (published) items, so a
  program you have not yet committed will not appear there.

## Hardware And Platform Proof

CivicCast does not claim SDI, DeckLink, Comcast/headend delivery,
streaming-TV app-store publication, DRM readiness, or broad hardware
compatibility. Those paths need separate station or lab proof on the exact
target. The config-gated real provider adapters (Internet Archive, YouTube,
SMTP) ship behind `CIVICCAST_PROVIDER_*=real` plus credentials; they are
contract-tested without live external calls and have not been field-proven
against the live services.

## Clean-Machine Proof

A prior source snapshot (`v3.2.0-beta1`) passed the local release gate and a
4-hour elapsed contract-lab soak before publication: Lite, Walkthrough, and
Full GauntletGate lanes with 0 blocker / 0 critical / 0 major / 0 minor / 0 nit
findings, and 48 passed / 0 failed soak cycles across the fixed-studio
livestreaming, portable field kit, and digitization/OBS profiles. The native
caption-SEI lane depends on the CivicCast-bundled private GStreamer runtime,
which the installer must extract and verify before claiming readiness.

**Historical: the paragraph below describes the retired public WSL2 line**
(`v1.0.0-rc18` and earlier, repository `scottconverse/civiccast-wsl-legacy`), not this
repository's native Windows line. Its cited verification documents
(`docs/releases/v1.0.0-rc{13,17,18}-verification.md`) are not present in this
repository and are not linked here for that reason. `v1.0.0-beta.1` (the
first native release) was USB-delivered; the current native-line package
evidence is in the [Beta 20 verification
record](../releases/v1.0.0-beta.20-verification.md).

<details>
<summary>Expand: retired WSL2-line (rc13-rc18) clean-machine proof record</summary>

The controlled Windows beta on that other line was `v1.0.0-rc18`. Its
clean-host proof covered packaging, lifecycle and the installer wizard; the
full product walkthrough on a clean host was last completed against rc17's
exact bytes on 2026-07-20 (captions were not exercised in that pass). rc13's
earlier lab-host evidence did not prove a genuine clean-Windows install and
was withdrawn.

</details>

External-provider paths, hardware paths, app-store paths, downstream headend
claims, and broad hardware compatibility need their own exact proof before
CivicCast should claim them.

The Iteration 21 local gate observed one installer lifecycle cleanup issue:
after runtime readiness, Windows still showed a CivicCast setup process even
though the operator console was healthy. Treat that as a beta reporting item
unless it blocks install, first-admin setup, login, or recording.

## Optional Module Empty States

Some optional operator modules can be visible before the station has configured
them. Paywall, CG board, egress channel config, and loudness planning should be
treated as optional/not-configured areas unless the core recording, first-admin,
System Health, or report-download paths also fail. Report any raw not-found
copy or confusing empty state, but do not treat those optional-module empty
states as equivalent to a failed install unless the active handoff says
otherwise.
