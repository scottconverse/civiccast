# S5 — Software Force Matrix: Wiring Live Takeover & Manual Route Audit

> **Status:** Built for v3.0.0-beta1; live-station proof remains external.
> **Relates to:** Master §2 (PEG automation coverage), §4 gap 7 (Force Matrix), §5 (proof ladder), §6 (entity model).

---

## 1. Goal & PEG automation rationale

### What incumbent PEG platform does
the incumbent PEG platform's **Force Matrix** is a core feature (their own term) for manual source→destination routing override. A meeting is interrupted; an operator takes live video and forces it to air, bypassing the automated schedule. The operator later returns control to the scheduled content with a "return to schedule" button. The Force Matrix logs who did it, what they took over, and when they handed back — creating an audit trail for regulatory compliance (PEG stations answer to franchise authorities).

### The gap
CivicCast already has the engine: supervisor.request_live_takeover() and
equest_live_handback() in egress/supervisor.py:53–72 + uild_live_takeover_source_plan() in egress/live_takeover.py:12–57. These implement the **priority model** (emergency-slate > live-takeover > committed-schedule > filler) correctly. But they are **completely unwired**:
- No API endpoint to invoke them.
- No CLI command.
- No operator UI.
- No audit record of who took over, when, from what, to what, and why.

### What 3.0 delivers
Wire the engine into an operational Software Force Matrix:
1. **API endpoints** POST /api/staff/egress/channels/{channel_id}/takeover (invoke) and DELETE .../takeover (handback) with role-based access.
2. **Operator UI** (mobile-first): one-touch "Take Live" button (with source selector if multiple ingest paths available) + "Return to Schedule" button.
3. **Takeover audit** (TakeoverSession) with operator, reason, source/target, timestamp, and handback record.
4. **CLI command** civiccast live-takeover take / civiccast live-takeover return for scripted/headless scenarios.
5. **Facility router integration** (preview): coordinate with the facility router (§2.1) so that a channel takeover can pre-stage an accompanying AV router command (e.g., camera→preview monitor) without sending it until confirmed.

---

## 2. Current state (code-grounded)

| Component | Location | Status |
|-----------|----------|--------|
| Live takeover engine | egress/supervisor.py:53–72 | **Built** (rung-0 contract: code exists, unit-tested); **NOT exercised by the in-flight soak** (the soak runs scheduled automation, not takeover). Implements priority model: emergency-slate > live-takeover > committed-schedule > filler. |
| Live source plan builder | egress/live_takeover.py:12–57 | **Built**, tested (	ests/egress/test_live_takeover.py). Validates ingest-path health and returns safe playback URL. |
| Fallback slate engine | egress/supervisor.py:74–87 | **Built** (emergency overlay / forced slate); unwired for operator UI/API. |
| EgressCommand model | egress/models.py:333–343 | **Exists** with actions: ["start", "stop", "reload", "drain"]. **Action enum must extend** to include "takeover" and "handback". |
| Queue + daemon loop | egress/router.py:575–590 (queue), egress/automation.py (consume) | **Built**; the loop is supervised in soak. |
| Facility router preview | acility/models.py + acility/router.py | **Built (planning tier only)**; no live execution. Can query inventory, preview commands, no send. |
| Audit trail | None | **Net-new**; stub only. No TakeoverSession entity, no per-operator record. |
| Operator screens | rontend/components/ChannelOpsScreen | **Built** (start/stop/reload buttons exist); no takeover/handback buttons yet. |

---

## 3. Entities / data model & migrations

### New entities (add to egress/models.py)

**pythondef**
class TakeoverSession(BaseModel):
    """Audit record for one live-takeover and handback cycle."""
    
    session_id: str                    # uuid
    channel_id: str
    source_ref: str                    # source_ref from EgressSourceSegment
    source_label: str                  # e.g. "Live: Council chamber"
    operator_id: str                   # from OperatorIdentity
    operator_name: str | None          # display name
    reason: str | None                 # operator-provided reason
    took_over_at: datetime             # UTC
    returned_at: datetime | None       # NULL if still live
    source_plan_json: str              # serialized EgressSourcePlan (immutable record)
    notes: str | None                  # operator notes on handback


class ManualRouteState(BaseModel):
    """Current takeover state per channel (runtime only, not persisted to DB)."""
    
    channel_id: str
    active_session: TakeoverSession | None
    can_takeover: bool                 # True if live source is ready
    can_return: bool                   # True if currently in takeover


class TakeoverAuditRecord(Base):
    """Durable audit log row."""
    
    __tablename__ = "takeover_audit"
    
    session_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    channel_id: Mapped[str] = mapped_column(String(80))
    source_ref: Mapped[str] = mapped_column(String(160))
    source_label: Mapped[str] = mapped_column(String(160))
    operator_id: Mapped[str] = mapped_column(String(120))
    operator_name: Mapped[str | None] = mapped_column(String(160))
    reason: Mapped[str | None] = mapped_column(Text)
    took_over_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    returned_at: Mapped[datetime | None] = mapped_column(DateTime)
    source_plan_json: Mapped[str] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
**enddef**

### Migrations
S5 owns a single migration on the global alembic chain. **As built: `0042`** — the spec text said
`0039`, but `0038`/`0039` were taken by the S9/S8 work and `0040`/`0041` by S4, so this takes the next
monotonic number and parents on the real head `0041_commit_rollback_fields`:
- **0042_takeover_audit_and_command_action.py**: (a) Create the `takeover_audit` table (the model name
  is authoritative — the earlier "takeover_sessions" prose was loose) with a composite index on
  (channel_id, took_over_at) for audit queries; AND (b) extend the EgressCommandDb.action CHECK to
  include "takeover" and "handback" (from "('start', 'stop', 'reload', 'drain')" to
  "('start', 'stop', 'reload', 'drain', 'takeover', 'handback')") — via `op.batch_alter_table` so the
  CHECK rebuild also works on SQLite (mirrors 0034). Both head pins advance to `0042`. The
  `took_over_at`/`returned_at` columns are `DateTime(timezone=True)` (the illustrative model snippet
  above used a naive `DateTime`; the codebase is UTC-aware everywhere). S5 owns the
  `EgressCommand.action` enum migration; S4 dispatch reused existing `start`/`reload` and added no action.

### Extend existing entity

**EgressCommand (models.py:333–343)**
- Extend action literal: EgressCommandAction = Literal["start", "stop", "reload", "drain", "takeover", "handback"]

---

## 4. API surface

### New endpoints

**1. Begin live takeover**
`
POST /api/staff/egress/channels/{channel_id}/takeover
Content-Type: application/json
Authorization: Bearer <token>
X-Operator-Role: meeting_operator (or setup_admin)

{
  "ingest_path_id": "gov:relay",              # optional; uses live subsystem default if omitted
  "reason": "Breaking news - live update",    # optional
  "duration_seconds": 3600.0                  # optional; default 1h
}

Response: 202 Accepted
{
  "session_id": "takeover-uuid-...",
  "channel_id": "gov",
  "source_ref": "gov:relay",
  "source_label": "Live: Council chamber",
  "operator_id": "alice@example.com",
  "operator_name": "Alice",
  "reason": "Breaking news",
  "took_over_at": "2026-06-13T14:30:45Z",
  "command_id": "egress-uuid-..."           # EgressCommand queued
}

Errors:
- 400: ingest_path_id not found / ingest path disabled / not ready
- 404: channel not found
- 503: database not ready
`"

**2. Return from takeover to schedule**
`
DELETE /api/staff/egress/channels/{channel_id}/takeover
Authorization: Bearer <token>
X-Operator-Role: meeting_operator

Query params: notes=<optional>

Response: 200 OK
{
  "session_id": "takeover-uuid-...",
  "returned_at": "2026-06-13T14:35:22Z",
  "notes": "Fire drill concluded",
  "command_id": "egress-uuid-..."           # EgressCommand queued
}

Errors:
- 404: not currently in takeover
- 503: database not ready
`"

**3. Query current takeover state (read-only)**
`
GET /api/staff/egress/channels/{channel_id}/takeover-state
Authorization: Bearer <token>

Response: 200 OK
{
  "channel_id": "gov",
  "active_session": {
    "session_id": "takeover-uuid-...",
    "operator_id": "alice@example.com",
    "source_label": "Live: Council chamber",
    "took_over_at": "2026-06-13T14:30:45Z",
    "can_return": true
  },
  "can_takeover": true,
  "can_return": true
}

or

{
  "channel_id": "gov",
  "active_session": null,
  "can_takeover": true,
  "can_return": false
}
`"

**4. Query takeover audit log (staff/compliance)**
`
GET /api/staff/egress/channels/{channel_id}/takeover-audit
Authorization: Bearer <token>
X-Operator-Role: setup_admin

Query params: 
  since=<datetime ISO 8601>  (optional; default 30 days)
  limit=100

Response: 200 OK
{
  "channel_id": "gov",
  "records": [
    {
      "session_id": "...",
      "operator_id": "alice@example.com",
      "operator_name": "Alice",
      "reason": "Breaking news",
      "took_over_at": "2026-06-13T14:30:45Z",
      "returned_at": "2026-06-13T14:35:22Z",
      "duration_seconds": 337,
      "notes": "Fire drill concluded"
    },
    ...
  ]
}
`"

### Role enforcement (\
equire_any_role\)

| Endpoint | Role(s) |
|----------|---------|
| POST \/takeover\ | \meeting_operator\, \setup_admin\ |
| DELETE \/takeover\ | \meeting_operator\, \setup_admin\ |
| GET \/takeover-state\ | \meeting_operator\, \setup_admin\ |
| GET \/takeover-audit\ | \setup_admin\ |

---

## 5. Operator UI surface

### Mobile-first ChannelOpsScreen enhancements

**Current state:** start/stop/reload/drain buttons + health + state machine.

**Add:**

1. **Takeover panel** (visible when channel is on-air)
   - Button: **"Take Live"** (green, prominent)
     - On tap: modal selector for ingest path (if multi-path) + optional reason field
     - Disabled if: live subsystem not ready, no ingest paths enabled
     - On confirm: POST takeover endpoint
   - Display: current session badge (if in takeover)
     - Shows: operator name + time elapsed + "Return to Schedule" button (red)
     - On tap: confirmation → DELETE takeover endpoint with optional notes

2. **Takeover status line** (below state)
   - When on-air normally: "Scheduled playout"
   - When in takeover: "🔴 Live takeover (Alice, 5 min) — [Return to Schedule]"

3. **Audit log view** (staff only, accessible from settings/support)
   - Sortable table: took_over_at | operator | reason | duration | notes
   - Filters: date range, operator
   - Export to CSV for compliance

### Facility router integration (preview mode)

If a facility router endpoint is configured:
- When operator confirms "Take Live", optionally show: "Preview: would route [Input: Camera 1] → [Output: Main TX]"
- No actual router command is sent (preview-only, per master §3).
- Router command execution is a separate manual step or a future S12-style automation.

---

## 6. Behavior / algorithms

### Takeover flow

1. **Operator POST \/channels/{channel_id}/takeover\**
   - Validate: channel exists, enabled, on-air.
   - Validate: ingest_path_id (if provided) is in live subsystem and READY state.
   - Call \live_subsystem.get_ingest_plan(channel_id)\ → select path.
   - Call \uild_live_takeover_source_plan(channel_id, ingest_plan, path_id, duration_seconds)\.
   - Create \TakeoverSession\ record (in-memory or persisted depending on tier).
   - Insert \TakeoverAuditRecord\ (durable).
   - **Queue \EgressCommand(action="takeover", channel_id, ...)\** → daemon.
   - Daemon picks it up in \utomation.py\ command loop.
   - Daemon calls \supervisor.request_live_takeover(channel_id, live_source_plan)\.
   - Supervisor stores live plan → at next source boundary, daemon switches to live.
   - Return 202 with session_id.

2. **Daemon consumes "takeover" command**
   - Fetch live source plan from request or TakeoverSession record.
   - Call \supervisor.request_live_takeover()\.
   - Emit \EgressProofEvent\ (existing) with event_type="live_takeover_started".

3. **Operator DELETE \/channels/{channel_id}/takeover\**
   - Validate: channel is currently in takeover (active_session is not null).
   - Queue \EgressCommand(action="handback", ...)\.
   - Update \TakeoverAuditRecord.returned_at = now()\.
   - Return 200 with session summary.

4. **Daemon consumes "handback" command**
   - Call \supervisor.request_live_handback(channel_id)\.
   - Supervisor clears live plan → at next boundary, daemon returns to scheduled.
   - Emit \EgressProofEvent\ with event_type="live_handback_completed".

### Priority model (already in supervisor) — S5 is the runtime source arbiter

**S5 owns runtime source arbitration.** The supervisor priority model is the single runtime
arbiter of what plays on a channel: **emergency-slate > live-takeover > committed-schedule >
filler**. The emergency-slate priority (top rung) comes from **S11** (an EAS-forced slate
pre-empts everything). S5 wires live-takeover into the second rung.

From \supervisor.py:_next_source_plan()\:
1. If forced_slate_reason is set → return slate (emergency-slate, S11 priority).
2. Else if live_plan is set → return live (live-takeover).
3. Else return lookahead committed-schedule source.
4. Else filler.

**No change needed to supervisor priority:** it is correct. We just wire the UI/API to invoke it.

**S4 and S5 are independent at runtime.** S4's `OnAirLockState` is a commit-approval gate only
(it prevents two operators committing conflicting schedules and gates dispatch); it does **not**
arbitrate runtime sources and S5 does **not** coordinate through it. Runtime arbitration is this
supervisor priority model alone.

### Audit record lifetime

- Created at takeover with \
eturned_at = NULL\.
- Updated at handback with \
eturned_at = now()\.
- Immutable thereafter (for compliance).
- Retained forever (for audit and regulatory queries).

---

## 7. Proof tier: current rung + how to advance it

### Current rung: **Contract-tested (rung 0)**

- Live takeover logic unit-tested (\	ests/egress/test_live_takeover.py\).
- Supervisor state machine unit-tested (\	ests/egress/test_daemon.py\).
- **The in-flight 24h soak does NOT exercise live takeover** — it runs scheduled automation
  (midnight crossover, encoder reboot, drain/stop/start). Takeover/handback cycles are a planned
  *addition* to the soak (see §7 rung-2 and §8), not something the current soak proves.
- API endpoints do not yet exist → no proof of HTTP contract.

### Advance to Lab (rung 1)

1. **Unit tests (contract)**
   - \	ests/egress/test_takeover_api.py\: POST/DELETE endpoints with mock supervisor + store.
   - \	ests/egress/test_takeover_audit.py\: audit record CRUD, time tracking.
   - Test role enforcement (\
equire_any_role\ check).
   - Test error cases: ingest path not ready, channel not found, handback when not in takeover.

2. **API integration test**
   - Spin up FastAPI test client with real EgressStore.
   - Queue takeover → inspect EgressCommand in store.
   - Verify \TakeoverAuditRecord\ is persisted.
   - Verify subsequent daemon loop consumes "takeover" command and calls supervisor.

3. **Soak integration** (extend the 24h soak)
   - Add takeover/handback steps to the virtual headend lifecycle (e.g. every 30 min during soak).
   - Verify command queue is drained, supervisor state transitions, audit records accumulate.
   - Verify handback cleanly returns to schedule without dropout.

### Advance to Machine (rung 2)

- Run the full 24h soak **with simulated takeover/handback cycles** every 30 min.
- Verify: no process leaks, no daemon crashes, clean transitions, audit records are durable across daemon restart.

### Proof boundary statement

"Live takeover API routes commands to the supervisor and logs audit records. We prove contract (unit tests) and lab (simulated multi-takeover soak). Field proof (rung 3) requires a station operator to intentionally use the feature during a real broadcast; the first station handoff will provide that. Hardware proof (rung 4) is satisfied if the facility router can coordinate (read-only in 3.0)."

---

## 8. Test plan & audit expectations

### Unit tests (0 minutes, runs in CI)

**File: \	ests/egress/test_takeover_api.py\**
- ✓ POST takeover with valid ingest path → 202, TakeoverSession created, EgressCommand queued.
- ✓ POST takeover with invalid path_id → 400.
- ✓ POST takeover with disabled path → 400.
- ✓ POST takeover with degraded path health → 400 (fails-closed).
- ✓ POST takeover when not on-air → 404.
- ✓ DELETE takeover when active → 200, returned_at set, EgressCommand queued.
- ✓ DELETE takeover when none active → 404.
- ✓ GET takeover-state returns accurate ManualRouteState.
- ✓ GET takeover-audit returns records since date, sorted by took_over_at desc.
- ✓ Role enforcement: meeting_operator can POST/DELETE; setup_admin can POST/DELETE/GET audit; meeting_operator cannot GET audit.

**File: \	ests/egress/test_takeover_audit.py\**
- ✓ TakeoverAuditRecord persists to DB.
- ✓ Duration is correctly calculated (returned_at - took_over_at).
- ✓ Operator info is immutable (snapshot at takeover time).
- ✓ Source plan JSON is preserved (for compliance traceability).

**File: \	ests/egress/test_takeover_daemon.py\**
- ✓ Daemon consumes "takeover" command → calls supervisor.request_live_takeover().
- ✓ Daemon consumes "handback" command → calls supervisor.request_live_handback().
- ✓ EgressProofEvent is emitted on takeover/handback.

### Integration tests (1–2 minutes, runs in CI)

**File: \	ests/egress/integration_takeover.py\**
- ✓ End-to-end: POST takeover → simulate daemon tick → verify source switches to live in supervisor → verify audit record.
- ✓ End-to-end: DELETE handback → simulate daemon tick → verify supervisor returns to scheduled.
- ✓ Concurrent takeover and scheduled boundary: handback at exact moment a scheduled item starts; no dropout, no race.

### Soak test (24h, manual)

**In \	ests/egress/virtual_headend_lifecycle.py\ (or new \	est_soak_with_takeover.py\)**
- Existing: 24h loop with midnight crossing, encoder reboot, drain/stop/start cycles.
- **Add:** every 30 min, pick a random channel, POST takeover (5 min), DELETE handback. Verify:
  - ✓ Command queue drains cleanly.
  - ✓ No process leaks.
  - ✓ Audit records accumulate.
  - ✓ No state machine inconsistency (e.g., stuck in TRANSITIONING).

### Audit expectations (all must reach 0/0/0/0/0 per master §12)

| Audit | Barrier | Resolution |
|-------|---------|-----------|
| **Correctness** | Does takeover actually switch source? | Unit test: supervisor mock. Soak: verify proof events. |
| **UX** | Is the button visible and responsive? | Playwright walkthrough (S3). |
| **Docs** | Are the new endpoints documented? | \docs/spec/3.0/sections/S5-...md\ (this file). |
| **Tests** | Are all error paths covered? | Unit test: 100% coverage of takeover_api, takeover_audit modules. |
| **Runtime** | Does the daemon handle "takeover"/"handback" commands? | Soak: verify EgressProofEvents. |

---

## 9. DONE criteria (what "shipped" means for this section)

All of the following must be true:

1. ✓ \EgressCommand.action\ enum extended to \["start", "stop", "reload", "drain", "takeover", "handback"]\.
2. ✓ \TakeoverSession\, \ManualRouteState\, \TakeoverAuditRecord\ entities defined in \egress/models.py\.
3. ✓ Migration created and verified (`0042_takeover_audit_and_command_action`: takeover audit table + `EgressCommand.action` enum extension).
4. ✓ API endpoints implemented:
   - \POST /api/staff/egress/channels/{channel_id}/takeover\ (202)
   - \DELETE /api/staff/egress/channels/{channel_id}/takeover\ (200)
   - \GET /api/staff/egress/channels/{channel_id}/takeover-state\ (200)
   - \GET /api/staff/egress/channels/{channel_id}/takeover-audit\ (200, admin only)
5. ✓ CLI commands implemented:
   - \civiccast live-takeover take --channel gov --path gov:relay --reason "..."\ → queues command.
   - \civiccast live-takeover return --channel gov --notes "..."\ → queues command.
6. ✓ Operator UI (ChannelOpsScreen): "Take Live" button + current session display + "Return to Schedule" + audit log view.
7. ✓ Daemon (\utomation.py\) consumes "takeover"/"handback" commands and calls \supervisor.*()\.
8. ✓ All unit + integration tests passing (≥95% code coverage for takeover modules).
9. ✓ 24h soak with takeover/handback cycles completes without regressions.
10. ✓ Audit log proof: can export a compliance report from a test station showing who took over, when, why, how long.
11. ✓ Playwright walkthrough confirms UX flows (take → return, error states, role enforcement).

---

## 10. Dependencies & cross-refs to other sections; Open decisions for Scott

### Dependencies

- **Live subsystem** (\civiccast/live/\): S5 depends on live ingest plan + readiness probe. Already shipped (master §3). No blocker.
- **Egress supervisor** (\egress/supervisor.py\): S5 wires existing methods. No code change to supervisor itself; no blocker.
- **Facility router** (\acility/\): S5 can optionally preview router commands (read-only). Preview API is shipped. Execution is future. No blocker for 3.0.
- **Auth / roles** (\uth/\): \
equire_any_role\ already supports meeting_operator / setup_admin. No blocker.
- **Master §5 proof ladder**: S5 advances from contract (rung 0) → lab (rung 1) in this build; machine (rung 2) in extended soak; field (rung 3+) on first station. Honest boundary: we claim lab only on 3.0 release.

### Interactions with other sections

| Section | Interaction |
|---------|-------------|
| S1 (Reference station) | StationBoxProfile may record "has live ingest hardware" flag; takeover is gated on it. |
| S2 (Headend) | No direct dependency. Takeover is live → supervisor priority model handles it. |
| S3 (Commissioning) | Commissioning wizard (S3) should test "Can I take live?" as a readiness check. |
| S4 (Commit-to-Air) | Independent at runtime. Takeover is runtime source arbitration (S5's supervisor priority model); Commit-to-Air is schedule materialization. S4's `OnAirLockState` is a commit-approval gate only — it does not arbitrate runtime sources, and S5 does not coordinate through it. |
| S6 (CG Bulletin) | Independent. Both can run; CG may overlay takeover video. |
| S8 (Health & alerting) | If takeover is active and operator forgets to handback, S8 should flag "still in takeover after 2h" as a warning. |
| S11 (EAS) | S11 owns the top (emergency-slate) priority. If EAS forces a slate, it pre-empts live-takeover (emergency-slate > live-takeover > committed-schedule > filler). No code change needed; the supervisor priority model handles it. |
| S12 (OTT apps) | OTT app should show current takeover state (observer-only, no action). |

### Open decisions for Scott

1. **Audit retention policy:** Forever (compliance-safe, storage cost), 1 year (balance), or configurable? *Recommend forever; PEG stations must answer to franchise authorities.*
2. **Facility router auto-trigger:** When operator takes live, should we auto-queue a preview-level router command (no send), or require the operator to manually arm it? *Recommend manual arm in 3.0; auto can come as a post-launch rule config.*
3. **Takeover reason field:** Should the operator be *required* to provide a reason, or optional? *Recommend optional in 3.0 (mobile UX); future: configurable per station policy.*
4. **Duration default:** Currently hardcoded 1h. Should it be configurable (per-station, per-channel, per-operator role)? *Recommend hardcoded 1h in 3.0; adaptive timeout is future.*
5. **Fallback slate priority:** Should an EAS-forced fallback slate *automatically* cancel an active takeover, or require the operator to manually handback first? *Recommend auto-cancel with audit record (emergency > takeover). Justify in EAS section (S11).*

---

*This section is ready for code review. Implementation order: unit tests + entity models + migrations → API endpoints → daemon integration → CLI → UI → soak. Estimated effort: ~60–80 hours (API + tests, assuming supervisor/audit store are straightforward).*

---
## Comparative additions (incumbent PEG platform gap closure → S18)
The **auto-schedule compiler** (S18 gap 1, migration `0049`) materializes saved-search rules into
`schedule_items` under the force-matrix arbitration + OnAirLock commit gate. See
the S18 comparative appendix.
