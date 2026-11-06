# ADR 0007 — HLS packager design: ffmpeg subprocess, ABR ladder, slate-as-variant

**Status:** Accepted
**Date:** 2026-05-08
**Deciders:** Scott Converse (human director)
**Related rung:** 0.2 — Streaming origin
**Related spec section:** §5.1 Backend stack (FastAPI + ffmpeg + PyAV), §8.2 civiccast-stream, §16.1 Broken-media regression suite, §10.5 CDN tier
**Supersedes:** N/A
**Superseded by:** N/A

---

## Context

Spec §8.2 names `civiccast-stream` as the streaming origin: a software-only encoder/packager that produces canonical HLS output, owns the broken-media slate fallback (§16.1), and publishes to the configured CDN. Sprint 0.2 lands the VOD path of this module — an asset on disk gets transcoded to an adaptive HLS ladder and uploaded to a CDN; the public portal player consumes the resulting manifest. Live ingest (RTMP / RTSP / NDI / SRT) lands at Sprint 0.4; captions inline at Sprint 0.5; full live source-switching at Sprint 0.4 onward.

Three implementation questions need to resolve before code lands:

1. **Implementation language.** Spec §8.2's D1-revised lists Python and Go as both viable for the streaming origin since the frame-accuracy budget softens to "one HLS segment boundary at 2 seconds" rather than single-frame precision. Without a Broadcast Engineering WG to formally own the call, we make it as a Sprint 0.2 design choice.
2. **HLS packaging tool surface.** ffmpeg is the obvious encoder; the question is whether to call it via subprocess, via a Python binding (PyAV / ffmpeg-python), or via a higher-level HLS toolkit (Bento4, Shaka Packager). Each option has different operational footprint, error surface, and version-pinning ergonomics.
3. **Slate fallback shape.** Spec §16.1 describes the slate as "rendered as a plain HLS variant." The packager needs to either (a) generate the slate manifest as a sibling variant in every output, always available; (b) generate it on-demand when a broken-media event is detected; or (c) maintain a single shared slate stream that all players fall back to.

Independently, the ABR ladder is fixed by spec §8.2 at 1080p / 720p / 480p / 240p; encoding parameters per rendition are not.

## Decision

The civiccast-stream module ships as **Python**, calling **ffmpeg via subprocess** with a thin typed wrapper, producing **a four-rendition ABR ladder** (1080p / 720p / 480p / 240p) plus a **fifth always-present "slate" variant** in every HLS multivariant manifest. The slate variant is generated once per output set, served from the CDN alongside the actual content variants, and the player falls back to it via standard HLS variant switching when the actual content fails to play.

Sprint 0.2 ships only the VOD path (`pack_vod_asset(input_path) -> hls_output_directory`). Live ingest is Sprint 0.4.

## Alternatives considered

### Implementation language

**Option A — Python.** Consistent with the rest of CivicCast (CLI, FastAPI, civiccast.platform). The streaming origin ships as a Python module under `civiccast.stream` and invokes ffmpeg via subprocess. Type hints throughout, runs under the same uv workspace as everything else, gets the same pre-commit hooks, ruff, mypy strict, pytest. **Selected.**

**Option B — Go.** The spec acknowledges Go is also viable. Rejected because (a) it would fork the project's language story (operators and contributors now need to know two stacks); (b) the frame-accuracy budget for streaming-first does not require Go's lower latency; (c) cross-module type sharing (Pydantic models in HardwareProbe et al.) gets cleaner when streaming-origin events go through the bus as Pydantic models, not Go protobuf or JSON-with-validators-on-both-sides. Future Sprint 0.4 (live) revisits if live performance demands it.

**Option C — Rust.** Spec §8.2 explicitly retires the Rust path from v1 because the frame-accuracy budget no longer requires it. Rejected automatically.

### HLS packaging tool surface

**Option A — ffmpeg via subprocess with a thin typed wrapper.** Most operational simplicity: the operator already has ffmpeg installed (it's a Sprint 0.2 dependency at the OS level), and CivicCast just constructs argument vectors and reads ffmpeg's stderr for progress and error parsing. Versioning is decoupled from Python: ffmpeg upgrades don't bump civiccast-stream. **Selected.**

**Option B — `ffmpeg-python` (Python wrapper around subprocess).** Same underlying mechanism but adds a fluent-API layer. Rejected because (a) the wrapper is a thin layer that adds another dependency for marginal ergonomic improvement; (b) ffmpeg's stderr parsing for progress reporting is something we want full control over.

**Option C — PyAV.** Direct libav* bindings. Better fine-grained control over individual frames, no subprocess overhead, no stderr parsing. Rejected for Sprint 0.2 because the segment-boundary accuracy budget doesn't require frame-level control, PyAV's installation surface is heavier (libav system libraries pinned to specific versions), and the operational model — operators inspecting ffmpeg invocations and tweaking flags — is a known quantity. PyAV may revisit at Sprint 0.4 (live) if frame-accurate live source switching demands it.

**Option D — Bento4 / Shaka Packager / mp4dash.** Higher-level HLS-specific toolkits. Rejected because they add a separate binary the operator must install on top of ffmpeg, and they don't transcode — they only repackage. We need transcoding (input → 4-rendition ladder), so ffmpeg is required either way.

### Slate fallback shape

**Option A — Always-present slate variant in every HLS multivariant manifest.** Every VOD output's manifest includes 5 entries: 1080p, 720p, 480p, 240p, slate. The slate is a low-bitrate plain-color (CivicCast brand frame) variant with simple text "We are experiencing technical difficulties." HLS players naturally switch to the lowest-available variant when higher ones fail, so the slate becomes the bottom of the ABR ladder. The player only ever displays the slate when *every* content variant has failed, which is exactly the "broken media" condition. Generated once per asset; ~2 MB per output. **Selected.**

**Option B — On-demand slate generation when a broken-media event fires.** Detect the failure, regenerate or fetch the slate, swap manifests at runtime. Rejected because it's a control-plane race: the player has already received the manifest; rewriting it requires a manifest-server roundtrip and sometimes a player reload. The always-present-variant approach uses standard HLS player behavior with no control-plane intervention.

**Option C — Single shared slate URL referenced by every output's manifest.** One slate stream lives at a known CDN path; every asset's manifest points to it as a sibling variant. Rejected because (a) it couples assets to a single shared resource that, if unavailable, breaks fallback for every asset simultaneously; (b) operators may want per-channel or per-event slates ("Channel 1 — please stand by") and that becomes a config nightmare; (c) the storage cost of a per-asset slate (~2 MB) is negligible. Worth revisiting at Sprint 0.10 if CDN storage costs become a real number.

### ABR ladder configuration

The four content renditions are:

| Rendition | Resolution | Video bitrate | Audio bitrate | H.264 profile |
| :---- | :---- | :---- | :---- | :---- |
| 1080p | 1920×1080 | 4.5 Mbps | 128 kbps | high |
| 720p  | 1280×720  | 2.5 Mbps | 128 kbps | main |
| 480p  | 854×480   | 1.0 Mbps | 96 kbps  | main |
| 240p  | 426×240   | 350 kbps | 64 kbps  | baseline |

Rationale: tuned for residential broadband viewers in 2026 (median US home connection ≈ 250 Mbps down per FCC data; mobile users on LTE / mid-range 5G drop to ~5-15 Mbps in real-world conditions). The 240p rendition handles cellular users in poor coverage and the slate fallback is a sibling at lower bitrate. Sprint 0.2 ships these as defaults; the per-channel ladder configuration named in spec §8.2 ("Adaptive bitrate ladder is configurable per channel") lands at Sprint 0.3 with the schedule module.

The slate variant: 426×240, 200 kbps video, 32 kbps audio (or muted with periodic 1 Hz beep), H.264 baseline profile, 2-second segments matching content variants for clean failover.

## Consequences

### Positive

- **Single-language stack.** civiccast-stream ships under the same Python toolchain as the rest of CivicCast — uv, ruff, mypy strict, pytest, pre-commit. Contributors and operators learn one stack.
- **Operational simplicity.** ffmpeg via subprocess means operators can copy the exact ffmpeg invocation civiccast-stream produces and run it manually for debugging. No hidden Python-binding behavior.
- **Slate-as-variant uses standard HLS behavior.** No control-plane intervention required. Players that implement the HLS spec correctly fall back to the lowest variant naturally; the broken-media test suite asserts this happens cleanly.
- **CDN-agnostic output.** The packager writes to a local directory; the CDN upload is a separate adapter (resolved by D16 / ADR 0006). Swapping CDN providers post-1.0 is a config change.
- **The four content renditions cover the bandwidth distribution.** Anyone with reliable home broadband gets 1080p; anyone on mobile or weak Wi-Fi gets 480p or 720p; anyone in cellular dead zones gets 240p; anyone whose stream truly breaks gets the slate.

### Negative

- **ffmpeg subprocess has stderr-parsing edge cases.** Progress reporting depends on parsing ffmpeg's stderr output, which can vary across ffmpeg versions. Mitigation: pin a tested ffmpeg version range in `civiccast doctor`'s checks; the parser is targeted at well-known progress fields rather than free-form text.
- **Slate variant is ~2 MB extra per asset.** Negligible at small-org scale; could matter at very large archive sizes (>100k assets). Revisit at Sprint 0.10 if archive storage costs become material.
- **No live ingest path in Sprint 0.2.** VOD only. Live ingest is Sprint 0.4. Operators who want to test live capture in Sprint 0.2 will have a working VOD pipeline only.

### Risks

- **ffmpeg version drift.** Different ffmpeg versions produce subtly different output (slight differences in segment boundary timing, codec parameters). Mitigation: civiccast doctor reports the detected ffmpeg version; the broken-media regression suite asserts behavior, not byte-level output; CI uses a pinned ffmpeg version installed via apt.
- **HLS player quirks.** Different HLS players (HLS.js, native iOS HLS, native Android, VLC) handle variant fallback slightly differently. Mitigation: the broken-media regression suite includes a player-fallback assertion via headless browser test in CI; mobile browsers verified manually before tagging the rung.
- **Slate generation idempotency.** If the slate generator is non-deterministic, repeated runs produce different bytes — bad for cache headers and CDN economics. Mitigation: slate generation uses fixed input parameters (color, text, duration); output bytes are content-addressable.

## Compliance

- The civiccast-stream module imports ffmpeg only via the `civiccast.stream._ffmpeg` adapter module. Other modules importing ffmpeg or invoking `subprocess.run("ffmpeg", ...)` directly are flagged in lint at Sprint 0.2 or later.
- The slate variant is part of every output set produced by the VOD packager. The broken-media regression suite (this rung's hard test) asserts the slate variant is present and that pathological inputs surface it.
- `civiccast doctor` (Sprint 0.1) is extended at Sprint 0.2 to report the detected ffmpeg version and warn if it's outside the supported range.
- The ABR ladder defaults are documented in `civiccast.stream.config` constants; per-channel overrides land at Sprint 0.3.

## Slate failover mechanism (v0.2 amendment)

The original ADR specified the slate as the lowest-bandwidth variant in the
multivariant manifest, with the implicit assumption that an HLS player would
"fall back" to the lowest-bandwidth entry only when other variants failed.
That assumption is wrong: standard HLS players (HLS.js, native iOS, native
Android) do not interpret "lowest BANDWIDTH" as "fallback." They estimate
the client's current bandwidth and select the variant whose advertised
BANDWIDTH most closely matches without exceeding it. A viewer on a slow
connection (sub-400 kbps mobile, congested wifi) would therefore be served
the slate as their *first* choice, displaying "We are experiencing technical
difficulties" over a perfectly working stream.

Two real failover mechanisms exist in the HLS spec:

1. **`EXT-X-MEDIA` failover groups** — declare alternate renditions in named
   groups; the player tries the primary first and fails over. Requires
   restructuring the manifest into media groups; player support is uneven
   in practice across HLS.js / native iOS / native Android / smart-TV
   players.

2. **`EXT-X-RENDITION-REPORT`** — players report which renditions they
   tried; the playlist server can use that to direct subsequent loads.
   Requires a stateful server endpoint, not just static files. Out of
   scope for v0.2's "static files on a CDN" deployment model.

**v0.2 mitigation (this amendment):** the slate is advertised at a
BANDWIDTH attribute of 50 Mbps (`50_000_000` bps) — well above the highest
content variant (1080p at 4.6 Mbps) and above any realistic residential or
mobile connection. The slate's *real* encoded bitrate is unchanged at ~232
kbps (cheap to deliver). Estimate-matching ABR clients will never select
the slate as a primary choice because no client measures itself at 50 Mbps
of headroom and chooses to "play it safe" by picking the highest variant
when a 4.6 Mbps option exists.

The slate is reached only when ALL content variants fail to load — the
fallback semantic this ADR originally named, achieved by a different
mechanism than the original "lowest bandwidth" plan.

**Implementation:** `RenditionConfig` gains an optional
`advertised_bandwidth_bps_override` field (default `None`); the
`manifest_bandwidth_bps` property falls through to `bandwidth_bps` for
content variants and to the override for the slate. The HLS manifest
builder uses `manifest_bandwidth_bps`. Verified by
`tests/stream/test_manifest.py::test_slate_advertised_bandwidth_above_all_content`
and `tests/stream/test_config.py::test_slate_manifest_bandwidth_is_above_all_content`.

## Slate failover mechanism (v0.3 amendment — real EXT-X-MEDIA group)

The v0.2 amendment kept the slate from being picked as a primary variant
by inflating its advertised BANDWIDTH to 50 Mbps. That is a workaround,
not a real failover mechanism — the slate appears in the manifest as a
sibling STREAM-INF, not as a declared alternate rendition. Compliant HLS
players have no signal that the slate is the failover destination; they
simply skip it because it advertises an unrealistically high bitrate.

**v0.3 implementation (this amendment):** the slate is now declared as a
proper alternate VIDEO rendition via `#EXT-X-MEDIA TYPE=VIDEO,
GROUP-ID="content", DEFAULT=NO, AUTOSELECT=NO`. Every content
`#EXT-X-STREAM-INF` carries a `VIDEO="content"` attribute that ties it
to the same rendition group. Compliant HLS players (hls.js, native iOS /
Safari, ExoPlayer) honor this construction: when a content variant's
segments stop loading, the player can switch to the slate within the
same rendition group, which is the exact failover semantic this ADR
originally named.

The v0.2 mitigation is preserved as belt-and-suspenders: the slate
ALSO appears as a STREAM-INF entry at the inflated 50 Mbps BANDWIDTH so
older clients that ignore EXT-X-MEDIA failover groups still cannot select
the slate as a primary. Both mechanisms cooperate: modern clients use
the EXT-X-MEDIA failover path; legacy clients fall through to the
bandwidth-inflated mitigation.

`#EXT-X-RENDITION-REPORT` (the second mechanism named in the v0.2
amendment) remains out of scope — it requires a stateful playlist
server endpoint, not just static files on a CDN.

**Implementation:** `civiccast.stream.manifest` exports a
`SLATE_FAILOVER_GROUP_ID = "content"` constant. The builder partitions
renditions into content + slate, emits the `EXT-X-MEDIA` descriptor
first, then the content `STREAM-INF` entries with `VIDEO="content"`,
then the slate `STREAM-INF` (preserved for legacy clients). Verified by
`tests/stream/test_manifest.py::TestSlateFailoverGroup` (5 tests).
ADR 0007 is now fully compliant with the original "fallback" semantic
without modifying any existing constants in `civiccast.stream.config`.

**Trade-off:** if a real `EXT-X-MEDIA` failover-groups implementation
arrives in v0.4 (portal-polish rung), this BANDWIDTH inflation can be
removed. Tracked in `next-cleanup.md`.

## S7 ingest-time transcode defaults and resource posture (2026-08-24 amendment)

This ADR's "Compliance" section already forbids any module outside
`civiccast.stream._ffmpeg` from invoking ffmpeg directly and, per that
module's own resolver, forbids a bare `libx264` (GPL) literal anywhere
except the resolver itself. A regression audit of
`civiccast.schedule.media_lifecycle_worker` (S7's transcode-on-ingest
worker, spec §7's "Transcode on ingest" section) found that compliance
posture violated in a second, unenforced dimension: the worker's default
transcode format seed list carried an `h265_1080p_8mbps` target whose
ffmpeg args template invoked `libx265` — GPL, and never resolved through
any encoder-selection seam at all, just a bare literal. This was possible
because `tests/policy/test_ffmpeg_h264_encoder.py`'s repo-wide sweep only
ever checked for the string `"libx264"`; it walked the whole `civiccast/`
tree the entire time, so the gap was in which GPL encoder NAME it
recognized, not which directory it covered.

**Resolution of S7 spec Open decision #1** ("Transcode format defaults:
h264_720p_5mbps (proxy), h265_1080p_8mbps (archive), h264_mezzanine
(SDI)?" — phrased as an open question, with "h264-only for simplicity"
named as the alternative): resolved to the h264-only alternative, for two
independent, compounding reasons:

1. **License.** H.264 has a real GPL-free resolution path
   (`resolve_h264_encoder()`: hardware → `h264_mf` → the royalty-free
   `libopenh264`, with GPL `libx264` reachable only when the exact probed
   binary itself already carries it). HEVC has no such path anywhere in
   this tree — no `hevc_mf`/`hevc_nvenc` resolver exists. There is
   therefore no sanctioned way to honor an HEVC transcode target today.
   Per this ADR's own compliance posture, the target is **removed**
   rather than shipped opt-in-but-broken or silently GPL-linked. A future
   ADR may add a real HEVC resolver, mirroring `resolve_h264_encoder()`'s
   shape, if operator demand justifies the engineering; nothing here
   forecloses that.
2. **Resource posture.** Independently of the license defect, the worker
   seeded every configured format unconditionally for every validated
   asset and dispatched each synchronously in its own thread, at normal
   process priority, under ffmpeg's flat 6-hour default timeout — a large
   amount of unsupervised, full-priority ffmpeg work sharing the box with
   operator HTTP requests, with no station-level way to turn it down. This
   is the same class of problem PR #45 found and fixed in the VOD
   packager's ABR ladder (a fixed-resolution ladder applied regardless of
   source resolution wasted most of its wall time on rungs the source
   couldn't fill, which is what blew `POST /api/staff/assets/{id}/package`
   past its caller's timeout) — S7's worker has the same "test every rung
   against a source small enough to already fit inside the first one"
   inefficiency, just for background proxy generation instead of a
   synchronous HTTP path.

**What changed** (`civiccast/schedule/media_lifecycle_models.py`,
`civiccast/schedule/media_lifecycle_worker.py`,
`civiccast/stream/_ffmpeg.py`):

- `DEFAULT_TRANSCODE_FORMATS` is now `("h264_720p_5mbps",)` — one web
  rendition, not three. `h264_mezzanine` (near-lossless, `-crf 12`, the
  heaviest of the three formats) remains available but is opt-in via the
  existing `CIVICCAST_TRANSCODE_FORMATS` override, not seeded by default.
- The `h264_720p_5mbps` template's scale filter is resolved at call time
  and capped at the SOURCE asset's own `height_px` when ffprobe measured
  one at ingest — the proxy never upscales past the source, the same
  never-upscale posture PR #45 gave `civiccast.stream.config.select_ladder`
  for the VOD packager, implemented locally here (this branch does not
  depend on PR #45's unmerged `stream/config.py` change).
- `MediaLifecycleWorkerSettings` gained `transcode_seeding_enabled`
  (default `True`, so first-run ingest still produces a proxy per spec
  §7/DONE criterion 3 — this is a resource-posture fix, not a silent
  removal of a DONE criterion) and an explicit, validated
  `transcode_concurrency` field fixed at `1` (the dispatch loop already
  ran jobs strictly one at a time; the field makes that fact
  station-configurable in principle and honest — it raises rather than
  silently ignoring any other value, since parallel dispatch is not
  implemented).
- Each dispatched transcode now runs at Windows `BELOW_NORMAL_PRIORITY_CLASS`
  (`civiccast.stream._ffmpeg.run_ffmpeg`'s new `lower_priority` parameter,
  opt-in and default `False` so real-time/latency-sensitive callers — live
  egress, the VOD packager answering an operator's HTTP request — are
  unaffected) and under a per-minute-of-source timeout budget
  (`_transcode_timeout_seconds`: 10 minute floor, 10x-realtime budget, 2
  hour ceiling) instead of the flat 6-hour default.
- **Amended 2026-11-05 (FT-7, RC4 field test):** `lower_priority` now maps to
  Windows `IDLE_PRIORITY_CLASS`, not `BELOW_NORMAL_PRIORITY_CLASS`. The live
  caption speech child is itself started at `BELOW_NORMAL`, so background
  transcodes and warm conforms tied with it, out-threaded it, and on the
  Beelink live captions were discarded while hour-long programmes were being
  prepared. IDLE keeps this background work strictly below the captions while
  still giving it every spare cycle. POSIX keeps `nice 10` (captions run at
  normal priority there, so the order was already right).
- `tests/policy/test_ffmpeg_h264_encoder.py`'s sweep is widened from a
  single hard-coded `"libx264"` string to a `_FORBIDDEN_GPL_ENCODER_LITERALS`
  set (currently `{"libx264", "libx265"}`), proved against a planted
  `libx265` literal shaped exactly like the real defect
  (`test_detector_flags_a_planted_libx265_literal`), with the resolver's
  narrow exception scoped to `libx264` only — a hypothetical stray
  `libx265` in the resolver file itself would still be flagged, since no
  HEVC resolver exists there to justify it.

**Not done here, named rather than silently deferred:** no operator-facing
settings UI exists for `transcode_seeding_enabled` (or any other
`MediaLifecycleWorkerSettings` field) — the config surface today is the
env-gated dataclass, the same per-station mechanism `retention_worker` and
`media_integrity_worker` already use as their own config surface, appropriate
for a single-station-per-install product but not yet an operator-visible
toggle. A dedicated settings panel is real follow-up work, not claimed done
by this amendment.
## ABR ladder selection: never upscale (v0.3 amendment)

The four-rung ladder above describes the ladder's *shape*, and the original
implementation encoded all four rungs for every source regardless of the
source's own resolution. That silently upscales: a 640x360 upload was
encoded to 1920x1080 and 1280x720, which invents pixels, spends 4.5 Mbps
carrying no additional detail, and costs the full encode time of a large
frame.

Measured on the Gate A sample clip (640x360, 67 s), the two upscaled rungs
were 14.6 s of an 18.4 s content-ladder encode — roughly 81% of the wall
time — which is what pushed `POST /api/staff/assets/{asset_id}/package`
past its callers' timeouts (the operator console and the station-acceptance
harness both call it synchronously).

**This amendment:** `civiccast.stream.config.select_ladder` chooses the
rungs before encoding.

- A source at or above the ladder's top rung gets the ladder unchanged. The
  top rung stays a deliberate product cap — a 4K source still publishes at
  1080p and below.
- A source whose height matches a rung gets that rung and everything below.
- A source between rungs (or below every rung) gets the rungs strictly below
  it, plus one rung at the source's own resolution — inheriting bitrate,
  profile and codec string from the shortest rung it outgrew — so the top
  tier is neither upscaled nor needlessly downscaled.
- When the source dimensions cannot be read, the full ladder is used. The
  packager never guesses its way into a smaller ladder.

The slate is not part of the content ladder and is unaffected: it is still
always generated first and always present in the manifest. What changes is
that "5 entries" in the *Slate fallback shape* section above is now an upper
bound rather than a fixed count.

**Trade-off:** residents watching a sub-1080p recording now see fewer ABR
choices. That is the honest outcome — the discarded choices only ever
carried upscaled copies of the same pixels — but it does mean a client on a
degrading connection has fewer intermediate rungs to step down through
before reaching the bottom of the ladder.

**Known limitation, not addressed here:** packaging remains a synchronous
HTTP request whose latency is proportional to source duration. This
amendment removes the wasted work; it does not bound the wait. A 90-minute
1080p meeting is unaffected by ladder selection (nothing upscales) and still
occupies the request for as long as the encode takes. Moving packaging to a
job-and-poll contract like the offline caption jobs is the real fix and
needs its own ADR and an owner decision, because it changes the endpoint's
response contract and every caller of it.

## References

- CivicCastUnifiedSpec-v2.md §5.1 Backend stack
- CivicCastUnifiedSpec-v2.md §8.2 civiccast-stream
- CivicCastUnifiedSpec-v2.md §10.5 CDN tier
- CivicCastUnifiedSpec-v2.md §16.1 Broken-media regression suite
- CivicCast-ReleasePlan-0.1-to-1.0.md — rung 0.2 Streaming origin scope and exit criteria
- [HLS RFC 8216](https://datatracker.ietf.org/doc/html/rfc8216)
- [ffmpeg HLS muxer documentation](https://ffmpeg.org/ffmpeg-formats.html#hls-2)
- ADR 0001 — NATS JetStream (event bus for stream-level events)
- ADR 0005 — Sprint 0.1 framework stack (psutil / pydantic posture continued here)

---

*ADRs are immutable once Accepted. Reversing or superseding requires a new ADR that references this one.*
