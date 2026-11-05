# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Tests for the app-payload builder + verifier I/O shells.

Both scripts live outside the `civiccast` package, so they are loaded by file
path (the house pattern, matching `test_closure_builder.py`). The pure helpers
(RECORD index, manifest/BOM rendering) are exercised on tiny fixtures; the
verifier is proven with a real round-trip AND a committed negative control
(tamper => FAIL) so the trust check cannot silently stop failing.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest


def _load(mod_name: str, filename: str) -> object:
    spec = importlib.util.spec_from_file_location(
        mod_name, Path(__file__).resolve().parents[2] / "scripts" / filename
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


builder = _load("scripts.build_native_app_payload", "build_native_app_payload.py")
verifier = _load("verify_native_app_payload", "verify_native_app_payload.py")

_APP_SHELL_TARGETS = (
    "android-mobile",
    "android-tv",
    "fire-tv",
    "ios-ipados",
    "roku",
    "tvos",
    "web-pwa",
)


# ---------------------------------------------------------------------------
# Retained dependency wheel ownership
# ---------------------------------------------------------------------------


def test_wheel_header_member_maps_to_uv_target_install_path() -> None:
    assert (
        verifier._wheel_member_install_path(
            "greenlet-3.5.4.data/headers/greenlet.h",
            distribution="greenlet",
        )
        == "include/greenlet/greenlet.h"
    )


def _write_required_runtime_app_files(archive: zipfile.ZipFile) -> None:
    archive.writestr("civiccast/apps/portal-operator/dist/index.html", "operator")
    archive.writestr("civiccast/apps/portal-public/dist/index.html", "public")
    shell_root = "civiccast/apps/app-platform-shells"
    archive.writestr(f"{shell_root}/scripts/build-targets.mjs", "script")
    archive.writestr(f"{shell_root}/src/shell.mjs", "source")
    archive.writestr(f"{shell_root}/src/shell.css", "style")
    archive.writestr(f"{shell_root}/fixtures/station-app-config.sample.json", "{}")
    for target in _APP_SHELL_TARGETS:
        archive.writestr(f"{shell_root}/targets/{target}/index.html", target)
        archive.writestr(f"{shell_root}/targets/{target}/manifest.json", "{}")


# ---------------------------------------------------------------------------
# Build toolchain identity
# ---------------------------------------------------------------------------


def test_build_toolchain_lock_rejects_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = tmp_path / "native-windows-build-toolchain.lock.json"
    lock.write_bytes(b'{"reviewed":true}\n')
    expected = hashlib.sha256(lock.read_bytes()).hexdigest()
    monkeypatch.setattr(builder, "APP_BUILD_TOOLCHAIN_LOCK_FILE", lock)
    monkeypatch.setattr(builder, "APP_BUILD_TOOLCHAIN_LOCK_SHA256", expected)

    assert builder.verify_app_build_toolchain_lock() == expected

    lock.write_bytes(b'{"reviewed":false}\n')
    with pytest.raises(SystemExit, match="toolchain lock SHA-256"):
        builder.verify_app_build_toolchain_lock()


def test_build_toolchain_rejects_tampered_delegated_tool_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node_root = tmp_path / "node"
    npm_root = node_root / "node_modules" / "npm"
    npm_root.mkdir(parents=True)
    python_root = tmp_path / "python"
    (python_root / "Lib").mkdir(parents=True)

    executables = {
        "node": node_root / "node.exe",
        "npm": node_root / "npm.cmd",
        "uv": tmp_path / "uv.exe",
        "python": python_root / "python.exe",
        "python312.dll": python_root / "python312.dll",
    }
    for name, path in executables.items():
        path.write_bytes(f"{name}-bytes".encode())
    (npm_root / "bin.js").write_text("tampered npm implementation", encoding="utf-8")
    (python_root / "Lib" / "pathlib.py").write_text(
        "tampered python stdlib",
        encoding="utf-8",
    )

    policy = {
        name: {
            "version": {
                "node": "v24.16.0",
                "npm": "11.12.1",
                "uv": "uv 0.11.15",
                "python": "Python 3.12.13",
                "python312.dll": "3.12.13",
            }[name],
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for name, path in executables.items()
    }
    policy["npm"]["tree_sha256"] = "0" * 64
    policy["python"]["tree_sha256"] = "0" * 64
    monkeypatch.setattr(builder, "APP_BUILD_TOOLCHAIN", policy)
    monkeypatch.setattr(
        builder,
        "which",
        lambda command: {
            "node.exe": str(executables["node"]),
            "npm.cmd": str(executables["npm"]),
            "uv.exe": str(executables["uv"]),
        }.get(command),
    )
    monkeypatch.setattr(builder.sys, "_base_executable", str(executables["python"]))
    monkeypatch.setattr(builder.sys, "base_prefix", str(python_root))

    def fake_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        path = Path(command[0])
        version = next(
            identity["version"] for name, identity in policy.items() if path == executables[name]
        )
        return subprocess.CompletedProcess(command, 0, stdout=version, stderr="")

    monkeypatch.setattr(builder.subprocess, "run", fake_run)

    with pytest.raises(SystemExit, match="tree SHA-256"):
        builder.verify_app_build_toolchain()


# ---------------------------------------------------------------------------
# CivicCast wheel build backend
# ---------------------------------------------------------------------------


def test_reviewed_pyav_build_uses_verified_python_toolchain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder_script = tmp_path / "build_native_pyav_wheel.py"
    builder_script.write_text("# test fixture\n", encoding="utf-8")
    monkeypatch.setattr(builder, "PYAV_BUILDER", builder_script)
    commands: list[list[str]] = []

    def fake_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        commands.append([str(part) for part in command])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(builder.subprocess, "run", fake_run)

    builder.build_reviewed_pyav_wheel(
        tmp_path / "scratch",
        python_executable="pinned-python.exe",
        uv_executable="pinned-uv.exe",
    )

    assert commands[0][0] == "pinned-python.exe"
    assert "--advisory-wheel-hash" not in commands[0]


def test_build_reviewed_pyav_wheel_forwards_advisory_hash_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The self-hosted build lane passes advisory_wheel_hash=True down to
    the PyAV subprocess as --advisory-wheel-hash; the hosted default (tested
    above) must NOT pass it."""
    builder_script = tmp_path / "build_native_pyav_wheel.py"
    builder_script.write_text("# test fixture\n", encoding="utf-8")
    monkeypatch.setattr(builder, "PYAV_BUILDER", builder_script)
    commands: list[list[str]] = []

    def fake_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        commands.append([str(part) for part in command])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(builder.subprocess, "run", fake_run)

    builder.build_reviewed_pyav_wheel(
        tmp_path / "scratch",
        python_executable="pinned-python.exe",
        uv_executable="pinned-uv.exe",
        advisory_wheel_hash=True,
    )

    assert "--advisory-wheel-hash" in commands[0]


def test_exact_reviewed_pyav_wheel_can_be_reused_without_rebuilding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel = tmp_path / "av-18.0.0-cp311-abi3-win_amd64.whl"
    wheel.write_bytes(b"reviewed-wheel")
    monkeypatch.setattr(builder, "REVIEWED_PYAV_WHEEL_BYTES", wheel.stat().st_size)
    monkeypatch.setattr(
        builder,
        "REVIEWED_PYAV_WHEEL_SHA256",
        hashlib.sha256(wheel.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        builder,
        "build_reviewed_pyav_wheel",
        lambda *_args, **_kwargs: pytest.fail("exact reviewed wheel was rebuilt"),
    )

    wheelhouse = builder.prepare_reviewed_pyav_wheel(
        tmp_path / "scratch",
        reviewed_wheel=wheel,
        python_executable="pinned-python.exe",
        uv_executable="pinned-uv.exe",
    )

    retained = wheelhouse / wheel.name
    assert retained.read_bytes() == b"reviewed-wheel"
    assert retained.resolve() != wheel.resolve()


def test_reviewed_pyav_wheel_reuse_rejects_hash_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel = tmp_path / "av-18.0.0-cp311-abi3-win_amd64.whl"
    wheel.write_bytes(b"tampered")
    monkeypatch.setattr(builder, "REVIEWED_PYAV_WHEEL_BYTES", wheel.stat().st_size)
    monkeypatch.setattr(builder, "REVIEWED_PYAV_WHEEL_SHA256", "0" * 64)

    with pytest.raises(SystemExit, match="SHA-256"):
        builder.prepare_reviewed_pyav_wheel(
            tmp_path / "scratch",
            reviewed_wheel=wheel,
            python_executable="pinned-python.exe",
            uv_executable="pinned-uv.exe",
        )


def test_caption_model_staging_is_exactly_hash_pinned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "config.json").write_bytes(b"cfg")
    (cache / "model.bin").write_bytes(b"weights")
    policy = {
        "config.json": (3, hashlib.sha256(b"cfg").hexdigest()),
        "model.bin": (7, hashlib.sha256(b"weights").hexdigest()),
    }
    monkeypatch.setattr(builder, "WHISPER_MODEL_FILES", policy)

    index = builder.place_whisper_model(tmp_path / "payload", cache=cache)

    assert index == {
        "MODELS/faster-whisper-large-v3/config.json": (
            "faster-whisper-large-v3-model",
            builder.WHISPER_MODEL_REVISION,
            "MIT",
        ),
        "MODELS/faster-whisper-large-v3/model.bin": (
            "faster-whisper-large-v3-model",
            builder.WHISPER_MODEL_REVISION,
            "MIT",
        ),
    }
    assert (
        tmp_path / "payload" / "MODELS" / "faster-whisper-large-v3" / "model.bin"
    ).read_bytes() == b"weights"


def test_caption_model_staging_rejects_hash_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "model.bin").write_bytes(b"tampered")
    monkeypatch.setattr(
        builder,
        "WHISPER_MODEL_FILES",
        {"model.bin": (7, hashlib.sha256(b"weights").hexdigest())},
    )
    monkeypatch.setattr(
        builder,
        "_download_whisper_model_file",
        lambda _name, _destination: None,
    )

    with pytest.raises(SystemExit, match=r"model\.bin"):
        builder.place_whisper_model(tmp_path / "payload", cache=cache)


def test_whistle_engine_staging_requires_the_pinned_python_distribution(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit, match=re.escape("cactus-needle 3.1.0 is not installed")):
        builder.place_whistle_engine(tmp_path / "site-packages", cache=tmp_path / "cache")


def test_civiccast_wheel_excludes_local_coverage_artifacts() -> None:
    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    excluded = config["tool"]["hatch"]["build"]["targets"]["wheel"]["exclude"]

    assert "**/*.cover" in excluded
    assert "**/*.py,cover" in excluded


def test_civiccast_wheel_declares_runtime_app_shell_inputs() -> None:
    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    artifacts = config["tool"]["hatch"]["build"]["targets"]["wheel"]["artifacts"]

    assert "/civiccast/apps/app-platform-shells/scripts/build-targets.mjs" in artifacts
    assert "/civiccast/apps/app-platform-shells/src/**" in artifacts
    assert "/civiccast/apps/app-platform-shells/fixtures/**" in artifacts
    assert "/civiccast/apps/app-platform-shells/targets/**" in artifacts


def test_civiccast_wheel_uses_hash_locked_isolated_hatchling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_lock = tmp_path / "requirements-native-app-build.txt"
    build_lock.write_text("hatchling==1.27.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    monkeypatch.setattr(builder, "APP_BUILD_REQUIREMENTS_FILE", build_lock)
    monkeypatch.setattr(
        builder,
        "APP_BUILD_REQUIREMENTS_SHA256",
        hashlib.sha256(build_lock.read_bytes()).hexdigest(),
    )
    source_snapshot = tmp_path / "source"
    source_snapshot.mkdir()
    monkeypatch.setattr(
        builder,
        "_prepare_civiccast_source_snapshot",
        lambda _scratch, **_kwargs: source_snapshot,
    )
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append([str(part) for part in command])
        if "hatchling" in command:
            destination = Path(command[command.index("-d") + 1])
            with zipfile.ZipFile(destination / "civiccast-1.2.3-py3-none-any.whl", "w") as archive:
                _write_required_runtime_app_files(archive)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(builder.subprocess, "run", fake_run)

    retained_wheel = tmp_path / "retained" / "civiccast.whl"
    version, wheel_hash = builder.build_and_install_civiccast_wheel(
        tmp_path / "site-packages",
        tmp_path / "scratch",
        toolchain=builder.VerifiedBuildToolchain(
            node="node",
            npm="npm",
            python="python",
            uv="uv",
        ),
        retained_wheel=retained_wheel,
    )

    assert version == "1.2.3"
    assert len(wheel_hash) == 64
    assert commands[0][:2] == ["uv", "venv"]
    assert "--require-hashes" in commands[1]
    assert "--no-deps" in commands[1]
    assert commands[2][1:4] == ["-m", "hatchling", "build"]
    assert commands[3][0] == "uv"
    assert all(command[1] != "build" for command in commands if command[0] == "uv")
    assert retained_wheel.is_file()
    assert hashlib.sha256(retained_wheel.read_bytes()).hexdigest() == wheel_hash


def test_wheel_layout_requires_built_portals_and_rejects_app_sources(tmp_path: Path) -> None:
    wheel = tmp_path / "civiccast.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        _write_required_runtime_app_files(archive)
        archive.writestr("civiccast/apps/portal-public/src/App.tsx", "source")

    with pytest.raises(SystemExit, match="non-runtime app file"):
        builder.assert_civiccast_wheel_layout(wheel)


def test_wheel_layout_requires_packaged_app_shell_runtime_inputs(tmp_path: Path) -> None:
    wheel = tmp_path / "civiccast.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("civiccast/apps/portal-operator/dist/index.html", "operator")
        archive.writestr("civiccast/apps/portal-public/dist/index.html", "public")

    with pytest.raises(SystemExit, match="app shell runtime"):
        builder.assert_civiccast_wheel_layout(wheel)


def test_wheel_layout_accepts_compiled_portals_and_app_shell_runtime(tmp_path: Path) -> None:
    wheel = tmp_path / "civiccast.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("civiccast/__init__.py", "")
        _write_required_runtime_app_files(archive)
        archive.writestr("civiccast/apps/portal-operator/dist/assets/app.js", "js")

    builder.assert_civiccast_wheel_layout(wheel)


def test_normalize_civiccast_install_removes_uv_metadata_and_pins_launchers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel = tmp_path / "civiccast.whl"
    dist_info = "civiccast-1.0.dist-info"
    wheel_record = (
        f"civiccast/__init__.py,,\n{dist_info}/entry_points.txt,,\n{dist_info}/RECORD,,\n"
    )
    entry_points = (
        "[console_scripts]\n"
        "civiccast = civiccast.cli:main_entrypoint\n"
        "civiccast-runtime = civiccast.native.runtime_cli:main_entrypoint\n"
    )
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("civiccast/__init__.py", b"")
        archive.writestr(f"{dist_info}/entry_points.txt", entry_points)
        archive.writestr(f"{dist_info}/RECORD", wheel_record)

    site_packages = tmp_path / "site-packages"
    installed_dist_info = site_packages / dist_info
    installed_dist_info.mkdir(parents=True)
    (site_packages / "civiccast").mkdir()
    (site_packages / "civiccast" / "__init__.py").write_bytes(b"")
    (installed_dist_info / "entry_points.txt").write_text(
        entry_points,
        encoding="utf-8",
    )
    for name, content in {
        "INSTALLER": b"uv",
        "REQUESTED": b"",
        "direct_url.json": b"machine path",
        "uv_cache.json": b"cache",
    }.items():
        (installed_dist_info / name).write_bytes(content)
    launcher_bytes = {
        "bin/civiccast.exe": b"launcher-a",
        "bin/civiccast-runtime.exe": b"launcher-b",
    }
    for relative, content in launcher_bytes.items():
        path = site_packages / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(content)
    monkeypatch.setattr(
        builder,
        "CIVICCAST_CONSOLE_LAUNCHERS",
        {
            relative: (len(content), hashlib.sha256(content).hexdigest())
            for relative, content in launcher_bytes.items()
        },
    )

    builder.normalize_civiccast_install_metadata(site_packages, wheel)

    assert not (installed_dist_info / "direct_url.json").exists()
    assert not (installed_dist_info / "uv_cache.json").exists()
    installed_record = (installed_dist_info / "RECORD").read_text(encoding="utf-8")
    assert "bin/civiccast.exe" in installed_record
    assert "bin/civiccast-runtime.exe" in installed_record
    assert "direct_url.json" not in installed_record


def test_remove_uv_cache_metadata_makes_separate_installs_identical(
    tmp_path: Path,
) -> None:
    records: list[bytes] = []
    for install_name, timestamp in (("install-a", 100), ("install-b", 200)):
        site_packages = tmp_path / install_name
        dist_info = site_packages / "av-18.0.0.dist-info"
        dist_info.mkdir(parents=True)
        cache = dist_info / "uv_cache.json"
        cache.write_text(
            json.dumps({"timestamp": {"secs_since_epoch": timestamp}}),
            encoding="utf-8",
        )
        cache_hash = hashlib.sha256(cache.read_bytes()).hexdigest()
        (dist_info / "RECORD").write_text(
            "av/__init__.py,sha256=reviewed,1\n"
            f"av-18.0.0.dist-info/uv_cache.json,sha256={cache_hash},"
            f"{cache.stat().st_size}\n"
            "av-18.0.0.dist-info/RECORD,,\n",
            encoding="utf-8",
        )

        builder.remove_uv_cache_metadata(site_packages)

        assert not cache.exists()
        record = (dist_info / "RECORD").read_bytes()
        assert b"uv_cache.json" not in record
        records.append(record)

    assert records[0] == records[1]


def test_remove_uv_cache_metadata_rejects_unowned_cache(tmp_path: Path) -> None:
    site_packages = tmp_path / "site-packages"
    dist_info = site_packages / "av-18.0.0.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "uv_cache.json").write_text("{}", encoding="utf-8")
    (dist_info / "RECORD").write_text(
        "av-18.0.0.dist-info/RECORD,,\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="exactly one owning RECORD row"):
        builder.remove_uv_cache_metadata(site_packages)


# ---------------------------------------------------------------------------
# RECORD -> distribution index
# ---------------------------------------------------------------------------


def test_record_index_maps_files_to_canonical_distribution_and_version(tmp_path: Path) -> None:
    di = tmp_path / "jaraco.classes-3.4.0.dist-info"
    di.mkdir()
    (di / "RECORD").write_text(
        "jaraco/classes/__init__.py,sha256=abc,10\n"
        "jaraco.classes-3.4.0.dist-info/METADATA,sha256=def,20\n",
        encoding="utf-8",
    )

    index, distributions = builder.build_site_packages_index(tmp_path)

    assert "jaraco-classes" in distributions
    assert index["Lib/site-packages/jaraco/classes/__init__.py"] == ("jaraco-classes", "3.4.0")


def test_record_index_ignores_parent_escaping_data_paths(tmp_path: Path) -> None:
    di = tmp_path / "somepkg-1.0.dist-info"
    di.mkdir()
    (di / "RECORD").write_text(
        "somepkg/__init__.py,sha256=abc,10\n../../Scripts/somepkg.exe,sha256=zzz,99\n",
        encoding="utf-8",
    )

    index, _ = builder.build_site_packages_index(tmp_path)

    assert "Lib/site-packages/somepkg/__init__.py" in index
    assert not any(".." in key for key in index)


def test_strip_pycache_removes_bytecode_caches(tmp_path: Path) -> None:
    """`__pycache__` dirs (runtime .pyc caches, in no RECORD, regenerated on
    import) must be stripped so the build hashes a clean, source-only tree."""
    pkg = tmp_path / "Lib" / "site-packages" / "pkg"
    (pkg / "__pycache__").mkdir(parents=True)
    (pkg / "__init__.py").write_text("x", encoding="utf-8")
    (pkg / "__pycache__" / "__init__.cpython-312.pyc").write_bytes(b"bytecode")

    builder.strip_pycache(tmp_path)

    assert not (pkg / "__pycache__").exists()
    assert (pkg / "__init__.py").exists()  # source untouched


def test_dependency_install_uses_the_reviewed_pyav_wheelhouse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "av-18.0.0-cp311-abi3-win_amd64.whl").write_bytes(b"reviewed")
    site_packages = tmp_path / "site-packages"
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(builder.subprocess, "run", run)
    builder.install_pinned_dependencies(
        site_packages,
        wheelhouse=wheelhouse,
        uv_executable="uv",
    )

    command = calls[0]
    assert command[command.index("--find-links") + 1] == str(wheelhouse)
    assert "--require-hashes" in command
    assert "--no-deps" in command
    assert "--no-index" in command
    assert command[command.index("-r") + 1] == str(builder.APP_REQUIREMENTS_FILE)
    assert len(calls) == 1


def test_dependency_install_splits_av_out_of_the_hash_check_when_advisory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On the self-hosted lane the compiled `av` wheel legitimately does not
    match `requirements-native-app.txt`'s hosted-reviewed hash pin (see
    docs/process/pyav-wheel-reproducibility.md) -- the SAME advisory posture
    that let the build step accept it with only a warning must let install
    accept it too, without weakening the hash check for anything else."""
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    pyav_wheel = wheelhouse / "av-18.0.0-cp311-abi3-win_amd64.whl"
    pyav_wheel.write_bytes(b"self-hosted-built, different bytes than the reviewed reference")
    site_packages = tmp_path / "site-packages"
    calls: list[list[str]] = []
    filtered_lock_contents_at_call_time: list[str] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "-r" in command:
            # Snapshot now: the real code deletes this scratch file in a
            # `finally` right after this subprocess.run() call returns.
            filtered_lock_contents_at_call_time.append(
                Path(command[command.index("-r") + 1]).read_text(encoding="utf-8")
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(builder.subprocess, "run", run)
    builder.install_pinned_dependencies(
        site_packages,
        wheelhouse=wheelhouse,
        uv_executable="uv",
        advisory_pyav_wheel_hash=True,
    )

    assert len(calls) == 2

    rest_command = calls[0]
    assert "--require-hashes" in rest_command
    assert "--no-deps" in rest_command
    assert "--no-index" in rest_command
    assert rest_command[rest_command.index("--find-links") + 1] == str(wheelhouse)
    filtered_lock_path = Path(rest_command[rest_command.index("-r") + 1])
    assert filtered_lock_path.parent == wheelhouse
    assert len(filtered_lock_contents_at_call_time) == 1
    assert "av==" not in filtered_lock_contents_at_call_time[0]
    assert "boto3==" in filtered_lock_contents_at_call_time[0]
    # The filtered lock is written, used, and cleaned up during the call --
    # by the time control returns here it must not linger in the wheelhouse.
    assert not filtered_lock_path.exists()

    av_command = calls[1]
    assert "--require-hashes" not in av_command
    assert "--no-deps" in av_command
    assert "--no-index" in av_command
    assert av_command[-1] == str(pyav_wheel)

    # The wheelhouse must contain nothing but the one av wheel afterward --
    # no leftover filtered-lock scratch file from either install call.
    assert [p.name for p in wheelhouse.iterdir()] == [pyav_wheel.name]


def test_dependency_install_advisory_flag_defaults_to_the_hosted_unified_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling without `advisory_pyav_wheel_hash` (every existing caller
    before this parameter was added, and the hosted lane's own call) must
    still take the single unified `--require-hashes` path -- byte-identical
    to before the advisory split existed."""
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "av-18.0.0-cp311-abi3-win_amd64.whl").write_bytes(b"reviewed")
    site_packages = tmp_path / "site-packages"
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(builder.subprocess, "run", run)
    builder.install_pinned_dependencies(
        site_packages,
        wheelhouse=wheelhouse,
        uv_executable="uv",
    )

    assert len(calls) == 1
    assert "--require-hashes" in calls[0]
    assert calls[0][calls[0].index("-r") + 1] == str(builder.APP_REQUIREMENTS_FILE)


def test_requirements_lock_without_av_removes_only_the_av_entry() -> None:
    lock_text = (
        "av==18.0.0 \\\n"
        "    --hash=sha256:aaaa\n"
        "    # via faster-whisper\n"
        "boto3==1.43.56 \\\n"
        "    --hash=sha256:bbbb\n"
    )

    filtered = builder._requirements_lock_without_av(lock_text)

    assert "av==" not in filtered
    assert "boto3==1.43.56" in filtered
    assert "--hash=sha256:bbbb" in filtered


def test_requirements_lock_without_av_refuses_a_lock_missing_av() -> None:
    with pytest.raises(SystemExit):
        builder._requirements_lock_without_av("boto3==1.43.56 \\\n    --hash=sha256:bbbb\n")


def test_dependency_wheel_download_is_hash_locked_and_binary_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pyav_wheelhouse = tmp_path / "pyav"
    pyav_wheelhouse.mkdir()
    (pyav_wheelhouse / "av-18.0.0-cp311-abi3-win_amd64.whl").write_bytes(b"reviewed")
    retained = tmp_path / "retained"
    commands: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(builder.subprocess, "run", fake_run)

    builder.download_pinned_dependency_wheels(
        retained,
        pyav_wheelhouse=pyav_wheelhouse,
        python_executable="reviewed-python.exe",
    )

    command = commands[-1]
    assert command[:4] == [
        "reviewed-python.exe",
        "-m",
        "pip",
        "download",
    ]
    assert "--require-hashes" in command
    assert "--only-binary=:all:" in command
    assert "--no-deps" in command
    assert command[command.index("--dest") + 1] == str(retained)
    assert not any(path.name.startswith(".requirements-") for path in retained.iterdir())


def test_pyav_wrapper_and_embedded_ffmpeg_files_have_distinct_licenses() -> None:
    assert (
        builder.license_for_payload_path(
            "av",
            "Lib/site-packages/av/__init__.py",
        )
        == "BSD-3-Clause"
    )
    assert (
        builder.license_for_payload_path(
            "av",
            "Lib/site-packages/av.libs/avcodec-62-test.dll",
        )
        == "LGPL-2.1-or-later AND LicenseRef-Microsoft-VCRedist"
    )
    assert (
        builder.license_for_payload_path(
            "av",
            "Lib/site-packages/av-18.0.0.dist-info/FFMPEG-PROVENANCE.json",
        )
        == "LGPL-2.1-or-later"
    )


def test_missing_wheel_license_texts_are_exactly_pinned() -> None:
    artifacts = {artifact.distribution: artifact for artifact in builder.EXTERNAL_LICENSE_ARTIFACTS}
    assert set(artifacts) == {
        "ctranslate2",
        "flatbuffers",
        "tokenizers",
    }
    assert artifacts["ctranslate2"].sha256 == (
        "54aa79d9fe3c09e67a16dcd95b9e88676405a6ec174efda31036983cf7672ecb"
    )
    assert artifacts["flatbuffers"].sha256 == (
        "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
    )
    assert artifacts["tokenizers"].sha256 == (
        "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"
    )


def test_external_license_file_is_attributed_to_its_distribution(tmp_path: Path) -> None:
    relative = "THIRD-PARTY-LICENSES/CTranslate2-MIT.txt"
    license_file = tmp_path / relative
    license_file.parent.mkdir()
    license_file.write_bytes(b"MIT terms")

    entries = builder.hash_payload_tree(
        tmp_path,
        site_packages_index={},
        pywin32_dlls=[],
        civiccast_version="1.0",
        external_license_index={
            relative: ("ctranslate2", "4.8.1", "MIT"),
        },
    )

    assert len(entries) == 1
    assert entries[0].distribution == "ctranslate2"
    assert entries[0].license == "MIT"


def test_payload_runtime_probe_requires_mandatory_imports_and_decode() -> None:
    # Mutmut keys use the source-relative module path. A bare loader alias
    # records trampoline hits under a different name and tests no mutants.
    assert builder.__name__ == "scripts.build_native_app_payload"
    assert builder.run_payload_runtime_probe.__module__ == builder.__name__
    report = {
        "imports": sorted(builder.REQUIRED_RUNTIME_IMPORTS),
        "decoded_frames": 16,
        "portal_deep_links": {
            "/operator/setup": 200,
            "/meetings/example": 200,
        },
    }
    builder.assert_payload_runtime_probe(report)

    report["imports"] = ["civiccast"]
    with pytest.raises(SystemExit, match="missing mandatory import"):
        builder.assert_payload_runtime_probe(report)

    report["imports"] = sorted(builder.REQUIRED_RUNTIME_IMPORTS)
    report["decoded_frames"] = 0
    with pytest.raises(SystemExit, match="decoded no audio frames"):
        builder.assert_payload_runtime_probe(report)

    report["decoded_frames"] = 16
    report["portal_deep_links"]["/operator/setup"] = 404
    with pytest.raises(SystemExit, match="portal deep-link"):
        builder.assert_payload_runtime_probe(report)


def test_payload_runtime_probe_disables_bytecode_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = tmp_path / "payload"
    out.mkdir()
    (out / "python.exe").write_bytes(b"python")
    report = {
        "imports": sorted(builder.REQUIRED_RUNTIME_IMPORTS),
        "decoded_frames": 16,
        "portal_deep_links": {
            "/operator/setup": 200,
            "/meetings/example": 200,
        },
    }
    seen: list[str] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.extend(command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(report), stderr="")

    monkeypatch.setattr(builder.subprocess, "run", fake_run)

    assert builder.run_payload_runtime_probe(out) == report
    assert seen[:4] == [str(out / "python.exe"), "-I", "-B", "-c"]


@pytest.mark.parametrize("probe_fails", [False, True], ids=["success", "failure"])
def test_payload_runtime_probe_isolates_parent_profile_and_cleans_temporary_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_fails: bool,
) -> None:
    out = tmp_path / "payload"
    out.mkdir()
    (out / "python.exe").write_bytes(b"python")
    parent_profile = tmp_path / "real-user-profile"
    poisoned = {
        "DATABASE_URL": "postgresql://real-station.invalid/civiccast",
        "CIVICCAST_STATION_STATE_PATH": str(parent_profile / "station-state.json"),
        "CIVICCAST_MANAGED_STORAGE_DIR": str(parent_profile / "managed-storage"),
        "CIVICCAST_CONFIG_DIR": str(parent_profile / "config"),
        "CIVICCAST_UPLOAD_DIR": str(parent_profile / "uploads"),
        "CIVICCAST_OPERATOR_CONSOLE_DIST": str(parent_profile / "operator"),
        "LOCALAPPDATA": str(parent_profile / "AppData" / "Local"),
        "APPDATA": str(parent_profile / "AppData" / "Roaming"),
        "USERPROFILE": str(parent_profile),
        "PROGRAMDATA": str(parent_profile / "ProgramData"),
        "HOME": str(parent_profile),
    }
    for name, value in poisoned.items():
        monkeypatch.setenv(name, value)
    report = {
        "imports": sorted(builder.REQUIRED_RUNTIME_IMPORTS),
        "decoded_frames": 16,
        "portal_deep_links": {
            "/operator/setup": 200,
            "/meetings/example": 200,
        },
    }
    seen: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        child_env = kwargs["env"]
        assert isinstance(child_env, dict)
        probe_root = Path(child_env["LOCALAPPDATA"]).parent
        seen["probe_root"] = probe_root
        seen["command"] = command
        assert probe_root.is_dir()
        assert Path(child_env["LOCALAPPDATA"]).is_dir()
        assert Path(child_env["PROGRAMDATA"]).is_dir()
        assert Path(child_env["TEMP"]).is_dir()
        assert "DATABASE_URL" not in child_env
        assert not any(str(parent_profile) in value for value in child_env.values())
        for name in poisoned:
            if name.startswith("CIVICCAST_"):
                assert child_env.get(name) != poisoned[name]
        for name in (
            "CIVICCAST_CONFIG_DIR",
            "CIVICCAST_MANAGED_STORAGE_DIR",
            "CIVICCAST_STATION_STATE_PATH",
            "CIVICCAST_STATION_STORAGE_ROOT",
        ):
            assert Path(child_env[name]).is_relative_to(probe_root)
        (Path(child_env["LOCALAPPDATA"]) / "probe-write.txt").write_text(
            "isolated", encoding="utf-8"
        )
        if probe_fails:
            raise subprocess.CalledProcessError(1, command, stderr="expected failure")
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(report), stderr="")

    monkeypatch.setattr(builder.subprocess, "run", fake_run)

    if probe_fails:
        with pytest.raises(subprocess.CalledProcessError):
            builder.run_payload_runtime_probe(out)
    else:
        assert builder.run_payload_runtime_probe(out) == report
    assert seen["command"] == [
        str(out / "python.exe"),
        "-I",
        "-B",
        "-c",
        builder._PAYLOAD_RUNTIME_PROBE,
    ]
    assert not Path(os.fspath(seen["probe_root"])).exists()


def test_place_msvc_runtime_requires_the_exact_reviewed_dll(tmp_path: Path) -> None:
    source = tmp_path / "msvcp140.dll"
    source.write_bytes(b"reviewed-msvc-runtime")
    expected_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    contract = {
        "msvcp140.dll": {
            "bytes": source.stat().st_size,
            "sha256": expected_sha256,
            "version": "14.50.35719.0",
            "license": "LicenseRef-Microsoft-VCRedist",
        }
    }
    out = tmp_path / "payload"
    out.mkdir()

    placed = builder.place_msvc_runtime(out, source, contract=contract)

    assert placed == {
        "msvcp140.dll": (
            "microsoft-vc-runtime",
            "14.50.35719.0",
            "LicenseRef-Microsoft-VCRedist",
        )
    }
    assert (out / "msvcp140.dll").read_bytes() == b"reviewed-msvc-runtime"

    source.write_bytes(b"tampered-msvc-runtime")
    with pytest.raises(SystemExit, match=r"MSVCP140\.dll.*(?:size|SHA-256)"):
        builder.place_msvc_runtime(out, source, contract=contract)


def test_locate_msvc_runtime_prefers_configured_build_tools_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_tools = tmp_path / "BuildTools"
    runtime = (
        build_tools
        / "VC"
        / "Redist"
        / "MSVC"
        / "14.50.35719"
        / "x64"
        / "Microsoft.VC145.CRT"
        / "msvcp140.dll"
    )
    runtime.parent.mkdir(parents=True)
    runtime.write_bytes(b"reviewed-msvc-runtime")
    monkeypatch.setattr(
        builder,
        "MSVC_RUNTIME_FILES",
        {
            "msvcp140.dll": {
                "bytes": runtime.stat().st_size,
                "sha256": hashlib.sha256(runtime.read_bytes()).hexdigest(),
                "version": "14.50.35719.0",
                "license": "LicenseRef-Microsoft-VCRedist",
            }
        },
    )
    monkeypatch.setenv("CIVICCAST_MSVC_INSTALLATION_PATH", str(build_tools))

    assert builder.locate_msvc_runtime() == runtime.resolve()


def test_console_launcher_script_disables_bytecode_before_importing_entrypoint() -> None:
    script = builder.render_console_launcher_script("civiccast.native.runtime_cli:main_entrypoint")

    assert script.index("sys.dont_write_bytecode = True") < script.index(
        "from civiccast.native.runtime_cli import main_entrypoint"
    )


def test_runtime_bytecode_policy_runs_before_existing_pth_imports(
    tmp_path: Path,
) -> None:
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    policy = site_packages / "distutils-precedence.pth"
    policy.write_text(
        "import os; __import__('_distutils_hack')\n",
        encoding="utf-8",
    )

    builder.normalize_runtime_bytecode_policy(site_packages)
    builder.normalize_runtime_bytecode_policy(site_packages)

    lines = policy.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "import sys; sys.dont_write_bytecode = True"
    assert lines.count(lines[0]) == 1
    assert "_distutils_hack" in lines[1]


@pytest.mark.windows_only
@pytest.mark.skipif(sys.platform != "win32", reason="PE resource rewrite is Windows-only")
def test_console_launcher_normalization_is_relative_and_non_mutating(
    tmp_path: Path,
) -> None:
    source = Path(sys.executable).with_name("civiccast-runtime.exe")
    assert source.is_file()
    launcher = tmp_path / "civiccast-runtime.exe"
    launcher.write_bytes(source.read_bytes())

    builder.normalize_console_launcher(
        launcher,
        "civiccast.native.runtime_cli:main_entrypoint",
    )

    normalized = launcher.read_bytes()
    assert b"..\\..\\..\\python.exe" in normalized
    # Resource lookup is authoritative for the interpreter. A stale raw path
    # may remain in a PE section after UpdateResourceW, so execute below too.
    assert builder._pe_rcdata(launcher, "UV_PYTHON_PATH") == b"..\\..\\..\\python.exe"
    assert str(Path(sys.executable)).encode() not in builder._pe_rcdata(launcher, "UV_SCRIPT_DATA")
    assert b"sys.dont_write_bytecode = True" in normalized
    assert normalized.index(b"sys.dont_write_bytecode = True") < normalized.index(
        b"from civiccast.native.runtime_cli import main_entrypoint"
    )

    # uv 0.12.13 leaves a stale ZIP payload in the PE after UpdateResourceW.
    # The trampoline loads its script through zipimport from the complete EXE,
    # so prove the normalized executable runs the replacement script through
    # the relative payload interpreter, rather than merely inspecting bytes.
    runtime = tmp_path / "runtime"
    bin_dir = runtime / "Lib" / "site-packages" / "bin"
    bin_dir.mkdir(parents=True)
    fixture_launcher = bin_dir / "fixture.exe"
    fixture_launcher.write_bytes(source.read_bytes())
    builder.normalize_console_launcher(fixture_launcher, "fixture_entry:main")

    base_python = Path(sys._base_executable)
    shutil.copy2(base_python, runtime / "python.exe")
    python_dll = f"python{sys.version_info.major}{sys.version_info.minor}.dll"
    shutil.copy2(base_python.with_name(python_dll), runtime / python_dll)
    (runtime / f"python{sys.version_info.major}{sys.version_info.minor}._pth").write_text(
        "\n".join(
            (
                str(
                    Path(sys.base_prefix)
                    / f"python{sys.version_info.major}{sys.version_info.minor}.zip"
                ),
                str(Path(sys.base_prefix) / "DLLs"),
                str(Path(sys.base_prefix) / "Lib"),
                ".",
                "Lib\\site-packages",
                "import site",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    receipt = tmp_path / "fixture-receipt.txt"
    (runtime / "Lib" / "site-packages" / "fixture_entry.py").write_text(
        "import os\nimport sys\nfrom pathlib import Path\n"
        "def main():\n"
        "    Path(os.environ['CIVICCAST_FIXTURE_RECEIPT']).write_text(\n"
        "        f'{sys.executable}\\n{sys.dont_write_bytecode}\\n', encoding='utf-8'\n"
        "    )\n"
        "    return 0\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment.pop("PYTHONDONTWRITEBYTECODE", None)
    environment["CIVICCAST_FIXTURE_RECEIPT"] = str(receipt)
    result = subprocess.run(
        [str(fixture_launcher)],
        capture_output=True,
        text=True,
        env=environment,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert receipt.read_text(encoding="utf-8") == f"{runtime / 'python.exe'}\nTrue\n"
    assert not list(runtime.rglob("fixture_entry*.pyc"))

    # The same launcher is normalized twice by some rebuild workflows.
    builder.normalize_console_launcher(fixture_launcher, "fixture_entry:main")
    receipt.unlink()
    second = subprocess.run(
        [str(fixture_launcher)], capture_output=True, text=True, env=environment, timeout=20
    )
    assert second.returncode == 0, second.stderr
    assert receipt.read_text(encoding="utf-8") == f"{runtime / 'python.exe'}\nTrue\n"
    assert not list(runtime.rglob("fixture_entry*.pyc"))


def test_retained_launcher_script_rejects_empty_or_malformed_data(tmp_path: Path) -> None:
    launcher = tmp_path / "fixture.exe"
    launcher.write_bytes(b"fixture")

    with pytest.raises(SystemExit, match="script resource is empty"):
        builder._discard_retained_pe_resource_data(launcher, b"", b"not-empty")
    with pytest.raises(SystemExit, match="script resource is malformed"):
        builder._discard_retained_pe_resource_data(launcher, b"not-a-zip", b"also-not-a-zip")
    with pytest.raises(SystemExit, match="script resource is malformed"):
        builder._discard_retained_pe_resource_data(launcher, b"not-a-zip", b"not-a-zip")

    old = builder._launcher_script_zip("print('old')\n")
    active = builder._launcher_script_zip("print('active')\n")
    launcher.write_bytes(b"fixture" + active)
    builder._discard_retained_pe_resource_data(launcher, old, active)
    assert launcher.read_bytes() == b"fixture" + active  # No retained payload on uv 0.12.12.

    overlapping = io.BytesIO()
    with zipfile.ZipFile(overlapping, "w") as archive:
        archive.writestr("__main__.py", b"print('active')\n")
        archive.writestr("retained-old.bin", old)
    with pytest.raises(SystemExit, match="overlaps the active resource"):
        builder._discard_retained_pe_resource_data(launcher, old, overlapping.getvalue())


def test_retained_launcher_script_preserves_active_payload_and_file_size(tmp_path: Path) -> None:
    old = builder._launcher_script_zip("print('old')\n")
    active = builder._launcher_script_zip("print('a longer replacement script')\n")
    launcher = tmp_path / "fixture.exe"
    original = b"prefix" + old + b"middle" + active + b"suffix" + old
    launcher.write_bytes(original)
    builder._discard_retained_pe_resource_data(launcher, old, active)
    assert launcher.read_bytes() == (
        b"prefix" + bytes(len(old)) + b"middle" + active + b"suffix" + bytes(len(old))
    )
    assert launcher.stat().st_size == len(original)
    builder._discard_retained_pe_resource_data(launcher, active, active)
    with zipfile.ZipFile(launcher) as archive:
        assert archive.read("__main__.py") == b"print('a longer replacement script')\n"


@pytest.mark.parametrize("active_copies", [0, 2])
def test_retained_launcher_script_rejects_ambiguous_active_payload(
    tmp_path: Path, active_copies: int
) -> None:
    old = builder._launcher_script_zip("print('old')\n")
    active = builder._launcher_script_zip("print('active')\n")
    launcher = tmp_path / "fixture.exe"
    original = old + active * active_copies
    launcher.write_bytes(original)
    with pytest.raises(SystemExit, match="active script payload is missing or ambiguous"):
        builder._discard_retained_pe_resource_data(launcher, old, active)
    assert launcher.read_bytes() == original


# ---------------------------------------------------------------------------
# manifest + SHA256SUMS rendering
# ---------------------------------------------------------------------------


def _entry(path: str, data: bytes, dist: str, version: str, lic: str) -> object:
    return builder.AppFileEntry(
        path=path,
        sha256=hashlib.sha256(data).hexdigest(),
        bytes=len(data),
        distribution=dist,
        version=version,
        license=lic,
    )


def test_manifest_is_sorted_and_carries_the_interpreter_pin() -> None:
    entries = [
        _entry(
            "python.exe",
            b"exe",
            builder.INTERPRETER_DISTRIBUTION,
            builder.INTERPRETER_VERSION,
            "PSF-2.0",
        ),
        _entry("Lib/site-packages/civiccast/__init__.py", b"x", "civiccast", "1.0", "Apache-2.0"),
    ]
    manifest = builder.build_app_manifest(
        entries,
        civiccast_version="1.0",
        source_state={
            "head": "d" * 40,
            "dirty": True,
            "diff_sha256": "e" * 64,
            "status_sha256": "f" * 64,
        },
        civiccast_wheel_sha256="a" * 64,
        app_lock_sha256="c0ffee",
        build_toolchain=builder.APP_BUILD_TOOLCHAIN,
    )
    assert manifest["file_count"] == 2
    assert manifest["interpreter"]["sha256"] == builder.INTERPRETER_SHA256
    assert manifest["civiccast"] == {
        "version": "1.0",
        "wheel_sha256": "a" * 64,
        "source_state": {
            "head": "d" * 40,
            "dirty": True,
            "diff_sha256": "e" * 64,
            "status_sha256": "f" * 64,
        },
    }
    assert "build_sha" not in manifest["civiccast"]
    assert manifest["caption_pack"] == builder.CAPTION_PACK_CONTRACT
    assert manifest["whistle_engine"] == builder.WHISTLE_ENGINE_CONTRACT
    assert manifest["build_toolchain_lock_sha256"] == builder.APP_BUILD_TOOLCHAIN_LOCK_SHA256
    # deterministic: files sorted by path
    assert [f["path"] for f in manifest["files"]] == sorted(f["path"] for f in manifest["files"])


def test_sha256sums_lines_match_the_entries() -> None:
    entries = [_entry("b.txt", b"bb", "civiccast", "1.0", "Apache-2.0")]
    text = builder.render_sha256sums(entries)
    assert text == f"{hashlib.sha256(b'bb').hexdigest()}  b.txt\n"


@pytest.mark.parametrize(
    "binary_license",
    [
        "LGPL-2.1-or-later",
        "LGPL-2.1-or-later AND LicenseRef-Microsoft-VCRedist",
    ],
)
def test_license_bom_splits_pyav_wrapper_from_embedded_ffmpeg(binary_license: str) -> None:
    entries = [
        _entry(
            "Lib/site-packages/av/__init__.py",
            b"a",
            "av",
            "18.0.0",
            "BSD-3-Clause",
        ),
        _entry(
            "Lib/site-packages/av.libs/avcodec-62-test.dll",
            b"ff",
            "av",
            "18.0.0",
            binary_license,
        ),
    ]

    text = builder.render_app_license_bom(entries)

    assert "| av | 18.0.0 | BSD-3-Clause | 1 | 1 |" in text
    assert (
        f"| av (embedded FFmpeg) | 611cec792a-security1-minimal-msvc | {binary_license} | 1 | 2 |"
    ) in text


# ---------------------------------------------------------------------------
# Verifier: real round-trip + negative controls
# ---------------------------------------------------------------------------


def _write_minimal_payload(tree: Path) -> None:
    """A minimal but structurally valid payload: an interpreter file + one
    site-packages file, with a matching manifest/SHA256SUMS."""
    tree.mkdir(parents=True)
    (tree / "python.exe").write_bytes(b"MZ-fake-exe")
    (tree / "python312.dll").write_bytes(b"fake-dll")
    sp = tree / "Lib" / "site-packages" / "civiccast"
    sp.mkdir(parents=True)
    (sp / "__init__.py").write_bytes(b"print('hi')")
    dist_info = tree / "Lib" / "site-packages" / "civiccast-1.0.dist-info"
    dist_info.mkdir()
    record_bytes = b"civiccast/__init__.py,,\nciviccast-1.0.dist-info/RECORD,,\n"
    (dist_info / "RECORD").write_bytes(record_bytes)
    wheel_path = tree / builder.CIVICCAST_RETAINED_WHEEL_PATH
    wheel_path.parent.mkdir()
    with zipfile.ZipFile(wheel_path, "w") as archive:
        archive.writestr("civiccast/__init__.py", b"print('hi')")
        archive.writestr(
            "civiccast-1.0.dist-info/RECORD",
            record_bytes,
        )
    wheel_bytes = wheel_path.read_bytes()

    entries = [
        _entry(
            "python.exe",
            b"MZ-fake-exe",
            builder.INTERPRETER_DISTRIBUTION,
            builder.INTERPRETER_VERSION,
            "PSF-2.0",
        ),
        _entry(
            "python312.dll",
            b"fake-dll",
            builder.INTERPRETER_DISTRIBUTION,
            builder.INTERPRETER_VERSION,
            "PSF-2.0",
        ),
        _entry(
            "Lib/site-packages/civiccast/__init__.py",
            b"print('hi')",
            "civiccast",
            "1.0",
            "Apache-2.0",
        ),
        _entry(
            "Lib/site-packages/civiccast-1.0.dist-info/RECORD",
            record_bytes,
            "civiccast",
            "1.0",
            "Apache-2.0",
        ),
        _entry(
            builder.CIVICCAST_RETAINED_WHEEL_PATH,
            wheel_bytes,
            "civiccast",
            "1.0",
            "Apache-2.0",
        ),
    ]
    manifest = builder.build_app_manifest(
        entries,
        civiccast_version="1.0",
        source_state={
            "head": "a" * 40,
            "dirty": False,
            "diff_sha256": "b" * 64,
            "status_sha256": "c" * 64,
        },
        civiccast_wheel_sha256=hashlib.sha256(wheel_bytes).hexdigest(),
        app_lock_sha256=builder.APP_REQUIREMENTS_SHA256,
        build_toolchain=builder.APP_BUILD_TOOLCHAIN,
    )
    (tree / "app-payload-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    (tree / "SHA256SUMS").write_text(builder.render_sha256sums(entries), encoding="utf-8")
    (tree / "LICENSE-BOM.md").write_text(builder.render_app_license_bom(entries), encoding="utf-8")


def test_verifier_rejects_dirty_source_payload(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    manifest_path = tree / "app-payload-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["civiccast"]["source_state"]["dirty"] = True
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = verifier.check_app_payload_verification(
        tree,
        expected_source_state={
            "head": "a" * 40,
            "dirty": False,
            "diff_sha256": "b" * 64,
            "status_sha256": "c" * 64,
        },
        require_clean_source=True,
    )

    assert result.status == "FAIL"
    assert "dirty" in result.detail


def test_verifier_requires_app_local_msvc_runtime_when_ctranslate2_is_present(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    extension = tree / "Lib" / "site-packages" / "ctranslate2" / "__init__.py"
    extension.parent.mkdir()
    extension.write_bytes(b"# ctranslate2 fixture\n")
    dist_info = tree / "Lib" / "site-packages" / "ctranslate2-4.8.1.dist-info"
    dist_info.mkdir()
    record = dist_info / "RECORD"
    record.write_bytes(b"ctranslate2/__init__.py,,\nctranslate2-4.8.1.dist-info/RECORD,,\n")

    manifest_path = tree / "app-payload-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for path, data in (
        ("Lib/site-packages/ctranslate2/__init__.py", extension.read_bytes()),
        ("Lib/site-packages/ctranslate2-4.8.1.dist-info/RECORD", record.read_bytes()),
    ):
        manifest["files"].append(
            {
                "path": path,
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
                "distribution": "ctranslate2",
                "version": "4.8.1",
                "license": "MIT",
            }
        )
    manifest["file_count"] = len(manifest["files"])
    manifest["total_bytes"] = sum(item["bytes"] for item in manifest["files"])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = verifier.check_app_payload_verification(
        tree,
        require_console_launchers=False,
        require_dependency_wheels=False,
    )

    assert result.status == "FAIL"
    assert "MISSING MSVC RUNTIME FILE: msvcp140.dll required by CTranslate2" in result.detail


def test_verifier_rejects_forged_clean_source_identity(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    manifest_path = tree / "app-payload-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["civiccast"]["source_state"] = {
        "head": "f" * 40,
        "dirty": False,
        "diff_sha256": "e" * 64,
        "status_sha256": "d" * 64,
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = verifier.check_app_payload_verification(
        tree,
        expected_source_state={
            "head": "a" * 40,
            "dirty": False,
            "diff_sha256": "b" * 64,
            "status_sha256": "c" * 64,
        },
        require_clean_source=True,
    )

    assert result.status == "FAIL"
    assert "checkout identity" in result.detail


def test_verifier_passes_on_a_faithful_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This tiny fixture isolates generic tree verification; OCR is checked separately.
    monkeypatch.setattr(verifier, "OCR_FILES", {})
    # The synthetic runtime isolates generic/PyAV checks; dedicated runtime tests
    # exercise the independent pinned provenance and original binary checks.
    monkeypatch.setattr(verifier, "verify_runtime_files", lambda *a, **kw: [])
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    result = verifier.check_app_payload_verification(tree)
    assert result.status == "PASS", result.detail


def test_release_verifier_requires_the_external_caption_pack_contract(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    manifest_path = tree / "app-payload-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("caption_pack")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = verifier.check_app_payload_verification(
        tree,
        require_caption_pack=True,
    )

    assert result.status == "FAIL"
    assert "CAPTION PACK" in result.detail
    assert "captions-large-v3" in result.detail


def test_release_verifier_requires_the_canonical_whistle_engine_dll(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)

    result = verifier.check_app_payload_verification(tree, require_caption_pack=True)

    assert result.status == "FAIL"
    assert "WHISTLE ENGINE" in result.detail
    assert "needle/libneedle3.dll" in result.detail


def test_whistle_engine_verifier_checks_the_pinned_dll_sha256(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"reviewed-needle-dll"
    digest = hashlib.sha256(body).hexdigest()
    package_file = "Lib/site-packages/needle/libneedle3.dll"
    contract = {
        "distribution": "cactus-needle",
        "version": "3.1.0",
        "license": "Apache-2.0",
        "wheel_source_url": "https://example.invalid/pinned-wheel.whl",
        "wheel_sha256": "a" * 64,
        "package_file": package_file,
        "file_bytes": len(body),
        "file_sha256": digest,
    }
    monkeypatch.setattr(verifier, "WHISTLE_ENGINE_CONTRACT", contract)
    tree = tmp_path / "payload"
    dll = tree / package_file
    dll.parent.mkdir(parents=True)
    dll.write_bytes(body)
    manifest = {"whistle_engine": contract}
    records = [
        {
            "path": package_file,
            "distribution": "cactus-needle",
            "version": "3.1.0",
            "license": "Apache-2.0",
            "bytes": len(body),
            "sha256": digest,
        }
    ]

    assert verifier._verify_required_whistle_engine_contract(tree, manifest, records) == []

    dll.write_bytes(b"tampered-needle-dll")
    problems = verifier._verify_required_whistle_engine_contract(tree, manifest, records)
    assert any("reviewed SHA-256" in problem for problem in problems)


def test_verifier_rejects_unreviewed_caption_model_file(tmp_path: Path) -> None:
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    injected = tree / builder.WHISPER_MODEL_PAYLOAD_DIR / "network-loader.py"
    injected.parent.mkdir(parents=True)
    injected.write_bytes(b"download on first use")
    manifest_path = tree / "app-payload-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append(
        {
            "path": injected.relative_to(tree).as_posix(),
            "sha256": hashlib.sha256(injected.read_bytes()).hexdigest(),
            "bytes": injected.stat().st_size,
            "distribution": builder.WHISPER_MODEL_DISTRIBUTION,
            "version": builder.WHISPER_MODEL_REVISION,
            "license": builder.WHISPER_MODEL_LICENSE,
        }
    )
    manifest["file_count"] = len(manifest["files"])
    manifest["total_bytes"] = sum(record["bytes"] for record in manifest["files"])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    entries = [builder.AppFileEntry(**record) for record in manifest["files"]]
    (tree / "SHA256SUMS").write_text(
        builder.render_sha256sums(entries),
        encoding="utf-8",
    )
    (tree / "LICENSE-BOM.md").write_text(
        builder.render_app_license_bom(entries),
        encoding="utf-8",
    )

    result = verifier.check_app_payload_verification(tree)

    assert result.status == "FAIL"
    assert "legacy caption model bytes must not be embedded in Core" in result.detail


def test_verifier_rejects_retained_wheel_hash_mismatch(tmp_path: Path) -> None:
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    wheel = tree / builder.CIVICCAST_RETAINED_WHEEL_PATH
    wheel.write_bytes(wheel.read_bytes() + b"tampered")

    result = verifier.check_app_payload_verification(tree)

    assert result.status == "FAIL"
    assert "retained CivicCast wheel SHA-256" in result.detail


def test_release_verifier_requires_the_complete_retained_dependency_wheelhouse(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)

    result = verifier.check_app_payload_verification(
        tree,
        require_dependency_wheels=True,
    )

    assert result.status == "FAIL"
    assert "retained dependency wheelhouse is incomplete" in result.detail


@pytest.mark.parametrize("artifact", ["SHA256SUMS", "LICENSE-BOM.md"])
def test_verifier_fails_when_a_derived_trust_artifact_is_tampered(
    tmp_path: Path,
    artifact: str,
) -> None:
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    (tree / artifact).write_text("attacker-controlled\n", encoding="utf-8")

    result = verifier.check_app_payload_verification(tree)

    assert result.status == "FAIL"
    assert artifact in result.detail
    assert "does not match" in result.detail


@pytest.mark.parametrize(
    "artifact",
    ["app-payload-manifest.json", "SHA256SUMS", "LICENSE-BOM.md"],
)
def test_verifier_returns_structured_failure_for_non_utf8_trust_artifact(
    tmp_path: Path,
    artifact: str,
) -> None:
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    (tree / artifact).write_bytes(b"\xff\xfe\x00\x80")

    result = verifier.check_app_payload_verification(tree)

    assert result.status == "FAIL"
    assert artifact in result.detail
    assert "UTF-8" in result.detail


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("schema_version", 999),
        ("file_count", 999_999),
        ("total_bytes", 999_999),
        ("app_lock_sha256", "0" * 64),
    ],
)
def test_verifier_fails_when_manifest_header_is_not_the_pinned_contract(
    tmp_path: Path,
    field: str,
    bad_value: object,
) -> None:
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    manifest_path = tree / "app-payload-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = bad_value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = verifier.check_app_payload_verification(tree)

    assert result.status == "FAIL"
    assert field in result.detail


@pytest.mark.parametrize("bad_bytes", ["1", -1, True])
def test_verifier_rejects_non_integer_or_negative_record_sizes(
    tmp_path: Path,
    bad_bytes: object,
) -> None:
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    manifest_path = tree / "app-payload-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["bytes"] = bad_bytes
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = verifier.check_app_payload_verification(tree)

    assert result.status == "FAIL"
    assert "malformed" in result.detail
    assert "bytes" in result.detail


def test_verifier_fails_when_a_file_is_tampered(tmp_path: Path) -> None:
    """NEGATIVE CONTROL: flip one byte of a shipped file; the manifest hash no
    longer matches, so verification must FAIL naming the file."""
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    target = tree / "Lib" / "site-packages" / "civiccast" / "__init__.py"
    target.write_bytes(b"print('tampered')")

    result = verifier.check_app_payload_verification(tree)

    assert result.status == "FAIL"
    assert "MISMATCH" in result.detail
    assert "civiccast/__init__.py" in result.detail


def test_verifier_fails_on_an_orphan_file(tmp_path: Path) -> None:
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    (tree / "Lib" / "site-packages" / "sneaky.py").write_bytes(b"unlisted")

    result = verifier.check_app_payload_verification(tree)

    assert result.status == "FAIL"
    assert "ORPHAN" in result.detail


def test_verifier_fails_when_the_interpreter_is_missing(tmp_path: Path) -> None:
    """A payload whose manifest omits python.exe cannot satisfy the installer's
    D3 gate -- the verifier must refuse it, not pass a non-bootable tree."""
    tree = tmp_path / "payload"
    tree.mkdir()
    (tree / "Lib" / "site-packages").mkdir(parents=True)
    (tree / "Lib" / "site-packages" / "civiccast.py").write_bytes(b"x")
    entries = [_entry("Lib/site-packages/civiccast.py", b"x", "civiccast", "1.0", "Apache-2.0")]
    manifest = builder.build_app_manifest(
        entries,
        civiccast_version="1.0",
        source_state={
            "head": "a" * 40,
            "dirty": False,
            "diff_sha256": "b" * 64,
            "status_sha256": "c" * 64,
        },
        civiccast_wheel_sha256="d" * 64,
        app_lock_sha256=builder.APP_REQUIREMENTS_SHA256,
        build_toolchain=builder.APP_BUILD_TOOLCHAIN,
    )
    (tree / "app-payload-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = verifier.check_app_payload_verification(tree)

    assert result.status == "FAIL"
    assert "INTERPRETER" in result.detail


def test_verifier_fails_on_an_unauthorized_distribution(tmp_path: Path) -> None:
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    # Rewrite the manifest to name a distribution not in the authorized set.
    manifest_path = tree / "app-payload-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][-1]["distribution"] = "totally-not-authorized"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    result = verifier.check_app_payload_verification(tree)

    assert result.status == "FAIL"
    assert "UNAUTHORIZED" in result.detail


def test_verifier_reconstructs_record_ownership_instead_of_trusting_manifest(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    evil = tree / "Lib" / "site-packages" / "evil.dll"
    evil.write_bytes(b"MZ-injected")

    manifest_path = tree / "app-payload-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append(
        {
            "path": "Lib/site-packages/evil.dll",
            "sha256": hashlib.sha256(b"MZ-injected").hexdigest(),
            "bytes": len(b"MZ-injected"),
            "distribution": "av",
            "version": "18.0.0",
            "license": "MIT",
        }
    )
    manifest["file_count"] = len(manifest["files"])
    manifest["total_bytes"] = sum(record["bytes"] for record in manifest["files"])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    entries = [builder.AppFileEntry(**record) for record in manifest["files"]]
    (tree / "SHA256SUMS").write_text(builder.render_sha256sums(entries), encoding="utf-8")
    (tree / "LICENSE-BOM.md").write_text(builder.render_app_license_bom(entries), encoding="utf-8")

    result = verifier.check_app_payload_verification(tree)

    assert result.status == "FAIL"
    assert "PROVENANCE" in result.detail
    assert "evil.dll" in result.detail


def test_forged_installed_record_and_dll_fail_against_retained_wheel(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    wheel_path = tree / "WHEELS" / "civiccast.whl"

    evil = tree / "Lib" / "site-packages" / "civiccast" / "evil.dll"
    evil.write_bytes(b"MZ-injected")
    installed_record = tree / "Lib" / "site-packages" / "civiccast-1.0.dist-info" / "RECORD"
    installed_record.write_bytes(installed_record.read_bytes() + b"civiccast/evil.dll,,\n")

    manifest_path = tree / "app-payload-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["civiccast"]["wheel_sha256"] = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
    manifest["files"] = [
        record
        for record in manifest["files"]
        if record["path"] != "Lib/site-packages/civiccast-1.0.dist-info/RECORD"
    ]
    manifest["files"].extend(
        [
            {
                "path": "Lib/site-packages/civiccast-1.0.dist-info/RECORD",
                "sha256": hashlib.sha256(installed_record.read_bytes()).hexdigest(),
                "bytes": installed_record.stat().st_size,
                "distribution": "civiccast",
                "version": "1.0",
                "license": "Apache-2.0",
            },
            {
                "path": "Lib/site-packages/civiccast/evil.dll",
                "sha256": hashlib.sha256(evil.read_bytes()).hexdigest(),
                "bytes": evil.stat().st_size,
                "distribution": "civiccast",
                "version": "1.0",
                "license": "Apache-2.0",
            },
        ]
    )
    manifest["file_count"] = len(manifest["files"])
    manifest["total_bytes"] = sum(record["bytes"] for record in manifest["files"])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    entries = [builder.AppFileEntry(**record) for record in manifest["files"]]
    (tree / "SHA256SUMS").write_text(
        builder.render_sha256sums(entries),
        encoding="utf-8",
    )
    (tree / "LICENSE-BOM.md").write_text(
        builder.render_app_license_bom(entries),
        encoding="utf-8",
    )

    result = verifier.check_app_payload_verification(tree)

    assert result.status == "FAIL"
    assert "retained CivicCast wheel" in result.detail
    assert "evil.dll" in result.detail


def test_forged_third_party_package_fails_against_retained_wheel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A self-consistent manifest rewrite must not bless replaced dependency code."""

    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    fastapi_source = b"VERSION = 'reviewed'\n"
    metadata = b"Name: fastapi\nVersion: 1.0\nLicense-Expression: MIT\n"
    record = (
        b"fastapi/__init__.py,,\nfastapi-1.0.dist-info/METADATA,,\nfastapi-1.0.dist-info/RECORD,,\n"
    )
    wheel = tree / "WHEELS" / "fastapi-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("fastapi/__init__.py", fastapi_source)
        archive.writestr("fastapi-1.0.dist-info/METADATA", metadata)
        archive.writestr("fastapi-1.0.dist-info/RECORD", record)
    wheel_hash = hashlib.sha256(wheel.read_bytes()).hexdigest()

    package = tree / "Lib" / "site-packages" / "fastapi"
    package.mkdir()
    (package / "__init__.py").write_bytes(fastapi_source)
    dist_info = tree / "Lib" / "site-packages" / "fastapi-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_bytes(metadata)
    (dist_info / "RECORD").write_bytes(record)

    fake_lock = tmp_path / "requirements-native-app.txt"
    fake_lock.write_text(
        f"fastapi==1.0 \\\n    --hash=sha256:{wheel_hash}\n",
        encoding="utf-8",
    )
    fake_lock_hash = hashlib.sha256(fake_lock.read_bytes()).hexdigest()
    monkeypatch.setattr(verifier, "APP_REQUIREMENTS_FILE", fake_lock)
    monkeypatch.setattr(verifier, "APP_REQUIREMENTS_SHA256", fake_lock_hash)

    manifest_path = tree / "app-payload-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["app_lock_sha256"] = fake_lock_hash
    additions = [
        _entry(
            "Lib/site-packages/fastapi/__init__.py",
            fastapi_source,
            "fastapi",
            "1.0",
            "MIT",
        ),
        _entry(
            "Lib/site-packages/fastapi-1.0.dist-info/METADATA",
            metadata,
            "fastapi",
            "1.0",
            "MIT",
        ),
        _entry(
            "Lib/site-packages/fastapi-1.0.dist-info/RECORD",
            record,
            "fastapi",
            "1.0",
            "MIT",
        ),
        _entry(
            "WHEELS/fastapi-1.0-py3-none-any.whl",
            wheel.read_bytes(),
            "fastapi",
            "1.0",
            "MIT",
        ),
    ]
    manifest["files"].extend(
        {
            "path": entry.path,
            "sha256": entry.sha256,
            "bytes": entry.bytes,
            "distribution": entry.distribution,
            "version": entry.version,
            "license": entry.license,
        }
        for entry in additions
    )

    # Attacker replaces FastAPI and regenerates every self-derived trust file.
    tampered = b"VERSION = 'attacker'\n"
    (package / "__init__.py").write_bytes(tampered)
    target = next(
        item
        for item in manifest["files"]
        if item["path"] == "Lib/site-packages/fastapi/__init__.py"
    )
    target["sha256"] = hashlib.sha256(tampered).hexdigest()
    target["bytes"] = len(tampered)
    manifest["file_count"] = len(manifest["files"])
    manifest["total_bytes"] = sum(item["bytes"] for item in manifest["files"])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    entries = [builder.AppFileEntry(**item) for item in manifest["files"]]
    (tree / "SHA256SUMS").write_text(
        builder.render_sha256sums(entries),
        encoding="utf-8",
    )
    (tree / "LICENSE-BOM.md").write_text(
        builder.render_app_license_bom(entries),
        encoding="utf-8",
    )

    result = verifier.check_app_payload_verification(tree)

    assert result.status == "FAIL"
    assert "retained dependency wheel" in result.detail
    assert "fastapi/__init__.py" in result.detail


# ---------------------------------------------------------------------------
# Self-hosted av wheel: authorized by build provenance, not wheel byte hash
# ---------------------------------------------------------------------------
#
# Candidate run 32822175257 (self-hosted): #30's advisory posture got the
# locally-built av wheel through the uv install step, but the pack build's
# INDEPENDENT deny-by-default provenance sweep (this module, run AFTER the
# build, from the assembled tree on disk) still required the retained
# WHEELS/av-*.whl to match the reviewed byte hash exactly -- so it failed
# with "WHEELS/av-18.0.0-cp311-abi3-win_amd64.whl is not an authorized
# retained dependency wheel" plus every one of av's installed files "named
# by no wheel RECORD" (the wheel was never authorized, so none of its
# members were ever added to the ownership map). advisory_pyav_wheel_hash
# extends the SAME advisory posture one layer deeper: on a byte-hash miss
# for `av` specifically, authorize it instead by re-asserting the two
# upstream, always-hash-verified build inputs (the PyAV sdist, the FFmpeg
# source archive) recorded in the wheel's own embedded
# FFMPEG-PROVENANCE.json against the pinned PYAV_SDIST_SHA256/BYTES and
# FFMPEG_SOURCE_SHA256/BYTES constants -- never a blind bypass.


def _write_av_wheel(
    tree: Path,
    *,
    wheel_bytes_suffix: bytes = b"",
    include_provenance: bool = True,
    pyav_sdist_sha256: str | None = None,
    pyav_sdist_bytes: int | None = None,
    source_archive_sha256: str | None = None,
    source_archive_bytes: int | None = None,
) -> tuple[Path, bytes, dict[str, object]]:
    """Build a minimal `av` retained wheel + its installed site-packages
    files. Provenance fields default to the REAL pinned upstream-input
    identity (correct); pass overrides to simulate a tampered claim.
    `wheel_bytes_suffix` perturbs the wheel's OWN bytes (hence its hash)
    without touching its provenance claim -- simulating the self-hosted
    lane's legitimately-different compiled bytes."""

    av_source = b"# av wrapper module (fixture)\n"
    provenance = {
        "schema_version": 1,
        "component": "FFmpeg",
        "build": verifier.ffmpeg_provenance()["build"],
        "security_patches": verifier.ffmpeg_provenance()["security_patches"],
        "source_archive_sha256": (
            source_archive_sha256
            if source_archive_sha256 is not None
            else verifier.ffmpeg_provenance()["source_archive_sha256"]
        ),
        "source_archive_bytes": (
            source_archive_bytes
            if source_archive_bytes is not None
            else verifier.ffmpeg_provenance()["source_archive_bytes"]
        ),
        "pyav_sdist_sha256": (
            pyav_sdist_sha256
            if pyav_sdist_sha256 is not None
            else verifier.ffmpeg_provenance()["pyav_sdist_sha256"]
        ),
        "pyav_sdist_bytes": (
            pyav_sdist_bytes
            if pyav_sdist_bytes is not None
            else verifier.ffmpeg_provenance()["pyav_sdist_bytes"]
        ),
    }
    provenance_bytes = json.dumps(provenance).encode("utf-8")
    record = (
        b"av/__init__.py,,\n"
        b"av-18.0.0.dist-info/FFMPEG-PROVENANCE.json,,\n"
        b"av-18.0.0.dist-info/RECORD,,\n"
    )

    wheel = tree / "WHEELS" / "av-18.0.0-cp311-abi3-win_amd64.whl"
    wheel.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("av/__init__.py", av_source)
        if include_provenance:
            archive.writestr("av-18.0.0.dist-info/FFMPEG-PROVENANCE.json", provenance_bytes)
        archive.writestr("av-18.0.0.dist-info/RECORD", record)
    if wheel_bytes_suffix:
        # Perturb the WHEEL FILE's own bytes (its hash) without touching
        # any archive member -- the self-hosted "legitimately different
        # compiled bytes" scenario. Appended after the zip's own end-of-
        # central-directory record, so the archive itself still opens fine
        # (zipfile reads from the end), matching how a real MSVC-embedded
        # build-path/PDB-path difference changes the wheel's bytes without
        # changing what verify_native_app_payload.py's member walk sees.
        wheel.write_bytes(wheel.read_bytes() + wheel_bytes_suffix)

    package = tree / "Lib" / "site-packages" / "av"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_bytes(av_source)
    dist_info = tree / "Lib" / "site-packages" / "av-18.0.0.dist-info"
    dist_info.mkdir(parents=True, exist_ok=True)
    if include_provenance:
        (dist_info / "FFMPEG-PROVENANCE.json").write_bytes(provenance_bytes)
    (dist_info / "RECORD").write_bytes(record)

    return wheel, av_source, provenance


def _fake_lock_with_wrong_av_hash(tmp_path: Path, *, version: str = "18.0.0") -> tuple[Path, str]:
    """A reviewed lock pinning av to a hash that will NOT match whatever
    wheel bytes the test constructs -- simulating the self-hosted lane's
    legitimately-different compiled wheel."""
    fake_lock = tmp_path / "requirements-native-app.txt"
    wrong_hash = "0" * 64
    fake_lock.write_text(
        f"av=={version} \\\n    --hash=sha256:{wrong_hash}\n",
        encoding="utf-8",
    )
    return fake_lock, hashlib.sha256(fake_lock.read_bytes()).hexdigest()


def _finish_av_manifest(
    tree: Path,
    wheel: Path,
    av_source: bytes,
    provenance: dict[str, object],
    *,
    include_provenance: bool,
    fake_lock_hash: str,
) -> None:
    manifest_path = tree / "app-payload-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["app_lock_sha256"] = fake_lock_hash
    record_bytes = (
        b"av/__init__.py,,\n"
        b"av-18.0.0.dist-info/FFMPEG-PROVENANCE.json,,\n"
        b"av-18.0.0.dist-info/RECORD,,\n"
    )
    additions = [
        _entry("Lib/site-packages/av/__init__.py", av_source, "av", "18.0.0", "BSD-3-Clause"),
        _entry(
            "Lib/site-packages/av-18.0.0.dist-info/RECORD",
            record_bytes,
            "av",
            "18.0.0",
            "BSD-3-Clause",
        ),
        _entry(
            "WHEELS/av-18.0.0-cp311-abi3-win_amd64.whl",
            wheel.read_bytes(),
            "av",
            "18.0.0",
            "BSD-3-Clause",
        ),
    ]
    if include_provenance:
        # A path license_for_payload_path() classifies as EMBEDDED_FFMPEG_
        # LICENSE (LGPL) records the FFmpeg component's OWN build identity
        # via component_version_for_payload_path() -- "611cec792a-minimal-
        # msvc" (EMBEDDED_FFMPEG_BUILD in civiccast/native/app_payload.py),
        # never PyAV's own "18.0.0" -- matching the house convention already
        # used elsewhere in this file (see test_pyav_wrapper_and_embedded_
        # ffmpeg_files_have_distinct_licenses's literal).
        additions.append(
            _entry(
                "Lib/site-packages/av-18.0.0.dist-info/FFMPEG-PROVENANCE.json",
                json.dumps(provenance).encode("utf-8"),
                "av",
                "611cec792a-security1-minimal-msvc",
                "LGPL-2.1-or-later",
            )
        )
    manifest["files"].extend(
        {
            "path": entry.path,
            "sha256": entry.sha256,
            "bytes": entry.bytes,
            "distribution": entry.distribution,
            "version": entry.version,
            "license": entry.license,
        }
        for entry in additions
    )
    manifest["file_count"] = len(manifest["files"])
    manifest["total_bytes"] = sum(item["bytes"] for item in manifest["files"])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    entries = [builder.AppFileEntry(**item) for item in manifest["files"]]
    (tree / "SHA256SUMS").write_text(builder.render_sha256sums(entries), encoding="utf-8")
    (tree / "LICENSE-BOM.md").write_text(builder.render_app_license_bom(entries), encoding="utf-8")


def test_advisory_pyav_wheel_hash_authorizes_a_build_provenance_matching_av_wheel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact candidate-run-32822175257 shape: a self-hosted-compiled av
    wheel whose bytes legitimately do not match the reviewed pin, but whose
    embedded FFMPEG-PROVENANCE.json correctly names the pinned, hash-
    verified PyAV sdist and FFmpeg source it was built from -- authorized."""
    tree = tmp_path / "payload"
    monkeypatch.setattr(verifier, "OCR_FILES", {})
    # The synthetic runtime isolates generic/PyAV checks; dedicated runtime tests
    # exercise the independent pinned provenance and original binary checks.
    monkeypatch.setattr(verifier, "verify_runtime_files", lambda *a, **kw: [])
    _write_minimal_payload(tree)
    wheel, av_source, provenance = _write_av_wheel(tree, wheel_bytes_suffix=b"self-hosted-build")
    fake_lock, fake_lock_hash = _fake_lock_with_wrong_av_hash(tmp_path)
    monkeypatch.setattr(verifier, "APP_REQUIREMENTS_FILE", fake_lock)
    monkeypatch.setattr(verifier, "APP_REQUIREMENTS_SHA256", fake_lock_hash)
    _finish_av_manifest(
        tree, wheel, av_source, provenance, include_provenance=True, fake_lock_hash=fake_lock_hash
    )

    result = verifier.check_app_payload_verification(tree, advisory_pyav_wheel_hash=True)

    assert result.status == "PASS", result.detail


@pytest.mark.parametrize("tamper", ["missing", "hash", "paths", "order", "build"])
def test_pyav_provenance_refuses_missing_or_changed_security_backports(
    tmp_path: Path, tamper: str
) -> None:
    provenance = verifier.ffmpeg_provenance()
    if tamper == "missing":
        provenance.pop("security_patches")
    elif tamper == "hash":
        provenance["security_patches"][0]["sha256"] = "0" * 64
    elif tamper == "paths":
        provenance["security_patches"][0]["included_paths"] = []
    elif tamper == "order":
        provenance["security_patches"].reverse()
    else:
        provenance["build"] = "unpatched-minimal-msvc"
    wheel = tmp_path / "av.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("av-18.0.0.dist-info/FFMPEG-PROVENANCE.json", json.dumps(provenance))
    problems = verifier._pyav_build_provenance_problems(wheel, version="18.0.0")
    assert problems
    assert any(
        ("build=" if tamper == "build" else "security_patches=") in item for item in problems
    )


def test_pyav_wheel_hash_mismatch_still_fails_without_the_advisory_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hosted lane's default (advisory_pyav_wheel_hash unset): the SAME
    wheel that authorizes under advisory=True must still fail outright --
    hosted-lane behavior is unchanged by this parameter existing."""
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    wheel, av_source, provenance = _write_av_wheel(tree, wheel_bytes_suffix=b"self-hosted-build")
    fake_lock, fake_lock_hash = _fake_lock_with_wrong_av_hash(tmp_path)
    monkeypatch.setattr(verifier, "APP_REQUIREMENTS_FILE", fake_lock)
    monkeypatch.setattr(verifier, "APP_REQUIREMENTS_SHA256", fake_lock_hash)
    _finish_av_manifest(
        tree, wheel, av_source, provenance, include_provenance=True, fake_lock_hash=fake_lock_hash
    )

    result = verifier.check_app_payload_verification(
        tree
    )  # advisory_pyav_wheel_hash defaults False

    assert result.status == "FAIL"
    assert "not an authorized retained dependency wheel" in result.detail


def test_advisory_pyav_wheel_hash_still_rejects_a_wrong_version_pin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build provenance authorizes a byte-hash MISS, never a version miss:
    av's own name/version pin against the reviewed lock stays a hard
    failure even in advisory mode."""
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    wheel, av_source, provenance = _write_av_wheel(tree)
    fake_lock, fake_lock_hash = _fake_lock_with_wrong_av_hash(tmp_path, version="17.0.0")
    monkeypatch.setattr(verifier, "APP_REQUIREMENTS_FILE", fake_lock)
    monkeypatch.setattr(verifier, "APP_REQUIREMENTS_SHA256", fake_lock_hash)
    _finish_av_manifest(
        tree, wheel, av_source, provenance, include_provenance=True, fake_lock_hash=fake_lock_hash
    )

    result = verifier.check_app_payload_verification(tree, advisory_pyav_wheel_hash=True)

    assert result.status == "FAIL"
    assert "not an authorized retained dependency wheel" in result.detail


def test_advisory_pyav_wheel_hash_rejects_a_tampered_build_provenance_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not a blind bypass: a wheel whose embedded FFMPEG-PROVENANCE.json
    claims an upstream sdist hash that does NOT match the pinned
    PYAV_SDIST_SHA256 must still fail, even in advisory mode."""
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    wheel, av_source, provenance = _write_av_wheel(
        tree,
        wheel_bytes_suffix=b"self-hosted-build",
        pyav_sdist_sha256="f" * 64,
    )
    fake_lock, fake_lock_hash = _fake_lock_with_wrong_av_hash(tmp_path)
    monkeypatch.setattr(verifier, "APP_REQUIREMENTS_FILE", fake_lock)
    monkeypatch.setattr(verifier, "APP_REQUIREMENTS_SHA256", fake_lock_hash)
    _finish_av_manifest(
        tree, wheel, av_source, provenance, include_provenance=True, fake_lock_hash=fake_lock_hash
    )

    result = verifier.check_app_payload_verification(tree, advisory_pyav_wheel_hash=True)

    assert result.status == "FAIL"
    assert "does not match the pinned upstream build input" in result.detail
    assert "pyav_sdist_sha256" in result.detail


def test_advisory_pyav_wheel_hash_rejects_a_missing_provenance_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wheel with no FFMPEG-PROVENANCE.json at all has nothing to
    authorize it by build provenance -- must fail with a clear reason, not
    a silent pass and not a confusing generic mismatch message."""
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    wheel, av_source, provenance = _write_av_wheel(
        tree,
        wheel_bytes_suffix=b"self-hosted-build",
        include_provenance=False,
    )
    fake_lock, fake_lock_hash = _fake_lock_with_wrong_av_hash(tmp_path)
    monkeypatch.setattr(verifier, "APP_REQUIREMENTS_FILE", fake_lock)
    monkeypatch.setattr(verifier, "APP_REQUIREMENTS_SHA256", fake_lock_hash)
    _finish_av_manifest(
        tree, wheel, av_source, provenance, include_provenance=False, fake_lock_hash=fake_lock_hash
    )

    result = verifier.check_app_payload_verification(tree, advisory_pyav_wheel_hash=True)

    assert result.status == "FAIL"
    assert "has no" in result.detail
    assert "FFMPEG-PROVENANCE.json" in result.detail


def test_advisory_pyav_wheel_hash_does_not_relax_other_distributions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """advisory_pyav_wheel_hash is scoped to `av` alone: a hash-mismatched
    fastapi wheel must still fail outright even with the flag set."""
    tree = tmp_path / "payload"
    _write_minimal_payload(tree)
    fastapi_source = b"VERSION = 'reviewed'\n"
    record = b"fastapi/__init__.py,,\nfastapi-1.0.dist-info/RECORD,,\n"
    wheel = tree / "WHEELS" / "fastapi-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("fastapi/__init__.py", fastapi_source)
        archive.writestr("fastapi-1.0.dist-info/RECORD", record)

    package = tree / "Lib" / "site-packages" / "fastapi"
    package.mkdir()
    (package / "__init__.py").write_bytes(fastapi_source)
    dist_info = tree / "Lib" / "site-packages" / "fastapi-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "RECORD").write_bytes(record)

    fake_lock = tmp_path / "requirements-native-app.txt"
    fake_lock.write_text(
        f"fastapi==1.0 \\\n    --hash=sha256:{'0' * 64}\n",
        encoding="utf-8",
    )
    fake_lock_hash = hashlib.sha256(fake_lock.read_bytes()).hexdigest()
    monkeypatch.setattr(verifier, "APP_REQUIREMENTS_FILE", fake_lock)
    monkeypatch.setattr(verifier, "APP_REQUIREMENTS_SHA256", fake_lock_hash)

    manifest_path = tree / "app-payload-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    additions = [
        _entry("Lib/site-packages/fastapi/__init__.py", fastapi_source, "fastapi", "1.0", "MIT"),
        _entry("WHEELS/fastapi-1.0-py3-none-any.whl", wheel.read_bytes(), "fastapi", "1.0", "MIT"),
    ]
    manifest["files"].extend(
        {
            "path": entry.path,
            "sha256": entry.sha256,
            "bytes": entry.bytes,
            "distribution": entry.distribution,
            "version": entry.version,
            "license": entry.license,
        }
        for entry in additions
    )
    manifest["file_count"] = len(manifest["files"])
    manifest["total_bytes"] = sum(item["bytes"] for item in manifest["files"])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    entries = [builder.AppFileEntry(**item) for item in manifest["files"]]
    (tree / "SHA256SUMS").write_text(builder.render_sha256sums(entries), encoding="utf-8")
    (tree / "LICENSE-BOM.md").write_text(builder.render_app_license_bom(entries), encoding="utf-8")

    result = verifier.check_app_payload_verification(tree, advisory_pyav_wheel_hash=True)

    assert result.status == "FAIL"
    assert "not an authorized retained dependency wheel" in result.detail
    assert "fastapi" in result.detail


def test_service_host_exe_is_relocated_to_the_payload_root(tmp_path) -> None:
    """Sandbox matrix run 6 (2026-07-30): pywin32's service install MOVES
    pythonservice.exe out of site-packages/win32 into the payload root,
    mutating the D2-verified tree after install; D5 repair then restores the
    manifest tree and the registered service's binary path dangles
    (StartService error 2). The builder pre-places the exe so the installed
    tree and the manifest agree and pywin32's move is a no-op."""

    out = tmp_path / "payload"
    site_packages = out / "Lib" / "site-packages"
    (site_packages / "win32").mkdir(parents=True)
    (site_packages / "win32" / "pythonservice.exe").write_bytes(b"host-exe")

    moved = builder.normalize_pywin32_service_host_exe(out, site_packages)

    assert (out / "pythonservice.exe").read_bytes() == b"host-exe"
    assert (site_packages / "win32" / "pythonservice.exe").read_bytes() == b"host-exe", (
        "the wheel-recorded copy must REMAIN: the payload's provenance "
        "verifier requires every retained-wheel RECORD member to be present "
        "(removing it fails the build). The root copy is an additional "
        "manifest member so D5 repair preserves the service's binary path; "
        "the NSIS chain restores this one after pywin32's install-time move."
    )
    assert moved == ["Lib/site-packages/win32/pythonservice.exe -> pythonservice.exe"]


def test_service_host_relocation_is_a_noop_when_pywin32_is_absent(tmp_path) -> None:
    out = tmp_path / "payload"
    site_packages = out / "Lib" / "site-packages"
    site_packages.mkdir(parents=True)
    assert builder.normalize_pywin32_service_host_exe(out, site_packages) == []
