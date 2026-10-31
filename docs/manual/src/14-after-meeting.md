# After the meeting: assets, captions, summaries, approvals {#ch-after-meeting}

This chapter covers everything that happens to a meeting video once the meeting is over: finding it in the library, trimming it, making it ready for residents to watch, correcting its captions, reviewing the AI summary, and keeping the library healthy. It is normally done by a records clerk, a video editor, or a station volunteer. Publishing the video to residents is the next chapter: [Publishing, and what residents see](#ch-publishing).

## Before you start

You need to be signed in to the staff console (see [Signing in](#ch-signing-in)). Almost every screen in this chapter is in the left-hand menu under **Review Records**: **Assets**, **Missing Media**, **Media Lifecycle Settings**, **Review queue** and **Summary review**.

Words used in this chapter:

- An *asset* is one video in the library, with its title, details and files.
- *Packaging* means making a ready-to-stream copy of the video so residents can watch it in a web browser. It does not show the video to residents. Publishing does that.
- A *cue* is one caption line: a short piece of text with a start time and an end time.
- *Retention* is how long the station must keep a record before it may be deleted.

CivicCast gives each person one or more roles. The role decides which buttons work. In beta.11 most buttons look clickable to everyone, and a wrong-role click gives an error message such as "This action requires one of these CivicCast roles: ..." instead of being greyed out. The table lists who can do what in this chapter.

| Task | Role that can do it |
| --- | --- |
| See the Assets list, open a recording, read the Review queue | Any signed-in staff member |
| Upload a video | records_clerk, meeting_operator, support_admin |
| Edit title, description, meeting body, retention | records_clerk, meeting_operator, support_admin |
| Edit trim and chapters; preview source media | records_clerk, meeting_operator, support_admin |
| Package for playback; replace the source file | publish_operator, setup_admin |
| Place or clear a legal hold | records_clerk, support_admin |
| Approve, edit or reject a caption cue; retry a failed caption job | records_clerk |
| Generate an AI summary | records_clerk, support_admin |
| Approve a summary | records_clerk |
| Missing Media list | meeting_operator, publish_operator, support_admin |

> **Note:** A sign-in whose token has the administrator or operator scope holds all five roles, so nothing is refused. Role differences show up when a person has a limited, role-only sign-in.

> **For IT staff:** Role names and how to create role-limited sign-ins are in Part II (security chapter) and the roles appendix.

## Find a video in the Assets list

1. In the left menu, click **Assets**.
2. To look for a video, type part of its title or its asset ID in the **Search assets** box. To narrow the list, click one of the tabs **All**, **Validated**, **Recorded**, **Analyzing** or **Rejected**.
3. Click a video's title to open its detail page.

You should see a table with the columns Title, State, Status, Duration, Size, Codec and Published. The asset ID appears in small type under each title.

### Apply common metadata to selected recordings

Records clerks, meeting operators and support administrators can check individual rows or use **Select visible assets**. Search and state filters do not clear a selection; the count includes hidden selections. **Clear selection** starts over.

Check **Apply common description** and/or **Apply common meeting body**, enter the replacement value, then choose **Apply metadata to selected assets**. A checked blank field clears that value; an unchecked field stays unchanged. Title, trim, captions and retention are not changed by this form. Each selected record uses its observed version, so a concurrent edit can refuse that record without overwriting another operator's work.

The result lists saved and failed recordings separately. Successful records leave the selection; failed records stay selected for review. Other successful saves are not rolled back. Refresh and review conflicts before applying again; there is no automatic retry. Sign-ins without the metadata-edit role do not see these controls, and the existing server permission gate still applies.

The **State** and **Status** columns describe the same video in two ways.

| State | What it means |
| --- | --- |
| Validated | The file passed CivicCast's checks. It is ready to trim, package or schedule. Every upload, accepted contributor video and watch-folder file starts here. |
| Recorded | A live meeting recording that CivicCast has finished and saved. |
| Analyzing | CivicCast is still checking the file. |
| Rejected | The file failed the check. |

| Status | What it means |
| --- | --- |
| Not packaged yet | The file is validated, but residents cannot stream it yet. Use **Package for playback**. |
| Packaged | A streamable copy exists. Residents still cannot see it until it is published. |
| Published | Live on the resident portal. |
| Not servable yet | A live recording that has no streamable copy yet, so residents cannot play it. |
| Missing file | CivicCast cannot find the video file on disk. |
| Validating, Ingesting, Transcoding, Queued for transcode | CivicCast is working on the file. |

> **Known issue (beta.11):** The Assets list shows only the first 50 assets, ordered with published videos first (most recently published first), then everything else by asset ID. A new upload is not published yet, so it sorts after the published videos. The search box and the tabs search only those 50. A station with more than 50 videos can upload a file and not see it in the list. The screen does not say this and has no "next page" button. Workaround: ask IT staff to list assets through the API with a larger page size.

> **For IT staff:** The list calls `GET /api/staff/assets`, which takes `limit` (default 50, maximum 500) and `offset`, and returns the real total in the `X-Total-Count` header. See the API appendix.

## Upload a video

Use this for a video that was not recorded by CivicCast, such as a file from a camera card or another recording system.

1. On the Assets screen, click **Upload video**.
2. In the **Video file** box, choose the video file. (While the panel is open, the **Upload video** button reads **Cancel upload**.) The accepted types are MP4, MOV, MKV, WebM, AVI or MPEG-TS (file endings .mp4, .mov, .mkv, .webm, .avi, .ts, .m2ts).
3. Check the **Title**. CivicCast fills it in from the file name, turning underscores and dashes into spaces. Change it if it is not what residents should see.
4. Click **Upload**. A progress bar shows "Uploading {file}…" and a percentage. Click **Cancel** to stop.

When it finishes you should see "Uploaded: {title}". The text below says the video now appears in the table, and the new row shows State **Validated** and Status **Not packaged yet**. No further action is needed for the check itself; CivicCast runs it before it says "Uploaded".

> **Note:** If CivicCast cannot read the file as a supported video, it refuses it and keeps nothing. The error appears in the upload panel and no row is created. The panel does not show a size limit. The server's default limit is 10 GB, set by the station's IT person.

> **Tip:** After uploading, set the title, description and **Meeting body** on the video's detail page right away (see the next section). Residents search and filter by these.

## Edit a video's public details

The title, description and meeting body are shown to residents on the portal. Edit them on the detail page.

1. On the Assets screen, click the video's title.
2. In the **Public metadata** card, change **Title** (up to 200 characters), **Description** (optional, up to 2000) and **Meeting body** (optional, up to 120). The meeting body is the group that held the meeting, for example "City Council". Residents filter the Recordings page by it. Leave it blank and the video is not tagged, so it never matches a meeting-body filter.
3. Click **Save metadata**. The button says "Saving…" and then "Saved".

> **Warning:** If the video is attached to a schedule item that is already published (committed to air), the save is refused. The message reads "Asset X cannot be edited: N linked schedule item(s) already published. Unpublish or cancel the named items before retrying the edit."

The same card holds the retention controls. The four cards are **Default**, **Permanent**, **Meeting (long)** and **Short**, plus an optional **Retention deadline**. The screen reminds you: "Records officer review required. State presets provide a starting point, but local schedules, litigation holds, and official-minutes rules can require longer retention." It also says deletion is never automatic: expired items are flagged for the records clerk to review. A button, **Convert to a length + unit or forever term...**, switches the card to a length-and-unit form (days, weeks, months, years or forever).

> **Known issue (beta.11):** The console has no screen that lists the videos flagged for records review. The list exists only in the API (`GET /api/staff/records/disposition-queue`), and it is a list only: the code says an action screen is a later follow-up. Ask IT staff to read it for the records clerk.

> **Note:** Below the retention controls there is a custom-fields editor for any extra fields your station has defined. It is not described here because the fields differ from station to station.

## Trim a video and mark chapters

Trimming sets where the video starts and ends. It does not change the original file.

1. Open the video's detail page and click **Edit trim & chapters**. On the Assets list you can click **Edit trim** instead, for a Validated row.
2. Use the timeline to move the playhead. The buttons step back or forward by one second (**-1s**, **+1s**) or one frame (**-1f**, **+1f**), or jump to the start and end.
3. At the moment the video should start, click **Set IN**. At the moment it should end, click **Set OUT**. You can also drag the **IN** and **OUT** handles on the timeline.
4. To mark a chapter, move to that moment and click **+ Mark**, then type a name for it. Remove a chapter with its remove button.
5. Click **Save trim & chapters**. A toast says "Saved." and the editor closes. To leave without saving, click **Discard**.

The preview has video playback controls. Use **Play selected trim** to inspect the chosen IN/OUT range. For packaged recordings, CivicCast plays the HLS copy and translates its playhead into the original source timeline, so trim points and chapter times retain their source positions. If the preview fails, use its retry action; the original recording is preserved.

> **Known issue (beta.11):** Chapters you mark are saved with the video, but we found nothing in the packaging code that writes them into the packaged video. The resident portal shows the meeting's agenda, not these chapters (see [Publishing, and what residents see](#ch-publishing)). To expose these markers to residents, use **Sync from chapters** on an agenda, review its times, and publish that agenda.

> **Warning:** For an uploaded video, the trim you save is used when the video is packaged. Set the trim before you click **Package for playback**. After changing an already packaged video, use **Repackage for playback** on the Assets screen to apply the new trim to the streamable copy. Existing English and selected translated caption tracks are rebuilt from their reviewed source cues before the new package replaces the old one. If a matching review is incomplete or no approved cues fall in the new range, repackaging refuses and keeps the previous playback package. For a live recording, the detail page says "Saving a trim re-renders the published recording automatically."

> **Note:** The trim also applies when the video airs from the schedule on a channel.

> **Note:** Caption review keeps timestamps from the original recording. Published English and translated captions are clipped to the range used to package the video and shifted to that playback timeline. For example, a source cue at 30 seconds appears at 28 seconds when the packaged trim starts at 2 seconds. Changing a saved trim takes effect in playback after repackaging; review records keep their original source timestamps.

If saving fails with a message about another writer, someone else changed the video while you were editing. Close the editor, open the video again and redo your change. If the video is already on a schedule item that is committed to air, the save is refused with the "cannot be edited ... already published" message described under [Edit a video's public details](#ch-after-meeting).

## Package a video for playback

1. On the Assets screen, find a Validated or Recorded row with a stored source file and no streamable copy. An unpackaged Validated row says **Not packaged yet**; an unpackaged Recorded row says **Not servable yet**.
2. Click **Package for playback**. The button says "Packaging..." while it works.

You should see the Status change to **Packaged**. This needs the publish_operator or setup_admin role; otherwise the row says "A publish operator or setup administrator must package this recording."

CivicCast packages one video at a time. If another is already running you see "Another recording is already being packaged…". Wait and try again. If packaging fails, the message is the server's text or "Packaging failed. The original file was kept; try again."

The list offers **Package for playback** for Validated and Recorded assets with a stored source file, or **Repackage for playback** when a streamable copy already exists. This also lets you package a recording whose automatic packaging did not finish.

Packaged is not the same as published. Next, go to the Publish screen ([Publishing, and what residents see](#ch-publishing)).

## Take a video off the public portal

1. Open the video's detail page.
2. In the **Technical** card, next to **Published**, click **Remove from portal**.
3. Read the dialog and confirm. It says: "Residents will no longer be able to view or find it there. This only affects portal visibility -- the asset row, its media, and the Internet Archive / syndication copies are untouched."

The video disappears from the resident portal. Publish shows it as a draft awaiting portal approval again; previous delivery history is retained. This needs publish_operator or setup_admin.

## Check a video's readiness, hold it, or replace its file

At the bottom of the **Technical** card is the **Media lifecycle** panel.

- A **readiness badge** says Ready, Transcoding (n%), Queued for transcode, Missing, Rejected or Not ready.
- **Loudness gate** shows "OK (-x.x LUFS)", "Failed — normalize before air" or "Not checked yet". LUFS is a measure of how loud the audio is. CivicCast checks against -16 LUFS with a margin of 1. The screen has no button for fixing a failed loudness result, so the line is for information.
- **Archival** lists three copies: the portal, the Internet Archive and a local NAS (a network storage box at the station). Each shows "verified" or "not verified", and the panel ends with "Archive-complete" or "Not archive-complete yet".

To place a **legal hold**, optionally type a reason, click **Place legal hold (blocks expiry)** and confirm "Place a legal hold on this asset?". A held video cannot expire under its retention rule. To release it, click **Clear legal hold** and confirm. Both actions are recorded in an audit log. This needs records_clerk or support_admin.

To replace the video file, choose a **Replacement video file** and confirm "Replace this asset's source file?". CivicCast renames the old file aside and never deletes it, and the video goes back to State **Validated**. This needs publish_operator or setup_admin.

> **Warning:** The replace-source dialog says viewers see the new file "immediately once processing finishes". In beta.11 that is not what the code does. The replacement resets the video to Validated and clears its transcode jobs, but it leaves the old streamable copy and the publish date in place. Residents keep seeing the old video. Use **Repackage for playback** in the Assets list to rebuild the streamable copy from the replacement source. Until that succeeds, residents keep seeing the earlier package.

> **For IT staff:** The repackage action uses `POST /api/staff/assets/{id}/package` for that asset. See the API appendix.

## Audio tracks, loudness plan and caption status

Three read-only cards are on the **Channels** screen, not on Assets. They describe a channel on the air, not a recording.

- **Audio tracks (SAP / descriptive)** lists any secondary audio tracks, such as a second language (SAP) or described video, each marked "on air" or "disabled". If there are none, it says "Single audio program — no secondary (SAP / descriptive) tracks configured." There is nothing to edit in the console.
- **Loudness** shows, for each output of the channel, the target loudness and standard, and a "Measured -x.x LUFS" chip. The card says each output is set to its destination's standard: cable to ATSC A/85 (-24 LKFS) and streaming to -16 LUFS. It is visible only to setup_admin and support_admin; a meeting operator sees a refusal message in the card.
- **Captions** shows whether captions are reaching the output: "Captions on", "Captions off", "Caption proof failed" or "Not verified". It reads "on" only after a recent passing check. The card says: "This proves carriage at the egress boundary; it is not a claim of FCC Part 79 compliance." It refreshes every 30 seconds.

See [Running the meeting](#ch-running-meeting) for the Channels screen.

## Offline captions: how a recording gets its captions

Supported recorded translations are **Spanish**, **Chinese (Simplified writing, zh-Hans)**, **Tagalog / Filipino**, **Vietnamese**, and **Arabic**. Existing Spanish settings and reviewed tracks remain. Chinese support does not claim every spoken variety or Traditional writing. Choose a reviewer who knows the selected language: model output is a draft, and every translated cue needs human review/editing before publication. English wording or timing changes require matching translation review again. Unsupported targets are refused rather than translated into Spanish.

Configure recorded captions under **Setup > Station Profile > Recorded captions**. Choose whether processing is enabled, which translations to include alongside English, and whether completion waits for all selected languages or only reviewed English. These are station choices. Publishing can queue captions automatically. For existing packaged recordings, open **Review queue > Recording caption backlog**, select up to ten recordings and click **Caption selected**. Each recording reports its own queue or packaging error; the existing serial worker processes the batch within the station’s current resource limits. Selecting a recording with an active job reuses that job. Inference completion means cues await human review, not that their words are accurate or approved.

1. With recorded captions enabled, you approve publishing the recording. CivicCast queues a caption job for it first, even if the Portal row is not selected. If it cannot queue the job, publication reports the failure. A recording with no stored video file gets no caption job. When recorded captions are disabled, new caption jobs are not created and existing jobs pause until processing is enabled again.
2. The job transcribes the audio into English cue lines. On the video's detail page, the **Offline caption jobs** card shows **Queued or transcribing…**. A full meeting can take several minutes, not seconds.
3. When transcription ends, the card shows **Awaiting review**. The cues are now in the **Review queue**.
4. You decide every English cue. For each selected language, CivicCast translates the reviewed English and queues those cues for their own review.
5. With **all selected languages** required, the selected tracks attach after their review is finished. With **reviewed English** required, the English track can attach first; selected translations attach later after their own review. English-only jobs do not need a translator or translation review.

> **Note:** Changing the completion setting never publishes unreviewed caption text. Disabling processing does not mark an uncaptioned recording as captioned.

If the job runs out of attempts, the card shows **Failed** with the reason in the **Last error** column. A records clerk can click **Retry** and confirm "Retry this offline caption job?". This resets the attempt budget. A transcription retry scans audio again but preserves retained cues and review decisions. Without the records clerk role the card says "Retrying a failed job requires the records clerk role."

In the recording backlog, **Cancel** stops remaining work at the next audio chunk boundary. An in-flight model call may finish first, and a publication write already started may finish. Existing review decisions and published captions remain. **Resume** keeps those decisions; unfinished transcription scans again without replacing existing cues, while a completed transcription returns to review/publication. **Retry** is available after a failure. Read the recording’s error before trying again.

Under **Station Profile > Recorded captions**, enter local names and terms one per line (up to 100 terms, 120 characters each). The saved hints reach the recorded-audio path’s faster-whisper and Whistle Whisper adapters as their initial prompt on the next scan. They do not apply to every possible caption engine, guarantee spelling, alter existing cues, or approve captions.

The job can also be held. These are the reasons CivicCast puts on the row:

- Every English cue was rejected. Open the Review queue, choose English, and edit or approve cues. If the audio is truly unusable, use **Cancel** in the recording caption backlog.
- Every selected Spanish cue was rejected. Open the Review queue, choose Spanish, and edit or approve cues. The Spanish track attaches once review is complete and at least one cue is approved or edited. Under **Attach English after its review**, reviewed English can already be available.
- Spanish is selected but no translation model is available. The row says to install or repair it under "Settings > AI Models > Translation" and run 'civiccast doctor'. With **Wait for every selected language**, CivicCast retries with growing waits; after its attempts run out, the card shows **Failed** and a records clerk must click **Retry** after repair. With **Attach English after its review**, reviewed English attaches while the active job waits for Spanish. English-only jobs do not encounter this hold.

Published English and translated captions use the packaged playback timeline: cues outside its trim are excluded, crossing cues are clipped, and retained cues are shifted by the packaged IN point. Review records keep their original source timestamps. Repackaging rebuilds existing tracks from reviewed source cues before replacing the old package; incomplete matching review or an unusable caption range leaves the previous package intact.

## Review and correct captions

The **Review queue** (page heading "Caption review") is where a person checks the machine's caption lines before the public sees them.

This queue is for recorded-caption jobs. Ordinary live captions go to the broadcast but are not saved here or turned into per-cue audio evidence. A live session therefore does not populate this queue; its empty state points operators to captioned recordings instead.

1. In the left menu, click **Review queue**. It opens on the **Pending** tab. The other tabs are **All**, **Edited**, **Approved** and **Rejected**, and a **Search** box above them filters by asset ID or caption text.
2. To work on one language, click **English** or **Spanish** in the **Language** row. **All languages** shows both.
3. Each card is one cue. It shows the recording's asset ID, the cue's time range, a language badge (EN or ES) and a status. The left box **Machine cue** is the computer's text and cannot be changed. The right box **Reviewed text** is yours to edit.
4. If the cue is wrong, change the **Reviewed text**, then click **Save edit**. The status becomes **Edited**, and the card leaves **Pending**. If you want that correction included in an AI summary, switch to **Edited** and click **Approve** on the saved cue. Complete the audio-evidence check below if the cue has low confidence.
5. If the cue is right, click **Approve**. The status becomes **Approved**.
6. If the cue should not be shown at all, click **Reject**.

You should see the card leave the Pending tab and the status pill change. The list refreshes after every decision.

What each button really does:

| Button | Result |
| --- | --- |
| **Approve** | Marks the cue Approved. It approves the text that is already stored: the machine text, or the text from your last **Save edit**. |
| **Save edit** | Stores what is in the **Reviewed text** box and marks the cue Edited. It is available only when the text has changed and is not empty. |
| **Reject** | Marks the cue Rejected and throws away any reviewed text. A rejected cue is left out of the captions. |

Approved and Edited cues both become captions, using the reviewed text. Rejected cues are left out. Pending cues hold their language's track back; selected Spanish review does not hold reviewed English back when **Attach English after its review** is selected.

> **Note:** The Review queue warns, "Save edit before approving if you want to keep these text changes." **Approve** uses the stored text; it does not save an unsaved draft. **Save edit** counts as a decision for the caption track. For AI summaries, open **Edited** and approve the saved correction as well; the console's summary workflow uses Approved cues only.

> **Warning:** **Reject** asks no question and discards the reviewed text. A rejected cue can be approved again later, which then approves the machine's original text, not your earlier correction.

### Correct cue timing and republish

Edit **Start (seconds)** and **End (seconds)** alongside **Reviewed text**, then click **Save edit**. Fractional seconds are accepted; the start must be non-negative and the end must be later. When retained review audio exists, it must cover the entire changed interval. An out-of-range edit explains the missing coverage and leaves the stored cue unchanged. Timings are source-recording seconds; the existing package timeline maps them into trimmed playback.

For a completed recording, select it again in **Recording caption backlog** and click **Caption selected**. CivicCast republishes the current reviewed cues without repeating inference. Pending cues still hold publication; no batch action approves them. If Spanish is selected, changing English wording or timing queues a fresh Spanish cue for human review before the affected translated track can publish. Unchanged older Spanish review decisions remain valid. Check the resident caption track after the job reports reviewed captions attached.

### Low-confidence cues

A cue the machine was unsure of carries a **Low confidence** badge, and a yellow bar at the top says how many need attention. For these cues, **Approve** stays disabled until you do two things:

1. Click **Load review audio**. CivicCast fetches a clip covering the complete cue, with nearby context where available, and shows an audio player. Play it. The clip is bounded to 120 seconds. If the source cannot cover the whole cue, the message explains the missing coverage and how to regenerate or restore the needed source; a partial clip is not presented as complete evidence.
2. Tick **I compared this low-confidence cue with its audio evidence.** This box becomes available only after the audio can play.

This audio check applies to **Approve** only. **Save edit** has no such check, so a low-confidence cue can be edited and counted as decided without anyone playing the audio.

If the audio is not available, the card says "Audio evidence is unavailable. Approval of this low-confidence cue is blocked." You cannot approve that cue until the audio is available, so ask IT staff. If the recording's media file is missing, loading the audio fails with "The local meeting media is missing. Relink or restore the asset, then retry."

### Who can use it

Everyone can read the queue and play audio. Only a records clerk can approve, edit or reject. For anyone else a yellow note says "Caption review actions require the records clerk role. The queue stays visible for read-only review." and the boxes and buttons are disabled.

### Agenda-based draft minutes

A records clerk can open the meeting agenda and expand **Draft meeting minutes** once its recording and reviewed English transcript are available. Choose a completed cited summary from that same recording, or choose the manual option when generation is unavailable. Creating minutes does not run a separate model for every agenda item. Untimed or uncited discussion needs clerk entry; an empty section is not a claim that nothing happened.

Open the draft in Summary review. Each agenda section has editable discussion and transcript citations. Citation playback follows the packaged recording timeline, while stored evidence keeps its source-recording times. Add motions with the mover, optional seconder, outcome and individual member votes. Check the recording and citations, then explicitly confirm each motion and its votes. Editing a motion or vote clears that confirmation. Mark an item not discussed only after checking it, and confirm that all items were checked for motions and votes.

Use **Save draft minutes** before approval. Approval stays unavailable while edits are unsaved or saving. Every save keeps the previous content, editor and time; approval is tied to the saved fingerprint. If another clerk edits the draft, reload and review the current version. Transcript corrections require rebuilding the draft against the current reviewed cues. These are editable drafts, not an automatic or certified parliamentary record.

After human approval, the signed record export includes the agenda discussion, citations, confirmed motions and votes in the rendered minutes, plus structured minutes and edit history in its PDF attachment. Approved minutes are immutable. If a model fails or no suitable cited summary exists, use the manual option with the same reviewed transcript and human approval controls.

## If it did not work

| What you see | Cause and fix |
| --- | --- |
| "No caption cues need review." | There are no cues at all for the chosen language, in any tab. Cues appear after a recording is approved for publishing on the Publish screen and the caption job has finished transcribing. |
| "No captions match the current search and filter." | Cues exist, but not under the tab or search you chose. After you decide the last cue on the **Pending** tab you will see this line. Click **All**, **Edited**, **Approved** or **Rejected** to see the decided cues. |
| "Spanish cues appear here after English captions are approved and translated." | Shown beside the **Language** row when the **Spanish** choice has no cues. The Spanish cues are made only after the English pass is fully decided. The Spanish text is a machine translation and needs review too. |
| "Caption review backend unavailable." | The station's database is not connected. Tell your IT person. |
| "Could not complete caption review action." after you clicked a button | The action failed and was not saved. Dismiss the message and try once more; if it keeps failing, ask your station administrator to check the CivicCast service and database. |
| "This is a low-confidence caption cue. Compare it with the retained audio, then explicitly acknowledge that review before approval." | Load and play the audio, tick the box, then Approve. |

## Review an AI summary

CivicCast can write a draft summary of a meeting from its approved caption lines. A records clerk checks and approves the draft, then exports a signed-record PDF for station records.

### Make a summary

1. Approve at least some caption cues for the recording in the Review queue. This console workflow uses only **Approved** cues. If you corrected cues with **Save edit**, open **Edited** and click **Approve** on those saved corrections, completing the low-confidence audio-evidence check when required. Edited cues that have not been Approved are excluded from the summary.
2. Open the recording's detail page and find the **AI summary** card, under the **Offline caption jobs** card.
3. Click **Generate summary**. The chip reads "queued" and then "generating…". The screen says generating locally can take 1-6 minutes on a CPU-only station, and you can close the tab; the job keeps running.
4. When it finishes the card says "Summary generated. Review it in Summary review".

If there are no approved cues the card says "No committed transcript cues yet. Approve caption review items for this recording first, then a summary can be generated from them." You need the records_clerk or support_admin role. If the job fails, the card shows **Failed** with the reason and a **Retry** button, which only a records clerk can use. The local AI program (Ollama) must be running with the summary model installed. If it is not, the job fails and the card says "Local Ollama AI runtime is not reachable. Start Ollama and retry, or configure a different summary model in AI model settings."

### Look at a summary

Local generation checks memory before loading the summary model and reserves 8 GiB for station operation. A resource-deferral message explains how to recover; wait for memory to become available or choose a smaller model before retrying. CivicCast unloads the local generation model after the request completes.

> **Note:** In one Beta 15 field run, 12.7 GiB was available against an 8 GiB model request plus an 8 GiB broadcast reserve, so the safeguard deferred generation. The guard worked, but summary editing, approval, export and publication were not completed in that run. Usability and full-workflow follow-up was recorded for Beta 17; this observation does not establish a defect in the current package. See the [deferred field report](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/beta17-deferred.md).

1. In the left menu, click **Summary review** (page heading "Summary review", label "Summary + signed records").
2. Each card shows the asset ID, a status, the summary paragraph, and a list of **Sourced claims**. Under each claim are buttons labeled with a cue ID and its time range.
3. Click a cue button. The **Inline transcript player** highlights the range and loads the private source recording with playback controls. The player seeks to the cited source time; press Play to listen. Packaged recordings map the trimmed playback timeline back to source time. If the cited passage lies outside the available copy, the player explains that rather than seeking to a different passage. Use **Renew playback** if access expires. To read caption text, open the recording or Review queue.

The page shows **Pending review**, **Approved** and **Needs evidence** items. Approved summaries remain on the page so you can export or revisit their signed records. A **Needs evidence** card has a message explaining that the model's output could not be tied to caption cues with timestamps. A yellow bar counts items that need more evidence.

If nothing is waiting, the page says "No summaries need review." and tells you to use **Generate summary** on a recording's detail page.

### Edit, approve and export

1. For a **Pending review** item, click **Edit summary** to revise its narrative. Click **Save changes** to keep the edit or **Cancel edit** to discard it. Sourced claims and their transcript references remain unchanged; compare the narrative with those claims before approval.
2. Click **Approve summary** only after checking the summary against its source cues. Approval is recorded with the identity from your signed-in staff account. After approval the card stays available with its status set to **Approved**.
3. On an approved card, click **Export signed record**. CivicCast creates a PDF/A-3B record and shows its record ID and digest. The timestamp is a deterministic test timestamp unless the station has configured a real timestamp authority.
4. Click **Download signed record** to save the PDF. Click **Verify signed record** to ask CivicCast to check its stored artifact and timestamp proof.
5. To return to an earlier export after leaving or refreshing the page, open the approved card and click **Load saved records**. CivicCast lists up to 10 recent exports for that summary; use each row's **Download** or **Verify** action.

Only a records clerk can edit, approve, export, download or verify. If an action fails, the page shows an error and a recovery step. Stale edits and approvals are rejected; reload and review the updated summary before trying again.

The summary review page does not provide a reject or regenerate action. To generate another draft, update the caption review as needed and use **Generate summary** on the recording's detail page.

> **For IT staff:** The approve route is `POST /api/staff/summaries/{id}/approve`; the request requires the displayed draft's `expected_audit_fingerprint`, and the server takes operator identity from the signed-in staff session. Approval atomically checks that the draft is still pending and unchanged; otherwise it returns 409 with reload guidance. Edits use `PATCH /api/staff/summaries/{id}` with the draft's expected audit fingerprint. Signed-record metadata is available from the records-clerk-only `GET /api/staff/records?summary_id={id}&limit=10` route; export, download and verify routes remain under `/api/staff/records`. See the API appendix.

## Check for videos that are missing before a meeting

The **Missing Media** page is a warning list. It shows meetings on the schedule for the next 7 days whose video is not ready to play.

1. In the left menu, click **Missing Media**. Only meeting operators, publish operators and support admins see this entry.
2. Read each card. It names the meeting and its time, the channel, the video's asset ID and state, and gives a reason.
3. Click **Open asset** to go to the video's detail page and fix it.

The reasons are: "Referenced asset no longer exists.", "Asset is in state '{state}', not validated/recorded." and "Asset's backing file is missing." Every card shows "Not ready" whatever the reason. When nothing is wrong the page says "Nothing missing."

The page loads once when you open it. Leave it and come back to refresh. To fix a card you usually need to upload a replacement file or ask a publish operator to replace the file.

> **Known issue (beta.11):** "Every asset scheduled in the coming week is validated or recorded and ready for air" is too strong. The page checks only schedule items that are still drafts. Items already committed to air are not checked. A file deleted in the last hour may not be flagged yet, because the file check runs once an hour by default.

If you open the page with a role that is not allowed, you see "This action requires one of these CivicCast roles: meeting_operator, publish_operator, support_admin."

## Watch folders, retention rules and storage (Media Lifecycle Settings)

Watch folders are read by the installed CivicCast service, not by your signed-in Windows account. If **Add watch folder** fails, the explanation stays beside the form and your path remains entered. Give the CivicCast service account read/traverse access to that folder and its videos; avoid granting broad access to a whole drive. Try **Add watch folder** again after correcting the named problem. Uploading through **Assets** is also supported; no API command is needed.

This page holds station-wide settings and cleanup review. In the left menu it is **Media Lifecycle Settings**, shown to setup_admin, publish_operator and records_clerk.

- **Watch folders.** A watch folder is a folder on the station's computer (a local disk, a USB drive or a network share) that CivicCast checks automatically and copies new videos from. Type the folder path in **Watch folder path** or click **Browse…**, then click **Add watch folder**. A new file is imported once it has stopped growing. The original stays where it is, and the new video appears in Assets as Validated. Click **Scan now** to check immediately, or **Remove** to stop watching (files already imported stay).
- **Retention automation.** A rule sets a video's retention policy by its meeting body. Fill in **Rule name**, **Meeting body (exact match)** and **Retention policy**, then click **Add rule**. Then click **Apply rules now**.
- **Storage budget.** A read-only view of how much disk the library uses, with a table by retention policy.
- **Storage cleanup review.** Shows recordings already flagged by the retention worker, 50 per page (use **Previous cleanup page** and **Next cleanup page** to review the rest), with current source-file size, expiry and protection checks. Each recording is fetched directly, including older records outside the Assets list's first page. Legal hold, permanent retention, an extended/no expiry, active processing or unavailable current protection prevent an eligibility label. **Eligible for records-officer review** is a review candidate, not permission to delete. Open its recording link to review retention/holds through existing controls. Nothing is purged from this screen; obtain the station's normal records-officer disposition authorization. These checks refresh every 30 seconds, and **Refresh cleanup review** reloads the flag queue.

Each folder row shows **Not scanned yet**, **OK** with "Last poll" and "Last ingest" times, or **Degraded** with the reason, for example a drive that was unplugged.

> **Note:** Rules are applied only when someone clicks **Apply rules now**; expired assets are not deleted automatically and are flagged for records review. A rule whose **Meeting body** is blank matches nothing. Applying a rule sets the retention policy label on every video whose meeting body matches, replacing a policy someone chose by hand on that video. Applying a rule does not recalculate the retention deadline.

> **Warning:** The **Browse…** and **Scan now** buttons need the setup_admin role, adding or removing a watch folder needs publish_operator or setup_admin, and retention buttons need records_clerk or setup_admin. The page shows every button to every role, and a wrong-role click returns an error message.

> **For IT staff:** Watch folders, upload storage and the storage budget are set up in Part II (configuration and operations chapters). If the budget card says no budget is configured, the setting is an environment variable that only IT can change.

## Agenda-based draft minutes

A records clerk can open the meeting agenda and expand **Draft meeting minutes** once its recording and reviewed English transcript are available. Choose a completed cited summary from that same recording, or choose the manual option when generation is unavailable. Creating minutes does not run a separate model for every agenda item. Untimed or uncited discussion needs clerk entry; an empty section is not a claim that nothing happened.

Open the draft in Summary review. Each agenda section has editable discussion and transcript citations. Citation playback follows the packaged recording timeline, while stored evidence keeps its source-recording times. Add motions with the mover, optional seconder, outcome and individual member votes. Check the recording and citations, then explicitly confirm each motion and its votes. Editing a motion or vote clears that confirmation. Mark an item not discussed only after checking it, and confirm that all items were checked for motions and votes.

Use **Save draft minutes** before approval. Approval stays unavailable while edits are unsaved or saving. Every save keeps the previous content, editor and time; approval is tied to the saved fingerprint. If another clerk edits the draft, reload and review the current version. Transcript corrections require rebuilding the draft against the current reviewed cues. These are editable drafts, not an automatic or certified parliamentary record.

After human approval, the signed record export includes the agenda discussion, citations, confirmed motions and votes in the rendered minutes, plus structured minutes and edit history in its PDF attachment. Approved minutes are immutable. If a model fails or no suitable cited summary exists, use the manual option with the same reviewed transcript and human approval controls.

## If it did not work

| What you see | Cause and fix |
| --- | --- |
| `"{name}" is not a supported video file. Accepted types: MP4, MOV, MKV, WebM, AVI, or MPEG-TS.` | The file ending is not one of the accepted types. Convert the video and upload again. |
| "Durable storage is not ready." with "Open Setup, prepare durable storage, then return here." | The station's storage has not been set up. Ask a setup administrator. |
| "Could not load assets." | Click **Retry**. If it fails again, tell your IT person. |
| "Save failed." on the detail page | Another person changed the video, or a linked published schedule item blocks the edit. Reload and retry, or unpublish the named items. |
| "Another recording is already being packaged…" | CivicCast packages one video at a time. Wait and click again. |
| "This action requires one of these CivicCast roles: ..." | Your sign-in does not have the needed role. Ask your station administrator. |

## Related

- [Publishing, and what residents see](#ch-publishing)
- [Before the meeting: schedules, agendas, recording schedules](#ch-before-meeting)
- [Running the meeting](#ch-running-meeting)
- [When something looks wrong](#ch-something-wrong)

<!-- SOURCES: inventory/screens/assets.md; inventory/screens/review.md; inventory/screens/summary.md; inventory/screens/missingmedia.md; inventory/screens/medialifecycle.md; civiccast/apps/portal-operator/src/screens/{AssetsScreen,AssetDetailScreen,TrimEditorScreen,ReviewQueueScreen,SummaryReviewScreen,GenerateSummaryPanel,OfflineCaptionJobsPanel,MediaLifecyclePanel,MissingMediaScreen,MediaLifecycleSettingsScreen}.tsx; civiccast/summary/router.py:111-186 (SummaryApprovalRequest extra=forbid; approve_summary); civiccast/apps/portal-operator/src/screens/SummaryReviewScreen.tsx:10-14,234 (OPERATOR payload); civiccast/captions/review.py:318-345 and civiccast/captions/persistence.py:291-335 (approve keeps stored reviewed_text; reject clears it); civiccast/captions/vod.py:430-480 (approved/edited become cues, pending counted, rejected dropped); civiccast/captions/vod_job.py:893-1010 and 100-165 (Spanish required, hold reasons); civiccast/schedule/router.py:245-280 (limit 50, X-Total-Count), 372-470 (package uses trim_in/out); civiccast/schedule/media_lifecycle_store.py:395-460 (replace-source leaves manifest_url and published_at); civiccast/egress/source_plan.py:1092 (trim used on air); civiccast/schedule/media_lifecycle_worker.py:141-145 (-16 LUFS, tolerance 1); civiccast/publish/router.py:442-480 (caption job queued before publish) -->

## Generate audio descriptions for a recording {#audio-descriptions}

**Feature branch availability:** this feature is on `feat/local-audio-description`;
it is not in the current RC2 installer. IT must install the audio-description
dependencies, provision the local narrator and make the local vision model available.

Open **Review Records → Assets → the recording → Audio description**. The recording
must already be packaged with its English caption track. A records clerk, meeting
operator, setup administrator or support administrator can choose **Generate audio
description**. CivicCast identifies visual information missing from the dialogue,
narrates it locally, and keeps a separate described edition. There is no new
description-approval queue. Existing caption and recording publication permissions
still apply.

Only one recording is described at a time. You may leave the page while it runs.
Background inference waits at safe boundaries while channels are on air. Use
**Cancel generation** to stop at a safe boundary, or **Resume generation** after an
interruption. Resume reuses verified work; a changed source, trim, model or processing
implementation can require **Generate again** instead. Repackage changed trims first.

When complete, **Show described recording preview** opens a private preview. Its
access expires after fifteen minutes; reopen it from the asset page if necessary.
It reports descriptions that fit safe pauses, those that require pausing, and visual
observations containing uncertainty. The counts do not measure completeness or
certify legal compliance. Originals are not overwritten.

Published recordings expose **Use audio-described playback** in the resident player
and asset embed. That player provides in-gap narration, optional pause-to-describe,
English captions, manual listening and skip/resume. Playback position is retained
when switching editions. The original HLS player remains available with its existing
caption options. Downloaded described MP4s contain only descriptions that fit safe
pauses; pause-to-describe requires the browser player. Automatic descriptions can
miss or misinterpret visuals. Unreadable and unplaced information remains a stated
limitation, not a claim of full accessibility.

IT setup, model/voice notices and API routes are documented in
[Audio-description operations](../../ops/audio-description.md).
