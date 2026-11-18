# S18 — Comparative Capability Record

> **Comparative research appendix.** This document preserves public-source
> third-party product research used during CivicCast 3.0 planning. CivicCast is
> independent and is not affiliated with, sponsored by, endorsed by, or approved
> by Tightrope Media Systems, Cablecast, or any other named vendor. Product names
> are trademarks or trade names of their respective owners. This appendix is not
> a claim that every CivicCast deployment is a drop-in substitute for another
> product, and it is not a patent clearance, freedom-to-operate opinion, or
> legal opinion. Public-facing release copy should use vendor-neutral CivicCast
> feature language and link to [LEGAL-NOTICES.md](../../../../LEGAL-NOTICES.md).

> Status: **authoritative parity record for the 3.0 set.** Produced 2026-06-14 from three
> independent passes — (1) our own discovery + code-grep verification against `main`, (2) a Gemini
> deep-research run (`gemini-cablecast-report.md`, 66 sourced citations, mostly Tightrope/Cablecast
> official docs), and (3) Scott's domain corrections (underwriting is real PEG revenue; SAP/audio).
> A ChatGPT run was attempted with the same prompt but returned an off-topic result (tool failure)
> and contributed nothing. This section is the **single place** that reconciles what Cablecast does
> against what CivicCast has, and specifies what we add to reach parity. Where a closure extends an
> existing section, that section carries a "Parity additions" pointer back here.

## 1. Method & confidence

- **Cablecast surface** = enumerated by domain from official docs + municipal RFPs/POs (see the
  Gemini report's Works Cited). **CivicCast state** = verified by grepping `main` (not spec claims).
- Each gap below is tagged: `MISSING` (no code, no prior spec), `PARTIAL` (some code, incomplete),
  or `HAVE` (real parity today — listed for completeness, not a gap).
- Two passes independently agreed on the gap set; the only corrections came from code-grep
  (which **reversed three false-missing flags** — see §4).

## 2. Settled decisions (new canonical decisions — also in RECONCILIATION D14–D19)

- **D14 — Multi-site federation = V2, not V1.** Cablecast's equivalent (LiveBridge) is itself only
  GA Summer 2026; deferring is safe and Scott's explicit call.
- **D15 — SCTE-35 dynamic ad-insertion = NOT in scope.** Cablecast lacks it too (parity-neutral);
  underwriting is handled by scheduled spots + pre-rolls, not splice markers.
- **D16 — EAS: software CAP/IPAWS *display* + crawl only.** Mandatory Part-11 relay is the cable
  operator's FCC-certified headend device — never claim "EAS-compliant." **New requirement:** strip
  EAS attention tones from web/OTT egress (FCC enforces heavy fines for rebroadcast).
- **D17 — Underwriting/sponsorship spot management = IN scope.** PEG is noncommercial but runs paid
  underwriting spots from for-profit entities (47 CFR 73.503: may identify sponsor; no
  calls-to-action / price / qualitative claims). Build spot-as-asset + trafficking + break insertion
  + per-underwriter affidavits. VOD/OTT pre-roll already exists; extend to linear.
- **D18 — Agenda integration = IN scope (essential, government-access).** Agenda items synced to
  video timecode for chaptered navigation; optional agenda-PDF display beside the player.
- **D19 — Subscription paywall = niche, V1.x-optional, default OFF.** Stripe-style magic-link gate.

## 3. The reconciled gap table

| # | Capability | Cablecast (module) | CivicCast state | Sev | Closure → section / migration |
|---|---|---|---|---|---|
| 1 | Query-driven auto-scheduling (Saved Searches → auto-fill slots; 14–60d rolling window) | Autoschedule | MISSING (program log binds a static `asset_id` per slot) | essential | S4/S5 → `0043` (shipped) |
| 2 | Scheduled recording from SDI **and** network streams | VIO + Automation | SHIPPED (S21, `0056_scheduled_recording`: forward-scheduled capture from live inputs (SDI/HDMI/NDI) + network streams (RTSP/SRT/HLS/RTMP/MPEG-TS) with one-shot + weekly recurrence, arm → record → finalize state machine, overlap detection + S8 alert on source/finalize failure, crash-mid-record reconcile; production wiring binds the capture pipeline, asset finalizer, alert sink, and scheduled-recording worker in `create_app()`. Shipped as a sibling off `0055`; merge revision `0060_recording_paywall_merge` unifies the heads.) | essential/common | S21 → `0056_scheduled_recording` SHIPPED |
| 3 | User-defined custom metadata fields (searchable, in API) | Cablecast Automation | MISSING (fixed `Asset` schema) | common | S22 → `0054` (shipped, HEAD) |
| 4 | Block / daypart scheduling | Autoscheduler | MISSING | common | S4 → `0043` (shipped) |
| 5 | As-run / proof-of-performance + EPG X-List (CSV/XML to TV Guide/TitanTV/XMLTV) + franchise/hours reporting | Cablecast Reporting | PARTIAL (generic egress proof exists; no as-run suite / EPG export / hours-by-category) | essential | S14/S23 → `0055` (SHIPPED 2026-06-18) |
| 6 | CG depth: uploaded bulletins, **live-video bulletins**, bulletin & channel background audio, zone tags/filtering, program-aware interstitials ("coming up next") | Cablecast CG | PARTIAL (S6 has templates/zones/feeds; these sub-features absent) | common | S6 → `0045` (shipped) |
| 8 | GPI / serial (RS-232/422) + router/switcher control; Take-Delay/Post-Roll transition timing | Control Module Sets | PARTIAL — router/switcher take + Take-Delay/Post-Roll timing are real (`ProductionDevice`/`DeviceProfile`/`DeviceCommand`, `0047`). **GPI and serial are honestly labeled network-relay triggers (TCP); direct GPI/serial hardware is not supported in this release** — `tsr_service/index.mjs` routes both `gpi`/`serial` device kinds through TSR's generic `TCPSEND` adapter (same as the plain `tcp` kind), disclosed at the `ProductionDevice.kind` field, the operator console's device-kind picker, `CAPABILITIES.md`, and the OpenAPI schema. A station needing real hardware fronts it with its own TCP-to-GPI or TCP-to-serial relay box. | common | S16 → extends `0047` (shipped) |
| 9 | SAP / Multiple Audio Programs + descriptive-audio tracks (accessibility/ADA) | Cablecast Automation 7.8+ | MISSING (no secondary-audio routing) | common | S11 → `0052` (shipped) |
| 10 | Underwriting spot management: spot-as-asset, trafficking/rotation (flights, freq caps, daypart), break insertion, **per-underwriter affidavits** + billing | Pre-Roll Messaging + Automation + Reporting | SHIPPED (S24, `0057_underwriting_spots`, 2026-06-18: linear spot-asset + trafficking compiler + per-underwriter affidavit via S23 as-run join + CSV/XML/PDF billing export; VOD/OTT pre-roll path unchanged) | essential | S24 → `0057_underwriting_spots` SHIPPED (renumbered from planned `0055` per RECONCILIATION D17 after S23 took `0055`) |
| A | **Meeting agenda integration** — agenda items synced to video timecode (chaptered nav) + optional agenda-PDF beside player | Cablecast VOD Pro (PDF agenda) | SHIPPED (S25, `0058_meeting_agenda`, 2026-06-18: `meeting_agendas`/`agenda_items` + publish gate + sync-from-chapters + plain-text import + public read endpoint + portal-public agenda sidebar with seek + a11y per S20 DC-5) | essential (gov) | S25 → `0058_meeting_agenda` SHIPPED (renumbered from planned `0056` per RECONCILIATION D18 after S23 took `0055`) |
| B | **EAS attention-tone stripping** on web/OTT egress (FCC compliance guardrail) | (manual in Cablecast) | MISSING | essential (compliance) | S11 (egress audio filter; `eas_tone_strip_enabled` schema field) |
| C | Subscription paywall (Stripe magic-link) | Internet Channels (7.10) | SHIPPED (S26, `0059_paywall_access`, 2026-06-18: `paywall_configs`/`access_grants`/`paywall_subscriptions` + Stripe-hosted Checkout + magic-link sign-in + signed webhook; default OFF, PCI SAQ-A scope) | niche | portal → `0059_paywall_access` SHIPPED (renumbered from planned `0057` per RECONCILIATION D19 after S23 took `0055`) |
| 7 | Multi-site federation | LiveBridge (GA Summer 2026) | MISSING | common | **V2 — deferred (D14)** |

## 4. Corrections this audit produced (do not re-flag these as missing)

Code-grep **reversed three false-missing flags** the matrix had raised:
- **Producer/contributor management — WE HAVE IT.** Full `civiccast/contribute/` module
  (`ContributorAccount`, `ContributorSubmission` state machine, `ContributorReviewQueue`,
  `ProducerActivityReport`, submission agreements, `ScheduleHandoff`). This is functionally
  comparable to the incumbent content-submission workflow; CivicCast implements its own contributor
  portal in the open-source product.
- **Dynamic/smart playlists — PARTIAL, not missing.** `SmartPlaylistDefinition` exists for OTT
  (`app_platform/`); only the *cable program-log* side lacks query-driven scheduling (that's gap #1).
- **Redundancy/failover — PARTIAL, not missing.** ~29 code refs; HA exists in some form.

Overclaims caught (were "have," really partial): Video Chaptering, Dynamic Playlists, VOD Player
Embedding, pop-on/roll-up captions. Confirmed real parity today (`HAVE`): producer portal,
translation (`translategemma`), rules-based archiving (`archive/` + retention), VOD/web, CEA-608/708
captions, loudness, analytics (viewer count + time-watched), OTT apps, NDI/SRT/RTMP/RTSP/HLS/SDI I/O.

## 5. Per-gap closure specs

### Gap 1 — Query-driven auto-scheduling (S4/S5, `0043` — shipped)
Entities: `SavedSearch` (name, query JSON over asset metadata), `AutoScheduleRule` (saved_search_id,
target slot/recurrence, pick = `top_result|random_result|newest`, rolling-window days 14–60),
`ScheduleBlock` (gap 4: daypart/block start–end dates). Behavior: a compiler materializes rules into
`schedule_items` on a rolling window, using a periodic compile cycle for query-driven schedule
materialization. It honors the existing OnAirLock commit gate (S4) and replaces static-asset-per-slot
binding with a query resolved at materialization time.

### Gap 2 — Scheduled recording (S21, `0056_scheduled_recording` — SHIPPED 2026-06-18, the sibling slot off `0055`)
Entities: `RecordingSchedule` (source = SDI/HDMI/NDI input id OR network stream URL
[RTSP/SRT/HLS/RTMP/MPEG-TS], one-shot OR weekly recurrence, duration_seconds, encoder_profile,
loudness_regime, optional target_series + custom_field_values stamps), `RecordingJob`
(state machine `scheduled → arming → recording → finalizing → done` with terminal `failed`/
`skipped` branches). Behavior: the scheduler expands enabled schedules to upcoming jobs; at
window-start − arm-lead the service validates source reachability (S8 alert if not), opens an S15
capture pipeline, and at window-end finalizes via S7 (`asset_readiness`) with target-series +
custom-field stamps. Overlap on the same input → `skipped` (DC-5); source unreachable →
`failed` + S8 alert (DC-3 never a silent miss); crash mid-record is reconciled at startup. The
capture pipeline + asset finalizer + alert sink are injected Protocols — production now wires them to
the ffmpeg-backed capture runtime, scheduled asset finalizer, and S8 condition hub; tests inject stubs. **This was the LAST S18 parity gap on the chain**; with the
merge revision `0060_recording_paywall_merge` data layer + service + API + UI are all on disk.

### Gap 3 — Custom metadata fields (S22, `0054` — shipped, HEAD)
Entities: `CustomFieldDef` (key, label, type = text|list|date|number|asset|producer, searchable),
`CustomFieldValue` (asset_id, field_id, value). Exposed in the asset API + portal search facets.

### Gap 5 — As-run + EPG + franchise reporting (S14/S23, `0055` — SHIPPED 2026-06-18)
Entities: `AsRunLogEntry` (channel, asset, scheduled vs actual air time, duration, verified bool),
`EpgExportConfig` (format = xlist|xmltv|csv, channel map, endpoint). Reports: shows-aired by date/
channel, hours-by-category (franchise: "government hours" vs "public-access hours"), per-underwriter
affidavit (links gap 10). EPG export feeds TV Guide / TitanTV / XMLTV aggregators.

### Gap 6 — CG depth (S6, `0045` — shipped)
Extend CG: `BulletinMedia` (uploaded image | full-screen | **live-video zone source**),
`BulletinAudio` (per-bulletin narration + per-channel background, under the existing loudness path),
`ZoneTag` (tag/filter content into zones). Program-aware interstitials = a feed-kind that reads the
program log ("coming up next" / "you were just watching").

### Gap 8 — Device control (S16, extends `0047`)
Extend `ProductionDevice`/`DeviceProfile` with `gpi` and `serial` (RS-232/422) device kinds + router/
switcher control; add `device_command` audit + Take-Delay/Post-Roll transition-timing fields. Router/
switcher take and the timing fields are real. **Honest label:** `gpi` and `serial` are network-relay
triggers (TCP), not a legacy contact-closure/serial path — TSR has no dedicated GPI or RS-232/422
device type, so `tsr_service/index.mjs` routes both through the generic `TCPSEND` adapter, same as
the plain `tcp` kind. Direct GPI/serial hardware is not supported in this release; a station needing
real hardware fronts it with its own TCP-to-GPI or TCP-to-serial relay box.

### Gap 9 — Secondary audio / SAP / descriptive audio (S11, `0052` — shipped)
Entity: `AudioProgramTrack` (asset_id or channel_id, kind = primary|sap|descriptive, language,
source). Engine (S15) muxes secondary audio PIDs on cable + exposes a track toggle on web/OTT players.

### Gap 10 — Underwriting spot management (S24, `0057_underwriting_spots` — SHIPPED 2026-06-18; renumbered from planned `0055` per RECONCILIATION D17)
Entities: `UnderwritingSpot` (asset, underwriter, FCC-compliant flag/notes), `SpotFlight` (start/end
dates, frequency cap, daypart targeting), `SpotPlacement` (resolved insertions into program-log
breaks), `UnderwriterAffidavit` (per-underwriter as-run proof: timestamp/channel/duration → billing
report). Linear insertion via the program log; VOD/OTT pre-roll path already exists. **Editorial note
(not enforced by code):** 47 CFR 73.503 — sponsor identification only; no calls-to-action/price/
qualitative claims.

### Gap A — Meeting agenda integration (S25, `0058_meeting_agenda` — SHIPPED 2026-06-18; renumbered from planned `0056` per RECONCILIATION D18)
Entities: `MeetingAgenda` (meeting/asset id, optional source PDF), `AgendaItem` (title, order,
**video_timecode**). Behavior: agenda items become video chapters (jump-to-item); portal player
optionally renders the agenda PDF beside the video (Cablecast VOD Pro parity). Government-access
essential.

### Gap B — EAS attention-tone stripping (S11, egress processing — no schema)
A mandatory audio-filter stage on web/OTT egress sinks that detects/removes EAS attention tones (and
the 853/960 Hz two-tone) so alerts aired on cable are **not** rebroadcast on internet outputs
(FCC enforcement). Config flag on `EgressSinkSpec`; default on for web/OTT sinks.

### Gap C — Subscription paywall (portal, `0059_paywall_access` — SHIPPED 2026-06-18, **optional / V1.x / default OFF**, PCI SAQ-A scope; chain HEAD moved to `0060_recording_paywall_merge` after S21 shipped as the `0056` sibling)
Entities: `PaywallConfig`, `AccessGrant`, `Subscription` (Stripe-hosted Checkout + magic-link
sign-in). Niche monetization, default OFF — a station that never enables it sees empty tables +
zero behavior change (DC-1). Card data never touches CivicCast — Stripe-hosted Checkout +
Customer Portal only (DC-4 = PCI SAQ-A scope). Magic-link tokens are HMAC-signed, single-use, and
short-lived (DC-5). Webhook signature verification enforced at the router (DC-3).

## 6. Migration assignments (authoritative — matched to the on-disk shipped chain)

> **Reconciled 2026-06-18 to the as-built chain (RECONCILIATION.md is the cross-check).** The
> earlier planned `0049`–`0057` numbering diverged from what actually shipped; the table below uses
> the **real on-disk migration numbers** for shipped gaps. Head =
> `0060_recording_paywall_merge` (the data-free merge revision that unifies the `0056` sibling
> branch off `0055` with the linear `0057→0058→0059` chain). Chain shape:
> ```
> 0054 → 0055 ─┬→ 0056 ─────┐
>              │             ↓
>              └→ 0057 → 0058 → 0059 → 0060 (HEAD)
> ```
> **There are no genuinely unshipped on-chain slots remaining**; every software-shippable S18
> parity gap is on disk. Hardware-only gates (Gap B EAS tone strip is an egress filter flag on
> `EgressSinkSpec` with no schema; Gap 8 GPI/serial extends shipped `0047`; Gap 7 federation is V2)
> do not add migrations.

### Shipped (file exists on disk under `civiccast/<module>/migrations/versions/`)

| Migration | Owner | Gap | Adds |
|---|---|---|---|
| `0043_scheduling_automation` | S4/S5 (S19) | 1, 4 | `saved_searches`/`auto_schedule_rules`/`schedule_blocks` |
| `0045_cg_depth` | S6 | 6 | `bulletin_media`/`bulletin_audio`/`zone_tags` |
| `0052_secondary_audio` | S11 | 9 | `audio_program_tracks` (SAP/descriptive) |
| `0054_custom_metadata_fields` | S22 | 3 | `custom_field_defs`/`custom_field_values` |
| `0055_asrun_and_epg` | S14 (S23) | 5 | `as_run_log`/`epg_export_configs` (SHIPPED 2026-06-18) |
| `0056_scheduled_recording` | S21 | 2 | `recording_schedules`/`recording_jobs` (SHIPPED 2026-06-18, sibling off `0055`) |
| `0057_underwriting_spots` | S7/S4/S14 (S24) | 10 | `underwriting_spots`/`spot_flights`/`spot_placements`/`underwriter_affidavits` (SHIPPED 2026-06-18) |
| `0058_meeting_agenda` | S7 (S25) | A | `meeting_agendas`/`agenda_items` (SHIPPED 2026-06-18) |
| `0059_paywall_access` | portal (S26) | C | `paywall_configs`/`access_grants`/`paywall_subscriptions` (SHIPPED 2026-06-18, optional/V1.x/default-OFF) |
| `0060_recording_paywall_merge` | S21/S26 | — | merge revision — unifies the `0056` branch and `0059` head into a single new head (data-free; no schema change) |

### Unshipped — none

Every S18 parity gap that adds a migration has shipped. Step-12 in master §10 has flipped
from `partial` to `built` with the S21 ship.

(EAS tone-stripping [B] and GPI/serial [8] add no new tables — B is an egress filter flag on
`EgressSinkSpec`; 8 extends the shipped `0047_production_control`.)

## 7. Build-order placement
These fold into the master §10 build order at the steps that own their domain (scheduling-automation
with S4/S5; recording/custom-fields/agenda/underwriting with S7/media-lifecycle; CG depth with the CG
step; SAP + tone-stripping with the captions/EAS step; as-run/EPG with the analytics step; GPI with
the control-room step). Paywall is V1.x-optional. Multi-site is V2.
