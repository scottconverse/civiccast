# When something looks wrong {#ch-something-wrong}

This chapter helps you when a screen shows a red or yellow warning, an alert appears, or you are not sure the station is working. It covers the **Readiness** screen, **Alerts**, **Emergency Alerts** and **Federation**, then gives you a list of what to do and what not to do, a way to describe a problem to your IT person, and where to report a beta problem. Every operator role can read the runtime safe-to-air health indicator. The Alerts event list, its rule and destination editors, and Emergency Alerts have narrower permissions, described below. Fixing most problems is the IT person's job.

## Before you start

### Graphics-driver maintenance

Schedule GPU/graphics-driver installation and device restarts for an off-air
maintenance window. They can interrupt every channel using that adapter at once.
Keep a station backup and the prior driver/recovery information before maintenance;
use the computer or adapter manufacturer's supported driver. Do not update or
reset the graphics device as a troubleshooting experiment during a broadcast.

If channels fail together during a driver change, preserve the CivicCast worker
logs and Windows event timestamps. The R9 investigation associated simultaneous
worker faults with a device restart, but it did not establish an instruction-level
driver defect. After planned maintenance, check all three actual outputs and
captions. CPU software fallback is available when configured; it uses CPU capacity
and is not a guarantee of uninterrupted playback through a device reset.

The four screens live in the left sidebar under **System Health**: **Readiness**, **Alerts**, **Emergency Alerts** and **Federation**. Most of them open for any role. Individual buttons are limited by role, and a button you cannot use shows a grey note that names the role needed.

> **Note:** The first administrator account carries all five roles. Staff members use their own accounts with assigned roles, so they may see fewer controls. A Setup admin manages accounts and roles in **Staff accounts**.

The sidebar's **Readiness** page is headed **Station health and readiness**. It separates live output from setup and recovery checks.

## Check whether the station is ready (Readiness)

Read live health and setup readiness separately. A backup, restore or setup warning needs attention, but does not by itself mean a running channel has stopped. Use the live banner and resident preview to assess the current broadcast; use the setup and recovery checks to prepare for operation and recovery.

1. In the sidebar, open **System Health**, then click **Readiness**.
2. Wait while the page says "Checking station readiness...".
3. Read the banner at the top, **On air right now**. It shows each channel, a coloured label and a message. The banner refreshes by itself every 5 seconds.
4. Read **Setup and recovery readiness** below the live banner. It refreshes every 30 seconds and shows **Last checked**. This is the setup verdict, not a second measurement of live output.
5. Scroll down to **Required before broadcast** and **Optional and advanced**. Each row is one check, with a coloured label, a short message and a "Next step".

The outgoing-feed panel refreshes every five seconds. Readings older than two minutes, with invalid times, or from a failed refresh cannot confirm current health. They are marked **Stale**, **Unavailable** or **Not confirmed**, with the sampling time. Historical measurements may remain visible to help diagnosis; they are not a healthy-current-output claim.

### What the colours and words mean

| Colour | Words on screen | What it means |
| --- | --- | --- |
| Green | **Ready** | The required checks passed, and no optional check needs a look |
| Yellow | **Check before meeting** | Something needs attention or proof before the meeting |
| Red | **Do not broadcast yet** | A required check failed for tonight's broadcast |

A single check row can also say **Not set up yet** (an optional feature has no setup or proof) or **Needs IT help** (the next step needs administrator work on the computer).

A channel tile uses these words:

- **On air** or **Ready** (green).
- **Working** (yellow).
- **Needs attention** (red).

After the word you may see "on safety slate" (the channel is showing its fallback card) or "live captions off".

When live captions are enabled, the channel tile also reports the caption worker state, its last heartbeat, and which recognition provider is selected. **Digital silence in latest processed audio** means the latest processed audio chunk contained zero-valued samples; it does not claim that speech was recognized. **Worker stalled** means the expected worker has stopped reporting fresh progress. A missing or stale worker heartbeat remains unknown during the first 180 seconds after a channel goes on air; after that, a heartbeat older than 90 seconds is stalled. If audio remains pending for more than 120 seconds without a chunk completing, the worker is stalled; starting another batch retry does not reset this clock. A recent heartbeat with no pending audio is not a failure. Channels that are stopped or have live captions switched off are not caption failures.

The public `/health` probe continues to return HTTP 200 while the process answers. Its `status` is **degraded** when the schema is not current or live-caption readiness is failed, stalled, or not yet known. Its `live_captions` value is only a station-wide state; it never includes channel names. Signed-in staff see the per-channel worker details in **On air right now**.

Caption capacity distinguishes **Within capacity**, **Temporarily behind** and **Sustained caption backlog**. Temporary delay means captions can continue catching up within the runtime's recovery allowance. Sustained overload means the queue depth or age has remained beyond that allowance. Read these alongside the queue measurements and current provider rather than treating every late caption as a failure.

> **Known issue (beta.11):** A yellow station can show two different verdicts at once. The small pill at the top says **Check before meeting**, while the card below can say **Ready with optional items**. Both mean the required checks passed and an optional item needs a look. The second wording is not one of the five standard phrases.

> **Known issue (beta.11):** The page does not refresh by itself, except for the **On air right now** banner. There is no **Refresh** button. The checklist updates only after you use one of its own buttons or re-open the page after about 30 seconds.

### What each check means

These eight checks are required. If any one is red, the station says **Do not broadcast yet**.

| Check | In plain words | What the "Next step" says |
| --- | --- | --- |
| **First admin** | The station has an administrator account | Open Setup and finish first-admin setup |
| **Recovery kit** | The one-time sheet that lets you get back in if the password is lost was made | Confirm someone printed or saved it |
| **Durable records storage** | The station's database is working and keeps its records | Open Setup and choose **Prepare storage** |
| **Backup** | A backup is set up | The message tells you what is missing |
| **Camera or meeting source** | A camera, encoder, Zoom or NDI source is added and passed its pre-flight check | Open Run Meeting and add or check the source |
| **Local recording** | A place to save recordings is chosen and passed a write, read and delete test | Choose where recordings are saved |
| **Resident portal** | The resident website address is set and the preview is confirmed | Open the preview and confirm residents can see the page |
| **Station policy** | The default station rules are active | Finish Setup |

The optional checks are:

| Check | In plain words |
| --- | --- |
| **Contributor upload storage** | How full the folder for resident uploads is. Yellow at 80 percent, red at 100 percent, when new uploads are refused |
| **Caption inference device** | Whether captions run on the graphics card or the main processor. It is always shown as informational, and never blocks |
| **Cable headend verification** | Whether the cable feed was tested. If no channel delivers to a cable headend, the row still appears and says so |
| **24/7 channel automation** | Whether channels set to run around the clock are running. If none are set up, the row still appears and says so |
| **YouTube**, **Subscriber notices**, **Federation**, **Internet Archive**, **Archive storage** | Optional publishing and archive features and whether each is set up |
| **Internal service certificates** | An advanced security item, marked "advanced". It is for IT staff |

### Run the broadcast readiness check

The **Check broadcast readiness** button runs a *private rehearsal*, a short test of the whole broadcast path with no public audience. You need the Meeting operator role.

1. Click **Check broadcast readiness** on the top card.
2. Wait for the **Broadcast readiness check result** card to appear.
3. Read the **Rehearsal result** line and the **Broadcast gate** line.

The rehearsal result is one of:

- **Passed**: a private session ran, passed its pre-flight check and finished a recording.
- **Failed**: the private session started but stopped before a recording was finished.
- **Not run**: something stopped the private session from starting.

The broadcast gate reads "All required items ready", or "N required item(s) need attention", or "N required item(s) not ready".

> **Warning:** The rehearsal makes a real private test session called "Private first-broadcast rehearsal" on the channel named `government`, copies a sample video, and saves a test recording. The button gives no warning and no "running" message. Run it before a meeting, not while a meeting is on the air. In testing we could not confirm whether the test recording is visible on the Assets screen.

> **Note:** Items listed under the gate link to matching rows lower on the page. If selecting one does not move the page to its row, scroll down and look for the same label.

> **Known issue (beta.11):** Some buttons on this page are grey with no reason shown, for example **Open maintenance window** and **Run failed-update rehearsal**. They need earlier steps to pass first. Only the role limits are explained on screen.

### The other panels on this page

Below the checklist, the page also holds tools for IT staff or trained staff. Do not press these unless you were asked to.

| Panel | What it is for |
| --- | --- |
| **Outgoing channel feed** | One card per channel with **Start**, **Stop**, **Restart feed** and **Finish current item, then stop**. The Meeting operator role is needed |
| **GStreamer engine repair** | Re-checks the video engine (called GStreamer) and, if it is broken, starts a signed repair of it. The page says this is never a reinstall. Setup admin or Support admin |
| **Backup and restore readiness** | **Check backup storage** and **Run real database restore drill** |
| **Update and rollback** | Checks and practice runs for updating CivicCast and going back if an update fails. It does not install an update itself |
| **Support bundle** | Makes a redacted troubleshooting file for support (Support admin) |
| **Latest self-check** | The result of the station's automatic test. It runs daily and weekly; the first daily check runs overnight |
| **Machine health** | Processor use, memory, free disk space, whether the database is reachable and whether the service is running |

The feed buttons ask you to confirm before they act. They only *queue* a command for the channel's feed. Nothing tells you to wait.

> **Known issue (beta.11):** After **Restart feed**, the state label may stay on the old value until you re-open the page. The page does not poll these cards. Wait a minute, then reload Readiness.

> **Warning:** **Stop** takes a channel off the air. **Finish current item, then stop** does the same after the current program ends. Only press these on purpose, and never on a channel you do not intend to stop.

## Watch for alerts (Alerts)

An *alert* is a warning the station raises by itself, such as "Disk space low" or "Channel off air". The **Alerts** screen lists them.

1. In the sidebar, open **System Health**, then click **Alerts**. You can also click **Review alerts** or **Open alerts** on the Readiness page.
2. Click **Active** to see current problems, or **Resolved** for past ones. The Active list refreshes every 10 seconds.
3. Read each row: the title (what went wrong), a severity (CRITICAL, WARNING or INFO), a summary, what it is about, and when it was first and last seen. "seen 3×" means it happened again.
4. Click **Acknowledge** on a row to say you have seen it. The row then shows "Acknowledged by" your name and the date.

An empty list says: "No active alerts. Everything the watch box monitors is healthy." The screen calls the station's own monitor "the watch box".

> **Note:** **Acknowledge** means "I have seen this". It does not fix the problem and does not close the alert. An alert closes itself when the problem goes away, and then moves to **Resolved**.

The alert titles you may see are:

- Channel off air
- Encoder stopped
- Server crashed
- Data format out of date
- Relay blocked
- Cable compliance check failed
- Missing media file
- Save to database failed
- Live takeover stuck over 2 hours
- AI engine is down
- Disk space low
- Computer clock out of sync
- Database unreachable
- CivicCast service is down
- Automatic self-check did not pass
- Live caption worker failed or stalled

Some alerts show a plain code with the dashes turned into spaces, for example "eas source unavailable", "asrun outbox degraded" or "caption tier degraded". "eas source unavailable" means an emergency-alert feed could not be reached; its rule card is titled **EAS source unavailable**. "asrun outbox degraded" concerns the on-air log that Reports reads.

### Rules and destinations

Under the list are two sections for administrators.

- **Alert rules** has cards for alert kinds with a rule, including **Live caption worker failed or stalled** and **EAS source unavailable**. On a card you can switch **Enabled** on or off, set **Severity** to Critical, Warning or Info, set **Re-alert after (minutes)** (how long CivicCast waits before warning you again while the problem continues), tick **Notify on resolve**, and choose destinations. Tick each email, text-message or webhook destination that should receive this alert, then click **Save**.
- **Where alerts go** lists *destinations*: an email address, a text-message number or a webhook (a web address that receives a message). Click **Add destination**, choose a **Type**, give it a **Name**, type the address in **Where to send (email, phone, or webhook URL)**, and click **Create destination**.

> **Beta.12 routing update:** A rule card's **Destinations** list shows the saved assignments. If an assigned destination no longer exists, it stays checked as **Unavailable destination** with its id; leave it checked to preserve the saved id, or uncheck it to remove it. Saving another rule setting without changing the destination checkboxes keeps its assignments.

The destination form has **Quiet hours start (UTC, HH:MM)** and **Quiet hours end (UTC, HH:MM)**. These are in UTC, not local time. A quiet-hours window holds back WARNING and INFO alerts. CRITICAL alerts ignore quiet hours. Sending still requires an enabled, assigned destination and a working delivery service.

> **Warning:** Do not click **Delete** on a destination unless you mean it. The first click turns the button into **Confirm delete?**, and a second click deletes right away. There is no cancel and no timeout.

### What happens in beta.12 when no destination is wired

> **Known issue (beta.12):** A rule with no enabled destination assigned cannot deliver its alert. Set up routing on the rule card before relying on email, text or webhook notifications.
>
> - A new install ships every alert rule with *no destinations attached*.
> - Adding a destination on this screen does not attach it automatically. In each rule card's **Destinations** list, tick one or more destinations and click **Save** to attach them.
> - When an alert fires and its rule has no live destination, CivicCast writes a "suppressed" delivery note that reads "No enabled alert channel is configured for condition ...". No screen shows that note.
> - The alert itself does still appear on the **Alerts** screen and in the red and yellow counts on **Readiness**.
> - There is no **Send test alert** button, so you cannot check a destination by pressing a button.
> - Some kinds of alert still have no rule, including "Automatic self-check did not pass", "asrun outbox degraded" and "caption tier degraded". They show on the **Alerts** screen as warnings but cannot be routed from **Alert rules**. **Live caption worker failed or stalled** and **EAS source unavailable** have warning rules; destinations still need to be attached by an administrator before those alerts can be delivered.
>
> Until a Setup admin assigns at least one enabled destination to each rule you rely on, those rules cannot notify anyone. Check **Readiness** and **Alerts** before every meeting and at the start of every shift.

> **For IT staff:** A rule takes `channel_ids` (the ids of destinations) through `PUT /api/staff/alert-rules/{rule_id}`, with the Setup admin role. The 14 rules that exist come with ids like `default:off-air`. See [Chapter 14](#ch-troubleshooting) and [Chapter 15](#ch-integrations).

> **Role permissions:** The Alerts event list and acknowledgement actions require Setup admin, Support admin or Meeting operator. Setup admins and Support admins can view alert rules and destinations, but only Setup admins can change them. Publish operators and Records clerks can read the runtime safe-to-air health indicator, but cannot load the Alerts event list or its rule and destination editors.

## Understand emergency alerts (Emergency Alerts) {#understand-emergency-alerts}

The **Emergency Alerts** screen shows public-safety alerts the station has pulled in from outside feeds, and lets an operator put one on a channel. You can open it with the Setup admin, Support admin or Meeting operator role.

> **Warning:** CivicCast is **not** an Emergency Alert System (EAS) device. A banner on the page says so permanently: "Public-safety display — not an EAS device". It does not send the national EAS signal and never takes over programming by itself. The required national alert relay stays with your cable operator's certified equipment at the headend.

The feeds come from the National Weather Service (NWS), the federal alert system IPAWS, and AMBER child-abduction alerts. IPAWS and AMBER alerts are read in a standard format called CAP (Common Alerting Protocol); the code reads National Weather Service alerts from that service's GeoJSON feed instead. We did not test any live feed.

### What airs by itself, and what staff must do

| Situation | What happens |
| --- | --- |
| A Beta.12 station with default settings | Nothing is polled and nothing airs by itself. The page says "No alert sources are configured. Ask your station administrator to configure an NWS, AMBER, or IPAWS (COG) feed." |
| IT staff turn on alert polling | CivicCast reads the configured feeds, by default once a minute, and lists active alerts under **Active alerts** |
| IT staff also turn on automatic display | Every active **severe** alert goes on every channel that is on air as a **crawl**. Every active **extreme** alert goes on as an **overlay**. A full-screen takeover is never automatic |
| An operator presses a button | The station queues a crawl, an overlay or (after a tick box) a full-screen takeover for the selected channel's next output update |

A *crawl* is a line of text that scrolls across the screen. An *overlay* is a message box placed over the picture. A *forced slate* is a full-screen message that replaces the programming.

> **Note:** There is no form to add an alert feed in the console. A station administrator must configure an NWS, AMBER, or IPAWS (COG) feed through the programming interface. Polling and automatic display remain off unless IT staff turn on the two station settings.

> **Known issue (beta.11):** Nothing on the page says that severe and extreme alerts go on air by themselves once IT turns on automatic display. The banner says only that CivicCast "never automatically pre-empts programming". Once automatic display is on, use **Clear** to take an alert down.

CivicCast draws the selected alert into the channel's GStreamer output: a scrolling crawl, a lower-half message panel, or an operator-confirmed full-screen slate. The resident home page also reads the real alert for its current channel automatically, without a special page link, and refreshes it every five seconds. Clear, CAP cancellation, replacement and expiration update the presentation on the next successful delivery; clearing also prevents the same alert from being automatically shown again. If several alerts are active, the highest severity is shown. Other channel graphics remain in place when an alert changes or clears. Very long broadcast text is shortened; the resident notice carries the full public alert text. No cellular fallback is configured by this feature.

When an alert changes, CivicCast prepares the replacement before removing the previous warning. A failed render leaves that warning in place while a later update retries. Delivery failures can delay replacement or clearing. If Windows prevents deletion of a retired image after clearing succeeds, the warning can be absent until cleanup retries succeed. Watch the actual output when making changes. Temporary alert images are bounded during retries; this is not a cleanup of files left by older runs or a crashed process.

> **For IT staff:** The two settings are `CIVICCAST_EAS` and `CIVICCAST_EAS_AUTO_SURFACE`. `CIVICCAST_EAS` defaults to `off`; any other value (the code's own comment uses `inline`) starts the polling. `CIVICCAST_EAS_AUTO_SURFACE` is off unless set to `1`, `true`, `yes` or `on`, and it is read only when `CIVICCAST_EAS` is on. `CIVICCAST_EAS_POLL_SECONDS` sets the polling interval and defaults to 60. Polling skips any source that is disabled, is of the `manual` type or has no endpoint address. Sources are added with `PUT /api/staff/eas/sources/{id}`. See [Chapter 15](#ch-integrations).

> **Output setup:** Keep emergency presentation off unless the station intends to use it. IT must enable `CIVICCAST_EAS=inline` with the GStreamer egress engine before starting the channel, so its graphics compositor is reserved. A display request is refused when emergency presentation or GStreamer is off. The installed runtime supports a CPU `compositor` fallback when D3D11 is unavailable; neither path requires a discrete GPU. Isolated MPEG-TS checks proved alert pixels and clearing on both CPU and D3D11 paths. This does not certify a cable headend or any live feed: verify the station's actual downstream output before relying on it.

### Check the feeds and active alerts

1. In the sidebar, open **System Health**, then click **Emergency Alerts**.
2. Under **Alert sources**, read each line. It shows the source name, its type (such as `nws-cap`, `ipaws-cap`, `amber-cap` or `manual`), the lowest severity it brings in (for example "≥ severe"), and **polling** or **disabled**.
3. Under **Active alerts**, read each alert: the event, a severity badge (extreme, severe, moderate, minor or unknown), a headline and the areas.
4. Under **On-channel now**, see what is currently shown on the channel you picked in **Channel**. Each line gives the channel name and the display type (`crawl`, `overlay` or `forced_slate`), not the alert's name.

> **Known issue (beta.12):** **polling** only means the source is switched on; a feed that is failing can still say **polling**. A failing feed raises an **eas source unavailable** alert on the Alerts screen. Beta.12 includes a warning rule for this alert, but a notification is delivered only after an administrator assigns an enabled destination and the delivery service is configured and working.

### Show an alert on a channel

You need the Setup admin or Meeting operator role.

1. In **Channel**, pick the channel. The first choice may be `gov`, a built-in guess. Pick your real channel.
2. Find the alert under **Active alerts**.
3. Click **Show crawl on** and the channel name, or click **Show overlay**.

A line for that channel appears under **On-channel now**. These buttons act at once with no confirmation and no success message.

### Take over the whole screen

1. Tick **Confirm full-screen takeover**.
2. Click **Forced slate**.

The tick resets after each use. The station refuses a forced slate without it.

> **Warning:** A forced slate replaces the picture for everyone watching that channel. Use it only on purpose.

### Take an alert down

1. Under **On-channel now**, click **Clear** on the line for that alert.
2. In the box, click **Clear alert**.

Residents on that channel go back to regular programming.

## Understand federation (Federation)

*Federation* lets other websites that use a shared standard, called ActivityPub (the network behind Mastodon and similar sites, sometimes called "the fediverse"), follow your station and see when a new meeting is published, much like following a page on a social network. **Most stations do not need this.** It is off by default, and the screen says so.

1. In the sidebar, open **System Health**, then click **Federation**.
2. If you see **Federation is off** with a green tag, **Default-safe**, nothing is shared and the station is not advertised. You can stop here.

> **Known issue (beta.11):** There is no on/off switch in the console. The page says nothing about how federation is turned on. An IT person has to change station settings and restart CivicCast. The old built-in manual points to a switch that does not exist.

### Prepare to turn it on (Setup admin)

1. Click **Generate station key**.
2. In the box **Generate the station key?**, click **Generate key**.
3. Click **Copy settings**, and give the copied lines to your IT person.

The key is the station's permanent identity on the network. Generating again reuses the same key. Creating a key does not turn federation on. It stays off until IT staff apply the settings and restart CivicCast, and it silently stays off if the web address or key file is missing.

> **For IT staff:** See [Chapter 15](#ch-integrations) for the `CIVICCAST_ACTIVITYPUB_*` settings.

### After federation is on

The page shows counts (Pending, Accepted, Blocked, Rejected, Removed), your settings, and lists of followers and delivery attempts. A follower is another site that asks to follow you. The Publish operator or Support admin role can act on requests.

1. Click the **Pending** tab.
2. Click **Approve** to accept a follower.
3. Click **Reject** to refuse one. A box asks you to confirm with **Reject follower**.
4. Click **Block** to refuse one permanently. Confirm with **Block follower**.
5. For a delivery that failed, click **Replay delivery** to try again.

> **Warning:** **Block** cannot be undone from the console. The box says a blocked site "cannot re-follow until unblocked", but the screen has no **Unblock** control.

> **Known issue (beta.11):** If an **Approve**, **Reject** or **Block** fails, the page says, "Federation moderation failed. Ask your station administrator to check your access and the CivicCast service, then retry the follower action." The buttons show for every role, so a role mismatch can cause a failure. Ask your administrator if it keeps failing.

## What to do, and what not to do

If you are not technical, this list is for you.

**Do**

- **Read the words, not only the colour.** Write down the exact message on the screen.
- **Check Readiness and Alerts before every meeting**, and at the start of every shift. Alerts appear in the console; notification destinations are not configured by default.
- **Click Acknowledge** on an alert you have seen, so your colleagues know someone has it.
- **Take a screenshot** of anything odd, including the time shown on the screen.
- **Note the time, the channel and what you were doing**, and write down whether the time is UTC or your local time.
- **Tell your IT person** early, with the details in the next section.
- **Run Check broadcast readiness before the meeting**, not during it.
- **Use the Manual** in the sidebar for how a screen works.

**Do not**

- **Do not rely on email or text alerts until they are configured and verified.** A Setup admin must assign an enabled destination to each rule you rely on, and the station's delivery service must be working. Local delivery checks do not prove that an external mail or SMS provider accepted a message.
- **Do not rely on Emergency Alerts as your EAS.** CivicCast is not an EAS device.
- **Do not press Stop, Restart feed, Repair, Update, Rollback or Restore buttons** unless you were trained to, or your IT person told you to.
- **Do not click Block or Delete casually.** Neither can be undone from the console.
- **Do not use the Paywall for live paid access** while its resident purchase and account workflows remain incomplete (see [Chapter 7](#ch-station-business)).
- **Do not type or paste passwords, recovery codes, staff passes (tokens) or private meeting material** into a report, an email or a public issue.
- **Do not keep pressing a button that did nothing.** Some actions only queue a request, and the page does not refresh by itself. Reload the page and look again.

## Describe a problem to your IT person

A good report lets IT staff fix a problem without coming to your desk. Copy this list into an email or message, and fill it in.

```
What I was doing:
Which screen (the name in the sidebar):
Which channel (if any):
What I expected:
What happened instead:
The exact words on the screen (copy them, or attach a screenshot):
The colour or label (Ready, Check before meeting, Do not broadcast yet, Not set up yet, Needs IT help):
Date and time it happened (say if the time came from the screen, and whether it is UTC or local):
Is a meeting or a broadcast on the air now?  yes / no
Has it happened before?  yes / no
What I already tried:
```

If you have the Support admin role, you can also make a *support bundle*. This is a redacted troubleshooting file (the screen's own description of it). We did not check exactly what it contains. On **Readiness**, type a short note in the **Short note** box, click **Create support bundle**, then click **Download support bundle**. Give the file to your IT person. Do not post it publicly.

## Report a beta issue

CivicCast is a beta. Check the [releases page](https://github.com/scottconverse/civiccast-native/releases) for available downloads and use the verification record that matches the installed package. Support is community-driven, with no contract and no guaranteed response time. Problems that stop broadcasting or installation, lose data or expose a secret are handled first.

1. In the operator console, click **Report a beta issue** at the bottom of the sidebar. It opens this **Report a beta issue** section in the Manual.
2. If you have a free GitHub account, open a new issue on the project's issue page: <https://github.com/scottconverse/civiccast-native/issues>.
3. If you do not have an account, ask your IT lead or anyone on staff who has one to post your description. Use the private contact in the project's security policy for security reports: <https://github.com/scottconverse/civiccast-native/blob/main/SECURITY.md>.

In every report, include:

- The CivicCast version. It is shown at the top of the console and in the health answer.
- Your Windows version.
- The screen where it happened.
- The exact message.
- What you were doing, and what you already tried.
- Whether it was a rehearsal, a test recording or a real meeting.

If the problem is with installing, also include the installer's file name and whether its checksum matched.

> **Warning:** Never include passwords, recovery codes, staff tokens, subscriber addresses or private meeting material in a report. Do not post a support bundle publicly. Say you have one, and wait for a maintainer to ask for it privately.

> **Warning:** If you think you have found a *security* problem, do **not** open a public issue. Use the private contact in the security policy linked above.

> **Support bundles:** Open **Readiness** to create a support bundle. **Create support bundle** requires the Support admin role. Keep the bundle private and share it only through an agreed support channel.

## If it did not work

| What you see | Where | What it means and what to do |
| --- | --- | --- |
| `Checking station readiness...` that never ends, or `Could not load System Health.` | Readiness | The page could not reach the station. Check you are signed in, then tell your IT person |
| `The live on-air signal could not load.` | Readiness | The live banner could not load. Reload; if it stays, tell IT |
| `Checking broadcast readiness requires the meeting operator role. Health checks remain visible.` | Readiness | You can read the checks but not run the test. Ask your station admin |
| `Alerts could not load.` or `This action requires one of these CivicCast roles: ...` | Alerts | The event list requires Setup admin, Support admin or Meeting operator. Ask an operator with one of those roles to check it. If your account has the required role, check your sign-in and retry; tell the station admin if it still fails |
| `Could not save the rule.` | Alerts | Your role cannot change rules. Only the Setup admin can |
| `Use 24-hour HH:MM, e.g. 22:00 (or leave blank).` | Alerts, destination form | Type quiet hours as hours and minutes, such as 22:00, in UTC, or leave blank |
| `Durable storage is not ready yet.` | Alerts and other screens | The station's database is not ready. Tell your IT person |
| `The Emergency Alerts console requires the setup admin, support admin, or meeting operator role. Ask your station admin for access.` | Emergency Alerts | Your role cannot open this screen |
| `Could not display the alert.` | Emergency Alerts | The server refused. The box usually shows the server's own reason instead of this sentence. For a full-screen takeover, tick **Confirm full-screen takeover** first |
| `Could not load federation status.` | Federation | Click **Retry**. If it fails again, tell IT |
| `Could not verify your staff identity (...)` | Emergency Alerts | Sign in again at `/operator/#/login` with your staff account; the first administrator uses `/operator/#/setup` on the station computer |

## Related

- [Chapter 2: Signing in](#ch-signing-in)
- [Chapter 4: Running the meeting](#ch-running-meeting)
- [Chapter 7: Reports, analytics, program guide export, underwriting and apps](#ch-station-business)
- [Chapter 14: Troubleshooting matrix](#ch-troubleshooting)
- [Chapter 15: Cable headend, streaming, federation, emergency alerts and the API](#ch-integrations)

<!-- SOURCES: inventory/screens/health.md, alerts.md, eas.md, activitypub.md, help.md, _shell-navigation-and-roles.md; civiccast/installer/service.py:3637-3720,3880-3900,4421-4440,5231-5300,5304-5362,5363-5440,5448-5500,5632-5745; civiccast/apps/portal-operator/src/screens/status-language.ts:28-130,390-405; civiccast/apps/portal-operator/src/screens/SystemHealthScreen.tsx:60-80,124-143; civiccast/alerting/router.py:222-300,370-400; civiccast/alerting/evaluator.py:150-190,412-460,524-537; civiccast/app.py:526-620,1415-1425,1721-1750,3169-3175; civiccast/eas/service.py:25-70,128-175; civiccast/cg/router.py:283-305,519-541; civiccast/apps/portal-public/src/screens/HomeScreen.tsx:59-115; civiccast/activitypub/config.py:55-90; SUPPORT.md; SECURITY.md; docs/adoption/support-intake.md; docs/USER-MANUAL.md:739-760 (topic only, re-verified against help.md inventory); docs/releases/v1.0.0-beta.10-verification.md; fact-check pass: civiccast/app.py:526-570,1721-1750; civiccast/eas/workers.py:60-130; civiccast/eas/service.py:56-150; civiccast/apps/portal-operator/src/screens/EasScreen.tsx; civiccast/apps/portal-public/src/screens/HomeScreen.tsx:59-115,868-890; civiccast/cg/router.py:283-305,519-541; civiccast/alerting/migrations/versions/0039_alerting_and_sinkhealth.py:42-60; civiccast/alerting/models.py:55-110; civiccast/alerting/evaluator.py:395-445; civiccast/installer/service.py:3637-3700,5740-5770; civiccast/installer/router.py:371-392 -->
