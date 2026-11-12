# SPDX-License-Identifier: Apache-2.0
"""Verify and apply the reviewed, same-ABI FFmpeg subset used by gst-libav."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "native-windows-gstreamer-ffmpeg.lock.json"
ARCHIVE_NAME = "ffmpeg-7.1.5-security1-ac3-gstreamer-windows-x64.zip"
VERSION = "7.1.5-security1"
DLL_NAMES = frozenset(
    {
        "avcodec-61.dll",
        "avfilter-10.dll",
        "avformat-61.dll",
        "avutil-59.dll",
        "swresample-5.dll",
        "swscale-8.dll",
    }
)


def policy() -> dict[str, Any]:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def verify_runtime(root: Path) -> None:
    reviewed = policy()
    expected = DLL_NAMES | {"COPYING.LGPLv2.1", "provenance.json"}
    if set(reviewed["files"]) != expected:
        raise ValueError("unexpected reviewed GStreamer FFmpeg file set")
    for name, pin in reviewed["files"].items():
        path = root / name
        if (
            not path.is_file()
            or path.stat().st_size != pin["bytes"]
            or hashlib.sha256(path.read_bytes()).hexdigest() != pin["sha256"]
        ):
            raise ValueError(f"unverified GStreamer FFmpeg {VERSION} file: {path}")
    if json.loads((root / "provenance.json").read_text(encoding="utf-8")) != reviewed["provenance"]:
        raise ValueError("GStreamer FFmpeg source/security-patch provenance mismatch")


def acquire_runtime(cache: Path) -> Path:
    out = cache / "ffmpeg-7.1.5-security1-ac3-gstreamer-runtime"
    partial = cache / "ffmpeg-7.1.5-security1-ac3-gstreamer-runtime.partial"
    if out.is_dir():
        try:
            verify_runtime(out)
            if {p.name for p in out.iterdir()} == set(policy()["files"]):
                return out
        except (ValueError, OSError):
            pass
        # A cache that fails verification (the runner's temp cleanup can leave
        # it emptied, or it was modified) is never used: rebuild it below from
        # the reviewed archive, which is itself hash-checked first.
    archive = Path(
        os.environ.get(
            "CIVICCAST_GSTREAMER_FFMPEG_RUNTIME_ZIP",
            str(Path("C:/CivicCastTester/runtime-artifact-mirror") / ARCHIVE_NAME),
        )
    )
    reviewed = policy()
    pin = reviewed["archive"]
    if (
        not archive.is_file()
        or archive.stat().st_size != pin["bytes"]
        or hashlib.sha256(archive.read_bytes()).hexdigest() != pin["sha256"]
    ):
        raise ValueError(
            f"GStreamer FFmpeg runtime archive missing or unverified: {archive}; "
            "build scripts/build_native_gstreamer_ffmpeg.py and stage its reviewed archive"
        )
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)) or set(names) != set(reviewed["files"]):
            raise ValueError("unexpected GStreamer FFmpeg runtime archive members")
        # Extract beside the cache and swap in only a verified, complete tree,
        # so an interrupted run can never leave a half-made cache in place.
        if partial.exists():
            shutil.rmtree(partial)
        partial.mkdir(parents=True)
        bundle.extractall(partial)
    verify_runtime(partial)
    if out.exists():
        shutil.rmtree(out)
    partial.rename(out)
    return out


def assert_compatible_imports(consumers: list[Path], runtime: Path) -> None:
    """Resolve every imported name/ordinal against the six replacement DLLs."""
    import pefile

    exports = {}
    for name in DLL_NAMES:
        with pefile.PE(str(runtime / name)) as pe:
            exports[name] = {
                symbol.name if symbol.name else symbol.ordinal
                for symbol in pe.DIRECTORY_ENTRY_EXPORT.symbols
            }
    for consumer in consumers:
        with pefile.PE(str(consumer)) as pe:
            for imported in [
                *getattr(pe, "DIRECTORY_ENTRY_IMPORT", []),
                *getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", []),
            ]:
                name = imported.dll.decode("ascii").lower()
                if name not in exports:
                    continue
                missing = [
                    symbol.name if symbol.name else symbol.ordinal
                    for symbol in imported.imports
                    if (symbol.name if symbol.name else symbol.ordinal) not in exports[name]
                ]
                if missing:
                    raise ValueError(
                        f"GStreamer FFmpeg ABI mismatch in {consumer}: {name}: {missing}"
                    )


def overlay_directory(out: Path, cache: Path) -> dict[str, tuple[str, str, str]]:
    """Caller incorporates attribution and regenerates all closure trust files."""
    runtime = acquire_runtime(cache)
    consumers = [
        p
        for p in out.rglob("*")
        if p.suffix.lower() in {".dll", ".exe", ".pyd"} and p.name.lower() not in DLL_NAMES
    ]
    assert_compatible_imports([*consumers, *(runtime / name for name in DLL_NAMES)], runtime)
    attribution = {}
    for name in policy()["files"]:
        relative = f"bin/{name}" if name in DLL_NAMES else f"licenses/ffmpeg-{VERSION}/{name}"
        destination = out / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(runtime / name, destination)
        license_ = (
            "LGPL-2.1-or-later AND LicenseRef-Microsoft-VCRedist"
            if name in DLL_NAMES
            else "Apache-2.0"
            if name == "provenance.json"
            else "LGPL-2.1-or-later"
        )
        attribution[relative] = ("ffmpeg", VERSION, license_)
    return attribution


def verify_overlay(out: Path) -> None:
    """Reconcile packaged bytes with committed pins, without acquisition or writes."""
    reviewed = policy()
    for name, pin in reviewed["files"].items():
        relative = f"bin/{name}" if name in DLL_NAMES else f"licenses/ffmpeg-{VERSION}/{name}"
        path = out / relative
        if (
            not path.is_file()
            or path.stat().st_size != pin["bytes"]
            or hashlib.sha256(path.read_bytes()).hexdigest() != pin["sha256"]
        ):
            raise ValueError(f"unverified packaged GStreamer FFmpeg file: {path}")
    provenance_path = out / f"licenses/ffmpeg-{VERSION}/provenance.json"
    if json.loads(provenance_path.read_text(encoding="utf-8")) != reviewed["provenance"]:
        raise ValueError("packaged GStreamer FFmpeg source/security-patch provenance mismatch")
