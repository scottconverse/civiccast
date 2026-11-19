# v1.0.0-rc.6 soak report — five test PCs, November 14–15, 2026

Version 1.0 · 2026-11-15 · Written by Claude (Opus 5.5) on the Halo coordinator PC from the station logger data. Bump the version on any change.

**Candidate:** signed kit from source `d74a8c3110618d62b82f25e7383e93258f9a556b` (build run 43576185174). Every PC reported `1.0.0-rc.6`, schema `0098_channel_repeat_schedule` (current) at the end of the soak.

**Result in one line:** five PCs ran 15 channels for 27.6 to 28.5 hours each. The service never stopped. No playout worker exited by itself. Three problems were found: slow `/health` answers on the MSI laptop, one lost reload on R7 that held a channel on the slate for about 16 minutes, and two small caption faults on the cable output ([#342](https://github.com/scottconverse/civiccast/issues/342)). All three are open issues. They are not fixed in rc.6.

## Hardware

All five PCs run Windows 11 and the product's native Windows service (`CivicCastSupervisor`). Hardware was read from each PC on 2026-11-15 ([hardware.json](hardware.json)).

| PC | Processor | RAM | Graphics | Windows | Captions in the soak |
|---|---|---|---|---|---|
| MSI laptop | Intel Core i7-13620H (10 cores, 16 threads) | 15.7 GB | Intel UHD + NVIDIA RTX 4050 Laptop | 11 Home, build 26200 | Off |
| 5060 Ti PC | AMD Ryzen 7 7700X (8 cores, 16 threads) | 31.3 GB | NVIDIA RTX 5060 Ti 16 GB | 11 Home, build 26300 | On, channel `public`, plus a UDP cable output |
| R9 (mini PC) | AMD Ryzen 9 7940HS (8 cores, 16 threads) | 25.8 GB | AMD Radeon 780M (integrated) | 11 Pro, build 26300 | Off |
| R7 (mini PC) | AMD Ryzen 7 8745HS (8 cores, 16 threads) | 27.8 GB | AMD Radeon 780M (integrated) | 11 Pro, build 26300 | On, all three channels |
| Beelink 64 GB (mini PC) | AMD Ryzen 7 255 (8 cores, 16 threads) | 47.8 GB | AMD Radeon 780M (integrated) | 11 Pro, build 26300 | On, all three channels |

Each PC has three channels (`public`, `education`, `government`) with a web (HLS) output. A channel with no scheduled video plays the CivicCast slate (`FALLBACK_SLATE` in the table below).

## How it was measured

- A station logger on each PC writes one row per minute: service state, six `/health` calls (one every 10 s), processor, memory, disk, and per channel the playout state, the age of the newest web segment, frame rate, worker restarts and caption chunks. Files: `C:\CivicCastTester\soak-logger\data\` on each PC.
- The coordinator read each PC every 20 minutes over Tailscale SSH: service state, health, application warnings and errors, playout worker exits (deliberate and not), network and Windows system errors, free memory, and segment age.
- The soak starts at the end of each PC's install (about 02:20 to 03:45 local time, November 14). It ends at the last logger row before the summary was taken (07:44 local time, November 15). The 5060 Ti PC's clock is one hour behind, so its times read one hour earlier.
- Not counted: the first minute after each install (the service was still starting).
- Full copies of the logger data and the application logs are on Halo in `C:\CivicCastTester\soak-evidence-rc6-2026-11-15\`. They are not in the repository (about 200 MB).

## Results

From [summary-stats.json](summary-stats.json). "Health checks failed" counts `/health` calls that got no answer in time; the service itself was running for every minute on every PC.

| PC | Hours | Service restarts | Health checks failed | Minutes healthy | Average CPU | Lowest free RAM | Worker exits not deliberate |
|---|---|---|---|---|---|---|---|
| MSI | 27.6 | 0 | 1,612 of 9,923 (16.2 %) | 75.4 % | 21.4 % | 1.3 GB | 0 |
| 5060 Ti | 28.4 | 0 | 1 of 10,223 | 100 % | 8.4 % | 17.9 GB | 0 |
| R9 | 28.5 | 0 | 0 of 10,260 | 100 % | 10.2 % | 3.8 GB | 0 |
| R7 | 28.1 | 0 | 71 of 10,098 (0.7 %) | 99.3 % | 26.5 % | 14.9 GB | 0 |
| Beelink | 28.5 | 0 | 0 of 10,258 | 100 % | 20.0 % | 31.5 GB | 0 |

Channels:

- **Web segments stayed fresh.** On the 5060 Ti, R9 and Beelink, every channel's newest segment was 0 to 2 s old for the whole soak, except one minute on the 5060 Ti `public` channel at 11:40 station time (50 s; the web relay had no live window and restarted itself). On the MSI, segments were 0 to 1 s old after the first minute. On R7, see the reload problem below.
- **Captions:** 5060 Ti `public` made 22,541 caption chunks; the slowest took 0.34 s. R7 made 32,742 to 33,051 per channel (slowest 1.09 s). Beelink made 34,291 to 34,309 per channel (slowest 0.72 s). Captions were off on the MSI and R9, as configured.
- **Cable output:** the 5060 Ti PC sent the `public` channel with captions to a UDP cable output for the whole soak.
- **Errors:** 0 HTTP 5xx answers on all PCs. Database errors: 3 on the Beelink in its first minute after install, and 1 on the MSI at 02:08 caused by a coordinator diagnostic query (not the product). Application errors: 2 on the 5060 Ti. At 11:40 the web relay restarted itself (above). At 12:04 the caption writer could not replace `active.vtt` (WinError 5), see problem 3.
- **R7 health:** 70 of its 71 failed health checks were in the 05:00 hour on November 15, during the reload problem below.
- **No PC restarted during the soak.** Last boot times: R9 November 6, R7 and Beelink November 11, 5060 Ti November 12, MSI November 13. Automatic Windows updates are off (`NoAutoUpdate=1`) on the MSI, Beelink and R7; this setting was not read on the 5060 Ti PC and R9.

## Problems found

### 1. Slow `/health` answers on the MSI laptop (open, [#343](https://github.com/scottconverse/civiccast/issues/343))

- Failed health checks per hour on the MSI: 0 from 05:00 to 07:00 on November 14, then rising to 105–115 per hour (about 30 % of calls) from midnight to 07:00 on November 15. Timed calls took 150 ms to 1.5 s, sometimes more than 10 s.
- Playout was not affected: the service never stopped, no worker exited, and segments stayed 0 to 1 s old.
- Cause: not found. One real but small part: the `egress_health_samples` table has no index and is never trimmed automatically (the only trim is the manual `civiccast egress trim-health` command). On the MSI it held about 621,000 rows since October 8, and reading it cost about 46 ms per slate channel per `/health` call.
- At 23:33 on November 14 free memory on the MSI fell to 1.3 GB. CivicCast's processes (FFmpeg, Python, PostgreSQL, Ollama) used about 4.7 GB of the 15.7 GB at that time; the rest was other programs on the laptop.
- **Sizing advice (owner, November 15):** a 16 GB laptop like the MSI runs a one- or two-channel station fine. Do not plan three channels on it. This soak tested three channels only; it did not run one or two channels on the MSI.
- The other four PCs, with more memory, did not show this.

### 2. R7: a lost reload held `public` on the slate for about 16 minutes (open, [#344](https://github.com/scottconverse/civiccast/issues/344))

- At 05:43 on November 15 the control plane accepted a reload for R7's `public` channel. The playout worker reached the end of its stream and logged that no reload was in flight, so it held the slate.
- Channel automation kept waiting ("still settling") until its 960 s fallback stopped and restarted the worker at 05:59:48. Those were the two deliberate worker exits on R7.
- The channel showed the slate for about 15 minutes. Captions went to `unknown`, so health said `degraded` for that time.
- It recovered by itself. It did not happen again on R7 or on any other PC.

### 3. Two small caption faults on the cable output (open, [#342](https://github.com/scottconverse/civiccast/issues/342))

- On the 5060 Ti PC the caption writer gave up once on `active.vtt` (WinError 5, likely the cable caption reader had it open). One caption update was lost; the next write worked.
- In a 12 s capture of the cable output, one of four embedded 608 captions lost its first letter.
- The station's cable caption proofs passed on every run.

## What this soak does not show

- The MSI walkthrough was not done. The MSI counts as a soak station only (owner decision, November 14).
- No real cable headend received the cable output.
- No upgrade from rc.5 was tested. No station outside the test PCs runs rc.5 (owner, November 14).
- R9 and the MSI played the slate only; no recorded video was scheduled on them.

## After the soak

On November 15, at about 07:50 local time, the MSI, the 5060 Ti PC and R9 were shut down at the owner's request (travel). Each was read and healthy just before. The Beelink and R7 keep running at the home lab as a longer soak.
