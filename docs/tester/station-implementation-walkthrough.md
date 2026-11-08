# Public Station Implementation Walkthrough

> **Historical: describes the retired public WSL2 line's rc-numbered
> release-artifact workflow, not this repository.** `civiccast` (formerly `civiccast-native`)
> ships one product, the native Windows station (session-0 Windows
> service, no WSL) -- see [BRANCHES.md](../../BRANCHES.md). This
> walkthrough's rc13/rc-artifact-soak process belongs to the separate,
> private `scottconverse/civiccast-wsl-legacy` repository and does not apply here.
> The native line's station-acceptance gate is Gate A -- see
> [docs/ops/gate-a.md](../ops/gate-a.md). For current Beta 20 guidance, use the
> [current User Manual](../USER-MANUAL.md), [Beta 20 verification
> record](../releases/v1.0.0-beta.20-verification.md), and [tester start
> page](START-HERE.md). This remains a historical reference.

Use this walkthrough when CivicCast is ready for a station-side hands-on pass.
It is written for the release owner and station implementer, not for CI. It is
also the checklist that decides whether the product is ready to hand to a public
access TV station for implementation and testing.

## Start Gate

Do not start the station walkthrough until all of these are true:

- The active release PRs are merged or explicitly accepted as release-blocking
  exceptions.
- The final beta release-artifact soak result for the current release candidate
  is present and reviewed.
- Any egress continuity branch for #151 (cable filler-to-program TS continuity)
  is either merged after evidence or explicitly recorded as still pending.
- GitHub release assets match the intended release commit and include the user
  manual PDF, DOCX, and render JSON.
- The Windows installer, release manifest, and SHA-256 checksums are published
  from the release artifact workflow, not from a local build folder.
- No open blocker, critical, major, minor, or nit finding remains in the current
  audit packet unless the release owner has written an explicit waiver.
- The active tester handoff names an approved replacement for withdrawn rc13.

If any item is not true, stop and record the missing evidence. Do not turn this
into a station acceptance test yet.

## Evidence To Collect

Create one folder for the station pass and keep every artifact together:

- downloaded installer filename and byte size,
- release manifest and checksum file,
- computed SHA-256 for the installer,
- screenshots of installer start, WSL/helper progress, service started, first
  admin, recovery kit confirmation with secrets redacted, operator dashboard,
  system health, channel egress, and public portal playback,
- exported support bundle with secrets redacted,
- beta release-artifact soak final result commit hash and summary,
- headend/TSDuck proof output when cable egress is tested,
- notes for every failed, skipped, or waived item.

Use absolute local paths in notes so another reviewer can find the raw evidence.

## Machine Prep

1. Use a clean Windows 11 machine or VM with virtualization available for WSL2.
2. Confirm the machine has current Windows updates and enough free disk space
   for WSL2, media packages, and test recordings.
3. Confirm the station network allows the intended local ports and any required
   provider endpoints.
4. Confirm FFmpeg and TSDuck availability when the headend path will be tested.
5. If testing real providers, prepare station-owned credentials for YouTube,
   Internet Archive, SMTP, CDN, and webhooks. Do not use personal accounts.

## Install And First Run

1. Use the exact installer and matching manifest from the approved replacement
   release named in the active tester handoff. Do not use rc13.
2. Verify the installer hash with PowerShell:

```powershell
Get-FileHash .\civiccast-<version>-windows-setup.exe -Algorithm SHA256
```

3. Compare the hash to the release manifest or checksum asset. It must match.
4. Run the installer by double-clicking the release installer.
5. Let the installer handle WSL2/helper setup. If a reboot is requested, reboot
   and resume from the installed CivicCast app.
6. Confirm the local service reaches healthy state.
7. Open the operator console from the installer handoff.
8. Create the first admin and save the recovery kit in the station-controlled
   location.

## Admin Safety Pass

1. Open System Health.
2. Confirm the storage location is durable and not a temporary test folder.
3. Run backup verification.
4. Run the database restore drill and record that it does not cover media,
   configuration, or credentials.
5. Generate and download a support bundle and confirm secrets are redacted.
6. Confirm emergency stop, safe-to-broadcast status, and visible recovery paths.

## Operator Workflow Pass

1. Create or import a test meeting.
2. Upload or select a short test media source.
3. Confirm the build reports **Source preview unavailable** and blocks preflight/live
   start unless an integrator has configured a supported server-side media probe.
4. Run the stock private rehearsal and confirm it copies and validates that
   exact recorded sample, finalizes a private recording, and loads resident
   preview. This does not enable live ingest.
5. Package the test recording.
6. Confirm the package remains private before approval.
7. Approve only the Portal surface.
8. Confirm the public portal can browse and play the published recording.
9. If a supported live probe is configured, test real-source preflight and live
   finalization as a separate proof lane.

## Cable Egress And Headend Pass

Use this section only when the station has a real or representative downstream
headend path available.

1. Configure the channel profile that matches the station handoff target.
2. Run the built-in egress readiness checks.
3. Start channel output with a filler slate.
4. Schedule a short test program after a filler gap.
5. Capture the transition from filler to program.
6. Verify continuity with TSDuck or the station's analyzer.
7. Record PAT/PMT, PCR, PTS/DTS, bitrate, caption, and audio/video observations.
8. Continue long enough to observe at least one program-to-filler transition.
9. If the downstream analyzer reports discontinuity, treat #151 as not closed
   and attach the raw analyzer output to the issue or PR.

## Provider And Publishing Pass

Run this only with approved station credentials.

1. Configure CDN publishing if the station will use it.
2. Publish a test recording and confirm segments upload before the manifest.
3. Confirm the public URL resolves only after verified upload.
4. Configure SMTP and send a test notification.
5. Configure YouTube or Internet Archive only if the station owns the account.
6. Confirm failures are retryable and do not delete local packages.

## Accessibility And Usability Pass

1. Navigate the installer with keyboard only.
2. Navigate the operator console with keyboard only.
3. Confirm visible focus on primary controls.
4. Confirm error states explain the next action in plain language.
5. Confirm resident-facing pages work at desktop and phone widths.
6. Use a screen reader for at least setup, system health, live room, and public
   playback if the station has an accessibility reviewer available.

## Go / No-Go

Mark the station pass as **go** only when all required evidence is attached and
no unwaived issue remains.

Mark it as **no-go** if any of these happen:

- installer hash mismatch,
- installer cannot reach service healthy on the target machine,
- recovery kit cannot be created or saved,
- backup or restore rehearsal fails,
- support bundle leaks secrets,
- egress transition fails downstream analyzer checks,
- public portal cannot play the published test recording,
- provider failure deletes local media or hides the retry path,
- any unresolved blocker, critical, major, minor, or nit finding remains without
  a written release-owner waiver.

## Final Handoff Packet

Before handing CivicCast to station staff, package:

- release version and commit SHA,
- installer URL and checksum,
- user manual PDF and DOCX,
- station walkthrough evidence folder,
- known limitations and waived items,
- rollback and backup instructions,
- support contact path,
- next scheduled retest date.

The release owner should sign this packet only after reviewing the raw evidence,
not just the summary.
