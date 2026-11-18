# S6 — CG Bulletin Board Designer (Multi-Zone + Persistence)

> **Status:** Built for v3.0.0-beta1; field-headend proof remains external.
> **Master ref:** civiccast-3.0-station-in-a-box-MASTER.md §4 item 4, §6 entity model.
> **Authored:** 2026-06-13, grounded in code @ main/69cc676.
>
> **Engine alignment (2026-06-13):** S6 is the bulletin-board **authoring/management layer**
> (designer + persistence + feed sources + moderation/scheduling). Its output is **rendered by the
> GStreamer playout engine (S15)** — `compositor` + `textoverlay`/`clockoverlay`/`gdkpixbufoverlay`
> on CPU for the base tier, and `wpesrc` (WPE WebKit) HTML/CSS/JS templates for rich/animated CG.
> S6 does **not** build a render engine, and it is **not** render-contract-only: it builds the app
> layer that produces the boards/zones/bulletins the GStreamer compositor/WPE composites into the
> channel. CasparCG is an **optional** GPLv3 premium co-process (designer-driven broadcast CG,
> bridged via NDI/SDI) — not required; most stations use the GStreamer compositor/WPE path.

---

## 1. Goal & PEG automation rationale

**What incumbent PEG platform does:** incumbent PEG graphics workflow (CBL-CGPLAYER-LIC ~$1,550 add-on) provides a resident on-channel graphics overlay system for PEG stations. Stations use it daily to show community bulletins (event announcements, service notices), rolling crawls (emergency alerts, weather), schedule updates, local-origin graphics, and logo/watermark overlays across up to 24/7 playout. Content is moderated by operators: community members submit bulletins, staff approve/decline/schedule them into a rotating board; feeds (RSS/iCal/weather) auto-populate zones; graphics and layouts are multi-zone (fullscreen, L-bar, lower-third, ticker crawl, emergency overlay). It is a core incumbent PEG platform revenue product that stations rely on to be compliant with franchise agreements (many cable franchises require PEG to show community announcements + emergency overlays).

**The gap we close:** CivicCast today has contract-only CG surfaces (model definitions, render plans, templates, feeds, emergency overlays—no persistence, no operator CRUD, no designer). Bulletins exist as deterministic mock data in `build_bulletin_queue()`. The bulletin filler (`BulletinFillerSourceGenerator`) renders approved bulletins to MPEG-TS slides — **but it has no real approved-bulletin set to draw from.** This section adds the **authoring/management layer**; the actual on-channel composition (overlay blend, lower-thirds, crawls, fullscreen bulletins, clock/logo, and animated rich CG) is performed by the **GStreamer engine (S15 §5)** — `compositor` + text/clock/image overlays for CG-lite, `wpesrc` HTML templates for CG-rich — driven by the boards/zones/bulletins this section persists. This section adds:

1. **Durable bulletin persistence** — submissions, moderation, scheduling, expiration.
2. **Multi-zone board designer** — the full V1 template set (fullscreen, L-bar, lower-third, bug, ticker, emergency overlay — all six, no deferrals), layout configuration, per-zone content binding, and a manual zone text editor for zones whose `content_source == "manual"`.
3. **Feed source management** — RSS, iCal, CalDAV, weather, social feed *sources* with refresh intervals and approval gates (manual/image/clock are zone content modes, not feeds — see §3).
4. **Bulletin lifecycle** — submission→moderation→approval→scheduling→on-air→expiration + operator audit trail.
5. **Operator UI** — phone-first moderation queue, template editor, feed configurator, live preview.
6. **Resolved CG snapshots** — the multi-zone snapshot becomes a real durable board configuration, not mock data.

**Parity mapping (Master §2.1):** CG Bulletin Board designer = incumbent PEG graphics workflow. NOT Carousel (the vendor's separate digital-signage product).

---

## 2. Current state (grounded in main @ 69cc676)

### What exists:

| Component | File:line | Status | Note |
|-----------|-----------|--------|------|
| **Pydantic contracts** | `civiccast/cg/models.py:1–290` | **shipped** | `IdlePage`, `EmergencyOverlay`, `CgTemplate`, `CgTemplateZone`, `CgZone`, `CgFeedItem`, `CgFeedAdapter`, `CgFeedCatalog`, `CgBulletinSubmission`, `CgBulletinQueue`, `MultiZoneCgSnapshot`, `CgPortalDisplay`, etc. All closed/proof-boundary marked. |
| **Public CG endpoints** | `civiccast/cg/router.py:113–189` | **shipped** | GET `/idle`, `/emergency-overlay`, `/channels/{channel_id}/snapshot`, `/feeds`, `/templates`, `/bulletins`, `/render-plan`, `/overlay-contract`, `/display`. All render-only, no POST/PATCH. |
| **Staff bulletin CRUD** | `civiccast/cg/router.py:192–284` | **shipped** | POST `/staff/cg/channels/{channel_id}/bulletins` (create), PATCH `/{submission_id}` (moderate), GET queue. Wired to `get_cg_bulletin_store()` DI seam. |
| **Bulletin builders** | `civiccast/cg/service.py:28–380` | **shipped** | `build_template_library()`, `build_multi_zone_snapshot()`, `build_feed_catalog()`, `build_bulletin_queue()`, etc. Deterministic mock data. |
| **Durable bulletin store** | `civiccast/cg/bulletin_store.py:1–162` | **shipped** | `PostgresCgBulletinStore` (create, get, update, list, list_approved). SQLAlchemy ORM. `CgBulletinDb` table (submission_id PK, channel_id, state, moderation_notes, approved_by_operator, created_at, updated_at). |
| **Migration** | `civiccast/cg/migrations/versions/0033_cg_bulletins.py` | **shipped** | Creates `cg_bulletins` table + `fill_policy` column on `egress_configs`. |
| **Bulletin filler** | `civiccast/egress/bulletin_filler.py:1–285` | **shipped** | `BulletinFillerSourceGenerator` selects approved bulletins for the filler source. `build_filler_source_provider()` wires the store. Branches on `config.fill_policy` ("slate" or "bulletins"). **Rendering retargets to the GStreamer engine (S15):** the filler feeds bulletin content into the engine's `compositor`/WPE path (a hot-swappable `interpipesink` source) instead of spawning per-segment ffmpeg slides. |
| **Egress model** | `civiccast/egress/models.py:286–331` | **shipped** | `EgressConfig.fill_policy: Literal["slate", "bulletins"] = "slate"`. `EgressConfigDb` column added. |
| **CG bridge proof** | `civiccast/egress/cg_bridge.py:1–111` | **shipped** | `EgressCgOverlayProof`, `build_cg_overlay_egress_proof()`, boundary constants. Contract-only; under S15 this becomes the contract for handing the resolved CG snapshot to the GStreamer compositor/WPE overlay (not a self-built render path). |
| **App wiring** | `civiccast/app.py:876–881` | **shipped** | DI override `get_cg_bulletin_store = PostgresCgBulletinStore(session_factory)`. |
| **Tests** | `tests/cg/test_bulletin_store.py`, `test_bulletin_api.py` | **shipped** | Store round-trip, CRUD, state transitions, model validators. ~50 assertions. |

### What is net-new:

| Component | Scope |
|-----------|-------|
| **Board configuration entity** | `CgBoard` — durable row mapping templates, zones, feeds to a channel. Persistence (DB table + SQLAlchemy ORM). |
| **Zone configuration entity** | `CgZoneConfig` — durable zone definition: region, kind, content source (`feed_adapter`/`manual`/`schedule`/`emergency`/`image`/`clock`), refresh interval, approval gate. |
| **Feed source entity + store** | `CgFeedSource` — durable feed *source* registration (`rss`/`ical`/`caldav`/`weather`/`social`) with refresh policy, trust tier, target zones. `PostgresCgFeedSourceStore`. (manual/image/clock are zone content modes, not feed sources.) |
| **Template management endpoints** | POST/PATCH `/staff/cg/channels/{channel_id}/templates` (activate, override defaults), DELETE. |
| **Board designer CRUD** | POST/PATCH `/staff/cg/channels/{channel_id}/board` (create board, assign template, bind zones). |
| **Feed source endpoints** | POST/PATCH/DELETE `/staff/cg/channels/{channel_id}/feeds`. Register, update, remove feeds. |
| **Zone override endpoints** | POST/PATCH `/staff/cg/channels/{channel_id}/zones/{zone_id}` (bind feed, set approval gate, manual content). |
| **Live preview endpoint** | GET `/staff/cg/channels/{channel_id}/preview` (render snapshot with current config, no real feed data yet). |
| **Board history/audit** | Append-only `cg_board_audit` table (board_id, event, operator_id, timestamp, details). |
| **Expiration + scheduler** | Bulletin `requested_start/end` enforcement in the filler (don't show expired; show scheduled). Background job to clear expired bulletins (daily). |
| **Operator UI screens** | Phone-first Bulletin Moderation Queue, Board Layout Designer, Feed Manager, Live Preview. |
| **Migrations** | New migration `0044_cg_board_designer` (single global chain) for `cg_boards`, `cg_zone_configs`, `cg_feed_sources`, `cg_board_audit`, + feed-item approval. |
| **Feed-item approval gate** | `cg_feed_item_approvals` table + approval workflow: zones with `approval_required=true` hide unapproved feed items until an operator approves them. Shipped in V1 (not deferred). |
| **Manual zone text editor** | Operator-edited text for zones with `content_source == "manual"`. Shipped in V1 (not deferred). |

---

## 3. Entities / data model & migrations

### New entities (reuse master §6 vocabulary):

#### `CgBoard` (durable board config for one channel)

```
board_id PK (VARCHAR 120)
channel_id FK (VARCHAR 120) → egress_configs
template_id (VARCHAR 120)
active (BOOLEAN, default true)
created_at (TIMESTAMP, default CURRENT_TIMESTAMP)
updated_at (TIMESTAMP)
created_by (VARCHAR 120)
```

#### `CgZoneConfig` (durable zone binding within a board)

```
zone_id PK (VARCHAR 120)
board_id FK (VARCHAR 120) → cg_boards
region (VARCHAR 50)
zone_kind (VARCHAR 20)
content_source (VARCHAR 20: feed_adapter|manual|schedule|emergency|image|clock)
feed_adapter_id FK (VARCHAR 120, nullable)
refresh_seconds (INTEGER, nullable)
approval_required (BOOLEAN, default false)
created_at (TIMESTAMP)
```

#### `CgFeedSource` (durable feed registration)

```
feed_source_id PK (VARCHAR 120)
channel_id FK (VARCHAR 120) → egress_configs
kind (VARCHAR 20: rss|ical|caldav|weather|social)
label (VARCHAR 160)
source_url (TEXT, max 500)
trust_tier (VARCHAR 30)
refresh_seconds (INTEGER)
enabled (BOOLEAN, default true)
created_at (TIMESTAMP)
created_by (VARCHAR 120)
last_fetched_at (TIMESTAMP, nullable)
last_fetch_error (TEXT, nullable)
```

#### `CgBoardAuditEvent` (append-only event log)

```
audit_id PK (VARCHAR 120, uuid)
board_id FK (VARCHAR 120)
channel_id FK (VARCHAR 120)
event_kind (VARCHAR 50)
operator_id (VARCHAR 120, nullable)
occurred_at (TIMESTAMP)
details_json (TEXT, default '{}')
```

---

## 4. API surface

All staff endpoints require `require_any_role("setup_admin", "publish_operator")`. Responses are JSON; errors return 400/403/404/503.

### Board CRUD

- **GET `/staff/cg/channels/{channel_id}/board`** — Read active board config. Returns `CgBoard` + zones + feeds.
- **POST `/staff/cg/channels/{channel_id}/board`** — Create board. Payload: `{ template_id, created_by }`. Returns `CgBoard`.
- **PATCH `/staff/cg/channels/{channel_id}/board`** — Update board. Payload: `{ template_id?, active? }`.

### Zone management

- **POST `/staff/cg/channels/{channel_id}/zones`** — Add zone. Payload: `{ region, zone_kind, content_source, feed_adapter_id?, refresh_seconds?, approval_required? }`.
- **PATCH `/staff/cg/channels/{channel_id}/zones/{zone_id}`** — Update zone. Payload: `{ content_source?, feed_adapter_id?, refresh_seconds?, approval_required? }`.
- **DELETE `/staff/cg/channels/{channel_id}/zones/{zone_id}`** — Remove zone. Returns 204.

### Feed source management

- **GET `/staff/cg/channels/{channel_id}/feeds`** — List feeds. Returns `list[CgFeedSource]`.
- **POST `/staff/cg/channels/{channel_id}/feeds`** — Register feed. Payload: `{ kind, label, source_url, trust_tier, refresh_seconds?, enabled? }`.
- **PATCH `/staff/cg/channels/{channel_id}/feeds/{feed_source_id}`** — Update feed. Payload: `{ label?, source_url?, trust_tier?, refresh_seconds?, enabled? }`.
- **DELETE `/staff/cg/channels/{channel_id}/feeds/{feed_source_id}`** — Remove feed. Returns 204.

### Live preview & snapshot

- **GET `/staff/cg/channels/{channel_id}/preview`** — Render live snapshot with mock feeds. Returns `MultiZoneCgSnapshot`.
- **GET `/public/cg/channels/{channel_id}/snapshot`** — Current on-air snapshot (existing, unchanged).

### Audit & history

- **GET `/staff/cg/channels/{channel_id}/board/audit`** — List audit events. Query: `?limit=50&offset=0`. Returns `list[CgBoardAuditEvent]`.

---

## 5. Operator UI surface (phone-first)

### Screen 1: Bulletin Moderation Queue
Path: `/operator/cg/{channel_id}/bulletins`  
Tabs: New Submissions | Scheduled | Declined | Archive  
Actions: Approve (with date override), Request Changes (with notes), Decline

### Screen 2: Board Layout Designer
Path: `/operator/cg/{channel_id}/board/design`  
Left: Template selector  
Center: Live preview with region labels  
Below: Zone configuration accordions (each shows: kind, content_source, feed_adapter selector, refresh interval, approval_required toggle)  
Actions: Save Board, Preview, Revert

### Screen 3: Feed Manager
Path: `/operator/cg/{channel_id}/board/feeds`  
List: kind, label, source_url, trust_tier, refresh, enabled, last_fetched, error indicator  
Actions: Edit, Delete  
Floating: Add Feed (modal: kind, label, url, trust_tier, refresh, enabled)

### Screen 4: Live Preview
Path: `/operator/cg/{channel_id}/board/preview`  
Full-page board rendering with mock content (sample ticker items, next 3 schedule items, station logo, "No alerts" for emergency)  
Bottom info: board_id, template_id, zone count, last updated

### Screen 5: Board Settings & History
Path: `/operator/cg/{channel_id}/board/settings`  
Section 1: Board metadata (board_id, channel_id, template_id, active toggle, created_at, updated_at, created_by)  
Section 2: Audit log accordion (list of events with JSON detail modal)

---

## 6. Behavior / algorithms

### Bulletin lifecycle

1. Submit → state "submitted"
2. Moderate: Approve (state "accepted", operator id + optional start/end), Request Changes (state "needs_changes", notes), Decline (state "declined", notes)
3. Schedule: Approved → state "scheduled" with requested_start/end
4. On-air: Filler calls `store.list_approved(channel_id)`, filters to state in ("accepted", "scheduled") + time window check (requested_start <= now < requested_end)
5. Expire: Filler skips if requested_end < now. Daily cleanup deletes old expired bulletins.

### Board configuration resolution

1. Load active `CgBoard` for channel
2. Load all `CgZoneConfig` for board_id
3. For each zone, load `CgFeedSource` if content_source="feed_adapter"
4. Build `MultiZoneCgSnapshot` with zones + feed items
5. Hand the snapshot to the **GStreamer engine (S15)** — CG-lite zones (text/clock/image/crawl)
   map to `compositor` + `textoverlay`/`clockoverlay`/`gdkpixbufoverlay`; rich/animated zones map
   to a `wpesrc` HTML template. (CasparCG optional premium path bridged via NDI/SDI.) S6 produces
   the snapshot; S15 composites it into the channel.

### Feed fetcher (on-demand + cache)

```
def fetch_feed_items(feed_source: CgFeedSource) -> list[CgFeedItem]:
  - kind == "rss" → parse_rss(source_url)
  - kind == "ical" → parse_ical(source_url)
  - kind == "caldav" → parse_caldav(source_url)
  - kind == "weather" → fetch weather alerts
  - kind == "social" → fetch with trust_tier filter
  - Catch errors, record in last_fetch_error, fallback to cache
```

### Bulletin time-window enforcement

```python
now = datetime.now(UTC)
airable = [
    b for b in bulletins 
    if b.state in ("accepted", "scheduled")
    and (b.requested_start is None or b.requested_start <= now)
    and (b.requested_end is None or now < b.requested_end)
]
```

---

## 7. Proof tier: current rung + how to advance it

**Current rung:** 0 (Contract-tested)

**Advance to rung 1 (Lab-proven):**

1. **Unit tests** (add to `tests/cg/test_bulletin_store.py`):
   - Board CRUD, zone config, feed source, audit log
   - Bulletin time-window filtering
   - Feed item normalization (mock)

2. **API tests** (new `tests/cg/test_board_api.py`):
   - POST/PATCH board, zones, feeds
   - GET preview with mock data
   - GET audit log
   - Role-based access control

3. **Integration tests** (new `tests/cg/test_board_integration.py`):
   - End-to-end: create board → zones → feeds → preview → snapshot
   - Bulletin filler: board config → approved bulletins → rendered slides
   - Time-window enforcement + expiration

**Proof boundary:** `"cg-board-and-bulletin-lifecycle-to-multi-zone-snapshot"`

**Advance to rung 2 (Machine-proven):**

24h soak with CG-enabled channel: create board, submit/approve 5 bulletins, schedule 2, drive the **GStreamer engine (S15)** with the board's snapshot, verify rotation composited on-channel, expire one, verify skip, board stable under reload/restart. (Engine-side soak/continuity proof is owned by S15; S6's rung-2 bar is that the authoring layer keeps feeding a correct, stable snapshot for the duration.)

**Advance to rung 3+ (SDI/headend/field):** Deferred (S1/S2/S10/S15 handle those). Rich/animated CG via `wpesrc` and CasparCG premium CG prove out under S15's tiers.

---

## 8. Test plan (unit/API/e2e + 0/0/0/0/0 audit)

### Test files to create:

1. **`tests/cg/test_board_store.py`** (~200 LOC)
   - PostgresCgBoardStore CRUD
   - PostgresCgZoneConfigStore zone management
   - PostgresCgFeedSourceStore feed registration
   - CgBoardAuditStore audit events

2. **`tests/cg/test_board_api.py`** (~300 LOC)
   - Board, zone, feed CRUD endpoints
   - Preview endpoint with mock data
   - Audit log endpoint
   - Role-based access control

3. **`tests/cg/test_board_integration.py`** (~250 LOC)
   - Full workflow: board creation → zones → feeds → snapshot
   - Bulletin filtering with time windows
   - Filler integration
   - Expiration logic

4. **`tests/egress/test_bulletin_filler_board.py`** (extend existing, ~150 LOC)
   - Board-config-aware filler
   - Time-window enforcement
   - Empty bulletins → fallback to slate

### Audit expectations (0/0/0/0/0):

- **0 bugs:** No wrong behavior in CRUD, no state violations, no auth bypass
- **0 gaps:** Every endpoint tested, every UI screen integrated, every DB table round-tripped
- **0 incomplete:** No TODOs, proof boundaries clear
- **0 unclear:** Actionable error messages
- **0 undocumented:** Docstrings + OpenAPI docs for all endpoints

---

## 9. DONE criteria

✓ All entities (CgBoard, CgZoneConfig, CgFeedSource, CgBoardAuditEvent) defined + ORM + DB tables
✓ All CRUD stores implemented + tested
✓ Migration `0040_cg_board_zone_bulletin` (single global chain) creates tables, indexes, constraints
✓ Full V1 template set (fullscreen, L-bar, lower-third, bug, ticker, emergency overlay) shipped — no deferrals
✓ Manual zone text editor + feed-item approval gate shipped in V1 — not deferred to S7
✓ All staff API endpoints implemented, role-gated, tested
✓ App wiring: DI overrides + session factory + durable-storage check
✓ Bulletin lifecycle: submit → moderate → schedule → on-air → expire, all tested
✓ Board config resolution: snapshot builder wired
✓ Operator UI: 5 phone-first screens fully wired + responsive
✓ Proof: 100% unit/API/integration tests passing
✓ Audit: 0/0/0/0/0 checklist signed off
✓ Docs: All endpoints documented + OpenAPI + operator guide

---

## 10. Dependencies & cross-refs; Open decisions for Scott

### Dependencies:

- **S15 (playout engine — GStreamer):** S6 is the authoring/management layer; **S15 is the renderer.**
  CG-lite zones composite via the engine's `compositor` + `textoverlay`/`clockoverlay`/`gdkpixbufoverlay`;
  rich/animated zones render via `wpesrc` HTML templates. CasparCG is S15's optional GPLv3 premium
  co-process (designer-driven broadcast CG, bridged via NDI/SDI) — not required. S6 hands the resolved
  `MultiZoneCgSnapshot` to S15 via the CG-overlay contract (`cg_bridge.py`); it does not build a render
  engine.
- **S1:** Boards are per-channel; channels must exist in egress_configs. Branding used in preview.
- **S3 (future):** Add "Configure CG board" to installer wizard (deferred; manual wiring OK for V1).
- **S8:** Feed fetch errors appear in health alerts (deferred; S8 covers operator alerting).
- **S11:** Emergency overlay wired (EAS CAP ingestion in S11).

### Open decisions:

1. **Feed fetcher:** Background job (every 5 min, pre-cached) or on-demand with timeout+cache?  
   **Rec:** On-demand with cache (simpler, fresher data).

2. **Manual zone editor:** Operators edit text for zones with `content_source == "manual"`.  
   **Resolved (D13):** Yes — shipped in V1, not deferred.

3. **Custom template upload:** Support JSON template definitions (and, for rich CG, operator-uploaded WPE HTML/CSS/JS templates per S15 §5)?  
   **Rec:** Deferred. V1 ships the full built-in template set (fullscreen, L-bar, lower-third, bug, ticker, emergency overlay — all six per D13), rendered via the S15 `compositor`/WPE path; only *custom operator-uploaded* templates (JSON layouts or WPE HTML bundles) are out of V1 scope.

4. **Feed trust tier enforcement:** If `approval_required=true` + feed delivers unapproved items, hide until operator approves.  
   **Resolved (D13):** Yes — feed-item approval gate ships in V1 via the `cg_feed_item_approvals` table, not deferred.

5. **Bulletin submission portal:** Public or staff-only?  
   **Rec:** Public (already wired); S8 handles operator notifications.

---

*S6 build spec complete. Implementation does not begin until Scott approves this and the master spec.*

---
## Comparative additions (incumbent PEG platform gap closure → S18)
This section gains **CG depth** (S18 gap 6, migration `0053`): uploaded + **live-video bulletins**,
per-bulletin/per-channel **background audio** (under the loudness path), **zone tags/filtering**, and
**program-aware interstitials** ("coming up next"). See
the S18 comparative appendix.

**Current shipping boundary:** the existing slide/multi-zone/feed/image/logo/
ticker/alert CG renderer remains active. Live video in a zone and board
background audio are not rendered in this release. The operator designer labels
both as "coming in a future release" and disables background-audio selection;
their persistence contracts are not evidence of playback.


## CG depth — build detail (migration `0053_cg_depth`)

Extends the CG model (boards/zones/bulletins from `0040`) with the depth incumbent PEG graphics ships:

```python
class BulletinMedia(BaseModel):            # richer bulletin content kinds
    media_id: Slug
    bulletin_id: Slug
    kind: Literal["uploaded_image", "fullscreen_slide", "live_video"]
    asset_ref: Slug | None = None          # uploaded image / slide asset
    live_source: str | None = None         # for kind=live_video: NDI/SDI/stream input id (composited via engine)

class BulletinAudio(BaseModel):
    audio_id: Slug
    scope: Literal["bulletin", "channel"]  # per-bulletin narration OR per-channel background bed
    target_id: Slug
    asset_ref: Slug
    loudness_regime: str = "inherit"       # mixed under the S11 loudness path (never clips program)

class ZoneTag(BaseModel):                  # tag/filter content into zones
    tag_id: Slug
    label: str
# CgZoneConfig gains: allowed_tags: list[Slug]  (only tagged content fills the zone)
```
**Behavior:** (1) **live-video bulletin** = a CG zone whose source is a live input, composited by the S15 `compositor` (an L-bar/PiP over a bulletin board); (2) **background audio** mixed beneath silent bulletin boards under the existing loudness normalization (S11) so it never overwhelms; (3) **zone tags** filter which feed/bulletin items may appear in a given zone; (4) **program-aware interstitials** = a CG feed-kind that reads the program log (S4) to render "Coming Up Next"/"You Were Just Watching." Migration `0053_cg_depth` adds `bulletin_media`/`bulletin_audio`/`zone_tags` (after `0052`).

**Testable done-criteria:** DC-CG1 a live-video bulletin composites a live input into a zone with the rest of the board (lab); DC-CG2 per-channel background audio plays under a silent board, loudness-normalized, no clip (lab); DC-CG3 a zone with `allowed_tags` only shows tagged items (contract); DC-CG4 a "coming up next" interstitial renders the correct next program from the log (contract). Audit 0/0/0/0/0.
