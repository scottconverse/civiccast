# CivicCast roadmap

CivicCast brings station scheduling, channel playout, meeting recording,
caption review, and resident publication into one native Windows system.

## Sprint 6 source work shipped in Beta 20

[Sprint 6](ops/sprint6.md) addresses remaining webhook, Roku and documentation
corrections, preparation efficiency and retry scheduling, hardware-aware encoding,
and CivicCast station federation. Catalog sharing and central channel management
are implemented. The owner deferred TownLight (formerly CivicSuite) integration,
including TownLight: Station, to 2.0. This work shipped in Beta 20.

The [documentation correction](ops/manual-reconciliation-2026-10-17.md) reconciles
operating instructions and generated references for that package. Existing
release evidence retains its original identity.

## Beta 20 release

The [published Beta 20 release](https://github.com/scottconverse/civiccast/releases/tag/v1.0.0-beta.20) ships the Sprint 6 source work and the current manual. The [Beta 20 package record](releases/v1.0.0-beta.20-verification.md) records its release identity and scoped checks. Earlier release records remain historical.

## Beta 19 release

The [published Beta 19 release](https://github.com/scottconverse/civiccast/releases/tag/v1.0.0-beta.19) connects remote guests to channel output, adds live
video and an audio bed to the bulletin board, and completes the native
viewer app controls for channel/replay browsing, artwork, search, deep
links and playback options. The earlier Beta 17 and 18 packages delivered
subscriber notices, Podcast episodes, magic-link delivery, underwriting
dayparts and Cablecast station import. The [Beta 19 package record](releases/v1.0.0-beta.19-verification.md) identifies its signed artifacts and scoped checks.

## Beta 18 release

The [Beta 18 release](https://github.com/scottconverse/civiccast/releases/tag/v1.0.0-beta.18) adds the Setup-admin Cablecast station-import workflow: connect, preview proposed show and schedule metadata with conflicts and skips, explicitly apply, inspect the batch, and confirm rollback. The [package record](releases/v1.0.0-beta.18-verification.md) identifies the signed candidate and its checks. This package predates Beta 19's remote-guest, bulletin-board and native-viewer additions.

## Beta 17 release

The [published Beta 17 release](https://github.com/scottconverse/civiccast/releases/tag/v1.0.0-beta.17) adds three completed Sprint 5 workflows: subscriber
publication notices with delivery outcomes and retries; Podcast MP3 episodes,
enclosures and RSS; and selection of saved
underwriting dayparts. The [package record](releases/v1.0.0-beta.17-verification.md)
identifies the signed package and its scoped checks. The later Beta 18 and 19 releases complete the remaining Sprint 5 features; protected paywall media remains outside this scope.

## Beta 16 release

The [published Beta 16 release](https://github.com/scottconverse/civiccast/releases/tag/v1.0.0-beta.16) adds:

- Encrypted station backup and restore, supported network setup, staff accounts and sessions, and a restricted Windows service identity. See the [station operations record](ops/station-operations-goal.md).
- Station-local scheduling across daylight-saving changes, bounded operational storage with usage reporting, low-disk handling, and automatic output recovery. See [Sprint 2](ops/sprint2-unattended.md).
- RTMP output, per-output audio processing, complete programme endings, coordinated HLS segment retention, and explicit recorded-caption language and completion settings. See [Sprint 3](ops/sprint3-output-completion.md).
- Updated product and operator guidance for this combined source edition.

The [Beta 16 verification record](releases/v1.0.0-beta.16-verification.md) binds the signed package to its installation results. [Beta 15 evidence](releases/v1.0.0-beta.15-verification.md) remains historical.

## Deferred follow-up

The owner deferred the field and workflow follow-ups in the [Beta 17
backlog](ops/beta17-deferred.md) for work after Beta 16. The list
separates confirmed Beta 15 observations from causes and workflows that remain
unconfirmed or incomplete. It does not qualify the Beta 16 package.

## Measured operation history

A Beta 11 host ran Public, Government, and Education together for 72 hours
with **99.78% live caption-input coverage**. The [72-hour host
report](ops/beta11-host-caption-performance-72-hour-2026-10-11.md) explains the
channel totals and measurement. This remains historical Beta 11 evidence; it
is not a Beta 15 field result or Beta 16 package qualification.

## Roadmap ownership and evidence

The owner sets implementation and release scope. The [Beta 20 verification
record](releases/v1.0.0-beta.20-verification.md) documents the current package. The [Beta 15 verification record](releases/v1.0.0-beta.15-verification.md) documents that older package's exact
identity and installation observations. The [Beta 17 deferred
backlog](ops/beta17-deferred.md) cites the separate Beta 15 R9 field report and
describes what that host evaluation did and did not establish. No release date
or additional feature is promised here.

The [earlier roadmap](history/roadmap-before-beta12-doc-audit-2026-10-11.md) is retained as historical planning. Its WSL/rc release line, old milestone numbers, and implementation cursor are not the current native Windows roadmap.
