# CivicCast overnight soak prompt (beta.5, kit 148c8d21)

> **Historical prompt:** This is for the Beta 5 kit and its former ports and
> interface. Do not use it as a current test procedure for the published
> release. Follow the current owner-authorized handoff, [tester start page](tester/START-HERE.md),
> and [RC6 verification record](releases/v1.0.0-rc.6-verification.md).
> The old run duration and instructions below are preserved as a dated artifact.


Paste everything below the line into the AI agent on the station PC. One
station per PC. Use the captions setting the owner tells you for that PC.

---

You are running an overnight soak of CivicCast (Native) 1.0.0-beta.5 on this
Windows PC. The station is installed and the operator console is at
http://127.0.0.1:8000. You have an admin sign-in. Do not install, uninstall,
reboot, or change settings other than the ones named here. Do not delete
anything. Never put passwords or recovery codes in any file.

## Settings for this PC
- Live captions: [OWNER FILLS IN: ON or OFF]. Set it in the console under
  Station Profile, "Show live captions on air". Restart each channel after
  changing it.
- Soak length: 8 hours, or until the owner says stop.
- Output: apply the "Generic CBR SPTS over UDP" headend preset on each channel,
  destination udp://127.0.0.1:5000 for Public, :5001 for Government, :5002 for
  Education (or whatever the preset UI offers; the point is that each channel
  has an outgoing feed).

## Setup, once
1. Assets: upload all four clips from the kit's samples\ folder (the 858 MB one
   takes a few minutes). Wait until each shows Ready.
2. Schedule: put clips on all three channels, back to back, so each channel
   has programs covering the next 9 hours. Mix the clips. Five-minute slots are
   fine; the program change is the thing under test.
3. Channels: apply the preset on each channel, then Start each channel. Confirm
   all three show On air within two minutes.
4. Readiness: screenshot it once as the start-of-soak record.

## Every 5 minutes, for 8 hours
Record one CSV row per channel in Desktop\SOAK-samples.csv:
time, channel, state, pid, source label, seconds on air, dropped frames,
captions state, and the RSS in MB of every process named gst-worker or
python.exe that belongs to CivicCast (Task Manager or PowerShell Get-Process).
Also note any red or yellow banner on Readiness.

## Watch for these events and log each with the time
- A channel state other than On air (Stopped, Starting, Changing source,
  error).
- A channel pid that changed since the last sample (a relaunch).
- A program change that did NOT happen at its scheduled time (the source label
  did not change within 30 seconds of the boundary).
- Dropped frames rising.
- RSS of any worker growing steadily across an hour.
- Any alert firing on the Alerts screen.
- With captions ON only: watch the resident portal or the UDP feed in VLC
  (Media > Open Network Stream > udp://@127.0.0.1:5000) for 2 minutes each
  hour and note any freeze longer than 5 seconds.

## At the end
1. Stop all three channels.
2. Copy these files to Desktop\SOAK-evidence\:
   C:\ProgramData\CivicCast\logs\control_plane-app.log
   C:\ProgramData\CivicCast\logs\supervisor.log
   C:\ProgramData\CivicCast\data\egress\<channel>\logs\gst-worker.stderr.log
   and gst-worker.stdout.log for each channel (paths may differ; find them).
3. Write Desktop\SOAK-REPORT.md: start and end times, captions setting, per
   channel the number of program changes, the number of relaunches with their
   times, the longest gap with nothing on air, RSS at start and end, alerts
   seen, and a one-paragraph plain verdict. Then zip Desktop\SOAK-evidence and
   the CSV and report together as Desktop\SOAK-<pcname>-<date>.zip.
