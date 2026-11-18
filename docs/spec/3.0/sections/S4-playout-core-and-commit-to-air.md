# S4 — Playout Core and Commit-to-Air Workflow

> Status: **SECTION SPEC — grounds implementation of the Commit-to-Air gate (MASTER §4 item 6).**
> Verified against code on main @ 69cc676. All "current state" and entity claims reference specific file:line.

---

## 1. Goal & PEG automation rationale

**Goal:** Introduce operator approval + dry-run + conflict & missing-media detection at the moment of
scheduling a program log to air, replacing today's fully automatic materialization. Enable the
operator to see what will play, catch mistakes before they go live, and rollback/recommit if needed.

**PEG automation coverage (MASTER §2.1):**
saved-search auto-scheduler automatically materializes program slots into a playout log. CivicCast today
does the same via ProgramLogMaterializer.run_once() (civiccast/programlog/materializer.py:124) —
the operator defines slots, the materialization loop runs every 5 minutes, and schedule items appear
in the log with no gate. **The gap:** neither system surfaces conflicts or missing media before the
item is air-ready, and spec.md §8.3 explicitly mandates "no auto-commit path to airtime."

This section closes that gap: introduce a CommitToAirPlan with a dry-run that **actively detects**:
- Schedule conflicts (time-range overlaps on the channel)
- Missing or unplayable media (asset missing, unvalidated, no file, or no duration)
- Scheduling anomalies (gap detection, duration mismatches)

On approval, the commit flow persists a CommitToAirReport and **dispatches to the egress automation
layer** (civiccast/egress/automation.py, civiccast/egress/supervisor.py) so the live playout
executes the plan. Rollback and recommit workflows allow the operator to fix and resubmit.

---

## 2. Current state (what exists vs. net-new)

### Implemented (ground in code)

- **ProgramLogMaterializer** (civiccast/programlog/materializer.py:90–232): generates schedule items
  from recurring slots idempotently. Records occurrences in program_slot_occurrences table.
  Skips on conflict (passive EXCLUDE constraint) or missing asset; skipped details at :207–224.

- **ScheduleConflictError** (civiccast/schedule/store.py:443–464): exception raised when
  Postgres EXCLUDE constraint rejects overlapping premiere items on the same channel.
  Store method at :689–744 enriches error with conflicting item detail.

- **ScheduleItem model** (civiccast/schedule/models.py:92–132): premiere/embargo modes, time-range
  columns (scheduled_at, scheduled_at_end), state machine (scheduled/cancelled/published).

- **Asset state machine** (civiccast/schedule/models.py:49–68): validated/recorded states.
  File path and duration live on Asset row (civiccast/schedule/models.py:417–506).

- **EgressSourcePlan** (civiccast/egress/source_plan.py): sequence of segments;
  consumed by egress automation loop.

- **PlayoutSupervisor** (civiccast/egress/supervisor.py:18–147): holds request_live_takeover(),
  request_live_handback(), request_fallback_slate(), request_slate_exit() methods.
  Currently unwired to staff API.

- **ChannelAutomationService** (civiccast/egress/automation.py:108–300): polls every 2 seconds,
  processes command queue, auto-starts enabled channels. Proof rung: **machine-proven** (24h soak).

### Net-new entities (not yet coded)

- **CommitToAirPlan**: operator's intent to air a materialized occurrence; built in-memory,
  includes dry-run result (conflicts, missing media, gaps).

- **CommitToAirReport**: persistent record of approved commitment; operator name, timestamp,
  conflicts seen, dispatch status.

- **ScheduleConflict**: structured representation of detected overlap.

- **PlayoutEventPlan**: segment-by-segment playout schedule with gap detection.

---

## 3. Entities and data model & migrations

### New persistence entities

#### CommitToAirPlan (ephemeral, not persisted)

Built in-memory during commit workflow; result is persisted as CommitToAirReport.

`python
class CommitToAirPlan(BaseModel):
    plan_id: str  # "ctap_" + token
    channel_id: str
    occurrence_id: str
    schedule_item_id: str
    asset_id: str
    title: str
    scheduled_at: datetime
    duration_seconds: int
    dry_run_passed: bool
    conflicts_detected: list[ScheduleConflict] = Field(default_factory=list)
    missing_media_detail: str | None = None
    gaps_detected: list[PlayoutEventPlan] = Field(default_factory=list)
    created_at: datetime
    operator_id: str | None = None
`

#### CommitToAirReport (persisted)

**Table: civiccast.commit_to_air_reports**

- report_id: VARCHAR(64) PRIMARY KEY
- channel_id: VARCHAR(80) NOT NULL, INDEX (composite with approved_at — see below)
- occurrence_id: VARCHAR(120) NOT NULL — soft reference to program_slot_occurrences
- schedule_item_id: VARCHAR(64) NOT NULL — soft reference to schedule_items (holds
  the `schedule_items.id` UUID as text)
- asset_id, title, scheduled_at, duration_seconds: from the occurrence
- approved_by_operator_id: VARCHAR(80) NOT NULL
- approved_at: TIMESTAMP WITH TIME ZONE
- conflicts_found, gaps_found: INT DEFAULT 0
- dispatch_status: VARCHAR(20) CHECK IN ('pending','queued','acknowledged','error','cancelled')
- dispatch_error_detail: TEXT
- dispatch_timestamp: TIMESTAMP WITH TIME ZONE
- operator_notes: TEXT (the operator's free-text reason from the commit request)
- rollback_reason: TEXT, rolled_back_at: TIMESTAMP WITH TIME ZONE (migration 0041 — set only on
  rollback; the operator's undo reason + when, distinct from operator_notes)
- created_at, updated_at: TIMESTAMP WITH TIME ZONE

> **Implementation note (slice 1, as built).** The reference columns carry **no
> DB foreign keys** — they are soft string references with application-layer
> integrity, matching the schedule module's existing convention
> (`schedule_items.asset_id` has no FK either). Three reasons: (1)
> `schedule_item_id` holds a UUID PK value as text, and a VARCHAR FK to a UUID
> column is a type mismatch Postgres rejects; (2) cross-table/cross-schema FKs add
> migration-ordering and SQLite-enforcement friction; (3) this is an **audit
> record** that must outlive cancellation/deletion of the item it references.
> `dispatch_status` includes `'cancelled'` because the rollback endpoint (§4) sets
> it — the original four-value CHECK would have rejected that write. The index is
> composite `(channel_id, approved_at)` so the "recent commits" list (filter by
> channel, order by commit time) is covered.

#### ScheduleConflict (model only)

`python
class ScheduleConflict(BaseModel):
    existing_schedule_item_id: str
    existing_asset_id: str
    existing_asset_title: str
    existing_scheduled_at: datetime
    existing_duration_seconds: int
    proposed_scheduled_at: datetime
    proposed_duration_seconds: int
    overlap_seconds: int
`

### Migrations

**Migration 0040_commit_to_air_reports** (civiccast/schedule/migrations/versions/0040_commit_to_air_reports.py):
Create the commit_to_air_reports table (PK + composite index, no FKs — see the implementation note
above). No changes to existing tables. **As built:** this section originally specified `0038` parented
on `0037_asset_meeting_body`, but since the spec was written the S9 reliability work added
`0038_reliability_fields` and the S8 alerting work added `0039_alerting_and_sinkhealth` (the current
head). On the **single global alembic chain** this migration therefore takes the next monotonic number
`0040` and parents on the real head `0039_alerting_and_sinkhealth`. The head pins in
`tests/live/test_real_postgres.py` and `tests/test_schema_check.py` advance to `0040_commit_to_air_reports`.

---

## 4. API surface

### Staff endpoints (require_any_role=["publish_operator", "setup_admin"])

Write/commit endpoints require `publish_operator` or `setup_admin`. Read-only diagnostic
listing/detail endpoints additionally accept `support_admin`.

#### POST /api/staff/playout/prepare-commit

Dry-run without approval.

Request: channel_id, occurrence_id, schedule_item_id
Response (200): CommitToAirPlan with dry_run_passed and conflicts_detected
Response (404): not found
Response (422): asset unplayable

#### POST /api/staff/playout/commit

Operator approval: execute dry-run again, persist report, dispatch to egress.

Request (as built): `channel_id`, `occurrence_id`, `schedule_item_id`, `operator_notes`, and an
optional `plan_id` echoed for audit correlation. **Why not `plan_id`-only:** a server-side plan cache
is fragile — lost on restart and **not shared across uvicorn workers**, so a commit could land on a
worker that never saw the prepare. Re-running the dry-run is cheap and is already step 1 of the commit
workflow, so the commit echoes the identifying params the prepare response already carries instead.
The re-run dry-run is the authoritative race check, which subsumes the spec's original `400 plan
expired` (there is no stored plan to expire).
Response (201): CommitToAirReport with dispatch_status="queued" (or "error" in the body if the engine
nudge failed — the approval is still durably recorded)
Response (409): a conflict appeared since review (CommitConflictError with conflicts)
Response (422): the asset became unplayable since review (CommitConflictError, missing media)
Response (404): schedule item not found

#### GET /api/staff/playout/commits

Read-only: `require_any_role=["publish_operator", "setup_admin", "support_admin"]`.
List reports by channel, optionally filtered by date range.

Query params: channel_id (required), start_at, end_at, limit (default 50)
Response (200): array of CommitToAirReport

#### GET /api/staff/playout/commits/{report_id}

Read-only: `require_any_role=["publish_operator", "setup_admin", "support_admin"]`.
Response (200): CommitToAirReport
Response (404): not found

#### POST /api/staff/playout/rollback/{report_id}

Operator undo: cancel the linked schedule item, hand back to the engine (a `reload` so the pull-based
resolver drops the cancelled item and falls to slate / the next program), and mark the report
`cancelled`. An already-removed schedule item is tolerated (the airing is undone regardless). Write
roles `require_any_role=["publish_operator", "setup_admin"]` (support_admin may read but not roll back).

Request: reason (required)
Response (200): report with dispatch_status="cancelled", rollback_reason + rolled_back_at set
Response (404): report not found

---

## 5. Operator UI surface

### Playout / Schedule Commit screen (phone-first)

1. Channel selector (dropdown/segmented control) with "On Air" / "Off Air" badge.

2. Upcoming occurrences list (next 24h):
   - Time, asset title, duration, status badge ("Ready", "Conflict", "Missing media", "Committed").
   - Tap for detail.

3. Detail modal:
   - Asset title, duration, start time.
   - Dry-run result panel: conflicts table, missing media detail, gaps timeline.
   - Action buttons: "Prepare commit", "Approve & air it" (enabled only if dry_run_passed=true), "Cancel".

4. Recent commits panel (collapsible):
   - Last 10 commits, sortable.
   - Tap for read-only detail and rollback option.

5. Rollback modal:
   - Confirm dialog.
   - Reason text field.
   - "Rollback" button (destructive).

### ChannelOpsScreen integration

Add **Schedule tab** showing current on-air item, next 3 upcoming occurrences with commit status,
and quick "Commit" button.

---

## 6. Behavior and algorithms

### Dry-run (conflict and missing-media detection)

Detects missing media, schedule conflicts, and gaps. Returns CommitToAirPlan without approval.

1. Load the schedule item; if not found, raise ScheduleItemNotFoundError.
2. Load the asset and check playability:
   - If missing, set passed=False, missing_detail="Asset ... does not exist"
   - If state not in (validated, recorded), set passed=False
   - If no file_path, set passed=False
   - If duration_seconds is None, set passed=False
3. Detect conflicts (active check, not just EXCLUDE):
   - If asset and duration_seconds valid, search for overlapping items on channel
   - If found, add to conflicts_detected[], set passed=False
4. Detect gaps (informational, does not fail):
   - Find prior item on channel before this scheduled_at
   - If gap > 1.0 second, add to gaps_detected[]

Return CommitToAirPlan with dry_run_passed, conflicts_detected, missing_media_detail, gaps_detected.

### Commit workflow

1. Re-run dry-run (race check).
2. If still failed or new conflicts detected, raise ConflictError.
3. Persist CommitToAirReport with dispatch_status="pending".
4. Dispatch to egress via PlayoutDispatcher (engine nudge — see the note below).
5. Set dispatch_status="queued", dispatch_timestamp=now.
6. Return stored report.

If dispatch fails, set dispatch_status="error" and a non-leaking dispatch_error_detail (the
exception type, never its message — which could carry a DB DSN; the full exception is logged
server-side). The pending report is persisted **before** the dispatch attempt so a crash mid-dispatch
still leaves a durable record that the operator approved this airing.

### Dispatch integration with egress

> **As built (slice 3) — reconciled with the pull-based engine.** The original text below assumed a
> push model: build an `EgressSourcePlan`, write it as the channel's "active source," and have a
> `SourcePlanProvider` read that slot every 2s. The real engine is **pull-based** — the proven
> `ScheduleSourcePlanProvider` / `build_source_plan_from_schedule` resolve a channel's source plan
> *dynamically from its scheduled premiere items* every cycle and re-resolve on a `reload`. There is
> no "active source" slot to write. So the committed item (already in `schedule_items`) needs no plan
> build/persist; the honest, minimal dispatch — and exactly what `channel_automation` already does —
> is to **enqueue an existing `EgressCommand`**: `reload` when the channel is running (on air /
> starting / slate / transitioning) so the resolver picks up the committed program, or `start` when
> the channel is dark, so a committed program brings it up. The resolver remains the single source of
> truth for what airs. S4 adds **no** new command action and does **not** extend the
> `EgressCommand.action` enum — S5 owns that enum and its `takeover`/`handback` additions (their
> migration takes the next free number at build time, **not** the spec's stale `0039`, which the S8
> alerting work already used). Live-source handback is therefore an S5 concern, out of S4 scope.

---

## 7. Proof tier: current rung and advancement path

### Current rung

**Contract (rung 0):** Dry-run logic, conflict detection, API contracts are code + unit tests.
No runtime egress proof yet (dispatching wired to existing supervisor, which is **machine-proven**,
but integration is **contract-only** until soak includes commit workflows).

### Advancement to Machine-proven (rung 2)

Testable units:
1. dry_run_commit() with synthetic fixtures (unit test).
2. execute_commit() with mock dispatcher (unit test).
3. API endpoints (Playwright e2e): prepare, commit, list, rollback.
4. Egress integration: commit a schedule item, observe supervisor state change.

Soak gate: Add commit workflows to 24h unattended soak (materialize → prepare → commit → air → rollback).

Honest claim boundary: Commit API and logic are **machine-proven** once soak exercises full workflow
without operator intervention. Egress automation (supervisor, daemon) already **machine-proven**;
this section adds schedule gate in front.

---

## 8. Test plan (unit / API / e2e + soak gate)

### Unit tests

- test_dry_run_no_conflict: healthy asset, no overlap
- test_dry_run_missing_asset: asset missing
- test_dry_run_unvalidated_asset: state="pending_ingest"
- test_dry_run_conflict_detected: overlapping item
- test_dry_run_gap_detected: prior item ends before this
- test_commit_executes_dispatch: dispatcher called
- test_commit_rerace_check: item cancelled between prepare and commit
- test_rollback_cancels_item_and_signals_handback

### API tests

- test_post_prepare_commit_success: returns plan with dry_run_passed=true
- test_post_prepare_commit_conflict: returns plan with conflicts_detected=[]
- test_post_prepare_commit_missing_media: returns plan with missing_media_detail
- test_post_commit_success: approved_at set, dispatch_status="queued"
- test_post_commit_race_conflict: commit fails if conflicts detected
- test_get_commits_by_channel: list pagination, sorting
- test_post_rollback_success: dispatch_status="cancelled", ScheduleItem cancelled
- test_auth_require_publish_operator_role: a role without publish_operator/setup_admin cannot POST; support_admin may GET but not POST

### E2e integration test

- Wire real PlayoutSupervisor into dispatcher.
- Commit a schedule item.
- Verify supervisor's internal state reflects committed source.

### Soak gate

1. Define program slot running at known time.
2. Materialize slot.
3. Prepare commit for occurrence.
4. Approve and commit.
5. At scheduled boundary, verify channel switches to committed asset.
6. At +5 minutes, rollback commit.
7. Verify channel returns to fallback or next scheduled source.

Audit expectation: 0/0/0/0/0 (0 bugs, 0 unsafe transitions, 0 races, 0 rollback failures, 0 constraints).

---

## 9. DONE criteria

Commit-to-air section is "shipped" when:

1. Entities & models: All Pydantic models compile, have unit test coverage.
2. Persistence: Migration 0040 applies cleanly; table durable and queryable.
3. Dry-run logic: Detects missing media, conflicts, gaps. 5+ unit test scenarios.
4. API endpoints: All 5 endpoints respond with correct codes. Playwright tests cover happy/error paths. Auth enforced.
5. Egress integration: PlayoutDispatcher calls supervisor methods. Integration tests verify state changes.
6. UI: Phone-first Playout / Schedule Commit screen, integrated into ChannelOpsScreen. Walkthrough audit 0/0/0/0/0.
7. Soak gate: 24h unattended soak includes commit → air → rollback cycle. Zero errors, no manual intervention. Rung 2 earned.
8. Documentation: Section spec finalized, API documented (OpenAPI updated), UI flows in design system.

---

## 10. Dependencies and cross-refs

### Cross-refs to other sections

- S1 (Reference Station): StationProfile, hardware tiers (memory determines playout location).
- S2 (Headend Handoff): Egress sinks pre-configured; commit dispatch respects active profile, sink readiness.
- S3 (Commissioning Wizard): Ask operator to stage test slot before sign-off.
- S5 (Software Force Matrix): `OnAirLockState` (this section) is a **commit-approval lock only** — it
  prevents two operators committing conflicting schedules simultaneously and gates dispatch. It does
  **not** arbitrate runtime sources. Runtime source arbitration is the **supervisor priority model
  owned by S5** (emergency-slate > live-takeover > committed-schedule > filler). S5 does not coordinate
  through `OnAirLockState`; the two are independent at runtime.
- S6 (CG Bulletin Board): CG overlays may be scheduled; commit must detect CG conflicts.
- S8 (Health & Alerting): Commit failures surface as alert events; unhealth on committed source triggers rollback recommendation.
- S9 (Reliability): Unclean restart after commit must recover from report row (on startup, load recent reports, re-dispatch).

### Dependencies (blocking)

- ProgramLogMaterializer stability: Slot materialization must be reliable (proven in soak).
- ScheduleStore conflict detection: EXCLUDE constraint must work correctly (already tested).
- EgressCommand actions: the existing `start`/`reload` actions (already coded) are sufficient for
  S4 dispatch — S4 sets the committed source then reloads. S4 takes no dependency on
  `takeover`/`handback`; those are S5's runtime-arbitration concern (enum extended in migration 0039).

### Open decisions for Scott

1. Conflict presentation: When conflicts detected, should UI (A) block commit and require manual resolution (safest for PEG), (B) allow force-override with acknowledge + typed confirmation, or (C) auto-bump conflicting item's end time (risky)?
   Recommendation: (A) safest for MVP; force-override later if needed.

2. Gap detection threshold: Is silence > 1 second reportable, or configurable per station?
   Recommendation: Hardcode 1s for MVP; add config field in S1/S3 if needed.

3. Recommit without re-approve: If operator rolls back and immediately re-commits same occurrence, should it require full dry-run or fast-path?
   Recommendation: Always re-run dry-run (race-safe). Typically seconds pass; not a UX burden.

---

*Next: implementation begins with unit tests for dry-run logic, migrations, and Pydantic models.
Full soak validation after S1 (hardware setup) and S2 (headend config) are stable.*

---
## Comparative additions (incumbent PEG platform gap closure → S18)
This section gains **query-driven auto-scheduling** + **block/daypart scheduling** (S18 gaps 1, 4 —
`SavedSearch`/`AutoScheduleRule`/`ScheduleBlock`, migration `0049`) and **underwriting spot
break-insertion** into the program log (S18 gap 10, `0057` — renumbered from planned `0055` per
[RECONCILIATION D17](../RECONCILIATION.md) after S23 took the on-disk `0055`). See
the S18 comparative appendix.
