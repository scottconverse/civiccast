# Publishing, and what residents see {#ch-publishing}

This chapter explains how a recording goes from your library to the public: the Publish screen, what "published" really means, what each publishing step does, and the Playback policy and Paywall screens. The second half is a tour of the resident portal, the public website that residents use to watch meetings. It is written for the people who publish (normally a publish operator) and for anyone who needs to know what a resident sees.

## Before you start

- The recording must already be **packaged**, which means it has a ready-to-stream copy. If it is not, CivicCast refuses to publish it. See [After the meeting](#ch-after-meeting).
- Approving and retrying a publishing step needs the **publish_operator** role. A setup administrator or a support administrator cannot publish unless their sign-in also holds publish_operator. Anyone signed in can look at the Publish screen. Without the role, the checkboxes and buttons are greyed out and the screen says "Publish operator role required to approve or retry surfaces."
- Words used here. A *surface* is one place a recording can be sent to, such as the resident portal or the Internet Archive (a free public online library). A *manifest* is the small file that tells a web browser how to play a video. The *resident portal* is the public web page, served from the station's own computer.

> **For IT staff:** The portal is served at the web root of the station and the staff console at `/operator/`. Settings for the outside services (Internet Archive, NAS, YouTube) are in Part II (integrations chapter).

## What "published" means

A recording is **published** when CivicCast sets its public publish date. From that moment it appears in the resident portal's Recordings list and has its own Watch page. That is the whole meaning. Three things are separate from it:

1. **Packaged** only means a streamable copy exists. Residents cannot see a packaged recording until it is published.
2. **Archive copies** (Internet Archive, local NAS) and **outside services** (YouTube) are other steps on the same screen. Publishing to the portal does not require them.
3. **Captions** are made after you approve. A recording can be public before its captions are reviewed.

Taking a recording off the portal is done on the video's detail page with **Remove from portal** (see [After the meeting](#ch-after-meeting)). That removes it from public view. It does not undo anything sent to other places.

## Publish a recording to the portal

1. In the left menu, click **Publish**.
2. Find the recording's card. You can narrow the list with the filter tabs: **All**, **Needs action**, **Draft**, **Archive pending**, **Archive verified**, **Reaching fewer places than planned** and **Complete**.
3. In the card, make sure only the **Portal** row is ticked. That is the default. The checkbox is labeled **Approve this surface**.
4. Click **Approve and Publish selected**.
5. Read the dialog titled `Publish "{title}" to residents?`. It says how many surfaces will publish for real, and that "The portal surface becomes publicly visible to residents immediately and starts offline caption transcription."
6. Click **Approve and Publish**. To back out, click **Cancel**.

You should see the **Portal** row change to "Succeeded". The card's state becomes **Complete**, or **Archive pending** if the recording is a public record (a recording kept with the Meeting (long) or Permanent retention policy) and archive copies are still needed. The **Portal live** tile at the top counts the recording.

At the top of the screen, tiles count your recordings: Total, Draft, Portal live, Archive verified, Degraded and Needs action. Each card shows three facts: **Canonical** (Portal public or Portal pending), **Archive** and **Published** (the date, or "Not public yet").

### What happens behind the button

When you approve publishing, CivicCast does these things in this order:

1. It checks the recording has a streamable copy. If not, it stops with "Publish preflight blocked: this asset has no manifest_url. Run the packager or fix ingest before approving publish."
2. This edition queues recorded-caption work only when the station's recorded-caption setting is enabled, and only for a recording with a stored video file. This happens even if the Portal row is not selected. When captions are disabled, approval can proceed without captions; existing jobs pause, and uncaptioned media is not marked caption-complete. The Beta 15 package predates this setting and attempts to queue caption work on approval. See [Recorded-caption settings](#recorded-captions) for the controls and whether reviewed English attaches first or waits for all selected languages. If enabled caption work cannot be queued, publishing stops with "Publish blocked: CivicCast cannot queue this recording's caption job ... Nothing was published." and names the cause.
3. It runs the surfaces you ticked.
4. If the Portal row is selected, it sets the public publish date. If that fails, the Portal row shows "Failed", the message says "The recording remains private", and you can click **Retry this surface**.

> **Warning:** If the station's federation setting is on, every approval that leaves the recording public on the portal also posts a public notice, "New CivicCast recording published: {title}.", to the outside servers that follow the station. (Federation is a setting that shares public notices with other servers on the internet.) The Publish screen does not mention this, and a notice that has gone out cannot be taken back. Ask your IT person whether federation is on.

## Schedule a meeting on YouTube

In **Publish**, use the meeting recording's card and choose **YouTube Live**.
Select the channel whose on-air output should be sent. Enter **YouTube start** and
**YouTube end** in your local time, then approve that surface. Leave both times
blank to send now. This schedules the destination only; it does not schedule
capture or start the local channel. The selected channel must have fresh web
output when sending begins. The event title comes from the selected recording.

**Scheduled** means the event is saved and waiting. **Sending** means the sender
has started and YouTube confirmation is pending; only **Live** confirms receipt.
Use **Update YouTube schedule** before sending begins to edit the same event.
Ordinary retry and application restart retain its event identity. An expired
schedule does not start. At the end, CivicCast stops this destination; **Stop
YouTube output** also stops it early without stopping local playback. Failed
updates retain the saved event for inspection and retry. Check YouTube Studio if
remote cleanup fails. The app must be running for timed start and stop; startup
rechecks the saved window. Real account activation and transmission remain a
separate station rehearsal; simulator results are not external delivery proof.

## Put a player on another website

Open a published recording in **Assets**, or select a channel in **Channel Ops**,
and choose **Copy website embed**. Paste the HTML into your website's embed block.
If the browser cannot copy automatically, select the displayed HTML and copy it.
The player fits the available width, uses keyboard-accessible playback/caption
controls, and keeps the stream's available caption tracks. Live channels without
web output show a standing-by message and refresh automatically.

Only anonymously accessible playback can be embedded. The server refuses private
or paywalled recordings and rechecks access and publication when the player
refreshes. Withdrawal or a new restriction removes playback at the next refresh
(about ten seconds). A missing access policy fails closed. IT must expose the
station's public HTTPS address; a loopback-only address cannot play on a remote
website. The dedicated player permits external framing; staff pages remain
protected from framing.

## The publishing steps (surfaces)

Each card has nine rows. Each row shows a status dot, a name, badges such as **Required** or an approval word, a line such as "archive / Succeeded", a message and a **Next step**. All nine surfaces below are implemented; optional surfaces still need their provider settings and prerequisites.

| Row | What it does in the current source |
| --- | --- |
| Portal | Makes the recording public on the resident portal. Recorded-caption job behavior follows the source/package version and station setting described above. |
| Internet Archive | Mock by default. A real adapter requires the `CIVICCAST_PROVIDER_INTERNET_ARCHIVE=real` service setting and credentials. It uploads the recording when a local file resolves; without one, the adapter receives an asset/title payload rather than the media. |
| Local NAS rsync, Local NAS ZFS | These two rows share the mock-by-default local archive adapter. A real adapter copies to the configured mounted folder (local path or UNC) and verifies the copies; the names do not mean CivicCast runs rsync or manages ZFS. Without a local recording file the copy is metadata only. |
| YouTube Live, YouTube VOD | Simulated until real credentials are configured. Follow [YouTube setup](#configuration-youtube) for OAuth and channel activation. For Live, start and select the intended CivicCast channel; Publish creates/binds the event and sends that channel's fresh local HLS output through a separate RTMPS process. The row distinguishes scheduled, starting, destination-confirmed live, failed and stopped, and provides a viewer watch link. Use **Stop YouTube output** when finished. Real VOD upload requires a resolved recording file or `CIVICCAST_YOUTUBE_MEDIA_ROOT/<asset_id>.mp4`. |
| Podcast episode | Optional. Converts the asset's local recording to an MP3, saves it as an episode, and adds an audio enclosure to the channel RSS feed. Publish a new recording to Portal first, then select or retry Podcast. A missing recording or conversion error appears as a retryable failure. IT must set a public station URL and provide FFmpeg. |
| Subscriber notifications | Optional. When selected, CivicCast sends notices to confirmed subscribers through configured mail or webhook providers, records each delivery outcome and retries eligible failures. The row shows delivered, queued and failed counts. Mock providers are labeled simulated. |
| Cable file package | Builds a ZIP with the video, captions, details and file fingerprints for the cable headend, in a folder IT has chosen. If no folder is set the row says it is "not set up (optional)". |

Provider selectors default to mocks, which do not send or archive media. The Setup Provider form saves values for station setup but does not configure these publishing adapters; IT must set the service environment described in [Integrations](#ch-integrations). The [capability matrix](https://github.com/scottconverse/civiccast/blob/main/CAPABILITIES.md) lists the current support boundary.

A step's status reads **Not run yet**, **Running**, **Succeeded**, **Failed**, **Blocked**, **Overridden** or **Not set up yet**. The recording as a whole shows one of **Draft**, **Preflight blocked**, **Publishing**, **Archive pending**, **Archive verified**, **Reaching fewer places than planned**, **Complete** or **Needs action**.

A recording that has not been packaged still gets a card. Its Portal row reads **Blocked** with the message "The portal cannot publish this asset until an HLS manifest exists.", the card reads **Preflight blocked**, and **Approve and Publish selected** stays disabled while that row is ticked.

The words **Required** and the kinds (canonical, archive, reach, record, audience) appear unexplained on screen. In plain terms: *Required* means an archive copy is needed before a public-record meeting counts as archived. *Canonical* is the portal. *Archive* is a stored copy. *Reach* is an outside service such as YouTube. *Record* is the cable package. *Audience* is podcast and notifications.

### Skip a required archive step

If a required archive copy cannot be made, a publish operator can skip it with an audit-logged reason.

1. In the archive row, tick **Use audit-logged archive override for this platform**.
2. Type the reason in the box. The screen needs at least 20 characters.
3. Approve as usual. The row becomes **Overridden** and counts as done.

### Retry a failed step

When a row shows **Failed**, read its message and **Next step**, fix the cause, then click **Retry this surface** on that row. A Portal retry queues caption work when recorded captions are enabled and the recording has a stored video file. Retrying another surface does not start caption work.

> **Known issue (beta.11):** If you approve a second time, CivicCast rebuilds the whole run. Any step you did not tick again goes back to "Not run yet", including the Portal row, and the card can read **Draft** and "Portal pending" even though the recording is still public. The screen's own text says archive and outside steps are opt-in "this time", which invites exactly this. Workaround: tick every step you want, all in one approval. To redo a single step, use **Retry this surface** on a Failed row instead of approving again: a retry changes only that row. If the Portal row has gone back to "Not run yet", its checkbox appears again; make sure it is ticked along with your other steps and approve again.

> **Archive status:** The "Archive verified" card text can appear when copies were simulated or overridden. Read each row's note: "Simulated" does not establish a legal archive copy. YouTube mock results are explicitly simulated and unverified. A saved setup or a starting sender is not destination-confirmed live delivery.

> **Note:** If publishing stops, the message says some earlier steps may already be published. Check each surface status before retrying, and retry only the failed surfaces.

> **Known issue (beta.11):** The Publish screen always records the person as "Operator dashboard", not your name, in its audit trail. Do not promise per-person publishing records.

> **Warning:** Uploads to the Internet Archive and YouTube, once real, cannot be taken back from CivicCast.

### If it did not work

| What you see | Cause and fix |
| --- | --- |
| "No assets are ready for publish review." | The library has no recordings at all. Upload or record a meeting, and package it. (A recording that is not packaged still shows a card; see above.) |
| "Publish dashboard needs a database." or "Durable storage is not ready." | The station's storage is not ready. Tell a setup administrator; they use Setup and **Prepare storage**. |
| "Publish preflight blocked: this asset has no manifest_url. ..." | The recording has no streamable copy. Use **Package for playback** on the Assets screen. |
| "Publish stopped. {detail} ..." | Read the detail text. It names the problem. |

## Playback policy: what is and is not enforced

The **Playback policy** screen lets you record who is supposed to be allowed to watch a channel or a single recording, plus a lock for public records, and up to four "prerolls" (a short card or clip meant to play before a recording). It is for the publish_operator and support_admin roles. Anyone can look at it. Other roles can type changes but get a refusal when they click **Save policy**.

What you set:

1. Choose **Channel** or **Asset** under **Policy target**, and type the **Target ID**, the channel's or recording's ID. The default is `government`. CivicCast accepts any ID made of lowercase letters, digits, `_` and `-`, even one that does not exist, so a typing mistake quietly creates a policy for nothing. Copy the ID from the Channels or Assets screen. A policy on a recording wins over the policy on its channel.
2. Choose **Public**, **Authenticated** or **Invite only** under **Access tier**. **Invite group** and **OIDC provider** (a single-sign-on service) are free-text boxes. Invite only needs an invite group name before **Save policy** works.
3. Tick **Public-record asset** or **Public archive complete** if the record must stay public. Ticking either one switches the tier to Public, and the server rejects any public-record policy that is gated.
4. Optionally tick **Preroll enabled**, click **Add preroll**, and fill in at least the creative ID, the file address, an accessible label and a duration.
5. Click **Save policy**. There is no confirmation message. The only sign is the "Updated {date}" box changing.

The **Decision audit** at the bottom lists the last 8 allow or block decisions with **Refresh**.

> **Known issue (beta.11):** **Access tier** does not stop anyone from watching a video. The setting is read in only three places: the podcast feed, the app catalog, and a public "evaluate" service. The resident portal's video player and the video file server never read it. Choosing **Invite only** or **Authenticated** does not put a gate in front of the Watch page. Residents do not sign in on the portal for playback. Do not rely on this screen to restrict a recording. Use **Remove from portal** to withdraw a recording instead.

Exactly what is enforced today:

| Setting | Enforced? |
| --- | --- |
| Access tier on a **channel's podcast feed** | Yes. If the tier is not Public, the feed answers "Authenticated RSS is not enabled for this channel." unless **Authenticated RSS** is ticked, and then needs a signed viewer token, a coded pass for one listener ("A signed viewer token is required for this podcast feed."). The console has no button to make a viewer token; the server can issue one only through an API call. |
| Public-record lock | Yes, when saving. A public-record policy cannot be gated. |
| Access tier for the portal's video | No. |
| Prerolls | Saved, but we found nothing in the resident portal that plays them. We could not confirm that any app plays them. |
| Access tier in app listings | The tier is copied into the app catalog as information. We could not confirm that any app enforces it. |
| Decision audit | Fills only when something asks the policy service for a decision, which today is the podcast feed. It usually stays empty ("No playback decisions yet."). |

## The paywall: optional paid access

Some stations may want some recordings behind a paid subscription. The **Paywall** screen is where a setup administrator configures it. It is off by default, and when off everything is public. It sits in the Setup part of the menu and is visible only to setup administrators. Everyone else sees "Forbidden — the subscription paywall is a setup-admin surface. Ask your station admin for access."

The screen has three cards: **Config** (the **Enable paywall** box, a provider, a signing secret), **Tiers** (plans that map to prices you create yourself in Stripe, a payment company) and **Comp access grants** (free passes you give to an email address).

> **Beta 19 status:** Treat the Paywall as a test-only screen, not protected paid access. In the current code:
>
> - Turning it on does not select particular recordings. The Watch page asks every resident for a pass on every recording.
> - The video file itself is not protected. The gate is a screen shown in front of the player.
> - The "Email me a sign-in link" button sends through the configured mail provider and reports a delivery failure when that provider is unavailable.
> - The pages the portal uses to list plans and start payment do not exist on the server, so a resident cannot subscribe.
> - An ordinary save with the **Signing secret** box left blank preserves the stored secret. To remove it, use the explicit clear control. The secret is hidden when the page reloads.
> - The list of comp grants shows only grants made in the current browser session, and does not provide a history of grants from earlier sessions.

If you turn the paywall on for testing, the Watch page shows the gate described in the resident tour below. Residents will be blocked.

## Resident tour: the public portal

The portal needs no sign-in. It is English only and always shows a dark theme. At the top are the title "CivicCast public portal", three buttons **Home**, **Recordings** and **Schedule**, and a link **Report a beta issue**. The page address changes as you move, so any page can be copied and shared. On a phone the same page stacks into one column.

![The portal Home page on a computer while a channel is on air.](manual/images/portal-home.png){width=90%}

*Figure: the portal Home page.*

![The portal Home page on a phone-sized screen.](manual/images/portal-home-phone.png){width=40%}

*Figure: Home on a phone.*

> **Known issue (beta.11):** **Report a beta issue** opens the staff console's help page in a new tab, not a page written for residents. The text under it, "Do not include passwords, recovery codes, staff tokens, or private meeting material in reports.", is written for staff. The portal has no help page, no privacy statement, and no contact details for the station.

### Home and Live now

Home has these parts, top to bottom:

- **Live now.** A sentence and a video, with a **Broadcast status** card beside it (State, Channel, Started). The portal checks again every 4 seconds, so a page left open follows the channel.

| What the station is doing | What residents see |
| --- | --- |
| On the air with web video | "{title} is on air." and the player. State reads "On air". |
| On the air, web video starting | "On air. The web preview is turned on but not serving yet." The page checks again by itself. |
| On the air, no web video | "On air, but web preview is not enabled for this channel." |
| Standing by | "The station is standing by. No program is on air right now." |
| Anything else | "No live broadcast is on air." State reads "Offline". |

- **Coming up.** Cards for upcoming premieres, each with the title, the date and time, and the channel. Empty: "No premieres are scheduled."
- **Latest recordings.** Up to 6 of the newest published recordings, each with **Watch recording**, and **Browse all recordings** to go to the Recordings page. Empty: "No published recordings are available."
- **Follow new recordings** and **Submit a program**, covered below.

If nothing at all is posted the page says "Nothing is posted yet. Check back after the station schedules a premiere or publishes a recording." If a part cannot load, an amber list "Some portal sections need attention" says which. If all three fail the page says "The public portal could not load right now. Refresh the page, then contact the station if the problem continues."

The Home page reads the real emergency notice for its current channel automatically and checks again every five seconds. No special page link is needed. Its notice reflects alerts presented by the station. See [Understand emergency alerts](#understand-emergency-alerts).

### Recordings: search and filters

1. Click **Recordings**.
2. Type words in **Search recordings** and click **Search** (pressing Enter works too). Search matches titles, descriptions and spoken words in the current published, reviewed transcript. It matches any part of a word, and capital letters do not matter. Matching spoken passages show a timestamp and excerpt; choose one to open the recording at that moment. Recordings without a published transcript still appear in browsing and title/description search.
3. To narrow results, use **Year** (the years of published recordings), **Meeting body** (the meeting body on the recording, such as "City Council") and any extra menus your station has made.
4. Use **Previous**, the page numbers and **Next** to move through the results. There are 12 recordings per page.

Each card shows the title, the description, "Published {date}" and **Watch recording**. A card with no description says "Recording description not posted." The line above the cards reads, for example, "2 recordings published / page 1 of 1" or "N recordings match this filter".

Meeting body and extra fields are set by staff on the video's detail page. A recording with no meeting body never matches a Meeting body filter. If the station has published nothing yet it says "No recordings have been published yet." and links to the channel schedule. If the list cannot load it says "Published recordings could not be loaded. Try again or contact the station." with **Retry**. When a search or filter matches nothing the page says "No recordings match this filter. Clear the search or facets to browse everything." There is no "Clear" button; the resident resets each control. When the search service is not ready, the page falls back to a plain list and shows "Showing recordings in a reduced-search mode — full search is temporarily unavailable, so some filters below may show fewer results than normal. Try again shortly for full search."

![The Recordings page with the search box, the Year and Meeting body menus, and recording cards.](manual/images/portal-recordings.png){width=90%}

*Figure: the Recordings page.*

The Watch page includes a keyboard-accessible **Transcript** with timestamp links. Choose a line to seek to that passage. These times follow the published video's trimmed playback timeline. **Refresh transcript** reads the latest published corrections. A missing transcript is labeled honestly; it does not mean the recording is unavailable.

For a recording that requires access, transcript text and search excerpts require a verified magic-link session and a current grant. An email typed into the browser is not sufficient proof. Reopen a valid magic link if the transcript asks you to sign in again. Withdrawal removes the recording and transcript from these public endpoints. This text access check does not change the pre-existing media or paywall authorization behavior.

### Schedule

1. Click **Schedule**.
2. Click a channel's name at the top to switch channels.

The page reads "What airs over the next three days. Times are shown in your local timezone." It lists each day with time, title and length. The program on the air now has a green bar and the tag **On now**. Entries are not links. If nothing is listed the page says "Nothing is on the schedule for this channel yet. Check back soon." The Schedule page comes from the station's program log. **Coming up** on Home is a different list: committed premieres only.

![The Schedule page with channel buttons. The channel shown has no entries yet.](manual/images/portal-schedule.png){width=90%}

*Figure: the Schedule page.*

### Watch page: video, captions and agenda

A resident opens a recording by clicking **Watch recording**. The page shows **Back to all recordings**, the title, "Published {date} / {length}", the video player, the description, and **Copy share link**. Click **Copy share link**; the page says "Link copied." and puts the page address on the clipboard. The video is a normal browser player with play, volume and full screen controls.

![A recording's Watch page with the video player and the Copy share link button.](manual/images/portal-watch.png){width=90%}

*Figure: a Watch page.*

**Captions.** If the video has caption tracks, a **Captions** bar appears under the player with buttons **Off** and one per track, such as English or Spanish. A track starts on only if the video marks it as the default. When a recording has no caption tracks there is no bar and no message. The portal cannot make captions; it only shows what staff approved (see [After the meeting](#ch-after-meeting)).

**Agenda chapters.** If staff published an agenda for the meeting, an **Agenda** card sits beside the video (below it on a phone). It lists the items by number, title and time. Click an item with a time and the video jumps there and plays. An item with no time shows a dash and cannot be clicked. If the agenda has a document, an **Agenda document** link opens it in a new tab, and a PDF may show in the card. If there is no agenda, nothing is shown. The agenda is the only chapter feature: the player has no chapter marks on its timeline.

**Errors.** A recording that is not public shows "Recording not found" and "This recording does not exist or is no longer published. Browse the archive for the current recordings." Other failures show "This recording could not be loaded right now. Try again, then contact the station if the problem continues." with **Retry**. If the video cannot play, the player says one of: "Network error while loading the video. Check your connection and try again.", "The video could not be played. The stream may be unavailable." or, for an old browser, "Your browser does not support HLS playback. Please try a recent version of Chrome, Firefox, Safari, or Edge." (HLS is the streaming format the portal uses.) The player has no retry button; reload the page.

> **Note:** The share link uses the address the resident used to reach the portal. If your station is reached by an inside address, a copied link will not work for people outside.

> **Note:** A Beta 15 field run could not confirm that **Copy share link** changed the clipboard: the clipboard still held an earlier Library URL, and the action itself was not verified. This remains an unconfirmed Beta 15 observation; it does not establish a defect in the current package. See the [Beta 17 deferred follow-up](https://github.com/scottconverse/civiccast/blob/main/docs/ops/beta17-deferred.md).

### What a resident sees when the paywall is on

Only if a setup administrator has turned the paywall on. The video is replaced by a gate. While it checks, a small label says "Checking access…". The gate shows "Subscription required", a reason line, a **Sign in by email** form with **Email me a sign-in link**, a "New here?" block with a plan list and **Subscribe**, and "Already signed in as {email}? Switch email".

In Beta 19, a resident can enter an email and request a sign-in link. A configured mail provider delivers it; the page reports an error if delivery fails. The plan list reads "Tier selection isn't configured yet on this station. Contact them for subscription details." With an email typed in, clicking **Subscribe** shows "This station hasn't finished setting up subscriptions yet. Please contact them." (With no email typed it says "Enter your email above before subscribing.") The agenda beside the video stays visible. See the Paywall status above.

### Subscribing to new recordings

At the bottom of Home is **Follow new recordings**. It offers an **Email address** box with **Subscribe**, a **Channel RSS feed** link, and a **Podcast RSS feed** link. RSS feeds are web addresses that a feed reader or podcast app checks for new items.

If a resident types an address and clicks **Subscribe**, a result box says "Subscription is waiting for confirmation." and "Open the confirmation link sent to this address." The small print says email uses a confirm step and a one-click unsubscribe link and carries no tracking pixels.

> **Known issue (beta.11):** Subscribing does not work end to end.
>
> - The signup is always for a channel named `government`, and both feed links point at that name. A station with no channel by that name gets feeds for nothing.
> - Unless IT has switched on a real mail connection, the confirmation email goes to a placeholder mailbox that never leaves the station computer.
> - Even with a real mail connection, the confirmation link written into the email is only a path (`/subscribe/confirm?token=...`) with no web address in front of it. The portal confirms a subscription from `/?subscription=confirm&token=...` and we found nothing that serves the path in the email, so we could not confirm that a resident can finish confirming.
> - In beta.11, a confirmed subscriber did not receive a publish notice. Current source offers the optional **Subscriber notifications** row with recorded delivery outcomes.
> - In beta.11, the **Channel RSS feed** was valid but empty and the Podcast step on Publish was unavailable. Current source can add approved podcast episodes and playable enclosures to the feed.
>
> Do not promise residents email alerts or working feeds in beta.11. The result box may also show a link labeled "Test one-click unsubscribe", which is a testing control.

> **For IT staff:** Mail delivery is chosen with the provider setting `CIVICCAST_PROVIDER_MAIL` (default is the local placeholder). See Part II (integrations chapter).

The **Submit a program** form lower on Home lets a producer send a program to the station. It is a staff-facing intake feature and is not covered in this chapter.

## Related

- [After the meeting: assets, captions, summaries, approvals](#ch-after-meeting)
- [When something looks wrong](#ch-something-wrong)

<!-- SOURCES: inventory/screens/publish.md; playback.md; paywall.md; public-shell.md; public-home.md; public-recordings.md; public-schedule.md; public-watch.md; public-player-captions.md; public-agenda.md; public-paywall.md; public-subscribe.md; civiccast/publish/router.py:85-129,160-185,420-540; civiccast/publish/service.py:203-300 (build_initial_surfaces, default_approved_surface_ids), 530-600 (approve_publish rebuilds from build_initial_surfaces; unticked surfaces stay pending), 915-990 (_dashboard_state); civiccast/apps/portal-operator/src/screens/PublishDashboardScreen.tsx:200-225 (Simulated note); civiccast/app_platform/router.py:880-925 and civiccast/podcast/router.py:44-95 (only consumers of playback policy); grep of civiccast/apps/portal-public/src for playback-policy/preroll finds none; civiccast/paywall/router.py:225-258 (no-op email sender; no override in civiccast/), 483-565; paywall routes list (no /paywall/tiers or /paywall/checkout); civiccast/paywall/service.py:230-262 (enabled gates every asset); civiccast/apps/portal-public/src/PaywallGate.tsx:270-345; civiccast/subscribe/service.py:45-135, civiccast/subscribe/router.py:185-210 (rss_feed returns empty feed), civiccast/subscribe/delivery.py (LocalMailbox), civiccast/platform/providers.py:100-120 (mail default local); civiccast/schedule/router.py:140-215 (public list is published_at set; coming-up = published premieres); docs/manual/images/portal-*.png -->


### Large-text live caption display

Open **Live captions** in the resident portal and choose the public channel. Share the page’s phone/browser link (for example `/#/captions?channel=public`). Adjust **Text size** from 24 to 72 pixels; channel selection and links work by keyboard.

This is the channel’s automatic **English** live sidecar, not an approved official transcript. Live translation is unavailable. The first snapshot says **Latest available captions; waiting for new input**; only a later changed feed says it is receiving new captions. An empty/missing feed says captions are unavailable. After 30 seconds without a changed sidecar, the display clears its text and reports no new input. Silence can also produce that message. It retries automatically; changing channels immediately clears the previous channel’s text. Recorded translations remain selectable in the recording player after staff review.

### RC1 translation review limitation

Qualified human language review for Spanish, Simplified Chinese, Filipino/Tagalog, Vietnamese and Arabic is pending. RC1 software workflow checks cover editing, review decisions, publication and player track selection; they do not establish translation accuracy. Model-assisted checks are not qualified human review. Have a competent speaker review material translated records before relying on them.
