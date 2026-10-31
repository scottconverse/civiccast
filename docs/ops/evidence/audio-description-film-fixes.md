# Audio-description film fixes — October 31, 2026

Implemented locally on `feat/local-audio-description`, independent clone
`C:\Users\scott\Desktop\Code\civiccast-audio-description`.
No push, RC changes, installer, release, live station data or default-model change.

## Five reported defects

1. Wrong visual labels and fabricated printed words: use only the current frame;
   compare an unprimed read with a separate context-aware read which never sees
   the first answer. Conservative lexical agreement is not independent truth.
   Omit disagreement, unreadable printed claims and low-contrast frames; broaden
   speculative creature/object comparisons. Preserve raw reads and uncertainty.
   Shortening retains newspaper/headline/article attribution. The reproduced
   skull/helmet, cheese headline and species/material labels are absent from
   accepted final narration. All accepted final facts were inspected against the
   exact sampled frames. This guards these examples, not every possible hallucination.
2. Repeated output: merge cut/periodic samples at least two seconds apart and
   consolidate equivalent observations while preserving changed facts. Final
   clips have no exact repeated narration and no near-coincident sampled frames.
   Two distinct control-panel facts at 49.99s are retained and queued serially.
3. Music/effects blocking placement: pinned offline Google YAMNet distinguishes
   speech/vocals from music, combined with caption protection and padded spans.
   No narration overlaps the computed protected intervals on these clips. This
   is not a human-labeled universal speech-detection score. Music is ducked 12dB.
4. Browser misses very short pause cues: queue every crossed cue instead of
   requiring timeupdate to land inside it. Seeking clears/reset queues; final
   cues are checked at media end. Real playback of the original Tears output
   fetched all 43/43 narration files, including the former 40/80ms misses, and
   reached 2:00. Five player tests pass.
5. Model repeatedly reloads: retain Gemma for a bounded five-minute lease during
   the job and explicitly unload in finally. Fresh uncached guarded runs reduced
   load overhead from roughly 69–73% to 2–3% of model request time.

## Same three 120-second excerpts — final artifacts

| Clip | In-gap | Pause required | Uncertain observations | Narration |
|---|---:|---:|---:|---:|
| Tears of Steel, 0:00–2:00 | 2 | 6 | 24 | 29.76s |
| Sintel, 2:00–4:00 | 1 | 4 | 29 | 18.24s |
| Big Buck Bunny, 0:45–2:45 | 2 | 0 | 19 | 5.76s |

The three original full-film sources and excerpts remain byte-identical. All
manifest output hashes match. Original and described MP4s for all three clips
decode without errors and retain 120s duration. Exact narration repetitions,
sample gaps below 0.5s, and computed protected-span overlaps: zero.

## Verification and performance provenance

33 backend feature/regression tests; five player tests; strict mypy on 15 modules;
Ruff; affected frontend lint; TypeScript/Vite production build; offline lockfile
check; built-in manual render freshness; git diff whitespace check: pass locally.
No GitHub CI or installed production-station acceptance was run for this change.

Fresh uncached guarded inference: Tears 270.899s (baseline 1204.231s), Sintel
351.420s (1254.627s), Bunny 240.958s (732.690s). Receipts are retained as
`fresh-results.json`, `fresh-verification.json`, and `fresh-comparison.json`.
Two subsequent deterministic safeguards preserve headline attribution and remove
speculative X-like/X-looking creature/object labels. Final normal-worker jobs
were regenerated with new implementation-bound checkpoints. Only matching
model-response cache files were copied, not checkpoints; changed-context requests
were recomputed. Their 30.133s/60.242s/110.420s elapsed times are cached regeneration,
not an independent fresh performance benchmark. `cache-provenance.json` records
copied hashes and `checkpoint_copies: 0`; `final-source-binding.json` checks each
job's implementation identity against the tested source.

Rejected exploratory outputs remain explicitly separate from final results.
No human edits were made to the final generated scripts or audio.

## Important remaining limit

Conservative agreement sacrifices coverage: Bunny retains only two descriptions
and omits much of its visual action; Sintel also omits ambiguous dragon shots.
The model has not become a reliable universal visual interpreter. These observed
software defects are addressed, but comprehensive automatic audio description,
full-film quality, human-listener usefulness and WCAG compliance are not proven.
No new validation campaign or additional product work was started.

## Playback and evidence

Final fixture preview: http://127.0.0.1:8795/#/watch/blender-tears-of-steel
Other fixtures: `#/watch/blender-sintel`, `#/watch/blender-big-buck-bunny`.
Select audio-described playback; optional pause narration and English captions.
The fixture does not populate normal HLS/transcript/agenda data; described MP4
playback uses the actual feature component. This is not production auth proof.

Local evidence root:
`C:\Users\scott\Documents\Codex\2026-09-21\re\outputs\civiccast-blender-20261031\fix-verification`.
Final results: `results.json`, `verification.json`, `final-source-binding.json`;
unaltered scripts/exact-frame contact sheets: `review/`; browser cue evidence:
`playback-regression-requests.json`, `player-original-43-complete.png`.
State and process ownership: parent `STATE.json`. Original media and all evidence
are retained. Isolated model server is stopped after completion; film preview
remains available. Other station processes and normal Ollama are left alone.
