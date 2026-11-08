# CivicCast v3.0.0-beta1 public-final cleanroom and installer proof

Date: 2026-06-25

This directory records the public-final cleanroom and installer proof after the
GauntletGate all fixes.

## Current release status

The current cleanroom-proven beta1 release is:

- Tag: `v3.0.0-beta1-cleanroom-r19-runtime-isolation-formatfix`
- Commit: `93f8958a0373d6daca7118713a3aab2ca6563dbb`
- Release:
  `https://github.com/scottconverse/civiccast-wsl-legacy/releases/tag/v3.0.0-beta1-cleanroom-r19-runtime-isolation-formatfix`
- GitHub release-artifacts run: `28196906486`, success
- GitHub ci-cleanroom-e2e run: `28196906460`, success

The external old-tester cleanroom install result is PASS:

- Tester branch: `tester/v3.0-finish-line-4h-soak`
- Evidence closure commit: `34d0d9c5`
- Install nonce:
  `OLDTESTER-R19-RUNTIME-ISOLATION-INSTALL-R1-20260625-93f8958a`
- Evidence-closure nonce:
  `OLDTESTER-R19-EVIDENCE-CLOSURE-R1-20260625-93f8958a`
- Closure result:
  `tester-handoff/v3.0/cleanroom-install/results/result-20260625T214407Z-OLDTESTER-R19-EVIDENCE-CLOSURE-R1-20260625-93f8958a.json`
- Detailed evidence:
  `tester-handoff/v3.0/cleanroom-install/evidence/r19-20260625-93f8958a/`

The closure result proves the installer asset/hash match, no Entry
Point/TaskDialogIndirect dialog, headless bootstrap install, isolated WSL
runtime identity, dashboard health at version `3.0.0-beta1`, fresh first-admin
setup, saved-state persistence, and scheduled-recording record-now/stop/output
with valid `ffprobe` proof. It also records `reboot_performed=false`,
`installer_state.reboot_required=false`, no new pending reboot marker, and no
blockers.

One diagnostic-only follow-up remains tracked for `3.0.1`: the packaged
installer runtime can serve `/health` with `schema=unknown` because the health
check cannot locate packaged Alembic metadata in that runtime. The beta tester
workflows above passed; this is a status/diagnostic repair, not a beta1
workflow blocker.

The older sections below are retained as historical local proof that led to
R19. They are no longer the current cleanroom release authority.

## Rebuilt release artifacts

Command:

```powershell
$env:CARGO_TARGET_DIR='C:\CivicCastTester\v3-beta-release-prep\target-v3.0.0-beta1-public-final'
uv run --python 3.12 python scripts\build_release_artifacts.py --version 3.0.0-beta1 --out-dir artifacts\release\v3.0.0-beta1-public-final --all-portable --python --wheelhouse --windows-installer
```

Result: `build_release_artifacts: PASS`.

Artifacts:

- `artifacts/release/v3.0.0-beta1-public-final/civiccast-3.0.0-beta1-windows-setup.exe`
- `artifacts/release/v3.0.0-beta1-public-final/civiccast-3.0.0-beta1-windows-tester-package.zip`
- `artifacts/release/v3.0.0-beta1-public-final/civiccast-3.0.0-beta1-clean-windows-proof-kit.zip`
- `artifacts/release/v3.0.0-beta1-public-final/civiccast-3.0.0-beta1-release-artifacts-manifest.json`

## Clean install proof

Command:

```powershell
uv run --python 3.12 python scripts\run_clean_windows_install_proof.py --execute --evidence-dir docs\releases\evidence\v3.0.0-beta1-public-final-cleanroom --release-manifest artifacts\release\v3.0.0-beta1-public-final\civiccast-3.0.0-beta1-release-artifacts-manifest.json
```

Result: clean WSL2 fresh-user install/import passed from the rebuilt wheel and
wheelhouse. Hyper-V and Windows Sandbox were not available to this unelevated
session; the local VirtualBox clean Windows lab is documented below.

Evidence:

- `clean-windows-install.json`
- `clean-windows-install.md`

## Installer UI and setup surface

Commands:

```powershell
npm.cmd --prefix civiccast\apps\installer run build
npm.cmd --prefix civiccast\apps\installer run test:e2e
```

Result: installer UI build passed; installer Playwright E2E passed `62 passed`.

## Docker cleanroom

Command:

```powershell
docker build -f docker/cleanroom.Dockerfile -t civiccast-cleanroom:gauntlet-final .
docker run --rm --add-host=host.docker.internal:host-gateway -v "${PWD}:/work/civiccast:ro" -v "//var/run/docker.sock:/var/run/docker.sock" civiccast-cleanroom:gauntlet-final
```

Result: `CivicCast cleanroom: ALL GATES GREEN`.

Included:

- `ruff check`
- `ruff format --check`
- `mypy civiccast`
- full cleanroom pytest: `4315 passed, 19 skipped`
- first-run installer proof commands
- real packager encode proof
- public portal build and a11y: `27 passed`
- cleanroom HLS playback: `2 passed`
- synthetic RTMP live source to HLS to portal playback: `2 passed`
- real PostgreSQL schedule contract: `19 passed`

The cleanroom pytest skips are substrate-gated categories. The final
GauntletGate rerun exercised those categories separately on their required
substrates: live Postgres, TSDuck network pins, Windows/POSIX compliance
branches, WSL hardware detection, and WSL GStreamer caption/runtime.

## VirtualBox clean Windows lab

The rebuilt proof kit was staged to:

`C:\Dev\Claude\vm-share\civiccast-cleanwin-v2\civiccast-3.0.0-beta1-public-final-clean-windows-proof-kit.zip`

SHA-256:

`6d14980187584d8023def80b91da1d65499304ebfa2df82d2b27740f84026a04`

The clean Windows VM `civiccast-cleanwin-v2` is running from the
`clean-windows-base-20260602` snapshot with Guest Additions active. Guest
control accepted `whoami` as `civiccast-clean\tester`.

A no-reboot/no-reset guest-control attempt to run a full current proof script
against the rebuilt installer timed out at the VirtualBox guest-control layer
before writing JSON evidence. Because reboot, reset, and power-cycle were
unauthorized for this release closeout, the VM was left running and the attempt
was not retried destructively.

The earlier v3.0.0-beta1 local VirtualBox proof remains the applicable VM
boundary evidence: this VM class can install and launch the native EXE and
exercise Windows helper handoff, but it cannot start nested WSL2 Ubuntu while
VirtualBox is running through the Windows Hypervisor Platform fallback.

## Release claim boundary

This proof supports the CivicCast v3.0.0-beta1 public beta software claim. It
does not claim production certification, live cable-headend acceptance,
physical DeckLink proof, app-store publication, or live external-provider
round trips.
