# Cross-Platform Installer

> **This repository (`civiccast`, formerly `civiccast-native`) ships ONE Windows product: the
> native line.** The "WSL line" described below was a real, separate
> Windows product this project used to also ship, retired under the
> owner's "no linux" decision (2026-08-19) -- see
> [BRANCHES.md](../../BRANCHES.md). Its history lives in the separate,
> private `scottconverse/civiccast-wsl-legacy` repository, not here. The paragraph and
> platform-matrix row below are kept as historical context for why
> `civiccast.installer.platform` still exists as a Linux/macOS-only module
> and why the native product's own identity work (ADR 0021) had to be
> disjoint from a second, real Windows product -- not as a description of
> anything this repository currently builds or ships.

CivicCast exposed guided installer contracts for Linux, macOS, and two
distinct Windows product lines. The (now-retired) public Windows beta was the
WSL line: its host bootstrapper prepared Ubuntu on WSL2 and its CivicCast
services ran inside that Ubuntu runtime with systemd. The native Windows line
was developed under ADR 0021 with a separate product identity and a
session-0 Windows service; it is what this repository builds today. The two
Windows products shared a codebase but never installed, repaired, or removed
each other's runtime.

## Platform Matrix

| Platform | Runtime | Package/bootstrap | Service manager | Readiness rule |
| --- | --- | --- | --- | --- |
| Linux | Native Linux | `.deb` or `.rpm` | systemd | Ready only when service metadata and package proof are present. |
| macOS | Native macOS operator host | `.pkg` | launchd | Ready only when `.pkg` metadata and launchd bootstrap proof are present. |
| Windows 11 - native line (this repository) | Native Windows | Tauri/NSIS setup app and native payload | session-0 Windows service | Readiness is governed by ADR 0021 and `docs/design/spec-installer-lifecycle.md`. |
| Windows 11 - WSL line (retired; separate archived repository) | Ubuntu on WSL2 | Tauri/NSIS setup app plus WSL2 bootstrap manifest | systemd inside WSL2 | Historical only -- this repository does not build this row. |

## Windows Setup App (retired WSL line)

This section describes the retired WSL-line setup app as historical context; it is not shipped by this repository. Its Windows
operator entry point was the Tauri setup executable built from
`civiccast/apps/installer` with `npm run tauri:build`. The setup app gave the
operator a guided readiness walkthrough, while CivicCast services ran
inside WSL2 Ubuntu per ADR 0003. ADR 0021 governs the native Windows
product and its installer lifecycle. A valid WSL-line release artifact had to be copied from
Tauri's NSIS bundle output and hashed by `scripts/build_release_artifacts.py
--windows-installer`; placeholder bytes or a bootstrap manifest alone did not
satisfy the Windows double-click installer lane.

When WSL2 Ubuntu was missing, the setup app had to offer `Install WSL2 Ubuntu` and
launch the Microsoft WSL installer with Windows elevation. It was not to leave a
non-technical beta tester at a bare terminal command as the only path forward.

## Artifact Verification

Every install artifact must have:

1. Real SHA-256 computed from the artifact bytes.
2. A `.sidecar.json` file with the same SHA-256.
3. A signed install manifest.
4. Service and bootstrap metadata.
5. For a Windows `.exe` whose install manifest claims `signed: true`, a
   genuinely embedded Authenticode certificate table (Azure Trusted Signing;
   see [CODE_SIGNING_POLICY.md](../../CODE_SIGNING_POLICY.md)). This release
   chain carries no Sigstore/cosign step, so no artifact is required to carry
   a `.sigstore.json` bundle.

Missing artifacts, hash mismatches, missing sidecars, unsigned manifests, and
a `signed: true` claim with no real Authenticode evidence are blocked states.
Operators should download the artifact and sidecar together, or rebuild the
package and rerun verification rather than bypass the proof.

Windows operators should verify the setup `.exe` before running it. The
operator-facing checklist lives in
[Windows Release Trust And Verification](../install/windows-release-trust.md)
and explains the difference between SHA-256 checksums, sidecars, and
Authenticode signing.

## Durable Storage Prerequisite

Every operator or beta-test install needs durable storage before staff
workflows are used. If `DATABASE_URL` is unset, CivicCast creates a local
durable SQLite database, applies the Alembic migration graph, and reports the
storage lane ready before first-admin setup. Technical admins can set
`DATABASE_URL` to use Postgres instead.

If `CIVICCAST_ALLOW_EPHEMERAL_STORES=1` is set, CivicCast can run lightweight
development and UI checks with in-memory stores, but staff writes are lost on
restart. That is not a beta-ready operator posture.

## Ollama Runtime Provisioning (Windows)

The Windows WSL2 bootstrap provisions the local Ollama AI runtime as part of
the guided setup, inside the same charter walls as the rest of the runtime
install: it detects and reuses a healthy existing Ollama install first, and
only installs a pinned version (sha-verified, standard `/usr/local` location,
no CivicCast-owned parallel install tree) when Ollama is genuinely absent from
the WSL distro. A present-but-unhealthy install is left untouched and refused,
never forced. Model provisioning (the standard summary and
translation tags -- a fixed three-tag set on every install, sourced from the same catalog the Setup wizard's own model
download uses) runs strictly after the operator console is already reachable,
so a slow or failed model pull never blocks or delays dashboard access; a
failure surfaces as an honest message on the existing runtime-readiness lane
rather than reverting the install to an error state. This is designed
behavior proven against real bash execution with faked tooling in this
repository's test suite; a real end-to-end run against the real ollama.com
archive and a real model pull on a clean machine is separately the VM
gauntlet's job.

## Model Setup

The installer reports model setup as planned, running, progress, complete, cancelled, skipped, unavailable, or blocked. Only hash-verified online downloads or offline bundle imports may report complete proof. Skipped and unavailable models must show `proof_unavailable` and tell the operator to rerun setup or import the offline bundle.

## Air-Gapped Import

Air-gapped imports require offline mode, an operator guide, proof metadata, and real hashes for every artifact in the bundle. If network access is enabled, proof metadata is missing, a file is absent, or a hash mismatches, the import is blocked with the exact file and next operator action.

External provider lanes such as Internet Archive remain credential-gated. Air-gapped proof does not claim those online integrations are verified.

## Beta Tester Handoff

For v1.2 beta testers, the installer summary is paired with
`civiccast installer beta-handoff --json`. That handoff summary adds the
release artifact acquisition manifest, clean Windows install proof evidence,
durable storage setup, dependency/model gates, mTLS, and external
provider credentials to the same fail-closed operator view. The detailed tester path lives in
[Beta Tester Handoff](beta-tester-handoff.md).
