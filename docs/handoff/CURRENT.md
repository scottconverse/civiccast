# CURRENT — Halo takeover (2026-11-11)

Updated 2026-11-11 (third update, about 18:45 UTC) by Claude (Fable 5.2) on the Halo PC. Owner instruction (Scott, 2026-11-11): Blackwell is down and cannot be restarted remotely; Scott is away for about a week; Claude on Halo takes over as coordinator.

Everything before this date is in [CURRENT-HISTORY-to-2026-11-05.md](CURRENT-HISTORY-to-2026-11-05.md). That file is history. It sets no gates.

## Release state

- Current release: **v1.0.0-rc.5** (published 2026-11-07). Record: `docs/releases/v1.0.0-rc.5-verification.md`.
- On `main` after rc.5 and not released: #313 (false "degraded" health blips), #314 (Gemma 4 summaries), #315 (shared Townlight reader for agenda import), #316 (docs).
- No release, tag or station upgrade is authorized by this note. Each one needs Scott's OK.

## Machines (checked 2026-11-11, 18:39 UTC)

| Machine | Role | State |
|---|---|---|
| Halo | Coordinator PC and CI runner `HALO-gate-a`. Working clone: `C:\dev\civiccast`. | Online. |
| Beelink (`beelink64gb`) | Test station, rc.5 | Service running, health healthy, captions healthy. |
| R7 (`r7`) | Test station, rc.4 | Service running, health healthy, captions healthy. |
| R9 (`scott@r9-machine`) | Test station, rc.5 | Service running, health healthy, captions healthy. Windows has been up since 2026-11-06 23:26 UTC; the update did not restart it. |
| Blackwell (`nvideablackwell`) | Former coordinator and the only CI runner | Offline since about 2026-11-10 20:00 UTC. |

Windows Update (KB5132061) restarted the Beelink, R7 and Halo on 2026-11-11. That ended the open-ended soaks. The proof is in the rc.5 verification record, version 1.2. Both stations came back on the air with no help.

Automatic Windows updates (policy value `NoAutoUpdate=1`), read back at 18:39 UTC: set on Halo, the Beelink, R7 and R9. Scott set this value; the coordinator does not change Windows settings.

Station logger (`C:\CivicCastTester\soak-logger\`, task `ClaudeCivicCastStationLogger`): on the Beelink and R7 since 2026-11-08, on R9 since 2026-11-11 16:21 UTC. It is the same file on all three (SHA-256 `d1e328c1871a013d…`). R9 has no minute record before that time.

## What is only on Blackwell

- The evidence folders under `E:\CivicCastTester\`.
- The test virtual machines.
- Any work that was not pushed. Nothing on GitHub shows what that is.

When Blackwell is back: push its unpushed work and copy `E:\CivicCastTester\` off it before anything else.

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

- Open pull requests: #155, #175, #202, #232, #258. All are from September or October. None is assigned by this note.
- Open issues: #90, #308, #309, #311.
- Branches with commits that are not on `main`: `fix/summary-gemma-acceptance` (2), `feat/local-audio-description` (3), `fix/rc1-ollama-probe` (4), `fix/rc1-installer-space` (1), `feat/rc1-agenda-offline` (10). Not reviewed.

## Next steps (in order)

1. Scott decides: upgrade R7 to rc.5, restart the Beelink soak, and what the next release holds.
2. Scott decides: a second copy of the six input files, and the fix for the dead zlib address.
3. Scott decides: install and upgrade tests of a kit built with the new Python runtime archive.
4. Hyper-V is off on Halo. Only the virtual-machine clean-install tests need it. Enabling it and any restart of Halo are Scott's decisions; C: on Halo is BitLocker-encrypted.

## Rules that still apply

Read `CLAUDE.md` and `AGENTS.md`. In short: branch from `main`, Conventional Commits with DCO sign-off, pull requests to `main`, no release or station change without Scott's OK, and docs-only changes do not start CI.
