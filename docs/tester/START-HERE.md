> **RC2 source update:** The current public download is RC1. For the next RC2
> operator run, use its matching packaged manual and built-in Help to install,
> set up and run Public, Education and Government from the normal UI. Use
> [the operator quick start](../QUICKSTART-OPERATOR.md) as the entry point.
> Do not substitute API calls or environment edits for an operator task that
> the manual says is available in the UI. The Beta20 instructions below are historical.

# Beta 20 tester quickstart

Historical Beta20 testing used the complete kit from the [release page](https://github.com/scottconverse/civiccast/releases/tag/v1.0.0-beta.20). Check the [package verification record](../releases/v1.0.0-beta.20-verification.md) for the exact signed installer and scoped results.

## Install, open, and check

1. **Confirm package identity.** Use the published Beta 20 release and compare its installer with the verification record before testing.
2. **Prepare the complete kit.** Keep `setup.exe`, `packs\\`, and `station\\`
   together. Compare its manifest, installer hash, and signature with the active
   handoff using [Windows release trust](../install/windows-release-trust.md).
   Stop and report any mismatch before running setup. If Windows shows
   **Windows protected your PC**, use the
   [SmartScreen walkthrough](SMARTSCREEN-WALKTHROUGH.md) after checking the
   package identity.
3. **Install on the named Windows station.** Run `setup.exe` from the kit and
   follow the installer prompts, approving Windows administrator access when
   requested. For an assigned upgrade, back up the station and plan for the
   service interruption; use the handoff's exact upgrade baseline and steps.
   See [Install CivicCast on Windows](../../INSTALL-WINDOWS.md) for the station
   requirements and folder layout.
4. **Open the operator console** from the CivicCast shortcut or visit
   <http://127.0.0.1:8000/operator/> on the station computer. On a fresh station,
   name it, create the first administrator, and save the recovery kit. Open
   **System Health** and confirm the station and configured services report
   their current state.
5. **Run the assigned feature checks.** Beta 20 adds recovery station-state
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
`C:\\ProgramData\\CivicCast\\install-progress.log`. Check the service and
installation logs before retrying. Use the [support guide](../../SUPPORT.md) and
[bug report template](bug-report-template.md); report package identity and
station observations separately. Do not infer a cause from a screen or a single
failed step.

For a meeting-night rehearsal, use the
[first broadcast checklist](first-broadcast-checklist.md). For station recovery,
use [recovery kit help](recovery-kit-help.md). The [current user
manual](../USER-MANUAL.md) describes operator features.

## Historical package evidence

The previous `v1.0.0-beta.15` release has [package-specific verification](../releases/v1.0.0-beta.15-verification.md). Its R9 field findings and follow-up work are listed in the
[Beta 17 deferred backlog](../ops/beta17-deferred.md). Those observations do not
establish Beta 20 runtime behavior.

Historical Beta11 evidence is separate from current package checks. A Beta11
host ran Public, Government, and Education together for 72 hours, with 99.78%
live caption-input coverage and 216 healthy scheduled checkpoints. See the
[72-hour host report](../ops/beta11-host-caption-performance-72-hour-2026-10-11.md).

Older Beta10 and earlier verification records are retained in
[`docs/releases`](../releases/). They describe their specific packages and do
not establish Beta 20 results. Current native Windows source is in
[civiccast](https://github.com/scottconverse/civiccast) (formerly `civiccast-native`). The
retired WSL2 product line and its `rc` releases belong to a separate repository
and are not current tester instructions.
