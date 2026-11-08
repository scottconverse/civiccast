# Sprint 3 — output completion

> Historical implementation record. The checkpoints below describe work at that time.
> These features subsequently shipped in Beta 16 and remain in Beta 19; use the
> [current published package record](../releases/v1.0.0-beta.19-verification.md)
> for that package identity and its results. Earlier unreleased/pending statements
> are historical, not current outstanding tasks.

Status: implementation and focused verification complete; CI and merge are tracked in PR #247 (https://github.com/scottconverse/civiccast/pull/247). No release or package claim.
Branch: `codex/sprint3-output-completion`, based on Sprint 2 merge `eb1b5e5b9753d468dec0eeccea76e0ed31f51584`.

## Authorized scope

8. Complete RTMP output, per-output loudness and configured alert-tone filtering in the actual GStreamer broadcast path. Selecting each feature must affect the emitted stream.
9. Preserve available closing picture and sound across normal programme transitions when audio/video end at different times; retain existing short-programme protection.
10. Coordinate HLS playlist publication and segment lifetime so a published playlist cannot reference a segment already removed by the muxer.
11. Make recorded-caption languages and completion requirements explicit station settings, including English-only and disabled operation. No new numerical release gate or blanket legal mandate.

## Delivery

Use existing focused tests and small runtime checks at the affected output boundary, consolidate corrections, update affected operator and technical documentation, then push and merge on green. No installer, tag, release, production cutover, soak or generalized test harness is included.

Luna Extra High workers own output branches, endings/HLS, and recorded-caption settings respectively. The coordinator owns integration and documentation; difficult problems may escalate to Sol 6.1 Medium. Changes affecting stream ordering, segment deletion and persisted station settings receive focused independent review.

Completion requires evidence for all four items, not merely graph construction or configuration acceptance. Exact check results and limitations will be recorded under `.agent-runs/native-windows/sprint3-output/evidence/`.

## Integration status

- Item 8: the actual engine graph emitted H.264/AAC to a local RTMP receiver.
  Emitted audio measured -24.90 LUFS for cable and -16.80/-16.90 LUFS for
  streaming. Isolated tone checks measured about 38 dB attenuation at 853 Hz
  and 44 dB at 960 Hz. The pinned GStreamer 1.28.5 wheels contain the required
  processing plugins; runtime closure and per-file provenance are updated.
  149 focused checks passed. These are isolated source-runtime checks, not a
  package claim. See `evidence/outputs.md`.
- Item 9: handoff waits for both natural EOS events and fills the shorter
  stream with its final picture or silence while preserving the longer tail.
  Native emitted-TS checks passed both 2.4/4.0-second and 4.0/2.4-second
  video/audio cases, plus four focused ordering checks. Independent review
  found shutdown and pacing defects, corrected before the final native pass.
  The existing ordering module now passes 128 checks, with two runtime-only
  diagnostics skipped in that invocation. Legacy shorter-end expectations and
  incomplete abort fixtures were reconciled; production initializes the events
  before exposing pending state. See `evidence/eos.md` for the correction trail.
- Item 10: publisher-controlled segment retention passed 24 focused checks
  and one 16.47-second real FFmpeg/HTTP/ffprobe check. Live HTTP playlist reads
  now copy a complete snapshot under the publisher's shared lock; the combined
  media-router and relay checks passed 41/41, including concurrent publication
  and fresh/dead relay retention. Startup withdraws the playlist first and
  starts new segment numbering beyond retained files. See `evidence/tails.md`
  and `evidence/hls-snapshot.md` for exact commands and bounded retention.
- Item 11: settings, staff API, UI and worker/enqueue wiring are implemented
  in the working tree. Focused checks passed: 119 Python checks, 26 Station
  Profile UI checks, and TypeScript compilation. Integration review found and
  corrected a CDN retry problem when an already-published English track is
  edited without changing its cue count; four focused follow-up checks passed.
  Operator and technical documentation now explain the conditional holds.

OpenAPI, generated TypeScript API types and the API reference include the
recorded-caption settings endpoints. The assembled manual, PDF, Word document,
output diagram and built-in Help were regenerated. The renderer completed with
MiKTeX's existing administrator-update notice. No installer, tag or release was
created.

## Verification boundaries

- Runtime output checks used pinned plugins in a temporary overlay, not a new
  installed package. RTMP capture preceded the final `streamable=true` property
  correction, which was checked against native factory metadata and a graph assertion.
- Unequal-tail output checks used the existing audio chain. The new normalizer's
  declared latency is integrated into drain handling and was reviewed, but that
  combined transition was not separately captured. Unsupported final-buffer
  padding logs a warning; a gap remains possible on that exceptional failure path.
- HLS synchronization is process-local. Segment grace is bounded, and external
  file locks are logged; clients retaining old playlists beyond the grace are
  not guaranteed indefinite segment availability.
- Recorded-caption languages remain English and optional Spanish. All attached
  tracks still require operator review.
