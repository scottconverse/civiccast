# S14 — Analytics / Audience Measurement

> **Scope:** A first-class audience-measurement capability for the PEG station: a durable
> viewership-event store + rollups, a four-panel staff dashboard, CSV **and** board-ready PDF
> export, and the as-run / proof-of-performance reports built off the program log and schedule.
> Improves on the incumbent PEG workflow's cloud-telemetry-gated "Audience Measurement" by being self-hosted with **no
> paid-cloud-CDN dependency**.
>
> **Status:** Built on branch `feat/s14-analytics-durable` (PR open against `main`, not yet
> merged) — migration `0076_analytics_viewership`, `PostgresAnalyticsStore` +
> `AnalyticsRollupWorker`, the role-gated `/reports/overview` + `/rollups` + `/export.csv` +
> `/reports/board-pdf` endpoints, and the four-panel operator dashboard (toolbar, bar +
> time-series charts, stats + expandable table, CSV/PDF export, honest "telemetry off" state) all
> exist and are tested (unit + API + rollup-worker + Alembic up/down/head + vitest). **Not yet
> built:** OTT/embedded beacon parity (the Roku/tvOS/Fire TV/Android TV/mobile shells do not emit
> analytics events at all today — greenfield, no existing pattern to extend), opt-in coarse geo
> derivation (the `geo_bucket` column exists, off by default per the recommended §10 decision, but
> nothing populates it), the VOD-24h/Live-30-min "hourly for a single selected day" auto-switch in
> the UI (the API supports `bucket=hour`; the toolbar does not yet auto-select it), and the master
> §12 24/72h soak run (no soak infrastructure was exercised this session). The real-Postgres
> migration up/down was verified against SQLite + a full local chain replay, not against
> `testcontainers`/Docker (unavailable in this environment) — `tests/live/test_real_postgres.py`'s
> pinned head assertion was updated to `0076_analytics_viewership` but has not itself been run.
>
> **Disposition:** *extended* — the playback-beacon → aggregate-report chain (`analytics/`, the
> portal `HlsPlayer`/`analytics.ts` emitter, the hardened public ingest endpoint) is now backed by
> durable Postgres storage instead of a JSON file. This section resolves the **"Analytics /
> Audience Measurement has no owning section"** gap flagged for Scott in `RECONCILIATION.md`
> (§"Gaps flagged for Scott") — see that file for the closure note. As-run / proof-of-performance
> reporting (the Schedule Report + Shows Report equivalents named in §4/§8d below) is **not**
> duplicated here: `civiccast/reporting` (S18/S23, migration `0055_asrun_and_epg`) already ships
> `GET /api/staff/reports/as-run`, `/shows`, `/hours-by-category`, and CSV/XML export, gated on
> `support_admin` — functionally equivalent to (and, with hours-by-category, broader than) the
> `/api/staff/reports/schedule` + `/shows` endpoints this section originally sketched in §4. S14
> cross-references that surface rather than re-implementing it.

---

## 1. Goal & PEG automation rationale

CivicCast must give a PEG station the audience numbers it uses to **make its own case** — justify
budgets to city council, strengthen a franchise-renewal needs-assessment, tell an impact story in
a board or annual report, and show the spike a live event drew. This is **self-driven advocacy,
not a compliance mandate**: there is no FCC or franchise rule that requires PEG viewership
reporting, so the product's job is to make the numbers that matter *easy to produce and credible*,
never to over-instrument residents.

The metrics that matter to a PEG operator, ranked (drives the dashboard's defaults and the PDF):
total reach → top VOD content → live-event peaks (peak concurrent) → watch-time → year-over-year
trend → geography (wanted, but rarely available — see §7/§8 privacy posture).

### PEG automation coverage target (sourced; do not re-research)

The incumbent PEG platform's **Audience Measurement** reports **exactly two metrics — Viewer Count and Time
Viewed** — for **both VOD and Live**, from **cloud CDN telemetry**. VOD rolls up on **24-hour**
buckets; Live on **30-minute** buckets (hourly when a single day is selected). It is surfaced as a
staff dashboard with a toolbar (chart type + metric + date range + stream type), a top-N bar
chart, a time-series, a stats summary, and an expandable data table, with **CSV export (no PDF)**.
It does **not** do unique viewers, geographic breakdown, device/platform segmentation, or
linear/cable (QAM) viewership — **only IP streaming through cloud telemetry** is measured. Critically, the
whole feature is **hard-gated behind a paid cloud-CDN subscription**: no
cloud telemetry, no analytics.

The incumbent PEG platform separately ships **Reporting** — a **Schedule Report** and a **Shows Report**
(as-run / proof-of-performance playout logs) plus a **TV Guide X-List** (CSV program metadata).
Those are not "audience" numbers; they are *what aired, when*. S14 owns both halves.

### How S14 matches and beats it

| incumbent PEG platform | CivicCast S14 |
|---|---|
| Viewer Count + Time Viewed, VOD + Live | **same two metrics, same VOD/Live split** (the functional floor) |
| VOD 24h rollups, Live 30-min (hourly single-day) rollups | **same rollup cadence** |
| Dashboard: toolbar + bar chart + time-series + stats + data table | **same four-panel dashboard, phone-friendly** (§5) |
| CSV export, **no PDF** | CSV **plus a one-click board-ready PDF** (totals, top content, YoY, live peaks) — **differentiator (§8b)** |
| Live viewer count only (peak implied) | **explicit peak-concurrent metric for live** — already modeled today — **differentiator (§8c)** |
| Streaming only (no QAM/linear) | **streaming only — same honest scope (§7)**; we do not claim linear ratings |
| No geo / device / platform segmentation | optional **coarse, opt-in geo** with an explicit privacy posture (§8 last); not claimed as PEG automation coverage |
| **Hard-gated behind paid cloud CDN** | **self-hosted, NO paid-cloud-CDN dependency** — the biggest structural win (§8a) |
| Reporting: Schedule Report + Shows Report + X-List | **as-run / proof-of-performance reports off `programlog`/`schedule`** (§8d) |

**Proof tier:** contract → **lab** via tests + soak (rung 1; see §7). No off-ladder claims.

---

## 2. Current state (file:line)

A working privacy-safe **playback-beacon → aggregate-report** chain already exists end-to-end. The
gaps are durability (it is a JSON file, not a migrated table), the missing auth role on the staff
read, the dashboard UI, exports, and the proof-of-performance reports.

### Existing (reuse / extend)

| Capability | File | Status |
|---|---|---|
| **Player-side beacon (VOD + live, web/PWA)** | `civiccast/apps/portal-public/src/HlsPlayer.tsx:43–99` | shipped — emits `playback_start` once per source, a coarse `playback_heartbeat` every 60s while playing (`HEARTBEAT_SECONDS=60`, `HlsPlayer.tsx:7`), `playback_complete` on `ended`, `playback_error` on fatal failure; identifier-free |
| **Beacon emitter (fail-silent, self-disabling)** | `civiccast/apps/portal-public/src/analytics.ts:60–87` | shipped — `POST /api/public/app/analytics/events`, Origin-allowlist path (no keys in browser), `keepalive:true`; first 403/503 disables for the browser session (ENG-006/QA-003) |
| **Routing beacon (`schedule_browse`)** | `civiccast/apps/portal-public/src/App.tsx:37–43` | shipped — one privacy-safe event per portal view |
| **Public ingest endpoint (hardened)** | `civiccast/app_platform/router.py:414–433` | shipped — `POST /api/public/app/analytics/events`, key **or** Origin-allowlist gate (`require_public_analytics_ingest`, 388–411), body-size cap (370–385), per-client rate limit (436–471) |
| **Ingest contract + privacy validator** | `civiccast/app_platform/models.py:453–500` (`AnalyticsEvent`), event-name enum `:60–66` (`playback_start`/`_heartbeat`/`_complete`/`_error`/`podcast_download`/`search`/…) | shipped — `model_validator` rejects sensitive keys (address/email/ip/name/phone/token/…) |
| **Aggregate store (record + report)** | `civiccast/analytics/store.py:68–184` (`AnalyticsStore`) | shipped — `record_event` strips viewer/session ids to a `_SAFE_PROPERTY_KEYS` allowlist (`:42–57`), persists atomic JSON, prunes to `_MAX_RETAINED_EVENTS=10000` / `366`-day retention |
| **VOD per-day rollup (views + seconds)** | `civiccast/analytics/store.py:223–237` (`_asset_views`) → `AssetViewPoint` (`models.py:39–47`) | shipped — Viewer Count (`playback_start` count) + Time Viewed (`view_seconds`/`duration_seconds`) per content per **day** |
| **Live concurrency rollup incl. PEAK** | `civiccast/analytics/store.py:240–259` (`_live_concurrent`) → `LiveConcurrentPoint` (`models.py:50–59`) | shipped — per-channel per-day `peak_concurrent_viewers` + `average_concurrent_viewers` + `samples` (the §8c differentiator is **already modeled**) |
| **Report assembly + privacy boundary** | `civiccast/analytics/store.py:96–120` (`report`) → `AnalyticsReport` (`models.py:72–89`) | shipped — `privacy_boundary="aggregate-only-no-session-ip-or-viewer-identity"`, `retained_fields` declared |
| **Staff report endpoint** | `civiccast/analytics/router.py:24–33` (`GET /api/staff/analytics/reports/overview`) | shipped — `range_days` 1–366 → `AnalyticsReport` |
| **Program log (as-run source-of-truth)** | `civiccast/programlog/router.py:249–282` (`GET /channels/{id}/log`), `models.py:50–61` (`SlotOccurrence` w/ `status` `scheduled`/`skipped_conflict`/`skipped_asset`/`cancelled` + `detail`) | shipped — the data behind a Schedule Report (honest skips recorded) |
| **Schedule / asset facts** | `civiccast/schedule/models.py` (`Asset`, `ScheduleItem`, `Chapter`), `schedule/router.py:176–208` (`list_staff_assets`) | shipped — titles, durations, body/meeting metadata behind a Shows Report |

### Net-new (in scope for S14)

| Feature | Gap today | Why needed |
|---|---|---|
| **Durable `ViewershipEvent` table + migration** | events live in a single JSON file (`analytics/store.py:28`, `_STATE_FILE_NAME`) | parity-plus reporting needs SQL aggregation, survives restart cleanly, no 10k-event ceiling truncating a busy month |
| **`ViewershipRollup` table (VOD 24h + Live 30-min)** | rollups computed on every request, per-**day** only | incumbent PEG cadence coverage (VOD 24h, Live 30-min / hourly single-day); a busy station can't recompute from raw on each dashboard load |
| **`AnalyticsReport` persistence + YoY** | report is computed ephemerally; no stored snapshots, no prior-year comparison | board/annual-report YoY trend (§8b) needs a comparable prior-period series |
| **Auth role on the staff read** | `analytics/router.py:24` has **no `require_any_role`** dependency | RECONCILIATION D1: staff reads must name explicit real roles; analytics read = `support_admin` (read/diagnostic) + `publish_operator` |
| **Four-panel dashboard UI** | no operator screen; only the JSON endpoint exists | the PEG automation surface (toolbar + bar + time-series + stats + table), phone-friendly (§5) |
| **CSV export** | not implemented | PEG automation coverage floor |
| **Board-ready PDF report** | not implemented | **differentiator** — incumbent PEG platform is CSV-only (§8b) |
| **As-run / proof-of-performance reports** | program-log/schedule data exists; no report endpoint/export | Schedule Report + Shows Report equivalents (§8d); franchise/funding-relevant |
| **OTT / embedded beacon parity** | beacon ships in the web portal only | the same coarse beacon must fire from OTT shells + embedded players so reach spans every surface (§6) |

---

## 3. Entities / data model & migrations

Reuse the existing **shared vocabulary** (master §6): the portal beacon, `AnalyticsEvent`
(ingest contract), `AnalyticsRetainedEvent`, `AssetViewPoint`, `LiveConcurrentPoint`,
`AnalyticsDimensionCount`, and `AnalyticsReport` (`analytics/models.py`) all stay. S14 **promotes
the JSON store to a migrated Postgres-backed store** and adds rollup + report-snapshot tables.

### 3.1 Net-new SQLAlchemy models (`civiccast/analytics/models.py` + a new `db_models.py`)

```python
# ViewershipEvent — durable, privacy-filtered store of the retained beacon event.
# Mirrors AnalyticsRetainedEvent (already the privacy-stripped shape) as a table.
class ViewershipEvent(Base):
    __tablename__ = "viewership_events"
    event_id:     Mapped[str]            # PK; from AnalyticsEvent.event_id
    event_name:   Mapped[str]            # playback_start|playback_heartbeat|playback_complete|playback_error|schedule_browse|podcast_download
    occurred_at:  Mapped[datetime]       # tz-aware UTC; indexed
    app_target:   Mapped[str]            # web_pwa | roku | appletv | firetv | androidtv | ios | embedded
    stream_type:  Mapped[str]            # "vod" | "live"  (derived at ingest: content_id => vod, channel_id-only => live)
    channel_id:   Mapped[str | None]     # indexed; for live
    content_id:   Mapped[str | None]     # indexed; for vod
    view_seconds: Mapped[int]            # coarse watch-time contribution (heartbeat/complete)
    concurrent_viewers: Mapped[int | None]  # coarse concurrency sample, live only
    geo_bucket:   Mapped[str | None]     # OPTIONAL coarse region (off by default; §8 privacy posture)
    # NOTE: NO anonymous_session_id, NO hashed_viewer_id, NO ip — privacy boundary preserved.

# ViewershipRollup — pre-aggregated buckets (the dashboard reads these, not raw events).
class ViewershipRollup(Base):
    __tablename__ = "viewership_rollups"
    rollup_id:      Mapped[str]          # PK
    stream_type:    Mapped[str]          # "vod" | "live"
    bucket_kind:    Mapped[str]          # "day" (VOD 24h) | "halfhour" (Live 30-min) | "hour" (Live single-day)
    bucket_start:   Mapped[datetime]     # UTC; UNIQUE(stream_type,bucket_kind,subject_id,bucket_start)
    subject_id:     Mapped[str]          # content_id (vod) or channel_id (live)
    viewer_count:   Mapped[int]          # incumbent PEG platform metric 1
    time_viewed_seconds: Mapped[int]     # incumbent PEG platform metric 2
    peak_concurrent: Mapped[int | None]  # live only — §8c differentiator
    avg_concurrent:  Mapped[float | None]
    samples:         Mapped[int]

# AnalyticsReportSnapshot — a stored, dated report (drives YoY + PDF/CSV reproducibility).
class AnalyticsReportSnapshot(Base):
    __tablename__ = "analytics_report_snapshots"
    snapshot_id:    Mapped[str]          # PK
    generated_at:   Mapped[datetime]
    range_start:    Mapped[datetime]
    range_end:      Mapped[datetime]
    report_json:    Mapped[str]          # serialized AnalyticsReport (+ YoY block)
    created_by:     Mapped[str]          # operator identity that ran it (audit)
```

The Pydantic `AnalyticsReport` (the API/PDF contract) is **extended** with a YoY block and the
coverage-named fields, without breaking the existing `/reports/overview` response:

```python
class YearOverYearPoint(BaseModel):       # net-new
    metric: Literal["viewer_count", "time_viewed_seconds", "peak_concurrent"]
    current_period: int
    prior_period: int
    delta_pct: float | None               # None when prior_period == 0 (no fabricated growth)

class AnalyticsReport(BaseModel):         # EXTEND existing (analytics/models.py:72)
    # ... all existing fields stay ...
    vod_rollups:  list[ViewershipRollupPoint] = []   # net-new (24h)
    live_rollups: list[ViewershipRollupPoint] = []   # net-new (30-min / hourly)
    year_over_year: list[YearOverYearPoint] = []      # net-new (§8b)
```

### 3.2 Migration

S14 ships a **single migration `0076_analytics_viewership`** on the **single global alembic
chain**. The chain has **one head** (currently `0037_asset_meeting_body` in-tree; per
`tests/live/test_real_postgres.py` "one head despite the per-module directory layout"), and the
3.0 sections take a single monotonic sequence `0038`+. S14 is assigned the **next number after
S13's `0045_ai_model_configuration`** (RECONCILIATION migration table) → **`0046`**. It creates
all three S14 tables — `viewership_events`, `viewership_rollups`, `analytics_report_snapshots` —
in one revision, with the indexes named above. It **also migrates** any existing JSON-file events
(`analytics-events.json`) into `viewership_events` on first run (one-time, idempotent backfill),
then the JSON store is retired. No other 3.0 migration touches these tables, so `0046` has no
co-edit sequencing constraint beyond following `0045`.

> RECONCILIATION addendum needed: add a row
> `| 0046_analytics_viewership | S14 | viewership_events / viewership_rollups / analytics_report_snapshots; backfill from analytics-events.json |`
> to the migration-assignment table, and remove the "Analytics has no owning section" gap note.

---

## 4. API surface

All staff reads name **explicit real roles** (RECONCILIATION D1 — five real roles only:
`setup_admin`, `meeting_operator`, `records_clerk`, `publish_operator`, `support_admin`). Analytics
is read-only diagnostic + publishing-impact data, so reads require **`support_admin` OR
`publish_operator`** (a read role + the role that owns published content). No new role is invented.
The public **ingest** endpoint is unchanged (it stays the hardened key/Origin-gated
`POST /api/public/app/analytics/events`).

### Extend (add the missing auth guard)

```
GET /api/staff/analytics/reports/overview        # analytics/router.py:24 — ADD:
    dependencies=[Depends(require_any_role("support_admin", "publish_operator"))]
    query: range_days 1..366 (existing) + stream_type=vod|live|all (new) + metric=viewer_count|time_viewed|peak_concurrent (new)
    -> AnalyticsReport (now incl. vod_rollups / live_rollups / year_over_year)
```

### Net-new endpoints (`civiccast/analytics/router.py`)

```
GET  /api/staff/analytics/rollups
       Auth: require_any_role("support_admin", "publish_operator")
       query: stream_type=vod|live, bucket=day|halfhour|hour, from, to, top_n (default 10)
       -> { rollups: [ViewershipRollupPoint], stats: {total_viewer_count, total_time_viewed_seconds, peak_concurrent} }
       (the dashboard's bar-chart + time-series + stats panels read this)

GET  /api/staff/analytics/export.csv
       Auth: require_any_role("support_admin", "publish_operator")
       query: same as /rollups + report range
       -> text/csv (PEG automation coverage floor)

POST /api/staff/analytics/reports/board-pdf
       Auth: require_any_role("support_admin", "publish_operator")
       body: { range_start, range_end, include: {totals, top_content, yoy, live_peaks}, station_label }
       -> application/pdf (the §8b board-ready report); persists an AnalyticsReportSnapshot

GET  /api/staff/reports/schedule          # AS-RUN — Schedule Report parity (off programlog)
       Auth: require_any_role("support_admin", "publish_operator", "meeting_operator")
       query: channel_id?, from, to, format=json|csv
       -> per-channel as-run log: what was scheduled, what aired, honest skips
          (reuses programlog SlotOccurrence.status/detail; never fabricates an airing)

GET  /api/staff/reports/shows             # PROOF-OF-PERFORMANCE — Shows Report parity
       Auth: require_any_role("support_admin", "publish_operator", "meeting_operator")
       query: from, to, format=json|csv
       -> per-show play counts + total airtime over the window (off schedule + programlog)
```

`GET /api/staff/reports/schedule` and `/shows` are the as-run / proof-of-performance reports — the
incumbent PEG platform **Schedule Report** and **Shows Report** equivalents, plus the CSV is the **TV Guide
X-List** equivalent (program metadata as CSV). They live under `/api/staff/reports/` because they
report on *playout*, not *audience*, and they read the **program log** (the authoritative as-run
record), so they remain accurate even when audience telemetry is off.

> **Build note (2026-08-21):** this pair is served by `civiccast/reporting`'s existing
> `GET /api/staff/reports/as-run` and `/shows` (S18/S23, migration `0055_asrun_and_epg`) rather than
> new S14-owned routes at these exact paths — that surface already exists, is role-gated
> (`support_admin`), reads the same program-log/schedule data this section names, and additionally
> ships `/hours-by-category` and a unified `/reports/export?type=as-run|shows&format=csv|xml`. S14
> did not duplicate it. The one real gap — `civiccast/reporting`'s role gate is `support_admin`
> only, not `+ publish_operator + meeting_operator` as sketched above — is a pre-existing S18/S23
> decision, out of this branch's scope to change.

---

## 5. Operator UI surface

A single **Analytics dashboard** screen, matching the incumbent PEG platform four-panel layout and
phone-first per master §1 (appliance posture):

1. **Toolbar (panel 1):** chart-type toggle (bar / line) · metric selector (Viewer Count / Time
  Viewed / **Peak Concurrent**) · date-range picker (presets: 7d / 30d / quarter / year) ·
   stream-type toggle (**VOD / Live / All**). Matches the incumbent PEG workflow's toolbar exactly, plus the
   Peak-Concurrent metric option.
2. **Top-N bar chart (panel 2):** top streams by the selected metric (default top 10) — "which
   VOD content drew the most viewers," "which channel peaked highest."
3. **Time-series (panel 3):** the selected metric over the range, bucketed VOD-24h / Live-30-min
   (hourly when a single day is selected) — the rollup cadence from §3.
4. **Stats summary + expandable data table (panel 4):** totals (total reach, total watch-time,
   highest live peak) above a collapsible table of the underlying rollup rows.

**Exports (toolbar buttons):** **Export CSV** (functional floor) and **Generate Board PDF** (the
differentiator — opens an include-checklist: totals / top content / YoY / live-event peaks, then
downloads the PDF and stores a snapshot).

**As-run reports (separate tab "Reports"):** Schedule Report (per-channel as-run log with honest
skips) and Shows Report (play counts + airtime), each with a CSV download. Phone layout collapses
the four panels to a vertical stack; the data table starts collapsed.

**Empty / disabled state (load-bearing honesty):** when public analytics ingest is **not
configured** for the deployment (`CIVICCAST_PUBLIC_ANALYTICS_KEY` unset **and** no allowed origins
— `app_platform/router.py:392–398`), the dashboard shows "Audience telemetry is off — turn it on
in Setup to collect Viewer Count and Time Viewed," **but the Reports tab (as-run / proof-of-
performance) still works**, because it reads the program log, not the beacon. This is the
structural inverse of incumbent PEG platform: there, *no cloud telemetry = no analytics at all*; here, the as-run
reports are always available and audience metrics are an opt-in overlay.

---

## 6. Behavior / algorithms

### 6.1 Beacon → store → rollup (the measurement spine)

1. **Player-side beacon (existing, extended to every surface).** The portal `HlsPlayer`
   (`HlsPlayer.tsx:43–99`) already emits, per playback: `playback_start` once, a coarse
   `playback_heartbeat` every **60s** with `position_seconds`, `playback_complete` on `ended`,
   `playback_error` (generic reason only). S14 ships the **same coarse beacon contract** from the
   OTT shells (`apps/app-platform-shells`) and any embedded player, tagging `app_target`
   (`roku`/`appletv`/`firetv`/`androidtv`/`ios`/`embedded`) — so reach spans **web/VOD/live/
   portal/OTT/embedded**. The beacon stays fail-silent and identifier-free (`analytics.ts:9–16`).
2. **Ingest (existing).** `POST /api/public/app/analytics/events` validates the privacy contract
   (`AnalyticsEvent` rejects sensitive keys) and rate-limits, then `store.record_event` strips to
   the `_SAFE_PROPERTY_KEYS` allowlist. **No viewer/session identity is ever stored** — so unique
   viewers are not derivable (matching incumbent PEG platform, and matching the privacy posture).
3. **Persist (net-new).** `record_event` writes a `ViewershipEvent` row (replacing the JSON file).
   `stream_type` is derived at ingest: an event carrying `content_id` is **VOD**; a
   `channel_id`-only event is **Live**.
4. **Rollup (net-new background pass).** A periodic roll-up job folds raw `ViewershipEvent` rows
   into `ViewershipRollup`:
   - **Viewer Count** = count of distinct `playback_start` in the bucket (incumbent PEG platform metric 1;
     it is a *play count*, not unique humans — stated plainly).
   - **Time Viewed** = sum of `view_seconds` contributions across `playback_heartbeat` (≈60s each)
     + the final `playback_complete` tail (incumbent PEG platform metric 2).
   - **VOD buckets = 24h (`day`)**; **Live buckets = 30-min (`halfhour`)**, switching to **hourly
     (`hour`)** when the requested range is a single day — exactly the incumbent PEG platform's cadence.
   - **Peak concurrent (live, §8c):** the max `concurrent_viewers` sample in the bucket — already
     modeled in `_live_concurrent` (`store.py:240–259` → `LiveConcurrentPoint.peak_concurrent_
     viewers`); S14 persists it per bucket instead of recomputing per request. Concurrency is
     estimated from overlapping active beacons (a viewer is "active" between its `playback_start`/
     last `playback_heartbeat` and `playback_complete`/timeout); this is an estimate, labeled as
     such, not a CDN-authoritative count.
5. **Report.** `report(range_days)` reads rollups (not raw events), assembles the existing
   `AnalyticsReport` plus `vod_rollups` / `live_rollups` / `year_over_year`.

### 6.2 Year-over-year (for the board PDF)

For each headline metric, compare the selected range to the **same calendar range one year prior**
from the rollups. `delta_pct = round((current - prior) / prior * 100, 1)`; when `prior == 0`,
`delta_pct = None` and the PDF prints "no prior-year data" — **never** a fabricated or
infinite growth number.

### 6.3 As-run / proof-of-performance (off programlog/schedule, telemetry-independent)

- **Schedule Report:** for each channel over the window, list every `SlotOccurrence` with its
  `status` (`scheduled` / `skipped_conflict` / `skipped_asset` / `cancelled`) and `detail`
  (`programlog/models.py:50–61`). Skips are reported honestly — the report shows what *aired* and
  what *did not and why*, never asserting an airing that the log didn't record.
- **Shows Report:** group occurrences by `asset_id`/title (resolved via the program-log titler /
  `schedule` asset rows), count plays and sum airtime over the window. This is the
  proof-of-performance artifact a station hands a franchise authority or a grant report.

### 6.4 CSV / PDF generation

- **CSV** is a flat rollup dump (one row per bucket per subject), generated in-process — no new
  dependency. Matches the incumbent PEG workflow's CSV export and doubles as the X-List for the as-run reports.
- **PDF** uses the existing in-tree PDF capability (the same path used elsewhere for operator
  artifacts; if none is wired for this surface, a single lightweight server-side renderer is added
  — confirm in §10 open decisions). Layout: cover (station label + range) → totals → top-content
  bar → YoY trend → live-event peaks. **Default OFF nothing** — the PDF is a deliberate operator
  action, not automatic.

---

## 7. Proof tier + honest claim boundary

**Current rung: CONTRACT (rung 0) — extending toward LAB (rung 1).** The beacon→store→report
chain has unit/API tests and Stage-G Playwright specs (`apps/portal-public/e2e/analytics.spec.ts`),
but there is no runtime audience-measurement soak.

**Advance to LAB (rung 1):** unit tests for `ViewershipEvent`/`ViewershipRollup`/snapshot models,
rollup-bucket math (VOD 24h, Live 30-min/hourly), YoY (incl. `prior==0`), CSV/PDF generation, and
the new auth guard; API tests for every endpoint in §4; an E2E that drives the portal player,
captures beacons through ingest, rolls them up, and reads the dashboard endpoints. **Soak
integration:** during the master §12 24/72h soak, emit synthetic playback beacons and verify
rollups stay bounded and accurate, with no drift between raw-event sums and rollup totals.

**Honest claim boundary (the cardinal rule — overclaiming is the cardinal sin):**

- **Streaming-only scope, exactly like incumbent PEG platform.** S14 measures **IP streaming** (web/VOD/live/
  portal/OTT/embedded) only. It **does NOT** claim **linear / cable (QAM) viewership** — that is
  unmeasurable without set-top return-path data the station does not have. The docs say so plainly.
- **Viewer Count is a play count, not unique humans.** No `anonymous_session_id`/`hashed_viewer_id`
  is stored (the privacy posture, `analytics.ts:9–16`), so **unique viewers are not derivable** —
  same limitation as the incumbent PEG baseline, stated, not hidden.
- **Geo / device / platform segmentation is NOT claimed as PEG automation coverage** — the incumbent PEG platform lacks
  these dimensions. CivicCast *can* offer **coarse, opt-in geo** (§8 last) but presents it as an
  optional extra, never as "matching incumbent PEG platform."
- **Concurrency is an estimate** from overlapping beacons, not a CDN-authoritative figure — labeled
  as such in the UI and PDF.

---

## 8. Differentiators (in scope for V1 — not deferred)

These are the wins over incumbent PEG platform and are **built in V1**, not punted:

**(a) Self-hosted, NO paid-cloud-CDN dependency — the biggest structural win.** The incumbent PEG platform's
Audience Measurement is **hard-gated behind a paid cloud-telemetry subscription** (~$4,100/yr per
master §2.3): no cloud telemetry, no analytics. CivicCast measures from its **own player beacon → own
store**, with **zero cloud-CDN dependency** — the same self-host story as the rest of 3.0. A
station that self-hosts gets full audience measurement for **$0/yr recurring**. This is the
headline.

**(b) One-click board-ready PDF report.** The incumbent PEG platform exports **CSV only**. S14 ships a single-click
PDF with totals, top content, YoY trend, and live-event peaks — the exact artifact a PEG manager
takes to a city-council budget hearing or a franchise-renewal needs-assessment. (CSV is also
shipped, for parity and for spreadsheet users.)

**(c) Explicit peak-concurrent for live.** The incumbent PEG platform reports a live *viewer count* and only
*implies* the peak. S14 makes **peak concurrent** a first-class, selectable metric — already
modeled today (`LiveConcurrentPoint.peak_concurrent_viewers`, `store.py:240–259`) — because the
live-event spike is one of the top things a PEG station cites for impact. Low-cost win.

**(d) As-run / proof-of-performance reports.** The **Schedule Report** (per-channel as-run log with
honest skips) and **Shows Report** (play counts + airtime), built off `programlog`/`schedule`
(§6.3), plus the CSV X-List. These are franchise- and funding-relevant and, unlike the incumbent PEG platform's
audience numbers, work **even with telemetry off** because they read the playout log.

**Optional coarse geo (with an explicit privacy posture).** Municipal buyers are privacy-sensitive
and PEG operators *want* geography but rarely get it. S14 supports an **opt-in, coarse** geo bucket
(`ViewershipEvent.geo_bucket`, e.g. region/state granularity only) that is **OFF by default**, and
when on, derives a coarse bucket server-side and **discards any IP immediately** (the privacy
contract already forbids storing IP — `app_platform/models.py:472–500`). Presented as an optional
extra, **not** as PEG automation coverage (the incumbent PEG baseline has no geo). See §10 open decision.

---

## 9. Test plan + 0/0/0/0/0 audit expectation

### 9.1 Unit tests (`tests/analytics/`)

- `test_viewership_event_model`: privacy shape; no session/viewer/ip fields ever persisted.
- `test_stream_type_derivation`: `content_id` → vod; `channel_id`-only → live.
- `test_rollup_vod_24h` / `test_rollup_live_halfhour` / `test_rollup_live_hourly_single_day`:
  bucket boundaries (incl. midnight-UTC crossover, matching the soak's crossover check).
- `test_viewer_count_is_playback_start_count` / `test_time_viewed_sum`: the two incumbent PEG platform metrics.
- `test_peak_concurrent_per_bucket`: peak from overlapping beacons.
- `test_year_over_year_prior_zero_returns_none`: no fabricated growth.
- `test_csv_export_shape` / `test_board_pdf_renders`: exports.
- `test_schedule_report_records_skips` / `test_shows_report_play_counts`: as-run off programlog.
- `test_json_backfill_idempotent`: one-time `analytics-events.json` → `viewership_events`.

### 9.2 API tests (`tests/analytics/test_reports_api.py`)

- Every §4 endpoint: 200 with a valid role; **403** without (`support_admin`/`publish_operator`
  enforced); `/reports/schedule` + `/shows` also accept `meeting_operator`.
- `overview` honors `stream_type` / `metric` / `range_days`.
- Disabled-ingest state: dashboard read returns the "telemetry off" shape; **Reports tab still
  returns data** (telemetry-independent).
- Public ingest endpoint unchanged (regression): key/Origin gate + rate limit still enforced.

### 9.3 E2E / soak

- Drive the portal player → beacons through ingest → rollup pass → dashboard endpoints reflect the
  plays; CSV + PDF download.
- Master §12 24/72h soak: synthetic beacons; assert raw-sum == rollup-sum (no drift), rollups
  bounded, no DB deadlocks.

### 9.4 0/0/0/0/0 audit expectation

- **Bugs found & fixed:** 0 · **API errors (500):** 0 · **Data corruption:** 0 ·
  **Unhandled exceptions:** 0 · **Security issues:** 0.
- Specifics: no PII ever reaches the store (validator + allowlist proven); auth guard rejects
  unauthenticated/under-privileged reads; rollup math has no off-by-one at bucket edges; CSV/PDF
  escape untrusted titles; no N² aggregation on the dashboard read (rollups are pre-aggregated).

---

## 10. DONE criteria; Dependencies & cross-refs; Open decisions

### DONE criteria (2026-08-21 status against each — branch, not yet merged)

1. ✅ **Migration built** — single `0076_analytics_viewership` on the global chain (real head was
   `0075_offline_caption_jobs`, not `0045` as this section originally assumed — corrected). Three
   tables; idempotent JSON backfill runs from app code, not the migration (documented deviation,
   see migration docstring). Head pin advanced in `tests/live/test_real_postgres.py`. Verified via
   SQLite upgrade→downgrade→upgrade + a full local chain replay (0000→0076); **not** verified
   against real Postgres/testcontainers (Docker unavailable in the build environment) — that
   remains open before a release candidate cites this migration as production-proven.
2. ✅ `GET /reports/overview` carries `require_any_role("support_admin","publish_operator")` and
   the `stream_type`/`metric` query params; `/rollups`, `/export.csv`, `/reports/board-pdf` shipped
   + role-gated the same way.
3. ✅ Durable `ViewershipEvent`/`ViewershipRollup` store replaces the JSON file when durable storage
   is active; `AnalyticsRollupWorker` produces VOD-24h + Live-30-min **and** hourly buckets (both
   granularities persisted). ⚠️ raw-sum == rollup-sum is verified by unit test on fixture data, not
   by a live soak (criterion 9's soak did not run this session).
4. ✅ Four-panel dashboard wired (toolbar / bar+time-series charts / stats+expandable table) with the
   honest "telemetry off" empty state (`AnalyticsReport.ingest_configured`, net-new field). Built
   with a small dependency-free SVG chart component (no chart library exists anywhere in
   `portal-operator`). ⚠️ Responsive/phone-friendly by virtue of the existing Tailwind grid classes;
   not independently verified at a phone viewport in a real browser this session.
5. ✅ CSV export **and** board-ready PDF (totals / top content / YoY / live peaks, each toggleable)
   ship; PDF persists an `AnalyticsReportSnapshot` on the durable store (a no-op, documented, on the
   ephemeral/JSON store).
6. ✅ As-run Schedule Report + Shows Report parity ships and works with telemetry off — via
   `civiccast/reporting`'s existing `/reports/as-run` + `/shows` (S18/S23), cross-referenced from §4
   rather than duplicated.
7. ❌ **Not built.** Beacon parity across OTT/embedded is greenfield — the shells emit no analytics
   events today and this branch does not add any. Flagged, not silently dropped.
8. ✅ Honest claim boundary documented (this section, §7) — unchanged from the original draft, still
   accurate to what's built: streaming-only, play-count not unique, concurrency estimated, no
   linear/QAM claim, geo opt-in (column exists, `OFF` by default, nothing derives it yet).
9. ❌ **Not run.** 0/0/0/0/0 audit and the master §12 soak are unrun this session — unit + API +
   rollup-worker + Alembic-chain + vitest tests all pass, which is proof tier CONTRACT extending
   toward LAB, not LAB itself. A soak run against real traffic (or synthetic beacons) is required
   before claiming rung 1.

### Dependencies & cross-refs

- **S2 outputs (egress sinks / channels):** `channel_id` keys live rollups; channel branding/labels
  decorate the dashboard and PDF.
- **S4 (Commit-to-Air) / programlog & schedule:** the as-run reports read the program log
  (`SlotOccurrence`) and schedule assets — the authoritative *what aired* record.
- **S8 (health/alerting):** S14 is read-only and does **not** add alert rules, but a future
  "viewership ingest down" signal could feed S8 (out of scope here; noted, not built).
- **S10 (proof ladder):** S14 maps contract → lab per master §5; no off-ladder labels.
- **S12 (OTT apps):** the OTT shells emit the same beacon (`app_target`); S14 consumes it. OTT's
  own proof rung is separate (RECONCILIATION D7: OTT rung-3 N/A).
- **S13 (AI):** none (analytics is not an AI feature); the dashboard does not invoke a model.
- **App-platform ingest (`app_platform/router.py`):** S14 reuses the existing hardened public
  ingest endpoint unchanged; it does not add a second ingest path.

### Open decisions for Scott

1. **Coarse geo: on or off by default?** Recommend **OFF by default**, opt-in per deployment, with
   region/state granularity only and immediate IP discard. (Municipal privacy posture; geo is a
   "wanted but rarely available" nice-to-have, not parity.) Confirm the granularity ceiling
   (region/state vs metro).
2. **Board PDF scope.** Recommend the four sections (totals / top content / YoY / live peaks) for
   V1. Add station logo/branding now, or defer cosmetic theming? Confirm whether an existing
   in-tree PDF renderer covers this surface or a lightweight one is added (§6.4).
3. **Rollup job cadence + raw-event retention.** Recommend rolling up on a short interval (e.g.
   every 5 min) and keeping raw `ViewershipEvent` rows for a bounded window (e.g. 90 days) while
   rollups persist for the full YoY horizon (≥ 14 months). Confirm the raw-retention window.
4. **`schedule_browse` in the dashboard?** It's a useful "portal reach" signal but isn't a Viewer
   Count / Time Viewed metric. Recommend surfacing it only in the stats summary as "portal visits,"
   not in the functional bar/time-series. Confirm.

---

## 11. Implementation order (build within S14)

1. **Week 1:** `0046` migration + `ViewershipEvent`/`ViewershipRollup`/snapshot models; JSON
   backfill; auth guard on `/reports/overview`; unit tests.
2. **Week 2:** rollup job (VOD-24h / Live-30-min/hourly + peak); `/rollups`, CSV export; API tests.
3. **Week 3:** board PDF + snapshot; as-run Schedule/Shows reports off programlog/schedule.
4. **Week 4:** four-panel dashboard UI (phone-first) + Reports tab + "telemetry off" state; OTT/
   embedded beacon parity; E2E + soak integration; 0/0/0/0/0 audit.

**Estimated effort:** 9–12 person-days (3 tables + 1 migration + rollup job + 6 endpoints +
2 exports + dashboard + reports tab + tests + soak).

---

*Spec authored 2026-06-13, grounded in code @ 69cc676. S14 promotes the existing privacy-safe
beacon→report chain to a durable, role-gated, parity-plus audience-measurement capability with the
self-hosted (no-cloud-CDN), board-PDF, explicit-peak-concurrent, and as-run-report differentiators.
Proof tier: contract → lab. Resolves the RECONCILIATION "Analytics has no owning section" gap.*

---
## Comparative additions (incumbent PEG platform gap closure → S18)
This section gains **as-run / proof-of-performance reporting**, **EPG export** (X-List/XMLTV/CSV →
TV Guide/TitanTV), and **franchise / hours-by-category reporting** (S18 gap 5,
`as_run_log`/`epg_export_configs`, migration `0052`), plus **per-underwriter affidavits** (with S18
gap 10). See the allowlisted S18 comparative research appendix.
