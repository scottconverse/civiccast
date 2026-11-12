# SPDX-License-Identifier: Apache-2.0
"""Focused security-boundary tests for the gst-libav FFmpeg overlay."""

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from civiccast.native.runtime_closure import (
    ABSENCE_TOLERANT_FACTORIES,
    CONDITIONAL_FACTORIES,
    FACTORY_PLUGIN,
    REQUIRED_FACTORIES,
    STAGED_OPTIONAL_FACTORIES,
)
from scripts import build_native_gstreamer_ffmpeg as builder
from scripts import native_gstreamer_ffmpeg_overlay as overlay


def _runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict]:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    files = dict.fromkeys(overlay.DLL_NAMES, b"reviewed binary")
    files["COPYING.LGPLv2.1"] = b"reviewed license"
    files["provenance.json"] = json.dumps(builder.provenance()).encode()
    policy = {"provenance": builder.provenance(), "files": {}}
    for name, data in files.items():
        (runtime / name).write_bytes(data)
        policy["files"][name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    monkeypatch.setattr(overlay, "policy", lambda: policy)
    return runtime, policy


def test_profile_retains_every_named_libav_factory_without_demuxers() -> None:
    factories = (
        REQUIRED_FACTORIES
        | STAGED_OPTIONAL_FACTORIES
        | CONDITIONAL_FACTORIES
        | ABSENCE_TOLERANT_FACTORIES
    )
    libav = {factory for factory in factories if FACTORY_PLUGIN.get(factory) == "gstlibav.dll"}
    assert libav == {"avdec_h264", "avdec_aac", "avenc_aac", "avdec_ac3", "avenc_ac3"}
    # eac3 rides with ac3: MSVC does not drop the AC-3 decoder's E-AC-3 code paths, so
    # avcodec does not link without the E-AC-3 tables (LNK2019 ff_eac3_*).
    assert "--enable-decoder=h264,aac,ac3,eac3" in builder.CONFIGURE_OPTIONS
    assert "--enable-encoder=aac,ac3" in builder.CONFIGURE_OPTIONS
    assert "--disable-everything" in builder.CONFIGURE_OPTIONS
    assert "--disable-network" in builder.CONFIGURE_OPTIONS
    assert not any(
        option.startswith(("--enable-demuxer", "--enable-muxer", "--enable-protocol"))
        for option in builder.CONFIGURE_OPTIONS
    )
    assert len(builder.SECURITY_PATCHES) == 2
    assert set(builder.DLL_NAMES) == overlay.DLL_NAMES


def test_modified_runtime_or_patch_provenance_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, reviewed = _runtime(tmp_path, monkeypatch)
    overlay.verify_runtime(runtime)
    path = runtime / "provenance.json"
    altered = builder.provenance()
    altered["security_patches"] = []
    data = json.dumps(altered).encode()
    path.write_bytes(data)
    with pytest.raises(ValueError, match="unverified"):
        overlay.verify_runtime(runtime)
    # Even a file identity record cannot replace the independent reviewed source contract.
    reviewed["files"][path.name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    with pytest.raises(ValueError, match="provenance mismatch"):
        overlay.verify_runtime(runtime)


def test_unexpected_archive_member_and_modified_archive_are_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, reviewed = _runtime(tmp_path, monkeypatch)
    archive = tmp_path / "reviewed.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for path in runtime.iterdir():
            bundle.write(path, path.name)
        bundle.writestr("../unreviewed.dll", b"unreviewed")
    reviewed["archive"] = {
        "bytes": archive.stat().st_size,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
    }
    monkeypatch.setenv("CIVICCAST_GSTREAMER_FFMPEG_RUNTIME_ZIP", str(archive))
    with pytest.raises(ValueError, match="archive members"):
        overlay.acquire_runtime(tmp_path / "cache")
    assert not (tmp_path / "cache" / "unreviewed.dll").exists()
    archive.write_bytes(archive.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="archive missing or unverified"):
        overlay.acquire_runtime(tmp_path / "cache")


def test_packaged_overlay_is_checked_without_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, reviewed = _runtime(tmp_path, monkeypatch)
    out = tmp_path / "packaged"
    for name in reviewed["files"]:
        relative = (
            f"bin/{name}"
            if name in overlay.DLL_NAMES
            else f"licenses/ffmpeg-{overlay.VERSION}/{name}"
        )
        path = out / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((runtime / name).read_bytes())
    monkeypatch.setattr(
        overlay, "acquire_runtime", lambda _: pytest.fail("verification must not acquire inputs")
    )
    before = sorted(str(path) for path in out.rglob("*"))
    overlay.verify_overlay(out)
    assert sorted(str(path) for path in out.rglob("*")) == before
    (out / "bin/avcodec-61.dll").write_bytes(b"tampered binary")
    with pytest.raises(ValueError, match="unverified packaged"):
        overlay.verify_overlay(out)


def test_reviewed_lock_matches_source_profile_and_required_member_set() -> None:
    reviewed = json.loads(overlay.LOCK_PATH.read_text(encoding="utf-8"))
    assert reviewed["provenance"] == builder.provenance()
    assert reviewed["version"] == overlay.VERSION == builder.VERSION
    assert reviewed["archive"]["name"] == overlay.ARCHIVE_NAME == builder.ARCHIVE_NAME
    assert set(reviewed["files"]) == overlay.DLL_NAMES | {"COPYING.LGPLv2.1", "provenance.json"}
