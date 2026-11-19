# CivicCast 3.0 — Software-Defined PEG Station In A Box (MASTER SPEC)

> **Comparative research/spec appendix.** This document contains historical
> third-party product comparisons from the CivicCast 3.0 planning process.
> CivicCast is independent and is not affiliated with, sponsored by, endorsed by,
> or approved by Tightrope Media Systems, Cablecast, or any other named vendor.
> Product names are trademarks or trade names of their respective owners. This
> appendix is not a substitute-product claim and is not a patent clearance,
> freedom-to-operate opinion, or legal opinion. Public release surfaces should
> describe CivicCast's own features and evidence boundaries in vendor-neutral
> language. See [LEGAL-NOTICES.md](../../../LEGAL-NOTICES.md).

> **Design-target boundary (October 11, 2026):** This is the accepted CivicCast
> 3.0 design target, not a statement of the current Beta 20 shipped scope.
> The approval/build cursor below was checked against `main @ 69cc676` in June
> 2026 and is a historical implementation snapshot. The dated October 10
> caption-policy amendment later in this document remains an owner decision.
> For current shipped behavior and candidate evidence, use the [Beta 12
> verification record](../../../docs/releases/v1.0.0-beta.12-verification.md)
> and source at the candidate revision.

> Status: **APPROVED — implementation IN PROGRESS** (branch `work/3.0-gstreamer-engine`).
> The build order in **§10 is the live tracker**; current cursor = **step 4 (S8 alerting)**,
> with steps 0–3 (prototype, GStreamer engine, 4h soak, S9 reliability) code-complete and
> machine-verified. Authored 2026-06-13; verified against code at authoring time on
> `main @ 69cc676`. This is the **master / index** of a layered spec set; the detailed
> build specs live in `docs/spec/3.0/sections/`. This document owns the thesis, the
> comparative PEG automation target, the entity model, the proof ladder, the AI/model decision, the
> build order, the global gates, and the reconciliation with the existing `docs/spec/spec.md`.
>
> Every external claim here is sourced from a grounded research pass (Cablecast product
> lineup + fee model; FCC Part 11 EAS; Gemma 4 facts/license) and a code-verification pass.
> Confidence and residual uncertainties are flagged inline. Overclaiming is the cardinal sin.

---

## 0. Read this first — the reframe

**CivicCast is already ~80% a software-defined PEG station in a box.** The cable-automation
sprint (CA-1..CA-8) shipped to `main`, in-tree: `civiccast/egress/` (~8,100 LOC, 30 modules),
`civiccast/cable/` (~1,100 LOC), `civiccast/programlog/` (~900 LOC), plus operator screens
(`ChannelOpsScreen`, `FacilityRouterScreen`, `SystemHealthScreen`). The 3.0 beta line
has since passed the v3.0.0-beta1 finish-line, 12-hour release-artifact, and 24-hour
public-beta release-artifact soaks.

So 3.0 is **not** a green-field build. It is: **finish the genuine gaps, harden for unattended
operation, prove on real hardware, and re-center the product** on cable/PEG as the flagship.
The prior v1.8→v2.0 "parity" run targeted a *community-media streaming* product and explicitly
deferred cable to a separate add-on — which is why it scored zero on the goal you actually
care about (software-defined PEG station automation). 3.0 makes that PEG automation profile the explicit target and finishes it.

**No deferrals of PEG-automation work.** The bulletin-board designer, OTT apps, captioning
compliance, and the software EAS layer are all in scope. The only things excluded are hard
external/legal gates (FCC EAS *certification*, app-store *review*, physical-hardware *proof*),
and even there we build the software up to the gate and document the gate honestly.

---

## 1. Product thesis

CivicCast 3.0 packages PEG broadcast automation as **software on one commodity
PC a station buys locally for under ~$5K.** One CivicCast PC runs playout, scheduling,
CG/bulletins, VOD, live ingest, recording, the public portal, OTT app configuration,
captioning/translation/summaries, analytics, health, backups, and support tooling. Hardware is
minimized to the unavoidable last mile: an **SDI output with embedded audio** (Blackmagic
DeckLink) plus any cableco-required headend interface.

We do not compete with any vendor's *hardware*. The software target is lower
operator lock-in and a smaller recurring cloud/license bill (see section 2.3).

**Posture:** for the PEG profile, CivicCast 3.0 embraces the "software-defined appliance"
experience — guided setup, sane defaults, visible health, recoverable failures, phone-first
operation — while remaining open-source and self-hosted (not SaaS, not a hardware vendor).
This intentionally supersedes the old spec's "not an appliance" framing for this profile.

**Cable is additive, not a replacement.** CivicCast remains the full community-streaming /
civic-meeting platform — VOD, captions, translation, AI summaries, the public portal, three-tier
publishing. The PEG/cable capability (playout, headend, SDI, CG channel) is an **opt-in deployment
profile layered on top**, not a new product that displaces the old one. A school board, an HOA, or
a small-town clerk runs CivicCast for streaming/VOD/meetings and **never touches** the cable
features. 3.0's "re-centering" promotes cable from a deferred separate-repo add-on to a first-class
*core capability you can turn on* — it does not make CivicCast cable-only.

**V1 target:** the standard **3-channel PEG triad** (public / education / government) — what LPM
and most PEG stations actually run, what the code already drives, and what the 24h soak exercised
(three concurrent encoders, stable for the full window; the only failure was the public-channel
continuity bug, not concurrency). The engine is **N-channel-capable**: a single-channel station
simply configures one channel; larger stations add more where the PC + SDI output hardware prove
it. Note the hardware implication — **3 channels = 3 SDI outputs**, so a multi-output DeckLink
(Duo 2 / Quad 2) or one device per channel (see S1/S2), not a single-output card.

---

## 2. Comparative PEG automation target (sourced)

### 2.1 What Cablecast actually is (Tightrope Media Systems)

Verified product surface (sources: cablecast.tv catalog, support docs, trade press):

| Cablecast capability | Product / status | CivicCast today |
|---|---|---|
| Automation / playout (24/7) | Cablecast 7.x + Autoscheduler (core) | `egress/` automation + `programlog/` — **built** |
| **Force Matrix** (manual source→dest routing, Channel Override) | core feature (their own term) | `facility/` router (preview) + `egress/live_takeover.py` (coded, unwired) |
| Playout server hardware | VIO family (Lite/2/4/OMNI/Stream/CG/VOD); SX/Flex legacy | we are the software; commodity PC + DeckLink |
| VOD + Public Site + Internet Channels (paywall/preroll) | core + Reflect cloud | portal + `publish/` + `playback_policy/` (gating/preroll) — **built** |
| Live streaming | Cablecast LIVE / LIVE Multi / RTMP (cloud) | `live/` + HLS — **built** |
| **CG Bulletin Board / CG Player** (on-channel bulletins, crawls, DSK) | add-on (CBL-CGPLAYER-LIC ~$1,550) | `cg/` render-only, **no persistence — real gap** |
| **OTT apps** (Roku/AppleTV/FireTV/Android/iOS) | "Screenweave" → "Branded/Basic Streaming App"; basic apps are listed with Reflect for up to 3 channels | `app_platform/` contracts only — **real gap** |
| Closed captioning | cloud ASR + editor, **metered ~$12/hr** | local faster-whisper (whisper-large-v3) — **built; local processing can reduce metered captioning costs** |
| Translation | cloud, 7 langs live / 72 VOD | local `translategemma:4b` — **built** |
| Smart Summary (AI) | Smart Summary | `summary/` (gemma4:e4b today) — **built** |
| EAS | **none — Cablecast is not an EAS device** (confirmed twice) | software CAP display in scope; mandatory relay at headend (parity-neutral) |
| Router/EPG/ingest/metadata | Force Matrix / Autoscheduler / SAM | facility + programlog + schedule — **built/partial** |
| Reporting/analytics | Audience Measurement (via Reflect) | `analytics/` aggregate — **partial** |

**Correction baked in (I had this wrong earlier):** the PEG bulletin board is **Cablecast CG
Player**, NOT "Carousel." Carousel is a *separate* Tightrope digital-signage division product
(Apple TV, schools/workplaces) and is **not** part of a PEG Cablecast license. Do not conflate.

### 2.2 Residual uncertainties (flagged, not blocking)

- Exact "Screenweave → Branded Streaming App" rebrand status (inferred from catalog; "Screenweave"
  persists in older blog content).
- Precise current REFLECT+ base price (reseller sheet dated Jan 2025).
These don't change the parity scope; they're noted for honesty.

### 2.3 Cost comparison (sourced to real municipal receipts)

Cablecast = capital purchase + recurring cloud/support. What a self-hosting CivicCast station
**eliminates**:

- **Recurring ~$4,000–$10,000+/yr**, dominated by **REFLECT+ cloud (~$4,100/yr)** + support
  (Gold $400/yr·I/O → Platinum $3–4k/yr) + hardware assurance ($700–$3,465/yr) + metered
  captioning ($12/hr) + per-TB cloud storage ($600/yr/TB) + branded-OTT renewal ($250/yr).
- Up-front capital: VIO Lite ~$10.5k list → VIO-4 Plus ~$34.6k. Real receipts: **St. Joseph, MN
  $14,845.94**; **Marshall, MN $42,435** — both from PEG access funds.
- Review current service terms before relying on any uncapped-delivery assumption; Reflect's Terms
  of Use reserve metering rights.

**The cost case a station evaluates:** self-host CivicCast on a ~$5K PC, an SDI converter, and local
storage to reduce or avoid recurring platform fees, subject to deployment choices. That is one of
the outcomes 3.0 must earn, and earning it is exactly the gap list in §4.

---

## 3. What is already built (verified on main @ 69cc676)

| Capability | Where | Proof rung (§5) |
|---|---|---|
| 24/7 automation: command queue, encoder supervision, gap-filler, join-in-progress, reboot recovery, orphan reap | `egress/automation.py`, `daemon.py`, `supervisor.py` | lab → **machine** (soak in flight) |
| Playout states STOPPED/STARTING/ON_AIR/TRANSITIONING/FALLBACK_SLATE/DRAINING/STOPPING/ERROR | `egress/models.py` | shipped |
| Program log: recurring slots → schedule_items, 72h rolling, idempotent, honest skips | `programlog/` | shipped |
| 6 headend profiles (generic-udp-spts, comcast-mtd-sd/hd, telvue, harmonic, leightronix) + apply API + readiness API | `egress/headend.py` | **lab** (machine-verified; none field-proven) |
| UDP/SPTS CBR MPEG-TS sink | `egress/sinks.py` | **lab** → production-wired |
| TSDuck compliance probe (TR 101 290 priority-1 subset) | `egress/compliance.py` | **lab** (0% drift @ 8 Mbps) |
| SDI: supervised BYO-DeckLink relay + readiness | `egress/sdi_relay.py` | **contract** (card proof pending) |
| NDI relay | `egress/ndi_relay.py` | **contract** (receiver proof pending) |
| Caption embed into TS (sidecar passthrough) + decode-proof CLI + caption_status health | `egress/caption_embed.py`, `cli.py` | **partial** (CEA-708 mode enum-declared, not implemented) |
| Loudness at conform (`loudnorm`, default -16 LUFS, per-channel) | `egress/preparer.py` | **lab** (playout encoder is `-c:a copy`) |
| Continuous health telemetry (event-driven, pull-only API) | `egress/health.py` | shipped (no push alerting) |
| Station identity (StationProfile + StationAppConfig + ChannelBranding) | `installer/`, `app_platform/` | shipped |
| Auth: 5 roles + `require_any_role` per endpoint | `auth/` | shipped |
| Installer 11-screen wizard, `doctor`, backup/restore/recovery-kit | `installer/` | shipped |
| Live ingest/recording (RTMP/RTSP/NDI/SRT), preflight, finalization | `live/` | shipped |
| Captions (faster-whisper whisper-large-v3) + translation (translategemma:4b) + summary (gemma4:e4b) | `captions/`, `translate/`, `summary/` | shipped |
| AV facility router (inventory + take **planning** + preview, in-memory) | `facility/` | shipped (planning tier) |
| Asset ingest validation (ffprobe whitelist, fail-closed) | `schedule/ingest.py` | shipped |

---

## 4. The real work (genuine gaps — none deferred)

Ordered by how much each gates the unattended software-defined PEG appliance profile.

> **24h soak verdict (2026-06-13): FAIL — and it pinpoints the #1 fix.** The platform ran the full
> 24h unattended and stable: server/monitor/automation alive, exactly 3 encoders in every sample,
> 0 cadence gaps >6min, 0 crashes, no memory leak, clean midnight-UTC crossover; the kill-test and
> the dirty-restart both recovered with no operator action. It FAILED on **sustained public-channel
> TSDuck continuity failures** (58 pass / 38 fail; every fail continuity-only with all other checks
> passing; clustered on public, the most-transitioned channel) — consistent with **#151** (TS
> session reset at program/filler boundaries) but not provably benign, so the tester honestly
> reported FAIL. The two other PASS-blockers were **not** software faults: no machine reboot (no
> Scott-approved "reboot now" in-window), and three transient `STARTING` samples that tripped the
> directive's allowed-state list (which omitted `STARTING`, a legitimate transient — a tester-harness
> over-strictness to relax on rerun). **Net: one real product blocker (#151 continuity); machine-
> proven (rung 2) is not yet achieved.**

0. **Public-channel TSDuck continuity (#151) — CONFIRMED by the soak; fixed by the engine
   re-platform.** The per-segment ffmpeg teardown reset the TS continuity counter at program/filler
   boundaries. The fix is the **GStreamer persistent-pipeline engine (S15)** — the mux/sink never
   restart, so continuity is unbroken by design. A **4-hour re-test** on the GStreamer engine
   validates clean TSDuck first; the full 24h machine-proven run (with a Scott-approved reboot + the
   `STARTING` directive fix) comes at the END of the build, not mid-stream.
   **(S15 + S9)**

1. **Engine + co-process supervision (S9).** The GStreamer engine (S15) is in-process, so the
   per-segment ffmpeg orphan-reap problem (#151 / ENG-001/003/009) largely dissolves — the engine
   recovers via pipeline restart, not relay reap. The surviving device-lock concern is the
   **optional CasparCG / SDI co-process** holding the DeckLink card after an unclean restart, so the
   process-identity primitive (pid+image+create_time) applies to the co-processes (CasparCG/OBS/VDO/
   NDI runtime), alongside uniform pacing latches and a pipeline watchdog/clean-restart. **(S9)**
2. **Operational alerting — none.** No push (email/SMS/webhook) to the operator on off-air /
   encoder-death / server-crash / schema-drift / relay-blocked. An unattended box must call for
   help. Includes runtime "safe-to-air" status + QA-004 fix (`sink_connected=false` on healthy
   idling UDP sink). **(S8)**
3. **SDI field proof (rung 3) — NOT a build blocker; build the path, prove it when hardware lands.**
   Egress wires to the GStreamer `decklinkvideosink` engine (S15) so a DeckLink + BMD Desktop Video
   SDK is plug-and-play. Physical SDI capture is a core product claim but happens when a card is
   available — Scott will try to source one; the proof most likely lands at LPM near the end — not as
   a gate. **(S15/S1/S2 + hardware)**
4. **CG Bulletin Board designer + persistence.** Today render-only, no DB, no CRUD. Build the
   multi-zone designer (fullscreen/L-bar/lower-third/bug/ticker/emergency), feed sources
   (RSS/iCal/CalDAV/weather/social) + zone content modes (manual/image/clock),
   moderation/scheduling/expiration, persisted. The comparison target is
   bulletin-board / CG workflow coverage. **(S6)**
5. **OTT apps (generic, multi-platform).** Roku/Apple TV/Fire TV/Android/iOS shells off the
   existing `app_platform/` contracts. Required: it's frequently a city/franchise contract
   condition for the PEG entity (true for LPM), and incumbent offerings may include basic apps with
   the hosted delivery tier. **(S12)**
6. **Commit-to-Air gate.** Program-log materialization is fully automatic; add operator
   approval / dry-run / conflict+missing-media validation / on-air lock (`spec.md §8.3` already
   mandates "no auto-commit to air"). **(S4)**
7. **Software Force Matrix wiring.** The takeover engine (`supervisor.request_live_takeover/
   handback`, priority model emergency>live>schedule>filler) exists but is unreachable — no API/
   CLI. Wire it + add take audit + handback. **(S5)**
8. **EAS software layer.** CAP/IPAWS + NWS weather + AMBER ingestion and on-channel display
   (crawl/overlay/forced slate); optional SAME encoder for local origination; honest boundary
   (mandatory Part 11 relay = operator headend; never claim "EAS-compliant"). **(S11)**
9. **CEA-708 ancillary captions + decode-proof in the live loop; per-headend loudness** (default
   -24 LKFS ATSC A/85 for cable sinks vs -16 LUFS streaming). **(S11)**
10. **AI model-selection surface** + adaptive 12B default (see §8). **(S13)**
11. **Commissioning wizard: add headend/SDI/TSDuck/output-proof steps** to the 11-screen
    installer. **(S3)**
12. **Production & Network I/O capability (NDI in/out + live inputs).** Comparison target = NDI **input**
    (ingest studio/chamber sources), NDI **output** (channel as NDI alongside SDI), and native
    live-input decoding (RTP/RTSP/RTMP/HLS/SRT/YouTube-Live). CivicCast's `live/` already ingests
    RTMP/RTSP/NDI/SRT; net-new is **first-class NDI output the OBS/DistroAV way** (Apache sender +
    user-installed free NDI runtime, replacing the patched-ffmpeg relay) + confirming HLS/YouTube-Live
    inputs. NDI is one of three first-class output paths (IP-TS / SDI / NDI) — **in scope, not
    deferred**. **(S2 + `live/`)**
13. **Analytics / Audience Measurement.** Match Cablecast (Viewer Count + Time Viewed × VOD+Live,
    dashboard + CSV) by extending the existing analytics module and differentiating from the
    incumbent workflow: self-hosted analytics that do not require a managed CDN subscription
    (audience measurement is tied to the Reflect delivery path/subscription), one-click
    board-ready PDF, explicit live peak-concurrent, plus as-run / proof-of-performance reports off
    `programlog`/`schedule` for franchise/funding. Streaming-only scope (no linear/QAM ratings —
    nobody can measure those). **(S14)**

---

## 5. Unified proof / certification ladder

One ladder subsumes the four overlapping vocabularies in the codebase today. Every capability,
doc line, and UI claim is tagged; **nothing may claim a rung above its proof.**

| Rung | Name | Bar |
|---|---|---|
| 0 | **Contract-tested** | Code + unit/API/UI tests; no runtime egress |
| 1 | **Lab-proven** | Runtime proof against synthetic/loopback at a declared `proof_boundary` |
| 2 | **Machine-proven** | Clean Windows install + unattended soak (24/72h) incl. reboot, midnight crossover, unclean-restart reap |
| 3 | **SDI-proven** | Physical SDI captured + verified off a real DeckLink card |
| 4 | **Headend-proven** | Accepted by a real station/cableco headend |
| 5 | **Field-proven** | Unattended in production for an agreed duration |

In-code mapping: `real component`→0; `real proof`(w/ boundary)→1; `production-wired`/
`production-supervised`→1 advancing to 2 via the soak; `complete_with_external_dependency`→rung
gated by the named dependency. **Hard public claim boundary preserved:** no app-store /
hardware / legal-FCC / managed-service / live-device claims without separate rung-appropriate
evidence.

### Caption performance reporting and owner acceptance — October 10, 2026

The CivicCast owner defines **99% successful live captioning** as the professional-level reporting
benchmark and **98%** as the professional contractual reporting benchmark. These are the owner's
benchmarks for CivicCast reports, not hard release minimums. The success metric is:

`100 × (1 − discarded caption-input audio seconds ÷ total active channel-audio seconds)`

Report each run's window and active-audio denominator by channel and in aggregate, along with
discarded input seconds and the resulting percentage. Report failure and retry counts separately;
retries of the same audio are attempts, not additional discarded audio seconds. Correct captions
that arrive late count as successful coverage. This metric measures input-audio coverage, not
transcript word accuracy.

Neither falling below 98%, an observed gap, nor a demand for zero loss is an automatic caption
release blocker. Consult Scott before treating any caption-performance result as a blocker. Scott
decides whether to release, including when a result falls below 98%. The observed HOST run is
recorded in the approximately 45-hour snapshot `docs/ops/beta11-host-caption-performance-2026-10-10.md`
and its 48-hour extension `docs/ops/beta11-host-caption-performance-48-hour-2026-10-10.md`; S11
defines proof and reporting behavior.

---

## 6. Entity model (shared vocabulary)

**Existing — reuse:** Egress (`EgressConfig`, `EgressSinkSpec` [udp-ts/srt/rtmp/file/local-ts +
ndi/sdi — under S15 each kind is realized by a GStreamer sink: `ndisink` / `decklinkvideosink`],
`EgressState`, `EgressCommand` [start/stop/reload/drain], `EgressHealthSample`, `EgressProofEvent`,
`EgressSourcePlan/Segment`, `ChannelAutomationSettings/Rollup`, `HeadendProfile`,
`HeadendReadinessRollup`, `ComplianceProbeResult`, `SdiReadiness`, caption embed +
`EgressCaptionDecodeBackProof`; the legacy `SdiRelayStatus`/`NdiRelayStatus` relay shapes are
superseded by engine-sink readiness `SdiSinkStatus`/`NdiSinkStatus` — S2 §3 / S15 §4); ProgramLog
(`ProgramSlot`, `SlotOccurrence`); Schedule (`Asset`, `ScheduleItem`, `Chapter`,
`ScheduleConflictError`); Facility (`RouterEndpoint/Inventory`, `RouterTakePlan`,
`VirtualRouterPanel`); Live (`LiveSession`, `LiveSource`, `RecordingTarget`); CG
(`EmergencyOverlay`, templates); Auth (`OperatorRole`×5); Identity (`StationProfile`,
`StationAppConfig`, `ChannelBranding`). **Coded-but-unwired:** `request_live_takeover/handback`,
`build_live_takeover_source_plan`.

**Net-new** (names match the section specs exactly):
- S4: `CommitToAirPlan`, `CommitToAirReport`, `OnAirLockState`, and `ScheduleConflict` — a **data
  model** distinct from the existing `ScheduleConflictError` exception.
- S5: `TakeoverSession`, `ManualRouteState`, route-take audit record.
- S8: `AlertRule`, `AlertChannel`, `AlertEvent`, `AlertEventDelivery`, `RuntimeSafeToAirStatus`,
  `ChannelRuntimeStatus`, `SystemResourceSample`, `SystemSelfTest`.
- S1: `StationBoxProfile` (field `system_ram_total_gb` is the AI-default RAM axis).
- S6: CG persistence — `Board`, `Zone`, `Bulletin`, `FeedSource`.
- S7: `MediaIngestJob`, `TranscodeJob`, `AssetReadiness`, `WatchFolderConfig`, `AssetRetentionPolicy`.
- S11: per-sink `loudness_target_lufs` on `EgressSinkSpec`; `EasCapSource`, `EasDisplayMode`.
- S12: `AppBuildRecord`, `StoreSubmissionMetadata`.
- S13: `ModelTier`, `FeatureModelRegistry`, `AiModelConfiguration`.
- S10: `ProofRung`, `ProofBoundary`, `CapabilityProof`.
- S9: `RelayProcessIdentity`, `UniformPacingLatch` (+ relay-pid fields on `EgressStateRow`).
- S3: `CommissioningCheckReport`, `ChannelCommissioningSetup`, `CommissioningProofRun`,
  `CommissioningReport` — **config/state-file only, no migration**.

---

## 7. EAS posture (resolved — software in, certified relay at headend)

For a PEG channel carried by a cable operator, the **operator is the legal EAS Participant** and
performs mandatory Part 11 relay at the headend across the whole lineup (incl. PEG). Cablecast
itself is **not** an EAS device. So our posture is **parity-neutral**:

- **Build (software, no certification needed):** CAP/IPAWS + NWS weather + AMBER ingestion and
  on-channel display (crawl/overlay/forced slate); optional SAME encoder for local origination.
- **Document the legal boundary, never overclaim:** mandatory Part 11 relay needs FCC-certified
  equipment (§11.34) + two-source RF monitoring (§11.52) — that lives at the operator's headend
  (DASDEC/Monroe), exactly as with Cablecast. The product **never** claims "provides EAS" or
  "EAS-compliant"; it says "ingests/displays CAP/IPAWS + weather/AMBER (informational); mandatory
  EAS relay is the cable operator's headend." (Sources: FCC 47 CFR Part 11; FEMA IPAWS.)

---

## 8. AI / model selection (resolved)

- **Gemma 4 is current** (launched 2026-04, 12B added 2026-06-03) and **Apache 2.0** — confirmed
  on Ollama's `gemma4:12b` license blob, the HF `google/gemma-4-12B-it` tag (ungated, no
  click-through), and Google's license page (full Apache text). The earlier "restrictive custom
  license" risk is **cleared**; commercial use + redistribution OK. (Caveat: Google publishes a
  separate Prohibited Use Policy; civic-broadcast use doesn't implicate it — link it in docs.)
- **Default = adaptive.** Summarization is a long-context task and that's where the small model
  fails (MRCR long-context: E4B 25.4 vs 12B 43.4, nearly double). So **default the summary model
  to `gemma4:12b` (QAT int4, ~7GB)** where hardware allows — Google states 16GB system RAM runs
  it (CPU-batch ~5–9 tok/s, fine for background summaries) — and **fall back to `gemma4:e4b` on
  8GB-class boxes.** `doctor`/`StationBoxProfile` picks per detected memory.
- **Translation stays 4B-class** (`translategemma:4b`) — latency-sensitive, EN↔ES gap is modest,
  a specialized 4B can match a general 12B.
- **Captions stay whisper-large-v3** (local processing can reduce metered captioning costs).
- **"Operator always chooses" is a hard principle — and it isn't built yet.** Build a per-feature
  model registry exposing three tiers: **local Ollama** (e4b/12b/26b/Apache alternates) →
  **Ollama Cloud** (`gemma4:31b-cloud`) → **OpenRouter** (frontier mid-tier: Gemini 2.5 Flash /
  Haiku 4.5 / GPT-5 mini). **Default stays local** (no required hosted AI service for default
  operation); hosted is opt-in escalation. **(S13)**

---

## 9. Reconciliation with the existing spec corpus

- **Cable status → RESOLVED: cable is core.** Supersede `spec.md §8.21/§3.3/§20.4` (deferred
  separate-repo add-on). Annotate those sections with pointers to this master; the
  `civiccast-cable` separate-repo concept is retired (its contents are in `egress/`/`cable/`/
  `programlog/`).
- **Appliance posture → RESOLVED: embrace it for the PEG profile.**
- **CG multi-zone → RESOLVED: build it** (supersede the `§8.10` cut; the parity addendum Gap 4
  already re-added it). **(S6)**
- **OTT → RESOLVED: build generic multi-platform now** (supersede the `§8.16` cut). **(S12)**
- **Playout engine → RESOLVED: GStreamer** (LGPL core + gst-plugins-rs MPL — Apache-clean) replaces
  the per-segment ffmpeg-relay: fixes #151 by design (persistent pipeline + GstInterpipe hot-swap),
  unifies all outputs (IP-TS/SRT/HLS/NDI/SDI), CG via compositor+overlays (CPU) + WPE HTML.
  Orchestration above the engine (programlog/scheduler/headend/channel/health/S4/S5) ports over
  unchanged. **CasparCG = optional GPLv3 premium-rich-CG co-process only, not the engine.** **(S15)**
- **License → RESOLVED: stay Apache-2.0** (GPLv3 rejected — would deter the integrators/MSPs who
  deploy for non-technical non-profits, fragment the CivicSuite family, and wouldn't even stop
  SaaS-resale; the engine's GStreamer deps are LGPL/MPL, Apache-clean).
- **Proof vocab → RESOLVED: unify under §5**, claim boundary preserved.
- **Disposition of old spec → annotate, don't rewrite.** 3.0 governs the PEG/cable profile; add a
  header banner + inline "SUPERSEDED by 3.0 §N" notes at the contradicted sections; leave the
  rest of `spec.md` intact as history.

---

## 10. Build order (reliability-first)

> **BUILD STATUS — as of 2026-06-14, branch `work/3.0-gstreamer-engine`.**
> **Cursor: step 4 (S8 operational alerting) is NEXT.** Steps 0–3 are code-complete,
> machine-verified, and committed; the step-3 (S9) close — `/walkthrough` + `/audit-team`
> to 0/0/0/0/0 + push — is in progress. SDI output (step 5) is descoped (IP-only).
> Markers: ✅ done · ▶ next · ⏳ pending. This list is the canonical progress tracker —
> keep it current at each step boundary.

0. ✅ **DONE — PROTOTYPE FIRST — GstInterpipe seamless raw-domain source-swap on real content (S15).** The
   architecture's risk gate: prove glitch-free program↔filler↔live↔slate swaps with continuous TS
   *before* committing the re-platform. *Result:* input-selector hot-swap PASS — 40 swaps, 0 MPEG-TS
   CC errors, flat RSS, clean exit. Swap mechanism chosen: `input-selector` (+ `fallbackswitch`),
   core GStreamer; GstInterpipe deferred/optional.
1. ✅ **DONE & PUSHED — Build the GStreamer playout engine (S15)** — persistent pipeline + hot-swap;
   all sinks (IP-TS/SRT/HLS/NDI; **SDI descoped**); CG-lite overlays land later (step 7). Replaces the
   ffmpeg-relay and **fixes #151 by design**; the program log/scheduler drive it via the base
   `EgressDaemon` control plane. *Re-opened and finished with nothing deferred* (engine + supervisor
   integration; seamless content-reload; live source leg; WSL-gated live harness). Re-audited to
   0/0/0/0/0. Pushed at `cb634db`. Report: `Desktop\Code\civiccast-stage1-report.md`.
2. ✅ **DONE & VERIFIED — 4-hour soak run on the GStreamer engine (the #151 re-test):** clean continuity
   across program/filler boundaries — first machine-confidence. *Result:* proxy 4h soak (160 swaps,
   0 CC) **plus** a real-engine 1h soak (`worker.py` + 39 reloads, realtime is-live path) —
   **0 CC over 2,280,436 packets** (whole-file scan), worker_rc 0, RSS flat. The full **24h
   machine-proven (rung 2)** run remains **step 13** (END of build, per Scott — no 24h run mid-build).
   Report: `Desktop\Code\civiccast-stage2-soak-report.md`.
3. ✅ **DONE (close in progress) — Reliability hardening (S9):** process-identity + supervision adapted
   to the GStreamer pipeline lifecycle + co-process management; pacing latches; schema-drift health;
   proof-event caps. *Shipped S9-1…S9-6 (`90deaf3..0b04153`):* `UniformPacingLatch` + TOCTOU-safe
   `verify_and_kill_process`; schema-currency surface + proof-event churn cap (10k/ch) + migration
   `0038`; boot co-process reap → durable proof event (IP-only rescope; durable pid tracking → step 7);
   **output-stall watchdog** (engine quits on a silent output flatline → daemon relaunch on the
   committed source); **latch-gated crash-relaunch back-off** + the **S8 escalation hook seam**;
   **schema-drift health badge** + NDI readiness-probe TTL cache (SDI-parity gap). Report:
   `Desktop\Code\civiccast-stage3-s9-report.md`. Close = `/walkthrough` + `/audit-team` 0/0/0/0/0 + push.
4. ▶ **NEXT — Operational alerting + runtime safe-to-air (S8).** Wires the S9-5b restart-escalation
   proof event (and other health signals) into operator alert dispatch + a runtime safe-to-air gate.
5. ⏳ **SDI output path — DESCOPED (Scott, 2026-06-14): 3.0 is IP-only.** The headend handoff is IP-TS;
   no new 3.0 SDI-output (`decklinkvideosink`) engine work. The v2.x BYO-SDI relay code stays as
   shipped. A local SDI feed, if ever needed, returns as a later hardware-gated item — not this build.
6. ⏳ **Commit-to-Air gate (S4); Software Force Matrix wiring (S5).**
7. ⏳ **CG: WPE rich-CG + optional CasparCG premium co-process (S6/S15).** (Durable co-process pid
   tracking — the `0038` columns — lands here, its first real device-co-process consumer.)
8. ⏳ **OTT apps (S12)** — build-to-spec + machine-verify the CODE only; no store submission / accounts
   (Scott, 2026-06-14).
9. ⏳ **Production & Control Room (S16, TSR over OBS/vMix/ATEM) + Remote Contribution (S17, VDO.Ninja)** — optional tiers.
10. ⏳ **Captions CEA-708 + per-sink loudness (S11); EAS software layer (S11).**
11. ⏳ **AI model-selection surface (S13); Analytics / Audience Measurement (S14).**
12. ⏳ **Commissioning wizard per-tier component install (S3); Media lifecycle (S7).**
13. ⏳ **24h machine-proven soak (rung 2) — RUN AT THE END (per Scott; no time mid-build):** the full
   unattended run with a Scott-approved reboot + the `STARTING`-state directive fix; clean TSDuck
   across the whole window. (The 4-hour re-test in step 2 is the mid-build confidence check.)
14. ⏳ **Headend-proven (rung 4): first-station beta** — at the LPM lab; the CODE must be there and
   tweakable. The ONLY outside test run before LPM is the clean-machine install on the tester machine.

**Parity closures (S18) fold into the step that owns each domain** — not a separate phase.
Migration numbers below are the **as-built on-disk numbers** for shipped gaps and the **next-free
numbers after the head (`0054`)** for unshipped gaps (reconciled to disk 2026-06-18; see S18 §6 /
RECONCILIATION.md):
step 6 (S4/S5) gains query-driven auto-scheduling + block/daypart (gaps 1,4, `0043` — shipped);
step 7 (CG) gains CG depth (gap 6, `0045` — shipped); step 9 (S16) gains GPI/serial device control
(gap 8, extends `0047` — shipped); step 10 (S11) gains SAP/descriptive audio (gap 9, `0052` —
shipped) + EAS attention-tone stripping (gap B); step 11 (S14) gains as-run/EPG/franchise reporting
(gap 5, `0055` — SHIPPED 2026-06-18); step 12 (S7) gains custom metadata fields
(gap 3, `0054` — shipped), scheduled recording (gap 2, `0056_scheduled_recording` — SHIPPED 2026-06-18 as the sibling slot off `0055`; merge revision `0060_recording_paywall_merge` unifies the heads), underwriting spot
management (gap 10, `0057` — SHIPPED 2026-06-18), meeting-agenda integration (gap A, `0058` — SHIPPED 2026-06-18),
and the optional subscription paywall (gap C, `0059` — SHIPPED 2026-06-18, V1.x-optional, default OFF — Stripe-hosted Checkout, PCI SAQ-A scope). With S21 (`0056` sibling slot) also shipped 2026-06-18, the chain head is the data-free merge revision `0060_recording_paywall_merge`.
Multi-site federation (gap 7) remains **V2**.

---

## 11. Section-spec index (the layered set)

Detailed build specs under `docs/spec/3.0/sections/`. "Extend" = code largely exists; "net-new"
= it does not.

| # | Section file | Disposition |
|---|---|---|
| S1 | `S1-reference-station-and-stationboxprofile.md` | extend (`doctor`, hardware tiers) |
| S2 | `S2-headend-handoff-matrix.md` | extend (`headend.py` + readiness) |
| S3 | `S3-commissioning-wizard.md` | extend (11-screen installer) |
| S4 | `S4-playout-core-and-commit-to-air.md` | net-new over programlog |
| S5 | `S5-software-force-matrix.md` | wire existing + net-new audit |
| S6 | `S6-cg-bulletin-board-designer.md` | net-new persistence + designer |
| S7 | `S7-media-lifecycle-and-readiness.md` | extend |
| S8 | `S8-health-alerting-support-updates.md` | net-new alerting |
| S9 | `S9-reliability-and-process-identity.md` | net-new (audit watchlist) |
| S10 | `S10-field-certification-and-proof-ladder.md` | formalize §5 |
| S11 | `S11-captions-loudness-eas-compliance.md` | extend + net-new |
| S12 | `S12-ott-apps.md` | net-new (off `app_platform/`) |
| S13 | `S13-ai-model-selection.md` | net-new wiring |
| S14 | `S14-analytics-audience-measurement.md` | extend analytics module + net-new reports |
| S15 | `S15-playout-engine-gstreamer.md` | **net-new engine** (GStreamer; replaces the ffmpeg-relay) |
| S16 | `S16-production-control-room.md` | net-new (TSR control over OBS/vMix/ATEM) — optional tier |
| S17 | `S17-remote-contribution.md` | net-new (VDO.Ninja remote guests) — optional tier |
| S18 | comparative capability appendix | archived planning record with the reconciled gap list and closures. All software gaps that ship migrations are now ON DISK: `0043`/`0045`/`0052`/`0054`/`0055`/`0056`/`0057`/`0058`/`0059` plus merge revision `0060_recording_paywall_merge`. |
| S19 | `S19-scheduling-automation.md` | net-new — query-driven auto-schedule + block/daypart (`0043` — shipped) |
| S20 | `S20-accessibility-ada-title-ii.md` | net-new — WCAG 2.1 AA / ADA Title II (no migration; release gate) |
| S21 | `S21-scheduled-recording.md` | net-new — scheduled recording from inputs/streams (`0056_scheduled_recording` — SHIPPED 2026-06-18 as the sibling slot off `0055`; the LAST S18 parity gap — step-12 flipped to `built` with this ship) |
| S22 | `S22-custom-metadata-fields.md` | net-new — user-defined custom fields (`0054` — shipped) |
| S23 | `S23-asrun-epg-franchise-reporting.md` | net-new — as-run + EPG export + franchise hours (`0055` — SHIPPED 2026-06-18) |
| S24 | `S24-underwriting-spot-management.md` | net-new — underwriting spots + affidavits (`0057_underwriting_spots` — SHIPPED 2026-06-18) |
| S25 | `S25-meeting-agenda-integration.md` | net-new — agenda + video-timecode chapters (`0058_meeting_agenda` — SHIPPED 2026-06-18; chain HEAD is now `0060_recording_paywall_merge` after S21 + the merge) |
| S26 | `S26-subscription-paywall.md` | net-new — Stripe paywall (`0059_paywall_access` — SHIPPED 2026-06-18, **optional / V1.x, default OFF**, PCI SAQ-A scope; chain HEAD is now `0060_recording_paywall_merge` after S21's `0056` sibling shipped 2026-06-18 and the merge revision unified the heads) |
| S27 | `S27-agenda-import-bridge.md` | Phases 1-3 (Legistar/PrimeGov/CivicClerk — plain-HTTP vendor endpoints, no migration of their own) retroactively documented; Phase 4 (`js_portal` — crawl4ai/Playwright for JS-hydrated portals, optional `civiccast[agenda-js-import]` extra, no migration) SHIPPED 2026-08-25 |
| — | `THIRD-PARTY-LICENSES.md` | dependency licensing manifest + codec-patent caveat |

> Parity closures `0045` (CG depth) and `0052` (SAP/descriptive audio) are **in-section extensions** of S6 and S11 (their owning sections), not standalone files. (Both shipped on disk; numbers reconciled to the as-built chain 2026-06-18.)

> **Engine = GStreamer (S15), Apache-clean (LGPL/MPL).** It replaces the ffmpeg-relay *engine layer*
> only; all orchestration above it (programlog/scheduler/headend/channel/health/S4/S5) ports over.
> CasparCG is an optional GPLv3 premium-CG co-process, not the engine. All 26 section files exist
> under `sections/`; S8 was rewritten to full depth. Cross-document consistency (entity names, the
> five real auth roles, proof rungs, the single global alembic chain `0038`+, per-sink loudness, the
> S4↔S5 runtime-arbitration split) is reconciled in **`RECONCILIATION.md`**, which is **binding**
> where a section disagrees. S3 adds no migration (commissioning is config/state-file).

---

## 12. Global gates

**Release-readiness (every release):** clean Windows install from artifact; commissioning wizard
completes (incl. headend/SDI once S3 lands); 24h unattended soak w/ kill+restart+**reboot**;
72h candidate soak before broad handoff; **physical SDI proof** (rung 3); TSDuck verify on
UDP-TS profiles; failure scenarios (missing media, bad feed, failed live input, full disk, clock
drift, encoder crash, **unclean-restart relay reap**); Playwright walkthrough (installer, console,
CG, force matrix, schedule commit, portal, OTT config, support bundle); `audit-lite` after fixes,
full `audit-team`+`walkthrough` after major UI — **every audit reaches 0/0/0/0/0.**

Caption performance is reported under §5 and S11. Caption percentages and loss observations are
not hard release minimums; below-98% results, observed gaps, and zero-loss demands do not create an
automatic release block. Consult Scott before treating caption performance as a blocker; release
below 98% remains the owner's decision.

**Station acceptance:** operator installs+commissions without terminal work; **runs the three PEG
channels (public/education/government) concurrently to SDI**; schedules a day and commits to air;
interrupts with live and returns safely; shows bulletins in gaps; publishes VOD from a recorded
meeting; **the box calls for help when it goes off-air unattended**; survives an unattended reboot;
OTT apps present; exports a support bundle that explains failures without guessing.

---

## 13. Open decisions for Scott (all parity-relevant items are now IN — these are the remainder)

> **Comparative capability pass complete (2026-06-14).** A
> three-way reconciliation (our discovery + code-grep + a Gemini deep-research run) closed the
> remaining software-defined PEG automation gaps. Decisions locked there (RECONCILIATION D14–D19): multi-site
> federation = **V2**; SCTE-35 = **not needed** (Cablecast lacks it); underwriting spot management +
> agenda integration = **IN**; EAS attention-tone stripping = **added**; paywall = **V1.x-optional**.

1. **Cable-grade OS — RESOLVED (soak + code check).** "Single Windows PC" means **Windows 11 +
   WSL2 (Ubuntu 24.04)** — per ADR 0003 there is *no* native-Windows (non-WSL2) path; the Python
   core is portable but the service/recovery layer (systemd, FHS paths, NATS/Postgres) is
   Linux-shaped. The 24h soak ran on exactly this Windows+WSL2 stack, stable for the full window
   (the FAIL was #151 continuity, not OS/stability). **Decision: promote single commodity
   Windows-PC-via-WSL2 for 24/7 cable; keep native Linux as the alternative; drop spec.md §5.3's
   "native Linux required for cable-grade" caveat.** A true native-Windows (no-WSL2) path is a
   permanently-forked codebase (Windows services, path handling, NATS/Postgres wrappers, separate
   CI) — **not recommended**; WSL2 is the appliance path and the installer bootstraps it
   transparently. (Open only if Scott explicitly wants bare-native-Windows as a separate project.)
   **Superseding note (2026-07-30):** ADR 0021 and the owner-approved native
   Windows execution contracts supersede this rejection. Native Windows is now
   a distinct, first-class product line. The owner retired the WSL2 line on
   2026-08-19 (see ADR 0021 and `docs/releases/release-truth.yaml`); this
   repository ships the native line only.
   **Amendment (2026-08-20):** NATS JetStream was removed from the product; see
   ADR 0023, which supersedes ADR 0001. The in-process broker is now the sole
   event-bus implementation. The NATS/Postgres references above describing the
   "service/recovery layer" and "Windows services, path handling, NATS/Postgres
   wrappers" are retained for historical context but no longer reflect the
   shipped system.
2. **DeckLink hardware — not a blocker.** The SDI output path is built regardless; only the rung-3
   *physical* SDI capture is gated on hardware. Scott will try to source a DeckLink (model TBD), but
   the proof most likely happens at LPM near the end of the build — it does not gate development.
3. **OTT apps:** V1 ships **working apps on every platform** (Roku / Apple TV / Fire TV / Android
   TV / iOS) using a templated public-media app model. This is **full platform coverage, with
   no PEG-automation work deferred**; the only thing left for later is optional custom per-platform
   visual design, which is neither a required software item nor a contract
   requirement. *(Confirmed in scope.)*

Everything else proceeds on the resolutions above.

---

*Implementation does not begin until Scott approves this spec. The 27 section specs are authored
under `docs/spec/3.0/sections/`, the cross-document reconciliation/consistency pass is complete
(`RECONCILIATION.md`, binding), and this master is finalized for review.*
