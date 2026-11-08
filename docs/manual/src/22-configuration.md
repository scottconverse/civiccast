# Configuring the station {#ch-configuration}

This chapter is for the person at the city who owns the CivicCast computer. It walks through every place a station is configured: First Setup and the admin account, the Station Profile, channels and their outputs, loudness, the playout engine, captions, AI models, cable commissioning, the Control Room, custom fields, the paywall, certificates and network exposure, storage, and the environment variables that sit behind all of it. Most of it is done once, before the first public meeting, by someone with the `setup_admin` role. A few things (the environment variables, the registry) need Administrator rights on the Windows computer itself.

## Before you start

You need:

- The station computer, installed and running (see [Installing, first run, upgrading, uninstalling](#ch-installing)). The Windows service is `CivicCastSupervisor` ("CivicCast Native Supervisor").
- A browser on the station computer for first setup, first-admin sign-in and recovery. Staff accounts can sign in remotely only through the configured staff hostname and from an approved network; see the [network access guide](https://github.com/scottconverse/civiccast/blob/main/docs/ops/network-access.md) and [staff accounts guide](https://github.com/scottconverse/civiccast/blob/main/docs/ops/staff-accounts.md).
- Administrator rights on Windows for the registry and file steps in the environment-variable section.
- A plan for what the station does. A public-meetings station (residents watch on the web) needs far less configuration than a station that feeds a cable headend. Cable Commissioning and the headend presets are only needed for the second kind.

Most screens in this chapter live in the sidebar group **Setup**. Almost all of them can be used only by the `setup_admin` role. The first admin you create has every role (the token scope `admin` expands to all five roles), so on a one-person station you can ignore role names. Roles are described in the appendix on roles ([Roles and what each can do](#app-roles)).

Scheduling forms use the time zone in **Station Profile**; reports, exports and some dated controls retain their stated UTC basis. Check the label beside each date or time field before saving.

## Where settings live

Settings are kept in six different places. Knowing which place holds a setting tells you how to change it, how to back it up, and why a change in one place does not move anything in another.

| Where | What is kept there | How you change it |
| --- | --- | --- |
| The database (PostgreSQL, `C:\ProgramData\CivicCast\data\pgdata`) | Each channel's output configuration, the AI model choices, custom fields, the paywall, control-room devices and cues, watch folders, retention rules, schedules, assets | The console screens in this chapter (or the staff API) |
| `station-state.json` and the files beside it | The Station Profile, first-admin and staff accounts and session hashes, recovery-kit state, commissioning progress, network settings, provider credentials (`provider-credentials.json`), proof references (`provider-proof-evidence.json`), `tester-ops-state.json` | Setup, Staff accounts, Network Settings and Station Profile screens |
| `C:\ProgramData\CivicCast\config\station-backups.json` | Backup destination, selected media roots, schedule, retention and last-run status | Station backup and restore screen; unattended passphrase is stored separately in the OS-protected secret vault |
| The service's environment (the registry value `Environment` on the service) | Tuning and switches that have no screen: timeouts, worker on/off switches, cache sizes, proxy and CORS rules, the control-room sidecar address, TSDuck location | You, by hand, in the registry (see [Environment variables](#configuration-env)) |
| The Windows credential store ("keyring") | AI provider API keys, control-room device passwords | The AI Models and Control Room Setup screens, or `civiccast model set-provider-key` |
| Fixed or selected by the installer and network settings | The folder layout under `C:\Program Files\CivicCast (Native)` and `C:\ProgramData\CivicCast`, a loopback-only control-plane address (default `127.0.0.1:8000`), the Ollama address `127.0.0.1:11434` | Choose `127.0.0.1` or `::1` in Network Settings; the application cannot bind directly to a LAN or public address |

The service's `station-state.json` and related profile files live under its configured service profile. The installer sets that profile to `C:\ProgramData\CivicCast\profile`; when migrating a default LocalSystem installation, it copies the old profile and imports CivicCast Credential Manager entries before changing service identity. See the [restricted service identity guide](https://github.com/scottconverse/civiccast/blob/main/docs/ops/restricted-service-identity.md) for migration details.

> **Backup:** Include the service profile and its state files in the station backup. Current native installations use `C:\ProgramData\CivicCast\profile`; older Beta 11 LocalSystem installations could use a profile outside `ProgramData`. Verify the configured path when recovering an older installation. See [Running it day to day](#ch-operations).

The folder layout is described in the installing chapter. The two folders that matter for configuration are `C:\ProgramData\CivicCast\data\uploads` (uploaded media and recordings, in a `recordings` sub-folder) and `C:\ProgramData\CivicCast\data\egress` (the playout work area: prepared copies of programs, slates, the live web-preview segments, the conform cache).

> **Known issue (beta.11):** The two folders above cannot be moved with a setting. The service sets `CIVICCAST_UPLOAD_DIR` and `CIVICCAST_EGRESS_WORK_DIR` for the control plane itself, derived from the `PROGRAMDATA` folder, and those values win over anything you put in the service environment. Plan disk space on the drive that holds `C:\ProgramData` (see [Planning your station](#ch-planning)). The Station Profile has three editable storage folders; they do not move anything either (see [Station Profile](#configuration-profile)).

## Run First Setup and create the admin account {#configuration-first-setup}

First Setup is at `/operator/#/setup` in the local operator console (`http://127.0.0.1:8000/operator/`). It creates the first administrator and one-time recovery kit; first-admin sign-in and recovery remain local-only. Staff accounts use the separate `/operator/#/login` page and can use the configured staff hostname from approved networks. See the [staff accounts guide](https://github.com/scottconverse/civiccast/blob/main/docs/ops/staff-accounts.md).

1. On the station computer, open the console and choose **First Setup** in the sidebar.
2. Under **Durable storage**, click **Prepare storage**. This creates or activates the local database for meeting records, captions, summaries and subscriptions. If the service was started with a `DATABASE_URL` the button does nothing and just reports status.
3. Fill in **Station name** (up to 120 characters), **Admin display name** (up to 120), **Admin username** (up to 80), **Admin password** (at least 12 characters; a counter shows "(n/12)") and **Confirm admin password**.
4. In **Where will you keep the recovery kit?** type a note such as "printed, in the clerk's safe". This is a free-text reminder. It is not a folder and nothing is written there.
5. Optionally fill in **Resident portal URL**. Use a complete `http://` or `https://` address without a username or password. A blank box clears the saved address; invalid addresses are refused.
6. Click **Create first admin**. The **Recovery kit ready** panel appears with eight one-time recovery codes. The rest of the console is locked until you confirm the kit.
7. Click **Print kit** or **Save kit**, tick **I have saved or printed this kit...** and click **Continue to the console**.

After step 7 you see a green **Setup complete** card, the **First-run defaults** card and the setup tools (camera or test media, backup destination, storage estimate, provider setup).

What the form does not show you, but the server chooses for you when you click **Create first admin**:

| Setting | Value chosen | What it means |
| --- | --- | --- |
| Number of channels | 3 | The channels `public`, `education` and `government` |
| Default channel | `government` | Used when the first-run sample content is added |
| Time zone | `local` | Until you set an IANA zone, `local` means UTC in the running service (see [Station Profile](#configuration-profile)) |
| Operation mode | `test` | Shown as "Test mode" on the First-run defaults card |
| Sample content | on | A short sample video is added to the library in the background |
| Starter schedule | on | A starter schedule is created with the sample content |

> **Known issue (beta.11):** The **First-run defaults** card shows Test mode, sample content and the starter schedule, but First Setup has no control to turn those defaults off. Remove the sample video and starter schedule item from Assets and Schedule before a public meeting if you do not want them shown to residents (see [Before the meeting](#ch-before-meeting)).

> **Known issue (beta.11):** The **Save kit** button downloads `civiccast-recovery-kit-<kit id>.txt` and that file contains the admin password in plain text as well as the eight codes. Keep it off the CivicCast computer, off email and off any synced cloud folder. The kit you regenerate later (Station Profile, Security) contains the codes only.

### Sign in again, and when the password is lost

At `/operator/#/setup`, the station computer shows **Admin sign-in** and **Use recovery code** for the first administrator. A correct password signs in that browser and opens Readiness (`/health`) or the page you were returning to; it does not sign out other browsers. Ordinary staff use `/operator/#/login`; remote sign-in is available only through the configured staff hostname from an approved network.

Too many wrong passwords are limited. The default is 10 failures per 60 seconds for each route and each connecting address (`CIVICCAST_AUTH_RATE_LIMIT`, `CIVICCAST_AUTH_RATE_LIMIT_WINDOW_SECONDS`). A correct password always gets through; a wrong one burns the budget.

To use a recovery code, click **Recover account**, enter one code and a new password (at least 12 characters), and click **Recover account** a second time. The second click is the real action.

> **Warning:** Using a recovery code permanently consumes it. If the password and all eight codes are lost, an elevated local administrator can use the offline `civiccast admin reset-password` procedure in [Security and privacy](#ch-security). Do not reset first setup to recover a password. While signed in, you can also regenerate the recovery kit.

To make First Setup run again on a station that is already set up, the service must have the environment variable `CIVICCAST_ALLOW_FIRST_ADMIN_RESET=1`. Without it, a second First Setup is refused with "First-admin setup is already complete. Use the recovery flow or set CIVICCAST_ALLOW_FIRST_ADMIN_RESET=1 for an intentional local reset." Treat this variable as a station reset. We did not trace everything the reset replaces, so do not use it on a station with real records without help from support.

### The rest of the Setup page

Once signed in, the Setup page has four tools:

- **Camera or test media** has six choices (USB webcam or HDMI capture, phone or tablet broadcast app, hardware encoder or AV system, NDI source, bundled sample video, upload a short test video). **Save meeting source** creates a live source on the channel `government` (the channel is fixed in the screen). **Create sample media** makes a 2-second sample video and adds it to Assets. The stream address box is for an address such as `rtmp://...`; do not put camera passwords in it.
- **Backup destination** with **Verify backup**. See the warning below.
- **Storage and viewing estimate** is arithmetic in your browser: hours per meeting times meetings per month times 2 gives gigabytes stored. Nothing is measured.
- **Provider setup** holds the cloud and archive providers. See [Provider setup and keys](#configuration-providers).

> **Note:** The Setup card's **Verify backup** checks whether CivicCast can write, read and delete a small test file in the selected folder; it does not create or schedule an encrypted station archive. Use **Station backup and restore** to create, verify and schedule `.ccbackup` archives, and review the [station backup guide](https://github.com/scottconverse/civiccast/blob/main/docs/ops/station-backup.md) before relying on them. The destination check runs under the service account, so use a local path or share that account can write to; a drive letter mapped only in your own session is unavailable to the service.

## Set the Station Profile {#configuration-profile}

The Station Profile shows the station's identity and what this computer actually has, and holds two security actions. Only `setup_admin` can save; the roles `meeting_operator` and `support_admin` can read it.

| Field | What it changes | What it does not change |
| --- | --- | --- |
| **Station name** (required, up to 120 characters) | The name shown in the console and on the community-board graphic that appears between programs when no channel branding is set | It is not the public site's title; channel and app names are set on Channels |
| **Timezone (IANA name, or "local")** | The zone used to enter, recur, preview and display schedules and auto-schedule dayparts, and to set the end-of-day deadline for retention terms | It does not change stored UTC timestamps in reports or exports |
| **Default channel id** | The channel the first-run sample content uses; unknown ids are healed to `government` when read | It does not change which channel residents see first |
| **Public base URL (optional)** | Stored and displayed | See the Known issue below: nothing reads it, and it cannot be cleared here |
| **Media library / Recordings / Backups** folders | The three paths stored in the profile; each Copy path button copies one | See the Known issue below: they do not move or choose where any file is written |
| **Show live captions on air** | The operator switch for live captions (the live speech-to-text tap on every on-air channel and the CEA-708 caption leg in the video). Turning it off stops recognition and drains queued audio on the next tap-worker scan; turning it on resumes recognition on the next scan. The caption routing in the video changes when each channel next starts | It does not affect captions on published recordings (those are the offline job, see [Captions](#configuration-captions)). New station profiles default to off |

The time zone is free text. Choose an IANA name such as `America/Denver`. The default `local`, a blank value, or an unknown name falls back to UTC; an unknown name is logged as a warning. Set the station's real zone here before creating schedules. A zone change does not rewrite already-generated occurrences; check the future calendar after saving.

> **Known issue (beta.11):** The three storage-folder fields are shown with a Save button, but the saved paths do not choose where media, recordings or backups are written. Editing them does not move any file. Use Assets to find recordings. The environment variables `CIVICCAST_STATION_MEDIA_LIBRARY`, `CIVICCAST_STATION_RECORDINGS` and `CIVICCAST_STATION_BACKUPS` override the displayed values; they do not move files either.

> **Known issue (beta.11):** The Station Profile's **Public base URL** field is stored but the program uses the service variable `CIVICCAST_PUBLIC_BASE_URL` to build RSS and federation addresses. Leaving the field blank does not clear a value already saved. The resident-portal preview link uses `CIVICCAST_RESIDENT_PORTAL_URL`.

The **Station box profile** card is read-only. It asks the program what this computer has: CPU, RAM, the recommended tier, the playout engine and its readiness, the clock (a row is yellow with "System clock is not NTP-synced." if the clock is not synchronized), DeckLink or SDI, TSDuck, the backup destination, AI model memory, and a "Cable-grade OS" line. Its readiness colors are shown as the raw words GREEN, YELLOW and RED. The card always asks for the `public-meetings` profile; there is no selector.

### Security actions

- **Sign out other sessions** ends every other station-local console session except this browser, after a confirmation. It requires a signed-in station session with the `setup_admin` role; database and environment API tokens cannot use it. The **Staff accounts** screen also lets a setup admin review and revoke individual sessions.
- **Regenerate recovery kit** creates eight new codes and invalidates the old ones at once. The new codes exist only on that page. Save or print them before you leave the page, tick the confirmation and click **Done**. If you leave early the codes are lost, and the old ones no longer work.

### Recorded captions

The **Recorded captions** card on Station Profile controls captions for saved
recordings separately from **Show live captions on air**. A setup admin can edit
these settings; meeting operators and support admins can read them.

1. Choose **Enable recorded captions**. Turning this off stops new caption jobs
   from being queued and pauses existing work until processing is enabled again.
2. Keep English selected and choose whether to include **Spanish**, translated
   from reviewed English. Clear Spanish for English-only operation.
3. When Spanish is selected, choose **Wait for every selected language** to
   attach both reviewed tracks together, or **Attach English after its review**
   to make reviewed English available while Spanish translation and review
   continue. The job remains active until the selected Spanish work finishes.
4. Click **Save recorded-caption settings**. The card reports save failures;
   do not assume an unsaved change has reached the worker.

Only reviewed text is attached. These choices do not change live-caption
performance thresholds or claim that an uncaptioned recording is captioned.
Defaults are enabled processing, English and Spanish, and waiting for every
selected language.

## Configure channels, outputs and sinks {#configuration-channels}

### How a channel is configured

A channel has an **egress configuration**, stored in the database. "Egress" means the signal CivicCast sends out. The configuration holds:

- **enabled**: whether the channel may be started.
- **auto_start** ("Keep this channel on air"): whether the automation starts the feed after a restart or crash.
- **allow_software_fallback** ("Allow software (CPU) encoding fallback"): see [Encoders](#configuration-encoders).
- **fill_policy**: what fills the gaps between programs, `slate` (the plain slate) or `bulletins` (approved community bulletins).
- **slate message** (1 to 240 characters), optional **NDI output name** and **SDI output device**.
- **sinks**: one or more output targets (below).
- **loudness target and tolerance** and the **canonical profile** (picture size, frame rate, codec, bitrates): see [Loudness](#configuration-loudness).
- the **lower-third banner** switch and text (up to 240 characters).

A new channel has no configuration until you create one. The Start button is disabled with "No outgoing-feed configuration for &lt;id&gt;. Apply a headend preset or the local rehearsal preset first." The normal way to create one is to apply a preset (next section). Creating it by preset gives the channel `enabled: true` and the slate message "CivicCast is preparing the channel."

The Channels screen (**Run Meeting** group) edits some of this: **Channel startup and repeat** (auto start, software fallback, fill policy, slate message, NDI name, SDI device) and **Cable headend delivery** (the presets). These panels apply only to the channel currently selected above them. Check its channel ID in the automation panel before saving; switching channel selects a different saved configuration, not a station-wide setting. It does not edit individual sinks, loudness numbers or the picture profile. Those can be set only through the staff API (`PUT /api/staff/egress/channels/{id}/config` with a full configuration, `setup_admin` only; see [Appendix: API](#app-api)).

> **Warning:** Changes to a channel's outputs do not reach a channel that is on air. A running pipeline reads its sinks, encode profile and loudness target only when it is built. A preset or a saved configuration lands at the next Stop and Start. **Apply headend preset** on a channel that is standing by on its slate restarts it at once, and every output (the cable feed included) drops for a few seconds while the pipeline rebuilds. On a channel that is airing a program nothing is interrupted, and the screen says "Preset saved. Restart the channel to put it on air."

### Apply a headend preset

1. Open **Channels** and select the channel.
2. In **Cable headend delivery**, choose a **Headend preset**.
3. Type the **Destination** (`udp://address:port` for network presets; a drop-folder path for the file preset; optional folder for the web preview).
4. Optionally set **Mux rate override (kbps)**. Tick **Keep the channel's other outputs alongside the headend feed** if the channel already has another output you want to keep.
5. Click **Apply headend preset** (or **Enable web preview**) and confirm the dialog.

You should see "Preset applied and going on air", "Preset saved. Restart the channel to put it on air", "Nothing to change" or "Preset saved for the next start", with a sentence of detail.

The presets in the program (each carries its sources and the note "Built from published vendor documentation; not field-proven against a real cable headend until the first-station beta."):

| Preset (screen label) | Picture | Video / audio codec requested | Mux rate | Transport |
| --- | --- | --- | --- | --- |
| Generic CBR SPTS over UDP | 1280x720, 30 fps | H.264 at 5,000 kbps, GOP 30 / AC-3 192 kbps | 8,000 kbps | UDP unicast |
| Comcast MTD - SD | 720x480, 30 fps | MPEG-2 at 3,180 kbps, GOP 15 / AC-3 192 kbps | 3,750 kbps | UDP multicast |
| Comcast MTD - HD | 1920x1080, 30 fps | H.264 at 10,000 kbps, GOP 30 / AC-3 384 kbps | 12,000 kbps (a placeholder; use your carriage agreement's rate) | UDP multicast |
| TelVue HyperCaster - IP transport stream input | 1280x720, 30 fps | H.264 at 5,000 kbps, GOP 30 / AC-3 192 kbps | 8,000 kbps; ports 1024 and up | UDP unicast |
| Harmonic Spectrum - transport stream ingest | 1920x1080, 30 fps | H.264 at 8,000 kbps, GOP 30 / AC-3 192 kbps | 10,000 kbps | UDP unicast |
| Leightronix UltraNEXUS - file handoff | 1280x720, 30 fps | H.264 at 8,000 kbps, GOP 30 / AC-3 192 kbps | none (file drop) | A file in a watched folder |
| Local rehearsal (web preview, HLS) | the channel's own profile is left alone | not applied | none | A local folder served to the resident portal |

Applying a cable preset replaces the channel's whole canonical profile and sets the channel loudness target to -24 (see [Loudness](#configuration-loudness)). Applying the web-preview preset adds only an `hls` output and leaves the encode profile and loudness alone.

> **Known issue (beta.11):** The native GStreamer engine maps MPEG-2 to its H.264 fallback and AC-3 to its AAC fallback. The Comcast MTD - SD preset therefore does not produce MPEG-2, and the cable presets do not produce AC-3. This is current source behavior; no real cable-headend output was verified for this release. Ask your cable operator whether the codecs actually emitted are acceptable, and complete [Cable Commissioning](#configuration-commissioning) before relying on a preset.

### Sink kinds {#configuration-sinks}

A **sink** is one output of a channel. The native station uses the GStreamer engine and supports the sink kinds below. Output destinations and any required credentials must be configured for the receiving service.

| Kind | URI form | What it is for | Notes from the code |
| --- | --- | --- | --- |
| `udp-ts` | `udp://host:port` (a port is required) | A headend or encoder that takes a transport stream over UDP | The cable preset creates this. Datagrams are 1316 bytes (7 transport packets). A constant `-muxrate` is added as an allowed extra argument. Passes through the TS relay when TSDuck is present (below) |
| `local-ts` | `udp://...` or `file://...` | A local transport-stream feed for another program on the same computer | |
| `file` | A path or `file://` URI | Writing a transport stream to a file | The file preset creates this |
| `srt` | `srt://...` | An SRT receiver | See the secret note below |
| `hls` | A local folder path or `file://` | The resident-portal web preview: a playlist and 2-second segments written to the folder | Delivered by a supervised ffmpeg helper because the shipped GStreamer has no HLS element. The folder must be inside the HLS root (below) |
| `sdi` | Not a network address | SDI output through a supervised relay using your own DeckLink-capable ffmpeg | Needs `CIVICCAST_SDI_FFMPEG` |
| `rtmp` | `rtmp://` or `rtmps://` | FLV through `rtmp2sink` | Configure the destination and stream-key handle; the worker resolves the secret when opening the output |

Each sink also has: a **label** (1 to 80 characters, unique within the channel), **latency** (0 to 60,000 ms, default 2,000), **extra output arguments** (only `-mpegts_flags`, `-muxrate`, `-pkt_size`, `-flush_packets` and `-max_delay` are allowed; shell characters and extra inputs are rejected), a **loudness regime** and optional target and tolerance, and an **EAS tone strip** switch (default on; it strips the 853/960 Hz attention tone on internet outputs and leaves a cable feed untouched).

Secrets never go in the URI. A URI that carries a passphrase, key, token or password is refused at save time. For an SRT passphrase the sink carries a `secret_ref`. The native automation resolves that reference by reading the **environment variable of that name** from the service's environment. So an SRT sink with `secret_ref: SRT_PASSPHRASE_GOV` needs `SRT_PASSPHRASE_GOV=<passphrase>` in the service environment (see [Environment variables](#configuration-env)).

The **HLS root.** The public web preview is served without sign-in from whatever folder the channel's `hls` sink names, so the folder is confined. By default it must be under the egress work folder (`C:\ProgramData\CivicCast\data\egress\live-hls\<channel>` when you leave the destination blank). `CIVICCAST_LIVE_HLS_ROOT` names a different root; every web-preview folder must then be under it. A drive root, a Windows system folder or a network share name is refused.

### The transport-stream relay {#configuration-ts-relay}

For each `udp-ts` sink, CivicCast can run a **TS relay**: a long-lived TSDuck `tsp` process between the encoder and the headend. The encoder sends to a local port (`127.0.0.1`, starting at 17800); the relay fixes the continuity counters and the PCR clock and forwards to your real destination from one pinned source port (the local port plus 1000). The point is that a plan change or encoder relaunch does not look to the headend like a new stream. The relay touches only `udp-ts` sinks.

The switch is `CIVICCAST_TS_RELAY`: `auto` (default; relay when `tsp` is found, otherwise pass straight through with a warning), `on` (log an error when `tsp` is missing, still pass through) or `off`. Any other value is an error. `CIVICCAST_TS_RELAY_BASE_PORT` moves the first local port. A separate helper feeds the `hls` sink and uses ports from 18000 (`CIVICCAST_HLS_RELAY_BASE_PORT`).

TSDuck is found in this order: `CIVICCAST_TSDUCK_PATH` (a folder or the file), the CivicCast-managed copy (`CIVICCAST_TSDUCK_HOME`, else `%LOCALAPPDATA%\CivicCast\tsduck` of the service account), the copy that the server-binaries pack ships (`tsp.exe` under `packs\native-server-binaries\payload\tsduck\bin`; the Gate A run found it there), then the PATH. The **Enable cable verification** button on Channels downloads TSDuck on demand, and the download has no confirmation step. We did not test the button on a station whose pack copy is present; by the lookup order it should not be needed there.

> **Known issue (beta.11):** The **Channels** screen's text for the TSDuck download says "Turn this on and CivicCast downloads the free TSDuck toolkit". The control is a download button with no confirmation and it needs an internet connection. Press it only on a station that is allowed to download software, and not during a meeting. The source does not state the download size.

### Encoders: hardware and software {#configuration-encoders}

The picture is encoded by a GStreamer encoder named from the profile's codec. The default `h264` (and `libx264`, `openh264`) maps to **`openh264enc`**, a software (CPU) encoder bundled with CivicCast. Other mapped names are `x264enc`, `x265enc` and, for hardware, `nvh264enc`/`nvh265enc` (NVIDIA) and, on Windows, `mfh264enc`/`mfh265enc` (Media Foundation, which stands in for the VAAPI names).

On Windows CivicCast runs a pre-flight before each start:

- A software encoder is used as is. Nothing is checked.
- A hardware encoder that is present is used. HEVC works through Media Foundation.
- A hardware encoder that is **absent**: for H.264, if **Allow software (CPU) encoding fallback** is ticked the channel encodes on the CPU with `openh264enc` and logs "No hardware video encoder was found; 'allow_software_fallback' is on, so this channel is encoding on the CPU (openh264enc). This is slower and may not keep up with live on a weak CPU." If it is not ticked, the start is refused with "No hardware video encoder was found on this machine. To broadcast on the CPU instead (slower), turn on 'Allow software (CPU) encoding fallback' in this channel's settings, then start the channel again. HEVC/H.265 needs hardware and is not available this way."
- HEVC without hardware is always refused ("There is no software HEVC in this build").

`openh264enc` runs as four slices on four threads so that high-motion pictures stay faster than real time.

> **Note:** The software-fallback checkbox only matters when the channel's configured codec names a hardware encoder that this computer does not have. With the default profile and the presets above, the channel already uses the software `openh264enc` and the checkbox changes nothing. It is off by default, so a configuration that asks for hardware fails loudly instead of silently dropping to the CPU.

Hardware **decoding** is off by default and the CPU decoders are preferred. `CIVICCAST_GST_ALLOW_HARDWARE_DECODE=1` (values `1`, `true`, `yes`, `on`) turns hardware decoders back on for the playout worker.

If the GStreamer files on the computer are corrupt and cannot be repaired in place when the service starts, the service falls back to the older FFmpeg concat engine so the channel keeps airing, raises an operator alert and marks the channel degraded. You do not choose this; it is automatic.

### Loudness and the leveling ride {#configuration-loudness}

The **Prepared media** loudness value in Channels comes from the last recorded
source/preparation measurement. It is not a rolling or continuous reading of the
currently emitted channel, and an input measurement can remain visible after a
normalization step. Output targets describe the configured processing, not a
measured compliance result. Use an actual output measurement when commissioning
the feed; do not read this value as proof that the current program meets its target.

Each channel has one **loudness target** (default -16 LUFS) and **tolerance** (default 2 LU). Each sink can name its own **regime**:

| Regime | Target | Standard label |
| --- | --- | --- |
| `streaming` | -16 | Streaming -16 LUFS (ITU-R BS.1770) |
| `atsc-a85` | -24 | ATSC A/85 -24 LKFS (CALM Act) |
| `ebu-r128` | -23 | EBU R128 -23 LUFS |
| `inherit` (default) | the channel's target | |

An explicit target on a sink wins over its regime. A sink whose target differs from the channel's gets its own re-encode of the audio; a sink equal to the channel's copies the program audio. The Channels screen's **Loudness** card shows this plan read-only. It is visible to `setup_admin` and `support_admin` only. To set a different target or regime you apply a preset (cable presets set the channel to -24 and the headend sink to `atsc-a85`; the web preview sets that sink to `streaming`) or use the API.

The level of each program is set when CivicCast prepares ("conforms") it, before it airs. A public meeting is not steady: the chair, a speaker who steps back and a public-comment microphone can sit 15 to 20 LU apart inside one recording. A single average gain cannot land every stretch at the target. CivicCast therefore runs a slow, speech-following gain **ride** instead of a one-pass normalizer. Its numbers are fixed in the program, not settings: it follows speech over a 45-second window, moves the gain at most 0.5 dB per second, may lower a passage by 6 dB and raise it by 18 dB, caps the lift in a quiet gap at 8 dB, and limits the true peak at -1.5 dBTP (it then checks the finished file and re-encodes once with more headroom if the peak is too hot). The acceptance check it is built against is every 240-second window within 0.9 LU of the target and the whole program within 0.5 LU.

> **Known issue (beta.11):** The limiter in the loudness ride does not guarantee a cap on how much quiet passages are raised; a historical recording measured a quiet gap lifted by about 13.7 dB. Treat that figure as a past measurement, not a beta.11 measurement. If the ride cannot run (for example numpy is missing, or ffmpeg fails), CivicCast logs one warning and uses a single-pass `loudnorm` conform to the same target instead; the channel airs and only the leveling is missing.

### The playout engine: what you can and cannot set {#configuration-engine}

On a native station these are fixed by the service and a value you put in the service environment is ignored: the engine (`CIVICCAST_EGRESS_ENGINE` is set to `gstreamer`), caption embedding (`CIVICCAST_EGRESS_EMBED_CAPTIONS=1`), the work and upload folders, and the portal folders. See [Environment variables](#configuration-env) for what the service forces.

What you can tune (all optional, all in the service environment; defaults are what the code uses when the variable is not set):

- **Channel automation.** `CIVICCAST_CHANNEL_AUTOMATION` is `inline` (default) or `off`. It checks every channel every `CIVICCAST_CHANNEL_AUTOMATION_POLL_SECONDS` (default 2.0). With **Keep this channel on air** ticked, a channel with no running process gets a start command, retried every 30 seconds until it is live.
- **Preparation.** Each program is prepared in segments of at most `CIVICCAST_GSTREAMER_SOURCE_SEGMENT_SECONDS` (default 1,800 s, 30 minutes). One preparation step may run at most `CIVICCAST_EGRESS_PREPARATION_TIMEOUT_SECONDS` (default 300 s). The cache of whole prepared programs is limited by `CIVICCAST_CONFORM_CACHE_GB` (default 60) and the folder of prepared plans in use by `CIVICCAST_PREPARED_PLAN_DIR_BUDGET_GB` (default 5.0).
- **Starting and reloading.** `CIVICCAST_EGRESS_SEAMLESS_RELOAD` is on unless set to `0`, `false`, `no` or `off`; turning it off makes the encoder restart at every plan boundary (for diagnosis only). The worker waits for the picture to start `CIVICCAST_GST_PREROLL_TIMEOUT_S` seconds (default 30, clamped to 5-45) and for the first output `CIVICCAST_GST_FIRST_OUTPUT_TIMEOUT_S` (default 45, clamped to 10-120). A running worker that makes no output for `CIVICCAST_STALL_TIMEOUT_S` (default 10) quits so the service restarts it. A reload waits `CIVICCAST_RELOAD_TIMEOUT_S` (default 10) for the new source and `CIVICCAST_RELOAD_COMMIT_TIMEOUT_S` (default 15; clamped 3-120 inside the engine) for the switch.
- **NDI and SDI.** CivicCast's bundled ffmpeg cannot contain the NDI muxer (a licensing limit). To publish a channel as NDI, set `CIVICCAST_NDI_FFMPEG` to your own NDI-capable ffmpeg and put a name in **NDI output name**. For SDI set `CIVICCAST_SDI_FFMPEG` to a DeckLink-capable ffmpeg and a device in **SDI output device**. `CIVICCAST_NDI_RELAY` and `CIVICCAST_SDI_RELAY` accept `inline` (default) or `off`. Without the variable, the console's readiness check says to set it.

> **Operator stops:** Stop and Finish current item, then stop suppress automatic starts for the current service session. An explicit Start clears that suppression. Disable **Keep this channel on air** if the channel must also stay off after a service or computer restart. Enabling that setting can start an eligible dark channel without another Start command.

### Repeat the published schedule

In **Channels → Channel startup and repeat**, enable **Repeat the published sequence** and click **Save automation settings**. It repeats the last contiguous group of published premiere items, identified by **In repeat sequence** in the published playout plan. A gap greater than one second starts a new group; older groups and gaps are not replayed. Use second-level start times and durations for adjacent programs.

Repeat is separate from **Keep this channel on air** (automatic startup). The default for a new station is finite playback. Saved repeat settings apply to the next prepared plan without editing an environment variable or restarting the service. An old `CIVICCAST_SCHEDULE_LOOP` value is a compatibility default only until this channel setting is saved. Auto-schedule uses the station time zone. See the two-recording recipe in [Before the meeting](#ch-before-meeting).

## Configure captions {#configuration-captions}

CivicCast captions in two different ways and they are configured separately. Native live captions use Whistle on the CPU by default; recorded-media captions use the separate Whisper workflow.

| | Live captions | Offline captions on recordings |
| --- | --- | --- |
| What it is | A real-time speech-to-text tap on each on-air channel's audio, with the text embedded in the video as CEA-708 captions | A job that captions a published recording after the meeting |
| Switch | **Show live captions on air** (Station Profile); the tap rechecks it on each scan and video routing changes at the next channel start | **Enable recorded captions** in Station Profile; disabling pauses caption work and stops new jobs being queued |
| Where results go | Into the stream as captions; live history is bounded and ordinary live operation does not create permanent per-cue review records or evidence WAVs | To the review queue; attach after all selected languages are reviewed, or attach reviewed English first if that completion option is selected |
| Engine | Whistle CPU primary by default, with Whisper fallback; service environment can select Whisper directly | Whisper speech recognition and the translation model |
| Needs | CPU for Whistle and video playout; no CUDA GPU is required | Whisper; a translation model is needed only when Spanish is selected |

### The caption tiers

The speech model comes in two **tiers**:

- **Medium** (`faster-whisper-medium`) is the required Whisper floor for fallback and recorded captions. It is staged by the signed station pack and runs on the CPU.
- **Large-v3** (`faster-whisper-large-v3`) is optional. The first-run wizard offers it as a checkbox and may preselect it when an NVIDIA card with at least 8 GB of video memory is found. CUDA runtime files are optional and accelerate Whisper only; they are not required by Whistle.

At service start the station validates the staged Whisper tier and self-test receipt. The **saved selection** in AI Models controls the verified Whisper model used when a runtime is created. An activation default cannot silently override a different saved choice. The native live default remains Whistle regardless of whether Large is installed; Whistle's assets are resolved independently of the Whisper model directory.

The live caption runtime is created when the service starts. **AI Models** shows its service-start fallback tier separately from the saved selection. If you change the selection, restart CivicCast during a maintenance window to apply it to the live runtime. New recorded-caption jobs use the saved selection when they start; an already-running job keeps its existing model.

The device is chosen the same way. `CIVICCAST_WHISPER_DEVICE` in the **service** environment, if set, wins in either direction (`cuda` or `cpu`), with `CIVICCAST_WHISPER_COMPUTE_TYPE` (default `float16` for cuda, `int8` for cpu). Otherwise the station uses the GPU only when an NVIDIA adapter with at least 8 GB is found **and** both CUDA runtime libraries are staged; anything else is CPU with `int8`.

> **Note:** The AI Models caption selection concerns Whisper tiers. It does not change the native live engine from Whistle. The installer's optional-component choices control which optional models are downloaded.

### Live captions: what the settings change

**Live runtime:** Native stations use Whistle on CPU as the primary engine, with Whisper as fallback. Whistle inference is serialized by one station-wide lock across channels. The first recognition is published immediately, including low-confidence text; overlap is deduplicated without requiring two readings to agree. Whistle requests have a 30-second deadline. On failure, Whisper supplies backup captions while a fresh Whistle process is tried after an initial 30-second cooldown. Further failed recovery attempts back off to 60, 120, 240 and at most 300 seconds. Only one recovery probe runs at a time, and successful recognition restores that channel to Whistle automatically. Whisper fallback requests allow 120 seconds by default; set `CIVICCAST_WHISPER_FALLBACK_TIMEOUT_SECONDS` to a finite value from 1 to 600 seconds to change that deadline. A failed Whisper child is closed and replayed once immediately; a repeated replacement failure starts a 30-second retry cooldown. `CIVICCAST_LIVE_CAPTION_ENGINE=whisper` selects Whisper as the primary engine instead.

Live captions are produced by the caption tap. The native station turns it on (`CIVICCAST_CAPTION_TAP=inline`) unless the service environment says `CIVICCAST_CAPTION_TAP=off`, in which case the tap does not run at all. The console switch is the safe-direction override: `off` in the environment forces live captions off whatever the profile says, but no environment value can turn them on against a profile that is off. When you turn the profile switch off, queued audio drains and speech recognition stops on the next tap-worker scan; caption routing is removed from the video when each channel next starts. When you turn it on, recognition resumes on the next scan, and each channel needs to start again before its video includes captions.

Whistle processes each new five-second segment with one second of prior context by default, rather than retranscribing five seconds of overlap on every call. Each channel keeps all new input in an ordinary completed window. The native engine remains serialized across channels; this reduces duplicate work, not the need for sufficient CPU. Explicit overlap settings still apply. A bounded three-channel trial supports this capacity correction; it is not a guarantee of transcript accuracy or a long soak.

For the live tap, audio is collected in 5-second segments (`CIVICCAST_CAPTION_TAP_SEGMENT_SECONDS`, default 5.0), scanned every 2 seconds (`CIVICCAST_CAPTION_TAP_POLL_SECONDS`) and kept in a bounded queue. If recognition falls behind, old working audio can be shed so playout retains priority. Whisper-specific CPU thread and worker settings control its runtime; they do not override Whistle's station-wide inference lock. The playout worker has priority over caption work.

A separate proof loop decodes the emitted stream, compares the captions it finds with the expected ones, and only then marks a channel's captions "on" (a fresh pass is required; otherwise the Channels screen shows "Not verified"). It runs every 30 seconds with a 15-second limit.

> **Known issue (beta.11):** The poll and timeout of that proof loop are read from the **one-C** variable names `CIVICAST_CAPTION_PROOF_POLL_SECONDS` (default 30) and `CIVICAST_CAPTION_PROOF_TIMEOUT_SECONDS` (default 15). The two-C spellings `CIVICCAST_...` do nothing for these two. This is the reverse of the rule everywhere else (see [the one-C spelling trap](#configuration-one-c)).

### Offline captions and Spanish

When recorded captions are enabled and the recording has a stored source file, publication queues a caption job. It transcribes in 30-second chunks (`CIVICCAST_OFFLINE_CAPTION_CHUNK_SECONDS`), polls every 60 seconds, retries after 300 seconds and permits 4 attempts by default. A reviewer approves the English cues. If Spanish is selected, the translation model creates Spanish cues for separate review. **Wait for all selected languages** attaches tracks after those reviews; **Attach English after its review** allows English to attach while Spanish work continues. The recording remains public while caption work proceeds.

> **Settings:** Use **Station Profile > Recorded captions** to disable recorded captions, select English only, or choose English-first completion. Legacy `CIVICCAST_OFFLINE_CAPTION_JOB=off` and `CIVICCAST_OFFLINE_CAPTION_SPANISH=0` are accepted defaults before profile settings are saved; they do not inherently prevent startup. Saved station settings take precedence. Spanish requires a working translation model only when selected.

### Caption evidence and retention

Live captioning uses `C:\ProgramData\CivicCast\data\caption-tap` as a temporary work area. Ordinary live operation does not create permanent review rows or evidence WAV clips for each live cue. Consumed audio chunks are deleted after processing; only the overlap needed for the next recognition is retained in memory. With Whistle, each channel retains at most 42 waiting completed five-second segments (210 seconds), plus in-flight inputs and the segment being written. The bound follows Whistle's finite processing grace. Other runtimes without that grace use a 12-segment working-file ceiling. This file ceiling is separate from the smaller backlog threshold used to detect sustained overload.

The live caption window is limited to the most recent 300 seconds and at most 512 cues. Delivery tracking follows that window instead of accumulating for the whole on-air session. Archive-wide caption review/evidence discovery is not a live-caption or broadcast-readiness prerequisite. These are implementation bounds, not controls on the configuration screen.

Original recordings, archived caption tracks and the recorded-caption review workflow are unchanged. Review recorded captions against the recording workflow; ordinary live captioning does not create an unattended review queue.

**Historical beta.10 behavior:** The older live path retained raw chunks for cleanup and created review evidence. Beta.11 ordinary live captioning does not create that permanent review/evidence archive or require archive discovery for readiness. This does not change original recordings or the recorded-caption review workflow.

## Configure AI models {#configuration-ai}

The AI Models screen (Setup group; `setup_admin` changes, `meeting_operator` reads) chooses the model behind three jobs. The default for each is a model that runs on this computer. The local AI engine is **Ollama 0.40.13**, started and watched by the CivicCast service on `127.0.0.1:11434`.

| Job | Default | Other choices |
| --- | --- | --- |
| Whisper captions (live fallback and recorded media; optional direct live-primary mode) | `whisper-medium-faster` (needs 4 GB of memory) | `whisper-large-v3-faster` (8 GB) |
| Summary | `gemma4-12b-ollama` (16 GB) when the computer has a GPU and at least 16 GB of RAM; otherwise `gemma4-e4b-ollama` (8 GB) | `gemma4-31b-cloud` (Ollama Cloud), `gemini-2.5-flash-openrouter` (OpenRouter). Check the provider's current pricing and terms before enabling a hosted model. |
| Translation (English to Spanish) | `translategemma-4b-ollama` | `gemma4-31b-cloud` |

The rule behind the summary default is measured, not guessed. On a 32 GB computer with no graphics card the 12B model took 366 seconds for one summary and then failed twice with memory errors, while the e4b model finished every attempt in 94 to 128 seconds. So 12B is only the default when a GPU is present. A summary is queued as a background job (poll every 15 s, retry after 120 s, 3 attempts; `CIVICCAST_SUMMARY_JOB`, `..._POLL_SECONDS`, `..._BACKOFF_SECONDS`, `..._MAX_ATTEMPTS`) so one slow generation does not time out a web request.

Before local summary generation, CivicCast requires the selected tier's catalog memory allowance plus 8 GiB of available RAM reserved for station work. If memory is insufficient, the job reports a recoverable deferral and explains whether to wait or select a smaller tier. CivicCast serializes its local Ollama generation requests and requests model unloading after generation, so an idle summary or translation model does not remain resident indefinitely.

How the Ollama child starts. The service starts Ollama only when the program `dependencies\ollama\ollama.exe` exists and a staged model store exists with a `manifests` folder (`models\ollama` first, then `packs\local-ai-model\models`, then `C:\ProgramData\CivicCast\packs\local-ai-model\models`, where the first-run window saves its downloads). If either is missing the service logs one line ("ollama child skipped (degraded AI, service healthy): ...") and keeps running without AI. A skipped child is re-checked every 60 seconds, so models downloaded while the service is running are picked up without a restart. The child is started with `OLLAMA_NO_CLOUD=1`; hosted models are called by CivicCast directly over the internet, not through the local Ollama.

- **Choosing a local model** saves at once, with no confirmation. Options that need more memory than the computer has are disabled and marked "(exceeds this box)"; the screen does not show how much memory the computer has.
- **Choosing a hosted model** (Cloud or Frontier) only stages the choice. The page asks for the provider's API key, then for a tick acknowledging the provider's terms and costs, then **Apply cloud model**. Hosted models send meeting content to the provider; possible charges depend on the provider's current pricing and account terms. The server refuses a hosted choice without the consent tick.
- **Provider keys** go in the Windows credential store under the service name `civiccast.ai-provider` (handles `ollama-cloud-key` and `openrouter-key`). They are write-only: the screen never shows one again, and there is no button to remove one. To clear a key, run `civiccast model set-provider-key <ollama-cloud|openrouter> --clear`. To store a key without the console (for an offline station), run `civiccast model set-provider-key <provider>` with the key in the environment variable `CIVICCAST_PROVIDER_API_KEY` of that command's own shell (not the service). A hosted choice with no stored key shows "Hosted tier selected but no provider credential is stored — feature will defer until a key is saved."
- Hosted requests go to `https://ollama.com` and `https://openrouter.ai`. A station that blocks outbound traffic cannot use them.

For an air-gapped station, `civiccast model download --offline-bundle --bundle-dir <folder>` prints the manifest of model files with their SHA-256 values and `civiccast model import-offline` verifies a bundle against expected hashes (see [Appendix: command line](#app-cli)).

Recorded-caption translation uses the configured translation model to queue translated Spanish caption cues for review. This is a post-publication workflow; it does not translate the live English caption stream. Review the translated cues before attaching or publishing the translated track.

The translation card reports saved Spanish review cues and their latest creation time. This persists across reloads; it describes the cues currently stored, rather than a lifetime total of translation-provider requests.

Model cards include identifiers such as `gemma4-e4b-ollama` and `whisper-medium-faster`. A saved caption-tier change applies to new recorded-caption jobs; an active job retains its original model. The live runtime keeps its service-start fallback tier until the service restarts. The screen reports that runtime separately from the saved selection and explains when a restart is needed.

## Provider setup and keys {#configuration-providers}

The Provider setup block on the Setup page lists twelve cards: Local resident portal, Backup destination, Internet Archive, YouTube, Subscriber notices, Local archive folder, Cloudflare R2, BunnyCDN, Fastly Object Storage, Akamai Object Storage, Podcast feed and Federation. Each shows a status (Ready, Check before meeting, Do not broadcast yet, Not set up yet, Needs IT help), a "Setup guide", and where it applies a credential form. Only `setup_admin` can save.

- **Save details** writes the values to `provider-credentials.json` beside `station-state.json`, as readable text in a file whose Windows access list is restricted. The screen says "CivicCast stores these locally and never shows secret values again." The Windows credential store is not used for these; it is used for AI provider keys and control-room device passwords. Protect the folder accordingly and keep it out of backups that leave the building unencrypted.
- **Test connection** exists for Cloudflare R2 and BunnyCDN only. The Fastly and Akamai setup text also says to run Test connection, but those cards have no such button.
- **Provision for me** on the Cloudflare R2 card takes a Cloudflare API token, verifies it, creates a bucket, enables a public domain, derives and saves the keys, and then health-checks. The token is not stored.
- **Record redacted proof** stores only the text you type (a file path, address or reference). The server rejects text containing `token=`, `secret=`, `password=` or `private_key=`. It does not run a live test.

The same providers can be configured with environment variables instead (for example `CIVICCAST_R2_ACCOUNT_ID`, `CIVICCAST_R2_ACCESS_KEY_ID`, `CIVICCAST_R2_SECRET_ACCESS_KEY`, `CIVICCAST_R2_BUCKET`, `CIVICCAST_R2_PUBLIC_BASE_URL`, `CIVICCAST_CDN_PROVIDER`, and the `CIVICCAST_BUNNY_*`, `CIVICCAST_FASTLY_*`, `CIVICCAST_AKAMAI_*`, `CIVICCAST_IA_*`, `CIVICCAST_YOUTUBE_*` and `CIVICCAST_SMTP_*` families listed in [Appendix: settings](#app-settings)). The CDN, archive and mail details are in [Cable headend, streaming, CDN, federation, emergency alerts, the API](#ch-integrations).

For Internet Archive, local archive, YouTube and SMTP mail, saved Setup details feed the existing real adapter directly. You do not need to copy those credentials into service environment variables or restart the station for this connection. Saved fields take precedence over environment fallback. An explicit `CIVICCAST_PROVIDER_*` selector still overrides automatic selection; ask IT to check it if a saved connection remains simulated. Subscriber notices with only a webhook secret retain their existing webhook behavior; SMTP selection requires SMTP details. Configuration alone does not prove delivery.

For **Subscriber notifications**, an SMTP port must be a whole number from 1 to 65535. Missing SMTP host or sender details are saved as a **draft**, not a working mail connection. Correct rejected values in place: the form retains your entries after a failed save. A successful save keeps ordinary fields visible and clears password fields; an omitted saved password is preserved. Complete the settings and check actual delivery before relying on notices.

### Connect YouTube, from account sign-in to live access {#configuration-youtube}

YouTube is optional. Its setup has three separate parts: a YouTube channel, Google's permission for CivicCast to use it (OAuth), and YouTube's permission to livestream. Signing into YouTube in Chrome completes none of the other two permissions by itself. Have the station's Google-account owner and an IT person do this together. Do it at least a day before the first planned stream; Google can impose a first-time activation wait.

#### 1. Select the station account and create its channel

1. Sign into YouTube with the Google account the station will own. Check the email in the account menu; several accounts can be signed into the same browser.
2. Open [YouTube's channel switcher](https://www.youtube.com/channel_switcher). Select the intended channel if one exists. A Google account can exist without a YouTube channel: **No channel** means one still needs to be created.
3. With the account owner's approval, choose **Create a channel**, set the channel name and available `@handle`, review the displayed terms, and choose **Create channel**. Record the channel address and channel ID. Do not create another channel if the station already has the one it wants.

#### 2. Prepare a Google Cloud project and OAuth client

1. In the same browser, open [Google Cloud Console](https://console.cloud.google.com/). Confirm the station email in its account menu too. Google Cloud, YouTube and YouTube Studio can each select a different signed-in account. The `authuser` query parameter with the station email can help select the correct account; always confirm the screen itself.
2. On a first visit, review Google's welcome/terms screen, select the country and accept the terms if authorized. Opening this console does not require starting its free trial or enabling billing for this procedure. Dismiss the trial offer.
3. Use the project picker to select the station's existing project. If there is none, have the owner approve creating a project, for example **CivicCast YouTube**, then select it. Keep its project ID with the station's setup records.
4. In **APIs & Services > Library**, find **YouTube Data API v3** and choose **Enable**. Confirm that the page says **API Enabled**. The same API supports CivicCast's live-broadcast operations; the YouTube Analytics and Reporting APIs are not needed for this connection.
5. Open **Google Auth Platform**. If it says it is not configured, choose **Get started**. Set an app name such as **CivicCast**, select the station's support email, choose **External** for a regular Gmail account, and enter the contact email. Review the Google API Services User Data Policy, finish the steps and choose **Create**. An organization-managed account may have different audience choices; ask its administrator.
6. Under **Audience**, leave an initial rehearsal app in **Testing** and choose **Add users**. Enter the station's exact Google email, press **Enter** so it becomes an email chip, then choose **Save**. Confirm the address appears in the test-user table. Text left in the entry box without a saved table row does not grant access.
7. Under **Clients**, choose **Create client**, select **Desktop app**, give it a name such as **CivicCast station**, and choose **Create**. CivicCast is the desktop application here; do not select a web application or the AI-agent option for this flow.
8. Download the client JSON while Google's creation dialog is open. Google may show the client secret only at creation. Store this file in a local folder restricted to the station administrator and necessary service/administrator accounts; keep it out of shared or cloud-synced folders. It contains a client ID and client secret, but it does **not** yet contain the refresh token CivicCast needs. Do not post it in chat, email it or commit it to source control.

#### 3. Authorize CivicCast and obtain the refresh token

The YouTube card has no **Sign in with Google** button in this version. IT must perform the desktop OAuth flow once outside the Setup screen. The source checkout includes [`scripts/authorize_youtube.py`](https://github.com/scottconverse/civiccast/blob/main/scripts/authorize_youtube.py), the repeatable helper for this step. Run it with the matching CivicCast Python environment (the example assumes the checkout's `.venv`, with CivicCast and its normal `httpx` dependency installed). This is a setup tool, not an installer rebuild or station restart.

From that checkout, replace the example paths and email with the station's own values:

```powershell
.\.venv\Scripts\python.exe scripts\authorize_youtube.py `
  --client-json 'C:\StationPrivate\youtube-client.json' `
  --output 'C:\StationPrivate\youtube-authorized.json' `
  --account 'station@example.org'
```

Use a new output filename; the helper refuses to overwrite an existing file. It opens the normal system browser, listens only on `127.0.0.1` at a temporary port, and waits up to ten minutes. It uses a random request state and PKCE (a one-time proof that protects the authorization-code exchange), requests offline access with the `youtube.force-ssl` permission, and exchanges the returned code directly with Google. It does not print the code, client secret or tokens. Its output file is access-restricted before the credentials are written. Google describes this flow in its [desktop OAuth guide](https://developers.google.com/youtube/v3/live/guides/auth/installed-apps).

1. In the Google consent screen, confirm the intended station account and, if offered, the intended YouTube channel. Review the app name and YouTube permissions. Complete consent only for the station's own trusted OAuth app.
2. For an app still in Testing, Google can show an unverified-app notice. Confirm that IT created the app and that the account is in its test-user table before continuing through Google's available test-app consent steps. If Google says **Access blocked** because the app is being tested, return to **Audience**, save the correct test-user entry and run the helper again; repeated sign-ins alone do not fix it.
3. On success the browser says authorization is complete, and the helper confirms that it saved protected credentials. The JSON contains `client_id`, `client_secret` and `refresh_token`. Keep all three private. If consent is declined, permission is missing or the helper times out, it does not report success; correct the cause and rerun it with a new output filename if a file was already saved.
4. A Google access token is short-lived and is not a substitute for the refresh token. Do not use the deprecated procedure that asks you to copy an authorization code out of a browser page. The local callback receives the code automatically.

**Testing-mode lifetime:** Google's External/Testing apps normally issue refresh tokens that expire after seven days for this YouTube scope. This is suitable for initial setup, but not a permanent unattended station connection. Before ongoing operation, have IT complete Google's applicable publishing/verification requirements and reauthorize as needed. Do not mistake publishing the OAuth app for publishing a CivicCast release. See Google's [token-expiration rules](https://developers.google.com/identity/protocols/oauth2#expiration). Revoking the app's account access or changing its client can also require new authorization.

#### 4. Save all three credentials in CivicCast

1. On the station computer, sign into CivicCast with the `setup_admin` role and open **Setup > Provider setup > YouTube**.
2. Open the protected authorization file locally and copy its **Client ID**, **Client secret** and **Refresh token** into the corresponding fields. Choose **Save details**. Do not paste the JSON itself into one field. Older setups that saved only the client ID and secret must add the refresh token.
3. CivicCast stores these values in its protected provider-credentials file and does not show secret values again. The real adapter reads that saved configuration; no duplicate environment setup or station restart is needed. Keep the original client/authorization files protected or remove working copies after the credentials and recovery arrangements are secure. Never attach them to a support report.
4. The YouTube card has no **Test connection** button in this version. **Record redacted proof** records an evidence reference; it does not test the connection. IT can check token refresh and the authorized channel through the API without starting a stream. A successful account/API check establishes authorization, not viewer delivery.

#### 5. Verify the phone number and request livestream access

1. Open [YouTube Studio](https://studio.youtube.com/) for the same station channel, then **Create > Go live**. If Studio says you do not have permission, switch to the station account before proceeding; creating a new project or replacing the OAuth token will not fix a browser signed into the wrong account.
2. If Studio offers **Request access to streaming**, choose **Request**. If it asks you to verify a phone number, choose **Verify**. Complete [YouTube phone verification](https://www.youtube.com/verify) yourself: select the delivery method and country, enter the number, request the code and enter it on Google's page. Do not send the phone number or verification code to support. YouTube may limit how many accounts a number can verify.
3. Check the account on the verification page. **Phone number verified** for a different signed-in account does not verify the station channel. Select the station account and finish its verification.
4. Return to the station channel's **Go live** page, refresh it, and finish **Request** if still offered. Confirm either the live controls or the displayed activation countdown. Phone verification and live activation are separate; completing one does not necessarily remove the other wait.
5. Google can impose a first-time wait of up to 24 hours. Write down Studio's request time and expected ready time in the station's local timezone. An active **Only … until you can stream** countdown is a YouTube activation delay; do not keep replacing credentials, restart CivicCast or rebuild an installer to address it. After it ends, check Studio's actual access status. See [YouTube's live-streaming guidance](https://support.google.com/youtube/answer/2474026).

#### 6. Run the first stream when YouTube is ready

Start the desired CivicCast channel and confirm its local program output, then open **Publish**, select that channel for **YouTube Live**, and start the optional YouTube output. The selected channel needs fresh local HLS output. CivicCast creates and binds the YouTube event, then sends that channel through a separate owned output process; the default event privacy is **unlisted**. Keep a first rehearsal away from a channel already serving a meeting.

The returned `youtube.com/watch?v=…` address is the viewer link. **Scheduled** means the saved event is waiting for its start time; **Starting** means the sender has started; **live** requires YouTube to report both an active stream and a live broadcast. **Simulated** means nothing was sent. The dashboard polls destination state periodically; setup success alone must never be described as a confirmed live stream. **Failed** leaves local portal publication available. When finished, choose **Stop YouTube output**, then check the event in Studio if remote cleanup needs attention. Stream keys and ingest addresses are not public viewer links.

If Google returns `liveStreamingNotEnabled`, check the selected channel's phone verification and activation countdown first. If its channel API returns no channels, check that the account has a YouTube channel and that OAuth authorized the intended identity. If refresh fails later, check testing-mode token expiry or revoked consent before replacing unrelated settings. An owner can accept completed setup separately from a later live rehearsal; record those as separate results rather than claiming the rehearsal happened.

## Run Cable Commissioning {#configuration-commissioning}

Cable Commissioning is a four-step wizard for a station that sends a channel to a cable company's headend. Skip it for a station that only serves residents on the web. It is available to `setup_admin` (who can press things) and `support_admin` (who can only look). Progress is saved to `station-state.json` after each step, so you can leave and come back.

1. **Screen 8: First-run cable checks.** Optionally type the station name, then click **Run cable checks**. It runs 11 checks: operating system, disk space (a failure under 100 GB free), the GStreamer playout engine, the DeckLink/BMD Desktop Video SDK (a warning only), TSDuck (a warning if missing), the database (a failure unless durable storage is ready), the event bus, the backup destination (a warning), the time zone (a warning if it is UTC: "Set the station's real local timezone in Station Profile."), release integrity (always skipped) and the CasparCG helper (skipped or a warning). Only a **FAIL** blocks the next step; warnings do not. The screen always sends the `peg-cable` profile, so DeckLink wording appears on every station.
2. **Screen 9: Channel output setup.** Choose a channel, an output format (`720p30`, `1080i60`, `1080p30` the default, `SD480i60`), a headend profile, the destination `address:port`, an optional SDI device, a fill policy (Slate, Loop, Silence) and whether to check CEA-708 caption passthrough. Click **Save and continue**.
3. **Screen 10: Output proof.** Choose the test pattern ("Bars + tone" or "Slate"; "Live" behaves exactly like bars), a duration (1 to 1800 seconds, default 60) and click **Start proof run**, then confirm. It sends the test pattern to the channel's UDP output for that time while TSDuck checks the stream, then (if ticked) runs the caption check. The request holds the page until it finishes.
4. **Screen 11: Commissioning report.** Type the station name and click **Generate report**. The result is "Ready for broadcast" or "Commissioning incomplete" with the steps still needed. A proof with any blocker never reads ready. The same page can make a support bundle (a redacted diagnostic file).

The same flow runs from the command line: `civiccast cable doctor`, `civiccast cable commission --channel-id ... --headend-profile ... --destination ...`, `civiccast egress verify --channel-id ...` (see [Appendix: command line](#app-cli)).

> **Warning:** **Do not run the output proof while the channel is on air.** The proof pushes a test pattern to the channel's UDP destination for the whole duration. The dialog warns that it replaces the channel's real output; in the code it starts a second stream to the same address. We found no check that stops it on a channel that is airing.

> **Known issue (beta.11):** Screen 9 only saves your choices to the station record for the report. It does not configure the channel. Apply the headend preset on the Channels screen first. If the channel has no UDP output the proof fails with "channel '&lt;id&gt;' has no udp-ts sink to verify — apply a headend delivery profile first." The destination box is not tested for reachability. The cards are numbered Screen 8 to 11 although there are no Screens 1 to 7 on this page.

> **Known issue (beta.11):** The text under Screen 8 says warnings can be passed with "Continue-anyway". There is no such button; the step opens by itself when no check fails. The check and verdict words are shown raw (PASS, FAIL, WARNING, SKIPPED, partial), and the proof ends with an internal note about "rung 3" and "MASTER §13.2". It means the test checks the network signal only and is not a proof of SDI hardware.

## Set up the Control Room {#configuration-controlroom}

Control Room Setup (Setup group; `setup_admin` only) registers the production equipment a meeting is run with (switchers, deck recorders, PTZ cameras, relays), and builds **surfaces** (a named panel) of **cues** (one-press actions such as "take scene 2"). The operate screen, Control Room, uses what you configure here. Nothing here sends a command to a device except **Test connection**.

1. **Register a device.** Enter a label (up to 160 characters), a kind (OBS Studio, vMix, Blackmagic ATEM, HyperDeck, PTZ camera, OSC, TCP device, HTTP device, CasparCG, GPI (network relay), Serial (network relay)), the transport, a host and a port (1 to 65535), and a secret if the device has a password. The secret goes to the Windows credential store and is never shown again. Click **Register device**.
2. **Test connection.** The service asks the control helper to open a connection and records Reachable or Unreachable. A result older than 5 minutes shows "Stale — probe again". A device host must be `localhost`, a `.local` name or a private or link-local address; any other host is refused ("Device host must be localhost, .local, or a private/link-local IP unless a setup admin records a public-host override reason in the device profile.").
3. **Set timing.** In a device's profile set the **Take-delay (ms)** and **Post-roll (ms)** (0 to 600,000) and click **Save profile**.
4. **Create a surface** with a label and pick it in **Edit surface**. Surfaces are created for the role `meeting_operator`.
5. **Add cues** with a label, a device, an action (Take scene, Take input, Transition, Run macro, Play deck, Cue deck, Recall PTZ preset, Send OSC, Send HTTP, Push overlay, Clear overlay, GPI pulse, Serial send, Router take), a bank (0 to 99), a payload (JSON, pre-filled with a template) and **Confirm**. The server refuses an action the device kind does not support.

The readiness panel at the top lists the checks (control service, devices, profiles, enabled devices, device health, target safety, surfaces, cues, cue action safety, a safe-state cue, station-device evidence). A surface that has cues must have at least one cue with **Confirm** ticked; the program treats that cue as the safe-state cue, and readiness is blocked without it.

The helper behind this is the **TSR sidecar**, a separate small Node.js program bound to `127.0.0.1` (default port 7717). The service reaches it through `CIVICCAST_CONTROL_ROOM_TSR_URL` (for example `http://127.0.0.1:7717`). With the variable unset, every probe reports unreachable and every cue fails closed.

> **Known issue (beta.11):** We found nothing in the installer or the service that installs Node.js, ships the sidecar's dependencies or starts the sidecar. On a native station as installed, the **TSR control service** readiness card is blocked and cues cannot fire. To use the Control Room, IT has to install a compatible Node.js, install and start `civiccast\control_room\tsr_service` (`CIVICCAST_TSR_PORT=7717`, `npm install --ignore-scripts --omit=optional`, then `npm start`; the sidecar's own notes say its library targets Node 14, 16 or 18), and set the service variable. The button inside the blocked cards that says "Open Control Room Setup" points at the page you are already on, and the screen has no control that starts the helper.

> **Known issue (beta.11):** **Save profile** always sends empty options. Saving a profile replaces earlier settings, including a public-host override recorded elsewhere. The take-delay and post-roll boxes start at 0 and do not show the saved values. The headline can never read "Ready": equipment verification is fixed to "pending" in this build, so the best result is "Check before meeting". Nothing on the screen disables or enables a device, although a readiness hint talks about it.

The Control Room displays its On-Air session deadline and remaining time, restores the owner's open session after a refresh, and lets a Setup admin or Support admin force-close a locked session. A previous dry-run result is not restored; dry-run the cue again before firing it. See [Running the meeting](#ch-running-meeting).

## Define Custom Fields {#configuration-custom-fields}

Custom Fields (Setup group; `setup_admin`) are your own labels for programs, such as "Meeting type" or "Department". Each field becomes a box on every asset's detail page; if it is **Searchable** and **Exposed to public API** it also becomes a filter on the public Recordings page. This screen holds the definitions. The values are typed on each asset (roles `setup_admin`, `meeting_operator` and `records_clerk` can set them).

1. Open **Custom Fields**. In **Define a new field** enter a **Field key** (for example `meeting_type`; fixed after creation) and a **Label** (up to 200 characters).
2. Choose a **Type**: Text, Long text, List (pick one), Date, Number, Yes / no, Asset reference or Producer reference. For a List, type the choices one per line.
3. Set **Required**, **Searchable** and **Exposed to public API** (the last two are ticked by default) and an **Order** number.
4. Click **Create field**.

> **Known issue (beta.11):** A field shows as a public filter only when **both** Searchable and Exposed to public API are ticked, and only once a published recording has a value. Ticking only Searchable does nothing for residents.

> **Known issue (beta.11):** **Required** makes every asset without a value unsavable, including edits to its other fields, and the error uses internal ids ("Required field 'cf-x' ('Label') must be present."). Add a required field only when you are ready to fill it on every asset. Creating a field whose key makes the same internal id as an existing one overwrites that field's label, type, list and flags without a warning. The first **Delete** on a field with no values deletes it with no confirmation; on a field with values you get a red **Confirm delete (cascades values)** and the delete removes the saved values too. The up and down arrows swap Order numbers, so fields that all have Order 0 do not appear to move. The screen always creates fields for the station id `civiccast-station`, so do not set `CIVICCAST_STATION_ID`, or new fields will not appear in the list.

## Configure the Paywall {#configuration-paywall}

The paywall (Setup group; `setup_admin`) is optional paid access to recordings through Stripe-hosted checkout, plus free "comp" passes. It is **off** by default; when off, every asset is public.

> **Beta 19 status:** Do not rely on the paywall for paid access. A configured mail provider sends magic links and reports delivery failure, but the public site's calls for tiers and checkout (`/api/public/paywall/tiers`, `/api/public/paywall/checkout`) have no matching server routes, and the public VOD media route does not enforce grants. The access check trusts the email address passed to it; the code does not prove the caller owns it.

> **Signing secret:** The server never returns the saved value, so the field stays blank after you open or reload this page. If a secret is saved, leave the field blank to keep it during an ordinary **Save**. To replace it, enter a new secret of at least 32 characters or generate one. Confirm the exact value currently in the field, then **Save**; editing the value clears confirmation, so confirm again. **Cancel** discards the pending replacement. To remove the saved secret, choose **Clear saved secret**, confirm, then **Save**. Clearing stops magic-link signing and Stripe webhook verification until you save a new one.

If you still want to look at it on a lab station:

1. Create the monthly or yearly prices in the Stripe website first. CivicCast never creates a price.
2. Tick **Enable paywall**. Provider **stripe** is the live one; **mock** is for tests and has no warning on a live station.
3. Click **Generate new secret** (32 random bytes, 44 characters, filled in the box), or paste your own of at least 32 characters. When replacing a saved secret, confirm the value currently in the box before **Save**; any edit requires a fresh confirmation. **Cancel** discards the pending replacement. Leave the field blank to keep the saved value when you save other settings. To remove it, choose **Clear saved secret**, confirm, and save. The Stripe webhook is `/api/webhooks/stripe`, and the code checks its `Stripe-Signature` header with this same secret. Stripe creates its own `whsec_` value for each endpoint, so whether Generate is the right value to give Stripe is not confirmed.
4. Add a tier for each price (**Tier ID** such as `basic`: lowercase letters, digits, `_` and `-`; **Display name**; **Stripe price id** starting `price_`; **Monthly** or **Yearly**) and click **Add tier**. Tier changes are local until you click **Save**.
5. Click **Save**.

**Comp access grants** issue a free pass to an email address for one asset, one series or everything, with an optional **Expires at** date (the end of that day, **in UTC**). The grant is created immediately on the server, even if the paywall is not saved ON. The table lists only grants issued in this browser session; after a reload you cannot see or revoke earlier grants from this page. **Delete config** removes the saved configuration and turns gating off; we could not confirm whether it also removes grants and subscriptions.

## TLS, certificates and network exposure {#configuration-tls}

What the program does, as found in the code:

- The control plane, which serves the operator console, resident portal and API, stays on a loopback address (`127.0.0.1` by default; Network Settings also accepts `::1`) and speaks plain HTTP upstream. For remote HTTPS, use the supported Caddy configuration: a public hostname for resident pages and a separate, CIDR-restricted staff hostname. First setup and first-admin recovery stay local-only. See the [network access guide](https://github.com/scottconverse/civiccast/blob/main/docs/ops/network-access.md).
- PostgreSQL listens on `127.0.0.1` (the first free of ports 5432, 5433, 5434, 5435 and 5544). Ollama listens on `127.0.0.1:11434`. The Control Room sidecar listens on `127.0.0.1`.
- The **local CA** is a small certificate authority for internal service-to-service TLS (mutual TLS). It holds two identities, `civiccast-api` and `civiccast-worker`. `civiccast cert rotate civiccast-api` creates the CA on first use (valid 3,650 days), then issues the named certificate (valid 90 days; "rotation due" starts when 30 days remain) with the key readable only by the account that ran the command. The folder is `CIVICCAST_CERT_ROOT`, default `.civiccast\certs` in the home folder of the account running the command. The only reader of these certificates is the CLI's `civiccast installer health-check`, which lists "Local CA mTLS" with the next step "Rotate internal service certificates every 90 days." We found no part of the station that serves or checks TLS with them.
- Behind a reverse proxy or CDN, set `CIVICCAST_TRUSTED_PROXY_CIDRS` (comma-separated CIDR list) so the real client address is used for rate limiting and audit. `CIVICCAST_TRUST_PRIVATE_PROXIES=true` trusts loopback and private-network hops too (default false; set it when a proxy such as nginx runs on the station itself). With `CIVICCAST_CDN_PROVIDER` set to `bunny` or `cloudflare`, the provider's published edge networks are added. With none of these set, forwarded headers are ignored.
- Browsers from another web origin are refused by default. `CIVICCAST_CORS_ALLOWED_ORIGINS` is a comma-separated list of exact origins (scheme, host, port); a wildcard is a startup error.

The installer-created TCP 8000 rule does not make the loopback-only application listener reachable from other computers. For supported remote access, deploy Caddy using the generated configuration; it terminates HTTPS and enforces the separate public/staff host policy. Do not expose port 8000 directly. See the [network access guide](https://github.com/scottconverse/civiccast/blob/main/docs/ops/network-access.md).

The application itself does not terminate HTTPS; Caddy provides the supported HTTPS edge. The local CA remains for internal certificate operations and is not a browser certificate.

## Storage and media lifecycle {#configuration-storage}

Media Lifecycle Settings (Review Records group) has three cards. All three call the server separately, and the server's roles do not match the sidebar:

| Card | Roles that can use it (server) |
| --- | --- |
| Watch folders: list; add and remove | List: `meeting_operator`, `publish_operator`, `support_admin`. Add and remove: `publish_operator`, `setup_admin`. **Browse...** and **Scan now**: `setup_admin` only |
| Retention automation | List: `meeting_operator`, `publish_operator`, `support_admin`, `records_clerk`. Add, remove, **Apply rules now**: `records_clerk`, `setup_admin` |
| Storage budget | `meeting_operator`, `publish_operator`, `support_admin` |

The first admin has every role, so it sees all three work. A token with only `setup_admin` cannot load the watch-folder list or the budget; the card shows "This action requires one of these CivicCast roles: ...".

**Watch folders.** A watch folder is a folder on the station computer (a local disk, a USB drive or a network share) that CivicCast checks and imports video from. Type the path or use **Browse...**, which lists local drives only (network paths must be typed), then **Add watch folder**. A new folder is enabled, waits until a file has stopped growing for 10 seconds, and is checked about every 5 seconds (the worker's tick is 2.0 seconds, `CIVICCAST_WATCH_FOLDER_POLL_SECONDS`). Files are copied, never moved or deleted, into the uploads folder, and the imported asset takes the file name as its title. Whole drives (`C:\`), Windows system folders and administrative shares such as `\\host\C$` are refused. The service account must be able to read the folder.

**Retention rules.** A rule sets an asset's retention policy (`default`, `permanent`, `meeting`, `short`) from its exact **Meeting body** text. CivicCast never deletes an asset because of retention. A separate worker (`CIVICCAST_RETENTION_WORKER`, `inline` by default, every `CIVICCAST_RETENTION_POLL_SECONDS`, default 3600) only flags assets whose retention date has passed into the records clerk's disposition queue. Retention is also described in [After the meeting](#ch-after-meeting).

> **Known issue (beta.11):** Rules do not run by themselves. The only thing that applies them is the **Apply rules now** button. A rule with a blank Meeting body is listed as "any" but matches nothing. Applying a rule changes the policy label; this step does not recalculate the retention deadline.

**Storage budget.** The card shows how much disk the library uses by retention tier. It shows "No budget configured (set CIVICCAST_MEDIA_STORAGE_BUDGET_BYTES)" until you set that variable to a number of bytes in the service environment. The uploaded file size limit is `CIVICCAST_UPLOAD_MAX_BYTES` (default 10 GiB, 10,737,418,240 bytes).

**Other storage workers**, all `inline` by default, each with an `off` switch (the switch is the variable named below): media lifecycle (`CIVICCAST_MEDIA_LIFECYCLE_WORKER`; every 300 seconds; ingest-time conversion is on and makes one `h264_720p_5mbps` proxy per validated asset, switch it off with `CIVICCAST_MEDIA_LIFECYCLE_TRANSCODE_SEEDING_ENABLED=0`; a dry run that records what it would do is `CIVICCAST_MEDIA_LIFECYCLE_WORKER_DRY_RUN=1`; scheduled items with missing media are flagged 7 days ahead, `CIVICCAST_MISSING_MEDIA_HORIZON_DAYS`), media integrity (`CIVICCAST_MEDIA_INTEGRITY_WORKER`, every 3,600 seconds, flags a missing file) and the archive. For a local archive set `CIVICCAST_NAS_ARCHIVE_PATH` and `CIVICCAST_PROVIDER_LOCAL_NAS=real`. The archive path must be a reachable folder or the service logs why.

## Environment variables {#configuration-env}

The current source inventory contains 443 environment variable names, including development and build settings. Almost all have a safe default. This section covers the ones a station's IT person will plausibly need, with the real default and what happens. Every variable below is read from the **service's** environment unless the table says it belongs to a command you run yourself.

### How to set one on the Windows service {#configuration-env-how}

The service's child programs (the control plane, the playout workers) get the service's environment, plus a short list of values the service forces. The supported way to give one variable to the service is the per-service `Environment` registry value, a list of `NAME=value` strings.

1. Open **PowerShell as Administrator** on the station computer.
2. Add the variable (this keeps the entries that are already there and replaces one of the same name):

   ```
   $key = 'HKLM:\SYSTEM\CurrentControlSet\Services\CivicCastSupervisor'
   $name = 'CIVICCAST_TS_RELAY'
   $value = 'off'
   $current = @()
   try { $current = @((Get-ItemProperty -Path $key -Name Environment -ErrorAction Stop).Environment) } catch { }
   $new = @($current | Where-Object { $_ -notlike "$name=*" }) + "$name=$value"
   Set-ItemProperty -Path $key -Name Environment -Value $new -Type MultiString
   ```

3. Restart the service so a fresh process reads it: `Restart-Service CivicCastSupervisor`.
4. Read the value back: `(Get-ItemProperty $key).Environment`.

To remove a variable, run the same lines without the `+ "$name=$value"` part.

> **Warning:** Restarting the service stops PostgreSQL, the control plane and the Ollama engine (each is given up to 15 seconds to stop gracefully) and takes every channel off the air until the service is back and the channels have started again (channels with **Keep this channel on air** start themselves). Do it outside a meeting.

> **Warning:** Do not use `setx /M` or `[Environment]::SetEnvironmentVariable(..., 'Machine')`. The project's own lab found that a machine-wide variable set that way never reached the running service: a Windows service gets its environment when it is started, and Windows services are started by a process whose environment block is captured once at boot. The per-service registry value is read at that service's own start. This is how the project's lab scripts inject variables; the code comments in the station also name it as the place to set them.

> **Known issue (beta.11):** The native installer does not create this optional per-service `Environment` registry value. On a fresh install it may not exist; the steps above create it. No console screen lists the environment the station is running with, so confirm a change by its effect or its log line.

The commands you run yourself in a shell (such as `civiccast token issue` or `civiccast model set-provider-key`) read the **shell's** environment, not the service's. The program's launcher is `civiccast.exe` inside the station runtime (the build manifest places it under `runtime\Lib\site-packages\bin`; we did not confirm the path on an installed station). The CLI is described in [Appendix: command line](#app-cli).

### What the service sets for you, and cannot be overridden {#configuration-env-forced}

The service builds the control plane's environment from its own, then applies these values last, so they win over anything you set:

| Variable | Value the service sets |
| --- | --- |
| `CIVICCAST_EGRESS_WORK_DIR` | `%PROGRAMDATA%\CivicCast\data\egress` |
| `CIVICCAST_UPLOAD_DIR` | `%PROGRAMDATA%\CivicCast\data\uploads` |
| `CIVICCAST_SUPERVISED` | `1` |
| `CIVICCAST_EGRESS_ENGINE` | `gstreamer` (or `ffmpeg-concat` if the GStreamer files cannot be verified) |
| `CIVICCAST_EGRESS_EMBED_CAPTIONS` | `1` |
| `CIVICCAST_CAPTION_RUNTIME`, `CIVICCAST_CAPTION_TIER`, `CIVICCAST_WHISPER_MODEL_PATH` | `faster-whisper`, the tier chosen at start, the verified model folder |
| `CIVICCAST_CAPTION_TAP`, `CIVICCAST_CAPTION_TAP_DIR` | `inline` and the caption-tap folder, unless you set `CIVICCAST_CAPTION_TAP=off` |
| `CIVICCAST_WHISPER_DEVICE`, `CIVICCAST_WHISPER_COMPUTE_TYPE` | CUDA only with supported NVIDIA hardware (8 GB or more) and staged CUDA files; otherwise CPU/int8, unless explicitly set |
| `CIVICCAST_NATIVE_STATION`, `..._ROOT`, `..._MANIFEST`, `CIVICCAST_NATIVE_REPORTED_VERSION`, `CIVICCAST_LAN_ONLY_STATION` | The station identity and version; LAN-only mode keeps the API documentation pages off (they load fonts and scripts from the internet) |
| `CIVICCAST_OPERATOR_CONSOLE_DIST`, `CIVICCAST_PUBLIC_PORTAL_DIST` | The folders of the two web portals |
| `DATABASE_URL` | Read from the registry value `HKLM\SOFTWARE\CivicCast\Native\DatabaseUrl`, unless a non-blank `DATABASE_URL` is already in the service environment, which then wins (one log line says which source won, never the value) |

### The one-C spelling trap {#configuration-one-c}

Almost every CivicCast variable begins `CIVICCAST_` (two C's). Sixteen names in the source begin `CIVICAST_` (one C). They are typos in the source that stayed. For most of them nothing is wrong, because they are lab, test or internal. Two of them are real settings that you can only reach under the one-C spelling, and two more are legacy fallbacks:

- `CIVICAST_CAPTION_PROOF_POLL_SECONDS` and `CIVICAST_CAPTION_PROOF_TIMEOUT_SECONDS` are the **only** spellings the caption proof loop reads. Writing them with two C's does nothing.
- `CIVICCAST_EGRESS_PREPARATION_TIMEOUT_SECONDS` and `CIVICCAST_GSTREAMER_SOURCE_SEGMENT_SECONDS` used to be read only under the one-C names. Since beta.10 the two-C name is read first; the one-C name still works as a legacy fallback with a one-time deprecation warning, and if both are set and differ, the two-C value wins and a warning says so.

A name with the wrong number of C's is silently ignored: nothing validates unknown variable names. Copy names from this chapter or from the appendix of settings and check the log for the effect.

### Curated list {#configuration-env-list}

Defaults are what the code uses when the variable is unset or blank. "Off/on" values accept `1`, `true`, `yes`, `on` unless stated.

**Identity and addresses**

| Variable | Default | Effect |
| --- | --- | --- |
| `CIVICCAST_STATION_STATE_PATH` | Installer-configured service profile under `C:\ProgramData\CivicCast\profile`; when unconfigured, `%LOCALAPPDATA%\CivicCast\station-state.json` | Overrides the state file location; related state/credential files sit beside it |
| `CIVICCAST_STATION_NAME` | The profile's name, else "CivicCast Station" | Overrides the station name shown and used |
| `CIVICCAST_STATION_TZ` | The profile's zone, else `local` (= UTC) | An IANA zone for dayparts and retention dates; an invalid name gives UTC and a warning |
| `CIVICCAST_STATION_ID` | `civiccast-station` | Single-station id used by custom fields, paywall, agendas, recordings and as-run logs. Leave it unset |
| `CIVICCAST_PUBLIC_BASE_URL` | unset | Absolute `http(s)` address used to build RSS links and as the federation base; unset gives relative links |
| `CIVICCAST_RESIDENT_PORTAL_URL` | unset | The address the operator console's resident preview points to; unset uses the station's own portal |
| `CIVICCAST_LOCAL_MEDIA_BASE_URL` | unset (site-relative links) | An absolute media origin for live-manifest links, only when media is served from a different origin than the portal |

**Sign-in and network**

| Variable | Default | Effect |
| --- | --- | --- |
| `CIVICCAST_AUTH_RATE_LIMIT` / `CIVICCAST_AUTH_RATE_LIMIT_WINDOW_SECONDS` | 10 / 60 | Failed sign-ins allowed per window; must be positive whole numbers or the service will not start |
| `CIVICCAST_ALLOW_FIRST_ADMIN_RESET` | unset | `1` lets First Setup run again on a configured station |
| `CIVICCAST_STAFF_TOKENS` | unset | Fixed staff bearer tokens (`token:operator_id:...`); see [Security and privacy](#ch-security) |
| `CIVICCAST_TRUSTED_PROXY_CIDRS` | none | Comma-separated networks whose forwarded client address is trusted |
| `CIVICCAST_TRUST_PRIVATE_PROXIES` | `false` | Trust loopback and private-network hops as proxies |
| `CIVICCAST_CORS_ALLOWED_ORIGINS` | empty (no cross-origin access) | Exact origins allowed; a `*` is a startup error |

**Playout, outputs and relays** (details in [Configure channels](#configuration-channels))

| Variable | Default | Effect |
| --- | --- | --- |
| `CIVICCAST_CHANNEL_AUTOMATION` | `inline` | `off` stops the automation driver that starts and supervises channels |
| `CIVICCAST_CHANNEL_AUTOMATION_POLL_SECONDS` | 2.0 | How often each channel is checked; must be positive |
| `CIVICCAST_EGRESS_SEAMLESS_RELOAD` | on | `0`, `false`, `no` or `off` makes the encoder restart at plan boundaries |
| `CIVICCAST_EGRESS_PREPARATION_TIMEOUT_SECONDS` | 300 | Longest one preparation (ffmpeg) step; invalid or non-positive values are logged and replaced by 300 |
| `CIVICCAST_GSTREAMER_SOURCE_SEGMENT_SECONDS` | 1800 | Longest span of a program prepared in one piece |
| `CIVICCAST_CONFORM_CACHE_GB` | 60 | Size limit of the cache of whole prepared programs |
| `CIVICCAST_PREPARED_PLAN_DIR_BUDGET_GB` | 5.0 | Limit for the prepared-plan folder |
| `CIVICCAST_GST_ALLOW_HARDWARE_DECODE` | off | Allows GPU video decoders in the playout worker |
| `CIVICCAST_GST_PREROLL_TIMEOUT_S` | 30 (5 to 45) | Wait for the picture to start |
| `CIVICCAST_GST_FIRST_OUTPUT_TIMEOUT_S` | 45 (10 to 120) | Wait for the first output |
| `CIVICCAST_STALL_TIMEOUT_S` | 10 | A worker with no output for this long quits so it can be restarted |
| `CIVICCAST_TS_RELAY` | `auto` | `auto`, `on` or `off` for the TSDuck relay on `udp-ts` outputs |
| `CIVICCAST_TS_RELAY_BASE_PORT` / `CIVICCAST_HLS_RELAY_BASE_PORT` | 17800 / 18000 | First local port used by each relay |
| `CIVICCAST_LIVE_HLS_ROOT` | the egress work folder | The only folder a web-preview folder may be under |
| `CIVICCAST_TSDUCK_PATH` / `CIVICCAST_TSDUCK_HOME` | unset / `%LOCALAPPDATA%\CivicCast\tsduck` | Where to find or keep TSDuck |
| `CIVICCAST_NDI_FFMPEG` / `CIVICCAST_SDI_FFMPEG` | unset | Your own NDI or DeckLink-capable ffmpeg |

**Captions and AI** (details in [Configure captions](#configuration-captions) and [Configure AI models](#configuration-ai))

| Variable | Default | Effect |
| --- | --- | --- |
| `CIVICCAST_CAPTION_TAP` | forced `inline` | `off` turns live captions off entirely |
| `CIVICCAST_LIVE_CAPTION_ENGINE` | `whistle` for native live captions | `whistle` (CPU primary, Whisper fallback) or `whisper` (Whisper primary) |
| `CIVICCAST_WHISPER_DEVICE` / `CIVICCAST_WHISPER_COMPUTE_TYPE` | CUDA with supported NVIDIA GPU and staged runtime; otherwise `cpu` / `int8` | Forces `cuda` or `cpu` and the compute type |
| `CIVICCAST_WHISPER_CPU_THREADS` / `CIVICCAST_CAPTION_TAP_CPU_THREADS` | live: 1, up to 2 with 16 or more CPUs | CPU threads for captioning; live ignores 0 and caps above 2 |
| `CIVICCAST_CAPTION_TAP_SEGMENT_SECONDS` / `..._POLL_SECONDS` / `..._MAX_BACKLOG_SEGMENTS` | 5.0 / 2.0 / 2 | Live caption audio segment, scan period, tolerated backlog |
| `CIVICCAST_OFFLINE_CAPTION_JOB` | `inline` | `off` disables recorded-caption work by default until station settings are saved; use Station Profile for ongoing configuration |
| `CIVICCAST_OFFLINE_CAPTION_POLL_SECONDS` / `..._BACKOFF_SECONDS` / `..._MAX_ATTEMPTS` / `..._CHUNK_SECONDS` | 60 / 300 / 4 / 30 | Offline caption job timing |
| `CIVICCAST_SUMMARY_JOB` and `..._POLL_SECONDS` / `..._BACKOFF_SECONDS` / `..._MAX_ATTEMPTS` | `inline`; 15 / 120 / 3 | Summary job |
| `CIVICCAST_OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Read only by the alerting self-test; the summary and translation adapters use the loopback address directly |
| `CIVICCAST_PROVIDER_API_KEY` | unset | Read by `civiccast model set-provider-key` only (your shell, not the service) |

**Storage and lifecycle** (details in [Storage and media lifecycle](#configuration-storage))

| Variable | Default | Effect |
| --- | --- | --- |
| `CIVICCAST_UPLOAD_MAX_BYTES` | 10,737,418,240 (10 GiB) | Largest accepted upload |
| `CIVICCAST_MEDIA_STORAGE_BUDGET_BYTES` | unset | Disk budget shown on the Storage budget card |
| `CIVICCAST_RETENTION_WORKER` / `CIVICCAST_RETENTION_POLL_SECONDS` | `inline` / 3600 | Flags expired assets for the records clerk's review; never deletes |
| `CIVICCAST_MEDIA_LIFECYCLE_WORKER` / `..._POLL_SECONDS` | `inline` / 300 | Readiness, ingest-time conversion and archive checks |
| `CIVICCAST_WATCH_FOLDER_WORKER` / `..._POLL_SECONDS` | `inline` / 2.0 | The watch-folder importer |
| `CIVICCAST_MEDIA_INTEGRITY_WORKER` | `inline` (checks every 3600 s) | Flags assets whose backing file has gone missing |
| `CIVICCAST_NAS_ARCHIVE_PATH` | unset | Local archive folder; needs `CIVICCAST_PROVIDER_LOCAL_NAS=real` |
| `CIVICCAST_BACKUP_DIR` | unset | A backup folder that overrides the one saved on the Setup page for the probe |

**Control Room and certificates**

| Variable | Default | Effect |
| --- | --- | --- |
| `CIVICCAST_CONTROL_ROOM_TSR_URL` | unset | Address of the Node TSR sidecar, for example `http://127.0.0.1:7717`; unset fails every cue closed |
| `CIVICCAST_CERT_ROOT` | `.civiccast\certs` in the CLI user's home | Where the local CA and service certificates are kept (CLI only) |

For all other variables, the appendix of settings ([Appendix: settings](#app-settings)) has the generated list with the file where each is read.

## If it did not work

| What you see | Likely cause | What to do |
| --- | --- | --- |
| "First setup can only be done from the station computer itself" | You opened `/operator/#/setup` away from the station | Use `/operator/#/setup` on the station computer for first-admin actions. Ordinary staff use `/operator/#/login`; remote staff sign-in requires the configured staff hostname and an approved network. |
| "First-admin setup is already complete. Use the recovery flow or set CIVICCAST_ALLOW_FIRST_ADMIN_RESET=1 for an intentional local reset." | The station is already set up | Sign in; use a recovery code if the password is lost |
| "Too many sign-in attempts from this station. Wait N seconds, then try again with the correct password, or use a printed recovery code." | The sign-in limit was reached | Wait the number of seconds shown |
| "Durable storage is not ready." or HTTP 503 on a screen | The database was not prepared or the service is still starting | Click **Prepare storage** on First Setup; check the service and `postgres.log` |
| The service stops soon after an environment edit | A value is invalid or a configured dependency cannot start | Read the exact control-plane error and correct the named value. `CIVICCAST_OFFLINE_CAPTION_JOB=off` is supported; it is not itself a startup error |
| "No outgoing-feed configuration for &lt;id&gt;. Apply a headend preset or the local rehearsal preset first." | The channel has no configuration | Apply a preset on the Channels screen |
| "Sink kind(s) [...] are not supported by the active GStreamer egress engine." | The configuration contains an unsupported sink kind | Use a supported kind: `srt`, `rtmp`, `udp-ts`, `local-ts`, `file`, `sdi` or `hls`; check the exact kind named in the error |
| "No hardware video encoder was found on this machine. To broadcast on the CPU instead (slower)..." | The profile names a hardware encoder this computer does not have | Tick **Allow software (CPU) encoding fallback**, or change the profile |
| "Start was queued but the feed did not start. The outgoing-feed worker did not report Starting or On air within 20s..." | The feed worker did not respond | Check the service in System Health, then try Start again |
| "channel '&lt;id&gt;' has no udp-ts sink to verify — apply a headend delivery profile first." | Commissioning proof on a channel with no UDP output | Apply the headend preset first |
| "Device host must be localhost, .local, or a private/link-local IP unless a setup admin records a public-host override reason..." | Control Room device on a public address | Use a private address; the screen has no way to record an override, so ask support |
| "The credential store is not available to persist the device secret." / "The OS credential store is unavailable; provider keys cannot be saved here." | The Windows credential store is not available to the service account | Ask support; use `civiccast model set-provider-key` from an account that has it |
| Control Room readiness is blocked at "TSR control service" | The sidecar is not installed or running, or the variable is not set | See [Set up the Control Room](#configuration-controlroom) |
| A setting you changed in the registry has no effect | The service was not restarted; the variable is one the service forces; or the name has the wrong number of C's | Restart the service; check the forced list above; check the spelling |

## Package status

These instructions describe the published Beta 20 edition. Use the release page and the Help and verification record that match the package on the station. The [Beta 20 verification record](https://github.com/scottconverse/civiccast/blob/main/docs/releases/v1.0.0-beta.20-verification.md) identifies that package's scoped checks; the [Beta 18 record](https://github.com/scottconverse/civiccast/blob/main/docs/releases/v1.0.0-beta.18-verification.md) remains historical.

## Related

- [Planning your station](#ch-planning) for the hardware, disk and network choices behind these settings.
- [Installing, first run, upgrading, uninstalling](#ch-installing) for the folder layout, the service and the first-run wizard.
- [Running it day to day](#ch-operations) for backups, logs and updates.
- [Security and privacy](#ch-security) for tokens, sign-in, the credential stores and what the paywall and provider keys expose.
- [Troubleshooting matrix](#ch-troubleshooting) for symptoms and fixes.
- [Cable headend, streaming, CDN, federation, emergency alerts, the API](#ch-integrations) for delivery to a headend, CDN and federation.
- [Appendix: command line](#app-cli), [Appendix: API](#app-api), [Appendix: settings](#app-settings) and [Roles](#app-roles).

<!-- SOURCES: inventory/screens/setup.md; inventory/screens/station-profile.md; inventory/screens/channels.md; inventory/screens/ai-models.md; inventory/screens/commissioning.md; inventory/screens/controlroomsetup.md; inventory/screens/custom-fields.md; inventory/screens/paywall.md; inventory/screens/medialifecycle.md; inventory/screens/installer-install-layout.md; inventory/screens/installer-component-catalog.md; inventory/generated/env-vars.md; inventory/generated/cli.md; docs/releases/v1.0.0-beta.10-verification.md; civiccast/native/supervisor/service.py:483-490,1491-1619; civiccast/native/supervisor/service_env.py; civiccast/native/supervisor/children.py:20-111,281-310,466-551,646-688; civiccast/native/supervisor/config.py:37-58; civiccast/native/supervisor/core.py:388-394,572-583; civiccast/native/station_runtime.py:60-260,672-760,771-813,960-1069,1180-1513; civiccast/egress/env_vars.py; civiccast/egress/models.py:74-135,136-208,423-492; civiccast/egress/router.py:219-249,343-404,614-860; civiccast/egress/headend.py:34-110,140-470; civiccast/egress/ts_relay.py; civiccast/egress/hls_relay.py:1-120,251,344; civiccast/egress/engine_select.py; civiccast/egress/loudness_plan.py; civiccast/egress/loudness_ride.py:1-390; civiccast/egress/automation.py:232-234,750-790,945,1335-1395,3289; civiccast/egress/preparer.py:92,144,243,307,316-367,896-960,3323; civiccast/egress/source_plan.py:75,113-165; civiccast/egress/gst/bridge.py:75-137,190-215,405-425; civiccast/egress/gst/encoder_probe.py:1-148; civiccast/egress/gst/strategy.py:600-660,880-915; civiccast/egress/gst/decode_policy.py:55-80; civiccast/egress/gst/engine.py:245-345,743-806; civiccast/egress/gst/worker.py:719-724; civiccast/egress/ndi_relay.py; civiccast/egress/compliance.py:47-70,230-250; civiccast/installer/station_state.py:216-262,279-380,385-470,598-740,1150-1175; civiccast/installer/models.py:262-380; civiccast/installer/service.py:195-235,1060-1200,3595-3640,3665-3720,4640-4745,4897-4935,5805-5840; civiccast/installer/commissioning.py:196-290,590-620; civiccast/captions/runtime.py:40-200,640-720; civiccast/captions/tap_worker.py:200-235,320-405; civiccast/captions/vod_job.py:60-130,340-530; civiccast/captions/retention.py:40-130,222-365,930-975; civiccast/ai_models/catalog.py:36-215; civiccast/ai_models/models.py:174-199; civiccast/ai_models/runtime.py:100-130; civiccast/ai_models/secrets.py; civiccast/ai_models/cloud/ollama_cloud.py; civiccast/app.py:984-1030,1500-1700,2880-2896,3040-3075,3300-3335; civiccast/auth/rate_limit.py:55-100; civiccast/auth/cors.py; civiccast/common/trusted_proxy.py:1-60; civiccast/certs/authority.py:26-125; civiccast/certs/readiness.py; civiccast/cli.py:898-1010; civiccast/paywall/router.py:232-330; civiccast/paywall/store.py:190-245; civiccast/schedule/retention_worker.py; civiccast/schedule/media_lifecycle_worker.py:372-475; civiccast/schedule/watch_folder_worker.py:208-250; civiccast/schedule/media_integrity_worker.py:85-110; civiccast/schedule/media_lifecycle_router.py:920-935; civiccast/schedule/router.py:808; civiccast/summary/job.py:262-300; civiccast/control_room/tsr_service/README.md; civiccast/subscribe/router.py:150-190; sandbox-lab/scripts/In-Sandbox-Soak.ps1:1131-1232; civiccast/native/app_payload.py:110-125 -->
