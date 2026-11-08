# Historical contextual help and instruction inventory — Beta 11 audit snapshot

> **Current help:** Beta 20 operator Help serves the bundled current manual, generated from `docs/USER-MANUAL.md` (with `docs/manual/src/` as chapter sources). Use the [Beta 20 User Manual](../USER-MANUAL.md) and its [package verification record](../releases/v1.0.0-beta.20-verification.md) for current operator guidance and release scope. The findings below record an October 8, 2026 Beta 11 source audit; they are not a current defect list. Read the current manual before applying any older audit note.

## Beta 12 follow-up — October 10, 2026

The bundled manual now explains that Whistle starts independently of the Whisper
backup, which loads only when fallback is needed. It also explains that a
scheduled recording prepared early stays in `arming` until a later scheduler
pass starts it when due. Disabling the schedule before capture starts skips
prearmed jobs and stops their capture processes; an already recording job
continues until its planned end or the operator presses Stop. The source audit below remains the dated Beta 11
snapshot; these additions describe the Beta 12 changes.

Live pre-flight uses the local rehearsal video only for the unchanged bundled
sample source. Editing its channel, type, address or credentials switches the
check to the configured live source; the sample file cannot stand in for it.

Audited 2026-10-08 against the Beta.11 manual source and application code. This is a dated source audit, not a release-status statement; check the [project releases page](https://github.com/scottconverse/civiccast/releases) for current publication and download status. Its Beta 11 observations are retained as historical audit evidence. The linked per-screen sheets indexed by [README.md](README.md) are historical Beta 10 snapshots.

## Source coverage

The recursive production-source inventory includes shared components, routes, shells, nested screens, and helpers; test files are excluded from these counts.

- **Operator console:** 113 production `.ts` / `.tsx` files under `civiccast/apps/portal-operator/src`, including all 40 `ROUTE_PATHS` entries in `routes.ts`: help, setup, live, facility, control room, control room setup, remote contribution, channels, CG board and designer, today, schedule, auto-schedule, program guide, assets, archive, contributors, caption review, summary review, agendas, missing media, media lifecycle, publish, playback policy, analytics, app administration, subscribers, ActivityPub, readiness, alerts, emergency alerts, AI models, station profile, cable commissioning, custom fields, reports, EPG export, underwriting, paywall, and recording. Recursive coverage also includes asset detail/trim, setup and recovery flows, auth/session handling, sidebar/top bar, shared confirmation/status/error components, provider readiness, and offline caption jobs.
- **Resident portal:** 16 production `.ts` / `.tsx` files under `civiccast/apps/portal-public/src`, covering its shell and home, recordings, schedule, watch, player/captions, agenda, subscription gate, contribution, and feedback surfaces.
- **Installer:** 9 production `.ts` / `.tsx` files under `civiccast/apps/installer/src`, covering machine checks, download choices and progress, acquisition errors and recovery actions, setup/activation, handoff, and support guidance. Provider-card help targets also come from `civiccast/installer/service.py`.

Across these areas, the audit covered visible labels and tooltips, placeholders and field descriptions, empty/error/confirmation/retry guidance, help links, and installer instructions. Runtime claims were checked against `civiccast/ai_models/runtime.py`, `civiccast/captions/whistle.py`, `civiccast/native/station_runtime.py`, and caption retention code.

## Beta 11 audit findings (historical)

The bullets below summarize what the October 8 audit found then. They do not establish that those conditions remain in Beta 12. For example, Beta 12 corrected canonical-sample pre-flight handling, blank signing-secret saves, and alert-rule permissions/routing; consult the current manual and Beta 12 release verification before applying an old finding.

- Help links now target headings in the beta.11 manual: `app-glossary`, `configuration-storage`, `the-publishing-steps-surfaces`, `cdn-and-provider-options`, `publishing-providers`, `federation-activitypub`, `report-a-beta-issue`, `ch-before-meeting`, and `live-captions-what-the-settings-change`. `manual-link.ts` maps legacy provider and help IDs returned by the installer API to those current headings.
- Internal fragment links in the manual body now receive a router-aware Help URL. This keeps a normal click in the manual and leaves the destination usable for copy-link and open-in-new-tab actions under HashRouter.
- Live-caption guidance now matches the native path: a new station starts with live captions off; Whistle is the CPU primary; Whisper is the fallback; optional CUDA acceleration applies to supported NVIDIA Whisper use while Whistle remains CPU-only. The Station Profile text says captions can have gaps under load, playout has priority, and recording captions are independent. Turning captions off stops speech recognition and drains queued audio on the next worker scan, then removes caption audio routing at the next channel start. Turning them on resumes speech recognition on the next scan and adds routing at the next channel start. Installer tier explanations no longer present Large Whisper as the live primary or promise CPU real-time performance.
- Standard native live-caption audio is not retained in the review queue (`retain_review_evidence=False`). The review empty state directs operators to recording-transcription review and explains that live captions are broadcast but not saved there. Recording caption cues are reviewed before captions are published to residents. The recording worker automatically applies the retention policy to its saved review evidence, with a 90-day expiry after resolution; the console has no retention switch. The policy's 24-hour raw-tap expiry belongs to optional retained-evidence mode, whose sweep is disabled in standard native live captioning. Standard live audio instead uses bounded waiting chunks and deletes processed audio.
- Publishing a recording queues offline transcription. Retrying a failed caption job requires confirmation and starts a fresh attempt; the existing UI says partial progress is discarded. This does not require publishing the recording again. Installer acquisition errors also distinguish resumable network failures from file restarts and give failure-specific recovery steps; unresolved stops expose the installer log.
- Caption and summary error messages no longer direct operators to inaccessible deployment logs. Mutation failures explain that the message can be dismissed before retrying the action; request failures point to a station administrator for server/database checks.
- Staff instructions now match current controls: authentication points to Admin sign-in on First Setup; Premiere requires separate resident publication and Embargo is unavailable; guide airings require Published status; auto-schedule compilation publishes its picks and enabled rules compile hourly in the standard configuration; retention rules require **Apply rules now**; and unsaved caption edits must be saved before approval.
- Live and operations guidance now states that Live source selection chooses what the checklist tests and what a new stream will put on air; selection alone does not switch the current broadcast. The CG Board layout preview is read-only while bulletin controls remain editable, and remote-contribution Mute/Off air/Drop/Close update CivicCast records without cutting VDO.Ninja media. EPG field-map guidance is limited to CSV/X-List and uses valid column examples. Installer setup directs first-admin creation to the operator console.
- Audience-telemetry, emergency-feed, federation-moderation, active Live-session, and partial-publish error guidance now points to actionable staff or administrator steps instead of nonexistent screens, inaccessible logs, unsafe refreshes, or an assumption that earlier publish steps were rolled back.
- The manual contents field filters section titles only; its label and placeholder now say so. Its error state gives a real recovery step: reload the page.

## Verification boundary

Manual heading IDs above were checked in `docs/USER-MANUAL.md` and against the Beta.11 manual route's expected TOC IDs. The rendered manual and package/build checks are handled by the repository owner. No USER-MANUAL source, generated docsite JSON, live station, or installer runtime behavior was changed in this slice. No install, activation, or station-capacity claim is made here.
