# Non-Technical Tester Walkthrough

> **Historical Beta 10 walkthrough.** The procedure below describes Beta 10
> only. For current v1.0.0-rc.9 guidance, use the [tester start page](START-HERE.md),
> [Windows install guide](https://github.com/scottconverse/civiccast/blob/main/INSTALL-WINDOWS.md), and [RC9 verification
> record](https://github.com/scottconverse/civiccast/blob/main/docs/releases/v1.0.0-rc.9-verification.md).

Use this path if your job is to see whether CivicCast makes sense for a clerk,
meeting operator, AV helper, or public-access volunteer.

For the final station-side acceptance pass, use
[Public Station Implementation Walkthrough](station-implementation-walkthrough.md)
with the release owner after the release gates and soak evidence are complete.

## Install

> **This walkthrough's release was `v1.0.0-beta.10`** (published
> 2026-10-02, a GitHub pre-release), which was then recorded as `current` in the
> release-truth record. Confirm the exact filename, size, hash, and
> Authenticode publisher against the active handoff before running it.

Beta 10 was a beta candidate: its automated clean-install check
passed, upgrade paths were not run for it, and the human/station acceptance
pass is not done. `v1.0.0-beta.7` is superseded. Do not substitute source code
or an older installer for an assigned candidate test.

1. **For a first install**, use the complete signed USB/LAN kit named in your
   handoff: installer, runtime packs, and `station\` model bundle. A GitHub
   `setup.exe` alone does not carry the large AI components; the installer
   window can download missing ones (with a progress display and a **Stop
   downloading** button), but a first install that relies only on downloads is
   not yet proven for beta.10.
2. **For a GitHub download**, obtain `setup.exe`, its sidecar, and
   `SHA256SUMS.txt` from the exact published release. For USB/LAN, use the
   kit's hash-pinned delivery manifest; do not expect a GitHub sidecar on the USB.
3. Confirm the installer hash and Authenticode publisher against the applicable
   handoff and manifest before running it. Stop on any mismatch.
4. Run the verified setup app. Windows may show **"Windows protected your PC"**.
   Continue only after the exact hash and valid publisher checks above pass. Read
   [SMARTSCREEN-WALKTHROUGH.md](SMARTSCREEN-WALKTHROUGH.md) beforehand so you know
   exactly what to click (it's two clicks: **More info**, then **Run anyway**).
5. Let the setup app finish. Setup can take several minutes, but the
   installer must keep showing the current phase, step, elapsed time, and a
   regularly updating heartbeat. It must explicitly say when a restart is
   required. If feedback stops updating, record it as a failure.
6. When the operator console opens, continue with First setup on the station
   computer. If it does not open automatically, select **Open operator console**
   in CivicCast Installer.

## First Admin

1. Open **Setup**.
2. Enter the station name.
3. Create the first admin username and password.
4. Save or print the recovery kit.
5. Put the recovery kit somewhere the organization controls.

## Backup And Restore

1. In **Setup**, choose a backup folder or drive.
2. Select **Verify backup**.
3. Open **System Health**.
4. Select **Run restore rehearsal**.
5. Confirm the database drill passes and says that media, configuration, and
   credentials are not covered.

## Live Safety Check

1. Open **System Health**.
2. Without a configured server-side media probe (the system that checks your camera/encoder before going live), confirm the screen says
   **Source preview unavailable** and does not allow live start.
3. Do not use this build for a real meeting unless an integrator has separately
   configured and proven the station's live ingest path.

## First Test Meeting

1. Open **Run Meeting**.
2. Choose the source option that matches your real equipment.
3. If no camera is ready, use a sample recording or upload a short test file.
4. Open **System Health** and choose **Run private rehearsal**. Confirm the
   result says the exact sample was copied, a private recording was created,
   and resident preview loaded.
5. Package the uploaded or sample recording.
6. Confirm it is not visible to residents before approval.
7. Approve the Portal publication surface.
8. Open resident preview in another browser and confirm playback.

## If You Get Stuck

1. Open **System Health**.
2. Create a support bundle.
3. Select **Download support bundle** and keep the downloaded JSON file.
4. Use [bug-report-template.md](bug-report-template.md).
5. Do not paste passwords, recovery codes, private keys, provider secrets,
   resident data, or private meeting content into a public report.
