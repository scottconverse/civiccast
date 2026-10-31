# Local recording audio description

Source branch: `feat/local-audio-description`. This is a local feature integration,
not a change to the current RC2 installer, release candidate or installed station.
First target is CivicCast. No Townlight or Records integration is claimed here.

The asset detail page generates a separate audio-described edition without a new
staff description-approval queue. The worker compares sampled current frames and
recent accepted text context with existing caption dialogue, records ambiguous
details rather than speaking
guesses, consolidates repeated observations, protects detected speech and vocal
sounds, measures fixed-voice narration, and fits it into non-dialogue gaps. Music
alone no longer blocks narration; it is lowered by 12 dB during inserted speech.
Descriptions that cannot fit remain explicit pause-to-describe entries. Existing asset publication permissions
still decide whether residents may watch a recording.

## Dependencies and configuration

Install the project's `audio-description` extra in its own environment. It pins
Pocket TTS 4.0.1, Pillow 13.0.0 and WebVTT 0.5.1, with ONNX Runtime >=1.22,<2
for the local Google YAMNet speech classifier; FFmpeg/ffprobe must
already be on the station process PATH. Configure a local Gemma 4 12B vision model
under **Settings → AI Models → Audio description**. That feature has only a local
tier and does not change summary, translation or caption model selections. E4B is
not offered here because the tested runtime gave unreliable visual facts.

Provisioning is an explicit administrative operation before processing civic media:
`python -m civiccast.audio_description.provision --directory <narrator-folder>`.
It downloads pinned public non-cloning model, tokenizer, Alba voice and YAMNet
classifier files, verifies SHA-256, and writes local configuration and attribution
notices. Internet is required for provisioning, not generation. Keep that folder
intact after setup. Existing narrator installations need provisioning again to add
`yamnet.onnx`; existing verified narrator files are reused.

The classifier uses Google's Apache-2.0 YAMNet architecture and the ONNX conversion
at `zeropointnine/yamnet-onnx`, revision
`ac2ca3bd45d12ec1f19f1144205ea529b4e9dedf`, model SHA-256
`1510041dce24a2e9e84ec546807ac408ae496da6d1ed41bc3ccba649623f8e19`.
The worker verifies that pin and never downloads a replacement during processing.
It classifies bounded audio blocks on CPU, protects caption times with padding,
and protects detected speech even when music is present. Classification remains
fallible; a gap is not a guarantee that every meaningful sound was recognized.

| Variable | Purpose |
| --- | --- |
| `CIVICCAST_UPLOAD_DIR` | Existing configured media root; mandatory for this feature. Existing recording-target roots are also accepted as sources. |
| `CIVICCAST_AD_VOICE_DIR` | Provisioned narrator folder containing local.yaml, model.safetensors, tokenizer.json and alba.safetensors. |
| `CIVICCAST_AD_SOUND_DIR` | Optional folder containing pinned `yamnet.onnx`; defaults to the narrator folder. |
| `CIVICCAST_AD_DATA_DIR` | Optional independent derivative/job folder; defaults to `.civiccast-audio-description` below the upload root. |
| `CIVICCAST_AD_OLLAMA_URL` | Explicit local Ollama HTTP endpoint, default `http://127.0.0.1:11434`. Only literal loopback addresses are accepted; no cloud tags or proxy routing. |

Development must redirect all station data/configuration roots and use its own
database, media folder, model endpoint and service port. Never point a feature
checkout at installed station AppData or the release candidate's database.

## Operator and resident use

1. Package the final intended recording with its English caption track. Caption
   times are already relative to the packaged trim; the worker does not subtract
   the trim twice.
2. Open the asset's **Audio description** panel and generate. One job is admitted at
   a time; closing the page does not cancel it. Background inference waits at safe
   stage/request boundaries while a live session or playout channel is on air.
   An already-running model call is not immediately preempted.
3. Cancel at a safe boundary or resume interrupted work. Checkpoints verify retained
   output hashes. Changed source, trim, captions, model digest, runtime, narrator or
   implementation requires regeneration if it no longer matches that job.
4. Open the private preview, or publish the asset through its existing workflow.
   Residents and asset embeds then expose **Use audio-described playback**. The
   original HLS player remains the default. Described playback keeps the media clock,
   offers English captions, and can pause the video for queued descriptions. Short
   cue windows crossed between browser updates are queued, including at video end;
   deliberate seeking resets that queue instead of narrating skipped scenes.

The described MP4 includes a complete program soundtrack plus in-gap narration.
Pause-only descriptions are played separately while the browser pauses the video;
they are not silently inserted over dialogue in the downloaded MP4. The narration
is synthetic fixed-voice speech, not voice cloning. Source media is read only.

## API contracts

| Method/path | Behavior |
| --- | --- |
| `GET /api/staff/assets/{asset_id}/audio-description` | Durable job status and coverage counts. |
| `POST .../generate` | Admit a new job; returns 202 and job ID. |
| `POST .../resume` | Resume interrupted/failed/cancelled job with matching identity. |
| `POST .../cancel` | Request cooperative cancellation; preserves completed checkpoints. |
| `POST .../preview-session` | Issue a fifteen-minute, HttpOnly, asset-scoped preview cookie and URL. |
| `GET /media/staff-audio-description/{asset_id}/preview` | Private preview; scoped cookie required. No staff bearer token appears in a URL. |
| `GET /api/public/assets/{asset_id}/audio-description` | Playback manifest only for a currently published asset and authorized viewer. |
| `GET /api/public/assets/{asset_id}/audio-description/{filename}` | Allowlisted description media with publication/access rechecked on every request. |

Staff job controls require records_clerk, meeting_operator, support_admin or
setup_admin. Viewer access uses the existing paywall policy and verified viewer
cookie. A source/trim replacement invalidates the previous description pointer.
Responses are private/no-store. File names are allowlisted and resolved inside the
derivative directory; job logs, frame prompts and local source paths are not public.
Generated OpenAPI and operator TypeScript contracts document the full response shapes.

## Storage, recovery and limits

Storage has one owning process (OS lock), one active job, per-asset `job.json`,
source-bound stage checkpoints, private `worker.log`, and a `current.json` pointer
to the last successful edition. A failed replacement does not remove a previous
successful edition of the unchanged source. After an API restart, a running job is
reported interrupted and can be resumed explicitly. Back up the derivative root
with the corresponding sources and caption packages to retain jobs across machines.

Generation uses no cloud API or processing telemetry. ONNX Runtime telemetry is
explicitly disabled. Python worker connections are restricted to loopback, Hugging
Face offline/telemetry-off flags are set, and
FFmpeg inputs accept local file/pipe protocols only. Provisioning performs explicit
downloads. Long recordings incur real local inference, CPU and disk costs; this is
not a promise of realtime description. Initial bounds are one asset at a time,
one second to 24 hours, and 12,000 sampled visual events. Local vision requests
keep the model warm under a five-minute lease rather than reloading it for each
frame. Job completion, failure or cancellation attempts an explicit unload; the
lease bounds retention if the model server is unavailable during cleanup.

Coverage reports in-gap descriptions, pause-required descriptions and observations
with uncertain details. It does not certify that every meaningful visual was found,
that a model interpretation is true, or that a video satisfies a legal standard.
Scene and periodic samples are separated by at least two seconds to avoid
near-coincident repeated descriptions. Each observation first receives a current-frame
reading without prior descriptions. A separate call sees the same current image
with recent text for identity continuity, but never sees the first answer. Only
shared supported content is retained; conflicting labels are recorded as uncertainty
and omitted rather than resolved by guessing. Printed quotes must agree exactly.
This conservative lexical check can omit valid descriptions that are phrased
differently. Speculative comparisons such as "stone-like creature" retain the
broader "creature" label, with the comparison preserved in the uncertainty record.
Shortening preserves newspaper/headline/article attribution; a headline about a
robot cannot become an unexplained claim that a robot is on screen. A description
that cannot be shortened safely remains a full pause description. Both calls use
the same local model: agreement does not prove truth.
Each request receives only its current image; multi-image target focus proved
unreliable in the film evaluation. Low-contrast fading frames and printed claims accompanied by
unreadability warnings are omitted with uncertainty rather than spoken as facts.
Frame sampling can miss brief events; small print, maps and factual grounding remain
model limitations. Input captions can contain recognition errors. The completed
original and described MP4s use the same trimmed clock; pause narration does not
change program timestamps. Original HLS keeps its existing language/caption features;
the described edition currently carries the packaged English caption track.

## Attribution

Selected engine helpers are adapted from Microsoft's ai-audio-descriptions under
MIT, with its license retained in `civiccast/audio_description/vendor/LICENSE.md`.
Active processing uses local adapters, not the upstream Azure path. Pocket TTS code
is MIT; its non-cloning model weights and the fixed Alba voice require CC BY 4.0
attribution. The provisioning notice and player credits retain those credits.
Google YAMNet and its pinned ONNX conversion are Apache-2.0; `YAMNET-NOTICE.txt`
records their source and model identity. Gemma is Google's local vision model;
provisioning/model terms remain applicable.
Existing FFmpeg distribution/source-notice obligations are unchanged by this branch.
