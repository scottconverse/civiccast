## Operator guidance — RC2 snapshot, October 30, 2026 (historical)

Schedule and Program Guide accept second-level starts through their normal date/time controls. Match the next start to the preceding duration; approve the entries and use Commit to Air. Upload local media through Assets, not an API workaround.

The shipped built-in Help is generated from `docs/manual/src/`; the audit below is historical.

> Historical beta.10 audit snapshot dated 2026-10-03; retained for traceability, not current UI or user guidance.

# Schedule (nav id: schedule)

Console group: Run Meeting. Spec written against beta.10. Paths are under `civiccast/apps/portal-operator/src/` unless they start with `civiccast/`. Authority: `docs/manual/src/12-before-meeting.md` ("Put a recording on a channel once", "Approve a program to air", "Cancel a scheduled item") and `ops/docs-sprint/inventory/screens/schedule.md`. Line numbers re-checked.

## Where the help text lives now
- `screens/ScheduleScreen.tsx`: header 856-861; view tabs and toolbar 866-929; empty state 273-295; error state 229-270; week-grid note 360; list 529-780 (publish review 751-753, role note 603-604, unreadable rows 618-621, buttons 708, 713, 727); cancel dialog 973-975; toasts 581, 809, 1004; no-items-this-week 963-964; "Not yet visible" / "Visible to residents" 210-226.
- `components/schedule/ScheduleDrawer.tsx`: drawer 222-523 (asset help 281-304, channels 338-342, start 353-376, timezone box 420-423, notes 431-437, conflict 453-475, buttons 504, 520-523).
- `types/schedule.ts:34-44` (mode descriptions) and `:52-56` (state words).
- `screens/CommitToAirPanel.tsx:79-177` (the review panel, shared with Channels).

## Current text
| String | Where | Source line |
|---|---|---|
| "Workflow" / "Schedule" | Eyebrow, heading | ScheduleScreen.tsx:856 |
| "Week of {dates} · times shown in your browser timezone. Conflicts on the same channel are rejected at the database layer." | Header line | ScheduleScreen.tsx:846, 860-861 |
| "week" / "list" / "Today" / "New scheduled item" | Toolbar | ScheduleScreen.tsx:866-929 |
| "Showing {h}–{h} (auto-extended to cover events outside business hours)." | Week grid note | ScheduleScreen.tsx:360 |
| "Nothing scheduled this week. Use the arrows to browse, or switch to List view to see all items." | Week view | ScheduleScreen.tsx:963-964 |
| "Schedule a premiere to publish a recorded asset at a specific time, or an embargo to release an approved asset later. Conflicts are caught at the database layer before the form submits." | Empty state | ScheduleScreen.tsx:281-283 |
| "Could not load schedule." / "Durable storage is not ready." / "Next step. Try again, or check the server logs for more detail." / "Open Setup, prepare durable storage, then return here." / "Go to Setup" / "Retry" | Error state | ScheduleScreen.tsx:239, 245-248, 257, 266 |
| "You can view the schedule here. Publishing a premiere to residents requires the publish operator or setup admin role." | List banner | ScheduleScreen.tsx:603-604 |
| "1 scheduled item has an air time this station cannot read, so it is not shown below." / "...Cancel and re-schedule them, or send this list to support:" | List banner | ScheduleScreen.tsx:618-621 |
| "Scheduled" / "Published" / "Cancelled"; "Premiere" / "Embargo" | Chips | types/schedule.ts:34-56 |
| "Not yet visible to residents" / "Visible to residents" | Row note | ScheduleScreen.tsx:215, 222 |
| "Publish to residents" / "Checking…" / "Cancel" | Row buttons | ScheduleScreen.tsx:708, 713, 727 |
| "Could not check whether this is ready to publish." | Error | ScheduleScreen.tsx:739 |
| "Publish to residents" / "Publishing…" / "Publishing to residents requires the publish operator or setup admin role." | Review panel | ScheduleScreen.tsx:751-753 |
| "Safe to air" / "Not safe to air yet" / "Clashes with N other program(s) already scheduled:" / "Dead-air gap before this program (it can still air):" | Review panel | CommitToAirPanel.tsx:111-113, 129-130 |
| "Visible to residents." / "Could not publish this to residents." | Toast, error | ScheduleScreen.tsx:581, 763 |
| `Cancel scheduled item for "{name}"?` / "The item is removed from the schedule and will not air at its scheduled time. This cannot be undone — you would need to create a new scheduled item to air it again." / "Cancel scheduled item" | Cancel dialog | ScheduleScreen.tsx:973-975 |
| "Cancelled." / "Could not cancel scheduled item." / "Scheduled." | Toasts | ScheduleScreen.tsx:809, 820, 1004 |
| "New scheduled item" / "Mode" / "Premiere" + "Publish a recorded asset to the public portal at a scheduled time." / "Embargo" + "Approve now; release becomes public at the embargo time." | Drawer | ScheduleDrawer.tsx:222, 244; types/schedule.ts:37-44 |
| "Asset" / "No validated assets. Upload and validate an asset in the Assets tab first." / "Only validated assets are eligible. Trim and chapter edits are applied at packaging time." | Drawer | ScheduleDrawer.tsx:265, 281-282, 303-304 |
| "Could not load this station's channels. Close and retry." / "No channels are configured on this station yet." | Drawer | ScheduleDrawer.tsx:338, 342 |
| "Start at" / "Release at" / "Enter a date and time to schedule this." / "Duration (min)" | Drawer | ScheduleDrawer.tsx:353, 376, 386 |
| "Timezone check: this time is saved from the browser timezone shown above. During daylight-saving changes, confirm the local meeting time against the station calendar before creating the schedule item." | Drawer | ScheduleDrawer.tsx:420-423 |
| "Notes (optional)" / "Operator notes — visible in the audit log." | Drawer | ScheduleDrawer.tsx:431, 437 |
| "Time slot conflicts." / "Existing: {asset} ..." / "Next step. Pick a different time, channel, or cancel the conflicting item." | Drawer | ScheduleDrawer.tsx:453, 461, 474-475 |
| "Schedule premiere" / "Schedule embargo" / "Scheduling…" / "Cancel" | Drawer buttons | ScheduleDrawer.tsx:520-523, 504 |

## What the screen really does
Schedule lists the one-off items placed on the station's channels and lets a Publish operator or Setup admin add a premiere (a recorded video placed on a channel at a start time for a set length). A new item is only a draft ("Scheduled"). It does not air, and residents cannot see it, until someone presses "Publish to residents" and the safety check passes. That button approves the program to air on the channel, not only on the portal. The list shows every item, not only this week's. The list has no Cancel button for a Published item. The Asset menu offers only videos in the Validated state, so a video that CivicCast recorded itself is not offered.

## Mismatches
| ID | Text says | What happens (code) | Severity |
|---|---|---|---|
| HELP-01 | Premiere: "Publish a recorded asset to the public portal at a scheduled time." | Saving creates a draft; nothing airs until Publish to residents (`civiccast/egress/source_plan.py:507-511`). | blocks work |
| HELP-02 | Embargo: "Approve now; release becomes public at the embargo time." | No code releases an embargo; it is left out of the channel program and Coming up, and approval refuses it (`civiccast/schedule/commit_service.py:154-161`). | blocks work |
| HELP-03 | Menu item visible to all roles | Reading needs publish_operator, setup_admin or support_admin (`civiccast/schedule/router.py:1242`); others get "Could not load schedule." plus "check the server logs". | misleading |
| HELP-04 | "No validated assets. Upload and validate an asset in the Assets tab first." | Drawer filters on `validated` only (ScheduleDrawer.tsx:99-101); recorded videos are not offered though approval accepts them (`commit_service.py:68`). | blocks work |
| HELP-05 | (nothing) | No cancel for a Published row (:716-729); Auto-schedule items are Published. | blocks work |
| HELP-06 | Toast "Visible to residents." | Shown on any 201 even if the engine nudge failed (:575-584). | misleading |
| HELP-07 | "rejected at the database layer" / "caught ... before the form submits" | Developer wording; the check happens when you save. | cosmetic |
| HELP-08 | Tabs "week" / "list", eyebrow "Workflow" | Raw ids, vague. | cosmetic |
| HELP-09 | "Publish to residents" | Also approves the program to air on the channel. | misleading |
| NEW-1 | "Showing 5 am–11 pm ..." etc. | Times are your browser's time zone; the Auto-schedule and Recording screens use other zones (UTC or station). | cosmetic |
| NEW-2 | Week-grid blocks | Clicking one switches to the list; no detail panel opens. | cosmetic |

## Proposed text
- Page line (:860-861): "Week of {dates}. Times are in your computer's time zone. The station will not let two programs overlap on the same channel."
- Page intro (new, above the toolbar): "Use this screen to place a recording on a channel at a set time. A new item is a draft. It is not on the air and residents cannot see it. To put it on the air, find it in the list and press Publish to residents."
- Who can use this (new): "Publish operator or Setup admin: add, publish, cancel. Support admin: look only. Other roles cannot open the list."
- Premiere card (types/schedule.ts:37-39): "Puts a recorded video on a channel at a set time. After you save it, press Publish to residents in the list to approve it to air." After fix: unchanged once saving also approves, otherwise as written.
- Embargo card (types/schedule.ts:41-44): "Not working in this version. Nothing releases an embargo item, and it cannot be approved to air. Use Premiere." Better: remove the card until it works. After fix: describe the real release behavior.
- Asset empty text (:281-282): "No videos are available. Only videos in the Validated state are listed, so a video CivicCast recorded itself is not offered. Upload the file again on the Assets screen, or ask your IT person." After fix (Recorded offered): "No videos are available. Add one on the Assets screen."
- Asset help (:303-304): "Only Validated videos are listed. Trim and chapter edits are applied when the video is packaged."
- Empty state (:281-283): "Nothing is scheduled. Press New scheduled item to place a recording on a channel."
- Error state for roles without access: "Viewing the schedule needs the Publish operator, Setup admin or Support admin role." Keep "Try again, or check the server logs" for real server errors only.
- Row button (:708 and :751): "Approve to air". Role message (:603-604, 753): "You can look at the schedule. Approving a program to air needs the Publish operator or Setup admin role."
- Review panel: add under the title: "Approving puts the program on the channel's schedule. It plays at its start time, not now. On a stopped channel it also starts the channel. There is no second confirmation."
- Toast (:581): "Approved to air." (Add: "The channel was told to re-read its schedule. If it did not respond, see Channels." only after the coder wires the dispatch status.)
- Cancel dialog (:974): keep; add "A Published program cannot be cancelled from this screen. Use Take off air on Channels." under the list when a Published row exists.
- Drawer conflict (:474-475): keep.
- Eyebrow (:856): "Schedule". Tabs: "Week" and "List".

## Notes for the coder
- Files: `screens/ScheduleScreen.tsx`, `components/schedule/ScheduleDrawer.tsx`, `types/schedule.ts`, `screens/CommitToAirPanel.tsx` (`DryRunReview` props `approveLabel`, `roleGateLabel`).
- Tests that pin strings: `screens/ScheduleList.publish.test.tsx` pins "Publish to residents" (120-122, 153, 176, 213, 229, 248, 262), "Not yet visible to residents" (146, 199, 212, 263), "Visible to residents" (211), /Publishing a premiere to residents requires the publish operator/ (156), "Safe to air", "Not safe to air yet", "Asset has no media file on disk yet.", and 'A schedule conflict appeared since you reviewed this — reload and try again.' (251). `components/schedule/ScheduleDrawer.test.tsx` pins /Schedule premiere/i buttons, 'No channels are configured on this station yet.' (133) and 'Enter a date and time to schedule this.' (185). `e2e/schedule.spec.ts` pins the dialog name "New scheduled item" (153, 189-487) and the button "Schedule premiere" (397-450). Renaming "Publish to residents" also touches the Program Guide and Channels help and `docs/manual/src/12-before-meeting.md`.
- Code fixes, not text fixes: make Embargo work or remove it (HELP-02); offer Recorded videos in the Asset menu or add a step that validates them (HELP-04); a cancel path for Published rows (HELP-05); read `dispatch_status` before saying "Visible" (HELP-06); role-aware error instead of "check the server logs" (HELP-03).
- Not verified: whether any other step turns a Recorded video into Validated; what Publish to residents does to the video's portal page; what the playout engine does after the nudge.
