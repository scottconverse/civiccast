# Support

CivicCast brings station scheduling, broadcast, recording, caption review, and
resident publication into one native Windows system. Support is
community-driven; there is no commercial support contract or SLA.

## How To Get Help

1. **Read the early-adopter docs first.** Start with
   [docs/adoption/early-adopter-quickstart.md](docs/adoption/early-adopter-quickstart.md),
   [docs/adoption/support-intake.md](docs/adoption/support-intake.md),
   [FAQ.md](FAQ.md), [docs/USER-MANUAL.md](docs/USER-MANUAL.md), and
   [docs/tester/START-HERE.md](docs/tester/START-HERE.md).
2. **Search existing issues.** [GitHub Issues](https://github.com/scottconverse/civiccast/issues)
   may already cover your question.
3. **Open a question or bug issue.** Use **Report a beta issue** in the
   installer, operator console, or resident portal, or open the repository's
   bug-report template directly. Include the CivicCast version, operating
   system, the screen where the issue happened, the exact operator
   message, and the steps already tried. Never
   include passwords, recovery codes, staff tokens, or private meeting
   material in a public report.
4. **Use [GitHub Issues](https://github.com/scottconverse/civiccast/issues) for
   open-ended planning too** (GitHub Discussions is not enabled on this
   repository). Examples: whether CivicCast is a fit for an HOA,
   public-access station, school board, or nonprofit workflow.

The source manual describes current station operation, scheduling,
output and recorded-caption settings. For an installed station, use the Help
bundled with its release or the matching manual assets from that release page.
RC5 is published ([release notes](docs/releases/v1.0.0-rc.5-release-notes.md)). Its known issues are listed there. Source changes after publication do not update an installed station.

## Retired WSL2 Support History

The following paragraph records the old WSL2 product line only. It is not
installation or support guidance for the native product in this repository.

`v1.0.0-rc13` was withdrawn after a genuine clean-host bootstrap failure;
`v1.0.0-rc18` was the most recently published release on that retired line.
The full clean-host product walkthrough was last completed against rc17's
exact bytes on 2026-07-20 and passed -- install with no restarts, first admin
and recovery kit, backup and scoped database restore, private rehearsal and
packaging, the publish privacy gate, resident playback, and unaided
cold-reboot recovery. Captions were not exercised in that pass. (The
verification record for that run is not present in this repository -- it
belongs to the separate, private `scottconverse/civiccast-wsl-legacy` repository.)
Preserve the installer log and support bundle for every failure.

Supported early-adopter paths are documented self-hosted deployment profiles,
with Windows running CivicCast as a native Windows service (no WSL, no
Ubuntu). Operator or beta-test installs require durable storage. The native
installer provisions the bundled local PostgreSQL service and saves its
connection URL for the supervisor; a `DATABASE_URL` service-environment value
overrides that URL. The Setup screen prepares the database schema before the
first admin and recovery kit are created. Standalone app use can prepare a
local SQLite database when no database URL is configured; this is not the
native Windows service default. In-memory stores are for tests and explicitly
enabled throwaway development.

## Native Windows Beta

The native Windows runtime ([ADR 0021](docs/adr/0021-native-windows-runtime.md))
is a public beta. The current published package is `v1.0.0-rc.5`, available
from its [GitHub Release](https://github.com/scottconverse/civiccast/releases/tag/v1.0.0-rc.5).
Its [release notes](docs/releases/v1.0.0-rc.5-release-notes.md)
identify the signed installer, verification and known issues.
Package and installation claims must name the exact published version; source
changes do not update an installed station. The
[RC5 release notes](docs/releases/v1.0.0-rc.5-release-notes.md)
contain package-specific results and boundaries. There is no dedicated,
SLA-backed support intake for the native beta.

If you are working on, evaluating, or running the native line:

- Read [ADR 0021](docs/adr/0021-native-windows-runtime.md) and
  [BRANCHES.md](BRANCHES.md) first.
- When reporting a source issue, include the branch or commit being used. The
  station-operation, unattended-scheduling, broadcast-output, and recorded-
  caption updates ship in Beta 16; Beta 15 remains a separate historical package.
- Use a regular [GitHub Issue](https://github.com/scottconverse/civiccast/issues)
  for questions (GitHub Discussions is not enabled on this repository), and say explicitly in the report
  that it concerns the native line and which release tag or source commit you're on -- the templates don't yet
  have a native-specific path, so context has to be spelled out by hand.
- Do not treat anything reported against the native line as a supported,
  SLA'd, or fully field-proven path. The same "community-driven, no SLA"
  posture above applies. The Beta 15 verification record
  ([`docs/releases/v1.0.0-beta.15-verification.md`](docs/releases/v1.0.0-beta.15-verification.md))
  states what was and was not proven. Historical Beta 12 evidence remains in its
  [verification record](docs/releases/v1.0.0-beta.12-verification.md). Superseded Beta 11 and Beta 10
  records, including the Beta 11 host caption report, remain version-scoped
  historical evidence. The superseded Beta 7
  verification records are
  [`docs/releases/v1.0.0-beta.10-verification.md`](docs/releases/v1.0.0-beta.10-verification.md)
  and
  [`docs/releases/v1.0.0-beta.7-verification.md`](docs/releases/v1.0.0-beta.7-verification.md);
  these are engineering records, not a support commitment.

The release verification record is an evidence boundary for its named package,
not a service-level agreement or complete field-qualification report. Support
remains community-driven; no broader intake or response commitment is implied.

## What Is Not Supported

- Untagged branch snapshots as production releases.
- Public ActivityPub exposure without the documented base URL, station key,
  policy mode, operator moderation, and redacted target-instance proof.
- Provider readiness claims without configured credentials and controlled
  evidence.
- SDI, DeckLink, Comcast/headend, streaming-TV app-store, DRM, or hardware
  claims without separate partner proof.
- Legal, accessibility, retention, or procurement certification.
- Custom integrations, white-labels, or vendor-specific deployments outside the
  documented module catalog.

## Security

For security vulnerabilities, see [SECURITY.md](SECURITY.md). Do not file public
issues for security reports.

## Bugs And Feature Requests

Use the bug-report and feature-request issue templates at
[.github/ISSUE_TEMPLATE/](.github/ISSUE_TEMPLATE/). Follow
[docs/adoption/support-intake.md](docs/adoption/support-intake.md) when deciding
which logs or support-bundle details are safe to share publicly.

The October 13 Beta 15 R9 field evaluation recorded several unresolved output,
signing, and caption-range observations. The report does not establish all
causes or complete every workflow. The owner deferred follow-up until after
Beta 16 release; the [Beta 17 backlog](docs/ops/beta17-deferred.md) preserves
the evidence status and avoids treating incomplete checks as confirmed bugs.
