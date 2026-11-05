# New PEG operator: install and run three channels

Use the complete signed kit matching the release. The current public download is [v1.0.0-rc.4](https://github.com/scottconverse/civiccast-native/releases/tag/v1.0.0-rc.4), published November 5, 2026; see its [release notes](https://github.com/scottconverse/civiccast-native/blob/main/docs/releases/v1.0.0-rc.4-release-notes.md) for verification and known issues. Start with the [Windows installation guide](https://github.com/scottconverse/civiccast-native/blob/main/INSTALL-WINDOWS.md) and [release-trust guide](https://github.com/scottconverse/civiccast-native/blob/main/docs/install/windows-release-trust.md). In the kit, read `manual/USER-MANUAL.pdf` or use Help in the app.

1. Back up a working station before upgrading. Install only the complete matching signed kit and wait for successful completion. In First Setup, save the recovery kit away from the station.
2. Open the local operator console at `http://127.0.0.1:8000/operator/` on the station.
3. Set Station Profile and station time zone. Configure Public, Education and Government in Channels with intended outputs and distinct branding.
4. Upload local video in Assets; wait for validation, review, and publish.
5. Schedule recordings with actual durations and second-level starts. Approve the schedule, use Commit to Air, and check the Published playout plan.
6. For a repeating rotation, save Repeat for the published sequence on each channel. Only the final contiguous marked group repeats; automatic startup is separate.
7. Start channels; watch moving video, audible audio, and a transition. If live captions are enabled, check visible captions and actionable status. A configured channel is not proof captions keep up on every machine.

Use the Manual for full steps, error recovery, and responsibilities. Preparation is not on-air proof. Do not repeatedly reload an already pending handoff.

For RC4, Windows 11 x64 is the observed platform. The tested 12 GiB/four-vCPU VM is not a certified minimum for three simultaneous captioned channels; Windows 10/Server support is unverified. In the retained sample, two outgoing Education caption pages and two Public pages were generated and admitted but retired at the next program cutoff, with completed display before that cutoff unestablished; they were absent from delivered transport. Recognition reported overload and backlog; the exact bottleneck is unresolved. Check caption delivery at transitions on your configuration. See the RC4 release notes for the measured scope, security findings and remaining limitations before a distribution decision.

---

## Additional workflow reference

# CivicCast Beta 18 - Field-Test Quick Start (historical)

The following section preserves the Beta 18 field-test path only. Use the current
operator instructions above and the manual matching your installed package.

Beta 18 adds guided Cablecast station import to the native Windows station,
building on Beta 17's subscriber delivery, Podcast publication, and
underwriting daypart selection. This quick start
covers the published kit, first setup, recovery, and closed field testing.
The [Beta 18 verification record](releases/v1.0.0-beta.18-verification.md)
identifies its signed build and scoped checks. Use the complete kit from the
[Beta 18 release](https://github.com/scottconverse/civiccast-native/releases/tag/v1.0.0-beta.18).

## Before you begin

1. Read the release-specific installation handoff and the Windows release-trust
   instructions.
2. For a first install or upgrade, use only the candidate kit and procedure
   named by the active handoff. If it names
   `download_windows_release_artifacts.ps1`, pass the exact `-Tag` and
   `-Version` from that handoff with `-Install`. The helper verifies
   `setup.exe`, runtime packs, and the station-model bundle, then runs
   `setup.exe /S`. Do not install `setup.exe` alone, mix releases, or assume
   the source-tree default points to a published download.
3. Before running the helper, compare its SHA-256 with its entry in the
   same-release `SHA256SUMS.txt` using the [Windows release-trust
   instructions](install/windows-release-trust.md). The helper then verifies
   the setup signature and hash, sidecar, runtime-pack hashes, multipart
   station parts and extracted file hashes. Setup checks the signed station
   metadata. Stop if any value differs.

## Install

1. Use the exact release-matched downloader named by the handoff and select
   `-Install`. It stages runtime packs under `packs\`, verifies and extracts
   the station bundle into `station\`, then runs `setup.exe /S`. Keep the
   verified files together; do not substitute a source ZIP, older release or
   generic latest download. The Beta 18 candidate verification record and
   handoff identify the exact source, package, and checks to use.
2. If Windows shows **Windows protected your PC**, choose **More info** and
   confirm the publisher is Scott Converse before continuing. If the publisher,
   filename or hash is wrong, choose **Don't run** and contact your technical
   lead.
3. Keep the PowerShell downloader window open until it reports **Done**. It
   waits for setup and reports a nonzero setup exit as an error. Preserve the
   console output and installer log at
   `%PROGRAMDATA%\CivicCast\install-progress.log` (normally
   `C:\ProgramData\CivicCast\install-progress.log`); do not rerun after a
   failure without technical-lead review.
4. The helper runs setup with `/S`; it does not open the interactive model
   selection window. After setup returns successfully, open **First setup**
   from the **CivicCast Operator Console** shortcut or
   `http://127.0.0.1:8000/operator/`. A successful setup exit alone does not
   prove that first-admin setup or station readiness completed.

## First setup and recovery

1. On the station itself, open **First setup** from the **CivicCast Operator
   Console** shortcut, or by opening `http://127.0.0.1:8000/operator/` in a
   browser on the station.
2. Enter the station name and create the first administrator account.
3. When CivicCast shows the one-time recovery codes, select **Print kit** or
   **Save kit** and store the result away from the computer. Do not put codes
   or passwords in a report. Continue to the console only after the recovery
   kit is safely stored.
4. Confirm **System Health** is green. This is an installation check, not yet a
   beta release or production-readiness claim.

## Beta 18 features to exercise

Use the current manual and candidate handoff for the exact screen names and
steps. Keep these checks on the assigned test station:

- Confirm the supervisor runs as `NT SERVICE\CivicCastSupervisor` and that
  the handoff's installed-runtime checks pass.
- Publish a packaged local recording to Portal, then select or retry Podcast.
  Open its RSS feed and play the MP3 enclosure.
- Confirm a resident subscription and publish a recording. Check the Publish
  card's delivered, queued and failed counts, then retry an eligible failure.
- In Underwriting, select a saved daypart for a flight and verify the selected
  schedule window is shown to the operator.
- As a Setup admin on a disposable station, open **Import station**, preview a
  Cablecast source, review conflicts, then apply and inspect the batch result.
  Confirm rollback removes only that batch's imported rows.

## Field-test boundary

Keep the station private while testing. Run the assigned rehearsal and tester
checks, record the exact candidate SHA and installed version, and wait for the
station owner's cutover decision. Routine steps within the assigned field
test do not need a new approval at every screen. Do not treat reaching the
operator console, an installer exit code of zero, or a version number as
proof that the station is ready for public cutover.

When checking a recording, staff source playback in Summary review and the
Trim & Chapters preview remain private. They use a completed package only when
its source and trim provenance still match; the player maps the package timeline
back to the source. If no verified package is available, trim preview reports
that instead of playing different media. The public portal still requires the
asset to be packaged and explicitly published.

## If the download or setup fails

- Preserve the first failure. Record the release tag, helper filename, time,
  PowerShell output, and setup exit code if reported. Do not rerun setup before
  the technical lead reviews the failure.
- Setup runs with `/S`, so do not wait for the interactive model window or use
  its **Open installer log** button. Check
  `%PROGRAMDATA%\CivicCast\install-progress.log` (normally
  `C:\ProgramData\CivicCast\install-progress.log`) and preserve the downloader
  console output.
- If setup returned zero but the operator console or First setup is unavailable,
  record that separately. Do not treat the installer exit alone as proof that
  the service or station is ready.

## Candidate and published-package evidence

The [Beta 18 verification record](releases/v1.0.0-beta.18-verification.md)
tracks the exact source and any candidate package checks. Use it for the current
status; do not infer a Beta 18 package result from the Beta 16 record.

The [Beta 15 verification record](releases/v1.0.0-beta.15-verification.md)
records one fresh offline Sandbox installation and scoped package checks for
the original signed Beta 15 candidate. It does not claim a normal
cross-version upgrade, extended soak, or automatic late-failure rollback.
Historical reports retain their original release identity.

A historical Beta 11 72-hour run across Public, Government and Education
channels recorded **99.78% live caption-input coverage**. The [72-hour host
report](ops/beta11-host-caption-performance-72-hour-2026-10-11.md) provides the
channel totals and measurement.

Live captions use Whistle CPU as the primary path, with per-channel Whisper
fallback and automatic return to Whistle after recovery. Needle telemetry is
disabled. Supported NVIDIA CUDA systems can accelerate Whisper; AMD or Intel
graphics do not imply Whisper CUDA acceleration. CPU fallback requests allow
120 seconds by default, configurable from 1 to 600 seconds through
`CIVICCAST_WHISPER_FALLBACK_TIMEOUT_SECONDS`.

In **Setup -> AI Models**, the selected model is the saved choice. An active
live-caption runtime continues using the model loaded when the service started;
restart CivicCast after changing the live choice and confirm the active model.
New recorded-caption jobs use the saved choice when they start.

The default native station data root is `%PROGRAMDATA%\CivicCast` (normally
`C:\ProgramData\CivicCast`): recordings and uploads are in `data\uploads`,
the database is in `data\pgdata`, and service logs are in `logs`. The installer
progress log is at the root. These paths follow `%PROGRAMDATA%`, not the signed-in
user's profile.

CivicCast automatically retries Whistle while Whisper supplies
backup captions. Failed probes use a 30-second initial cooldown, then capped
delays of 60, 120, 240 and 300 seconds; retries continue while the affected
channel has audio. It reports a slow warning after 10 seconds, and the
30-second watchdog terminates an unresponsive child. The 210-second queue
bound is `(3 × 2 + 1) × 30` seconds of serialized request allowance, not a
delivery guarantee. The default queue holds up to 42 five-second WAV chunks;
catch-up after sustained overload is finite. Diagnostics are bounded and no
audio archive is created.

See the [Beta 15 verification record](releases/v1.0.0-beta.15-verification.md)
for package scope and limitations. SDI hardware, a cable headend and production
cutover require separate evidence.

## After a successful field test

Keep the signed kit, installer log, recovery-kit confirmation and results
together. Record the exact version and installer hash. Field observations
inform suitability for your station; deployment remains the station owner's
decision. The verification record distinguishes candidate-specific installation
checks from historical station measurements.
