# Historical in-app help audit: beta.10 snapshot

This is the archived documentation-sprint index from 2026-10-03. It describes beta.10 screens and proposed copy from before the Beta.11 audit. The 62 linked per-screen audit files are retained for traceability and each is marked as a historical snapshot. Do not apply their proposed strings as current guidance. The [Beta.11 source inventory](help.md) is also a historical audit; use the [current source User Manual](../USER-MANUAL.md) and [published RC7 verification record](../releases/v1.0.0-rc.7-verification.md) for current guidance and package scope.

## How to use these files

The tables below are the historical beta.10 audit index, not a current implementation checklist. Their mismatch counts, source lines, and proposed copy record what that audit found at the time. Nothing in those archived tables establishes later behavior; use the current manual for operator instructions and [help.md](help.md) only as dated Beta.11 audit evidence.

## Operator console: Run Meeting

| File | Screen | Mismatches |
| --- | --- | --- |
| [live.md](live.md) | Live (nav id: live) | 14 |
| [facility.md](facility.md) | Facility (nav id: facility) | 9 |
| [controlroom.md](controlroom.md) | Control Room (nav id: controlroom) | 10 |
| [remotecontribution.md](remotecontribution.md) | Remote Contribution (nav id: remotecontribution) | 11 |
| [channels.md](channels.md) | Channels (nav id: channels) | 13 |
| [cg.md](cg.md) | CG Board (nav id: cg) | 10 |
| [cgdesigner.md](cgdesigner.md) | CG Designer (nav id: cgdesigner) | 11 |
| [schedule.md](schedule.md) | Schedule (nav id: schedule) | 11 |
| [autoschedule.md](autoschedule.md) | Auto-schedule (nav id: autoschedule) | 12 |
| [guide.md](guide.md) | Program Guide (nav id: guide) | 12 |
| [recording.md](recording.md) | Recording (nav id: recording) | 12 |

## Operator console: Review and publish

| File | Screen | Mismatches |
| --- | --- | --- |
| [assets.md](assets.md) | Assets (nav id: assets) | 15 |
| [missingmedia.md](missingmedia.md) | Missing Media (nav id: missingmedia) | 7 |
| [medialifecycle.md](medialifecycle.md) | Media Lifecycle Settings (nav id: medialifecycle) | 11 |
| [contribute.md](contribute.md) | Contributors (nav id: contribute) | 12 |
| [review.md](review.md) | Review queue (nav id: review) | 9 |
| [agendas.md](agendas.md) | Agendas (nav id: agendas) | 12 |
| [summary.md](summary.md) | Summary review (nav id: summary) | 9 |
| [publish.md](publish.md) | Publish (nav id: publish) | 14 |
| [playback.md](playback.md) | Playback policy (nav id: playback) | 10 |
| [analytics.md](analytics.md) | Analytics (nav id: analytics) | 11 |
| [epg.md](epg.md) | EPG Export (nav id: epg) | 10 |
| [underwriting.md](underwriting.md) | Underwriting (nav id: underwriting) | 10 |
| [appadmin.md](appadmin.md) | App Admin (nav id: appadmin) | 11 |
| [reports.md](reports.md) | Reports (nav id: reports) | 10 |

## Operator console: Setup, health and shell

| File | Screen | Mismatches |
| --- | --- | --- |
| [setup.md](setup.md) | First Setup (nav id: setup) | 14 |
| [_shell-signin-and-session.md](_shell-signin-and-session.md) | Sign-in, sign-out and session messages (nav id: _shell-signin-and-session) | 9 |
| [_shell-navigation-and-roles.md](_shell-navigation-and-roles.md) | Navigation, top bar and roles (nav id: _shell-navigation-and-roles) | 8 |
| [_shell-shared-components.md](_shell-shared-components.md) | Shared components: confirmation boxes, status words, toasts, banners (nav id: _shell-shared-components) | 9 |
| [controlroomsetup.md](controlroomsetup.md) | Control Room — setup (nav id: controlroomsetup) | 11 |
| [station-profile.md](station-profile.md) | Station Profile (nav id: station-profile) | 12 |
| [commissioning.md](commissioning.md) | Cable Commissioning (nav id: commissioning) | 13 |
| [ai-models.md](ai-models.md) | AI Models (nav id: ai-models) | 12 |
| [custom-fields.md](custom-fields.md) | Custom Fields (nav id: custom-fields) | 9 |
| [paywall.md](paywall.md) | Subscription paywall (nav id: paywall) | 11 |
| [health.md](health.md) | Readiness (nav id: health) | 14 |
| [alerts.md](alerts.md) | Alerts & monitoring (nav id: alerts) | 12 |
| [eas.md](eas.md) | Emergency Alerts (nav id: eas) | 10 |
| [activitypub.md](activitypub.md) | Federation (nav id: activitypub) | 11 |
| [help.md](help.md) | Manual (nav id: help) | 20 |

## Resident portal

| File | Screen | Mismatches |
| --- | --- | --- |
| [public-shell.md](public-shell.md) | Resident portal frame: header, menu, report link (route: every page; `index.html` + `#/...`) | 11 |
| [public-home.md](public-home.md) | Home: live now, coming up, latest recordings (route: `#/`) | 10 |
| [public-recordings.md](public-recordings.md) | Browse recordings (route: `#/recordings?q=&year=&body=&cf.<key>=&page=`) | 10 |
| [public-schedule.md](public-schedule.md) | Channel schedule (route: `#/schedule?channel=<id>`) | 8 |
| [public-watch.md](public-watch.md) | Watch a recording (route: `#/watch/<asset_id>`) | 8 |
| [public-player-captions.md](public-player-captions.md) | Video player and captions (shown on Home, on Watch pages and in `?manifest=` previews) | 10 |
| [public-agenda.md](public-agenda.md) | Meeting agenda card (shown beside the video on a Watch page, `#/watch/<asset_id>`) | 8 |
| [public-contribute.md](public-contribute.md) | Submit a program (form at the bottom of Home, `#/`) | 12 |
| [public-paywall.md](public-paywall.md) | Subscription gate (shown in place of the video on a Watch page when a paywall is on) | 10 |
| [public-subscribe.md](public-subscribe.md) | Follow new recordings: email and feeds (section at the bottom of Home, `#/`) | 10 |

## Installer

| File | Screen | Mismatches |
| --- | --- | --- |
| [installer-gui-checking-computer.md](installer-gui-checking-computer.md) | Checking This Computer (Tauri window "CivicCast (Native) Setup", first-run screen 1) | 8 |
| [installer-gui-download-plan.md](installer-gui-download-plan.md) | What CivicCast Needs (Tauri window "CivicCast (Native) Setup", first-run screen 2) | 7 |
| [installer-gui-downloading.md](installer-gui-downloading.md) | Downloading / Setting Up (Tauri window "CivicCast (Native) Setup", first-run screen 3) | 9 |
| [installer-gui-setup-wizard.md](installer-gui-setup-wizard.md) | CivicCast Installer status window (Tauri window "CivicCast (Native) Setup", after the download screens) | 11 |
| [installer-nsis-setup-wizard.md](installer-nsis-setup-wizard.md) | Windows setup: start, folder page, pre-install checks (NSIS wizard, setup.exe) | 7 |
| [installer-nsis-packs-and-verify.md](installer-nsis-packs-and-verify.md) | Windows setup: C++ runtime, pack staging and re-check (NSIS details list and dialogs) | 8 |
| [installer-nsis-upgrade-database.md](installer-nsis-upgrade-database.md) | Windows setup: upgrade engine and database creation (NSIS details list and dialogs) | 9 |
| [installer-nsis-activation-selftest.md](installer-nsis-activation-selftest.md) | Windows setup: station activation and self-test (exit 123; NSIS details list and dialogs) | 6 |
| [installer-nsis-service-finish.md](installer-nsis-service-finish.md) | Windows setup: service, firewall rule, shortcuts and finish (NSIS details list and dialogs) | 7 |
| [installer-uninstall.md](installer-uninstall.md) | Uninstall (Windows Settings, Apps; NSIS uninstaller dialogs and details list) | 8 |
| [installer-failures-and-logs.md](installer-failures-and-logs.md) | Setup failures: exit codes and logs (reference; dialogs, `install-progress.log`) | 6 |
| [installer-component-catalog.md](installer-component-catalog.md) | Component catalog: what each piece is called on screen and on disk (reference; no screen of its own) | 6 |
| [installer-install-layout.md](installer-install-layout.md) | Install layout: folders, service, ports, data and logs (reference; no screen of its own) | 5 |

Total mismatch rows across the files: 645.

## Code fixes found while writing the help (not text work)

These came from the manual writers, the fact-checkers and the help-file writers. Each is a product defect or gap, with the manual chapter or help file that describes it.

| Area | Problem | Where documented |
| --- | --- | --- |
| Summary review | Corrected in beta.12 source: approval uses the reviewed draft fingerprint; approved summaries remain available for signed-record export and history. Package checks are recorded with the matching release | `summary.md`, manual ch. 5 |
| Contributors | Send to schedule likely fails for any submission with a requested air date (no time zone on the producer's date); producers never see operator notes | `contribute.md`, manual ch. 3 |
| Program Guide | Skipped airings are never retried by Refresh guide; the Channel schedule page lists unpublished airings | `guide.md`, manual ch. 3 |
| Auto-schedule | The screen says items need an operator commit; they are created Published and compiled hourly | `autoschedule.md`, manual ch. 3 |
| Alerts | Destinations are empty by default and cannot be attached to a rule; several alert kinds (including emergency-alert source down) have no rule | `alerts.md`, manual ch. 8 |
| Emergency alerts | Polling and auto-surface are off unless CIVICCAST_EAS and CIVICCAST_EAS_AUTO_SURFACE are set and nothing sets them; the portal's emergency box shows a hard-coded placeholder | `eas.md`, `public-shell.md`, manual ch. 8 |
| Paywall | Saving with the signing-secret box blank erases the stored secret; no tiers or checkout routes; no sign-in email is sent | `paywall.md`, `public-paywall.md`, manual ch. 6 |
| Resident subscribe | Notices are never sent; the confirmation link is a bare path with no web address and no page serving it; RSS feeds are empty | `public-subscribe.md`, manual ch. 6 |
| Playback policy | Most settings are not enforced on video | `playback.md`, manual ch. 6 |
| Analytics | The "Telemetry is off" box points to a Reports tab and a Setup switch that do not exist | `analytics.md`, manual ch. 7 |
| Live and Channels | The sample test source passes pre-flight from a file with no camera; Take live with a source last checked more than 30 seconds ago is refused before it re-checks | `live.md`, `channels.md`, manual ch. 4 |
| Facility | Sample data only; no way to send a command | `facility.md`, manual ch. 4 |
| Sign-in page | The only sign-in is on a page titled First setup | `_shell-signin-and-session.md`, manual ch. 2 |
| Installer | The setup page and first-run wizard promise AI models download after setup; setup needs the full kit and stops with exit 110 or 123 without it | `installer-nsis-setup-wizard.md`, `installer-gui-download-plan.md`, manual ch. 10 |
| Installer | The exit 128 and 129 dialogs say nothing was changed; setup has already stopped the service and replaced the program files | `installer-nsis-upgrade-database.md`, manual ch. 10 |
| Installer | The self-test failure dialog says the failing test is named in the installer log; it is not | `installer-nsis-activation-selftest.md`, manual ch. 10 |
| Installer | Uninstall deletes the model-pack cache (about 21 GB) that a reinstall cannot re-download (MA-17) | `installer-uninstall.md`, manual ch. 10 |
| Backup and restore | No `civiccast backup` or `restore` command exists; the DR drill calls the Postgres tools by bare name and most likely fails on native Windows | manual ch. 12 |
| Service configuration | No installer code writes the service `Environment` registry value that the manual's environment-variable recipe relies on | manual ch. 11 and 12 |
| Control Room | The sidecar service has no installer; the effect of Mute, Drop and Close room on a guest's sound was not found in code | manual ch. 4 and 11 |
| Logs | Corrected in beta.12 source: raw control-plane and PostgreSQL logs rotate; old oversized copies age out through rotation. Support-bundle coverage remains a separate limitation | manual ch. 12 and 14 |
| Health | `/health` shows `degraded` only for a non-current database schema | manual ch. 14 |
| Live CDN publisher | `civiccast/live/cdn_publisher.py` is not covered in the architecture; unclear whether it is wired into the shipped app | manual ch. 16 |
| In-app manual | `civiccast/docsite/manual.json` is generated from `docs/USER-MANUAL.md` and must be regenerated; the generator keeps only about 2.8% of the new manual's text, 50 screenshots are missing, and internal links may land on Page not found | `help.md` |
| Captions | Live caption audio is dropped when the caption worker falls behind (the top open engine item) | manual Appendix H |
