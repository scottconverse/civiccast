# Channel Egress Operator And Tester Runbook

This runbook explains how to operate and test CivicCast's local outgoing channel
feed. It is for meeting operators, station admins, integrators, and release
testers.

## What The Outgoing Feed Is

The outgoing feed is the local CivicCast worker that sends one channel to its
configured output, such as a cable handoff, SRT receiver, RTMP endpoint, or local
transport-stream file. It is separate from the resident portal player.

Use the outgoing feed when the station needs CivicCast to drive a linear channel
or a headend-style destination.

## What This Proves

The egress proof path can show:

- CivicCast queued a start, stop, reload, or drain command from a signed-in staff
  operator.
- The daemon started the configured source plan or fallback slate.
- The encoder reported health samples while running.
- The output file or receiver preserved the expected source duration and
  loudness boundary during local proof.
- Caption status changed to **On** only after decoded emitted-stream captions
  matched expected captions.
- CivicCast recorded emergency banner raise and clear events as CivicCast CG
  overlay intent, not as EAS proof.

The egress proof path does not by itself prove:

- a cable headend accepted the feed;
- a downstream station switcher rendered overlays correctly;
- legal caption compliance;
- ATSC A/85 or station-specific loudness compliance unless the configured
  target was agreed and measured for that handoff;
- app-store, CDN, external provider, or production operations readiness.

## Operator Checklist

### Native reload status

The current native repair runs clean ten times out of ten with normal logging,
each run covering three workers and six in-place replacements with clean
transport checks and clean stop. A normal replacement keeps output while the new
leg is prepared and holds the switch until the outgoing leg retires. An
overlapping commit request is explicitly declined and uses the existing
full-graph restart recovery path; it is not queued as a latest-request-wins
operation. Existing watchdog time bounds and station capabilities are unchanged.

Two watchdogs cover a replacement and they no longer overlap: while a commit is
in progress the stall bound stands down so the commit watchdog owns that window
and can emit its stack dump, and the stall bound restarts from full once the
commit settles. Ten clean runs bounds the failure rate rather than proving
absence, and this diagnostic does not establish beta.5 installer acceptance or a
two-hour physical soak.

A caption heartbeat is a timing signal for an interval without caption text.
Its guard is separate from caption conversion: it admits one
heartbeat reservation at a time and prevents an older send/probe callback from
clearing a newer reservation. Queue capacity settings alone are not the event
bound. Actual caption cue buffers are unchanged and are not counted by this
heartbeat guard.

1. Open the operator console.
2. Open **System Health** or **Channels**.
3. Find **Outgoing channel feed**.
4. Confirm you are signed in with the meeting operator role. If the buttons are
   disabled, ask an admin to assign the role or have a meeting operator run the
   command.
5. Check the channel state:
   - **Stopped:** no outgoing feed is running.
   - **On air:** the outgoing worker is sending the configured source.
   - **Showing slate:** CivicCast is sending fallback slate instead of blank
     output.
   - **Changing source:** CivicCast is switching from one source to another.
   - **Finishing current item:** CivicCast is draining before stopping.
   - **Needs attention:** read the error and ask a technical admin before
     retrying.
6. Use **Start** to begin the outgoing feed.
7. Use **Reload** after the schedule/source plan changes and the current worker
   should hand off to the new source.
8. Use **Drain** when the current item should finish before the worker stops.
9. Use **Stop** when the feed should stop now.
10. Watch encoder health, sink connection, loudness, dropped frames, and caption
    status.

Do not use outgoing feed controls during a live meeting unless station policy
allows the action you are taking.

## Tester Evidence Checklist

A tester should collect all of the following for a release or acceptance proof:

- CivicCast version, branch, commit, and artifact under test.
- Operating system and whether the test used a clean install, upgrade, or
  developer checkout.
- The configured channel id.
- The sink type under test: FileSink, SRT loopback, RTMP, local TS, SDI, or
  external headend.
- The configured loudness target and tolerance.
- The proof command used.
- The JSON proof output path.
- PASS, PARTIAL, or FAIL.
- Exact blocker code for any failure.
- A note that secrets, passphrases, tokens, recovery codes, subscriber data, and
  private URLs were not included in the report.

## FileSink Continuity Proof

Use FileSink when you need a local proof that does not depend on a receiver.

```powershell
uv run civiccast egress continuity-proof `
  --source-plan-json C:\path\to\source-plan.json `
  --config-json C:\path\to\egress-config.json `
  --output-path C:\path\to\evidence\egress-proof.ts `
  --work-dir C:\path\to\evidence\work `
  --json
```

Expected PASS evidence:

- `status` is `PASS`;
- `boundary_count` is greater than zero for a multi-source plan;
- measured duration is within tolerance;
- `loudness_status` is `ok`;
- `output_path` points to the emitted `.ts` file;
- `concat_plan_path` points to the generated FFmpeg concat plan.

If this fails, keep the JSON output and the generated work directory. Do not
delete evidence before a developer has read it.

## SRT Loopback Continuity Proof

Use SRT loopback when you need proof that CivicCast can send to a local SRT
receiver.

```powershell
uv run civiccast egress srt-continuity-proof `
  --source-plan-json C:\path\to\source-plan.json `
  --config-json C:\path\to\egress-config.json `
  --sender-url "srt://127.0.0.1:19001?mode=caller" `
  --receiver-url "srt://127.0.0.1:19001?mode=listener" `
  --receiver-output-path C:\path\to\evidence\receiver.ts `
  --work-dir C:\path\to\evidence\work `
  --json
```

Expected PASS evidence:

- `status` is `PASS`;
- `sink_kind` is `srt`;
- receiver return code is `0`;
- measured duration and loudness are within the configured target;
- receiver metrics and receiver output path are present.

This proves local SRT loopback. It does not prove a real cable headend accepted
the feed.

## Caption Decode-Back Proof

Caption status must stay **Not verified** until decoded emitted-stream captions
match the expected captions.

First, create or collect:

- the emitted stream file;
- the expected caption file as WebVTT or SRT;
- the decoded caption file from the emitted stream as WebVTT or SRT.

Then run:

```powershell
uv run civiccast egress caption-decode-proof `
  --channel-id public `
  --emitted-stream C:\path\to\evidence\egress-proof.ts `
  --expected-captions C:\path\to\expected.vtt `
  --decoded-captions C:\path\to\decoded-from-emitted-stream.vtt `
  --decoder-name ffmpeg-cc-decode `
  --output-path C:\path\to\evidence\caption-decode-proof.json `
  --json
```

Expected PASS evidence:

- `status` is `PASS`;
- `caption_status` is `on`;
- expected and decoded cue counts are present;
- matched cue count equals the expected cue count;
- max timing delta is within the configured tolerance;
- the emitted stream path points to a real file.

Expected FAIL evidence:

- `EGRESS_CAPTION_DECODE_BACK_NO_EXPECTED_CUES` means the expected caption file
  did not contain timed cues.
- `EGRESS_CAPTION_DECODE_BACK_MISMATCH` means decoded captions did not match the
  expected text and timing.

Do not describe a PASS as legal caption compliance. It proves this emitted
stream's decoded captions matched the expected file under the configured
tolerance.

## Emergency Banner Proof

CivicCast can record emergency banner raise and clear events in the egress proof
log when the CG overlay path supplies a valid overlay proof.

Operators should understand this wording:

- **Emergency banner:** CivicCast visual banner intent.
- **Not EAS:** CivicCast is not claiming EAS origination, EAS certification,
  CAP relay, or alert authority.
- **Cleared:** CivicCast recorded that the banner was removed from the egress
  proof path.

If a report includes emergency banner evidence, it must say whether it is raise
or clear evidence and must not call it EAS proof.

## Running Three Channels On One Box

The cable-automation lane (CA-1..CA-4) runs multiple channels from the app
itself — no CLI worker needed. Posture for a three-channel station:

- Configure each channel's egress config (sinks, slate message, canonical
  profile), set `auto_start` for every channel that should run 24/7, and pick
  each channel's `fill_policy` (`slate` or `bulletins`).
- Each running channel is one ffmpeg encoder process. Three concurrent 720p
  H.264 encodes are the realistic load shape. The CA-8 4-hour development
  acceptance window measured flat combined encoder RSS of 49–89 MB across
  three channels on one machine (see
  `tester-handoff/v2.1.0/test-results/windows/20260612-0824-local-ca8-4h-acceptance.md`);
  full-day CPU/memory figures come from the pending 24-hour unattended
  soak — this runbook does not claim load figures that have not been
  measured.
- Work files live under `CIVICCAST_EGRESS_WORK_DIR/<channel_id>/` (prepared
  segments, slates, bulletin slides) — one subtree per channel.
- **Program-start speed and the conform cache (issue #156, fixed):** an asset
  that has aired before starts within seconds — its canonical conform is kept
  in a persistent cache (`CIVICCAST_EGRESS_WORK_DIR/conform-cache/`, bounded by
  `CIVICCAST_CONFORM_CACHE_GB`, default 60 since beta.10 -- it was 20, and a
  4.4-hour asset measured about 11 GB, so a few long assets exceeded a 20 GB
  cache and were rebuilt repeatedly; set `0` to disable). The **first-ever
  airing of a long asset still conforms at airtime** (same duration as before —
  schedule long premieres with that in mind, or air a short lead-in first); a
  join-in-progress first airing conforms only the remaining portion, exactly as
  before, and the full asset is cached in the background for the next airing.
  Join-in-progress starts from the cache land on the nearest keyframe (at most
  one GOP — about 2 seconds — early); on the GStreamer engine the cached copy is
  re-cut by stream copy rather than re-encoded, which costs seconds, not
  minutes.
- **Cache HIT accuracy vs MISS accuracy (item 66, corrected round-3):** for a
  TRIMMED (join-in-progress) request, a cache HIT's window is a `-c copy` cut
  out of the shared conform — fast, but floors to the previous keyframe (up to
  ~2 seconds early, per the keyframe note above); a first-time trimmed MISS's
  bounded conform re-encodes the wanted window instead (`-ss` before `-i`, but
  ffmpeg still decodes every source frame from that keyframe forward when
  transcoding rather than stream-copying, so the OUTPUT starts at the exact
  requested time, not the nearest keyframe). So a join-in-progress asset's
  FIRST airing at a given offset (a MISS, re-encoded) can start more precisely
  than a LATER airing at that same offset served from the cache (a HIT,
  keyframe-floored) — the opposite of what "cached = better" intuition
  suggests. Neither is a defect; don't read a later airing's ~2s-early start
  as a regression from the first.
  For an UNTRIMMED asset there is no flooring concern either way: the
  GStreamer engine's bounded conform for an untrimmed MISS never seeks (the
  whole asset is wanted from the start, so no `-ss`), and that already-
  finished per-plan file is what airs directly — the persistent cache entry
  is populated afterward from a hard link of that SAME file (round-4
  correction: **if the cache and the per-plan directory live on different
  volumes, the link fails and the cache entry populates in the BACKGROUND
  instead** — a queued byte-for-byte copy, same one-worker warm queue as an
  ordinary warm — never a second encode, and never blocking that airing).
  A cache HIT for an untrimmed asset is therefore byte-identical to its
  first airing, whether it arrived by link or by background copy.
- **Cache promotion never waits behind an in-progress warm (item 66
  round-4, non-blocking acquire; round-5 fixed a self-deadlock in the
  cross-volume path — see below):** if a background warm for the identical
  asset is already running when a foreground airing tries to populate (or
  link/copy into) the same cache entry, that foreground promotion is
  skipped outright rather than waiting for the warm to finish — a warm's
  own single-threaded conform can take tens of minutes for a long asset,
  and item 66 exists precisely to keep the synchronous start path from
  waiting on work like that. Skipping costs nothing beyond that one
  airing's copy being (for now) absent from the persistent cache: the
  segment already aired from its own file regardless, and the in-progress
  warm (or a later airing) still populates the cache normally. On a
  cross-volume cache/work-dir layout (the hard-link fallback), the
  background copy job itself also acquires non-blocking and skips on
  contention rather than waiting or busy-looping — round 4 shipped this
  scheduling from INSIDE the still-held lock, which self-deadlocked with a
  synchronous scheduler and head-of-line-blocked the real one; round 5
  schedules it only after the lock is released.
- **A broken ffprobe on a station means every slot-capped untrimmed airing
  queues a background whole-asset conform (item 66 round-8, HIGH fix):**
  when this call's own live ffprobe can't determine an asset's real media
  duration, an untrimmed segment whose schedule slot is shorter than the
  asset (D42) is never promoted straight from its own bounded conform — it
  always falls through to a background warm that conforms the whole asset
  fresh instead. That warm is single-threaded and bounded by the existing
  warm dedupe (one queued conform per asset, not one per airing), so the
  station self-heals after one pass per asset rather than piling up
  duplicate work — but on a GStreamer station with many assets and a
  genuinely broken ffprobe, expect a long queue of first-run background
  conforms until ffprobe is fixed or every asset has been warmed once.
- **Upgrading past item 66 round-7/8 invalidates every pre-existing
  conform-cache entry:** a `.json` sidecar written by older code has no
  `full_asset_conform` key at all, which now reads as `False` rather than
  a trusted HIT (see the flag's own docstring in `preparer.py`). Each asset
  therefore pays exactly one re-conform after the upgrade the first time it
  airs again — synchronous, on the start path, when
  `playout_trim_supported=True` (the ffmpeg-concat engine); scheduled as a
  background warm otherwise (GStreamer). This is a one-time cost per asset,
  not a permanent regression — once re-conformed, the entry carries the
  flag and hits normally again.
- **The loudness probe samples the asset, it does not measure the whole
  file (item 66, revised round-6):** a TRIMMED (join-in-progress) segment's
  probe is bounded to its own wanted window. An UNTRIMMED segment's probe
  uses the asset's REAL MEDIA DURATION — NOT the head, and NOT the
  prepared segment's `duration_seconds` (which is capped by the schedule
  slot and can be shorter than the asset itself; using the slot duration
  for an offset could seek past the asset's actual end, read as silence,
  and trigger an unnecessary floor verdict). The real duration comes from
  a cheap ffprobe query, cached alongside the loudness result so a later
  airing of the same asset never re-probes either one.
  - **Duration known, asset > 120 seconds:** samples 120 seconds starting
    40% in (a head sample can land on cold-open silence or room tone and
    measure the silence floor instead of the program's real loudness — a
    real field failure: -70 LUFS measured at the head of a 39-minute
    meeting recording). If that sample measures at or below -60 LUFS
    integrated (still silence, e.g. a long pause that happens to land in
    the window), the preparer resamples ONCE more at 70% in — but only if
    a genuinely non-overlapping second window actually fits (the second
    must start at least 120 seconds after the first AND stay inside the
    file); below 400 seconds that room never exists — item 66 round-8
    corrected this from a previously-documented "120-240 seconds": working
    the two clamped windows through by hand (not just describing them)
    shows the non-overlap requirement only starts holding at exactly 400
    seconds, not 240 — so for any asset shorter than 400 seconds no
    resample is ever attempted, and the single (floor) reading is used as
    measured. When a
    non-overlapping resample IS possible and
    both independent samples land at the floor, the asset is treated as
    genuinely silent (normalization is skipped outright rather than
    trusting either floor reading as a real target). If the resample
    itself fails to produce a measurement at all, the FIRST reading is
    kept rather than raising or discarding it.
  - **Duration known, asset <= 120 seconds:** one sample spanning
    `min(duration, 120s)` from the very start already covers the whole (or
    nearly the whole) file, so a floor reading on it is trusted directly
    as silence with no resample needed. (Item 66 round-6 used a 240-second
    cutoff here instead of 120; that let a 120-240 second asset's
    HEAD-only 120-second sample stand in for the whole file when it
    covered at most half of it — a real field failure: a 200-second clip
    whose audio starts at 130 seconds got memoized as silent because the
    sampled window never reached it. Round 7 lowered the cutoff to the
    probe window's own size, so a "duration known" asset over 120 seconds
    always falls into the sampled-window case above instead.)
  - **Duration genuinely unknown** (ffprobe unavailable or failed): the
    probe samples from the very start (never past EOF for any asset with
    real audio) and a floor reading is NEVER trusted as proof of silence —
    there is no independent second window, and no known length, to
    cross-check it against. This corrects a real, measured failure: with
    the previous fixed-offset design (120s and 240s in), a real 67-second
    clip (true loudness -10.9 LUFS) had BOTH offsets land past its actual
    end, read as silence, and got misreported as a genuinely silent asset.
  Either way this remains a sample rather than a full-file measurement for
  material whose loudness varies significantly across its length.

- Never run the inline automation driver AND a `civiccast egress run` CLI
  worker for the same channels: two daemons would race the same durable
  command queue.
- **Restarting the server process while channels are on air (issue #161,
  fixed):** before the startup reap shipped, the old server's encoder
  children survived the restart and kept streaming to the sink ports while
  the new server auto-started its own — two writers on one UDP port
  corrupts the headend feed (continuity errors) until the orphan's plan
  ends. The daemon now terminates any still-running encoder pid recorded in
  durable channel state before starting fresh and logs the reap as a
  channel event, so routine deploy restarts no longer need a manual stop
  sequence. If you are running a build older than the reap, stop the
  channels (or kill the ffmpeg encoder processes) BEFORE replacing the
  server process, then let `auto_start` bring them back. Found and
  root-caused by the CA-8 acceptance run's TSDuck monitoring; the
  post-fix server-restart case re-runs as part of the 24-hour soak.
- **Seamless mux across encoder relaunches (issue #151, fixed for udp-ts):**
  plan boundaries, reloads, and crash restarts relaunch the encoder, which
  used to reset the TS session at the headend (new continuity counters and a
  new source port at every splice — logged as CC errors by TR 101 290
  monitoring). With TSDuck available (`tsp` on PATH, `CIVICCAST_TSDUCK_PATH`,
  or the managed pull), a channel-lifetime relay now sits between the encoder
  and every `udp-ts` destination (`tsp -P continuity --fix -P pcradjust`,
  pinned source port), so the headend sees one continuous session no matter
  how often the encoder relaunches. `CIVICCAST_TS_RELAY=auto` (default:
  on when tsp exists) | `on` | `off`. Without TSDuck the behavior is the
  historical direct output, with a startup log telling you what you're
  giving up.
- System Health shows the "24/7 channel automation" rollup: green when every
  auto-start channel is on air (or honestly on schedule-gap filler), red with
  the channel ids when any automated channel is dark. A dark cable channel
  never blocks meeting-broadcast readiness — it is an optional check by
  design.

### Controlled soak settings

The default schedule remains finite. For a controlled local soak, set
`CIVICCAST_SCHEDULE_LOOP=1` before starting the service. This repeats the
published sequence from its first item, preserving the schedule's assets and
gaps; it does not invent filler or change the schedule in the database. Leave
the variable unset (or set it to `0`) for normal finite-schedule behavior.

Caption proof capture is bounded so a stalled ffmpeg invocation cannot hold the
caption/control path indefinitely. `CIVICAST_CAPTION_PROOF_TIMEOUT_SECONDS`
(spelled with one C; the code reads exactly this name) defaults to 15 seconds and applies to both capture and decode-back. In a
supervised service, proof work runs in a separately supervised child process by
default (`CIVICCAST_CAPTION_PROOF_PROCESS=process`); developer/unsupervised
launches keep the inline worker by default. Set the variable explicitly to
`off`, `process`, or `inline` when a test needs a specific mode. `off` is only
for a controlled diagnostic window and means no emitted-caption proof is
collected. A proof timeout is a failed proof sample, not a caption-pass signal.

Decode-back compares completed encoder display/erase times with the independently
decoded transport. Pop-on caption loading starts before the page appears and is
not its display time. Missing clocks, pending undisplayed pages, and decode
mismatches remain **Not verified**. A pass does not certify transcription accuracy
or synchronization of delayed live transcription with the original speech.

Startup housekeeping is also bounded. Each one-shot startup hook gets a
`CIVICCAST_STARTUP_HOOK_TIMEOUT_SECONDS` budget (default 1 second); slow
housekeeping is allowed to finish in the background and must not hold the
airing path. A warning is logged when a hook exceeds that budget so the cause
can be investigated without making the station appear healthy while startup
is still stalled.

## Sending A Channel To A Cable Headend

The cable-automation lane (CA-6) ships a `udp-ts` sink — constant-mux-rate
SPTS MPEG-TS over UDP unicast or multicast — plus named presets built from
published vendor documentation. Apply one from **Channels → Cable headend
delivery** (or `POST /api/staff/egress/channels/{id}/config/headend-profile`).

When it reaches the air: the daemon reads a channel's outputs, encode profile
and loudness target only when it **builds** a pipeline, and a content reload
never rebuilds them. The apply response says what happened in
`on_air_effect` / `on_air_detail`, and the card shows it:

- `restart_queued` -- the channel was standing by on its fallback slate, so a
  `stop` + `start` pair was queued (one durable write) and the pipeline comes
  back with the new output within one daemon poll. No program is interrupted,
  but the restart terminates the one worker that produces **every** output:
  the cable headend feed drops for the few seconds of the rebuild, and cable
  subscribers watching the slate see that. The Channels screen says so before
  the operator confirms. The daemon runs the `stop` only while the channel is
  still on its slate and the `start` only after that `stop` ran, so a program
  that commits to air in between is never cut. **Residual:** the pair is
  drained in one pass, so if the daemon dies *between* the two halves the
  channel is left `STOPPED` with the `start` already marked consumed, and
  nothing retries it -- the one durable write moved this failure from the API
  process to the daemon process, it did not eliminate it. The window is a
  single in-process loop iteration, but if a slate-only channel is found
  `STOPPED` after a daemon crash, **Start** it from the Channels screen; no
  reconciler will.
- `restart_required` -- a program is on air (or the channel is starting up /
  handing off). The preset is saved and lands at the channel's next start; to
  put it on air now, **Stop** then **Start** the channel. The route never cuts
  a program on its own.
- `next_start` -- the channel is not running (or is going off air); its next
  start reads the config.
- `unchanged` -- the stored config already matched AND the channel is either
  not running or measured to be delivering those outputs already (the
  daemon's latest health sample is keyed by the sinks the running pipeline
  was built with -- in **every** appender that writes a sample, the start,
  the poll tick, the fallback-slate transition and a content reload's
  settlement alike, because the keying is applied inside `_sink_connected`
  rather than at each call site). Re-applying a preset after `restart_required`, once the
  program has ended and the channel is back on its slate, is therefore NOT
  `unchanged` -- it restarts the channel and puts the output on air.

Until a pipeline built with the `hls` sink writes `playlist.m3u8`,
`/api/public/live/current` reports `on_air_no_web_output` with
`reason: "HLS output configured but not serving yet"` rather than a manifest
URL that would 404; the resident Home renders that reason as "the web preview
is turned on but not serving yet" (distinct from "not enabled"). The daemon
removes the previous broadcast's `playlist.m3u8` on an operator stop and on a
start that finds no live relay, so a restarted channel is not advertised on
the strength of a stale playlist; a daemon that died uncleanly leaves its last
playlist in place until the channel's next start.

| Preset | Encode | Mux rate | Transport | Built from |
| --- | --- | --- | --- | --- |
| `generic-udp-spts` | H.264 720p30 5 Mbps, AC-3 192k | 8 Mbps default | UDP unicast | TelVue feed-setup KB; CableLabs encoding tech notes |
| `comcast-mtd-sd` | MPEG-2 720×480, GOP 15, AC-3 192k/48k | 3.75 Mbps (CableLabs SD aggregate) | UDP multicast | Comcast MTD page; CableLabs VOD encoding profile |
| `comcast-mtd-hd` | H.264 1080p30 10 Mbps, AC-3 384k | 12 Mbps placeholder — your carriage agreement sets the real rate | UDP multicast | Comcast MTD page |
| `telvue-hypercaster-ip` | H.264 720p30 5 Mbps, AC-3 192k | 8 Mbps; match the feed's Max Bit Rate | UDP unicast or multicast, port 1024–65535 | TelVue KB (feed setup, content prep, ports) |
| `harmonic-spectrum-ts` | H.264 1080p30 8 Mbps, AC-3 192k | 10 Mbps | UDP unicast | Harmonic Spectrum X/XE datasheets |
| `leightronix-file-drop` | H.264 720p file handoff | n/a | Watched folder | Leightronix UltraNEXUS-HD docs |
| `local-rehearsal-hls` | Adds an `hls` web-preview sink; leaves the channel's encode and loudness untouched | n/a | Local folder (blank = `<egress work dir>/live-hls/<channel>`, or `<root>/<channel>` with no `live-hls/` segment when `CIVICCAST_LIVE_HLS_ROOT` is set; a typed folder must be an absolute path under the egress work dir or `CIVICCAST_LIVE_HLS_ROOT` -- UNC, relative and elsewhere paths are refused because the folder is served publicly, and `/media/live` re-checks that containment on the resolved path at serve time), served at `/media/live/<channel>/playlist.m3u8` | Not a cable delivery: RFC 8216 + ffmpeg `hls` muxer. Lets the resident portal play the channel with no headend and no CDN |

Mechanics worth knowing:

- The **mux** is what must be constant (TelVue documents this explicitly):
  the `-muxrate` flag makes the mpegts muxer null-pad to the constant rate,
  so the requirement holds even though the encoder stream-copies prepared
  segments. The encode numbers land on the channel's canonical profile, so
  every prepared segment conforms before air.
- Datagrams carry seven 188-byte TS packets (`pkt_size=1316`) unless you pin
  a different `pkt_size` on the destination URI yourself.
- Multicast presets refuse a unicast destination; unicast presets accept
  either (a multicast group is still valid). The TelVue preset enforces the
  KB's 1024–65535 port floor.
- The operator supplies ONLY the destination address/port and, where the
  carriage agreement sets one, the mux rate. Everything else is baked from
  the published docs.
- **Honesty boundary:** these presets are built from published vendor
  documentation and verified machine-locally. None of them is field-proven
  against a real cable headend until the first-station beta.

## Verifying The Headend Stream

CA-7 ships a bring-your-own-TSDuck verification lane. TSDuck is the free,
BSD-licensed MPEG-TS toolkit from <https://tsduck.io>; install it (or set
`CIVICCAST_TSDUCK_PATH` to its `bin` directory) and CivicCast can capture
and judge the live udp-ts output.

Three ways to run the same probe:

- Console: **Channels → Cable headend delivery → Verify stream (TSDuck)**.
- API: `POST /api/staff/egress/channels/{id}/compliance-probe`.
- CLI: `civiccast egress verify --channel-id <id> --seconds 10` (exits 1 on
  fail — loopable for soak monitoring).

What the probe judges (a bounded `tsp ... -P analyze --json` capture):

| Check | Pass means |
| --- | --- |
| `cbr-mux-rate` | measured PCR bitrate within 5% of the sink's `-muxrate` |
| `ts-sync` | zero invalid sync bytes, zero transport-error indicators |
| `continuity` | zero continuity-counter discontinuities on every PID |
| `pat-pmt` | PAT and PMT both present |
| `pcr-present` | at least one PID carries PCR |
| `single-program` | exactly one service (a true SPTS) |

Mechanics and honesty:

- Multicast destinations are probed **alongside** the live headend (one more
  group member). Unicast destinations are probed **in place of** the
  receiver during commissioning — UDP unicast has one listener per port.
- Without TSDuck the verdict is an honest `not-run` with the install
  pointer; CivicCast never fakes a pass.
- These checks are an analyze-plugin subset aligned with TR 101 290
  priority-1 concerns — not the full TR 101 290 monitoring suite, and not
  headend field proof. System Health carries a "Cable headend verification"
  rollup (yellow when TSDuck is missing or channels were never verified,
  red on a failing last probe).
- A reference run on this machine: a CA-6-shaped stream (`-muxrate 8000k`)
  measured **exactly 8,000,000 b/s (0.00% drift)** with zero errors on
  every check; a deliberately double-sourced stream failed `continuity`,
  which is the probe doing its job.
- `POST /api/staff/egress/headend-device-probe` ({host, ports}) checks TCP
  reachability of the headend appliance's management surface (TelVue and
  Leightronix boxes manage over their web UIs) — reachability of the web
  UI says nothing about the video path; use the stream probe for that.

## NDI Output (Bring Your Own NDI-Capable FFmpeg)

Issue #116: CivicCast can republish any channel as an NDI source for the
station's production network — with the NDI side supplied by the station,
because mainline FFmpeg removed the NDI muxer in 2019 over the NewTek
license and CivicCast's bundled ffmpeg cannot and does not include it.

What the station brings:

1. The NDI runtime / NDI Tools from <https://ndi.video> (their license to
   accept).
2. An FFmpeg build with the NDI muxer (`libndi_newtek`), self-built against
   the NDI SDK or obtained from an integrator. `civiccast cable ndi-check`
   reports exactly what is missing.
3. `CIVICCAST_NDI_FFMPEG` pointing at that build.

What CivicCast does once the wire exists:

- Set the channel's **NDI output name** on the Channels screen (blank = no
  NDI). The automation driver supervises a relay process that consumes the
  channel's UDP transport-stream output and publishes it as that NDI
  source — programs, slate, and bulletins all flow through automatically.
- The relay is crash-isolated from the on-air encoder and restarts with
  5/15/60-second backoff. `GET /api/staff/egress/ndi-readiness` shows the
  BYO posture and live relay states; a missing binary or failed readiness
  check is an honest `blocked` status with the next step, never a crash
  loop and never a faked "running".
- The relay needs the channel's UDP TS output to exist (apply a headend
  delivery preset, or any `udp://` local-ts/udp-ts sink).
- `CIVICCAST_NDI_RELAY=off` disables NDI relay supervision host-wide
  (default `inline`); blanking a channel's NDI name field disables it per
  channel.

**Honesty boundary:** receiver-side proof is the station's step — an NDI
receiver (e.g. Studio Monitor) showing the stream is what makes NDI
delivery *proven* on that network. Until a station records that, CivicCast
claims a supervised, config-gated component, not field-proven NDI delivery.

## SDI Output (Bring Your Own DeckLink-Capable FFmpeg)

Issue #117: CivicCast can put any channel on a physical SDI wire through a
Blackmagic DeckLink output card — with the SDI side supplied by the
station, because FFmpeg's `decklink` output requires building against the
Blackmagic SDK (their license to accept) and CivicCast's bundled ffmpeg
cannot and does not include it.

What the station brings:

1. A DeckLink output-capable card and Blackmagic's **Desktop Video**
   driver installed on the playout host.
2. An FFmpeg build with the `decklink` muxer, self-built against the
   Blackmagic SDK (`--enable-decklink`) or obtained from an integrator.
   It may be the same binary as the NDI one if it was built with both.
3. `CIVICCAST_SDI_FFMPEG` pointing at that build.

Finding the device name (this exact string goes in the channel config):

```powershell
& $env:CIVICCAST_SDI_FFMPEG -sinks decklink
```

What CivicCast does once the wire exists:

- Set the channel's **SDI output device** on the Channels screen (blank =
  no SDI). The automation driver supervises a relay process that consumes
  the channel's UDP transport-stream output, re-encodes to the raw frames
  the card needs (`uyvy422` video, 48 kHz stereo PCM embedded audio), and
  feeds the named DeckLink device — programs, slate, and bulletins all
  flow through automatically.
- The relay is crash-isolated from the on-air encoder and restarts with
  5/15/60-second backoff. `GET /api/staff/egress/sdi-readiness` shows the
  BYO posture and live relay states; a missing binary or a build without
  the decklink muxer is an honest `blocked` status with the next step,
  never a faked "running". A malformed device name (blank, control
  characters) is rejected at save time and blocks honestly if it reaches
  the relay anyway. A syntactically valid name that matches **no installed
  card** is different: the readiness check cannot see the card, so the
  relay process exits and the supervisor retries on the same
  5/15/60-second backoff, showing `restarting` with the ffmpeg exit error
  captured in `last_error`. If the relay never reaches `running`, check
  the device string against `& $env:CIVICCAST_SDI_FFMPEG -sinks decklink`
  first.
- `CIVICCAST_SDI_RELAY=off` disables SDI relay supervision host-wide
  (default `inline`); blanking a channel's device field disables it per
  channel.
- The relay needs the channel's UDP TS output to exist (apply a headend
  delivery preset, or any `udp://` local-ts/udp-ts sink).

**Honesty boundary:** SDI is *proven* when a downstream device (headend
encoder, broadcast monitor) shows the feed from the card's BNC output.
Until a station records that, CivicCast claims a supervised, config-gated
component, not field-proven SDI delivery.

### No custom FFmpeg? Use the OBS bridge

Stations that already run OBS Studio with a DeckLink card can skip the
custom FFmpeg build entirely — OBS ships DeckLink output support and
Blackmagic's licensing is handled inside OBS:

1. Install OBS Studio and Blackmagic Desktop Video on the playout host;
   confirm the card shows up in OBS under **Tools → Decklink Output**.
2. Give the channel a UDP TS output (apply a headend delivery preset, or
   add a `udp://239.x.x.x:port` / `udp://127.0.0.1:port` sink).
3. In OBS add a **Media Source** to an empty scene:
   - uncheck *Local File*; set *Input* to the channel's `udp://` URI
     (or an `srt://` URI if the channel publishes SRT);
   - set *Input Format* to `mpegts`; leave hardware decode on if offered.
4. **Tools → Decklink Output** → pick the DeckLink device and a mode that
   matches the channel's canonical profile → **Start**.
5. The card's SDI output now carries the channel. OBS's media source
   re-opens a network input when data resumes on current OBS versions —
   verify the reconnect behavior on your OBS version during commissioning
   by restarting the channel once and watching the output recover.

The OBS bridge is operationally outside CivicCast — its supervision is
OBS's job, and the SDI readiness endpoint will not report it. It is the
right answer when the station wants SDI today without building FFmpeg.

## Diagnosing A Worker Exit: Never Produced Output vs Stopped Producing It

A GStreamer worker's stderr distinguishes two DIFFERENT failure shapes that
both end in a daemon-driven restart -- read the exact marker before assuming
a bounced channel is the same failure mode as any other:

- `CTRL first-output: no output within Ns of PLAYING - quitting for daemon
  restart` (item 84) -- the pipeline reached `PLAYING` (see the preceding
  `CTRL preroll: reached PLAYING after ...s` line) but never produced a
  single output buffer within `first_output_timeout_s` (45s default,
  `CIVICCAST_GST_FIRST_OUTPUT_TIMEOUT_S` env override, clamped `[10, 120]`s).
  This is a SLOW START under load (a concurrent conform, a synchronous
  content-reload source preparation, live caption-tap overload), not a
  stalled or dead pipeline -- the daemon relaunches it through the normal
  back-off path but rate-limits how often it counts toward the crash-loop
  streak that forces fallback slate, the same way it already does for
  `CTRL preroll: worker exiting -- ...` (item 82, a preroll that never
  reached `PLAYING` at all). If this line recurs steadily on a box, look at
  what else is competing for CPU/IO at worker start (an `ffmpeg` conform, a
  slow disk, another channel's caption tap) before assuming the source
  itself is unreachable.
- `CTRL stall: no output for 10s - quitting for daemon restart` (S9-5,
  unchanged by item 84) -- the pipeline WAS producing output and then
  stopped for `stall_timeout_s` (10s default). Treat a line you actually see
  as a genuine on-air interruption, not a slow start -- but read what it
  CANNOT see before concluding that a dark channel is feeding fine. Its
  progress signal is ONE aggregate count of what leaves the mux
  (`_install_output_counter`, a probe on the mux src pad), and `_check_stall`
  treats any advance of that single integer as healthy output, so a freeze
  that stops one stream while another keeps flowing never trips it. All four
  2026-09-25 live freezes had that shape: the video branch stopped delivering
  buffers while audio carried on. The worker's own `CTRL output:` interval
  deltas fall from ~392 to ~245 buffers per 5s in all four captures, and that
  step matches the audio-alone share of the mux's ~77 buffers per second. A
  dark channel whose worker stderr holds no `CTRL stall` line is therefore NOT
  evidence that residents are still being fed. Since U30 the progress line also
  names the streams that aggregate cannot separate --
  `CTRL output: <total> buffers (+<delta>) since PLAYING [mux-in 5.0s: video=+0 audio=+235]`
  -- where `(+<delta>)` is still cumulative since PLAYING while each
  `[mux-in ...]` value is that stream's count over the interval since the
  previous line (always armed, not an opt-in diagnostic). `video=+0` against a
  climbing `audio` is the marker for a video-only freeze. Two readings of the
  clause are worth knowing: a pad the worker could not count at all is omitted
  rather than shown as `+0`, so absence means "not counted", not "stopped"; and
  a pad registered after the previous line renders `+0` for that one interval,
  so it is a *run* of `+0`s that is the freeze, not a single one.
- **A channel restarted while its worker was still ALIVE (U30 freeze
  escalation).** The one daemon-driven restart here that is not a worker
  failure: when the HLS live window measures as still frozen 30s after the
  relay self-heal replaced its ffmpeg child, the daemon terminates that
  channel's worker through the same crashed-encoder relaunch path and logs at
  ERROR `... the HLS live window is still frozen <n>s after the relay
  self-heal replaced its ffmpeg child, so the self-heal did not restore it
  (residents are watching a stalled stream). Restarting this channel's worker
  through the ordinary crashed-encoder relaunch path; restart N of at most 3
  in the last hour (M left in this hour).` The budget is per channel per
  rolling hour; once it is spent the daemon stops restarting that channel and
  logs a CRITICAL `... Freeze escalation budget exhausted (N worker restarts
  already spent in the last hour): NOT restarting the worker again ... check
  the channel's source program and the relay child's own output.` at most once
  per 10 minutes. **A CRITICAL here needs an operator**: the daemon has spent
  its restarts and the window is still frozen, so the cause is upstream of the
  worker. The escalation does not stop the relay child itself (the self-heal it
  escalates already replaced that child, and the replacement wrote nothing
  either), but the relaunch reaches the relay's `new_session=True` path, so a
  fresh child with one fresh heal attempt arrives with each restart -- that is
  why the restart count is the bound rather than the heal latch alone.

A THIRD line, `CTRL first-output: first buffer after Ns pid=N` (item 84
Round-2 review), is not a failure -- it is the positive-evidence marker a
worker prints exactly once, the moment its first TS buffer actually crosses
the mux. It is the daemon's own on-air evidence for the GStreamer strategy
(`EgressDaemon._observed_on_air_evidence`): a worker that only ever prints
`CTRL preroll: reached PLAYING` and never this marker is NOT credited as
on-air, on purpose -- reaching `PLAYING` is not proof any output ever
flowed, and treating it as such was the measured cause of a worker that
kept reaching `PLAYING` on every relaunch, but never producing output,
resetting its own crash-loop streak forever instead of ever escalating to
fallback slate.

Both failure lines land in the operator state row's `last_error` (folded
from the worker's stderr tail); the exit code the daemon actually observed
(`GST_FIRST_OUTPUT_TIMEOUT_EXIT_CODE` vs the ordinary crash code for a
`("stall", ...)` exit vs `GST_PREROLL_TIMEOUT_EXIT_CODE`) is not itself
operator-visible, so the stderr marker text is the fastest way to tell the
three failure shapes apart when triaging a relaunch storm. The U30 freeze
escalation is not one of those shapes -- the worker did not fail at all, so
its marker is the daemon's own ERROR line above rather than the worker's
stderr, and the restart it triggers then arrives at the same crash-relaunch
machinery these markers otherwise describe.

**`CIVICCAST_STALL_TIMEOUT_S=0` no longer disables output supervision.**
Before item 84, setting this env var to `0` (or any non-positive value) fully
disabled the stall watchdog -- a worker that silently stopped producing
output, OR one that never produced any at all, would run forever with no
daemon-driven restart. That opt-out now only covers the POST-first-buffer
stall check (`CTRL stall: ...`, unchanged S9-5 semantics); the first-output
budget (`CTRL first-output: no output within Ns ...`) is separate, floors at
10s regardless of this env var, and cannot be disabled entirely -- a worker
that never produces a single output buffer is always eventually restarted.

## What To Do With Failures

Use the exact blocker code in the report. Do not paraphrase it away.

- If continuity fails, keep the output `.ts`, concat plan, JSON proof, and
  stdout/stderr logs.
- If SRT fails, keep sender and receiver command output, receiver metrics, JSON
  proof, and the receiver output file if one exists.
- If caption decode-back fails, keep expected captions, decoded captions, and
  the emitted stream file.
- If the operator UI blocks controls, record the visible role message and the
  staff role used for the test.
- If Windows, installer, WSL, or reboot behavior is involved, record exactly
  whether the test installed, cleaned, rebooted, restarted, or reused app state.

## Reporting Rules

Every report should use the narrowest true claim:

- **PASS:** the specific proof command and environment met the stated exit
  criteria.
- **PARTIAL:** some evidence passed, but a named part was skipped, unavailable,
  or inconclusive.
- **FAIL:** a blocker prevented the proof from meeting exit criteria.

Never turn a local proof into a broader claim. For example, SRT loopback PASS is
not a headend PASS, and caption decode-back PASS is not legal compliance.
