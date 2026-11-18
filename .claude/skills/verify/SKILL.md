---
name: verify
description: Start CivicCast locally and prove a change works end to end before committing. Backend - start the FastAPI app hidden on a free port with throwaway state, drive real first-run setup, sign-in and account flows through the HTTP API, restart, stop by PID, and write evidence. Routes operator-portal (civiccast/apps/portal-operator) changes to that app's own verify skill. Use before every commit that touches civiccast/, alembic/, tests/ or civiccast/apps/.
---

# Verify CivicCast (router + backend)

## 1. Route first

| Changed files | Run |
|---|---|
| `civiccast/apps/portal-operator/**` | `civiccast/apps/portal-operator/.claude/skills/verify/SKILL.md` |
| `civiccast/**` (Python), `alembic/**`, `tests/**`, `pyproject.toml`, `uv.lock` | the backend verify below |
| both | both, backend first |
| docs only (`*.md`, `docs/`, `website/`) | nothing to verify; say so |

Never verify against the installed station. Port 8000 (installed CivicCast) and 5433 (its PostgreSQL)
are refused by the scripts. Nothing here touches `C:\Program Files\CivicCast (Native)` or `C:\CivicCastTester`.

## 2. Prepare once (per checkout)

Git Bash:
```bash
export GIT_LFS_SKIP_SMUDGE=1
uv sync --all-extras --no-extra agenda-js-import --group dev     # ~55 s cold
```
PowerShell:
```powershell
$env:GIT_LFS_SKIP_SMUDGE = '1'
uv sync --all-extras --no-extra agenda-js-import --group dev
```
This builds `.venv` in the checkout. The script uses `.venv\Scripts\python.exe` and never a global Python.

## 3. Run the backend verify

The same command works in both shells (it is a PowerShell 7 script):
```
pwsh -NoProfile -File .claude/skills/verify/scripts/verify-backend.ps1
```
Options: `-Port <n>` (default 0 = a free loopback port picked by the OS), `-EvidenceDir <dir>`,
`-StartupTimeoutSec 90`, `-RequestTimeoutSec 240`, `-KeepState` (keep the throwaway station folder for debugging).

What it does:
1. Makes throwaway station state in `%TEMP%\civiccast-verify-<id>` and points every CivicCast path
   variable at it (LOCALAPPDATA, XDG_*, CIVICCAST_CONFIG_DIR, CIVICCAST_CERT_ROOT,
   CIVICCAST_MANAGED_STORAGE_DIR, station state, support bundles, subscribe secrets). DATABASE_URL is cleared,
   so the app uses managed SQLite in that folder.
2. Starts `python -m uvicorn civiccast.app:create_app --factory --host 127.0.0.1 --port <free>` **hidden**,
   with stdout and stderr in files. It records the PID, creation time and command line.
3. Proves it is up: `GET /health` answers (polled until it does), then Flow 1 requires a 200 from it.
4. **Flow 1, first run:** station not set up → `POST /api/setup/storage` (real Alembic migrations on SQLite)
   → `POST /api/setup/first-admin` → acknowledge the recovery kit → wrong password is 401 → admin
   `POST /api/staff/auth/login` → `/me` without a token is 401 → `/me` with the token shows `setup_admin`.
5. **Flow 2, accounts and durability:** the admin creates a `records_clerk` account (201) → the clerk signs in → clerk
   `/me` shows only `records_clerk` → the clerk is refused the accounts list (403) → **stop and restart the server** →
   setup is still complete → the clerk and admin sign in again → the account list still has the clerk.
6. Stops the server **by its recorded PID only** (the record is re-checked first: same creation time,
   marker in the command line; then `taskkill /PID <pid> /T /F`). Never by process name.
7. Fails if any `Traceback` shows up in the server logs. Deletes the throwaway state (unless `-KeepState`).

Expected time: 15-40 s on a quiet disk. The `POST /api/setup/storage` step makes about 100 SQLite commits in
rollback-journal FULL-sync mode, so it follows disk fsync latency. On a disk that other programs saturate, it was
measured at 150 s. `report.md` shows the time of each step, so a slow run is easy to explain.

## 4. Evidence

Everything goes to `.vlite-evidence/<yyyyMMdd-HHmmss-fff>-backend/` (git-ignored):
- `report.md` - the pass/fail table
- `summary.json` - machine-readable result
- `http-transcript.log` - every request and response, with passwords and tokens redacted
- `processes.log` - PIDs started and stopped, with how each was stopped
- `server-<label>.out.log` / `.err.log` - uvicorn output for each start

## 5. Report format

`report.md` and the console end with:
```
| Flow | Step | Call | HTTP | Expected | ms | Result |
...
RESULT: PASS        (or FAIL, with the first failing step named)
EVIDENCE: <absolute evidence dir>
```
The exit code is 0 on PASS and 1 on FAIL. In your reply, quote the RESULT line, the evidence path and any failing
row. Never call it a pass without the RESULT line.

## 6. Stamp (last step, always)

```
vlite stamp verify --result pass --evidence <evidence dir>     # or --result fail
```
If `vlite` is not on PATH (`command -v vlite` in Git Bash, `Get-Command vlite` in PowerShell), skip the stamp
and say "vlite not found; verify not stamped".
