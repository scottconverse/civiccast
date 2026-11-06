---
title: CivicCast Technical Operations Reference
subtitle: Installation, configuration, command-line evidence, and recovery operations
author: The CivicCast Authors
date: 2026-10-12
documentclass: article
geometry: margin=1in
fontsize: 11pt
mainfont: Liberation Serif
sansfont: Liberation Sans
monofont: Liberation Mono
colorlinks: true
linkcolor: blue
urlcolor: blue
toccolor: black
---

# CivicCast Technical Operations Reference

> **v1.3 operator-product foundation note.** This reference preserves the
> detailed installer, command-line, recovery, credential, and release-proof
> material for admins, support engineers, and technical operators. Most meeting
> operators and records clerks should start with the persona guides linked from
> [User Manual](USER-MANUAL.md).
>
> This reference focuses on the installer-first path, first-admin recovery,
> backup and restore rehearsal, provider setup guidance, safe-to-broadcast,
> support bundles, release trust, safer secret defaults, real-boundary smoke
> tests, and task-based operator guidance.
>
> **Station report note.** Readiness and handoff commands report what is
> configured or observed on a particular station. Their output is operational
> evidence, not a universal release-acceptance checklist. A configured path or
> credential reference alone does not prove that an external service accepted
> a real upload.

## Who this reference is for

Operators running a CivicCast deployment for an organization that needs to put public-interest video in front of the public:

- School boards
- HOA boards
- City councils, county boards, and other local government bodies
- Boards and commissions of all kinds
- Nonprofit boards
- Local public access TV stations
- Individuals or community groups producing public-interest programming

This reference assumes you can install software on the machine CivicCast runs on,
can edit a configuration file, and can read a log line. It does not assume you
administer Linux servers, run Kubernetes clusters, or maintain CDN
configurations. The first-run wizard walks through staff tokens, model download,
portal checks, and publish-target checks before an operator treats the station
as ready.

## What CivicCast does

CivicCast is open-source, self-hostable civic meeting recording and publication
software. The application wires a production live-source probe into live
pre-flight, in addition to the recorded-media and Portal workflows. A working
probe is not proof that a particular station's source, hardware, output,
archive, CDN or headend works end to end. The Beta 20 verification record
identifies the installed package's scoped checks and limitations;
station-specific external services still require their own checks.

## AI models: defaults and operator selection

CivicCast has operator-selectable models for captions, summary, and translation.
Native live captions use Whistle as the CPU primary, with Whisper as the live
fallback; Whisper also handles recorded-media transcription. Operators choose
models in the operator console under **Setup -> AI Models** (see "AI Models
console" below). Selection is also exposed over the staff API at
`/api/staff/ai-models`.

The current defaults and component identities are:

1. **Live captions** use the included Whistle CPU engine as the primary and
   Whisper fallback. Failed Whistle probes use a
   30-second initial cooldown followed by capped delays of 60, 120, 240 and
   300 seconds; retries continue while the affected channel has audio. A
   10-second slow warning is emitted, and the 30-second watchdog terminates
   an unresponsive child. The 210-second queue bound is `(3 × 2 + 1) × 30`
   seconds of serialized request allowance; it is not a delivery guarantee.
   The default queue holds at most 42 five-second WAV chunks, and catch-up
   after sustained overload is finite. Diagnostics are bounded; audio is not
   archived. Whisper fallback requests have a 120-second deadline by default;
   set `CIVICCAST_WHISPER_FALLBACK_TIMEOUT_SECONDS` from 1 to 600 seconds to
   change it. This is a per-request limit, not an end-to-end caption delivery
   guarantee. **Recorded-media captions** use the selected local
   faster-whisper model; Medium is the required tier and Large is optional.
   Check the current catalog on the station for the actual selection.
2. **Summary** uses an *adaptive* local default gated on GPU presence AND RAM,
   not RAM alone. On a box with **a real (NVIDIA/NVML-detected) GPU and 16 GB
   or more of system RAM**, the default is Ollama `gemma4:12b` (the
   long-context 12B QAT model, registry slug `gemma4-12b-ollama`). On any
   **CPU-only box — regardless of how much RAM it reports** — the default is
   Ollama `gemma4:e4b` (slug `gemma4-e4b-ollama`). Field evidence (2026-08-29,
   a 32 GB CPU-only reference station): the old RAM-only rule picked 12B on
   that box, which took 366s to complete one summary and then failed twice
   more under realistic memory pressure, while e4b completed every attempt
   (94-128s) — RAM headroom does not predict CPU token-generation throughput,
   a discrete GPU does. The first-run wizard reports which default the
   detected hardware gets, and commissioning provisions **both** summary tags
   (`gemma4:12b` and `gemma4:e4b`) so an operator can select 12B manually on
   a CPU-only box if they choose to accept the slower path. These runtime tags
   differ from the staged component IDs (`summary-gemma4-12b` and
   `summary-gemma4-e4b`). Component presence and model activation depend on the
   package and acquisition path; silent setup does not run the GUI acquisition
   or automatically fetch models. Provenance records the model tag, model digest, Ollama
   runtime version, and manifest source.
3. **Summary generation runs as an async job**, not a blocking request:
   `POST /api/staff/summaries/jobs` queues generation from committed
   transcript cues and returns immediately; the operator console (Asset
   detail, next to the offline caption job panel) polls
   `GET /api/staff/summaries/jobs/{job_id}` for `pending` -> `running` ->
   `complete`/`failed` state. A CPU-only local generation legitimately takes
   1-6+ minutes depending on meeting length and station load — this was a
   blocking `POST /api/staff/summaries/generate` request before 2026-08-29,
   which 503'd at the control plane's ~120s socket budget even when Ollama
   itself had already produced a completion. `POST /generate` still exists
   (a longer 600s socket budget now backs it) for a fast tier or a script
   that wants to wait synchronously.
4. **Spanish translation** uses the configured translation model in the
   recorded-caption workflow. Translated Spanish cues are queued for review;
   this does not translate the live English caption stream. Review the cues
   before attaching or publishing the translated track.

Deterministic adapters are test infrastructure. Check the installed runtime
and current station configuration before relying on an AI feature; the Beta 15
verification record describes the scoped installed-package checks and the
upgrade and soak paths not run.

### Cloud and frontier tiers (optional, default OFF)

Beyond the local defaults, the model catalog offers **hosted** tiers an operator
can opt into per feature: Ollama Cloud (`gemma4:31b-cloud`) and an OpenRouter
frontier route (Gemini 2.5 Flash) for summary, and Ollama Cloud for
translation. These tiers:

- are **off by default** — every feature ships on its local default and stays
  there until an operator explicitly selects a hosted model;
- **send meeting content to a third-party provider** (they are not private and
  require network access);
- may incur charges under the provider's current pricing and account terms;
  pricing may be per-token or use another provider-specific billing model, so
  check current rates before enabling a hosted model; and
- require the operator to acknowledge the provider terms and costs before the
  selection is saved. The consent (who accepted,
  and when) is recorded with the selection.

Only the **setup_admin** role can change a selection; the **meeting_operator**
role has read-only access to the AI Models console. The model catalog is
hard-coded (CivicCast does not fetch a provider model list at runtime), and the
local-runtime loopback guard remains in force — local models are only reached on
`127.0.0.1`.

### AI Models console

Operators reach model selection in the operator console at **Setup -> AI
Models**. The screen shows one card per feature with the current model, its tier
band (Local / Cloud / Frontier), provider-cost information where available, the privacy label, and a
runtime-availability hint. Selecting a hosted (cloud or frontier) model surfaces
a consent checkbox; the selection is only saved after the operator accepts the
provider terms and costs. Check the provider's current pricing. For the full
local-vs-cloud and consent guidance written for the people who run the station, see the
[Admin Guide](admin-guide.md) ("Cloud & frontier models") and the
[Meeting Operator Guide](meeting-operator-guide.md).

The selected model is the saved choice. An already-running live-caption runtime
continues with the model loaded when the service started; if that differs from
the saved live selection, restart the CivicCast service to apply the new live
model and confirm the active model afterward. A recorded-caption job started
after the choice is saved uses that saved model. Saving a selection alone does
not switch an active live process.

## Installation

The installer (`civiccast-installer`) is designed to:

1. Confirm it is running on a supported Windows host.
2. Probe your hardware and recommend a deployment tier.
3. Download and hash-verify the component packs it needs.
4. Install the CivicCast packages, register the station service, and run a
   first-run health check.

CivicCast is a native Windows product. There is no WSL2 bootstrap, no Linux
package, and no macOS build: nothing is installed inside a virtual machine, and
Windows is never asked to enable Virtual Machine Platform or Windows Subsystem
for Linux.

These are stock acceptance results for the bounded recorded-media path, not broad
station or hardware claims. NVDA and
other release-QA tools are separate developer/tester setup, not part of the
normal operator installer.

For operator setup details, read [Cross-Platform Installer](installer/cross-platform-installer.md). Development setup remains documented in the project [README](../README.md):

```bash
git clone https://github.com/scottconverse/civiccast.git
cd civiccast
uv sync --all-extras --group dev
uv run civiccast doctor
```

On Windows release-QA workstations, install NVDA with the bundled bootstrap
script:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-windows-accessibility-tools.ps1
```

Technical staff can use the handoff summary to collect station-specific status:

```bash
uv run civiccast installer beta-handoff --json
```

That summary reports available package, installation, dependency, model, mTLS
and provider evidence. Missing evidence can prompt station-specific follow-up;
the output is not a universal package-release gate or a claim about every
installation.

For linear-channel and headend-style output proof, use the
[Channel Egress Operator And Tester Runbook](ops/channel-egress-runbook.md).
It covers outgoing feed controls, FileSink continuity proof, SRT loopback proof,
caption decode-back proof, emergency banner proof language, and report rules.
For the native station, bundled `tsp.exe` is under
`<install-root>\packs\native-server-binaries\payload\tsduck\bin`; the
install root is derived from the service runtime. A channel using a UDP-TS
headend sink needs TSDuck for its compliance check; installation alone is not
output proof. If no UDP-TS channels are configured, headend verification is not
applicable. When it is applicable but TSDuck is missing, install it or set
`CIVICCAST_TSDUCK_PATH`, then run the channel's headend verification. The
historical R7/R9 report available for Beta 14 does not include the raw proof
folders, so its original failure cause is unknown; current candidate proof must
be recorded separately in the verification record.

For operating the recording-finalization worker (the service that turns an
ended live broadcast into a recorded, packaged VOD asset), use the
[Finalization Worker Runbook](ops/finalization-worker-runbook.md). It covers
the inline/external/off start modes, configuration knobs including the public
manifest base URL, the serving story for packaged output, and failed-job
recovery.

For selecting the CDN adapter (`CIVICCAST_CDN_PROVIDER`), the external
provider registry (`CIVICCAST_PROVIDER_*`), and the analytics trusted-proxy
reconciliation required when a CDN fronts the public portal, see
[CDN Selector and Provider Registry](ops/cdn-and-providers.md).

For the ActivityPub delivery retry worker and the retention review worker
(plus how they relate to the finalization worker's lifecycle), see
[Background Workers](ops/background-workers.md).

### Download and verify the complete Windows kit

Install only from the official release or from a package repository your
organization controls. Follow the exact release handoff and use its matching
`download_windows_release_artifacts.ps1` helper. With `-Install`, the helper
downloads and verifies `setup.exe`, all five runtime packs, and the complete
station-model bundle before running setup with `/S`; `-IncludePacks` stages the
complete kit without installing. Keep `setup.exe`, `packs\` and `station\`
together. A standalone `setup.exe` download is not the complete install kit.
Before running the helper, verify its own SHA-256 against the same release's
`SHA256SUMS.txt`. See the [Windows Release Trust And Verification](install/windows-release-trust.md)
checklist and the [operator quick start](tester/START-HERE.md). The
[Beta 20 verification record](releases/v1.0.0-beta.20-verification.md) is the
source for the current package's identity, installation evidence and limits.

For a direct setup-app hash check when the handoff calls for one, compare it to
the matching checksum entry:

```powershell
Get-FileHash .\CivicCast-Setup.exe -Algorithm SHA256
```

If the hash does not match the release page or sidecar, delete the installer
and download it again. Do not assume a candidate is Authenticode-signed; verify
the actual status and named publisher in the exact handoff. SmartScreen is not
a substitute for checksum or signature verification. See
[Windows Release Trust And Verification](install/windows-release-trust.md) for
the full operator checklist.

### Stock recorded-media dry run

Run this before using real meeting content:

1. Finish every installer readiness lane that applies to your station.
2. Confirm the storage lane reports ready.
3. Create the first local admin and sign in to the operator console.
4. Run `civiccast doctor` and save the hardware tier in your setup notes.
5. Create or upload a short recorded test file.
6. In System Health, run the private rehearsal and confirm it copies and
   validates that exact sample, finalizes a private recording, and loads
   resident preview.
7. Validate and package the recording; confirm it remains private.
8. Approve only the Portal surface.
9. Review captions and summary evidence before approving anything public.
10. Confirm the resident portal exposes only the approved recording.
11. Open the public portal from a second browser and confirm the replay works.
12. Create and download a redacted support bundle from System Health.

### Operator data and persistence

Do not run an operator or beta-test station without durable storage. If
`DATABASE_URL` is unset, CivicCast starts in local setup mode until the native
installer or operator Setup screen prepares the managed PostgreSQL database
and applies migrations. SQLite is a separate standalone backend; it is not the
database prepared for an installed native station. If
`CIVICCAST_ALLOW_EPHEMERAL_STORES=1` is set, CivicCast uses volatile in-memory
stores for tests or throwaway development only; that is not an operator install.

For a beta or production-like setup:

1. Use the installer storage lane or operator Setup screen to prepare managed
   local durable storage.
2. Create the first local admin in the Setup screen and save the recovery kit.
3. Start the API and operator console only after storage and first-admin setup
   succeed.
4. The native station uses bundled PostgreSQL and stores its connection string
   in the protected registry value. Standalone deployments may select a
   supported database explicitly with `DATABASE_URL` and apply migrations with
   `uv run alembic upgrade head`.

On native Windows, station paths derive from `%PROGRAMDATA%` (normally
`C:\ProgramData`), not the interactive user's profile. The default station data
root is `%PROGRAMDATA%\CivicCast`: PostgreSQL data is under
`data\pgdata`, media uploads and recordings under `data\uploads`, egress work
under `data\egress`, logs under `logs`, and first-run acquired packs under
`packs`. The installer progress log is
`%PROGRAMDATA%\CivicCast\install-progress.log`. A custom `PROGRAMDATA` value
changes these paths together. The service's installed binaries are rooted at
the service executable's install directory; do not diagnose service dependencies
using only the signed-in user's `PATH` or profile.

The operator console, asset library, schedule, publish records, summaries,
signed records, subscribers, podcast feeds, and beta-handoff evidence all
depend on this durable path.

### Staff-token recovery and subscriber secret rotation

Staff tokens should normally live in the durable staff-token store. Emergency
environment fallback tokens are supported only when
`CIVICCAST_STAFF_TOKENS_FALLBACK_WITH_DB=1` is set for a recovery window. A
token revoked in Postgres stays revoked even if the same secret appears in the
environment fallback list.

Generate every `CIVICCAST_STAFF_TOKENS` bearer secret with `civiccast token
generate-env`. CivicCast enforces the current versioned shape and rejects
obvious low-complexity values, but it cannot prove randomness from a string;
operators must use the generator. Pre-upgrade legacy formats fail at startup.
The deterministic-token override relaxes this check only for test fixtures and
must never be enabled on a station.

Subscriber confirmation and unsubscribe tokens use deployment-specific secret
material. New installs create or load durable secret material from
`CIVICCAST_SUBSCRIBE_SECRETS_FILE`, `CIVICCAST_CONFIG_DIR`, or the default
user config directory. To rotate safely:

1. Add the new current secret and keep the previous secret in the legacy list.
2. Restart CivicCast and confirm new subscriber links use the new secret.
3. Leave legacy secrets in place long enough for old confirmation and
   unsubscribe links to expire.
4. Remove the legacy entry only after the station's retention window closes.

Existing v0.8 deployments that still need the historical local proof key must
opt in with `CIVICCAST_SUBSCRIBE_ACCEPT_V08_LEGACY_SECRETS=1` during the
migration window. Do not use deterministic subscriber secrets for a real
station; that mode is only for tests.

## Command-line reference for operators and technical staff

Most non-technical operators should use the installer window and the operator
console. Technical staff, automation scripts, and support engineers use the
`civiccast` command when they need exact evidence, JSON output, or a repeatable
setup step. Commands that touch staff identity or durable records require
managed durable storage or a configured `DATABASE_URL` unless the help text
says otherwise.

Run `uv run civiccast --help` to see the installed command tree. Add `--json`
to supported commands when you are collecting evidence for a beta handoff or a
support ticket.

### Installer commands

| Command | Use it for | Important options |
| --- | --- | --- |
| `uv run civiccast installer plan` | Print the first-run setup plan for a deployment profile. | `--profile`, `--recommended-tier`, `--json` |
| `uv run civiccast installer health-check` | Report station readiness and configured publish-target posture. The result is station-specific, not a universal release gate. | `--profile`, `--json` |
| `uv run civiccast installer platform-plan` | Show the host requirements the installer checks before it runs. | `--os-family`, `--json` |
| `uv run civiccast installer verify-package` | Verify package bytes, sidecar hash, signed install manifest, and metadata (Authenticode evidence for a Windows `.exe` claiming `signed: true`). | `--artifact`, `--sidecar`, `--json` |
| `uv run civiccast installer summary` | Print the installer readiness summary used by the GUI. | `--json` |
| `uv run civiccast installer beta-handoff` | Summarize the beta package and station evidence supplied to the command; it does not define release-acceptance criteria. | `--release-manifest`, `--clean-windows-evidence`, `--json` |

### Model commands

| Command | Use it for | Important options |
| --- | --- | --- |
| `uv run civiccast model download` | Download the local AI model set or show the download plan. | `--dry-run`, `--cache-dir`, `--json` |
| `uv run civiccast model download --offline-bundle` | Build a hash manifest from model files already present in an offline bundle directory. | `--bundle-dir`, `--profile`, `--json` |
| `uv run civiccast model state` | Show the installer-facing model proof state. | `--json` |
| `uv run civiccast model import-offline` | Verify a model bundle from real file hashes during air-gapped setup. | `--bundle-dir`, repeat `--expected filename=sha256`, `--json` |
| `uv run civiccast model set-provider-key <provider>` | Store or clear a hosted AI provider API key (`ollama-cloud` or `openrouter`) in the OS keyring; write-only, never echoed. | `--key`, env `CIVICCAST_PROVIDER_API_KEY`, `--clear`, `--json` |

### Certificate commands

| Command | Use it for | Important options |
| --- | --- | --- |
| `uv run civiccast cert rotate civiccast-api` | Rotate the local-CA certificate and private key for one service identity. | `--json` |
| `uv run civiccast cert rotate civiccast-worker` | Rotate the worker service identity used by internal mTLS checks. | `--json` |

Private keys are written under `CIVICCAST_CERT_ROOT` when it is set; otherwise
CivicCast uses the default local certificate directory. On Windows, CivicCast
restricts private-key ACLs to the current user, `SYSTEM`, and Administrators.
Do not paste private-key material into support tickets.

### Staff-token commands

| Command | Use it for | Important options |
| --- | --- | --- |
| `uv run civiccast token generate-env` | Generate a versioned random bearer secret for one legacy `CIVICCAST_STAFF_TOKENS` entry. | None |
| `uv run civiccast token issue` | Issue a bearer token for a named operator. The secret is printed once. | `--operator-id`, `--display-name`, `--scopes`, `--save-keyring`, `--json` |
| `uv run civiccast token list` | List token metadata without bearer secrets. | `--json` |
| `uv run civiccast token revoke TOKEN_ID` | Revoke a token so staff-route requests fail closed. | `--reason`, `--json` |
| `uv run civiccast token rotate TOKEN_ID` | Revoke one token and issue a replacement for the same operator. | `--save-keyring`, `--json` |

Treat staff bearer tokens like passwords. Store them in the OS keyring or a
local secret manager, never in screenshots, chat, or a public issue.

Failed staff authentication uses a process-local observed-peer budget. After
saturation, exact full-token misses receive immediate `429` without expensive
verification or queueing; exact valid tokens bypass the failed-guess control.
Operators tuning `CIVICCAST_AUTH_RATE_LIMIT` or
`CIVICCAST_AUTH_RATE_LIMIT_WINDOW_SECONDS` should follow the deployment and
reverse-proxy guidance in `docs/ops/staff-route-protection.md`.

After upgrading a database that contains pre-fingerprint active lifecycle
tokens, rotate each one from the host with `uv run civiccast token rotate
<token-id> --save-keyring`, then replace the old secret. The API returns the
same precise rotation instruction for a legacy token while the observed peer
still has failure budget. If that peer is already saturated, it receives `429`
and must honor `Retry-After`; host-side rotation remains available. Revoked
legacy rows need no action.

### Cable and NDI commands

| Command | Use it for | Important options |
| --- | --- | --- |
| `uv run civiccast cable package` | Create a PEG/headend ZIP from real media and captions. | `--asset-id`, `--title`, `--media`, `--captions`, `--output-dir`, `--portal-url`, `--json` |
| `uv run civiccast cable ndi-check` | Check FFmpeg, NDI runtime, SDK inputs, and local sender readiness. | `--json` |
| `uv run civiccast cable ndi-plan` | Print the FFmpeg-to-NDI command plan for a local media file and NDI channel name. | `--media`, `--ndi-name`, `--muxer`, `--json` |

NDI commands may need local runtime software, SDK inputs, or hardware receiver
proof. If those pieces are absent, the command exits with an actionable blocked
message instead of a Python traceback.

## Hardware probe (`civiccast doctor`)

CivicCast ships a hardware probe that reports the operator-relevant facts about the machine and recommends a deployment tier:

```text
$ civiccast doctor
CivicCast 0.1.0 - hardware probe
  hostname: station-1
  os:       windows (Windows 11 26100, x86_64)

CPU
  brand:    AMD Ryzen 7 7800X3D 8-Core Processor
  cores:    8 physical / 16 logical

RAM
  total:    32.0 GB    available: 24.5 GB

Disk
  path:     /home/operator
  total:    1907 GB    free: 1842 GB

GPU
  name:     NVIDIA GeForce RTX 5070 Ti
  VRAM:     16.0 GB total / 15.5 GB free
  driver:   555.42.06    CUDA: 12.5

Recommended deployment tier: tier-1-plus
  Tier 1+ Streaming: 16-24GB VRAM. Captions, summary, and translation
  can stay loaded simultaneously - no hot-swap latency. Comfortable
  headroom for live caption + live translation simultaneously.
```

The probe data is also served as JSON at `/api/hardware` for integrators and automation pipelines, and the `--json` flag prints the same JSON to stdout:

```bash
civiccast doctor --json | jq '.recommended_tier'
"tier-1-plus"
```

### What the tier recommendation means

Per spec §7.7's hardware tier decision tree:

| Tier | VRAM | Capabilities |
| :---- | :---- | :---- |
| `tier-0` | none or < 8 GB | Batch-only or streaming-only on CPU. Captions and summary are slower. Suitable for stations broadcasting fewer than ~10 hours per week. |
| `tier-1` | 8-16 GB | GPU-accelerated captions and summary. TranslateGemma hot-swaps with summary model. The headline reference build (spec §10.2). |
| `tier-1+` | 16-24 GB | Captions, summary, and translation can stay loaded simultaneously — no hot-swap latency. |
| `tier-2` | 24+ GB | Multi-stream / consortium territory. Concurrent live streams (public + education + government channels) with full AI on each. |

The tier is a recommendation. You can run any deployment on any hardware, and the platform degrades capability rather than refusing to start. The `civiccast doctor` output is what the installer uses to choose model loadouts and what your support contact will ask for first.

## Prepare recorded video for the public portal

CivicCast turns a recorded meeting (any video file ffmpeg can read) into an
adaptive-bitrate HLS stream that any modern browser, mobile device, or smart
TV can play. The packager produces a five-variant ladder per asset:

| Variant | Resolution | Video bitrate | Profile |
| :--- | :--- | :--- | :--- |
| 1080p | 1920 × 1080 | 4.5 Mbps | high |
| 720p | 1280 × 720 | 2.5 Mbps | main |
| 480p | 854 × 480 | 1.0 Mbps | main |
| 240p | 426 × 240 | 350 kbps | baseline |
| slate | 426 × 240 | 200 kbps | baseline |

The slate is always present in the manifest. If every content variant fails
to play, the player falls back to a branded "we are experiencing technical
difficulties" loop instead of showing a broken-video icon to the public.

### Prerequisites

- **ffmpeg 4.4 or newer.** The `civiccast doctor` command checks this and
  warns if the installed version is too old. On Windows, the easiest install
  path is [Scoop](https://scoop.sh/): `scoop install ffmpeg`. On Debian or
  The installer ships ffmpeg in its media-tools pack; install it from the
  CivicCast installer rather than separately.
- A CDN. See "CDN configuration" below — BunnyCDN is the documented default.

### Encoding from Python

```python
from pathlib import Path
from civiccast.stream import pack_vod_asset

result = pack_vod_asset(
    input_path=Path("/path/to/council-2026-05-15.mp4"),
    output_dir=Path("/var/civiccast/assets/council-2026-05-15"),
)
print(f"Manifest written to {result.manifest_path}")
print(f"Renditions: {[r.config.name for r in result.renditions]}")
```

The output directory layout is the standard HLS tree:

```
output_dir/
├── playlist.m3u8                      # multivariant manifest
├── 1080p/
│   ├── playlist.m3u8                  # rendition playlist
│   ├── seg000.ts ... segNNN.ts        # 2-second segments
├── 720p/  …
├── 480p/  …
├── 240p/  …
└── slate/
    ├── playlist.m3u8
    └── seg000.ts ... seg014.ts        # 30-second slate loop
```

Upload the entire `output_dir` to your CDN. The packager writes relative
URIs in the manifests, so the same encoded output works from any HTTP
origin without re-encoding.

### CDN configuration

CivicCast ships with a Protocol-based CDN adapter so the upload step is
isolated from the encoding step.

**BunnyCDN (default — see ADR 0006).** Recommended for sub-5000-concurrent
viewer deployments. Two values to configure:

- Storage Zone API key (from the BunnyCDN dashboard → Storage → your zone → FTP & API Access)
- Pull-zone hostname (e.g., `your-station.b-cdn.net`)

```python
from civiccast.stream.cdn.bunny import BunnyCDNAdapter

cdn = BunnyCDNAdapter(
    storage_zone_name="your-station",
    access_key="<your-storage-zone-api-key>",
    cdn_hostname="your-station.b-cdn.net",
)
public_url = cdn.upload_file(
    local_path=Path("/var/civiccast/assets/council-2026-05-15/playlist.m3u8"),
    remote_key="council-2026-05-15/playlist.m3u8",
)
# Returns: https://your-station.b-cdn.net/council-2026-05-15/playlist.m3u8
```

Egress charges depend on the provider's current rates, traffic and account
terms. Check those terms for the expected meeting size and audience.

**Cloudflare R2 + CDN (DDoS-protection alternate).**

> **Status: shipped as an optional adapter.** CivicCast includes
> `civiccast.stream.cdn.cloudflare_r2.CloudflareR2Adapter`. Install the
> optional dependency with `uv sync --extra cloudflare-r2` or
> `pip install 'civiccast[cloudflare-r2]'` before using this adapter. BunnyCDN
> remains the default CDN path.

For stations facing coordinated traffic during high-stakes meetings
(contentious zoning votes, budget hearings, recall debates), Cloudflare's
network provides L7 DDoS mitigation, bandwidth protection, and rate
limiting at the standard tier that BunnyCDN's pull-zone does not.
Trade-off: more credentials to manage (R2 access key + secret + account
ID + bucket name + public hostname) and slightly higher egress.

To use R2 with CivicCast, configure a Cloudflare account id, R2 access key,
secret key, bucket name, and HTTPS public base URL. The adapter constructs the
R2 S3-compatible endpoint and returns public URLs under the supplied base URL.
Use `uv run civiccast doctor` after setting the R2 environment values so the
health check can catch missing credentials or bucket access before a meeting.

**Other CDNs.** Any object-storage + CDN combination that exposes an HTTP
PUT upload API can implement `civiccast.stream.cdn.CDNAdapter`. Fastly is
documented in the spec as a future option for premium SLA deployments.

### What residents see on the public portal

The React + HLS.js portal app ships in `civiccast/apps/portal-public/`.
For v0.4, the portal presents three resident-facing areas:

- **Live now** reads `GET /api/public/live/current` and shows the current
  on-air broadcast when a live session is active.
- **Coming up** reads `GET /api/public/schedule/coming-up` and shows
  scheduled public premieres only. Embargo entries, cancelled rows, and
  already-published rows are hidden from residents.
- **Published recordings** reads `GET /api/public/assets` and lists recordings
  that are both packaged and explicitly published to the Portal surface.
  Packaging alone does not expose metadata or HLS bytes publicly.

The page still accepts a manifest URL via the `?manifest=` query string. This
is useful for cleanroom playback tests, station previews, and direct links to
an encoded recording:

```
https://your-portal.example.com/?manifest=https://your-station.b-cdn.net/council-2026-05-15/playlist.m3u8
```

Build for production:

```bash
cd civiccast/apps/portal-public
npm install
npm run build      # emits dist/
```

The portal treats each public API section independently. If one section cannot
load, residents still see the sections that did load and a specific message
for the unavailable section. A total public API outage shows an actionable
error asking the resident to refresh and contact the station if the problem
continues.

Residents do not need an account or an operator link. During a live meeting,
send them to the public portal URL. Between meetings, the same page shows the
next scheduled public premieres and the published-recording directory.

### Embed widget

Other websites can embed a CivicCast asset by hitting the public embed API:

```
GET /api/public/embed/{asset_id}
```

The response includes a ready-to-paste iframe HTML snippet, the manifest
URL (for custom integrations), and the canonical portal page URL. No
authentication required — embeds are public.

**Multi-host deployments.** If you serve the API and the portal SPA from
different origins (the recommended production posture — e.g. API at
`api.your-station.org`, portal at `portal.your-station.org`), set
`CIVICCAST_PORTAL_BASE` to the public portal origin so embed snippets
point at the right host:

```bash
export CIVICCAST_PORTAL_BASE=https://portal.your-station.org
```

If the variable is unset, embed URLs default to the API request's own
base URL — correct for single-host deployments.

## Stream a live meeting

The operator console includes a Live Room for running a meeting broadcast from
a configured source. Production pre-flight probes the configured live source
through the application's source-probe implementation. A successful probe is
not proof of sustained ingest, every output device, or downstream headend
acceptance. The separate recorded-sample rehearsal checks that selected
recording path; it does not substitute for verifying a real live source.

For remote rooms, field kits, or partner facilities, CivicCast can also build a
live ingest plan that keeps the local encoder path as the default and adds
optional outbound relay paths. See
[Live Remote Ingest Relay Operations](ops/live-remote-ingest-relay.md) for the
project-hosted relay, integrator-hosted relay, direct platform, health-state,
and failure-handling guidance.

### Start a live broadcast

These steps require a configured source and station-specific output path. The
Beta 15's scoped fresh-install and component checks do not establish a normal
cross-version upgrade, extended soak or sustained live-operation result; see
its verification record for the evidence boundary.

1. Open the operator console and choose **Live**.
2. Confirm that at least one live source appears. If the room says no live
   sources are configured, create an RTMP, RTSP, NDI, or SRT source through the
   staff live-source API before continuing.
3. Select the source for the meeting.
4. Choose **Create live session**.
5. Choose **Start pre-flight**.
6. Choose **Run pre-flight** and review every check. Required checks cover
   network, storage, live source, recording target, and operator confirmation.
   The last three — syndication, Internet Archive, and NAS — report the
   station's **provider posture** and never block go-live, because those
   surfaces complete after the recording:

   | Pre-flight result | What it means | Set by |
   | --- | --- | --- |
   | `pass` | The provider is selected as real and resolves during pre-flight. This does not prove a later upload or archive write succeeded. | `CIVICCAST_PROVIDER_<KIND>=real` plus that provider's credentials |
   | `fail` | Real publish was selected but the client cannot be built — the message names the missing variable or unmounted path. The broadcast still proceeds; the surface fails at publish time. | a real selection with incomplete configuration |
   | `not_configured` | The shipped simulation is in use. **Nothing is written anywhere.** | the default, when `CIVICCAST_PROVIDER_<KIND>` is unset |

   The kinds are `CIVICCAST_PROVIDER_YOUTUBE`,
   `CIVICCAST_PROVIDER_INTERNET_ARCHIVE`, and `CIVICCAST_PROVIDER_LOCAL_NAS`.
   Pre-flight resolves them through the same registry the publish run uses, so
   the two cannot disagree. See [CDN and providers](ops/cdn-and-providers.md).
7. If a required check is blocked, follow the message shown in the Live Room,
   fix the source, storage, or network issue, then run pre-flight again.
8. When the Live Room shows **Pre-flight ready**, choose **Start Live Stream**.
9. Watch a separately verified resident playback surface. The stock build does not claim
   automatic source-drop detection or slate failover without station-specific
   ingest and egress proof.
10. When the meeting ends, choose **End Live Stream**. CivicCast advances the
    session toward recording finalization so the recording can enter the asset
    library at state `recorded`.

### After the broadcast

The finalized recording appears in the operator asset workflow as a recorded
asset linked to the live session that produced it. Packaging creates private
HLS media. Only a later successful Portal approval sets the publication state
that allows public metadata and `/media/vod` access.

## Run captions and caption review

The backend captions contract is now present for the 0.5 rung. CivicCast can
run the optional local faster-whisper caption runtime, represent audio chunks,
custom vocabulary hints, runtime transcript hypotheses, stabilized caption
cues, WebVTT output, HLS subtitle-track publication, and caption review queue
items. The live-caption worker seam keeps caption stabilization alive across
small audio batches, sends stable cues to the review queue, and can publish a
caption WebVTT track beside the HLS package as cues commit. Install
`civiccast[captions-runtime]` on hosts that execute local caption models;
default installs stay lightweight for portal-only or review workstations. The
operator console now includes **Review queue**, where staff
can search caption cues, filter
pending/approved/edited/rejected work, compare the machine cue with reviewed
text, approve a cue, save an edited cue, or reject a cue. If the caption
backend is unavailable, the screen tells the operator to confirm durable
storage and API health before retrying.

After review, approved caption cues can be written beside an HLS package as
segmented WebVTT files under `captions/{language}/`. CivicCast rewrites the
multivariant playlist with `EXT-X-MEDIA TYPE=SUBTITLES` so compatible HLS
players can discover the track.

When the public portal sees a captioned HLS manifest, it shows **Captions**
controls below the player. Residents can leave the default caption track on,
turn captions off, or re-enable the posted language track with keyboard or
pointer controls.

Historical Beta.8 live GPU caption stabilization required corroboration across overlapping audio windows: matching words with overlapping observed time spans in distinct audio windows. The current native live path is different: Whistle publishes its first recognition, including low-confidence text, and removes overlap already aired; it does not require two readings to agree. Whisper supplies fallback while bounded recovery probes try to restore Whistle. A committed live cue is not rewritten later. Low-confidence cues are flagged in the operator review queue
with a specific next step: compare the cue against the audio, then approve,
edit, or reject it. The staff review API preserves the original machine cue
text separately from approved or edited reviewer text so the UI keeps a clear
audit trail.

For live-tap concurrency, retained-audio sweep scheduling, overload pauses
and the loaded CUDA/compute-type diagnostic, see
[Live caption tap](ops/background-workers.md#live-caption-tap). Live captions
remain off by default. The beta.10 candidate's eight-hour lab run and its known
limits (including some caption audio dropped under heavy load) do not establish
public-installer or Gate A acceptance; consult the
[beta.10 verification record](releases/v1.0.0-beta.10-verification.md).

## Run agenda import (vendor bridge + js_portal)

`civiccast/agenda_import/` is a separate module from the docparse-based
agenda editor above (`civiccast/agenda/`): it bridges a municipality's
existing agenda system into a draft agenda instead of the operator typing
or uploading one by hand. Four vendor adapters ship: `legistar`, `primegov`,
and `civicclerk` talk to each vendor's documented, anonymous, plain-HTTP
JSON/HTML endpoint (no browser needed); `js_portal` is the fallback for a
portal that renders its content client-side with JavaScript and has no such
endpoint (CivicPlus AgendaCenter, Granicus, or a JS-hydrated Legistar public
page). Use **Agendas → Agenda source setup** as a setup administrator to save
the default provider and tenant. Settings apply on the next request and survive
restart. Environment-based configuration remains a compatibility fallback until
the first save. Import is disabled by default. Every confirmed import lands
as a draft — an import into an already-published agenda reopens it to draft
for operator review before it can go public again (AI/agenda non-negotiables
§4.2); this is never configurable to auto-publish.

`js_portal` additionally needs the portal's own URL and an optional vendor
hint per import (there is no single fixed host the way the other three
vendors have one). It uses [crawl4ai](https://github.com/unclecode/crawl4ai)
(Apache-2.0) with a headless Playwright Chromium browser to render the page,
then applies a best-effort text heuristic (numbered items, markdown
headings, confidence-scored like the PDF-import heuristic above) to extract
meetings and agenda items. The crawl is bounded: same-origin only (it will
not follow a link off the configured portal), robots.txt is fetched and
respected before any navigation, no login/auth flow is ever attempted, and
at most two pages are fetched per call (the listing page, then one meeting's
detail page).

crawl4ai + Playwright Chromium is a heavy optional dependency (Playwright's
browser binary alone is roughly 300 MB) and is **not** installed by the
native Windows installer by default. A station that wants `js_portal`
installs it explicitly:

```bash
pip install "civiccast[agenda-js-import]"
playwright install chromium
```

Until both steps are done, `GET /api/staff/agenda-sources/js-portal/posture`
reports an honest `{"installed": false, ...}` — the operator console's
external-import section polls this and disables `js_portal` discovery with
a visible "not installed" state rather than letting the operator hit a
confusing failure. That posture check only confirms the `crawl4ai` package
itself imports; if the Playwright browser binary is still missing, a crawl
attempt fails with a distinct, actionable upstream error instead (run
`playwright install chromium`).

`js_portal`'s extraction heuristic is fixture-tested (synthetic
CivicPlus/Granicus-shaped markdown, since crawl4ai's own weight keeps it out
of the default CI dependency set) but was also live-smoke-tested against a
real CivicPlus tenant during development: the crawl itself succeeds, but
that tenant's real meeting rows only render after an interactive
category-selection step this v1 does not perform, so today's extraction is
an honest empty/low-yield result on that shape of tenant rather than a
useful one. See `civiccast/agenda_import/js_portal.py`'s module docstring
for the full live-verification ledger and the disclosed follow-up work.

## Approve summaries and export signed records

The summary workflow turns supplied transcript cues into a draft for operator
review and supports PDF/A-3B record export. The console selects approved caption
cues; authorized API callers can supply cues directly. Citation checks validate
the supplied cue IDs and time ranges, not the semantic truth of every claim.
Read the export validation result: veraPDF validation and fallback local shape
checks are distinct evidence. The exported artifact includes sourced-claim, provenance,
approval, and timestamp-token sidecars. The timestamp authority is deterministic/local by default, so do not
describe the export as having legal-record status or real timestamp-authority
proof unless a real authority has been configured and verified. It is
intentionally scoped to Summary + signed records only; three-tier publish is
handled by the v0.7 workflow below.

### Review a sourced summary

1. Open the operator console and choose **Summary review**.
2. Read the summary narrative and the **Sourced claims** list.
3. For every quantitative claim, activate the cue timestamp link. The inline
   transcript player moves to that cue without moving focus away from the
   review/export action you were using. It requests private staff playback; a
   provenance-verified package uses its HLS timeline and maps the playhead back
   to the source timeline. If the cited range is outside the available media,
   the player explains that instead of seeking to a different passage.
4. If the summary has no source ranges or shows an evidence-refusal message,
   regenerate the summary after adding committed transcript evidence. Do not
   approve unsupported quantitative claims.
5. When every quantitative claim is backed by transcript timestamp evidence,
   choose **Approve summary**.

The screen includes actionable states:

- **Loading:** skeleton rows appear while the review queue loads.
- **Empty:** the screen tells you to generate a summary from committed
  transcript cues.
- **Error:** the screen tells you to retry and check API health if the backend
  is unavailable.
- **Partial/refusal:** the screen tells you to regenerate after adding
  transcript evidence for unsupported claims.
- **Success:** approved summaries enable signed-record export.

### Export a signed record

1. Approve the summary first.
2. Choose **Export signed record**.
3. Confirm the success message shows a record id and digest.
4. If export fails, follow the message shown in the operator console. The most
   common fix is to approve the sourced summary first or regenerate it with
   complete timestamp evidence.

The server does not trust client-provided approval status. Signed-record export
loads the persisted summary and approval metadata server-side, renders the
PDF/A-3B signed-record artifact, attaches timestamp proof metadata, and
persists the record export metadata and artifact bytes. Do not file the export
as having legal-record status or real timestamp-authority proof unless a real
authority has been configured and verified.

### Transcript CSV export

Operators and clerks can export committed transcript cues as CSV through the
staff summary API. The CSV includes stable columns for cue id, start/end
seconds, formatted timestamps, cue text, confidence, and the low-confidence
flag. Use this when a clerk needs a spreadsheet-friendly transcript sidecar
for review or archival intake.

### Private source and packaged-media preview

The cue player in Summary review uses a short-lived staff playback grant for
the original recording. When a completed package has verified source and trim
provenance, the player uses its private HLS copy and maps the HLS playhead back
to the original source timeline. The Trim & Chapters editor previews the
selected range from that verified package; if no current package is available,
it reports that state instead of silently previewing another timeline. Use
**Renew playback** if the staff grant expires. These previews do not publish the
asset: residents can watch only an asset that is both packaged and explicitly
published to the Portal surface.

## Publish to Portal; integrate other targets separately

Stock acceptance covers the canonical Portal surface only. Internet
Archive, local NAS, YouTube, podcast, and notification surfaces exist as source
contracts and deterministic test adapters; they are not accepted provider
integrations without real credentials and redacted live evidence.

### Publish a recording

1. Package or finalize the recording so it has a portal HLS `manifest_url`.
2. Open the operator console and choose **Publish**.
3. For stock acceptance, select only Resident Portal. Treat all other
   surfaces as blocked or not configured.
4. Choose **Approve and Publish selected**.
5. Confirm the Portal recording is public and unapproved recordings remain private.

### Required archive rule

The required publish surfaces are determined by the station configuration and
the asset's `public_record_required` state. Do not treat Internet Archive or
local NAS as a universal legal requirement for every station or asset; the
applicable obligations come from the station's records policy and jurisdiction.
For any surface marked required, check its actual result. A simulated provider
result records no upload or file copy, and an override is an operator action
recorded with the publish evidence.

### Preflight and credentials

The publish preflight API checks portal packaging, Internet Archive credential
reference, local NAS rsync credential reference, local NAS ZFS credential
reference, YouTube Live credential reference, and YouTube VOD credential
reference. The v0.7 implementation uses an OS credential-store abstraction and
deterministic mock references such as `os-keyring://civiccast/internet-archive`;
no secrets belong in the repository or in committed environment files.

The v1.2 broker hardening path emits a documented
`publish.asset.approved` platform event after a successful publish approval.
Blocked preflight approvals do not emit success events.

### Retry and degraded reach

If a surface fails, the dashboard shows **Retry this surface** on that row. A
retry affects only that platform and preserves the rest of the publish record.
YouTube failures are displayed as degraded reach, not as portal or archive
failure. Archive failures remain operator-actionable until they are fixed or
overridden.

## Backup, Restore, and Disaster Recovery

Use **Station backup and restore** for encrypted station archives, manual or
scheduled backups, retained-copy limits and progress. Archives include the
configured database, station files, portable credentials and selected media.
Restore with `civiccast backup restore` while station writers are stopped;
the [station backup guide](ops/station-backup.md) explains passphrases, archive
verification, replacement paths and restoration. Keep an off-station copy.
Installer rollback is temporary protection during setup, not this backup.

CivicCast backup planning separates the working media store from the archival
record. Keep database backups, media originals, HLS outputs, local NAS archive
copies, signed-record exports, and release evidence in separate retention
sets. The Setup backup status is a live storage check: `ready` means CivicCast
wrote a temporary probe file to the selected destination, read it back and
verified its hash, then removed it. A saved destination by itself is not proof
that the directory is currently writable. This check is separate from database
storage readiness and does not create or verify an actual backup. For the
station's own recovery plan, identify:

1. `CIVICCAST_NAS_ARCHIVE_PATH` points to a mounted archive directory.
2. The backup-storage probe can write, read/checksum and remove its temporary
   file in the selected destination. This is a storage round-trip check, not a
   backup job or station restore.
3. A database drill has separately restored a dump into a throwaway database;
   that does not restore the live station or its media/configuration/provider
   state.
4. Keep recovery instructions and any station-specific restore evidence with
   the records needed by your own IT process.

Do not describe either probe as proof that a full station restore has worked.
No universal release gate is defined by these operational checks.

## Manage subscribers and podcast feeds

The v0.8 workflow adds audience reach after a recording is ready to publish:
residents can subscribe, operators can generate podcast episodes, and publish
approval can prepare subscriber notification proofs. These surfaces are
independent from the v0.7 archive rule; a notification or podcast failure must
be visible and retryable, but it must not rewrite the portal, Internet Archive,
or NAS proof history.

### What residents see

The public portal includes a **Follow new recordings** area. Residents can:

1. Enter an email address and request a confirmation email.
2. Use the signed confirmation link to complete double opt-in.
3. Use the signed unsubscribe link to stop notices.
4. Copy a public meeting-body RSS feed.
5. Copy the public podcast RSS feed.

The portal handles loading, success, empty, error, partial, invalid-token,
already-confirmed, and unsubscribed states with copy that tells the resident
what happened and what to do next. Email delivery uses a deterministic local
mailbox adapter in CI and development; no real external email credentials are
required for the v0.8 proof.

### Subscriber privacy

Subscriber email handles and webhook secrets are encrypted before persistence.
Webhook payloads are signed with HMAC headers so receivers can verify the
message body and reject tampered payloads. CivicCast does not add third-party
trackers or remote-image tracking pixels to subscriber notices in v0.8.

ActivityPub is a v1.2 opt-in station-actor surface, not a default beta
publication path. Fresh installs keep `CIVICCAST_ACTIVITYPUB_MODE=disabled`.
Technical operators can enable federation with a generated station key, an
explicit public base URL, and one of three modes:

- `open` accepts signed follows except from blocked domains.
- `limited` accepts signed follows only from allowed domains.
- `approval-only` queues signed follows until staff approve them.

Run `civiccast activitypub keygen --private-key-path <path> --base-url
https://station.example.gov --handle <station> --mode approval-only` to create
the station key and print the non-secret environment settings. The enabled
path verifies inbound HTTP Signatures, signs outbound `Accept` and publish
`Create` deliveries, accepts signed `Undo Follow`, supports staff `Reject`,
persists follower/outbox/delivery state, and can require authorized fetch. The
operator console includes a **Federation** screen that shows mode, actor URL,
allow/block policy, follower counts, pending moderation, accepted/blocked/
rejected/removed follower tabs, outbox records, and signed delivery attempts.
See
[ActivityPub Federation Ops](ops/activitypub-federation.md). Email, RSS,
webhook, and podcast delivery remain the non-federated resident-notice paths.

### Managing other CivicCast stations (Sprint 6 source)

**Other stations** browses trusted peers' published programme catalogs and shows
their configured channels, current programme and latest health timestamp.
Setup admins and meeting operators can request channel start/stop; read-only
staff cannot. Requests are queued on the remote station, and operators refresh
status to confirm execution. Administrators provision trusted public/staff HTTPS
origins and credential references as described in
[Station federation](ops/station-federation.md). This is separate from ActivityPub
and TownLight suite integration (owner-deferred to 2.0). It shipped in the
published Beta 20 package and was not in Beta 19.

### Podcast episodes

Operators can generate a podcast episode from a published recording's local
media file. CivicCast extracts and normalizes audio into an MP3, serves the
enclosure and updates the channel RSS feed with its URL and actual byte length.
The station needs FFmpeg and a configured public URL. Missing source media or
conversion failures appear as retryable publication failures.

### Publish workflow behavior

The publish dashboard now has independent audience surfaces for podcast and
subscriber notifications. When approved, the podcast surface generates an
episode and the subscriber-notification surface delivers notices through the
configured providers, recording outcomes and retrying eligible failures.
Mock providers report simulated delivery. These rows can degrade independently,
just like YouTube reach. The
public-record archive requirement still depends on the resident portal,
Internet Archive, Local NAS rsync, and Local NAS ZFS surfaces only.

### Nightly publish + soak proof

For v1.0 readiness, CivicCast includes a dedicated `nightly-publish-soak`
workflow. The gate runs the deterministic Public Meetings publish path across
the canonical portal, Internet Archive, both local NAS archive modes, YouTube
Live, YouTube VOD, podcast, and subscriber notifications. It also writes a JSON
evidence artifact at `artifacts/v1.0-publish-soak-result.json` so operators can
see exactly which surfaces were exercised and which hashes or URLs were
produced.

Run the same proof locally with:

```bash
python scripts/run_publish_soak.py --iterations 24 --output artifacts/v1.0-publish-soak-result.json
```

The command exits non-zero if a required surface fails, archive hashes are
missing, or the dashboard does not reach `archive_verified`.

## AI benchmark baseline

For v1.0 readiness, CivicCast includes a deterministic AI benchmark baseline
for the local caption, translation, and summary contracts. The baseline records
caption WER, translation BLEU plus adequacy, and summary ROUGE-L plus
factual-correctness scores. The tracked corpus and result JSON live under
`docs/releases/evidence/`.

Run the same proof locally with:

```bash
python scripts/run_ai_benchmarks.py
```

The command exits non-zero if any score misses its v1.0 release floor.

## Use translation and accessibility checks

The v0.9 workflow adds translated caption tracks and raises the accessibility
release gate. The local release proof uses a deterministic Spanish translation
adapter with the same contract shape as the production TranslateGemma 4B via
Ollama runtime. CI does not need a live Ollama model to prove the data path.

### Translated caption tracks

The caption worker can publish the original English WebVTT track and a Spanish
WebVTT track into the same HLS package. The resident player exposes both tracks
as keyboard-operable caption buttons. English remains the default track;
Spanish is selectable and autoselectable.

The translation contract preserves protected glossary placeholders such as
`??0001??`. If a backend drops or mutates one of those placeholders, the
translation fails closed instead of publishing a misleading caption. This lets
operators protect names, case numbers, agenda identifiers, and other terms that
must survive translation exactly.

### Translation latency proof

v0.9 records per-cue translation latency and checks the 95th percentile against
the 800ms live-caption budget. The deterministic local adapter is fast enough
for CI; a live deployment using Ollama must run the same proof before claiming
real-model readiness.

### Accessibility release gate

Public and operator Playwright checks now scan WCAG-tagged axe rules with a
zero-violation gate for the covered pages. The v0.9 evidence also covers
desktop and mobile rendering, keyboard caption-track toggling, focus behavior,
browser console cleanliness, and actionable resident copy.

Windows screen-reader verification uses NVDA. The Windows installation package
path installs NVDA from the `NVAccess.NVDA` winget package via
`scripts/install-windows-accessibility-tools.ps1`. NVDA on Windows is the
whole screen-reader verification surface for this product; the VoiceOver pass
this section used to describe belonged to the retired macOS build.

### 21st CVAA / Section 508 captioning posture

CivicCast treats captions as a public-access requirement, not decoration:

1. Captions must be available as WebVTT tracks in the resident player.
2. Caption controls must be keyboard-operable and have accessible names.
3. English captions remain available when translated captions are added.
4. Translation must not hide the original caption text from operator review.
5. Error states must tell staff what failed and what action to take next.
6. Release evidence must include browser accessibility checks and human NVDA /
   VoiceOver screen-reader notes before a tag is published.

This v0.9 posture supports 21st CVAA and Section 508 captioning expectations
for the local product proof. Final legal compliance for a specific station
still depends on that operator's jurisdiction, content rules, and deployment
policy.

## Run installer and first-run proof

The installer workflow targets a cold operator reaching first useful recorded-
media publication. It is not proven for a given release until that exact installer passes
the clean-machine acceptance run.

### Public Meetings first-run plan

Run the first-run plan:

```bash
uv run civiccast installer plan --profile public-meetings
```

The plan covers:

1. Deployment profile selection.
2. Hardware probe and tier recommendation.
3. Storage configuration for media and a separate local NAS archive target.
4. First operator account creation.
5. Profile-aware publish target setup.
6. Model download with hash verification.
7. First-run health check and recorded-media Portal playback confirmation.

Cloud fallback remains off by default. Operators must explicitly opt in before
using a cloud provider.

### Station readiness and health reports

`civiccast installer health-check --profile public-meetings` reports
station-specific readiness and configured target status. Read the current
command output and address the conditions relevant to the station. This report
does not define universal release-acceptance criteria. A configured provider,
credential reference, or destination path alone does not prove a real external
write. Database storage readiness requires the managed database and required
media storage to be prepared; it is not satisfied by a configured backup
destination. Backup `ready` means only that the current temporary write/read/hash
probe succeeded; it does not restore the station database, media, or configuration.

### Monitoring the running service (`/health`)

`GET /health` is public and needs no authentication. It answers two different
questions, and monitoring setups should not confuse them. This illustrative
response uses a release version string for illustration; it is not evidence of
package installation or health. Read the
version from the station you are monitoring.

```bash
curl -s http://127.0.0.1:8000/health
{"status":"degraded","version":"1.0.0-rc.5","schema":"not-configured"}
```

**The HTTP status code is liveness.** `/health` returns `200` whenever the
process is answering, in every state — including a station that has been
installed but has not had its storage prepared yet. Point restart-on-failure
supervisors and container health checks at the status code.

**The `status` field is readiness.** It reads `healthy` only when the database
schema matches the running code and live-caption readiness is neither
`degraded` nor `unknown`. The schema contribution is:

| `schema` | `status` | What it means |
| --- | --- | --- |
| `current` | `healthy` unless captions are degraded or unknown | The database matches the program; this does not certify every station output. |
| `not-configured` | `degraded` | No database yet — run **Prepare storage** in the console, or set `DATABASE_URL`. |
| `behind` | `degraded` | The code is newer than the database. Run `alembic upgrade head`. |
| `unknown` | `degraded` | CivicCast could not read the schema version — usually the database is unreachable. |

`live_captions` is `disabled`, `idle`, `unknown`, `degraded` or `healthy`.
Disabled captions and no active channel do not themselves degrade readiness.
The public field is coarse; signed-in System Health shows affected channels,
worker activity and speech-engine fallback. Fresh processing during silence
differs from stalled recognition.

The response always carries `schema_db_revision` and `schema_expected_head` (not just when `behind`) — `"none"`/`"unknown"` when either could not be read. When `schema` is `current` the two values are, by definition, equal; a caller proving a post-upgrade migration actually landed (rather than trusting the `current` label alone) can compare them directly.

**Alert on the `status` field, not on the status code.** A station with no
database returns `200` and cannot serve a single recording; on the retired
line it
also reported `"healthy"`, which told an uptime monitor the opposite of the
truth. CivicCast never auto-migrates — `behind` is a state an operator resolves
deliberately.

Readiness is re-checked when storage is prepared, so a station flips from
`degraded` to `healthy` without a restart.

### Offline model bundle

For air-gapped installs, generate the bundle manifest:

```bash
uv run civiccast model download --offline-bundle --bundle-dir /path/to/model-bundle --profile public-meetings
```

The manifest lists the caption, summary, and translation model artifacts and
their SHA-256 hashes from the actual files in `--bundle-dir`. CivicCast refuses
to emit an offline manifest when the bundle directory is missing required model
artifacts, because placeholder hashes are not valid air-gapped proof. The
installer must verify hashes before activation.

The air-gap proof this section used to cite paired the model bundle with a
Python wheelhouse and ran the install inside WSL2 on the retired line, so it
does not describe how this product is proven. A native equivalent -- install
on an isolated Windows target with the default route removed, then verify the
model bundle hashes from inside that environment -- has not been run and
recorded here yet. Treat air-gapped install as unproven for the native
station until it has been.

### Beta handoff report

Technical staff may run this command to collect the status and evidence inputs
available for a beta handoff:

```bash
uv run civiccast installer beta-handoff --json
```

The output is an evidence summary. It is not a release gate, a clean-install
result by itself, or proof that a station's external providers work. Consult
the release's verification record for package-specific checks and limitations.

### Idle page and emergency overlay

The resident portal has a between-streams idle page and a channel-specific
emergency overlay. The idle page points residents to published recordings when
no meeting is live. The page polls `/api/public/cg/emergency-overlay?channel_id=...`
for actual warning text and clears it when no active display remains; it does not
require the old `?emergency=1` preview flag. The notice uses an assertive live region.

Broadcast presentation is opt-in through `CIVICCAST_EAS` with GStreamer. The
channel must start with its graphics compositor reserved before a display is
accepted. Emergency updates replace only their own compositor layer; clearing
retains other graphics. Full-frame forced slates require explicit confirmation.
See the manual's Emergency Alerts section for configuration and operational limits.

## Troubleshooting

Start with `civiccast installer beta-handoff --json` for beta issues and
`civiccast installer health-check --profile public-meetings` for first-run
publish issues.

| Symptom | Meaning | Operator action |
| --- | --- | --- |
| `/health` reports `"status":"degraded"` | Database readiness or caption processing needs attention. Check both `schema` and the coarse `live_captions` field; the public probe does not expose channel details. | For schema `not-configured`, run **Prepare storage**; for `behind`, prepare the current schema; for `unknown`, check database reachability. For caption `degraded` or `unknown`, inspect authenticated System Health for the affected enabled on-air channel, worker activity and fallback state. Fresh processing during silence is distinct from a stalled worker. |
| Clean Windows proof is blocked | The host did not provide an isolated target, or no install was exercised on it. | Rerun the proof on a fresh Hyper-V, Windows Sandbox, or VirtualBox Windows target. |
| External provider lane is credential-gated | A secret may be missing or live proof has not been recorded. | Use approved credentials only, run the controlled provider proof, and store redacted evidence. |
| Model lane is blocked | Required model hashes are unavailable. | Download or import the approved model bundle and verify hashes before captions or summaries. |
| NDI lane needs hardware | FFmpeg, the NDI runtime, SDK inputs, sender helper, or receiver proof is missing. | Install operator-approved NDI components or leave the cable/NDI proof lane gated. |

## License

CivicCast is open-source software. Code is licensed under [Apache License 2.0](../LICENSE-CODE); documentation (including this reference) is licensed under [Creative Commons Attribution 4.0 International](../LICENSE-DOCS).

You are free to fork, modify, redistribute, and adapt CivicCast and its documentation. Attribution is required (this reference itself); no warranty is provided (see the license texts).

## Reporting bugs and security issues

- **Bugs and feature requests:** Open an issue at <https://github.com/scottconverse/civiccast/issues>.
- **Security vulnerabilities:** Do not file public issues. See [SECURITY.md](../SECURITY.md) for the private disclosure process.

## Source of truth

This reference is maintained in `docs/technical-ops-reference.md`. The user-facing PDF and DOCX are generated from `docs/USER-MANUAL.md`.

## Published package evidence and historical release identity

This reference follows the published `CivicCast 1.0.0-beta.20`. The
[Beta 20 verification record](releases/v1.0.0-beta.20-verification.md)
identifies the signed package and its scoped checks. The [Beta 19 verification record](releases/v1.0.0-beta.19-verification.md)
remains historical. Earlier results remain tied to their own release records.
Consult `docs/releases/release-truth.yaml` for the authored release state; this
reference does not claim an extended soak or general service-uptime result.

The `v1.0.0-rcNN` line this document used to describe is the retired WSL2/Linux
deployment, published from the archived repository. Its release and
verification artifacts are not in this repository and its rc numbering does
not continue here.


## RC2 operator corrections

Use the source-matched manual and built-in Help for second-level scheduling, repeat
controls, three-channel setup and download recovery. Per-channel repeat settings
replace environment editing for ordinary operators. Preparation status is transient;
as-run output observations are not physical receiver measurements. Historical rows
are not rewritten. Whistle uses one second of overlap with each five seconds of new
input by default; overload/fallback limits still apply. Authentication validation
responses omit submitted inputs and custom validator context.
