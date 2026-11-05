# Install CivicCast on Windows

CivicCast RC4 is a complete native Windows station. It includes Python, PostgreSQL, FFmpeg, GStreamer and local AI runtimes. You do not need Git, Python, Docker, WSL, Ollama or GitHub CLI installed separately.

[RC4 manual PDF](https://github.com/scottconverse/civiccast-native/releases/download/v1.0.0-rc.4/USER-MANUAL.pdf) · [RC4 release notes](docs/releases/v1.0.0-rc.4-release-notes.md) · [Current source manual](docs/USER-MANUAL.md)

`v1.0.0-rc.4` is the current [published release](https://github.com/scottconverse/civiccast-native/releases/tag/v1.0.0-rc.4). Check `setup.exe` against the SHA-256 in the [release notes](docs/releases/v1.0.0-rc.4-release-notes.md), then run it with the adjacent `packs` and `station` folders intact, or use the download command below.

## Download and install the complete station

Use Windows 11 x64, internet for the download, and a Windows administrator account for setup. Allow about **100 GB free** for preparation and installation, plus your media and recordings. The model download is about 22 GB; preparation temporarily holds the parts, assembled archive and extracted models.

Open **Windows PowerShell** and paste:

```powershell
$installerScript = Join-Path $env:TEMP 'CivicCast-Install-RC4.ps1'
Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/scottconverse/civiccast-native/releases/download/v1.0.0-rc.4/download_windows_release_artifacts.ps1' -OutFile $installerScript
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installerScript -Tag v1.0.0-rc.4 -Version 1.0.0-rc.4 -Install -Destination "$env:USERPROFILE\Downloads\CivicCast-RC4"
```

The helper downloads the installer, all five runtime packs and all six signed station packs. It verifies SHA-256 hashes and the installer's valid **Scott Converse** Authenticode signature, and creates the `packs` and `station` folders setup needs. Windows requests administrator approval if needed. Setup uses supported silent mode with the complete kit, without the separate optional raw-model acquisition screen. Wait for it to finish; any actual nonzero exit is reported as failure.

Keep the PowerShell window open until it reports **Done**. During silent
setup, the installer writes a timestamped progress heartbeat before and after
each step to `C:\ProgramData\CivicCast\install-progress.log`; its latest line
names the step in progress.

For download only, replace `-Install` with `-IncludePacks`. Keep the resulting folder together when copying it to USB. The complete folder supplies the dependencies for offline installation.

[RC4 downloader source](scripts/download_windows_release_artifacts.ps1). The source defaults target RC4. The command explicitly supplies both tag and version so it also targets RC4 when a frozen helper asset retains older defaults.

## First start

Open **CivicCast Operator Console** from the desktop or Start menu, or visit <http://127.0.0.1:8000/operator/> on the station computer.

1. In **Setup**, prepare durable storage if prompted, then name the station and create the first administrator. Storage failures show the next recovery step; retry after correcting it.
2. Print or save the recovery kit somewhere safe and confirm it is saved. Use the password you just created for normal sign-in; recovery codes are for a lost password.
3. Follow **Your first published recording** on Setup. Create **Bundled sample video** or upload a short test clip under **Camera or test media**. Saving a source or upload does not prove playback.
4. Open **Assets**, find the recording by title and choose **Package for playback**. Keep the page open until packaging finishes; correct any error before continuing.
5. Open **Publish**, select that recording by title and approve only **Portal**. External archive, CDN and YouTube credentials are not prerequisites for this local recording workflow. Do not enter a secret a second time merely because another optional provider is not configured.
6. Refresh recording progress on Setup. Open **Check resident playback** and verify picture, sound and any available captions. A published flag alone is not that check. Use **System Health** to check the actual installed/configured components before broadcasting.

For a live source instead, choose a configured **Channel** by its displayed name in the source form, save the source, then run broadcast readiness/preflight. Saving an endpoint is not a successful connection test. Provider connection tests use saved details; a saved credential is not proof that its destination is reachable.

The live-caption path automatically retries Whistle while Whisper supplies backup captions. Whistle is the CPU primary for live captions. Whisper supplies recorded transcription and live fallback; supported NVIDIA hardware can accelerate Whisper. The installation path does not require a dedicated GPU.

## Existing or partially installed stations

Use the same complete download and run setup over the installation. Do not uninstall or delete station data first. Setup stops the station during upgrade: back up a working station and plan the interruption. It is intended to preserve accounts, settings, schedules, recordings and model caches. The verification record identifies the exact paths observed; it does not guarantee every historical version pair.

For an unknown existing administrator password, a Windows administrator on the station can use the supported interactive reset with the service stopped:

```powershell
Stop-Service CivicCastSupervisor
& 'C:\Program Files\CivicCast (Native)\runtime\python.exe' -I -m civiccast.cli admin reset-password
Start-Service CivicCastSupervisor
```

It prompts privately, preserves station data and revokes that administrator's existing sessions and recovery codes. Do not place passwords in command arguments or reports.

## Folder layout

The release's `station-models.zip.001`, `.002` and subsequent parts form one ZIP64 archive. **Use the helper to verify and extract them.** Individual parts are not runtime packs.

The layout below is the RC4 kit created by the published-download command. It is derived from the downloader script and the RC4 release's asset list, not from a fresh run of the command.

```text
CivicCast-RC4/
  setup.exe
  setup.exe.sidecar.json
  SHA256SUMS.txt
  station-models.json
  station-models.zip.001 ... station-models.zip.012
  packs/
    native-app-payload.ccpack
    native-server-binaries.ccpack
    native-ffmpeg-runtime.ccpack
    native-ollama-runtime.ccpack
    native-ollama-runtime.ccpack.zip   (downloaded, then extracted to the .ccpack above)
    native-cuda-runtime.ccpack
  station/
    station-index.json
    SHA256SUMS.txt
    core.ccpack
    captions-floor.ccpack
    captions-whistle.ccpack
    summary-gemma4-12b.ccpack
    summary-gemma4-e4b.ccpack
    translation-translategemma-4b.ccpack
```

The helper may also add its own verification files beside `setup.exe`; they are not listed here.

Downloading only setup, placing runtime packs flat beside it, or using a source ZIP does not prepare this layout.

## If installation fails

If a download stops, rerun the same helper with the same destination; verified matching files are reused. Do not mix release versions or use `-Force` merely to retry. If verification reports an incomplete or mismatched file, preserve that exact message, move only the named downloaded file out of the kit, then rerun the same command to obtain that matching file again. Keep station data and the other verified downloads.

Read `C:\ProgramData\CivicCast\install-progress.log`. Exit 110 concerns runtime-pack staging; exit 123 concerns station activation or its self-test. Preserve the log and actual exit code. An Apps entry or copied files alone do not prove success.

First setup must be opened on the station. If its URL refuses connection, check the `CivicCastSupervisor` Windows service and installation log. A missing service is an installation failure, not a password problem.

See [Windows release trust](docs/install/windows-release-trust.md) and [Support](SUPPORT.md). Exclude passwords, recovery codes and provider secrets from reports.

## Package evidence and historical results

[RC4 release notes](docs/releases/v1.0.0-rc.4-release-notes.md) are the current package record. [RC2 package checks](docs/releases/v1.0.0-rc.2-verification.md) document the
published package and its scoped acceptance. [Beta 17 follow-up](docs/ops/beta17-deferred.md)
records deferred field findings.

Beta 15 passed Windows Sandbox installation, first-admin setup, built-in Help and built-in uninstall checks. Its corrected installer also restored a working station automatically after a simulated activation failure. The bundled Welcome programme aired and returned to slate in the short-programme rollover check. The [Beta 15 verification record](docs/releases/v1.0.0-beta.15-verification.md) identifies each tested package and its results.

Historical Beta 13 installation evidence: its normal Beta 11 upgrade, controlled interruption and same-candidate repair, and separate fresh installation apply to Beta 13 only. See the [Beta 13 verification record](docs/releases/v1.0.0-beta.13-verification.md) and [fresh-install evidence](docs/ops/evidence/beta13-fresh-install-observations-2026-10-11.json). Raw Whisper or Ollama directories do not substitute for signed station packs.

## Selective model acquisition decision

The current supported station activation contract requires all six signed station components, including both summary packs and translation. Removing one from a downloaded kit is not a supported small installation. Selective acquisition is feasible only with coordinated signed-index/activation/runtime changes and a smaller supported capability profile; it is deferred as separate implementation work. The complete offline kit and data-preserving upgrade route remain supported.

## Agenda setup and OCR

Published `v1.0.0-rc.2` includes English OCR in its offline runtime. Do not install Tesseract separately or edit service environment variables for ordinary PrimeGov setup. A setup administrator uses **Agendas → Agenda source setup → Use Longmont / PrimeGov → Save agenda source**. Review the chosen meeting’s preview and original pages before confirming. Unreadable pages cannot be confirmed. Use the matching RC2 package verification record for the scope and limitations of installed checks.

Qualified human review of Spanish, Simplified Chinese, Filipino/Tagalog, Vietnamese and Arabic remains pending. RC2 does not claim human-validated translation accuracy; review translated records before publication.
