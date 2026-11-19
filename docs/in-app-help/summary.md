# Summary review (nav id: summary)

Console group: Review Records. The page label on screen is "Summary + signed records". Current operator guidance is followed by the beta.10 audit snapshot kept for traceability. Paths are under `civiccast/apps/portal-operator/src/`. The part that makes a summary is the "AI summary" card on an asset page; see assets.md.

## Current operator guidance (rc.7)

A records clerk reviews a computer-written meeting summary against the caption cues listed under **Sourced claims**. The cue buttons show cue IDs and times; they do not show caption words or play audio, so open the recording or Review queue to check the text.

1. On a **Pending review** card, read the narrative and its sourced claims. Click **Edit summary** to revise the narrative, then **Save changes** or **Cancel edit**. Saving changes the audit fingerprint; the sourced claims stay as generated.
2. Click **Approve summary** after checking the claims. CivicCast records the operator identity from the signed-in staff session. Only records clerks can edit or approve.
3. An **Approved** card stays on this page. Click **Export signed record** to create a PDF/A-3B record. Its timestamp is deterministic test data unless the station has configured a real timestamp authority.
4. Use **Download signed record** to save the export and **Verify signed record** to check its stored artifact and timestamp proof.
5. To find an export after a page refresh, click **Load saved records** on the approved card. The page requests at most 10 recent records for that summary and offers **Download** and **Verify** actions for each. Only records clerks can access these actions.

If a save is stale, reload the card and reapply the edit. If an action fails, follow the page's recovery message. Summary review does not have a reject or regenerate action; to make another draft, update caption review if needed and use **Generate summary** on the recording's asset page.

> The material below this heading is the historical beta.10 audit snapshot dated 2026-10-03. It describes the earlier UI and is retained for traceability only; do not use it as current operator guidance.

## Historical beta.10 audit snapshot

## Where the help text lives now
- `screens/SummaryReviewScreen.tsx`: label 255; heading 257; intro 259-261; role note 267; yellow evidence bar 108-110; card title (asset ID) 146; summary ID and model tag 148; operator message 159-160; Approve summary 185; Export signed record 197; export success 206-209; empty state 87-92; error box 61-67; error box also used after failed Approve or Export 273-281.
- `components/review/SourcedClaimList.tsx`: heading 30; no-claims text 24-25.
- `components/review/TranscriptCuePlayer.tsx`: "Inline transcript player" 20; "Transcript seek target" 26.
- `types/summary.ts`: status words 6-11.
- `screens/GenerateSummaryPanel.tsx`: time note 25; chip 80; Generate summary 133; running text 147; done text 164; no-cues 103; role notes 137, 203.
- Approve payload: `SummaryReviewScreen.tsx:10-14,234`; server model `civiccast/summary/router.py:111-115`.

## Current text
| String | Where | Source line |
|---|---|---|
| "Summary + signed records" / "Summary review" | Label, heading | SummaryReviewScreen.tsx:255, 257 |
| "Approve only summaries whose quantitative claims link to transcript cue timestamps, then export the PDF/A-3B signed-record artifact for local record review." | Intro | :259-261 |
| "Summary approval and signed-record export require the records clerk role. Evidence remains readable." | Role note | :267 |
| "{n} summary needs / items need more evidence before export. Next step: regenerate from committed transcript cues or add timestamp-backed source ranges before approving." | Yellow bar | :108-110 |
| "No summaries need review." / "Next step: open a recording's asset detail page and use the "Generate summary" action there (next to its offline caption jobs) to start one from its committed transcript cues. New pending summaries and evidence refusals will appear here once generation completes." | Empty | :87-92 |
| "Could not load summary review." + "Retry this request. If it fails again, check summary review logs and confirm the CivicCast database is connected." | Error (also shown after a failed Approve or Export) | :61-67 |
| "{operator message} Next step: regenerate after adding transcript evidence for each quantitative claim." | Needs-evidence card | :159-160 |
| "This summary has no sourced claims. Next step: regenerate the summary from committed transcript cues before approval." | Card | SourcedClaimList.tsx:24-25 |
| "Inline transcript player" / "Transcript seek target" (cue IDs and times only) | Right box | TranscriptCuePlayer.tsx:20,26 |
| "Approve summary" / "Export signed record" | Buttons | :185 / :197 |
| "Signed record exported: {record id}. Digest {sha256}. The server validates the PDF/A-3B artifact; timestamp authority remains deterministic unless a real authority is configured." | Export success | :206-209 |
| Pending review / Approved / Rejected / Needs evidence | Status pills | types/summary.ts:6-11 |
| "Generating locally can take 1-6 minutes on a CPU-only station, depending on ..." | AI summary card | GenerateSummaryPanel.tsx:25 |

## What the screen really does
It lists AI-written summaries that are waiting (status Pending review) or refused for lack of evidence (Needs evidence). Each card shows the recording's asset ID, the summary paragraph and "Sourced claims" with a button per source cue; a click highlights that cue in the right-hand box, which shows only the cue ID and times, not the words, and plays no audio. Approve summary sends an extra operator ID and name that the server refuses (it accepts only an approval note), so in our reading of the code it most likely fails with HTTP 422 and the page shows "Could not load summary review." with technical text. This was read from code and not run on a station. If Approve did work, the summary would become Approved and drop off this list, so Export signed record could no longer be reached; nothing in the console lists, downloads or verifies signed records. There is no Reject, regenerate or download button. Treat the whole workflow as unusable in beta.10.

## Mismatches
| ID | Text says | What really happens | Severity |
|---|---|---|---|
| HELP-01 | Approve summary offered to records clerks | Sends `operator_id` and `operator_display_name`; server model is `extra="forbid"` with only `approval_note` (`summary/router.py:111-115`), so a 422 is expected (`SummaryReviewScreen.tsx:10-14,234`). Not run live | blocks work |
| HELP-02 | "then export the PDF/A-3B signed-record artifact" | After Approve the card leaves the list (only Pending review and Needs evidence are listed, `summary/store.py:54-59`); Export is unreachable; no list, download or verify in the console | blocks work |
| HELP-03 | "Inline transcript player", "link to transcript cue timestamps" | Shows cue IDs and times only; no transcript words, no audio (`TranscriptCuePlayer.tsx:20-47`) | misleading |
| HELP-04 | "regenerate" in three places | No regenerate button here; the asset card shows only "Summary generated. Review it in Summary review" once any job exists (`GenerateSummaryPanel.tsx:392-399`) | misleading |
| HELP-05 | Card title is the asset ID; raw summary ID and model tag | No recording title or date | cosmetic |
| HELP-06 | "quantitative claims", "PDF/A-3B", "signed-record artifact", "timestamp authority remains deterministic" | Jargon; the timestamp is a test value unless a real authority is configured | misleading |
| HELP-07 | Failed Approve or Export titled "Could not load summary review."; 422 shown as raw JSON | The action failed, not the load | misleading |
| HELP-08 | "needs more evidence" | Does not say what to do: fix cues in Review queue, then generate again | misleading |
| HELP-09 | No Manual or Review queue link | n/a | cosmetic |

## Proposed text
- **What this is for (new, under heading):** "A records clerk checks a computer-written summary of a meeting against the caption lines it was built from, before it is kept as a record."
- **Who can use it (new):** "Anyone signed in can read. Approving and exporting need the records clerk role. Making a summary needs records clerk or support admin."
- **Banner for beta.10 (new, top of page):** "Beta.10 note: Approve summary is expected to fail with a technical error, and signed records cannot be reached from this page. Do not tell your records officer that a summary has been approved or a signed record exported."
  - After fix: "Approve a summary to keep it as a record, then export it as a signed record."
- **Intro (honest for now):** "Summaries are written by a computer from the caption lines you approved in Review queue. Each claim links to the caption lines it came from. Read the summary and compare it with those lines. In beta.10 you can read summaries here; approving and exporting are not working."
  - After fix: "Approve only summaries whose claims match the caption lines they point to. Approving makes the summary a record; Export signed record creates a tamper-evident file for your records officer."
- **Status words:** Pending review = "Waiting for a records clerk"; Needs evidence = "The computer could not tie this summary to caption lines. Fix or approve more caption lines in Review queue, then generate the summary again from the recording's page."
- **Needs-evidence message (replace the "Next step" text):** "No summary could be written because it could not be matched to caption lines with times. Open Review queue, approve the caption lines for this recording, then use Generate summary on its Assets page."
- **Source box:** rename "Inline transcript player" to "Source caption lines" with text "Shows which caption lines (by ID and time) support the claim. In beta.10 it does not show the words or play audio; open Review queue or the recording to read them."
- **Card title:** "{recording title} ({asset ID})".
- **Approve error (replace title):** "That did not go through: {reason}."
- **Export success:** "Signed record exported: {record id}. The timestamp on this record is a test timestamp unless your IT person set up a real timestamp service."
- **Empty state:** "No summaries need review. To start one: open a recording on the Assets screen, make sure its caption lines are approved in Review queue, and click Generate summary on its AI summary card."
- **Generate summary card (assets):** "Only caption lines with the status Approved are used; lines that are only Edited are not."

## Notes for the coder
- Edit `SummaryReviewScreen.tsx`, `SourcedClaimList.tsx`, `TranscriptCuePlayer.tsx`, `types/summary.ts`, `GenerateSummaryPanel.tsx`.
- Tests that pin strings (verified by grep): there is no `SummaryReviewScreen.test.tsx`. e2e `e2e/summary-review.spec.ts` pins "No summaries need review" and the button name "Approve summary"; `e2e/signed-records.spec.ts` pins "Approve summary" and "Summary review"; `screens/GenerateSummaryPanel.test.tsx` covers the asset card. Check what those e2e specs mock before changing the Approve call.
- Code fixes, not text fixes (do not do them here): send only `approval_note` from `approveSummary` (the server reads the operator from the sign-in token, `router.py:174-181`) (HELP-01); list Approved summaries and add Download and Verify (HELP-02); show cue text in the source box (HELP-03); add a Generate again button (HELP-04); separate error title for actions (HELP-07).
- UNVERIFIED in the inventory: that Approve returns 422 on a running station (no test posts these fields; `tests/summary/test_summary_router.py:120` posts `{}` only); that the card disappears after approval; whether another screen lists signed records.
