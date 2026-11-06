# Signing in and finding your way around {#ch-signing-in}

This chapter shows you how to open the operator console, sign in, recover a lost password, sign out, and find the screen you need. It also explains the colors and status words you will see, how to use the Manual built into the console, and how to set up a brand-new station the first time. Everyone who uses the console reads this chapter; the first-run section is for the person setting up a new station.

## Before you start

- For **first setup, first-admin sign-in and recovery**, use a browser on the station computer (a remote-desktop session to that computer also works). Staff accounts can sign in from the station or, when the separate staff hostname is configured, from an approved staff network. See the [network access guide](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/network-access.md) and [staff accounts guide](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/staff-accounts.md).
- You need the **admin username and password** from the station's recovery kit. The kit is the printout or saved file made when the station was first set up.
- CivicCast has to be running in the background. If it is not, the shortcut opens a browser error page instead of the console.
- If nobody has set this station up yet, go straight to [Set up a brand-new station](#signing-in-first-run).

> **For IT staff:** The Windows service, the console address and the sign-in limits are in [Installing, first run, upgrading, uninstalling](#ch-installing) and [Security and privacy](#ch-security).

## Open the console {#signing-in-open}

1. On the station computer, double-click the **CivicCast Operator Console** shortcut on the Desktop. The Start menu has the same shortcut.
2. Wait for the browser to open.

The Desktop shortcut opens the local operator console. A signed-out browser now has a separate **Staff sign-in** page. Use **First setup** at `/operator/#/setup` only to create or recover the first administrator; ordinary staff sign in at `/operator/#/login`.

> **Note:** First-admin setup and recovery remain local-only. A staff account can use `/operator/#/login` on the configured staff hostname from an allowed network; the public hostname does not expose staff sign-in.

## Sign in

1. Staff: open `/operator/#/login`. First administrator: open `/operator/#/setup` on the station computer and choose **Admin sign-in**.
2. Enter the account's username and current password. For the first administrator, use the password chosen during setup (or the new password from the most recent recovery/reset); an older printed password may no longer work.
3. Click **Sign in**. The button does nothing until both boxes are filled.

You should be taken to the **Readiness** screen. If a screen sent you here because you were signed out, you go back to that screen instead.

> **Note:** Signing in on one browser does not sign out any other browser or device that is already signed in.

> **Note:** A normal sign-in takes you straight to Readiness, so you will not see this on that path. After you create the first admin on a new station (see [Set up a brand-new station](#signing-in-first-run)), the **Setup complete** card says "CivicCast saved a fresh console token in this browser for &lt;your admin display name&gt;." A *console token* is the private pass your browser keeps so the station knows it is you. The sentence means you are signed in.

> **Warning:** Closing the browser does not sign you out. The browser keeps its token until you sign out, an administrator revokes the session, or the 12-hour session expiry is reached. On a shared or borrowed computer, always click **Sign out** before you leave.

## Use a recovery code

Use this only when the admin password is lost. If you have the password, use **Admin sign-in** instead. Each recovery code works once, and the station has eight.

1. Find your printed or saved recovery kit and pick one unused code.
2. On the sign-in page, find the card named **Use recovery code**.
3. Type your username in **Admin username**.
4. Type the code in **Recovery code**.
5. Type a new password in **New admin password**. It needs at least 12 characters.
6. Type the same password in **Confirm new admin password**. If the two differ, you see "Passwords do not match."
7. Click **Recover account**. The button changes to **Confirm — consume recovery code**, and a red message appears: "This permanently consumes one recovery code — only 8 exist for this station. Click Recover account again to confirm." That message refers to the renamed **Confirm — consume recovery code** button.
8. Click **Confirm — consume recovery code**.

You are signed in and taken to the same place as after a normal sign-in. Every other browser or device that was already signed in stays signed in.

> **Warning:** The code is used up when the station accepts it. If you change any box after the first click, the button goes back to **Recover account** and you must click twice again.

> **Warning:** Your printed kit may still list an old password. After a recovery, write the new password somewhere safe. While you can still sign in, a Setup admin can make a fresh set of eight codes with **Regenerate recovery kit** in the **Security** card on **Station Profile**; the old codes stop working at once, and the new kit lists the codes only, not the password. If the password and all recovery codes are lost, a local Windows administrator can recover the account offline with the administrator-reset command in [Security and privacy](#ch-security). That reset preserves station data, revokes existing first-admin sessions and old recovery codes, and requires a fresh recovery kit afterward.

The Beta20 package includes station-state recovery repair so the normal service can replace the session automatically without manually changing permissions. In the signed Beta20 package, recovery/reset and normal HTTP login were exercised without manual ACL repair; private-key protection remained in place. The interactive prompt was not exercised.

## Sign out

1. Look at the right end of the top bar.
2. Click **Sign out**. The button reads "Signing out…" for a moment.

You should land on the sign-in page. This signs out the browser you are using and no other.

The **Sign out** button appears only after the console has confirmed who you are. It is grey and unusable while a new station's recovery kit is waiting to be confirmed.

## Find your way around the console

### The top bar

The top bar runs across the whole screen. From the left it shows the **C** logo, the word **CivicCast** and the installed version number, for example `v1.0.0-rc.5`. On a wide screen the middle shows a pill reading **No live meeting broadcast** and a clock reading "Local &lt;time&gt; / Next No events scheduled". On the right are a theme button (**Switch to dark theme**), a round badge with your initials, and **Sign out**.

Hover over the round badge to see your name and your roles, in the form "&lt;your display name&gt; / &lt;your role names&gt;". Setup admins can also review account role assignments in **Staff accounts**.

> **Known issue (beta.12; first recorded in beta.11):** The **No live meeting broadcast** pill and the words "Next No events scheduled" still never change. They stay the same during a live meeting and with a full schedule. Do not use them to decide whether the station is on air or what comes next. Use **Readiness**, **Channels** and **Schedule** instead.

### The sidebar

The sidebar on the left is the menu. At its top a card reads **Public meetings** over **CivicCast station**. Below it are six sections. Click a section's name to open or close it. At the bottom is a link **Report a beta issue** and a tag **Operator-first beta**.

> **Known issue (beta.12; first recorded in beta.11):** The card at the top of the sidebar always reads "CivicCast station" and "Public meetings". It does not show your station's name. The name you chose appears on the sign-in page, in the **Setup complete** card.

The six sections start like this. The Help, Setup and System Health sections start closed, and the section that holds the screen you are on always opens.

| Section | Screens in it | You use it to |
| --- | --- | --- |
| **Help** | Manual | Read the manual built into the console. |
| **Setup** | First Setup, Staff accounts, Backup and restore, Import station, Network access, Control Room Setup, Station Profile, Cable Commissioning, AI Models, Custom Fields, Paywall | Set the station up and change its settings. |
| **Run Meeting** | Live, Facility, Control Room, Remote Contribution, Channels, Other stations, CG Board, CG Designer, Schedule, Auto-schedule, Program Guide, Recording | Plan and run what goes on air. |
| **Review Records** | Assets, Missing Media, Media Lifecycle Settings, Contributors, Review queue, Summary review, Agendas | Look after videos, captions, summaries and agendas. |
| **Publish** | Publish, Playback policy, Analytics, Reports, EPG Export, Underwriting, App Admin | Decide what residents see, and read audience numbers. |
| **System Health** | Readiness, Alerts, Emergency Alerts, Federation | Check that the station is healthy. |

Altogether the console has 42 sidebar entries. On a phone or a narrow window, under 768 pixels wide, the sidebar becomes a drawer. Click the button **Open navigation** to open it. Pick a screen, or click outside it, to close it.

### Which screens you can see

You hold the roles of the account you signed in with. The account made on First Setup holds all five roles, and sees all 42 entries. A person with fewer roles sees fewer entries. Nineteen entries are shown to everyone signed in: Manual, First Setup, Live, Facility, Control Room, Channels, CG Board, Schedule, Program Guide, Assets, Contributors, Review queue, Summary review, Publish, Playback policy, Analytics, Readiness, Alerts and Federation. The other twenty-three need at least one of the roles marked below.

| Screen | Setup admin | Meeting operator | Records clerk | Publish operator | Support admin |
| --- | :-: | :-: | :-: | :-: | :-: |
| Staff accounts | yes | - | - | - | - |
| Backup and restore | yes | - | - | - | yes |
| Import station | yes | - | - | - | - |
| Network access | yes | - | - | - | yes |
| Control Room Setup | yes | - | - | - | - |
| Station Profile | yes | yes | - | - | yes |
| Cable Commissioning | yes | - | - | - | yes |
| AI Models | yes | yes | - | - | - |
| Custom Fields | yes | - | - | - | - |
| Paywall | yes | - | - | - | - |
| Remote Contribution | yes | yes | - | - | yes |
| Other stations | yes | yes | - | yes | yes |
| CG Designer | yes | - | - | yes | yes |
| Auto-schedule | yes | - | - | yes | yes |
| Recording | yes | yes | - | - | yes |
| Missing Media | - | yes | - | yes | yes |
| Media Lifecycle Settings | yes | - | yes | yes | - |
| Agendas | - | yes | yes | - | - |
| Reports | - | - | - | - | yes |
| EPG Export | yes | - | - | yes | - |
| Underwriting | yes | - | - | yes | yes |
| App Admin | yes | - | - | yes | - |
| Emergency Alerts | yes | yes | - | - | yes |

> **Warning:** The sidebar is a convenience, not a lock. A screen in your sidebar can still refuse you, and a button on it can still fail when you press it. Each screen and each button is checked again when you use it. Roles are added together: one matching role is enough.

These screens open for every role but refuse some things:

| Role | Screen | What happens |
| --- | --- | --- |
| Records clerk, Publish operator | Alerts | The event list does not load; it requires Setup admin, Support admin or Meeting operator. The runtime safe-to-air health indicator is available to every operator role. |
| Records clerk | Federation | **Approve**, **Reject** and **Block** are refused. |
| Setup admin without Publish operator or Support admin | Federation | **Approve**, **Reject** and **Block** are refused. Creating the station key works. |
| Support admin | Alerts | **Save**, **Add destination** and **Delete** are refused. |

### Find a screen by what you want to do

| I want to | Go to |
| --- | --- |
| Check whether we can broadcast | Readiness |
| Put a recorded video on a channel | Schedule, then **Publish to residents** |
| Set up a repeating weekly slot | Program Guide |
| Record a meeting from a capture card or stream | Recording |
| Run a live meeting | Live, then Channels |
| Upload or find a video | Assets |
| Check caption lines | Review queue |
| Approve an AI summary | Summary review |
| Decide what residents can see | Publish |
| See audience numbers | Analytics |
| Read the manual | Manual |

> **Known issue (beta.11):** Some screens carry a different name at the top of the page than in the sidebar. If you cannot find a heading, look at this list.
>
> | Sidebar name | Page heading |
> | --- | --- |
> | Readiness | Safe to broadcast |
> | Alerts | Alerts & monitoring |
> | Federation | ActivityPub federation |
> | CG Designer | CG Board Designer |
> | Recording | Scheduled recording |
> | Contributors | Contributor submissions |
>
> Some on-screen messages also say "System Health" when they mean the **Readiness** screen.

> **Note:** Schedule, Program Guide, Auto-schedule and Recording use the station's configured time zone. The browser may show other timestamps in its own zone; [Chapter 3](#ch-before-meeting) identifies the zone used by each time-entry screen.

## Read the colors and status words

CivicCast uses five phrases to answer "can I act on this?". Each has a color. Wherever you see one, it means the same thing.

| Phrase | Color | What it means |
| --- | --- | --- |
| **Ready** | Green | The required checks passed. |
| **Check before meeting** | Amber | Something optional or recoverable needs attention. |
| **Do not broadcast yet** | Red | A required check failed for tonight's broadcast. |
| **Not set up yet** | Amber | An optional provider or feature has no sign-in details or proof yet. |
| **Needs IT help** | Red | The next step needs administrator, certificate, database or service work. |

A channel's outgoing feed has its own words. **On air** is green. **Needs attention** is red. The others are amber: **Showing slate**, **Starting**, **Stopping**, **Finishing current item**, **Changing source** and **Stopped**. A feed that is not on air during a meeting is amber on purpose.

Other words you will meet: **Not run yet** (a check has not been run), **Undeliverable** (a message could not be delivered), **Live on the portal**, **Archive pending**, **Archive verified**, **Waiting for media**, **Needs changes**, **Under review**, **Draft**, **Publishing**, **Complete** and **Needs action**. Each screen's chapter explains the words on that screen. The statuses appendix ([Statuses and what they mean](#app-status)) lists them all.

> **Known issue (beta.11):** The word **Ready** does not mean the same thing everywhere. On Readiness it means the whole station is ready. In the media readiness panel it means one video's playable copy is ready. On Assets, **Packaged** and **Published** are different words again. Read the screen's own text each time.

> **Known issue (beta.11):** Not every status uses the five phrases. You will see plain lowercase words such as `approval-only` on Federation, `critical`, `warning` and `info` on Alerts, `forced_slate` on Emergency Alerts, and job states such as `arming` and `finalizing` on Recording. Readiness also uses an extra phrase, **Ready with optional items**.

### Messages and confirmation boxes

- **Pop-up messages** (toasts) appear at the bottom of the screen and close by themselves after 4.5 seconds. Click **Dismiss notification** to close one sooner.
- **Confirmation boxes** are the centered boxes that ask before a risky action. Focus starts on **Cancel**. Pressing Escape or clicking the dim background cancels. The action button names what it does, such as **Stop feed**.
- A **sample setup banner**, a red box reading "CivicCast could not finish first-run sample setup", can appear at the top of any screen except First Setup. It means the sample video or starter schedule item failed, and nothing else. The buttons are **Retry sample setup** (for a Setup admin or Publish operator) and **Dismiss**.
- **Loading this CivicCast screen...** shows while a screen loads. **Page not found** with the text "This operator route does not exist in this build." appears for an address that does not exist. Its buttons are **Manual**, **First Setup**, **Recording**, **Reports** and **Readiness**.

> **Known issue (beta.11):** Pop-up messages appear on only a few screens (Schedule, the trim editor, Program Guide, CG Board and Media Lifecycle). On other screens a successful save or command is silent. If nothing seems to happen, check the screen for a changed value before you press again.

> **Warning:** Not every risky button asks first. For example, **Start Live Stream** on Live, **Approve & put on air** on Channels, **Reject** on a caption line, and **Save automation settings** on Channels act at once. Alerts uses an inline **Delete** then **Confirm delete?**, and Emergency Alerts uses a tick-box for a forced slate. Treat every button as real.

> **Note:** No banner tells you that the console has lost contact with the station. Each screen reports its own loading failure.

## Open the Manual

1. In the sidebar, open the **Help** section.
2. Click **Manual**.

You should see a page headed **Operator manual** with a contents list on the left. The Manual works without signing in, works without an internet connection, and is the only screen you can open while a new station's recovery kit is waiting to be confirmed.

- To find a section, type in **Filter by section title**. It narrows the contents list to sections whose *title* contains your words. It does not search the text inside sections. If nothing matches you see `No section title matches "<text>".`
- Click a contents entry to jump to that section.
- **Report a beta issue** at the bottom of the sidebar opens this manual at [Report a beta issue](#report-a-beta-issue).

> **Note:** The Manual screen displays an offline copy assembled with the release. Check the version on this manual's cover if you are unsure which operating guide you have.

## Use the keyboard or a screen reader

- The first stop when you press **Tab** on a fresh page is a link **Skip to main content**. It appears only while it has focus. Press Enter to jump past the top bar and sidebar.
- Every control you can reach with Tab shows a visible outline.
- After you change screens by clicking or pressing a key, focus moves to the main area of the new screen. It does not move on the first page load.
- On a narrow window, the **Open navigation** drawer keeps Tab inside it, and Escape closes it.
- The sidebar is labelled **Primary navigation**, and the screen you are on is marked as the current page. Each section header is labelled **Show &lt;name&gt; navigation** or **Hide &lt;name&gt; navigation**.
- Error boxes, toasts and the on-air banner on Readiness are announced to screen readers.
- The theme button switches between light and dark.

> **Known issue (beta.11):** The theme resets to light every time you reload the page and does not follow your computer's own dark-mode setting. The browser tab always reads "CivicCast Operator" on every screen, so look at the heading on the page to see where you are.

### The resident portal

The public portal has a hidden link **Skip to main content** that appears on the first Tab press. Its header has links **Home**, **Recordings** and **Schedule**, with the current one marked. After you change pages, focus moves to the new page's heading. Tap targets are at least 44 pixels tall. The portal is English only and always uses a dark look.

## Set up a brand-new station {#signing-in-first-run}

Do this once, on the station computer, when CivicCast is installed and nobody has set it up. The person who does it becomes the station's first admin and holds all five roles.

**Before you begin:** choose the person who will be the admin. Have a printer, or a safe place to save a file. Plan to finish in one sitting, because you cannot leave the recovery kit before you confirm it.

1. Open the console as described in [Open the console](#signing-in-open). You see **First setup** and a card named **Durable storage**.
2. Click **Prepare storage** on the **Durable storage** card. It reads "Preparing..." and then shows **Storage ready** and "CivicCast has a local database for meeting records, captions, summaries, and subscriptions." Until then the page says "Create the first admin after storage is ready." If the card already shows **Storage ready** when you arrive, there is nothing to click; go on to step 3.
3. Fill in the form that appears:
   - **Station name**: the name residents will recognize.
   - **Admin display name**: the person responsible for setup and recovery.
   - **Admin username**: a local sign-in name for the first admin.
   - **Admin password**: at least 12 characters. A counter shows how many you have typed.
   - **Confirm admin password**: type it again.
   - **Where will you keep the recovery kit?**: a note for your records, such as "printed, stored in the clerk safe". It is not a file path, and nothing is saved there.
   - **Resident portal URL**: optional for rehearsal. Use a complete `http://` or `https://` address without credentials. Set it before public launch.
4. Click **Create first admin**. The button stays grey until the required boxes are valid.
5. Read the green **Recovery kit ready** panel. It shows your admin username, your admin password and eight emergency recovery codes. It says: "This screen and the saved/printed copy are the only place your admin password and these codes are ever shown together."
6. Click **Print kit** to open your print dialog, or **Save kit** to download a text file named `civiccast-recovery-kit-<kit id>.txt` to your Downloads folder.
7. Tick **I have saved or printed this kit — the admin password and the recovery codes — and stored it away from this computer.** The box stays grey until you have used **Print kit** or **Save kit**. After **Print kit**, it unlocks when the print dialog closes, whether you printed or cancelled.
8. Click **Continue to the console**. It reads "Recording confirmation..." while it works.

You should see a green **Setup complete** card, a **First-run defaults** card, and a block of setup tools.

> **Warning:** The saved kit file contains the admin password in plain text, together with all eight codes. Keep it, or the printout, where only authorized people can find it. Do not email it, and do not leave it in a folder that syncs to the internet.

> **Warning:** Until you confirm the kit, `/operator/#/setup` holds you on First Setup. Every sidebar entry except **Manual** is grey, **Sign out** is disabled, other console addresses return to First Setup, and your browser warns before you close the page. The kit lives in that browser tab. The station can never show the codes again. If you return to `/operator/#/setup` without having confirmed, a warning named **Recovery kit never confirmed** appears there with a button **I found the kit — it is stored safely**. That button only records your word; it does not check anything.

### What a new station starts with

You did not choose these. A new station starts with three channels (public, education and government), the default channel government, the time zone "local", **Test mode**, a sample video, and a starter schedule item. The sample content is made in the background after you create the admin. The **First-run defaults** card shows the channels, the time zone, the mode, the storage folders, and whether sample content and the initial schedule are enabled.

> **Known issue (beta.11):** The **First-run defaults** card shows the starting channels, time zone, mode, sample content and initial schedule, but this page has no control to change **Test mode** or disable those defaults. Before a public meeting, remove the sample video and starter schedule item if you do not want them to appear. See [Chapter 10](#ch-installing) and [Before the meeting](#ch-before-meeting).

### The setup tools

Below **Setup complete** are four tools for a Setup admin: **Camera or test media** (six choices, with **Bundled sample video** selected first), **Backup destination**, **Storage and viewing estimate**, and **Provider setup** (cloud storage, archive and notice providers). Part II explains each one. If you do not hold the Setup admin role, the provider boxes are disabled and a warning says so.

> **Note:** **Verify backup** writes, reads and deletes a small test file in the folder you name, to prove CivicCast can write there. It is a check of the folder. See [Running it day to day](#ch-operations) for how backups are made.

## If it did not work

| What you see | Cause | What to do |
| --- | --- | --- |
| A browser error page instead of the console | CivicCast is not running, or the browser is not on the station computer. | Ask your IT person to start it. See [Troubleshooting matrix](#ch-troubleshooting). |
| "Invalid admin username or password." | The username or password is wrong. | Check the kit and retype both boxes. If the password is lost, use a recovery code. |
| "Invalid staff username or password." | The staff credentials are wrong, or the account is disabled. | Retype your credentials at `/operator/#/login`. Ask a Setup admin to check your account or reset its password. |
| "Too many staff sign-in attempts. Wait and try again." (possibly followed by a retry time) | Too many failed staff sign-ins. | Wait for the stated retry time, then use the correct credentials at `/operator/#/login`. |
| "Invalid recovery code or admin username." | The code or username is wrong, or the code was already used. | Try another unused code. A wrong guess does not use up a code. |
| "Too many sign-in attempts from this station. Wait N seconds, then try again with the correct password, or use a printed recovery code." | Too many wrong guesses. | Wait the time shown. A correct password or recovery code still gets through. |
| "Too many failed attempts to authenticate with the staff API. Wait N seconds, then try again." | Too many requests with a bad sign-in. The station's default is 10 failures in 60 seconds. | Wait, then try again. "Staff API" means the station's staff-side service. |
| "The station is cooling down after too many requests" | The same slowdown, shown on First Setup. | Click **Try again**. The sign-in form on that card still works. |
| **You were signed out**, "This browser's console session is no longer valid..." | Your session expired after 12 hours, an administrator revoked it, or the station's sign-in state was reset. | Sign in again. A Setup admin can review and revoke station sessions in **Staff accounts**. |
| "This browser's console sign-in is no longer valid" | The station rejected the sign-in this browser still held. | Click **Sign in again**, then use `/operator/#/login` for a staff account or `/operator/#/setup` for the first administrator. |
| "First setup can only be done from the station computer itself" | You opened first setup or first-admin recovery from another computer. | Open `/operator/#/setup` on the station computer, or inside a remote session on it. Staff accounts use `/operator/#/login` from the approved staff network when configured. |
| "Could not read setup state." with more text | The station did not answer properly. | Wait a moment and reload. If it repeats, tell your IT person. |
| "Sign in with the local station admin account, then try again." | The request carried no sign-in. | Sign in on First Setup. |
| "This action requires one of these CivicCast roles: ..." | Your account does not hold a role the action needs. | Ask your admin. See below. |
| "Staff identity is required for this action." | You are signed out. | Sign in. |
| "Could not verify your staff identity (...)" | The screen could not check who you are. | Sign in again at `/operator/#/login` with your staff account, or use `/operator/#/setup` if you are the first administrator. |
| "Durable storage is not ready." | The station's database has not been prepared. | Click **Go to Setup**, then **Prepare storage**. |
| **Page not found** | The address does not exist in this build. | Use a button on the page, or the sidebar. |

The role message lists raw role names. They map to the names in the console like this:

| Raw name | Console name |
| --- | --- |
| `setup_admin` | Setup admin |
| `meeting_operator` | Meeting operator |
| `records_clerk` | Records clerk |
| `publish_operator` | Publish operator |
| `support_admin` | Support admin |

> **Known issue (beta.11):** The message "First setup can only be done from the station computer itself" also appears for first-admin sign-in and recovery when `/operator/#/setup` is opened remotely. Those `/api/setup/*` actions remain local-only. Staff use `/operator/#/login` and, when configured, the CIDR-restricted staff hostname.

> **Note:** Setup admins can add staff, assign roles and disable access in **Staff accounts**. See the [staff accounts guide](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/staff-accounts.md) for account and session management.

## Related

- [What CivicCast is and who uses it](#ch-welcome)
- [Before the meeting](#ch-before-meeting)
- [Running the meeting](#ch-running-meeting)
- [When something looks wrong](#ch-something-wrong)
- [Roles and permissions](#app-roles)

<!-- SOURCES: inventory/screens/_shell-signin-and-session.md; inventory/screens/_shell-navigation-and-roles.md (sections 1-3, 6); inventory/screens/_shell-shared-components.md; inventory/screens/setup.md; inventory/screens/help.md; inventory/screens/public-shell.md; inventory/screens/public-home.md; inventory/screens/installer-nsis-service-finish.md (shortcut names); civiccast/apps/portal-operator/src/screens/SetupScreen.tsx:220-418 (recovery kit panel), 449-538 (First-run defaults), 540-564 (signed-in panel), 566-634 (storage), 1440-1554 (mutations, kit gate), 1583-1639 (sign-in card), 1641-1735 (headings, stale token, cooling down, loopback card), 1753-1935 (notices, Setup complete, recovery form), 1949-2068 (first-admin form); civiccast/apps/portal-operator/src/auth/recoveryKitGate.ts:1-134; civiccast/apps/portal-operator/src/App.tsx:135-144 (returnTo, default /health), 222-238 (kit bounce, 401 bounce), 256 (/ redirects to /setup); civiccast/apps/portal-operator/src/components/shell/TopBar.tsx:90-222 (clock, theme, badge, Sign out); civiccast/apps/portal-operator/src/components/shell/Sidebar.tsx:83-183 (profile card, sections, requiredRoles); civiccast/apps/portal-operator/src/components/AuthRequiredState.tsx:8-10; civiccast/apps/portal-operator/src/api/client.ts:383-390,422-427; civiccast/apps/portal-operator/src/screens/status-language.ts:30-142,207-250; civiccast/apps/portal-operator/src/auth/roles.ts:6-12; civiccast/installer/station_state.py:76,795-870,1347-1356; civiccast/installer/router.py:1088-1097,1100-1130,1211-1264; civiccast/installer/models.py:325-356; civiccast/auth/roles.py:80-98; civiccast/docsite/manual.json (text "has not been published"); civiccast/apps/portal-operator/src/screens/{SystemHealthScreen.tsx:1515,AlertsScreen.tsx:775,ActivityPubScreen.tsx:582,CgBoardDesignerScreen.tsx:533} (page headings) -->
