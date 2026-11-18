# S12 — Generic Multi-Platform OTT Apps

**Status:** Built starter native app source for all 8 targets (Roku, iOS, tvOS, Android TV, Fire TV,
Android mobile, Samsung Tizen, LG webOS) and machine-CI-built on hosted runners — confirmed GREEN
end-to-end on `.github/workflows/ci-ott-apps.yml`, all 8 platforms producing a real build artifact,
as of 2026-08-25 (fix/ott-tizen-real-package, run 32819441306; every job independently re-verified).
Per-platform build status: **Roku** — real BrightScript static check
(`bsc`) + zip package. **CI-green.** **Android** (`tv`/`firetv`/mobile product flavors, one Gradle
module) — real `gradle assemble{Tv,Firetv}Debug` / `assembleDebug` builds. **CI-green.** **Apple**
(iOS + tvOS) — real `xcodebuild build-for-testing` (unsigned, `generic/platform=... Simulator`
destination) on `macos-latest`. **CI-green.** **LG webOS** — real `ares-package` build
(`@webosose/ares-cli`, no device/EULA needed; `--no-minify` works around the bundled minifier
choking on ES module syntax). **CI-green.** **Samsung Tizen** — the headless Tizen Studio CLI
install, certificate generation, security-profile add, `cli-config`, and (as of this fix) `tizen
package` itself all genuinely succeed in CI, producing a real signed `.wgt`
(`civiccast-tizen-wgt` artifact). Root cause of the earlier packaging failure: Tizen Studio CLI
2.5.25's `security-profiles add` writes a nonexistent sidecar `.pwd` file *path* into `profiles.xml`'s
password attribute instead of the real plaintext password, which the packager then reads literally
and rejects as an invalid password — `tizen/fix_signing_profile.py` patches it to the real plaintext
passwords right before packaging (see `tizen/README.md` for the full diagnosis). **CI-green with a
real `.wgt` build**, matching every other platform in this matrix; the static `config.xml`
validation (`tizen/validate_config.py`) remains as the fallback path for the (currently theoretical)
case where the headless Tizen Studio install itself fails on a given run. App-store publication
remains external (owner decision 2026-06-14: code-verify only, no store submission in V1).
**Scope:** Roku, Apple TV, Fire TV, Android TV, Android mobile, iOS/iPadOS, Samsung Tizen, LG webOS and Web/PWA shells  
**Functional target:** incumbent PEG workflow "branded streaming app workflow" / templated streaming app workflow (basic apps free with the incumbent cloud service; up to 3 channels)
**Key claim boundary:** Build + locally-prove generic multi-platform apps; app-store publication/certification is complete_with_external_dependency (rung gated by external store review)

---

## 1. Goal & PEG automation Rationale

An incumbent PEG workflow bundles "templated branded streaming apps" free with its cloud streaming service — pre-built native shells for Roku, Apple TV, Fire TV, Android, and iOS that ingest channel branding, live feeds, and VOD catalogs from a central config and push them to platform app stores. A PEG entity legally required by franchise to offer OTT (increasingly common with LPM and similar large systems) either deploys these templated incumbent app shells or hand-builds their own (LPM did, expensively).

**CivicCast 3.0 closes this gap with generic multi-platform OTT shells** built atop the existing `app_platform/` contract. The shells consume a unified `StationAppConfig` from `/api/public/app/config` and adapt it for each platform — phone-first (Android/iOS) and living-room-first (Roku/tvOS/Fire TV/Android TV) — all using the same station identity, channel branding, live-state feeds, VOD catalogs, captions, and audio tracks. **Generic-first for V1** (functional bar: proof the architecture works); platform-specific UX (remote polish, accessibility, platform features) is later work.

**Rationale for IN SCOPE and not deferred:**
- Frequently a franchise/city contract requirement (confirmed for LPM).
- the incumbent PEG platform includes it at no per-channel cost; absent in CivicCast, it's a functional gap.
- The contract layer (`app_platform/models.py`, `/api/public/app/config`, `app-platform-shells/`) already exists (v1.8.2) — this is wiring the shells to real operators + adding per-platform packaging, not building from zero.
- The proof bar for V1 is local (build + smoke test each target); store review is external and deferred.

---

## 2. Current State (File:Line Grounding)

### Existing contracts & infrastructure (shipped v1.8.0–v1.8.2)

| Component | File:Line | Status |
|---|---|---|
| **App-platform models** (StationAppConfig, ChannelPublicConfig, ChannelBranding, CgFeedSnapshot, etc.) | `civiccast/app_platform/models.py` (all) | **shipped** · defines AppTarget = Literal["web_pwa", "roku", "tvos", "fire_tv", "android_tv", "android_mobile", "ios_ipados", "cg", "epg"] |
| **Public config endpoint** (`GET /api/public/app/config`) | `civiccast/app_platform/router.py:100–111` | **shipped** · returns StationAppConfig w/ station identity + build profile + channels |
| **Live-state endpoint** (`GET /api/public/app/channels/{id}/live`) | `civiccast/app_platform/router.py:203–212` | **shipped** · returns LiveState (state, playback_url, caption_tracks, audio_tracks, proof_boundary) |
| **Schedule-feed endpoint** (`GET /api/public/app/channels/{id}/schedule`) | `civiccast/app_platform/router.py:215–227` | **shipped** · returns ScheduleFeedItem list |
| **VOD-catalog endpoint** (`GET /api/public/app/channels/{id}/catalog`) | `civiccast/app_platform/router.py:291–331` | **shipped** · returns VodCatalogResponse (items, playlists, facets, captions, chapters, playback_policy) |
| **Staff channel branding PATCH** | `civiccast/app_platform/router.py:180–201` | **shipped** · operator edits ChannelBranding (display_name, short_name, color, logo_url) |
| **Staff station config PATCH** | `civiccast/app_platform/router.py:141–162` | **shipped** · operator edits build_profile (app_name, tier, store_ready, store_notes) + config (support_url, privacy_url, analytics settings) |
| **AppPlatformConfigStore** (read/write/persist) | `civiccast/app_platform/store.py` | **shipped** · in-process thread-safe store w/ file persistence |
| **Shared shell runtime** (load config, select channel, render display) | `civiccast/apps/app-platform-shells/src/shell.mjs:1–101` | **shipped** · `loadChannelExperience()`, `selectDefaultChannel()`, `renderShell()` |
| **Target manifests + entry points** | `civiccast/apps/app-platform-shells/targets/{web-pwa,roku,tvos,fire-tv,android-tv,android-mobile,ios-ipados}/manifest.json` | **shipped** · define platform label, appTarget id, capabilities, entry point |
| **Build & smoke-test scripts** | `civiccast/apps/app-platform-shells/scripts/{build-targets.mjs,smoke-targets.mjs}` | **shipped** · npm run build + npm run smoke |
| **Store-readiness checklist** | `civiccast/apps/app-platform-shells/store-readiness.json` | **shipped** · defines monitoring checklist, proof classes, external requirements per target |
| **Contract test suite** | `civiccast/apps/app-platform-shells/test/shell-contract.test.mjs` | **shipped** · verifies targets exist, manifests point at /api/public/app/config, sample config covers all targets |

### The S12 build list (net-new work — i.e. what doesn't exist yet)

**Read this as the build list, not a status snapshot:** every row is net-new work S12 delivers. When
S12 ships, this table is empty — the gaps are closed. The third column is the **deliverable** (what we
build), not the workaround if we don't.

| Component | Missing today | What S12 builds (closes the gap) |
|---|---|---|
| **App admin panel** (operator screen) | No UI to set `build_profile`, pick targets, upload a logo, toggle store_ready | The App Admin Dashboard (§5) — self-serve config + the "drop your logo here" branding field (§6) |
| **Per-platform branding assets** (logo/icon/splash) | `icon_url`/`splash_url` fields exist but no upload UI | Operator logo/icon/splash upload, themed at runtime (§6 — now **V1**, previously "future") |
| **Native build toolchain** (the 4 codebases) | Only generic HTML shells; no pipeline to produce native packages | In-tree build toolchain for Roku / Apple / Android / Web + per-storefront maintenance Routines (§11) |
| **Native platform apps** (real per-platform input/UX) | Generic shells "work but feel generic" | **The 4 native codebases themselves (§11)** — this is now *core* S12 work, **not** deferred polish |
| **Store submission tooling** | No store-API integration; submissions tracked by hand | `StoreSubmissionMetadata` tracking + the Routines that prep submission PRs (final submit stays human-gated, §11) |
| **App version rollback** | Immutable build log (`AppBuildRecord`) exists for audit | V1 = audit every built version; **one-click rollback / re-submit of a prior build is V2** (open-decision #3) |

---

## 3. Entities / Data Model & Migrations

### Reuse from master §6 (no changes)

- `StationAppConfig`, `AppBuildProfile`, `ChannelBranding`, `ChannelPublicConfig`, `ChannelOutput`
- `LiveState`, `ScheduleFeedItem`, `VodCatalogItem`, `VodCatalogResponse`, `CaptionTrack`, `AudioTrack`, `ChapterMarker`

### Net-new entities (S12 additions)

**`AppBuildRecord`** — immutable build log entry

```python
class AppBuildRecord(BaseModel):
    """Immutable record of a platform-specific app build."""
    record_id: Slug
    station_id: Slug
    app_target: AppTarget
    build_tier: AppBuildTier
    app_name: str
    icon_url: str | None = None
    splash_url: str | None = None
    channels: list[dict]  # channel_id, branding snapshot
    artifact_path: str
    artifact_sha256: str
    entry_point: str
    manifest_json: dict[str, Any]
    built_at: datetime
    built_by: str
    proof_boundary: str = "local-build-artifact-sha256-verified"
    store_submission: StoreSubmissionMetadata | None = None
```

**`StoreSubmissionMetadata`** — deployment tracking

```python
class StoreSubmissionMetadata(BaseModel):
    """External store submission metadata and status."""
    app_target: AppTarget
    store_account_email: str | None = None
    package_id: str | None = None
    version_code: int
    version_name: str
    submitted_at: datetime | None = None
    submission_status: Literal["draft", "pending_review", "approved", "rejected", "published", "withdrawn"] = "draft"
    submission_notes: str | None = None
    published_url: str | None = None
    support_contact: str | None = None
```

### No schema migration required

Build records and submission metadata are auxiliary tables under `app_builds/` subdirectory with own persistence layer; they do not alter core config schema.

---

## 4. API Surface

### Public read endpoints (existing, unchanged)

All endpoints documented in `civiccast/app_platform/router.py:63-362`; no new public endpoints.

### Staff/operator write endpoints

**Existing (v1.8.0+):**

```
PATCH /api/staff/app/config
PATCH /api/staff/app/channels/{channel_id}/branding
```

**Net-new S12 endpoints:**

```
GET  /api/staff/app/builds
GET  /api/staff/app/builds/{record_id}
POST /api/staff/app/builds
GET  /api/staff/app/builds/{record_id}/download
GET  /api/staff/app/store-submissions
PATCH /api/staff/app/store-submissions/{app_target}
```

All require `require_any_role("setup_admin", "publish_operator")` (build queueing is `setup_admin`).

---

## 5. Operator UI Surface

### New screens (S12)

- **App Admin Dashboard** (`/portal-operator/app-admin`)
  - Build Profile section (tier, app_name, icon_url, splash_url, store_ready)
  - Platform Target Selection (all 7 targets, V1 disabled selector)
  - Build History table (sortable, paginated, download artifacts)
  - Store Submission Tracker (inline editable, status tracking)

- **Build Target Selector Modal** (triggered by "New Build")
  - Select platform target(s)
  - Select tier (unbranded/branded)
  - Queue builds

- **Build Details View** (click build history row)
  - Artifact SHA256, entry point, manifest preview
  - Channel branding snapshot
  - Download options
  - Rebuild/delete actions

- **Store Submission Editor**
  - Edit account email, package ID, version, status, notes
  - Mark published

### Responsiveness

All screens phone-first (480px min width); stacked layouts on mobile, desktop-optimized on ≥800px.

---

## 6. Behavior / Algorithms

### App build orchestration

1. **Validate & snapshot** — Read current config, validate URLs (HTTPS or relative)
2. **Queue job** — Create AppBuildRecord stub, write to app_builds/ store
3. **Build** — Generate platform-specific entry point, inject branding assets, run build tool, compute SHA256
4. **Failure handling** — Log errors to AppBuildRecord, allow operator to requeue

### Branded vs unbranded tier

- **Unbranded:** app_name = "CivicCast", generic bundled assets (fallback when a station provides nothing).
- **Branded — self-serve, V1 (decided 2026-06-14):** the station uploads a name + logo (+ color) via a
  "drop your logo here" field; the shared per-platform app reads it from `/api/public/app/config` and
  reskins at **runtime** — no rebuild, no store trip. This is "Version A" (see §11). A standalone
  *named* store app per station is a separate **premium managed-service add-on**, not this tier.

### Store submission tracking

Operator manually updates submission status, published URL, and support contact in portal. No API calls to external stores; operator submits offline.

---

## 7. Proof Tier: Current Rung + Path to Advancement

### Current proof state (end of S12)

| Step | Rung | Evidence |
|---|---|---|
| Build artifact generated | 0 (contract) | Unit tests: shell.mjs loads config, renders per platform |
| Artifact SHA256 verified | 1 (lab) | Build script checksums; recorded in AppBuildRecord |
| Operator side-load smoke test | 1 (lab) | Operator downloads, side-loads, confirms live/VOD playback (one-off side-load = rung 1) |
| Device-lab / emulator smoke pass | 2 (machine) | Automated build + emulator smoke across targets — this is the OTT rung-2 bar |
| Store submission | rung 4 (store acceptance); rung 3/SDI N/A for OTT | Operator records published_url in portal after app-store acceptance |

### Path to advancement

> **Rung 3 (SDI) is NOT APPLICABLE to OTT** (per master §5 / RECONCILIATION D7). OTT carries no
> SDI signal path, so it advances **rung 2 → rung 4 directly via app-store acceptance**. The
> **device-lab / emulator smoke pass is the OTT rung-2 bar**; a one-off operator side-load of a
> build artifact is only **rung 1**.

- **Rung 1 → 2:** Device-lab / emulator smoke pass — automated nightly build + emulator smoke test (7 days) clears the rung-2 bar (a single operator side-load is rung 1, not rung 2).
- **Rung 2 → 4:** Published to real store (store acceptance), operator confirms playback on real device. (Rung 3/SDI is skipped — N/A for OTT.)
- **Rung 5:** Running 30 days unattended in production

### Proof claim boundary

**Do claim:** "Local build artifact for each platform, verified by operator."  
**Do NOT claim:** "App-store certified", "Production-ready on all devices", "DRM-protected".  
**Document:** "App-store publication subject to each platform's review process."

---

## 8. Test Plan

### Unit tests (contract-tier)

- Config loading, channel selection, shell rendering
- Target manifest validation
- Build script smoke test
- Coverage: >80% on shell.mjs, models.py, router.py

### API tests (contract-tier)

- Config endpoints, build API, store submission API
- All 13 endpoints tested (happy path + error cases)

### E2E tests (lab-tier)

- Navigate to App Admin, edit build profile, queue build, download artifact, edit store submission
- 5 Playwright scenarios

### Audit expectations

- 0 unfixed bugs, 0 unresolved decisions, 0 undocumented APIs, 0 untested paths, 0 ungrounded claims
- Code audit reaches 0/0/0/0/0

---

## 9. DONE Criteria

S12 is complete when:

1. All 13 endpoints live on main, marshalled into OpenAPI schema
2. Operator UI complete & responsive; all PATCH calls succeed
3. Build orchestration complete; artifacts downloadable
4. Test coverage >80%; all 5 E2E scenarios pass
5. v1.8.0 app-platform contract updated with S12 entities + endpoints
6. Commissioning wizard includes OTT app section
7. Proof boundary clearly marked; audit passes 0/0/0/0/0

---

## 10. Dependencies & Cross-References

### Depends on

| Section | Why |
|---|---|
| **S1** | Commissioning includes OTT app config |
| **S3** | Commissioning Wizard has new "OTT App Setup" step |

### Cross-references

| Section | How |
|---|---|
| **S6** (CG) | Both use ChannelBranding; independent features |
| **S7** (Media) | VOD flows through publish → app_platform → apps |
| **S11** (Captions) | Caption/audio tracks in LiveState; apps consume as-is |

### Open decisions for Scott

1. **Branded tier in V1? — DECIDED IN (Scott, 2026-06-14):** self-serve **runtime-themed** branding
   ships in V1 (Version A, §11 / §6). Per-station *named* store apps stay a premium add-on.
2. **Toolchain & ongoing maintenance — DECIDED (Scott, 2026-06-14):** in-tree build toolchain for the
   4 codebases, with a **scheduled Routine per codebase/storefront** automating detect → update → build
   → test → PR + alert (store submission/cert steps stay human-gated). See §11. This is what makes the
   6-storefront maintenance tail tractable.
3. **App version history & rollback?** Resolved: **V1 tracks an immutable build log** (`AppBuildRecord`, one entry per build) so the operator can see and audit every version built and select which to submit. The **rollback mechanism** (one-click re-submit of a prior build to a store) is the **deferrable part** — V2.
4. **DRM / content protection?** Not in V1 scope; deferred.

---

## 11. Platform build matrix (4 codebases → 6 storefronts) + maintenance automation

> **Decided 2026-06-14 (Scott):** ship the full reach — **4 native codebases → 6 storefronts** —
> using the **self-serve runtime-themed branding** model (Version A) already wired into
> `app_platform/`. This *exceeds* incumbent PEG platform templated streaming app workflow (which targets only Roku/Apple TV/Fire TV/
> Android TV/iOS). The generic HTML shells (§2) graduate to real per-family apps; the
> `StationAppConfig` contract is unchanged — the apps stay thin clients over `/api/public/app/config`.

### Version A model (what we build)
**One shared app per platform, themed per-station at runtime, content auto-populated.** There is no
per-station app binary and no separate "load content" step:
- **Branding** = config. A clerk uploads a logo + name + color (the existing `ChannelBranding` PATCH +
  a "drop your logo here" field); the running app reads it from `/api/public/app/config` and reskins.
  Instant, no rebuild, no store trip.
- **Content** = the same API. The app calls our existing live/schedule/catalog endpoints, so a station's
  cable content, web VOD, and OTT app all serve from one source of truth (CivicCast). New meeting
  uploaded → it appears in the app automatically. **Nobody populates the app; it reads what already
  exists.**
- Per-station *named* store apps (a separate "City of X TV" listing) remain a **premium managed-service
  add-on**, not the baseline — that's the only part whose cost scales with station count.

### The 4 codebases → 6 storefronts
| # | Codebase | Language/stack | Devices it serves | Storefront(s) |
|---|---|---|---|---|
| 1 | **Roku** | BrightScript / SceneGraph | Roku players + Roku TVs | Roku Channel Store |
| 2 | **Apple** | Swift / SwiftUI (universal) | Apple TV (tvOS) + iPhone/iPad (iOS) | Apple App Store |
| 3 | **Android** | Kotlin (TV + mobile form factors) | Android TV/Google TV + Fire TV + Android phones/tablets | **Google Play** (Android TV + mobile) **and Amazon Appstore** (Fire TV) |
| 4 | **Web/HTML** (graduates from today's `app-platform-shells`) | HTML/JS/PWA | Samsung Tizen TVs + LG webOS TVs + any browser | **Samsung Tizen Store** + **LG Content Store** (+ direct PWA, no store) |

**The 6 storefront pipelines:** Roku · Apple App Store · Google Play · Amazon Appstore · Samsung Tizen ·
LG Content Store. (Web/PWA is a 7th *reach* with no store.) Note the asymmetry that drives cost:
**4 codebases but 6 store pipelines** — Fire TV (Amazon) and Android TV (Google) share Kodebase 3 yet
are two separate accounts/reviews; Tizen and webOS share Codebase 4 yet are two separate stores. The
maintenance burden scales with **storefronts (6)**, not codebases (4).

**Sequencing by PEG audience:** Roku + Fire TV + Apple TV first (the must-haves), then Android TV +
mobile (Google Play), then Samsung + LG (the "extend beyond incumbent PEG workflow expectations" reach).

### Maintenance automation via scheduled Routines (Scott's strategy)
The store-churn tail (SDK deprecations, store-policy changes, cert renewals, dependency CVEs across 6
storefronts) is made tractable by a **scheduled CivicCast Routine per codebase/storefront** that:
1. **Detects** — polls each platform's SDK/release notes + store-policy/cert-expiry + our dependency
   advisories on a cadence (monthly, plus on-SDK-release triggers).
2. **Updates + builds + tests** — bumps SDKs/deps, rebuilds the app, runs the existing
   `smoke-targets` suite, and **opens a PR** with the diff + a changelog of what changed and why.
3. **Alerts** — flags anything that needs a human (a breaking store-policy change, an expiring cert, a
   failed smoke test) rather than silently passing.

**Honest boundary (not full automation):** the Routine automates *detect → update → build → test → PR
+ alert*. The **store submission/review and cert renewals remain human-gated** (Apple/Roku review,
Google/Amazon/Samsung/LG signing are not fully scriptable). So the Routine turns "6 stores rotting
silently until something breaks on-air" into "6 stores whose drift is caught early and 80%-prepared as
a reviewable PR." That is what makes 6 storefronts maintainable by a small team over time.

### Effort & proof
This is materially larger than the generic-shell proof. The **8-engineer-week figure below covered the
contract + generic HTML shells + API + UI** (largely shipped). The native 4-codebase / 6-storefront
build is a **phased, multi-month effort** (roughly: Roku ~3–4 wk, Apple ~3–4 wk, Android ~4–5 wk, Web/
TV-OS ~3–4 wk, + the Routines maintenance harness ~2 wk), each clearing the OTT proof ladder
(side-load = rung 1 → emulator/device-lab smoke = rung 2 → store acceptance = rung 4; SDI/rung-3 N/A).
The branding + feed *backend* stays small (it's the existing contract + a logo-upload field).

---

Estimated implementation effort: **8 engineer-weeks for the contract/shell/API/UI layer (largely
shipped); the full 4-codebase / 6-storefront native build is a separate phased effort — see §11.**
