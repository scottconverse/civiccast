# S17 — Remote Contribution (VDO.Ninja)

> **Status: Built optional remote-contribution tier for v3.0.0-beta1.** Authored 2026-06-13,
> ground-checked against `main @ 69cc676`. This section defines the **remote-contribution tier** —
> an **optional feature module** that lets remote participants (remote council members, remote
> presenters, public comment from home) join a CivicCast channel over the browser via WebRTC, with
> the guest feed ingested as a **live source** into the compositor and then out through the
> GStreamer playout engine (S15). It is **net-new**: CivicCast has **no remote-guest / WebRTC
> capability today** (the existing `contribute/` module is file-upload-only — see §2).
>
> **Scott-locked decision (2026-06-13):** the remote-guests path is **VDO.Ninja**, run as an
> **unmodified, self-hosted, separate AGPL-3.0 process** plus a **coturn TURN server**, driven from
> the CivicCast portal via its **IFRAME API**, and ingested as a **browser-source** into the
> compositor (S16/OBS or the GStreamer compositor) → CivicCast's existing egress (S15). **MediaMTX
> (MIT)** is the optional SFU/relay scaling option. The installer (S3) turnkey-installs VDO.Ninja +
> coturn for this tier — no BYO.

---

## 1. Goal & PEG automation rationale

**Goal.** Give a PEG/civic station a turnkey way to bring **remote humans onto the channel** without
a studio truck, a Zoom-to-SDI hack, or a paid cloud meeting bridge: a remote council member dials in
from home, a remote presenter shares a deck, a resident gives public comment from their living room —
each as a clean WebRTC feed that the operator drops into the live composition like any other source,
recorded and aired through the same engine that drives the rest of the channel.

**PEG automation rationale.** the incumbent PEG platform's own "remote contribution" answer is **incumbent cloud live contribution /
LIVE Multi** plus the operator wiring a third-party conferencing tool into the encoder — there is no
free, self-hosted, open remote-guest contribution path in a PEG incumbent PEG platform license; it leans on cloud
cloud-metered live contribution and on the operator's own meeting software at the input. CivicCast's wedge is
the same one that runs through the whole 3.0 thesis (master §2.3): **eliminate the recurring cloud
bill.** VDO.Ninja is free and self-hosted; coturn is free; the only recurring cost is the station's
own bandwidth. This is **additive, opt-in** (master §0 / §1): a school board or HOA that never needs
remote guests never installs this tier. It is explicitly the **remote-contribution tier** — one of
the optional feature modules layered on the core, not a coverage item that gates the V1 PEG triad.

**Honest scope boundary (stated up front).** VDO.Ninja has **no direct SDI or SRT output** — it is an
**ingest/contribution** technology only. It produces a browser-renderable WebRTC stream and nothing
else. To reach a CivicCast channel it **requires a compositor in the middle** (S16/OBS or the
GStreamer `compositor`, S15 §5) to turn the browser source into a clean video frame, after which
CivicCast's existing egress (UDP-TS / SRT / HLS / NDI / SDI, S15 §4) carries it. S17 owns the
**ingest + room/invite/session orchestration**; it does **not** own compositing or egress.

## 2. Current state (file:line — what exists vs net-new)

**Be honest: nearly all of S17 is net-new.** A repo-wide search for `webrtc|vdo|coturn|TURN|whip|whep`
matches **only `node_modules`** — there is **no remote-guest, WebRTC, TURN, or browser-source code in
CivicCast core today.** What exists is adjacent and reusable, not the feature:

- **`civiccast/contribute/` (938 LOC) is NOT this — and the name collision must not mislead.** It is a
  **file-upload contributor portal**: external producers upload a finished media *file*
  (`SubmissionMediaReference`, `civiccast/contribute/models.py:95-107`), accept terms-of-submission
  (`SubmissionAgreementAcceptance`, `models.py:51-60`), and an operator reviews / schedules / publishes
  it (`ContributorReviewRequest`, `models.py:243-261`; `review_contributor_submission`,
  `civiccast/contribute/router.py:178-203`). It is asynchronous, asset-based, has **no real-time / live
  / WebRTC path**, and its `ContributorAccountTier = Literal["viewer","contributor","operator"]`
  (`models.py:17`) is a *contributor-portal-local* tier string, **distinct from the five real auth
  roles** (§4). S17 is the **live/real-time** sibling of this asset path; reuse its terms-acceptance and
  notification patterns conceptually, but it is a separate module (`civiccast/live/contribution/`).
- **`civiccast/live/` is the ingest path the guest feed lands in (reuse).** `LiveSource`
  (`civiccast/live/models.py:212-239`) is the operator-managed live input descriptor, with
  `LiveSourceTypeValue = Literal["rtmp","rtsp","ndi","srt"]` (`models.py:79`) and a DB CHECK constraint
  pinning those four kinds (`models.py:222-227`). **Net-new:** S17 adds a `"webrtc"` source kind (or,
  preferred, registers the composited guest output as an existing `ndi`/`srt` source — see §6) so the
  guest feed becomes a first-class live source the existing `LiveSession` lifecycle
  (`LiveSession`, `models.py:175-210`; `go_on_air`, `civiccast/live/router.py:390`) and recording
  (`RecordingTarget`, `models.py:242-263`) already handle.
- **`civiccast/egress/` is the engine the composited guest feed exits through (reuse, S15).** No change
  to egress beyond treating the composited contribution as a source — the persistent GStreamer pipeline
  (S15 §3) hot-swaps the contribution source in like any other (`interpipesrc listen-to`, S15 §3).
- **Auth (`civiccast/auth/roles.py:14-20`) — reuse, no new roles.** The five real roles are present and
  `require_any_role` gates every staff endpoint; S17 adds none.

**Net-new for S17:** the self-hosted VDO.Ninja process + coturn (deployed by S3), the IFRAME-API
control bridge in the portal, the `ContributionRoom` / `RemoteGuestSession` / `GuestInvite` entities
and their persistence (migration `0048`, §3), the staff room/invite/guest API (§4), the operator
"Remote Contribution" console (§5), the browser-source → compositor → live-source bridge (§6), and the
process supervision of the VDO/coturn co-processes (handed to S9, §10).

## 3. Entities / data model & migrations

**Net-new entities** (names are canonical for the spec set; reuse `LiveSession`/`LiveSource`/
`RecordingTarget` from `civiccast/live/` for the ingest+record side, §6):

- **`ContributionRoom`** — a named, channel-scoped WebRTC room (the VDO.Ninja "room" the operator
  publishes guests into). Fields: `room_id` (slug PK), `channel_id`, `name`, `vdo_room_name` (the
  opaque VDO.Ninja room token, never reused across rooms), `max_guests` (int, default 6),
  `state` (`Literal["idle","open","live","closing","closed"]`), `compositor_target`
  (`Literal["obs_browser_source","gst_compositor"]`), `created_at`, `updated_at`. A room maps 1:1 to a
  browser-source slot in the compositor.
- **`GuestInvite`** — a single-use, expiring invite to one remote participant. Fields: `invite_id`
  (slug PK), `room_id` (FK), `guest_display_name`, `role`
  (`Literal["council_member","presenter","public_comment"]` — a *contribution* role, **not** an auth
  role), `invite_token` (opaque, ≥32 chars, single-use), `push_url` / `view_url` (the VDO.Ninja
  director/guest URLs the IFRAME API mints), `terms_agreement_id` + `terms_version` (reuse the
  `contribute/` terms pattern — public comment from home must accept terms before joining),
  `expires_at`, `consumed_at` (nullable), `created_at`. Tokens follow the existing receipt-token
  discipline (`contribute/models.py:194` `receipt_token`, min-length-enforced).
- **`RemoteGuestSession`** — the live, per-guest connection record (one per guest actually connected).
  Fields: `session_id` (slug PK), `room_id` (FK), `invite_id` (FK), `guest_display_name`,
  `state` (`Literal["invited","joining","connected","on_air","muted","dropped","ended"]`),
  `connection_quality` (`Literal["unknown","good","degraded","poor"]` — from VDO.Ninja stats, advisory
  only), `joined_at`, `on_air_at` (nullable), `ended_at` (nullable), `proof_boundary` (text — the
  declared boundary string, mirroring the egress/contribute proof-boundary convention).

**Migration: `0048_remote_contribution`** on the **single global alembic chain** (one head, currently
`0037_asset_meeting_body`; 3.0 migrations take a single monotonic sequence from `0038`+ per
`RECONCILIATION.md` and `tests/live/test_real_postgres.py`). Adds `contribution_rooms`,
`guest_invites`, `remote_guest_sessions`. **`down_revision` = the highest migration shipped ahead of it
on the chain** (the RECONCILIATION table assigns `0038`–`0046` to S4–S14; S15's engine and a future
S16 compositor section may claim `0047`, so S17 lands at **`0048`** — the implementer pins
`down_revision` to whatever is actually `HEAD` at merge time, never to a number not yet on `main`). New
tables only; **no `egress_*` or `live_*` co-edit** unless §6 chooses the `"webrtc"` `LiveSource` kind,
which would add a value to the `live_sources_source_type_check` CHECK constraint
(`civiccast/live/models.py:222-227`) in this same migration — note that co-edit explicitly if chosen.

> **RECONCILIATION addendum (proposed line for `RECONCILIATION.md` migration table):**
> `| 0048_remote_contribution | S17 | contribution_rooms/guest_invites/remote_guest_sessions; (opt.) adds 'webrtc' to live_sources_source_type_check |`
> Also add S17 to the §11 section index of the master (new optional-tier section, after S15/S16).

## 4. API surface (+ the five real auth roles)

All staff endpoints gate on the **five real roles only** (`setup_admin`, `meeting_operator`,
`records_clerk`, `publish_operator`, `support_admin`) via `require_any_role` — matching the live
router's pattern (`meeting_operator` runs sessions, `setup_admin` does source/infra CRUD,
`support_admin` reads diagnostics; `civiccast/live/router.py:237,549`). New module
`civiccast/live/contribution/router.py`, prefixes `/api/staff/contribution` and a narrow
`/api/public/contribution`.

| Method + path | Purpose | Role(s) |
|---|---|---|
| `POST /api/staff/contribution/rooms` | Create a contribution room on a channel | `setup_admin` |
| `GET /api/staff/contribution/rooms` | List rooms + state | `meeting_operator`, `support_admin` |
| `POST /api/staff/contribution/rooms/{room_id}/open` | Open the room (spins up the VDO director session via IFRAME API) | `meeting_operator` |
| `POST /api/staff/contribution/rooms/{room_id}/close` | Close room, drop all guests | `meeting_operator` |
| `POST /api/staff/contribution/rooms/{room_id}/invites` | Mint a single-use `GuestInvite` (returns `view_url` to send the guest) | `meeting_operator` |
| `POST /api/staff/contribution/sessions/{session_id}/on-air` | Put a connected guest on-air (selects guest into the composition) | `meeting_operator` |
| `POST /api/staff/contribution/sessions/{session_id}/mute` / `.../off-air` / `.../drop` | Per-guest control | `meeting_operator` |
| `GET /api/staff/contribution/sessions` | Live guest session list + connection quality | `meeting_operator`, `support_admin` |
| `GET /api/staff/contribution/diagnostics` | TURN reachability, VDO/coturn process health, ICE summary | `support_admin` |
| `GET /api/public/contribution/invites/{invite_token}` | Resolve a single-use invite → guest join page (validates + consumes token; no auth, token is the capability) | public (token-gated) |
| `POST /api/public/contribution/invites/{invite_token}/accept-terms` | Record terms acceptance before join (public comment from home) | public (token-gated) |

`setup_admin` owns room creation and the VDO/coturn infra config (consistent with live source CRUD
being `setup_admin`); `meeting_operator` owns the live show (consistent with the live session
lifecycle); `support_admin` is the read-only diagnostic surface (RECONCILIATION D1: read-only
diagnostics use `support_admin`). The public invite endpoints are **capability-gated by the opaque
single-use token**, never by an auth role.

## 5. Operator UI

A new **"Remote Contribution"** console (channel-scoped, under the live/channel-ops area; reuses the
role-aware console shell already shipped — `audit-lite-role-aware-operator-console-2026-05-30.md`):

- **Room panel:** create/open/close a room; shows room state and the embedded **VDO.Ninja director
  view via the IFRAME API** (the operator sees and hears every connected guest in one pane).
- **Invite composer:** name + contribution-role (council member / presenter / public comment), mint a
  single-use invite, copy/send the guest `view_url` (the guest opens it in any browser — no install).
- **Guest tray:** one tile per `RemoteGuestSession` with live connection-quality badge (good/degraded/
  poor, advisory), and per-guest **On-Air / Mute / Off-Air / Drop** controls. Putting a guest on-air
  routes that guest into the compositor slot (§6); the rest of the channel composition is unchanged.
- **Diagnostics drawer (`support_admin`):** TURN reachability test, VDO/coturn co-process health (from
  S9 supervision), ICE/relay summary, and an honest banner when no compositor is configured ("Remote
  contribution requires a compositor (OBS or the GStreamer compositor) — guests cannot reach the
  channel until one is configured" — directly stating the §1 boundary in-product).

The guest-facing surface is **VDO.Ninja's own unmodified browser UI** (the `view_url` opens VDO.Ninja
itself), not a CivicCast-built page — this keeps the AGPL process arms-length (§7) and means zero
WebRTC client code in CivicCast.

## 6. Behavior / algorithms

**The ingest chain (the load-bearing path):**

1. **Operator opens a room.** CivicCast calls the **VDO.Ninja IFRAME API** (postMessage control of the
   embedded VDO.Ninja iframe — the documented, supported integration surface) to create a director
   session in the self-hosted VDO instance, and persists a `ContributionRoom` (state `open`).
2. **Guest joins.** The guest opens their single-use `view_url`; VDO.Ninja negotiates WebRTC,
   preferring a direct peer path and **falling back through coturn (TURN relay)** when NAT/firewall
   blocks direct (the common case for "public comment from home"). CivicCast records a
   `RemoteGuestSession` (state `joining` → `connected`).
3. **Guest → browser source.** The guest's VDO.Ninja output URL is consumed as a **browser source**:
   either **OBS Browser Source** (S16) or the GStreamer **`wpesrc`** browser engine already specified
   for HTML-CG (S15 §5). VDO.Ninja produces only a browser-renderable stream — this compositor hop is
   **mandatory**, not optional (§1).
4. **Compositor → live source.** The compositor emits a clean composited frame as **NDI or SRT**, which
   registers as a CivicCast `LiveSource` (existing `ndi`/`srt` kinds — **preferred, zero schema
   change**) — *or* S17 adds a `"webrtc"` `LiveSource` kind (§3 co-edit). The existing `LiveSession`
   lifecycle and `RecordingTarget` then handle on-air + recording with no new egress code.
5. **On-air.** Operator puts the guest on-air; the GStreamer engine hot-swaps the contribution source
   into the program via `interpipesrc listen-to` (S15 §3) — same seamless raw-domain swap as
   program↔filler↔live, so the guest goes to air with continuous TS (no #151-class break).

**Scaling (optional, stated honestly):** a single self-hosted VDO.Ninja + coturn handles a handful of
simultaneous guests (the PEG common case: 1–6). For larger fan-out / many viewers, **MediaMTX (MIT
license)** is the optional **SFU/relay** in front of the path — it scales viewer distribution without
adding any AGPL surface (MIT is permissive). MediaMTX is a documented scaling hook, **not required for
V1**.

**Failure behavior:** a dropped guest (`RemoteGuestSession` → `dropped`) never takes the channel
off-air — the engine simply swaps back to program/filler (S15 hot-swap); the operator is alerted via
S8 (guest-drop is an alert condition handed to S8, §10). TURN unreachable at room-open is a hard
blocker surfaced in diagnostics, not a silent failure.

## 7. Proof tier (rung) + honest claim boundary

**Current rung: 1 (lab/source-proven).** The optional remote-contribution tier
is built for v3.0.0-beta1, with field NAT traversal and station-side guest proof
still outside the beta1 claim.

**Target ladder (master §5):**
- **Rung 1 (Lab-proven):** one guest joins a self-hosted VDO.Ninja over loopback/LAN through coturn,
  is composited and ingested as a CivicCast live source, and reaches a loopback egress sink — verified
  at a declared `proof_boundary` (LAN, no internet NAT traversal).
- **Rung 2 (Machine-proven):** the contribution tier survives the clean-install + unattended-soak gate
  with the VDO/coturn co-processes supervised across a reboot and an unclean restart (S9), guest join/
  drop/rejoin cycles, no co-process orphaning.
- **Rung 3 (SDI) and above are inherited from the engine, not claimed by S17** — S17 ends at "guest is
  a live source"; the SDI/headend rungs belong to S15/S2.

**Hard claim boundary (never overclaim):**
- **VDO.Ninja is AGPL-3.0, run unmodified as a separate process.** CivicCast **consumes its stream/URL
  and controls it via the IFRAME API** — arms-length integration. **AGPL §13's network-source-offer
  obligation attaches only if you fork and modify VDO.Ninja's code and serve that modified version**;
  running the **unmodified** project as a separate process and ingesting its output does **not** pull
  AGPL onto the Apache-2.0 CivicCast core. **Do NOT vendor VDO.Ninja source into CivicCast core.** If a
  station ever modifies the self-hosted VDO.Ninja, the station (operator of that service) carries the
  §13 source-offer obligation for *their* modified copy — document this, don't absorb it.
- **coturn = BSD-3-Clause** (permissive — no copyleft concern). **MediaMTX = MIT** (permissive).
- **No "secure/encrypted-conferencing-grade" claim.** WebRTC media is DTLS-SRTP encrypted in transit,
  but CivicCast neither audits nor certifies VDO.Ninja's security; the product says "self-hosted
  WebRTC contribution via VDO.Ninja," not "end-to-end-secure meetings."
- **No SDI/SRT-direct claim for VDO.Ninja** — it is ingest-only; the compositor hop is always stated.
- **Single-maintainer dependency risk (real, disclosed):** VDO.Ninja is primarily one maintainer's
  project. Mitigation in spec: **pin a specific released version** (vendor the pinned build artifact /
  container, not the source), test against that pin, and treat upgrades as reviewed events. The
  TURN-server requirement (coturn deployment + a reachable public IP/port) is a **real operational
  cost** the station owns — disclose it in S3 commissioning.

## 8. Test plan + 0/0/0/0/0 audit

1. **Contract (rung 0):** unit/API/UI tests for `ContributionRoom`/`GuestInvite`/`RemoteGuestSession`
   models (validators, single-use-token consumption, expiry), the staff/public router (role gating on
   all five-role surfaces, token-capability gating on public invites, 403/404/410-expired paths), and
   the IFRAME-API control bridge (mocked VDO postMessage).
2. **Lab (rung 1):** end-to-end one-guest join over self-hosted VDO + coturn on LAN → browser source →
   compositor → CivicCast live source → loopback egress; assert the guest frame reaches the sink and
   the `RemoteGuestSession` state machine transitions correctly; declare the `proof_boundary`.
3. **TURN-fallback test:** force a relay path (block direct ICE) and confirm coturn carries the media
   — the "public comment from home behind NAT" case.
4. **Co-process supervision (rung 2, with S9):** kill VDO.Ninja and coturn mid-session; confirm
   detection, clean restart, no orphaned processes holding ports, and that a guest can rejoin.
5. **Drop/recovery:** guest disconnects on-air → channel swaps back to program (no off-air), S8 alert
   fires.
6. **Scaling smoke (optional):** MediaMTX relay in front, N viewers, confirm no AGPL surface added and
   fan-out works.
7. **Playwright walkthrough:** operator creates room, mints invite, guest (headless browser) joins,
   operator puts on-air, mutes, drops; diagnostics drawer shows TURN reachability.
8. **License-hygiene check (CI):** assert no VDO.Ninja source is vendored into the Python core (path
   guard); assert the AGPL/BSD/MIT third-party notices are present.

**Every audit reaches 0/0/0/0/0** (Blocker/Critical/Major/Minor/Nit all zero): `audit-lite` after each
fix; full `audit-team` + `walkthrough` at tier completion (per master §12 and the standing audit
cadence).

## 9. DONE

The remote-contribution tier is DONE when:
- A remote guest opens a single-use invite link in a plain browser (no install), joins a self-hosted
  VDO.Ninja room through coturn, and the operator puts them **on-air on a CivicCast channel** through
  the compositor → live-source → GStreamer engine path, **recorded** like any live segment.
- Council-member, presenter, and public-comment-from-home flows all work through the same path.
- VDO.Ninja + coturn are **turnkey-installed by the S3 commissioning wizard** (no BYO, pinned version),
  and supervised by S9 across reboot/unclean-restart with no orphaning.
- A dropped guest never takes the channel off-air; guest-drop and TURN-unreachable raise S8 alerts.
- The license boundary holds: VDO.Ninja runs **unmodified as a separate AGPL process**, no source
  vendored into the Apache core, third-party notices present, CI guard green.
- Rung 1 (lab) and rung 2 (machine) are proven and recorded; claims never exceed the proven rung.

## 10. Dependencies / cross-refs + open decisions

**Cross-refs:**
- **S15 (GStreamer engine)** — the composited guest feed enters as a hot-swappable source
  (`interpipesrc listen-to`); `wpesrc` is the GStreamer browser-source option. Egress is unchanged.
- **S16 / OBS (compositor)** — the **mandatory** middle hop (VDO.Ninja → browser source → composited
  frame). S17 depends on a compositor existing; if S16 is the OBS-integration section, S17 consumes its
  browser-source output. *(S16 does not yet exist as a section file; flagged in open decisions.)*
- **`civiccast/live/`** — the composited guest output registers as a `LiveSource`; `LiveSession` +
  `RecordingTarget` handle on-air and recording (reuse, §6).
- **S3 (commissioning wizard)** — turnkey-installs VDO.Ninja (pinned) + coturn for this tier, adds a
  TURN-reachability commissioning check; the tier is opt-in.
- **S9 (reliability / process identity)** — VDO.Ninja and coturn join the co-process supervision set
  (pid+image+create_time identity, watchdog, clean restart) alongside CasparCG/OBS/NDI runtimes
  (master §4 item 1 already names "VDO" in the co-process list).
- **S8 (alerting)** — guest-drop, TURN-unreachable, and VDO/coturn-co-process-down are alert conditions
  handed to S8's hub.
- **`civiccast/contribute/`** — the asset-upload sibling; reuse its terms-acceptance and notification
  patterns conceptually, distinct module.

**Open decisions for Scott:**
1. **Compositor choice for V1:** OBS (mature, GPL co-process, S16) vs the GStreamer `wpesrc` compositor
   (in-engine, S15 §5, CPU-heavy). Recommendation: **GStreamer `wpesrc`** to avoid adding an OBS/GPL
   co-process to the base tier, with OBS as the documented premium-compositor option — but OBS is the
   lower-risk near-term path. *(Needs the S16 section to exist; see #2.)*
2. **S16 compositor section:** S17 (and S15 §5) both reference a compositor tier; the section now
   exists as `S16-production-control-room.md`. Migration `0047` is reserved for it ahead of S17's `0048`.
3. **`LiveSource` kind:** add a `"webrtc"` kind to the CHECK constraint (cleaner semantics, schema
   co-edit) vs reuse existing `ndi`/`srt` from the compositor (zero schema change). Recommendation:
   **reuse `ndi`/`srt`** for V1 (no migration risk), add `"webrtc"` later if the distinction earns its
   keep.
4. **VDO.Ninja version-pin & upgrade policy:** which released version to pin, and the cadence/criteria
   for reviewed upgrades, given the single-maintainer risk.
5. **MediaMTX in V1 or documented-hook-only:** ship the SFU scaling path functional in V1, or document
   it as a future hook? Recommendation: **documented hook for V1** (the PEG common case is ≤6 guests);
   build it when a station needs large fan-out.
6. **Public-comment-from-home moderation:** do public invites require operator admit-to-room before the
   guest can be put on-air (waiting-room model)? Recommendation: **yes** — mint invite → guest joins to
   a held state → operator admits, mirroring the `contribute/` review gate for the live path.
