# S3 — Commissioning Wizard: Extend the Installer with Headend/SDI/Output Proof

> **Status (2026-08-25 CEA-708 decode-back wiring):** The 2026-08-21 correction
> below states that a requested CEA-708 passthrough check was always reported as
> `cea708_verified: null` because no decode-back check was wired in. That gap is
> now closed on `feat/cea708-decode-back`:
>
> - `civiccast/installer/cea708_verification.py` (new): a real, fail-closed
>   embed-through-decode-back check. Writes a deterministic one-cue WebVTT test
>   caption, embeds it through the product's real GStreamer sidecar caption-embed
>   leg (`egress/gst/graph.py`'s `caption_embed_leg_from_sidecar`, run via
>   `egress/gst/worker.py` as a subprocess over the same D2 named-pipe control
>   seam `scripts/prove_native_live_caption_transport.py`'s code already
>   assembles this way for the live appsrc leg), then
>   decodes the emitted stream back with the existing engine-agnostic
>   `civiccast.egress.caption_proof.decode_embedded_captions` (the same function
>   the S11a ON_AIR proof loop uses) and compares. Reports a real
>   `verified: bool` + detail; never reports `True` without an actual matching
>   decode, and never reports anything but `None` (not attempted) when the
>   verifier itself could not run.
> - `run_output_proof` (`civiccast/installer/commissioning.py`) now calls this
>   (injectable via the new `caption_verifier` parameter, matching the existing
>   `test_pattern_runner`/`compliance_prober` DI seams) AFTER the main test-
>   pattern/TSDuck window when `cea708_expected` is set, and sets
>   `cea708_verified` to the real `True`/`False` result. It stays `None` only
>   when the verifier itself raised (engine/plugins unavailable, subprocess
>   error) — an honest "could not attempt", not a fabricated claim either way.
> - Standalone CLI: `civiccast egress verify-captions` runs the same check
>   without going through commissioning, for checking a box's CEA-708 lane
>   (GStreamer engine + plugins + ffmpeg decode) on its own.
> - A real, latent bug this work found and fixed: `_clean_caption_text`
>   (`civiccast/egress/caption_embed.py`) had never been exercised against real
>   ffmpeg-decoded closed-caption output before (only hand-written SRT text in
>   unit tests) — ffmpeg's `eia_608`/`cc_dec` decoder wraps decoded text in an ASS
>   position override (`{\an7}`) that the old cleaner never stripped, so every
>   real decode-back text comparison would have mismatched even when captions
>   embedded and decoded correctly. Fixed and covered by a regression test.
> - Tests: `tests/installer/test_cea708_verification.py` (new — the decode leg
>   proven against a real, committed MPEG-TS fixture with genuine embedded
>   CEA-608-in-708 SEI data; the embed leg's compare-and-report logic covered
>   with an injected fake `embed_runner`; the real GStreamer worker round trip
>   covered by an `@pytest.mark.integration` test that skips without the bundled
>   `gi` bindings this dev/CI sandbox does not carry), plus new/updated cases in
>   `tests/installer/test_commissioning.py`, `tests/egress/test_caption_proof.py`,
>   and `tests/egress/test_caption_embed.py`.
> - **Still honestly unverifiable in this dev/CI sandbox**: the real GStreamer
>   embed subprocess round trip (no `gi`/GStreamer runtime here) — not executed
>   or independently proven from this change. It reuses code review of
>   `scripts/prove_native_live_caption_transport.py`, which assembles the same
>   worker/pipe machinery for the live appsrc leg, but that script's own
>   end-to-end execution is not evidenced anywhere in this repo either (only its
>   component pieces are unit-tested in
>   `tests/native/test_live_caption_transport_proof.py`) — treat this as
>   real, reviewed code following an established pattern, not as a proven
>   round trip. A native Windows box with the packaged runtime, or the
>   WSL/system-GStreamer dev tier, is required to actually run and prove
>   `run_gst_caption_embed_test_pattern`.
>
> **Status (2026-08-21 audit correction):** The banner below this note
> previously claimed "Built for v3.0.0-beta1" while the code did not exist —
> corrected here to state what actually landed on
> `feat/s1-station-profile-s3-commissioning`, contract-tested (rung 0):
>
> - `civiccast/installer/commissioning.py`: all §3 models
>   (`CommissioningCheckItem/Report`, `ChannelCommissioningSetup`,
>   `OutputProofSettings`, `CommissioningProofRun`, `CommissioningReport`,
>   plus `CommissioningState` for resumability) and the 4 step functions
>   (`run_first_run_cable_checks` — the real 11 checks per §6;
>   `validate_channel_commissioning_setup`; `run_output_proof` — a real
>   bounded ffmpeg SMPTE-bars+tone generator run concurrently with the
>   existing TSDuck compliance prober, fail-closed, never a soft pass;
>   `build_commissioning_report`). 23 unit tests
>   (`tests/installer/test_commissioning.py`).
> - Persistence: `read_commissioning_state`/`save_commissioning_*` in
>   `station_state.py`, one namespaced `"commissioning"` key, no DB table —
>   proven resumable (a step's result survives independently of the others).
> - API: `POST /api/staff/cable/commissioning/{checks,channel-setup,
>   output-proof,report}` + `GET .../state`, role-gated per §4
>   (`setup_admin` write, `+support_admin` read). 11 API tests
>   (`tests/installer/test_commissioning_api.py`).
> - CLI: `cable doctor`/`commission`/`support-bundle` (the last is a thin
>   wrapper over the pre-existing `create_diagnostic_bundle`, not
>   reimplemented), `output sdi-readiness`, `egress output test-pattern`. 6
>   CLI tests (`tests/test_cli_commissioning.py`).
> - Operator console: `CommissioningWizardScreen.tsx` — screens 8-11 as 4
>   server-state-gated panels (channel/headend pickers reuse the existing
>   `listChannelProfiles`/`listHeadendProfiles` endpoints; screen 11 reuses
>   the existing `SupportBundlePanel` rather than rebuilding it).
> - Honesty boundaries carried into the code: `CommissioningProofRun`
>   always carries a `not_claimed` line stating the proof is a headend/
>   format proof via ffmpeg + TSDuck, not physical SDI/DeckLink hardware
>   proof; a requested CEA-708 passthrough check is always reported as
>   unverified (`cea708_verified: null`) with a blocker, never faked
>   true/false, since no decode-back check is wired in yet.
>   **(Superseded 2026-08-25 — see the banner above: a real decode-back
>   check is now wired in and `cea708_verified` carries a real
>   `True`/`False`, `None` only when the verifier itself could not run.)**
> - **Not done in this slice**: the output-proof HTTP endpoint blocks
>   synchronously for the full `duration_seconds` (up to 30 min) rather than
>   running as a background job with progress polling — functionally
>   correct (mirrors the pre-existing `egress verify` CLI's same bounded-
>   blocking posture) but not the smoothest console UX for a long proof
>   run; a future slice could move it to a job/poll pattern. CEA-708
>   decode-back verification itself (S11) is not implemented — only its
>   honest non-claim. **(Superseded 2026-08-25 — see the banner above.)**
>
> The banner immediately below is the section's **original**
> (pre-2026-08-21) claim, retained for history.
>
> **Status:** Built for v3.0.0-beta1; headend/lab acceptance remains external.
> **Scope:** Extend the existing 7-step first-run installer wizard with 4 new commissioning steps (headend/SDI/TSDuck/output-proof), wiring CLI commands (`doctor`, `commission`, `support-bundle`, `output sdi-readiness`, `egress verify`), and ensuring phone-first operator guidance where possible. **Per-tier TURNKEY install** — the installer does ALL the work (no BYO): it installs/configures the **GStreamer playout engine** (S15) and its tier-appropriate plugins/SDKs, and the output proof now exercises that engine end-to-end to the selected sink.

---

## 1. Goal & PEG automation rationale

**What incumbent PEG platform does:** the incumbent PEG platform's commissioning path bakes hardware verification, headend connectivity, output format, and SDI proof into the product install flow. A station connects an SDI output, configures the headend profile, and the system proves the output is alive before the operator's first broadcast.

**The gap we close:** CivicCast today ships a 7-step first-run wizard (profile, hardware, storage, operator account, publish targets, models, health check) that focuses on publishing and metadata. It has no cable-specific commissioning path: no playout-engine install, no headend profile selection, no SDI device discovery, no output format choice, no test-pattern generation, no 10-minute proof run, no commissioning report.

**Turnkey, per-tier engine install (Scott's hard requirement — no BYO):** the installer itself provisions the **GStreamer playout/compositor/output engine** (S15) and everything it needs, scaled to the detected/selected tier. There is no "bring your own ffmpeg" step anywhere; the commissioning checks and the output proof verify the engine the installer placed:
- **BASE / streaming tier:** GStreamer runtime + the base/good/bad/rs plugin set + the bundled `openh264enc` CPU encoder. Optional operator-provided `x264enc` remains supported but is not shipped by CivicCast. Drives IP-TS / SRT / HLS + CG-lite. The "$5K commodity PC, no GPU" promise stays intact.
- **SDI / broadcast tier:** the above **plus** Blackmagic DeckLink drivers + the **BMD Desktop Video SDK** + the GStreamer `decklink` plugin (`decklinkvideosink`, fill+key) + GPU/OpenGL stack + hardware-encoder plugins (`nvh264enc`/`nvh265enc` or `vah264enc`/`vah265enc`) for broadcast-quality encode and WPE rich CG.
- **NDI tier:** plus the **NDI SDK** (runtime) + the GStreamer `ndi` plugin (`ndisink`, gst-plugins-rs / MPL).
- **PREMIUM-CG tier:** plus **CasparCG** as a separate (GPLv3) co-process, quarantined to that tier and bridged via NDI/SDI — optional, never required, never linked into the Apache core (see S15 §5).

**This section's work:** Extend the 7-step installer with 4 new commissioning steps inserted after "health check," producing the canonical **11-step wizard**. Per RECONCILIATION D12, **S3 owns the canonical commissioning wizard step list** — OTT (S12), AI adaptive-default (S13), watch-folder (S7), and CEA-708 passthrough (S11) **fold into S3's existing screens rather than each section inventing its own screen.** Master §4 item 11 / §12 track this final count.

The 4 new commissioning steps:
- [NEW] First-run cable checks: hardware, OS, storage, **GStreamer engine + tier plugin set** (S15), DeckLink driver + BMD SDK + `decklinkvideosink` (SDI tier), TSDuck, db, services, backup, timezone, release integrity
- [NEW] Channel setup: station/channel name, output format, SDI device, headend profile, fill policy, emergency slate — **plus CEA-708 caption passthrough toggle (S11)** and **watch-folder ingest path (S7)** folded in here
- [NEW] Output proof: generate test bars + tone + slate **through the GStreamer engine to the selected sink** (S15), live preview + 10-minute capture — **CEA-708 passthrough is verified here when enabled (S11)**
- [NEW] Commissioning report: pass/fail/blockers + support bundle

The folded-in concerns ride existing screens (no new screens added): **AI adaptive-default (S13)** rides the existing Screen 6 (models) — the summary-model default keys off detected system RAM; **OTT app configuration (S12)** rides the existing Screen 5 (publish-targets) as an additional publish target; **watch-folder (S7)** and **CEA-708 passthrough (S11)** ride Screen 9 (channel setup). The canonical step list is enumerated in §1a below.

---

## 1a. Canonical commissioning wizard step list (D12 — S3 owns this; total = 11 screens)

This is the single authoritative wizard step list for CivicCast 3.0. No other section may add or
renumber a screen; cross-section concerns fold in as noted. **Final count: 11 screens.**

| # | Screen | Owner | Folded-in concerns |
|---|---|---|---|
| 1 | Profile (hard-wired "public-meetings" in V1) | installer | — |
| 2 | Hardware probe + canonical tier (`tier-0`/`tier-1`/`tier-1-plus`/`tier-2`) | installer / S1 | calls S1's `doctor` (D10) |
| 3 | Storage (media volume + NAS archive path) | installer | — |
| 4 | Operator account (creates `StationProfile`) | installer | created with `setup_admin` role (D1) |
| 5 | Publish targets (CDN, IA, YouTube, NAS, …) | installer | **OTT app configuration (S12)** folds in here as an added publish target |
| 6 | Models (orchestrates model download) | installer | **AI adaptive-default (S13)** folds in here — summary-model default (`gemma4:12b` ≥16GB RAM, fallback `gemma4:e4b`) keys off detected system RAM |
| 7 | Health check (`run_first_health_check()`) | installer | — |
| 8 | First-run cable checks (incl. GStreamer engine + tier plugins/SDKs, fail-closed) | **S3** | engine install verified per tier (S15) |
| 9 | Channel output setup | **S3** | **watch-folder ingest path (S7)** and **CEA-708 caption passthrough toggle (S11)** fold in here |
| 10 | Output proof — test pattern & capture | **S3** | **CEA-708 passthrough verified here when enabled (S11)** |
| 11 | Commissioning report | **S3** | — |

---

## 2. Current state

### Existing: 7-step FirstRunPlan (code @ `civiccast/installer/service.py:291`)

The `build_first_run_plan()` function returns a `FirstRunPlan` with:
1. profile — hard-wired to "public-meetings" in V1
2. hardware — calls `civiccast/platform/hardware.py:probe()`, recommends the canonical VRAM-keyed tier (`tier-0` / `tier-1` / `tier-1-plus` / `tier-2`, per RECONCILIATION D2)
3. storage — operator picks media volume + NAS archive path
4. operator-account — creates `StationProfile` via `station_state.py`
5. publish-targets — gathers credentials (CDN, IA, YouTube, NAS, etc.)
6. models — orchestrates model download
7. health — runs `run_first_health_check()`, checks mTLS/external targets (the
   NATS/JetStream readiness check this used to run was removed along with
   NATS itself -- owner decision 2026-08-20, see ADR 0023)

### Existing CLI commands ready to integrate

- `civiccast doctor` (cli.py:139) — **S1's canonical doctor surface** (hardware probe + `StationBoxProfile`, recommends the canonical VRAM-keyed tier per D2). S3 does NOT redefine `doctor`; S3's `station doctor`/`station commission --cable` are cable-readiness extensions that cross-reference S1's command (RECONCILIATION D10).
- `civiccast installer health-check` (cli.py:209) — First-run health checks
- `civiccast egress verify` (cli.py:921) — TSDuck compliance probe on UDP-TS (now run against the GStreamer engine's TS output, S15)

### Existing egress modules

- `civiccast/egress/headend.py` — 6 named `HeadendProfile` (generic-udp-spts, comcast-mtd-sd/hd, telvue, harmonic, leightronix). Lab-proven; not field-proven. **Orchestration ported unchanged onto the GStreamer engine** (S15) — these profiles now configure the engine's sink/mux, not a per-segment ffmpeg.
- `civiccast/egress/sdi_relay.py` — `SdiReadiness`, `SdiRelayStatus` (state machine). Contract-level; no field proof without real card. SDI output is now the GStreamer `decklinkvideosink` (S15), not a BYO-ffmpeg relay.
- `civiccast/egress/compliance.py` — `ComplianceProbeResult`, `HeadendReadinessRollup`. Lab-proven; not field-proven.
- `civiccast/egress/sinks.py` — `EgressSinkSpec` (udp-ts/srt/rtmp/file/ndi-relay/sdi-relay), each now realized as a GStreamer sink (S15 §4)
- `civiccast/egress/slate_source.py` — Emergency slate generation

### CLI (shipped)

The cable CLI commands shipped (see the implementation status above): `cable doctor`,
`cable commission`, `cable support-bundle` (a thin wrapper over the pre-existing
`create_diagnostic_bundle`), plus `output sdi-readiness` and `egress output test-pattern`.
- `civiccast output sdi-readiness` — NOT YET IMPLEMENTED; verifies the SDI output path the installer placed: GStreamer `decklinkvideosink` element present + DeckLink card detected + BMD Desktop Video SDK at runtime (S15) — not a BYO ffmpeg

---

## 3. Entities / data model & migrations

### New Pydantic models

All per master §6 net-new list:
- `CommissioningCheckItem` — One check (id, label, status, detail, next_step)
- `CommissioningCheckReport` — All checks collected (station_name, checks[], ready, blockers[], support_bundle_path)
- `ChannelCommissioningSetup` — Operator's channel choices (channel_id, channel_name, output_format, headend_profile_id, destination, muxrate, sdi_device, fill_policy, emergency_slate_asset_id)
- `OutputProofSettings` — Control proof run (test_pattern, duration_seconds, channel_id, output_directory, capture_raw_ts)
- `CommissioningProofRun` — Proof result (channel_id, proof_id, started_at, ended_at, test_pattern, compliance_probe_result, sdi_device_status, verdict, blockers, detail, raw_ts_path, not_claimed)
- `CommissioningReport` — Final handoff (station_name, channel_name, headend_profile_id, output_format, sdi_device, completed_at, first_run_checks, channel_setup, proof_run, next_steps, support_bundle_path)

### Migrations

**None (RECONCILIATION D-table).** S3 adds **no** alembic migration. Commissioning state and the
final commissioning report persist as **config / state-file** (riding S1's station-state
mechanism, `installer/station_state.py`), not database tables — consistent with the single global
alembic chain (head `0037`; the `0038`+ sequence is owned by S4–S13). The `CommissioningReport`,
`CommissioningProofRun`, and `ChannelCommissioningSetup` models above are serialized into the
station-state JSON, not migrated.

---

## 4. API surface

### New endpoints (all on `cable_app` and `egress_app`)

Auth uses the five real roles (RECONCILIATION D1: `setup_admin`, `meeting_operator`,
`records_clerk`, `publish_operator`, `support_admin`; `operator`/`admin` are all-roles aliases,
not real roles). Commissioning is a setup activity, so **write/orchestration endpoints require
`setup_admin`** and **read-only diagnostic surfaces require `support_admin`** (D1's rule for
diagnostic reads). Per endpoint:

```
cable doctor [--json]                    auth: require_any_role(["support_admin", "setup_admin"])
  Return: CommissioningCheckReport
  Purpose: Run first-run cable readiness checks (read-only diagnostic)

cable commission --channel-id=<id> --headend-profile=<id> --destination=<addr:port> \
  [--sdi-device=<name>] [--fill-policy=slate|loop|silence] [--slate-asset-id=<id>] [--json]
                                         auth: require_any_role(["setup_admin"])
  Return: CommissioningReport
  Purpose: Run the cable commissioning steps (checks, setup, proof, report)

cable support-bundle [--output-dir=<path>] [--json]
                                         auth: require_any_role(["support_admin", "setup_admin"])
  Return: { bundle_path: str, size_bytes: int, manifest: list[str] }
  Purpose: Export logs, config, health, commissioning reports, soak data (read-only diagnostic)

output sdi-readiness [--json]            auth: require_any_role(["support_admin", "setup_admin"])
  Return: SdiReadiness (ok / decklinkvideosink_missing / decklink_card_absent / bmd_sdk_unavailable)
  Purpose: Quick check: is the GStreamer SDI output path ready — `decklinkvideosink` element + DeckLink card + BMD SDK (S15)? (read-only diagnostic)

egress verify --channel-id=<id> --seconds=10 [--work-dir=<path>] [--json]
  (ALREADY EXISTS @ cli.py:921)        auth: require_any_role(["support_admin", "setup_admin"])
  Return: ComplianceProbeResult
  Purpose: Bounded TSDuck probe on live UDP-TS (read-only diagnostic)

egress output test-pattern --channel-id=<id> --pattern=bars|tone|slate \
  [--duration-seconds=600] [--json]      auth: require_any_role(["setup_admin"])
  Return: { started_at: datetime, pattern: str, channel_id: str }
  Purpose: Start a test pattern through the GStreamer engine to the selected sink for 10 minutes (S15) (state-changing)
```

---

## 5. Operator UI surface

### Wizard screens 8–11 (4 new screens)

**Screen 8: First-run cable checks** 
- Scrollable list of 11 checks (OS, disk, GStreamer engine + tier plugins, DeckLink + BMD SDK + `decklinkvideosink` [SDI tier], TSDuck, db, services, backup, timezone, release)
- Each: label, status (pass/fail/warning/skipped), detail, next-step guidance
- Buttons: Retry, Continue anyway (warnings only), Back
- Phone-friendly: list layout, tap-to-expand, large buttons
- Fail-closed: if any fail, disable Continue; show blockers; offer support bundle

**Screen 9: Channel output setup**
- Form fields (one per screen on mobile):
  1. Channel name (text input)
  2. Output format (dropdown: 720p30 / 1080i60 / 1080p30 / SD480i60)
  3. Headend profile (dropdown: 6 options)
  4. Destination address:port (text input)
  5. Constant muxrate (text input, optional)
  6. SDI device (dropdown: detected cards + None)
  7. Fill policy (radio: slate / loop / silence)
  8. Emergency slate asset (file picker or asset ID)
  9. CEA-708 caption passthrough (toggle; default off) — **folded in from S11** (D12); when on, Screen 10 verifies passthrough
  10. Watch-folder ingest path (text input, optional) — **folded in from S7** (D12); sets the channel's `watch_folder_configs` ingest path
- Buttons: Save and preview, Back
- Validation: destination reachable (TCP check), profile exists, format matches profile, **selected sink type is supported by the installed GStreamer engine/tier** (S15 — e.g. SDI requires the DeckLink/SDI tier); if a watch-folder path is set, it must exist and be readable

**Screen 10: Output proof — test pattern & capture**
- Live preview window (scaled to phone)
- Countdown timer (10 minutes)
- Test pattern picker (bars+tone / live / slate; default bars+tone)
- Status line: "Generating test bars on Channel Gov-Ch12 through the GStreamer engine → 192.168.1.100:5000..."
- Buttons: Start proof run, Stop, View raw TS
- Actions: Click Start → drive the GStreamer engine pipeline to the selected sink (S15) → TSDuck probe on UDP output
- Progress: live updates, show TSDuck verdict on completion
- CEA-708 verification: if CEA-708 passthrough was enabled on Screen 9 (S11, D12), run the decode-back check during the proof and surface the result in the proof verdict
- Fallback: allow local inspection if headend unreachable, mark as "partial"
- Phone-friendly: full-screen preview, large buttons

**Screen 11: Commissioning report**
- Green/red banner: "Ready for broadcast" vs "Commissioning incomplete"
- Checkbox list (collapsed): ✓ First-run checks, ✓ Channel setup, ✓ Output proof
- Blockers section (if any): "DeckLink driver missing", "TSDuck not installed", etc.
- Support bundle section: Download button
- Buttons: Schedule first broadcast, View detailed report, Export PDF, Done
- Phone-friendly: tap to expand sections, large download button

---

## 6. Behavior / algorithms

### Check implementations (Screen 8, 11 checks, fail-closed)

The engine checks verify what the **turnkey installer placed** for the active tier (S15 §8) — they do not ask the operator to supply anything.

1. `os_version` — Windows 10+ / Ubuntu 22.04+ / RHEL 8+
2. `disk_available_gb` — ≥100 GB free on media volume
3. `gstreamer_engine` — GStreamer runtime present + the tier plugin set loadable (base/good/bad/rs); bundled CPU encoder (`openh264enc`) on base tier, optional operator-provided `x264enc` only when explicitly installed; hardware encoder (`nvh264enc`/`vah264enc`) + GPU/OpenGL on SDI tier; `ndisink` + NDI SDK on NDI tier (S15 §4, §6)
4. `decklink_sdi` (SDI tier only) — Blackmagic Desktop Video ≥12.0 + BMD Desktop Video SDK + GStreamer `decklinkvideosink` element registered (S15 §4)
5. `tsduck` — ≥3.30
6. `db` — Postgres connectivity (SELECT 1)
7. `services` — civiccast-egress, civiccast-schedule running
8. `backup` — Local backup path configured + writable
9. `timezone` — System timezone not UTC
10. `release_integrity` — Release manifest signature valid
11. `caspar_cg` (premium-CG tier only) — CasparCG co-process installed and reachable via AMCP (optional tier; skipped otherwise) (S15 §5)

If any fail, disable Continue; show blockers; offer support bundle.

### Channel setup (Screen 9)

Validate:
- Headend profile exists in catalog
- Output format matches profile's canonical profile
- TCP port reachable (non-fatal warning)
- Selected sink type is realizable by the installed GStreamer engine/tier (S15)
- Create EgressSinkSpec for UDP-TS output (realized as the engine's `udpsink`+`mpegtsmux`, S15 §4)

### Output proof (Screen 10)

1. Drive the GStreamer engine to emit a test pattern (bars+tone / live / slate) to the selected sink for 10 minutes (S15)
2. Launch TSDuck probe in parallel (10-minute capture) on the engine's TS output
3. Aggregate results into CommissioningProofRun

### Commissioning report (Screen 11)

Aggregate all 4 steps; flag blockers; generate support bundle if present.

---

## 7. Proof tier: current rung + how to advance it

### Current: Contract

- Code exists; unit tests pass; API tests pass; no runtime egress

### Advance to Lab

24-hour soak: all 6 headend profiles validate, TSDuck loopback probes pass, test patterns run cleanly, 10 commissioning workflows complete, support bundle exports correctly.

### Advance to SDI-Proven

Real DeckLink card + the installer-provisioned GStreamer SDI stack (`decklinkvideosink` + BMD Desktop Video SDK, S15) — no BYO ffmpeg. Route test pattern to SDI through the engine; capture with oscilloscope; verify signal present.

### Advance to Headend-Proven

First-station beta: operator installs, runs commissioning, schedules broadcast, cable operator headend accepts stream.

---

## 8. Test plan

### Unit tests (60+ cases)

All checks isolated; models validate; validation logic correct.

### API tests (25+ cases × 3–5 assertions)

All endpoints return schema-valid responses; auth enforced; JSON output valid.

### E2E tests (Playwright)

All 4 screens + navigation + responsive layout (iOS Safari + Android Chrome).

### Soak gate

24-hour loop: 10 full commissioning workflows; each proof run 10 minutes; all TSDuck verdicts "pass"; no crashes.

### Hardware gate (SDI)

Real DeckLink card + oscilloscope verification.

### Audit expectation: 0/0/0/0/0

Zero bugs, UX gaps, docs gaps, test gaps, security issues.

---

## 9. DONE criteria

S3 is shipped when:

1. ✓ Commissioning models defined; persisted to station-state JSON (no DB migration)
2. ✓ First-run checks module complete
3. ✓ Channel setup validation live
4. ✓ Output proof CLI wired
5. ✓ Commissioning report builder done
6. ✓ API endpoints live (5 new)
7. ✓ Wizard screens 8–11 wired (phone-first)
8. ✓ All tests pass (unit/API/e2e/soak)
9. ✓ Proof artifacts exported
10. ✓ Docs updated

---

## 10. Dependencies & cross-refs; Open decisions

### Dependencies

- **S1:** Hardware probe. S3 calls `probe()` API; S1 must land first.
- **S2:** Headend profiles. S3 depends on all 6 profiles + lookup; S2 must be complete.
- **S15:** Playout engine. S3's turnkey install provisions the GStreamer engine + tier plugins/SDKs, and the output proof drives that engine; S15 defines the pipeline/sinks S3 installs and verifies.
- **S9:** Pipeline lifecycle & supervision. The output proof runs through the GStreamer engine pipeline (S15); S9 supervises engine/co-process lifecycle and clean restart on element failure.
- **S8:** Health alerting. If services fail mid-proof, S8 must alert.

### Cross-refs

- **S4:** After commissioning, operator schedules broadcast; S4 validates schedule.
- **S5:** Commissioning could offer optional dry-run live takeover (future).
- **S6:** Commissioning verifies emergency slate loaded; if CG bulletin, S6 must land first.
- **S7:** Watch-folder ingest path folds into Screen 9 (D12); writes the channel's `watch_folder_configs`.
- **S11:** CEA-708 caption passthrough folds into Screen 9 (toggle) and is verified in the Screen 10 proof when enabled (D12) — not a separate screen.
- **S12:** OTT app configuration folds into Screen 5 (publish-targets) as an added publish target (D12).
- **S13:** AI adaptive-default folds into Screen 6 (models) (D12); summary-model default keys off detected system RAM.

### Open decisions for Scott

1. **DeckLink hardware:** Which model? (Recommend: Mini Monitor ~$400.)
2. **Proof duration:** 10 min or offer 1-min quick check? (Recommend: both; default 1 min.)
3. **Support bundle format:** ZIP or tarball? (Recommend: ZIP.)
4. **Phone-first scope:** 4 screens or single accordion? (Recommend: keep 4 screens.)
5. **Headend defaults:** Generic or decision tree? (Recommend: generic + call-support link.)

---

*End of S3 spec. Implementation does not begin until Scott approves.*
