## Operator guidance — RC2 snapshot, October 30, 2026 (historical)

Use **Channel startup and repeat** to save automatic startup and **Repeat the published sequence** independently. Repeat marks the last contiguous published group in **Published playout plan**. A pending status is not Stopped. Preparation and handoff readiness are distinct from observed output.

The shipped built-in Help is generated from `docs/manual/src/`; the audit below is historical.

> Historical beta.10 audit snapshot dated 2026-10-03; retained for traceability, not current UI or user guidance.

# Channels (nav id: channels)

Console group: Run Meeting. Spec written against beta.10. Paths are under `civiccast/apps/portal-operator/src/screens/` unless they start with `civiccast/`. Authority: `docs/manual/src/13-running-meeting.md` (Start, Stop, 24/7, Take live, Commit to air, Lower-third, Headend tasks) and `ops/docs-sprint/inventory/screens/channels.md`. Line numbers re-checked. This spec covers the controls that change what is on the air; the read-only cards (Now / next, Playout plan, Proof log, Loudness, Captions, Audio tracks, Software outputs, Reference CTV feed) are only listed in the coder notes.

## Where the help text lives now
- `ChannelOpsScreen.tsx`: header 1971-1981; error 141-152; feed card 692-828 (buttons 768-772); 24/7 card 884-1074; lower-third card 1126-1273; headend card 1342-1574; station config 246-369; branding 395-487; role gates 1949-1965; start watch 1880-1892.
- `feed-command-confirm.ts:15-47` (Start, Stop, Restart, Drain dialogs); `egress-start.ts:13-27` (why Start is grey).
- `CommitToAirPanel.tsx`: 85-87, 111-131, 200-256, 340-351, 367, 397, 429-436; `commit-format.ts:13-65`.
- `TakeoverCard.tsx`: 46-67, 153-158, 183-303; `CableVerificationCard.tsx:57-68`; `headendConfirm.ts:43-80`.

## Current text
| String | Where | Source line |
|---|---|---|
| "Channels" / "Monitor linear channel identity, current playout, fallback behavior, proof logs, and CTV feed readiness." / "refreshes every 30s" | Header | ChannelOpsScreen.tsx:1971-1981 |
| "Outgoing channel feed" / "Start or stop the local worker that sends {id} to configured outputs." | Feed card | ChannelOpsScreen.tsx:692, 694 |
| State words: On air, Showing slate, Starting, Stopping, Finishing current item, Changing source, Stopped, Needs attention | Pill | status-language.ts:232-238 |
| "Start" / "Stop" / "Restart feed" / "Finish current item, then stop" / "Queuing..." | Buttons | ChannelOpsScreen.tsx:768-771, 790 |
| "Start the outgoing feed for {ch}?" / "{ch} goes live to its configured outputs and becomes visible to residents." / "Start feed" | Dialog | feed-command-confirm.ts:22-24 |
| "Stop the outgoing feed for {ch}?" / "This takes {ch} off the air. Residents watching lose the stream until the feed is started again." / "Stop feed" | Dialog | feed-command-confirm.ts:29-31 |
| "Restart the outgoing feed for {ch}?" / "The stream drops briefly for residents while {ch} restarts." / "Restart feed" | Dialog | feed-command-confirm.ts:36-38 |
| "Finish the current item, then stop {ch}?" / "{ch} plays out its current item and then goes off the air until the feed is started again." / "Finish, then stop" | Dialog | feed-command-confirm.ts:43-45 |
| "No outgoing-feed configuration for {id}. Apply a headend preset or the local rehearsal preset first." | Start grey reason | egress-start.ts:14 |
| "Outgoing feed for {id} is disabled in its egress configuration. Enable it in Outgoing feed configuration, then start." | Start grey reason | egress-start.ts:17 |
| "Checking for an outgoing-feed configuration..." / "Could not check the outgoing-feed configuration for {id}. Retry the check, then start." / "Retry check" | Start grey reason | ChannelOpsScreen.tsx:675, 805; egress-start.ts:27 |
| "Start was queued but the feed did not start. ... Check that the CivicCast egress service is running on this station (System Health), then try Start again." | After 20 s | ChannelOpsScreen.tsx:812-815 |
| "Outgoing feed controls require the meeting operator role." | Role note | ChannelOpsScreen.tsx:825 |
| "Run this channel 24/7" / "Keep this channel on air" / "Starts the feed automatically after restarts and crashes." | 24/7 card | ChannelOpsScreen.tsx:884, 917, 919 |
| "Allow software (CPU) encoding fallback" / "Between programs, show" / "Station slate" / "Community bulletins" / "Slate message" | 24/7 card | ChannelOpsScreen.tsx:932, 945, 952, 957, 974 |
| "NDI output name (optional)" / "SDI output device (optional)" + help | 24/7 card | ChannelOpsScreen.tsx:991, 1003-1006, 1015, 1027-1031 |
| "Save automation settings" / "Unsaved changes" / "Automation settings require the setup admin role." | 24/7 card | ChannelOpsScreen.tsx:1058, 1062, 1073 |
| "This channel has no outgoing-feed configuration yet. Create one from the channel egress runbook (or the setup flow) first; then automation settings appear here." | 24/7 and lower-third cards | ChannelOpsScreen.tsx:896-898, 1159-1161 |
| "Commit programs to air" / "Review the safety check, then approve a program to put it on this channel." | Commit card | CommitToAirPanel.tsx:341, 344 |
| "Review & prepare" / "Approve & put on air" / "Putting on air…" / "Safe to air" / "Not safe to air yet" | Commit buttons, tags | CommitToAirPanel.tsx:397, 85-86, 111-113 |
| "Clashes with N other program(s) already scheduled:" / "Dead-air gap before this program (it can still air):" | Review | CommitToAirPanel.tsx:129-130; commit-format.ts |
| "Preparing" / "Queued to air" / "On air (confirmed)" / "Couldn't reach the engine" / "Rolled back" | Recent commits tags | commit-format.ts:16-24 |
| "aired {time} · approved by {operator}" | Commit row | CommitToAirPanel.tsx:200 |
| "Take off air" / "Why are you taking this off air?" / "Confirm take-off" / "Keep on air" | Undo | CommitToAirPanel.tsx:256, 220, 236, 244 |
| "Nothing has been committed to air on this channel yet." | Recent commits empty (also shown on load error) | CommitToAirPanel.tsx:434 |
| "You can review the schedule here. Putting a program on air or taking it off requires the publish operator or setup admin role." | Role note | CommitToAirPanel.tsx:350-351 |
| "Live takeover" / "Put a live source on this channel now, overriding the schedule, then return when done." / "This channel is on its scheduled program." | Takeover card | TakeoverCard.tsx:154, 157, 223 |
| "Take live" / "Why are you going live? (optional)" / "Confirm take live" / "Going live…" / "No live source is ready yet." | Takeover | TakeoverCard.tsx:272, 229, 248, 276 |
| "Live takeover — {who}, {elapsed}" (red badge) / "Return to schedule" / "e.g. meeting adjourned" / "Confirm return to schedule" / "Stay live" | Takeover | TakeoverCard.tsx:57, 215, 183, 195, 203 |
| "Takeover history" / "No live takeovers have been recorded for this channel." / "Live" / "Returned" | History | TakeoverCard.tsx:300, 67, 85 |
| "Taking a channel live requires the meeting operator or setup admin role." | Role note | TakeoverCard.tsx:286 |
| "Lower-third banner" / "On air"/"Off air" (saved setting) / "Lower-third text" / "Put on air" / "Take off air" | Banner card | ChannelOpsScreen.tsx:1126, 1135, 1177, 1254 |
| "Applies to this channel’s next pipeline build (a fresh start or a scheduled content swap). Does not hot-change an already-live broadcast’s on-screen text." / "Confirm: put on air" / "Confirm: take off air" | Banner confirm | ChannelOpsScreen.tsx:1198, 1219-1220 |
| "The lower-third banner requires the meeting operator or setup admin role." | Banner role note | ChannelOpsScreen.tsx:1271 |
| "Cable headend delivery" / "Headend preset" / "Apply headend preset" / "Enable web preview" / "Verify stream (TSDuck)" / "Headend delivery requires the setup admin role." | Headend card | ChannelOpsScreen.tsx:1342, 1354, 1472, 1527, 1574 |
| "Station app config" / "Save station config" and "Channel branding" / "Save channel branding" | Public-app forms | ChannelOpsScreen.tsx:246, 365, 395, 483 |
| "Turn this on and CivicCast downloads the free TSDuck toolkit for you — no separate setup, no admin rights ..." | Cable verification | CableVerificationCard.tsx:68 |

## What the screen really does
Channels is where a channel's outgoing video feed is started, stopped and restarted. Start, Stop, Restart and "Finish current item, then stop" only queue a command for a separate feed program, so the state tag can take up to about 30 seconds to change. "Take live" switches the channel to the first ready live source, "Approve & put on air" approves one scheduled program and also starts or reloads the feed, and the 24/7 box can restart a stopped channel by itself. Several controls (Save automation settings, Approve & put on air, Confirm take live, Return to schedule, lower-third Put on air) take effect with no pop-up box. The lower-third text reaches the picture only on the channel's next restart or content swap.

## Mismatches
| ID | Text says | What happens (code) | Severity |
|---|---|---|---|
| HELP-01 | Stop dialog: "Residents watching lose the stream until the feed is started again." | With "Keep this channel on air" on, automation queues a new Start for a dark channel, retrying about every 30 s (`civiccast/egress/automation.py:1348-1383`). No stop is remembered. | blocks work |
| HELP-02 | "Starts the feed automatically after restarts and crashes." | Saving it ON for an enabled channel can start the channel with nobody pressing Start; Save has no confirmation. | blocks work |
| HELP-03 | Lower-third pill "On air" | Shows the saved setting; picture changes only at next pipeline build (:1135, 1198). | misleading |
| HELP-04 | "Approve & put on air" / "Queued to air" | Publishes the schedule item and queues a start or reload (`civiccast/schedule/commit_service.py:362-476`); program airs at its scheduled time. No confirmation. "On air (confirmed)" is never set (`commit_models.py:79-88`). Future rows say "aired". | misleading |
| HELP-05 | "Put a live source on this channel now" | Source is not named or chosen (`path_id` not sent); first Delivering source is used; lasts up to 3600 s (`takeover_service.py:146`); button greys 30 s after last Check source. | blocks work |
| HELP-06 | Station config and branding forms look usable by all | Server needs setup_admin or publish_operator (`civiccast/app_platform/router.py:152, 192`); others get a raw role error. "Build tier", "Store ready", "Analytics enabled" are unexplained. | misleading |
| HELP-07 | "Nothing has been committed to air on this channel yet." | Also shown when the list call fails (CommitToAirPanel.tsx:432-436). | misleading |
| HELP-08 | Banner note: "meeting operator or setup admin" | Screen enables the button only for meeting_operator (ChannelOpsScreen.tsx:2089). | misleading |
| HELP-09 | "Turn this on ... downloads ... free TSDuck toolkit" | It is a download button with no confirmation; needs internet; size not stated (UNVERIFIED). | misleading |
| HELP-10 | "Create one from the channel egress runbook (or the setup flow)" | Real step is applying a headend preset on this screen. | blocks work |
| HELP-11 | "Proof log", "Machine summary", "CTV feed", "Decode-back proofs", "LUFS/LKFS", "SAP", "TSDuck", "mux rate", "HLS", "NDI", "SDI", "pipeline build", "Regime" | Jargon, unexplained. | cosmetic |
| HELP-12 | "refreshes every 30s" | After Stop, Restart or Finish the tag can lag up to 30 s; only Start polls every 2 s for 20 s (:1880-1892). | misleading |
| NEW-1 | "Enable it in Outgoing feed configuration" (`egress-start.ts:17`) | No panel has that name and no control on this screen enables a disabled configuration. | blocks work |

## Proposed text
- Header intro (:1973): "Start, stop and watch each channel's video feed, put scheduled programs on the air, and take a channel live from a camera. Anything marked 'changes what is on the air' takes effect for residents at once or at the next restart."
- Who can use this (new): "Meeting operator: Start, Stop, Restart, Finish, Take live, lower-third. Publish operator or Setup admin: Commit programs to air. Setup admin: 24/7 settings, cable headend, takeover history."
- Feed card note (new, under the buttons): "These buttons send a command to the feed program. The state tag can take up to 30 seconds to change. Do not press again right away."
- Stop dialog body (feed-command-confirm.ts:30): "This takes {ch} off the air. If 'Keep this channel on air' is on for this channel, CivicCast starts it again within about 30 seconds. Ask a Setup admin to turn that off first if you want it to stay stopped." After fix (stop remembered): original text.
- "Keep this channel on air" help (:919): "CivicCast starts this channel again whenever it is not running, including after you press Stop. Saving this setting can put the channel on the air at once. There is no confirmation box."
- Start grey reason (egress-start.ts:17): "The outgoing feed for {id} is switched off in its settings. Ask your IT person to switch it on." (:14): "No outgoing feed is set up for {id}. A Setup admin sets one up with a headend preset in Cable headend delivery."
- No-configuration text (:896-898, 1159-1161): "This channel has no outgoing feed set up yet. A Setup admin chooses a preset in Cable headend delivery on this screen. Then these settings appear."
- Take live card (:157): "Switches this channel to a live camera or encoder now, ahead of the schedule. Before you press it, run Check source on the Live screen. It uses the first source that is Delivering, which may not be the one you picked. It runs for up to one hour, so press Return to schedule when you are done."
- "No live source is ready yet." (:276): "No source has passed Check source in the last 30 seconds. Go to the Live screen, press Check source, then come back. This button can take about 15 seconds to turn on."
- Take-live badge note: "The red badge shows who took over, not which source. Read the Source line in the Outgoing channel feed box and watch the channel's output."
- Commit card intro (:344): "Check a scheduled program, then approve it. Approving publishes it so it plays at its scheduled time. It does not play it now. On a stopped channel it also starts the channel. There is no second confirmation."
- Button (:85): "Approve for air". Tag "Queued to air" (commit-format.ts:18): "Command sent". Remove "On air (confirmed)". Row text (:200): "scheduled for {time} · approved by {operator}".
- Empty recent commits (:434): "No approvals to show. If you expect some and your role cannot read them, ask a Publish operator." After fix: show the load error.
- Lower-third pill (:1135): "Saved: on" / "Saved: off". Banner role note (:1271): "Needs the Meeting operator role." (:1271 after role fix: "Meeting operator or Setup admin".)
- Station config and branding: one line above each: "Changes the name, colours and links shown in the resident app and portal. Setup admin or Publish operator only." Add one-line meanings for "Build tier" (branded or unbranded app), "Store ready" and "Analytics enabled" after the coder confirms them.
- Cable verification text (:68): "Downloads the free TSDuck tool from the internet so CivicCast can check your cable stream. Press the button only on a station that is online. It runs without confirmation and can take several minutes."
- Glossary (new): "Proof log: a record of what the feed program says it played. Headend: the cable company's equipment. NDI and SDI: ways to send video to other equipment. HLS: the web video format residents' players use. Pipeline build: the moment the feed program rebuilds the picture, at a restart or content swap."

## Notes for the coder
- Files: `ChannelOpsScreen.tsx`, `CommitToAirPanel.tsx`, `commit-format.ts`, `TakeoverCard.tsx`, `egress-start.ts`, `feed-command-confirm.ts`, `CableVerificationCard.tsx`.
- Tests that pin strings: `ChannelOpsScreen.test.tsx` pins "Fallback", "Stopped", "On air", "Needs attention", "Put on air", "Take off air", "Confirm: put on air", "Confirm: take off air", "Enter banner text before putting it on air." (372), /requires the meeting operator or setup admin role/ (387), 'No outgoing-feed configuration for public.' (758), "Start feed" (885), "Restart the channel to put it on air" (535), "HLS web output is not enabled for this channel." (457). `components/ConfirmDialog.test.tsx:15-31, 103-105` pins "Residents watching lose the stream" and the labels "Stop feed", "Start feed" (the Stop body text is the test's own prop, but line 103 asserts `feedCommandConfirmCopy` labels). `CommitToAirPanel.test.tsx` pins "Approve & put on air" (144-161), /requires the publish operator or setup admin role/ (153), "Take off air", "Confirm take-off", label 'Reason for taking off air', "Rolled back: aired in error". `TakeoverCard.container.test.tsx` pins "Take live", "Confirm take live", /No live source is ready yet/, /Live takeover — Dana/, /requires the meeting operator or setup admin role/. Renaming "Approve & put on air" needs those tests and the manual updated.
- Code fixes, not text fixes: stop must be remembered when auto-start is on (HELP-01); confirm box on Save automation settings and on Approve (HELP-02, HELP-04); name and choose the takeover source, say what happens at 1 hour (HELP-05); gate or explain forms by role (HELP-06); show errors in Recent commits (HELP-07); allow Setup admin to edit the lower third or fix the note (HELP-08); a control to enable a disabled feed configuration (NEW-1); set `acknowledged` or stop showing "On air (confirmed)".
- Not verified: what happens when a 3600 s takeover ends; size of the TSDuck download; role gates of the programlog and audio-tracks reads.
