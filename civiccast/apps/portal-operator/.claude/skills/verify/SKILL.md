---
name: verify
description: Verify the CivicCast operator portal (civiccast/apps/portal-operator) end to end before committing. It builds the portal, starts vite preview hidden on a free port, drives real operator flows headless with Playwright against real CivicCast API processes (the publish approval and first-run setup), saves trace and screenshot evidence, and stops the preview by the PID it started.
---

# Verify the operator portal

Run from anywhere in the repo. All paths below are relative to `civiccast/apps/portal-operator`.

## Prepare once

Git Bash:
```bash
export GIT_LFS_SKIP_SMUDGE=1
(cd "$(git rev-parse --show-toplevel)" && uv sync --all-extras --no-extra agenda-js-import --group dev)   # the specs start the API from .venv
npm ci --no-audit --no-fund                                # ~3.5 min cold
npm --prefix ../portal-public ci --no-audit --no-fund      # the setup flow serves the public portal dist
```
PowerShell: the same commands, with `$env:GIT_LFS_SKIP_SMUDGE = '1'` and `Push-Location (git rev-parse --show-toplevel)` in place
of the subshell.

Headless Chromium must already be in `%LOCALAPPDATA%\ms-playwright`. The script uses the revision this
Playwright pins (`node_modules/playwright-core/browsers.json`). If that revision is missing, it uses the closest
newer installed `chromium_headless_shell-*` through the app's `CIVICCAST_PLAYWRIGHT_EXECUTABLE` hook, and writes
that on the `Browser:` line of the report. If neither exists, it stops. `npx playwright install chromium-headless-shell`
fetches one, but that is a download, so ask the owner first.

## Run

The same command works in both shells:
```
pwsh -NoProfile -File civiccast/apps/portal-operator/.claude/skills/verify/verify-portal.ps1
```
Options: `-Flows publish` (the default) or `-Flows publish,setup`. Add `setup` when you change setup, the first-run
wizard or migrations, and the disk is not saturated (see below). `-Port <n>` (default 0 = a free loopback port),
`-SkipBuild` (reuse `dist`), `-Install` (force `npm ci`), `-EvidenceDir <dir>`, `-TestTimeoutSec 900`, `-PhaseTimeoutSec 900` (limit for each install and build step; a step past it is stopped by its PID and fails).

What it does:
1. `npm ci` only if `node_modules` is missing (or `-Install`). Then `npm run build` (`tsc -b && vite build`) and,
   for the setup flow, the portal-public build.
2. Starts `node node_modules/vite/bin/vite.js preview --host 127.0.0.1 --port <free> --strictPort` **hidden**.
   It records the PID, creation time and command line, then waits for HTTP 200.
3. Runs Playwright **headless** with `.claude/skills/verify/playwright.verify.config.ts`. That config reuses the app's
   config, removes its `webServer` (so nothing attaches to a stray preview on 4174), and sets trace and screenshots to `on`, with output in the evidence folder.
4. Stops the preview **by its recorded PID only** (re-checked first; `taskkill /PID <pid> /T /F`). If a fixture
   API process outlived its spec, it is stopped by PID too and the run FAILS.

## The flows (existing specs, reused because each is a true user flow)

| Flow | Spec | What the operator does | Real parts |
|---|---|---|---|
| `publish` | `e2e/full-stack-publish.spec.ts` | opens Publish, sees the council meeting's surfaces, Approve and Publish, sees per-surface proof (simulated-archive notes, sha256 hashes) | real CivicCast API (`tests.integration.operator_fullstack_fixture_app`, in-memory stores, deterministic staff token) through the built UI |
| `setup` | `e2e/setup-real-boundary.spec.ts` | first-run wizard: prepare storage, create the first admin, recovery kit, verify a backup, save a source, sample media, rehearsal, support bundle | real `create_app()` with real SQLite managed storage and Alembic migrations in a temp folder; serves the real portal-public dist |

The skill does not use these: `a11y`, `route-table-smoke` and the other mocked-API specs (`npm run test:a11y` runs them), `full-ui-walkthrough`
(a route sweep, not a user flow), and `real-boundary-smoke` (it needs Docker, which this machine does not have).

The setup flow reads `CIVICCAST_E2E_PREVIEW_ORIGIN`, which the script sets, so the fixture API allows CORS from the free preview
port. Without it, the fixture allows only 4174 (as before).

The setup flow is disk-bound. Preparing storage runs the Alembic migrations, about 100 SQLite commits in rollback-journal
FULL-sync mode. The running app itself uses WAL with synchronous=NORMAL. The spec waits 30 s for "Storage ready". On
Nov 18 2026 the disk was saturated by other programs: 340-420 ms per commit, and about 150 s to prepare storage. The flow
then failed at that wait. That is why `setup` is not in the default run. The fixture `operator_fullstack_fixture_app` had
no `mark_published` (the publish router calls it since the Portal-visibility change), so Portal always showed
"could not make the recording public". It was fixed in this change.

## Evidence

Everything goes to `.vlite-evidence/<yyyyMMdd-HHmmss-fff>-portal/` at the repo root (git-ignored):
`report.md`, `summary.json`, `<phase>.log` and `<phase>.err.log` for each install, build and Playwright step, `preview.out/err.log`, `processes.log`,
`playwright-results.json`, `playwright-report/` (HTML), and `playwright-output/<test>/` with `trace.zip` and screenshots.
**Nothing in this evidence folder is redacted.** The Playwright traces (`trace.zip`) record network traffic, so they
contain the throwaway test-server's login tokens and request bodies. Do not commit, upload or share an evidence
folder, and delete it when you no longer need it.
To open a trace: `npx playwright show-trace <path>\trace.zip`. That opens a window, so do it only when the owner asks.

## Report format

```
| Phase | Seconds | Exit | Log |          (install / build / preview-up / playwright)
| Spec | Test | Status | Seconds | Screenshot | Trace |
## Failures (if any)
RESULT: PASS | FAIL
EVIDENCE: <dir>
```
The exit code is 0 only when every chosen test passed. A skipped test counts as a failure, because a skip proves nothing.

## Stamp (last step, always)

```
vlite stamp verify --result pass --evidence <evidence dir>     # or --result fail
```
If `vlite` is not found, skip the stamp and say "vlite not found; verify not stamped".
