# Beta Tester Handoff

> **Historical: describes the retired public WSL2 line, not this
> repository.** `civiccast` (formerly `civiccast-native`) ships one product, the native Windows
> station (session-0 Windows service, no WSL) -- see
> [BRANCHES.md](../../BRANCHES.md). This document's `v1.0.0-rc18` handoff
> process, and the `docs/releases/v1.0.0-rc17-verification.md` link below,
> belong to the separate, private `scottconverse/civiccast-wsl-legacy` repository and
> do not resolve or apply here. Kept as historical reference pending a
> native-line beta-handoff guide.

This whole document describes the retired WSL2 line as of 2026-07-23, when
`v1.0.0-rc18` was its current published release. It is not current native
Windows release guidance. For current native guidance, use the [current User
Manual](../USER-MANUAL.md), [Windows install guide](../../INSTALL-WINDOWS.md),
and [Beta 20 verification record](../releases/v1.0.0-beta.20-verification.md).
The remainder of this file is preserved only for WSL2 historical reference.

This guide was the beta tester path for the CivicCast operator-first
tester line on the retired WSL2 product. Windows testers used the Windows
installer as a host bootstrapper, then ran CivicCast services in Ubuntu
24.04 on WSL2. That beta path did not ship a Windows Service runtime.

A **lane** is a named readiness track recorded in the handoff record — the
JSON output of `civiccast installer beta-handoff --json` (see step 5 below).
Each lane is marked `passed`, `blocked`, or `credential_or_secret_required`.

## Artifact Set

_Audience: release manager / build team, producing the artifact a station
tester will install. Station testers do not need a git checkout or a
`.venv`; skip to [Windows First Run](#historical-windows-first-run-retired-wsl2-line) if you are testing
an already-built installer._

Use one release artifact manifest as the acquisition source of truth:

```powershell
.\.venv\Scripts\python.exe scripts\build_release_artifacts.py --version <release-version> --all-portable --python --wheelhouse --windows-installer
```

The `--version` value must match the package version in `civiccast/_version.py`.

The manifest must include `beta_handoff_acquisition` with the Windows setup
artifact, Python wheel, Linux CPython 3.12 wheelhouse manifest, model bundle
manifest, SHA-256 hashes, and the offline install command. If the Windows
installer tooling is unavailable, record the tool blocker and do not mark the
Windows package lane passed.

## Historical: Windows First Run (retired WSL2 line)

1. Verify the Windows tester package or setup executable and release manifest
   hashes before running it. Use
   [Windows Release Trust And Verification](../install/windows-release-trust.md)
   for the operator checklist.
2. Run the Windows setup app. It checks or installs WSL2 Ubuntu 24.04, prepares
   OS dependencies, installs CivicCast from the bundled wheelhouse, prepares
   managed local storage and upload folders, starts the local API, and opens the
   operator console. It also provisions the local Ollama AI runtime (reusing a
   healthy existing install, or installing a pinned version if absent) and
   ensures the same three-tag target set of standard summary/translation
   models, downloading only the tags still missing, in the background after
   the console is already reachable; a model-download failure is reported
   honestly in the runtime lane and does not block or revert the rest of the
   install.
3. Use the operator console **Setup** screen to create the first local admin and
   recovery kit.
4. Verify backup, run restore rehearsal from **System Health**, and run the
   private first-broadcast rehearsal.
   Keep the v1.4
   [restore, update, rollback, and observed beta proof protocol](../ops/v1.4-restore-update-beta-proof.md)
   open while recording release evidence.
5. Technical testers can run `civiccast installer beta-handoff --json` and
   keep every blocked lane in the handoff record.
6. Technical testers: if this test includes advanced certificate lanes,
   rotate local CA certificates and rerun the handoff check.
7. Confirm FFmpeg and any local NDI runtime or sender required for the station
   workflow. NDI and third-party installer redistribution remains an
   operator-gated acquisition step. Ollama itself is provisioned by the
   installer (detect-and-reuse if healthy, pinned install if absent); confirm
   it reports healthy rather than installing it by hand.
8. For a normal (networked) install, the standard model set pulls
   automatically once the Ollama runtime is ready — no manual step needed.
   For an air-gapped install, import model bundles only when hashes are
   available; missing model hashes block captions, summaries, and translation
   proof.

Do not treat a beta station as ready while storage is volatile. In-memory stores
are acceptable only for throwaway UI checks; staff writes, assets, publish
state, summaries, records, subscribers, and podcast state must survive restart
before a tester uses real meeting content.

## Credential-Gated Lanes

Internet Archive, YouTube, email, webhook, ActivityPub, subscriber, podcast
validator, cable headend, and station portal proof require approved credentials
or controlled targets. Configured secrets alone do not make a lane ready. Record
redacted evidence for any live provider exercise; otherwise leave the lane as
`credential_or_secret_required`.

## Clean Windows Install Proof

_Audience: release manager / build team, running from a git checkout to
produce release evidence. This is not a step a station tester runs._

Run the clean install proof runner from the repository root:

```powershell
.\.venv\Scripts\python.exe scripts\run_clean_windows_install_proof.py --execute --evidence-dir .agent-runs\2026-05-23-public-tester-readiness\evidence --release-manifest artifacts\release\v<version>\civiccast-<version>-release-artifacts-manifest.json
```

Replace the manifest filename with the current release-candidate manifest when
validating a later release candidate.

The runner tries Hyper-V, Windows Sandbox, WSL2 fresh distro, and WSL2 fresh
user evidence in that order. A WSL2 fresh-user install proves the packaged
runtime can install offline, but it is recorded as `partial` unless a native
isolated Windows target actually boots and completes the installer-to-dashboard
handoff. Copy the resulting Markdown summary into
`docs/releases/evidence/v<version>-clean-windows-install-proof.md`.

On the RTX validation host (as of 2026-07-18, pre-rc17), an elevated native
follow-up also attempted the Hyper-V and Windows Sandbox feature enables with
`-NoRestart`. Windows 11 Home returned `feature name unknown` for both native
features, so no native Hyper-V/Sandbox target was booted there. The WSL2
fresh-user offline wheelhouse install is useful runtime evidence, but at that
time the final public tester release gate still required a native isolated
Windows proof or an explicit release-manager waiver.

**Historical status (retired WSL2 line):** `v1.0.0-rc18` was the current
published release on that line. Its own bytes were proven for clean-host
install, app launch, reinstall, uninstall, rc17->rc18 upgrade, and an
interactive installer wizard walkthrough. The full product path -- WSL2
helper setup, first admin, create/upload recording -> private rehearsal ->
package -> Portal approval -> resident playback -- and cold-reboot recovery
were last proven end to end against rc17's exact bytes on 2026-07-20 and had
not been repeated on rc18's bytes as of that line's retirement. (That
evidence document is not present in this repository -- it belonged to the
separate, private `scottconverse/civiccast-wsl-legacy` repository.) This is historical
record only, not proof of anything about this repository's native line.
