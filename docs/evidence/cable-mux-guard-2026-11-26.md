# Cable mux arrival guard — 2026-11-26

Change: PR 400 (`fix/cable-mux-arrival-guard`). The only source file changed is
`civiccast/egress/gst/engine.py`. Every TS mux now guards against a
future-dated closing video frame.

This is current-source evidence. It is NOT installed-service, boot or
pre-login evidence, and it does not renew earlier spike or installed-service
evidence.

## Source tests

`tests/egress/test_gst_engine_cable_mux_guard.py` — 4 tests. They fail on the
old engine and pass on the new one.

## Station run

Six Windows test stations, cable output on all three channels (one station had its government channel stopped), running from
2026-11-25 22:45 to 2026-11-26 10:28 (about 12 h):

- 0 worker crashes.
- 51 future-dated cable video arrivals dropped by the guard (some 1800 s ahead).
- 0 audio-only cable stretches.

## Before the fix

On 2026-11-25 one station crashed 3 times with `gstudp.dll` `0xC00000FD`
(stack overflow).
