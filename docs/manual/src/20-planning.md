# Planning your station: hardware, network, storage, accounts {#ch-planning}

This chapter helps the person who runs IT at a small city decide which computer to use for CivicCast, what to open or leave closed on the network, how much disk space to set aside, who needs which accounts, and what to download and check before running the installer. Read it before [Chapter 10](#ch-installing). Nothing here changes the computer; it is all planning.

## Before you start

You need three decisions and one fact:

- **How many channels** the station will run at the same time (the lab station described below ran three).
- **Whether live captions matter** on the air. Captions are the part of the system most sensitive to the computer you choose.
- **Who will sit at the station computer** for first setup. The first administrator can only be created from a browser running on the station computer itself ([Chapter 10](#ch-installing), "First setup in the operator console").
- **The installation route:** use the project's release helper to assemble a supported package with its station-model resources: `scripts\download_windows_release_artifacts.ps1 -Install`. It verifies `station-models.json` and all matching `station-models.zip.*` parts before starting setup with `/S`.

```mermaid
flowchart TB
  A[Computer has no CivicCast yet] --> B[Download the complete signed kit]
  B --> C[Run setup and create the first administrator]
  E[Computer already runs CivicCast] --> F[Back up the station]
  F --> G[Run the newer setup over it]
  C --> H[Check health and configure channels]
  G --> H
```

*Figure 9.1. Use the complete signed kit for a first installation or an upgrade.*

## Choose the computer

### What the installer looks at

The installer's first-run window has a screen called **Checking This Computer**. It reads the processor, memory, graphics card and free disk space and shows them. Only one reading can stop you.

| Reading | How it is taken | What it does |
| --- | --- | --- |
| Processor name and logical cores | Windows registry and `GetLogicalProcessorInformationEx` | Shown. Never blocks |
| Memory | `GlobalMemoryStatusEx`, in GB with one decimal | Shown. Never blocks |
| Graphics cards | Windows DXGI adapter list; the software adapter and exact duplicates are skipped | Shown as `<name> (<VRAM> GB)`. Only NVIDIA cards (PCI vendor 0x10DE) change the recommendation |
| Free space on the install drive | `GetDiskFreeSpaceExW` | **Blocks** when free space is less than the total of the default downloads |

A value the installer cannot read is shown as **Unavailable**. It is never replaced with a guess. If the whole check fails, the window says **Hardware check unavailable** and lets setup continue.

> **Known issue (beta.11):** The **Checking This Computer** screen runs after Windows setup has already copied and extracted the program. Its free-space check covers only optional downloads selected in the first-run window; it is not a pre-flight for the full installation. Use the storage table below to plan room for the staged packs, extracted models, conform cache and recordings, and follow the exact package's disk-space message.

### The operating system and software

| Item | What the code and release files show |
| --- | --- |
| Windows, 64-bit | The installer, runtime and Visual C++ prerequisite are all x64. **No minimum Windows version is stated** in the installer configuration or code. The lab station ran Windows 11. We have no record of tests on Windows 10 or on Windows Server |
| Administrator rights | Setup installs for the whole machine (`perMachine`) and asks Windows for approval (UAC). The first-run window runs as the normal signed-in user |
| Visual C++ runtime | Setup installs a bundled offline copy. It may ask for a Windows restart (the runtime installer returns code 3010) |
| WebView2 | The setup window needs Microsoft WebView2. A bundled offline copy is installed silently if it is missing. No internet is needed for it |
| Windows Defender Firewall | Setup adds one rule with `netsh advfirewall`. If that fails, setup stops with code 119 |
| No WSL, no Docker | CivicCast Native runs as a Windows service. It does not use WSL or Docker |
| The older WSL-based CivicCast | If a computer already has the older "CivicCast Installer" (WSL edition), setup may stop with 135 or 127, and uninstalling CivicCast (Native) asks an ownership question. Plan to remove the older edition first |
| Clock and time zone | Keep the Windows clock synchronized and set the station's IANA time zone in Station Profile. Chapter 3 lists the time zone used by each entry form |

One more rule of thumb: dedicate the computer. The station runs a database, a web server, an AI engine and several video processes under the restricted Windows virtual account `NT SERVICE\CivicCastSupervisor`. We do not know of testing alongside other heavy software.

### Graphics card: what it changes

**Live captions use Whistle on the CPU by default.** Whistle publishes its first recognition without waiting for a second reading to agree. One station-wide lock serializes primary recognition across the station's channels. Requests have a 30-second deadline. If Whistle fails or times out, Whisper supplies backup captions while CivicCast automatically attempts to restore Whistle, one channel at a time. No service restart is needed after a successful recovery. Whisper fallback allows up to 120 seconds per request by default so slower CPU recognition can finish; its deadline and audio queues remain finite.

Whisper remains the engine for recorded-media captions and the configured fallback for Whistle. It can also be selected as the live primary through the service environment. CUDA is optional Whisper acceleration; it is not needed for the Whistle primary. AMD and Intel graphics do not provide CUDA acceleration. If `CIVICCAST_WHISPER_DEVICE` is unset, the service uses CUDA only when a supported NVIDIA GPU with at least 8 GB and the staged CUDA runtime are both present; otherwise Whisper runs on CPU. Set `CIVICCAST_WHISPER_DEVICE=cuda` or `cpu` to choose explicitly. `CIVICCAST_LIVE_CAPTION_ENGINE=whistle` selects the default Whistle primary with Whisper fallback; `whisper` selects Whisper for live captions. Restart the service after changing these settings. Needle usage telemetry is disabled; speech processing and model assets remain local.

The installer ships the signed Whistle component with the station packs. **Medium Whisper** is also required for fallback and recorded captions. **Large Whisper** and the CUDA runtime are optional; Large may be preselected on capable hardware.

A graphics card can accelerate **Whisper** and affect the default summary model. It is not required for live captions on the native Whistle path. It does not lighten video encoding (see the encoding paragraph after the tables).

| Your computer has | What the installer recommends | Effect on Whisper |
| --- | --- | --- |
| No dedicated graphics card, or AMD or Intel graphics only | Medium; Large and CUDA remain optional | CPU with int8 by default |
| NVIDIA card with **less than 8 GB** of video memory | Medium; Large and CUDA remain optional | CPU with int8 by default |
| NVIDIA card with **8 GB or more** | Large and CUDA may be preselected | CUDA only when the CUDA runtime files are staged; otherwise CPU |

These Whisper choices affect the fallback, recorded captions, or an explicitly selected Whisper live-primary mode. Native live captions still default to Whistle.

> **Note:** The rule uses video memory as a stand-in for card capability. The code records that an older NVIDIA card with enough memory but no tensor cores can be *slower* on the card than on the processor (one older card missed all 30 deadlines in testing). The station operator can override the device with the `CIVICCAST_WHISPER_DEVICE` environment variable; see [Chapter 11](#ch-configuration).

> **Historical beta.10 behavior:** The older native live path selected the highest installed Whisper tier. Beta.12 defaults to Whistle with Whisper fallback. The October 11 R9 report found that the separate acquisition screen downloaded Large Whisper despite an unchecked option. Use the complete release helper and its supported silent setup path; it does not depend on that acquisition screen.

The AI that writes summaries and translations is chosen by a second rule.

| Condition | Summary model chosen by default |
| --- | --- |
| Any NVIDIA graphics card detected **and** 16 GB of memory or more | `gemma4:12b` |
| Anything else | `gemma4:e4b` |

Do not assume every model is present on every station. Silent setup does not launch the first-run GUI acquisition flow or automatically fetch its models; the GUI offers acquisition separately. A displayed download or completed transfer does not by itself prove that a model was activated. The installed runtime tags and the installer pack-component IDs are different identifiers; use the installed model/runtime status when troubleshooting. The product catalog lists a minimum of 16 GB of memory for the 12B model and 8 GB for the smaller one. A historical no-dedicated-GPU benchmark used a computer with 32 GB of memory and 16 cores (32 threads): the smaller model finished a summary in 94 seconds warm and 128 seconds cold, while the 12B model took 366 seconds once and then failed twice. We have no measurement on a smaller computer.

**The default video encoder runs on the processor.** CivicCast uses OpenH264 software encoding by default. Compatible NVIDIA or Media Foundation hardware encoders can be configured when present; the channel profile and pre-flight determine whether one is used or a CPU fallback is allowed. The default output profile is 1280 x 720, 30 frames per second, H.264 at 6,000 kbps and AAC audio at 192 kbps. For multiple live channels with captions, size the processor for video playout and Whistle's station-wide serialized recognition, then observe the planned workload on the intended computer.

### What was measured on the lab station

The three-channel evidence in the verification record came from one lab computer. The project's oversight notes describe it as Windows 11 with an AMD Ryzen 7 7800X3D (8 cores, 16 threads) and an NVIDIA RTX 5070 Ti. We do not have its memory size on record.

| Measure on that computer | Result |
| --- | --- |
| Three channels on air for 8 hours | One service process the whole time; no restart; 0 watchdog firings |
| Processor load while preparing a long program for the first time | 95 to 98 percent (oversight notes) |
| Conform cache | 46 GB of its 60 GB budget used |

### Our recommendation

These follow from the evidence above. They are not tested minimums, because the project has not published any.

1. **For several live channels with captions**, size the processor for video playout and Whistle's station-wide serialized recognition, then observe and measure the planned workload on the intended computer.
2. **Without an NVIDIA card**, Whistle live captions still run on CPU. An NVIDIA card can accelerate Whisper fallback/recorded captions and may allow the larger summary model; neither the GPU nor CUDA is required for Whistle. Test the summary and caption workload on the intended machine.
3. **Do not run other large programs on the station** during meetings.

To see what CivicCast itself detects after install, open `http://127.0.0.1:8000/api/hardware` in a browser on the station (it needs no sign-in) or run `civiccast doctor` ([Appendix A](#app-cli)).

## Plan the network and firewall

### What listens where

The application services listen on the station computer's loopback address. To provide remote resident and staff access, use the supported Caddy configuration with separate public and CIDR-restricted staff hostnames; do not expose port 8000 directly. See the [network access guide](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/network-access.md).

| Port | Used by | Reachable from | Source |
| --- | --- | --- | --- |
| TCP 8000 | The CivicCast web server: operator console at `/operator/`, resident portal at `/`, the API, and `/health` | The station computer only | `children.py`, `core.py` |
| TCP 5432 (or the first free of 5433, 5434, 5435, 5544) | PostgreSQL database | The station computer only | `provision/models.py`, `port_select.py` |
| TCP 11434 | The bundled Ollama AI engine (version 0.40.13) | The station computer only | `children.py` |
| TCP 38474 | The setup window's one-copy-at-a-time guard | The station computer only | `main.rs` |
| UDP 17800 upward; UDP 18000 to 18499 | Internal media relays between the playout engine and ffmpeg or TSDuck. Source ports for outgoing transport streams sit 1,000 above the first range | The station computer only | `ts_relay.py`, `hls_relay.py` |

Do not open the internal ports on the firewall. If another program on the same computer already uses 11434 or 8000, plan to move it; we did not test what CivicCast does when a port is taken.

Setup adds a Windows Firewall rule named **CivicCast (Native) Portal/API (TCP 8000)**, but the application listener remains loopback-only. The rule does not expose it to other computers; configure the Caddy edge and its firewall separately for remote access.

### What the station connects out to

| Purpose | Destination | When |
| --- | --- | --- |
| First-run window downloads (signed runtime packs) | `github.com` release downloads (and the hosts GitHub redirects to) | Only if the window has to download something. Redirects that are not HTTPS are refused |
| First-run window downloads (caption weights) | `huggingface.co` | Same |
| First-run window downloads (AI model) | `registry.ollama.ai` | Same |
| Windows setup itself | None. The setup phase makes no network call; the Gate A run had networking disabled | Never |
| Video and publishing outputs | Whatever you configure: a cable headend (UDP or SRT), a CDN, a streaming service, email, a federation server | [Chapter 15](#ch-integrations) |

The station does not need the internet to run. The Gate A run had networking disabled, and its station installed, came up and passed its checks. The code even hides the built-in `/docs` page because "a council-chamber station is frequently firewalled outbound and sometimes air-gapped".

The first-run GUI has a separate model-and-caption acquisition flow. Its optional selections control that download plan; required components remain included. Silent setup uses the complete signed kit rather than opening this GUI. A downloaded raw model directory does not replace the signed station resources needed for activation.

If a security appliance does TLS inspection or an allow-list, allow the destinations above needed by the selected GUI acquisitions, or use a complete local kit where available and block outbound access.

Two details matter if you control outbound traffic. A UDP transport stream to a headend leaves from a local port pinned 1,000 above the relay's listening port, so a firewall that filters by source port needs to know that. And the Gate A test harness added its own allow rules for `tsp.exe`, `ffmpeg.exe` and `ffprobe.exe`; the product does not add them. If a third-party firewall prompts on first use of those programs, allow them. The other outputs depend on what you configure ([Chapter 15](#ch-integrations)).

## Plan storage

CivicCast keeps the program under the install folder (default `C:\Program Files\CivicCast (Native)`) and everything it creates under `C:\ProgramData\CivicCast`. The data folder follows the `PROGRAMDATA` environment variable and is on the system drive unless that variable is changed. We know of no test that moved it.

| What | Where | Size, with source |
| --- | --- | --- |
| Program, runtime, video tools, AI engine | `<install folder>` | Signed runtime packs are staged here; check the matching release checksum and package manifest for exact sizes |
| Model packs: cached copy | `<install folder>\packs\.station-cache` | "about 21 GB" (the uninstall notice) |
| Model packs: extracted copy | `<install folder>\packs\captions-floor`, `components\`, `models\ollama\` | Extraction is about 1 to 1 with the pack size (code constant). The cached copy stays on disk too, so models take about twice the pack size |
| First-run downloads, if any | `C:\ProgramData\CivicCast\packs` and `components` | The download plan includes required components and the optional models selected in the GUI |
| Database | `C:\ProgramData\CivicCast\data\pgdata` | Holds records, not video; grows with use. We have no measured figure |
| Uploads and finished packages | `C:\ProgramData\CivicCast\data\uploads` | Operator-uploaded media |
| Conform cache (ready-to-play copies of programs) and working files | `C:\ProgramData\CivicCast\data\egress` (cache in `conform-cache`) | Budget **60 GB** by default; set `CIVICCAST_CONFORM_CACHE_GB` to change it (zero or less turns the cache off). Another budget keeps the three newest plan folders and about 5 GB (`CIVICCAST_PREPARED_PLAN_DIR_BUDGET_GB`) |
| Temporary live-caption work | `C:\ProgramData\CivicCast\data\caption-tap` | Short working audio is removed during normal processing; native live captioning does not create permanent per-cue review rows or evidence WAVs |
| Scheduled recordings | In a `scheduled-recordings` folder under the recording target you set in the console | One program-hour is about 2 GB by the Setup screen's planning figure; the default output profile works out to about 2.8 GB per hour (6,192 kbps x 3,600 s, our arithmetic) |
| Logs | `C:\ProgramData\CivicCast\logs` | Supervisor, application and raw control-plane/PostgreSQL logs rotate at 10 MiB with 10 older copies. Older oversized raw logs may remain until rotation replaces them; `ollama.log` is not bounded. See [Chapter 12](#ch-operations) |
| Backups | A folder you choose | You decide |

Use the checksums for the matching release and the receipt for any local kit you received; pack sizes can change between releases. Activation checks staged station-pack sizes plus 2 GB of working room and reports "Not enough free disk space to activate this station..." when short. Add the conform cache budget (60 GB by default) and recording storage. The matching verification record identifies measured package observations.

> **Tip:** Do not rely on the default 60 GB cache fitting on a small system drive. Either give the station a large drive, or lower `CIVICCAST_CONFORM_CACHE_GB` before the first busy week. A single prepared program larger than the whole budget cannot be kept. The station then refuses it with the error "Conform-cache budget too small to retain '&lt;file name&gt;'; increase CIVICCAST_CONFORM_CACHE_GB or exclude this asset."

> **Known issue (beta.11):** The Setup screen's **Backup destination** control only proves that the folder accepts a test file (it writes, reads and deletes one). Its success message is "Backup destination accepted a write/read/delete proof." It does not copy station data there. See [Chapter 12](#ch-operations) for how backups are actually made. Use a local drive or a network path the restricted service account can reach; a drive letter mapped only in your session is not visible to a Windows service.

## Plan accounts and permissions

| Who or what | What they need | Why |
| --- | --- | --- |
| The person who installs | A Windows administrator account | Setup is per-machine and uses UAC. Run a silent install from an elevated prompt |
| The Windows service | Nothing to create | It is registered as **CivicCast Native Supervisor** (`CivicCastSupervisor`), runs as the restricted virtual account `NT SERVICE\CivicCastSupervisor`, starts automatically, and restarts after 5, 10 and 30 seconds if it fails |
| The person at the station for first setup | A normal Windows account on the station computer and a browser | First setup, sign-in and recovery only work from the station itself (a loopback check; other computers get HTTP 403) |
| The first CivicCast administrator | A display name, a username, a password of **12 characters or more**, and a place to keep the recovery kit | Created by the browser form in [Chapter 10](#ch-installing). This recovery account carries all five roles. A setup admin creates other staff accounts and assigns their roles ([Chapter 13](#ch-security), [Appendix F](#app-roles)) |
| A person who keeps the recovery kit | A safe place that is not a public folder | The kit holds eight one-time recovery codes and, in the saved file, the administrator password in plain text |
| Security software | An exclusion or allow rule for `<install folder>` and `C:\ProgramData\CivicCast` if it blocks writes | The first-run window's own error text names "security software blocking the CivicCast folder" as the usual cause of a write refusal |
| Backup and publishing destinations | Credentials you hold | Chapter 15 covers provider credentials |

Windows accounts matter in one more place: the first-run window remembers that it has finished in `%USERPROFILE%\.civiccast` and in the browser storage of the Windows account that ran it. A different Windows account sees **Checking This Computer** again.

## Download and verify what you need

### What is on the release page

This manual describes the 1.0.0-rc.4 edition. Use the [releases page](https://github.com/scottconverse/civiccast-native/releases) and the version-specific verification record for the package you use to confirm publication, download, installation, and acceptance status. Run the release-matched helper to obtain the installer, runtime packs and signed station-model resources together. The [Beta 18 verification record](https://github.com/scottconverse/civiccast-native/blob/main/docs/releases/v1.0.0-beta.18-verification.md) remains historical.

The complete offline kit contains the installer, its verification metadata, runtime packs, and signed station index/model packs. The exact file list comes from the matching release manifest; the Chapter 10 tree shows the working layout rather than every download or checksum file. The release helper uses `station-models.json` and every matching `station-models.zip.*` part, verifies the set and prepares the folders before setup. Raw model downloads or unsigned substitutes are not activation packs.

The assembled kit includes Whistle and the signed station-model resources. The first-run GUI separately offers optional model downloads; leaving Large unchecked excludes it from that GUI plan. See [Chapter 10](#ch-installing) for the complete-kit and GUI workflows.

### Check the files

1. Put `setup.exe`, `SHA256SUMS.txt`, `setup.exe.sidecar.json` and any `.ccpack` files in one folder. Open PowerShell there.
2. Compute the installer's hash and compare it with the same file's line in `SHA256SUMS.txt` and with the `sha256` value in the sidecar. All three must match exactly.

   ```powershell
   Get-FileHash .\setup.exe -Algorithm SHA256
   Get-Content .\SHA256SUMS.txt
   Get-Content .\setup.exe.sidecar.json
   ```

   Hashes change with each package. Compare the whole value from the release page you downloaded from, not from an example in this manual.
3. Check the signature:

   ```powershell
   Get-AuthenticodeSignature .\setup.exe | Format-List Status, SignerCertificate
   ```

   Expect `Status` to be `Valid` and the signer to be `CN=Scott Converse` (Azure Trusted Signing; the project's signing policy records this). Any other result is a reason to stop.
4. Check the packs the same way: `Get-FileHash .\*.ccpack -Algorithm SHA256` and compare each with `SHA256SUMS.txt`. The installer re-checks every pack's signature itself.

A **kit** has its own checks. Use the kit's own hash list or delivery manifest from whoever handed it over, and do not expect the GitHub sidecar in a kit. The installer inside a kit may carry a different file name than `setup.exe`; use the name in the kit's manifest.

### The Windows SmartScreen warning

Windows may show a blue **Windows protected your PC** screen. That screen is about download reputation, which a new publisher has not built up yet. It is not a verdict on the signature. Check the hash and the signature first (steps above). If both are right, choose **More info** and then **Run anyway**. If those choices are missing, or the publisher or hash differs from what you checked, stop. Your IT policy can also allow the signed build by publisher or by hash.

### Pre-install checklist

- [ ] I know this is a beta candidate and who in the city has accepted that.
- [ ] For a first install, I have prepared the matching kit with `scripts\download_windows_release_artifacts.ps1 -Install`; the helper verifies and assembles runtime and signed station resources before setup runs. The R9 asset set failed activation because required station packs were missing.
- [ ] I checked the installer's SHA-256 against two sources and its signature shows `Valid` for the expected signer.
- [ ] The computer is 64-bit Windows, dedicated to CivicCast, and not running the older WSL-based CivicCast.
- [ ] The install drive has about 55 GB free, plus room for the conform cache and recordings.
- [ ] I have an administrator account and the computer will stay powered and awake for the whole install (Gate A took about 33 minutes from start to a healthy station inside Windows Sandbox; a real computer will differ).
- [ ] Windows Defender Firewall is on and not locked by policy against adding a rule, or I have a plan for exit code 119.
- [ ] Security software will not quarantine `C:\Program Files\CivicCast (Native)` or `C:\ProgramData\CivicCast`.
- [ ] I know the plan for backups ([Chapter 12](#ch-operations)) and where the recovery kit will be kept.
- [ ] Someone will be at the station to create the first administrator in a browser on that computer.
- [ ] If this is an upgrade, I have a separate backup and recovery path. Beta 12 has one observed normal upgrade from Beta 11 with the recorder check incomplete; see its verification record for the exact result.
- [ ] I have a way to copy text out of the setup window and out of `C:\ProgramData\CivicCast\install-progress.log` to send to support.

## If it did not work

| What you see | Cause | What to do |
| --- | --- | --- |
| The hashes do not match | A damaged or altered download | Do not run it. Download again from the exact release page, or ask the person who gave you the kit for a new copy |
| `Get-AuthenticodeSignature` is not `Valid` | The file is not the signed release | Stop. Do not install |
| Hardware check unavailable. "CivicCast could not check this computer's hardware. It will not guess..." | The probe failed | Setup can continue. If a download later runs out of room, free space and choose Retry |
| Not enough free disk space. "This drive doesn't have enough free space: needs X.X GB free, this drive has Y.Y GB." | The first-run window found too little room for its downloads | Free space on the drive, then close the window and open **CivicCast (Native)** again so the check repeats |
| "Not enough free disk space to activate this station..." in the setup details list (exit 123) | Activation needs the model-pack sizes plus 2 GB | Free the space the message names and run setup again. Nothing was deleted |

## Historical measurements and package evidence

These host measurements belong to Beta 11 and are not Beta 16 capacity results. The 72-hour run covered Public, Government and Education without a service restart and recorded **99.78% live caption-input coverage**; the earlier 48-hour window recorded **99.88% rounded coverage**. See the [Beta 11 72-hour host report](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/beta11-host-caption-performance-72-hour-2026-10-11.md) and [Beta 11 48-hour host report](https://github.com/scottconverse/civiccast-native/blob/main/docs/ops/beta11-host-caption-performance-48-hour-2026-10-10.md) for the methods and run identities. Package checks belong to the verification record for the matching release; the [Beta 18 record](https://github.com/scottconverse/civiccast-native/blob/main/docs/releases/v1.0.0-beta.18-verification.md) tracks its package results.

## Related

- [Chapter 10: Installing, first run, upgrading, uninstalling](#ch-installing)
- [Chapter 11: Configuring the station](#ch-configuration)
- [Chapter 12: Running it day to day](#ch-operations)
- [Chapter 13: Security and privacy](#ch-security)
- [Chapter 15: Cable headend, streaming, CDN, federation, emergency alerts, the API](#ch-integrations)
- [Appendix A: command-line reference](#app-cli)

<!-- SOURCES: docs/releases/v1.0.0-beta.10-verification.md; docs/releases/release-truth.yaml; INSTALL-WINDOWS.md (topics only); docs/tester/START-HERE.md (setup.exe size, kit); docs/install/windows-release-trust.md; CODE_SIGNING_POLICY.md; SUPPORT.md; gh release view v1.0.0-beta.10 (asset names, sizes) and downloaded SHA256SUMS.txt / setup.exe.sidecar.json; ops/docs-sprint/inventory/screens/installer-gui-checking-computer.md, installer-gui-download-plan.md, installer-component-catalog.md, installer-install-layout.md, installer-nsis-setup-wizard.md, installer-nsis-activation-selftest.md, installer-nsis-service-finish.md, setup.md; civiccast/apps/installer/src-tauri/src/hardware_inventory.rs:21-241; src-tauri/src/native_activation.rs:20-60,820-880; src-tauri/src/native_distribution.rs:725-770; src-tauri/src/main.rs:30-40,3296-3330,3682-3750; src-tauri/src/acquisition_catalog.rs:210-262,670-690; src-tauri/nsis-hooks-bootstrap.nsh:150-200,625-700,702-770,2196-2260; src-tauri/tauri.native.conf.json; src-tauri/src/native_service_registration.rs:154-167,2765-2790; civiccast/platform/hardware.py:1-120,349-357; civiccast/ai_models/models.py:174-199; civiccast/ai_models/catalog.py:60-130; civiccast/app.py:1179-1193; civiccast/native/station_runtime.py:672-730,771-795,816-820,960-1030,1075-1110; civiccast/captions/runtime.py:100-200; civiccast/native/supervisor/children.py:34,146-152,272-290,460-560; civiccast/native/supervisor/core.py:388-394; civiccast/native/supervisor/service.py:34,192-193,2050-2110; civiccast/native/supervisor/install_layout.py:160-275; civiccast/egress/preparer.py:1-30,90,144,243,301-315; civiccast/egress/models.py:116-134; civiccast/egress/gst/graph.py:19; civiccast/egress/ts_relay.py:35-95; civiccast/egress/hls_relay.py:247-262,347; civiccast/recording/runtime.py:55-80,204,528; civiccast/installer/service.py:1075-1140; civiccast/installer/models.py:325-365; civiccast/apps/portal-operator/src/screens/SetupScreen.tsx:1217-1229; ops/beta10-oversight/POST-BETA10-BACKLOG.md items 3; ops/beta10-oversight/OVERSIGHT-LOG.md:115,589; ops/beta10-oversight/briefs/U29.md:28, U68.md:30; docs/releases/evidence/v1.0.0-beta.10-gate-a/run5-clean-lane-PASS-b6520847/ (summary.json, T3T5-RESULT.txt); sandbox-lab/CivicCastSandboxTest.wsb (MemoryInMB 16384) -->
