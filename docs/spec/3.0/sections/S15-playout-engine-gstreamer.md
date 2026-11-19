# S15 — Playout Engine (GStreamer)

> **Current implementation clarification (Sprint 6, October 17, 2026):**
> the implemented swap controller is `input-selector`; `fallbackswitch` is
> not shipped in the installed runtime. `hlssink3` is available, but live HLS
> continues through the supervised relay. The older design wording below is
> historical; see [the current alternatives assessment](../../../ops/sprint6-gstreamer-alternatives.md).

> **BUILD STATUS: BUILT & PUSHED — master §10 step 1, code-complete + machine-verified, re-opened and
> finished with nothing deferred (2026-06-14, `cb634db` on `work/3.0-gstreamer-engine`).** The engine
> (`civiccast/egress/gst/`): persistent pipeline, element-factory build (no `parse_launch`),
> `input-selector` hot-swap, seamless content-reload on the live pipeline (build new program leg →
> switch on first buffer → dispose old, with a watchdog/abort/supersede + bus-error containment), live
> source leg (srt/udp/rtmp/rtsp/http → decodebin), time-bounded teardown + force-exit, A/V + headend
> CBR/UDP sinks. Selected via `CIVICCAST_EGRESS_ENGINE`. WSL-gated live harness (`tests/egress/
> test_gst_engine_wsl.py`, 11 tests: 0 CC + monotonic PCR across swap/reload/A-V/live-ingest, leak-flat,
> watchdog recovery). Re-audited to 0/0/0/0/0. Report: `Desktop\Code\civiccast-stage1-report.md`.
> **Only named deferral:** the operator live-takeover CONTROL surface (instant-cut `live` role + switcher
> UI) → S16 (build step 9). The build spec below is retained as the design of record.

> **Status: architecture keystone (Scott-confirmed 2026-06-13).** This section defines the
> CivicCast playout/compositor/output **engine**, built on **GStreamer**, replacing the
> per-segment ffmpeg-relay. It **supersedes** the egress *engine* layer; the orchestration
> *above* the engine — program log, scheduler/automation, headend profiles, channel/health
> models, Commit-to-Air (S4), Force Matrix (S5) — **ports over unchanged** (it drives the new
> engine instead of spawning ffmpeg). GStreamer is the engine across **all tiers**; CasparCG
> is demoted to an **optional** premium-rich-CG co-process only (see §5).

> **🔧 DECISION — SWAP MECHANISM RECONCILED (2026-06-14, Stage-0 result; amended October 17, 2026).** The shipping hot-swap
> mechanism is **`input-selector` only** (`fallbackswitch` is not shipped in the installed
> runtime — see the clarification at the top), **not**
> GstInterpipe. Stage 0 validated `input-selector`: **0 MPEG-TS continuity-counter errors across 40
> source swaps, flat RSS, clean teardown** — the #151-fix premise holds. **GstInterpipe (called
> "primary" in §3 and gated in §9 below) is demoted to an OPTIONAL future enhancement**: its external
> source build is unauthorized and the plugin is unproven (open issues #56/#99). Read §3's interpipe
> design as the optional path; **what §9 calls "Plan B (a)" is now Plan A.** The engine ships on
> `input-selector` behind a pluggable `SwapController`; GstInterpipe can drop in later iff Scott
> authorizes the build and it wins a head-to-head.

---

## 1. Goal & PEG automation rationale

A single, owned, **Apache-license-clean** playout engine that runs 24/7 on a commodity box and
scales to broadcast SDI — replacing the brittle "spawn ffmpeg per segment" relay that caused the
soak's #151 failure. incumbent PEG platform does this with a proprietary VIO appliance; CivicCast does it with
**GStreamer (LGPL core + gst-plugins-rs MPL)** on commodity hardware, with every output path
(IP-TS / SRT / HLS / NDI / SDI) in one engine. Real 24/7 broadcast playout on GStreamer + DeckLink
SDI is **proven prior art** (Ideal World TV ran two 24/7 channels this way; BBC R&D "Brave" is a
REST API over persistent GStreamer pipelines) — we are not pioneering the pattern, only the
PEG-product layer on top.

## 2. Current state (what this replaces)

- **`civiccast/egress/` ffmpeg-relay** (CA-1..CA-8): the daemon spawns/tears-down ffmpeg **per
  segment**, which resets the MPEG-TS continuity counter at program/filler boundaries → **#151**
  (the 24h soak's FAIL cause). `sdi_relay.py` / `ndi_relay.py` are BYO-ffmpeg relays (fragile;
  NDI needs a non-distributable patched ffmpeg). CG is render-contract-only (no real compositor).
- **What survives and ports over (do not rebuild):** `programlog/` (slots→schedule materialization),
  the scheduler/automation (`egress/automation.py` logic), `egress/headend.py` profiles, the
  channel/health models, Commit-to-Air (S4), Force Matrix (S5). These become the **control plane
  that drives the GStreamer pipeline** instead of driving ffmpeg.

## 3. Architecture — persistent pipeline + hot-swap (the #151 fix by design)

One **persistent GStreamer pipeline per channel.** Two halves:

```
[ SOURCE SIDE — hot-swappable, never blocks output ]     [ OUTPUT SIDE — stays in PLAYING forever ]
 program  ─ interpipesink ┐
 filler   ─ interpipesink ┤   interpipesrc ─► compositor ─► encoder ─► mpegtsmux ─┬─► udpsink (UDP-TS)
 live     ─ interpipesink ┤   (listen-to)      ▲ (CG layers)                       ├─► srtsink (SRT)
 slate    ─ interpipesink ┘                    │                                   ├─► hlssink (HLS)
 CG/overlays ─────────────────────────────────┘                                   ├─► ndisink (NDI)
                                                                                   └─► decklinkvideosink (SDI)
```

- **Hot-swap = GstInterpipe** (`interpipesrc`/`interpipesink`, RidgeRun, **LGPL-2.1**, actively
  maintained). Each source (program / filler / live / slate) is its **own independent pipeline**
  feeding an `interpipesink`; the output pipeline's `interpipesrc` selects which one via the
  `listen-to` property — **at any time, no teardown, no EOS/valve/selector gymnastics.** The
  scheduler just sets `listen-to`.
- **The #151 fix:** the output half (`compositor → encoder → mpegtsmux → sink`) **stays in PLAYING
  continuously** while sources swap upstream. The mux never restarts → **PCR + continuity counters
  are unbroken** → the per-segment-teardown bug cannot recur.
- **Glitch-free swap rules** (the make-or-break — see §9): swap on the **RAW (pre-encode) side**
  with **identical caps** (CivicCast already conforms sources to a common format at ingest via
  `preparer.py` — this is what makes clean swaps tractable); re-base timestamps with
  `interpipesrc` `stream-sync` (`compensate-ts`/`restart-ts`) so the mux sees monotonic PTS; keep
  the encoder running so no uncontrolled `DISCONT` reaches the mux; compose audio+video together so
  they swap atomically.

## 4. Outputs / sinks (all Apache-friendly licensing)

| Output | Element | Package / license |
|---|---|---|
| UDP MPEG-TS (headend) | `udpsink` (+ `mpegtsmux`) | gst-plugins-good / LGPL |
| SRT | `srtsink` | gst-plugins-bad / LGPL |
| HLS (web/VOD) | `hlssink3` (Rust) / `hlssink2` | gst-plugins-rs MPL / -bad LGPL |
| NDI | `ndisink` | **gst-plugins-rs / MPL-2.0** (NDI SDK 5/6 at runtime) |
| **SDI (Blackmagic)** | **`decklinkvideosink`** | gst-plugins-bad / **LGPL** — fill+key via `keyer-mode`+`duplex-mode`; needs BMD Desktop Video SDK at runtime |

**No GPLv3 anywhere in the output path** — SDI and NDI are LGPL/MPL. Proprietary *runtime* SDKs
(BMD Desktop Video for SDI, NDI SDK for NDI) are installed, not redistributed as code. Genlock/
reference for SDI is a card/driver-level setting (verify at the BMD level — not a documented
GStreamer property; bench-test).

## 5. CG / graphics (tiered)

- **CG-lite (base tier, CPU):** `compositor` (gst-plugins-base; software layer blend, alpha,
  z-order, `ignore-inactive-pads` for live) + `textoverlay`/`clockoverlay`/`timeoverlay` +
  `gdkpixbufoverlay`/`overlaycomposition` (lower-thirds, crawls, fullscreen bulletins, clock,
  logo/bug). Covers the large majority of PEG CG with no GPU.
- **CG-rich (premium, GPU-leaning):** `wpesrc` (WPE WebKit) renders **HTML/CSS/JS templates** as a
  live video source — animated lower-thirds, agenda overlays, motion graphics. Runs headless/
  software (`LIBGL_ALWAYS_SOFTWARE=true` + WPEBackend-FDO) but is CPU-heavy; GPU is the comfortable
  path. This is CivicCast's HTML-CG runtime; templates are our content (not GPL).
- **CasparCG = optional, premium only:** for stations wanting a mature designer-driven broadcast-CG
  ecosystem, CasparCG runs as a **separate GPLv3 co-process**, bridged in via NDI/SDI (and AMCP
  control), **quarantined to that tier** — never required, never linked into the Apache core.

## 6. Encoders (tiered, license-aware)

- **Broadcast/SDI tier → hardware:** `nvh264enc`/`nvh265enc` (NVENC) or `vah264enc`/`vah265enc`
  (VA-API) — **LGPL plugins, broadcast quality, Apache-clean.** Primary path where a GPU/QSV exists.
- **Base/CPU tier →** `openh264enc` from the CivicCast-bundled runtime for the public
  beta default. Operators who install and accept GPL x264 themselves may explicitly
  select `x264enc`; CivicCast does not ship that plugin.

## 7. Bindings & control plane

- **PyGObject** drives the engine from CivicCast's Python/FastAPI control plane. The GIL is
  **released during every GStreamer C call**, so Python only issues control (set `listen-to`, add/
  remove CG, read the bus) while the media runs in native streaming threads — the proven "Python
  orchestrates, C does the heavy work" model.
- **Discipline (required):** GStreamer callbacks fire from arbitrary streaming threads — marshal any
  shared-state access back via `GLib.idle_add()`. Keep **no per-buffer/frame-rate logic in Python**
  (do it inside elements, or a small Rust element via `gst-plugins-rs` if profiling demands).

## 8. Tiers (resolves the "hardware-play" concern)

| Tier | Hardware | Engine config |
|---|---|---|
| **Base / streaming** | commodity CPU box, **no GPU**, WSL2-fine | GStreamer CPU: IP-TS/SRT/HLS + CG-lite. The "$5K commodity PC" promise, intact. |
| **SDI / broadcast** | + DeckLink card + **native OS** (PCIe can't passthrough WSL2) + GPU for rich CG | adds `decklinkvideosink` (fill+key), hardware encode, WPE rich CG |
| **Premium CG** | + CasparCG (GPU) | optional GPLv3 CG co-process bridged via NDI/SDI |
| **Production / control room** (optional, S16) | + a Node TSR sidecar; OBS/vMix/ATEM as the operator's switcher | CivicCast UI over TSR drives the switcher; its program feed enters the engine as a live source |
| **Remote contribution** (optional, S17) | + VDO.Ninja (self-hosted) + coturn TURN | browser WebRTC guests → compositor hop → engine; no extra broadcast hardware |

## 9. Proof tier + the make-or-break risk gate

- **Current rung: 0 (contract).** Target: 1 (lab) on the bench test, then 2 (machine) via the soak.
- **⚠ VALIDATED RISK (pre-build, 2026-06-14).** GstInterpipe is the right *native* choice for 4-way
  raw-domain switching, but it is **not proven for 24/7 unattended source-swap**: RidgeRun issues
  **#56 (audio-sync glitch on swap, open 6+ yrs)** and **#99 (memory corruption on re-listen, open
  4+ yrs)** are unresolved, and **no published 24/7 case study uses it** — our cited prior art (Ideal
  World TV) actually *avoided* interpipe, joining separate pipelines via UDP multicast. GStreamer also
  does **not explicitly guarantee** mpegtsmux continuity-counter persistence across an upstream
  hot-swap (the #151-fix premise) — FFmpeg's segment muxer famously got exactly this wrong. **So the
  prototype below is a true GO/NO-GO gate and a Plan B is pre-specified.**
- **THE GATING PROTOTYPE (build task #1) — hardened pass criteria.** Prove GstInterpipe seamless
  raw-domain source-swap on **real CivicCast content** (program→filler→live→slate) over a
  **multi-hour automated cycling run** (not a single swap). PASS requires ALL of:
  1. **TS continuity:** 0 MPEG-TS continuity-counter errors across **every** swap — instrument
     per-PID CC and TSDuck-verify; do **not** assume mux state persists (explicit #151 re-test).
  2. **Audio:** no audible blip/skip and A/V sync within tolerance across N swaps (the issue-#56
     failure mode) — measure the sync offset and gate on it.
  3. **Memory:** flat RSS over the multi-hour run, no unbounded growth (the issue-#99 failure mode).
  4. **Caps:** no caps-renegotiation glitch (`allow-renegotiation=false`; pre-verify all source caps match).
  Clean on all four → commit the re-platform. Any failure → **Plan B; do NOT force it.**
- **Plan B (pre-specified so a prototype failure is a pivot, not a crisis), in preference order:**
  (a) gst-plugins-rs **`fallbackswitch`** for the program↔filler path (better-maintained, Rust/MPL) +
  `input-selector` for live takeover — loses elegant 4-way unification but is more robust;
  (b) **separate pipelines joined via UDP multicast** (the Ideal World pattern — switch at the TS
  layer), which sidesteps interpipe entirely; (c) **fork GstInterpipe + fix #56/#99** (last resort).
  GStreamer stays the engine in every branch — only the *swap mechanism* changes. **If the prototype
  fails, write a report with the failure data + the recommended Plan-B branch and PAUSE for Scott —
  do not pivot silently.**
- **PyGObject control** is safe (GIL releases during C calls; swaps are seconds-granularity, not
  frame-accurate); marshal swap commands via `GLib.idle_add()`.
- **Prior art:** BBC Brave (REST over persistent GStreamer pipelines, Apache-2.0); Ideal World TV
  (24/7 GStreamer + DeckLink SDI) — note Ideal World validates **Plan-B (b)**, the separate-pipeline
  pattern, not GstInterpipe specifically.

## 10. Test plan · DONE · dependencies · open decisions

**Test plan:** (1) the seamless-swap bench (§9 hardened go/no-go criteria — 0 TS continuity errors,
A/V sync, and **flat memory over a multi-hour cycling run**); (2) persistent-pipeline soak — the 24h rerun must show clean TSDuck (this is
the #151 re-test); (3) per-sink output proof (udp/srt/hls/ndi/decklink), TSDuck on the TS paths;
(4) recovery — pipeline supervision/watchdog, clean restart on element failure; (5) CPU-load test —
3 SD/HD channels + CG-lite + encode on the reference CPU box. **0/0/0/0/0 audit.**

**DONE:** GStreamer engine drives a channel end-to-end (source hot-swap → compositor/CG → encode →
all sinks), the program log/scheduler drive it via the control plane, #151 is gone on the soak
rerun, and the base tier runs on a CPU box with no GPU/GPLv3.

**Dependencies / cross-refs:** S2 (headend matrix — outputs are now GStreamer sinks); S6 (CG —
compositor/WPE here, CasparCG optional); S9 (reliability — now GStreamer pipeline lifecycle +
co-process supervision, not ffmpeg-reap); S1 (StationBoxProfile — GPU/DeckLink detection per tier);
S3 (commissioning — installs GStreamer + plugins + SDKs per tier).

**Open decisions for Scott:** (a) WPE rich-CG on CPU vs require GPU for the rich-CG tier; (b) whether
the premium-CG tier ships at all in V1 or is a documented CasparCG hook; (c) primary encoder default
per tier (hardware vs openh264 vs x264).

**Migration:** the engine is runtime; channel/pipeline config extends the existing egress config
(no new table strictly required for the engine itself; any pipeline-state persistence reuses the
egress state tables). No new alembic head beyond what S2/S9 touch.

---

## 11. S21 capture-pipeline integration (cross-reference)

S21 (scheduled recording) ships `CapturePipelineProtocol` in `civiccast/recording/service.py` as the
seam where the S15 engine plugs in for forward-scheduled capture. The Protocol surface is the four
verbs `arm(source, profile) → capture_id`, `start(capture_id)`, `finalize(capture_id) → bytes_written`,
and `stop(capture_id)` — engine-agnostic by construction. The S15 GStreamer engine wires the
production implementation here (a capture pipeline built from the same elements the playout pipeline
uses — `decodebin` for network sources, `decklinkvideosrc` / `ndisrc` for live inputs, the configured
encoder, and a `filesink`); S21's unit tests inject a stub. This is the symmetric back-reference for
S21 §6 ("open an S15 capture pipeline") and S21 §9's cross-ref to S15.
