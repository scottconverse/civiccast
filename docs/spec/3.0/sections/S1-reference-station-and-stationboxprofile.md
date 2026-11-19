# S1 — Reference Station Build(s) & the StationBoxProfile Capability Model

> **Status (2026-08-21 audit correction):** The banner below this note
> previously claimed "Built for v3.0.0-beta1" while the code did not exist —
> corrected here to state what actually landed on
> `feat/s1-station-profile-s3-commissioning`, contract-tested (rung 0), not
> field/hardware-proven:
>
> - `civiccast/platform/station_box_profile.py`: the full `StationBoxProfile`
>   model tree (§3), `select_ai_defaults` (§6.1), `EngineReadiness` +
>   `EngineTierVerdict` GStreamer-prerequisite detection (§3, §6.5),
>   `PegReadinessRollup` (§6.2), `CableOsVerdict` (§6.3) — computed, no DB
>   table, exactly as specified. 40 unit tests
>   (`tests/platform/test_station_box_profile.py`).
> - `GET /api/staff/station-box-profile` and `/readiness`, role-gated
>   (`setup_admin`/`meeting_operator`/`support_admin`) — **note the path is
>   `/api/staff/...`, not the spec §4 table's literal
>   `/api/station-box-profile`**: this codebase's staff bearer-token
>   middleware only authenticates `/api/staff/*`, so the literal spec path
>   would 401 for every caller. 13 API tests
>   (`tests/platform/test_station_router_api.py`).
> - `civiccast doctor --profile` (human + `--json`) extends the existing
>   `doctor` with the Playout engine / Cable output / Clock / AI default /
>   PEG readiness sections; plain `doctor --json` stays byte-identical to
>   the pre-existing `HardwareProbe` shape for back-compat. 7 CLI tests.
> - A new **Station Profile** operator-console screen
>   (`StationProfileScreen.tsx`) — the identity half (name/timezone/storage
>   roots, view+edit, via the new `GET`/`PUT /api/staff/station/profile`)
>   and the capability half (the `StationBoxProfile` card + PEG readiness
>   badges + the verbatim §13.1 cable-OS caveat). 11 vitest cases.
> - Honesty boundaries carried into the code, not just prose:
>   `EngineReadiness.decklink.card_present` is a `gst-device-monitor-1.0`
>   heuristic, and `opengl_45` is inferred from NVIDIA GPU presence — both
>   documented in the module docstring as heuristics, never claimed as a
>   measured fact. Physical SDI proof (rung 3) is untouched by this slice.
> - **Not done in this slice**: the `civiccast/installer/station_state.py`
>   loader migration was scoped to the timezone path (S1's own precedent)
>   plus new `resolve_station_display_name`/`resolve_station_storage_locations`
>   loaders; it deliberately does NOT fold in `CIVICCAST_UPLOAD_DIR` /
>   `CIVICCAST_NAS_ARCHIVE_PATH` (a separate, more deeply cross-cutting
>   env-driven contract used by the native supervisor's child-process env
>   inheritance) — see the PR description for the reasoning.
>
> The banner immediately below is the section's **original** (pre-2026-08-21)
> claim and is retained for history; treat the note above as authoritative
> for what the current codebase actually contains.
>
> **Status:** Built for v3.0.0-beta1; field-headend proof remains external.
> Verified against code on `main @ 69cc676`; the later v3.0.0-beta1 public-beta
> release-artifact soak passed against `cbd3265c5b69260b634abc92b466786f311e73ef`.
> **Scope:** The reference PC build(s) for the PEG profile, plus a typed `StationBoxProfile`
> capability model that extends `civiccast doctor` from a streaming-tier GPU probe into a
> full cable/PEG appliance-readiness report — including **playout-engine prerequisite detection**
> for the now-canonical **GStreamer engine (S15)**. Reconciles `spec.md §5.3/§7.7/§10` (PowerSpec
> G730 ~$2,000, Tier 0–2, Win11+WSL2) with the cable/SDI flagship, adds a cable/SDI build
> tier, and makes the AI default selection adaptive on detected memory per MASTER §8.
> **Cross-refs:** MASTER §5.3/§10 (build order), §6 (entity model), §8 (AI/model decision),
> §13.1 (cable-grade OS open decision); **S15 (playout engine — GStreamer; S1 supplies the
> per-tier engine-prerequisite verdict S15 commissioning consumes)**. Feeds S2 (headend),
> S3 (commissioning wizard), S8 (alerting), S11 (loudness/EAS), S13 (AI model selection).

---

## 1. Goal & PEG automation rationale

**What incumbent PEG platform does (MASTER §2.1):** the third-party vendor ships *hardware* — the VIO family (Lite/2/4/
OMNI/Stream/CG/VOD) — pre-validated, pre-sized, and pre-warrantied for 24/7 PEG playout. The
station never reasons about CPU, VRAM, SDI driver state, or storage headroom; the appliance is
the answer. That validated-hardware story is the entire value proposition behind the up-front
capital (VIO Lite ~$10.5k → VIO-4 Plus ~$34.6k; real receipts: St. Joseph MN $14,845.94,
Marshall MN $42,435) and the recurring hardware-assurance line ($700–$3,465/yr, MASTER §2.3).

**The parity move:** CivicCast 3.0 replaces the *need* for that hardware with **software on one
commodity PC a station buys locally for under ~$5K** (MASTER §1). To make that credible we must
do for a BYO PC what the third-party vendor does for a VIO: **tell the operator, before they ever go to air,
whether the box in front of them is actually fit to run an unattended PEG channel** — and size
the recommended build so the answer is "yes." That is exactly what S1 delivers: a published
reference build (the "buy this") and a `StationBoxProfile` capability model (the "is what I have
good enough, and what's missing"). This is the software-defined-appliance posture MASTER §1
adopts for the PEG profile.

**Why the existing probe is not enough:** `civiccast doctor` today answers a *streaming* question
("which AI tier does my GPU support") and was written for the v1.8→v2.0 community-media product
that explicitly deferred cable (MASTER §0). It says nothing about the things a cable/PEG appliance
lives or dies on: **playout-engine readiness (GStreamer + the required plugin set — the engine the
box must actually run per S15), GPU/OpenGL-4.5 + hardware-encoder (NVENC/VA-API/QSV) availability,**
DeckLink/SDI readiness (now incl. the BMD Desktop Video SDK the `decklinkvideosink` needs at
runtime), NDI SDK readiness, TSDuck verification readiness, system clock/timezone sanity for a 24/7
program log, backup-destination reachability, and the release identity the box is actually running.
S1 closes that gap by **extending** the existing typed probe rather than replacing it — the GPU/tier
logic stays, the cable-appliance and **engine-prerequisite** dimensions are added.

**Engine note (S15-aligned):** the playout/compositor/output engine is **GStreamer** across all
tiers (S15), replacing the per-segment ffmpeg-relay. S1's readiness lines therefore probe the
**GStreamer engine prerequisites per tier** — not just "is there an ffmpeg." (ffmpeg feature flags
are still reported as a transitional/utility-path detail, but the engine the box must run is
GStreamer.) The DeckLink and NDI sinks are now GStreamer elements (`decklinkvideosink`, `ndisink`)
that depend on the BMD Desktop Video SDK and NDI SDK respectively — so the readiness verdict checks
for those SDKs, not for a BYO `--enable-decklink`/patched-NDI ffmpeg.

This is the leadoff of MASTER §10's build order (step 2, "SDI-proven (S1/S2 + hardware)") because
every later section assumes a box that has been characterized: S3's commissioning wizard renders
the profile, S8's alerting keys off its readiness rollups, S13's AI selection consumes its memory
verdict, and **S15's GStreamer engine consumes S1's per-tier engine-prerequisite verdict to know
which pipeline tier the box can run.**

---

## 2. Current state (file:line — exists vs net-new)

### Exists (reuse / extend)

- **`HardwareProbe` typed probe** — `civiccast/platform/hardware.py:85-111`. Returns
  `cpu`/`ram`/`disk`/`gpu`/`os`/`recommended_tier`/`civiccast_version`. Served by `/api/hardware`
  and rendered by `civiccast doctor`. **This is the extension point.**
  - `CPUInfo` (`:32`), `RAMInfo` (`:40`, `total_gb`/`available_gb`), `DiskInfo` (`:47`),
    `GPUInfo` (`:55`, NVIDIA/NVML-only per ADR 0005), `OSContext` (`:68`, `kind` ∈
    `wsl2|linux|macos|windows|unknown`, `:225` `_classify_os`).
  - `Tier = tier-0|tier-1|tier-1-plus|tier-2` (`:28`); `_tier_for()` (`:349`) keys **only on GPU
    VRAM** (`<8 → tier-0`, `<16 → tier-1`, `<24 → tier-1-plus`, else `tier-2`). **No system-RAM
    input today** — this is the gap MASTER §8's adaptive-AI default must fill.
  - CPU brand resolution is cross-platform: `/proc/cpuinfo` → WMIC on Windows (`:316`) →
    `platform.processor()`.
- **`civiccast doctor` CLI** — `civiccast/cli.py:139-167`; human render `_render_probe_human()`
  (`:1702-1737`) prints hostname/os/CPU/RAM/disk/GPU/tier + ffmpeg/ffprobe checks
  (`:1742-1771`, min ffmpeg/ffprobe 4.4) + CDN check (`:1779`). `--json` emits the model verbatim.
  **`doctor` already reaches into ffmpeg version detection** — the natural home for ffmpeg
  feature-flag and DeckLink/TSDuck readiness lines.
- **SDI readiness** — `civiccast/egress/sdi_relay.py`: `SdiReadiness` (`:79`,
  `status` ∈ `ok|decklink_muxer_missing|ffmpeg_unavailable`, `ffmpeg_detected`, `muxer_present`,
  `next_step`); `check_sdi_runtime()` (`:149`) probes a BYO `CIVICCAST_SDI_FFMPEG` for the
  `decklink` muxer; `cached_check_sdi_runtime()` (`:200`, 300s TTL, audit ENG-008).
  `SdiRelaySettings.from_env()` (`:68`) reads `CIVICCAST_SDI_RELAY`/`CIVICCAST_SDI_FFMPEG`.
  **DeckLink readiness primitive exists; it is just not surfaced by `doctor`.** *Note (S15):* the
  SDI output path is moving to the GStreamer `decklinkvideosink` (BMD Desktop Video SDK at runtime),
  so this BYO-ffmpeg primitive is being superseded by the `EngineReadiness.decklink` check (BMD SDK
  + `decklinkvideosink` plugin presence). S1 surfaces both during the transition; the engine-side
  verdict is the canonical one going forward.
- **TSDuck readiness** — `civiccast/egress/compliance.py`: `TsduckStatus` (`:57`,
  `installed`/`path`/`version`/`install_hint`); `locate_tsduck()` (`:139`) resolves `tsp` via
  `CIVICCAST_TSDUCK_PATH` or PATH. **TSDuck readiness primitive exists; not surfaced by `doctor`.**
- **NDI readiness** — `check_ndi_runtime()` (used by `cli.py:343`, `cable ndi-check`) — same
  pattern; should appear as a profile line for completeness.
- **Station identity** — `civiccast/installer/models.py`: `StationProfile` (`:228`,
  `station_name`/`admin_username`/`default_channel_id`/`public_base_url`/`recovery_kit_*`);
  `DeploymentProfile = public-meetings|streaming-only|peg-cable` (`:12`). Note: identity and
  capability are deliberately separate concerns — `StationProfile` is operator identity,
  `StationBoxProfile` is hardware/OS capability. **Do not conflate.**
- **Backup destination** — `BackupSetupRequest.destination` (`:568`), `BackupStatus`
  (`:592`, `status`/`last_probe_at`); installer already probes a backup destination. The reach
  test exists; `StationBoxProfile` references its result rather than re-implementing it.
- **Release identity** — `civiccast/_version.py __version__` (read at `hardware.py:127`);
  `PackageVerificationResult`/`ModelSetupItem.proof_state` (`installer/models.py:807,823`) and
  `release_api.py` carry build/hash identity.
- **AI model tags (today, hard-coded)** — summary `gemma4:e4b` (`summary/ollama.py:19`),
  translation `translategemma:4b` (`translate/ollama.py:17`), captions whisper-large-v3
  (`captions/`). `ai_runtime/ollama_client.py` is loopback-only (`:113`). **No memory-adaptive
  default and no operator selection surface yet** (that surface is S13; S1 supplies the *verdict*).

### Net-new (this section)

- `StationBoxProfile` typed model (capability + readiness rollup), built by extending `probe()`.
- **`EngineReadiness` + `EngineTierVerdict` — GStreamer playout-engine prerequisite detection per
  S15 tier** (plugin set, OpenGL 4.5 + hardware encoder, DeckLink + BMD Desktop Video SDK, NDI SDK,
  native-OS-vs-WSL2), driving which S15 engine tier (base / sdi-broadcast / premium-cg) the box
  qualifies for. **This is the core net-new work of the S15 alignment.**
- System-RAM dimension wired into AI-default selection (`ai_default` block on the profile).
- `cable-sdi` build tier added to `spec.md §10` (doc) and a `peg-cable`-aware profile verdict.
- `civiccast doctor --profile` extended render + `/api/station-box-profile` endpoint.
- The cable-grade-OS verdict field (single-Windows-PC vs native-Linux), reported as
  **soak-pending** until MASTER §13.1 resolves.

---

## 3. Entities / data model & migrations (reuse MASTER §6 names)

`StationBoxProfile` is the single net-new entity MASTER §6 assigns to S1. It is a **computed,
in-memory report** (like `HardwareProbe` and `SystemHealthReport`) — **no DB table, no Alembic
migration.** It is produced on demand from live probes; nothing about a box's capability should be
read from stale persisted rows. (If a future section wants to persist a commissioning snapshot,
that is S3's commissioning report, which may *embed* a `StationBoxProfile` — S1 owns the type, not
its storage.)

Shape (Pydantic, `model_config = ConfigDict(extra="forbid")`, same posture as the existing
installer models):

```
StationBoxProfile
  schema_version: int                      # bump on shape change; lets S3 reports pin a version
  generated_at: datetime
  civiccast_version: str                   # reused from HardwareProbe
  # --- capability (extends HardwareProbe; embed it rather than duplicate) ---
  hardware: HardwareProbe                  # cpu/ram/disk/gpu/os/recommended_tier (existing)
  system_ram_total_gb: float               # net-new: mirrors HardwareProbe.ram.total_gb (RAMInfo.total_gb); the
                                           #   system-RAM axis the adaptive AI default keys off — SEPARATE from the
                                           #   VRAM-keyed recommended_tier. S1 defines this field; S13 reads it.
  engine: EngineReadiness                  # net-new: GStreamer playout-engine prerequisites (S15) — plugin set,
                                           #   GPU/OpenGL-4.5 + hw-encoder, DeckLink+BMD SDK, NDI SDK, native-OS-vs-WSL2
  ffmpeg: FfmpegFeatureReport              # net-new: version + decklink/ndi/libx264/loudnorm flags (transitional/
                                           #   utility-path detail; the engine the box runs is GStreamer per S15)
  clock: ClockReport                       # net-new: timezone, UTC offset, NTP-sync best-effort, drift note
  network: NetworkReport                   # net-new: hostname (from OSContext), primary iface up, headend-iface hint
  backup_destination: BackupDestinationRef # net-new: reachable? (references installer BackupStatus, not re-probed)
  release_identity: ReleaseIdentityRef     # net-new: version + package-verification/proof_state if known
  # --- cable/PEG readiness rollups (reuse existing readiness models verbatim) ---
  sdi: SdiReadiness                        # from egress/sdi_relay.py — DeckLink-readiness (now feeds the GStreamer
                                           #   decklinkvideosink path per S15; engine.decklink carries the SDK verdict)
  tsduck: TsduckStatus                     # from egress/compliance.py — TSDuck-readiness
  ndi: NdiReadiness                        # from egress/ndi_relay — completeness (NDI SDK verdict mirrored in engine.ndi)
  # --- derived verdicts ---
  qualified_engine_tier: EngineTierVerdict # net-new: which S15 tier the box qualifies for (base | sdi-broadcast |
                                           #   premium-cg) from the adaptive engine-prerequisite detection
  ai_default: AiDefaultSelection           # net-new: memory-adaptive summary/translate/caption picks
  peg_readiness: PegReadinessRollup        # net-new: green/yellow/red roll-up across the cable + engine dimensions
  cable_os_verdict: CableOsVerdict         # net-new: single-windows-pc | native-linux-recommended | soak-pending
```

Net-new leaf types:

- **`EngineReadiness`** (S15 playout-engine prerequisites — the net-new core of this revision):
  `{ gstreamer_present: bool, gstreamer_version: str|None, required_plugins_present: bool,
  missing_plugins: list[str], opengl_45: bool, hw_encoder: Literal["nvenc","vaapi","qsv","none"],
  decklink: DeckLinkEngineRef, ndi_sdk: NdiSdkRef, native_os: bool, next_step: str }`.
  - `gstreamer_present`/`gstreamer_version` from `gst-inspect-1.0 --version` (cache like
    `cached_check_sdi_runtime`); `required_plugins_present`/`missing_plugins` from probing the
    **per-tier required plugin set** via `gst-inspect-1.0 <element>` — base needs the CG-lite/output
    core (`compositor`, `interpipesrc`/`interpipesink`, `mpegtsmux`, `udpsink`, `srtsink`,
    `hlssink3`, `textoverlay`/`clockoverlay`); the SDI/broadcast set adds `decklinkvideosink`,
    `ndisink`, and a hardware encoder (`nvh264enc`/`vah264enc`/QSV); the rich-CG set adds `wpesrc`
    (S15 §4–§6).
  - `opengl_45` = OpenGL ≥4.5 present (needed for GPU compositing/rich CG and the hardware-encode
    comfort path, S15 §5–§6); `hw_encoder` = which hardware H.264/H.265 encoder GStreamer can use
    (NVENC/VA-API/QSV) or `none` (base CPU tier → bundled `openh264enc`;
    optional operator-provided `x264enc`, S15 §6).
  - `decklink: DeckLinkEngineRef` = `{ card_present: bool, bmd_sdk_present: bool, sdk_version:
    str|None }` — the BMD **Desktop Video SDK** the GStreamer `decklinkvideosink` needs at runtime
    (S15 §4). `ndi_sdk: NdiSdkRef` = `{ sdk_present: bool, sdk_version: str|None }` — the NDI SDK 5/6
    the `ndisink` needs at runtime (S15 §4).
  - `native_os` = the box runs a native OS (not WSL2): **DeckLink is a PCIe card and cannot pass
    through to WSL2, so the SDI tier requires native OS** — this field gates the SDI/broadcast
    qualification (see §6.5 and `cable_os_verdict`). `next_step` carries the concrete remediation
    for the highest-priority missing prerequisite.
- **`EngineTierVerdict`**: `{ qualifies_for: Literal["base","sdi-broadcast","premium-cg"],
  base_ok: bool, sdi_broadcast_ok: bool, premium_cg_ok: bool, blockers:
  list[{tier,reason,next_step}] }` — the derived "which S15 tier can this box actually run" verdict,
  computed by §6.5 from `EngineReadiness`. `premium-cg` reflects the **optional** CasparCG GPLv3
  co-process tier (S15 §5) and is never required.
- **`FfmpegFeatureReport`**: `{ detected: bool, version: str|None, supported: bool, has_decklink:
  bool, has_ndi: bool, has_libx264: bool, has_loudnorm: bool, byo_sdi_binary: str|None,
  next_step: str }`. **Transitional / utility-path only** — the box's playout engine is GStreamer
  (`EngineReadiness` above), not per-segment ffmpeg; this report is retained for ancillary ffmpeg
  use and migration visibility. `has_decklink`/`byo_sdi_binary` come straight from `SdiReadiness`/
  `SdiRelaySettings`; the rest from one `ffmpeg -muxers`/`-filters`/`-encoders` probe (reuse the
  `_ffmpeg.py` runner pattern; cache like `cached_check_sdi_runtime`).
- **`ClockReport`**: `{ timezone: str, utc_offset_minutes: int, system_time: datetime,
  ntp_sync: Literal["synced","unsynced","unknown"], note: str }`. A 24/7 program log (S4) is only
  honest if the clock is. `ntp_sync` is best-effort (`w32tm /query /status` on Windows, `timedatectl`
  on Linux) and **fails to `"unknown"`, never to a faked `"synced"`** (same honesty posture as the
  TSDuck `not-run` verdict).
- **`NetworkReport`**: `{ hostname: str, primary_interface_up: bool, headend_interface_hint:
  str|None }` — `hostname` reuses `OSContext.hostname`; the headend hint defers detail to S2.
- **`BackupDestinationRef`**: `{ configured: bool, reachable: bool|None, destination: str|None,
  last_probe_at: datetime|None }` — populated from the installer `BackupStatus`; `None`/`configured:
  false` when no destination is set (fail-open is wrong here: an unattended box with no backup is a
  **yellow** in `peg_readiness`).
- **`ReleaseIdentityRef`**: `{ version: str, package_verified: bool|None, proof_state: str|None }`.
- **`AiDefaultSelection`** — see §6.1. `{ summary_model: str, translate_model: str,
  caption_model: str, basis: Literal["ram-12b","ram-e4b","forced-cpu"], detected_ram_gb: float,
  rationale: str }`.
- **`PegReadinessRollup`**: `{ overall: Literal["green","yellow","red"], dimensions:
  list[{id,label,color,message,next_step}] }` — reuses the `green|yellow|red` vocabulary already in
  `SafeToBroadcastColor` (`installer/models.py:17`).
- **`CableOsVerdict`**: `{ verdict: Literal["single-windows-pc-ok","native-linux-recommended",
  "soak-pending"], os_kind: OSKind, rationale: str, decision_ref: str }` where `decision_ref`
  points at MASTER §13.1.

**Migrations:** none. S1 adds **no** migration to the single global Alembic chain (one head, currently
`0037_asset_meeting_body`, per `tests/live/test_real_postgres.py` — "one head despite the per-module
directory layout"); the 3.0 migration sequence from `0038` is owned by other sections (RECONCILIATION
migration table), and S1 is explicitly not on it. (Explicitly: do not add a table. The 0/0/0/0/0 audit
expectation in §8 includes "no orphaned migration.")

---

## 4. API surface (endpoints + auth roles)

Auth uses the existing five real roles (`setup_admin`, `meeting_operator`, `records_clerk`,
`publish_operator`, `support_admin`; `operator`/`admin` are all-roles aliases) + `require_any_role`
per endpoint (MASTER §6; `civiccast/auth/roles.py:14-20`). These are **read-only diagnostic
surfaces**, so per the canonical role decision they are gated to `support_admin` (the diagnostic
role) plus the commissioning/console roles that legitimately consult readiness — `setup_admin` and
`meeting_operator`. They expose hostname/paths but no secrets; there are no mutating endpoints in
S1 (the profile is computed, not configured).

| Method | Path | Roles | Purpose |
|---|---|---|---|
| GET | `/api/staff/station-box-profile` | `setup_admin`, `meeting_operator`, `support_admin` | Full `StationBoxProfile` JSON. |
| GET | `/api/staff/station-box-profile/readiness` | `setup_admin`, `meeting_operator`, `support_admin` | `PegReadinessRollup` only — the cheap roll-up for the console badge and for S8 alerting to poll. |
| GET | `/api/hardware` | (existing) | Unchanged; still returns the bare `HardwareProbe`. |

**S1 owns the canonical `civiccast doctor` command and the `StationBoxProfile` output surface.**
S3 (`station doctor`/`commission`/`--cable`), S10 (`doctor --proof`), and S13 (AI model default) are
**extensions** that cross-reference this canonical command, not parallel doctors. CLI (extend the
existing `doctor`, do **not** add a parallel command):

- `civiccast doctor` — unchanged default output **plus** new lines: **playout-engine readiness
  (GStreamer + per-tier plugin set, OpenGL 4.5, hardware encoder, DeckLink+BMD SDK, NDI SDK,
  native-OS, and the qualified S15 engine tier)**, DeckLink, TSDuck, NDI, clock, AI default. Honest
  "—" / "not set up" where a dimension is absent.
- `civiccast doctor --profile` — render the full `StationBoxProfile` including the
  `peg_readiness` roll-up and `cable_os_verdict`.
- `civiccast doctor --json` — emits `StationBoxProfile` (superset of today's `HardwareProbe` JSON;
  keep `HardwareProbe` embedded under `hardware` for backward compatibility with any consumer that
  parses the old shape).

No endpoint may claim a readiness it has not probed; absent BYO binaries yield honest `blocked`/
`not-run`/`unknown` states exactly as the underlying `SdiReadiness`/`TsduckStatus` already do.

---

## 5. Operator UI surface

S1 is mostly a CLI + API + commissioning-data layer; the rich UI is rendered by S3's
commissioning wizard and the operator console's SystemHealthScreen. S1 owns:

1. **`doctor` terminal render** (primary surface) — the cable lines described in §4, grouped:
   `Hardware` (existing), `Streaming tools` (existing ffmpeg/ffprobe), **new** `Playout engine`
   (GStreamer present + version, per-tier required-plugin set / missing plugins, OpenGL 4.5,
   hardware encoder, DeckLink+BMD SDK, NDI SDK, native-OS — i.e. the S15 engine prerequisites + the
   `qualified_engine_tier` verdict base/sdi-broadcast/premium-cg), **new** `Cable output` (DeckLink
   readiness; NDI; TSDuck install/version), **new** `Clock` (timezone/offset/NTP), **new** `AI
   default` (selected summary/translate/caption models + the memory basis), **new** `PEG readiness`
   (green/yellow/red + per-dimension next steps).
2. **A `StationBoxProfile` card spec** for the operator console (consumed by S3's "First-run cable
   checks" step, S3 §1) — a read-only panel: capability summary, the readiness roll-up badge
   (reusing the green/yellow/red `SafeToBroadcastColor` chips already in the console), and an
   explicit `cable_os_verdict` line that, while `soak-pending`, reads *exactly*: "Single-Windows-PC
   certification for 24/7 cable is pending the soak result — see MASTER §13.1." No green claim
   before the decision.

S1 does not build new standalone screens; it defines the data and the `doctor` text. (Building the
commissioning UI is S3; this avoids two sections owning the same screen.)

---

## 6. Behavior / algorithms

### 6.1 Adaptive AI-default selection (resolves MASTER §8 onto detected memory)

Today `_tier_for()` (`hardware.py:349`) keys only on **GPU VRAM** and the hard-coded model tags
are `gemma4:e4b` / `translategemma:4b` / whisper-large-v3. MASTER §8 requires the **summary**
default to become `gemma4:12b` (QAT int4, ~7GB) where hardware allows, falling back to `gemma4:e4b`
on 8GB-class boxes, keyed on **system RAM** (Google states 16GB system RAM runs the 12B at
CPU-batch ~5–9 tok/s — fine for background summaries).

`select_ai_defaults(probe: HardwareProbe) -> AiDefaultSelection` (keys off **system RAM** via the
canonical `system_ram_total_gb` field, which mirrors `probe.ram.total_gb`):

```
ram = system_ram_total_gb   # == probe.ram.total_gb; the system-RAM axis, NOT VRAM
if ram >= 16:
    summary   = "gemma4:12b"      # MASTER §8 default where memory allows
    basis     = "ram-12b"
    rationale = "16GB+ system RAM detected; 12B summary default (better long-context, MRCR 43.4 vs 25.4)."
elif ram >= 8:
    summary   = "gemma4:e4b"      # current shipped default; matches summary/ollama.py:19
    basis     = "ram-e4b"
    rationale = "8–16GB system RAM; e4b summary default to stay within memory budget."
else:
    summary   = "gemma4:e4b"      # smallest viable; flag a warning in peg_readiness
    basis     = "forced-cpu"
    rationale = "<8GB system RAM; e4b on CPU, expect slow background summaries; box is under-provisioned."
translate = "translategemma:4b"   # MASTER §8: stays 4B-class (latency-sensitive); matches translate/ollama.py:17
caption   = "whisper-large-v3"    # MASTER §8: stays large-v3 (local, cost advantage)
```

Notes / honesty boundaries:
- This is the **recommended default**; "operator always chooses" is a hard MASTER §8 principle —
  the *selection surface* (per-feature registry, Ollama-Cloud/OpenRouter escalation) is **S13**.
  S1 only computes the default and surfaces it; S1 must not silently change a model already pinned
  by the operator.
- The threshold is **system RAM**, not VRAM, because the 12B target is CPU-batch on 16GB boxes
  (MASTER §8). A box can be `tier-0` on VRAM yet still get the 12B summary default if it has 16GB
  RAM — this is a deliberate decoupling of the GPU tier from the summary-model default. Keep
  `recommended_tier` and `ai_default.basis` as separate fields so neither overwrites the other.
- The PowerSpec G730 reference (`spec.md §10.2`, 32GB RAM / 16GB VRAM) lands `ram-12b` + `tier-1`
  — confirming the reference dev box gets the new 12B summary default.

### 6.2 PEG readiness roll-up

Compute `PegReadinessRollup` from the dimensions. Color rules (fail-closed, never soft-green):
- **red** if any *required-for-cable* dimension is hard-failed: **GStreamer engine absent or the
  base required-plugin set missing (no engine = no playout at all, S15)**; clock unknown AND no NTP;
  no working channel output path.
- **yellow** if a required cable dimension is *blocked-but-recoverable*: **a tier-specific engine
  prerequisite missing when the matching profile/tier is selected — DeckLink card or BMD Desktop
  Video SDK absent / `decklinkvideosink` missing for the SDI tier; NDI SDK absent for NDI; no
  hardware encoder / OpenGL 4.5 when the SDI-broadcast tier is targeted; not running native OS for
  SDI (PCIe can't passthrough WSL2)**; TSDuck not installed; backup destination unconfigured or
  unreachable; `<8GB` RAM (`forced-cpu`); `cable_os_verdict == soak-pending`.
- **green** only when every required cable dimension is `ok` **and** `cable_os_verdict` is resolved
  to an OK state.
- Each dimension carries a concrete `next_step` (reuse the `next_step` discipline already pervasive
  in `installer/models.py`). DeckLink/TSDuck `next_step` strings reuse the existing
  `_BYO_BINARY_HINT`/`_MUXER_HINT` (`sdi_relay.py:45,51`) and `_INSTALL_HINT` (`compliance.py:43`);
  the **engine** dimension's `next_step` points at the missing GStreamer plugin / SDK and the S15
  per-tier requirement (e.g. "install gst-plugins-bad for `decklinkvideosink` + BMD Desktop Video
  SDK"; "install the NDI SDK 5/6 for `ndisink`"; "no native OS — SDI requires native OS, PCIe
  DeckLink can't passthrough WSL2").
- Profile-awareness: when `DeploymentProfile == streaming-only`, DeckLink/TSDuck/**SDI-tier engine
  prerequisites** (DeckLink+BMD SDK, hardware encoder, OpenGL 4.5, native-OS) are **not** a cable
  failure — those dimensions report `not-applicable`, not `yellow`. The base GStreamer engine +
  IP-output plugin set is still **required** for streaming-only (no engine = no playout). The
  roll-up reads the active profile so a streaming station isn't nagged about SDI.

### 6.3 Cable-grade OS verdict (MASTER §13.1, soak-pending)

`spec.md §5.3` currently says cable-grade 24/7 "should use native Linux until WSL2 has been
validated for that load profile." MASTER §13.1 makes the soak result the deciding evidence (decision
due ~20:15Z on soak completion). S1 therefore computes:
- `os_kind == "linux"` → `native-linux-recommended` (verdict already favorable).
- `os_kind == "windows"` or `"wsl2"` → **`soak-pending`** with `rationale` referencing §13.1 and
  the running soak nonce, until Scott resolves §13.1. **The product never prints a green
  single-Windows-PC cable certification before that decision.** When §13.1 resolves to "certify,"
  flip the default to `single-windows-pc-ok`; if it resolves to "keep the caveat," WSL2/windows stays
  `native-linux-recommended` for the cable profile.
- `os_kind == "macos"` → `native-linux-recommended` with the `spec.md §5.3/§10.4` note that cable on
  Mac runs CivicCast on a Linux box attached to the DeckLink card.

### 6.4 Reference build tiers (doc deliverable — reconcile `spec.md §10`)

S1 publishes the build matrix that the parity pitch (MASTER §2.3, "self-host on a ~$5K PC") rests on.
Reconcile with `spec.md §10` and **add a cable/SDI tier**. The engine column maps each build to its
**S15 GStreamer engine tier** (base = CPU GStreamer, no GPU/no GPLv3; SDI/broadcast = +DeckLink +
native OS + GPU; premium-CG = optional CasparCG):

| Tier | Source | CPU / RAM / GPU | Storage | AI default (per §6.1) | Engine tier (S15) | Cable last-mile |
|---|---|---|---|---|---|---|
| Tier 0 (batch/stream) | `spec.md §10.1` (~$1,800) | Ryzen 7 7700 / 32GB / none | 2×4TB NVMe RAID1 | e4b CPU (16GB→still ram-12b-capable) | base (GStreamer CPU; **no GPU**) | none |
| Tier 1 Streaming (reference) | `spec.md §10.2` (~$2,520 all-in ~$2,780) | Ryzen 7 7700 / 32GB / RTX 4060 8GB | 2×4TB NVMe RAID1 | **gemma4:12b** (32GB RAM) | base (GStreamer CPU; GPU optional) | none |
| Reference dev/validation | `spec.md §10.2` PowerSpec G730 (~$2,000) | Ryzen 7 7800X3D / 32GB / RTX 5070 Ti 16GB | 2TB NVMe | **gemma4:12b** + large-v3 concurrent | base/SDI-capable (GPU present) | none |
| **Cable/SDI (NEW)** | this section | Ryzen 7 7700/7800X3D / **32GB+** / RTX 4060–4070 8–16GB (**OpenGL 4.5**) | 2×4TB NVMe RAID1 + NAS | **gemma4:12b** | **SDI/broadcast (GStreamer + decklinkvideosink + hw-encode + WPE rich CG)** | **+ Blackmagic DeckLink + BMD Desktop Video SDK + native OS + UPS + headend iface** |
| Tier 2 multi-stream | `spec.md §10.3` (~$6,060) | Ryzen 9 7950X / 128GB / RTX 4070 Ti S 16GB | 8×4TB NVMe ZFS | gemma4:12b | SDI/broadcast (multi-channel) | optional DeckLink per channel |

**Base boxes need no GPU** — a Tier-0/Tier-1 streaming build runs the GStreamer CPU engine (IP-TS/
SRT/HLS + CG-lite via software `compositor`) with no GPU and no GPLv3 (S15 §5–§6, §8). **The
Cable/SDI build wants a GPU (OpenGL 4.5)** for GPU compositing, hardware encode (NVENC/VA-API/QSV),
and WPE rich CG — **but it is still commodity hardware the operator buys, not a proprietary
appliance.** The **Cable/SDI tier** is Tier-1-class compute plus the unavoidable last mile (MASTER
§1): a Blackmagic DeckLink card, the **BMD Desktop Video SDK** (the GStreamer `decklinkvideosink`
runtime dep — *not* a BYO `--enable-decklink` ffmpeg anymore, per S15), a **native OS** (PCIe
DeckLink cannot passthrough WSL2), a UPS, and whatever headend interface the cableco requires.
**32GB RAM is the floor** so the 12B summary default (§6.1) holds. An **optional premium-CG** add-on
(CasparCG, GPLv3 co-process, GPU) sits above the SDI tier for designer-driven broadcast CG (S15 §5)
— never required. This stays comfortably under the ~$5K headline (MASTER §1) and is the "buy this to
replace a VIO" answer.

### 6.5 Tier→engine qualification (adaptive detection → which S15 tier the box can run)

The adaptive engine-prerequisite detection (`EngineReadiness`, §3) drives **which S15 engine tier
the box actually qualifies for**, computed into `qualified_engine_tier: EngineTierVerdict`:

- **base** (`base_ok`) — GStreamer present + the base required-plugin set (`compositor`,
  `interpipesrc`/`interpipesink`, `mpegtsmux`, `udpsink`/`srtsink`/`hlssink3`, CG-lite overlays). No
  GPU, no GPLv3 required; WSL2 is fine. This is the "$5K commodity PC" floor — every box that runs
  CivicCast at all qualifies here.
- **sdi-broadcast** (`sdi_broadcast_ok`) — base **plus** DeckLink card + BMD Desktop Video SDK +
  `decklinkvideosink`/`ndisink` present, a hardware encoder (NVENC/VA-API/QSV) and OpenGL 4.5 for
  rich CG, **and `native_os == true`** (the hard gate: PCIe DeckLink can't passthrough WSL2). Missing
  any of these demotes the box to base with a `blockers` entry naming the specific gap + `next_step`.
- **premium-cg** (`premium_cg_ok`) — sdi-broadcast **plus** a GPU adequate for the **optional**
  CasparCG GPLv3 co-process (S15 §5). Never required; absence is never a failure, only an unmet
  optional capability.

The verdict is **fail-closed and honest**: a box qualifies for a higher tier only when *every*
prerequisite for that tier is detected `ok`; otherwise it reports the highest tier it genuinely
meets and lists the blockers. This is the engine-side analogue of the `peg_readiness` roll-up and
feeds S3's commissioning ("this box can run base/SDI/premium-CG") and S15's per-tier pipeline
selection.

---

## 7. Proof tier: current rung + how to advance + honest claim boundary

Using the unified ladder (MASTER §5):

- **`StationBoxProfile` model + `doctor`/API surface → Rung 0 (Contract-tested)** on landing:
  pure-Python probe over typed models, fully unit/API/CLI testable with injected fakes (the
  underlying `SdiReadiness`/`TsduckStatus`/`HardwareProbe` are already test-seamed via runner/
  connector injection — `sdi_relay.py:151`, `compliance.py:39`).
- **Adaptive AI-default selection → Rung 0** (deterministic function of `system_ram_total_gb`), and
  reaches **Rung 1 (Lab-proven)** the moment the soak box runs `gemma4:12b` against real meeting
  audio and we capture tok/s + a sane summary — the `proof_boundary` is "CPU-batch background
  summary on a 16GB-class box," not "interactive."
- **The reference Cable/SDI build → Rung 3 (SDI-proven) is GATED ON HARDWARE.** Today the SDI path
  is **contract-only** (MASTER §3; S15 §9 — the GStreamer `decklinkvideosink` path is at rung 0–1,
  card proof pending). S1 can document and recommend the build at rung 0–1, and can verify the
  **engine prerequisites** (`decklinkvideosink` plugin + BMD Desktop Video SDK present, native OS,
  hardware encoder) at rung 0; it **cannot** claim SDI-proven until a DeckLink card is on the tester
  and physical SDI is captured through the GStreamer engine (MASTER §10 step 2; MASTER §13.2; S15 §9
  — the one physical action gating rung 3). **This is the honest boundary: S1 says "this build is
  designed and sized for SDI output, and the engine prerequisites are detected," never "SDI output
  is proven," until rung 3 evidence exists.**
- **`cable_os_verdict` → blocked at `soak-pending`** for Windows/WSL2 until MASTER §13.1 resolves.
  The profile **must not** print a single-Windows-PC cable certification before then; that is a
  hard-claim-boundary item (MASTER §5: "no live-device / certification claims without rung-
  appropriate evidence").
- **Hard public claim boundary preserved:** no "appliance-certified," no "field-proven hardware,"
  no "EAS/loudness compliant" emerges from S1 — it reports *readiness*, not certification.

---

## 8. Test plan + the 0/0/0/0/0 audit expectation

**Unit (Rung 0):**
- `select_ai_defaults` truth table: RAM 6/8/15.9/16/32/128 → `forced-cpu`/`ram-e4b`/`ram-e4b`/
  `ram-12b`/`ram-12b`/`ram-12b`; assert summary tag flips at exactly 16.0GB; assert translate stays
  `translategemma:4b` and caption stays `whisper-large-v3` always (lock to the shipped tags at
  `summary/ollama.py:19`, `translate/ollama.py:17`).
- `peg_readiness` color matrix: each dimension forced to its failure/blocked/ok state via injected
  fakes; assert fail-closed (no soft green); assert `streaming-only` profile reports DeckLink/TSDuck
  as `not-applicable` not `yellow`.
- `cable_os_verdict`: `linux`→native-linux-recommended; `windows`/`wsl2`→`soak-pending` with the
  §13.1 ref string present; `macos`→native-linux-recommended.
- `ClockReport.ntp_sync` fails to `"unknown"` (never `"synced"`) when the query tool is
  absent/errors — inject a raising runner.
- `FfmpegFeatureReport` flag parsing against a captured `ffmpeg -muxers/-filters` fixture (mirror
  the `tsduck-analyze-clean-cbr.json` fixture pattern); `has_decklink` agrees with `SdiReadiness`.
- **`EngineReadiness` plugin/SDK probing against captured `gst-inspect-1.0` fixtures (mirror the
  fixture pattern): assert `required_plugins_present`/`missing_plugins` for (a) full base set,
  (b) base set with `decklinkvideosink` missing, (c) no GStreamer at all; assert `hw_encoder`
  resolves `nvenc`/`vaapi`/`qsv`/`none` from the encoder fixture; assert `decklink.bmd_sdk_present`
  and `ndi_sdk.sdk_present` read from injected SDK-probe fakes (never faked to `true`).**
- **`EngineTierVerdict` (§6.5) truth table: base-only box → `qualifies_for: base`;
  base+DeckLink+BMD-SDK+hw-encoder+OpenGL-4.5+native-OS → `sdi-broadcast`; same box on WSL2
  (`native_os == false`) → demoted to `base` with a `blockers` entry citing the PCIe-passthrough
  gate; SDI box without GPU → `base` with hw-encoder/OpenGL blockers; assert premium-cg never blocks
  a lower tier (optional).**
- Backward-compat: `doctor --json` output still contains a parseable `hardware` block matching the
  old `HardwareProbe` shape.

**API:** `/api/station-box-profile` and `/readiness` — 200 for `setup_admin`/`meeting_operator`/
`support_admin`, 403 for a role without diagnostic access (e.g. `records_clerk`); `extra="forbid"`
rejects unknown fields; schema matches the generated OpenAPI.

**CLI/UI:** `doctor` and `doctor --profile` golden-output tests (CliRunner) on a fixture box with
(a) full cable readiness, (b) no DeckLink/TSDuck, (c) `<8GB` RAM — assert honest "—"/hints, no
fabricated readiness, and that the `soak-pending` line prints the §13.1 caveat verbatim.

**Machine (Rung 2):** the profile runs inside the 24h soak's clean-Windows install; assert
`doctor --json` succeeds headless and the `peg_readiness` roll-up is stable across the soak (it
should not flap green↔yellow on a healthy box).

**0/0/0/0/0 audit expectation (MASTER §12; MEMORY: fix-all-severities-zero-audit):** after
implementation, `/audit-lite` between fixes and a full `/audit-team` + `/walkthrough` at stage
completion must each reach **0 Blocker / 0 Critical / 0 Major / 0 Minor / 0 Nit**. Specific traps
this section must not trip: (1) no faked readiness or soft-green (every dimension honest), (2) no
orphaned DB migration (S1 adds none — assert none appears), (3) no model auto-override of an
operator's pinned selection, (4) no single-Windows-PC cable claim while §13.1 is `soak-pending`,
(5) `extra="forbid"` on every new model, (6) `next_step` non-empty on every blocked/yellow/red
dimension.

---

## 9. DONE criteria

1. `StationBoxProfile` (+ leaf models) lands with `extra="forbid"`, embedding `HardwareProbe`; no
   DB table, no migration.
2. `select_ai_defaults()` implements the §6.1 RAM-keyed table; the 12B summary default activates at
   ≥16GB; translate/caption tags unchanged; operator pins are never overwritten.
3. `civiccast doctor` shows the new Playout engine / Cable output / Clock / AI default / PEG
   readiness blocks; `doctor --profile` renders the full profile; `doctor --json` is a
   backward-compatible superset.
4. `GET /api/station-box-profile` and `/readiness` exist, role-gated, OpenAPI-documented.
5. `peg_readiness` is fail-closed and profile-aware (streaming-only ≠ nagged about SDI); DeckLink/
   TSDuck/clock/backup dimensions reuse existing readiness primitives, not re-implemented probes.
5a. **`EngineReadiness` detects the S15 GStreamer engine prerequisites per tier (plugin set, OpenGL
   4.5, hardware encoder NVENC/VA-API/QSV, DeckLink+BMD Desktop Video SDK, NDI SDK, native-OS), and
   `qualified_engine_tier` correctly classifies the box as base / sdi-broadcast / premium-cg
   (fail-closed, with `blockers`+`next_step` for every unmet higher tier). WSL2 boxes are demoted
   out of sdi-broadcast (PCIe DeckLink can't passthrough). No SDK/plugin is ever faked present.**
6. `cable_os_verdict` is `soak-pending` for Windows/WSL2 with the §13.1 reference, and never prints
   a green single-Windows-PC cable certification before §13.1 resolves.
7. The reference build matrix (incl. the **new Cable/SDI tier**, 32GB RAM floor) is published and
   reconciled against `spec.md §10`; a `[SUPERSEDED-BY-3.0-S1]` annotation is added at the
   `spec.md §5.3` cable-grade-OS caveat pointing here and to §13.1.
8. All §8 tests pass; `/audit-lite` then `/audit-team`+`/walkthrough` reach 0/0/0/0/0.
9. Honest claim boundary holds: SDI build is "designed/sized," not "SDI-proven," until rung 3
   hardware evidence (MASTER §13.2) exists.

---

## 10. Dependencies & cross-refs to other sections; Open decisions for Scott

**Depends on (already in tree):** `civiccast/platform/hardware.py` (extension point);
`egress/sdi_relay.py` `SdiReadiness`; `egress/compliance.py` `TsduckStatus`;
`installer/models.py` `StationProfile`/`BackupStatus`/`DeploymentProfile`/`SafeToBroadcastColor`;
`stream/_ffmpeg.py` ffmpeg detection; `ai_runtime/ollama_client.py` (loopback model probe);
`auth/` 5-role gate.

**Feeds / cross-refs:**
- **S15 (Playout engine — GStreamer)** is the engine S1's `EngineReadiness`/`qualified_engine_tier`
  detect prerequisites for. S1 supplies the per-tier engine-prerequisite verdict (GStreamer +
  plugin set, GPU/OpenGL-4.5 + hardware encoder, DeckLink+BMD SDK, NDI SDK, native-OS) that S15's
  commissioning (S15 "Dependencies": "S1 — StationBoxProfile — GPU/DeckLink detection per tier")
  and per-tier pipeline selection consume. S15 owns the engine; S1 owns the box-fitness verdict.
- **S3 (Commissioning wizard)** consumes `StationBoxProfile` in its "First-run cable checks" step
  (S3 §1 lists hardware/OS/DeckLink/TSDuck/clock checks) **plus the engine-readiness lines** (S15
  installs GStreamer + plugins + SDKs per tier — S3 renders S1's engine verdict); S1 supplies the
  data, S3 the UI.
- **S2 (Headend handoff)** owns the headend interface detail S1's `NetworkReport.headend_interface_hint`
  defers to.
- **S8 (Health/alerting)** polls `/api/station-box-profile/readiness` for the "safe-to-air" /
  off-air alerting inputs.
- **S11 (Captions/loudness/EAS)** uses `FfmpegFeatureReport.has_loudnorm` and the per-headend
  loudness target (−24 LKFS cable vs −16 LUFS streaming) — S1 reports the capability, S11 sets policy.
- **S13 (AI model selection)** consumes `AiDefaultSelection` as the *default*; S13 builds the
  operator's per-feature registry + Ollama-Cloud/OpenRouter escalation. S1 must not encroach on the
  selection UI.
- **MASTER §10 step 2** (SDI-proven) gates the Cable/SDI tier's rung-3 claim on DeckLink hardware
  (MASTER §13.2).

**Open decisions for Scott:**
1. **Cable-grade OS (MASTER §13.1):** does the soak result let S1 print
   `single-windows-pc-ok` for 24/7 cable, or does `spec.md §5.3`'s native-Linux caveat stand for
   the cable profile? S1 ships the verdict as `soak-pending` until you decide; the field is
   designed to flip on your call with no code change beyond the threshold constant. *(Decide on
   soak completion ~20:15Z.)*
2. **DeckLink model (MASTER §13.2):** which Blackmagic DeckLink card to standardize the reference
   Cable/SDI tier on (e.g., DeckLink Mini Monitor 4K vs a Duo/Quad for multi-channel)? It must be a
   card the GStreamer `decklinkvideosink` supports via the BMD Desktop Video SDK (S15 §4). This is
   the only physical action gating rung-3 SDI proof and it pins the build matrix's last-mile line.
3. **Cable/SDI tier RAM floor:** S1 recommends **32GB** as the floor so the `gemma4:12b` summary
   default (§6.1) holds with broadcast encoding headroom. Confirm 32GB-floor (recommended) vs
   allowing a 16GB cable build that still gets 12B but with tighter headroom.
4. **AI-default RAM threshold:** §6.1 flips to 12B at **≥16GB system RAM** per MASTER §8's
   Google-stated figure. Confirm 16GB is the trigger, or set a more conservative 24/32GB trigger if
   you want CPU-batch summary tok/s headroom before defaulting up. *(Recommend 16GB to match
   MASTER §8.)*
```
