# Agenda guidance (historical RC1 snapshot)

> The guidance below is the current wording as published with the RC9 manual; the RC1 label records when it was written.

A setup administrator uses **Agenda source setup → Use Longmont / PrimeGov → Save agenda source**. Public PrimeGov needs no credentials. Saving applies immediately and survives restart. Records clerks and meeting operators choose a meeting explicitly, review the retained original and numbered preview, then confirm a complete import. Previewing does not create agenda items; confirmation does not publish them.

Load the original PDF and use the source-page links to inspect titles. Large packets and OCR may take minutes. Cancel stops processing; Resume reuses completed pages from the retained revision. This release includes offline English OCR. If a page remains unreadable or a limit is reached, the preview stays incomplete and cannot be confirmed. Obtain a legible source or enter reviewed items manually.

Agenda time fields accept `hh:mm:ss` and whole seconds. Use the recording clock indicated by the form.

The historical audit below describes older releases only. Current shipped Help is rendered from `docs/USER-MANUAL.md`.

> Historical beta.10 audit snapshot dated 2026-10-03; retained for traceability, not current UI or user guidance.

# Agendas (nav id: agendas)

Console group: Review Records. Spec for the in-app help of the agenda builder. Written against beta.10. Paths are under `civiccast/apps/portal-operator/src/`.

## Where the help text lives now
All in `screens/AgendasScreen.tsx`.
- Page heading and intro: 1202-1206. Wrong-role banner: 1129. "Loading…": 1113.
- Create new agenda card: heading 194; labels 196, 209, 222; helper under Source doc URL 235; button 246.
- Selected agenda card: Publish tooltip 325; Unpublish 342; no-items line 371-372; Delete / Confirm delete 346-366 (warning line 378).
- Items form: labels 485, 501, 519, 549, 595, 610; Notes placeholder 616; timecode tip 563-588; timecode error 590-592; Order error 514.
- Items table: empty 668-670; stale warning 1482; Confidence tooltip 707; column heads 679-683.
- Bulk actions: heading 1512; Sync from chapters 1524; synced banner 1533; Import from doc 1547; help 1575; PDF help 1581-1583; Import PDF 1605; reopen-to-draft note 1607-1610; imported banner 1617-1621.
- External import block: heading 903; tenant label 929; runtime line 986-992; date label 997; Find meetings 1018; Import selected meeting 1066; reopen note 1070-1073. Future-release card 796.
- No agendas: 1256-1257.
- Server messages: `civiccast/agenda/service.py:136-142` (publish with no items, contains "DC-1"); `civiccast/agenda/router.py:160-169`; `civiccast/agenda_import/router.py:57-60`; `civiccast/agenda/store.py:253-256`.

## Current text
| String | Where | Source line |
|---|---|---|
| "Build and publish a meeting agenda the resident sees alongside the recording. Agendas stay drafts until published; published agendas appear on the public meeting page. Publishing needs at least one item." | Page intro | :1204-1206 |
| "Agendas require the records clerk or meeting operator role. Ask your station admin for access." | Wrong role | :1129 |
| "Agenda ID (slug)" / "Meeting asset ID" / "Source doc URL (optional)" | Create form | :196,209,222 |
| "Link to the published PDF/HTML agenda the resident can read alongside the video." | Source doc helper | :235 |
| "Publish needs at least one item — add one below or sync from chapters." | Publish tooltip | :325 |
| "Confirming will also delete every item under this agenda." | After first Delete click | :378 |
| "Item ID (slug)" / "Order" / "Number (label, optional)" / "Video timecode (seconds, optional)" / "Doc anchor (optional)" / "Notes (optional)" | Item form | :485,501,519,549,595,610 |
| "Operator-private notes; not shown to viewers." | Notes placeholder | :616 |
| "Tip: open the meeting on the public portal in another tab, find the moment in the player, then paste the seconds here." | Timecode tip | :567-588 |
| "Another agenda item already occupies (agenda_id='…', order=0)." | Server 409 shown raw | agenda/store.py:253-256 |
| "Cannot publish agenda '{id}': it has zero items. Add at least one item (DC-1) before publishing." | Server 422 shown raw | agenda/service.py:136-142 |
| "An agenda already exists for (station_id='…', meeting_asset_id='…')." | Server 409 shown raw | agenda/router.py:160-169 |
| "No items on this agenda yet. Add one with the form above, or run "Sync from chapters" in the Bulk actions section if the meeting asset already has chapter markers." | Items empty | :668-670 |
| "Import from doc (paste plain-text agenda, one item per line)" / "Taken literally, one item per line — nothing to review." | Bulk actions | :1547,1575 |
| "Or upload a PDF agenda (best-effort: numbered items, ALL-CAPS section headings, and call-time markers are recognized; each imported item is scored with a confidence so you can spot guesses that need a check)" | PDF help | :1581-1583 |
| "Confidence score from the PDF import heuristic — review before publishing if low." | Tooltip | :707 |
| "Import from an external agenda system"; "Tenant / site code" | External block | :903,929 |
| "Agenda import is not enabled. Set CIVICCAST_AGENDA_SOURCE to 'legistar', 'primegov', 'civicclerk', or 'js_portal' to turn it on." | Server 404 in yellow banner | agenda_import/router.py:57-60 |
| "No agendas yet." / "Agendas list what a meeting will cover and appear alongside its recording on the public meeting page. Create one with the form above — it stays a private draft until you publish it." | Empty state | :1256-1257 |

## What the screen really does
One agenda belongs to one recording, named by its asset ID, which you type exactly as shown under the title on the Assets screen; a second agenda for the same recording is refused. An agenda stays a private draft until you click Publish, which needs at least one item; once published, residents see each item's number, title and time beside the video on the recording's watch page and can click an item to jump to its time. Items can be typed in, made from the recording's chapter markers, imported from pasted text, imported from a PDF's text layer (with a confidence mark per item), or imported from an outside agenda system, which is off unless IT turns it on. Edits to a published agenda go live at once; only a PDF or external import moves it back to draft. Delete removes the agenda and all its items with no undo. Only records_clerk and meeting_operator can open this screen.

## Mismatches
| ID | Text says | What really happens | Severity |
|---|---|---|---|
| HELP-01 | "Meeting asset ID" with no help | Free-text, internal ID, lowercase; one agenda per recording; whether a nonexistent ID is refused is UNVERIFIED | misleading |
| HELP-02 | "Agenda ID (slug)", "Item ID (slug)" | Jargon; allowed characters are lowercase letters, digits, hyphen, underscore, starting with a letter or digit, up to 120 (`agenda/models.py:45`); a wrong character shows raw JSON | misleading |
| HELP-03 | "Order" starts at 0 every time | Second item with the same Order is refused with the raw 409 text | blocks work |
| HELP-04 | "(DC-1)" in the publish-refused message | Internal rule code (`service.py:136-142`) | cosmetic |
| HELP-05 | Delete then "Confirm delete" | No Cancel; only choosing another agenda backs out; no undo; same for item delete | misleading |
| HELP-06 | Intro silent about live edits | Item edits on a published agenda are public at once (`router.py:493-501`) | misleading |
| HELP-07 | External import block shown to everyone | Off by default; the server text names an environment setting a clerk cannot change; "Tenant / site code" unexplained | misleading |
| HELP-08 | "Video timecode (seconds…)" | Clerk must convert to seconds; tip tells them to paste seconds | misleading |
| HELP-09 | PDF help is long and technical | A scanned PDF with no text finds nothing | cosmetic |
| HELP-10 | Page always creates agendas for station `civiccast-station` | List is filtered by `CIVICCAST_STATION_ID`; UNVERIFIED whether it differs on any install | cosmetic |
| HELP-11 | Never says where residents see the agenda | Recording's public watch page; link only when `VITE_PUBLIC_PORTAL_BASE_URL` was set at build | misleading |
| HELP-12 | Developer comments mention "PDF returns 415" | Not shown to users; no text change | cosmetic |

## Proposed text
What this is for (new, under heading): "An agenda is the numbered list of items for one recorded meeting. When you publish it, residents see it next to the video on that recording's watch page, and clicking an item with a time jumps the video to that moment."
Who can use it (new): "Records clerk or meeting operator."
Intro: "Each recording can have one agenda. An agenda stays private until you click Publish, and it needs at least one item. After it is published, changes you save to its items are visible to residents right away. Residents never see your Notes."
Wrong-role text: keep.
Create form: "Agenda ID" helper: "A short name for your own use, for example council-2026-01. Lowercase letters, numbers and dashes only." "Meeting asset ID" helper: "The ID of the recording, shown in small type under its title on the Assets screen. Type it exactly, in lowercase. One agenda per recording." After fix: a recordings drop-down by title.
Source doc URL helper: "Optional. A web address for the agenda document. Residents get an Agenda document link; a .pdf link opens in a viewer."
Item form: "Item ID" helper: "A short name, for example item-01-call-to-order. Lowercase letters, numbers, dashes." Order helper: "Position in the list: 0 for the first item, 1 for the next, and so on. Each item needs its own number." After fix: default to the next free number. Timecode helper: "Where the item starts in the video, in whole seconds. For 1 hour 5 minutes type 3900." After fix: accept 1:05:00.
Publish tooltip: "Add at least one item first, or use Sync from chapters."
Publish refused (replace the server text): "Add at least one item before publishing."
Delete warning: "This deletes the agenda and every item in it. It cannot be undone. To back out, choose a different agenda in Pick an agenda." After fix: Delete plus a Cancel button.
Items empty: keep. Sync from chapters helper (new): "Makes one item per chapter marker on the recording. Chapter markers are added in the trim editor. Items whose Order already exists are skipped."
Import from doc help: "One item per non-blank line. A leading number such as 3.a becomes the Number. Nothing is scored; check the result yourself."
PDF help: "Upload a PDF that contains text. CivicCast guesses the items; each gets a Confidence mark (green 90% or more, amber 50 to 89%, red below 50). A scanned paper PDF with no text finds nothing; paste the text instead. Importing moves a published agenda back to draft."
External import (when off): "Not turned on for this station. Ask your IT person." Tenant helper: "The short name your city uses in that system, for example longmont."
Where residents see it (new line on the selected-agenda card): "Residents see this on the watch page for meeting asset {id}."

## Notes for the coder
- Edit `AgendasScreen.tsx`. Server strings: `agenda/service.py:136-142` and the 409/422 texts (translate client-side or change the server).
- Tests that pin strings (verified by grep): `screens/AgendasScreen.test.tsx` pins "Confirming will also delete" and "No agendas yet". "DC-1" is not pinned in any agenda test. "Build and publish" and "Publish needs" are not pinned.
- Code fixes, not text fixes: recordings drop-down and auto-generated IDs (HELP-01/02); next-free Order default (HELP-03); Cancel for delete (HELP-05); h:mm:ss timecode (HELP-08); hide or disable the external import block when `CIVICCAST_AGENDA_SOURCE` is off (HELP-07); a link to the public watch page that does not depend on a build variable (HELP-11); check that a station-id mismatch cannot hide new agendas (HELP-10).
- UNVERIFIED in the inventory: whether the server rejects a nonexistent meeting asset; how the public agenda panel looks.
