<!-- SPDX-License-Identifier: CC-BY-4.0 -->

# Background Workers

CivicCast runs four lifespan-supervised background services when durable
storage is active. Each follows the same deployment shape: env-selected mode,
fail-fast settings validation at startup, a loop that survives and logs scan
errors, clean stop on shutdown.

| Worker | What it does | Mode variable (default) |
|---|---|---|
| Recording finalization | Turns ended broadcasts into recorded, packaged VOD assets. Full guide: [Finalization Worker Runbook](finalization-worker-runbook.md). | `CIVICCAST_FINALIZATION_WORKER` (`inline`; also `external`/`off`) |
| ActivityPub delivery retry | Re-delivers failed follower deliveries with bounded exponential backoff; dead-letters after max attempts. | `CIVICCAST_ACTIVITYPUB_RETRY_WORKER` (`inline`; or `off`) |
| Retention review | Flags assets whose retention schedule expired into the records-clerk disposition queue. **Never deletes anything.** | `CIVICCAST_RETENTION_WORKER` (`inline`; or `off`) |
| Offline caption job | Captions a published recording: transcribe → operator review queue → reviewed WebVTT attached to the published VOD. | `CIVICCAST_OFFLINE_CAPTION_JOB` (`inline`; or `off`) |

## ActivityPub delivery retry

A delivery that fails at publish time (network error or HTTP >= 400) is
queued durably in `activitypub_delivery_retries`. The worker retries due rows
and records successful retries in the normal delivery log.

| Variable | Default | Meaning |
|---|---|---|
| `CIVICCAST_ACTIVITYPUB_RETRY_POLL_SECONDS` | `60` | Scan interval. |
| `CIVICCAST_ACTIVITYPUB_RETRY_BACKOFF_SECONDS` | `120` | Base for exponential backoff (120s, 240s, 480s, …). |
| `CIVICCAST_ACTIVITYPUB_RETRY_MAX_ATTEMPTS` | `8` | Attempts (including the original send) before a row is dead-lettered. |

Dead-lettered rows stay in the table with the last status code and error for
inspection; they are never rescanned automatically. To replay one after fixing
the follower-instance issue:

- **Operator console:** the Federation screen's "Delivery retry queue" panel
  lists pending/dead-lettered deliveries with a **Replay delivery** button.
- **API:** `GET /api/staff/activitypub/delivery-retries` to inspect the queue;
  `POST /api/staff/activitypub/delivery-retries/{retry_id}/replay` (409 unless
  dead-lettered) grants a fresh attempt budget and the worker re-delivers on
  its next scan.

## Channel automation driver (cable automation)

The app drives enabled egress channels 24/7 — the `civiccast egress run` CLI
is no longer required for normal operation (it remains for external-process
deployments). Each poll processes every enabled channel's durable command
queue and supervises its encoder. Channels with `auto_start` set on their
egress config are brought back on air automatically after an app or machine
restart, and join-in-progress source planning resumes the current program at
the wall-clock offset so the channel stays on its published log. A channel
on fallback slate is reloaded the moment a scheduled program becomes due.

| Variable | Default | Meaning |
|---|---|---|
| `CIVICCAST_CHANNEL_AUTOMATION` | `inline` | `inline` runs the driver as a lifespan thread; `off` disables it (use the CLI worker instead). |
| `CIVICCAST_CHANNEL_AUTOMATION_POLL_SECONDS` | `2` | Poll cadence per pass over all enabled channels. |
| `CIVICCAST_EGRESS_WORK_DIR` | platform default | Directory for prepared segments and generated slates. |

Gaps between scheduled programs fill per the channel's `fill_policy`
(egress config): `slate` (default) or `bulletins` — a rotation of branded
slides rendered from the channel's APPROVED community bulletins
(`/api/staff/cg/channels/{id}/bulletins`). With zero approved bulletins the
channel falls back to the plain slate; an unchanged board is served from a
per-slide render cache.

Do not run the inline driver AND a CLI egress worker for the same channels
at once — two daemons would race on the same command queue.

## Program-log materializer (cable automation)

Operator-defined recurring program slots (`/api/staff/programlog/slots`)
materialize into real premiere schedule items over a rolling horizon, so a
cable channel always has upcoming programming for the playout path. Skipped
occurrences (schedule conflicts, unplayable assets) are recorded with their
reason and surfaced in the channel log — never retried silently.

| Variable | Default | Meaning |
|---|---|---|
| `CIVICCAST_PROGRAM_LOG_WORKER` | `inline` | `inline` runs the materializer as a lifespan thread; `off` disables it (the `/materialize` endpoint still works on demand). |
| `CIVICCAST_PROGRAM_LOG_POLL_SECONDS` | `300` | Scan interval. |
| `CIVICCAST_PROGRAM_LOG_HORIZON_HOURS` | `72` | How far ahead occurrences are materialized. |

## Subscriber webhook delivery retry

A real webhook delivery that fails at dispatch time (network error or HTTP
>= 400; only possible when `CIVICCAST_PROVIDER_WEBHOOK=real` — the default
mock never fails) is queued durably in `subscription_webhook_retries`. The
row carries only the subscription id and the notification payload: the
webhook URL and per-subscription signing secret stay sealed in the
subscriptions table and are reopened at send time. A subscription that
unsubscribed after the failure is dead-lettered without being called.

| Variable | Default | Meaning |
|---|---|---|
| `CIVICCAST_WEBHOOK_RETRY_WORKER` | `inline` | `inline` runs the worker as a lifespan thread; `off` disables it. |
| `CIVICCAST_WEBHOOK_RETRY_POLL_SECONDS` | `60` | Scan interval. |
| `CIVICCAST_WEBHOOK_RETRY_BACKOFF_SECONDS` | `120` | Base for exponential backoff (120s, 240s, 480s, …). |
| `CIVICCAST_WEBHOOK_RETRY_MAX_ATTEMPTS` | `8` | Attempts (including the original send) before a row is dead-lettered. |

Dead-lettered rows stay in the table with the last status code and error for
inspection; they are never rescanned automatically.

## Retention review

The worker scans `assets.retention_until` and flags expired, non-`permanent`
assets exactly once into `asset_disposition_reviews`. Records clerks read the
queue at `GET /api/staff/records/disposition-queue`.

| Variable | Default | Meaning |
|---|---|---|
| `CIVICCAST_RETENTION_POLL_SECONDS` | `3600` | Scan interval (hourly is plenty; schedules are measured in days). |

**Disposition is a human decision.** The retention presets ship with
"confirm with your records officer" disclaimers; automatic purge is an
explicit pending product decision. When a flagged asset's disposition is
decided, act per your station's records policy — purge, extend
`retention_until`, or apply a litigation hold — and keep the disposition
record with the station's retention files.

## Live caption tap

On a native Windows station, enabling live captions adds an audio tap to each
newly started channel's GStreamer playout pipeline. It writes rolling mono
16 kHz s16le WAV segments under
`CIVICCAST_CAPTION_TAP_DIR/<channel_id>/chunk-NNNNNN.wav`; the worker reads only
settled segments and publishes caption output to that channel's live VTT. Live
cue history and delivery bookkeeping are bounded. Live captions do not
automatically create permanent review records or an audio-evidence archive.

### Current native live recognition

- Whistle is the native station's default CPU primary. Whistle primary
  inference is serialized across channels within one station runtime; Whisper
  fallback requests use a separate serialization lock. Fallback is sticky
  only for the affected channel; a failure on one channel does not switch the
  others.
- `CIVICCAST_LIVE_CAPTION_ENGINE=whistle` selects Whistle with Whisper fallback;
  `whisper` selects Whisper directly. `CIVICCAST_WHISPER_DEVICE=auto|cpu|cuda`
  selects Whisper's device. Whistle itself always runs on CPU; CUDA applies to
  Whisper on supported NVIDIA hardware with the required CUDA libraries.
- A failed Whisper child is closed before replacement. The runtime replays the
  current audio once immediately; if that replacement also fails, it waits
  30 seconds before another fallback child can be created. The cooldown is
  internal to the runtime, not an environment setting.
- The Station Profile switch `live_captions_enabled` is off by default and is
  checked by the tap worker each scan. Switching it off stops transcription on
  the next scan; adding or removing the GStreamer tap and caption-embed graph
  legs takes effect on that channel's next start. A content reload does not
  rebuild those graph legs.

The `CIVICCAST_CAPTION_TAP_DIR` and `CIVICCAST_CAPTION_TAP_SEGMENT_SECONDS`
settings control the audio fork's storage root and segment length. Do not treat
an audio segment as a permanent review artifact: automatic live review and
audio-evidence retention are disabled in the native station wiring.

Three rules keep the tap from pausing over audio it never had a chance to
transcribe (U11, 2026-09-24; U23, 2026-09-25):

- **A restarted worker discards the previous broadcast's leftovers.** Audio
  already in `<channel_id>/` when the worker starts belongs to the session that
  ended, not to live backlog; it is deleted at startup (with a log line naming
  the channel and count), before the backlog gate is ever evaluated. Previously
  this discard only ran on an explicit `START`, so after a service restart the
  tap counted the last session's segments as live backlog and paused captions
  for the full backoff window before a single new segment existed.
- **An overload must persist before it does anything.** The gate counts
  over-limit scans and acts only after
  `CIVICCAST_CAPTION_TAP_OVERLOAD_PERSISTENCE_SCANS` in a row (default `15` =
  30 s at the default 2 s poll). While the overshoot is still new, the tap
  transcribes the **oldest** `MAX_BACKLOG_SEGMENTS` segments on that scan — the
  same ASR call size as a legal batch — leaves the remainder queued, keeps live
  captions flowing, and logs one INFO line per episode (`Caption tap is behind
  for channel ...`). A backlog dropping back to the limit clears the count. A
  deferred scan does not count as recovery evidence for the escalation ladder.
- **A persistent overshoot catches up; it does not pause (U23).** When the
  overshoot survives the whole persistence window, the channel keeps captioning
  from the **newest** audio and discards only the oldest settled segments that
  can no longer be transcribed in time — it sheds down to the newest
  `MAX_BACKLOG_SEGMENTS` and submits them on that same scan, so a channel is
  never silent for longer than the audio it shed. Nothing about this is a new
  threshold: the trigger is still the U11 persistence window, and a single
  over-limit scan still does nothing. The reason this replaced the pause is
  measured, not theoretical — see *Captions are best effort* below.

| Variable | Default | Meaning |
|---|---|---|
| `CIVICCAST_CAPTION_TAP` | `off` | `inline` runs the worker as a lifespan-supervised thread; `external` means you run `python -m civiccast.captions.tap_worker` as a separate process (same env + `DATABASE_URL`); `off` disables the worker AND the egress fork (item 91, 2026-09: `off` now wins even if a tap dir is still set -- `build_audio_tap_plan` checks the mode itself before it ever looks at the dir). |
| `CIVICCAST_CAPTION_TAP_DIR` | unset | Tap root shared by the egress fork and the worker. Required when the mode is not `off`; setting it also enables the egress fork -- UNLESS the mode is explicitly `off`, which now suppresses the fork regardless of a configured (or stray leftover) dir. |
| `CIVICCAST_CAPTION_TAP_SEGMENT_SECONDS` | `5` | Segment length — the floor of the caption latency budget (tap → recognize → publish to live VTT). |
| `CIVICCAST_CAPTION_TAP_POLL_SECONDS` | `2` | Worker scan interval. |
| `CIVICCAST_CAPTION_TAP_MAX_CHANNEL_WORKERS` | hardware-selected | Bounds concurrent channel work in the tap worker. In Whistle mode, primary calls and fallback Whisper calls are serialized separately. Direct Whisper selection uses its configured CTranslate2 worker concurrency. |
| `CIVICCAST_CAPTION_TAP_MAX_BACKLOG_SEGMENTS` | `2` | Settled segments a channel may be behind before a scan counts as over-limit. Reaching this bound on a single scan no longer pauses the channel, and neither does a persistent overshoot — see `..._OVERLOAD_PERSISTENCE_SCANS` and `..._CATCH_UP_SHED_LIMIT`. It is also the number of newest segments a catch-up keeps and submits, so a shed never issues a larger ASR call than a legal batch. |
| `CIVICCAST_CAPTION_TAP_OVERLOAD_PERSISTENCE_SCANS` | `15` | Consecutive over-limit scans required before the tap acts on a channel (U11, 2026-09-24). Must be at least `1`. `15` is ~30 s at the default 2 s poll, chosen to clear the measured worst-case slow batch (19 s) with margin while staying short enough to react during a real ASR collapse. Under U23 the action at this trigger is a **catch-up shed**, not a pause; `1` therefore restores the pre-U11 trigger *timing*, not the pre-U11 behaviour. For the pre-U23 pause, use `..._CATCH_UP_SHED_LIMIT=0`. |
| `CIVICCAST_CAPTION_TAP_CATCH_UP_SHED_LIMIT` | `3` | How many catch-up sheds a channel may take inside `..._CATCH_UP_SHED_WINDOW_SECONDS` before the tap concludes the box genuinely cannot keep up and falls back to the retained pause ladder (U23, 2026-09-25). Three sheds in five minutes needs at least ~90 s of over-limit scans in that span — three whole persistence windows — which no transient produces. `1` escalates on the second episode; `0` disables catch-up entirely and restores the pre-U23 rule (pause on the first persistent overshoot). A negative value, or anything unparseable, is **refused with a WARNING and the default `3` is used** — it is not read as "catch-up off" (the value is discarded, not clamped to the minimum), so switch catch-up off with an explicit `0`. |
| `CIVICCAST_CAPTION_TAP_CATCH_UP_SHED_WINDOW_SECONDS` | `300` | Window over which catch-up sheds are counted. Long enough to contain a real under-capacity episode and short enough that a station which recovers — the operator lowers the caption tier, a channel goes off air — returns to catch-up behaviour instead of carrying a stale verdict. Must be at least `1`; an unparseable or smaller value is refused with a WARNING and the default `300` is used. |
| `CIVICCAST_CAPTION_TAP_OVERLOAD_BACKOFF_SECONDS` | `120` | First pause after an overload; each consecutive overload doubles it. Item 79 (2026-09): doubled from `60` — a struggling station needs real recovery room before ASR is attempted again. |
| `CIVICCAST_CAPTION_TAP_MAX_OVERLOAD_BACKOFF_SECONDS` | `900` | Ceiling on that doubling. |
| `CIVICCAST_CAPTION_TAP_CPU_THREADS` | one per 8 CPUs, max 2 | CTranslate2 intra-op threads for Whisper in the live tap. This does not tune Whistle, which uses its own CPU recognizer. |
| `CIVICCAST_WHISPER_CPU_THREADS` | (unset) | CTranslate2 threads for Whisper; overrides the tap-specific value when set. Live values are capped at `2`; batch/VOD settings are separate. |
| `CIVICCAST_WHISPER_BEAM_SIZE` | `1` on CPU, `5` on CUDA | Whisper decoder beam width. It does not tune Whistle. Wider is slightly more accurate and roughly linearly more expensive. |

### Turning live captions off

`PUT /api/staff/station/profile` with `{"live_captions_enabled": false}`
(role: `setup_admin`), or `true` to turn them on. The persisted Station Profile
setting defaults off (`LIVE_CAPTIONS_DEFAULT` in `civiccast/installer/models.py`).
The caption tap checks it on each worker scan: switching captions off stops
transcription on the next scan and discards queued tap audio. Turning captions
on lets the worker process audio on its next scan, but the GStreamer audio-tap
and caption-embed graph legs are built only when a channel starts. Turning
captions off likewise removes those graph legs on that channel's next **START**.
A content reload does not rebuild them. A station-state file without the key
reads as off; an explicitly stored `true` is kept across upgrades. The
safe-to-air gate can expect captions before a running channel has rebuilt its
embed leg, so start that channel after enabling captions.

Every runtime reader of the switch -- the egress strategy, the safe-to-air
banner, and the `is_enabled` callbacks of the caption tap, feed, and proof
workers -- goes through `resolve_live_captions_enabled_or_default()`
(`civiccast/installer/station_state.py`): a locked or corrupt
`station-state.json` lands on the shipped default (`LIVE_CAPTIONS_DEFAULT`)
and is logged once per process, rather than aborting a worker's scan with a
traceback every poll.

While it is off the tap transcribes nothing, blanks every channel's live
caption file, reports `"state": "disabled"`, and *deletes* the forked audio
rather than filing it as evidence. The caption feed and the caption
decode-back proof workers (`civiccast/egress/caption_feed.py`,
`civiccast/egress/caption_proof_worker.py`; both only built when
`CIVICCAST_EGRESS_EMBED_CAPTIONS` is on) also re-read the switch on every
scan and idle while it is off -- no sidecar poll, no ffmpeg capture, no FAIL
proof row -- and resume on their next cycle once it is on. The runtime
safe-to-air signal (`GET /api/staff/runtime-safe-to-air`) does not apply its
caption-readiness gate while the switch is off; each channel reports
`captions_expected: false`. Captions on **published recordings** (the
offline caption job below) use their separate recorded-caption settings;
this switch controls only live captions.

`CIVICCAST_CAPTION_TAP=off` in the native service environment overrides the
profile setting and disables the tap. The native runtime normally sets the
mode to `inline`; it reads the service environment when its child starts, so
changing that override requires restarting the native runtime. The Station
Profile value is checked on each tap scan, but GStreamer graph legs are added
or removed only when a channel next starts. A content reload does not rebuild
the audio tap or caption embed.

### Optional retained-evidence mode and runtime diagnostics

The retention behavior below applies only when a caption worker is explicitly
constructed with `retain_review_evidence=True`. The native station wiring
leaves this option off, so live captions do not create permanent review records
or retain an audio-evidence archive.

The live tap makes its first retained-audio readiness check synchronously.
Later retention sweeps run on a guarded background thread rather than
blocking every transcription scan. Only one sweep may be in flight;
transcription still refuses work when the latest retention result is not
ready. During a periodic recheck, transcription and caption text can continue,
but no new evidence, processed or quarantined WAV is retained. Review rows
created in that interval have no retained audio clip; the review interface
reports that the clip is unavailable. A definitive storage refusal stops new
transcription and publication and discards settled input audio. Permission is
checked again after transcription, at the point audio would be stored.
Disabling live transcription does not disable retention of audio
already collected. The separate offline-caption worker retains its own
readiness check; do not confuse the two workers' scheduling.

After model preparation the tap logs `Caption runtime resolved after
prepare`, with requested and actually loaded device/compute type plus worker
capacity. Check `loaded_device=cuda` and the loaded compute type when proving
GPU use. An environment request for CUDA alone is not evidence that CUDA
loaded. If backend identity is unavailable, the diagnostic says so rather
than presenting the request as the result.

The beta.10 candidate's lab evidence and its limits (including 13 catch-up
discard events in an eight-hour three-channel run) are in the
[verification record](../releases/v1.0.0-beta.10-verification.md). These
changes do not raise the backlog threshold or shorten overload backoff.

If a channel's caption-session reset fails (for example, a locked caption
file), broadcast can continue but that session's caption embedding and audio
tap stay disabled. The channel reports the reset failure and caption status
is not verified. A content reload or duplicate Start cannot clear this guard.
Resolve the storage problem, then Stop and Start the channel at an appropriate
time; only a successful new-session reset re-enables its captions.

### Captions are best effort; playout wins

**Read this before tuning any of the numbers above.** Live captioning and the
GStreamer playout workers share one box. If they compete, the air signal loses
first and the viewer sees it — a playout worker that stops producing output
for ten seconds is killed and restarted by its own watchdog, which is a visible
interruption to the broadcast. A caption that arrives late, or not at all, is
not.

CivicCast therefore enforces an explicit ordering, and none of it is
negotiable at runtime by the caption feature itself:

- **ASR is bounded.** Whistle primary inference is serialized across channels
  within one station runtime; independent station runtimes do not share that
  lock. Whistle fallback requests have a separate lock. The tap worker bounds
  channel work with `CIVICCAST_CAPTION_TAP_MAX_CHANNEL_WORKERS`; direct Whisper
  selection uses its configured CTranslate2 worker concurrency. NVIDIA CUDA
  can accelerate Whisper; it does not move Whistle off CPU.
  If any channel's settled backlog exceeds `..._MAX_BACKLOG_SEGMENTS`, its old
  audio is handled by the catch-up path below rather than queued indefinitely.
  An explicit `CIVICCAST_CAPTION_TAP_MAX_CHANNEL_WORKERS` value remains
  authoritative.
- **A persistent overload catches up; only a hopeless one pauses.** When a
  channel's backlog stays over `..._MAX_BACKLOG_SEGMENTS` for the whole
  persistence window, the tap **sheds** the oldest settled segments down to the
  newest `..._MAX_BACKLOG_SEGMENTS` and keeps captioning from there — one
  `WARNING` per episode, no blanked caption file, no pause. A historical U22
  measurement (2026-09-25) found faster-whisper on CUDA kept up on average —
  2.8–3.4 s per 10 s window against a 5 s cadence, 1.86× headroom — but a
  single window could take 10.6–19.5 s as three channel callers shared one GPU
  model. These figures are not a Whistle CPU capacity result. Shedding costs
  the seconds the box genuinely could not transcribe and nothing more.
  A channel that sheds `..._CATCH_UP_SHED_LIMIT` times inside
  `..._CATCH_UP_SHED_WINDOW_SECONDS` is a different animal — it is shedding as
  fast as the persistence window allows and still not holding the cadence — and
  *that* channel is **paused** on the retained ladder: an exponentially growing
  window (120 s, 240 s, 480 s … capped at 15 minutes), its active caption file
  blanked, and its stale audio **discarded**. During the pause the station
  spends *nothing* on transcribing that channel, and keeps nothing: audio that
  was never transcribed cannot be reviewed, so retaining it would only grow an
  unpruned directory of raw broadcast audio for as long as the station could
  not keep up. Captions resume automatically once the window expires and the
  backlog is clear; a channel has to stay healthy for several scans before its
  escalation is forgiven.
- **Playout outranks the caption caller thread.** On Windows the playout
  workers are spawned at `ABOVE_NORMAL` priority class. The caption tap lowers
  its own Python ASR caller threads to `BELOW_NORMAL`; this does not reprioritize
  a separate Whistle process or Whisper's internal CTranslate2 pool. Treat the
  thread setting as a scheduling hint, not a CPU-capacity guarantee.

**What you will see in the log.** Two different `WARNING` lines, deliberately
worded so a grep for one does not find the other:

- One per **catch-up**, naming the channel, how many settled segments stayed
  over the maximum for the whole persistence window, how many oldest segments
  were discarded with the seconds of audio they carried, and that captions are
  still running — e.g. `Caption tap catch-up for channel education: 4 settled
  segments stayed over the maximum 2 for the whole 15-scan window. Discarded
  the oldest 2 segment(s) (10.0s of audio, indices 118-119) and resumed at the
  newest 2. Live captions continue; the pause is not engaged (shed 1 of 3 in
  300s).` The seconds come from each segment's own WAV header, so they are the
  real audio skipped. Occasional catch-ups on a loaded station are expected and
  need no action: that audio could not have been transcribed in time.
- One per **pause**, naming the channel, the backlog, the pause length and how
  many times that channel has overloaded — *not* a `CRITICAL` line every scan.
  A pause now means the channel shed its full budget inside one window and
  still could not hold the cadence. Repeated escalations on the same channel
  mean the station cannot transcribe that channel in real time.

**What you will see in the status file.** Each channel publishes
`<egress work dir>/<channel_id>/captions/runtime-status.json`:

```json
{
  "channel_id": "education",
  "state": "paused",
  "backlog_segments": 3,
  "max_backlog_segments": 2,
  "resume_in_seconds": 118.4,
  "consecutive_overloads": 2,
  "updated_at": "2026-09-05T18:20:11.482913+00:00"
}
```

`state` is `within-capacity`, `paused`, `overloaded`, `storage-refused`, or
`disabled` (the operator switch above). **A catch-up is not an overload**: a
shedding channel keeps publishing `within-capacity` with its
`consecutive_overloads` at `0`, so this file still means what it always meant —
only the pause ladder appends to `overloaded_channels`/`paused_channels`. Use
the log to see catch-ups; use this file to see a channel in trouble.

**If a station is permanently paused,** reduce caption work: use a lighter
caption tier or caption fewer channels at once. Optional NVIDIA acceleration
applies to Whisper, not to the Whistle CPU primary. Whisper thread settings do
not tune Whistle.

Live captions are on by default. When enabled, Whistle is the CPU primary;
Whisper can serve as a sticky fallback for the affected channel, or can be
selected directly with `CIVICCAST_LIVE_CAPTION_ENGINE=whisper`. The native tap
publishes live VTT and does not automatically create review records or retain
audio evidence. Published-recording captions use the separate offline job and
review workflow below.

## Offline caption job (published-file captions)

The live tap above is the accessibility path. This one is the **legal**
path: captions on the file a station publishes. Approving publish for a
recording queues a durable job in `offline_caption_jobs`; the worker
transcribes the recording's audio with the station's staged caption model
and queues the recording's cues for operator review before attaching them to
the published file. The live tap publishes VTT through its separate path.

Every offline worker tick also runs the retained-audio-evidence retention
sweep (`CaptionEvidenceRetentionPolicy.from_system`,
`civiccast/captions/retention.py`) for its review WAVs. This policy is shared
with optional live-tap evidence retention, but the standard native live path
does not retain review evidence or gate live-caption readiness on this sweep.
A station running offline/VOD captioning with no airing live channel still
gets retention cleanup for its per-cue WAVs. A sweep *failure* (an exception,
including building the policy the first time) never fails the caption job it
runs alongside; it is logged and retried on the next tick.

A clean sweep *result* is a different thing from a failure, and is honored:
when the sweep reports the storage is not ready (the free-space reserve
would be breached, or retained evidence is still over the storage cap even
after pruning everything eligible), `run_once` skips every due job that
tick -- neither stage-one transcription (which writes new evidence WAVs)
nor stage-two publish runs. This readiness gate applies to the offline job;
it does not describe ordinary live-caption scans, whose standard native
configuration leaves retained-review-evidence mode off.

The sweep's free-space reserve and storage-cap decisions are measured
against the volume that actually holds this worker's evidence WAVs: the VOD
package root (`CIVICCAST_VOD_PACKAGE_DIR`, or `CIVICCAST_UPLOAD_DIR` +
`.civiccast-packages` -- the same resolution
`civiccast.schedule.paths.resolve_vod_package_root` gives every queued
job's own `package_dir`), not the live egress work directory
`CaptionTapWorker` measures (audit finding, P1; those can be different
filesystems, and reading the wrong one could let free-space or the storage
cap silently blow out on the volume evidence is actually written to).

Evidence WAVs shared by several cues from the same ASR window (one
low-confidence chunk that produced multiple cues, so
`_offline_audio_evidence_factory` / `CaptionTapWorker._audio_evidence_factory`
attached the same file to each) are retained until the *latest* of those
cues' resolutions, not the earliest: `_discover_candidates`'s per-path
coalescing tracks the maximum `resolved_at` across every review row sharing
a path, order-independent (audit finding, P2; fixed directly rather than
deferred -- the change only ever delays a prune, never advances one, so it
cannot make live evidence disappear earlier than before). This is shared
code path for both the live tap and the offline job.

The job has two stages because operator approval sits between them: for this
recorded-file workflow, caption cues are reviewed before they attach to a
published VOD. Live VTT follows a separate path and may be published without
operator review.

1. `pending` → transcribe and queue for review. **Publishes nothing.**
2. `awaiting_review` → re-checked each poll. Once every queued cue has an
   operator decision, the approved/edited text is attached to the packaged
   VOD: a segmented WebVTT track declared in the multivariant manifest
   (`captions/<lang>/playlist.m3u8`) plus a flat whole-recording
   `captions/captions.vtt` for records requests. Both are served by the
   existing published-gated `/media/vod/{asset_id}/...` route.

Rejected cues are dropped. Rejecting all cues does not publish rejected text
or falsely establish that a usable caption track exists.

**When captions exist relative to publication.** Approving publish makes the
recording public immediately; the caption job runs afterwards, because a
public record must not wait days on caption review. The operator-facing
statement is: *the recording is public immediately; selected captions attach
after review according to the station's completion setting.*

When recorded captions are enabled, `POST /api/staff/publish/assets/
{asset_id}/approve` queues the caption job **before** it approves anything,
and returns **409** naming the cause if it cannot — no caption job store, no
upload storage, or a failing enqueue. Nothing is published in that case, so
there is no window in which a recording is public with no caption job and
only a log line to say so. The one case that is still a skip rather than a
block is an asset with no local recording file: there is nothing to
transcribe, so no caption job is owed. A portal *retry* that publishes cannot
be pre-checked the same way (the recording is public by the time the retry's
outcome is known), so it raises a 409 that says plainly that the portal
publish succeeded and the caption job did not queue.

**Recorded-caption station settings.** Under **Setup > Station Profile >
Recorded captions**, choose enabled/disabled processing, English with selected recorded translations, and whether caption completion requires all selected languages or
reviewed English. Existing stations retain the enabled English-and-Spanish,
all-selected-languages defaults. English-only operation needs no translation
runtime. Disabled processing skips new caption jobs and pauses existing work;
it does not reject station startup or mark missing captions complete.

Recorded target codes are `es`, `zh-Hans` (Simplified Chinese only), `fil`
(Tagalog/Filipino), `vi`, and `ar`. The existing local translation adapter uses
the requested target name and supplied terminology; unsupported targets fail
explicitly. Each target uses the same source-wording/window review identity,
completeness gate and human review as the Spanish example below. Published
`captions.<code>.vtt` sidecars and HLS tracks are included in CDN republishing
and rebuilt from matching reviewed cues during repackaging. English remains
the default. A missing model or rejected/pending target leaves the job actionable;
no unreviewed translation is attached. No live-translation or language-accuracy
certification is implied.

With the Spanish leg running, `awaiting_review`
becomes a two-phase gate. Once the **English** review pass is complete and
something was approved, the approved English cues are translated to Spanish
through the same operator-selected translation tier the live tap uses (local
TranslateGemma by default), and the Spanish cues are queued for their **own**
operator review pass — the Spanish text is AI output too, so spec §4.2's
operator-review-before-publish applies to it. With all selected languages
required, both tracks attach after both reviews. With reviewed English
required, English may attach first and Spanish follows after its own review.
The recording itself remains public while caption review proceeds. Track paths
are English `captions/en/playlist.m3u8` default + `captions.vtt`, Spanish
`captions/es/playlist.m3u8` secondary + `captions.es.vtt`. The two passes are
separated by a `language` column on `caption_review_items` (migration
`0083_caption_review_language`, default `en`); the operator console's review
queue shows an EN/ES badge and a language filter. Spanish review rows are
created `low_confidence=False` (a translation of human-approved English has no
ASR audio to retain), so the low-confidence audio-evidence approval gate
cannot deadlock the Spanish track.

No way the caption pass can come up empty resolves to a green job:

* **Spanish selected but no translation runtime available.** With
  `all_selected_languages`, the job records an attempt and remediation on
  `last_error`, retries on normal backoff, and lands in `failed` if its
  attempt budget runs out. With `english_reviewed`, reviewed English attaches
  and the active job keeps waiting for Spanish, with the remediation visible.
  English-only jobs need no translator. Fix translation in **Settings → AI
  Models → Translation**, or run `civiccast doctor`.
* **The operator rejected every Spanish cue.** The job stays in
  `awaiting_review` with a remediation on `last_error` and does **not** burn
  the retry budget: the block is a human decision, not a fault. Open the
  caption review queue, filter to Spanish, and edit the cues with the correct
  wording (or approve the ones that are right) — review decisions are not
  terminal, so a rejected row can be edited. Spanish attachment continues on
  the next poll after review finishes with at least one approved or edited
  cue. Under `english_reviewed`, reviewed English can already be attached.
* **The operator rejected every English cue.** Symmetric with the Spanish
  case: no English means nothing to translate and no track in either
  language, so the job stays in `awaiting_review` with its own remediation
  rather than completing with zero cues attached. If the audio is genuinely
  unusable, a technical admin cancels the job; the worker will not decide
  that on its own.
* **The translation pass came back short.** `queue_translated_captions`
  writes one review row per cue, so a store failure partway through a long
  meeting leaves fewer Spanish rows than approved English cues. The job
  compares the stored Spanish cue ids against the ids the approved English
  cues *should* produce, queues only the missing ones on the next attempt,
  and fails with a remediation naming the shortfall ("3 of 6") if they still
  cannot be written. A short Spanish track is never attached. Note the
  distinction: an operator *rejecting* some Spanish cues is an editorial
  decision on rows that exist and legitimately yields a shorter track; a row
  that was never created is data loss and blocks.
* **An English cue was revised after it was translated.** The expected
  Spanish review-item id binds the source *wording*, not just the source cue
  (a short digest of the approved English text, via
  `translate.service.translated_cue_id`), so editing an English cue after the
  Spanish pass makes that cue's translation "missing" and re-queues it for
  review — only that cue; unedited cues keep their ids and are not
  re-translated. The superseded Spanish row stays in the queue as a record
  and is never attached, even if it was approved. Without this, a cue
  corrected from "the motion carries" to "the motion FAILS" published English
  saying one thing beside Spanish saying the other, with the job green.

Both hold states are idempotent — the job row and the log are written once
per state change, not once per 60-second poll.

**CDN republish after review.** Attaching captions rewrites the multivariant
manifest and writes the caption files on **local disk**. When the asset's
package was published to a CDN (`CIVICCAST_CDN_PROVIDER`, or credentials
entered in the setup wizard) before caption review finished, the CDN copy
still has the pre-caption manifest — so the job re-uploads the rewritten
manifest, both segmented caption tracks, and both flat sidecars to the same
key prefix the package was published under
(`civiccast/captions/cdn_republish.py`, through the same
`upload_package_files` helper the finalization worker publishes with, so the
manifest still uploads **last**). Only the caption artifacts are re-uploaded;
the video renditions are unchanged. A republish failure fails the job with the
provider's message on the row rather than completing it — a green job on a
stale CDN manifest would be a false claim that the recording is captioned.
Nothing is uploaded when no CDN is configured, or when this station has no
record of publishing that package to the configured CDN.

| Variable | Default | Meaning |
|---|---|---|
| `CIVICCAST_OFFLINE_CAPTION_JOB` | `inline` | Initial processing default until recorded-caption station settings are saved: `inline` enables processing, `off` disables it without rejecting startup. Models load lazily when work needs them. |
| `CIVICCAST_ALLOW_CAPTIONS_OFF_FOR_TESTS` | unset | No longer required. Disabling recorded-caption processing is a supported station choice. |
| `CIVICCAST_OFFLINE_CAPTION_POLL_SECONDS` | `60` | Scan interval. |
| `CIVICCAST_OFFLINE_CAPTION_BACKOFF_SECONDS` | `300` | Base for exponential backoff (300s, 600s, 1200s, …). |
| `CIVICCAST_OFFLINE_CAPTION_MAX_ATTEMPTS` | `4` | Attempts per stage before the job is marked `failed` with its reason. |
| `CIVICCAST_OFFLINE_CAPTION_CHUNK_SECONDS` | `30` | Audio handed to the model per call. Offline has no latency budget, so this is Whisper's own encoder window rather than the live tap's 5 s. |
| `CIVICCAST_OFFLINE_CAPTION_SPANISH` | enabled | Initial language default until station settings are saved. A false value (`off`/`0`/`false`/`no`) selects English only; a true value includes Spanish. |

The saved station policy lives in `offline-caption-settings.json` beside the
station-state file. It records `enabled`, `languages` (`en` and optional `es`)
and `completion_requirement` (`all_selected_languages` or `english_reviewed`).
Saving station settings takes precedence over these initial environment defaults.

Model, device, and compute type are **not** configured here — the job builds
its runtime through the same seam the live path uses, which resolves the
operator-selected caption tier and inherits the hardware-adaptive
device/compute-type the native station runtime published
(`CIVICCAST_WHISPER_DEVICE` / `CIVICCAST_WHISPER_COMPUTE_TYPE` /
`CIVICCAST_WHISPER_MODEL_PATH`).

A `failed` job stays in the table with its reason; the recording remains
published and uncaptioned rather than shipping unverified text. Re-approving
publish for that asset queues a fresh job.

**Operator visibility (staff API):** the router (`civiccast/captions/router.py`)
has no React screen yet, but exposes:

- `GET /api/staff/captions/offline-jobs` — list jobs, optionally filtered by
  `asset_id` and/or `state`, with `state`/`attempts`/`last_error` on each row.
- `POST /api/staff/captions/offline-jobs/{job_id}/retry` — reset a `failed`
  job to `pending` with a fresh attempt budget, without re-approving publish.
  Requires the `records_clerk` role. 409 if the job is not currently `failed`,
  or if a different job is already active (`pending`/`awaiting_review`) for
  the same asset -- retrying a failed job must not put two active jobs on
  one asset (audit finding, MAJOR; see the partial-unique index note
  below).

Only a **Portal** retry (`POST
/api/staff/publish/assets/{asset_id}/surfaces/portal/retry`) queues offline
captioning; retrying any other surface (YouTube, Internet Archive, ...) does
not, even on an asset with no caption job yet, since only Portal success
makes the recording public. This also means retrying an unrelated surface on
an asset whose captions already completed never starts a second
transcription pass for it.

Two concurrent enqueue attempts for the same asset (a publish approval racing
a retry, for instance) cannot both create an active job: a partial-unique
index (`ix_offline_caption_jobs_one_active_per_asset`,
`0075_offline_caption_jobs`) allows at most one `pending`/`awaiting_review`
row per asset. The loser of that race gets back the winning job instead of a
duplicate. The manual retry endpoint above is guarded by the same index --
it pre-checks `active_for_asset` before reopening a `failed` job, and the
durable store also catches the index's `IntegrityError` on the write itself
(closing the gap between that check and the write), so a race there ends in
a clean 409 rather than two active jobs or a raw 500.

### Known follow-ups (out of One v1 scope)

CivicCast One v1 serves uploaded files through the local portal VOD path;
LIVE broadcast and CDN delivery are deferred to keystone K4. The items below
were consequences of that scope line. Both have since been closed.

1. ~~**A live-finalized recording would transcribe but fail to attach.**~~
   **Closed.** `_queue_offline_captions` used to resolve the package
   directory with `resolve_vod_package_dir` alone, which only knows the
   **upload** convention (`.civiccast-packages/<asset_id>` under the upload
   root). A live-finalized recording packages somewhere else entirely --
   `<recording_path.parent>/<live_session_id>-hls/`, recorded on the
   finalization job's `local_package_manifest_path` -- so stage two would
   have failed every retry against a directory that was never written.
   Deferred while it was only a stage-two failure; it stopped being
   deferrable the moment an unqueueable caption job began blocking approval,
   because a live station with no upload root was then refused permission to
   publish at all. `_resolve_caption_package_dir`
   (`civiccast/publish/router.py`) now checks the finalization job's manifest
   path first, resolved through the same `finalization_worker.get_status`
   seam the route already uses for the recording file, and falls back to the
   upload convention. The gate still closes when neither resolves.

   How far this agrees with the media-serving path
   (`civiccast.stream.media_router._package_dir_for_asset`): the
   **live-finalized precedence matches** — the finalization job's manifest
   path wins in both. The **upload branch does not**. The caption resolver
   returns the standard `.civiccast-packages/<asset_id>` path only, with no
   existence or containment checks; the media router additionally validates
   the asset's `manifest_url`, checks containment, requires `playlist.m3u8`
   to exist, and falls back to the legacy pre-rc14 shared `<file_path>/hls`
   location. **Known gap:** a legacy pre-rc14 package at `<file_path>/hls` is
   not resolved by the caption gate, so publishing such an asset is blocked
   even though the media router can still serve it. Blast radius is stations
   upgraded across rc14 that still hold pre-rc14 packages; new packaging
   never writes that path.

2. ~~**Caption attach never re-uploads to a CDN.**~~ **Closed.** Caption
   attach still writes only local disk, but `OfflineCaptionJobWorker` now
   re-publishes the rewritten manifest and the new caption files to the CDN
   the package was published to — see "CDN republish after review" above and
   `civiccast/captions/cdn_republish.py`. What remains scope-limited is the
   *reachability*: the only writer of CDN-published VOD packages is
   `LiveFinalizationWorker`, and follow-up 1 above still keeps live-finalized
   recordings out of the offline caption path, so on a One v1 station the
   republisher's lookup finds no CDN-published package and correctly does
   nothing. The code path is proven by the mock-CDN tests in
   `tests/captions/test_caption_cdn_republish.py`, not by a live CDN run.
