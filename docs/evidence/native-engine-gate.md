# Decision-gate evidence — native Python media engine (charter §2 gate)

**Date:** 2026-07-17 · **Machine:** Windows 11 Pro 10.0.26200, developer box
(confidence class 3) · **Runtime:** stock CPython 3.12.13 venv +
`gstreamer-bundle==1.28.5` (pip) · **Engine code:** `civiccast/egress/gst/`
worker.py + engine.py + graph.py at integration-branch HEAD, **zero
modifications** — the exact files WSL production runs.

## Current interpretation (2026-09-08)

The July17 experiment and its original verdict below are historical, not a
statement about the current runtime or release. Later native caption/reload
tests are recorded separately in
[the beta.5 source-level record](../../.agent-runs/native-windows/beta5-caption-flow-20260908/evidence/verification.md).
Those tests are not installed-service, boot/pre-login, or field acceptance.
The registry's current code hashes are change-detection inputs for source
review; they do not identify the original spike's bytes. The historical D6
trust-root language below is not an active owner-approval requirement.

RC2 source review (2026-10-30): the engine/graph changes add selected-source
metadata and output-boundary observations. Focused native three-source and
prerolled replacement checks passed in the development runtime. The registry
bindings were renewed for these changed files and tests; this does not renew
the historical hardware result or establish installed RC2 acceptance.

PR291 current-source review (head `2f2dd2a5c6a65c074e9b4f8cb5447a8c12a33560`):
the engine delta from the prior reviewed `e8b56185b22565ab32a54d5882356ad141e56435`
adds only a simultaneous native-PTS/UTC sample to the existing private selected-source
receipt. The source is copied before adding metadata; the existing selection,
output-fence and log-flush behavior remains. Optional clock sampling failure is
contained and cannot suppress the original source-output observation. Current engine
blob: `b2edf5168b72638ae8beed3ffab9acb4e61b1501`.

The affected selected-frame/daemon/publisher source checks passed as recorded in
`docs/handoff/CURRENT.md`. Required Windows WS4 job `125057095871`, run
`41376103989`, completed successfully on that exact head, including native media
and installer/filesystem checks. Unit in the same run failed on these stale source
bindings; its downstream native-pure suite did not execute. This review renews only
current-source drift bindings. It does not renew July hardware observations or
establish current installed-service, boot/pre-login, viewer-label or speech/caption
timing acceptance. Those remaining PR291 installed outcomes are still pending.

## Gate behaviors (charter: switching, recovery, multichannel)

1. **Hot source-swapping** — `SWAPS=4 INTERVAL=2 python worker.py demo-graph.json`
   → `WORKER_RESULT {'swaps': 4, 'error': None, 'teardown_clean': True}`,
   exit 0, 4,163,072-byte TS; ffprobe: h264, 10.5s; full decode via
   `ffmpeg -f null` with ZERO errors.
2. **Crash recovery** — worker hard-killed mid-broadcast after 6s
   (3,312,372 bytes written): process reaped in **0.00s, no hang, no
   zombie** (`os._exit` teardown held natively); immediate relaunch produced
   3,316,132 bytes in the next 6s. The pathology the WSL keeper existed to
   babysit does not manifest natively.
3. **Multichannel** — three concurrent engine processes, 8 swaps each,
   distinct outputs: all `{'swaps': 8, 'error': None, 'teardown_clean':
   True}`, exits 0, ~11.4 MB each, 18.9s wall clock.

## Verdict input (per charter §2 decision gate)

The Python engine meets switching/recovery/multichannel natively with zero
porting cost. **Gate verdict: the Python engine is validated** for hot
source-swapping, crash recovery, and multichannel operation (the three
behaviors above); recommendation is to retain the Python media worker — the
Rust `gstreamer-rs` port is closed as unnecessary (remains the recorded
fallback if soak/session-0 evidence later demands it). This verdict is
machine-bound to evidence via `docs/claims/claims.yaml`, pending owner
trust-root acceptance (D6) — the correct honest state until Scott accepts
the pin. <!-- claim:native-decision-gate -->

## Out of scope here (remaining gate items, tracked)

Session-0 service execution (next spike) · packaging/minimal closure ·
memory/soak stability · clean-machine · live-peer/hardware sinks.
Commands and raw outputs preserved in the program transcript. CORRECTION
(2026-07-17): this file originally said "the committed demo-graph JSONs" —
no graph JSON was committed with this evidence. Reruns are one command each
from `graph.demo_test_graph()` (which generated the gate's graphs), or from
the equivalent graph now committed at
`../spike-session0/evidence/demo-graph.json`.
