# S7 — Media Lifecycle and Readiness

> **Status (2026-08-21):** Built for v3.0.0-beta1 at Rung 1 (lab-proven: unit
> + API + migration-reversibility tests; see `feat/s7-media-lifecycle`).
> `docs/spec/3.0/ROADMAP.status.yaml` previously carried `status: built` for
> this section with evidence that never actually covered the five net-new
> entities below (§2) — corrected in the same change that built them.
> Rung 2 (24h unattended soak per §7) has not been run; nothing here is a
> field-ready claim.

## 1. Goal & PEG automation rationale

CivicCast must manage the complete media lifecycle from operator upload through archive, with
real-time visibility into readiness and failure states — essential for unattended playout in a
PEG environment. the incumbent PEG platform's model is: operator uploads a file → system validates codecs → renders
playout-ready mezzanine → archives a copy → sets retention policy. CivicCast today ships the
upload and ingest-validation layers (contract-tested); S7 closes the remaining gaps: media
readiness dashboards, transcode/proxy generation on ingest (not just at egress), honest loudness
checks before scheduling, and missing-media alerting. This is the "middle mile" between raw upload
and scheduled playout — it must be bulletproof for unattended operation.

**Master spec §2 mapping:** CivicCast closes **incumbent PEG platform gap 0** (asset ingest validation exists,
loudness/transcode exist in egress, archive/retention exist as presets). S7 wires the missing
**dashboards and readiness badges** that let the operator see at a glance: "this asset is ready
for air" vs "waiting on transcode" vs "codec not supported." **Proof tier: contract → lab via
test + soak.**

---

## 2. Current state (file:line)

### Existing

| Capability | File | Status |
|---|---|---|
| **Asset upload HTTP endpoint** | civiccast/schedule/router.py:389–540 (async upload_asset) | shipped — multipart form, file write to disk, ffprobe subprocess call, validation-gate HTTP 422 on bad codec |
| **ffprobe validation gate** | civiccast/schedule/ingest.py:159–192 (fn validate_ingest) | shipped — fails closed; rejects if no video stream, unsupported codec (curated whitelist), or bad container format |
| **Asset state machine** | civiccast/schedule/models.py:49–68 (constants ASSET_STATE_*) | shipped — 5 states: pending_ingest, ingesting, validated, rejected, recorded; DB CHECK constraint enforced |
| **Asset table schema** | civiccast/schedule/models.py:417–540 (class Asset) | shipped — 20+ columns; includes ffprobe extracts (codec_video, codec_audio, width_px, height_px, bitrate_bps, format_name, duration_seconds), state, trim/chapters/retention metadata, version (OCC) |
| **Ingest workflow** | civiccast/schedule/store.py:315–330 (method ingest_upload) | shipped — creates Asset row with state=validated after ffprobe passes; no manifest_url yet (Sprint 0.4 packager fills it) |
| **Asset library UI** | civiccast/schedule/router.py:176–208 (fn list_staff_assets), civiccast/schedule/models.py:277–324 (class StaffAssetRow) | shipped — operator sees all assets (pending/ingesting/validated/packaged) in one list |
| **Retention presets** | civiccast/archive/retention_presets.py:29–150 | shipped — 9 US state retention schedules with source URLs + review notes; operator chooses at asset level |
| **Retention enforcement** | civiccast/schedule/retention_worker.py:127–203 (class RetentionEnforcementWorker) | shipped — background worker flags expiring assets into disposition-review queue (never auto-deletes) |
| **Archive models** | civiccast/archive/models.py:13–77 (classes ArchiveProof, LocalNasArchivePlan) | shipped — proof records for backup + hash verification; backup/restore rehearsal exists in installer |
| **Loudness checking** | civiccast/stream/loudness.py:26–98 (fn check_streaming_loudness) | shipped — ITU-R BS.1770 / EBU R128 gate; returns LUFS measured + pass/fail vs target; called in egress preparer |
| **Source conforming (loudnorm)** | civiccast/egress/preparer.py:45–150 (class SourcePreparer, method prepare) | shipped — conforms segments to canonical profile, runs loudness check, applies ffmpeg loudnorm if needed; caches identical segments |

### Net-new (in scope for S7)

| Feature | Gap | Why needed |
|---|---|---|
| **MediaIngestJob** | No durable job tracking for async ingest | Watch-folder and background transcode need a way to track "what's running" and survive restart |
| **TranscodeJob** | Transcode on ingest is hardcoded to egress preparer only | Need to generate proxy/mezzanine formats **at upload time**, not just at playout; operator needs to see progress |
| **AssetReadiness** | No badge showing "ready for air" vs "transcoding" | Operator dashboard must show readiness; hidden state = risk |
| **WatchFolderConfig** | No directory monitor for auto-ingest | Unattended station needs hands-off ingest from USB/NAS |
| **MissingMediaDashboard** | No alert when scheduled asset is missing or in wrong state | Unattended box must call for help before air-check fails |
| **AssetRetentionPolicy** | Presets exist; policy assignment is manual in metadata edit | Should allow automation: "all meeting recordings in this series → retention_policy=meeting" |
| **ReplaceMediaWorkflow** | No API for operator to swap out a source file | If a recording is corrupt, operator needs "replace" not just "delete + re-upload" |

---

## 3. Entities / data model & migrations

### New SQLAlchemy models

#### MediaIngestJob

\\\python
# civiccast/schedule/models.py (add to existing Asset module)
class MediaIngestJob(Base):
    """Durable record of an async ingest operation (upload or watch-folder)."""
    
    __tablename__ = "media_ingest_jobs"
    
    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)  # UUID
    asset_id: Mapped[str] = mapped_column(String(64), ForeignKey("civiccast.assets.asset_id"))
    source_kind: Mapped[str] = mapped_column(String(20))  # "http_upload" | "watch_folder" | "live_finalization"
    source_path: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|running|completed|failed
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_detail: Mapped[str | None] = mapped_column(Text)  # operator-readable error message
    metadata_json: Mapped[str | None] = mapped_column(Text)  # ffprobe result as JSON
\\\

### Migration

S7 ships a **single migration `0080_watch_folder_daemon`** on the single global alembic chain
(the chain has one head; S7 landed as `0080`). It creates all five S7 tables in
one revision: `media_ingest_jobs`, `transcode_jobs`, `asset_readiness`, `watch_folder_configs`,
`asset_retention_policies`. (One head despite the per-module directory layout, per
`tests/live/test_real_postgres.py`.)

---

## 4. API surface

### New endpoints (S7)

#### GET /api/staff/assets/{asset_id}/readiness
\\\
Auth: require_any_role(['meeting_operator', 'publish_operator', 'support_admin'])
Response: {
  asset_id: string,
  readiness_state: "not_ready" | "pending_transcode" | "transcoding" | "ready" | "missing_file" | "rejected",
  readiness_reason: string | null,
  loudness_status: "ok" | "failed" | "not_checked" | null,
  measured_lufs: number | null,
  in_flight_transcode_jobs: [
    { job_id: string, output_format: string, progress_percent: int, estimated_remaining_secs: int }
  ],
  updated_at: datetime
}
\\\

#### GET /api/staff/assets/readiness-dashboard
\\\
Auth: require_any_role(['meeting_operator', 'publish_operator', 'support_admin'])
Response: {
  total_assets: int,
  ready_count: int,
  transcoding_count: int,
  missing_count: int,
  rejected_count: int,
  by_asset: [
    { asset_id, readiness_state, readiness_reason, in_flight_jobs_count }
  ]
}
\\\

---

## 5. Operator UI surface

### Media Library Dashboard (extend existing)
- Add **Readiness Badge** column to asset list: 🟢 Ready | 🟡 Transcoding (45%) | 🔴 Missing | ⚪ Rejected
- Clicking a badge opens **Asset Readiness Detail** modal:
  - File path + size
  - Loudness gate result (LUFS measured vs target)
  - Ingest job status + elapsed time
  - Transcode jobs in flight (progress bar per format)
  - Retry buttons: "Re-run loudness check", "Replace source file", "Restart transcode"
- Bulk action: "Mark all transcoding ready for air" (operator override for patience; recorded as exception)

### Missing Media Alert (new screen)
- Background worker (every 5 min) queries: assets scheduled in next 7 days + not in state 'validated'|'recorded'
- Shows list: "City Council meeting (2026-06-15 09:00) — source asset 'meeting-20260615' is missing"
- Action: "Replace source" (picks a different validated asset) or "Cancel this schedule item"

---

## 6. Behavior / algorithms

### Upload → Validated lifecycle

1. **Operator uploads file via POST /api/staff/assets**
   - Router creates Asset row with state='pending_ingest' + filename (route:423)
   - Queues ffprobe on temp file
   - On ffprobe success: validate_ingest gate (ingest.py:159); if fails → HTTP 422, Asset.state='rejected'
   - If passes: move temp file to asset_dir, ingest_upload creates final row with state='validated'

2. **Watch-folder monitor (background daemon)**
   - Runs on a 5-second poll interval
   - Lists files in configured monitor_path matching import_naming_pattern
   - monitor_path may be a **local disk, USB, or NAS/SMB path** — NAS/SMB is supported (D13); see
     open-decision §10.5 for the SMB resilience design (settle-window write-completion detection,
     copy retry/backoff, post-copy CRC/size verify)
   - For each new file: waits for write-completion (size+mtime stable across two polls), copies to
     upload_dir asynchronously with retry on transient SMB errors, verifies CRC/size, queues ingest
     (same path as upload workflow)
   - On ingest completion: asset appears in operator console
   - **Build note (2026-08-25):** implemented at
     `civiccast/schedule/watch_folder_worker.py` (`WatchFolderWorker`), migration
     `0080_watch_folder_daemon`. This paragraph's "asynchronous with retry/backoff" wording and
     its "CRC" wording are approximated, not implemented verbatim: the daemon's copy retry is
     poll-interval-paced (a failed copy leaves the file's ledger row FAILED; since the source is
     unchanged the very next poll re-attempts it) rather than sub-second exponential backoff
     within a single pass, and post-copy verification checks size (cheap, catches the realistic
     truncated-SMB-copy failure mode) rather than a full CRC/hash of the SMB-side source, which
     would double read I/O over the network for every file. Processed-file disposition
     (move-to-subfolder vs. leave-with-ledger), the per-config degraded/unreachable-path state,
     and the delete-safety posture (never deletes the source file, either mode) were open
     decisions this spec paragraph didn't resolve — see
     `docs/adr/0024-watch-folder-daemon-processed-file-and-degraded-state.md`.

### Transcode on ingest

1. **After Asset.state → 'validated'**, create TranscodeJob rows for each output format
   - Formats (configurable, defaults): h264_720p_5mbps, h265_1080p_8mbps (proxy), h264_mezzanine (high-quality)
   - Each job is independent; can be scheduled/retried separately
2. **Background transcode daemon polls TranscodeJob(status='pending')**
   - Locks job (status → 'running', started_at = now)
   - Calls ffmpeg with conform args
   - Updates progress_percent every second
   - On success: status='completed', file_size_bytes set
   - On failure: status='failed', error_detail set

### Loudness check during ingest

**Ownership (D6):** S7 owns the **ingest-time loudness gate and badge** (`check_streaming_loudness`)
— measure once at ingest, store the result, surface the readiness badge. **Egress-time per-sink
target selection lives in S2/S11** (each `EgressSinkSpec` carries its own `loudness_target_lufs`;
a cable sink at -24 LKFS and a streaming sink at -16 LUFS differ). Both ends run the **same
`loudnorm` code path** — S7 does not re-implement loudness normalization; it gates and badges at
ingest, S2/S11 select the per-sink target at egress.

1. **After ffprobe validation** (ingest.py:159), call check_streaming_loudness (stream/loudness.py:26)
   - Runs ffmpeg filter ebur128=peak=true
   - Returns LoudnessGateResult { status, measured_lufs, operator_action }
2. **Store result on Asset + AssetReadiness**
   - AssetReadiness.loudness_status = result.status, measured_lufs = result.measured_lufs
3. **Loudness failure ≠ ingest failure**
   - Asset still transitions to validated (operator can manually normalize at egress time)
   - Readiness badge shows 🟡 "Loudness failed — must normalize before air"

---

## 7. Proof tier: current rung + how to advance it

### Current proof state
- **Upload + ffprobe validation:** contract-tested (tests/schedule/test_upload_router.py, test_ingest.py)
- **Asset state machine:** contract-tested (test_schedule_models.py)
- **Loudness checking:** contract-tested (stream loudness tests)

### How S7 advances the ladder

#### Rung 1: Lab-proven (required for ship)
- **Unit tests:** MediaIngestJob/TranscodeJob creation + state transitions, AssetReadiness computation
- **API tests (Pytest + FastAPI test client):** POST upload, GET readiness, PUT replace-source
- **E2E scenario:** Upload valid asset → validated → TranscodeJob created → watch progress

#### Rung 2: Machine-proven (24h unattended soak)
- Run soak with background transcode daemon enabled
- Metrics: 10 test files, transcode completion = 100%, AssetReadiness updates correctly
- Watch-folder ingests 5 files without operator intervention
- Soak gates: no db deadlocks, progress updates durable

---

## 8. Test plan (unit/API/e2e + soak gate)

### Unit tests (tests/schedule/)

- test_media_ingest_job.py: Job lifecycle, state transitions
- test_transcode_job.py: Progress updates, error handling
- test_asset_readiness.py: Readiness computation for all states
- test_watch_folder_config.py: CRUD operations

### API tests (tests/schedule/test_readiness_api.py - new)

- GET /api/staff/assets/{asset_id}/readiness
- GET /api/staff/assets/readiness-dashboard
- PUT /api/staff/assets/{asset_id}/replace-source
- POST /api/staff/watch-folder/config

### E2E scenario tests (tests/schedule/test_media_lifecycle_e2e.py - new)

- Upload → validate → transcode → ready
- Upload invalid codec → rejected state
- Missing media detection

### 0/0/0/0/0 audit expectation

- **Bugs found & fixed:** 0
- **API errors (500):** 0
- **Data corruption:** 0
- **Unhandled exceptions:** 0
- **Security issues:** 0

---

## 9. DONE criteria

1. ✅ Migration merged (single `0041_media_lifecycle` on the global chain; all five S7 tables in one revision)
2. ✅ Unit + API tests pass (100% coverage; 0/0/0/0/0 audit)
3. ✅ E2E scenario runs (upload → validate → transcode → ready)
4. ✅ 24h soak completes (concurrent load; no stuck jobs)
5. ✅ Operator screens wired (readiness badge, missing-media alert, watch-folder monitor)
6. ✅ Readiness computation correct (denormalized; dashboard queries don't timeout)
7. ✅ Ingest-time loudness gate/badge integrated and owned by S7 (result stored + displayed); egress-time per-sink target selection deferred to S2/S11 on the same `loudnorm` code path (D6)
8. ✅ Replace-source flow end-to-end (old file archived, new file validated)
9. ✅ Watch-folder hands-off (background; operator sees ingests)
10. ✅ Honest claim boundary (proof tier = lab; no unqualified field-ready claims)

---

## 10. Dependencies & cross-refs to other sections; Open decisions for Scott

### Dependencies

- **S1 (StationBoxProfile):** transcode daemon respects hardware tier (CPU cores, RAM) to pick formats
- **S2 (Headend profiles):** owns egress-time per-sink loudness **target selection** (`loudness_target_lufs` on each `EgressSinkSpec`; default -16 LUFS streaming, cable sinks -24 LKFS). S7 owns the ingest-time gate/badge; same `loudnorm` code path (D6).
- **S3 (Commissioning wizard):** "Configure watch folder" step added
- **S4 (Commit-to-Air gate):** readiness dashboard shows if any asset is missing before air
- **S8 (Health alerting):** missing-media worker integration; alerts if <5 min to air
- **S11 (Captions/loudness):** CEA-708 ingest; owns egress-time per-sink loudness target selection (with S2). S7 owns the ingest-time loudness gate/badge; same `loudnorm` code path (D6).

### Open decisions for Scott

1. **Transcode format defaults — RESOLVED (2026-08-24, ADR 0007 amendment)
   as the named alternative: h264-only.** The originally-shipped three-format
   default (h264_720p_5mbps + h265_1080p_8mbps + h264_mezzanine) was found,
   via a resource-posture regression audit, to violate this repo's no-GPL
   posture (`h265_1080p_8mbps`'s ffmpeg args carried a bare `libx265` (GPL)
   literal with no encoder-selection seam at all — unlike H.264, no
   GPL-free HEVC encoder resolution path exists anywhere in this tree) and
   to seed every format unconditionally, dispatched synchronously at
   normal process priority under ffmpeg's flat 6-hour timeout — a large
   amount of unsupervised, full-priority ffmpeg work with no station-level
   off switch. `DEFAULT_TRANSCODE_FORMATS` is now `("h264_720p_5mbps",)`
   only; `h264_mezzanine` remains available opt-in via
   `CIVICCAST_TRANSCODE_FORMATS`. See ADR 0007's "S7 ingest-time transcode
   defaults and resource posture" amendment for the full rationale,
   including the new `transcode_seeding_enabled` off switch and the
   per-minute-of-source timeout budget replacing the flat 6h ceiling.
   - Q: How many transcodes per asset acceptable for 100-asset station?
     Still open at the "how many" granularity beyond this default; not
     reopened by this resolution.

2. **Watch-folder detection:** inode+mtime cache in sqlite (survive restart, no false re-ingests)?
   - Alternative: simple mtime polling (simpler, risk of occasional double-ingest)
   - Q: Accept complexity of inode cache, or tolerate duplicate-detection?

3. **Readiness denormalization:** separate AssetReadiness table for speed, or compute on-the-fly?
   - AssetReadiness = 1-2ms insert cost per asset edit
   - Q: Dashboard speed or simplicity?

4. **Archive retention enforcement:** defer auto-delete to S10 safety audit?
   - S7 flags expired assets; never auto-deletes (safe, auditable)
   - Q: Recommend defer auto-delete?

5. **Watch-folder over SMB/NAS — SUPPORTED (D13).** NAS/SMB watch-folder ingest is a core station
   workflow and is supported, not flagged unsupported. SMB atomicity assumptions are weaker than
   local disk, so the monitor builds in resilience rather than excluding the path: detect
   write-completion via a settle window (size+mtime stable across two consecutive polls) before
   copy, copy with retry/backoff on transient SMB errors, and verify a CRC/size match after copy
   before queueing ingest. (Resolved as supported; remaining tuning — settle-window duration —
   is operator-configurable.)

---

## 11. Build order note

S7 lands in build order slot #10 (after S6 CG designer, before final polish).

**Estimated effort:** 8–10 person-days (models + migrations + 3 daemons + 4 screens + tests + soak)

---

*Spec authored 2026-06-13, grounded in code @ 69cc676. S7 closes all loudness/transcode/retention gaps; proof tier is lab (contract + tests + soak).*

---
## Comparative additions (incumbent PEG platform gap closure → S18)
This section gains four comparative closures (migration numbers reconciled to the on-disk chain 2026-06-18;
see S18 §6): **scheduled recording** (gap 2, `0056_scheduled_recording` — SHIPPED 2026-06-18 as the
sibling off `0055`; the LAST S18 capability gap closed; the S7 record-sink seam is `AssetFinalizerProtocol`
in `civiccast/recording/service.py` — see W-5 §x-ref below), **user-defined custom metadata fields**
(gap 3, `0054` — shipped on disk; owned by S22), **underwriting spot management** (gap 10, `0057` —
SHIPPED 2026-06-18; shared with S4/S14; renumbered from planned `0055` per RECONCILIATION D17), and
**meeting-agenda integration** (gap A, `0058` — SHIPPED 2026-06-18, owned by S25; agenda items synced
to video timecode for chaptered navigation; renumbered from planned `0056` per RECONCILIATION D18).
The chain HEAD is now `0060_recording_paywall_merge` (the data-free merge revision that unifies the
`0056` sibling branch back into the `0059_paywall_access` line; see RECONCILIATION's chain-shape
footer). See the S18 comparative appendix.

### S21 asset-finalizer integration (cross-reference)
S21's `RecordingService` finalizes a completed capture into a normal CivicCast `Asset` + `asset_readiness`
record (identical in shape to a watch-folder ingest, S7 §6) by calling the `AssetFinalizerProtocol` seam
declared in `civiccast/recording/service.py`. The S7 ingest path is the production implementation; S21's
unit tests inject a stub. Target-series + custom-field stamps (S22) are applied at finalize time.
