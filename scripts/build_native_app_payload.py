#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Build the native Windows APPLICATION payload (`slice:ws5-installer` WP-6
Part A -- the resolution of the WP-5 app-payload finding).

The media runtime closure (`build_native_runtime_closure.py`) makes the
GStreamer/FFmpeg plumbing shippable. This is its sibling for the OTHER half a
bootable install needs, so that `"$INSTDIR\\runtime\\python.exe" -m
civiccast.native.upgrade` (the installer's D3 engine invocation) can actually
run:

  1. **Interpreter.** Verify the pinned CPython 3.12 embeddable zip against its
     SHA-256 (`app_payload.INTERPRETER_SHA256`) and extract it to the payload
     root, so `python.exe` / `python312.dll` / `python3.dll` land at
     `<out>/python.exe` (== `$INSTDIR\\runtime\\python.exe` once laid). Rewrite
     the `._pth` so `Lib\\site-packages` is importable.
  2. **Dependencies.** `uv pip install --require-hashes --no-deps` the
     hash-pinned `requirements-native-app.txt` (compiled from pyproject's base
     runtime deps for cp312/windows) into `<out>/Lib/site-packages`. No network
     at INSTALL time -- the installer lays a prebuilt tree; ALL resolution and
     download happen HERE at build time.
  3. **Application.** Build the `civiccast` wheel from THIS repo at the build
     SHA and install it `--no-deps` into the same site-packages.
  4. **pywin32 DLLs.** Copy `pywin32_system32/*.dll` next to `python.exe` so the
     lazily-imported win32 modules load under the embeddable interpreter.
  5. **License + provenance gate (deny-by-default).** Every installed
     distribution must be in `app_payload.AUTHORIZED_APP_DISTRIBUTIONS`; each
     wheel's own METADATA is swept and refused if it self-reports GPL/AGPL; the
     confirmed per-distribution license comes from `APP_DISTRIBUTION_LICENSE`.
  6. **Trust artifacts.** Emit `app-payload-manifest.json` (every file: sha256,
     bytes, distribution, version, source), `SHA256SUMS`, and `LICENSE-BOM.md`
     -- the D2 root of trust that chains to the installer's Authenticode
     signature.

Refusals propagate: an unauthorized distribution, a GPL/AGPL self-report, a
hash mismatch on the interpreter, or an unmapped-license file are all halt
triggers, never soft warnings.
"""

from __future__ import annotations

import argparse
import base64
import configparser
import csv
import ctypes
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from shutil import copy2, copytree, rmtree, which
from tempfile import TemporaryDirectory, mkdtemp
from typing import Final, NoReturn

from packaging.utils import InvalidWheelFilename, parse_wheel_filename

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from civiccast.native.app_payload import (  # noqa: E402
    APP_BUILD_REQUIREMENTS_SHA256,
    APP_BUILD_TOOLCHAIN,
    APP_BUILD_TOOLCHAIN_LOCK_SHA256,
    APP_BYTECODE_POLICY_PATH,
    APP_BYTECODE_POLICY_PREFIX,
    APP_EXTERNAL_LICENSE_FILES,
    APP_MANIFEST_SCHEMA_VERSION,
    APP_REQUIREMENTS_SHA256,
    CAPTION_PACK_CONTRACT,
    CIVICCAST_CONSOLE_ENTRY_POINTS,
    CIVICCAST_CONSOLE_LAUNCHERS,
    CIVICCAST_DISTRIBUTION,
    CIVICCAST_RETAINED_WHEEL_PATH,
    EMBEDDED_FFMPEG_BINARY_LICENSE,
    EMBEDDED_FFMPEG_LICENSE,
    INTERPRETER_DISTRIBUTION,
    INTERPRETER_LICENSE,
    INTERPRETER_SHA256,
    INTERPRETER_SOURCE_URL,
    INTERPRETER_VERSION,
    INTERPRETER_ZIP_BYTES,
    MSVC_RUNTIME_DISTRIBUTION,
    MSVC_RUNTIME_FILES,
    WHISPER_MODEL_DISTRIBUTION,
    WHISPER_MODEL_FILES,
    WHISPER_MODEL_LICENSE,
    WHISPER_MODEL_PAYLOAD_DIR,
    WHISPER_MODEL_REPO,
    WHISPER_MODEL_REVISION,
    WHISTLE_ENGINE_CONTRACT,
    WHISTLE_ENGINE_VERSION,
    assert_authorized_app_distributions,
    assert_no_prohibited_declared_licenses,
    canonical_distribution_name,
    component_version_for_payload_path,
    resolve_app_license,
)
from civiccast.native.app_payload import (  # noqa: E402
    license_for_payload_path as app_payload_license_for_path,
)
from civiccast.native.whistle_assets import (  # noqa: E402
    WhistleAssetError,
    stage_engine_library,
)
from scripts.build_native_ocr import stage_offline_ocr  # noqa: E402
from scripts.build_native_pyav_wheel import (  # noqa: E402
    EXPECTED_WHEEL_BYTES as REVIEWED_PYAV_WHEEL_BYTES,
)
from scripts.build_native_pyav_wheel import (  # noqa: E402
    EXPECTED_WHEEL_SHA256 as REVIEWED_PYAV_WHEEL_SHA256,
)
from scripts.collect_source_state import collect_source_state  # noqa: E402

APP_REQUIREMENTS_FILE = ROOT / "requirements-native-app.txt"
APP_BUILD_REQUIREMENTS_FILE = ROOT / "requirements-native-app-build.txt"
PYAV_BUILDER = ROOT / "scripts" / "build_native_pyav_wheel.py"
DEFAULT_PYAV_CACHE = ROOT / "build" / "native-pyav-cache"
REVIEWED_PYAV_WHEEL_NAME = "av-18.0.0-cp311-abi3-win_amd64.whl"
DEFAULT_EXTERNAL_LICENSE_CACHE = ROOT / "build" / "native-license-cache"
DEFAULT_WHISPER_MODEL_CACHE = ROOT / "build" / "native-model-cache" / "faster-whisper-large-v3"
DEFAULT_WHISTLE_ENGINE_CACHE = ROOT / "build" / "native-whistle-cache"
#: Where the pinned interpreter zip is cached (git-ignored). The build verifies
#: its bytes against INTERPRETER_SHA256 before extracting -- a cached file with
#: the wrong hash is refused, so the cache can never poison the payload.
DEFAULT_INTERPRETER_ZIP = (
    ROOT / "build" / "native-app-cache" / "python-3.12.16-expat-2.10.0-civiccast-embed-amd64.zip"
)
HTTP_CLIENT_BACKPORT_SOURCE = (
    ROOT / "scripts" / "resources" / "cpython-3.12.16-http-client-security1.py.txt"
)

REQUIRED_RUNTIME_IMPORTS = frozenset(
    {
        "av",
        "boto3",
        "botocore",
        "civiccast",
        "ctranslate2",
        "faster_whisper",
        "huggingface_hub",
        "needle",
        "numpy",
        "onnxruntime",
        "tokenizers",
    }
)


@dataclass(frozen=True)
class AppFileEntry:
    """One file in the app payload tree, fully described."""

    path: str  # forward-slash, relative to the payload root
    sha256: str
    bytes: int
    distribution: str
    version: str
    license: str


@dataclass(frozen=True)
class VerifiedBuildToolchain:
    """Resolved executable paths after exact version and byte verification."""

    node: str
    npm: str
    python: str
    uv: str


@dataclass(frozen=True)
class ExternalLicenseArtifact:
    """A full upstream license text absent from the corresponding wheel."""

    distribution: str
    version: str
    license: str
    filename: str
    url: str
    bytes: int
    sha256: str


EXTERNAL_LICENSE_ARTIFACTS = (
    ExternalLicenseArtifact(
        distribution="ctranslate2",
        version="4.8.1",
        license="MIT",
        filename="CTranslate2-MIT.txt",
        url="https://raw.githubusercontent.com/OpenNMT/CTranslate2/v4.8.1/LICENSE",
        bytes=1_115,
        sha256="54aa79d9fe3c09e67a16dcd95b9e88676405a6ec174efda31036983cf7672ecb",
    ),
    ExternalLicenseArtifact(
        distribution="flatbuffers",
        version="25.12.19",
        license="Apache-2.0",
        filename="FlatBuffers-Apache-2.0.txt",
        url="https://raw.githubusercontent.com/google/flatbuffers/v25.12.19/LICENSE",
        bytes=11_358,
        sha256="cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
    ),
    ExternalLicenseArtifact(
        distribution="tokenizers",
        version="0.23.1",
        license="Apache-2.0",
        filename="Tokenizers-Apache-2.0.txt",
        url="https://raw.githubusercontent.com/huggingface/tokenizers/v0.23.1/LICENSE",
        bytes=11_357,
        sha256="c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4",
    ),
)


def _fail(message: str) -> NoReturn:
    raise SystemExit(f"build_native_app_payload: {message}")


# ---------------------------------------------------------------------------
# Step 1 -- interpreter
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_tree(root: Path) -> str:
    """Hash a delegated tool tree without volatile bytecode caches."""

    resolved = root.resolve()
    if not resolved.is_dir():
        _fail(f"pinned tool tree does not exist: {resolved}")
    digest = hashlib.sha256()
    files = sorted(
        path
        for path in resolved.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and path.suffix.lower() not in {".pyc", ".pyo"}
    )
    for path in files:
        relative = path.relative_to(resolved).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(path.stat().st_size).encode("ascii"))
        digest.update(b"\0")
        digest.update(_sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _verify_versioned_tool(
    name: str,
    executable: Path,
    *version_args: str,
    tree_root: Path | None = None,
) -> str:
    expected = APP_BUILD_TOOLCHAIN[name]
    resolved = executable.resolve()
    if not resolved.is_file():
        _fail(f"pinned {name} executable does not exist: {resolved}")
    actual_sha256 = _sha256_file(resolved)
    if actual_sha256 != expected["sha256"]:
        _fail(
            f"{name} executable SHA-256 {actual_sha256} != reviewed "
            f"{expected['sha256']} ({resolved})"
        )
    result = subprocess.run(
        [str(executable), *version_args],
        capture_output=True,
        text=True,
        check=True,
    )
    actual_version = f"{result.stdout}{result.stderr}".strip()
    if actual_version != expected["version"]:
        _fail(f"{name} version {actual_version!r} != reviewed {expected['version']!r}")
    expected_tree_hash = expected.get("tree_sha256")
    if expected_tree_hash is not None:
        if tree_root is None:
            _fail(f"{name} policy requires a delegated tool tree")
        actual_tree_hash = _sha256_tree(tree_root)
        if actual_tree_hash != expected_tree_hash:
            _fail(
                f"{name} delegated tree SHA-256 {actual_tree_hash} != reviewed "
                f"{expected_tree_hash} ({tree_root.resolve()})"
            )
    return str(resolved)


APP_BUILD_TOOLCHAIN_LOCK_FILE = ROOT / "native-windows-build-toolchain.lock.json"


def verify_app_build_toolchain_lock() -> str:
    """Bind the local tool identities to the reviewed acquisition recipe."""

    if not APP_BUILD_TOOLCHAIN_LOCK_FILE.is_file():
        _fail(f"build toolchain lock is missing: {APP_BUILD_TOOLCHAIN_LOCK_FILE}")
    actual = _sha256_file(APP_BUILD_TOOLCHAIN_LOCK_FILE)
    if actual != APP_BUILD_TOOLCHAIN_LOCK_SHA256:
        _fail(
            f"build toolchain lock SHA-256 {actual} != reviewed {APP_BUILD_TOOLCHAIN_LOCK_SHA256}"
        )
    return actual


def verify_app_build_toolchain() -> VerifiedBuildToolchain:
    """Resolve and pin every executable allowed to influence payload bytes.

    Resolution order: the provisioned toolchain RECEIPT first
    (``build/wp1-native-toolchain/toolchain-receipt.json`` -- exact verified
    paths written by ``provision_native_build_toolchain.py``), PATH lookup
    only when no receipt exists. Lesson (matrix-candidate build night,
    2026-07-30): plain ``which()`` resolution made the SAME machine pass at
    1AM (toolchain dir first on that shell's PATH) and fail at 3:29AM
    (system ``C:\\Program Files\\nodejs`` npm resolved instead -- different
    delegated tree, attestation correctly refused). Every sha256/version/
    tree attestation below still runs regardless of how the path was
    resolved; the receipt only removes the PATH-order ambiguity.
    """

    verify_app_build_toolchain_lock()
    node_command = npm_command = uv_command = None
    receipt_path = ROOT / "build" / "wp1-native-toolchain" / "toolchain-receipt.json"
    if receipt_path.is_file():
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("lock_sha256") != APP_BUILD_TOOLCHAIN_LOCK_SHA256:
            _fail(
                f"toolchain receipt {receipt_path} was provisioned from a different "
                "lock than this builder pins -- re-run provision_native_build_toolchain"
            )
        recorded = receipt.get("verified", {})
        node_command = recorded.get("node", {}).get("path")
        npm_command = recorded.get("npm", {}).get("path")
        uv_command = recorded.get("uv", {}).get("path")
        if not (node_command and npm_command and uv_command):
            _fail(f"toolchain receipt {receipt_path} is missing node/npm/uv paths")
    else:
        node_command = which("node.exe") or which("node")
        npm_command = which("npm.cmd") or which("npm")
        uv_command = which("uv.exe") or which("uv")
        if node_command is None or npm_command is None or uv_command is None:
            _fail("the pinned Node.js, npm, and uv build tools must all be on PATH")

    python = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    python_dll = Path(sys.base_prefix).resolve() / "python312.dll"
    verified_python = _verify_versioned_tool(
        "python",
        python,
        "--version",
        tree_root=Path(sys.base_prefix),
    )
    expected_dll = APP_BUILD_TOOLCHAIN["python312.dll"]
    if not python_dll.is_file() or _sha256_file(python_dll) != expected_dll["sha256"]:
        _fail(f"python312.dll does not match the reviewed Python 3.12.13 build ({python_dll})")

    return VerifiedBuildToolchain(
        node=_verify_versioned_tool("node", Path(node_command), "--version"),
        npm=_verify_versioned_tool(
            "npm",
            Path(npm_command),
            "--version",
            tree_root=Path(npm_command).resolve().parent / "node_modules" / "npm",
        ),
        python=verified_python,
        uv=_verify_versioned_tool("uv", Path(uv_command), "--version"),
    )


def _download_external_license(url: str, destination: Path) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "raw.githubusercontent.com":
        _fail(f"refusing external license download from unapproved URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "CivicCast-native-builder/1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        final = urllib.parse.urlparse(response.geturl())
        if final.scheme != "https" or final.hostname != "raw.githubusercontent.com":
            _fail(f"external license redirected to unapproved URL: {response.geturl()}")
        with destination.open("wb") as target:
            while chunk := response.read(1024 * 1024):
                target.write(chunk)


def place_external_license_artifacts(
    out: Path,
    *,
    cache: Path = DEFAULT_EXTERNAL_LICENSE_CACHE,
) -> dict[str, tuple[str, str, str]]:
    """Acquire missing full license texts and return their manifest attribution."""

    cache.mkdir(parents=True, exist_ok=True)
    destination_root = out / "THIRD-PARTY-LICENSES"
    destination_root.mkdir(parents=True, exist_ok=True)
    index: dict[str, tuple[str, str, str]] = {}
    for artifact in EXTERNAL_LICENSE_ARTIFACTS:
        payload_path = f"THIRD-PARTY-LICENSES/{artifact.filename}"
        expected = APP_EXTERNAL_LICENSE_FILES.get(payload_path)
        actual = (
            artifact.distribution,
            artifact.version,
            artifact.license,
            artifact.sha256,
        )
        if expected != actual:
            _fail(f"external license policy drift for {payload_path}: {actual!r} != {expected!r}")
        cached = cache / artifact.filename
        valid_cache = (
            cached.is_file()
            and cached.stat().st_size == artifact.bytes
            and _sha256_file(cached) == artifact.sha256
        )
        if not valid_cache:
            cached.unlink(missing_ok=True)
            partial = cached.with_suffix(cached.suffix + ".part")
            partial.unlink(missing_ok=True)
            try:
                _download_external_license(artifact.url, partial)
                if partial.stat().st_size != artifact.bytes:
                    _fail(
                        f"{artifact.filename} byte length {partial.stat().st_size} "
                        f"!= pinned {artifact.bytes}"
                    )
                actual_hash = _sha256_file(partial)
                if actual_hash != artifact.sha256:
                    _fail(f"{artifact.filename} SHA-256 {actual_hash} != pinned {artifact.sha256}")
                partial.replace(cached)
            finally:
                partial.unlink(missing_ok=True)
        destination = destination_root / artifact.filename
        copy2(cached, destination)
        index[payload_path] = (
            artifact.distribution,
            artifact.version,
            artifact.license,
        )
    return index


def _download_whisper_model_file(filename: str, destination: Path) -> None:
    """Download one file from the immutable reviewed model revision."""

    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        _fail("huggingface_hub is required to acquire the pinned offline caption model")
    destination.parent.mkdir(parents=True, exist_ok=True)
    downloaded = Path(
        hf_hub_download(
            repo_id=WHISPER_MODEL_REPO,
            filename=filename,
            revision=WHISPER_MODEL_REVISION,
            local_dir=str(destination.parent),
        )
    )
    if downloaded.resolve() != destination.resolve():
        copy2(downloaded, destination)


def place_whisper_model(
    out: Path,
    *,
    cache: Path = DEFAULT_WHISPER_MODEL_CACHE,
) -> dict[str, tuple[str, str, str]]:
    """Stage the exact pinned large-v3 model for offline native captions."""

    cache.mkdir(parents=True, exist_ok=True)
    destination_root = out / WHISPER_MODEL_PAYLOAD_DIR
    destination_root.mkdir(parents=True, exist_ok=True)
    index: dict[str, tuple[str, str, str]] = {}
    for filename, (expected_bytes, expected_sha256) in sorted(WHISPER_MODEL_FILES.items()):
        cached = cache / filename
        valid = (
            cached.is_file()
            and cached.stat().st_size == expected_bytes
            and _sha256_file(cached) == expected_sha256
        )
        if not valid:
            cached.unlink(missing_ok=True)
            _download_whisper_model_file(filename, cached)
        if (
            not cached.is_file()
            or cached.stat().st_size != expected_bytes
            or _sha256_file(cached) != expected_sha256
        ):
            _fail(
                f"caption model file {filename} does not match the reviewed "
                f"{WHISPER_MODEL_REVISION} identity"
            )
        destination = destination_root / filename
        copy2(cached, destination)
        payload_path = f"{WHISPER_MODEL_PAYLOAD_DIR}/{filename}"
        index[payload_path] = (
            WHISPER_MODEL_DISTRIBUTION,
            WHISPER_MODEL_REVISION,
            WHISPER_MODEL_LICENSE,
        )
    return index


def place_whistle_engine(
    site_packages: Path,
    *,
    cache: Path = DEFAULT_WHISTLE_ENGINE_CACHE,
) -> dict[str, tuple[str, str, str]]:
    """Stage the pinned Needle DLL in cactus-needle's canonical package path."""

    package_root = site_packages / "needle"
    if not package_root.is_dir():
        _fail("cactus-needle 3.1.0 is not installed in the app payload")
    destination = package_root / "libneedle3.dll"
    try:
        stage_engine_library(destination, cache=cache)
    except WhistleAssetError as exc:
        _fail(f"could not stage the pinned Whistle engine library: {exc}")
    return {
        "Lib/site-packages/needle/libneedle3.dll": (
            "cactus-needle",
            WHISTLE_ENGINE_VERSION,
            "Apache-2.0",
        )
    }


_PAYLOAD_RUNTIME_PROBE = r"""
import importlib
import json
import os
import pathlib
import tempfile
import wave

required = (
    "av",
    "boto3",
    "botocore",
    "civiccast",
    "ctranslate2",
    "faster_whisper",
    "huggingface_hub",
    "needle",
    "numpy",
    "onnxruntime",
    "tokenizers",
)
for module in required:
    importlib.import_module(module)

import av
import civiccast
with tempfile.TemporaryDirectory(prefix="cc-app-probe-") as temporary:
    wav_path = pathlib.Path(temporary) / "silence.wav"
    with wave.open(str(wav_path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * 16000)
    with av.open(str(wav_path)) as container:
        decoded_frames = sum(1 for _frame in container.decode(audio=0))

package_root = pathlib.Path(civiccast.__file__).resolve().parent
os.environ["CIVICCAST_OPERATOR_CONSOLE_DIST"] = str(
    package_root / "apps" / "portal-operator" / "dist"
)
os.environ["CIVICCAST_PUBLIC_PORTAL_DIST"] = str(
    package_root / "apps" / "portal-public" / "dist"
)
from fastapi.testclient import TestClient
from civiccast.app import create_app

deep_links = {}
with TestClient(create_app()) as client:
    for path in ("/operator/setup", "/meetings/example"):
        deep_links[path] = client.get(path, headers={"accept": "text/html"}).status_code

print(json.dumps({
    "imports": sorted(required),
    "decoded_frames": decoded_frames,
    "portal_deep_links": deep_links,
}))
"""


def assert_payload_runtime_probe(report: Mapping[str, object]) -> None:
    imports = report.get("imports")
    imported = set(imports) if isinstance(imports, list) else set()
    missing = sorted(REQUIRED_RUNTIME_IMPORTS - imported)
    if missing:
        _fail(f"payload runtime probe missing mandatory import(s): {', '.join(missing)}")
    frames = report.get("decoded_frames")
    if not isinstance(frames, int) or frames <= 0:
        _fail("payload runtime probe decoded no audio frames")
    deep_links = report.get("portal_deep_links")
    expected_links = {"/operator/setup": 200, "/meetings/example": 200}
    if deep_links != expected_links:
        _fail(f"payload runtime probe portal deep-link result {deep_links!r} != {expected_links!r}")


def _isolated_payload_runtime_probe_environment(state_root: Path) -> dict[str, str]:
    """Build a clean-station environment rooted in disposable build state.

    The embedded probe starts the complete app lifespan.  It must retain the
    build machine's ordinary runtime environment (PATH, SystemRoot, TLS
    settings, and DLL lookup), but it must never adopt an operator's database,
    CivicCast overrides, or profile-backed durable state.
    """

    profile_names = {
        "APPDATA",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
    }
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() != "DATABASE_URL"
        and not name.upper().startswith("CIVICCAST_")
        and name.upper() not in profile_names
    }

    directories = {
        "APPDATA": state_root / "app-data",
        "HOME": state_root / "profile",
        "LOCALAPPDATA": state_root / "local-app-data",
        "PROGRAMDATA": state_root / "program-data",
        "TEMP": state_root / "temp",
        "XDG_CACHE_HOME": state_root / "xdg" / "cache",
        "XDG_CONFIG_HOME": state_root / "xdg" / "config",
        "XDG_DATA_HOME": state_root / "xdg" / "data",
        "XDG_STATE_HOME": state_root / "xdg" / "state",
    }
    for directory in directories.values():
        directory.mkdir(parents=True, exist_ok=True)
    environment.update({name: str(path) for name, path in directories.items()})
    environment["USERPROFILE"] = environment["HOME"]
    environment["TMP"] = environment["TEMP"]
    environment["TMPDIR"] = environment["TEMP"]

    # These are the app factory's durable path resolvers.  Supplying fresh
    # locations makes the build-time probe explicit even if a future resolver
    # stops using LOCALAPPDATA/Path.home as its default.
    environment.update(
        {
            "CIVICCAST_CONFIG_DIR": str(state_root / "config"),
            "CIVICCAST_CONTRIBUTOR_STORE_PATH": str(state_root / "contributor-submissions.json"),
            "CIVICCAST_CONTRIBUTOR_UPLOAD_DIR": str(state_root / "contributor-uploads"),
            "CIVICCAST_EGRESS_WORK_DIR": str(state_root / "egress"),
            "CIVICCAST_MANAGED_STORAGE_DIR": str(state_root / "managed-storage"),
            "CIVICCAST_PLAYBACK_POLICY_STATE_PATH": str(state_root / "playback-policy.json"),
            "CIVICCAST_REPORTING_WORK_DIR": str(state_root / "reporting"),
            "CIVICCAST_STATION_STATE_PATH": str(state_root / "station-state.json"),
            "CIVICCAST_STATION_STORAGE_ROOT": str(state_root / "station-storage"),
            "CIVICCAST_TESTER_OPS_STATE_PATH": str(state_root / "tester-ops-state.json"),
            "CIVICCAST_UPLOAD_DIR": str(state_root / "uploads"),
            "CIVICCAST_VOD_PACKAGE_DIR": str(state_root / "vod-packages"),
        }
    )
    return environment


def run_payload_runtime_probe(out: Path) -> dict[str, object]:
    """Run the embedded smoke test against disposable, profile-isolated state."""

    python = out / "python.exe"
    with TemporaryDirectory(prefix="cc-app-runtime-probe-", dir=out.parent) as temporary:
        environment = _isolated_payload_runtime_probe_environment(Path(temporary))
        result = subprocess.run(
            [str(python), "-I", "-B", "-c", _PAYLOAD_RUNTIME_PROBE],
            cwd=out,
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )
    report = json.loads(result.stdout)
    if not isinstance(report, dict):
        _fail("payload runtime probe did not return a JSON object")
    assert_payload_runtime_probe(report)
    return report


def extract_verified_interpreter(zip_path: Path, out: Path) -> None:
    """Verify the pinned embeddable zip against its SHA-256 and size, then
    extract it to ``out`` and make `Lib\\site-packages` importable.

    A hash mismatch is a hard refusal (a poisoned cache or a tampered download
    must never reach the payload). The `._pth` rewrite is what turns the
    embeddable's isolated, site-disabled layout into one that imports the
    packages the next step installs -- without it the interpreter runs but
    cannot find `civiccast`.

    After original provenance verification, replace the stdlib HTTP client with
    the pinned security backport and enforce the independent installed ZIP pin.
    """
    if not zip_path.is_file():
        _fail(
            f"interpreter zip not found at {zip_path}. Provision the reviewed source-built "
            "archive from the local runtime-artifact-mirror; do not download a source tarball "
            "as an embeddable archive."
        )
    actual = _sha256_file(zip_path)
    if actual != INTERPRETER_SHA256:
        _fail(
            f"interpreter zip SHA-256 mismatch: expected {INTERPRETER_SHA256}, "
            f"got {actual}. Refusing to build on an unpinned interpreter."
        )
    size = zip_path.stat().st_size
    if size != INTERPRETER_ZIP_BYTES:
        _fail(f"interpreter zip size mismatch: expected {INTERPRETER_ZIP_BYTES}, got {size}")

    from civiccast.native.python_runtime import (
        STDLIB_PATH,
        patch_stdlib_http_client,
        verify_runtime_files,
    )

    with zipfile.ZipFile(zip_path) as archive:
        problems = verify_runtime_files(archive.read)
        if problems:
            _fail("interpreter provenance: " + "; ".join(problems))
        # Validate the reviewed backport before writing any extracted files.
        # Keep the original provenance and PSF LICENSE.txt unchanged; installed
        # verification independently pins the sole approved stdlib replacement.
        try:
            patched_stdlib = patch_stdlib_http_client(
                archive.read(STDLIB_PATH), HTTP_CLIENT_BACKPORT_SOURCE.read_bytes()
            )
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            _fail(f"interpreter HTTP client security backport: {exc}")
        archive.extractall(out)
    (out / STDLIB_PATH).write_bytes(patched_stdlib)

    # Make the site-packages we are about to populate importable. The default
    # embeddable `._pth` lists `python312.zip` and `.` and leaves `import site`
    # commented out; add the site-packages dir and enable site so pip metadata
    # and .pth files (pywin32.pth) are processed.
    pth = out / f"python{INTERPRETER_VERSION.rsplit('.', 1)[0].replace('.', '')}._pth"
    if not pth.is_file():
        # Fall back to whatever python3XX._pth the zip shipped.
        candidates = sorted(out.glob("python*._pth"))
        if not candidates:
            _fail("no python*._pth found in the extracted interpreter")
        pth = candidates[0]
    lines = pth.read_text(encoding="utf-8").splitlines()
    if "Lib\\site-packages" not in lines:
        lines.append("Lib\\site-packages")
    lines = ["import site" if line.strip() == "#import site" else line for line in lines]
    if "import site" not in [line.strip() for line in lines]:
        lines.append("import site")
    pth.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    (out / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)
    problems = verify_runtime_files(lambda name: (out / name).read_bytes(), installed=True)
    if problems:
        _fail("installed interpreter provenance: " + "; ".join(problems))


# ---------------------------------------------------------------------------
# Step 2 + 3 -- dependencies + the civiccast wheel
# ---------------------------------------------------------------------------


def _requirements_lock_without_av(lock_text: str) -> str:
    """Return ``lock_text`` with the ``av==...`` requirement entry removed.

    Shared by the download step (which fetches every OTHER pinned wheel from
    the original hash lock, av retained separately) and, on the self-hosted
    lane, the install step (which must not re-enforce the hosted-reference
    hash against a self-hosted-compiled wheel that legitimately has different
    bytes -- see docs/process/pyav-wheel-reproducibility.md).
    """
    filtered = re.sub(
        r"(?ms)^av==.*?(?=^[A-Za-z0-9][A-Za-z0-9._-]*==|\Z)",
        "",
        lock_text,
        count=1,
    )
    if filtered == lock_text:
        _fail("the app requirements lock has no av requirement to replace")
    return filtered


def install_pinned_dependencies(
    site_packages: Path,
    *,
    wheelhouse: Path,
    uv_executable: str,
    advisory_pyav_wheel_hash: bool = False,
) -> None:
    """`uv pip install --require-hashes --no-deps` the pinned app requirements
    into ``site_packages`` for cp312/windows.

    `--no-deps` + `--require-hashes` mean exactly the locked set installs, each
    verified against its hash -- the same D1 posture the closure uses. Cross-
    compiled for cp312/windows so the tree is correct regardless of the build
    host's own interpreter.

    ``advisory_pyav_wheel_hash`` must be set whenever the ``av`` wheel in
    ``wheelhouse`` may legitimately NOT match `requirements-native-app.txt`'s
    hash-pinned ``av==18.0.0`` entry -- i.e. whenever
    ``build_reviewed_pyav_wheel`` compiled it on this lane with
    ``--advisory-wheel-hash`` rather than reusing the byte-exact reviewed
    artifact. Passing the SAME advisory posture that gated the build's own
    byte-exact check through to install is required: a wheel the build step
    already accepted with only a warning must not then hard-fail here against
    the identical reference hash. When set, ``av`` installs from the
    wheelhouse by its verified-unique filename with no hash check of its own
    (its provenance is already hash-verified upstream -- the pinned uv, FFmpeg
    source, MSYS2 base, and PyAV sdist downloads stay a hard failure on every
    lane, per ``build_native_pyav_wheel.py``), while every OTHER dependency
    still installs `--require-hashes` against the unmodified reviewed lock.
    When unset (the hosted lane's default), behavior is byte-identical to
    before this parameter existed: a single `--require-hashes` install of the
    full lock, including av.
    """
    if not APP_REQUIREMENTS_FILE.is_file():
        _fail(f"{APP_REQUIREMENTS_FILE} does not exist")
    lock_text = APP_REQUIREMENTS_FILE.read_text(encoding="utf-8")
    if "--hash=" not in lock_text:
        _fail(f"{APP_REQUIREMENTS_FILE} has no --hash lines (refusing an unauthenticated lock)")
    actual_lock_hash = _sha256_file(APP_REQUIREMENTS_FILE)
    if actual_lock_hash != APP_REQUIREMENTS_SHA256:
        _fail(
            f"{APP_REQUIREMENTS_FILE} SHA-256 {actual_lock_hash} "
            f"!= reviewed {APP_REQUIREMENTS_SHA256}"
        )
    pyav_wheels = sorted(wheelhouse.glob("av-18.0.0-*.whl"))
    if len(pyav_wheels) != 1:
        _fail(
            "the reviewed PyAV wheelhouse must contain exactly one "
            f"av-18.0.0 wheel; found {len(pyav_wheels)} in {wheelhouse}"
        )

    if not advisory_pyav_wheel_hash:
        subprocess.run(
            [
                uv_executable,
                "pip",
                "install",
                "--require-hashes",
                "--no-deps",
                "--no-index",
                "--python-version",
                "3.12",
                "--python-platform",
                "windows",
                "--find-links",
                str(wheelhouse),
                "--target",
                str(site_packages),
                "-r",
                str(APP_REQUIREMENTS_FILE),
            ],
            check=True,
        )
        return

    filtered_lock = wheelhouse / ".requirements-without-reviewed-pyav-install.txt"
    filtered_lock.write_text(_requirements_lock_without_av(lock_text), encoding="utf-8")
    try:
        subprocess.run(
            [
                uv_executable,
                "pip",
                "install",
                "--require-hashes",
                "--no-deps",
                "--no-index",
                "--python-version",
                "3.12",
                "--python-platform",
                "windows",
                "--find-links",
                str(wheelhouse),
                "--target",
                str(site_packages),
                "-r",
                str(filtered_lock),
            ],
            check=True,
        )
    finally:
        filtered_lock.unlink(missing_ok=True)

    # av installs by exact path, not by name+hash: its bytes are this same
    # build's own freshly-compiled output (uniqueness already verified above),
    # so there is nothing a hash check would add that the wheelhouse glob and
    # the upstream pinned-download verification haven't already covered.
    subprocess.run(
        [
            uv_executable,
            "pip",
            "install",
            "--no-deps",
            "--no-index",
            "--python-version",
            "3.12",
            "--python-platform",
            "windows",
            "--find-links",
            str(wheelhouse),
            "--target",
            str(site_packages),
            str(pyav_wheels[0]),
        ],
        check=True,
    )


def download_pinned_dependency_wheels(
    destination: Path,
    *,
    pyav_wheelhouse: Path,
    python_executable: str,
) -> None:
    """Retain the exact hash-authorized wheels used to construct the payload."""

    destination.mkdir(parents=True, exist_ok=True)
    pyav_wheels = sorted(pyav_wheelhouse.glob("av-18.0.0-*.whl"))
    if len(pyav_wheels) != 1:
        _fail(
            "the reviewed PyAV wheelhouse must contain exactly one "
            f"av-18.0.0 wheel; found {len(pyav_wheels)} in {pyav_wheelhouse}"
        )
    copy2(pyav_wheels[0], destination / pyav_wheels[0].name)

    # pip chooses the index candidate before checking whether its digest is
    # authorized, so a local same-version PyAV wheel cannot override PyPI via
    # --find-links. Retain the reviewed PyAV wheel explicitly, and download the
    # rest from the original hash lock with only that one requirement removed.
    lock_text = APP_REQUIREMENTS_FILE.read_text(encoding="utf-8")
    filtered = _requirements_lock_without_av(lock_text)
    filtered_lock = destination / ".requirements-without-reviewed-pyav.txt"
    filtered_lock.write_text(filtered, encoding="utf-8")
    try:
        subprocess.run(
            [
                python_executable,
                "-m",
                "pip",
                "download",
                "--require-hashes",
                "--only-binary=:all:",
                "--no-deps",
                "--dest",
                str(destination),
                "-r",
                str(filtered_lock),
            ],
            check=True,
            cwd=ROOT,
        )
    finally:
        filtered_lock.unlink(missing_ok=True)


def build_reviewed_pyav_wheel(
    scratch: Path,
    *,
    python_executable: str,
    uv_executable: str,
    advisory_wheel_hash: bool = False,
) -> Path:
    """Build the exact LGPL-only PyAV candidate authorized by the app lock."""

    if not PYAV_BUILDER.is_file():
        _fail(f"reviewed PyAV builder is missing: {PYAV_BUILDER}")
    wheelhouse = scratch / "pyav-wheelhouse"
    environment = dict(os.environ)
    environment["CIVICCAST_UV_EXE"] = uv_executable
    command = [
        python_executable,
        str(PYAV_BUILDER),
        "--output-dir",
        str(wheelhouse),
        "--cache-dir",
        str(DEFAULT_PYAV_CACHE),
        "--scratch",
        str(scratch / "pyav-build"),
    ]
    if advisory_wheel_hash:
        command.append("--advisory-wheel-hash")
    subprocess.run(
        command,
        cwd=ROOT,
        check=True,
        env=environment,
    )
    return wheelhouse


def prepare_reviewed_pyav_wheel(
    scratch: Path,
    *,
    reviewed_wheel: Path | None,
    python_executable: str,
    uv_executable: str,
    advisory_wheel_hash: bool = False,
) -> Path:
    """Build PyAV or stage an independently reproduced exact wheel.

    The optional artifact path avoids recompiling the same FFmpeg/PyAV source
    for every independent application-payload build. Reuse is fail-closed:
    only the exact reviewed filename, byte length, and SHA-256 are accepted,
    and the artifact is copied into the build's private wheelhouse before use.
    """

    if reviewed_wheel is None:
        return build_reviewed_pyav_wheel(
            scratch,
            python_executable=python_executable,
            uv_executable=uv_executable,
            advisory_wheel_hash=advisory_wheel_hash,
        )

    source = reviewed_wheel.resolve()
    if not source.is_file():
        _fail(f"reviewed PyAV wheel does not exist: {source}")
    if source.name != REVIEWED_PYAV_WHEEL_NAME:
        _fail(f"reviewed PyAV wheel must be named {REVIEWED_PYAV_WHEEL_NAME}; got {source.name}")
    actual_bytes = source.stat().st_size
    if actual_bytes != REVIEWED_PYAV_WHEEL_BYTES:
        _fail(
            f"reviewed PyAV wheel byte length {actual_bytes} "
            f"!= expected {REVIEWED_PYAV_WHEEL_BYTES}"
        )
    actual_sha256 = _sha256_file(source)
    if actual_sha256 != REVIEWED_PYAV_WHEEL_SHA256:
        _fail(
            f"reviewed PyAV wheel SHA-256 {actual_sha256} != expected {REVIEWED_PYAV_WHEEL_SHA256}"
        )

    wheelhouse = scratch / "pyav-wheelhouse"
    if wheelhouse.exists() and any(wheelhouse.iterdir()):
        _fail(f"reviewed PyAV wheelhouse is not empty: {wheelhouse}")
    wheelhouse.mkdir(parents=True, exist_ok=True)
    destination = wheelhouse / source.name
    if destination.resolve() == source:
        _fail("reviewed PyAV source must be outside the private build wheelhouse")
    copy2(source, destination)
    return wheelhouse


def _prepare_civiccast_source_snapshot(
    scratch: Path,
    *,
    npm_executable: str,
) -> Path:
    """Copy build inputs to scratch and compile both portal distributions there."""

    snapshot = scratch / "app-source"
    if snapshot.exists():
        _fail(f"source snapshot path already exists: {snapshot}")
    snapshot.mkdir(parents=True)
    for filename in ("pyproject.toml", "README.md", "LICENSE-CODE"):
        copy2(ROOT / filename, snapshot / filename)

    ignored_names = {
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".vite",
        "__pycache__",
        "dist",
        "node_modules",
        "playwright-report",
        "target",
        "test-results",
    }

    def ignore_generated(_directory: str, names: list[str]) -> set[str]:
        return {name for name in names if name in ignored_names or name.endswith((".pyc", ".pyo"))}

    copytree(ROOT / "civiccast", snapshot / "civiccast", ignore=ignore_generated)
    for portal in ("portal-operator", "portal-public"):
        portal_root = snapshot / "civiccast" / "apps" / portal
        subprocess.run(
            [npm_executable, "ci", "--no-audit", "--no-fund"],
            cwd=portal_root,
            check=True,
        )
        subprocess.run(
            [npm_executable, "run", "build"],
            cwd=portal_root,
            check=True,
        )
        if not (portal_root / "dist" / "index.html").is_file():
            _fail(f"{portal} build produced no dist/index.html")
    return snapshot


_APP_SHELL_TARGETS: Final[tuple[str, ...]] = (
    "android-mobile",
    "android-tv",
    "fire-tv",
    "ios-ipados",
    "roku",
    "tvos",
    "web-pwa",
)
_APP_SHELL_ROOT: Final[str] = "civiccast/apps/app-platform-shells"
_APP_SHELL_RUNTIME_FILES: Final[frozenset[str]] = frozenset(
    {
        f"{_APP_SHELL_ROOT}/scripts/build-targets.mjs",
        f"{_APP_SHELL_ROOT}/src/shell.css",
        f"{_APP_SHELL_ROOT}/src/shell.mjs",
        f"{_APP_SHELL_ROOT}/fixtures/station-app-config.sample.json",
        *(
            f"{_APP_SHELL_ROOT}/targets/{target}/{filename}"
            for target in _APP_SHELL_TARGETS
            for filename in ("index.html", "manifest.json")
        ),
    }
)


def assert_civiccast_wheel_layout(wheel: Path) -> None:
    """Require runtime app assets and reject every other apps-tree file."""

    required_portals = {
        "civiccast/apps/portal-operator/dist/index.html",
        "civiccast/apps/portal-public/dist/index.html",
    }
    with zipfile.ZipFile(wheel) as archive:
        names = {name.rstrip("/") for name in archive.namelist() if not name.endswith("/")}
    missing_portals = sorted(required_portals - names)
    if missing_portals:
        _fail(
            "CivicCast wheel is missing compiled portal entry point(s): "
            + ", ".join(missing_portals)
        )
    missing_shell = sorted(_APP_SHELL_RUNTIME_FILES - names)
    if missing_shell:
        _fail("CivicCast wheel is missing app shell runtime input(s): " + ", ".join(missing_shell))
    app_prefix = "civiccast/apps/"
    compiled_portal_prefixes = (
        "civiccast/apps/portal-operator/dist/",
        "civiccast/apps/portal-public/dist/",
    )
    leaked = sorted(
        name
        for name in names
        if name.startswith(app_prefix)
        and name not in _APP_SHELL_RUNTIME_FILES
        and not name.startswith(compiled_portal_prefixes)
    )
    if leaked:
        _fail("CivicCast wheel contains non-runtime app file(s): " + ", ".join(leaked[:10]))


def build_and_install_civiccast_wheel(
    site_packages: Path,
    scratch: Path,
    *,
    toolchain: VerifiedBuildToolchain,
    retained_wheel: Path | None = None,
) -> tuple[str, str]:
    """Build the `civiccast` wheel from THIS repo and install it `--no-deps`
    into ``site_packages``. Returns ``(version, wheel_sha256)``.

    Built here at the build SHA (recorded in the manifest), never resolved from
    an index -- the application shipped is exactly this repo's code.
    """
    if not APP_BUILD_REQUIREMENTS_FILE.is_file():
        _fail(f"{APP_BUILD_REQUIREMENTS_FILE} does not exist")
    actual_build_lock_hash = _sha256_file(APP_BUILD_REQUIREMENTS_FILE)
    if actual_build_lock_hash != APP_BUILD_REQUIREMENTS_SHA256:
        _fail(
            f"{APP_BUILD_REQUIREMENTS_FILE} SHA-256 {actual_build_lock_hash} "
            f"!= reviewed {APP_BUILD_REQUIREMENTS_SHA256}"
        )
    build_environment = scratch / "app-build-environment"
    wheel_out = scratch / "wheel"
    if build_environment.exists() or (wheel_out.exists() and any(wheel_out.iterdir())):
        _fail("CivicCast wheel build requires fresh app-build-environment and wheel directories")
    wheel_out.mkdir(parents=True, exist_ok=True)
    source_snapshot = _prepare_civiccast_source_snapshot(
        scratch,
        npm_executable=toolchain.npm,
    )
    subprocess.run(
        [
            toolchain.uv,
            "venv",
            str(build_environment),
            "--python",
            toolchain.python,
        ],
        check=True,
        cwd=ROOT,
    )
    build_python = build_environment / "Scripts" / "python.exe"
    subprocess.run(
        [
            toolchain.uv,
            "pip",
            "install",
            "--python",
            str(build_python),
            "--require-hashes",
            "--no-deps",
            "-r",
            str(APP_BUILD_REQUIREMENTS_FILE),
        ],
        check=True,
        cwd=source_snapshot,
    )
    subprocess.run(
        [
            str(build_python),
            "-m",
            "hatchling",
            "build",
            "-t",
            "wheel",
            "-d",
            str(wheel_out),
        ],
        check=True,
        cwd=source_snapshot,
    )
    wheels = sorted(wheel_out.glob("civiccast-*.whl"))
    if not wheels:
        _fail("uv build produced no civiccast wheel")
    wheel = wheels[-1]
    assert_civiccast_wheel_layout(wheel)
    subprocess.run(
        [
            toolchain.uv,
            "pip",
            "install",
            "--no-deps",
            "--python-version",
            "3.12",
            "--python-platform",
            "windows",
            "--target",
            str(site_packages),
            str(wheel),
        ],
        check=True,
    )
    if retained_wheel is not None:
        retained_wheel.parent.mkdir(parents=True, exist_ok=True)
        copy2(wheel, retained_wheel)
    # civiccast-<version>-py3-none-any.whl
    return wheel.name.split("-")[1], _sha256_file(wheel)


def render_console_launcher_script(entrypoint: str) -> str:
    """Render the deterministic embedded script for a relocatable uv launcher."""

    module, separator, function = entrypoint.partition(":")
    if (
        not separator
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", module)
        or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", function)
    ):
        _fail(f"malformed console entry point: {entrypoint!r}")
    return (
        "# -*- coding: utf-8 -*-\n"
        "import sys\n"
        "sys.dont_write_bytecode = True\n"
        f"from {module} import {function}\n"
        'if __name__ == "__main__":\n'
        '    if sys.argv[0].endswith("-script.pyw"):\n'
        "        sys.argv[0] = sys.argv[0][:-11]\n"
        '    elif sys.argv[0].endswith(".exe"):\n'
        "        sys.argv[0] = sys.argv[0][:-4]\n"
        f"    sys.exit({function}())\n"
    )


def _launcher_script_zip(script: str) -> bytes:
    output = io.BytesIO()
    info = zipfile.ZipInfo("__main__.py", date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED
    info.external_attr = 0o644 << 16
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(info, script.encode("utf-8"))
    return output.getvalue()


def _pe_rcdata(path: Path, name: str) -> bytes:
    """Read one named, language-neutral uv launcher RCDATA resource."""

    if os.name != "nt":
        _fail("console-launcher PE normalization requires Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    load = kernel32.LoadLibraryExW
    load.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_uint]
    load.restype = ctypes.c_void_p
    free = kernel32.FreeLibrary
    free.argtypes = [ctypes.c_void_p]
    free.restype = ctypes.c_bool
    find = kernel32.FindResourceExW
    find.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ushort]
    find.restype = ctypes.c_void_p
    size_of = kernel32.SizeofResource
    size_of.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    size_of.restype = ctypes.c_uint
    load_resource = kernel32.LoadResource
    load_resource.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    load_resource.restype = ctypes.c_void_p
    lock_resource = kernel32.LockResource
    lock_resource.argtypes = [ctypes.c_void_p]
    lock_resource.restype = ctypes.c_void_p
    handle = load(str(path), None, 0x00000002)  # LOAD_LIBRARY_AS_DATAFILE
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        name_buffer = ctypes.create_unicode_buffer(name)
        resource = find(
            handle,
            ctypes.c_void_p(10),  # RT_RCDATA / MAKEINTRESOURCE(10)
            ctypes.cast(name_buffer, ctypes.c_void_p),
            0,
        )
        if not resource:
            raise ctypes.WinError(ctypes.get_last_error())
        data = load_resource(handle, resource)
        pointer = lock_resource(data)
        if not pointer:
            raise ctypes.WinError(ctypes.get_last_error())
        return ctypes.string_at(pointer, size_of(handle, resource))
    finally:
        free(handle)


def _discard_retained_pe_resource_data(path: Path, old: bytes, active: bytes) -> None:
    """Discard stale full RCDATA payload copies retained by newer uv stubs.

    uv reads its script with zipimport from the executable rather than
    through the resource API. With uv 0.12.13, ``UpdateResourceW`` can retain
    an old ZIP that wins zipimport's end-of-directory search.
    Discarding only complete pre-update script payloads leaves the new active
    resource untouched and avoids a broad interpreter-path substitution.
    """

    if not old or not active:
        _fail("uv launcher script resource is empty")
    try:
        with zipfile.ZipFile(io.BytesIO(old)) as archive:
            archive.read("__main__.py")
        with zipfile.ZipFile(io.BytesIO(active)) as archive:
            archive.read("__main__.py")
    except (KeyError, zipfile.BadZipFile) as exc:
        _fail(f"uv launcher script resource is malformed: {exc}")
    if old == active:
        return  # A second normalization must not erase the active script.
    if active.find(old) >= 0:
        _fail("retained uv launcher script overlaps the active resource")
    contents = bytearray(path.read_bytes())
    active_start = contents.find(active)
    if active_start < 0 or contents.find(active, active_start + 1) >= 0:
        _fail("uv launcher active script payload is missing or ambiguous")
    active_end = active_start + len(active)
    offsets: list[int] = []
    offset = 0
    while True:
        offset = contents.find(old, offset)
        if offset < 0:
            break
        if offset < active_end and offset + len(old) > active_start:
            _fail("retained uv launcher script overlaps the active resource")
        offsets.append(offset)
        offset += len(old)
    for offset in offsets:
        contents[offset : offset + len(old)] = b"\0" * len(old)
    if offsets:
        path.write_bytes(contents)


def _update_pe_rcdata(path: Path, resources: Mapping[str, bytes]) -> None:
    """Replace named RT_RCDATA resources without rebuilding the uv stub."""

    if os.name != "nt":
        _fail("console-launcher PE normalization requires Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    begin = kernel32.BeginUpdateResourceW
    begin.argtypes = [ctypes.c_wchar_p, ctypes.c_bool]
    begin.restype = ctypes.c_void_p
    update = kernel32.UpdateResourceW
    update.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ushort,
        ctypes.c_void_p,
        ctypes.c_uint,
    ]
    update.restype = ctypes.c_bool
    end = kernel32.EndUpdateResourceW
    end.argtypes = [ctypes.c_void_p, ctypes.c_bool]
    end.restype = ctypes.c_bool

    handle = begin(str(path), False)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        for name, data in resources.items():
            name_buffer = ctypes.create_unicode_buffer(name)
            data_buffer = ctypes.create_string_buffer(data)
            ok = update(
                handle,
                ctypes.c_void_p(10),  # RT_RCDATA / MAKEINTRESOURCE(10)
                ctypes.cast(name_buffer, ctypes.c_void_p),
                0,
                ctypes.cast(data_buffer, ctypes.c_void_p),
                len(data),
            )
            if not ok:
                raise ctypes.WinError(ctypes.get_last_error())
    except BaseException:
        end(handle, True)
        raise
    if not end(handle, False):
        raise ctypes.WinError(ctypes.get_last_error())


def normalize_console_launcher(launcher: Path, entrypoint: str) -> None:
    """Make a uv console trampoline relocatable and bytecode-non-mutating."""

    if not launcher.is_file():
        _fail(f"generated console launcher is missing: {launcher}")
    resources = {
        "UV_PYTHON_PATH": b"..\\..\\..\\python.exe",
        "UV_SCRIPT_DATA": _launcher_script_zip(render_console_launcher_script(entrypoint)),
    }
    previous_script = _pe_rcdata(launcher, "UV_SCRIPT_DATA")
    _update_pe_rcdata(launcher, resources)
    active_script = _pe_rcdata(launcher, "UV_SCRIPT_DATA")
    if active_script != resources["UV_SCRIPT_DATA"]:
        _fail("uv launcher script resource did not update")
    _discard_retained_pe_resource_data(launcher, previous_script, active_script)
    if _pe_rcdata(launcher, "UV_SCRIPT_DATA") != active_script:
        _fail("uv launcher script resource changed while clearing retained data")
    if _pe_rcdata(launcher, "UV_PYTHON_PATH") != resources["UV_PYTHON_PATH"]:
        _fail("uv launcher interpreter resource did not update")
    with zipfile.ZipFile(launcher) as archive:
        selected_script = archive.read("__main__.py")
    if selected_script != render_console_launcher_script(entrypoint).encode("utf-8"):
        _fail("uv launcher whole-file ZIP still selects a different script")


def normalize_pywin32_service_host_exe(out: Path, site_packages: Path) -> list[str]:
    """Pre-place ``pythonservice.exe`` where the service host must run from.

    pywin32's ``win32serviceutil.HandleCommandLine`` MOVES (not copies)
    ``pythonservice.exe`` from ``site-packages\\win32`` to ``sys.exec_prefix``
    on every service install, unconditionally when the source exists. That
    move MUTATES the D2-verified payload tree after installation, so the D5
    repair pass restores the manifest tree and the registered service's
    binary path is left dangling -- the service then fails to start with
    Windows error 2 (proven live, Sandbox matrix run 6, 2026-07-30:
    ``StartService FAILED 2`` against
    ``...\\runtime\\pythonservice.exe``).

    COPY, never move: the payload's own provenance verifier requires every
    member of a retained dependency wheel's RECORD to be present, so
    removing the site-packages copy fails the build (proven: "retained
    dependency wheel member is missing"). Shipping the exe at BOTH the
    wheel-recorded path and the payload root makes the root path a
    first-class manifest member, so D5 repair preserves it and the
    registered service's binary path can never dangle. pywin32's install
    still moves the site-packages copy over the root one (identical bytes);
    the NSIS chain restores the site-packages member immediately after
    registration, leaving the installed tree byte-identical to the
    manifest. Returns the payload-relative paths copied, for the build log.
    """

    copied: list[str] = []
    for name in ("pythonservice.exe", "pythonservice_d.exe"):
        source = site_packages / "win32" / name
        if not source.is_file():
            continue
        destination = out / name
        copy2(source, destination)
        copied.append(f"Lib/site-packages/win32/{name} -> {name}")
    return copied


def normalize_civiccast_console_launchers(site_packages: Path) -> None:
    for command, entrypoint in sorted(CIVICCAST_CONSOLE_ENTRY_POINTS.items()):
        normalize_console_launcher(
            site_packages / "bin" / f"{command}.exe",
            entrypoint,
        )


def strip_unreviewed_console_launchers(site_packages: Path) -> list[str]:
    """Remove dependency entry-point shims that are not product surfaces."""

    bin_dir = site_packages / "bin"
    if not bin_dir.is_dir():
        return []
    retained = {PurePosixPath(path).name for path in CIVICCAST_CONSOLE_LAUNCHERS}
    removed: list[str] = []
    for path in sorted(bin_dir.iterdir()):
        if path.is_file() and path.name not in retained:
            removed.append(path.relative_to(site_packages).as_posix())
            path.unlink()
    return removed


def normalize_runtime_bytecode_policy(site_packages: Path) -> None:
    """Disable bytecode before any executable site-packages ``.pth`` import."""

    relative = PurePosixPath(APP_BYTECODE_POLICY_PATH).relative_to(
        PurePosixPath("Lib/site-packages")
    )
    policy_file = site_packages / Path(*relative.parts)
    if not policy_file.is_file():
        _fail(f"runtime bytecode policy anchor is missing: {policy_file}")
    original = policy_file.read_bytes()
    if original.startswith(APP_BYTECODE_POLICY_PREFIX):
        return
    policy_file.write_bytes(APP_BYTECODE_POLICY_PREFIX + original)


def normalize_civiccast_install_metadata(
    site_packages: Path,
    retained_wheel: Path,
) -> None:
    """Normalize uv's target-install transform to deterministic reviewed bytes."""

    with zipfile.ZipFile(retained_wheel) as archive:
        record_names = [
            name
            for name in archive.namelist()
            if name.endswith(".dist-info/RECORD")
            and canonical_distribution_name(
                PurePosixPath(name).parts[0].removesuffix(".dist-info").rpartition("-")[0]
            )
            == CIVICCAST_DISTRIBUTION
        ]
        if len(record_names) != 1:
            _fail(
                "retained CivicCast wheel must contain exactly one CivicCast "
                f"RECORD; found {len(record_names)}"
            )
        record_name = record_names[0]
        dist_info = PurePosixPath(record_name).parts[0]
        entry_points_name = f"{dist_info}/entry_points.txt"
        if entry_points_name not in archive.namelist():
            _fail("retained CivicCast wheel has no entry_points.txt")
        entry_points = configparser.ConfigParser()
        try:
            entry_points.read_string(archive.read(entry_points_name).decode("utf-8"))
            console_scripts = dict(entry_points.items("console_scripts"))
        except (UnicodeDecodeError, configparser.Error, KeyError) as exc:
            _fail(f"retained CivicCast entry_points.txt is malformed: {type(exc).__name__}: {exc}")
        if console_scripts != CIVICCAST_CONSOLE_ENTRY_POINTS:
            _fail("retained CivicCast console entry points do not match reviewed policy")
        wheel_rows = {
            tuple(row) for row in csv.reader(archive.read(record_name).decode("utf-8").splitlines())
        }

    installed_dist_info = site_packages / dist_info
    generated_rows: set[tuple[str, ...]] = set()
    for relative, (expected_bytes, expected_sha256) in sorted(CIVICCAST_CONSOLE_LAUNCHERS.items()):
        launcher = site_packages / Path(*PurePosixPath(relative).parts)
        if (
            not launcher.is_file()
            or launcher.stat().st_size != expected_bytes
            or _sha256_file(launcher) != expected_sha256
        ):
            _fail(
                "generated CivicCast console launcher does not match reviewed "
                f"uv output: {relative}"
            )
        digest = (
            base64.urlsafe_b64encode(bytes.fromhex(expected_sha256)).decode("ascii").rstrip("=")
        )
        generated_rows.add((relative, f"sha256={digest}", str(expected_bytes)))

    for filename in ("INSTALLER", "REQUESTED", "direct_url.json", "uv_cache.json"):
        (installed_dist_info / filename).unlink(missing_ok=True)

    installed_record = installed_dist_info / "RECORD"
    with installed_record.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerows(sorted(wheel_rows | generated_rows))


def remove_uv_cache_metadata(site_packages: Path) -> None:
    """Remove uv's timestamped wheel-cache metadata and its RECORD rows.

    ``uv pip install --target`` adds ``uv_cache.json`` to locally built wheels.
    Its wall-clock timestamp changes between otherwise identical builds.  It is
    installer bookkeeping, not runtime content, so ship neither the file nor
    the row uv injected into the wheel's installed RECORD.
    """

    for cache_path in sorted(site_packages.glob("*.dist-info/uv_cache.json")):
        dist_info = cache_path.parent
        record_path = dist_info / "RECORD"
        if not record_path.is_file():
            _fail(f"uv cache metadata has no owning RECORD: {cache_path}")
        relative_cache = f"{dist_info.name}/uv_cache.json"
        with record_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        matching_rows = [row for row in rows if row and row[0] == relative_cache]
        if len(matching_rows) != 1:
            _fail(
                "uv cache metadata must have exactly one owning RECORD row: "
                f"{relative_cache} has {len(matching_rows)}"
            )
        cache_path.unlink()
        with record_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerows(row for row in rows if row[0] != relative_cache)


def remove_installer_bookkeeping(site_packages: Path) -> None:
    """Delete uv/pip installation ledgers that are not wheel runtime content.

    The payload retains the exact source wheels as its independent provenance
    authority. ``INSTALLER``, ``REQUESTED``, ``direct_url.json``, and uv's
    target lock are mutable install-session metadata, so remove both those
    files and their generated RECORD rows.
    """
    lock = site_packages / ".lock"
    if lock.is_file():
        lock.unlink()
    for dist_info in sorted(site_packages.glob("*.dist-info")):
        record_path = dist_info / "RECORD"
        if not record_path.is_file():
            continue
        with record_path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
        removed_relatives: set[str] = set()
        for filename in ("INSTALLER", "REQUESTED", "direct_url.json"):
            bookkeeping = dist_info / filename
            if bookkeeping.is_file():
                bookkeeping.unlink()
                removed_relatives.add(f"{dist_info.name}/{filename}")
        if removed_relatives:
            with record_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerows(row for row in rows if row and row[0] not in removed_relatives)


def strip_pycache(out: Path) -> None:
    """Remove every `__pycache__` directory from the payload tree.

    `.pyc` files are non-reproducible bytecode caches (they embed source mtimes),
    are named by no wheel RECORD, and are regenerated by the interpreter on first
    import at runtime. Shipping them would (a) make the manifest non-deterministic
    and (b) trip the AC7 unprovenanced-file gate. A fresh `pip install --target`
    tree has none; this strips any that a prior import (e.g. a smoke test) left
    behind, so the build always hashes a clean, source-only tree.
    """
    for cache in sorted(out.rglob("__pycache__"), reverse=True):
        if cache.is_dir():
            rmtree(cache, ignore_errors=True)


#: Directory names anywhere under the payload that are test/build artifacts, not
#: runtime content. The civiccast wheel is built by hatchling from the WORKING
#: tree (`packages = ["civiccast"]`), which sweeps in untracked local outputs —
#: notably `civiccast/apps/portal-operator/test-results/` (Playwright run output,
#: NOT committed to git). These are never imported by the product, bloat the
#: payload, and their deep attachment paths blow past Windows MAX_PATH when the
#: tree is staged into the (longer) installer bundle path. Stripped so the
#: payload ships only runtime content. See wp6-app-payload-design.md.
_NON_RUNTIME_ARTIFACT_DIRS = frozenset(
    {
        "test-results",
        "playwright-report",
        "node_modules",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".vite",
    }
)


def _long_path(path: Path) -> str:
    """Windows extended-length (`\\\\?\\`) form so rmtree survives MAX_PATH."""
    resolved = str(path.resolve())
    if os.name == "nt" and not resolved.startswith("\\\\?\\"):
        return "\\\\?\\" + resolved
    return resolved


def strip_non_runtime_artifacts(out: Path) -> list[str]:
    """Remove test/build-artifact directories (`_NON_RUNTIME_ARTIFACT_DIRS`)
    from the payload tree. Returns the payload-relative paths removed.

    Uses the extended-length path form for deletion so the deep Playwright
    attachment paths (which overflow MAX_PATH) can actually be removed.
    """
    removed: list[str] = []
    for name in _NON_RUNTIME_ARTIFACT_DIRS:
        for target in sorted(out.rglob(name), reverse=True):
            if target.is_dir():
                removed.append(target.relative_to(out).as_posix())
                rmtree(_long_path(target), ignore_errors=True)
    return removed


def place_pywin32_dlls(out: Path, site_packages: Path) -> list[str]:
    """Copy `pywin32_system32/*.dll` next to `python.exe` so the lazily-imported
    win32 modules (win32event/win32security/win32job/... used in
    `civiccast.native`) load under the embeddable interpreter.

    Returns the payload-relative destination paths so the caller can attribute
    them to the `pywin32` distribution in the manifest. pywin32's own postinstall
    normally does this against a full CPython install; the embeddable has no such
    step, so the build does it explicitly.
    """
    src_dir = site_packages / "pywin32_system32"
    placed: list[str] = []
    if not src_dir.is_dir():
        return placed
    for dll in sorted(src_dir.glob("*.dll")):
        dest = out / dll.name
        copy2(dll, dest)
        placed.append(dll.name)
    return placed


def place_msvc_runtime(
    out: Path,
    source: Path,
    *,
    contract: Mapping[str, Mapping[str, str | int]] = MSVC_RUNTIME_FILES,
) -> dict[str, tuple[str, str, str]]:
    """Place the exact reviewed x64 Microsoft C++ runtime beside python.exe."""

    filename = "msvcp140.dll"
    expected = contract[filename]
    if not source.is_file():
        _fail(f"reviewed MSVCP140.dll is missing: {source}")
    actual_bytes = source.stat().st_size
    if actual_bytes != expected["bytes"]:
        _fail(f"MSVCP140.dll size {actual_bytes} != reviewed {expected['bytes']} ({source})")
    actual_sha256 = _sha256_file(source)
    if actual_sha256 != expected["sha256"]:
        _fail(f"MSVCP140.dll SHA-256 {actual_sha256} != reviewed {expected['sha256']} ({source})")
    destination = out / filename
    copy2(source, destination)
    return {
        filename: (
            MSVC_RUNTIME_DISTRIBUTION,
            str(expected["version"]),
            str(expected["license"]),
        )
    }


def locate_msvc_runtime() -> Path:
    """Locate the reviewed x64 app-local redistributable from pinned Build Tools."""

    override = os.environ.get("CIVICCAST_MSVC_RUNTIME_DLL")
    if override:
        return Path(override).resolve()
    configured_root = os.environ.get("CIVICCAST_MSVC_INSTALLATION_PATH")
    if configured_root:
        redist_root = Path(configured_root).resolve() / "VC" / "Redist" / "MSVC"
    else:
        program_files_x86 = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
        redist_root = (
            Path(program_files_x86)
            / "Microsoft Visual Studio"
            / "18"
            / "BuildTools"
            / "VC"
            / "Redist"
            / "MSVC"
        )
    candidates = sorted(redist_root.glob("*/x64/Microsoft.VC145.CRT/msvcp140.dll"))
    expected = MSVC_RUNTIME_FILES["msvcp140.dll"]
    for candidate in candidates:
        if (
            candidate.stat().st_size == expected["bytes"]
            and _sha256_file(candidate) == expected["sha256"]
        ):
            return candidate.resolve()
    _fail(
        "reviewed x64 MSVCP140.dll was not found in the pinned Visual Studio "
        "Build Tools redist tree; set CIVICCAST_MSVC_RUNTIME_DLL to its exact path"
    )


# ---------------------------------------------------------------------------
# Step 5 -- distribution index + license gate
# ---------------------------------------------------------------------------


def _distribution_of_record_path(record_path: str) -> str | None:
    """The canonical distribution name a dist-info RECORD path belongs to, or
    None for a file not under a `*.dist-info` (those are mapped from RECORD
    membership, not their own path)."""
    first = PurePosixPath(record_path.replace("\\", "/")).parts[0]
    if first.endswith(".dist-info"):
        stem = first.removesuffix(".dist-info")
        name = stem.rpartition("-")[0] or stem
        return canonical_distribution_name(name)
    return None


def build_site_packages_index(site_packages: Path) -> tuple[dict[str, tuple[str, str]], set[str]]:
    """Map every installed file (payload-relative, under Lib/site-packages) to
    its ``(canonical_distribution, version)`` by parsing each
    `*.dist-info/RECORD`, and return the set of installed canonical
    distribution names.

    A file present in site-packages but named by NO RECORD is left unmapped
    here; `hash_payload_tree` turns an unmapped file into a hard failure (AC7:
    an unprovenanced shipped file halts the build).
    """
    prefix = "Lib/site-packages"
    index: dict[str, tuple[str, str]] = {}
    distributions: set[str] = set()
    for dist_info in sorted(site_packages.glob("*.dist-info")):
        stem = dist_info.name.removesuffix(".dist-info")
        raw_name, _, version = stem.rpartition("-")
        canonical = canonical_distribution_name(raw_name or stem)
        distributions.add(canonical)
        record = dist_info / "RECORD"
        if not record.is_file():
            continue
        for line in record.read_text(encoding="utf-8", errors="replace").splitlines():
            field = line.split(",", 1)[0].strip()
            if not field:
                continue
            rel = PurePosixPath(field.replace("\\", "/"))
            # RECORD paths are relative to site-packages; ".." escapes (data
            # scripts) are ignored -- they land outside the tree we ship.
            if rel.parts and rel.parts[0] == "..":
                continue
            index[f"{prefix}/{rel.as_posix()}"] = (canonical, version)
    return index, distributions


def sweep_declared_licenses(site_packages: Path) -> dict[str, str]:
    """canonical distribution name -> the license string read from its
    `*.dist-info/METADATA` (License-Expression, else License, else the License
    classifiers joined). The cross-check input for
    `assert_no_prohibited_declared_licenses`."""
    import re

    lex_re = re.compile(r"^License-Expression:\s*(.+)$", re.MULTILINE)
    lic_re = re.compile(r"^License:\s*(.+)$", re.MULTILINE)
    cls_re = re.compile(r"^Classifier:\s*License\s*::\s*(.+)$", re.MULTILINE)
    declared: dict[str, str] = {}
    for dist_info in sorted(site_packages.glob("*.dist-info")):
        stem = dist_info.name.removesuffix(".dist-info")
        canonical = canonical_distribution_name(stem.rpartition("-")[0] or stem)
        metadata = dist_info / "METADATA"
        if not metadata.is_file():
            declared[canonical] = ""
            continue
        text = metadata.read_text(encoding="utf-8", errors="replace")
        lex = lex_re.search(text)
        lic = lic_re.search(text)
        classifiers = cls_re.findall(text)
        declared[canonical] = (
            lex.group(1).strip()
            if lex
            else (lic.group(1).strip() if lic else " AND ".join(classifiers))
        )
    return declared


def build_retained_wheel_index(
    wheel_dir: Path,
) -> dict[str, tuple[str, str, str]]:
    """Map retained third-party wheel paths to reviewed component identity."""

    index: dict[str, tuple[str, str, str]] = {}
    seen: set[str] = set()
    for wheel in sorted(wheel_dir.glob("*.whl")):
        if wheel.name == Path(CIVICCAST_RETAINED_WHEEL_PATH).name:
            continue
        try:
            raw_name, parsed_version, _build, _tags = parse_wheel_filename(wheel.name)
        except InvalidWheelFilename:
            _fail(f"retained dependency has a malformed wheel filename: {wheel.name}")
        distribution = canonical_distribution_name(str(raw_name))
        if distribution in seen:
            _fail(f"retained dependency wheelhouse has multiple wheels for {distribution}")
        seen.add(distribution)
        version = str(parsed_version)
        index[f"WHEELS/{wheel.name}"] = (
            distribution,
            version,
            license_for_payload_path(distribution, f"WHEELS/{wheel.name}"),
        )
    return index


# ---------------------------------------------------------------------------
# Step 6 -- hash the tree + trust artifacts
# ---------------------------------------------------------------------------

#: Files this script authors itself -- excluded from the manifest they describe
#: (a manifest cannot contain its own hash).
_TRUST_ARTIFACTS = frozenset({"app-payload-manifest.json", "SHA256SUMS", "LICENSE-BOM.md"})


def hash_payload_tree(
    out: Path,
    *,
    site_packages_index: Mapping[str, tuple[str, str]],
    pywin32_dlls: Sequence[str],
    civiccast_version: str,
    external_license_index: Mapping[str, tuple[str, str, str]] | None = None,
    model_index: Mapping[str, tuple[str, str, str]] | None = None,
    retained_wheel_index: Mapping[str, tuple[str, str, str]] | None = None,
    msvc_runtime_index: Mapping[str, tuple[str, str, str]] | None = None,
) -> tuple[AppFileEntry, ...]:
    """Describe every file in the payload tree as an `AppFileEntry`.

    Attribution, in order: the three trust artifacts are skipped; files under
    `Lib/site-packages` come from the RECORD index; the copied pywin32 DLLs are
    attributed to `pywin32`; everything else at the root is the interpreter.
    An installed file the RECORD index does not name is a hard failure (AC7).
    """
    pywin32_version = ""
    for dist, version in site_packages_index.values():
        if dist == "pywin32":
            pywin32_version = version
            break

    external_license_index = external_license_index or {}
    model_index = model_index or {}
    retained_wheel_index = retained_wheel_index or {}
    msvc_runtime_index = msvc_runtime_index or {}
    entries: list[AppFileEntry] = []
    for file_path in sorted(p for p in out.rglob("*") if p.is_file()):
        rel = file_path.relative_to(out).as_posix()
        if rel in _TRUST_ARTIFACTS:
            continue
        external_license = external_license_index.get(rel)
        reviewed_component = (
            external_license
            or model_index.get(rel)
            or retained_wheel_index.get(rel)
            or msvc_runtime_index.get(rel)
        )
        if reviewed_component is not None:
            distribution, version, license_ = reviewed_component
        elif rel == CIVICCAST_RETAINED_WHEEL_PATH:
            distribution, version = CIVICCAST_DISTRIBUTION, civiccast_version
        elif rel.startswith("Lib/site-packages/"):
            mapping = site_packages_index.get(rel)
            if mapping is None:
                _fail(
                    f"{rel} is installed but named by no dist-info RECORD "
                    "(unprovenanced file -- refusing to ship it)"
                )
            distribution, version = mapping
        elif rel in pywin32_dlls:
            distribution, version = "pywin32", pywin32_version
        else:
            distribution, version = INTERPRETER_DISTRIBUTION, INTERPRETER_VERSION

        if reviewed_component is None:
            if distribution == INTERPRETER_DISTRIBUTION:
                license_ = INTERPRETER_LICENSE
            elif distribution == CIVICCAST_DISTRIBUTION:
                license_ = resolve_app_license(CIVICCAST_DISTRIBUTION)
                version = civiccast_version
            else:
                license_ = license_for_payload_path(distribution, rel)
                version = component_version_for_payload_path(distribution, version, rel)

        entries.append(
            AppFileEntry(
                path=rel,
                sha256=_sha256_file(file_path),
                bytes=file_path.stat().st_size,
                distribution=distribution,
                version=version,
                license=license_,
            )
        )
    return tuple(entries)


def license_for_payload_path(distribution: str, path: str) -> str:
    """Compatibility wrapper for callers/tests; policy lives in app_payload."""

    return app_payload_license_for_path(distribution, path)


def build_app_manifest(
    entries: Sequence[AppFileEntry],
    *,
    civiccast_version: str,
    source_state: Mapping[str, object],
    civiccast_wheel_sha256: str,
    app_lock_sha256: str,
    build_toolchain: Mapping[str, Mapping[str, str]],
) -> dict[str, object]:
    """The app-payload-manifest.json document -- sorted by path (deterministic),
    with the interpreter pin, honest source state, wheel hash, and lock hash."""
    ordered = sorted(entries, key=lambda e: e.path)
    source_identity = {
        key: source_state[key] for key in ("head", "dirty", "diff_sha256", "status_sha256")
    }
    return {
        "schema_version": APP_MANIFEST_SCHEMA_VERSION,
        "interpreter": {
            "distribution": INTERPRETER_DISTRIBUTION,
            "version": INTERPRETER_VERSION,
            "sha256": INTERPRETER_SHA256,
            "source": INTERPRETER_SOURCE_URL,
            "license": INTERPRETER_LICENSE,
        },
        "civiccast": {
            "version": civiccast_version,
            "wheel_sha256": civiccast_wheel_sha256,
            "source_state": source_identity,
        },
        "caption_pack": dict(CAPTION_PACK_CONTRACT),
        "whistle_engine": dict(WHISTLE_ENGINE_CONTRACT),
        "app_lock_sha256": app_lock_sha256,
        "app_build_lock_sha256": APP_BUILD_REQUIREMENTS_SHA256,
        "build_toolchain_lock_sha256": APP_BUILD_TOOLCHAIN_LOCK_SHA256,
        "build_toolchain": {
            name: dict(identity) for name, identity in sorted(build_toolchain.items())
        },
        "file_count": len(ordered),
        "total_bytes": sum(e.bytes for e in ordered),
        "files": [
            {
                "path": e.path,
                "sha256": e.sha256,
                "bytes": e.bytes,
                "distribution": e.distribution,
                "version": e.version,
                "license": e.license,
            }
            for e in ordered
        ],
    }


def render_sha256sums(entries: Sequence[AppFileEntry]) -> str:
    ordered = sorted(entries, key=lambda e: e.path)
    return "".join(f"{e.sha256}  {e.path}\n" for e in ordered)


def render_app_license_bom(entries: Sequence[AppFileEntry]) -> str:
    ordered = sorted(entries, key=lambda e: e.path)
    by_component: dict[tuple[str, str, str], list[AppFileEntry]] = {}
    for entry in ordered:
        component = entry.distribution
        version = entry.version
        if entry.distribution == "av" and entry.license in (
            EMBEDDED_FFMPEG_LICENSE,
            EMBEDDED_FFMPEG_BINARY_LICENSE,
        ):
            component = "av (embedded FFmpeg)"
            version = component_version_for_payload_path(
                entry.distribution,
                entry.version,
                entry.path,
            )
        by_component.setdefault((component, version, entry.license), []).append(entry)

    lines = ["# CivicCast (Native) Application Payload — License Bill of Materials", ""]
    lines.append(
        "The interpreter (CPython 3.12 embeddable, PSF-2.0), the `civiccast` "
        "application (Apache-2.0), and every hash-pinned third-party pip "
        "dependency. Deny-by-default: every distribution below is in "
        "`civiccast.native.app_payload.AUTHORIZED_APP_DISTRIBUTIONS`. No "
        "GPL/AGPL. License texts ship either in each wheel's installed tree or "
        "under `THIRD-PARTY-LICENSES`; embedded FFmpeg carries its LGPL "
        "compliance text and provenance inside the PyAV wheel."
    )
    lines.append("")
    lines.append("## Summary by distribution")
    lines.append("")
    lines.append("| Distribution | Version | License | Files | Bytes |")
    lines.append("| --- | --- | --- | --- | --- |")
    for (component, version, license_), group in sorted(by_component.items()):
        lines.append(
            f"| {component} | {version} | {license_} | {len(group)} | "
            f"{sum(e.bytes for e in group)} |"
        )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def build(
    *,
    out: Path,
    interpreter_zip: Path,
    scratch: Path,
    reviewed_pyav_wheel: Path | None = None,
    msvc_runtime: Path | None = None,
    allow_dirty_source: bool = False,
    advisory_pyav_wheel_hash: bool = False,
) -> dict[str, object]:
    source_state = collect_source_state(repo_root=ROOT)
    if source_state["dirty"] and not allow_dirty_source:
        _fail(
            "source tree is dirty; release payloads require a clean, reproducible "
            "checkout (use --allow-dirty-source only for an explicitly non-release proof build)"
        )
    if out.exists() and any(out.iterdir()):
        _fail(f"refusing to build into a non-empty output directory: {out}")
    toolchain = verify_app_build_toolchain()
    out.mkdir(parents=True, exist_ok=True)

    print(f"[1/6] Verifying + extracting the pinned interpreter ({INTERPRETER_VERSION}) ...")
    extract_verified_interpreter(interpreter_zip, out)
    site_packages = out / "Lib" / "site-packages"

    print("[2/6] Building the reviewed LGPL-only PyAV wheel ...")
    pyav_wheelhouse = prepare_reviewed_pyav_wheel(
        scratch,
        reviewed_wheel=reviewed_pyav_wheel,
        python_executable=toolchain.python,
        uv_executable=toolchain.uv,
        advisory_wheel_hash=advisory_pyav_wheel_hash,
    )
    retained_wheelhouse = out / "WHEELS"
    print("      Retaining the exact hash-pinned dependency wheels (cp312/windows) ...")
    download_pinned_dependency_wheels(
        retained_wheelhouse,
        pyav_wheelhouse=pyav_wheelhouse,
        python_executable=toolchain.python,
    )
    print("      Installing only from the retained offline dependency wheelhouse ...")
    install_pinned_dependencies(
        site_packages,
        wheelhouse=retained_wheelhouse,
        uv_executable=toolchain.uv,
        advisory_pyav_wheel_hash=advisory_pyav_wheel_hash,
    )

    print("[3/6] Building + installing the civiccast wheel ...")
    civiccast_version, civiccast_wheel_sha256 = build_and_install_civiccast_wheel(
        site_packages,
        scratch,
        toolchain=toolchain,
        retained_wheel=out / CIVICCAST_RETAINED_WHEEL_PATH,
    )
    relocated_service_host = normalize_pywin32_service_host_exe(out, site_packages)
    for relocation in relocated_service_host:
        print(f"      relocated service host exe: {relocation}")
    normalize_civiccast_console_launchers(site_packages)
    normalize_runtime_bytecode_policy(site_packages)
    stripped_launchers = strip_unreviewed_console_launchers(site_packages)
    if stripped_launchers:
        print(
            f"      stripped {len(stripped_launchers)} unreviewed dependency console launcher(s)."
        )
    normalize_civiccast_install_metadata(
        site_packages,
        out / CIVICCAST_RETAINED_WHEEL_PATH,
    )
    remove_uv_cache_metadata(site_packages)
    source_state_after_wheel = collect_source_state(repo_root=ROOT)
    for field in ("head", "dirty", "diff_sha256", "status_sha256"):
        if source_state_after_wheel[field] != source_state[field]:
            _fail(f"source state changed during CivicCast wheel build ({field})")

    print("[4/6] Placing required app-local runtime DLLs beside the interpreter ...")
    pywin32_dlls = place_pywin32_dlls(out, site_packages)
    msvc_runtime_index = place_msvc_runtime(
        out,
        msvc_runtime.resolve() if msvc_runtime is not None else locate_msvc_runtime(),
    )
    external_license_index = place_external_license_artifacts(out)
    external_license_index.update(stage_offline_ocr(out, scratch / "offline-ocr"))
    external_license_index.update(place_whistle_engine(site_packages))
    print("      Binding the required signed large-v3 caption-pack contract ...")
    runtime_report = run_payload_runtime_probe(out)
    runtime_imports = runtime_report.get("imports")
    if not isinstance(runtime_imports, list):
        _fail("payload runtime probe did not return an import list")
    print(
        f"      runtime probe imported {len(runtime_imports)} mandatory "
        f"modules and decoded {runtime_report['decoded_frames']} audio frame(s)."
    )
    remove_installer_bookkeeping(site_packages)
    strip_pycache(out)
    removed_artifacts = strip_non_runtime_artifacts(out)
    if removed_artifacts:
        print(
            f"      stripped {len(removed_artifacts)} non-runtime artifact dir(s) (test/build output)"
        )

    print("[5/6] License + provenance gate (deny-by-default) ...")
    index, distributions = build_site_packages_index(site_packages)
    # civiccast is installed too; ensure it is counted as authorized.
    distributions.add(CIVICCAST_DISTRIBUTION)
    assert_authorized_app_distributions(frozenset(distributions))
    declared = sweep_declared_licenses(site_packages)
    assert_no_prohibited_declared_licenses(declared)
    print(f"      {len(distributions)} distribution(s) authorized; no GPL/AGPL declared.")

    print("[6/6] Hashing the tree + writing trust artifacts ...")
    retained_wheel_index = build_retained_wheel_index(retained_wheelhouse)
    entries = hash_payload_tree(
        out,
        site_packages_index=index,
        pywin32_dlls=pywin32_dlls,
        civiccast_version=civiccast_version,
        external_license_index=external_license_index,
        retained_wheel_index=retained_wheel_index,
        msvc_runtime_index=msvc_runtime_index,
    )
    manifest = build_app_manifest(
        entries,
        civiccast_version=civiccast_version,
        source_state=source_state,
        civiccast_wheel_sha256=civiccast_wheel_sha256,
        app_lock_sha256=APP_REQUIREMENTS_SHA256,
        build_toolchain=APP_BUILD_TOOLCHAIN,
    )
    (out / "app-payload-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    (out / "SHA256SUMS").write_text(render_sha256sums(entries), encoding="utf-8")
    (out / "LICENSE-BOM.md").write_text(render_app_license_bom(entries), encoding="utf-8")

    print("Build complete.")
    print(f"  file_count  = {manifest['file_count']}")
    print(f"  total_bytes = {manifest['total_bytes']}")
    print(
        f"  civiccast   = {civiccast_version} wheel {civiccast_wheel_sha256} "
        f"from {source_state['head']} (dirty={source_state['dirty']})"
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the CivicCast (Native) application payload (interpreter + app + deps)."
    )
    parser.add_argument("--out", required=True, type=Path, help="output payload tree directory")
    parser.add_argument(
        "--interpreter-zip",
        type=Path,
        default=DEFAULT_INTERPRETER_ZIP,
        help=f"pinned CPython embeddable zip (default: {DEFAULT_INTERPRETER_ZIP})",
    )
    parser.add_argument(
        "--reviewed-pyav-wheel",
        type=Path,
        help=(
            "reuse the exact independently reproduced PyAV wheel instead of "
            "recompiling it; filename, size, and SHA-256 are reverified"
        ),
    )
    parser.add_argument(
        "--msvc-runtime",
        type=Path,
        help=(
            "reviewed x64 msvcp140.dll; defaults to the pinned Visual Studio "
            "Build Tools redist tree"
        ),
    )
    parser.add_argument(
        "--scratch",
        type=Path,
        default=None,
        help="scratch dir for the wheel build (default: a temp dir)",
    )
    parser.add_argument(
        "--allow-dirty-source",
        action="store_true",
        help="permit an explicitly non-release proof build from a dirty source tree",
    )
    parser.add_argument(
        "--advisory-pyav-wheel-hash",
        action="store_true",
        help=(
            "forwarded to the PyAV wheel build: log a warning instead of failing when the "
            "compiled wheel's byte-exact hash does not match the pinned reference (every "
            "pinned download still verifies strictly)"
        ),
    )
    args = parser.parse_args(argv)

    out = args.out.resolve()
    scratch = (
        args.scratch.resolve() if args.scratch is not None else Path(mkdtemp(prefix="cc-app-"))
    )
    try:
        build(
            out=out,
            interpreter_zip=args.interpreter_zip.resolve(),
            scratch=scratch,
            reviewed_pyav_wheel=(
                args.reviewed_pyav_wheel.resolve() if args.reviewed_pyav_wheel is not None else None
            ),
            msvc_runtime=(args.msvc_runtime.resolve() if args.msvc_runtime is not None else None),
            allow_dirty_source=args.allow_dirty_source,
            advisory_pyav_wheel_hash=args.advisory_pyav_wheel_hash,
        )
    finally:
        if args.scratch is None:
            rmtree(scratch, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
