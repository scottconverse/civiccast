# "Windows protected your PC" — what to expect, and what to do

When you open a CivicCast installer, Windows may show a blue SmartScreen page.
That page alone does not prove the file is safe, signed, or approved. Verify the
exact filename, SHA-256, and signature status in the active handoff first.
("Active handoff" means the tester packet you were given for this release —
see `docs/tester/START-HERE.md` if you don't have one.)

This walkthrough explains how to handle that screen and check the actual file
before running it. Beta 20 is the current published native release.

---

## For the operator (no technical background needed)

### Why this happens

SmartScreen reputation and Authenticode signing are separate. In plain terms:
SmartScreen's warning is about download popularity, not about whether the file
is genuinely from us — a brand-new, genuinely signed release can still show
this screen. A signed new release can still lack reputation, while a
`NotSigned` file can show an unknown publisher. Follow the active
handoff; do not click through an identity or status that differs from it.

### What you'll see, and exactly what to click

1. Verify the exact installer named in the handoff, then open it. A GitHub
   release calls it `setup.exe`; a USB/LAN kit may use the branded filename.
2. If a screen appears titled **"Windows protected your PC,"** follow the
   checks below. Its presence or absence is not acceptance evidence.
3. Click the small text link that says **More info**.
4. Compare the Publisher field with the exact signature status and publisher in
   the active handoff. If they differ, stop. Click **Run anyway** only when the
   handoff explicitly authorizes that exact publisher and signature status for
   that exact SHA-256 — the hash must match too, not just the publisher name.
5. Continue through the Windows installation wizard. If Windows requests
   administrator approval, check the publisher again before approving it.
   Then follow the [Beta 20 tester quickstart](START-HERE.md). Do not assume
   installation succeeded merely because a window opened.

The hash and valid Authenticode checks establish installer identity. Clicking
through SmartScreen is not itself a signature or operational check.

### Want to be extra sure before you click "Run anyway"?

Both checksum and signature verification are required. For GitHub, compare
against the exact release's checksum file and installer sidecar metadata.
For USB/LAN, use the complete kit's own hash-pinned delivery manifest and
trusted handoff; a GitHub sidecar need not be present. Computing the hash needs
a command (`Get-FileHash`, shown in the **For IT / technical verification**
section below). If that's not something you're set up to run yourself, ask
your IT contact to run it for you before you click "Run anyway."

---

## For IT / technical verification

Use these checks against the exact candidate handoff:

### 1. Confirm the signature (identity + integrity)

```powershell
Get-AuthenticodeSignature .\setup.exe | Format-List Status, SignerCertificate
```

For a USB/LAN kit, substitute the exact branded installer filename from the
handoff in both PowerShell commands on this page. For the public beta, require
`Status: Valid` and the exact named signer, Scott Converse.
If it says `NotSigned`, that file is local-acceptance-only and must never be
treated as a public beta download.

### 2. Confirm the SHA-256 hash matches the handed-off package

```powershell
Get-FileHash .\setup.exe -Algorithm SHA256
```

For GitHub, compare against the `sha256` value in `setup.exe.sidecar.json` published
alongside the installer in the exact release, and against `setup.exe`'s own
line in `SHA256SUMS.txt`. They must match. Do not use a generic "latest"
link, an older prerelease, or a detached sidecar. For USB/LAN, compare the
actual filename's entry in that kit's `SHA256SUMS.txt` and the trusted handoff.
The checksum file and sidecar are metadata, not separately signed attestations.

### 3. Allowlist by publisher or hash

Once verified, IT can allowlist a signed build by its approved publisher or any
approved build by exact hash, according to local policy.

### Why SmartScreen still warns on a signed installer

A valid Authenticode signature does not guarantee that Windows will omit a
SmartScreen warning. Do not promise that the warning will disappear after a
particular number of downloads. See [CODE_SIGNING_POLICY.md](../../CODE_SIGNING_POLICY.md)
for the signing posture, and verify the actual downloaded executable.

## Release context

The current published native installer is `v1.0.0-beta.20`, a signed GitHub
pre-release. Follow the [current User Manual](../USER-MANUAL.md),
[Windows install guide](../../INSTALL-WINDOWS.md), [Windows release-trust guide](../install/windows-release-trust.md),
and exact owner-authorized handoff. The Beta 10 details below are historical
and do not qualify Beta 20.

### Historical Beta 10 release state (October 2, 2026 snapshot)

`v1.0.0-beta.10` was the current published release when this page was written
(published 2026-10-02), a
GitHub **pre-release** (a "Beta Candidate", not a production release) at
<https://github.com/scottconverse/civiccast/releases/tag/v1.0.0-beta.10>:
`setup.exe`, `.ccpack` runtime packs, `SHA256SUMS.txt`, and
`setup.exe.sidecar.json`. `v1.0.0-beta.7` is superseded history (as are beta.5,
beta.4, and `v1.0.0-beta.3`, the first downloadable release); beta.8 and beta.9
were never published and their work is inside beta.10.
`v1.0.0-beta.1` (USB-delivered, no GitHub download) is also superseded.
`v1.0.0-beta.2` was never published -- it exists
only as an internal Gate A upgrade-baseline kit. See
[`docs/releases/release-truth.yaml`](../releases/release-truth.yaml) for the
authored release-state record.

Beta 10 was a beta candidate: its clean-install automated check
passed, upgrade paths were not run for it, and the human/station acceptance
pass is not done (see
[`docs/releases/v1.0.0-beta.10-verification.md`](../releases/v1.0.0-beta.10-verification.md)).
A familiar filename or a passing SmartScreen check is not evidence that its
release checks have passed.
