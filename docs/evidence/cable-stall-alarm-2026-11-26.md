# Cable output stall alarm — current-source evidence (2026-11-26)

Branch: `feat/cable-output-stall-alarm` (based on `origin/main` fe20adce).
Change: commit 152b2afd, `civiccast/egress/gst/engine.py` only (+193 lines), and
a new test file `tests/egress/test_gst_engine_cable_stall_alarm.py`.

## What changed

The stall watchdog now also watches every mpegtsmux other than the watched web
preview mux (for example the cable `udp-ts` mux). If such a mux's audio pad
keeps advancing while its video pad has not advanced for `stall_timeout_s`,
after a 60 s grace from arming and never during a reload commit, the engine
prints a `CTRL stall:` line and quits for a daemon restart — the same action as
the existing watchdog.

## Current-source proof for these bytes

- `tests/egress/test_gst_engine_cable_stall_alarm.py`, 5 tests.
- On the old engine, 3 of those 5 fail; on the new engine, all 5 pass.
- The existing stall tests in `tests/egress/test_gst_engine_reload_commit_ordering.py -k stall` still pass.
- All of the above are unit tests that use a fake Gst only.

## Not proven here

- Not run on a real GStreamer runtime.
- Not run on a station, an installed service, boot or pre-login.
- Historical spike and installed-service evidence is not renewed by this rebind.

## Blob binding

`civiccast/egress/gst/engine.py` in `docs/claims/claims.yaml` was re-hashed with
`git hash-object --path civiccast/egress/gst/engine.py civiccast/egress/gst/engine.py`
per D2, for the `native-decision-gate` and `session0-service-broadcast` claims.
Other bound files are byte-identical and keep their recorded blobs.
