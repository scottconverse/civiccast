# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Installed native station -> fail-closed control-plane environment."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path

import pytest


def _write_station(
    root: Path,
    *,
    runtime_updates: dict[str, object] | None = None,
    receipt_index: str | None = None,
    direct_layout: bool = False,
    write_large_v3: bool = True,
) -> tuple[Path, dict[str, tuple[int, str]]]:
    """``direct_layout=True`` writes the station straight at ``root`` (no
    ``app/<version>`` wrapping) -- the fresh-install shape the installer's
    payload extraction actually produces (no junction, no version-named
    directory; that layer belongs to the post-tag upgrade engine's
    junction-flip design, see civiccast.native.upgrade.junction). Defaults to
    the existing junction/version-root shape so every pre-existing caller is
    unaffected.

    Owner-ratified two-pack contract (2026-08-07): the ``captions-floor``
    pack is MANDATORY and is therefore ALWAYS declared in the written
    ``station-set.json``'s pack inventory, matching
    ``native_activation.rs``'s ``REQUIRED_COMPONENTS``. ``captions-large-v3``
    is OPTIONAL -- ``write_large_v3=False`` omits BOTH its model bytes on
    disk AND its pack entry from the inventory (a real floor-only station's
    manifest never carries a ``captions-large-v3`` pack at all), matching
    ``OPTIONAL_COMPONENTS``.

    The receipt's ``caption_inference`` block defaults to the REAL
    (never-monkeypatched) large-v3 identity -- what a real receipt looks
    like whenever large-v3 is the tier that actually ran, and unaffected by
    a test that monkeypatches only the FLOOR entry of ``CAPTION_TIER_REGISTRY``
    (see ``_fake_floor_registry``, which always leaves large-v3 untouched). A
    floor-only test that needs the receipt to instead claim the floor
    identity overwrites it afterward with
    ``_replace_receipt_caption_inference`` once the floor tier's real staged
    bytes (and, where used, a fake registry) are known -- ``_write_station``
    itself cannot know that identity in advance, since floor bytes are
    written by a separate helper called after this one returns."""

    version_root = root if direct_layout else root / "app" / "4.0.0-beta1"
    version_root.mkdir(parents=True, exist_ok=True)
    files: dict[str, tuple[int, str]] = {}
    if write_large_v3:
        model_root = (
            version_root / "components" / "captions-large-v3" / "models" / "faster-whisper-large-v3"
        )
        model_root.mkdir(parents=True)
        for name, body in {
            "README.md": b"readme",
            "config.json": b"config",
            "model.bin": b"model",
            "preprocessor_config.json": b"preprocessor",
            "tokenizer.json": b"tokenizer",
            "vocabulary.json": b"vocabulary",
        }.items():
            (model_root / name).write_bytes(body)
            files[name] = (len(body), hashlib.sha256(body).hexdigest())

    index = "ab" * 32
    runtime: dict[str, object] = {
        "caption_tap": "inline",
        "caption_tap_atomic": True,
        "caption_model_root": ("components/captions-large-v3/models/faster-whisper-large-v3"),
        "caption_runtime": "faster-whisper",
        "caption_device": "cpu",
        "caption_compute_type": "int8",
        "egress_engine": "gstreamer",
        "egress_embed_captions": True,
        "offline_only": True,
        "whistle_assets_root": "packs/captions-whistle",
    }
    runtime.update(runtime_updates or {})
    packs: list[dict[str, object]] = [
        {"component": "core", "root": ".", "outer_sha256": "00" * 32},
        # Mandatory two-pack contract: captions-floor is ALWAYS declared,
        # regardless of write_large_v3 -- a real activation's manifest
        # always carries it (native_activation.rs's REQUIRED_COMPONENTS).
        {
            "component": "captions-floor",
            "root": "packs/captions-floor",
            "outer_sha256": "55" * 32,
        },
        {
            "component": "captions-whistle",
            "root": "packs/captions-whistle",
            "outer_sha256": "66" * 32,
        },
        {
            "component": "summary-gemma4-12b",
            "root": "components/summary-gemma4-12b",
            "outer_sha256": "22" * 32,
        },
        {
            "component": "summary-gemma4-e4b",
            "root": "components/summary-gemma4-e4b",
            "outer_sha256": "33" * 32,
        },
        {
            "component": "translation-translategemma-4b",
            "root": "components/translation-translategemma-4b",
            "outer_sha256": "44" * 32,
        },
    ]
    if write_large_v3:
        # Optional: declared only when actually staged, matching
        # OPTIONAL_COMPONENTS/OPTIONAL_COMPONENT_ROOTS.
        packs.append(
            {
                "component": "captions-large-v3",
                "root": "components/captions-large-v3",
                "outer_sha256": "11" * 32,
            }
        )
    station = {
        "schema_version": 2,
        "product": "civiccast-native",
        "product_version": "4.0.0-beta1",
        "compatible_core": "4.0.0-beta1",
        "distribution_index_sha256": index,
        "signing_key_id": "test-key",
        "packs": packs,
        "runtime": runtime,
    }
    (version_root / "station-set.json").write_text(json.dumps(station), encoding="utf-8")
    receipt = {
        "schema_version": 1,
        "product": "civiccast-native",
        "product_version": "4.0.0-beta1",
        "distribution_index_sha256": receipt_index or index,
        "caption_inference": {
            "runtime": "faster-whisper 1.2.1",
            "ctranslate2": "4.8.1",
            "model": ("Systran/faster-whisper-large-v3@edaa852ec7e145841d8ffdb056a99866b5f0a478"),
            "model_path": ("components/captions-large-v3/models/faster-whisper-large-v3"),
            "model_bin_bytes": 3_087_284_237,
            "model_bin_sha256": (
                "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1"
            ),
            "device": "cpu",
            "compute_type": "int8",
            "local_files_only": True,
            "result": "passed",
        },
    }
    (version_root / "activation-self-test.json").write_text(json.dumps(receipt), encoding="utf-8")
    return version_root, files


_WHISTLE_LIVE_PROFILE = {
    "CIVICCAST_CAPTION_TAP_SEGMENT_SECONDS": "3",
    "CIVICCAST_CAPTION_TAP_OVERLAP_SECONDS": "2",
    "CIVICCAST_CAPTION_TAP_POLL_SECONDS": "0.5",
    "CIVICCAST_CAPTION_FEED_POLL_SECONDS": "0.5",
    "CIVICCAST_CAPTION_TAP_OVERLOAD_PERSISTENCE_SCANS": "60",
    "CIVICCAST_CAPTION_TAP_RECOVERY_SCANS": "12",
}


def _native_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    return station_runtime.load_native_station_environment(
        version_root, program_data_root=tmp_path / "ProgramData" / "CivicCast"
    )


def test_station_environment_applies_measured_whistle_live_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # RC4 measurement (3 x 90 s RC4 speech, shipped d4d4ec8d engine): 5 s
    # pieces left words 4.2 s late on average / 8 s worst before two 2 s
    # polls; 3 s pieces + 2 s overlap matched 5 s accuracy at half the delay.
    for name in _WHISTLE_LIVE_PROFILE:
        monkeypatch.delenv(name, raising=False)

    env = _native_env(tmp_path, monkeypatch)

    for name, value in _WHISTLE_LIVE_PROFILE.items():
        assert env[name] == value, name


def test_station_environment_keeps_operator_caption_timing_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overrides = {name: f"{index + 7}" for index, name in enumerate(_WHISTLE_LIVE_PROFILE)}
    for name, value in overrides.items():
        monkeypatch.setenv(name, value)

    env = _native_env(tmp_path, monkeypatch)

    for name, value in overrides.items():
        assert env[name] == value, name


def test_station_environment_enables_mandatory_offline_captions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from civiccast.native import station_runtime

    monkeypatch.setenv("PATH", "existing-control-plane-path")
    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)

    env = station_runtime.load_native_station_environment(
        version_root,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
    )

    assert env["CIVICCAST_NATIVE_STATION"] == "1"
    assert env["CIVICCAST_CAPTION_TAP"] == "inline"
    assert env["CIVICCAST_CAPTION_TAP_ATOMIC"] == "1"
    assert env["CIVICCAST_CAPTION_TAP_DIR"] == str(
        tmp_path / "ProgramData" / "CivicCast" / "data" / "caption-tap"
    )
    assert env["CIVICCAST_CAPTION_RUNTIME"] == "faster-whisper"
    assert env["CIVICCAST_WHISPER_MODEL_PATH"] == str(
        version_root / "components" / "captions-large-v3" / "models" / "faster-whisper-large-v3"
    )
    assert env["CIVICCAST_WHISTLE_ROOT"] == str(version_root / "packs" / "captions-whistle")
    assert env["CIVICCAST_WHISPER_DEVICE"] == "cpu"
    assert env["CIVICCAST_WHISPER_COMPUTE_TYPE"] == "int8"
    assert env["CIVICCAST_EGRESS_ENGINE"] == "gstreamer"
    assert env["CIVICCAST_EGRESS_EMBED_CAPTIONS"] == "1"
    assert env["PATH"] == "existing-control-plane-path"
    assert "CIVICCAST_CAPTION_CUDA_DIR" not in env
    assert "CIVICCAST_CAPTION_CUDA_HASH_RECEIPT" not in env
    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["TRANSFORMERS_OFFLINE"] == "1"


def test_station_environment_caption_tap_off_switch_disables_the_tap_leg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 91: ``CIVICCAST_CAPTION_TAP=off`` in the service environment must
    survive into the child environment unmolested, and the tap directory must
    be neutralized to the empty string alongside it -- otherwise
    ``build_audio_tap_plan`` (``civiccast.captions.tap``) sees a directory and
    builds the tee anyway, since it only ever consults
    ``CIVICCAST_CAPTION_TAP_DIR``.

    Review round 2: merely OMITTING the key from this function's return value
    (which becomes ``spec.env``) is not enough, because
    ``civiccast/native/supervisor/service.py`` composes the child's real
    environment as ``env = {**os.environ, **spec.env}`` -- an inherited
    ``CIVICCAST_CAPTION_TAP_DIR`` already present in the SAME ``os.environ``
    this function itself reads (a stray leftover from a previous
    configuration) would win the merge unopposed if ``spec.env`` merely
    left the key out. The key must be explicitly set to ``""`` here so this
    dict (as ``spec.env``, applied LAST) always overrides whatever
    ``os.environ`` carries. This test drives the real merge, not just this
    function's return value, so it would have caught the omission-only
    version of the fix.
    """

    from civiccast.native import station_runtime

    monkeypatch.setenv("PATH", "existing-control-plane-path")
    monkeypatch.setenv("CIVICCAST_CAPTION_TAP", "off")
    # A stray leftover from a previous configuration, in the SAME os.environ
    # the merge in service.py will also read.
    stale_dir = str(tmp_path / "stale-tap-dir")
    monkeypatch.setenv("CIVICCAST_CAPTION_TAP_DIR", stale_dir)
    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)

    spec_env = station_runtime.load_native_station_environment(
        version_root,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
    )

    assert spec_env["CIVICCAST_CAPTION_TAP"] == "off"
    assert spec_env["CIVICCAST_CAPTION_TAP_DIR"] == ""
    # Everything else the tap doesn't own is unaffected.
    assert spec_env["CIVICCAST_CAPTION_TAP_ATOMIC"] == "1"
    assert spec_env["CIVICCAST_CAPTION_RUNTIME"] == "faster-whisper"
    assert spec_env["CIVICCAST_EGRESS_EMBED_CAPTIONS"] == "1"

    # The real merge civiccast/native/supervisor/service.py performs when it
    # spawns the child: env = {**os.environ, **spec.env}. os.environ here
    # still carries the stale CIVICCAST_CAPTION_TAP_DIR set above (this
    # function reads os.environ but never mutates it) -- prove spec.env's
    # explicit "" wins that merge rather than the stray value surviving it.
    merged_child_env = {**os.environ, **spec_env}
    assert merged_child_env["CIVICCAST_CAPTION_TAP_DIR"] == ""
    assert merged_child_env["CIVICCAST_CAPTION_TAP_DIR"] != stale_dir

    # And build_audio_tap_plan (the actual consumer) treats that "" exactly
    # like unset, so the tee is not built even though a dir was configured
    # in the environment this child inherits from.
    from civiccast.captions.tap import build_audio_tap_plan

    with monkeypatch.context() as merged_patch:
        for key, value in merged_child_env.items():
            merged_patch.setenv(key, value)
        assert build_audio_tap_plan("gov-ch12") is None


@pytest.mark.parametrize("configured_value", ["", "inline", "ON", "  ", "unexpected-value"])
def test_station_environment_caption_tap_non_off_values_keep_forcing_inline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_value: str
) -> None:
    """Anything other than the literal ``off`` (unset, ``inline``, or a typo)
    must keep today's behavior byte-for-byte: forced inline mode, tap dir
    injected. Only the exact ``off`` string (case/whitespace-insensitive)
    changes anything."""

    from civiccast.native import station_runtime

    monkeypatch.setenv("PATH", "existing-control-plane-path")
    if configured_value:
        monkeypatch.setenv("CIVICCAST_CAPTION_TAP", configured_value)
    else:
        monkeypatch.delenv("CIVICCAST_CAPTION_TAP", raising=False)
    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)

    env = station_runtime.load_native_station_environment(
        version_root,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
    )

    assert env["CIVICCAST_CAPTION_TAP"] == "inline"
    assert env["CIVICCAST_CAPTION_TAP_DIR"] == str(
        tmp_path / "ProgramData" / "CivicCast" / "data" / "caption-tap"
    )


def test_station_environment_starts_without_cuda_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mandatory native station must operate on a CPU-only Windows host."""

    from civiccast.native import station_runtime

    monkeypatch.setenv("PATH", "existing-control-plane-path")
    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)

    env = station_runtime.load_native_station_environment(version_root)

    assert env["CIVICCAST_WHISPER_DEVICE"] == "cpu"
    assert env["CIVICCAST_WHISPER_COMPUTE_TYPE"] == "int8"
    assert env["PATH"] == "existing-control-plane-path"
    assert "CIVICCAST_CAPTION_CUDA_DIR" not in env
    assert "CIVICCAST_CAPTION_CUDA_HASH_RECEIPT" not in env


def _write_real_install_gstreamer_closure(version_root: Path) -> None:
    """The REAL installed-product shape (K2): the closure is composed into
    the native-app-payload pack (``scripts/build_native_app_payload_pack.py``'s
    ``_compose_payload_with_closure``, which writes it to ``dependencies/
    gstreamer`` relative to that pack's OWN root), and that pack extracts to
    ``<version_root>/runtime`` (``native_pack_staging::
    pack_extraction_destination``) -- the same directory ``station-set.json``'s
    embedded interpreter lives beside per ``station_environment_for_python``.
    So on disk the closure lands at ``<version_root>/runtime/dependencies/
    gstreamer``, mirrored here one level below the flat fixture
    ``tests/native/test_gstreamer_runtime.py``'s own ``_root`` helper builds
    for ``installed_gstreamer_environment``'s unit tests."""

    for item in (
        "runtime/dependencies/gstreamer/bin/gst-discoverer-1.0.exe",
        "runtime/dependencies/gstreamer/lib/gstreamer-1.0/gstcoreelements.dll",
        "runtime/dependencies/gstreamer/lib/girepository-1.0/Gst-1.0.typelib",
        "runtime/dependencies/gstreamer/python/gi/__init__.py",
    ):
        path = version_root / item
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")


def _write_pre_fix_wrong_path_gstreamer_closure(version_root: Path) -> None:
    """The shape the ORIGINAL (buggy) gate checked --
    ``<version_root>/dependencies/gstreamer``, one level too shallow, missing
    the ``runtime/`` component the real installer's payload extraction always
    inserts. Never occurs on a real install (see
    ``_write_real_install_gstreamer_closure``); written only to pin that the
    fixed gate does not accidentally treat it as valid."""

    for item in (
        "dependencies/gstreamer/bin/gst-discoverer-1.0.exe",
        "dependencies/gstreamer/lib/gstreamer-1.0/gstcoreelements.dll",
        "dependencies/gstreamer/lib/girepository-1.0/Gst-1.0.typelib",
        "dependencies/gstreamer/python/gi/__init__.py",
    ):
        path = version_root / item
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")


def test_station_environment_injects_gstreamer_at_the_real_install_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K2 regression: a station whose GStreamer closure is staged where the
    real installer actually puts it -- ``<version_root>/runtime/dependencies/
    gstreamer`` -- must get the GStreamer environment injected."""

    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    _write_real_install_gstreamer_closure(version_root)

    env = station_runtime.load_native_station_environment(
        version_root,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
    )

    gstreamer_root = version_root / "runtime" / "dependencies" / "gstreamer"
    assert env["CIVICCAST_GSTREAMER_RUNTIME_ROOT"] == str(version_root / "runtime")
    assert env["GST_PLUGIN_PATH"] == str(gstreamer_root / "lib" / "gstreamer-1.0")
    assert env["GI_TYPELIB_PATH"] == str(gstreamer_root / "lib" / "girepository-1.0")
    assert env["PYGI_DLL_DIRS"] == str(gstreamer_root / "bin")
    assert env["CIVICCAST_GSTREAMER_PYTHON"] == str(gstreamer_root / "python")
    assert env["PATH"].startswith(str(gstreamer_root / "bin"))


def test_station_environment_does_not_inject_gstreamer_at_the_pre_fix_wrong_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K2 regression: a closure staged ONLY at the original gate's wrong,
    one-level-too-shallow path (``<version_root>/dependencies/gstreamer``,
    with no ``runtime/`` component) must NOT be treated as installed --
    that shape never occurs on a real station, and the fixed gate must not
    be reverted to accept it."""

    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    _write_pre_fix_wrong_path_gstreamer_closure(version_root)

    env = station_runtime.load_native_station_environment(
        version_root,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
    )

    assert "CIVICCAST_GSTREAMER_RUNTIME_ROOT" not in env
    assert "GST_PLUGIN_PATH" not in env
    assert "GI_TYPELIB_PATH" not in env


def test_station_environment_degrades_past_a_corrupt_gstreamer_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """CRITICAL fix (post-K2, PR #404 follow-up): the dir gate at
    ``<version_root>/runtime/dependencies/gstreamer`` only proves the
    directory exists, not that the closure inside it is intact.
    ``installed_gstreamer_environment`` raises ``GstreamerRuntimeError`` for
    a partial/corrupt/AV-quarantined closure (a missing required file, here
    ``bin/gst-discoverer-1.0.exe``), and that used to propagate straight out
    of ``load_native_station_environment`` -- crashing the whole supervisor
    (all streaming, not just GStreamer egress) for a non-malicious,
    recoverable install state. It must now degrade the same way CUDA-absent
    already does: log loudly and continue WITHOUT the GStreamer env
    injected -- and, when in-place self-repair does not restore the closure,
    actually SWITCH egress to the FFmpeg concat engine so the channel keeps
    airing (the Codex P1 on PR #406; before that fix the engine stayed
    ``gstreamer`` with no runtime keys, the overclaim this test now pins)."""

    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    _write_real_install_gstreamer_closure(version_root)
    # Corrupt the closure: the directory gate at station_runtime.py's
    # `if (gstreamer_runtime_root / "dependencies" / "gstreamer").is_dir()`
    # still passes (the directory exists), but a required file inside it is
    # missing -- exactly the shape `installed_gstreamer_environment` raises
    # `GstreamerRuntimeError` for. The DEFAULT self-repair (re-verify once)
    # also fails here (the file stays missing), so this exercises the
    # unrepaired -> FFmpeg fallback path.
    (version_root / "runtime/dependencies/gstreamer/bin/gst-discoverer-1.0.exe").unlink()

    with caplog.at_level("ERROR", logger="civiccast.native.station_runtime"):
        env = station_runtime.load_native_station_environment(
            version_root,
            program_data_root=tmp_path / "ProgramData" / "CivicCast",
        )

    # Degraded, not crashed: no GStreamer keys, but the rest of the station
    # environment (mandatory offline captions, etc.) is still present.
    assert "CIVICCAST_GSTREAMER_RUNTIME_ROOT" not in env
    assert "GST_PLUGIN_PATH" not in env
    assert "GI_TYPELIB_PATH" not in env
    assert "PYGI_DLL_DIRS" not in env
    assert "CIVICCAST_GSTREAMER_PYTHON" not in env
    assert env["CIVICCAST_NATIVE_STATION"] == "1"
    assert env["CIVICCAST_CAPTION_RUNTIME"] == "faster-whisper"
    # The P1: egress is actually switched to FFmpeg (not left claiming
    # gstreamer), and the degraded reason is recorded for the control plane.
    assert env["CIVICCAST_EGRESS_ENGINE"] == "ffmpeg-concat"
    assert "corrupt or partial" in env["CIVICCAST_EGRESS_DEGRADED_REASON"]
    assert any(
        "gstreamer" in record.message.lower() and "degrad" in record.message.lower()
        for record in caplog.records
    )


def test_station_environment_for_python_degrades_past_a_corrupt_gstreamer_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same corruption, exercised through the supervisor's actual entry
    point (``station_environment_for_python``) so the fix is proven at the
    seam that used to crash the whole supervisor, not just at the lower-level
    helper."""

    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path, direct_layout=True)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    _write_real_install_gstreamer_closure(version_root)
    (version_root / "runtime/dependencies/gstreamer/bin/gst-discoverer-1.0.exe").unlink()
    python_path = version_root / "runtime" / "python.exe"
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_bytes(b"fixture")

    env = station_runtime.station_environment_for_python(
        python_path,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
    )

    assert "CIVICCAST_GSTREAMER_RUNTIME_ROOT" not in env
    assert env["CIVICCAST_NATIVE_STATION"] == "1"
    # Same P1 through the supervisor's real entry point: egress switched to
    # FFmpeg so the channel keeps airing.
    assert env["CIVICCAST_EGRESS_ENGINE"] == "ffmpeg-concat"


def test_corrupt_closure_self_repair_success_runs_gstreamer_normally(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Degraded-mode tier 2: a corrupt closure that the ONE automatic in-place
    self-repair restores must run GStreamer egress normally -- the GStreamer
    env keys are injected and the egress engine stays ``gstreamer`` (no FFmpeg
    switch, no degraded reason). Models the transient AV-quarantine case: the
    file was missing when first validated and present a moment later."""

    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    _write_real_install_gstreamer_closure(version_root)
    missing = version_root / "runtime/dependencies/gstreamer/bin/gst-discoverer-1.0.exe"
    missing.unlink()

    def _repair(runtime_root: Path) -> bool:
        # A real transient recovery: restore the quarantined file, then report
        # healthy so re-validation succeeds.
        (runtime_root / "dependencies/gstreamer/bin/gst-discoverer-1.0.exe").write_bytes(b"fixture")
        return True

    env = station_runtime.load_native_station_environment(
        version_root,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
        gstreamer_repair_hook=_repair,
    )

    gstreamer_root = version_root / "runtime" / "dependencies" / "gstreamer"
    assert env["CIVICCAST_GSTREAMER_RUNTIME_ROOT"] == str(version_root / "runtime")
    assert env["GST_PLUGIN_PATH"] == str(gstreamer_root / "lib" / "gstreamer-1.0")
    assert env["CIVICCAST_EGRESS_ENGINE"] == "gstreamer"
    assert "CIVICCAST_EGRESS_DEGRADED_REASON" not in env


def test_unrepaired_corrupt_closure_switches_the_selector_to_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Degraded-mode tier 3 / the Codex P1 on PR #406: when self-repair does
    NOT restore the closure, the returned environment must select the FFmpeg
    concat engine so ``engine_select.build_encoder_strategy`` -- fed that
    environment -- actually returns ``ConcatEncoderStrategy`` and the channel
    keeps airing on FFmpeg. Asserts the SELECTOR switches, not merely the env
    string."""

    from civiccast.egress.encoder_strategy import ConcatEncoderStrategy
    from civiccast.egress.engine_select import build_encoder_strategy
    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    _write_real_install_gstreamer_closure(version_root)
    (version_root / "runtime/dependencies/gstreamer/bin/gst-discoverer-1.0.exe").unlink()

    env = station_runtime.load_native_station_environment(
        version_root,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
        gstreamer_repair_hook=lambda _root: False,  # self-repair fails
    )

    assert env["CIVICCAST_EGRESS_ENGINE"] == "ffmpeg-concat"
    # The selector actually switches when handed the degraded environment.
    strategy = build_encoder_strategy(env["CIVICCAST_EGRESS_ENGINE"])
    assert isinstance(strategy, ConcatEncoderStrategy)
    # And a healthy closure still selects the gstreamer engine, proving the
    # switch is the degrade, not the default.
    assert build_encoder_strategy("gstreamer").name != ConcatEncoderStrategy().name


def test_corrupt_closure_self_repair_hook_that_raises_falls_back_to_ffmpeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A self-repair hook that RAISES must never turn a degrade into a crash:
    the supervisor stays up and egress falls back to FFmpeg."""

    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    _write_real_install_gstreamer_closure(version_root)
    (version_root / "runtime/dependencies/gstreamer/bin/gst-discoverer-1.0.exe").unlink()

    def _boom(_root: Path) -> bool:
        raise RuntimeError("repair blew up")

    env = station_runtime.load_native_station_environment(
        version_root,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
        gstreamer_repair_hook=_boom,
    )

    assert env["CIVICCAST_EGRESS_ENGINE"] == "ffmpeg-concat"
    assert env["CIVICCAST_NATIVE_STATION"] == "1"


def test_station_environment_rejects_an_unaccepted_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from civiccast.native import station_runtime

    version_root, files = _write_station(
        tmp_path, runtime_updates={"caption_runtime": "whispercpp-vulkan"}
    )
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)

    with pytest.raises(
        station_runtime.NativeStationConfigurationError,
        match="runtime contract",
    ):
        station_runtime.load_native_station_environment(version_root)


def test_station_environment_rejects_a_receipt_from_another_distribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path, receipt_index="cd" * 32)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)

    with pytest.raises(
        station_runtime.NativeStationConfigurationError,
        match="activation self-test",
    ):
        station_runtime.load_native_station_environment(version_root)


def test_station_environment_rejects_missing_packaged_model_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    (
        version_root
        / "components"
        / "captions-large-v3"
        / "models"
        / "faster-whisper-large-v3"
        / "model.bin"
    ).unlink()

    with pytest.raises(
        station_runtime.NativeStationConfigurationError,
        match=r"model\.bin",
    ):
        station_runtime.load_native_station_environment(version_root)


def test_station_environment_rejects_same_length_model_tamper_before_returning_child_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Byte-count validation alone must never admit a substituted large-v3 file."""

    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    model = (
        version_root
        / "components"
        / "captions-large-v3"
        / "models"
        / "faster-whisper-large-v3"
        / "model.bin"
    )
    model.write_bytes(b"M" * len(b"model"))

    with pytest.raises(
        station_runtime.NativeStationConfigurationError,
        match=r"model\.bin.*SHA-256",
    ):
        station_runtime.load_native_station_environment(version_root)


def test_station_environment_exposes_verified_model_hash_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The child receives the exact verified model identity, not an unchecked path."""

    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)

    env = station_runtime.load_native_station_environment(version_root)

    assert json.loads(env["CIVICCAST_CAPTION_MODEL_HASH_RECEIPT"]) == {
        name: {"bytes": size, "sha256": digest} for name, (size, digest) in files.items()
    }


def test_station_environment_does_not_select_optional_cuda_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    cuda = version_root / "dependencies" / "ollama" / "lib" / "ollama" / "cuda_v12"
    cuda.mkdir(parents=True)
    (cuda / "cublas64_12.dll").write_bytes(b"optional accelerator")

    env = station_runtime.load_native_station_environment(version_root)

    assert env["CIVICCAST_WHISPER_DEVICE"] == "cpu"
    assert str(cuda) not in env["PATH"]
    assert "CIVICCAST_CUDA_BIN_DIR" not in env


def test_station_environment_selects_cuda_staged_only_at_the_acquisition_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chain H1: a future ``native-cuda-runtime`` component the non-elevated
    first-run GUI downloaded lands under the acquisition root
    (``<PROGRAMDATA>\\CivicCast``), never under ``version_root`` -- the GUI
    cannot write there at all. The presence gate must still find it there,
    exactly like caption tier resolution already does, and the child's PATH
    must carry the WINNING root's bin dir, not version_root's (nothing is
    staged there)."""

    from civiccast.native import station_runtime

    monkeypatch.setenv("PATH", "existing-control-plane-path")
    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: 16.0)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)

    acquisition_root = tmp_path / "ProgramData" / "CivicCast"
    acquisition_cuda_bin = _stage_cuda_libs(acquisition_root)

    env = station_runtime.load_native_station_environment(
        version_root, program_data_root=acquisition_root
    )

    assert env["CIVICCAST_WHISPER_DEVICE"] == "cuda"
    assert env["PATH"].startswith(str(acquisition_cuda_bin) + os.pathsep)
    assert not (version_root / "dependencies" / "cuda").exists()
    # TESTER4 (RTX 5070 Ti): PATH alone was proven insufficient for the
    # Windows loader's dependent-DLL resolution -- CIVICCAST_CUDA_BIN_DIR is
    # the other half of that fix (civiccast.captions.runtime reads it and
    # calls os.add_dll_directory), and must name the SAME winning root's bin
    # dir the PATH prepend above used, not version_root's.
    assert env["CIVICCAST_CUDA_BIN_DIR"] == str(acquisition_cuda_bin)


def test_station_environment_for_python_derives_its_version_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    python = version_root / "runtime" / "python.exe"
    python.parent.mkdir(exist_ok=True)
    python.write_bytes(b"python")

    env = station_runtime.station_environment_for_python(
        python,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
    )

    assert env["CIVICCAST_NATIVE_STATION_ROOT"] == str(version_root)


def test_station_environment_for_python_accepts_the_direct_fresh_install_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sandbox run 16 layer-4 crash: a fresh install extracts the payload
    DIRECTLY to ``<install_root>\\runtime\\`` -- there is no ``app/<version>``
    junction layer (that belongs to the post-tag upgrade engine, see
    civiccast.native.upgrade.junction). ``install_root``'s own directory name
    (here ``tmp_path``, e.g. a pytest temp dir name) does NOT equal the
    station-set's ``product_version`` ("4.0.0-beta1"), and this must still be
    accepted -- RED today: ``load_native_station_environment`` demands
    ``root.name == version``, which only ever holds for the junction-resolved
    shape."""

    from civiccast.native import station_runtime

    install_root, files = _write_station(tmp_path, direct_layout=True)
    assert install_root == tmp_path  # sanity: genuinely no app/<version> wrapping
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    python = install_root / "runtime" / "python.exe"
    python.parent.mkdir(exist_ok=True)
    python.write_bytes(b"python")

    env = station_runtime.station_environment_for_python(
        python,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
    )

    assert env["CIVICCAST_NATIVE_STATION_ROOT"] == str(install_root)


def test_station_environment_for_python_accepts_the_running_services_pythonservice_exe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sandbox run 16 layer-4 crash: the Windows service host's ``sys.executable``
    is ``<version-root>/runtime/pythonservice.exe``, never ``python.exe`` --
    RED today: the name check only accepts ``python.exe``/``pythonw.exe``."""

    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    python = version_root / "runtime" / "pythonservice.exe"
    python.parent.mkdir(exist_ok=True)
    python.write_bytes(b"python")

    env = station_runtime.station_environment_for_python(
        python,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
    )

    assert env["CIVICCAST_NATIVE_STATION_ROOT"] == str(version_root)


def test_station_environment_for_python_accepts_pythonservice_exe_on_the_direct_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact combination from the live Sandbox run 16 traceback: the
    fresh-install direct layout (no app/<version> wrapping) PLUS the
    service host's pythonservice.exe name -- both must be accepted at once."""

    from civiccast.native import station_runtime

    install_root, files = _write_station(tmp_path, direct_layout=True)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    python = install_root / "runtime" / "pythonservice.exe"
    python.parent.mkdir(exist_ok=True)
    python.write_bytes(b"python")

    env = station_runtime.station_environment_for_python(
        python,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
    )

    assert env["CIVICCAST_NATIVE_STATION_ROOT"] == str(install_root)


def test_station_environment_for_python_still_rejects_a_mismatched_junction_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Do NOT weaken the junction-layout check beyond adding the direct shape:
    a version-root reached via the ``app/<version>`` convention whose
    directory name does not match the station-set's ``product_version`` must
    still fail closed -- this is the "wrong version pointed at" corruption
    case the original check exists to catch, and it is structurally
    distinguishable from the direct/fresh-install shape (an ``app`` parent
    directory is present)."""

    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    wrong_name_root = version_root.parent / "not-the-right-version"
    version_root.rename(wrong_name_root)
    python = wrong_name_root / "runtime" / "python.exe"
    python.parent.mkdir(exist_ok=True)
    python.write_bytes(b"python")

    with pytest.raises(
        station_runtime.NativeStationConfigurationError,
        match="version root",
    ):
        station_runtime.station_environment_for_python(
            python,
            program_data_root=tmp_path / "ProgramData" / "CivicCast",
        )


def _fake_floor_registry(files: dict[str, tuple[int, str]]):
    """A registry with a real (unchanged) large-v3 entry plus a SYNTHETIC
    bound floor entry -- mirrors the pattern
    `tests/native/test_caption_pack_tier_verification.py`'s Rust-side sibling
    already uses (a small, fast fixture file set standing in for the real,
    multi-GB `Systran/faster-whisper-medium` pin) rather than downloading or
    hard-coding the real pinned hashes into this test."""

    from civiccast.native.caption_tiers import (
        CAPTION_TIER_REGISTRY,
        FLOOR_TIER_ID,
        CaptionTierSpec,
    )

    return {
        **CAPTION_TIER_REGISTRY,
        FLOOR_TIER_ID: CaptionTierSpec(
            tier_id=FLOOR_TIER_ID,
            model_directory="faster-whisper-medium",
            model_repository="Systran/faster-whisper-medium",
            model_revision="test-revision",
            files=files,
            pending=False,
        ),
    }


def _receipt_caption_inference_for_tier(tier_id: str, registry: dict) -> dict[str, object]:
    """Build the ``caption_inference`` block a REAL activation receipt would
    contain for ``tier_id`` given ``registry`` -- computed independently
    from ``registry``'s own pinned fields (``model_repository``,
    ``model_revision``, ``files["model.bin"]``), never by calling
    ``station_runtime``'s private receipt-validation helper. These tests
    must exercise the tier-aware receipt gate against a ground truth built
    the same way the real Rust receipt writer
    (``main.rs::write_native_activation_self_test_receipt``) builds one, not
    by echoing the implementation back at itself. ``model_path`` is the one
    field reused from ``station_runtime.caption_tier_model_relative_root``
    (an existing, separately-tested path-resolution utility, not the
    hash-pinning logic under test here)."""

    from civiccast.native import station_runtime

    spec = registry[tier_id]
    model_bin_bytes, model_bin_sha256 = spec.files["model.bin"]
    return {
        "runtime": "faster-whisper 1.2.1",
        "ctranslate2": "4.8.1",
        "model": f"{spec.model_repository}@{spec.model_revision}",
        "model_path": station_runtime.caption_tier_model_relative_root(tier_id),
        "model_bin_bytes": model_bin_bytes,
        "model_bin_sha256": model_bin_sha256,
        "device": "cpu",
        "compute_type": "int8",
        "local_files_only": True,
        "result": "passed",
    }


def _replace_receipt_caption_inference(
    version_root: Path, caption_inference: dict[str, object]
) -> None:
    """Overwrite ONLY the ``caption_inference`` block of an already-written
    ``activation-self-test.json`` -- everything else (``schema_version``,
    ``product_version``, ``distribution_index_sha256``) stays exactly what
    ``_write_station`` wrote, so a test that needs a different caption
    identity does not have to re-derive or duplicate those unrelated
    fields."""

    receipt_path = version_root / "activation-self-test.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["caption_inference"] = caption_inference
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")


def test_caption_tier_model_relative_root_resolves_large_v3_unchanged() -> None:
    from civiccast.native import station_runtime

    assert (
        station_runtime.caption_tier_model_relative_root("large-v3")
        == "components/captions-large-v3/models/faster-whisper-large-v3"
    )


def test_caption_tier_model_relative_root_resolves_the_floor_tier_at_its_staged_location() -> None:
    """Task #57 (b): the floor tier must resolve at the EXACT on-disk
    location the installer's acquisition download experience stages it
    (`component_acquisition.rs`'s `caption_floor_tier_destination`:
    `packs\\captions-floor\\models\\faster-whisper-medium`), via the SAME
    per-tier resolution mechanism large-v3 uses -- never a parallel
    convention."""

    from civiccast.native import station_runtime

    assert (
        station_runtime.caption_tier_model_relative_root("floor")
        == "packs/captions-floor/models/faster-whisper-medium"
    )
    assert (
        station_runtime.FLOOR_CAPTION_MODEL_RELATIVE_ROOT
        == "packs/captions-floor/models/faster-whisper-medium"
    )


def test_caption_tier_model_relative_root_rejects_an_unknown_tier() -> None:
    from civiccast.native import station_runtime

    with pytest.raises(station_runtime.NativeStationConfigurationError):
        station_runtime.caption_tier_model_relative_root("not-a-real-tier")


def _write_floor_tier_files(version_root: Path) -> dict[str, tuple[int, str]]:
    model_root = version_root / "packs" / "captions-floor" / "models" / "faster-whisper-medium"
    model_root.mkdir(parents=True)
    files: dict[str, tuple[int, str]] = {}
    for name, body in {
        "config.json": b"floor-config",
        "model.bin": b"floor-model-bytes",
        "tokenizer.json": b"floor-tokenizer",
        "vocabulary.txt": b"floor-vocab",
    }.items():
        (model_root / name).write_bytes(body)
        files[name] = (len(body), hashlib.sha256(body).hexdigest())
    return files


def test_validate_floor_caption_model_root_accepts_a_correctly_staged_floor_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED-first (task #57 (b)): before this fix, station_runtime had no
    function at all that could find or verify the floor tier -- only
    large-v3's fixed `components/captions-large-v3/models/...` path was ever
    checked. After the fix, the floor tier resolves and verifies via the
    SAME per-tier machinery, at the location the installer's download
    experience actually stages it."""

    from civiccast.native import station_runtime

    version_root = tmp_path / "app" / "4.0.0-beta1"
    files = _write_floor_tier_files(version_root)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", _fake_floor_registry(files))

    resolved_root, hash_receipt = station_runtime.validate_floor_caption_model_root(version_root)

    expected_root = version_root / "packs" / "captions-floor" / "models" / "faster-whisper-medium"
    assert resolved_root == expected_root.resolve()
    assert hash_receipt == {
        name: {"bytes": size, "sha256": digest} for name, (size, digest) in files.items()
    }


def test_validate_floor_caption_model_root_fails_closed_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from civiccast.native import station_runtime

    version_root = tmp_path / "app" / "4.0.0-beta1"
    version_root.mkdir(parents=True)
    files = {"config.json": (5, "0" * 64)}
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", _fake_floor_registry(files))

    with pytest.raises(station_runtime.NativeStationConfigurationError, match="is missing"):
        station_runtime.validate_floor_caption_model_root(version_root)


def test_validate_floor_caption_model_root_rejects_a_tampered_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from civiccast.native import station_runtime

    version_root = tmp_path / "app" / "4.0.0-beta1"
    files = _write_floor_tier_files(version_root)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", _fake_floor_registry(files))
    model_bin = (
        version_root / "packs" / "captions-floor" / "models" / "faster-whisper-medium" / "model.bin"
    )
    # Same-length tamper -- proves byte-count alone never admits a
    # substituted file (same posture as the large-v3 tamper test above).
    model_bin.write_bytes(b"X" * len(b"floor-model-bytes"))

    with pytest.raises(station_runtime.NativeStationConfigurationError, match="SHA-256"):
        station_runtime.validate_floor_caption_model_root(version_root)


def test_validate_floor_caption_model_root_never_disturbs_the_large_v3_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The large-v3 mandatory gate must remain byte-identical (same path
    resolution, same WHISPER_MODEL_FILES source) whether or not the floor
    tier is also staged -- this is an ADDITIVE consumption capability, not a
    replacement."""

    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)

    env = station_runtime.load_native_station_environment(version_root)

    assert env["CIVICCAST_WHISPER_MODEL_PATH"] == str(
        version_root / "components" / "captions-large-v3" / "models" / "faster-whisper-large-v3"
    )


# ---------------------------------------------------------------------------
# Installer ground truth: fresh install vs. activation vs. caption tiers.
#
# Verified against the Rust sources (never against this module's own
# comments): a fresh native install lays ONLY `<INSTDIR>\runtime\` (the app
# payload, incl. `pythonservice.exe`) and `<INSTDIR>\packs\` (acquired
# ccpacks; server binaries under `packs\native-server-binaries\payload\`).
# `station-set.json` is written ONLY by the activation flow
# (`native_activation.rs` `stage_distribution_with`, manifest written at the
# end of staging) and `activation-self-test.json` ONLY by `main.rs`'s
# `write_native_activation_self_test_receipt` -- both into the `app\<version>`
# staging tree. Floor-tier caption bytes land at
# `packs\captions-floor\models\faster-whisper-medium\` (`acquisition_catalog.rs`
# `captions_medium` component via `caption_floor_tier_destination`); large-v3
# is OPTIONAL (`captions_large` is explicitly out of `PRODUCTION_CATALOG_IDS`).
# ---------------------------------------------------------------------------


def _write_fresh_install(install_root: Path) -> Path:
    """EXACTLY the fileset the installer's payload extraction produces --
    no station-set.json, no activation-self-test.json, no caption models."""

    runtime = install_root / "runtime"
    runtime.mkdir(parents=True)
    (runtime / "python.exe").write_bytes(b"python")
    (runtime / "pythonservice.exe").write_bytes(b"python-service-host")
    payload = install_root / "packs" / "native-server-binaries" / "payload"
    payload.mkdir(parents=True)
    (payload / "postgres.exe").write_bytes(b"server-binary")
    return install_root


def test_fresh_install_without_station_artifacts_raises_not_activated(
    tmp_path: Path,
) -> None:
    """HEADLINE: a fresh native install (runtime\\ + packs\\ ONLY -- the
    exact fileset the installer's payload extraction produces) is a
    LEGITIMATE installed-but-not-yet-activated state and must raise the
    typed :class:`NativeStationNotActivatedError`, which the supervisor
    catches to degrade gracefully instead of crashing the service.

    RED at HEAD 1ec943b0: ``load_native_station_environment`` hard-requires
    ``station-set.json`` and raises the generic configuration error (and the
    typed subclass does not even exist)."""

    from civiccast.native import station_runtime

    install_root = _write_fresh_install(tmp_path)

    with pytest.raises(station_runtime.NativeStationNotActivatedError):
        station_runtime.station_environment_for_python(
            install_root / "runtime" / "pythonservice.exe",
            program_data_root=tmp_path / "ProgramData" / "CivicCast",
        )


def test_not_activated_and_captions_unavailable_are_typed_subclasses() -> None:
    """The supervisor's contract (Worker A) catches EXACTLY the name
    ``NativeStationNotActivatedError``; the captions-unavailable state must
    be caught by that same handler while remaining separately typed."""

    from civiccast.native import station_runtime

    assert issubclass(
        station_runtime.NativeStationNotActivatedError,
        station_runtime.NativeStationConfigurationError,
    )
    assert issubclass(
        station_runtime.NativeStationCaptionsUnavailableError,
        station_runtime.NativeStationNotActivatedError,
    )


def test_fresh_install_with_floor_captions_but_no_station_set_is_still_not_activated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acquisition may stage caption bytes before activation writes the
    station-set: the activation gate comes first."""

    from civiccast.native import station_runtime

    install_root = _write_fresh_install(tmp_path)
    files = _write_floor_tier_files(install_root)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", _fake_floor_registry(files))

    with pytest.raises(station_runtime.NativeStationNotActivatedError):
        station_runtime.station_environment_for_python(
            install_root / "runtime" / "pythonservice.exe",
            program_data_root=tmp_path / "ProgramData" / "CivicCast",
        )


def test_a_present_but_corrupt_station_set_stays_a_loud_configuration_error(
    tmp_path: Path,
) -> None:
    """ABSENT station-set.json == not activated (graceful); PRESENT but
    unreadable/corrupt station-set.json == fail loud with the parent error,
    never the graceful not-activated state."""

    from civiccast.native import station_runtime

    install_root = _write_fresh_install(tmp_path)
    (install_root / "station-set.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(station_runtime.NativeStationConfigurationError) as excinfo:
        station_runtime.station_environment_for_python(
            install_root / "runtime" / "pythonservice.exe",
            program_data_root=tmp_path / "ProgramData" / "CivicCast",
        )
    assert not isinstance(excinfo.value, station_runtime.NativeStationNotActivatedError)


def test_activated_station_with_floor_tier_only_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE SHIPPING CONFIGURATION (settled owner architecture: captions ship
    with a MEDIUM floor tier; large-v3 is optional): an activated station
    whose only staged caption tier is the floor tier at
    ``packs/captions-floor/models/faster-whisper-medium`` must validate and
    start -- through EVERY gate, not just model-byte verification.

    REWRITTEN (was previously RED-then-green only through the byte-walk):
    before the Python-side gate closed, ``station-set.json``'s pack
    inventory still had to declare the OLD mandatory ``captions-large-v3``
    pack (``REQUIRED_COMPONENT_ROOTS`` required it) and the receipt's
    ``caption_inference`` still had to carry large-v3's hard-pinned identity
    (``EXPECTED_CAPTION_RECEIPT`` required it) for THIS test to reach the
    byte-walk at all -- i.e. a real floor-only station's actual manifest and
    receipt (no ``captions-large-v3`` pack, a MEDIUM-model receipt) would
    have been REJECTED before ever reaching the code this test exercised.
    Now the station-set has no ``captions-large-v3`` pack entry at all
    (``write_large_v3=False``), and the receipt is rewritten to the real
    tier-derived floor identity via ``_replace_receipt_caption_inference`` --
    proving the pack-inventory gate and the receipt gate both accept the
    real floor-only shape, not just the model-byte walk downstream of them."""

    from civiccast.native import station_runtime

    version_root, _ = _write_station(tmp_path, write_large_v3=False)
    files = _write_floor_tier_files(version_root)
    registry = _fake_floor_registry(files)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", registry)
    station = json.loads((version_root / "station-set.json").read_text(encoding="utf-8"))
    assert all(pack["component"] != "captions-large-v3" for pack in station["packs"])
    _replace_receipt_caption_inference(
        version_root,
        _receipt_caption_inference_for_tier(station_runtime.FLOOR_TIER_ID, registry),
    )

    env = station_runtime.load_native_station_environment(
        version_root,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
    )

    assert env["CIVICCAST_CAPTION_TIER"] == "floor"
    assert env["CIVICCAST_WHISPER_MODEL_PATH"] == str(
        version_root / "packs" / "captions-floor" / "models" / "faster-whisper-medium"
    )
    assert json.loads(env["CIVICCAST_CAPTION_MODEL_HASH_RECEIPT"]) == {
        name: {"bytes": size, "sha256": digest} for name, (size, digest) in files.items()
    }
    event = json.loads(env["CIVICCAST_CAPTION_TIER_EVENT"])
    assert event["event"] == "caption_tier_selected"
    assert event["tier"] == "floor"
    assert event["fallback"] is False


def test_floor_only_station_set_and_receipt_validate_directly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unit-level proof (not just the full env-loading integration path
    above) that a floor-only station's ``station-set.json`` and
    ``activation-self-test.json`` -- shaped exactly like a real two-pack
    activation writes them, no ``captions-large-v3`` pack, no large-v3
    caption identity anywhere -- pass ``_validate_station_set`` AND
    ``_validate_activation_receipt`` on their own, calling each gate
    directly rather than only observing that the composed function
    succeeds."""

    from civiccast.native import station_runtime

    version_root, _ = _write_station(tmp_path, write_large_v3=False)
    files = _write_floor_tier_files(version_root)
    registry = _fake_floor_registry(files)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", registry)

    station = json.loads((version_root / "station-set.json").read_text(encoding="utf-8"))
    version, index_sha256 = station_runtime._validate_station_set(station)
    assert version == "4.0.0-beta1"

    caption_inference = _receipt_caption_inference_for_tier(station_runtime.FLOOR_TIER_ID, registry)
    receipt = {
        "schema_version": 1,
        "product": "civiccast-native",
        "product_version": version,
        "distribution_index_sha256": index_sha256,
        "caption_inference": caption_inference,
    }
    # Must not raise: a floor-only receipt is checked against the floor
    # tier's own pinned identity, never large-v3's.
    station_runtime._validate_activation_receipt(
        receipt,
        version=version,
        index_sha256=index_sha256,
        tier_id=station_runtime.FLOOR_TIER_ID,
    )


def test_flat_layout_activation_files_validate_directly_at_install_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """K1 fix cross-language contract.

    ``native_activation.rs``'s new ``activate_flat_station_with`` writes
    ``station-set.json``/``activation-self-test.json`` directly at
    ``install_root`` (no ``app/<version>`` wrapping), reusing
    ``station_manifest_value`` -- the SAME composition
    ``write_station_manifest`` already used for the versioned layout, just
    relocated. `native_activation.rs`'s own Rust unit test
    (``flat_activation_writes_both_files_directly_at_install_root_and_they_parse``)
    asserts the written station-set.json is byte-for-byte
    ``station_manifest_value(&distribution)`` -- proving the RUST side emits
    exactly this schema.

    Invoking the Rust writer from pytest is impractical (it needs a full
    ``AcquiredDistribution`` plus a live self-test subprocess chain), so this
    test proves the PYTHON side of the same contract instead, per this
    slice's task instructions: a fixture built with this module's own
    ``_write_station(direct_layout=True, ...)`` helper -- the SAME
    schema-2 station-set / schema-1 receipt shape every other test in this
    module exercises for the versioned layout (mandatory
    ``core``/``captions-floor``/``summary-gemma4-12b``/``summary-gemma4-e4b``/
    ``translation-translategemma-4b`` pack entries, optional
    ``captions-large-v3``, the exact ``EXPECTED_RUNTIME_CONTRACT`` runtime
    block) -- written straight at ``install_root`` instead of
    ``install_root/app/<version>``, with the mandatory caption floor tier
    physically staged alongside it. If this shape ever diverges from what
    the Rust writer actually emits, `native_activation.rs`'s own tests (which
    assert full equality against ``station_manifest_value``) are the
    detector on that side; this test is the detector that the SHAPE, once
    landed on disk in the flat layout, is one `load_native_station_environment`
    actually accepts."""

    from civiccast.native import station_runtime

    install_root, _ = _write_station(tmp_path, direct_layout=True, write_large_v3=False)
    assert install_root == tmp_path
    assert not (tmp_path / "app").exists(), "the flat layout must never wrap in app/<version>"

    files = _write_floor_tier_files(install_root)
    registry = _fake_floor_registry(files)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", registry)
    station = json.loads((install_root / "station-set.json").read_text(encoding="utf-8"))
    assert station["schema_version"] == 2
    assert all(pack["component"] != "captions-large-v3" for pack in station["packs"])
    _replace_receipt_caption_inference(
        install_root,
        _receipt_caption_inference_for_tier(station_runtime.FLOOR_TIER_ID, registry),
    )
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)

    env = station_runtime.load_native_station_environment(
        install_root,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
    )

    assert env["CIVICCAST_NATIVE_STATION"] == "1"
    assert env["CIVICCAST_NATIVE_STATION_ROOT"] == str(install_root.resolve())
    assert env["CIVICCAST_CAPTION_TIER"] == "floor"
    assert env["CIVICCAST_WHISPER_MODEL_PATH"] == str(
        install_root / "packs" / "captions-floor" / "models" / "faster-whisper-medium"
    )


def test_rust_station_set_runtime_contract_matches_python_validator() -> None:
    """Keep the Rust station-set producer aligned with the Python validator."""
    from civiccast.native import station_runtime

    repo_root = Path(__file__).resolve().parents[2]
    rust_source = (
        repo_root
        / "civiccast"
        / "apps"
        / "installer"
        / "src-tauri"
        / "src"
        / "native_activation.rs"
    ).read_text(encoding="utf-8")
    producer = re.search(
        r"fn station_manifest_value\([^)]*\)\s*->\s*Value\s*\{.*?"
        r'"runtime"\s*:\s*\{(?P<runtime>[^{}]*)\}',
        rust_source,
        flags=re.DOTALL,
    )
    assert producer is not None, "Rust station-set runtime object was not found"
    rust_contract = {
        match.group("key"): json.loads(match.group("value"))
        for match in re.finditer(
            r'"(?P<key>[a-z_]+)"\s*:\s*'
            r'(?P<value>"(?:\\.|[^"\\])*"|true|false)\s*,',
            producer.group("runtime"),
        )
    }

    assert rust_contract == station_runtime.EXPECTED_RUNTIME_CONTRACT


def test_both_tiers_staged_still_passes_with_large_v3_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A station carrying BOTH tiers (the optional captions-large-v3 pack
    present alongside the mandatory captions-floor pack) still validates and
    starts, with the resolved tier -- and therefore the identity the receipt
    is checked against -- being large-v3 (the preferred quality tier), not a
    silent floor substitution."""

    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    floor_files = _write_floor_tier_files(version_root)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    registry = _fake_floor_registry(floor_files)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", registry)
    station = json.loads((version_root / "station-set.json").read_text(encoding="utf-8"))
    assert any(pack["component"] == "captions-large-v3" for pack in station["packs"])
    assert any(pack["component"] == "captions-floor" for pack in station["packs"])

    env = station_runtime.load_native_station_environment(
        version_root,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
    )

    assert env["CIVICCAST_CAPTION_TIER"] == "large-v3"
    receipt = json.loads((version_root / "activation-self-test.json").read_text(encoding="utf-8"))
    assert receipt["caption_inference"]["model"] == (
        "Systran/faster-whisper-large-v3@edaa852ec7e145841d8ffdb056a99866b5f0a478"
    )


def test_receipt_claiming_large_v3_on_a_floor_only_station_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A receipt that claims large-v3 ran, on a station where large-v3 is
    not staged anywhere (floor-only), must FAIL. The receipt is checked
    against the tier THIS STATION actually resolved and verified from disk
    (floor), never against whatever tier the receipt itself claims -- so a
    receipt cannot self-certify a tier that was never installed."""

    from civiccast.native import station_runtime

    version_root, _ = _write_station(tmp_path, write_large_v3=False)
    files = _write_floor_tier_files(version_root)
    registry = _fake_floor_registry(files)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", registry)
    # The receipt lies: it claims the REAL large-v3 identity even though no
    # large-v3 bytes are staged anywhere on this station.
    _replace_receipt_caption_inference(
        version_root,
        {
            "runtime": "faster-whisper 1.2.1",
            "ctranslate2": "4.8.1",
            "model": "Systran/faster-whisper-large-v3@edaa852ec7e145841d8ffdb056a99866b5f0a478",
            "model_path": "components/captions-large-v3/models/faster-whisper-large-v3",
            "model_bin_bytes": 3_087_284_237,
            "model_bin_sha256": (
                "69f74147e3334731bc3a76048724833325d2ec74642fb52620eda87352e3d4f1"
            ),
            "device": "cpu",
            "compute_type": "int8",
            "local_files_only": True,
            "result": "passed",
        },
    )

    with pytest.raises(
        station_runtime.NativeStationConfigurationError,
        match="activation self-test",
    ):
        station_runtime.load_native_station_environment(
            version_root,
            program_data_root=tmp_path / "ProgramData" / "CivicCast",
        )


def test_receipt_with_a_tampered_model_hash_still_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pinning must remain real: a receipt naming the CORRECT tier and
    the correct model identity, but with a tampered ``model_bin_sha256``,
    must still fail -- tier-awareness generalizes the mandatory gate, it
    never degrades it into 'accept any self-reported hash for this tier'."""

    from civiccast.native import station_runtime

    version_root, _ = _write_station(tmp_path, write_large_v3=False)
    files = _write_floor_tier_files(version_root)
    registry = _fake_floor_registry(files)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", registry)
    caption_inference = _receipt_caption_inference_for_tier(station_runtime.FLOOR_TIER_ID, registry)
    caption_inference["model_bin_sha256"] = "0" * 64
    _replace_receipt_caption_inference(version_root, caption_inference)

    with pytest.raises(
        station_runtime.NativeStationConfigurationError,
        match="activation self-test",
    ):
        station_runtime.load_native_station_environment(
            version_root,
            program_data_root=tmp_path / "ProgramData" / "CivicCast",
        )


def test_activated_station_with_large_v3_only_passes_and_reports_its_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A five-pack activated station whose only staged tier is the signed
    large-v3 component (the activation flow's own layout) keeps working."""

    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)

    env = station_runtime.load_native_station_environment(
        version_root,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
    )

    assert env["CIVICCAST_CAPTION_TIER"] == "large-v3"
    assert env["CIVICCAST_WHISPER_MODEL_PATH"] == str(
        version_root / "components" / "captions-large-v3" / "models" / "faster-whisper-large-v3"
    )


def test_activated_station_with_both_tiers_prefers_large_v3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With both tiers staged and valid, the quality tier is selected via
    ``select_caption_tier`` (never a silent floor substitution)."""

    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    floor_files = _write_floor_tier_files(version_root)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", _fake_floor_registry(floor_files))

    env = station_runtime.load_native_station_environment(
        version_root,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
    )

    assert env["CIVICCAST_CAPTION_TIER"] == "large-v3"
    assert env["CIVICCAST_WHISPER_MODEL_PATH"] == str(
        version_root / "components" / "captions-large-v3" / "models" / "faster-whisper-large-v3"
    )


def test_activated_station_with_no_caption_tier_raises_captions_unavailable(
    tmp_path: Path,
) -> None:
    """B1c semantics: a valid, activated station-set with NO caption tier
    staged at all is a typed, supervisor-catchable degraded state
    (:class:`NativeStationCaptionsUnavailableError`, a subclass of the
    not-activated error the supervisor already catches) -- never a service
    crash, and never a silent captionless start either (captions are
    mandatory product scope; the control plane must not launch without a
    verified caption model)."""

    from civiccast.native import station_runtime

    version_root, _ = _write_station(tmp_path, write_large_v3=False)

    with pytest.raises(station_runtime.NativeStationCaptionsUnavailableError):
        station_runtime.load_native_station_environment(
            version_root,
            program_data_root=tmp_path / "ProgramData" / "CivicCast",
        )


def test_activated_floor_only_station_with_corrupt_receipt_fails_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shipping (floor-only) configuration keeps the receipt gate: a
    receipt from another distribution fails loud, exactly as before."""

    from civiccast.native import station_runtime

    version_root, _ = _write_station(tmp_path, write_large_v3=False, receipt_index="cd" * 32)
    files = _write_floor_tier_files(version_root)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", _fake_floor_registry(files))

    with pytest.raises(
        station_runtime.NativeStationConfigurationError,
        match="activation self-test",
    ):
        station_runtime.load_native_station_environment(version_root)


def test_a_present_but_corrupt_large_v3_fails_loud_and_never_falls_back_to_floor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anti-silent-swap: a STAGED large-v3 tier with tampered bytes is a
    hard failure even when a valid floor tier is also staged -- corruption
    must never be laundered into a quiet tier downgrade."""

    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    floor_files = _write_floor_tier_files(version_root)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", _fake_floor_registry(floor_files))
    model_bin = (
        version_root
        / "components"
        / "captions-large-v3"
        / "models"
        / "faster-whisper-large-v3"
        / "model.bin"
    )
    model_bin.write_bytes(b"M" * len(b"model"))

    with pytest.raises(
        station_runtime.NativeStationConfigurationError,
        match=r"model\.bin.*SHA-256",
    ) as excinfo:
        station_runtime.load_native_station_environment(version_root)
    assert not isinstance(excinfo.value, station_runtime.NativeStationNotActivatedError)


def test_a_present_but_corrupt_floor_tier_fails_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The floor tier keeps every fail-closed property the large-v3 gate
    has: staged-but-tampered bytes are a hard failure, never treated as
    'absent' and never a captions-unavailable degrade."""

    from civiccast.native import station_runtime

    version_root, _ = _write_station(tmp_path, write_large_v3=False)
    files = _write_floor_tier_files(version_root)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", _fake_floor_registry(files))
    model_bin = (
        version_root / "packs" / "captions-floor" / "models" / "faster-whisper-medium" / "model.bin"
    )
    model_bin.write_bytes(b"X" * len(b"floor-model-bytes"))

    with pytest.raises(
        station_runtime.NativeStationConfigurationError,
        match="SHA-256",
    ) as excinfo:
        station_runtime.load_native_station_environment(version_root)
    assert not isinstance(excinfo.value, station_runtime.NativeStationNotActivatedError)


def test_a_model_root_that_is_not_a_directory_is_present_and_fails_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Presence detection must use the directory ENTRY (lstat semantics), so
    a mis-typed or mis-pointed model root is 'present but invalid' (loud
    failure through the existing fail-closed walk) -- never quietly treated
    as an absent tier."""

    from civiccast.native import station_runtime

    version_root, _ = _write_station(tmp_path, write_large_v3=False)
    files = _write_floor_tier_files(version_root)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", _fake_floor_registry(files))
    model_root = version_root / "packs" / "captions-floor" / "models" / "faster-whisper-medium"
    for child in model_root.iterdir():
        child.unlink()
    model_root.rmdir()
    model_root.write_bytes(b"not a directory")

    with pytest.raises(station_runtime.NativeStationConfigurationError) as excinfo:
        station_runtime.load_native_station_environment(version_root)
    assert not isinstance(excinfo.value, station_runtime.NativeStationCaptionsUnavailableError)


def test_station_runtime_exposes_the_tier_selection_seam() -> None:
    """WP1 adaptive-tier: the pure "which tier do I load" seam must be
    reachable from the runtime consumption point even though the real
    capacity-based selection policy that will call it is a later slice."""

    from civiccast.native import station_runtime

    result = station_runtime.select_caption_tier(
        available_tier_ids={"floor", "large-v3"},
        floor_tier_id="floor",
        decision=station_runtime.TierSelectionDecision(
            requested_tier_id="large-v3",
            allow_floor_fallback=True,
            reason="test",
        ),
    )

    assert result.tier_id == "large-v3"
    assert issubclass(station_runtime.TierSelectionError, RuntimeError)


# ---------------------------------------------------------------------------
# Front door: the control-plane child env must carry the packaged portal dist
# paths.
#
# BLOCKER this closes: civiccast/app.py's _mount_packaged_portals mounts
# /operator and / ONLY when CIVICCAST_OPERATOR_CONSOLE_DIST /
# CIVICCAST_PUBLIC_PORTAL_DIST are set. Nothing on a NATIVE station ever set
# them (only the WSL headless-bootstrap.ps1 did), so the control plane came up
# answering /health and 404ing the operator console and the resident portal --
# the two surfaces the whole product is reached through.
#
# The env used to also carry CIVICCAST_SETUP_NONCE, gating every
# /api/setup/* mutation. That nonce was retired 2026-08-29 (owner decision):
# the control plane binds 127.0.0.1 only, so first setup is unreachable from
# the network by construction and the nonce was a redundant, failure-prone
# gate. First setup is now admitted by loopback alone
# (civiccast.installer.router._require_local_setup_request).
# ---------------------------------------------------------------------------


def test_station_environment_points_the_control_plane_at_the_packaged_portals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chain L: the served paths come from the ``civiccast`` package's OWN
    location -- the source of truth the interpreter uses -- not from root
    arithmetic, and they must EXIST on the extract-shaped layout a real
    install produces (``native-app-payload``'s
    ``payload/Lib/site-packages/civiccast/...`` laid down at
    ``<root>\\runtime``)."""

    import civiccast
    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)

    package = version_root / "runtime" / "Lib" / "site-packages" / "civiccast"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    for portal in ("portal-operator", "portal-public"):
        dist = package / "apps" / portal / "dist"
        dist.mkdir(parents=True)
        (dist / "index.html").write_text("<h1>portal</h1>", encoding="utf-8")
    monkeypatch.setattr(civiccast, "__file__", str(package / "__init__.py"))

    env = station_runtime.load_native_station_environment(
        version_root,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
    )

    apps = package / "apps"
    assert env["CIVICCAST_OPERATOR_CONSOLE_DIST"] == str(apps / "portal-operator" / "dist")
    assert env["CIVICCAST_PUBLIC_PORTAL_DIST"] == str(apps / "portal-public" / "dist")
    assert Path(env["CIVICCAST_OPERATOR_CONSOLE_DIST"]).is_dir()
    assert Path(env["CIVICCAST_PUBLIC_PORTAL_DIST"]).is_dir()


def test_pre_activation_environment_carries_the_front_door(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chain L (TESTER2 request-0050c). A station that is installed but not yet
    activated must still get everything that is ALREADY true at that point:
    the packaged portals (delivered by the native-app-payload pack). Without
    them the operator console 404s, so first-run setup could not even be
    reached."""

    from civiccast.native import station_runtime

    env = station_runtime.pre_activation_control_plane_environment()

    assert env["CIVICCAST_OPERATOR_CONSOLE_DIST"]
    assert env["CIVICCAST_PUBLIC_PORTAL_DIST"]
    assert "CIVICCAST_SETUP_NONCE" not in env


def test_pre_activation_environment_never_claims_the_station_is_activated() -> None:
    """``installer/service.py``'s ``_native_station_activated`` reads
    ``CIVICCAST_NATIVE_STATION`` + ``CIVICCAST_NATIVE_STATION_MANIFEST`` to
    decide whether setup has finished. Serving the front door a not-yet-
    activated station needs must never turn that lane green."""

    from civiccast.native import station_runtime

    env = station_runtime.pre_activation_control_plane_environment()

    assert "CIVICCAST_NATIVE_STATION" not in env
    assert "CIVICCAST_NATIVE_STATION_MANIFEST" not in env


def test_station_environment_never_carries_a_setup_nonce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The retired installer-handoff nonce must never resurface on the child
    env -- first setup is admitted by loopback alone now
    (``civiccast.installer.router._require_local_setup_request``)."""

    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    # Device selection is hardware-adaptive (owner ruling 2026-08-15); pin the
    # probe so these env assertions are deterministic on any test machine.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)

    env = station_runtime.load_native_station_environment(
        version_root,
        program_data_root=tmp_path / "ProgramData" / "CivicCast",
    )

    assert "CIVICCAST_SETUP_NONCE" not in env


# ---------------------------------------------------------------------------
# Chain H1: acquisition and consumption must agree on where a downloaded
# component lives.
#
# The installed GUI is non-elevated and can no longer write
# `<install_root>\packs\...`; first-run downloads land under the per-machine
# writable acquisition root (`<PROGRAMDATA>\CivicCast\packs\...`) instead.
# The station runtime must therefore find a floor-tier caption model in
# EITHER place -- the elevated installer's staged copy under the install root,
# or the acquired copy under ProgramData -- with the install root preferred.
# ---------------------------------------------------------------------------


def _write_floor_tier_files_under(root: Path) -> dict[str, tuple[int, str]]:
    model_root = root / "packs" / "captions-floor" / "models" / "faster-whisper-medium"
    model_root.mkdir(parents=True)
    files: dict[str, tuple[int, str]] = {}
    for name, body in {
        "config.json": b"floor-config",
        "model.bin": b"floor-model-bytes",
        "tokenizer.json": b"floor-tokenizer",
        "vocabulary.txt": b"floor-vocab",
    }.items():
        (model_root / name).write_bytes(body)
        files[name] = (len(body), hashlib.sha256(body).hexdigest())
    return files


def test_caption_tier_search_roots_prefer_the_install_root_then_the_acquisition_root() -> None:
    from civiccast.native import station_runtime

    roots = station_runtime.caption_tier_search_roots(
        Path(r"C:\Program Files\CivicCast (Native)"),
        acquisition_root=Path(r"C:\ProgramData\CivicCast"),
    )
    assert roots == (
        Path(r"C:\Program Files\CivicCast (Native)"),
        Path(r"C:\ProgramData\CivicCast"),
    )


def test_a_floor_tier_downloaded_to_the_writable_root_is_found_and_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chain H1 RED: the acquisition flow now writes here, so the station
    runtime has to read here. Before this, `validate_floor_caption_model_root`
    only ever looked under the version root, so a component the first-run GUI
    actually managed to download was invisible to the runtime that needs it."""

    from civiccast.native import station_runtime

    version_root = tmp_path / "app" / "4.0.0-beta1"
    version_root.mkdir(parents=True)
    acquisition_root = tmp_path / "ProgramData" / "CivicCast"
    files = _write_floor_tier_files_under(acquisition_root)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", _fake_floor_registry(files))

    resolved_root, hash_receipt = station_runtime.validate_floor_caption_model_root(
        version_root, acquisition_root=acquisition_root
    )

    expected = acquisition_root / "packs" / "captions-floor" / "models" / "faster-whisper-medium"
    assert resolved_root == expected.resolve()
    assert hash_receipt == {
        name: {"bytes": size, "sha256": digest} for name, (size, digest) in files.items()
    }


def test_an_installer_staged_floor_tier_still_wins_over_the_acquisition_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reconciling the two sources: what the ELEVATED installer staged under
    the install root is preferred, so a non-elevated user cannot shadow it
    from the writable root."""

    from civiccast.native import station_runtime

    version_root = tmp_path / "app" / "4.0.0-beta1"
    version_root.mkdir(parents=True)
    acquisition_root = tmp_path / "ProgramData" / "CivicCast"
    files = _write_floor_tier_files_under(version_root)
    _write_floor_tier_files_under(acquisition_root)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", _fake_floor_registry(files))

    resolved_root, _receipt = station_runtime.validate_floor_caption_model_root(
        version_root, acquisition_root=acquisition_root
    )

    expected = version_root / "packs" / "captions-floor" / "models" / "faster-whisper-medium"
    assert resolved_root == expected.resolve()


def test_the_acquisition_root_is_never_searched_unless_it_is_supplied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No implicit machine-wide probe: a caller that names only a version
    root gets exactly that root searched, so tests and offline tooling cannot
    silently pick up this machine's real ProgramData tree."""

    from civiccast.native import station_runtime

    version_root = tmp_path / "app" / "4.0.0-beta1"
    version_root.mkdir(parents=True)
    acquisition_root = tmp_path / "ProgramData" / "CivicCast"
    files = _write_floor_tier_files_under(acquisition_root)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", _fake_floor_registry(files))

    with pytest.raises(station_runtime.NativeStationConfigurationError):
        station_runtime.validate_floor_caption_model_root(version_root)


# ---------------------------------------------------------------------------
# Field evidence regression (candidate 4eca729, 2026-08-29): a station
# activated against the mandatory floor tier alone, whose operator later
# acquires the OPTIONAL large-v3 tier through the non-elevated post-install
# GUI (chain H1's acquisition root, never the install root), must not
# crash-loop on its next start. `_resolve_caption_tier` already prefers the
# highest staged tier across both roots; the receipt lookup must follow that
# SAME resolved tier to whichever root it actually came from, so a tier this
# station can prove (via `main.rs`'s addendum receipt) validates, while a
# tier it CANNOT prove still fails exactly as loudly as before.
# ---------------------------------------------------------------------------


def _stage_large_v3_under(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, tuple[int, str]]:
    """Stages the large-v3 caption model at the acquisition-flow's own
    `components/captions-large-v3/models/faster-whisper-large-v3` layout
    (`component_acquisition.rs::caption_large_tier_destination`) with small
    fixture bytes -- mirrors `_write_station`'s own `write_large_v3=True`
    branch: `_validate_model_root` verifies against these FAKE bytes (via the
    monkeypatched `WHISPER_MODEL_FILES`), while `_validate_activation_receipt`
    separately checks the receipt against the REAL, never-monkeypatched
    `CAPTION_TIER_REGISTRY[LARGE_V3_TIER_ID]` identity -- the two checks are
    intentionally independent, exactly as the existing large-v3 tests already
    rely on."""

    model_root = root / "components" / "captions-large-v3" / "models" / "faster-whisper-large-v3"
    model_root.mkdir(parents=True)
    files: dict[str, tuple[int, str]] = {}
    for name, body in {
        "README.md": b"readme",
        "config.json": b"config",
        "model.bin": b"model",
        "preprocessor_config.json": b"preprocessor",
        "tokenizer.json": b"tokenizer",
        "vocabulary.json": b"vocabulary",
    }.items():
        (model_root / name).write_bytes(body)
        files[name] = (len(body), hashlib.sha256(body).hexdigest())

    from civiccast.native import station_runtime

    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    return files


def test_orphaned_large_v3_without_a_receipt_degrades_to_the_proven_floor_tier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The 2026-08-30 field failure (DESKTOP-2BR3SJR, UPGRADE-18-REPORT.md):
    uninstall-old -> reinstall-new preserves `components/captions-large-v3`
    under ProgramData, so tier resolution picks large-v3 -- but nothing on
    the new install ever wrote an addendum receipt there. The station must
    NOT crash-loop: it degrades to the floor tier whose receipt the fresh
    install DID write at the install root, comes up with floor captions, and
    logs a WARNING naming the orphaned tier so the operator re-acquires it
    from the console. (This test fails on pre-fix main, which raised
    `activation self-test receipt is missing or unreadable` here.)"""

    from civiccast.native import station_runtime
    from civiccast.native.caption_tiers import FLOOR_TIER_ID, LARGE_V3_TIER_ID

    version_root, _ = _write_station(tmp_path, write_large_v3=False)
    floor_files = _write_floor_tier_files(version_root)
    fake_registry = _fake_floor_registry(floor_files)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", fake_registry)
    _replace_receipt_caption_inference(
        version_root, _receipt_caption_inference_for_tier(FLOOR_TIER_ID, fake_registry)
    )
    acquisition_root = tmp_path / "ProgramData" / "CivicCast"
    _stage_large_v3_under(acquisition_root, monkeypatch)
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)

    with caplog.at_level(logging.WARNING, logger=station_runtime.__name__):
        env = station_runtime.load_native_station_environment(
            version_root, program_data_root=acquisition_root
        )

    assert env["CIVICCAST_CAPTION_TIER"] == FLOOR_TIER_ID
    tier_event = json.loads(env["CIVICCAST_CAPTION_TIER_EVENT"])
    assert tier_event["tier"] == FLOOR_TIER_ID
    assert tier_event["requested"] == LARGE_V3_TIER_ID
    assert tier_event["fallback"] is True
    warnings = [
        record.getMessage() for record in caplog.records if record.levelno == logging.WARNING
    ]
    orphan_warnings = [message for message in warnings if LARGE_V3_TIER_ID in message]
    assert orphan_warnings, f"expected a WARNING naming the orphaned tier, got: {warnings}"
    assert str(acquisition_root / "components" / "captions-large-v3") in orphan_warnings[0]
    assert "operator console" in orphan_warnings[0]


def test_an_unproven_tier_with_no_floor_to_fall_back_to_still_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fail-closed floor the degrade path must never lose: a station
    whose ONLY staged tier is large-v3 (five-pack layout) with no readable
    receipt has no proven tier to fall back to, and must keep raising exactly
    as loudly as before."""

    from civiccast.native import station_runtime

    version_root, files = _write_station(tmp_path)
    monkeypatch.setattr(station_runtime, "WHISPER_MODEL_FILES", files)
    (version_root / "activation-self-test.json").unlink()
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)

    with pytest.raises(
        station_runtime.NativeStationConfigurationError,
        match="activation self-test receipt is missing or unreadable",
    ):
        station_runtime.load_native_station_environment(version_root)


def test_large_v3_acquired_after_floor_activation_with_a_valid_addendum_receipt_starts_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression proof: exactly the field-evidence sequence (activate
    on the floor tier, THEN acquire large-v3 into the acquisition root) must
    now start cleanly once `main.rs`'s addendum receipt
    (`finalize_captions_large_acquisition`) is present there -- and doing so
    must leave the PRIMARY receipt and every other already-activated
    component at the install root completely untouched."""

    from civiccast.native import station_runtime
    from civiccast.native.caption_tiers import FLOOR_TIER_ID, LARGE_V3_TIER_ID

    version_root, _ = _write_station(tmp_path, write_large_v3=False)
    floor_files = _write_floor_tier_files(version_root)
    fake_registry = _fake_floor_registry(floor_files)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", fake_registry)
    _replace_receipt_caption_inference(
        version_root, _receipt_caption_inference_for_tier(FLOOR_TIER_ID, fake_registry)
    )
    primary_receipt_before = (version_root / "activation-self-test.json").read_text(
        encoding="utf-8"
    )
    other_component = version_root / "components" / "summary-gemma4-12b"
    other_component.mkdir(parents=True)
    (other_component / "marker.bin").write_bytes(b"already-activated")

    acquisition_root = tmp_path / "ProgramData" / "CivicCast"
    _stage_large_v3_under(acquisition_root, monkeypatch)
    station = json.loads((version_root / "station-set.json").read_text(encoding="utf-8"))
    addendum_receipt = {
        "schema_version": 1,
        "product": "civiccast-native",
        "product_version": station["product_version"],
        "distribution_index_sha256": station["distribution_index_sha256"],
        "caption_inference": _receipt_caption_inference_for_tier(LARGE_V3_TIER_ID, fake_registry),
    }
    (acquisition_root / "activation-self-test.json").write_text(
        json.dumps(addendum_receipt), encoding="utf-8"
    )
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)

    env = station_runtime.load_native_station_environment(
        version_root, program_data_root=acquisition_root
    )

    assert env["CIVICCAST_CAPTION_TIER"] == LARGE_V3_TIER_ID
    # Additive, not destructive: the primary receipt and every previously
    # activated component are byte-for-byte untouched by acquiring the
    # optional tier.
    assert (version_root / "activation-self-test.json").read_text(
        encoding="utf-8"
    ) == primary_receipt_before
    assert (other_component / "marker.bin").read_bytes() == b"already-activated"


# ---------------------------------------------------------------------------
# resolve_whisper_device (owner ruling 2026-08-15: hardware-adaptive captions;
# option B same day: capability = VERIFIED PRESENCE of the CUDA runtime DLLs,
# not VRAM alone -- see resolve_whisper_device's docstring for why VRAM-only
# guaranteed the runtime's own cuda-load fallback fired on ~95%+ of machines)
# ---------------------------------------------------------------------------


def _stage_cuda_libs(install_root: Path) -> Path:
    """Stage both required CUDA runtime DLLs at
    ``cuda_bin_dir(install_root)`` and return that directory."""

    from civiccast.native import station_runtime

    bin_dir = station_runtime.cuda_bin_dir(install_root)
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name in ("cublas64_12.dll", "cudnn64_9.dll"):
        (bin_dir / name).write_bytes(b"")
    return bin_dir


def test_resolve_whisper_device_selects_cuda_on_a_capable_nvidia_gpu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """>= 8 GB NVIDIA VRAM (the hardware_inventory.rs ladder's own line) AND
    both CUDA runtime DLLs staged selects GPU inference -- the owner's
    2026-08-15 ruling plus the option-B presence gate: a station whose
    hardware can run the caption engine on GPU, and actually HAS the runtime
    to do it, gets GPU."""

    from civiccast.native import station_runtime

    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: 16.0)
    _stage_cuda_libs(tmp_path)

    assert station_runtime.resolve_whisper_device(tmp_path) == ("cuda", "float16")


def test_resolve_whisper_device_capable_gpu_without_libs_falls_closed_to_cpu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The new default reality (owner review, option B): ~95%+ of NVIDIA
    machines have the VRAM but not the component pack's cuBLAS/cuDNN DLLs
    yet. A capable GPU with no libs staged must resolve to cpu -- selecting
    cuda here guaranteed the runtime's own load-failure fallback fired on
    nearly every capable machine, a silent degradation this gate closes."""

    from civiccast.native import station_runtime

    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: 16.0)

    # No install root at all -- the no-arg call the override/no-gpu paths
    # still support.
    assert station_runtime.resolve_whisper_device() == ("cpu", "int8")

    # An install root with capable VRAM but no staged DLLs directory.
    assert station_runtime.resolve_whisper_device(tmp_path) == ("cpu", "int8")


def test_resolve_whisper_device_weak_gpu_with_libs_present_stays_cpu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Staged libs alone are not capability: sub-threshold VRAM (the ladder's
    < 8 GB line) still fails closed to cpu even when the CUDA component pack
    is fully installed."""

    from civiccast.native import station_runtime

    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: 6.0)
    _stage_cuda_libs(tmp_path)

    assert station_runtime.resolve_whisper_device(tmp_path) == ("cpu", "int8")


def test_resolve_whisper_device_libs_staged_only_in_acquisition_root_selects_cuda(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Chain H1: the non-elevated first-run GUI cannot write under
    ``install_root`` at all, so a GUI-downloaded CUDA runtime component lands
    under the acquisition root instead -- exactly the reason
    ``caption_tier_search_roots`` takes an ``acquisition_root``. The presence
    gate must find libs there too, or a GUI-acquired component would be
    invisible to it."""

    from civiccast.native import station_runtime

    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: 16.0)

    install_root = tmp_path / "install-root"
    acquisition_root = tmp_path / "acquisition-root"
    install_root.mkdir()
    _stage_cuda_libs(acquisition_root)

    assert station_runtime.resolve_whisper_device(
        install_root, acquisition_root=acquisition_root
    ) == ("cuda", "float16")


def test_resolve_whisper_device_libs_in_neither_root_stays_cpu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from civiccast.native import station_runtime

    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: 16.0)

    install_root = tmp_path / "install-root"
    acquisition_root = tmp_path / "acquisition-root"
    install_root.mkdir()
    acquisition_root.mkdir()

    assert station_runtime.resolve_whisper_device(
        install_root, acquisition_root=acquisition_root
    ) == ("cpu", "int8")


def test_resolve_cuda_bin_dir_prefers_version_root_when_both_present(tmp_path: Path) -> None:
    """Elevated-staged ``version_root`` shadows the non-elevated GUI's
    ``acquisition_root`` when both carry the DLLs -- the same precedence
    rationale ``caption_tier_search_roots`` documents (chain H1)."""

    from civiccast.native import station_runtime

    version_root = tmp_path / "version-root"
    acquisition_root = tmp_path / "acquisition-root"
    version_bin = _stage_cuda_libs(version_root)
    _stage_cuda_libs(acquisition_root)

    resolved = station_runtime.resolve_cuda_bin_dir(version_root, acquisition_root=acquisition_root)

    assert resolved == version_bin


def test_resolve_whisper_device_fails_closed_to_cpu_without_a_capable_gpu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from civiccast.native import station_runtime

    monkeypatch.delenv("CIVICCAST_WHISPER_DEVICE", raising=False)
    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)

    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    assert station_runtime.resolve_whisper_device() == ("cpu", "int8")

    # Sub-threshold VRAM is also CPU: the ladder's < 8 GB line.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: 6.0)
    assert station_runtime.resolve_whisper_device() == ("cpu", "int8")


def test_resolve_whisper_device_operator_override_wins_in_both_directions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The machine-level env override is the operator's escape hatch -- it
    must beat the probe whichever way it points (e.g. forcing cpu on a
    pre-tensor-core card the VRAM proxy would wrongly promote), and even
    without the component pack's DLLs staged: the operator may have a
    system-wide CUDA toolkit install this module cannot see."""

    from civiccast.native import station_runtime

    monkeypatch.delenv("CIVICCAST_WHISPER_COMPUTE_TYPE", raising=False)

    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: 24.0)
    monkeypatch.setenv("CIVICCAST_WHISPER_DEVICE", "cpu")
    assert station_runtime.resolve_whisper_device() == ("cpu", "int8")

    # No install root, no staged libs -- the override still wins toward cuda.
    monkeypatch.setattr(station_runtime, "_probe_nvidia_vram_gb", lambda: None)
    monkeypatch.setenv("CIVICCAST_WHISPER_DEVICE", "cuda")
    assert station_runtime.resolve_whisper_device() == ("cuda", "float16")

    monkeypatch.setenv("CIVICCAST_WHISPER_COMPUTE_TYPE", "int8_float16")
    assert station_runtime.resolve_whisper_device() == ("cuda", "int8_float16")


def test_configured_caption_tier_resolves_its_verified_model_independent_of_activation_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from civiccast.native import station_runtime

    version_root, _large_files = _write_station(tmp_path, write_large_v3=False)
    floor_files = _write_floor_tier_files(version_root)
    registry = _fake_floor_registry(floor_files)
    monkeypatch.setattr(station_runtime, "CAPTION_TIER_REGISTRY", registry)

    model_root, receipt = station_runtime.resolve_caption_model_for_tier(
        version_root,
        station_runtime.FLOOR_TIER_ID,
        acquisition_root=tmp_path / "ProgramData" / "CivicCast",
    )

    assert model_root == (
        version_root / "packs" / "captions-floor" / "models" / "faster-whisper-medium"
    )
    assert set(receipt) == set(floor_files)
    with pytest.raises(station_runtime.NativeStationCaptionsUnavailableError):
        station_runtime.resolve_caption_model_for_tier(
            version_root, station_runtime.LARGE_V3_TIER_ID
        )
