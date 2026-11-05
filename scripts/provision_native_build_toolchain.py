# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Provision CivicCast's reviewed native-Windows build toolchain.

The committed lock identifies every downloaded byte and every installed tool
identity used by the native app-payload build. Portable tools are extracted
into a caller-owned directory; MSVC uses Microsoft's fixed-version Build Tools
bootstrapper and is separately verified by the PyAV builder before compiling.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Final

ROOT: Final[Path] = Path(__file__).resolve().parent.parent


class ToolchainProvisionError(RuntimeError):
    """The reviewed toolchain could not be acquired or reconstructed."""


LOCK_PATH: Final[Path] = ROOT / "native-windows-build-toolchain.lock.json"
DEFAULT_CACHE: Final[Path] = ROOT / "build" / "native-toolchain-cache"
DEFAULT_OUTPUT: Final[Path] = ROOT / "build" / "native-toolchain"
_CHUNK_BYTES: Final[int] = 1024 * 1024
_DOWNLOAD_HOSTS: Final[frozenset[str]] = frozenset(
    {
        "download.visualstudio.microsoft.com",
        "github.com",
        "nodejs.org",
        "objects.githubusercontent.com",
        "registry.npmjs.org",
        "release-assets.githubusercontent.com",
        "releases.astral.sh",
    }
)
_PORTABLE_ARTIFACTS: Final[tuple[str, ...]] = ("node", "npm", "python", "uv")
# Microsoft documents a "less than 80 characters" full installation path for
# the Visual Studio installer family, layout and install alike:
# https://learn.microsoft.com/en-us/visualstudio/install/create-an-offline-installation-of-visual-studio
_MSVC_PATH_LIMIT: Final[int] = 80


def _app_build_toolchain_policy() -> dict[str, dict[str, str]]:
    """Read the literal attestation without importing CivicCast dependencies."""

    source = ROOT / "civiccast" / "native" / "app_payload.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "APP_BUILD_TOOLCHAIN"
            and node.value is not None
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, dict):
                return value
    raise ToolchainProvisionError(f"cannot locate literal APP_BUILD_TOOLCHAIN policy in {source}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_tree(root: Path) -> str:
    resolved = root.resolve()
    if not resolved.is_dir():
        raise ToolchainProvisionError(f"tool tree does not exist: {resolved}")
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


def load_lock(path: Path = LOCK_PATH) -> dict[str, Any]:
    """Load and validate the committed toolchain lock."""

    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolchainProvisionError(f"cannot read toolchain lock {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ToolchainProvisionError("toolchain lock root must be an object")
    validate_lock(parsed)
    return parsed


def _validated_source_url(value: object) -> urllib.parse.ParseResult:
    if not isinstance(value, str):
        raise ToolchainProvisionError("artifact URL must be a string")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https":
        raise ToolchainProvisionError(f"artifact URL must use HTTPS: {value}")
    if parsed.hostname not in _DOWNLOAD_HOSTS:
        raise ToolchainProvisionError(f"artifact URL host is not approved: {value}")
    if parsed.username or parsed.password or parsed.fragment:
        raise ToolchainProvisionError(f"artifact URL contains forbidden fields: {value}")
    return parsed


def validate_lock(lock: Mapping[str, Any]) -> None:
    """Refuse incomplete, malformed, or attestation-divergent locks."""

    if lock.get("schema_version") != 1:
        raise ToolchainProvisionError("unsupported toolchain lock schema")
    if lock.get("target") != "windows-x86_64":
        raise ToolchainProvisionError("toolchain lock target must be windows-x86_64")
    artifacts = lock.get("artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {
        "node",
        "npm",
        "python",
        "uv",
        "msvc",
    }:
        raise ToolchainProvisionError("toolchain lock has an incomplete artifact set")
    if lock.get("installed_identities") != _app_build_toolchain_policy():
        raise ToolchainProvisionError(
            "toolchain lock installed identities differ from APP_BUILD_TOOLCHAIN"
        )

    for name, artifact in artifacts.items():
        if not isinstance(artifact, dict):
            raise ToolchainProvisionError(f"{name} artifact must be an object")
        _validated_source_url(artifact.get("url"))
        filename = artifact.get("filename")
        if not isinstance(filename, str) or not filename or filename != Path(filename).name:
            raise ToolchainProvisionError(f"{name} artifact filename is unsafe")
        expected_bytes = artifact.get("bytes")
        if not isinstance(expected_bytes, int) or expected_bytes <= 0:
            raise ToolchainProvisionError(f"{name} artifact size is invalid")
        expected_sha256 = artifact.get("sha256")
        if (
            not isinstance(expected_sha256, str)
            or len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)
        ):
            raise ToolchainProvisionError(f"{name} artifact SHA-256 is invalid")

    msvc = artifacts["msvc"]
    if msvc.get("version") != "18.5.2+11723.231":
        raise ToolchainProvisionError("MSVC Build Tools product version drifted")
    if msvc.get("compiler_version") != "19.50.35730":
        raise ToolchainProvisionError("MSVC compiler version drifted")
    if msvc.get("linker_version") != "14.50.35730.0":
        raise ToolchainProvisionError("MSVC linker version drifted")
    if msvc.get("cmake_version") != "4.2.3-msvc3" or msvc.get("ninja_version") != "1.12.1":
        raise ToolchainProvisionError("MSVC CMake/Ninja versions drifted")
    if msvc.get("components") != [
        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
        "Microsoft.VisualStudio.Component.VC.Redist.14.Latest",
        "Microsoft.VisualStudio.Component.Windows11SDK.26100",
        "Microsoft.VisualStudio.Component.VC.CMake.Project",
    ]:
        raise ToolchainProvisionError("MSVC component selection drifted")


def _verify_artifact(path: Path, artifact: Mapping[str, Any]) -> None:
    actual_size = path.stat().st_size
    expected_size = int(artifact["bytes"])
    if actual_size != expected_size:
        raise ToolchainProvisionError(f"{path.name} size {actual_size} != reviewed {expected_size}")
    actual_sha256 = _sha256_file(path)
    expected_sha256 = str(artifact["sha256"])
    if actual_sha256 != expected_sha256:
        raise ToolchainProvisionError(
            f"{path.name} SHA-256 {actual_sha256} != reviewed {expected_sha256}"
        )


def fetch_locked_artifact(
    name: str,
    artifact: Mapping[str, Any],
    cache: Path,
    *,
    offline: bool = False,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> Path:
    """Acquire one reviewed artifact, verifying bytes before cache admission."""

    parsed = _validated_source_url(artifact.get("url"))
    del parsed
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / str(artifact["filename"])
    if destination.exists():
        _verify_artifact(destination, artifact)
        return destination
    if offline:
        raise ToolchainProvisionError(
            f"offline cache is missing reviewed {name} artifact: {destination}"
        )

    partial = destination.with_name(f"{destination.name}.partial")
    partial.unlink(missing_ok=True)
    request = urllib.request.Request(
        str(artifact["url"]),
        headers={"User-Agent": "CivicCast-native-toolchain-provisioner/1"},
    )
    try:
        with opener(request, timeout=60) as response:
            final_url = response.geturl()
            try:
                final = _validated_source_url(final_url)
            except ToolchainProvisionError as exc:
                raise ToolchainProvisionError(
                    f"{name} download redirect refused: {final_url}"
                ) from exc
            if final.hostname not in _DOWNLOAD_HOSTS:
                raise ToolchainProvisionError(
                    f"{name} download redirected to unapproved host: {final_url}"
                )
            with partial.open("wb") as handle:
                while chunk := response.read(_CHUNK_BYTES):
                    handle.write(chunk)
        _verify_artifact(partial, artifact)
        partial.replace(destination)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return destination


def _archive_relative_path(
    member_name: str,
    *,
    strip_prefix: str | None,
) -> Path | None:
    normalized = member_name.replace("\\", "/")
    member = PurePosixPath(normalized)
    parts = member.parts
    if member.is_absolute() or not parts or ".." in parts or parts[0].endswith(":"):
        raise ToolchainProvisionError(f"unsafe archive member: {member_name}")
    if strip_prefix is not None:
        if parts[0] != strip_prefix:
            raise ToolchainProvisionError(
                f"archive member is outside reviewed prefix {strip_prefix!r}: {member_name}"
            )
        parts = parts[1:]
    if not parts:
        return None
    if any(part in {"", ".", ".."} for part in parts):
        raise ToolchainProvisionError(f"unsafe archive member: {member_name}")
    return Path(*parts)


def _destination_for(
    root: Path,
    member_name: str,
    *,
    strip_prefix: str | None,
) -> Path | None:
    relative = _archive_relative_path(member_name, strip_prefix=strip_prefix)
    if relative is None:
        return None
    destination = root / relative
    try:
        destination.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ToolchainProvisionError(f"unsafe archive destination: {member_name}") from exc
    return destination


def safe_extract(
    archive: Path,
    destination: Path,
    *,
    archive_kind: str,
    strip_prefix: str | None,
) -> None:
    """Extract a reviewed zip/tar archive without traversal or links."""

    destination.mkdir(parents=True, exist_ok=True)
    if archive_kind == "zip":
        with zipfile.ZipFile(archive) as zip_handle:
            for zip_info in zip_handle.infolist():
                mode = zip_info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise ToolchainProvisionError(f"unsafe archive symlink: {zip_info.filename}")
                target = _destination_for(
                    destination,
                    zip_info.filename,
                    strip_prefix=strip_prefix,
                )
                if target is None:
                    continue
                if zip_info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zip_handle.open(zip_info) as zip_source, target.open("wb") as output:
                    shutil.copyfileobj(zip_source, output)
        return

    if archive_kind == "tar.gz":
        with tarfile.open(archive, "r:gz") as tar_handle:
            for tar_info in tar_handle:
                if (
                    tar_info.issym()
                    or tar_info.islnk()
                    or not (tar_info.isdir() or tar_info.isfile())
                ):
                    raise ToolchainProvisionError(f"unsafe archive member type: {tar_info.name}")
                target = _destination_for(
                    destination,
                    tar_info.name,
                    strip_prefix=strip_prefix,
                )
                if target is None:
                    continue
                if tar_info.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                tar_source = tar_handle.extractfile(tar_info)
                if tar_source is None:
                    raise ToolchainProvisionError(f"cannot read archive member: {tar_info.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with tar_source, target.open("wb") as output:
                    shutil.copyfileobj(tar_source, output)
        return
    raise ToolchainProvisionError(f"unsupported archive format: {archive_kind}")


def portable_environment(
    root: Path,
    base: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a build environment that prefers only provisioned tools."""

    environment = dict(os.environ if base is None else base)
    prefix = ";".join(str(root / name) for name in ("node", "uv", "python"))
    existing_path = environment.get("PATH", "")
    environment["PATH"] = f"{prefix};{existing_path}" if existing_path else prefix
    environment["UV_PYTHON"] = str(root / "python" / "python.exe")
    environment["CIVICCAST_UV_EXE"] = str(root / "uv" / "uv.exe")
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _run_version(executable: Path, *args: str) -> str:
    if executable.suffix.lower() == ".cmd":
        command = ["cmd.exe", "/d", "/s", "/c", str(executable), *args]
    else:
        command = [str(executable), *args]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return f"{result.stdout}{result.stderr}".strip()


def verify_portable_toolchain(
    root: Path,
    identities: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, str]]:
    """Verify reconstructed executables, delegated trees, and versions."""

    paths = {
        "node": root / "node" / "node.exe",
        "npm": root / "node" / "npm.cmd",
        "python": root / "python" / "python.exe",
        "python312.dll": root / "python" / "python312.dll",
        "uv": root / "uv" / "uv.exe",
    }
    version_args = {
        "node": ("--version",),
        "npm": ("--version",),
        "python": ("--version",),
        "uv": ("--version",),
    }
    verified: dict[str, dict[str, str]] = {}
    for name, path in paths.items():
        if not path.is_file():
            raise ToolchainProvisionError(f"provisioned {name} is missing: {path}")
        expected = identities[name]
        actual_sha256 = _sha256_file(path)
        if actual_sha256 != expected["sha256"]:
            raise ToolchainProvisionError(
                f"provisioned {name} SHA-256 {actual_sha256} != reviewed {expected['sha256']}"
            )
        verified[name] = {"path": str(path.resolve()), "sha256": actual_sha256}
        if name in version_args:
            actual_version = _run_version(path, *version_args[name])
            if actual_version != expected["version"]:
                raise ToolchainProvisionError(
                    f"provisioned {name} version {actual_version!r} != "
                    f"reviewed {expected['version']!r}"
                )
            verified[name]["version"] = actual_version

    for name, tree in {
        "npm": root / "node" / "node_modules" / "npm",
        "python": root / "python",
    }.items():
        actual_tree = _sha256_tree(tree)
        expected_tree = identities[name]["tree_sha256"]
        if actual_tree != expected_tree:
            raise ToolchainProvisionError(
                f"provisioned {name} tree SHA-256 {actual_tree} != reviewed {expected_tree}"
            )
        verified[name]["tree_sha256"] = actual_tree
    return verified


def provision_portable_toolchain(
    lock: Mapping[str, Any],
    cache: Path,
    output: Path,
    *,
    offline: bool = False,
) -> dict[str, dict[str, str]]:
    """Reconstruct and verify the portable build tools in a fresh directory."""

    validate_lock(lock)
    if output.exists() and any(output.iterdir()):
        raise ToolchainProvisionError(f"refusing non-empty toolchain output directory: {output}")
    output.mkdir(parents=True, exist_ok=True)
    artifacts = lock["artifacts"]
    acquired = {
        name: fetch_locked_artifact(
            name,
            artifacts[name],
            cache,
            offline=offline,
        )
        for name in _PORTABLE_ARTIFACTS
    }
    safe_extract(
        acquired["node"],
        output / "node",
        archive_kind=artifacts["node"]["archive"],
        strip_prefix=artifacts["node"]["strip_prefix"],
    )
    npm_root = output / "node" / "node_modules" / "npm"
    if npm_root.exists():
        if not npm_root.resolve().is_relative_to(output.resolve()):
            raise ToolchainProvisionError("npm_root escapes output")
        shutil.rmtree(npm_root)
    safe_extract(
        acquired["npm"],
        output / "node" / "node_modules" / "npm",
        archive_kind=artifacts["npm"]["archive"],
        strip_prefix=artifacts["npm"]["strip_prefix"],
    )
    safe_extract(
        acquired["python"],
        output / "python",
        archive_kind=artifacts["python"]["archive"],
        strip_prefix=artifacts["python"]["strip_prefix"],
    )
    safe_extract(
        acquired["uv"],
        output / "uv",
        archive_kind=artifacts["uv"]["archive"],
        strip_prefix=artifacts["uv"]["strip_prefix"],
    )

    npm_bin = output / "node" / "node_modules" / "npm" / "bin"
    for shim in ("npm", "npm.cmd", "npm.ps1", "npx", "npx.cmd", "npx.ps1"):
        shutil.copyfile(npm_bin / shim, output / "node" / shim)
    (output / "python" / "BUILD").write_bytes(
        str(artifacts["python"]["build_marker"]).encode("ascii")
    )
    (output / "python" / "Lib" / "EXTERNALLY-MANAGED").write_text(
        str(artifacts["python"]["externally_managed"]),
        encoding="utf-8",
        newline="\n",
    )

    verified = verify_portable_toolchain(output, lock["installed_identities"])
    receipt = {
        "lock_sha256": _sha256_file(LOCK_PATH),
        "schema_version": 1,
        "target": lock["target"],
        "verified": verified,
    }
    (output / "toolchain-receipt.json").write_text(
        json.dumps(receipt, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return verified


def assert_msvc_path_length(path: Path, option: str) -> None:
    """Refuse a path Microsoft's installer will reject after the download.

    Microsoft: "Make sure that your full installation path is less than 80
    characters." A longer path makes vs_BuildTools exit 1 and log "The root
    installation path is too long for this product" only after acquiring the
    product, so this fires first.
    """

    rendered = str(path)
    if len(rendered) >= _MSVC_PATH_LIMIT:
        raise ToolchainProvisionError(
            f"{option} path is {len(rendered)} characters; Microsoft's Build Tools "
            f"installer requires a full installation path under {_MSVC_PATH_LIMIT} "
            f"characters and fails after downloading otherwise: {rendered}. "
            "Choose a short root such as C:\\ccbt."
        )


def msvc_layout_command(
    bootstrapper: Path,
    layout: Path,
    msvc: Mapping[str, Any],
) -> list[str]:
    """Return Microsoft's noninteractive exact-version layout command."""

    command = [
        str(bootstrapper),
        "--layout",
        str(layout),
        "--lang",
        "en-US",
    ]
    for component in msvc["components"]:
        command.extend(["--add", str(component)])
    command.extend(["--quiet", "--wait", "--norestart"])
    return command


def msvc_install_command(
    bootstrapper: Path,
    install_root: Path,
    msvc: Mapping[str, Any],
) -> list[str]:
    """Return Microsoft's noninteractive exact-version install command."""

    command = [
        str(bootstrapper),
        "--installPath",
        str(install_root),
    ]
    for component in msvc["components"]:
        command.extend(["--add", str(component)])
    command.extend(["--quiet", "--wait", "--norestart"])
    return command


def assert_msvc_version_output(
    compiler_output: str,
    linker_output: str,
    msvc: Mapping[str, Any],
) -> None:
    """Refuse an installed compiler/linker whose exact versions drifted."""

    compiler_version = str(msvc["compiler_version"])
    linker_version = str(msvc["linker_version"])
    if (
        re.search(
            rf"(?<![\d.]){re.escape(compiler_version)}(?![\d.])",
            compiler_output,
        )
        is None
    ):
        raise ToolchainProvisionError(
            f"MSVC compiler is not reviewed {compiler_version}: {compiler_output.strip()!r}"
        )
    if (
        re.search(
            rf"(?<![\d.]){re.escape(linker_version)}(?![\d.])",
            linker_output,
        )
        is None
    ):
        raise ToolchainProvisionError(
            f"MSVC linker is not reviewed {linker_version}: {linker_output.strip()!r}"
        )


def verify_msvc_cmake_tools(
    install_root: Path,
    versions: Mapping[str, str],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Path]:
    """Use only the CMake component belonging to the selected MSVC installation."""
    root = install_root / "Common7/IDE/CommonExtensions/Microsoft/CMake"
    paths = {"cmake": root / "CMake/bin/cmake.exe", "ninja": root / "Ninja/ninja.exe"}
    for name, path in paths.items():
        if not path.is_file():
            raise ToolchainProvisionError(f"Pinned MSVC {name} is missing: {path}")
        completed = runner(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        lines = completed.stdout.splitlines()
        expected = f"cmake version {versions[name]}" if name == "cmake" else versions[name]
        if not lines or lines[0].strip() != expected:
            raise ToolchainProvisionError(f"Pinned MSVC {name} is not reviewed {versions[name]}")
    return paths


def verify_msvc_installation(
    install_root: Path,
    msvc: Mapping[str, Any],
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    """Verify compiler, linker and OCR build tools from one explicit installation."""

    vcvarsall = install_root / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat"
    if not vcvarsall.is_file():
        raise ToolchainProvisionError(f"MSVC vcvarsall.bat is missing: {vcvarsall}")
    results: list[subprocess.CompletedProcess[str]] = []
    with tempfile.TemporaryDirectory(prefix="cc-msvc-verify-") as temporary:
        for executable in ("cl.exe", "link.exe"):
            wrapper = Path(temporary) / f"verify-{executable}.cmd"
            wrapper.write_text(
                "\r\n".join(
                    (
                        "@echo off",
                        f'call "{vcvarsall}" x64 >nul',
                        "if errorlevel 1 exit /b %errorlevel%",
                        executable,
                        "",
                    )
                ),
                encoding="utf-8",
            )
            results.append(
                runner(
                    ["cmd.exe", "/d", "/c", str(wrapper)],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=60,
                )
            )
    compiler, linker = results
    assert_msvc_version_output(
        f"{compiler.stdout}\n{compiler.stderr}",
        f"{linker.stdout}\n{linker.stderr}",
        msvc,
    )
    verify_msvc_cmake_tools(
        install_root,
        {name: str(msvc[f"{name}_version"]) for name in ("cmake", "ninja")},
        runner=runner,
    )


def prepare_msvc_layout(
    lock: Mapping[str, Any],
    cache: Path,
    layout: Path,
    *,
    offline: bool = False,
) -> None:
    """Download the fixed MSVC bootstrapper and create its reviewed layout."""

    assert_msvc_path_length(layout, "--msvc-layout")
    msvc = lock["artifacts"]["msvc"]
    bootstrapper = fetch_locked_artifact(
        "msvc",
        msvc,
        cache,
        offline=offline,
    )
    subprocess.run(
        msvc_layout_command(bootstrapper, layout, msvc),
        check=True,
        timeout=60 * 60,
    )


def install_msvc(
    lock: Mapping[str, Any],
    cache: Path,
    install_root: Path,
    *,
    offline: bool = False,
) -> Path:
    """Install and verify the fixed MSVC Build Tools product, reusing an
    already-verified install in place when ``install_root`` already holds one.

    A hosted runner is always fresh, so ``install_root`` never pre-exists
    there -- this function's hosted-lane behavior is byte-identical to
    before it gained a reuse path: verification is skipped because there is
    nothing at ``install_root`` to verify, and install proceeds exactly as
    it always did. A SELF-HOSTED runner's `_work\\_temp` persists across
    runs, so `install_root` can already hold a previous run's install --
    complete (worth reusing: this installer takes real minutes, not
    hosted-runner-ephemeral seconds), or incomplete/interrupted. Candidate
    run 32810709045's own follow-up investigation found a concrete case of
    the latter that a naive "does the dir look non-empty" check would have
    missed: a from-a-crashed-run tree that LOOKED complete (cl.exe/link.exe
    both present, ~1.8 GB) but was actually left over from a deletion that
    itself failed partway through (Windows still held handles open via
    vctip.exe/mspdbsrv.exe) -- an unknown mix of the old and prior states.
    Trust is therefore never marker-only: `verify_msvc_installation` -- the
    same real cl.exe/link.exe launch-and-version check a fresh install
    already trusts before use -- reruns against ANY pre-existing tree before
    it is reused, so stale metadata next to corrupted or missing binaries is
    still caught.

    If the pre-existing tree fails that verification, this function tries to
    clear it (so this run's install lands at the canonical `install_root`,
    which every unconditional literal downstream in
    native-beta-candidate-artifacts.yml assumes). If Windows still has a
    handle open on any file in it, `shutil.rmtree` raises `OSError` rather
    than partially succeeding into an even-more-ambiguous state -- caught
    here, not propagated, and this run installs at a uniquely suffixed
    SIBLING directory instead of failing the job outright. The old,
    undeletable directory is deliberately left behind (best-effort cleanup
    is the caller's job, on a later run once whatever was holding it open
    has exited) rather than retried in a loop here.

    Returns the install root actually used: `install_root` unchanged in the
    hosted-lane case, the reuse case, and the normal fresh-install case, or
    the relocated sibling path in the rare locked-and-uninstallable case.
    Callers that need the resolved path outside this process (the workflow's
    later steps all reference `$env:CIVICCAST_MSVC_INSTALLATION_PATH`
    directly) must propagate it themselves -- see `main()` below, which
    re-exports it to `$GITHUB_ENV` whenever it differs from what was asked
    for.
    """

    msvc = lock["artifacts"]["msvc"]
    target = install_root
    if install_root.exists():
        try:
            verify_msvc_installation(install_root, msvc)
            return install_root  # already valid -- reuse, no reinstall
        except ToolchainProvisionError:
            pass
        try:
            shutil.rmtree(install_root)
        except OSError:
            target = install_root.with_name(f"{install_root.name}-{uuid.uuid4().hex[:8]}")

    assert_msvc_path_length(target, "--msvc-install")
    bootstrapper = fetch_locked_artifact(
        "msvc",
        msvc,
        cache,
        offline=offline,
    )
    # The Visual Studio installer returns 3010 (ERROR_SUCCESS_REBOOT_REQUIRED)
    # on a machine with a pending reboot. That is a SUCCESS code -- the tools
    # are installed and usable -- but `check=True` tolerates only 0, so the
    # provisioning step aborts on a perfectly good install. Reproduced on this
    # project's own dev box 2026-08-06; a fresh hosted runner happens to have no
    # pending reboot, which is why CI never surfaced it.
    #
    # Accepting 3010 does not weaken anything: `verify_msvc_installation` runs
    # immediately below and is the real correctness gate -- it checks the
    # product/compiler/linker versions and component selection against the lock.
    # If the install were genuinely broken, that is what catches it, not the
    # exit code.
    completed = subprocess.run(
        msvc_install_command(bootstrapper, target, msvc),
        check=False,
        timeout=2 * 60 * 60,
    )
    if completed.returncode not in (0, 3010):
        raise ToolchainProvisionError(
            f"MSVC Build Tools install failed with exit code {completed.returncode}"
        )
    verify_msvc_installation(target, msvc)
    return target


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--offline", action="store_true")
    msvc = parser.add_mutually_exclusive_group()
    msvc.add_argument("--msvc-layout", type=Path)
    msvc.add_argument("--msvc-install", type=Path)
    return parser


def reexport_relocated_msvc_install(
    requested: Path,
    resolved: Path,
    *,
    github_env: str | None,
) -> None:
    """Tell the caller when `install_msvc()` relocated, and how.

    `install_msvc()` only relocates when `requested` existed, failed
    verification, AND could not be removed (a locked file from something
    still holding it open -- see that function's docstring). Every later
    step in native-beta-candidate-artifacts.yml reads
    $env:CIVICCAST_MSVC_INSTALLATION_PATH directly (the Tauri vcvars64.bat
    import, the pack build's env block), so the resolved path is re-exported
    here rather than left for the caller to notice on its own -- GITHUB_ENV
    values set mid-job take effect starting with the NEXT step, matching how
    this exact variable is already bound before this script ever runs (see
    the workflow's "Bind reviewed MSVC install location"). A no-op, both the
    stderr note and the GITHUB_ENV write, when `resolved == requested` (the
    common case: the hosted lane always, and self-hosted whenever nothing
    was locked).

    Split out of `main()` -- which is unconditionally gated to Windows-only
    (`os.name != "nt"`), appropriately, since actually provisioning MSVC
    only makes sense there -- so this glue logic can be exercised by
    `tests/native`'s platform-independent suite, which CI runs on a Linux
    runner. Calling this indirectly through `main()` made that suite fail
    on candidate-review PR #31 with "the native Windows toolchain must be
    provisioned on Windows" -- a real OS-gate hit, not a GITHUB_ENV
    collision (both original tests already isolated GITHUB_ENV via
    monkeypatch.setenv; the ambient value was never the issue).
    """
    if resolved == requested:
        return
    print(
        f"MSVC install at {requested} failed verification and "
        f"could not be removed (still in use by something); installed fresh "
        f"at {resolved} instead.",
        file=sys.stderr,
    )
    if github_env:
        with Path(github_env).open("a", encoding="utf-8") as handle:
            handle.write(f"CIVICCAST_MSVC_INSTALLATION_PATH={resolved}\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if os.name != "nt":
        raise ToolchainProvisionError("the native Windows toolchain must be provisioned on Windows")
    lock = load_lock()
    verified = provision_portable_toolchain(
        lock,
        args.cache.resolve(),
        args.output.resolve(),
        offline=args.offline,
    )
    if args.msvc_layout is not None:
        prepare_msvc_layout(
            lock,
            args.cache.resolve(),
            args.msvc_layout.resolve(),
            offline=args.offline,
        )
    if args.msvc_install is not None:
        requested_msvc_install = args.msvc_install.resolve()
        resolved_msvc_install = install_msvc(
            lock,
            args.cache.resolve(),
            requested_msvc_install,
            offline=args.offline,
        )
        reexport_relocated_msvc_install(
            requested_msvc_install,
            resolved_msvc_install,
            github_env=os.environ.get("GITHUB_ENV"),
        )
    print(json.dumps({"output": str(args.output.resolve()), "verified": verified}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
