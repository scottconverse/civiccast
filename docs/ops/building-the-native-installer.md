# Building the native Windows installer

How to build `CivicCast (Native)_<version>_x64-setup.exe` on a Windows
workstation, including a historical reproducibility diagnosis.

Nothing in this repository documented this before. Every step below was
recovered by reading `.github/workflows/native-beta-candidate-artifacts.yml`
and running it, so it is a record of what actually happens rather than what
was intended.

> **Status boundary (October 11, 2026):** The failure described below is a
> historical diagnostic snapshot, not the current candidate build result.
> Beta 12 source `a0c98cb7367dc106042ec89d35a91905470d350c` completed the
> signed candidate-assets workflow in run `38097350705`; see the [Beta 12
> verification record](../releases/v1.0.0-beta.12-verification.md). That build
> result does not establish fresh-install, repair, or sustained-operation
> acceptance. The diagnostic below is retained as history.

---

## What builds what

| Script | Produces |
| --- | --- |
| `scripts/provision_native_build_toolchain.py` | the reviewed node / npm / python / uv, and the reviewed MSVC Build Tools |
| `scripts/build_native_runtime_closure.py` | the GStreamer runtime tree (221 files) |
| `scripts/build_native_pyav_wheel.py` | the LGPL-only PyAV wheel, compiled from source |
| `scripts/build_native_app_payload.py` | the application payload tree |
| `scripts/build_native_installer.py` | the **unsigned** installer `.exe`, orchestrating all of the above |

Signing is separate from the local unsigned build. The candidate workflow can
sign when its configured Azure credentials are available; the successful
Beta 12 run demonstrates signing for that exact candidate, not credential
availability on this workstation. See "Signing".

## Prerequisites

* 64-bit Windows.
* Git, and a checkout with a **clean working tree**. `build_native_app_payload`
  refuses a dirty source: release payloads require a reproducible checkout.
  (`--allow-dirty-source` exists for explicitly non-release proof builds.)
* About 3 GB of free disk for the toolchain, plus ~1 GB of build trees.
* Nothing else. The reviewed toolchain is downloaded and hash-verified; it does
  not use, and does not modify, any Visual Studio already on the machine.

## 1. Provision the reviewed toolchain

```powershell
python -I -B scripts/provision_native_build_toolchain.py `
  --cache  "$env:TEMP\civiccast-toolchain-cache" `
  --output build\wp1-native-toolchain `
  --msvc-install "C:\ccmsvc"
```

Every byte is pinned by SHA-256 in `native-windows-build-toolchain.lock.json`.
On success it prints the verified identities: node `v24.16.0`, npm `11.12.1`,
python `3.12.13`, uv `0.11.15`, and MSVC compiler `19.50.35730`.

Two things that will waste your time otherwise:

* **`--output` must be empty or absent.** It refuses a non-empty directory.
* **`--msvc-install` must be a NEW directory, not an existing Visual Studio.**
  Point it at your installed VS and the script verifies *that* compiler and
  rejects it — e.g. `MSVC compiler is not reviewed 19.50.35730: ... Version
  19.44.35228`. Given a fresh path it downloads the pinned
  `vs_BuildTools-18.5.2.exe` and installs a self-contained copy there.
  Keep the path short: Microsoft's installer rejects a full path of 80
  characters or more, *after* downloading the product.

## 2. Cache the pinned payload interpreter

The payload embeds CPython 3.12.10 (the **embeddable** package, which is not
the same binary as a normal 3.12.10 install):

```powershell
Invoke-WebRequest `
  -Uri "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip" `
  -OutFile "build\native-app-cache\python-3.12.10-embed-amd64.zip"
```

Reviewed identity: 11,133,606 bytes, SHA-256
`4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3`.

## 3. Build

Order matters, and getting it wrong fails late and confusingly.

```powershell
$toolchain = (Resolve-Path "build\wp1-native-toolchain").Path
$env:UV_PYTHON            = "$toolchain\python\python.exe"
$env:UV_PYTHON_DOWNLOADS  = "never"
$env:UV_PROJECT_ENVIRONMENT = "C:\ccbuildvenv"
& "$toolchain\uv\uv.exe" sync --frozen --all-groups `
    --python "$toolchain\python\python.exe" --project .

# MSVC FIRST: importing vcvars re-exports the whole cmd environment, PATH
# included, so a PATH prefix set before this is silently wiped.
cmd /c '"C:\ccmsvc\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1 && set' |
  ForEach-Object { if ($_ -match "^([^=]+)=(.*)$") {
      Set-Item ("Env:" + $matches[1]) $matches[2] -ErrorAction SilentlyContinue } }

# Toolchain prefix SECOND.
$env:PATH = "C:\ccbuildvenv\Scripts;$toolchain\node;$toolchain\uv;$env:PATH"

& "C:\ccbuildvenv\Scripts\python.exe" scripts\build_native_installer.py
```

The build venv is seeded *from* the reviewed interpreter, so
`sys._base_executable` resolves to the reviewed build while the venv still
carries the project's dependencies. Neither a bare PATH-prepend of the
provisioned python nor an unreviewed system python satisfies both at once.

Why the ordering rule is spelled out: prepending the toolchain before
importing vcvars leaves the ambient `uv` on PATH, and the build then dies four
stages later with `uv executable SHA-256 68a22cba... != pinned d4ffe0b7...`,
which does not obviously mean "your PATH got clobbered".

During the application-payload stage, the embedded Python runtime probe starts
the complete app lifespan to verify mandatory imports, audio decoding, and both
packaged portal deep links. The builder removes inherited `DATABASE_URL` and
`CIVICCAST_*` values for that child and points its profile and application-state
resolvers at a fresh temporary directory beside the payload build. That
directory is removed whether the probe passes or fails. This prevents the
current app factory's environment- and profile-resolved stores from adopting
the build operator's real station database, CivicCast configuration,
`%LOCALAPPDATA%`, `%PROGRAMDATA%`, or home-directory state. It is environment
routing, not an operating-system sandbox; probe code would still be able to
access an explicitly named path outside the temporary root. This changes the
build-time validation process only: it does not change the source files
selected for the shipped application, installer behavior, or installed-station
storage resolution. A rebuild from the fix commit still carries that commit's
new source identity and artifact hashes.

### Useful flags

* `--stage-only` — stop after staging and verifying both payloads; skip Tauri.
* `--skip-closure-build` — reuse an existing tree at `--tree-out`.
* `--skip-app-build` — reuse an existing payload at `--app-tree-out`.

## Historical diagnostic: where the earlier build stopped

Measured on a clean Windows 11 workstation, 2026-08-20, with everything above
verified as matching the reviewed identities:

| Stage | Result |
| --- | --- |
| Runtime closure | **PASS** — 106 PE files, 221 files, 75,319,816 bytes; `manifest_verification: PASS` byte-for-byte |
| Toolchain provisioning | **PASS** — every hash matched, MSVC `19.50.35730` |
| PyAV compile | **PASS** — compiled, linked, and repaired into a wheel |
| PyAV reproducibility pin | **FAIL** — `av-18.0.0-cp311-abi3-win_amd64.whl byte length 4346817 != pinned 4346940` |

`build_native_pyav_wheel.py` requires the wheel it just compiled to be
**byte-identical** to a pinned SHA-256. This machine's is 123 bytes different.

### Where the difference actually is

A copy of the reviewed wheel exists on this workstation
(`sandbox-lab/hoststore/install/runtime/WHEELS/`), so the two were unpacked and
compared file by file rather than guessed about.

**All seven FFmpeg DLLs differ in content while being byte-for-byte identical
in size:**

| DLL | bytes | pinned sha256 (16) | this build (16) |
| --- | ---: | --- | --- |
| avcodec-62 | 3,514,880 | `1b8a35bf0bd28b3f` | `a8553288873154e4` |
| avdevice-62 | 107,008 | `5f0e7e212e01d810` | `7be1228dd23aafe6` |
| avfilter-11 | 254,976 | `18b4c7562251d5b3` | `03688250e4d600f0` |
| avformat-62 | 708,096 | `d3549e7b0207d77e` | `62f70c7e6a2ba107` |
| avutil-60 | 1,126,912 | `d78abe5993aa05b7` | `5c762295f9eaa780` |
| swresample-6 | 231,936 | `64fdc591075021be` | `986cd78fbbdd46b6` |
| swscale-9 | 1,175,040 | `6d2f4546dfa64584` | `0bee8162081880b5` |

Everything else follows from that. `delvewheel` mangles each DLL's filename
with a hash of its content, so all seven names change; every `.pyd` embeds
those names in its import table, so all 44 extension modules change while
keeping their exact sizes; `RECORD` changes because the names did. The 123-byte
total delta is the zip and RECORD bookkeeping around a much larger content
difference.

Comparing `avutil` byte-wise: 170,697 differing bytes in 7,055 runs, mostly
one- and two-byte deltas in address operands, many of them a constant `0x70`
apart. That is shifted code layout, not metadata.

Both DLLs carry a `IMAGE_DEBUG_TYPE_REPRO` (type 13) debug entry, so both were
reproducibly linked and their `TimeDateStamp` is a content hash rather than a
clock reading — `0x38e86ec9` against `0x9e72ac9b`. The difference is real
compiled output.

### What is and is not pinned

Pinned, verified, and matching on this machine:

* compiler `19.50.35730`, linker `14.50.35730.0`
* MSYS2 base, and `diffutils`, `make` and `nasm` — every FFmpeg build tool,
  by SHA-256
* the FFmpeg source archive and the PyAV source, by SHA-256
* `delvewheel==1.13.0`
* `SOURCE_DATE_EPOCH`, `PYTHONHASHSEED=0`
* the repacked provenance notice, which is entirely constants

Not pinned:

* **the Windows SDK's servicing level.** `Windows11SDK.26100` names a component
  and `10.0.26100.0` is a directory name; nothing checks which servicing build
  of the ucrt/um headers and libraries is inside it. This is the best-supported
  remaining hypothesis for the divergence, and it is a hypothesis, not a
  finding.

One caveat about `/Brepro`: `reproducible_build_environment` sets `CL` and
`LINK` to `/Brepro`, but `build_minimal_ffmpeg` deliberately pops both before
configuring FFmpeg (stray flags in `CL` break configure's compile probes). It
passes them back through configure instead, which is the right way — the
configure string embedded in both DLLs is byte-identical and ends:

```
--extra-cflags='/Brepro @civiccast-cl.rsp' --extra-ldflags=/Brepro
```

### The build already does everything reasonable

This matters for what the fix can be. `civiccast-cl.rsp` is written per-build
as:

```
/experimental:deterministic
"/pathmap:<source_dir>=C:\CivicCast\FFmpegSource"
```

So the FFmpeg build already: links with `/Brepro`, compiles with
`/experimental:deterministic`, normalises the source path with `/pathmap` so
the build directory cannot leak in, assembles with `nasm --reproducible`, and
pins `SOURCE_DATE_EPOCH`. The embedded configure strings confirm all of it
reached the compiler.

The output still differs between machines. That rules out the usual causes —
paths, timestamps, assembler nondeterminism — and leaves two candidates, both
outside what the lock can currently pin:

1. **The Windows SDK servicing level**, which nothing checks (above).
2. **`/experimental:deterministic` not being complete.** It is experimental by
   name; MSVC does not promise bit-identical output across toolset and SDK
   internals.

Either way, the remedy is not more flags. It is to pin the SDK servicing build
alongside the compiler, or to stop asserting a byte-exact hash for an artifact
this toolchain cannot guarantee byte-exactly and verify it some other way —
for example by checking the FFmpeg configure string, exported symbols and
licence posture, which is what the wheel pin is really trying to protect.

### What this means

The reviewed wheel hash **is not reproducible from the reviewed toolchain
specification**. Every input that specification names was verified identical
here, and the output still differs.

Corroboration that this machine is not simply unstable: an earlier, unrelated
build on this same workstation
(`civiccast-tester-dispatch/tester-handoff/native-caption-r7/controller/artifacts/`)
produced a wheel **byte-identical** to this one — 4,346,817 bytes, SHA-256
`2eb68720311d463c…`. Two independent builds here agree with each other and
disagree with the pin, so the divergence is between machines, not between runs.

At the time of this earlier diagnosis, the native candidate workflow had not
completed a run and the missing input remained unresolved. That status is
superseded by the successful Beta 12 candidate-assets run recorded above.
This historical run does not establish clean-machine reproducibility of every
step in this guide; use the exact candidate-bound evidence for claims about a
particular build.

## Signing

`build_native_installer.py` produces an **unsigned** `.exe`. The candidate
workflow signs through Azure Artifact Signing when its repository credentials
are configured. Beta 12's signed artifact is recorded in its verification
record. The required configuration names are:

| Kind | Name |
| --- | --- |
| Variable | `CIVICCAST_PACK_PUBLIC_KEY_BASE64` |
| Variable | `CIVICCAST_PACK_SIGNING_KEY_ID` |
| Secret | `CIVICCAST_PACK_SIGNING_PRIVATE_KEY` |
| Secret | `AZURE_CLIENT_ID` |
| Secret | `AZURE_TENANT_ID` |
| Secret | `AZURE_CLIENT_SECRET` |

The pack build fails closed without them:
`throw "Pack signing private-key secret is missing."`

## Cleaning up

```powershell
Remove-Item -Recurse -Force C:\ccmsvc, C:\ccbuildvenv, build\wp1-native-toolchain,
  build\native-runtime-tree, build\native-app-tree, build\native-pyav-cache,
  build\native-app-cache, "$env:TEMP\civiccast-toolchain-cache"
```

Nothing above is placed on the system PATH or registered with Windows.
