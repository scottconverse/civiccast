# Beta 18 Technical Tester Walkthrough (historical)

For the current Beta 20 package and feature paths, start with the
[Beta 20 tester quickstart](START-HERE.md) and its linked manual. This page
preserves the Beta 18 test scope.

Beta 18 adds guided Cablecast station import to CivicCast for native Windows,
building on Beta 17's subscriber delivery, Podcast publication, and
underwriting daypart selection. This walkthrough
covers package acquisition, installation, the release feature paths, upgrade,
and uninstall.

Beta 18 is published. The [package verification record](../releases/v1.0.0-beta.18-verification.md)
identifies the signed kit and its scoped checks.

For the broader station acceptance path, use the
[Public Station Implementation Walkthrough](station-implementation-walkthrough.md)
when the handoff assigns it.

## Package acquisition

1. Acquire the complete kit from the [Beta 18 release page](https://github.com/scottconverse/civiccast/releases/tag/v1.0.0-beta.18).
2. If the handoff names `download_windows_release_artifacts.ps1`, pass its
   exact `-Tag` and `-Version` values with the requested asset options. Do not
   rely on a generic latest download or mix packages from different builds.
3. Verify the installer and pack hashes against the candidate's
   `SHA256SUMS.txt`, verify the setup signature and publisher, and compare the
   sidecar and station-model manifest with the handoff.
4. Do not use repository source ZIPs, tester handoff binaries, or Git LFS-backed
   files for normal installer acquisition.
5. Record the candidate version, source SHA, workflow run, artifact names and
   hashes, Windows edition, and handoff revision.

## Install and service checks

1. On the isolated Windows target named by the handoff, follow the clean-install
   procedure and capture setup's exit code and install log.
2. Complete first-admin setup and preserve the recovery kit outside the test
   station. Confirm the operator console and resident portal load from the
   installed build.
3. Confirm `CivicCastSupervisor` runs as the restricted virtual account
   `NT SERVICE\CivicCastSupervisor`; record the service state and installed
   runtime manifest.
4. Check `/health` and the station schema. A responsive endpoint alone does not
   establish station readiness.
5. Run the handoff's upgrade path from its named baseline, then its uninstall
   path. Record the station-data outcome and any recovery artifacts. Do not use
   a production station.

## Beta 18 feature path

Use a disposable station, short test media, and the current manual for exact
screen names and settings.

Before testing the publishing workflows below, use a Setup-admin account to
open **Import station**. Preview a Cablecast source, review proposed shows and
schedule rows plus conflicts and skips, apply a small batch, and confirm its
result and rollback in **Import history**. This imports metadata only.

1. Publish a packaged local recording to Portal, then use Podcast and verify
   its channel RSS entry, audio enclosure, and retryable failure status.
2. Confirm a resident subscription, publish a recording, and inspect the
   delivery receipts and retry status. Exercise unsubscribe before another send.
3. Select a saved scheduling daypart in an underwriting flight and verify that
   its channel and window appear correctly in the operator console.
4. Keep external delivery limited to the assigned test configuration and
   record the exact candidate version and package identity.

## Optional advanced evidence

Technical testers can collect CLI evidence when useful:

```powershell
civiccast installer beta-handoff --json
civiccast doctor --json
```

Do not share raw environment dumps. Use the support bundle unless a maintainer
asks for a specific redacted command output.

## Historical Beta 10 release state and evidence

`v1.0.0-beta.10` was the current published release when this walkthrough was
written (published 2026-10-02); use only the exact release named
in the handoff. It is `setup.exe` + `.ccpack` runtime packs + `SHA256SUMS.txt`
+ `setup.exe.sidecar.json`, published as a GitHub pre-release (a "Beta
Candidate", not a production release) at
<https://github.com/scottconverse/civiccast/releases/tag/v1.0.0-beta.10>.
`v1.0.0-beta.7` was a download-only upgrade proven for stations already on
`v1.0.0-beta.5`; it is now superseded history, as are beta.5, beta.4 and
`v1.0.0-beta.3` (the first downloadable release).
`v1.0.0-beta.1` (USB-delivered, no GitHub download) is also superseded.
`v1.0.0-beta.2` was never published -- it exists only as an internal Gate A
upgrade-baseline kit.
See [`docs/releases/release-truth.yaml`](../releases/release-truth.yaml) for
the authored release-state record.

Beta 10 was a beta candidate. Gate A (automated station
acceptance): the clean-install lane passed, 10 of 10 criteria, run locally in
Windows Sandbox on 2026-10-02 against exactly this build; the cross-version
(upgrade) lane and the download-only lane were not run (waived by the owner for
this publication). The human/station acceptance pass is not done. Its lab
checks do not replace testing the exact installer you downloaded. See
[`docs/releases/v1.0.0-beta.10-verification.md`](../releases/v1.0.0-beta.10-verification.md)
and
[Known Limitations](known-limitations.md#historical-beta-10-evidence)
for the current captions evidence boundary.
