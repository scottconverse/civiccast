# SPDX-License-Identifier: Apache-2.0
"""Build the same-ABI, minimal LGPL FFmpeg replacement for gst-libav.

Uses the reviewed PyAV source-build machinery and toolchain, with a separate
source/patch/profile identity. This does not rebuild any GStreamer component.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.build_native_pyav_wheel import (  # noqa: E402
    MSVC_COMPILER_VERSION,
    MSVC_LINKER_VERSION,
    build_minimal_ffmpeg,
)

COMMIT = "98581d546ff453ce1f29c4af8448996d6ac992f7"
VERSION = "7.1.5-security1"
SOURCE_BYTES = 15_948_907
SOURCE_SHA256 = "52d7ba800ee2adbbbb4c57130ac0cd0dfb18369a3d36a049648ec673af8df4c5"
# Stable 7.1 already contains the AAC TNS fix c2184b65 (upstream ed09aa28).
# Both libavutil defects still exist and both verified upstream patches apply.
# MOV/pssh demuxing is absent from this profile, so no new append API is needed.
SECURITY_PATCHES = (
    (
        "969c7eb091989a664cb7bf66a1f02051cc46cb52",
        1207,
        "594a0cbca9527bc1edab7f76679af5a73cee23aadc878e2cc6a9001e22742c45",
        ("libavutil/encryption_info.c",),
    ),
    (
        "2c2f6e96e31795ae95a8f8323a493ffbc493111f",
        2179,
        "fb9d5c7625495040858ef316396d10d8be62c514187d0e19cc455410dbce79a9",
        ("libavutil/hdr_dynamic_metadata.c",),
    ),
)
DLL_NAMES = (
    "avcodec-61.dll",
    "avfilter-10.dll",
    "avformat-61.dll",
    "avutil-59.dll",
    "swresample-5.dll",
    "swscale-8.dll",
)
ARCHIVE_NAME = "ffmpeg-7.1.5-security1-ac3-gstreamer-windows-x64.zip"
CONFIGURE_OPTIONS = (
    "--toolchain=msvc",
    "--arch=x86_64",
    "--target-os=win64",
    "--enable-shared",
    "--disable-static",
    "--disable-programs",
    "--disable-doc",
    "--disable-debug",
    "--disable-autodetect",
    "--disable-network",
    "--disable-everything",
    "--disable-avdevice",
    "--enable-avcodec",
    "--enable-avfilter",
    "--enable-avformat",
    "--enable-avutil",
    "--enable-swresample",
    "--enable-swscale",
    "--enable-decoder=h264,aac,ac3,eac3",
    "--enable-encoder=aac,ac3",
    "--enable-parser=h264,aac,aac_latm,ac3",
    # Retain the original filter set and gst-libav's deinterlacing support.
    "--enable-filter=null,psnr,scale,ssim,yadif,nullsrc,nullsink,abuffer,buffer,abuffersink,buffersink",
    "--extra-cflags=/Brepro @civiccast-cl.rsp",
    "--extra-ldflags=/Brepro",
)


def provenance() -> dict[str, object]:
    return {
        "schema_version": 1,
        "component": "FFmpeg",
        "version": VERSION,
        "source_commit": COMMIT,
        "source_url": f"https://github.com/FFmpeg/FFmpeg/archive/{COMMIT}.tar.gz",
        "source_bytes": SOURCE_BYTES,
        "source_sha256": SOURCE_SHA256,
        "security_patches": [
            {
                "commit": commit,
                "url": f"https://github.com/FFmpeg/FFmpeg/commit/{commit}.patch",
                "bytes": size,
                "sha256": digest,
                "included_paths": list(paths),
            }
            for commit, size, digest, paths in SECURITY_PATCHES
        ],
        "configure_options": list(CONFIGURE_OPTIONS),
        "external_libraries": [],
        "compiler": MSVC_COMPILER_VERSION,
        "linker": MSVC_LINKER_VERSION,
        "license": "LGPL-2.1-or-later",
        "linkage": "shared DLLs",
        "crt_linkage": "static",
        "crt_license": "LicenseRef-Microsoft-VCRedist",
        "relinking": "Replace these DLLs with interface-compatible modified builds; preserve DLL filenames expected by gstlibav.dll.",
    }


def build(work: Path, cache: Path) -> Path:
    output = build_minimal_ffmpeg(
        cache_dir=cache,
        scratch=work / "ffmpeg-build",
        source_commit=COMMIT,
        source_bytes=SOURCE_BYTES,
        source_sha256=SOURCE_SHA256,
        security_patches=SECURITY_PATCHES,
        configure_options=CONFIGURE_OPTIONS,
        dll_prefixes=tuple(name.split("-")[0] for name in DLL_NAMES),
    )
    return package_runtime(output, work)


def package_runtime(output: Path, work: Path) -> Path:
    """Write deterministic reviewed input bytes without recompiling native code."""
    actual = {path.name for path in (output / "bin").glob("*.dll")}
    if actual != set(DLL_NAMES):
        raise ValueError(f"Unexpected GStreamer FFmpeg DLL set: {actual}")
    runtime = work / "runtime"
    runtime.mkdir(exist_ok=True)
    for name in DLL_NAMES:
        shutil.copyfile(output / "bin" / name, runtime / name)
    shutil.copyfile(output / "LICENSE.txt", runtime / "COPYING.LGPLv2.1")
    (runtime / "provenance.json").write_text(
        json.dumps(provenance(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    archive = work / ARCHIVE_NAME
    files = {}
    if {p.name for p in runtime.iterdir()} != set(DLL_NAMES) | {
        "COPYING.LGPLv2.1",
        "provenance.json",
    }:
        raise ValueError("unexpected GStreamer FFmpeg packaging files")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for path in sorted(runtime.iterdir()):
            data = path.read_bytes()
            info = zipfile.ZipInfo(path.name, date_time=(2024, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            bundle.writestr(info, data)
            files[path.name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    lock = {
        "schema_version": 1,
        "version": VERSION,
        "provenance": provenance(),
        "archive": {
            "name": ARCHIVE_NAME,
            "bytes": archive.stat().st_size,
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        },
        "files": files,
    }
    (work / "native-windows-gstreamer-ffmpeg.lock.json").write_text(
        json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    print(build(args.work.resolve(), args.cache.resolve()), flush=True)


if __name__ == "__main__":
    main()
