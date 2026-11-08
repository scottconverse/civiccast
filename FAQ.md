# CivicCast FAQ

Questions and practical answers for CivicCast. Source documentation describes
the unpublished RC2 candidate; public downloads remain RC1.

## What is CivicCast?

CivicCast is open-source broadcast software for public meetings and community
media. It helps a small organization stream or upload a meeting, publish it to
a public portal, preserve archive copies, create captions and summaries, and manage the public replay.

## Who is it for?

School boards, HOA boards, city councils, county boards, commissions,
nonprofits, public-access stations, and community groups that need durable
public video without per-minute CivicCast software fees or appliance lock-in.
Optional cloud AI providers may charge for usage.

## How do I install it?

Follow [Install CivicCast on Windows](INSTALL-WINDOWS.md). The download helper supplies the installer, the matching runtime packs and complete signed station-model bundle, verifies them, creates the correct folders and runs setup. You do not need Git, Python, Docker, WSL or a separate Ollama installation. Open **CivicCast Operator Console** after setup completes and create the first administrator on the station computer.

Use the same complete kit for a new station or an existing installation. Back up a working station and plan the upgrade interruption. The installation guide includes supported local administrator-password recovery for previous test installs.

## How do I verify the installer download?

Read [docs/install/windows-release-trust.md](docs/install/windows-release-trust.md).
At minimum, compare the SHA-256 hash published with the release to the hash of
the `.exe` you downloaded. A checksum proves the bytes match the release; code
signing or attestation status is called out separately in each release.

## What do I need before the first real meeting?

- A Windows 11 host that passes `civiccast doctor`.
- The native station's installer-provisioned local PostgreSQL service and a
  first local admin account with a saved recovery kit. Standalone app/setup
  mode can use managed SQLite; that is not the native Windows service default.
- A first local admin account and saved recovery kit from the Setup screen.
- FFmpeg for packaging video.
- Ollama and model bundles if you want local summaries or translation. The complete station download includes the signed packs and local Ollama runtime. Bring your own API key instead if
  you choose a paid hosted model.
- The camera, encoder, or NDI/RTMP/RTSP/SRT source you plan to use.
- Archive and notification credentials only if your station will use those
  providers during the test.

## Do I need Postgres?

Yes, for the native Windows service: the installer provisions local PostgreSQL
and configures the service to use it. A technical administrator can provide a
different PostgreSQL `DATABASE_URL`. The standalone app/setup path can prepare
a managed SQLite database when no database URL is set; that development/setup
fallback is not the native service default. In-memory stores are for tests and
explicitly enabled throwaway development.

## How do I connect a camera?

The normal app includes server-side source, network, and storage probes. Set up
a valid, reachable camera or encoder source (for example RTSP, SRT, RTMP, or
NDI), then run a fresh pre-flight check before taking it live. A configured
source must pass that check; the bundled rehearsal sample is only a local
rehearsal source. **Source preview unavailable** means the console has no video
preview there; it does not mean the server cannot probe the source.

## Does CivicCast support NDI?

Yes for planning and readiness checks. CivicCast can inspect the local NDI
handoff posture and generate FFmpeg-to-NDI command plans. Some NDI components
cannot be redistributed publicly with CivicCast, so the operator may need to
install approved NDI runtime or SDK pieces on the station host.

Technical admins can use the `civiccast cable` CLI for detailed NDI evidence,
but meeting operators should start with the plain-language source setup guide
in **Run Meeting**.

## What happens if the network drops during a live meeting?

If the configured source or its network path drops during a meeting, check the
Live and Readiness screens for the current source/session state and follow the
station's recovery procedure. Live start requires a valid source to pass fresh
pre-flight; this is not a guarantee that an outside source or network will stay
available throughout a meeting.

For a meeting that must be preserved, also record locally at the camera,
encoder, or station machine. After the meeting, upload the local recording,
package it, and publish the replay even if the live stream was interrupted.

## How do captions, summaries, and translation work?

Live Whistle captions run locally and may appear on the channel without
operator review. Captions generated for recordings are reviewed before they
attach to a published file. Summaries are sourced: quantitative claims must
be backed by transcript timestamps before approval. Spanish translation can
publish an additional WebVTT track while keeping the original English captions
available.

The live-caption path automatically retries Whistle while Whisper supplies
backup captions. The 30-second initial cooldown and capped backoff delays
(60, 120, 240, then 300 seconds) govern failed probes; retries continue while
the affected channel has audio. A slow warning appears after 10 seconds. At
30 seconds, the watchdog terminates an unresponsive child. The 210-second
queue bound comes from the serialized request allowance `(3 × 2 + 1) × 30`,
not the retry schedule. The default queue caps at 42 five-second WAV chunks;
after sustained overload it can catch up only at a finite rate. Diagnostics
are bounded and do not archive audio.

Test builds may use simplified stand-in captions and summaries that are not
meant for real meetings. A real beta station should use the local model
runtime or leave that lane blocked until the model bundle is installed and
hash-verified.

## Where does the public watch the meeting?

Residents use the public portal URL for live channels, schedules, and published
recordings. Staff package and explicitly publish recordings before they appear
there. Residents do not need an operator account.

## How do subscribers work?

Residents can use email double opt-in and public RSS feeds. Automatic
subscriber notifications on publication and podcast publication are not available
in this edition. Email handles and
webhook secrets are encrypted at rest. Public subscribe endpoints are
rate-limited to reduce spam and abuse.

## Can CivicCast publish to YouTube or the Internet Archive?

Internet Archive and YouTube VOD adapters use local recording files and configured
provider credentials. NAS export copies files to the configured destination.
YouTube Live creates and binds a broadcast; station output must still be
connected to its ingest. Provider adapters default to mock mode; configure the
service environment as described in the manual before using a real provider.

## Does ActivityPub work?

ActivityPub is fully operator-gated and off by default. Technical stations can
enable it with a public base URL, generated station key, moderation mode, and
operator policy controls. The recommended beta posture is approval-only mode
with authorized fetch enabled.

Do not enable public federation for a station until you have a controlled
target instance and can keep redacted evidence of follow, moderation, and
delivery behavior.

## Is the signed record legally notarized?

No, not by default. CivicCast can export PDF/A-3B signed-record artifacts with
provenance and approval metadata. A station needs its own legal review and a
real timestamp or signing authority before making jurisdiction-specific legal
record claims.

## What should I do if setup gets stuck?

Open **System Health** and choose **Create support bundle**. Include the
generated bundle path or attach the bundle if your support channel allows it.
Also include the exact screen and button where you stopped. Do not paste
tokens, passwords, private keys, resident data, or private meeting content.

## Where are the technical details?

- [User Manual](docs/USER-MANUAL.md)
- [Admin Guide](docs/admin-guide.md)
- [Meeting Operator Guide](docs/meeting-operator-guide.md)
- [Records Clerk Guide](docs/records-clerk-guide.md)
- [Technical Operations Reference](docs/technical-ops-reference.md)
- [Operator Language Guide](docs/operator-language-guide.md)
- [Architecture overview](ARCHITECTURE.md)
- [API guide and reference](docs/API-REFERENCE.md)
- [ActivityPub ops](docs/ops/activitypub-federation.md)
- [NDI output ops](docs/ops/ndi-output.md)
- [Release-state record](docs/releases/release-truth.yaml) -- the authored
  source of truth for which tag is current, staging, or historical. (The rc11
  through rc18 and v0.2.0/v0.1.0-rc6 verification documents this FAQ used to
  link here belonged to the retired, separate `scottconverse/civiccast-wsl-legacy`
  repository and are not present in this repository, so they are not linked.)
- [LPM beta handoff](docs/tester/lpm-beta-test-handoff.md) for the controlled
  Longmont Public Media tester artifact and station-side proof steps.

## Release status and evidence

RC1 is the [published release](https://github.com/scottconverse/civiccast-native/releases/tag/v1.0.0-rc.1); its [verification record](docs/releases/v1.0.0-rc.1-verification.md) identifies the signed package and scoped checks.

Beta 15 passed Windows Sandbox installation, first-admin setup, built-in Help and built-in uninstall checks. Its corrected installer also restored a working station automatically after a simulated activation failure. The bundled Welcome programme aired and returned to slate in the short-programme rollover check. The [Beta 15 verification record](docs/releases/v1.0.0-beta.15-verification.md) identifies each tested package and its results.

Beta 13's earlier normal upgrade, controlled interruption and repair, and separate fresh-install observations are historical and apply only to that release. See its [verification record](docs/releases/v1.0.0-beta.13-verification.md) and [fresh-install evidence](docs/ops/evidence/beta13-fresh-install-observations-2026-10-11.json). Optional raw-model downloads in the separate acquisition screen cannot substitute for signed activation packs. The earlier Beta 12 distribution history is recorded in its [verification record](docs/releases/v1.0.0-beta.12-verification.md).
