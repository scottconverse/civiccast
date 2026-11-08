# Code Signing Policy

## Current status

**Windows release artifacts ARE Authenticode code-signed** via **Azure Trusted Signing**
(Microsoft's cloud signing service; the private key is held in a Microsoft-managed HSM and never
leaves Azure). The installer is signed during the GitHub Actions release build using the project's
service principal — no key or `.pfx` ever touches the runner. Verify a downloaded installer with:

```powershell
Get-AuthenticodeSignature .\setup.exe | Format-List Status, SignerCertificate
```

Expect **Status: Valid**, signer **CN=Scott Converse**, chaining to the **Microsoft Identity
Verification Root Certificate Authority 2020**, RFC3161-timestamped.

**SmartScreen note:** Windows may still show a "Windows protected your PC / unrecognized app"
SmartScreen prompt on a freshly published installer. This is **reputation**, not the signature: a
newly issued certificate has no SmartScreen download history yet, and Microsoft's 2026
Trusted-Signing certificate-authority changes reset reputation for new signers (EV certificates no
longer bypass this). The prompt shows the verified publisher (Scott Converse) and fades as download
volume accrues. See [docs/tester/SMARTSCREEN-WALKTHROUGH.md](docs/tester/SMARTSCREEN-WALKTHROUGH.md)
for the operator/IT walkthrough.

## How signing is wired

- **Where:** the native release chain — `.github/workflows/native-beta-candidate-artifacts.yml`
  builds the unsigned installer, then `.github/workflows/sign-native-installer.yml` signs it with
  `azure/artifact-signing-action@v2` and a fail-closed `Get-AuthenticodeSignature` check aborts if
  the result is not `Valid`. (`.github/workflows/release-artifacts.yml`, the legacy WSL-era release
  pipeline that used to own this step, was retired under chore/retire-wsl-lane.)
- **Integrity ordering (issue #253):** every published checksum/sidecar/manifest is regenerated from
  the *signed* binary (a fail-closed step asserts `sidecar.sha256 == the signed installer's hash`),
  so the published integrity artifacts always describe the signed file.
- **Auth:** service-principal client secret passed directly to the signing action (this account has
  no Azure subscription and no OIDC federated credential); a token-endpoint preflight fails the build
  fast on a bad/expired secret.
- **Account/profile:** Azure Artifact (Trusted) Signing account `scottconverse-signing`, certificate
  profile `ScottConversePublic`, endpoint `wcus.codesigning.azure.net`.

## Supply-chain provenance: sigstore/cosign was evaluated and denied

Sigstore/cosign keyless attestation was evaluated for the native release chain and **denied by
the owner** — this repo's supply-chain provenance runs on Azure certs (Authenticode) only, not
Sigstore. See [ADR 0022](docs/adr/0022-sigstore-attestation-denied.md) for the decision record.

The native chain (`native-beta-candidate-artifacts.yml` builds, `sign-native-installer.yml`
signs) performs **Authenticode signing only**. There is no cosign/sigstore step anywhere on the
native path, no code in this repository generates a `.sigstore.json` bundle, and no verifier in
this repository requires one. The Windows setup executable's only cryptographic provenance is its
Authenticode publisher signature (see "How signing is wired" above); native distribution packs
(`.ccpack` manifests under `civiccast/native/`) additionally carry **ed25519 signatures** verified
by the installer at unpack time — see `verify_native_pack` in
`civiccast/installer/native_packs.py` for that code path. Package sidecars
(`*.sidecar.json`) carry a real SHA-256 of the artifact bytes and an `attestation` field that is
always `null`, kept only for sidecar-schema stability.

To verify a downloaded release artifact yourself:

1. **Windows installer (`setup.exe`):** `Get-AuthenticodeSignature` per "Current status"
   above, and compare its SHA-256 against the matching `*.sidecar.json` / release manifest.
2. **Native distribution packs (`*.ccpack`):** verified automatically by the installer via their
   embedded ed25519 signature; `uv run civiccast installer verify-package --artifact --sidecar`
   reproduces the sidecar-hash half of that check from the command line.
3. **Any other release asset:** compare its SHA-256 against the release's published
   `SHA256SUMS` / manifest file. No other asset in this chain carries a code signature.

## Distribution
Releases are published at: https://github.com/scottconverse/civiccast-native/releases
(beta candidates appear there as prereleases under the `v1.0.0-beta.N` tag family; see
`docs/ops/release-candidates.md`). The retired WSL2 line's `v1.0.0-rcNN` releases live in
the separate, private `scottconverse/civiccast-wsl-legacy` repository and are not an install target.

## Privacy

Code signing establishes publisher identity; runtime privacy is a separate concern documented elsewhere.

This software will not transfer any information to other networked systems unless specifically requested by the user.
