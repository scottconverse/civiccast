# S11 — Captions (CEA-708), Per-Headend Loudness, and the EAS Software Layer

> **Sprint 3 recorded-caption requirement — October 13, 2026:** Recorded-caption
> processing, selected output languages and completion requirements are station
> choices. English-only operation and disabling recorded-caption processing must
> be supported without a test override or a startup refusal. Selected captions
> still require operator review before their text is published. This supersedes
> blanket requirements that every recording carry both English and Spanish;
> it introduces no numerical caption-performance or release gate. Implementation
> and verification status are tracked in `docs/ops/sprint3-output-completion.md`.

> **Owner decision — October 10, 2026:** use the
> [master's caption performance standard](../civiccast-3.0-station-in-a-box-MASTER.md#caption-performance-reporting-and-owner-acceptance--october-10-2026).
> The 99% and 98% benchmarks are reporting references, not release minimums.
> Scott decides whether to release, including below 98%; consult him before
> treating caption performance as a blocker. Accurate late captions are accepted.
> This supersedes earlier caption gap, percentage, and uninterrupted-soak release
> requirements in this section and in historical specifications.

> Status: **Built for v3.0.0-beta1; physical-headend proof remains external.**
> Part of the CivicCast 3.0 layered spec set. Master:
> `docs/spec/3.0/civiccast-3.0-station-in-a-box-MASTER.md`. This section owns master Gap 8
> (EAS software layer) and Gap 9 (CEA-708 ancillary captions + decode-proof in the live loop;
> per-headend loudness). Disposition per master §11: **extend + net-new.**
> Code-verified against `main @ 69cc676` (the source the 24h soak runs). File:line citations are
> to `C:/CivicCastTester/civiccast/civiccast/...`. Overclaiming is the cardinal sin (master §0).
>
> **Addendum (2026-08-25, narrow scope — do not read as a rung change for this
> whole section):** `feat/cea708-decode-back` closed a *different*, narrower gap
> than the live-loop work this section tracks: S3's commissioning-wizard Screen 10
> output-proof (a one-shot, bounded, operator-triggered check — not the S11a live
> ON_AIR decode loop `caption_proof_worker.py` already runs) previously always
> reported `cea708_verified: null` because no decode-back check was wired into
> *that specific proof run*. It now runs a real embed-through-decode-back check
> (new module `civiccast/installer/cea708_verification.py`) and reports a genuine
> `True`/`False`. See `docs/spec/3.0/sections/S3-commissioning-wizard.md`'s
> 2026-08-25 banner for the full detail. This addendum does NOT claim any change
> to this section's own rung table below (loudness, EAS, or the live-loop rung
> status) — none of that was touched.

This section covers **three compliance subsystems** that share one honest-boundary discipline:

- **(a) Captions** — implement the enum-declared `cea-708` embedding mode and wire decode-back
  proof into the LIVE health loop. Honest about FCC Part 79.
- **(b) Loudness** — add a per-sink loudness *regime* defaulting to **−24 LKFS (ATSC A/85)** for
  cable sinks, alongside today's **−16 LUFS** streaming default. Coordinates with S2.
- **(c) EAS software layer** — CAP/IPAWS + NWS weather + AMBER ingestion and on-channel display
  (crawl / overlay / forced slate); optional SAME encoder for local origination. The **hard legal
  boundary** is documented per master §7: mandatory Part 11 relay lives at the **cable operator's
  headend**; the product **NEVER** claims "EAS-compliant" or "provides EAS." Parity-neutral with
  incumbent PEG platform (which is not an EAS device — master §2.1, §7).

---

## 1. Goal & PEG automation rationale

**Goal.** Make CivicCast's three "is-it-actually-correct-on-air" guarantees real and provable:
(a) captions reach the *emitted* stream and a live decode-back loop reports proof at the declared
boundary; the soak records observed results, including interruptions and recovery;
(b) audio loudness is normalized to the **correct standard for each output's destination** (cable
vs. streaming have different legal/operational targets); (c) the station can ingest official
public-safety alerts and put them on its own channel **as information**, while being scrupulously
honest that the legally mandated EAS relay is not its job.

**PEG automation rationale, per subsystem:**

- **(a) Captions — parity + cost wedge.** The incumbent PEG workflow sells captioning as a **metered cloud service
  (~$12/hr)** (master §2.1). CivicCast already does ASR/captioning locally (whisper-large-v3 —
  master §2.1, "built, and a cost advantage"). The remaining capability gap is not *generating*
  captions — it's *guaranteeing they reach the linear cable stream as CEA-708 ancillary data*, the
  cable-standard caption format. (**FCC Part 79** carriage/quality is the cable *operator's* legal
  obligation, not CivicCast's — see §7's claim boundary. CivicCast delivers captions in the correct
  format and proves it via decode-back; it does **not** certify Part 79.) Today the `cea-708` mode is
  an enum label with no implementation (master §3: "**partial** — CEA-708 mode enum-declared, not
  implemented"). Closing it converts the cost advantage into a true **technical-coverage** claim.
- **(b) Loudness — parity correctness.** A cable headend expects **ATSC A/85 (−24 LKFS)**; a
  streaming/OTT destination expects **−16 LUFS** (the EBU/Apple web convention). incumbent PEG appliance
  encoders normalize to the headend's spec. CivicCast today normalizes everything to −16 LUFS at
  conform (master §3: "**lab** … default −16 LUFS, per-channel"). Shipping one global target to a
  cable sink is a **correctness defect at parity**, not a nicety — it makes the PEG channel
  audibly louder than the rest of the operator's lineup.
- **(c) EAS — scope-neutral, honesty-positive.** the incumbent PEG platform **is not an EAS device** (master §2.1,
  confirmed twice; §7). So building an EAS *display* layer does not chase incumbent PEG platform — it is a
  product differentiator that we must build **without ever overclaiming**. Parity is preserved by
  matching the incumbent PEG platform's posture (no certified relay) while adding informational CAP/weather/AMBER
  display that a community channel genuinely wants. The win is the *honesty*: we document the
  Part 11 boundary so a station never believes CivicCast satisfies its operator's EAS obligation.

---

## 2. Current state (file:line — exists vs net-new)

### (a) Captions
- **`EgressCaptionEmbeddingPlan.mode`** = `Literal["passthrough", "cea-708", "sidecar"]` —
  `egress/caption_embed.py:30`. The `cea-708` value is **declared but never produced**: only two
  embedders exist.
- **`PassThroughCaptionEmbedder`** (`caption_embed.py:67`) — emits no caption args,
  `status="not-verified"`, `not_claimed` explicitly disclaims CEA-708.
- **`SidecarCaptionEmbedder`** (`caption_embed.py:92`) — maps a subtitle sidecar input with
  `-c:s copy`; `not_claimed[0]` says "does not claim CEA-708 ancillary caption embedding."
- **No `Cea708CaptionEmbedder`** exists. (Grep for the class returns the two above only.)
- **Decode-back proof** — `evaluate_caption_decode_back(...)` (`caption_embed.py:125`) +
  `EgressCaptionDecodeBackProof` (`caption_embed.py:41`) + timed-text parser
  (`parse_caption_cues_from_timed_text`, `caption_embed.py:192`). **Reachable only via CLI**:
  `civiccast egress caption-decode-proof` (`cli.py:1393`), an offline, operator-run, file-in
  file-out utility. **Nothing calls it from a running channel.**
- **Health seam exists but is unfed.** The egress daemon accepts a
  `caption_status_provider: CaptionStatusProvider | None` (`daemon.py:62,94,110`) and stamps
  `caption_status` onto every `EgressHealthSample` (`daemon.py:385-389`), defaulting to
  `"not-verified"` when the provider is `None`. The provider is **never constructed by the live
  service** — grep shows only the type alias and the daemon ctor reference; no runtime decode loop
  feeds it. So `caption_status` is `"not-verified"` for the life of every live channel today.
- **Persistence:** `caption_status` column on `egress_health_samples`
  (`egress/migrations/versions/0021_egress_health_caption_status.py:21-40`,
  check-constrained to `('not-verified','on')`); surfaced through `store.py:331,440`,
  `router.py:119,180`.

### (b) Loudness
- **One target, −16 LUFS, per-channel.** `EgressConfig.loudness_target_lufs: float = -16.0`
  (`egress/models.py:320`), `loudness_tolerance_lufs … = 2.0` (`:321`). Durable defaults
  `-16.0 / 2.0` on `EgressConfigDb` (`egress/models.py:166-167`).
- **Conform applies it** in `SourcePreparer._prepare_segment` → `build_conform_source_args`,
  emitting `-af loudnorm=I={target}:LRA=11:TP=-1.5` (`egress/preparer.py:195`), gated by a probe.
  The probe (`check_streaming_loudness`, `stream/loudness.py:26`) hardcodes
  `standard="ITU-R BS.1770 / EBU R128"` and operator text "Normalize stream audio to −16 LUFS"
  (`loudness.py:32,91-98`). It is **streaming-flavored throughout**.
- **`EgressSinkSpec` has NO loudness fields** (`egress/models.py:94-147`): `kind,label,uri,
  secret_ref,latency_ms,extra_output_args` only. `EgressSinkKind` (`models.py:41`) =
  `srt/rtmp/local-ts/udp-ts/file/sdi`. There is no per-sink notion of "this is a cable
  destination, normalize to A/85."
- **Live encoder is `-c:a copy`** (master §3, §3-table): loudness is applied at *conform*, not at
  the persistent encoder, so per-sink loudness has to be a **conform-time decision keyed by which
  sink(s)/headend the channel feeds** (not a post-encode re-loudness). This is the load-bearing
  design constraint and the S2 coordination point.
- **HeadendProfile carries no loudness field** (`egress/headend.py:43-59`); cable profiles set
  `canonical_profile` (codec/rate/audio) but say nothing about loudness target.

### (c) EAS
- **No CAP/IPAWS/NWS/AMBER/SAME code exists anywhere** in the tree. (Targeted grep for `IPAWS`,
  `alerts.weather.gov`, `same_encoder`, `EAS relay`, `Part 11` → **no matches**.) This subsystem
  is **net-new**.
- **What exists is display-only and manually triggered:** `EmergencyOverlay`
  (`cg/models.py:32`), the public read endpoint `GET /api/public/cg/emergency-overlay`
  (`cg/router.py:122-131`, returns a *deterministic mock*), `build_emergency_overlay`
  (`cg/service.py:89`), the egress proof bridge `build_cg_overlay_egress_proof` /
  `…clear…` (`egress/cg_bridge.py:60,93`), and the FFmpeg overlay filter plan
  `build_branding_filter_plan` (`egress/branding.py:52`) which orders an `alert`-kind zone last so
  it renders on top (`branding.py:101-113`).
- **The honesty discipline is already in code and must be the template for S11:** every overlay
  artifact stamps `eas_claim="not_eas"` (`cg_bridge.py:39,55,83`; `branding.py:48`) and carries
  `not_claimed` lines that explicitly disclaim "EAS origination, EAS certification, CAP relay, or
  alert authority" (`cg_bridge.py:16-20`) and "FCC EAS origination, ENDEC control, or EAS
  certification" (`branding.py:95-96`). **There is no ingestion side** — nothing fetches CAP,
  weather, or AMBER; the overlay content is operator-typed or mock.
- **CG `alert` zone + `weather` feed kind already modeled:** `ZoneKind` includes `"alert"`
  (`cg/models.py:12`), `FeedKind` includes `"weather"` (`cg/models.py:14`), and the standard
  templates reserve a `(lower, alert)` region (`cg/service.py:41,53`). S11 ingestion feeds these,
  it does not invent the display surface.

**Net-new vs extend summary:** (a) extend (`cea-708` embedder is net-new code behind an existing
enum; decode loop is net-new wiring of an existing seam); (b) extend (per-sink fields + regime
selection over existing conform path); (c) **net-new subsystem** (`civiccast/eas/`) feeding the
existing CG/overlay display.

---

## 3. Entities / data model & migrations (reuse master §6 names)

Master §6 net-new for S11: **`EasCapSource`**, **`EasDisplayMode`**, **per-sink
`loudness_target_lufs` on `EgressSinkSpec`** (D6; LKFS == LUFS). Full set:

### (a) Captions — no new persisted entity; one new embedder + a live proof loop
- **`Cea708CaptionEmbedder`** (new, `egress/caption_embed.py`) — a third `CaptionEmbedder`
  Protocol impl producing `EgressCaptionEmbeddingPlan(mode="cea-708", …)` with FFmpeg args that
  embed cues as CEA-708 ancillary data in the MPEG-TS (`-c:s copy` is wrong for 708; this path
  uses the closed-caption encoder lane, e.g. mux of EIA-608/708 via the `ccaption`/`mov_text→a53`
  route — exact arg form is an **implementation-time ffmpeg-capability question**, see §6 + §10).
- **Reuse** `EgressCaptionDecodeBackProof` (`caption_embed.py:41`) **unchanged** — it is already
  the right contract; S11 makes it run *live* instead of *CLI-only*.
- **New (persisted) `EgressCaptionProofSample`** — a small rolling table the live decode loop
  writes (channel_id, sampled_at, status PASS/FAIL, matched/expected counts, decoder_name,
  proof_boundary, blocker). Feeds the `caption_status_provider`. Capped/rolling like the existing
  health/proof tables (cross-ref S9 proof-event caps). Migration: ships in S11's loudness/EAS
  revisions `0049_per_sink_loudness`, `0050_caption_proof_samples` and
  `0051_public_safety_eas` (single global alembic chain).

### (b) Loudness — per-sink `loudness_target_lufs` on `EgressSinkSpec` (master §6, D6)
The canonical per-sink field is **`loudness_target_lufs`** on `EgressSinkSpec` (RECONCILIATION D6:
the target moves from `EgressConfig` to `EgressSinkSpec` so a channel's cable sink and streaming
sink can differ). **LKFS == LUFS** (same ITU-R BS.1770 measurement; only the *target value* and
its standard label differ). Add to `EgressSinkSpec` (`egress/models.py:94`):
```
loudness_target_lufs: float | None = None   # None ⇒ inherit channel EgressConfig.loudness_target_lufs
loudness_regime: Literal["streaming", "atsc-a85", "ebu-r128", "inherit"] = "inherit"  # operator-facing UI label: "Loudness Standard"
loudness_tolerance_lufs: float | None = None # None ⇒ regime default
eas_tone_strip_enabled: bool = True          # strip EAS attention tones on internet/OTT egress (FCC §11.31); default ON for web/OTT (srt/rtmp/hls/web) sinks, OFF for cable (udp-ts/sdi) sinks
```
- **Regime defaults (set `loudness_target_lufs` when not explicitly given):** `streaming` →
  **−16 LKFS (==LUFS)** / ±2 (today's behavior, the streaming default); `atsc-a85` → **−24 LKFS
  (==LUFS)** / ±2 (cable default); `ebu-r128` → −23 LKFS / ±2 (broadcast EU, offered for
  completeness); `inherit` → fall back to the channel's `EgressConfig.loudness_target_lufs`
  (back-compat: an un-migrated sink behaves exactly as today).
- **Default by sink kind (S2 coordination):** when a sink is bound to a cable headend profile (the
  `udp-ts` / `local-ts` headend path, or an `sdi` cable feed), the commissioning/headend-apply
  step (S2/S3) **sets `loudness_regime="atsc-a85"` by default**; `srt`/`rtmp` streaming sinks
  default to `streaming`. The *value* never silently changes a stored config — it is written
  explicitly at apply time so it is visible and overridable.
- **Migration `0044_loudness_and_eas`** (single global alembic chain, head `0037` → `0044`; see
  RECONCILIATION migration table) adds `loudness_target_lufs`, `loudness_regime`,
  `loudness_tolerance_lufs`, `eas_tone_strip_enabled` to `egress_sinks` (`EgressSinkDb`, `models.py:177`), nullable with
  server_default mirroring `inherit`/NULL so existing rows are untouched. The **same `0044`
  revision** also creates the EAS tables (§3c) — one migration for both S11 subsystems.
- **`HeadendProfile`** (`egress/headend.py:43`) gains a `recommended_loudness_regime:
  Literal[...] = "atsc-a85"` advisory field so S2's headend presets *carry* the recommendation and
  S3's apply step reads it. (Advisory only — the per-sink field is authoritative.)

### (c) EAS — net-new `civiccast/eas/` package (master §6: `EasCapSource`, `EasDisplayMode`)
- **`EasCapSource`** — a configured alert ingestion source.
  ```
  source_id: str
  kind: Literal["ipaws-cap", "nws-cap", "amber-cap", "manual"]
  source_url: str                      # FEMA IPAWS-OPEN feed / api.weather.gov alerts / state AMBER
  trust_tier: Literal["official", "operator_curated"]
  enabled: bool
  poll_seconds: int (>=30, <=3600)
  geocode_filter: list[str]            # SAME/FIPS or UGC zones this channel cares about
  severity_floor: Literal["minor","moderate","severe","extreme"]
  ```
- **`EasCapAlert`** — one normalized ingested alert (CAP `identifier`, `sender`, `sent`, `status`,
  `msgType`, `scope`, `event`, `urgency`, `severity`, `certainty`, `effective`, `expires`,
  `areaDesc`, `headline`, `description`, `instruction`, `geocodes`). De-duplicated on
  `(sender, identifier)`; supersession honored via CAP `references`. Persisted with `ingested_at`,
  `displayed_at`, `cleared_at`.
- **`EasDisplayMode`** (master §6) — how an alert is shown on-channel:
  `Literal["crawl", "overlay", "forced_slate"]`. Escalation map by CAP severity:
  `minor/moderate → crawl`; `severe → overlay`; `extreme → forced_slate` (operator-overridable per
  source).
- **`EasDisplayDecision`** — the resolved on-air action for an alert on a channel: the chosen
  `EasDisplayMode`, the rendered text, target `EmergencyOverlay`/CG `alert`-zone payload, and
  the **mandatory honesty stamp** `eas_claim="not_eas"` + `not_claimed[...]` (reuse the
  `cg_bridge.py` constant pattern).
- **`SameEncodeRequest` / `SameEncodeResult`** (optional, local origination only) — generates a
  SAME (EAS audio header/EOM) burst for a *locally originated* informational message; **explicitly
  not** a Part 11 relay. Gated behind a config flag default-off and stamped with the loudest
  possible disclaimer (§7).
- **Migration:** the EAS tables (`eas_cap_sources`, `eas_cap_alerts`, `eas_display_decisions`,
  rolling/capped on alerts) ship in the **same `0044_loudness_and_eas` revision** as the per-sink
  loudness columns — one revision in the **single global alembic chain** (not a separate
  `civiccast/eas/migrations` head), per the RECONCILIATION migration table.

---

## 4. API surface (endpoints + auth roles)

Auth uses the existing 5 roles (`auth/roles.py:14-20`: `setup_admin, meeting_operator,
records_clerk, publish_operator, support_admin`) via `require_any_role(...)` (`roles.py:60`),
matching how `cg/router.py` already gates staff routes (`cg/router.py:196,211,245`).

### (a) Captions
| Method/Path | Auth | Purpose |
|---|---|---|
| `GET /api/staff/egress/channels/{id}/caption-status` | `require_any_role("setup_admin","meeting_operator","support_admin")` | Latest live `EgressCaptionProofSample` (PASS/FAIL, counts, last proof time). |
| `GET /api/staff/egress/channels/{id}/caption-proofs?limit=N` | same | Rolling decode-proof history for the channel. |
| (existing) `civiccast egress caption-decode-proof` CLI (`cli.py:1393`) | local | Kept as the offline/forensic tool. |
> No public caption-status endpoint (operator-only). The CEA-708 *mode* is selected at channel
> config (extend the egress config write path, already role-gated).

### (b) Loudness
| Method/Path | Auth | Purpose |
|---|---|---|
| (extend) egress config write (per-sink `loudness_target_lufs` / `loudness_regime` / `loudness_tolerance_lufs`) | `require_any_role("setup_admin")` | Set per-sink loudness regime. |
| `GET /api/staff/egress/channels/{id}/loudness-plan` | `require_any_role("setup_admin","support_admin")` | Show, per sink, the resolved standard + target + last measured LUFS/LKFS. |
> No new public surface. The conform path consumes the resolved per-sink target internally.

### (c) EAS
| Method/Path | Auth | Purpose |
|---|---|---|
| `GET /api/staff/eas/sources` / `POST` / `PATCH /{source_id}` | `require_any_role("setup_admin")` | CRUD `EasCapSource`. |
| `GET /api/staff/eas/alerts?active=true` | `require_any_role("setup_admin","meeting_operator","support_admin")` | Ingested + active alerts. |
| `POST /api/staff/eas/alerts/{id}/display` | `require_any_role("setup_admin","meeting_operator")` | Operator confirms/forces an `EasDisplayMode` on a channel (audit-logged actor). |
| `POST /api/staff/eas/alerts/{id}/clear` | same | Clear an on-air alert (audit-logged). |
| `POST /api/staff/eas/same-encode` | `require_any_role("setup_admin")` | Optional local-origination SAME burst; **disabled unless config flag on**; returns disclaimer. |
| (existing) `GET /api/public/cg/emergency-overlay` (`cg/router.py:122`) | public | Stays; EAS pipeline now *populates* its real source instead of the mock. |
> Operator-in-the-loop default: severe/extreme alerts may auto-escalate display per `EasCapSource`
> policy, but the **`/display` and `/clear` actions and the auto-escalation toggle are role-gated
> and audited**. Public APIs never expose anything labeled "EAS."

---

## 5. Operator UI surface

- **(a) Captions — `ChannelOpsScreen` (existing operator console).** Per-channel caption chip:
  `Captions: on` only when the **live decode-back proof** is PASS; otherwise `not verified`
  (drives off `caption_status` from the health sample — `router.py:180`). A "Caption proof"
  drawer shows the rolling `EgressCaptionProofSample` history (matched/expected, last decoder,
  blocker). Channel config gains a **caption embedding mode** selector
  (`passthrough / sidecar / cea-708`) with inline help that selecting `cea-708` still shows
  `not verified` until the first live decode-proof passes.
- **(b) Loudness — channel/sink config + `SystemHealthScreen`.** Each sink row shows its
  **loudness regime** (Cable −24 LKFS / Streaming −16 LUFS / Broadcast −23 LUFS / Inherit) with a
  one-line standard label and the last measured value. Cable sinks default to A/85 with a visible
  "recommended by headend profile" note (from S2's `recommended_loudness_regime`). Health surfaces
  per-sink last-measured loudness next to its target.
- **(c) EAS — net-new `EasScreen` (operator console).** Three panes:
  1. **Sources** — list/add/edit `EasCapSource` (IPAWS/NWS/AMBER/manual), geocode filter, severity
     floor, poll interval, enable toggle.
  2. **Active alerts** — ingested alerts with severity, area, expiry; per-alert **Display
     (crawl/overlay/forced slate)** and **Clear** actions; shows which channel is currently
     showing what.
  3. **Posture banner (always visible, non-dismissible):** "CivicCast displays public-safety
     information (CAP/IPAWS, NWS weather, AMBER). **It is not an EAS device and does not perform
     the legally mandated EAS relay** — that is your cable operator's certified headend equipment
     (FCC Part 11)." This banner is a **hard UI requirement**, mirroring the `not_claimed` stamps
     already in code (`cg_bridge.py:16-20`, `branding.py:95-96`).
  - SAME-encoder controls are hidden unless the flag is on, and when shown carry the same banner
    plus "local informational tone only — not a Part 11 EAS transmission."

---

## 6. Behavior / algorithms

### (a) CEA-708 embed + live decode-back proof
1. **Embed (conform/encode boundary).** `Cea708CaptionEmbedder.build_plan` produces FFmpeg args
   that carry the channel's caption cues as **CEA-708 ancillary captions** in the emitted MPEG-TS.
   Because the live persistent encoder is `-c:a copy`/stream-copy (master §3), captions are
   embedded **at conform** (alongside the existing `loudnorm` step in `build_conform_source_args`,
   `preparer.py:171`) or via a dedicated caption-mux pass — chosen at implementation time based on
   the station's ffmpeg build capability (the `cea-708` arg form is the one open ffmpeg question,
   §10). The plan keeps `status="not-verified"` until proven (never assert from intent).
2. **Live decode-back loop (net-new wiring of the existing seam).** A periodic worker, per ON_AIR
   channel: tap a short segment of the *emitted* stream → decode its captions → call
   `evaluate_caption_decode_back(expected_cues, decoded_cues, …)` (`caption_embed.py:125`) →
   persist an `EgressCaptionProofSample` → expose a `caption_status_provider` closure that returns
   `"on"` iff the most-recent sample is PASS within a freshness window, else `"not-verified"`.
   Inject that provider into the daemon (`daemon.py:94`) so every `EgressHealthSample` carries a
   **proven** `caption_status` (today it is hardwired `"not-verified"` because the provider is
   `None`).
3. **Fail-closed.** No expected cues → `EGRESS_CAPTION_DECODE_BACK_NO_EXPECTED_CUES` →
   `not-verified` (already the contract, `caption_embed.py:139-148`). Mismatch → FAIL →
   `not-verified` + a health condition S8 can alert on (cross-ref).

### (b) Per-sink loudness regime
1. **Resolve target per sink:** `effective_target = sink.loudness_target_lufs or REGIME_DEFAULT[
   sink.loudness_regime]` with `inherit` → `EgressConfig.loudness_target_lufs` (the channel-level
   field). Tolerance likewise (`loudness_tolerance_lufs`). LKFS == LUFS — same BS.1770 meter.
2. **Conform decision:** the channel feeds one canonical prepared asset. If **all** sinks share a
   regime, conform once to that target (today's single-pass path, `preparer.py:_prepare_segment`).
   If sinks disagree (e.g. a cable −24 LKFS sink *and* a streaming −16 LUFS sink on one channel),
   the resolver either (i) conforms to the **most-conservative shared target and re-loudness per
   sink only where the sink encoder is not stream-copy**, or (ii) — preferred for V1 simplicity —
   **requires per-channel single-regime** and surfaces a config validation error advising a
   separate channel per loudness domain. **This is an Open Decision for Scott (§10).**
3. **Measurement/labeling:** `check_streaming_loudness` is generalized to `check_loudness(...,
   standard_label)` so the result reports `"ATSC A/85 (BS.1770 / −24 LKFS)"` vs `"EBU R128 /
   −16 LUFS"` and the operator text matches the regime (no more hardcoded "−16 LUFS"
   string at `loudness.py:97`). LKFS and LUFS are the same BS.1770 measurement; only the **target**
   differs — so this is a target/label change, not a new meter.

### (c) EAS ingestion → display
1. **Ingest (poll loop per enabled `EasCapSource`):** fetch IPAWS-OPEN CAP / `api.weather.gov`
   alerts / state AMBER CAP; parse CAP 1.2; filter by `geocode_filter` (SAME/FIPS/UGC) and
   `severity_floor`; de-dup on `(sender, identifier)`; apply supersession via `references`; drop
   expired (`expires` past). Persist `EasCapAlert`. **Fail-closed:** a fetch/parse error never
   fabricates an alert and never clears an existing one; it surfaces a source-health condition
   (S8).
2. **Decide display mode:** severity→`EasDisplayMode` escalation (§3), operator-overridable.
   `extreme` → `forced_slate` only if the channel/operator policy allows pre-emption; otherwise
   highest non-pre-empting mode + operator notification (S8). Default posture: auto-escalate severe+
   to display, but require operator confirm for `forced_slate` unless the source is configured
   "official + auto-forced."
3. **Render via the EXISTING display path — do not build a parallel one.** An accepted display
   decision populates the real `EmergencyOverlay` (`cg/models.py:32`) / CG `alert`-kind zone
   (`cg/service.py` zone `emergency-alert`) and rides the existing
   `build_cg_overlay_egress_proof` (`cg_bridge.py:60`) + `build_branding_filter_plan`
   (`branding.py:52`, alert zone rendered last) to the linear/SDI/UDP output, and the public
   `GET /api/public/cg/emergency-overlay` (`cg/router.py:122`) for the portal. Every artifact keeps
   `eas_claim="not_eas"`.
4. **Clear:** on expiry or operator action → `build_cg_overlay_clear_egress_proof`
   (`cg_bridge.py:93`), reset the CG alert zone `active:false`, record `cleared_at`.
5. **SAME (optional, off by default):** generate a SAME header+EOM AFSK burst for a **locally
   originated informational** message only; never relays an ingested federal alert; never claims
   Part 11. Hard-gated by config flag + disclaimer.

---

## 7. Proof tier: current rung + how to advance + honest claim boundary

Ladder per master §5 (0 Contract → 1 Lab → 2 Machine → 3 SDI → 4 Headend → 5 Field).

| Subsystem | Current rung (today) | Target after S11 | How to advance |
|---|---|---|---|
| (a) CEA-708 captions | **Partial / rung 0** — enum-declared, decode-proof CLI-only, `caption_status` always `not-verified` live | **Rung 1 (Lab)** then **rung 2 (Machine)** via observed run evidence | Implement embedder → run live decode loop against loopback/file emitted stream at `proof_boundary = egress-caption-embed-to-emitted-stream-decode-back` → soak reports observed proof/status results and caption-input coverage, including interruptions and recovery. No uninterrupted-`on` soak requirement or numeric release minimum applies; see master §5. Rung 3/4 (real DeckLink/headend caption survival) ride S1/S2 hardware. |
| (b) Per-sink loudness | **Rung 1 (Lab)** for −16 LUFS at conform | **Rung 1** for −24 LKFS path; **rung 2** via soak | Add regime, conform to −24 LKFS, measure emitted asset back to target within tolerance at a declared boundary; cable-correctness confirmed against a real headend = rung 4 (S2). |
| (c) EAS display | **Net-new / rung 0** | **Rung 1 (Lab)** + **rung 2 (Machine)** | Ingest a CAP fixture (and a live IPAWS/NWS sample) → display through the existing overlay path → soak proves poll-loop survives unattended; SAME burst proven only as locally-generated audio at rung 1. |

**Honest claim boundary (the hard line — master §7, §5):**
- Captions: claim only "CEA-708 ancillary captions embedded and **proven by emitted-stream
  decode-back** at the declared boundary." **Never** claim FCC Part 79 *compliance* — Part 79 is a
  carriage/quality legal obligation broader than "captions are present in the TS." Link Part 79;
  state CivicCast provides the captions and the proof, not a legal compliance certificate.
- Loudness: claim "normalized to the per-sink target (ATSC A/85 −24 LKFS for cable, −16 LUFS for
  streaming) and measured back within tolerance." Never claim headend-accepted loudness without
  rung-4 evidence (S2).
- **EAS — the cardinal boundary (master §7):** the product **NEVER** says "EAS-compliant,"
  "provides EAS," "EAS device," or "EAS relay." It says: *"ingests and displays CAP/IPAWS, NWS
  weather, and AMBER alerts as on-channel information; the mandatory FCC Part 11 EAS relay is
  performed by your cable operator's certified headend equipment (§11.34 certified device +
  §11.52 two-source RF monitoring)."* This mirrors the `eas_claim="not_eas"` /`not_claimed` stamps
  already enforced in `cg_bridge.py:16-20,39` and `branding.py:48,95-96`, and is **scope-neutral**
  with incumbent PEG platform (not an EAS device — master §2.1, §7). SAME local origination is "informational
  tone, not a Part 11 transmission." Sources: FCC 47 CFR Part 11; FEMA IPAWS; FCC Part 79.

---

## 8. Test plan + the 0/0/0/0/0 audit expectation

**Unit / contract (rung 0):**
- (a) `Cea708CaptionEmbedder.build_plan` returns `mode="cea-708"`, `status="not-verified"`, correct
  `not_claimed`; reuse the existing decode-proof unit suite (`evaluate_caption_decode_back`
  PASS/FAIL/no-expected paths, `caption_embed.py:139-178`).
- (b) Regime resolution table (each regime → correct target/tolerance; `inherit` → channel value);
  `EgressSinkSpec` migration round-trips; un-migrated sink == legacy −16 LUFS behavior; multi-regime
  channel triggers the chosen validation/resolution path (§6).
- (c) CAP 1.2 parser fixtures (IPAWS/NWS/AMBER samples), de-dup, supersession, geocode + severity
  filtering, expiry; `EasDisplayDecision` always stamps `eas_claim="not_eas"`; SAME generator
  fixture; **a test that fails the build if any public string contains "EAS-compliant"/"provides
  EAS"/"EAS device"** (honesty guard).

**Lab / runtime (rung 1):**
- (a) End-to-end: embed cues → emit to a loopback/file TS → decode → `caption_status` flips to
  `"on"` only on PASS; corrupt the stream → stays `not-verified`.
- (b) Conform a known asset to −24 LKFS → measure integrated loudness back within ±2; label reads
  ATSC A/85.
- (c) Feed a CAP fixture through ingest → confirm `EmergencyOverlay`/CG alert zone populated and the
  overlay egress proof READY; clear on expiry.

**Machine (rung 2) — folds into the global soak (master §12):** record observed caption proof and
`caption_status` results across the run, including any interruptions and recovery. Calculate
successful caption-input coverage per channel and in aggregate using the active-audio denominators
and discarded-input seconds defined in master §5; report failure and retry counts separately.
Correct late captions count as successful coverage. This is performance reporting, not an
uninterrupted-`on` requirement or a hard percentage floor; consult Scott before treating any
caption-performance result as a blocker. The EAS poll loop still survives reboot and de-dups across
restart; the per-sink loudness target persists.

**Playwright walkthrough (master §12):** `EasScreen` (sources CRUD, display/clear, posture banner
present and non-dismissible), caption-mode selector + proof drawer on `ChannelOpsScreen`, loudness
regime labels on sink/health screens.

**0/0/0/0/0 audit expectation (per `MEMORY.md` fix-all-severities):** after implementation run
`/audit-lite` on the diff, and `/walkthrough` + `/audit-team` at stage completion; **every audit
reaches 0 Blocker / 0 Critical / 0 Major / 0 Minor / 0 Nit** before this section is done. Special
audit attention: the honesty guard (no overclaim strings anywhere — code, API, UI, docs), the
fail-closed paths (caption mismatch, CAP fetch error, multi-regime conflict), and the
non-dismissible EAS posture banner.

---

## 9. DONE criteria

- [ ] `Cea708CaptionEmbedder` implemented; channel can select `cea-708` mode; emitted stream
      carries CEA-708 ancillary captions.
- [ ] Live decode-back loop wires a real `caption_status_provider` into the daemon; `caption_status`
      shows `"on"` only on a passing live proof; rolling `EgressCaptionProofSample` persisted +
      surfaced in the operator UI; CLI proof retained.
- [ ] `EgressSinkSpec` has per-sink **`loudness_target_lufs`** (+ `loudness_regime` /
      `loudness_tolerance_lufs`); cable sinks default to **−24 LKFS (==LUFS) ATSC A/85** (set
      explicitly at S2/S3 apply, from `HeadendProfile.recommended_loudness_regime`); streaming sinks
      remain −16 LKFS (==LUFS); conform applies the resolved per-sink target; loudness probe reports
      the correct standard label (no hardcoded "−16 LUFS").
- [ ] Migration **`0044_loudness_and_eas`** (single global chain, head `0037` → `0044`) adds the
      sink loudness columns + the caption-proof table + the EAS tables (S11 owns exactly one
      revision); existing rows behave exactly as today (`inherit`/NULL back-compat).
- [ ] `civiccast/eas/` ingests CAP/IPAWS + NWS weather + AMBER, de-dups/supersedes/expires,
      renders via the **existing** `EmergencyOverlay`/CG/branding path (crawl/overlay/forced slate),
      and clears correctly.
- [ ] Optional SAME local-origination generator behind a default-off flag with disclaimer.
- [ ] Every alert/overlay artifact stamps `eas_claim="not_eas"`; the **non-dismissible EAS posture
      banner** is present; the honesty-guard test passes (zero overclaim strings).
- [ ] Rung honesty: captions/loudness/EAS each tagged at their proven rung (master §5); no claim
      above its proof; soak records live caption proof/status results and measured input coverage,
      plus EAS poll-loop survival unattended. Caption performance is reported under master §5 and
      is not an automatic release blocker.
- [ ] Tests green at every tier; **0/0/0/0/0** on `/audit-lite` and the stage-completion
      `/walkthrough` + `/audit-team`.

---

## 10. Dependencies & cross-refs to other sections; Open decisions for Scott

**Cross-refs:**
- **S2 (headend handoff matrix)** — *load-bearing coordination.* S2 owns `HeadendProfile`
  (`egress/headend.py:43`) and the apply path; S11 adds `recommended_loudness_regime` to the
  profile and the per-sink defaulting at apply time. The −24 LKFS cable default must land **with**
  S2's headend-apply step, not separately.
- **S3 (commissioning wizard)** — the wizard's headend/SDI/output-proof steps set the per-sink
  loudness regime and can run a first caption decode-proof as a commissioning check.
- **S8 (health alerting)** — caption proof FAIL, EAS source fetch/parse failure, and loudness
  out-of-tolerance are alertable conditions; S8 is the push channel. S11 emits the conditions; S8
  delivers them.
- **S9 (reliability / process identity + proof-event caps)** — the new `EgressCaptionProofSample`
  and `eas_cap_alerts` tables must follow S9's rolling/cap discipline; the EAS poll worker and
  caption decode worker are supervised long-running tasks subject to S9's no-unguarded-waits rule
  (`MEMORY.md`).
- **S6 (CG bulletin-board designer)** — owns the multi-zone display surface; S11's EAS alerts ride
  the `alert` zone S6 formalizes. S11 ingests *into* S6's surface; S6 must reserve the alert zone +
  duck-under-alerts behavior (already modeled, `cg/service.py:171`).
- **Master §7 (EAS posture), §5 (proof ladder), §8 (model selection — captions stay
  whisper-large-v3, not affected here).**

**Open decisions for Scott:**
1. **Multi-regime channel (§6b).** When one channel feeds both a cable (−24 LKFS) and a streaming
   (−16 LUFS) sink, do we (A) **require single-regime per channel** (simplest, recommended for V1 —
   advise a separate channel per loudness domain), or (B) support per-sink re-loudness at egress
   (more code, only meaningful where a sink encoder is not stream-copy)? **Recommend (A) for V1.**
2. **CEA-708 ffmpeg arg form.** Embedding 708 ancillary captions depends on the station's ffmpeg
   build (608/708 encoder availability, `a53`/`ccaption` lane). Confirm we target the BYO-ffmpeg
   approach (like BYO-DeckLink/NDI, master §3) and document the required ffmpeg capability, vs.
   bundling a caption-capable build. **Recommend BYO + capability check in `doctor`.**
3. **EAS auto-escalation default.** Should `extreme`-severity alerts **auto-force a slate**
   (pre-empt programming) by default, or always require operator confirm? **Recommend: auto-display
   severe+ as overlay/crawl; require operator confirm for `forced_slate` unless the source is
   explicitly configured "official + auto-force."**
4. **SAME local origination — ship in V1 or defer?** It is optional, off-by-default, and carries
   overclaim risk. **Recommend: build the generator behind the flag (scope-neutral, low risk if
   disclaimed) but keep it off by default and out of the default UI.**
5. **IPAWS-OPEN access.** Live IPAWS feeds may require a COG/credentialed endpoint; NWS
   (`api.weather.gov`) is open. Confirm whether V1 ships IPAWS as "configure your COG endpoint"
   (operator-supplied) with NWS+AMBER working out-of-box. **Recommend operator-supplied IPAWS,
   NWS/AMBER out-of-box.**

---

*Implementation does not begin until Scott approves this spec.*

---
## Comparative additions (incumbent PEG platform gap closure → S18)
This section gains **SAP / Multiple Audio Programs + descriptive-audio tracks** (S18 gap 9,
`AudioProgramTrack`, migration `0054`) and **EAS attention-tone stripping** on web/OTT egress sinks
(S18 gap B).

**EAS attention-tone stripping — CORRECTED per pre-build validation (it IS a schema field, not "no schema"):**
- **Field:** `eas_tone_strip_enabled: bool = True` on `EgressSinkSpec` (added to migration `0044`
  above). Default **ON** for internet/OTT sinks (`srt`/`rtmp`/`hls`/web), **OFF** for cable sinks
  (`udp-ts`/`sdi`), which carry the cable operator's legitimate Part-11 EAS relay.
- **Filter:** on enabled sinks an egress audio stage rejects the EAS attention signal — the
  **853 Hz + 960 Hz two-tone** plus SAME header/EOM bursts — via notch filtering (FFmpeg/GStreamer
  `bandreject`/`equalizer` at 853 & 960 Hz ±15 Hz, gated to sustained bursts so legitimate program
  audio is untouched). Basis: FCC **47 CFR §11.31** — EAS tones are control signals; rebroadcasting
  them on non-authorized internet/OTT paths is an enforcement violation.
- **Test:** a fixture (`civiccast/eas/fixtures/same_burst_with_tones.wav`) is run through an OTT-sink
  pipeline → assert the 853/960 Hz energy is removed (FFT below threshold) while surrounding audio
  survives; a **build-failing** test asserts no web/OTT sink can ship with `eas_tone_strip_enabled=
  false` absent an explicit operator override + logged acknowledgement.
See the S18 comparative appendix.

## Secondary audio / SAP / descriptive audio — build detail (migration `0054_secondary_audio`)

the incumbent PEG platform's Multiple Audio Programs (MAP, v7.8+) = SAP: discrete secondary audio tracks for
non-English translation or **descriptive audio** (narrated on-screen action for the visually
impaired) — an accessibility requirement that S20 (ADA) depends on.

```python
class AudioProgramTrack(BaseModel):
    track_id: Slug
    scope: Literal["asset", "channel"]
    target_id: Slug                        # asset_id or channel_id
    kind: Literal["primary", "sap", "descriptive"]
    language: str                          # BCP-47 (e.g., "es", "en")
    source: str                            # input id / file / live source for this track
```
**Behavior:** (1) on the **cable** MPEG-TS path, the S15 engine muxes secondary audio as additional
**audio PIDs** (so a TV's SAP button / language selector works); (2) on **web/OTT** players, the
tracks surface as a selectable **audio-track toggle**; (3) **descriptive audio** is just
`kind="descriptive"` routed the same way; (4) loudness normalization (above) applies per track.
Migration `0054_secondary_audio` adds `audio_program_tracks` (after `0053`).

**Testable done-criteria:** DC-SAP1 a channel with a `sap` Spanish track emits a second audio PID in
the TS, selectable as SAP (lab + TSDuck PID check); DC-SAP2 the web/OTT player exposes the track
toggle and switches audio (lab, ties S20 DC-5); DC-SAP3 a `descriptive` track routes identically and
is announced as audio-description in the player (lab); DC-SAP4 each track is loudness-normalized to
its sink's standard (contract). Audit 0/0/0/0/0.
