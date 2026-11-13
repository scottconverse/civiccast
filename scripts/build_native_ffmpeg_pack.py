#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
r"""Build the signed native ``native-ffmpeg-runtime`` component pack: the
FFmpeg COMMAND-LINE tools (``ffmpeg.exe`` + ``ffprobe.exe``) and the minimal
set of FFmpeg shared libraries they actually import.

The gap this closes: the native bootstrap install ships NO ``ffmpeg.exe`` or
``ffprobe.exe`` at all. ``scripts/verify_native_runtime_closure.py`` says so
in its own words ("the packaged tree ships no `ffmpeg.exe`, so a decode check
built around shelling out to one can never pass"), yet the five-pack
activation path REQUIRES ``dependencies/ffmpeg/bin/ffmpeg.exe``
(``native_activation.rs``'s ``validate_staged_runtime_layout``, and
``main.rs``'s staged-runtime self-test, which runs that exact path with
``-version``). Nothing built the artifact that satisfies those pins. This
script is that builder.

Mirrors ``scripts/build_native_server_pack.py``'s conventions exactly --
pinned-input validation before packing, ``--acquire`` reusing the ALREADY
REVIEWED ``native-windows-runtime-dependencies.lock.json`` and its
``fetch_locked_artifact``/``safe_extract_zip`` primitives verbatim, a signed
ZIP64 pack via ``build_native_pack``, a development-signing-key guard, a
``--report`` JSON, and a LIVE build-time proof that the exact packed
selection works before any pack is written.

## Payload layout, and why it is ``bin/``-rooted

``native_pack_staging::pack_extraction_destination`` maps this component to
``<INSTDIR>\dependencies\ffmpeg`` (the same per-component bridge
``native-app-payload`` already uses to reach ``<INSTDIR>\runtime``). Rooting
the payload at ``bin/`` therefore lands ``ffmpeg.exe`` at exactly
``<INSTDIR>\dependencies\ffmpeg\bin\ffmpeg.exe`` -- the convention
``native_activation.rs`` already pins, reached without inventing a second
path anywhere.

## Minimization

BtbN's ``win64-lgpl-shared`` archive is a general-purpose SDK distribution:
223 files / ~175.9 MB extracted, of which ~32.5 MB is developer material this
product can never execute (C headers under ``include/``, import libraries and
``.def``/``.pc`` files under ``lib/``, the full HTML manual under ``doc/``,
libvpx encoder presets) plus ``ffplay.exe`` (~17.9 MB), an interactive SDL
media player nothing in this repository invokes. This builder ships only the
PE closure of the two executables the product actually shells out to,
determined by a REAL recursive ``pefile`` import-table walk (ordinary AND
delay-load directories) -- the same method
``scripts/build_native_runtime_closure.py`` uses for the media closure, and
the same method ``build_native_server_pack.py``'s DLL pins were derived by.
See ``FFMPEG_BIN_PINS`` for the exact resulting file set.

## LGPL obligations

Follows ``build_native_server_pack.py``'s pattern exactly: the verbatim
upstream license text is packed at ``licenses/ffmpeg/LICENSE.txt``, a
generated ``notices/ffmpeg-runtime.txt`` records the exact upstream build
identity plus the corresponding-source offer, and
``_require_zero_gpl_and_full_license_provenance`` refuses to build if ANY
packed path lacks confirmed provenance in
``civiccast.native.runtime_licenses`` or resolves to a GPL-family license.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Final

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from civiccast._native_version import __version__  # noqa: E402
from civiccast.installer.native_packs import build_native_pack  # noqa: E402
from civiccast.native.runtime_licenses import (  # noqa: E402
    classify_ffmpeg_pack_file,
    is_gpl_license,
)
from scripts.provision_native_runtime_dependencies import (  # noqa: E402
    LOCK_PATH,
    fetch_locked_artifact,
    load_lock,
    safe_extract_zip,
)

_REPARSE_POINT: Final[int] = 0x400

#: The pack "component" identity. Mirrored on the Rust side by
#: ``native_pack_staging::FFMPEG_RUNTIME_COMPONENT`` (a drift-guard test there
#: pins the two against each other, the same way ``APP_PAYLOAD_COMPONENT`` is
#: already pinned across the language boundary).
FFMPEG_RUNTIME_COMPONENT: Final[str] = "native-ffmpeg-runtime"

#: Upstream version this builder was reviewed against -- the SAME pin
#: ``native-windows-runtime-dependencies.lock.json``'s ``ffmpeg`` entry
#: carries. Not a second source of truth: ``acquire_ffmpeg_pack_sources``
#: asserts the loaded lock's ``version`` equals this before fetching, so a
#: lock edited out from under this builder fails loud instead of silently
#: shipping an unreviewed FFmpeg.
#:
#: RE-PINNED 2026-08-27: the previously reviewed autobuild
#: (``autobuild-2026-08-12-13-15``, ``n8.1.2-34-g9b6c8969e0``) was pruned
#: from BtbN/FFmpeg-Builds' GitHub releases -- BtbN retains only recent daily
#: autobuilds plus one per calendar month, and this one was neither (candidate
#: run 33094460301 hit a 404 on the exact pinned URL). Confirmed unrecoverable
#: before repinning: the exact bytes are gone from GitHub (404 on the asset
#: URL and via ``objects.githubusercontent.com``), from the Wayback Machine
#: (empty CDX index for the URL), and from every web/code search checked; the
#: locally cached ``build/native-pyav-cache/ffmpeg-8c95...``.tar.gz is a
#: DIFFERENT artifact (FFmpeg SOURCE at commit ``8c9502e9b0...`` for the PyAV
#: build lane, not this lane's BtbN win64-lgpl-shared BINARY archive -- wrong
#: commit, wrong size, wrong type). Re-pinned to the next same-branch BtbN
#: autobuild still live at review time
#: (``autobuild-2026-08-26-13-06``, ``n8.1.2-46-g139afe709a`` -- 12 commits
#: ahead on the same ``n8.1.2`` release line, same ``win64-lgpl-shared-8.1``
#: build variant). Re-reviewed before repinning: a real ``pefile`` PE-import
#: closure walk against the new archive reaches the exact same nine
#: ``bin/`` files as the previous pin (no new or dropped imports), and a live
#: ``-version``/``-L``/testsrc-encode/ffprobe proof (the same shape
#: ``prove_ffmpeg_runtime`` runs) passed locally against the new archive:
#: LGPLv3 self-report, ``--disable-libx264``/``--disable-libx265``, and a real
#: h264/aac mp4 produced and probed successfully.
#: RC1 uses the reviewed upstream snapshot with RTSP redirect, SSH host-key,
#: HDR serialization and PSSH fixes absent from the current release branches.
#: Historical repin notes above do not describe this security revision.
#: RE-PINNED 2026-11-12: the daily build ``autobuild-2026-10-28-19-47``
#: (``N-128142-g28fda48e0b``) was pruned upstream (candidate run 43297934007
#: hit HTTP 404, and no mirror held the bytes). Re-pinned to the 2026-10-31
#: MONTHLY build (``autobuild-2026-10-31-12-59``), which upstream keeps. It is
#: the same master line, 85 commits ahead and 0 behind, so every security fix
#: named above is still present. Always pin a monthly build, never a daily.
FFMPEG_VERSION: Final[str] = "N-128227-g4811625b5f"

#: The reviewed SPDX identifier for this artifact, cross-checked three ways
#: (see ``civiccast.native.runtime_licenses``'s Category 7 header): the
#: binaries' own ``-L`` self-report, their ``-version`` configuration string
#: (``--enable-version3``, no ``--enable-gpl``, no ``--enable-nonfree``), and
#: the reviewed lock's own ``spdx_license`` field. Asserted against the lock
#: at acquire time by ``acquire_ffmpeg_pack_sources``.
FFMPEG_SPDX_LICENSE: Final[str] = "LGPL-3.0-or-later"

#: The upstream build recipe, published by the same project that publishes the
#: binary archive. Named in the generated NOTICE as the corresponding-source
#: pointer alongside FFmpeg's own source tree -- see ``_render_notice``.
FFMPEG_BUILD_RECIPE_URL: Final[str] = "https://github.com/BtbN/FFmpeg-Builds"
FFMPEG_SOURCE_URL: Final[str] = "https://git.ffmpeg.org/ffmpeg.git"


class FfmpegPackBuildError(RuntimeError):
    """The native-ffmpeg-runtime pack could not be built."""


# ---------------------------------------------------------------------------
# Pinned minimal-closure inventory.
#
# ``filename under bin/`` -> (expected_bytes, expected_sha256), computed
# directly from the reviewed, hash-pinned upstream archive. Same per-file pin
# style as ``build_native_server_pack.POSTGRES_BIN_PINS``, and covered
# transitively by ``fetch_locked_artifact``'s whole-archive SHA-256 check
# before any of these bytes are ever extracted.
#
# DERIVATION (not assumed from the ``bin/`` listing): a recursive PE import
# walk over ordinary AND delay-load import directories, seeded at
# ``bin/ffmpeg.exe`` + ``bin/ffprobe.exe``, using
# ``civiccast.native.runtime_closure.resolve_pe_closure`` driven by
# ``scripts.build_native_runtime_closure._pe_imports``. It reaches exactly the
# nine files below and nothing else. The ONLY ``bin/`` file it does not reach
# is ``ffplay.exe`` (17,902,592 bytes) -- an interactive SDL-based player no
# code in this repository invokes, and not a dependency of either tool.
#
# ``avdevice-62.dll`` IS in the closure: ``ffmpeg.exe`` imports it directly
# (it backs the ``dshow``/``gdigrab`` input devices), so it stays even though
# no current call site names those devices -- the walk decides membership, not
# a guess about which features get used.
#: Re-derived 2026-09-17 against the repinned archive (see ``FFMPEG_VERSION``)
#: -- same nine-file set, same DLL SONAME versions (ABI-compatible, 4 commits
#: forward on the same ``n8.1.2`` release branch from the previous pin), new
#: bytes/hashes. The previous pin (``autobuild-2026-08-26-13-06``,
#: ``n8.1.2-46-g139afe709a``) became a hard 404: BtbN prunes autobuild
#: releases, and that tag was neither among the retained recent autobuilds nor
#: a monthly survivor. The new pin deliberately uses the 2026-08-31 MONTHLY
#: release (``autobuild-2026-08-31-13-27``) so the exact bytes survive pruning.
#: Re-derived 2026-11-12 against the 2026-10-31 monthly archive: same nine-file
#: set and the same DLL names; new bytes and hashes.
FFMPEG_BIN_PINS: Final[dict[str, tuple[int, str]]] = {
    "avcodec-63.dll": (
        92660736,
        "4a3298d98ac0bdac118e6a35ab0b9cfbce444eb216d239158423ebbfd878fc99",
    ),
    "avdevice-63.dll": (
        5008384,
        "329571b290276b545de9de305be6888952d6c26de5f6bca839b1961b519f168a",
    ),
    "avfilter-12.dll": (
        32043520,
        "aaabf8f5a478fbbae46a77a3108f1822284e5dca0eaac421667ccc609d6d0652",
    ),
    "avformat-63.dll": (
        22864896,
        "86fef2b27f95ca82554b8b249bcc67c36c608b1b8219cbee0de57ec8c78ad67c",
    ),
    "avutil-61.dll": (
        3049984,
        "562f3d5dc56d973e7a82d64d34680a847a28e112f899c1afc513dc088027cbee",
    ),
    "ffmpeg.exe": (
        573952,
        "86b4c2a73482b84b63d1603668056c1ced6bb8031b49fe8b6fecf3d3f283acd8",
    ),
    "ffprobe.exe": (
        276480,
        "f25a19327fbc58e0645a5da466f9455395c101bd9c38c35ccd94cf0d6cdfc6dd",
    ),
    "swresample-7.dll": (
        751616,
        "6af4e626521e8c29f42a50f149d12158b383ea9c79ac91d6c7759889bb0a7391",
    ),
    "swscale-10.dll": (
        2417664,
        "9ae223e63ca0b8d5fa82248e25fccbdb7e03b02678f58c082d29864e94a7d4e9",
    ),
}

#: The two executables, kept as a named tuple rather than re-derived from the
#: pin table's ``.exe`` suffixes -- these are what the live proof runs and what
#: the lock's ``expected_executables`` field independently names.
FFMPEG_EXECUTABLES: Final[tuple[str, ...]] = ("ffmpeg.exe", "ffprobe.exe")

#: Required upstream license text, at its exact archive-relative path. Same
#: contract as ``build_native_server_pack``'s ``*_LICENSE_FILES``: presence is
#: a hard build requirement (a missing license file refuses the build the same
#: as a missing binary), and its content is checked through
#: ``classify_ffmpeg_pack_file``'s provenance gate rather than hash-pinned.
FFMPEG_LICENSE_FILES: Final[tuple[str, ...]] = ("LICENSE.txt",)

#: Windows-provided imports the closure walk legitimately leaves unresolved.
#: Deny-by-default: ``resolve_pe_closure`` raises ``UnknownProvenanceError``
#: naming every import that resolves neither in-tree nor here, so a future
#: FFmpeg build that picks up a NEW external dependency halts this builder
#: instead of silently shipping a tree that fails to load on an operator's
#: machine.
#:
#: Every entry confirmed individually, not assumed:
#:   * ``usp10.dll`` (Uniscribe), ``avicap32.dll`` (Video for Windows
#:     capture), ``avrt.dll`` (Multimedia Class Scheduler), ``ncrypt.dll``
#:     (CNG key storage) -- all four present in ``%SystemRoot%\System32`` on
#:     the supported Windows floor, none redistributable by a third party.
#:   * ``api-ms-win-crt-private-l1-1-0.dll`` -- a genuine OS API-set contract,
#:     confirmed against the anti-spoof check
#:     ``build_native_runtime_closure.api_set_resolves_as_a_real_contract``
#:     (which verifies the loader FORWARDS the name to a real host DLL rather
#:     than resolving it to a planted file of that name).
#:
#: Deliberately this builder's OWN inventory rather than an edit to
#: ``build_native_runtime_closure.SYSTEM_DLL_ALLOWLIST``: that allowlist is the
#: reviewed system-dependency story for the app-payload media closure, and
#: widening it for an unrelated artifact would quietly widen what THAT closure
#: is allowed to leave unresolved too.
FFMPEG_SYSTEM_DLL_ALLOWLIST: Final[tuple[str, ...]] = (
    "usp10.dll",
    "avicap32.dll",
    "avrt.dll",
    "ncrypt.dll",
    "api-ms-win-crt-private-l1-1-0.dll",
)

#: The H.264 encoder the live proof uses. NOT ``libx264``: this artifact is
#: the LGPL build, and libx264 is GPL, so ``--disable-libx264`` is inherent to
#: the owner-settled no-GPL constraint rather than an upstream accident.
#: ``libopenh264`` (Cisco's BSD-2-Clause encoder) is the software H.264
#: encoder this build actually registers -- confirmed against the packed
#: binary's own ``-encoders`` output -- and is already a name
#: ``civiccast/egress/gst/bridge.py``'s encoder map understands.
PROOF_VIDEO_ENCODER: Final[str] = "libopenh264"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _require_regular_file(path: Path, *, label: str) -> Path:
    try:
        details = path.lstat()
    except OSError as exc:
        raise FfmpegPackBuildError(f"{label} is missing: {path}") from exc
    attributes = int(getattr(details, "st_file_attributes", 0))
    if not stat.S_ISREG(details.st_mode) or path.is_symlink() or attributes & _REPARSE_POINT:
        raise FfmpegPackBuildError(f"{label} must be a regular non-reparse file: {path}")
    return path


def _require_real_directory(path: Path, *, label: str) -> Path:
    path = path.expanduser().resolve()
    try:
        details = path.lstat()
    except OSError as exc:
        raise FfmpegPackBuildError(f"{label} is missing: {path}") from exc
    attributes = int(getattr(details, "st_file_attributes", 0))
    if not stat.S_ISDIR(details.st_mode) or path.is_symlink() or attributes & _REPARSE_POINT:
        raise FfmpegPackBuildError(
            f"{label} must be a real directory, not a link or reparse point: {path}"
        )
    return path


def _validate_pinned_file(
    path: Path, *, expected_bytes: int, expected_sha256: str, label: str
) -> None:
    path = _require_regular_file(path, label=label)
    data = path.read_bytes()
    if len(data) != expected_bytes:
        raise FfmpegPackBuildError(
            f"{label} byte length mismatch: expected {expected_bytes}, observed {len(data)}"
        )
    observed = _sha256_bytes(data)
    if observed != expected_sha256:
        raise FfmpegPackBuildError(
            f"{label} SHA-256 mismatch: expected {expected_sha256}, observed {observed}"
        )


def require_allowed_signing_key(key_id: str, *, allow_development_key: bool) -> None:
    """Keep development trust roots out of an accidental release build (same
    contract as ``build_native_server_pack``'s guard)."""

    if key_id.startswith("development-") and not allow_development_key:
        raise FfmpegPackBuildError(
            "development pack signing keys require --allow-development-key; "
            "release packaging must use Scott-approved production key custody"
        )


def load_ed25519_private_key(path: Path) -> Ed25519PrivateKey:
    if not path.is_file():
        raise FfmpegPackBuildError(f"pack signing private key is missing: {path}")
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise FfmpegPackBuildError("pack signing private key must be Ed25519")
    return key


# ---------------------------------------------------------------------------
# Acquisition (--acquire): reuse the reviewed lock + primitives verbatim.
# ---------------------------------------------------------------------------


def _extracted_ffmpeg_is_complete(destination: Path) -> bool:
    """Re-verify a pre-existing extraction against the same pinned bin/
    license file set ``build_ffmpeg_pack()`` itself requires, rather than
    trusting bare directory existence.

    A self-hosted runner's ``--cache`` persists across runs (a hosted
    runner is always fresh); a previous run's extraction into
    ``cache/extracted/ffmpeg`` interrupted partway through (a crash, a
    cancelled run, a disk hiccup) leaves a directory that EXISTS but is
    missing files -- candidate run 32858543561 failed with "FFmpeg closure
    seed bin/ffmpeg.exe is missing" from exactly this path, the same
    idempotent-scratch bug class fixed for civiccast-server-pack-cache
    (#41) applied here to a different cache."""
    try:
        _ffmpeg_sources(destination)
    except FfmpegPackBuildError:
        return False
    return True


def acquire_ffmpeg_pack_sources(cache: Path, *, lock_path: Path = LOCK_PATH) -> Path:
    """Download + verify + extract ONLY the ``ffmpeg`` artifact from the
    reviewed runtime-dependency lock (never the postgres/tsduck/node/
    ollama artifacts the same lock also pins for unrelated packs).

    ``cache`` is caller-controlled and MUST live outside the repository --
    callers pass a scratch/temp directory, never a path under the checked-out
    tree.

    A pre-existing ``cache/extracted/ffmpeg`` is trusted only if it is
    actually COMPLETE (see ``_extracted_ffmpeg_is_complete``), not merely
    present -- see that function's docstring for why.
    """

    lock = load_lock(lock_path)
    artifact = lock["artifacts"]["ffmpeg"]
    if str(artifact["version"]) != FFMPEG_VERSION:
        raise FfmpegPackBuildError(
            "ffmpeg artifact version drifted from this builder's reviewed pin: "
            f"lock has {artifact['version']!r}, builder expects {FFMPEG_VERSION!r} "
            "-- re-review before rebuilding"
        )
    if str(artifact["spdx_license"]) != FFMPEG_SPDX_LICENSE:
        raise FfmpegPackBuildError(
            "ffmpeg artifact license drifted from this builder's reviewed pin: "
            f"lock has {artifact['spdx_license']!r}, builder expects "
            f"{FFMPEG_SPDX_LICENSE!r} -- the owner-settled constraint is the LGPL "
            "FFmpeg build; re-review before rebuilding"
        )
    expected_executables = {str(name) for name in artifact["expected_executables"]}
    declared = {f"bin/{name}" for name in FFMPEG_EXECUTABLES}
    if expected_executables != declared:
        raise FfmpegPackBuildError(
            "ffmpeg artifact expected_executables drifted from this builder's "
            f"reviewed pin: lock has {sorted(expected_executables)}, builder "
            f"expects {sorted(declared)}"
        )

    archive = fetch_locked_artifact("ffmpeg", artifact, cache / "archives", offline=False)
    destination = cache / "extracted" / "ffmpeg"
    if destination.exists() and not _extracted_ffmpeg_is_complete(destination):
        shutil.rmtree(destination, ignore_errors=True)
    if not destination.exists():
        safe_extract_zip(
            archive,
            destination,
            strip_prefix=str(artifact["strip_prefix"]),
            include=artifact.get("include"),
        )
    return destination


# ---------------------------------------------------------------------------
# Closure re-derivation (the pins above are DERIVED, so they are re-checkable)
# ---------------------------------------------------------------------------


def resolve_ffmpeg_pe_closure(ffmpeg_root: Path) -> tuple[str, ...]:
    """Re-derive the minimal PE closure of the two executables against
    ``ffmpeg_root``, returning root-relative POSIX paths.

    This is the SAME walk that produced ``FFMPEG_BIN_PINS``, kept executable
    rather than reduced to a comment so ``--verify-closure`` can prove the
    checked-in pin table still matches what a real import walk says about the
    real archive. A comment claiming "these are the imports" is unverified;
    this function is the claim's test.

    ``pefile`` is imported lazily (inside the function) so the rest of this
    module -- including its CLI's argument parsing and key handling -- stays
    importable on a host without it.
    """

    from civiccast.native.runtime_closure import resolve_pe_closure
    from scripts.build_native_runtime_closure import _pe_imports, is_system_dll

    ffmpeg_root = _require_real_directory(ffmpeg_root, label="FFmpeg source root")
    index: dict[str, Path] = {}
    for candidate in sorted(ffmpeg_root.rglob("*")):
        if candidate.is_file() and candidate.suffix.lower() in (".dll", ".exe"):
            index.setdefault(candidate.name.lower(), candidate)

    def imports_of(relative: str) -> list[str]:
        # `is_system_dll` decides the OS-provided names the media closure
        # already reviewed; this builder's own allowlist covers the additional
        # OS DLLs only FFmpeg reaches (see FFMPEG_SYSTEM_DLL_ALLOWLIST). Both
        # are deny-by-default -- anything in neither reaches
        # `resolve_pe_closure`, which fails loud on it.
        return [name for name in _pe_imports(ffmpeg_root / relative) if not is_system_dll(name)]

    def resolve(name: str) -> str | None:
        found = index.get(name.lower())
        return None if found is None else found.relative_to(ffmpeg_root).as_posix()

    seeds = [f"bin/{name}" for name in FFMPEG_EXECUTABLES]
    for seed in seeds:
        _require_regular_file(ffmpeg_root / seed, label=f"FFmpeg closure seed {seed}")
    return tuple(
        sorted(
            resolve_pe_closure(
                seeds,
                imports_of=imports_of,
                resolve=resolve,
                system_allowlist=FFMPEG_SYSTEM_DLL_ALLOWLIST,
            )
        )
    )


def verify_pinned_closure_matches_a_real_import_walk(ffmpeg_root: Path) -> tuple[str, ...]:
    """Fail loud if ``FFMPEG_BIN_PINS`` and a real import walk disagree.

    Returns the walked closure on success. An upstream rebuild that adds or
    drops a dependency changes the walk's answer, and this turns that into a
    BUILD failure naming the difference -- never a pack that is missing a DLL
    the operator's machine will only discover at first encode.
    """

    walked = resolve_ffmpeg_pe_closure(ffmpeg_root)
    pinned = tuple(sorted(f"bin/{name}" for name in FFMPEG_BIN_PINS))
    if walked != pinned:
        missing = sorted(set(walked) - set(pinned))
        extra = sorted(set(pinned) - set(walked))
        raise FfmpegPackBuildError(
            "the pinned FFmpeg closure no longer matches a real PE import walk of "
            f"this archive. Reached by the walk but not pinned: {missing or 'none'}; "
            f"pinned but not reached: {extra or 'none'}. Re-derive FFMPEG_BIN_PINS "
            "before shipping this pack."
        )
    return walked


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def _ffmpeg_sources(ffmpeg_root: Path) -> dict[str, Path]:
    ffmpeg_root = _require_real_directory(ffmpeg_root, label="FFmpeg source root")
    sources: dict[str, Path] = {}
    for filename, (expected_bytes, expected_sha256) in sorted(FFMPEG_BIN_PINS.items()):
        path = ffmpeg_root / "bin" / filename
        _validate_pinned_file(
            path,
            expected_bytes=expected_bytes,
            expected_sha256=expected_sha256,
            label=f"pinned FFmpeg {filename}",
        )
        sources[f"bin/{filename}"] = path
    for filename in FFMPEG_LICENSE_FILES:
        path = _require_regular_file(
            ffmpeg_root / filename, label=f"FFmpeg license file {filename}"
        )
        sources[f"licenses/ffmpeg/{filename}"] = path
    return sources


def _require_zero_gpl_and_full_license_provenance(sources: dict[str, Path]) -> None:
    """Refuse the build if any packed path has no confirmed license, or a
    confirmed license that is GPL-family (the owner-settled no-GPL constraint
    for this artifact). Runs on every path this build is ABOUT to pack, so a
    future addition to ``_ffmpeg_sources`` that forgets to update
    ``civiccast.native.runtime_licenses`` fails the build loud instead of
    shipping an unreviewed file silently."""

    unresolved: list[str] = []
    gpl_flagged: list[tuple[str, str]] = []
    for relative_path in sorted(sources):
        if relative_path.startswith("notices/"):
            continue  # this builder's own generated NOTICE, not upstream bytes
        license_id = classify_ffmpeg_pack_file(relative_path)
        if license_id is None:
            unresolved.append(relative_path)
        elif is_gpl_license(license_id):
            gpl_flagged.append((relative_path, license_id))
    if gpl_flagged:
        raise FfmpegPackBuildError(
            "native-ffmpeg-runtime pack refuses GPL/AGPL-family entries (the "
            "owner-settled constraint is the LGPL FFmpeg build): "
            + ", ".join(f"{path} ({license_id})" for path, license_id in gpl_flagged)
        )
    if unresolved:
        raise FfmpegPackBuildError(
            "native-ffmpeg-runtime pack has unconfirmed license provenance for: "
            + ", ".join(unresolved[:10])
            + (f" (+{len(unresolved) - 10} more)" if len(unresolved) > 10 else "")
        )


def _run_capturing(
    argv: list[str], *, run: Callable[..., subprocess.CompletedProcess[str]]
) -> subprocess.CompletedProcess[str]:
    return run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")


def prove_ffmpeg_runtime(
    ffmpeg_root: Path,
    *,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, object]:
    """Live build-time proof that the EXACT nine-file selection this builder
    packs can run, encode, and probe -- from a tree containing ONLY those nine
    files, laid out the way the installer will lay them out.

    This is the FFmpeg analogue of ``build_native_server_pack.
    prove_postgres_bootstrap``, and it exists for the same reason: no
    hash/path-set validation can catch a minimization that dropped a DLL the
    binary only needs at run time. The only authority on completeness is the
    binary itself. Every real pack build therefore materializes the selected
    files into a scratch ``dependencies/ffmpeg/bin`` tree and runs, in order:

      1. ``ffmpeg.exe -version`` and ``ffprobe.exe -version`` -- the SAME
         probe ``main.rs``'s staged-runtime self-test runs against
         ``dependencies/ffmpeg/bin/ffmpeg.exe``.
      2. ``ffmpeg.exe -L``, asserting the binary's OWN license self-report
         names the LESSER GPL and not the plain GNU General Public License.
         The owner-settled no-GPL constraint is thereby checked against the
         artifact, not merely against the archive's filename.
      3. A real 2-second ``testsrc``+``sine`` H.264/AAC encode -- the same
         shape ``civiccast.installer.service._write_sample_video`` produces
         for the rehearsal path -- followed by an ``ffprobe`` of the result,
         asserting a real ``h264`` video stream and a real ``aac`` audio
         stream came back out.

    Any failing step fails the BUILD with that step's full output -- never a
    station install. ``run`` is injectable for the unit suite (no real FFmpeg
    execution in unit tests; the live execution happens on every real CLI pack
    build).

    Returns the collected evidence (versions, the license line, the encoded
    file's size, and the probe's stream summary) so ``--report`` can carry it.

    ENCODER NOTE, deliberately not papered over: this build is configured
    ``--disable-libx264`` (libx264 is GPL, so it cannot appear in an LGPL
    FFmpeg), and the proof therefore encodes with ``libopenh264`` -- the
    BSD-2-Clause H.264 encoder this build DOES carry. Product call sites use a
    declarative H.264 request; ``civiccast.stream._ffmpeg`` probes the exact
    binary and resolves NVENC -> Media Foundation -> OpenH264 (-> libx264
    strictly last, never present in this LGPL pack) immediately
    before execution. The pack proof pins its software encoder explicitly so
    it remains a hardware-independent closure check.
    """

    sources = _ffmpeg_sources(ffmpeg_root)
    evidence: dict[str, object] = {}
    with tempfile.TemporaryDirectory(
        prefix="civiccast-ffmpeg-pack-proof-", ignore_cleanup_errors=True
    ) as temporary:
        root = Path(temporary)
        # The EXACT layout the installer produces: <root>\dependencies\ffmpeg\bin.
        bin_dir = root / "dependencies" / "ffmpeg" / "bin"
        bin_dir.mkdir(parents=True)
        for key, source in sources.items():
            if not key.startswith("bin/"):
                continue
            shutil.copy2(source, bin_dir / PurePosixPath(key).name)

        ffmpeg_exe = str(bin_dir / "ffmpeg.exe")
        ffprobe_exe = str(bin_dir / "ffprobe.exe")

        def check(step: str, argv: list[str]) -> str:
            result = _run_capturing(argv, run=run)
            combined = (result.stdout or "") + (result.stderr or "")
            if result.returncode != 0:
                raise FfmpegPackBuildError(
                    f"FFmpeg pack proof failed at {step} (exit {result.returncode}). "
                    "The packed FFmpeg selection cannot run -- most likely a DLL the "
                    "pins do not carry. Fix the selection (re-derive FFMPEG_BIN_PINS); "
                    f"do NOT ship this pack.\n--- output ---\n{combined}"
                )
            return combined

        version_output = check("ffmpeg -version", [ffmpeg_exe, "-hide_banner", "-version"])
        first_line = version_output.strip().splitlines()[0] if version_output.strip() else ""
        if FFMPEG_VERSION not in first_line:
            raise FfmpegPackBuildError(
                f"FFmpeg pack proof: ffmpeg -version does not report the pinned build "
                f"{FFMPEG_VERSION!r}: {first_line!r}"
            )
        evidence["ffmpeg_version_line"] = first_line

        probe_version_output = check("ffprobe -version", [ffprobe_exe, "-hide_banner", "-version"])
        probe_first_line = (
            probe_version_output.strip().splitlines()[0] if probe_version_output.strip() else ""
        )
        if FFMPEG_VERSION not in probe_first_line:
            raise FfmpegPackBuildError(
                f"FFmpeg pack proof: ffprobe -version does not report the pinned build "
                f"{FFMPEG_VERSION!r}: {probe_first_line!r}"
            )
        evidence["ffprobe_version_line"] = probe_first_line

        license_output = check("ffmpeg -L", [ffmpeg_exe, "-hide_banner", "-L"])
        normalized_license = " ".join(license_output.split()).upper()
        if "GNU LESSER GENERAL PUBLIC LICENSE" not in normalized_license:
            raise FfmpegPackBuildError(
                "FFmpeg pack proof: the packed binary does not self-report the GNU "
                f"Lesser General Public License:\n{license_output}"
            )
        if is_gpl_license(license_output):
            raise FfmpegPackBuildError(
                "FFmpeg pack proof: the packed binary self-reports a GPL-family "
                "license -- this pack must carry the LGPL FFmpeg build "
                f"(owner-settled: no GPL):\n{license_output}"
            )
        evidence["ffmpeg_license_self_report"] = " ".join(
            license_output.strip().splitlines()[:3]
        ).strip()

        sample = root / "ffmpeg-pack-proof.mp4"
        check(
            "encode (testsrc -> h264/aac mp4)",
            [
                ffmpeg_exe,
                "-hide_banner",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=640x360:rate=15",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=880:sample_rate=48000",
                "-t",
                "2",
                "-shortest",
                "-c:v",
                PROOF_VIDEO_ENCODER,
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(sample),
            ],
        )
        if not sample.is_file() or sample.stat().st_size == 0:
            raise FfmpegPackBuildError(
                "FFmpeg pack proof: the encode step reported success but produced no "
                f"output file at {sample}"
            )
        evidence["encoder_used"] = PROOF_VIDEO_ENCODER
        evidence["encoded_sample_bytes"] = sample.stat().st_size

        probe_output = check(
            "ffprobe (verify the encoded mp4)",
            [
                ffprobe_exe,
                "-hide_banner",
                "-v",
                "error",
                "-show_entries",
                "format=format_name,duration:stream=codec_name,codec_type",
                "-of",
                "json",
                str(sample),
            ],
        )
        try:
            probed = json.loads(probe_output)
        except json.JSONDecodeError as exc:
            raise FfmpegPackBuildError(
                f"FFmpeg pack proof: ffprobe did not return JSON:\n{probe_output}"
            ) from exc
        codecs = {
            str(stream.get("codec_type")): str(stream.get("codec_name"))
            for stream in probed.get("streams", [])
        }
        if codecs.get("video") != "h264" or codecs.get("audio") != "aac":
            raise FfmpegPackBuildError(
                "FFmpeg pack proof: the encoded sample is not a real h264/aac file "
                f"(ffprobe reported {codecs!r})"
            )
        evidence["probed_streams"] = codecs
        evidence["probed_format"] = probed.get("format", {})

    return evidence


def _render_notice(closure_paths: tuple[str, ...]) -> str:
    """The pack's generated NOTICE: upstream identity, license, and the
    corresponding-source offer LGPL-3.0 section 4 expects for a distributed
    Combined Work.

    Deliberately states what this notice does NOT establish (the per-library
    provenance of the third-party code statically linked into these
    binaries), rather than implying a completeness the archive's single
    LICENSE.txt does not support -- the same honesty posture
    ``build_native_runtime_closure.render_license_notices_readme`` takes for
    the media closure's notices.
    """

    packed = "\n".join(f"  {path}" for path in closure_paths)
    return (
        "CivicCast native FFmpeg-runtime pack\n"
        "\n"
        f"FFmpeg {FFMPEG_VERSION}, win64 LGPL shared build.\n"
        f"License: {FFMPEG_SPDX_LICENSE}.\n"
        "\n"
        "The full license text is packed in this component at\n"
        "  licenses/ffmpeg/LICENSE.txt\n"
        "copied verbatim from the upstream archive.\n"
        "\n"
        "Files in this component:\n"
        f"{packed}\n"
        "\n"
        "WRITTEN OFFER OF CORRESPONDING SOURCE\n"
        "-------------------------------------\n"
        "These FFmpeg libraries are conveyed in object-code form under the GNU\n"
        "Lesser General Public License, version 3 or later. The complete\n"
        "corresponding source code for this exact build is publicly available:\n"
        "\n"
        f"  FFmpeg source, at the tagged revision this build was made from ({FFMPEG_VERSION}):\n"
        f"    {FFMPEG_SOURCE_URL}\n"
        f"  The complete build recipe, toolchain, and configuration used to produce it:\n"
        f"    {FFMPEG_BUILD_RECIPE_URL}\n"
        "\n"
        "The exact configure options this build was produced with are printed by\n"
        "the shipped binaries themselves; run:\n"
        "  ffmpeg.exe -version\n"
        "\n"
        "CivicCast links these libraries dynamically and does not modify them.\n"
        "The shipped .dll files are the unmodified upstream binaries, so a\n"
        "recipient may replace them with their own build of the same libraries\n"
        "(LGPL-3.0 section 4(d)(1)) simply by substituting the files in this\n"
        "component's bin directory.\n"
        "\n"
        "SCOPE OF THIS NOTICE\n"
        "--------------------\n"
        "The identifier above governs the FFmpeg code in these files. This build\n"
        "also statically links external libraries under their own separate\n"
        "licenses; the complete enabled set is enumerated verbatim in the\n"
        "configuration string printed by 'ffmpeg.exe -version'. The upstream\n"
        "archive ships no per-dependency license texts, so this component does\n"
        "not claim per-dependency provenance it does not have. See\n"
        "civiccast.native.runtime_licenses (Category 7) for the recorded\n"
        "evidence and the scope of what was and was not confirmed.\n"
    )


def build_ffmpeg_pack(
    *,
    output: Path,
    ffmpeg_root: Path,
    signing_private_key: Ed25519PrivateKey,
    signing_key_id: str,
    product_version: str,
    compatible_core: str | None = None,
) -> dict[str, object]:
    """Validate the pinned FFmpeg inputs and build the signed
    ``native-ffmpeg-runtime`` pack.

    ``payload_tree_sha256`` in the returned report carries the same
    cross-machine reproducibility meaning ``build_native_server_pack.
    build_server_pack``'s docstring describes: two machines building this pack
    from the same commit necessarily differ in ``pack_sha256`` and
    ``signing_key_id`` (each signs with its own local development key), but
    must agree byte-for-byte on ``payload_tree_sha256``.
    """

    sources = _ffmpeg_sources(ffmpeg_root)
    closure_paths = tuple(sorted(key for key in sources if key.startswith("bin/")))

    with tempfile.TemporaryDirectory(prefix="civiccast-ffmpeg-pack-") as temporary:
        notice_path = Path(temporary) / "NOTICE.txt"
        notice_path.write_text(_render_notice(closure_paths), encoding="utf-8", newline="\n")
        sources["notices/ffmpeg-runtime.txt"] = notice_path

        _require_zero_gpl_and_full_license_provenance(sources)

        result = build_native_pack(
            output=output,
            component=FFMPEG_RUNTIME_COMPONENT,
            product_version=product_version,
            compatible_core=compatible_core or product_version,
            sources=sources,
            signing_private_key=signing_private_key,
            signing_key_id=signing_key_id,
            metadata={
                "ffmpeg_version": FFMPEG_VERSION,
                "ffmpeg_spdx_license": FFMPEG_SPDX_LICENSE,
                "ffmpeg_executables": list(FFMPEG_EXECUTABLES),
            },
        )
    return {
        "component": result.component,
        "file_count": result.file_count,
        "output": str(result.path),
        "pack_bytes": result.path.stat().st_size,
        "pack_sha256": result.sha256,
        "payload_bytes": result.total_bytes,
        "payload_tree_sha256": result.payload_tree_sha256,
        "product_version": result.product_version,
        "signing_key_id": result.signing_key_id,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--acquire",
        action="store_true",
        help=(
            "download + verify + extract the ffmpeg artifact from the reviewed "
            "lock into --cache before building (mutually exclusive with --ffmpeg-root)"
        ),
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=Path(tempfile.gettempdir()) / "civiccast-native-ffmpeg-pack-cache",
        help="scratch directory OUTSIDE the repo for --acquire's downloads/extraction",
    )
    parser.add_argument("--lock", type=Path, default=LOCK_PATH)
    parser.add_argument("--ffmpeg-root", type=Path)
    parser.add_argument("--signing-private-key", required=True, type=Path)
    parser.add_argument("--signing-key-id", required=True)
    parser.add_argument("--product-version", default=__version__)
    parser.add_argument("--compatible-core", default=None)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--allow-development-key",
        action="store_true",
        help="explicitly allow a development-only trust root for non-release proof",
    )
    parser.add_argument(
        "--skip-runtime-proof",
        action="store_true",
        help=(
            "skip the live -version/-L/encode/probe proof of the packed FFmpeg "
            "selection (emergency/debug ONLY -- the proof is the only thing that "
            "can catch a minimization that dropped a run-time-only DLL)"
        ),
    )
    args = parser.parse_args()

    try:
        require_allowed_signing_key(
            args.signing_key_id, allow_development_key=args.allow_development_key
        )
        key = load_ed25519_private_key(args.signing_private_key)

        if args.acquire:
            if args.ffmpeg_root:
                raise FfmpegPackBuildError("--acquire is mutually exclusive with --ffmpeg-root")
            ffmpeg_root = acquire_ffmpeg_pack_sources(args.cache, lock_path=args.lock)
        elif args.ffmpeg_root is None:
            raise FfmpegPackBuildError("missing required flag (or pass --acquire): --ffmpeg-root")
        else:
            ffmpeg_root = args.ffmpeg_root

        print("build_native_ffmpeg_pack: re-deriving the PE import closure...")
        walked = verify_pinned_closure_matches_a_real_import_walk(ffmpeg_root)
        print(
            f"build_native_ffmpeg_pack: closure OK -- {len(walked)} files, "
            "matches the checked-in pins"
        )

        proof: dict[str, object] = {}
        if args.skip_runtime_proof:
            print(
                "build_native_ffmpeg_pack: WARNING -- runtime proof SKIPPED "
                "(--skip-runtime-proof); this pack's FFmpeg selection is unproven "
                "against a live encode",
                file=sys.stderr,
            )
        else:
            print(
                "build_native_ffmpeg_pack: running live runtime proof (-version, -L, encode, probe)..."
            )
            proof = prove_ffmpeg_runtime(ffmpeg_root)
            print("build_native_ffmpeg_pack: runtime proof PASSED")
            print(
                "build_native_ffmpeg_pack: NOTE -- this LGPL build carries no "
                f"libx264 (GPL). The proof encoded with {PROOF_VIDEO_ENCODER!r}. "
                "CivicCast resolves H.264 against the exact runtime binary at "
                "execution time.",
                file=sys.stderr,
            )

        report = build_ffmpeg_pack(
            output=args.output.resolve(),
            ffmpeg_root=ffmpeg_root,
            signing_private_key=key,
            signing_key_id=args.signing_key_id,
            product_version=args.product_version,
            compatible_core=args.compatible_core,
        )
        report["closure_files"] = list(walked)
        report["runtime_proof"] = proof
    except FfmpegPackBuildError as exc:
        print(f"build_native_ffmpeg_pack: {exc}", file=sys.stderr)
        return 1

    rendered = json.dumps(report, indent=2) + "\n"
    if args.report is not None:
        report_path = args.report.resolve()
        if report_path.exists():
            raise FileExistsError(f"ffmpeg pack report already exists: {report_path}")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered, encoding="utf-8", newline="\n")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
