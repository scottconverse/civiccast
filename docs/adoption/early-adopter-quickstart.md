# Early-Adopter Quickstart

> **Historical: describes the retired public WSL2 line, not this
> repository.** `civiccast-native` ships one product, the native Windows
> station (session-0 Windows service, no WSL2) -- see
> [BRANCHES.md](../../BRANCHES.md). The `v1.0.0-rc18` release, its GitHub
> release page, and the `docs/releases/v1.0.0-rc18-verification.md` link
> below belong to the separate, private `scottconverse/civiccast-wsl-legacy`
> repository and do not resolve or apply here. ADR 0021, referenced below
> as "in development," is what this repository now ships. Kept as
> historical reference pending a native-line early-adopter quickstart.

> **Release state (historical): `v1.0.0-rc18` was the published controlled
> beta of the retired WSL2 line.** Its installer was built from the
> gate-cleared `main` of the other repository, Authenticode-signed, and
> proven on a genuinely clean Windows host.

Status (historical): rc18 was the WSL2 line's current controlled beta; rc17 was its rollback target.

This guide covered the (now-retired) Windows 11 + WSL2 public-beta product
line. The native Windows line developed under
[ADR 0021](../adr/0021-native-windows-runtime.md) is what this repository
builds today; no WSL2 setup step or beta-readiness claim from this guide
applies to it.

## Who This Is For

Use this guide if your station, board, nonprofit, school district, community
media team, or public-access partner wants to try CivicCast before a broad
public launch.

Early adopters should have one person who can install Windows software, follow a
checklist, save a recovery kit, and share a support bundle when something fails.

## Current Native Line: What To Actually Use

This whole document is historical (retired WSL2 line). For the native line
this repository ships, read [START-HERE](../tester/START-HERE.md),
[INSTALL-WINDOWS.md](../../INSTALL-WINDOWS.md), and
[Windows Release Trust And Verification](../install/windows-release-trust.md)
instead -- they carry the latest published `v1.0.0-beta.20` release guidance.
The [Beta 20 verification record](../releases/v1.0.0-beta.20-verification.md)
is the current package record for this native line. The historical native
release identities below remain background only.

## Historical: Installer Status (retired WSL2 line)

The paragraphs and numbered steps below describe the retired WSL2 line and
are not instructions for this repository's native product.

`v1.0.0-rc13` was withdrawn from beta use after a genuine clean-Windows run
exposed concurrent bootstrap and missing-feedback defects. `v1.0.0-rc18` was
the most recently published release on that line, carrying rc15's
clean-Windows installer repairs and cold-reboot recovery, rc16's published
UI/UX repairs, the audited rc17 beta-blocker fixes, and the sixteen
stage-gate remediations that defined it. Its exact bytes passed a clean-host
install, launch, reinstall, uninstall and rc17-to-rc18 upgrade on a pristine
Windows 11 machine, and an interactive installer walkthrough. Its GitHub
release page and verification record are not present in this repository --
they belonged to the separate, private `scottconverse/civiccast-wsl-legacy` repository.

## Historical: Before You Install (retired WSL2 line)

1. Confirm this guide explicitly names an approved replacement beta release.
2. Match its Windows setup executable to its supplied checksum and release manifest.
3. Verify the SHA-256 hash.
4. Compare the installer's actual Authenticode status and publisher with the
   active handoff. SmartScreen is not proof by itself. If the handoff says
   the build is signed, confirm its verified publisher. Stop on any mismatch.
5. Keep a copy of the release version and installer filename for any support
   report.

## Historical: First Station Run (retired WSL2 line)

1. Run the setup app.
2. Let the installer prepare WSL2 and open the operator console.
3. Create the first admin account.
4. Save or print the recovery kit.
5. Verify backup.
6. Run the database restore drill from System Health and record its explicit
   media/configuration/credential limits.
7. Open Run Meeting and confirm **Source preview unavailable** shows and
   keeps live start blocked without a server-side media probe.
8. Upload or create a short test recording.
9. Package it and confirm it is still absent from the resident portal.
10. Approve the Portal publication surface.
11. Open the public portal from a second browser and confirm playback.

## What The Retired WSL2-Line Beta Could Claim

- A guided Windows setup path proven by the exact public installer from a
  WSL-disabled baseline through cold-reboot recovery.
- Operator console workflows for meeting setup, private packaging, explicit
  Portal publication, support bundles, a database-scoped restore drill, update
  and rollback readiness, and channel/CTV (Connected TV) software proof.
- Public portal and feed-based resident reach.
- Open-source code and documentation that stations can inspect and operate.

## What The Retired WSL2-Line Beta Did Not Claim

- Roku Channel Store publication or certification.
- SDI, DeckLink, Comcast, or physical headend delivery proof.
- Legal certification for public-records retention or accessibility compliance.
- A managed cloud service.
- A one-for-one replacement claim for every incumbent deployment.
- Stock live ingest or go-on-air without an integrator-supplied server-side
  media probe and separate station proof.
- Full-station restore of media, configuration, or credentials from the
  database restore drill.

## When To Stop And Ask For Help

Stop before a real meeting if System Health says **Do not broadcast yet**, if
backup or restore rehearsal fails, if the resident preview will not load, if the
support bundle cannot be created, or if the installer checksum does not match
the release notes.
