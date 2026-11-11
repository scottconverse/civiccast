# CURRENT — Halo takeover (2026-11-11)

Updated 2026-11-11 by Claude (Fable 5.2) on the Halo PC. Owner instruction (Scott, 2026-11-11): Blackwell is down and cannot be restarted remotely; Scott is away for about a week; Claude on Halo takes over as coordinator.

Everything before this date is in [CURRENT-HISTORY-to-2026-11-05.md](CURRENT-HISTORY-to-2026-11-05.md). That file is history. It sets no gates.

## Release state

- Current release: **v1.0.0-rc.5** (published 2026-11-07). Record: `docs/releases/v1.0.0-rc.5-verification.md`.
- On `main` after rc.5 and not released: #313 (false "degraded" health blips), #314 (Gemma 4 summaries), #315 (shared Townlight reader for agenda import), #316 (docs).
- No release, tag or station upgrade is authorized by this note. Each one needs Scott's OK.

## Machines (checked 2026-11-11, about 15:50 UTC)

| Machine | Role | State |
|---|---|---|
| Halo | Coordinator PC. Working clone: `C:\dev\civiccast`. | Online. |
| Beelink (`beelink64gb`) | Test station, rc.5 | Service running, health healthy, captions healthy. |
| R7 (`r7`) | Test station, rc.4 | Service running, health healthy, captions healthy. |
| R9 (`r9-machine`) | Test station | Answers a Tailscale ping. SSH from Halo is refused, so its state is not checked. |
| Blackwell (`nvideablackwell`) | Former coordinator and the only CI runner | Offline since about 2026-11-10 20:00 UTC. |

Windows Update (KB5132061) restarted the Beelink, R7 and Halo on 2026-11-11. That ended the open-ended soaks. The proof is in the rc.5 verification record, version 1.2. Both stations came back on the air with no help.

## What is only on Blackwell

- The evidence folders under `E:\CivicCastTester\`.
- The test virtual machines.
- Any work that was not pushed. Nothing on GitHub shows what that is.

When Blackwell is back: push its unpushed work and copy `E:\CivicCastTester\` off it before anything else.

## CI gap

- The repository has one self-hosted runner, `blackwell-builder`. It is offline.
- Twelve workflows have jobs that need it. One is a job in `ci-test`, so a code pull request has a check that cannot finish.
- Signing is not affected (Azure Trusted Signing, secrets in GitHub). The candidate build defaults to GitHub's own Windows machines.
- Plan: register Halo as a runner with `sandbox-lab/runner/Install-GateARunner.ps1`. Not done yet.

## Open work on GitHub

- Open pull requests: #155, #175, #202, #232, #258. All are from September or October. None is assigned by this note.
- Open issues: #90, #308, #309, #311.
- Branches with commits that are not on `main`: `fix/summary-gemma-acceptance` (2), `feat/local-audio-description` (3), `fix/rc1-ollama-probe` (4), `fix/rc1-installer-space` (1), `feat/rc1-agenda-offline` (10). Not reviewed.

## Next steps (in order)

1. Scott adds Halo's SSH key on R9. Then check R9's version and health.
2. Scott turns off forced Windows Update restarts on the station PCs and Halo.
3. Register Halo as the CI runner.
4. Scott decides: upgrade R7 to rc.5, restart the Beelink soak, and what the next release holds.

## Rules that still apply

Read `CLAUDE.md` and `AGENTS.md`. In short: branch from `main`, Conventional Commits with DCO sign-off, pull requests to `main`, no release or station change without Scott's OK, and docs-only changes do not start CI.
