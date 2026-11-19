# S2 — Headend Handoff Matrix

**Formalize and test the cable-headend delivery profiles** atop the existing `egress/headend.py` + API surface. Establish per-vendor proof tiers (field/lab/contract/unsupported), SDI audio embedding, TSDuck compliance verification, per-sink loudness targets (streaming vs cable), and operator-vs-baked configuration boundaries.

> **Engine alignment (Scott-confirmed 2026-06-13):** every output in this section is now a **GStreamer sink** on the channel's persistent pipeline — UDP-TS (`udpsink`+`mpegtsmux`), SRT (`srtsink`), HLS (`hlssink`), NDI (`ndisink`, gst-plugins-rs / MPL), and SDI (`decklinkvideosink`, gst-plugins-bad / LGPL) — replacing the per-segment BYO-ffmpeg relay. All Apache-clean (LGPL/MPL); no GPLv3 in the output path. The headend **profiles, proof tiers, TSDuck compliance, and operator configuration described here live ABOVE the engine and are unchanged** — they now drive the GStreamer pipeline. See **S15 (Playout Engine — GStreamer)** for the engine itself.

---

## 1. Goal & PEG automation rationale

**What incumbent PEG platform does here:** the incumbent PEG platform ships named, vendor-specific delivery "templates" (CableLabs, Comcast MTD, TelVue, Harmonic, Leightronix) that encode the video/audio codec, bitrates, frame rate, and constant multiplex rate required by each headend. Operators select a template, fill in the headend IP/port or file path, and the system delivers a conformant MPEG-TS stream. The operator is never left guessing about what bitrate or frame size a headend expects.

**The gap we close:** CivicCast 3.0 implements **6 named headend profiles** (generic-udp-spts, comcast-mtd-sd, comcast-mtd-hd, telvue-hypercaster-ip, harmonic-spectrum-ts, leightronix-file-drop), each sourced to published vendor documentation. An API endpoint allows an operator to apply a profile to a channel, overriding the canonical profile and adding a validated headend sink. A second API surface probes the headend's reachability and runs TSDuck compliance verification (if the station has TSDuck installed) to catch stream misconfigurations before they reach the cableco.

**Parity mapping:** Master §2.1 §2.3: cable automation CA-6 (headend profiles) + CA-7 (compliance probe). Headend-proven rung (§5) is deferred to first-station beta; this section delivers lab proof (rung 1) + machine proof via unattended soak (rung 2).

---

## 2. Current state (file:line basis)

### Implemented (CA-6 headend profiles, CA-7 compliance)

- **`civiccast/egress/headend.py:43–251` — `HeadendProfile` model + 6 named profiles**: generic-udp-spts, comcast-mtd-sd/hd, telvue-hypercaster-ip, harmonic-spectrum-ts, leightronix-file-drop. All profiles carry `operator_must_supply` list (address/port/muxrate from carriage agreement) and `not_claimed` (field proof pending). Named constants `_FIELD_PROOF_BOUNDARY` + `_HEADEND_SINK_LABEL`.

- **`civiccast/egress/headend.py:258–304` — `apply_headend_profile()` function**: Takes an existing `EgressConfig`, a `HeadendProfile`, and operator-supplied destination URI + optional muxrate override. Validates destination against the profile's transport (udp-unicast/udp-multicast/file-drop). Returns a new config with the profile's `CanonicalProfile` and a validated `EgressSinkSpec` (kind=udp-ts or file).

- **`civiccast/egress/sinks.py:119–146` — `UdpTsSink` class**: Maps the headend profile's CBR-SPTS-over-UDP requirements onto the engine's `mpegtsmux`+`udpsink` (S15 §4) — constant mux-rate, 7 × 188-byte TS framing (1316-byte UDP payload), unicast vs multicast destination. Carries the profile's mux-rate through to the muxer/sink. (Legacy basis: the same args previously fed a BYO-ffmpeg `mpegts` muxer; under S15 they parameterize the persistent GStreamer pipeline's TS sink instead.)

- **`civiccast/egress/sinks.py:177–185` — `SdiSink` (legacy BYO-ffmpeg stub, superseded by S15)**: Under the GStreamer engine (S15), SDI is no longer a separate supervised BYO-ffmpeg relay — it is a **first-class engine sink** (`decklinkvideosink`, gst-plugins-bad / LGPL, fill+key via `keyer-mode`+`duplex-mode`) on the same persistent pipeline as every other output. The legacy `SdiSink`/`sdi_relay.py` stub stands only as the historical contract boundary; SDI egress is delivered by the engine, not by spawning a DeckLink ffmpeg relay. See S15 §4 (outputs/sinks) and §8 (SDI / broadcast tier).

- **`civiccast/egress/compliance.py` — TSDuck compliance probe**: `locate_tsduck()` (lines 139–162) finds the station's BYO TSDuck install via `CIVICCAST_TSDUCK_PATH` or PATH. `build_compliance_probe_args()` (lines 165–192) constructs a bounded `tsp` analyze run. `evaluate_tsduck_report()` (lines 195–295) grades the report against TR 101 290 priority-1 checks: CBR mux-rate, TS sync, continuity, PAT/PMT, PCR, single-program. `run_compliance_probe()` (lines 309–376) orchestrates the probe and persists the last result to `work_dir/<channel>/compliance-last.json`. `probe_device()` (lines 401–425) performs TCP reachability of headend appliance management ports (80, 443 by default).

- **`civiccast/egress/router.py:306–377` — `/api/staff/egress/headend-profiles` + apply endpoint**: `headend_profiles()` (lines 306–314) lists all profiles (static, works before DB is ready). `apply_headend_profile_to_channel()` (lines 317–377) is a POST endpoint that applies a profile to a channel's config. Validates the profile exists, destination satisfies transport rules, and persists the result. First-time setup creates a placeholder `EgressConfig` if needed (lines 342–357).

- **`civiccast/egress/router.py:427–519` — `/api/staff/egress/headend-readiness` + device/compliance probes**: `headend_readiness()` (lines 496–519) returns TSDuck install status + per-channel last probe results. `ndi_readiness()` + `sdi_readiness()` (lines 427–493) report engine sink readiness — under S15 these report GStreamer plugin/SDK presence (`ndisink` + NDI Runtime; `decklinkvideosink` + BMD Desktop Video SDK), not BYO-ffmpeg wiring. `run_channel_compliance_probe()` (lines 522–551) is a POST endpoint that runs a bounded probe (default 10 seconds) and returns the result. `run_headend_device_probe()` (lines 554–564) probes TCP reachability of a headend appliance.

- **`civiccast/egress/models.py:94–148` — `EgressSinkSpec` model**: Validates sink kind + URI matching (e.g., udp-ts requires `udp://` with explicit port). Each sink is realized by a **GStreamer sink element** on the channel's persistent pipeline (S15 §4): udp-ts → `udpsink`+`mpegtsmux`, SRT → `srtsink`, HLS → `hlssink`, NDI → `ndisink` (gst-plugins-rs / MPL), SDI → `decklinkvideosink` (gst-plugins-bad / LGPL). `extra_output_args` carries the cable-compliance mux parameters (constant mux-rate / CBR, TS packet size, PCR/buffering) that map onto the `mpegtsmux`+`udpsink` properties — the same TR 101 290-conformant TS the headend expects, now produced by the engine rather than a per-segment ffmpeg relay. Per D6, carries the **per-sink** `loudness_target_lufs` so a channel's cable sink (-24 LKFS ATSC A/85) and streaming sink (-16 LUFS) can differ (LKFS == LUFS per ITU-R BS.1770).

- **`civiccast/egress/models.py:286–330` — `EgressConfig` model**: Carries channel-wide encode/loudness tolerance settings; the loudness **target** is now per-sink on `EgressSinkSpec` (D6), with `loudness_tolerance_lufs` (default 2.0) remaining channel-level. Validation enforces sink label uniqueness (lines 325–330).

### Lab-proven (in-tree, 24h soak)

- 6 profiles + apply API: **lab** (machine-verified; none field-proven per master §3 line 118).
- UDP/SPTS CBR sink: **lab** (machine-verified; production-wired per master §3 line 119).
- TSDuck compliance: **lab** (0% drift @ 8 Mbps, production-wired; not field-proven per master §3 line 120).

### Contract-only (stub boundary documented)

- SDI engine sink (`decklinkvideosink`, S15 §4) is **rung-0 contract** until a DeckLink card + BMD SDK are on a bench (see § 7); the legacy `SdiSink`/`sdi_relay.py` BYO-ffmpeg stub is superseded by the engine sink and remains only as the historical boundary.

### Net-new (S2 scope)

1. **Per-sink loudness target specification (D6)**: The loudness **target** lives on `EgressSinkSpec` so each sink carries its own target — a cable udp-ts sink at -24 LKFS (ATSC A/85 for cable receivers) and a streaming sink at -16 LUFS (LKFS == LUFS per ITU-R BS.1770). The field migrates off `EgressConfig` onto `EgressSinkSpec`; the per-sink target is selected at egress time by the same `loudnorm` code path (see Behavior § 6). Owned for egress-time selection by S2/S11; the migration adding the column lands in S11's `0049_per_sink_loudness`.

2. **Video profile matrix — interlaced cable profiles (D13, NOT deferred)**: Master scope specifies "default video profiles 1080i59.94/720p59.94/480i29.97." Currently the 6 profiles are hard-coded in `headend.py` with fixed fps=30 + resolution. Per D13 the interlaced cable profiles **1080i59.94** and **480i29.97** plus **720p59.94** are cable-parity and **ship in S2/S3** as selectable `CanonicalProfile` presets — they are not later polish (see Behavior § 6.5).

3. **Readiness & discovery API wiring**: `headend-readiness`, `ndi-readiness`, `sdi-readiness` endpoints exist but are incomplete (e.g., no discovery of devices on the local network, no "next steps" copy for device probe failures). Partially wired in `router.py:427–519`.

---

## 3. Entities / data model & migrations

### Reused from Master §6

- `EgressConfig` (channel-level settings; carries `loudness_tolerance_lufs`, encode defaults — the loudness target is per-sink per D6).
- `EgressSinkSpec` (per-sink output target; uri, kind, extra_output_args, label, **`loudness_target_lufs`** per D6).
- `HeadendProfile` (named vendor profile from `headend.py`).
- `CanonicalProfile` (codec, resolution, bitrate, audio sample rate).
- `ComplianceProbeResult` (TSDuck result: verdict, checks, raw report path).
- `HeadendReadinessRollup` (readiness summary; from compliance.py:96–105).
- `SdiReadiness` / `SdiSinkStatus` (SDI **engine sink** readiness — `decklinkvideosink` + BMD SDK presence per S15 §4; supersedes the legacy `SdiRelayStatus`/BYO-ffmpeg shape).
- `NdiReadiness` / `NdiSinkStatus` (NDI **engine sink** readiness — `ndisink` + NDI Runtime presence per S15 §4; supersedes the legacy BYO-patched-ffmpeg shape).
- `TsduckStatus` (locate result; from compliance.py:57–65).

### Net-new for S2

**Per-sink `loudness_target_lufs` on `EgressSinkSpec` (D6 — binding)**

The loudness **target** is per-sink, not channel-level: the whole point is that a channel's cable sink (-24 LKFS ATSC A/85) and its streaming sink (-16 LUFS) differ, so the target rides on each `EgressSinkSpec` (LKFS == LUFS per ITU-R BS.1770). At egress time the per-sink target feeds the same `loudnorm` code path; ownership is **S7** = ingest-time loudness gate/badge, **S2/S11** = egress-time per-sink target selection.

**Pydantic field (`EgressSinkSpec`):**

`python
loudness_target_lufs: float = -16.0  # per-sink; cable headend sinks set -24.0 (ATSC A/85). LKFS == LUFS.
`

When a headend profile is applied, `apply_headend_profile()` sets the new udp-ts sink's `loudness_target_lufs` to -24.0; streaming/file sinks keep -16.0.

**DB migration:** the column is added by **S11's `0044_loudness_and_eas`** (per the reconciliation migration table) — `loudness_target_lufs` on `EgressSinkSpec`. S2 adds no migration of its own for loudness.

---

## 4. API surface

### Public API
None (all headend configuration is operator-only).

### Staff API (require_any_role("setup_admin") unless noted; read-only diagnostic surfaces use `support_admin` per D1)

#### 4.1 List headend profiles (read-only)
`GET /api/staff/egress/headend-profiles`
→ `list[HeadendProfile]`

Static product surface; works before DB is ready. Returns all 6 profiles with vendor, canonical profile, transport, operator_must_supply, and not_claimed lists. **Auth:** support_admin (read-only diagnostic surface, D1).

#### 4.2 Apply a headend profile to a channel
`POST /api/staff/egress/channels/{channel_id}/config/headend-profile`
`
payload: HeadendProfileApplyRequest {
  profile_id: str (e.g. "generic-udp-spts")
  destination_uri: str (e.g. "udp://192.0.2.1:9000")
  muxrate_kbps: int | null (override profile default)
  keep_existing_sinks: bool (default false; first-time setup always replaces)
}
→ EgressConfig (updated with profile + sink)
`
Validates destination against profile's transport. First-time setup creates a placeholder config if needed. Returns the persisted config. **Auth:** setup_admin.

#### 4.3 Get headend readiness (TSDuck + last probe results)
`GET /api/staff/egress/headend-readiness`
→ `HeadendReadinessResponse {`
  `tsduck: TsduckStatus (installed, path, version, install_hint)`
  `channels: list[HeadendChannelReadiness] {`
    `channel_id: str`
    `destination: str (udp sink uri)`
    `last_probe: ComplianceProbeResult | null`
  `}`
`}`

**Auth:** support_admin (read-only diagnostic surface, D1). Returns installation status and per-channel last probe results (persisted to `work_dir/<channel>/compliance-last.json`).

#### 4.4 Run a compliance probe on a channel
`POST /api/staff/egress/channels/{channel_id}/compliance-probe`
`
payload: ComplianceProbeRequest {
  seconds: int (default 10, bounded 1–600)
}
→ ComplianceProbeResult {
  channel_id, destination, probed_at, expected_muxrate_kbps, tsduck_version
  checks: list[ComplianceCheck] {
    check: str (cbr-mux-rate, ts-sync, continuity, pat-pmt, pcr-present, single-program)
    status: "pass" | "fail"
    detail: str
  }
  verdict: "pass" | "fail" | "not-run"
  detail: str
  raw_report_path: str | null
  not_claimed: list[str] (TR 101 290 priority-1 subset only, not field-proven)
}
`
Runs a bounded `tsp analyze` capture (if TSDuck is installed) and persists the result. Honest "not-run" if TSDuck is absent. **Auth:** setup_admin.

#### 4.5 Probe headend appliance reachability
`POST /api/staff/egress/headend-device-probe`
`
payload: DeviceProbeRequest {
  host: str (IP or FQDN of headend appliance)
  ports: list[int] (default [80, 443])
}
→ DeviceProbeResult {
  host: str
  ports: list[DevicePortProbe] {
    port: int
    reachable: bool
  }
  any_reachable: bool
  probed_at: datetime
}
`
TCP reachability check (vendor-agnostic; applies to TelVue HyperCaster, Harmonic Spectrum, Leightronix, etc.). **Auth:** setup_admin.

#### 4.6 Get SDI readiness
`GET /api/staff/egress/sdi-readiness`
→ `SdiReadinessResponse {`
  `decklink_sink_available: bool (gst-plugins-bad decklinkvideosink loadable)`
  `bmd_sdk_present: bool (Blackmagic Desktop Video SDK at runtime)`
  `next_step: str (install/config hint)`
  `sinks: list[SdiSinkStatus]`
`}`

Reports SDI **engine sink** readiness (S15 §4): whether the `decklinkvideosink` element is loadable and the BMD Desktop Video SDK/driver is present, plus per-channel SDI sink status. Not BYO-ffmpeg wiring. **Auth:** support_admin (read-only diagnostic surface, D1).

#### 4.7 Get NDI readiness
`GET /api/staff/egress/ndi-readiness`
→ `NdiReadinessResponse {`
  `ndi_sink_available: bool (gst-plugins-rs ndisink loadable)`
  `ndi_runtime_present: bool (user-installed NDI Runtime at runtime)`
  `next_step: str (install/config hint)`
  `sinks: list[NdiSinkStatus]`
`}`

Reports NDI **engine sink** readiness (S15 §4): whether the `ndisink` element (gst-plugins-rs / MPL) is loadable and the free user-installed NDI Runtime is present. NDI is first-class via `ndisink` — not BYO-patched-ffmpeg. **Auth:** support_admin (read-only diagnostic surface, D1).


---

## 5. Operator UI surface

### ChannelOpsScreen enhancements (existing, S2 adds headend setup tab)

**New "Headend" tab on the channel detail screen:**

1. **Headend Profile Selection** (phone-first card):
   - Dropdown: list headend profiles (read via GET /api/staff/egress/headend-profiles).
   - For each profile, display vendor + operator_must_supply items (e.g., "Destination address and UDP port").
   - Selected profile shows its canonical profile (1920×1080@30, H.264 10 Mbps, AC-3 192 kbps, 48 kHz).

2. **Operator Input Fields**:
   - Destination URI (text input, e.g., "udp://192.0.2.1:9000").
   - Constant muxrate override (optional text input, e.g., "8000 kbps").
   - Checkbox: "Keep existing sinks" (for multi-sink stations; disabled on first setup).

3. **Apply Button**:
   - POST to `/api/staff/egress/channels/{channel_id}/config/headend-profile`.
   - On success: show "Profile applied. Canonical profile updated to..." + sink destination.
   - On error (validation, unknown profile): show error detail (e.g., "Destination must be multicast for Comcast MTD").

4. **Headend Readiness Card**:
   - Shows TSDuck status (installed Y/N, version, or install hint).
   - Per-channel last probe result (if any): verdict + timestamp.
   - "Run Compliance Probe" button (POST to `/api/staff/egress/channels/{channel_id}/compliance-probe`, 10s default).
     - On probe complete: display verdict (pass/fail/not-run) + checks list (cbr-mux-rate, ts-sync, continuity, etc.).
     - Show drift % if CBR check failed.
     - Offer download link for raw TSDuck JSON report if available.

5. **Headend Device Probe Card** (collapsible):
   - Text input: headend appliance IP/FQDN.
   - Port list (default 80, 443; editable).
   - "Probe Reachability" button: POST to `/api/staff/egress/headend-device-probe`.
   - Show results (port 80 reachable Y/N, port 443 reachable Y/N).

6. **SDI Output Status** (if applicable):
   - Display SDI engine-sink readiness (from `sdi_readiness`): `decklinkvideosink` loadable Y/N + BMD Desktop Video SDK present Y/N (S15 §4).
   - "Next steps" copy if not configured (install BMD Desktop Video SDK / select DeckLink device).

7. **Loudness for Cable** (info card):
   - Display: "Cable sinks use a -24 LKFS target (ATSC A/85). Streaming sinks use -16 LUFS."
   - Each sink carries its own `loudness_target_lufs` (per-sink, D6); the card shows the target in effect for each configured sink (e.g., the cable udp-ts sink at -24, a streaming sink at -16).

### Installation / Commissioning Wizard (S3, but S2 provides the config endpoints)

S2 does **not** modify the wizard here; that is S3 scope. S2 provides the underlying API for headend setup.

---

## 6. Behavior / algorithms

### 6.1 Headend Profile Selection & Application

1. Operator selects a profile from the list (GET `/api/staff/egress/headend-profiles`).
2. Operator supplies destination URI (e.g., `udp://192.0.2.1:9000`).
3. POST to `/api/staff/egress/channels/{channel_id}/config/headend-profile` with profile_id + destination.
4. Server-side:
   - `apply_headend_profile()` (headend.py:258) validates destination against profile.transport.
     - If transport is "udp-multicast", parse host and verify it's in 224.0.0.0–239.255.255.255.
     - If transport is "udp-unicast", accept any IP.
     - If transport is "file-drop", verify path is a filesystem location (not URL).
   - Build an `EgressSinkSpec` with kind=udp-ts (or file), label="Cable headend", uri=destination, extra_output_args=["-muxrate", "8000k", ...].
   - Copy profile.canonical_profile to config.canonical_profile (overwriting existing encode settings).
   - Persist the config via `store.upsert_config()`.

### 6.2 Per-sink Loudness Target (streaming vs cable, D6)

**Current state:** `loudness_target_lufs` defaults to -16.0 LUFS (streaming standard, used in `preparer.py:129–146`). Per D6 the target lives on `EgressSinkSpec`, not `EgressConfig`, so each sink carries its own target (LKFS == LUFS per ITU-R BS.1770).

**Behavior (S2 + S11 coordination, same `loudnorm` code path):**

- Each sink declares its own `loudness_target_lufs`. A streaming/file sink keeps -16.0; a cable udp-ts headend sink uses -24.0 (ATSC A/85 for cable receivers).
- `apply_headend_profile()` sets -24.0 on the udp-ts sink it creates; other sinks are untouched.
- A channel can therefore feed cable at -24 LKFS and a streaming backup at -16 LUFS **simultaneously**, each normalized to its own target — the reason the field is per-sink.
- `SourcePreparer.prepare()` (preparer.py:59) selects the loudness target **per sink** rather than once per channel.

**Implementation (preparer.py):**

`python
def prepare(self, source_plan: EgressSourcePlan, config: EgressConfig) -> SourcePreparationReport:
    # Per-sink loudness target (D6): each sink normalizes to its own target.
    for sink in config.sinks:
        loudness_target = sink.loudness_target_lufs  # -24.0 cable, -16.0 streaming
        # ... (preparation logic applies loudness_target on this sink's loudnorm pass)
`

### 6.3 Compliance Probe Workflow

1. Operator presses "Run Compliance Probe" in the UI.
2. POST to `/api/staff/egress/channels/{channel_id}/compliance-probe` with seconds=10.
3. Server-side `run_compliance_probe()` (compliance.py:309):
   - Locate TSDuck via `locate_tsduck()`.
   - If not installed, return `ComplianceProbeResult` with verdict="not-run" + install_hint.
   - If installed, build `tsp analyze` args for the channel's udp-ts sink destination.
   - Run bounded capture (default 10 seconds; user can override up to 600s).
   - Parse the resulting JSON report.
   - `evaluate_tsduck_report()` (compliance.py:195) checks:
     - **cbr-mux-rate**: measured bitrate vs expected (within 5% tolerance by default).
     - **ts-sync**: invalid-syncs + transport-errors = 0.
     - **continuity**: no discontinuities across PIDs.
     - **pat-pmt**: both present.
     - **pcr-present**: at least one PID carries PCR.
     - **single-program**: exactly 1 service (SPTS requirement).
   - Verdict = "pass" if all checks pass; "fail" if any fail.
   - Persist to `work_dir/<channel_id>/compliance-last.json`.
4. Return the result to the operator.

**Honesty boundary:** The checks are TR 101 290 priority-1 subset only. Not a full monitoring suite; not field-proven against a real headend (rung 4 deferred to first-station beta).

### 6.4 Device Reachability Probe

1. Operator enters headend appliance IP/FQDN + optional port list (default 80, 443).
2. POST to `/api/staff/egress/headend-device-probe`.
3. Server-side `probe_device()` (compliance.py:401):
   - For each port, attempt TCP connect with 3s timeout.
   - Return result (port, reachable Y/N) for each port + summary (any_reachable Y/N).
4. Use case: verify headend is on the network before feeding it a stream.

### 6.5 Video Profile Matrix — interlaced cable profiles are cable-parity (D13, NOT deferred)

Master scope specifies "default video profiles 1080i59.94/720p59.94/480i29.97." Per D13 these interlaced cable profiles (1080i59.94 and 480i29.97) plus 720p59.94 are **cable-parity, not later polish** — they ship in S2/S3, not deferred. They join the matrix alongside the existing per-vendor profile resolutions:

| Profile | Resolution / scan / rate | Notes |
|---|---|---|
| **1080i59.94** | 1920×1080 interlaced, 59.94 fields/s | cable-parity HD interlaced (D13) |
| **720p59.94** | 1280×720 progressive, 59.94 fps | cable-parity HD progressive (D13) |
| **480i29.97** | 720×480 interlaced, 29.97 frames/s | cable-parity SD interlaced (D13) |
| generic-udp-spts | 1280×720 | vendor-doc default |
| comcast-mtd-sd | 720×480 | vendor-doc default |
| comcast-mtd-hd | 1920×1080 | vendor-doc default |
| telvue-hypercaster-ip | 1280×720 | vendor-doc default |
| harmonic-spectrum-ts | 1920×1080 | vendor-doc default |
| leightronix-file-drop | 1280×720 | vendor-doc default |

**Build (not defer):** add the three cable-parity profiles (1080i59.94, 720p59.94, 480i29.97) as selectable `CanonicalProfile` presets in `headend.py`, with proper field-rate / interlacing flags wired through the encode args. Each vendor profile maps its default to one of these presets; operators may also select a preset explicitly at profile-apply time. S2 delivers the presets + the mechanism; S3's commissioning wizard surfaces the selection — neither defers the interlaced profiles.


---

## 7. Proof tier: current rung + how to advance it

### Current proof tier (per master §3 line 118–120)

| Capability | Current rung | Path to next |
|---|---|---|
| 6 headend profiles + apply API | **Lab (1)** — machine-verified in soak | → Machine-proven (2) on clean-install 24h soak |
| UDP/SPTS CBR sink | **Lab (1)** — production-wired | → Machine-proven (2) via unattended soak restart reap |
| TSDuck compliance | **Lab (1)** — 0% drift @ 8 Mbps, no field error yet | → Machine-proven (2) via continuous monitoring |
| SDI engine sink (`decklinkvideosink`, S15) | **Contract (0)** — engine sink defined; supersedes the legacy `SdiSink`/`sdi_relay.py` stub | → SDI-proven (3) once DeckLink card + BMD Desktop Video SDK are on a bench and the GStreamer SDI sink is verified |

### How to advance S2 to rung 2 (machine-proven)

1. **24-hour unattended soak** (in flight per master §10 step 0):
   - Three-channel automation with headend profiles applied (generic-udp-spts on each).
   - Verify no codec/rate drift, no encoder crash, no sink disconnection.
   - Kill + restart the engine pipeline; verify clean recovery (S9 scope, but proves UDP sink resilience).
   - Midnight crossover to prove no discontinuity.
   - **Acceptance:** soak completes with 0% drift, 0 reap failures, 0 encoder crashes.

2. **Compliance probe continuous verification**:
   - During soak, run periodic compliance probes (e.g., every 2 hours).
   - Collect all verdicts (pass/fail/not-run).
   - **Acceptance:** all probes pass verdict=pass (or not-run if TSDuck not available on test rig).

3. **Reboot + recovery**:
   - Kill soak at an arbitrary point; hard-reboot the machine.
   - Automation respins; the engine pipeline recovers cleanly; soak resumes.
   - **Acceptance:** no stale processes, no manual intervention needed.

4. **Schema drift monitoring**:
   - Periodically read the stored `EgressConfig` and its `EgressSinkSpec`s for a channel.
   - Verify each sink's per-sink `loudness_target_lufs` (D6) is persisted + loaded correctly (cable udp-ts sink at -24.0, streaming sink at -16.0).
   - **Acceptance:** no silent defaults; all fields round-trip.

### How to advance S2 to rung 3 (SDI-proven)

Requires procurement of a DeckLink card + the Blackmagic Desktop Video SDK/driver (master §13 open decision). SDI is delivered by the engine's `decklinkvideosink` (S15 §4 / §8), not a BYO-ffmpeg relay. Once available:

1. Install the BMD Desktop Video SDK/driver and confirm `decklinkvideosink` loads (`gst-inspect-1.0 decklinkvideosink`); `sdi-readiness` reports `decklink_sink_available` + `bmd_sdk_present` true.
2. Select the DeckLink device on the channel's SDI sink (device name, e.g., "DeckLink Duo 2"); set fill+key via `keyer-mode`/`duplex-mode` where keying is required (per S15 §4).
3. Apply a headend profile to the same channel so the persistent pipeline drives both the udp-ts and the SDI sink off one engine.
4. Run automation on a 4-hour soak with live SDI capture.
5. Verify SDI output on a monitor (1080i59.94 or 720p59.94 video with embedded 48 kHz PCM audio).
6. **Acceptance:** no artifacts, audio in sync, no pipeline/element faults (engine sink, not a relay process).

### Headend last-mile options — ranked by directness (not Blackmagic-locked)

A cable headend ingests **SDI or an IP transport stream — never NDI** (NDI is a LAN production
format). LPM confirms this: they feed **SDI into Comcast's headend box** today, and that box may
also accept IP. Headend paths, in priority order:

1. **IP transport stream direct (UDP-SPTS / SRT)** — *already in code*, **$0 hardware** where the
   headend accepts IP (many modern headends do — confirm the specific headend's accepted inputs).
   Cheapest, most-direct path.
2. **SDI-direct via Blackmagic DeckLink** (GStreamer `decklinkvideosink`, S15 §4 — supersedes the
   legacy `sdi_relay.py` BYO-ffmpeg path) — for headends that require baseband SDI (LPM/Comcast
   today). SDI is now a native engine sink: GStreamer's `decklinkvideosink` (gst-plugins-bad,
   **LGPL** — no GPLv3, no BYO-ffmpeg, fill+key via `keyer-mode`+`duplex-mode`) drives the
   Blackmagic device directly off the persistent pipeline. DeckLink remains the supported pro-SDI
   target (GStreamer's only native pro-SDI sink is the DeckLink element, mirroring the old ffmpeg
   `decklink`-only constraint — no AJA/Magewell/Bluefish/DELTACAST GStreamer output element). **3 PEG
   channels = 3 SDI outs** → multi-output card; reference hardware is the **DeckLink Duo 2** (4×
   independent 3G-SDI, ~$579, PCIe desktop — Thunderbolt can't practically drive 3 independent SDI
   outs; avoid input-only "Recorder" models; native OS required, PCIe can't passthrough WSL2 per S15
   §8). DeckLink is the dominant affordable SDI-I/O standard for PEG/playout; **native AJA** is a
   reasonable *later* premium-tier add (broadcast-grade, not PEG-dominant); Bluefish/DELTACAST are
   niche and not warranted.
3. **NDI → SDI hardware converter** (vendor-neutral fallback). Feed CivicCast's NDI output to a
   third-party box (Magewell Pro Convert NDI→SDI ~$489; Kiloview D350 ~$679) → SDI from any vendor.
   One SDI out per box, so 3 channels ≈ $1,467 (more than the Duo 2) plus an extra hop. Only the
   right choice for a shop that is already NDI-centric.

**NDI is a first-class output path — IN SCOPE for this work, not deferred.** It is **incumbent PEG platform
parity**: the incumbent PEG platform lists native NDI in/out as a feature, so CivicCast matches it. NDI's primary
value is the LAN production chain (vMix, OBS — both in use at LPM); it reaches a headend only via a
converter (path 3 above), so it is *not* the cheapest cable route, but it **is** one of the three
first-class output paths CivicCast ships. Under the GStreamer engine (S15 §4) NDI output is the
native **`ndisink`** element from **gst-plugins-rs (MPL-2.0, Apache-clean)**, which links the
**MIT-licensed NDI headers** and **loads the user-installed, free NDI Runtime** at runtime (never
bundle the proprietary runtime; carry the "NDI®" attribution). This **supersedes** both the old
"BYO patched ffmpeg with NDI re-added" approach (mainline ffmpeg removed NDI in 2019;
non-distributable, a maintenance trap) and the previously-proposed standalone OBS/DistroAV-style
sender — `ndisink` gives the same licensing posture as a first-class element on the same persistent
pipeline as every other output. The user installs the free NDI Tools/Runtime once — exactly what an
OBS user already does. **Three first-class output paths ship together as engine sinks: IP-TS/SRT,
SDI (`decklinkvideosink`), and NDI (`ndisink`). None deferred.**

### Claim boundary preserved

- No claim of "field-proven" (rung 5) until first-station beta handoff.
- No claim of "EAS-compliant" (that's S11 + FCC boundary).
- No claim of "headend-proven" (rung 4) until live headend acceptance.

---

## 8. Test plan (unit/API/e2e + 0/0/0/0/0 audit)

### 8.1 Unit tests (contract tier 0)

**File: `tests/egress/test_headend.py`**

- **HeadendProfile model validation:**
  - `test_all_profiles_have_valid_canonical_profile()`: each profile's canonical profile is a valid CanonicalProfile.
  - `test_all_profiles_have_source_urls()`: non-empty source_urls list.
  - `test_profile_operator_must_supply_is_non_empty()`: each has ≥1 operator_must_supply item.
  - `test_profile_not_claimed_includes_field_proof_boundary()`: not_claimed always includes the boundary disclaimer.

- **apply_headend_profile() validation:**
  - `test_apply_validates_destination_against_transport()`: udp-unicast rejects multicast addr; udp-multicast requires 224.x.x.x; file-drop requires filesystem path.
  - `test_apply_with_muxrate_override()`: muxrate override is passed to sink extra_output_args.
  - `test_apply_preserves_other_sinks_when_keep_existing_true()`: multiple sinks on a channel.
  - `test_apply_replaces_all_sinks_on_first_time_setup()`: fresh config gets exactly 1 headend sink.
  - `test_apply_canonical_profile_is_copied()`: codec, bitrate, audio sample rate match the profile.

- **UdpTsSink output args:**
  - `test_udp_sink_appends_pkt_size_1316()`: default packet size is set.
  - `test_udp_sink_detects_multicast()`: is_multicast() returns True for 224.x.x.x.
  - `test_udp_sink_includes_muxrate_in_args()`: extra_output_args with -muxrate are preserved.

**File: `tests/egress/test_compliance.py`**

- **TSDuck location:**
  - `test_locate_tsduck_finds_env_path()`: CIVICCAST_TSDUCK_PATH env var is honored.
  - `test_locate_tsduck_falls_back_to_path()`: PATH lookup works if env var not set.
  - `test_locate_tsduck_returns_not_installed_if_absent()`: gracefully handles missing tsp.

- **Compliance probe args:**
  - `test_build_compliance_probe_args_for_unicast()`: builds tsp args for udp://host:port.
  - `test_build_compliance_probe_args_for_multicast()`: builds tsp args for multicast address.

- **TSDuck report evaluation (pure functions):**
  - `test_evaluate_report_passes_on_clean_stream()`: loads fixture `tests/egress/fixtures/tsduck-analyze-clean-cbr.json`, verdict="pass".
  - `test_evaluate_report_fails_on_cbr_drift_exceeds_tolerance()`: drifted bitrate returns verdict="fail" for cbr-mux-rate.
  - `test_evaluate_report_flags_ts_sync_errors()`: report with invalid-syncs > 0 fails ts-sync check.
  - `test_evaluate_report_flags_discontinuities()`: discontinuity count reflected in continuity check.
  - `test_evaluate_report_fails_if_pat_or_pmt_missing()`: pat-pmt check fails if either missing.
  - `test_evaluate_report_fails_if_not_single_program()`: service_total != 1 fails single-program.

- **Device probe:**
  - `test_probe_device_tcp_reachable()`: mocked connector returns reachable=True.
  - `test_probe_device_tcp_unreachable()`: unreachable returns reachable=False.
  - `test_probe_device_any_reachable_flag()`: any_reachable is True if ≥1 port reachable.

### 8.2 API tests (contract tier 0)

**File: `tests/egress/test_router_headend.py`**

- **GET /api/staff/egress/headend-profiles:**
  - `test_list_headend_profiles_returns_all_six()`: list contains generic-udp-spts, comcast-mtd-sd/hd, telvue, harmonic, leightronix.
  - `test_list_headend_profiles_before_db_ready()`: no 503 error; static product.

- **POST /api/staff/egress/channels/{channel_id}/config/headend-profile:**
  - `test_apply_profile_first_time_setup()`: fresh channel, no prior config; result has exactly 1 udp-ts sink.
  - `test_apply_profile_unknown_profile()`: returns 404 if profile_id not found.
  - `test_apply_profile_invalid_destination()`: returns 422 if destination doesn't match transport (e.g., multicast IP for udp-unicast profile).
  - `test_apply_profile_with_muxrate_override()`: muxrate_kbps in request overrides profile default.
  - `test_apply_profile_requires_setup_admin()`: anonymous returns 403.
  - `test_apply_profile_persists_to_store()`: config is readable from GET /api/staff/egress/channels/{channel_id}/config afterward.

- **GET /api/staff/egress/headend-readiness:**
  - `test_headend_readiness_includes_tsduck_status()`: response has tsduck field with installed Y/N.
  - `test_headend_readiness_lists_all_channels_with_udp_sinks()`: response includes per-channel destination + last_probe.
  - `test_headend_readiness_last_probe_is_null_if_never_run()`: new channel has last_probe=null.
  - `test_headend_readiness_requires_support_admin()`: read-only diagnostic surface requires `support_admin` (D1); anonymous returns 403.

- **POST /api/staff/egress/channels/{channel_id}/compliance-probe:**
  - `test_compliance_probe_not_run_if_tsduck_absent()`: verdict="not-run", detail contains install_hint.
  - `test_compliance_probe_run_with_tsduck_mocked()`: mocked tsp + report; verdict parsed correctly.
  - `test_compliance_probe_timeout_if_no_packets()`: tsp runs but gets no packets (firewall, bad address); verdict="fail", detail mentions firewall.
  - `test_compliance_probe_persists_last_result()`: result is written to `work_dir/<channel_id>/compliance-last.json`.
  - `test_compliance_probe_requires_setup_admin()`: anonymous returns 403.
  - `test_compliance_probe_seconds_bounded()`: seconds query param is clamped 1–600.

- **POST /api/staff/egress/headend-device-probe:**
  - `test_device_probe_tcp_reachable()`: mocked connector; reachable=True.
  - `test_device_probe_tcp_unreachable()`: unreachable=False.
  - `test_device_probe_default_ports()`: 80, 443 probed if ports not specified.
  - `test_device_probe_custom_ports()`: custom ports list is honored.
  - `test_device_probe_requires_setup_admin()`: anonymous returns 403.

### 8.3 E2E tests (lab proof, soak gate)

**File: `tests/egress/e2e/test_headend_e2e.py`**

- **Three-channel soak with headend profiles:**
  - Fixture sets up 3 channels, each with generic-udp-spts profile applied.
  - Local UDP loopback sinks for each channel (no real headend needed for E2E).
  - Automation runs; compliance probe runs every 2 hours (mocked tsp with clean fixture report).
  - Verify: 0% drift, 0 encoder crashes, 0 sink disconnections over 24 hours.
  - Perform midnight crossover; verify continuity markers.
  - Kill the engine pipeline; verify clean recovery (S9 scope, but proves sink resilience).
  - **Acceptance:** soak completes with no manual intervention.

- **Compliance probe lifecycle:**
  - Create a channel with headend profile.
  - Probe before stream is running: verdict="fail" (no packets).
  - Start automation; probe again: verdict="pass" (all checks pass).
  - Probe with custom 20-second capture: verify result persists to work_dir.
  - Stop automation; probe: verdict="fail" (no packets again).

- **Device probe:**
  - Probe local machine (127.0.0.1:80): reachable=False (no service running on 80).
  - Start a dummy HTTP server on port 8888; probe 127.0.0.1:8888: reachable=True.

### 8.4 Soak gate (machine proof)

**Pre-release checklist (master §12):**

- [ ] 24-hour unattended three-channel soak completes with 0% CBR drift on all channels.
- [ ] Compliance probes run every 2 hours; all verdicts are "pass" (or "not-run" if TSDuck unavailable).
- [ ] Hard reboot during soak; automation recovers without manual intervention; soak resumes.
- [ ] Schema drift check: each `EgressSinkSpec.loudness_target_lufs` (per-sink, D6) persists and loads correctly (cable sink -24.0, streaming sink -16.0).
- [ ] TSDuck locate gracefully handles missing tsp; endpoints return not-run verdict with install hint.
- [ ] Device probe works for both reachable and unreachable ports.

### 8.5 Audit expectation: 0/0/0/0/0

Per master §12, every audit must reach **0 findings across all 5 categories**:

1. **Correctness bugs:** No silent failures (e.g., profile applies but encoding is wrong).
2. **Reuse/simplification:** No duplicated profile definitions; no hardcoded vendor strings (all sourced to constants or docs).
3. **UX:** Operator errors (bad destination) surface immediately with clear messages.
4. **Docs:** All profiles cite source URLs; all not_claimed items are honest.
5. **Tests:** All endpoints have API tests; all sinks have unit tests; happy path + error paths covered.


---

## 9. DONE criteria (what "shipped" means for S2)

**Shipped S2 when:**

1. ✅ All 6 headend profiles are defined in `civiccast/egress/headend.py` with vendor documentation sourced to URLs.
2. ✅ `apply_headend_profile()` function validates destination + applies profile to a channel config (persisted).
3. ✅ API endpoints wired:
   - GET `/api/staff/egress/headend-profiles` (list).
   - POST `/api/staff/egress/channels/{channel_id}/config/headend-profile` (apply).
   - GET `/api/staff/egress/headend-readiness` (TSDuck + last probe).
   - POST `/api/staff/egress/channels/{channel_id}/compliance-probe` (run probe).
   - POST `/api/staff/egress/headend-device-probe` (device reachability).
4. ✅ `EgressSinkSpec` carries per-sink `loudness_target_lufs` (D6); `apply_headend_profile()` sets -24.0 on the udp-ts cable sink while streaming/file sinks keep -16.0.
5. ✅ `SourcePreparer` selects the loudness target **per sink** (each sink normalized to its own target).
6. ✅ TSDuck compliance probe (locate, build args, evaluate report, persist) fully implemented.
7. ✅ SDI documented as a first-class engine sink (`decklinkvideosink`, S15 §4) at rung-0 contract until a DeckLink card + BMD SDK are on a bench; the legacy `SdiSink`/`sdi_relay.py` BYO-ffmpeg stub is recorded as superseded.
8. ✅ Operator UI (ChannelOpsScreen):
   - Profile selection dropdown + apply flow.
   - Headend readiness card + probe button + last result display.
   - Device probe card + reachability results.
   - Loudness target info for cable sinks.
9. ✅ DB migration for per-sink `loudness_target_lufs` on `EgressSinkSpec` deployed (lands in S11's `0044_loudness_and_eas`; S2 consumes it).
10. ✅ All unit tests (headend.py, compliance.py, sinks.py models) pass.
11. ✅ All API tests (router endpoints) pass.
12. ✅ E2E soak test (3 channels, 24h, 0% drift, compliance probes every 2h) passes.
13. ✅ Hard reboot recovery during soak verified.
14. ✅ Code audit reaches 0/0/0/0/0 (no correctness, reuse, UX, docs, test gaps).
15. ✅ Documentation:
    - Spec section (this file) is complete.
    - Operator runbook covers "Apply Headend Profile," "Run Compliance Probe," "Interpret Results."
    - Admin guide documents TSDuck installation + CIVICCAST_TSDUCK_PATH env var.
    - Per-vendor notes link to PEG automation coverage target (master §2.1).

---

## 10. Dependencies & cross-refs; Open decisions for Scott

### Dependencies

- **S1 (Reference Station):** StationBoxProfile hardware detection (RAM → Gemma model selection). S2 assumes default station is available; no hardware-specific profile variants yet.
- **S3 (Commissioning Wizard):** Headend setup steps in the 11-screen wizard (currently stub in installer).
- **S8 (Operational Alerting):** Compliance probe failures should trigger an alert ("compliance probe failed, check headend connectivity").
- **S9 (Reliability):** GStreamer pipeline lifecycle/supervision (and clean restart of the engine + any optional CG co-process) must recover the channel's sinks before S2 probes can succeed — replaces the old ffmpeg-relay-reap dependency.
- **S11 (Captions, Loudness, EAS):** Loudness target coordination (streaming -16 LUFS vs cable -24 LKFS).
- **S15 (Playout Engine — GStreamer):** the engine that realizes every S2 output as a GStreamer sink (`udpsink`+`mpegtsmux`, `srtsink`, `hlssink`, `ndisink`, `decklinkvideosink`); the headend profiles, proof tiers, and TSDuck compliance in S2 live ABOVE the engine and drive it. **S2 ↔ S15 is the primary cross-ref for outputs.**

### Cross-refs to other sections

- **Master §2.1 (PEG automation coverage):** This section delivers CA-6 + CA-7 from the gap list.
- **Master §5 (Proof ladder):** Rungs for headend profiles (lab → machine → field).
- **Master §10 (Build order):** S2 is step 2 (after S9 reliability hardening).

### Open decisions for Scott

1. **Video profile matrix (interlaced cable profiles) — RESOLVED per D13:**
   - **Status:** Interlaced cable profiles **1080i59.94** and **480i29.97** plus **720p59.94** are cable-parity and ship in S2/S3 as selectable `CanonicalProfile` presets (see § 6.5). This is no longer an open question — D13 binds it as build-now, not deferred.
   - **Remaining for Scott:** confirm the added scope is acceptable (the reconciliation flags interlaced profiles, full CG template set, and functional hosted adapters as the V1 scope pulled in by D13).

2. **Per-sink loudness target — RESOLVED per D6:**
   - **Status:** The loudness **target** is per-sink on `EgressSinkSpec` (`loudness_target_lufs`), so a channel can feed cable at -24 LKFS and a streaming backup at -16 LUFS simultaneously, each normalized to its own target (LKFS == LUFS). No global channel-level field. The migration adding the column lands in S11's `0044_loudness_and_eas`.
   - **Remaining for Scott:** none — D6 binds the per-sink design.

3. **TSDuck tolerance % for CBR mux-rate check:**
   - **Status:** Currently hardcoded 5% tolerance in `evaluate_tsduck_report()` (compliance.py:199).
   - **Decision needed:** Is 5% the right tolerance, or should it be operator-configurable?
   - **Recommendation:** 5% is conservative; ship as-is. Later, add operator override in UI if needed.

4. **Device probe port list for headend appliances:**
   - **Status:** Defaults to ports 80, 443.
   - **Decision needed:** Should we auto-discover common ports (e.g., TelVue HyperCaster on 9191), or keep it operator-supplied?
   - **Recommendation:** Keep operator-supplied for generality (vendor-agnostic). Runbook can list common ports per vendor.

---

*S2 is complete when all DONE criteria (§9) are met and field-proof (rung 4) is deferred to first-station beta.*
