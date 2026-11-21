# Build assets for another machine

Inventory inspected October 24, 2026. This is an acquisition guide, not a command
to rebuild Beta20. No installers, models, LFS objects or large archives were
transferred for this inventory. [Current state and scope](CURRENT.md).

## Source development versus release packaging

For source work, start with Git, Python 3.12 and uv, then use `uv.lock` with
`uv sync --frozen --group dev` when that environment is needed. Frontend work uses
the relevant app's `package-lock.json` and `npm ci`. Do not automatically install
every optional dependency or run every suite. Native packaging requires x64 Windows;
Python-only edits do not require the previous machine or a station installation.

For a future authorized release build, the executable recipe is
[native-beta-candidate-artifacts.yml](../../.github/workflows/native-beta-candidate-artifacts.yml).
The [local installer guide](../ops/building-the-native-installer.md) contains useful
commands and explicitly historical failures; prefer current locks and workflow
over copied historical version numbers. A signed candidate requires a clean source
checkout. This handoff does not launch that workflow.

## Inputs and how to obtain them

| Input | Source / acquisition | Local-machine requirement |
|---|---|---|
| Python packages | `uv.lock`, PyPI; preserve locked versions | Recreate the environment; do not copy `.venv` |
| Frontend packages | Each app's `package-lock.json`, npm registry | Recreate only the relevant app dependencies |
| Reviewed build tools | `native-windows-build-toolchain.lock.json`; `scripts/provision_native_build_toolchain.py` | Node/npm, build Python, uv and MSVC identities pinned; short new MSVC directory required |
| Embedded Python | `scripts/build_native_app_payload.py`, Python.org 3.12.10 embeddable ZIP | Distinct from build Python; 11,133,606 bytes, SHA-256 `4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3` |
| Runtime archives | `native-windows-runtime-dependencies.lock.json`; `scripts/provision_native_runtime_dependencies.py` | Upstream URLs and exact sizes/hashes pinned; use local mirror first if available |
| GStreamer closure | `scripts/build_native_runtime_closure.py` and `civiccast/native/runtime_closure.py`; pinned upstream wheels | Recreated from the build recipe, not an undocumented local tree |
| Reviewed PyAV wheel | Local reviewed runtime-artifact mirror; details below | Current patched wheel is transferred to hosted reproducibility as a one-day workflow artifact; historical Beta20 asset is preserved |
| Whistle engine/model | `scripts/provision_native_whistle_assets.py`; `civiccast/native/app_payload.py` and existing station bundle | Pinned upstream assets; optional old `build/native-whistle-cache` is a cache |
| Whisper caption models | `civiccast/native/app_payload.py`, `civiccast/native/caption_tiers.py`; existing caption/station packs or pinned model repository | Large model bytes are not Git source; preserve revisions and hashes |
| Ollama product models | `native-windows-ollama-models.lock.json` and `native-windows-ollama-manifests/`; existing signed station bundle or registry through provisioners | Includes Gemma and TranslateGemma; unrelated to the coding agent's model |
| VC++ runtime / NSIS / Rust | Current candidate workflow and installer scripts | Follow their pinned acquisition/toolchain steps only for packaging |
| Pack signing and Authenticode | Existing repository secrets / authorized signing service | Not in Git and must not be copied into a public handoff |
| Station database, media, profiles, credentials | Existing test installation / deliberate secure migration | Not required to compile source; no automatic copying, reset or upload |

## Important local mirror to preserve

The previous machine has `C:\CivicCastTester\runtime-artifact-mirror` with this
layout: `<SHA256 from lock>/<filename>`. Set `CIVICCAST_RUNTIME_ARTIFACT_MIRROR`
to the copied mirror root on the new machine if using it. The provisioner checks
the lock's byte length and hash before use. No new bulk rehash was performed here.

| File | Observed bytes | Purpose |
|---|---:|---|
| `ffmpeg-n8.1.2-50-g1a748fe2cd-win64-lgpl-shared-8.1.zip` | 70,835,150 | Pinned FFmpeg binary archive for pack assembly |
| `TSDuck-Win64-3.44-4676-Portable.zip` | 38,476,542 | Transport-stream tooling |
| `postgresql-17.10-2-windows-x64-binaries.zip` | 333,927,270 | Bundled database binaries |
| `ollama-windows-amd64-v0.30.6.zip` | 1,456,569,673 | Bundled product Ollama runtime |
| `ollama-LICENSE-v0.30.6.txt` | 1,058 | Required license notice |
| `MicrosoftEdgeWebView2RuntimeInstallerX64.exe` | 215,195,856 | Reviewed WebView2 offline installer Tauri embeds in setup.exe (SHA-256 `1d1c838e5971343c060415da7c630c3c1ca7ad33fbb61cca660f3c1122f4cca8`, Authenticode-valid Microsoft signature); the Microsoft fwlink is not a pin |

The FFmpeg mirror subdirectory is
`e9712ffbdb03ef71bbab660c75b835bfe698ef6fad0247c76d8d394a39a3db63`.
Its pinned upstream URL returned **HTTP 200 to a HEAD request** on October 24.
The workflow documents that upstream autobuild archives can be pruned. Preserve
this 70.8 MB original ZIP or copy it directly between machines if necessary;
do not assume an unpacked release pack can reconstruct its exact archive hash.
The mirror is not in GitHub source. No upload or transfer was performed.

The other archives' upstream availability was not rechecked. Their URLs, sizes and
SHA-256 values are in the runtime lock. Existing `kit-staging` and `candidates`
directories on the prior machine are historical caches, not the authoritative
current Beta20 source. The current checkout has no `build/` directory to transfer.

## Reuse the reviewed PyAV wheel

The current security-patched pins are:

- Name: `av-18.0.0-cp311-abi3-win_amd64.whl`
- Bytes: `4348926`
- SHA-256: `5ab3fae3f85d4121919f70643e026452eb2a678b4a3f557b9ca487724c1399f0`

Stage these reviewed bytes in
`C:\CivicCastTester\runtime-artifact-mirror` before starting the candidate or
reproducibility workflow. The self-hosted candidate lane uses this file when
`reviewed_pyav_release_tag` is empty and checks its exact identity against the
builder constants. The reproducibility workflow independently checks the two
reviewed mirror inputs (this wheel and the pinned interpreter ZIP), transfers
them through a one-day same-run workflow artifact, and rechecks identity and
embedded provenance on the hosted runner before its two independent builds.
The transfer never checks out repository source on the internal runner and is
limited to internal pull requests and manual runs.

The Beta20 release asset remains historical evidence; it predates the required
FFmpeg security backports and fails the current pin. Preserve it. An explicit
`reviewed_pyav_release_tag` is still supported for an authorized release containing
the exact current wheel, but this acquisition flow publishes no asset or tag.
For local packaging, pass the reviewed mirror file with `--reviewed-pyav-wheel`.
Never relax the identity checks because a local recompilation differs.
See [PyAV reproducibility](../process/pyav-wheel-reproducibility.md).

## Existing release assets (not Git LFS)

[Beta20 release](https://github.com/scottconverse/civiccast-native/releases/tag/v1.0.0-beta.20)
contains 27 assets: the 26-asset installable kit plus the small CI provenance
asset `av-18.0.0-cp311-abi3-win_amd64.whl` (4,347,090 bytes), which is not part of
the kit and is never installer-required. These are release downloads, not LFS
objects. Download only
what a later task actually requires; no asset download is needed to read/edit code.

| Asset | Published bytes |
|---|---:|
| `setup.exe` | 260,612,848 |
| `native-app-payload.ccpack` | 578,545,643 |
| `native-server-binaries.ccpack` | 76,249,023 |
| `native-ffmpeg-runtime.ccpack` | 144,130,093 |
| `native-ollama-runtime.ccpack` | 1,941,233,058 |
| `native-cuda-runtime.ccpack` | 1,893,729,051 |
| `station-models.zip.001` through `.011` | 1,900,000,000 each |
| `station-models.zip.012` | 1,113,106,169 |
| `USER-MANUAL.pdf` | 7,302,664 |
| `USER-MANUAL.docx` | 5,648,854 |

The release also includes checksums, station index, setup sidecar, manual receipts
and `download_windows_release_artifacts.ps1`. Use their identities and signatures
when acquiring a kit; metadata presence alone is not a download-integrity proof.
The station ZIP is split into twelve ordered parts; the release downloader handles
assembly. Do not download all ~26.9 GB simply to set up source development.

## Signing and access that Git cannot supply

The candidate workflow uses repository variables
`CIVICCAST_PACK_PUBLIC_KEY_BASE64`, `CIVICCAST_PACK_SIGNING_KEY_ID`, and secrets
`CIVICCAST_PACK_SIGNING_PRIVATE_KEY`, `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`,
`AZURE_CLIENT_SECRET`. Secret names are documentation, not assurance that a new
machine/account has permission to use them. Prefer the existing authorized GitHub
signing workflow; do not export keys into the checkout. Other workflow configuration
remains in its source. The self-hosted lane's `C:\CivicCastTester` paths and runner
labels require an appropriately configured runner; they are not clone prerequisites.

No known irreplaceable local-only input was identified for ordinary source work.
A future signed/offline build additionally needs the listed assets and signing access.
A complete fresh-machine build was not attempted or claimed. Preserve the FFmpeg
mirror before decommissioning the old machine, and keep station data/recovery material
separate from a source-code handoff.
