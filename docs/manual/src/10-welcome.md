# What CivicCast is and who uses it {#ch-welcome}

This chapter explains what CivicCast does, which jobs the people at a small station do with it, and what happens to a meeting from the day it is put on the calendar to the day a resident watches it. It also gives a short word list for Part I. Everyone reading Part I should read it first.

## What CivicCast is

CivicCast is software for a public-meeting station: a city or county channel that records council and board meetings, is built to put them on a cable or web channel, and keeps an archive that residents can watch later. It runs on one Windows computer at the station. This manual calls that computer the **station computer**. The program keeps running in the background on that computer; you work with it through two web pages that open in a browser.

CivicCast does these jobs:

- **Keeps a library of meeting videos.** Each video file in the library is called an *asset*.
- **Schedules what airs on each channel.** A *channel* is one program stream the station sends out. A new station starts with three channels, named public, education and government.
- **Runs the channel.** A staff member starts and stops the channel's *outgoing feed* (the running stream), checks that a live camera or encoder is delivering a picture, and can put a live source on the air.
- **Records meetings** from a capture card or a network stream at times you set.
- **Makes captions.** Recorded-caption jobs turn speech into text that a person reviews before it is published with the recording. Live broadcasts can publish first-pass captions without a human review step (see [Running the meeting](#ch-running-meeting)).
- **Writes an AI summary on request.** The summary is built from the approved captions, and each quantitative claim (a count, an amount, a vote tally) must point to timestamp ranges in the transcript. A person approves it.
- **Publishes recordings to a resident website** and can copy them to archive and outside services you set up.
- **Watches its own health** and tells staff when something needs attention.

The two web pages are:

| Page | What it is | Who uses it |
| --- | --- | --- |
| **Operator console** (the *console*) | The staff screens. A menu down the left side (the *sidebar*) leads to every screen. Chapter 2 shows how to sign in and find your way around. | Station staff and volunteers |
| **Resident portal** (the *portal*) | The public website. It has three pages: Home, Recordings and Schedule. No sign-in. | Residents |

The installer puts a shortcut named **CivicCast Operator Console** on the Desktop and in the Start menu, and a second Start-menu shortcut named **CivicCast Public Portal**. The console and the portal are two different web pages served by the same program.

> **For IT staff:** The console and portal addresses, the Windows service that runs them, and how to make the portal reachable from residents' own devices are covered in [Installing, first run, upgrading, uninstalling](#ch-installing) and [Planning your station](#ch-planning).

## The people and their roles

CivicCast has five *roles*. A role is a set of things a signed-in person is allowed to do. The roles are named after jobs that exist at a small station, where one or two people usually do all of them. Think of a role as a hat. One person can wear all five hats.

| Role (as the console shows it) | The job, in plain words | What this job does in the console |
| --- | --- | --- |
| **Setup admin** (`setup_admin`) | Sets the station up and keeps its settings right. | Creates the station's first account and recovery kit; adds camera or test sources, a backup folder and provider accounts on First Setup; edits Station Profile, AI Models, Custom Fields, Paywall and Control Room Setup; applies cable-headend settings and 24/7 settings on Channels; can create and cancel schedule items. |
| **Meeting operator** (`meeting_operator`) | Runs the meeting night. | Works the Live screen (check the source, run the checklist), starts and stops a channel's feed and puts a live source on air from Channels, runs Control Room, starts and stops scheduled recordings, builds agendas, adds recurring slots in the Program Guide. |
| **Records clerk** (`records_clerk`) | Looks after the official record of the meeting. | Approves, edits or rejects caption lines in the Review queue; approves AI summaries; places and clears legal holds; uploads videos; edits a recording's public title and description. |
| **Publish operator** (`publish_operator`) | Decides what residents and outside services can see. | Creates and cancels schedule items and approves them to air; prepares a recording for playback; approves publishing on the Publish screen; removes a recording from the portal; manages Auto-schedule, the community board, program-guide export, underwriting and App Admin. |
| **Support admin** (`support_admin`) | The help desk: the person who keeps the station healthy and fixes it. | Creates the support bundle and runs repair, restore, update and rollback tools on Readiness; reads Reports; can read most screens. Mostly read-only elsewhere. |

The names in parentheses are the raw role names. You will see them in error messages, for example "This action requires one of these CivicCast roles: meeting_operator, setup_admin." The console never shows them any other way.

### Everyone signed in

Some screens ask for no role at all. The sidebar shows them to every signed-in person: Live, Facility, Control Room, Channels, CG Board, Schedule, Program Guide, Assets, Contributors, Review queue, Summary review, Publish, Playback policy, Analytics, Readiness, Alerts and Federation. The Manual and First Setup are open even to a person who is signed out.

> **Warning:** A screen that appears in your sidebar can still refuse you. The sidebar hides some screens by role, but each screen and each button is checked again when you use it. For example, anyone can open Schedule, but a person without the Publish operator, Setup admin or Support admin role gets a red "Could not load schedule." box. If a screen refuses you, you do not have the role it needs. Chapter 2 lists the wording you will see.

### How people get roles

The first account, created on First Setup, holds all five roles and remains the station's recovery account. A setup admin can add staff accounts, assign roles, disable sign-in, reset passwords and manage sessions from **Staff accounts**. Each staff member signs in with their own username and password and receives only their assigned roles. See [Chapter 2](#ch-signing-in) and [Security and privacy](#ch-security).

The command-line tool also makes access tokens for scripts and tools. Those tokens are separate from browser sign-in.

> **For IT staff:** Role-limited keys, the role-to-screen table and the access rules by feature are in [Security and privacy](#ch-security) and the roles appendix ([Roles and permissions](#app-roles)).

## The life of a meeting

Here is the path a meeting takes through CivicCast. Each step names the console screens involved. Chapters 3 to 6 explain each step with exact buttons.

```mermaid
flowchart LR
  A["1 Plan<br/>Schedule, Program Guide,<br/>Recording"] --> B["2 Approve what airs<br/>Publish to residents"]
  B --> C["3 Meeting night<br/>Readiness, Live, Channels"]
  C --> D["4 Recording saved<br/>Assets"]
  D --> E["5 Make it playable<br/>Package for playback"]
  E --> F["6 Publish<br/>Publish screen"]
  F --> G["7 Captions made,<br/>then checked<br/>Review queue"]
  G --> H["8 Optional summary<br/>Summary review"]
  C --> I["Residents watch live<br/>Portal: Home"]
  F --> J["Residents watch the recording<br/>Portal: Recordings"]
  G --> J
```

*Figure 1.1. The life of a meeting in CivicCast. Boxes 1 to 8 are staff work in the operator console; the two boxes on the right are what residents see on the portal.*

**How to read the figure.** Follow the arrows from the left. Steps 1 and 2 are done ahead of time. Step 3 is the meeting itself, and while it runs residents can watch on the portal's Home page. After the meeting, the recording appears in the Assets library (step 4). Before residents can watch a saved recording it has to be made playable in a browser (step 5) and then published (step 6). When recorded captions are enabled, publish approval also starts caption work for a recording with a stored source file (step 7). The recording becomes visible to residents at once; reviewed captions attach later according to the station's completion setting. The summary (step 8) is optional.

1. **Plan.** Put the meeting on the calendar. You can place a recorded video on a channel at a set time (Schedule, Program Guide or Auto-schedule). To capture a live meeting, set up a recording from a capture card or network stream (Recording). Details: [Before the meeting](#ch-before-meeting).
2. **Approve what airs.** A recorded program placed on the Schedule does not air, and residents do not see it, until someone approves it with **Publish to residents**. Saving a schedule item and approving it are two separate steps. A live meeting goes on air in step 3, not here.
3. **Meeting night.** Check Readiness (the "can we broadcast right now?" page). On Live, check that the camera or encoder is delivering, then run the checklist. On Channels, start the channel's feed and, for a live meeting, put the checked live source on the air. Details: [Running the meeting](#ch-running-meeting).
4. **Recording saved.** A finished recording becomes an asset in the Assets library. After you end a live session on the Live screen, CivicCast looks for the recording file, checks and packages it, and shows "Recording saved as asset" followed by the asset's ID.
5. **Make it playable.** *Packaging* makes a copy of the video that a web browser can stream. An asset with no playable copy cannot be watched on the portal.
6. **Publish.** On the Publish screen a publish operator ticks where the recording should go. The Portal option makes the recording public to residents. The other options copy it to archives and outside services.
7. **Captions.** When recorded captions are enabled, approving publication queues caption work for a recording with a stored source file, even if the Portal row is not selected. CivicCast holds the resulting caption lines for a person to approve, correct or reject. Station Profile can disable recorded captions, select English only or English and Spanish, and choose whether reviewed English attaches first or waits for every selected language. See [Recorded-caption settings](#recorded-captions).
8. **Summary (optional).** From an asset's page a records clerk can ask for an AI summary. This console workflow uses only approved caption lines, including saved corrections that were subsequently approved. A person reviews and approves the summary.

After the meeting, the portal shows residents the recording on **Recordings** and on its own watch page, with captions when they are attached. The portal's **Schedule** page shows what airs on each channel over the next 72 hours.

> **Packaging recordings:** A meeting captured on **Recording** can arrive in Assets as **Recorded**, with **Not servable yet** until a streamable copy exists. **Package for playback** is offered for both **Validated** and **Recorded** rows with a stored source file. If a streamable copy already exists, use **Repackage for playback** to replace it after changing a trim or source. A recording that Live finalizes is checked and packaged by that step. See [Chapter 5](#ch-after-meeting).

## What residents see

The portal is an English-only website with a dark look and no sign-in. Its header has three links: **Home**, **Recordings** and **Schedule**.

- **Home** shows whether the station is on air. When it is, a video player appears. When it is not, you see "The station is standing by" or "No live broadcast is on air." It also lists what is coming up and the six newest recordings.
- **Recordings** is a searchable archive of every published meeting.
- A recording's **watch page** shows the video with captions, the meeting's agenda beside it if staff published one, the description, and a **Copy share link** button.
- **Schedule** shows what airs on each channel.

![The resident portal Home page while a channel is on air. The header shows the links Home, Recordings and Schedule. The Live now panel has a video player beside a Broadcast status card.](manual/images/portal-home.png){width=90%}

*Figure 1.2. The resident portal's Home page.*

## Word list for Part I

These are the words used most in Part I. Each is explained again where it first matters.

| Word | What it means in this manual |
| --- | --- |
| **Station computer** | The one Windows computer where CivicCast is installed. |
| **Console** | The staff web page. The sidebar on its left is the menu. |
| **Portal** | The public web page for residents. |
| **Channel** | One program stream the station sends out. A new station has three: public, education, government. |
| **On air** | A channel's feed is running and sending out what is playing. |
| **Outgoing feed** | The running stream for one channel. Staff start and stop it on the Channels screen. |
| **Slate** | A standby card the channel shows when no program is playing. |
| **Source** | A live picture CivicCast can receive, such as a camera or encoder. Source types shown on the Live screen are RTMP, RTSP, NDI and SRT. |
| **Asset** | One video file in the library (the Assets screen). |
| **Recorded / Validated** | Two of an asset's states. A *Validated* asset passed CivicCast's file checks, as uploads do. A *Recorded* asset is a finished live capture. |
| **Package for playback** | Make a copy of an asset that web browsers can stream. |
| **Publish** | Make a recording visible to residents, and optionally copy it to archives and outside services. |
| **Premiere** | A recorded asset placed on a channel at a start time. |
| **Caption** | The text of what is said, shown with the video. |
| **Cue** | One caption line with a start and end time. The Review queue shows cues one at a time. |
| **Summary** | An AI-written overview of a meeting. Every quantitative claim must link to caption timestamps. |
| **Readiness** | The answer to "can we broadcast right now?" on the Readiness screen. |
| **Role** | A set of things a signed-in person may do. There are five. |
| **Recovery kit** | The one-time printout or file with the admin username, the admin password and eight emergency codes, created on First Setup. |
| **Test mode** | The mode a new station starts in. The First-run defaults card on First Setup shows a **Mode** heading with "Test mode" under it. |
| **Headend** | The cable company's equipment that receives a channel's signal. |

## Source edition and release evidence

This manual describes the 1.0.0-rc.6 edition. Use the download and installation instructions in [Chapter 10](#ch-installing) with a package on the [releases page](https://github.com/scottconverse/civiccast/releases), and follow the version-specific verification record for that package. The [Beta 18 record](https://github.com/scottconverse/civiccast/blob/main/docs/releases/v1.0.0-beta.18-verification.md) remains historical. Keep each historical result with its named release. For publication, download, installation, and acceptance status, use the matching release page and verification record; do not infer status from this manual.

**Historical Beta 11 operating result:** one CivicCast host ran Public, Government, and Education channels together for 72 hours and recorded **99.78% live caption-input coverage**. This remains a Beta 11 host measurement; it does not establish performance of the current package. See the [72-hour host performance report](https://github.com/scottconverse/civiccast/blob/main/docs/ops/beta11-host-caption-performance-72-hour-2026-10-11.md) for the channel totals and method.

**Live captions:** Whistle is the CPU primary, publishes first-pass recognition, and uses Whisper fallback for the affected channel when needed. CivicCast automatically probes Whistle recovery after an initial 30-second cooldown and retries with bounded backoff; a successful probe returns later chunks to Whistle without a service restart. Probe timing is not a fixed recovery guarantee. Optional NVIDIA CUDA applies to supported Whisper work; Whistle remains CPU-only.

The [evidence appendix](#app-evidence) keeps historical measurements with their original builds and runs.

## If it did not work

This chapter contains no steps to follow, so there is nothing here to fail. If a later chapter's screen shows a message you do not understand, chapter 8 ([When something looks wrong](#ch-something-wrong)) lists the common ones.

## Related

- [Signing in and finding your way around](#ch-signing-in)
- [Before the meeting](#ch-before-meeting)
- [Running the meeting](#ch-running-meeting)
- [After the meeting](#ch-after-meeting)
- [Publishing, and what residents see](#ch-publishing)

<!-- SOURCES: inventory/screens/_shell-navigation-and-roles.md (sections 1-3, 5); inventory/screens/schedule.md; inventory/screens/recording.md; inventory/screens/live.md; inventory/screens/channels.md (feed Start/Stop, Take live); inventory/screens/assets.md; inventory/screens/review.md; inventory/screens/summary.md; inventory/screens/publish.md; inventory/screens/public-shell.md; inventory/screens/public-home.md; inventory/screens/public-watch.md; inventory/screens/public-schedule.md; inventory/screens/installer-nsis-service-finish.md (shortcuts); docs/releases/v1.0.0-beta.10-verification.md (all proof statements and numbers); inventory/screens/installer-nsis-packs-and-verify.md (packs and station folders, exit 110); civiccast/auth/roles.py:14-57; civiccast/apps/portal-operator/src/auth/roles.ts:6-12; civiccast/apps/portal-operator/src/components/shell/Sidebar.tsx:88-183; civiccast/apps/portal-operator/src/screens/AssetsScreen.tsx:408-441 (Package button only for validated rows); civiccast/schedule/router.py:375-409 (API accepts validated or recorded); civiccast/live/finalization_worker.py:739-752 (live capture packaged and manifest set); civiccast/apps/installer/src-tauri/nsis-hooks-bootstrap.nsh:1794-1808 (shortcut names); civiccast/installer/models.py:349-356 (three channels, test mode defaults); civiccast/installer/station_state.py:795-811 (first admin scope admin) -->
