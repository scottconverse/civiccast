# Historical GitHub Discussion Seed: v0.2.0 Clean Windows Proof Call

> Historical seed for the v0.2.0 line. Use the current
> [v1.0.0-rc18 community seed pack](v1.0.0-rc18-community-seed-pack.md) for
> release-facing posts.

Category: Announcements

Title: CivicCast v0.2.0 is ready for clean Windows proof

Body:

CivicCast `v0.2.0` was the current test-release candidate for the
Windows installer and first-run path when this draft was written. The
current public release is now `v1.0.0-rc18`.

Start by reading the docs, then install from the release assets:

- Tester packet: `docs/tester/START-HERE.md`
- Windows install guide: `INSTALL-WINDOWS.md`
- Trust and verification: `docs/install/windows-release-trust.md`
- RC evidence boundary: `docs/releases/v0.2.0-verification.md`
- Release tag: `https://github.com/scottconverse/civiccast-wsl-legacy/releases/tag/v0.2.0`

The clean-machine proof should confirm the published manifest and setup sidecar,
run the Windows setup app, handle any required helper reboot, and prove the app
does not loop back to the install screen after restart. The target result is a
reachable local service, an operator-console handoff, and first-admin setup from
the packaged flow.

Do not use repository source ZIPs, stale local artifact hashes, or files copied
through chat. If the docs, release assets, manifest, sidecar, proof kit, or
installer UI disagree about version, filename, checksum, or next step, stop and
report that mismatch before installing.

Do not post passwords, recovery codes, provider secrets, private keys,
subscriber data, private meeting content, or unredacted logs.
