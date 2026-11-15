# CURRENT — Halo coordinator (2026-11-15)

Updated 2026-11-15 by Claude (Opus 5.5) on the Halo PC. Claude on Halo has been coordinator since 2026-11-11 (owner instruction; Blackwell is down). Earlier versions of this note are in git history.

Everything before 2026-11-05 is in [CURRENT-HISTORY-to-2026-11-05.md](CURRENT-HISTORY-to-2026-11-05.md). That file is history. It sets no gates.

## Release state

- Current release: **v1.0.0-rc.5** (published 2026-11-07). Record: `docs/releases/v1.0.0-rc.5-verification.md`.
- Candidate: **v1.0.0-rc.6**, unpublished. Signed kit from source `d74a8c3110618d62b82f25e7383e93258f9a556b`, build run 43576185174. Details: `docs/releases/v1.0.0-rc.6-release-notes.md`.
- No release, tag or station upgrade is authorized by this note. Publishing needs Scott's word "publish".

## Machines (checked 2026-11-15, 07:50 local time)

| Machine | Role | State |
|---|---|---|
| Halo | Coordinator PC and CI runner `HALO-gate-a`. Working clone: `C:\dev\civiccast`. | Online. |
| MSI (`msi-remote`) | Test station, rc.6 `d74a8c31` | Soak done (27.6 h). Shut down 2026-11-15 for travel (owner). Walkthrough not done. |
| 5060 Ti PC (`civic@blackwell-5060ti`) | Test station, rc.6 `d74a8c31` | Soak done (28.4 h). Shut down 2026-11-15 for travel (owner). Walkthrough passed. |
| Beelink (`beelink64gb`) | Test station, rc.6 `d74a8c31` | Running, healthy. Soak continues at the home lab (owner). Walkthrough passed. |
| R9 (`scott@r9-machine`) | Test station, rc.6 `d74a8c31` | Soak done (28.5 h). Shut down 2026-11-15 for travel (owner). Walkthrough passed. |
| R7 (`scott@r7`) | Test station, rc.6 `d74a8c31` (installed over rc.4) | Running, healthy. Soak continues at the home lab (owner). Walkthrough passed. |
| Blackwell (`nvideablackwell`) | Former coordinator | Offline since about 2026-11-10. Not checked since 2026-11-11. |
| 5070 Ti PC (`blackwell-5070ti`) | Not a test station in this round | Tailscale: offline, last seen about 2026-11-11. Cause not checked. |

Soak result (2026-11-14 to 2026-11-15, about 28 h per PC): [docs/evidence/rc6-soak-2026-11-15/SOAK-REPORT.md](../evidence/rc6-soak-2026-11-15/SOAK-REPORT.md). Full logger data and app logs: `C:\CivicCastTester\soak-evidence-rc6-2026-11-15\` on Halo. Soak readings: `C:\CivicCastTester\evidence\rc6-d74a\soak\` on Halo. Install receipts: `C:\CivicCastTester\results\install-rc6-d74a.json` on each station.

## What is only on Blackwell

- The evidence folders under `E:\CivicCastTester\`, the test virtual machines, and any work that was not pushed.

## CI runner and its input files

- Halo is registered as the self-hosted runner `HALO-gate-a` (labels `self-hosted`, `windows`, `sandbox-lab`), set up with `sandbox-lab/runner/Install-GateARunner.ps1`. It starts when `scott` signs in. `blackwell-builder` is still listed and offline.
- Three workflows copy reviewed input files from `C:\CivicCastTester\runtime-artifact-mirror` on the runner. Those files were only on Blackwell. Five of the six were recovered on Halo, each byte-identical to its pinned SHA-256:
  - the OpenSSL, GLib/libsoup, FFmpeg and security-library archives were rebuilt from files inside the published v1.0.0-rc.5 packs, with the zip settings of the repository's build scripts. Nothing was compiled. The GLib archive needs Python 3.14 (zlib-ng) to come out identical; the other three use Python 3.12;
  - the PyAV wheel is inside the rc.5 `native-app-payload.ccpack` as `payload/WHEELS/av-18.0.0-cp311-abi3-win_amd64.whl`.
  - The scripts are on Halo: `C:\dev\civiccast-ops\rebuild_archives.py` and `glib_variants.py`.
- The sixth file, `python-3.12.16-expat-2.10.0-civiccast-embed-amd64.zip`, could not be recovered. Scott approved a new one on 2026-11-11. It was built on Halo with `scripts/build_native_python_runtime.py`, unchanged, from the same pinned sources and the same compiler version (19.50.35730). #320 (merged, `b910cd5b`) moved the pins to it: SHA-256 `00bd6e3fae3e0dd122f363f430aae20e472c6ae4ed68ef7f7435a44d5f693e79`, 11,201,358 bytes.
  - Compared with the old archive: all 598 standard-library members and the patched standard-library hash are the same; 18 of the 45 files are byte-identical; 27 compiled files differ. The cause of those differences was not checked.
  - Build record on Halo: `C:\CivicCastTester\python-runtime-build-2026-11-11\` (archive, provenance file, logs, `SHA256SUMS.txt`).
  - A kit built with this archive has had no install or upgrade test yet. Whether those tests are needed before the next release is Scott's decision.
- All six files are now in `C:\CivicCastTester\runtime-artifact-mirror` on Halo. **Halo holds the only copy.** A second copy needs Scott's OK.
- The build script's zlib download address (`https://zlib.net/zlib132.zip`) is dead (404). The same file, with the pinned hash, came from the zlib GitHub release `v1.3.2`. Changing the address in the script changes `BUILDER_SHA256`; that needs Scott's OK.
- Signing is not affected (Azure Trusted Signing, secrets in GitHub).

## Open work on GitHub

- Open pull requests: this soak-record pull request (2026-11-15).
- Open issues (2026-11-15): #343, #344 (from the soak), #342 (cable captions), #90 (on-air graphics: lower-third banner proven on R9 2026-11-12; station logo, bulletin board and clock not tested on a station), #327 (part 2 fixed by #328; part 1, health during a restart loop, partly fixed). #308, #311 and #330 were closed by the owner on 2026-11-14.
- Other branches that are not on `main`: not checked on 2026-11-14.

## Gate A

Retired as a release requirement (owner decision 2026-11-14). The real test PCs are the station evidence. The workflow no longer starts after a build. See `docs/ops/release-candidates.md`, section 2.

## Owner decisions (2026-11-14)

- No station runs rc.5, so an rc.5 → rc.6 upgrade test is not required.
- The WSL field-proof runbook was deleted (#339). It is dead work.
- Gate A is not required for any release. The real test PCs over Tailscale are the station testing.
- Issues #308, #311 and #330 are closed.

## Next steps (in order)

1. Soak done and recorded (2026-11-15). Beelink and R7 keep soaking; Halo reads them every 20 minutes while this session runs.
2. Soak problems are open issues: #343 (MSI slow health answers), #344 (R7 lost reload held the slate), #342 (two small cable caption faults). Fixing them before publish is Scott's decision.
3. The rc.6 verification record, `docs/releases/v1.0.0-rc.6-verification.md`.
4. The physical-host receipt for `scripts/release/publish_beta_candidate.py` (the old tool for it was on Blackwell), then the dry runs.
5. Publish only on Scott's word "publish".

## Rules that still apply

Read `CLAUDE.md` and `AGENTS.md`. In short: branch from `main`, Conventional Commits with DCO sign-off, pull requests to `main`, no release or station change without Scott's OK, and docs-only changes do not start CI.
