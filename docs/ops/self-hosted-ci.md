# CivicCast Self-Hosted CI

> **Historical runner snapshot:** This page describes the retired WSL2-era
> self-hosted runner configuration. Its runner names, workflow list, and old
> expected test counts are not the current native Windows CI contract. The
> older `634 passed` / 19 real-Postgres figures below are historical counts,
> not current release thresholds. The later
> [`workflow-contract.yaml`](../claims/workflow-contract.yaml) records run
> `29569282094` with 5,673 collected tests (5,643 passed, 30 skipped) and a
> per-producer collection floor of 5,000. That floor checks collection
> completeness; it is not a product-quality or release-acceptance threshold.
> Inspect current workflow definitions and run logs for current CI evidence.

CivicCast PR gates run on Scott's `scott-desktop-wsl` self-hosted runner while
the `scottconverse` GitHub Actions account is on the free-tier hard cap.

## Runner Contract

- Runner label: `scott-desktop`
- Required labels: `self-hosted`, `linux`, `x64`, `scott-desktop`
- Runtime host: WSL2 Ubuntu on Scott's desktop
- Service unit:
  `actions.runner.scottconverse-civiccast.scott-desktop-wsl.service`
- Required host services: Docker Desktop WSL integration, Docker socket,
  Node.js, npm, Python setup action support, apt, Pandoc/TeX install ability
- Security guard: every PR-triggered self-hosted job must skip fork PRs with:

```yaml
if: |
  github.event_name != 'pull_request' ||
  github.event.pull_request.head.repo.full_name == github.repository
```

The repo setting "Require approval for all outside collaborators" is a
defense-in-depth layer. The workflow-level guard is the load-bearing protection
that prevents untrusted fork code from running on Scott's machine.

The runner must run as a systemd service, not as a foreground `./run.sh`
terminal process. If GitHub reports `scott-desktop-wsl` as offline, the correct
first check is:

```powershell
gh api repos/scottconverse/civiccast/actions/runners
wsl -u root bash -lc "cd /root/actions-runner-civiccast && ./svc.sh status"
```

On 2026-05-13, workflow dispatch run `25811390584` was canceled during `Set up
job` because the runner was offline. That was a runner-availability failure, not
a GitHub Actions billing failure. After the service was active and GitHub API
reported `status: online`, run `25816564268` passed on head
`eed7cf93cfbe7d9d520ea5f3938b57a5cde467e3`.

## Historical PR Gates

The following workflows are pinned to the self-hosted runner so PR validation
continues at zero GitHub-hosted runner spend:

- `ci-lint`
- `ci-test`
- `ci-docs`
- `ci-a11y`
- `ci-operator-build`
- `diagnose-blackwell-runtime` (manual only; pinned to `rtx5070` for v0.5
  caption runtime proof)

The job names remain unchanged so branch-protection contexts do not drift.

## Cost Hygiene

- New heavy workflows must include `concurrency.cancel-in-progress`.
- No daily cron is allowed without explicit Scott approval.
- `upload-artifact` retention is seven days unless the artifact is a release
  artifact or Scott explicitly approves longer retention.
- Cleanroom CI builds the Docker image with a shell `docker buildx build`
  command instead of the Docker setup/build GitHub Actions wrappers, and falls
  back to plain `docker build` when a Docker-capable self-hosted runner lacks
  the Buildx plugin. The shell command matches the runner smoke test and avoids
  wrapper-side cancellation on the WSL self-hosted runner.
- Cleanroom image builds do not use GitHub Actions cache export/import. The
  WSL self-hosted runner uses its local Docker/Buildx cache; the GitHub cache
  path canceled during image export on 2026-05-13.
- Use exact released action tags when a moving major tag is unavailable.
- The cleanroom job timeout is 60 minutes. Normal execution is expected to stay
  near 7-10 minutes after the runner accepts the job; the extra time is queue
  slack for WSL wake-up and GitHub self-hosted runner assignment delay.
- `diagnose-blackwell-runtime` is manual-only and must stay pinned to
  `[self-hosted, Linux, X64, scott-desktop, rtx5070, ubuntu-2404]`. It exists
  because normal validation can run on the generic non-NVIDIA runner; the v0.5
  caption gate needs direct evidence from a runner that exposes NVIDIA NVML and
  CUDA.

## Expected Proof

Before claiming a PR head is green, inspect logs for the claimed behavior:

- `ci-test`: `634 passed` and `Real-Postgres tests passed: 19`.
- `ci-cleanroom-e2e`: NOT IN THIS REPOSITORY. It was the Docker/Linux lane's
  gate, carrying the recorded-asset HLS playback check, the synthetic RTMP
  live-source Gate 8, and the real-Postgres testcontainers gate. Do not cite
  `CivicCast cleanroom: ALL GATES GREEN` as evidence for anything here -- no
  run of it exists against any native commit.
- `ci-docs`: rendered `USER-MANUAL.pdf` and `USER-MANUAL.docx`.
- `ci-a11y`: public and operator axe suites pass.
- `ci-operator-build`: operator lint and build pass.
- `ci-lint`: ruff, format, and mypy pass.
- `diagnose-blackwell-runtime`: `nvidia-smi` sees the RTX runner, optional
  `captions-runtime` plus CUDA runtime wheel dependencies install, and
  `scripts/verify-blackwell-runtime.py` exits 0.
