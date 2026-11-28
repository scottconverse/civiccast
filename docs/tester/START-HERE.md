> **Current release:** the current public download is v1.0.0-rc.9. For an
> operator run, use its matching packaged manual and built-in Help to install,
> set up and run Public, Education and Government from the normal UI. Use
> [the operator quick start](https://github.com/scottconverse/civiccast/blob/main/docs/QUICKSTART-OPERATOR.md) as the entry point.
> Do not substitute API calls or environment edits for an operator task that
> the manual says is available in the UI. The Beta20 instructions below are historical.

# Tester quickstart (v1.0.0-rc.9)

The minimum machine is 32 GB of RAM.

Use the complete kit from the [v1.0.0-rc.9 release page](https://github.com/scottconverse/civiccast/releases/tag/v1.0.0-rc.9). Check the [RC9 verification record](https://github.com/scottconverse/civiccast/blob/main/docs/releases/v1.0.0-rc.9-verification.md) for the exact signed installer and scoped results. Earlier Beta 20 instructions are kept as history below.

## Install, open, and check

1. **Confirm package identity.** Use the published v1.0.0-rc.9 release and compare its installer with the verification record before testing. The published release holds the downloader `download_windows_release_artifacts.ps1`, `setup.exe`, `SHA256SUMS.txt`, the five `native-*.ccpack` runtime packs, and the `station-models` parts; the downloader assembles them into the `packs\` and `station\` folders below. `setup.exe` SHA-256 is `fd5b9e118b414ed009ed22391c59bbf15ca2f8bddb72af3b70d4a9d889dd168a`.
2. **Prepare the complete kit.** Run the [Windows install guide](https://github.com/scottconverse/civiccast/blob/main/INSTALL-WINDOWS.md) download command, which fetches every part and verifies hashes and the signature; keep `setup.exe`, `packs\`, and `station\`
   together. Compare its manifest, installer hash, and signature with the active
   handoff using [Windows release trust](https://github.com/scottconverse/civiccast/blob/main/docs/install/windows-release-trust.md).
   Stop and report any mismatch before running setup. If Windows shows
   **Windows protected your PC**, use the
   [SmartScreen walkthrough](SMARTSCREEN-WALKTHROUGH.md) after checking the
   package identity.
3. **Install on the named Windows station.** Run `setup.exe` from the kit and
   follow the installer prompts, approving Windows administrator access when
   requested. For an assigned upgrade, back up the station and plan for the
   service interruption; use the handoff's exact upgrade baseline and steps.
   See [Install CivicCast on Windows](https://github.com/scottconverse/civiccast/blob/main/INSTALL-WINDOWS.md) for the station
   requirements and folder layout.
4. **Open the operator console** from the CivicCast shortcut or visit
   <http://127.0.0.1:8000/operator/> on the station computer. On a fresh station,
   name it, create the first administrator, and save the recovery kit. Open
   **System Health** and confirm the station and configured services report
   their current state.
5. **Run the assigned feature checks.** Since Beta 20 the package has recovery station-state
   replacement ACL repair that preserves private-key protection, dependency
   security corrections, bounded relay log retention and efficient draining,
   and CG text wrapping/reduction; long text may render small, so preview at
   the intended resolution. Check the feature paths relevant to this station
   and record what you actually observed, including any incomplete or blocked
   step.
6. **Send useful evidence.** Record the candidate version, package hash, station,
   date and time, steps, results, and relevant logs. Create a redacted
   [support bundle](support-bundle-instructions.md) when requested. Exclude passwords,
   recovery codes, provider secrets, and resident personal information.

## If setup or the station does not work

Preserve the exact error, installer exit code, and
`C:\ProgramData\CivicCast\install-progress.log`. Check the service and
installation logs before retrying. Use the [support guide](https://github.com/scottconverse/civiccast/blob/main/SUPPORT.md) and
[bug report template](bug-report-template.md); report package identity and
station observations separately. Do not infer a cause from a screen or a single
failed step.

For a meeting-night rehearsal, use the
[first broadcast checklist](first-broadcast-checklist.md). For station recovery,
use [recovery kit help](recovery-kit-help.md). The [current user
manual](https://github.com/scottconverse/civiccast/blob/main/docs/USER-MANUAL.md) describes operator features.

## Historical package evidence

The previous `v1.0.0-beta.15` release has [package-specific verification](https://github.com/scottconverse/civiccast/blob/main/docs/releases/v1.0.0-beta.15-verification.md). Its R9 field findings and follow-up work are listed in the
[Beta 17 deferred backlog](https://github.com/scottconverse/civiccast/blob/main/docs/ops/beta17-deferred.md). Those observations do not
establish Beta 20 runtime behavior.

Historical Beta11 evidence is separate from current package checks. A Beta11
host ran Public, Government, and Education together for 72 hours, with 99.78%
live caption-input coverage and 216 healthy scheduled checkpoints. See the
[72-hour host report](https://github.com/scottconverse/civiccast/blob/main/docs/ops/beta11-host-caption-performance-72-hour-2026-10-11.md).

The current release verification record is
[`v1.0.0-rc.9-verification.md`](https://github.com/scottconverse/civiccast/blob/main/docs/releases/v1.0.0-rc.9-verification.md). Older
Beta10 and earlier verification records describe their specific packages and do
not establish Beta 20 results. Current native Windows source is in
[civiccast](https://github.com/scottconverse/civiccast) (formerly `civiccast-native`). The
retired WSL2 product line and its `rc` releases belong to a separate repository
and are not current tester instructions.
