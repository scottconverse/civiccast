# Windows Release Trust And Verification

> **The rc-numbered instructions in the "Historical" appendix at the bottom of
> this page are preserved as historical evidence only.** A native release must
> bind its own exact installer, SHA-256, signature, and proof, using the
> steps in this page's current sections.

## Current Release State

`v1.0.0-rc.4` is the current signed public release. Download the complete kit from its [release page](https://github.com/scottconverse/civiccast-native/releases/tag/v1.0.0-rc.4),
and use that release's `SHA256SUMS.txt`, installer signature and station/pack
manifests to verify the bytes you receive. The [RC4 release
notes](../releases/v1.0.0-rc.4-release-notes.md) identify the exact signed
package (`setup.exe` SHA-256 `d86e1faeaa6284bd646211cb2592a4ceaf930dfafb539b717d14c26005a53f8d`,
Authenticode Valid, signer `CN=Scott Converse`), its verification and its
known issues. Earlier installation results remain
in their own verification records.
[release-truth.yaml](../releases/release-truth.yaml)
is the authored release-state record. Watch the official native release page,
not `scottconverse/civiccast` (the retired, separate WSL2-line repository) and
not any `v1.0.0-rcNN` tag, which belongs to that other repository.

`v1.0.0-beta.11` and earlier published betas are superseded. Beta 8 and 9
were never published; their work was included in Beta 10. See their
verification records for historical package scopes. Verify the downloaded
installer yourself using the steps below; lab evidence is not a substitute
for checking the exact bytes you downloaded.

`v1.0.0-beta.7` (a download-only upgrade for stations on `v1.0.0-beta.5`) and
`v1.0.0-beta.3`, the first downloadable release, are now superseded.
`v1.0.0-beta.1` (USB-delivered, no downloadable assets) is also superseded.
`v1.0.0-beta.2` was never published -- it exists only as an internal Gate A
upgrade-baseline kit.

This page explains how an operator should verify a CivicCast Windows setup
download before running it.

For the published RC4 interface, use the Help installed with RC4 or the manual assets from its release page. Repository manual revisions may contain later editorial corrections; source changes do not update an installed station.

## Read The Release Docs First

For a clean-machine proof, read these documents before downloading or running
anything:

1. [Beta Tester Start Here](../tester/START-HERE.md)
2. [Install CivicCast On Windows](../../INSTALL-WINDOWS.md)
3. This trust and verification page

For GitHub downloads, use the exact tagged release named by the handoff and
verify `SHA256SUMS.txt`, installer sidecar metadata and the executable's actual
Authenticode signature before installation. A first-install USB/LAN kit is a
different delivery set: use its own hash-pinned delivery manifest and the
installer signature, plus the signed station and runtime-pack manifests.
Do not require a GitHub sidecar that is not part of the USB/LAN kit.

Record that these documents were opened and read in the proof report. If the
approved package source, manifest, sidecar, installer UI, or docs disagree about the
version, filename, checksum, or next step, stop and report the mismatch before
installing.

## What You Are Checking

A CivicCast release can provide three different kinds of trust evidence:

| Evidence | What it proves | What it does not prove |
| --- | --- | --- |
| SHA-256 checksum | The file on your machine matches the owner-approved package or published release. | It does not identify the publisher by itself. |
| Release sidecar or delivery manifest | Records the expected files and hashes for the named artifact set; compare these with the trusted handoff and actual downloaded bytes. | Plain checksum metadata is not a publisher signature or a separately signed attestation. |
| Installer signature | A valid Authenticode verification identifies the signer and detects changes to the signed executable. See [CODE_SIGNING_POLICY.md](../../CODE_SIGNING_POLICY.md). | It does not establish SmartScreen reputation, successful installation or operational acceptance. |

The generated `setup.exe.sidecar.json` is plain checksum/signature-status
metadata, with `attestation: null`. It is not separately signed. The installer
has the Authenticode signature; station indexes and runtime packs have their
own signed-manifest verification. Do not call the root delivery checksum file
a signed manifest merely because it lists signed files.

Do not assume a candidate is Authenticode-signed. The active handoff and exact
artifact metadata must state its actual signature status. A public beta must
report `Valid` and the approved publisher; a local `NotSigned` engineering build
is suitable only for an explicitly authorized local acceptance run and must not
be distributed as a public beta.

## Download The Complete Beta 20 Kit

Use the helper asset from the exact tagged release. Download the helper and
`SHA256SUMS.txt`, then verify the helper's own hash before executing it:

```powershell
$tag = "v1.0.0-beta.20"
$base = "https://github.com/scottconverse/civiccast-native/releases/download/$tag"
$helper = Join-Path $env:TEMP "CivicCast-Install-Beta20.ps1"
$sums = Join-Path $env:TEMP "CivicCast-Beta20-SHA256SUMS.txt"
Invoke-WebRequest -UseBasicParsing "$base/download_windows_release_artifacts.ps1" -OutFile $helper
Invoke-WebRequest -UseBasicParsing "$base/SHA256SUMS.txt" -OutFile $sums
$expected = ((Get-Content $sums) | Where-Object { $_ -match '\s+download_windows_release_artifacts\.ps1$' } | Select-Object -First 1) -replace '^([0-9a-fA-F]{64})\s+.*$', '$1'
$actual = (Get-FileHash $helper -Algorithm SHA256).Hash
if ($actual -ne $expected) { throw "Downloader hash does not match the release checksum list." }
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $helper -Tag $tag -Version $tag.TrimStart('v') -Install -Destination (Join-Path $env:USERPROFILE "Downloads\CivicCast-Beta20")
```

With `-Install`, the helper downloads and checks all five runtime packs,
the station manifest and every multipart ZIP part, reconstructs and validates
the archive, and checks station-index and pack hashes against the manifest.
The helper launches `setup.exe /S`, elevating when required; setup verifies
signed station metadata. For download-only staging, use `-IncludePacks`; it
downloads and verifies the complete kit but does not launch setup. Follow the
active handoff for the machine-specific procedure, and do not treat a
successful download as installation or operating proof.

## Verify The Download With PowerShell

1. For the complete Beta 20 download, use the release-matched helper and
   choose `-IncludePacks` to stage without installing or `-Install` to run setup
   after verification. The destination contains `setup.exe`, its sidecar,
   `SHA256SUMS.txt`, five runtime packs under `packs\`, station files under
   `station\`, and the station manifest and multipart parts. The installer
   remains at the destination root; the helper runs from its temporary
   download path. Do not move or rename files.
2. Open PowerShell in the destination folder.

3. Compute the local hash:

```powershell
Get-FileHash .\setup.exe -Algorithm SHA256
```

4. Compare the `Hash` value with the SHA-256 value recorded for `setup.exe`
   in `SHA256SUMS.txt`, and cross-check it against `setup.exe.sidecar.json`'s
   own `sha256` field. All three values must match exactly.

If they do not match, quarantine the package and request a replacement proof
bundle from the owner. Do not run an installer with a mismatched hash.

Do not trust stale local artifact paths or hashes copied from earlier proof
runs. The `SHA256SUMS.txt` and setup sidecar published with the exact
GitHub Release are the public source of truth.

## Verify The Release Manifest

Technical operators can also use CivicCast's package verifier after placing the
setup executable and sidecar in the same folder:

```powershell
uv run civiccast installer verify-package `
  --artifact ".\setup.exe" `
  --sidecar ".\setup.exe.sidecar.json" `
  --json
```

The verifier should report `ok` only when the artifact bytes, sidecar hash,
install manifest, and service/bootstrap metadata line up, and -- when the
sidecar claims `signed: true` -- the `.exe` genuinely carries an embedded
Authenticode certificate table. It should report blocked or failed when any
one is missing or inconsistent.

## Authenticode And SmartScreen Status

Each release must say whether the Windows setup executable is Authenticode
signed. If it is signed, verify the signature in PowerShell:

```powershell
Get-AuthenticodeSignature .\setup.exe | Format-List
```

For a handoff that says the installer is signed, expected good output has
`Status` equal to `Valid` and the exact signer named by that handoff. A `Valid`
result confirms both publisher identity and that the file is unmodified since
signing. Any other result is a stop condition for public beta installation.

A freshly-signed installer can still trigger a Windows SmartScreen "Windows
protected your PC" prompt until the publisher builds up enough download
reputation with Microsoft. That prompt by itself is expected and is not a
stop condition as long as `Get-AuthenticodeSignature` reports `Status: Valid`
for the correct signer. SmartScreen reputation is a separate, slower-moving
signal from Authenticode validity; do not treat the absence of a SmartScreen
warning as proof of anything, and do not treat the presence of one as proof
the file is untrustworthy if Authenticode already reports `Valid`.
Before responding to the warning, verify the exact SHA-256 and the expected
publisher; a warning alone proves neither signature failure nor a valid
publisher. If the expected **More info** or **Run anyway** options are missing,
or the publisher/hash differs, stop.

## Operator Rule

Install only the exact release or field candidate named in the active handoff.
Verify its actual installer signature and delivery-specific checksum set. Use
the sidecar for a GitHub release download, or the complete delivery manifest
for an authorized USB/LAN kit. Approved sources include:

- the official CivicCast GitHub Release,
- a complete USB/LAN kit explicitly identified by the field handoff,
- a release artifact set built by your organization from source, or
- an internal package repository your organization controls.

Do not install a CivicCast setup executable received through email, chat, a
shared drive, or an issue comment unless your organization independently checks
the hash and release provenance.

---

## Historical: retired rc line

Everything below describes the retired public WSL2 line
(`v1.0.0-rc18` and earlier, repository `scottconverse/civiccast`) that this
repository does not carry. It is preserved as historical evidence only. The
verification document it used to cite
(`docs/releases/v1.0.0-rc18-verification.md`) is not present in this
repository -- it is omitted below rather than linked, because it does not
exist on `main`.

<details>
<summary>Expand: retired WSL2-line (rc17-rc18) release state</summary>

`v1.0.0-rc18` was the most recently published release on that other line and
superseded `v1.0.0-rc17`. Its signed GitHub release assets and complete
manifest were public. `v1.0.0-rc13` was withdrawn on that line; only rc18
and its matching proof assets were the recommended install.

</details>
