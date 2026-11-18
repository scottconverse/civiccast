# CivicCast Meeting Operator Guide

This guide is for the person running the meeting broadcast. You should not need
to install databases, rotate certificates, edit environment variables, or use a
terminal to do this job.

## Your Job

You answer one question: **can residents see and hear the meeting tonight?**

Your main tasks are:

1. Open the operator console.
2. Check **System Health** and the safe-to-broadcast state.
3. Select the meeting and camera/source.
4. Run preflight.
5. Start the broadcast.
6. Watch source health and the resident preview.
7. End the broadcast.
8. Hand the recording to records review.

## Before The Meeting

Open the operator console at least 20 minutes before the meeting.

Look for the safe-to-broadcast state:

- **Ready:** start the meeting workflow when the board is ready.
- **Check before meeting:** you can probably proceed, but read the yellow item
  and decide whether to ask for help.
- **Do not broadcast yet:** stop and get the issue fixed before the public
  meeting starts.

System Health also shows a separate color-coded card for each individual
check (camera, audio, recording path, and so on), in addition to the overall
safe-to-broadcast state above. A card can say things like **not set up yet**
or **needs IT help** even when the overall state is Ready or Check before
meeting.

If a card says **not set up yet**, it is usually optional. If it says
**needs IT help**, contact the station admin.

Run the private rehearsal before the first real meeting and after major setup
changes. Rehearsal uses the same System Health checks and resident preview the
public broadcast uses.

## Choose A Source

Open **Run Meeting** and confirm the correct scheduled meeting is selected.
Then use the source picker to choose the camera or source. Pick the
description that matches the real equipment instead of thinking in acronyms:

| What You Have | What CivicCast May Use |
| --- | --- |
| USB webcam or HDMI capture card | Local camera input or encoder feed |
| Camcorder connected to an encoder | RTMP, RTSP, or SRT source |
| Phone or tablet broadcast app | RTMP or SRT source |
| Control-room or public-access switcher | NDI, RTMP, RTSP, or SRT source |
| Zoom or meeting software | Local capture or an approved stream feed |
| Recorded meeting file | Upload and publish workflow |

If you do not know which one to choose, ask the admin to label sources before
meeting night.

For a no-camera dry run, create the bundled sample under **First Setup**, then
choose **Check broadcast readiness**. This uses a private file rehearsal; the
sample is not a live RTMP camera. Use **Assets** and **Publish** for the separate
recording, review and resident-preview workflow before the room camera is available.

## Remote Ingest

Some rooms or partner facilities send video through a remote relay instead of
directly to the CivicCast computer. In **Run Meeting**, check **Remote ingest**:

- **Recommended** is the path CivicCast thinks is safest right now.
- **Local default** means the room should use the local encoder path.
- **Outbound only** means the relay does not require opening an inbound firewall
  path to the CivicCast computer.
- **Degraded** or **Offline** means ask the admin before relying on that remote
  path.

If the screen says no cloud relay is configured, that is not a failure. Use the
local encoder path unless the admin tells you the meeting must use a relay.

## Outgoing Channel Feed

Some stations also send a local outgoing feed to a cable channel, SRT receiver,
RTMP endpoint, or local transport-stream file. In **System Health** or
**Channels**, look for **Outgoing channel feed**.

The outgoing feed controls require the meeting operator role. If the buttons are
disabled, ask an admin or meeting operator to run them.

Use **Start**, **Reload**, **Drain**, and **Stop** only when station policy says
the channel handoff should change. For the full operating and tester checklist,
read [Channel Egress Operator And Tester Runbook](ops/channel-egress-runbook.md).

## Run Preflight

Preflight should confirm:

- Video is present.
- Audio is present.
- The fallback slate is ready.
- The recording path is available.
- Required publish and archive lanes are ready for your station policy.

Do not broadcast if preflight says a required check failed. If only optional
provider lanes are not set up, you may still broadcast to the resident portal
and publish required records later.

**Read the last three lines carefully.** Syndication, Internet Archive, and the
local NAS archive happen *after* the meeting, so they never stop you going on
air — but preflight is the last convenient moment to notice a problem with
them. Each one says which of three things is true for your station:

- **Configured** — the recording will genuinely be published there afterwards.
- **Running in simulation** — CivicCast will go through the motions and
  **nothing will actually be sent or stored anywhere**. This is what a fresh
  install does until someone sets the tier up. If your body treats Internet
  Archive as its public record of the meeting, do not leave it here.
- **Set up but not working** — someone configured it for real and something is
  missing (usually a credential or an unmounted drive). The message names what.
  The meeting will still record; that one destination will fail afterwards and
  can be retried once it is fixed.

Once every required check passes, start the broadcast in **Run Meeting**.

## During The Broadcast

Watch three things:

1. **Source health:** camera, encoder, and audio.
2. **Resident preview:** what the public portal shows right now.
3. **Recording status:** whether CivicCast is preserving the meeting.

If the source drops, follow the screen's next step. If a fallback slate is
configured and verified, CivicCast shows it instead of leaving residents with a
blank player; the stock build does not claim source-drop detection or slate
failover on its own.

## After The Broadcast

1. Stop the broadcast.
2. Confirm the recording is saved.
3. Add any notes the records clerk needs.
4. Send the meeting to review.

You do not need to approve captions, summaries, signed records, or archive
surfaces unless your station has assigned you the records-clerk job too.

## AI Models

The captions, summary, and translation features run AI models you can view in
the operator console under **Setup -> AI Models**. Live captions use Whistle
as the CPU primary with Whisper fallback; recorded-media captions use Whisper.
These captions run locally. By default summary and translation also use local
models. Some stations also offer optional hosted models
for summary and translation. A hosted selection sends meeting text to a
third-party provider and may incur charges under that provider's current terms;
pricing may be per-token or use another provider-specific billing model, so
check current rates and account terms before enabling it. Only the station
admin can change the selection, and meeting operators can view the current
model.

## Common Problems

| Problem | What To Do |
| --- | --- |
| No audio | Check the microphone, mixer, capture device, or encoder, then rerun preflight. |
| Camera is missing | Confirm the camera is powered on, connected, and selected. Ask for IT help if the source still cannot be reached. |
| Network drops | Keep the local recording. After the meeting, upload the recording and publish the replay. |
| YouTube is not set up | Continue only if the resident portal and required archive lanes are ready. Ask an admin to set up YouTube later. |
| Captions are not ready | Follow station policy: continue with auto-generated captions, hold publish for review, or publish video first and captions later. |

## What Not To Do

- Do not paste tokens, passwords, private keys, or resident data into chat.
- Do not change technical settings during a live meeting unless an admin tells
  you to.
- Do not mark a meeting ready if the screen says **do not broadcast yet**.
