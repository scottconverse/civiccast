# CURRENT — Halo takeover (2026-11-11)

Updated 2026-11-11 (second update, about 17:00 UTC) by Claude (Fable 5.2) on the Halo PC. Owner instruction (Scott, 2026-11-11): Blackwell is down and cannot be restarted remotely; Scott is away for about a week; Claude on Halo takes over as coordinator.

Everything before this date is in [CURRENT-HISTORY-to-2026-11-05.md](CURRENT-HISTORY-to-2026-11-05.md). That file is history. It sets no gates.

## Release state

- Current release: **v1.0.0-rc.5** (published 2026-11-07). Record: `docs/releases/v1.0.0-rc.5-verification.md`.
- On `main` after rc.5 and not released: #313 (false "degraded" health blips), #314 (Gemma 4 summaries), #315 (shared Townlight reader for agenda import), #316 (docs).
- No release, tag or station upgrade is authorized by this note. Each one needs Scott's OK.

## Machines (checked 2026-11-11, about 16:20 UTC)

| Machine | Role | State |
|---|---|---|
| Halo | Coordinator PC and CI runner `HALO-gate-a`. Working clone: `C:\dev\civiccast`. | Online. |
| Beelink (`beelink64gb`) | Test station, rc.5 | Service running, health healthy, captions healthy. |
| R7 (`r7`) | Test station, rc.4 | Service running, health healthy, captions healthy. |
| R9 (`scott@r9-machine`) | Test station, rc.5 | Service running, health healthy, captions healthy. Windows has been up since 2026-11-06 23:26 UTC; the update did not restart it. |
| Blackwell (`nvideablackwell`) | Former coordinator and the only CI runner | Offline since about 2026-11-10 20:00 UTC. |

Windows Update (KB5132061) restarted the Beelink, R7 and Halo on 2026-11-11. That ended the open-ended soaks. The proof is in the rc.5 verification record, version 1.2. Both stations came back on the air with no help.

Automatic Windows updates (policy value `NoAutoUpdate=1`), read back at 16:20 UTC: set on Halo and R9. **Not set on the Beelink and R7.** Scott sets this value; the coordinator does not change Windows settings.

Station logger (`C:\CivicCastTester\soak-logger\`, task `ClaudeCivicCastStationLogger`): on the Beelink and R7 since 2026-11-08, on R9 since 2026-11-11 16:21 UTC. It is the same file on all three (SHA-256 `d1e328c1871a013d…`). R9 has no minute record before that time.

## What is only on Blackwell

- The evidence folders under `E:\CivicCastTester\`.
- The test virtual machines.
- Any work that was not pushed. Nothing on GitHub shows what that is.

When Blackwell is back: push its unpushed work and copy `E:\CivicCastTester\` off it before anything else.

## CI runner and its input files

- Halo is registered as the self-hosted runner `HALO-gate-a` (labels `self-hosted`, `windows`, `sandbox-lab`), set up with `sandbox-lab/runner/Install-GateARunner.ps1`. It starts when `scott` signs in. `blackwell-builder` is still listed and offline.
- Three workflows copy reviewed input files from `C:\CivicCastTester\runtime-artifact-mirror` on the runner. Those files were only on Blackwell. Five of the six are now on Halo, each byte-identical to its pinned SHA-256:
  - the OpenSSL, GLib/libsoup, FFmpeg and security-library archives were rebuilt from files inside the published v1.0.0-rc.5 packs, with the zip settings of the repository's build scripts. Nothing was compiled. The GLib archive needs Python 3.14 (zlib-ng) to come out identical; the other three use Python 3.12;
  - the PyAV wheel is inside the rc.5 `native-app-payload.ccpack` as `payload/WHEELS/av-18.0.0-cp311-abi3-win_amd64.whl`.
  - The scripts are on Halo: `C:\dev\civiccast-ops\rebuild_archives.py` and `glib_variants.py`.
- **Still missing: `python-3.12.16-expat-2.10.0-civiccast-embed-amd64.zip`.** It cannot be rebuilt byte-identical: the build script stores real file times in that zip, and two of its 45 inner files (`python312.zip`, `python312._pth`) are not in the release in their original form. It is on no reachable PC.
  - `ci-test` does not need it. Its runner job passed on Halo on 2026-11-11.
  - `native-app-reproducibility` and the release candidate build (`native-beta-candidate-artifacts`) need it. Those stay blocked until Blackwell is back or Scott approves a new Python runtime archive with new pins.
- Signing is not affected (Azure Trusted Signing, secrets in GitHub).

## Open work on GitHub

- Open pull requests: #155, #175, #202, #232, #258. All are from September or October. None is assigned by this note.
- Open issues: #90, #308, #309, #311.
- Branches with commits that are not on `main`: `fix/summary-gemma-acceptance` (2), `feat/local-audio-description` (3), `fix/rc1-ollama-probe` (4), `fix/rc1-installer-space` (1), `feat/rc1-agenda-offline` (10). Not reviewed.

## Next steps (in order)

1. Scott sets `NoAutoUpdate=1` on the Beelink and R7.
2. Scott decides how to get the Python runtime archive: wait for Blackwell, or approve a new archive with new pins.
3. Scott decides: upgrade R7 to rc.5, restart the Beelink soak, and what the next release holds.
4. Hyper-V is off on Halo. Only the virtual-machine clean-install tests need it. Enabling it and any restart of Halo are Scott's decisions; C: on Halo is BitLocker-encrypted.

## Rules that still apply

Read `CLAUDE.md` and `AGENTS.md`. In short: branch from `main`, Conventional Commits with DCO sign-off, pull requests to `main`, no release or station change without Scott's OK, and docs-only changes do not start CI.
