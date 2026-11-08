# macOS Installation Posture

Date: 2026-05-22
Last reviewed: 2026-10-11; no supported public macOS package is available.

Status: no supported public macOS package in the current beta.

## Support Level

CivicCast can be evaluated from source on macOS by technical users, but the
project does not currently publish a supported `.pkg` installer for operators.
Do not describe macOS as a supported self-install path until package build,
signing/notarization posture, install, first launch, upgrade, and uninstall
evidence are recorded.

Future macOS package work should target Apple Silicon first. Intel macOS
packaging is not a current beta target.

## Signing Posture

No current CivicCast macOS package should be treated as signed or notarized
unless the specific GitHub Release says so and provides verification evidence.
Gatekeeper warnings are expected for unsigned local builds; they are not proof
that a package is safe.

## Approved Installation Sources

**There is no approved macOS install source in this beta.** The beta line
ships no macOS installer or package. The current native Windows release is
`v1.0.0-beta.20`, a published pre-release, not a macOS package. See its
[release page](https://github.com/scottconverse/civiccast/releases/tag/v1.0.0-beta.20),
the [Beta 20 verification record](../releases/v1.0.0-beta.20-verification.md),
and the [Windows install guide](../../INSTALL-WINDOWS.md) plus
[Windows Release Trust And Verification](windows-release-trust.md).

Technical users can still evaluate CivicCast from source on macOS (see
Support Level above), but there is no packaged artifact to fetch and no
release to verify against.

Do not install CivicCast from third-party mirrors, repackaged binaries,
unsolicited downloads, files sent through chat or email, or packages whose name
does not match an official CivicCast release.

<details>
<summary>Historical: retired WSL2-line macOS reference (repository not present here)</summary>

An earlier revision of this page pointed testers at
`https://github.com/scottconverse/civiccast-wsl-legacy` as an approved source. That
repository was the retired, separate WSL2-line product -- it is now private
and not part of this repository's product line; its GitHub page does not
resolve from here. It never shipped an approved macOS package either.

</details>

## Evidence Required Before Supported macOS Claims

Before a macOS package is called supported, record:

- Apple Silicon model and macOS version;
- package artifact name, size, and SHA-256 digest;
- package signing and notarization result, or an explicit unsigned posture;
- install command or Finder flow;
- first launch result;
- `civiccast doctor` output;
- first-run setup wizard result;
- upgrade and uninstall behavior;
- Gatekeeper warning text and approved operator guidance;
- screenshots or terminal logs with secrets redacted.

CI or durable manual evidence must also show package artifact build, sidecar
generation, sidecar verification, release upload, and the explicit
Apple Silicon package result.
