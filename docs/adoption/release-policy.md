# CivicCast Release Policy

> **Release state:** [v1.0.0-beta.20](https://github.com/scottconverse/civiccast/releases/tag/v1.0.0-beta.20)
> is the current published GitHub pre-release. Its
> [verification record](../releases/v1.0.0-beta.20-verification.md) describes
> the exact public package and its evidence limitations; an owner-held
> corrected candidate does not replace the published package until publication.
>
> **Historical Beta 13 evidence:** v1.0.0-beta.13 was published October 11,
> 2026. The 26-asset release includes the signed
> installer, runtime packs, manual assets, checksums, sidecar, release-matched
> downloader, station bundle manifest and multipart station archive. The
> [verification record](../releases/v1.0.0-beta.13-verification.md) binds the
> package to source 8053fb1cf05b989fb1f23e650893cc6abfa659d4 and producer run
> 38160778457; normal upgrade, interrupted-install repair, and fresh-install
> five-minute observations passed across all three channels. See the
> [GitHub release](https://github.com/scottconverse/civiccast/releases/tag/v1.0.0-beta.13).
> release-truth.yaml is the authored release-state record. Earlier published
> beta releases are superseded; Beta 8 and 9 were never published. Beta 2 was
> never published and exists only as an internal upgrade-baseline kit. See
> [docs/releases/release-truth.yaml](../releases/release-truth.yaml) for the
> authored release-state record and current version.

Status: current beta adoption guidance

## Release Channels

- **Source/runtime release:** code, docs, tests, generated API artifacts, and
  release identity are aligned and tagged.
- **Packaged Windows release:** a Windows setup executable is attached to a
  GitHub Release and has exact-asset checksum and smoke proof.
- **Partner hardware proof:** a station or lab validates hardware or headend
  behavior and records redacted evidence.

Do not use evidence from one channel to claim another channel passed.

## Required Before A Source/Runtime Tag

- Release plan updated.
- Changelog updated.
- Runtime version updated.
- Generated OpenAPI and TypeScript artifacts updated.
- Release verification page added.
- Targeted tests pass.
- Policy suite passes.
- audit-lite passes for each meaningful slice.
- audit-full passes before merge/tag.

## Required Before A Packaged Windows Claim

- GitHub Release contains the exact Windows setup executable.
- SHA-256 checksum is published.
- The exact downloaded asset is smoke-tested, not a local build artifact.
- Authenticode signing status is stated. The Windows installer is signed (Azure Trusted
  Signing; verified publisher Scott Converse); release notes explain that SmartScreen may
  still warn until the certificate earns reputation, and how to verify.
- Installer proof records Windows version, CivicCast version, setup outcome,
  first-admin handoff, and System Health result.

The draft-first sequence is mandatory:

1. Verify the complete owner-held asset set without publishing it:
   `python scripts/policy/check_published_release_assets.py <tag> --candidate`.
2. Obtain the owner's explicit publication approval.
3. Publish the release, then immediately verify its live publication state and
   asset set with
   `python scripts/policy/check_published_release_assets.py <tag> --require-published`.
4. Run the live visitor audit before announcing the release.

## Required Before A Hardware Or Platform Claim

- The exact hardware, platform, or distribution target is named.
- Proof is run on the named target.
- Evidence records what passed, what failed, and what was outside scope.
- Claims are limited to the proven configuration.

Examples: SDI, DeckLink, Comcast/headend delivery, Roku Channel Store
publication, Fire TV, Apple TV, Android TV, DRM readiness, and hosted-service
operation all require separate proof.

## Security Release Handling

Security reports follow `SECURITY.md`. Public issues should not be used for
vulnerability reports. A security fix release should describe impact, affected
versions, fixed versions, and operator action without exposing exploit details
before coordinated disclosure.

## Overclaim Rules

- Do not call a provider, platform, hardware path, or release artifact proven
  until the exact path has evidence.
- Do not hide unfinished work by changing docs.
- Do not replace failed proof with softer marketing language.
- Do not claim legal, procurement, accessibility, public-records, or retention
  certification.
