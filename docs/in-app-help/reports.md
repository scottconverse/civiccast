## Operator guidance — RC2 snapshot, October 30, 2026 (historical)

**Output start observed** replaces the ambiguous Verified label. An unfinished row shows Awaiting end / Not finalized. Internal source boundaries update current labels and as-run rows; preparation-window continuations stay one play. Aggregate airtime is clipped to the selected report window; old ledger rows are retained.

The shipped built-in Help is generated from `docs/manual/src/`; the audit below is historical.

> Historical beta.10 audit snapshot dated 2026-10-03; retained for traceability, not current UI or user guidance.

# Reports (nav id: reports)

Console group: Publish. Spec for the in-app help of the as-run (what aired) reports. Written against beta.10. Paths are under `civiccast/apps/portal-operator/src/`; line numbers are in `screens/ReportsScreen.tsx` unless stated.

## Where the help text lives now
- Heading and intro 612-620; "No content has aired yet" banner 623-628.
- Tab names 70-72; filter bar 147; "Pick a From date..." 185 and 642; "Channel (optional)" 190; "Channel list unavailable; type the channel ID." 207; "Field key (required)" 230.
- Download buttons 294, 304; download failure 278.
- Shows empty 389-392; As-Run empty 430-433; table heads As-Run 440-449 (Source 445, Verified 448); Hours empty 480; Hours no key 712.
- Query errors 664, 691, 722. Identity loading 586; identity error 594-597; no-role banner 605.
- Unknown custom field text: `screens/reports-format.ts:72-77`.
- Role gate: `components/shell/Sidebar.tsx:166` (`support_admin` only); API `civiccast/reporting/router.py:70,141,166,196,220`. Public copy: `router.py:271-293`.

## Current text
| String | Where | Source line |
|---|---|---|
| "Franchise-compliance reports off the as-run log. The date range is half-open ([from, to)) so a single day = From today, Through tomorrow. CSV/XML downloads use the same filter set as the on-screen table. Dates are UTC midnight to UTC midnight; if your station runs on local time, factor the offset into the From / Through you pick." | Intro | :616-619 |
| "No content has aired yet on this station. Reports will populate after your first scheduled meeting plays out." | Blue banner | :625-626 |
| "Pick a From date that is strictly before the Through date." | Warning | :185, 642 |
| "Channel (optional)"; "Channel list unavailable; type the channel ID." | Filter | :190, 207 |
| "Field key (required)" (default `category`) | Filter | :230 |
| "Download CSV" / "Download XML"; "Downloading CSV..." | Buttons (Shows and As-Run only) | :294, 304 |
| "No air times in the selected range. As-run rows appear automatically when a meeting plays out - schedule a meeting under Run Meeting > Schedule, then run it. (Wider date ranges may surface earlier broadcasts if you already have history.)" | Shows empty | :389-392 |
| "Source" column showing `program`, `filler`, `live`, `slate`, `spot`; "Verified" yes/no; "Asset" and "Channel" shown as raw IDs | As-Run table | :440-449 |
| "No custom field named "{key}" is defined for this station. Define it in Setup > Custom Fields, then re-run the report." | Hours tab | reports-format.ts:72-77 |
| "Enter the custom-field key to group by (e.g. category)." | Hours tab | :712 |
| "Reports require the support admin role. Ask your station admin for access." | Wrong role | :605 |

## What the screen really does
It reads the as-run log, the list of what the playout engine actually put on air, and shows it three ways: Shows (each recording once, with plays and airtime), As-Run (every change on air, in order) and Hours by Category (airtime added up by a custom field you name). The date range includes From and excludes Through, and both are UTC midnight, so an evening meeting in a Mountain-time station can fall on the next UTC day. Shows and As-Run can be downloaded as CSV or XML; Hours by Category cannot. Shows leaves out filler, slate and live rows. Every row is "Verified yes" because rows exist only when the engine confirmed the source went on air. The screen does not count viewers; use Analytics for that. Only the support_admin role sees this menu entry. A copy of the as-run list is also readable by anyone without sign-in at `/api/public/reports/as-run`, without Category and Verified.

## Mismatches
| ID | Text says | What really happens | Severity |
|---|---|---|---|
| HELP-01 | "Define it in Setup > Custom Fields" | Custom Fields is for setup_admin only (`Sidebar.tsx:113`); this screen is for support_admin only | misleading |
| HELP-02 | "half-open ([from, to))" | Jargon; UTC offset warning is buried in the paragraph | misleading |
| HELP-03 | Asset and channel shown as raw IDs | No recording titles | misleading |
| HELP-04 | "Verified" undefined | Always yes for engine-written rows (`asrun_recorder.py:46`) | cosmetic |
| HELP-05 | "Source" raw words | `spot` never appears; filler is the community bulletin board, slate is the fallback card (`egress/asrun.py:90-108`) | cosmetic |
| HELP-06 | "No content has aired yet" blames an empty log | Also a wrong channel, a UTC range, or the log not being written | misleading |
| HELP-07 | Hours tab has no download buttons | Not stated | cosmetic |
| HELP-08 | Default `category` key | If no such field exists the first Hours view shows an error | misleading |
| HELP-09 | "Franchise-compliance" | Undefined | cosmetic |
| HELP-10 | Nothing mentions the public as-run copy | Anyone can read the air log without signing in (`reporting/router.py:271-293`) | misleading |

## Proposed text
- **What this is for (new, under heading):** "A record of what actually went out on your channels, for a city or cable franchise that asks for proof of what aired and when. It does not count viewers."
- **Who can use it (new):** "Support admin only."
- **Warning (new, beside the date boxes):** "Dates are in UTC, the world clock. Mountain daylight time is 6 hours behind UTC, so a 7 p.m. meeting is already the next day in UTC. If a meeting seems missing, widen the range by a day."
- **Intro:** "Pick the first day you want in From and the day AFTER the last day you want in Through. To see one day, pick that day and the next day. Downloads use the same dates and channel as the table. A franchise report is a report a cable franchise agreement may require: what aired, when and for how long."
- **Empty banner:** "Nothing has aired in this date range. Check that the Channel is right and that the dates cover your meeting (they are UTC). If nothing has ever aired, reports fill in after your first scheduled program plays."
- **Tabs help:** Shows = "Each recording once, with how many times it aired and the total time."; As-Run = "Every change of what was on air, in order."; Hours by Category = "Total airtime for each value of a custom field you name, such as category."
- **Field key helper:** "The name of a custom field on your recordings, such as category. Fields are created on the Custom Fields screen in Setup, which a setup administrator can open."
- **Unknown field text:** "No custom field named "{key}" exists. Ask your setup administrator to create it on the Custom Fields screen, then run this again."
- **Verified tooltip:** "Yes means the station's playout system confirmed this really went on air. Every row is yes in beta.10."
- **Source tooltip:** program = a recording from your library; filler = the community bulletin board; live = a live feed; slate = the fallback card shown when a feed fails; spot = a sponsor message (not recorded in beta.10).
- **Hours tab note:** "This tab cannot be downloaded. Read it on screen."
- **Public-copy note (new, small print):** "A list of what aired is also available to anyone, without sign-in, at /api/public/reports/as-run. It leaves out Category and Verified."
  - After fix: show recording titles beside Asset IDs.

## Notes for the coder
- Edit `ReportsScreen.tsx`, `reports-format.ts`.
- Tests that pin strings (verified by grep): `screens/ReportsScreen.test.tsx` pins "No content has aired". "Franchise-compliance", "half-open" (in operator tests) and "Define it in Setup" were not found in any operator test.
- Code fixes, not text fixes: Title column (HELP-03); let setup_admin and support_admin both open Custom Fields or both Reports (HELP-01); download for Hours tab (HELP-07); explain or limit the public as-run endpoint (HELP-10).
- UNVERIFIED in the inventory: exact CSV/XML columns; that the engine writes an as-run row on every real transition on a live install; whether the public endpoint is reachable from the portal.
