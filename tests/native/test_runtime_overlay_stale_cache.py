# SPDX-License-Identifier: Apache-2.0
"""A stale runtime-overlay cache is rebuilt from the reviewed archive, never trusted.

RC4 candidate build 41874879732 (2026-11-04) failed on an EMPTY
``security-libraries-*`` cache directory on the self-hosted runner: the runner
cleans RUNNER_TEMP after each job and can leave emptied directories behind, and
the builder trusted the directory because it existed. The OpenSSL overlay got
the same fix in 0c6ff9c3; these are the remaining runtime overlays.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from scripts import native_glib_soup_overlay as glib_soup
from scripts import native_gstreamer_ffmpeg_overlay as gst_ffmpeg
from scripts import native_security_overlay as security


def _pin(body: bytes) -> dict:
    return {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


def _files(module) -> dict[str, bytes]:
    if module is gst_ffmpeg:
        files = {name: f"reviewed {name}".encode() for name in sorted(gst_ffmpeg.DLL_NAMES)}
        files["COPYING.LGPLv2.1"] = b"license"
        files["provenance.json"] = json.dumps({"source": "reviewed"}).encode()
        return files
    return {"libreviewed.dll": b"reviewed runtime", "LICENSE.txt": b"license"}


def _reviewed(module, tmp_path: Path, monkeypatch) -> tuple[Path, dict[str, bytes]]:
    files = _files(module)
    archive = tmp_path / module.ARCHIVE_NAME
    with zipfile.ZipFile(archive, "w") as bundle:
        for name, body in files.items():
            bundle.writestr(name, body)
    reviewed = {
        "archive": _pin(archive.read_bytes()),
        "files": {name: _pin(body) for name, body in files.items()},
    }
    if module is gst_ffmpeg:
        reviewed["provenance"] = {"source": "reviewed"}
    monkeypatch.setattr(module, "policy", lambda: reviewed)
    env = {
        security: "CIVICCAST_SECURITY_LIBRARIES_ZIP",
        glib_soup: "CIVICCAST_GLIB_SOUP_RUNTIME_ZIP",
        gst_ffmpeg: "CIVICCAST_GSTREAMER_FFMPEG_RUNTIME_ZIP",
    }[module]
    monkeypatch.setenv(env, str(archive))
    return archive, files


def _cache_dir(module, cache: Path) -> Path:
    if module is security:
        return cache / f"security-libraries-{security.policy()['archive']['sha256'][:16]}"
    if module is glib_soup:
        return cache / "glib-2.88.4-libsoup-3.8.1-nghttp2-1.70.0-runtime"
    return cache / "ffmpeg-7.1.5-security1-ac3-gstreamer-runtime"


MODULES = pytest.mark.parametrize(
    "module", [security, glib_soup, gst_ffmpeg], ids=["security", "glib-soup", "gst-ffmpeg"]
)


@MODULES
@pytest.mark.parametrize("stale", ["empty", "modified"])
def test_stale_runtime_cache_is_rebuilt_from_the_reviewed_archive(
    module, stale, tmp_path, monkeypatch
):
    _, files = _reviewed(module, tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    out = _cache_dir(module, cache)
    out.mkdir(parents=True)
    if stale == "modified":
        first = next(iter(files))
        (out / first).write_bytes(b"x" * len(files[first]))
        (out / "planted.dll").write_bytes(b"not reviewed")

    assert module.acquire_runtime(cache) == out
    assert sorted(p.name for p in out.iterdir()) == sorted(files)
    for name, body in files.items():
        assert (out / name).read_bytes() == body
    assert not [p for p in cache.iterdir() if p != out]


@MODULES
def test_stale_cache_is_never_rebuilt_from_an_unreviewed_archive(module, tmp_path, monkeypatch):
    archive, _ = _reviewed(module, tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    out = _cache_dir(module, cache)
    archive.write_bytes(archive.read_bytes() + b"tampered")
    out.mkdir(parents=True)
    with pytest.raises(ValueError):
        module.acquire_runtime(cache)
