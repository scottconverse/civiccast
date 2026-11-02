# Reports, analytics, program guide export, underwriting, and apps {#ch-station-business}

This chapter covers the "business side" of a station: how many residents watched, what aired and when, the schedule file that outside TV-guide services read, sponsor messages, the resident apps, and the optional paid-access feature. A PEG station employee, a records clerk or a station manager normally uses these screens. You do not need any technical background, but a few steps need an IT person, and this chapter says so each time.

## Before you start

You need to be signed in (see [Chapter 2](#ch-signing-in)). Five of the screens in this chapter sit in the left sidebar under **Publish**: **Analytics**, **Reports**, **EPG Export**, **Underwriting** and **App Admin**. The sixth, **Paywall**, sits under **Setup**.

CivicCast gives each person one or more *roles*. A role is a named set of permissions, such as "Support admin" or "Publish operator". The table shows who sees each screen in the sidebar and what each role can do on it.

| Screen | Shown in the sidebar to | What the role can do |
| --- | --- | --- |
| Analytics | Everyone signed in | Only Support admin and Publish operator can load the numbers |
| Reports | Support admin | Read and download |
| EPG Export | Setup admin, Publish operator | Create, run and delete exports |
| Underwriting | Setup admin, Publish operator, Support admin | Spots, Flights, Placements: Publish operator or Setup admin. Affidavits: Support admin |
| App Admin | Setup admin, Publish operator | Both can view and track; only Setup admin can start a build |
| Paywall | Setup admin | Everything |

> **Note:** The first administrator account carries all five roles. A setup admin can create staff accounts with narrower roles; your assigned roles determine which screens and actions are available.

> **Warning:** Several screens in this chapter ask you to type dates. CivicCast treats those dates as **UTC**, the world reference clock. In the United States UTC is several hours ahead of local time. Mountain daylight time is 6 hours behind UTC. A meeting that starts at 7 p.m. Mountain daylight time is already 1 a.m. UTC *the next day*. When a report looks one day off, this is almost always why.

## Read your audience numbers (Analytics)

Analytics shows how many residents watched recorded meetings (called *VOD*, "video on demand") and live streams.

1. In the left sidebar, open **Publish**, then click **Analytics**.
2. Look below the toolbar. If a box says **Audience telemetry is off**, nothing is being counted. Go to "If it did not work" at the end of this chapter.
3. Click one of the range buttons: **7d**, **30d**, **Quarter** or **Year**. The page opens on **30d**.
4. Click **VOD**, **LIVE** or **ALL** to choose which panels to show. The page opens on **ALL**.
5. Choose a number from the **Metric** drop-down: **Viewer Count**, **Time Viewed** or **Peak Concurrent**.
6. Click **bar** or **line** to change the chart style.

You should see two panels, **VOD** and **Live**. Each starts with a line in the form `12 views · 3.4h watched`, followed by a chart called "Top by" your chosen metric and a chart called "over time". A `· peak 5` ending appears only on the **Live** panel, and only when the live numbers include a viewers-at-once count (see the Known issue below).

> **Note:** **Quarter** means the last 90 days and **Year** means the last 365 days, counted back from now. They are not calendar quarters or calendar years.

> **Note:** The two panels and their charts read totals that CivicCast recalculates in the background about every 5 minutes by default, so they can trail the tiles under them by a few minutes.

Under the panels, the page shows:

- Four tiles: **Asset views**, **View hours**, **Live peak** and **Podcast downloads**.
- A box called **Privacy boundary**, with the time the report was made.
- Tables named **Asset Time Series** and **Live Concurrent Viewers**.
- Six small tables: **Geography**, **Device**, **Platform**, **Caption Usage**, **Audio Usage** and **Subscription Growth**.
- A link, **Show rollup data table**, that opens a table of the raw totals.

### What the numbers mean

- A **view** is one time someone starts playing a recording. The same person watching twice counts twice. The PDF report says this itself: "Viewer Count is a play count, not unique humans."
- The numbers cover **streaming only**: the resident website and apps. They do not include people watching on cable.
- Live "concurrent" numbers (how many people watch at the same moment) are estimates.
- **Podcast downloads** counts analytics events named `podcast_download`. Publishing an episode now creates playable audio and an RSS enclosure; the audio route does not itself emit that analytics event, so this tile can remain at 0 while downloads occur.

### What is collected, and what is not

The resident website sends a small anonymous message ("a beacon") for five things: playback started, playback heartbeat (a signal sent once a minute while a video plays, with the position in the video), playback finished, playback error, and which section of the schedule page someone opened.

CivicCast keeps: the event name, the time, which app sent it, the channel, the recording, and a short list of coarse extras.

CivicCast does **not** keep names, email addresses, IP addresses, session ids or any viewer identifier. The code states this as its privacy boundary, and the screen's **Privacy boundary** box repeats the rule as a machine phrase, `aggregate-only-no-session-ip-or-viewer-identity`. It means "counts only".

By default CivicCast discards events older than 366 days.

> **Note:** In beta.11 the resident website does not show residents a notice that views are counted. If your station wants one, you will need to post your own.

> **Known issue (beta.11):** Most of the extra tables stay empty. The resident website sends only the five messages above. It does not send the viewer's device, platform, country, caption language, audio track or how many people are watching live. The **Geography**, **Device**, **Platform**, **Caption Usage**, **Audio Usage** and **Subscription Growth** tables, the **Live peak** tile and **Live Concurrent Viewers** table therefore stay empty unless another app sends that information. This is expected, not a fault with your station. Watch time is also approximate: in both the file-based and the database-backed store we read, it is added up only from the position reported when a video finishes, so people who stop early add nothing.

> **Note:** When audience telemetry is off, the empty-state box says, "Ask your station administrator to enable audience telemetry in the service settings to collect Viewer Count and Time Viewed. The Reports tab (as-run / proof-of-performance) still works — it reads the program log, not the beacon." There is no Setup-screen switch; the station administrator changes the service settings. Until then, the Analytics report and PDF have no viewer-count or time-viewed data.

> **For IT staff:** The two settings are `CIVICCAST_PUBLIC_ANALYTICS_KEY` and `CIVICCAST_PUBLIC_ANALYTICS_ALLOWED_ORIGINS`; see [Chapter 11](#ch-configuration) and [Chapter 13](#ch-security). The resident website sends no key, so the website's own messages are accepted only when its address is listed in the allowed-origins setting (a comma-separated list of exact addresses such as `https://tv.example.gov`, matched against the browser's origin header). Setting only the key removes the "telemetry is off" box, but the website's messages are then refused.

### Download a spreadsheet

1. Choose the range you want with **7d**, **30d**, **Quarter** or **Year**.
2. Choose **VOD** or **LIVE** (see the Known issue below about **ALL**).
3. Click **Export CSV**.

Your browser saves a file named like `analytics-rollups-vod-30d.csv`. CSV is a plain table you can open in Excel or Google Sheets.

> **Known issue (beta.11):** With **ALL** selected, **Export CSV** exports the **VOD** table only, and says nothing about it. The file holds the totals table, not everything the page shows. If the download fails, nothing appears on screen. Choose **LIVE** and export again to get the live table.

### Make the board report (PDF)

1. Click **Generate Board PDF**. A small panel called **Include sections** opens. Four boxes are ticked: **Totals**, **Top content**, **Year-over-year** and **Live-event peaks**.
2. Untick any section you do not want.
3. Click **Download PDF**. The button reads **Generating…** while it works.

Your browser saves `audience-report-` followed by today's date (UTC). The PDF starts with the title, the reporting period and the time it was made, then three notes: streaming only, "a play count, not unique humans", and live numbers are estimated. Your ticked sections follow. **Top content** lists the ten most-played recordings by *recording id*, not by title.

If it fails, the page says "Could not generate the board PDF. Try again."

> **Known issue (beta.11):** The PDF always covers "now minus the range you chose" and ignores the **VOD**/**LIVE**/**ALL** buttons. Its title always reads "CivicCast station", even if your station has a name. If you need your station's name on the report, add it by hand before you send it on.

> **Known issue (beta.11):** The **Analytics** menu entry is visible to every role, but only Support admin and Publish operator can load it. Anyone else sees the frame of the page and the words "Report unavailable.", which does not say why. If you see this message and you expect access, ask your station admin.

## Find out what aired, and when (Reports)

Reports lists what actually went out on your channels. CivicCast keeps a log of each change of source on air, called the *as-run log*. Cities and cable companies sometimes ask for this as proof that programs aired (a "franchise report": a report your cable franchise agreement may require).

Reports reads the on-air log. It does not count viewers; use Analytics for that.

1. In the sidebar open **Publish**, then **Reports**. Only the Support admin role sees this entry.
2. Click a tab: **Shows**, **As-Run** or **Hours by Category**.
3. In **From**, pick the first day you want.
4. In **Through**, pick the day *after* the last day you want. The range includes **From** and excludes **Through**. To see one day, set **From** to that day and **Through** to the next day.
5. Leave **Channel (optional)** on **All channels**, or pick one channel.
6. On the **As-Run** and **Hours by Category** tabs, check **Field key (required)**. It opens as `category`.

The page opens on today's date through tomorrow's, UTC midnight to UTC midnight. If the dates are wrong, you see "Pick a From date that is strictly before the Through date." and nothing loads.

> **Warning:** Dates here are UTC. A station on Mountain time should widen the range by a day when looking for an evening meeting.

### What each tab shows

| Tab | Columns | Use it to |
| --- | --- | --- |
| **Shows** | Asset, Plays, Total airtime, First aired (UTC), Last aired (UTC) | See each recording once, with how many times it aired |
| **As-Run** | Channel, Planned start (UTC), Actual on-air start (UTC), Start variance, End (UTC), Duration, Source, Asset, Category, Output start observed | Compare the intended schedule with each actual change on air |
| **Hours by Category** | Category, Entries, Total airtime, Hours | See airtime per category, such as "Council" |

The **Asset** column shows the recording's id, not its title. The **Shows** tab leaves out anything without a library recording (filler, slate and live). **Actual on-air start** comes from the engine observing a selected source frame pass encoding into progressing output. Internal playlist changes update the source label and ledger; preparing a playlist is not an on-air start. **Start variance** compares that observation with the planned start. This is output-path evidence, not measurement at a television receiver. VOD preroll does not itself create an as-run entry.

Reports count overlapping records on the same channel only until the next observed source starts. A newer program, slate or open record ends the earlier record's contribution to the total; this does not imply that the earlier program resumes afterward. Different channels remain additive. Historical start/end records are preserved, and an open record remains provisional even when a later observation bounds its counted airtime.

The **Source** column uses these words:

- `program`: a recording from your library.
- `filler`: a community bulletin board graphics page.
- `live`: a live source.
- `slate`: the safety card the station shows in place of video, for example when a feed fails.
- `spot`: a sponsor message. In beta.11 this word never appears. See Underwriting below.

**Output start observed** identifies an engine observation, not a physical receiver check. An open row shows **Awaiting end** with provisional elapsed airtime labelled **in progress** until the next source or stop closes it. Continuations of one scheduled program across preparation windows stay one play; a new rotation counts as another play. Shows and category totals clip airtime to the requested report window. Historical Beta20 rows are retained unchanged and are not retroactively repaired.

Report durations and aggregate airtime count only the part of each airing within the selected date range. An ongoing airing contributes provisional elapsed airtime through the report request time, labelled **in progress**. Its end remains **Awaiting end**. CSV and XML use the same window durations while retaining the original recorded start/end timestamps; the stored ledger is unchanged. Repeat occurrences count separately when distinct output starts were recorded.

### Download a report file

On **Shows** and **As-Run**, click **Download CSV** or **Download XML**. Both use the dates and channel you chose. Your browser saves `civiccast-shows-` or `civiccast-as-run-` followed by the start date.

The **Hours by Category** tab has no download buttons. Read it on screen.

> **Known issue (beta.11):** The **Hours by Category** tab needs a *custom field* (an extra label you add to recordings, such as "category"). An unknown name shows: `No custom field named "{key}" is defined for this station. Define it in Setup → Custom Fields, then re-run the report.` Only the Setup admin role can open **Custom Fields**, while Reports is for the Support admin role. If you are not both, ask your Setup admin.

> **Note:** A copy of the as-run list is also available to the public at `/api/public/reports/as-run` with no sign-in. It leaves out Category and output-start evidence. Reports does not tell you this, so do not treat the as-run list as private.

> **For IT staff:** The public as-run endpoint is described in [Chapter 13](#ch-security).

## Send your schedule to a TV-guide service (EPG Export)

An *EPG* (electronic program guide) is the schedule file that cable boxes, TV-guide services and apps read to show "what is on". EPG Export turns your published schedule for the next several days into such a file. Each *export* is a saved setup naming a channel, a file type, how many days ahead, and optionally a web address to send the file to.

> **Note:** **Program Guide**, under Run Meeting, is a different thing. It holds your recurring slots and builds the on-air schedule (see [Chapter 3](#ch-before-meeting)). **EPG Export** makes a file for outside services from the schedule that already exists.

### Before you start

- The items you want in the file must be **published** on the Schedule screen. The file contains only published items on that channel that start between now and the number of days ahead.
- You need the channel's id. The **Reports** screen has a channel drop-down that lists them as `slug (channel_id)`, or ask your IT person.
- Ask the guide service for a sample file and its address first. In testing we could not confirm that any particular service, such as TitanTV, accepts what CivicCast makes. The "X-List" format here is a generic eight-column table.

### Create an export and download the file

1. Open **Publish**, then **EPG Export**.
2. In **Config ID**, type a name using lowercase letters, numbers, `-` and `_` only, such as `tv-guide-channel-1`. It cannot be changed later.
3. In **Channel ID**, type the channel's id.
4. In **Format**, choose **X-List (TV Guide / TitanTV)**, **XMLTV** (a common TV-guide file format) or **CSV**. Ask the guide service which one it reads.
5. In **Horizon (days)**, type how many days ahead to include. The box starts at 14. It must be more than 0.
6. Leave **Aggregator endpoint (optional)** empty. An empty address means "give me the file to download".
7. Click **Create config**.
8. In the **Configured exports** list, click **Generate now** on your export.
9. Click **Download document**.

You should see a green panel such as `12 slots · 3.0 KB (xlist)` (a single slot reads `1 slot`) with a **Download document** button. A *slot* is one scheduled program. Your browser saves `epg-export.xml`, `epg-export.csv` or `epg-export.txt` (X-List files end in `.txt`).

If the panel says `0 slots`, the channel has no published items in the window, or the channel id is wrong.

Each slot carries a start and end date and time in **UTC**, and the recording's title.

> **Known issue (beta.11):** The description, category and rating columns are always empty. The screen's wording suggests a full guide. Do not promise a service genre or rating information from this export in beta.11.

> **Note:** **Field map** accepts one `key=value` pair per line; blank lines and lines beginning with `#` are ignored. It renames headings in CSV and X-List only; XMLTV is unchanged. The current examples use valid columns: `title=Program Title` and `description=Meeting summary`. Available keys are `start_date`, `start_time`, `end_date`, `end_time`, `title`, `description`, `category` and `rating`.

### Send the file to an address instead

If you give an address in **Aggregator endpoint (optional)**, **Generate now** sends the file there.

1. Edit your export, or create a new one.
2. Type the service's address in **Aggregator endpoint (optional)**. It must start with `https://`.
3. Click **Save changes** or **Create config**.
4. Click **Generate now**.

You should see a panel that says `Pushed to {address} at {time}.`

> **Warning:** **Generate now** sends the whole guide at once, with no confirmation, and it cannot be taken back. CivicCast cannot log in to the receiving service, because no password or key can be stored. The file is sent as an open request. Nothing sends the file automatically on a schedule. Each press of **Generate now** sends it again.

If the send fails, the panel says "Push failed" with a reason, and gives you no file to download. To get a file, clear the address and run **Generate now** again.

The server accepts only `https` addresses and refuses `localhost` and private network addresses typed as numbers. It waits 5 seconds and does not follow redirects. The code does not look up where a web name points, so a name that points to a private address is not refused.

### Change or remove an export

1. To change days ahead, address or field map, click **Edit**, change the box, and click **Save changes**. Clearing the address switches the export to download-only.
2. To remove an export, click **Delete**, then **Confirm delete**. Data the guide service already received stays there.

## Record sponsor messages (Underwriting)

Underwriting means paid "sponsor acknowledgment" messages, such as "Support for this program comes from Acme Co-op". The screen uses these words:

- A **spot** is one short sponsor video, with the sponsor's name.
- A **flight** is a sponsor's run: which spot, which channels, and between which dates.
- A **placement** is a specific schedule slot a spot was assigned to.
- An **affidavit** is a sponsor-ready list of every time their spot aired, used for billing.

**Scheduling boundary:** Creating a flight does not insert video into the schedule automatically. IT staff must run the trafficking compiler against existing schedule slots through the station's programming interface. The scheduled asset must match the creative chosen for that placement. Commit that schedule through the ordinary approval controls. Once that exact slot, channel and asset actually air, CivicCast records a spot airing for the affidavit. Playing the same sponsor asset as an ordinary program does not count as a trafficked spot. A planned placement alone is not proof of airing.

### What does work

| You can | Result |
| --- | --- |
| Create, edit and delete spots | Saved |
| Create, edit and delete flights | Saved |
| Tick the compliance attestation | Saved as a record that a person reviewed the spot |
| Read **Placements** | Empty unless IT staff trigger the compile request |
| Read **Affidavits** | Actual airings matching compiled placements; empty until a matching spot airs |
| Click **Download CSV**, **Download XML**, **Download PDF** | Downloads the selected affidavit using your staff session |

### Add a spot

1. Open **Publish**, then **Underwriting**, then the **Spots** tab (Publish operator or Setup admin).
2. In **Spot ID**, type a name using lowercase letters, numbers, `-` and `_`, such as `acme-15s-2026q3`.
3. In **Underwriter**, type the sponsor's business name exactly as you will type it later when you look for it. The affidavit matches the name exactly, including capitals.
4. In **Asset ID (the :15 / :30 acknowledgment video)**, type the id of the sponsor video (the recording's id, from the Assets screen). The label's ":15 / :30" means a 15-second or 30-second message. Use lowercase letters, numbers, `-` and `_` here too.
5. Read the reminder box about 47 CFR 73.503. It says acknowledgments may name the sponsor, logo, location and a value-neutral description, and may not contain calls to action, prices, comparisons or promotional language.
6. Tick **I attest this spot meets 47 CFR 73.503.** only after you have watched the spot.
7. Optionally write what you checked in **Review notes (optional)**.
8. Click **Create spot**.

The spot appears in the **Spots** list with "FCC 73.503 attested." or "NOT attested — operator must attest before traffic."

> **Note:** CivicCast does not check the content of a spot. Your attestation is the only gate. The screen cites a federal rule written for broadcast stations. Ask your station manager or counsel how it applies to your channel.

> **Note:** CivicCast does not check that the **Asset ID** is real. A wrong id saves without a warning.

### Add a flight

1. Open the **Flights** tab.
2. In **Flight ID**, type a name such as `acme-2026q3-pubgov`.
3. In **Spot**, pick the spot from the drop-down.
4. Set **Start date** and **End date**. The window includes both days.
5. In **Channels**, type at least one channel id, separated by commas, such as `pub-1, gov-1`. The box has no "required" mark, but the server refuses a flight with no channel.
6. Leave **Frequency cap (per day, optional)** empty, or type a whole number from 1 to 1440.
7. In **Daypart (optional)**, leave **Any time** or choose a saved daypart by name, channel and hours. Use **Manage dayparts** to create or edit them in Auto Schedule. If an older flight refers to a deleted daypart, choose another or **Any time** before saving.
8. Click **Create flight**.

To delete a spot or flight, click **Delete**, then **Confirm delete**. Deleting a spot also deletes every flight and placement that used it. This cannot be undone.

### The Placements and Affidavits tabs

On **Placements**, set **From** and **Through** (UTC; **From** is included and **Through** is not), and optionally a channel and flight. On **Affidavits**, type the sponsor's name and a date range. The affidavit includes *both* the first and last day, in UTC.

An empty affidavit shows "No airings recorded for {name} between {from} and {to}." Check the exact sponsor name, UTC period, compiled placement, and whether its matching asset actually aired on the selected channel.

Use **Download CSV**, **Download XML** or **Download PDF** to save the current affidavit through your authenticated staff session.

> **Known issue (beta.11):** If your IT person switches on the setting `CIVICCAST_REQUIRE_FCC_ACK=1`, saving a spot without the tick fails with "Station policy requires fcc_compliant_ack=true". Without that setting an un-attested spot saves, despite the "NOT attested" wording.

> **For IT staff:** See [Chapter 15](#ch-integrations) for the programming interface.

## Build the resident apps (App Admin)

A resident app lets people watch your station on a phone or a TV box. CivicCast provides native viewer source apps for Roku, Apple, Android/Fire TV, Tizen and webOS, plus a web PWA. They browse live channels and replays, show station artwork, search the catalog, handle deep links and use platform playback controls. **App Admin** packages generic station-branded starter artifacts on the station computer, keeps a history with download buttons, and tracks app-store submissions; it does not build or submit the separate native source apps.

> **Warning:** App Admin does not put any app in an app store. Someone technical still has to sign and submit platform packages. The App Admin package is a generic starter; the native source apps are built with their platform toolchains. Apps read station branding from the station API when they run.

### Make a build

You must have the Setup admin role.

1. Open **Publish**, then **App Admin**.
2. Look at **Build profile**. It shows **App name**, **Tier**, **Store-ready** and an icon address. You cannot change these here. Change them on the **Channels** screen.
3. In **Platform target**, choose a platform. The names are lowercase: `web pwa`, `roku`, `tvos`, `fire tv`, `android tv`, `android mobile` and `ios ipados`.
4. In **Tier**, choose **unbranded** or **branded**.
5. Click **Queue build**.
6. In the box that asks "Queue a {platform} build ({tier} tier)?", click **Queue build**.
7. Wait. The button reads **Building…**.
8. When the build finishes, find it in **Build history** and click **Download**.

Your browser saves `{platform}-{record id}.zip`. Each row shows the time, the first 12 characters of a *SHA fingerprint* (a code that changes if the file is altered, useful for checking that a file you pass on arrived unchanged) and who made the build.

> **Known issue (beta.11):** **Tier** only labels the build record. The build uses the same steps for **unbranded** and **branded**, and does not read the station's settings. Do not expect the tier to change the file.

> **Known issue (beta.11):** The build runs inside a single web request. The page shows **Building…** with no progress bar and no time estimate, and it must stay open. In testing we could not confirm how long builds take.

> **Known issue (beta.11):** The **Store submissions** section stays on "No submissions tracked yet." The screen offers no way to add a platform, so the notebook has no rows. Only IT staff can create one through the programming interface.

> **Note:** **Store-ready: yes** is a flag someone set on the Channels screen. It does not test anything.

> **Note:** If you have the Publish operator role you see "Queueing a build requires the setup admin role." and can still view and download builds.

## Turn on paid access (Paywall)

The **Paywall** screen, in the **Setup** section, lets a station hold some recordings behind a paid subscription (through a payment company called Stripe) or give a free "comp" pass to a named email address. It is off by default. When it is off, every recording is public. The Setup admin role can open it. How residents experience a paywall is described in [Chapter 6](#ch-publishing).

A *tier* is a price level, for example "Basic monthly", linked to a price you made in Stripe. CivicCast never creates prices and never stores card numbers.

> **Beta 19 status:** The magic-link button sends through a configured mail provider and reports delivery failure. Paid access remains incomplete: the portal's tier-list and checkout routes are unavailable, and the gate covers the player rather than protecting the video file itself. The **Recently issued grants** list shows grants from the current browser session only. The screen preserves a saved signing secret when an ordinary save leaves the secret box blank; clearing it requires the explicit clear control.

> **For IT staff:** See [Chapter 15](#ch-integrations) before enabling this on a live station.

## If it did not work

| What you see | Where | What it means and what to do |
| --- | --- | --- |
| `Audience telemetry is off` | Analytics | Counting is not switched on. Ask your IT person to turn it on (see the For IT staff note above) |
| `Report unavailable.` | Analytics | You may lack the role (Support admin or Publish operator), or the station is not ready. Ask your station admin |
| `Reports require the support admin role. Ask your station admin for access.` | Reports | You need the Support admin role |
| `Pick a From date that is strictly before the Through date.` | Reports | Set **Through** to a later day than **From** |
| `No content has aired yet on this station. Reports will populate after your first scheduled meeting plays out.` | Reports | No air log rows in the range. Check the channel, widen the dates (UTC), and confirm something aired |
| `Durable storage is not ready yet.` | Reports, EPG, Underwriting, Alerts | The station's database is not ready. Tell your IT person |
| `Push failed: {error}...` | EPG Export | The guide service did not take the file. Check the address, then try again. Nothing changed on your station |
| `Could not run the export.` | EPG Export | The export could not run. Check the channel id and try again |
| `Missing Authorization header. Use Bearer <staff-token>.` | Staff programming-interface request | Sign in with the required staff role; use the screen's download controls for affidavit files |
| `Station policy requires fcc_compliant_ack=true ...` | Underwriting, saving a spot | Tick the attestation box after reviewing the spot |
| `Underwriting spot '...' already exists. Use PATCH to update.` | Underwriting | That Spot ID is taken. Use a different id, or click **Edit** on the existing spot |
| `App build tooling is not configured in this runtime...` | App Admin | This computer does not have the app build tools. Meeting recording and scheduled recording are not affected. Ask IT |
| `Could not download the artifact.` or `Build artifact file is no longer present on disk.` | App Admin | The ZIP file is missing from the station computer. Build again |

## Related

- [Chapter 2: Signing in](#ch-signing-in)
- [Chapter 3: Before the meeting](#ch-before-meeting)
- [Chapter 6: Publishing, and what residents see](#ch-publishing)
- [Chapter 8: When something looks wrong](#ch-something-wrong)
- [Chapter 15: Cable headend, streaming, federation and the API](#ch-integrations)

<!-- SOURCES: inventory/screens/analytics.md, reports.md, epg.md, underwriting.md, appadmin.md, paywall.md, _shell-navigation-and-roles.md, guide.md; civiccast/analytics/router.py:30-70; civiccast/analytics/store.py:28-60,141-150,272-334; civiccast/analytics/exports.py:120-215; civiccast/apps/portal-public/src/analytics.ts:1-90; civiccast/apps/portal-public/src/HlsPlayer.tsx:96-160; civiccast/app_platform/router.py:573-590; civiccast/reporting/router.py:262-293; civiccast/reporting/schedule_adapter.py:70-100; civiccast/egress/asrun.py:90-110; civiccast/egress/daemon.py:3165-3200; civiccast/underwriting/router.py:23-80,606-640; civiccast/underwriting/service.py:539-546; civiccast/apps/portal-operator/src/screens/UnderwritingScreen.tsx:1005-1050; civiccast/apps/portal-operator/src/api/client.ts:3285-3300; civiccast/auth/middleware.py:24,73-100; civiccast/apps/app-platform-shells/scripts/build-targets.mjs (no tier/branded reference); civiccast/paywall/store.py:190-205; civiccast/apps/portal-operator/src/screens/PaywallScreen.tsx:340-360; docs/releases/v1.0.0-beta.10-verification.md; fact-check pass: civiccast/apps/portal-operator/src/screens/AnalyticsScreen.tsx, ReportsScreen.tsx, EpgExportScreen.tsx, AppAdminScreen.tsx; civiccast/analytics/router.py, pg_store.py:370-540; civiccast/reporting/router.py, epg.py, schedule_adapter.py; civiccast/underwriting/models.py:57,132-154; civiccast/app_platform/build_router.py; civiccast/paywall/router.py:296-324,575-650; civiccast/app_platform/router.py:397-418 -->
