# CivicCast 3.0 Spec Set — Reconciliation & Canonical Decisions

> **Historical reconciliation snapshot (October 11, 2026):** This record was
> produced in June 2026 against `main @ 69cc676`; its “current” readiness,
> implementation cursor, and migration-head statements describe that dated
> review, not Beta 12. The Beta 12 verification record reports the observed
> candidate schema as 0089. Use the [current release verification
> record](../../releases/v1.0.0-beta.20-verification.md) and current
> source for shipped scope. This notice preserves the historical design
> decisions below without treating them as current implementation proof.

> **Version-numbering note (added 1.0-hardening):** the `3.0` in this
> directory name is the INTERNAL spec-document era (master spec section
> numbering), decoupled from the public release line. The public releases
> reset to semver `0.x` at `v0.1.0-rc1` (July 2026). At the time this note was
> written, this spec set and its machine-checked `ROADMAP.status.yaml` were the
> actively reconciled 1.0-readiness ledger for that public line; this dated
> status no longer establishes current Beta 20 readiness.


> Status: authoritative consistency record for the 3.0 spec set, produced 2026-06-13 after a
> cross-document reconciliation pass over the master + 13 section specs (ground-checked against
> `main @ 69cc676`). **This document's "Canonical Decisions" are binding** — where a section
> spec disagrees, this document wins, and the section is corrected to match.
>
> **Implementation status (2026-06-14):** APPROVED — IN PROGRESS on `work/3.0-gstreamer-engine`;
> master §10 steps 0–3 are built (cursor = step 4, S8). **Migration-numbering reality:** the single
> chain's head is `0038_reliability_fields` (S9, step 3 — the first 3.0 migration; the CA cable sprint
> is already in `main` at ≤`0037`). The per-section migration numbers in the table below (S8=`0042`,
> S9=`0043`, parity migrations spanning `0049` forward — canonical assignment in the migration table
> below) are a **planning** guide; 3.0 migrations are assigned **as-built, monotonically from
> `0038`** (S9 took `0038`; S8/step-4 takes the next free number after the head).

## What was checked
Master + S1–S13. Cross-checked entity names, auth roles, proof-ladder rungs, alembic chain,
hardware tiers, AI model identifiers, CG feed kinds, loudness placement, and every cross-section
dependency, against the actual code.

## Structural fixes applied
- **S1 and S11** were re-authored (their first agents returned summaries without writing files).
- **S8** is rewritten to full depth as the alerting hub (it first came back as a stub).
- **Alembic chain** is a SINGLE GLOBAL chain (one head, currently `0037_asset_meeting_body`),
  per `tests/live/test_real_postgres.py` ("one head despite the per-module directory layout").
  All 3.0 migrations take a single monotonic sequence from `0038` — see the table below.

---

## Canonical Decisions (binding)

1. **Auth roles — the five real roles only.** `setup_admin`, `meeting_operator`, `records_clerk`,
   `publish_operator`, `support_admin` (`auth/roles.py:14-20`). `operator`/`admin` are all-roles
   aliases; `viewer` does not exist. Every endpoint names explicit real roles. Read-only
   diagnostic surfaces use `support_admin`. (Fixes S3, S4, S7, S10.)

2. **Hardware tiers — `tier-0 / tier-1 / tier-1-plus / tier-2`** (VRAM-keyed, `platform/hardware.py`).
   No `tier-3`, no `tier-1/2/3` scheme. The **AI model default keys off system RAM**, which is a
   separate axis from the VRAM tier. (Fixes S3.)

3. **`StationBoxProfile` system-RAM field = `system_ram_total_gb`** (mirrors `RAMInfo.total_gb`).
   S13's adaptive-default logic reads this exact field. (S1 defines it; S13 consumes it.)

4. **AI model identifiers.** `ModelTier.model_id` = the Ollama/runtime tag
   (`gemma4:12b`, `gemma4:e4b`, `translategemma:4b`, `whisper-large-v3`); `ModelTier.key` = a
   registry slug (`gemma4-12b-ollama`). S13 carries the mapping table. **Translation is
   `translategemma:4b` everywhere** (fixes S10's stray `gemma4:4b`).

5. **CG feed kinds vs zone content modes.** `FeedKind = rss|ical|caldav|weather|social` (feed
   SOURCES, `cg/models.py:14`). Zone content modes = `feed_adapter|manual|schedule|emergency|
   image|clock` (`CgZoneConfig.content_source`). Prose must not call manual/image/clock "feeds."
   (Fixes master §4 item 4, S6.)

6. **Loudness — per-sink on `EgressSinkSpec`.** The whole point is that a channel's cable sink
   (-24 LKFS ATSC A/85) and its streaming sink (-16 LUFS) differ, so the target is per-sink, not
   channel-level. Field `loudness_target_lufs` (note once: **LKFS == LUFS** per ITU-R BS.1770).
   Ownership: **S7** = ingest-time loudness gate/badge (`check_streaming_loudness`); **S2/S11** =
   egress-time per-sink target selection; same `loudnorm` code path. (Fixes S2 [moves field from
   `EgressConfig` to `EgressSinkSpec`], master §6.)

7. **Proof ladder — master §5 is authoritative.** rung 1 Lab = loopback/synthetic runtime;
   rung 2 Machine = clean install + 24/72h soak incl. reboot/crossover/reap; rung 3 SDI; rung 4
   Headend; rung 5 Field. No off-ladder labels ("Shipped"/"Partial"). For **OTT (S12): rung 3
   (SDI) is N/A** — OTT advances 2→4 via store acceptance; a device-lab emulator pass is the OTT
   rung-2 bar (one operator side-load = rung 1). (Fixes S2, S5, S10, S12.)

8. **`OnAirLockState` = commit-approval lock ONLY (S4).** It prevents two operators committing
   conflicting schedules and gates dispatch. It does **not** arbitrate runtime sources. Runtime
   source arbitration is the **supervisor priority model: emergency-slate > live-takeover >
   committed-schedule > filler** (S5), with the emergency-slate priority coming from S11. S4 must
   not claim S5 coordinates through `OnAirLockState`; S5 is the runtime arbiter. They are
   independent at runtime. (Fixes S4↔S5 contradiction.)

9. **`EgressCommand.action` enum — S5 owns the migration** adding `takeover`/`handback`. S4
   dispatch reuses existing `start`/`reload` (sets the committed source, then reloads); S4 adds no
   action. (Fixes S4/S5 ownership.)

10. **`doctor` surface — S1 owns** `civiccast doctor` + `StationBoxProfile` output. S3
    (`station doctor`/`commission`/`--cable`), S10 (`doctor --proof`), S13 (model default) are
    extensions that cross-reference S1's canonical command.

11. **`EgressHealthSample` co-edit.** S8 owns the QA-004 `sink_connected`/`egress_state` semantics;
    S9 owns `schema_version`/`proof_events_appended`. Both touch `egress_health_samples`; their
    migrations are sequential and each notes the co-edit (see table).

12. **Wizard step list — S3 owns it.** OTT (S12), AI adaptive-default (S13), watch-folder (S7), and
    CEA-708 passthrough (S11) fold into S3's canonical step list rather than each adding a screen.
    Master §4 item 11 / §12 updated to match S3's final count.

13. **No-deferrals corrections (per "get it done right"):**
    - **S2:** interlaced cable profiles (1080i59.94 / 480i29.97) + 720p59.94 are cable-parity, not
      "later polish" — build them into S2/S3.
    - **S6:** ship the full template set (fullscreen / L-bar / lower-third / bug / ticker /
      emergency) + manual zone text editor + feed-item approval gate in V1 — do **not** defer to S7.
    - **S13:** OpenRouter + Ollama Cloud adapters ship **functional** (default OFF, operator opts
      in and accepts per-token cost), not stubbed — "operator always chooses" requires the hosted
      path actually works.

14. **Multi-site federation = V2, not V1** (S18 D14). the incumbent PEG platform's LiveBridge is itself only GA
    Summer 2026; deferral is safe and is Scott's explicit call.

15. **SCTE-35 dynamic ad-insertion = NOT in scope** (S18 D15). the incumbent PEG platform lacks it too
    (scope-neutral); underwriting is handled via scheduled spots + pre-rolls, not splice markers.

16. **EAS = software CAP/IPAWS display + crawl only** (S18 D16). Mandatory Part-11 relay is the cable
    operator's FCC-certified headend device — never claim "EAS-compliant." **New requirement:** strip
    EAS attention tones (incl. the 853/960 Hz two-tone) from web/OTT egress sinks (FCC enforcement).

17. **Underwriting/sponsorship spot management = IN scope** (S18 D17). PEG is noncommercial but airs
    paid underwriting spots from for-profit entities (47 CFR 73.503: sponsor ID only — no
    calls-to-action / price / qualitative claims). Spot-as-asset + trafficking + break insertion +
    per-underwriter affidavits. VOD/OTT pre-roll already exists; extend to linear. **SHIPPED 2026-06-18
    as migration `0057_underwriting_spots`.**

18. **Agenda integration = IN scope, essential for government-access** (S18 D18). Agenda items synced
    to video timecode for chaptered navigation; optional agenda-PDF beside the player. **SHIPPED
    2026-06-18 as migration `0058_meeting_agenda`.**

19. **Subscription paywall = niche, V1.x-optional, default OFF** (S18 D19). Stripe magic-link gate.
    **SHIPPED 2026-06-18 as migration `0059_paywall_access`** (the chain forked at `0055` for S21's
    `0056_scheduled_recording` sibling slot; the chain HEAD is the data-free merge revision
    `0060_recording_paywall_merge` that unified `0056` + `0059`; later revisions
    extend the single chain through `0072_normalize_recording_file_uris` — see the
    chain-shape footer).

---

## Alembic migration assignments (single global chain, head `0037` → `0038`+)

| Migration | Owner | Adds |
|---|---|---|
| `0038_reliability_fields` | S9 | `egress_health_samples` schema-version/proof-churn fields; `egress_proof_events(channel_id, observed_at)` index |
| `0039_alerting_and_sinkhealth` | S8 | `alert_rules`/`alert_channels`/`alert_events`/`alert_event_deliveries`/`system_resource_samples`/`system_self_tests` |
| `0040_commit_to_air_reports` | S4 | `commit_to_air_reports` table |
| `0041_commit_rollback_fields` | S4 | `rollback_reason`/`rolled_back_at` on `commit_to_air_reports` |
| `0042_takeover_audit_and_command_action` | S5 | `takeover_audit` + extends `egress_commands.action` enum (`takeover`/`handback`) |
| `0043_scheduling_automation` | S4/S5 | `saved_searches`/`auto_schedule_rules`/`schedule_blocks` (S18 gaps 1,4) |
| `0044_cg_board_designer` | S6 | `cg_boards`/`cg_zones`/`cg_bulletins`/`cg_feed_sources` + approval (S18 gap 6 authoring) |
| `0045_cg_depth` | S6 | `bulletin_media`/`bulletin_audio`/`zone_tags` (S18 gap 6) |
| `0046_cg_feed_source_tags` | S6 | `cg_feed_sources.tags` (S18 gap 6 CG depth) |
| `0047_production_control` | S16 | `production_devices`/`device_profiles`/`control_surfaces`/`control_room_sessions` |
| `0048_remote_contribution` | S17 | `contribution_rooms`/`guest_invites`/`remote_guest_sessions` |
| `0049_per_sink_loudness` | S11 | per-sink loudness regime/target/tolerance + `eas_tone_strip_enabled` on `egress_sinks` |
| `0050_caption_proof_samples` | S11 | `egress_caption_proof_samples` (CEA-608/708 decode-back proofs) |
| `0051_public_safety_eas` | S11 | `eas_cap_sources`/`eas_cap_alerts`/`eas_display_decisions` |
| `0052_secondary_audio` | S11 | `audio_program_tracks` — SAP/descriptive audio (S18 gap 9) |
| `0053_ai_model_configuration` | S13 | `ai_model_configuration`/`feature_model_registry` |
| `0054_custom_metadata_fields` | S22 | `custom_field_defs`/`custom_field_values` (S18 gap 3) |
| `0055_asrun_and_epg` | S23 | `as_run_log`/`epg_export_configs` (S18 gap 5 — franchise-compliance proof-of-performance + TV-guide export) |
| `0056_scheduled_recording` | S21 | `recording_schedules`/`recording_jobs` (S18 gap 2 — forward-scheduled capture from live inputs + network streams; SHIPPED as a sibling off `0055` — sees the chain-shape footer for the fork + merge `0060`) |
| `0057_underwriting_spots` | S24 | `underwriting_spots`/`spot_flights`/`spot_placements` (S18 gap 10 — PEG underwriting acknowledgments under 47 CFR 73.503; trafficking + per-underwriter affidavits via S23 as-run join) |
| `0058_meeting_agenda` | S25 | `meeting_agendas`/`agenda_items` (S18 gap A — government-access agenda + video-timecode chapters; agenda items double as player chapters when published) |
| `0059_paywall_access` | S26 | `paywall_configs`/`access_grants`/`paywall_subscriptions` (S18 gap C — OPTIONAL/V1.x subscription paywall, default OFF; Stripe-hosted Checkout + magic-link sign-in, PCI SAQ-A scope) |
| `0060_recording_paywall_merge` | S21/S26 | merge revision — unifies the `0056` branch and the `0059` chain head into a single new head (data-free; no schema change) |
| `0061_control_room_mode_gate` | Control room | explicit Test Mode / On-Air Mode session fields |
| `0062_media_integrity_columns` | Media | missing-file detection and relink metadata on assets |
| `0063_producer_ops` | Producer ops | producer, volunteer, and equipment operations tables |
| `0064_control_room_health_and_versioning` | Control room | device health/freshness and cue versioning |
| `0065_recording_dropout_fields` | Recording | mid-recording source-dropout tracking |
| `0066_hls_sink_kind` | Egress | HLS egress sink kind |
| `0067_agenda_import_provenance` | Agenda import | agenda import provenance ledger |
| `0068_migrate_batches` | Station migration | import batch/item rollback ledger |
| `0069_control_room_session_surface_lock` | Control room | one-open-session-per-surface database lock |
| `0070_grandfather_scheduled_to_published` | Scheduling | data migration for the Commit-to-Air state model |
| `0071_published_blocks_overlap` | Scheduling | published schedule items participate in overlap exclusion |
| `0072_normalize_recording_file_uris` | Recording repair | rc16 rehearsal/recording rows re-pointed from raw file:// URIs to usable local paths (rc17 D3) |
| `0073_egress_allow_software_fallback` | Egress | `allow_software_fallback` operator opt-in on `egress_configs` |
| `0074_caption_review_audio_evidence` | Captions | private live-audio evidence retention for caption review items |
| `0075_offline_caption_jobs` | Captions | `offline_caption_jobs` queue (CivicCast One keystone K3) |
| `0076_analytics_viewership` | S14 | `viewership_events`/`viewership_rollups`/`analytics_report_snapshots`; promotes `analytics-events.json` to a durable, migrated store (idempotent app-level backfill, not in-migration — see the migration's own docstring for why) |

(S1, S3, S10, S12 add no migrations — S1/S3 are config/state-file + CLI; S10 is doc/gate; S12 is
app-build artifacts.

**Current chain shape — single head with a historical sibling branch.** S21's migration
`0056_scheduled_recording` declares `down_revision = "0055_asrun_and_epg"`, branching off `0055`. So
`0055` now has TWO children: `0056` (S21) and `0057` (S24). The chain forks at `0055` and rejoins at
the merge revision `0060_recording_paywall_merge` (whose `down_revision` is the TUPLE
`("0056_scheduled_recording", "0059_paywall_access")`). Later work extends that
single line through `0072_normalize_recording_file_uris`. The full shape is:

```
0054 → 0055 ─┬→ 0056 ─────┐
             │             ↓
             └→ 0057 → 0058 → 0059 → 0060 → 0061 → … → 0072 → 0073 → 0074 → 0075 → 0076 (HEAD)
```

The head pin in `tests/live/test_real_postgres.py` is `0098_channel_repeat_schedule`. **All S18
capability gaps are closed on disk**; master §10 step-12 flips from `partial` to `built` with this
ship.)

---

## Per-section fix list (applied in the finalization pass)

- **S2:** move `loudness_target_lufs` to `EgressSinkSpec` (D6); add interlaced/720p profiles (D13);
  fix proof-rung labels rung 2=Machine / rung 3=SDI (D7).
- **S3:** tier vocab (D2); real roles (D1); own the canonical wizard step list (D12); fold OTT/AI/
  watch-folder/CEA-708 steps in.
- **S4:** real roles (D1); `OnAirLockState` = commit-gate only, drop "S5 coordinates" (D8);
  reuse `start`/`reload`, no new action (D9); migration `0038` (table).
- **S5:** runtime priority arbiter; owns `EgressCommand` enum migration `0039` (D9); state engine is
  rung-0 contract, NOT "working in soak" (D7); references S11 emergency-slate priority (D8).
- **S6:** full template set + manual zone editor + feed approval in V1 (D13); feed-kind vs
  content-mode prose (D5); migration `0040`.
- **S7:** real roles (D1); owns ingest-time loudness gate (D6); migration `0041`; watch-folder-over-
  NAS is supported (flag from punch-list §5.4 — resolve as supported).
- **S8:** full rewrite to depth; enumerate alert conditions handed in by S2/S4/S5/S7/S9/S13
  (off-air, encoder-death, server-crash, schema-drift, relay-blocked, compliance-probe-fail,
  missing-media, commit-failure, takeover-stuck-2h, ai-runtime-down) + QA-004 safe-to-air;
  migration `0042`.
- **S9:** migration `0043` (sequenced after S8 on `egress_health_samples`); single-global-chain (not
  per-package).
- **S10:** real roles incl. replace nonexistent `viewer` with `support_admin` (D1); proof-ladder
  labels per master §5, no off-ladder terms (D7); translation = `translategemma:4b` (D4).
- **S11:** owns CEA-708 ancillary + decode-back in live loop; per-sink loudness `0044` (D6); EAS
  software display + honest Part 11 boundary (master §7).
- **S12:** OTT rung-3 N/A note (D7); reconcile internal version-rollback signal.
- **S13:** `system_ram_total_gb` (D3); model id/key mapping (D4); functional cloud adapters (D13).

## Master edits
- §4 item 4: fix feed-kind parenthetical (D5).
- §6: per-sink loudness on `EgressSinkSpec` (D6); add the entities sections actually define
  (S3/S7/S8/S9/S10/S12/S13 — correct `AppCatalog/AppChannel`→`AppBuildRecord/StoreSubmissionMetadata`,
  `ModelSelection`→`ModelTier/FeatureModelRegistry/AiModelConfiguration`); add `ScheduleConflict`
  (model, distinct from existing `ScheduleConflictError`).
- §10: add the migration assignment table.
- §11: S1/S11 now exist; S8 now full-depth.
- §12: wizard step count to match S3.

## Gaps flagged for Scott (no silent deferral)
- ~~**Analytics / Audience Measurement** has no owning section.~~ RESOLVED: owned by
  `docs/spec/3.0/sections/S14-analytics-audience-measurement.md`, built out (migration
  `0076_analytics_viewership`, durable rollups + rollup worker, role-gated API surface, CSV +
  board-ready PDF export, four-panel operator dashboard). As-run / proof-of-performance reporting
  (Schedule Report + Shows Report parity) is owned by `civiccast/reporting` (S18/S23,
  `0055_asrun_and_epg`) rather than duplicated inside S14 — S14 §4 cross-references it.
- **Interlaced profiles, full CG template set, functional hosted adapters** are pulled into V1 per
  D13 — confirm the added scope is acceptable.
