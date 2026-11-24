# CivicCast 2.0 Complete Specification

Status: release specification for the CivicCast 2.0 software parity line
Historical release line: `2.0.1` (pre-reset; the current release is v1.0.0-rc.8)
Primary source specs:

- `docs/spec/spec.md`
- `docs/spec/2.0/civiccast-2.0-industry-standard-parity-addendum.md`
- `docs/spec/2.0/release-plan-v1.8-to-v2.0-industry-standard-parity.md`
- `docs/spec/2.0/parity-evidence-matrix.json`
- `docs/releases/v2.0.0-verification.md`

## 1. Product Definition

CivicCast 2.0 is a self-hostable, open-source civic broadcast platform for
public meetings, community media, schools, houses of worship, nonprofits,
PEG access stations, and CivicSuite-integrated municipalities. It covers the
public-interest video lifecycle from scheduling and ingest through live
streaming, VOD, captions, translation, summaries, review, publication,
archival, syndication, subscriber updates, reporting, and proof.

CivicCast 2.0 is not a hosted SaaS product, proprietary appliance, legal
certification product, managed app-store publication service, or live hardware
certification program. It is Apache-2.0 code and CC-BY-4.0 documentation that
stations or certified integrators can run, inspect, modify, and operate.

The 2.0 release claim is software parity with an industry-standard community
media platform, with explicit boundaries wherever proof depends on external
accounts, stores, devices, credentials, production traffic, or station hardware.

## 2. Deployment Profiles

CivicCast is one codebase with deployment profiles, not separate products.

Public Meetings: for municipalities, boards, commissions, school boards, and
CivicSuite deployments. Defaults include live meetings, captions, agendas,
summary review, public portal VOD, Internet Archive, local NAS archive, YouTube
syndication, signed transcripts, retention rules, and subscriber notifications.

Community Media: for PEG stations, community media nonprofits, school AV
programs, and streaming-first public access groups. Defaults include live
channels, VOD library, asset ingest, captions, optional translation, YouTube,
Facebook or PeerTube syndication, podcast feeds, subscriber notifications, and
optional public-record rules per channel.

Worship and Nonprofit Streaming: for organizations that need reliable live
streaming, VOD, captions, podcasting, and subscriptions without public-record
features enabled by default.

PEG Cable: starts from Public Meetings or Community Media and adds the future
`civiccast-cable` path for SDI output, 24/7 cable programming, 608/708 caption
insertion, loudness compliance, and franchise-cable obligations.

CivicSuite Integrated: uses CivicCore identity, RBAC, audit, and event
infrastructure and connects to CivicClerk, CivicRecords, and related municipal
workflows through published contracts.

## 3. Non-Negotiables

Operator approval before publish: AI captions, translations, summaries, chapter
markers, and public-facing generated content must pass through review. No
AI-generated public content auto-publishes.

Local-first AI: captions, translation, and summaries run locally by default.
Cloud providers are opt-in, explicit, and never silently used as a fallback.

Refusal on uncertainty: summaries must refuse unsupported claims instead of
guessing, especially for vote counts, motions, dollar amounts, and outcomes.

Sourced claims: every summary claim links to transcript timestamp evidence, and
that link survives operator edits in the audit trail.

Three-tier public-record publish: public-record meetings must publish to the
station portal, the Internet Archive, and local station archive storage unless
an authorized operator records a specific audit-logged override.

Phone-first operation: primary operator workflows must be usable from a small
phone with one thumb. Desktop is supported, but mobile is not an afterthought.

Plain errors: every user-facing error names what failed, what file or operation
was affected, and what the operator can do next.

No prohibited surveillance or manipulation: CivicCast must not implement voice
cloning, sentiment scoring of named individuals, biometric identification,
predictive resident scoring, covert recording, AI training retention outside
operator policy, or sale or sharing of subscriber data.

## 4. Core Architecture

Backend: Python 3.12+, FastAPI, Uvicorn, httpx, SSE for long operations,
SQLite for standalone beta/operator deployments, PostgreSQL 17 plus pgvector for
advanced standalone and CivicSuite mode, Redis, Celery, Celery Beat, NATS
JetStream where low-latency broadcast coordination is required, faster-whisper
for ASR, Ollama for local LLMs, and Sigstore-oriented release proof.

Frontend: TypeScript applications for the operator portal, public portal, and
installer, using shared contracts, generated API artifacts, accessible UI
patterns, and responsive layouts.

Installer: a Windows Tauri installer app that guides setup in plain language,
checks local machine readiness, explains blockers, installs the Windows helper
environment needed by CivicCast, and blocks WSL1 or missing WSL2 instead of
letting users reach a broken runtime.

Mode A standalone: ships its own slim platform layer for identity, audit,
manifests, providers, and local operation.

Mode B CivicSuite: uses CivicCore and CivicSuite services through published
interfaces rather than CivicCast-specific forks.

## 5. Default AI Runtime

Captions: `whisper-large-v3` with faster-whisper and CTranslate2.

Translation: `translategemma:4b` through Ollama.

Summaries: `gemma4:e4b` through Ollama.

Alternates may be registered, but the default stack must remain suitable for
commercial municipal use and must not require per-minute vendor fees.

Cloud fallback order, when explicitly configured by an operator, is local
default, local alternate, Anthropic, OpenAI, Google, then AWS. Cloud is never
preselected and never automatic on local failure.

## 6. Operator Workflows

First setup: the installer checks whether the Windows machine can run the
required local tools, tells the user what is ready, explains what is blocked in
plain English, and offers the next action only when that action makes sense.
On Windows, the app requires WSL2 with Ubuntu because CivicCast's local meeting
tools need the newer Windows Linux environment with full virtualization support.
WSL1 is blocked because it cannot run CivicCast's Linux containers and local
runtime services reliably.

First admin: the station creates an administrative account, stores recovery
material, and verifies that support bundle generation works before production
use.

Asset ingest: operators upload media, the system validates media with ffprobe
or equivalent checks, errors identify the rejected file and reason, and passing
assets enter the review and packaging flow.

Live event operation: operators preflight sources, start and stop live streams,
monitor source health, switch sources, observe fallback slate behavior, monitor
captions, and see syndication target health.

Trim and chapters: operators set in/out points, create chapter markers, review
timecodes, and preserve non-destructive metadata unless a future destructive
operation is explicitly confirmed.

Caption review: operators review low-confidence captions, edit cues, approve
or reject changes, and publish only after human review.

Summary review: operators inspect sourced claims, seek to transcript evidence,
edit summaries, handle refusals, and approve only after confirming the source.

Publish dashboard: operators see canonical portal status, archive status,
syndication reach, retry actions, and plain-language states such as ready for
review, approved and publishing, public with archive pending, degraded, archive
complete, complete, and needs operator action.

Support bundle: operators can generate a support package with logs, app state,
machine facts, and release identity information without exposing secrets.

## 7. Public And Contributor Workflows

Public portal: residents can view live and VOD content, use captions, navigate
chapters, subscribe to updates, and access published public records through the
station-controlled canonical portal.

Contributor portal: community producers can submit media without operator
credentials, attach metadata, accept submission terms, receive status updates,
and remain below the operator privilege tier.

Operator contributor review: operators accept, decline with reason, run media
validation, edit metadata, schedule accepted content, and retain final control
over what airs or publishes.

Viewer accounts: where gated non-public-record content is enabled, viewer
accounts are separate from operators and contributors.

## 8. Industry Parity Scope

Native OTT and mobile app suite: shared app contracts and reference shells cover
web/PWA, Roku, tvOS, Fire TV, Android TV, Android mobile, and iOS/iPadOS.
Store publication, platform developer accounts, signed production binaries, and
device certification remain external proof boundaries.

Gated and private video access: public, authenticated, and invite-only playback
policies exist for non-public-record content. Public-record meeting assets and
completed public archives cannot be gated by mistake.

VOD preroll messaging: channels and assets can define preroll metadata and
playback policy enforcement. Prerolls affect playback and reporting, not
Internet Archive uploads, local archives, or signed transcript exports.

Full multi-zone CG bulletin board: multi-zone templates, queueing, feed
adapters, snapshots, operator editing, HLS render planning, schedule zones,
tickers, sponsor/logo zones, and community bulletin approval are part of the
software parity set.

AV router control: router inventory, manual take planning, scheduled take
planning, TCP/UDP and serial command previews, and mobile-friendly operator
controls are implemented. Live validation requires attached station hardware.

Caption appliance integration: external SRT, WebVTT, and decoded caption
payloads can enter the existing caption review queue. Live appliance validation
requires a real appliance or partner-provided feed.

Squeezebacks and L-bar overlays: overlay z-order and operator planning exist for
L-bars, squeezebacks, bugs, lower thirds, and emergency overlays. Live GPU/HLS
execution proof requires runtime video validation.

RTMP cloud ingest relay: relay configuration, ingest plans, deployment guidance,
direct-to-syndication planning, and operator visibility are implemented.
Provisioned hosted endpoints and credentials are external.

Expanded audience measurement and reporting: CivicCast provides aggregate,
privacy-safe reporting, EPG export, optional GA4 export, and operator reports.
Production traffic and provider-side analytics properties are external proof.

Contributor submission portal: public submission/status surfaces, operator
review controls, status outbox, and contributor contracts are implemented.

## 9. Privacy And Reporting

Default analytics are aggregate only. CivicCast must not require per-viewer
session tracking, per-IP tracking, cross-session identity, advertising profiles,
or sale of subscriber information.

Analytics may include per-asset view counts over time, live concurrent viewer
trends, country/state-level geography, device/platform breakdown, caption and
audio track usage, subscription growth, podcast downloads, optional GA4 export,
and EPG export.

Public documentation must explain what data is collected, why it is collected,
where it is stored, and how stations can operate within their legal and policy
obligations.

## 10. Release And Proof Requirements

Every 2.0 release must keep runtime version, OpenAPI schema, README, changelog,
installer metadata, release documentation, Python package metadata, and Tauri
metadata aligned.

Required local gates include full pytest, mypy, ruff, ruff format check,
OpenAPI artifact check, policy checks, frontend production builds, installer
build, portal accessibility tests, installer tests, release package smoke, and
cleanroom gates where applicable.

The v2.0.0 proof baseline included 1347-plus pytest coverage, mypy, ruff,
portal accessibility, cleanroom HLS tests, real Postgres schedule contract,
local Docker cleanroom, clean Windows venv install, WSL2 fresh-user offline
wheelhouse install, release artifacts, and a VirtualBox Windows installer
execution proof.

The v2.0.1 line adds clearer Windows helper setup language, stricter WSL2/Ubuntu
checks, and plain-English blocking messages for Windows machines that cannot run
the required helper environment.

## 11. Windows Installer Requirements

The Windows installer must:

- show plain-language machine readiness;
- explain that the Windows helper lets CivicCast run its local meeting tools on
  Windows;
- require WSL2 Ubuntu rather than WSL1;
- block WSL1 with user-facing explanation;
- keep diagnostic logs allowed to mention WSL2 and Ubuntu, while keeping primary
  user copy plain;
- avoid showing setup actions that cannot work on the current platform;
- keep install/update/uninstall identity aligned with the release version;
- produce proof kits and tester packages for clean Windows verification.

The installer must tell the user why something is blocked and what to do next.
It must not assume the user knows what Linux, Ubuntu, WSL, containers, or local
runtime services are.

## 12. Clean Windows Proof

Clean Windows proof means the installer is exercised on a Windows target that
did not already have CivicCast state, stale installed binaries, stale installer
processes, old shared artifacts, or a prior CivicCast runtime.

VirtualBox proof can establish installer launch, installation, UI launch, WSL
approval/reboot paths, and blocker messaging. Installer-to-dashboard runtime
proof needs a clean Windows target where WSL2 Ubuntu can actually start. If a
VM cannot expose the virtualization layer WSL2 needs, the correct result is an
actionable blocker, not a hidden failure or overclaim.

Proof evidence should record artifact hashes, VM identity, OS version,
pre-existing path checks, install result, installed binary version, launch
state, WSL status, screenshots where useful, and final boundary statements.

## 13. Documentation Requirements

The 2.0 documentation set must serve station admins, operators, viewers,
contributors, and integrators separately.

Station admin docs cover setup, identity, storage, retention, channel settings,
credentials, privacy, upgrades, release trust, and support.

Operator docs cover daily use: scheduling, ingest, live operation, caption
review, summary review, publishing, troubleshooting, and support bundles.

Viewer docs cover public portal use, subscriptions, captions, VOD, private
access where enabled, and privacy expectations.

Contributor docs cover account creation, submission, terms, status, acceptance,
declines, and operator control.

Integrator docs cover installation, release artifacts, clean Windows proof,
configuration, optional services, external proof boundaries, hardware
integration, and supported claim language.

All public docs must avoid claiming app-store publication, hardware
certification, legal certification, managed-service operation, external
accessibility certification, production analytics proof, or live device proof
unless a separate evidence file proves that claim.

## 14. Claim Boundary

CivicCast 2.0 may claim implemented and locally proven software capability.

CivicCast 2.0 must not claim:

- app-store publication;
- signed production app binaries for all stores;
- platform store approval;
- live router hardware validation;
- live caption appliance validation;
- live GPU/HLS overlay execution on station hardware;
- provisioned managed RTMP relay operation;
- legal compliance certification;
- external accessibility certification;
- production analytics traffic proof;
- managed-service availability.

Those items can become claims only when separate evidence exists.

## 15. Acceptance Criteria

CivicCast 2.0 is acceptable only when:

- the ten parity gaps are complete or explicitly marked complete with external
  dependency;
- release artifacts are generated and hashed;
- GitHub release assets are present for the release line;
- CI and local release gates pass;
- cleanroom proof is recorded;
- Windows installer proof is recorded with honest boundaries;
- docs and version identity align;
- public copy respects the claim boundary;
- operators receive natural-language explanations for blocked setup states;
- no stale workspace, synced-folder workspace, or poisoned local state is used
  as a release root or clean proof source.

## 16. Current 2.0.1 Release State

The current release line is `v2.0.1`. It is a post-2.0.0 clarification and
installer-hardening release. It keeps the 2.0 software parity scope and updates
the Windows setup path so users see natural-language explanations of the
Windows helper, blocked WSL1 states, missing WSL2 requirements, and next steps.

The remaining local work after publication is clean Windows tester execution on
a truly clean target and, where possible, a WSL2-capable clean Windows target
for installer-to-dashboard runtime proof.
