# SPDX-License-Identifier: Apache-2.0
"""Exact, verified OpenSSL replacement shared by the two existing pack builders."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "native-windows-openssl.lock.json"
ARCHIVE_NAME = "openssl-3.5.9-civiccast-windows-x64.zip"
VERSION = "3.5.9"


def policy() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def verify_runtime(root: Path) -> None:
    for name, pin in policy()["files"].items():
        path = root / name
        if (
            not path.is_file()
            or path.stat().st_size != pin["bytes"]
            or hashlib.sha256(path.read_bytes()).hexdigest() != pin["sha256"]
        ):
            raise ValueError(f"unverified OpenSSL {VERSION} runtime file: {path}")


def verify_overlay(out: Path) -> None:
    """Independent built/installed closure check; never acquires build inputs."""
    for name, pin in policy()["files"].items():
        rel = f"bin/{name}" if name.endswith(".dll") else f"licenses/openssl-3.5.9/{name}"
        path = out / rel
        if (
            not path.is_file()
            or path.stat().st_size != pin["bytes"]
            or hashlib.sha256(path.read_bytes()).hexdigest() != pin["sha256"]
        ):
            raise ValueError(f"unverified installed OpenSSL {VERSION} overlay: {path}")


def acquire_runtime(cache: Path) -> Path:
    out = cache / "openssl-3.5.9-runtime"
    partial = cache / "openssl-3.5.9-runtime.partial"
    if out.is_dir():
        try:
            verify_runtime(out)
            if {p.name for p in out.iterdir()} == set(policy()["files"]):
                return out
        except ValueError:
            pass
        # A cache that fails verification (an interrupted earlier run left it
        # empty or partial, or it was modified) is never used: rebuild it from
        # the reviewed archive below, which is itself hash-checked first.
    archive = Path(
        os.environ.get(
            "CIVICCAST_OPENSSL_RUNTIME_ZIP",
            str(Path("C:/CivicCastTester/runtime-artifact-mirror") / ARCHIVE_NAME),
        )
    )
    pin = policy()["archive"]
    if (
        not archive.is_file()
        or archive.stat().st_size != pin["bytes"]
        or hashlib.sha256(archive.read_bytes()).hexdigest() != pin["sha256"]
    ):
        raise ValueError(
            f"OpenSSL runtime archive missing or unverified: {archive}; run python -m scripts.build_native_openssl --work <scratch> and stage its reviewed archive"
        )
    with zipfile.ZipFile(archive) as bundle:
        if set(bundle.namelist()) != set(policy()["files"]):
            raise ValueError("unexpected OpenSSL runtime archive members")
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
    """Require every normal/delay import symbol, including ordinals, to resolve."""
    import pefile

    exports = {}
    for name in ("libcrypto-3-x64.dll", "libssl-3-x64.dll"):
        with pefile.PE(str(runtime / name), fast_load=True) as pe:
            pe.parse_data_directories(
                directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]]
            )
            exports[name] = {
                symbol.name if symbol.name else symbol.ordinal
                for symbol in pe.DIRECTORY_ENTRY_EXPORT.symbols
            }
    for consumer in consumers:
        with pefile.PE(str(consumer), fast_load=True) as pe:
            pe.parse_data_directories(
                directories=[
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
                ]
            )
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
                    raise ValueError(f"OpenSSL ABI mismatch in {consumer}: {name}: {missing}")


def assert_runtime_closure(runtime: Path) -> dict[str, list[str]]:
    """Refuse hidden CRT/provider dependencies outside the two-DLL closure."""
    import pefile

    system = {
        "kernel32.dll",
        "user32.dll",
        "advapi32.dll",
        "ws2_32.dll",
        "crypt32.dll",
        "bcrypt.dll",
        "ntdll.dll",
    }
    result = {}
    for name in ("libcrypto-3-x64.dll", "libssl-3-x64.dll"):
        with pefile.PE(str(runtime / name)) as pe:
            if pe.FILE_HEADER.Machine != 0x8664:
                raise ValueError(f"OpenSSL is not an AMD64 runtime: {name}")
            imports = sorted(
                {
                    entry.dll.decode("ascii").lower()
                    for entry in [
                        *getattr(pe, "DIRECTORY_ENTRY_IMPORT", []),
                        *getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", []),
                    ]
                }
            )
            unexpected = set(imports) - system - {"libcrypto-3-x64.dll"}
            if unexpected:
                raise ValueError(
                    f"OpenSSL imports unshipped dependencies: {name}: {sorted(unexpected)}"
                )
            result[name] = imports
    return result


def overlay_directory(out: Path, cache: Path) -> dict[str, tuple[str, str, str]]:
    """Overlay an existing GStreamer closure; caller regenerates its trust files."""
    runtime = acquire_runtime(cache)
    assert_compatible_imports(
        [p for p in out.rglob("*") if p.suffix.lower() in {".dll", ".exe", ".pyd"}], runtime
    )
    attribution = {}
    for name in policy()["files"]:
        rel = f"bin/{name}" if name.endswith(".dll") else f"licenses/openssl-3.5.9/{name}"
        destination = out / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(runtime / name, destination)
        license_ = (
            "Apache-2.0 AND LicenseRef-Microsoft-VCRedist"
            if name.endswith(".dll")
            else "Apache-2.0"
        )
        if name == "LicenseRef-Microsoft-VCRedist.txt":
            license_ = "LicenseRef-Microsoft-VCRedist"
        attribution[rel] = ("openssl", VERSION, license_)
    return attribution


def secured_postgres_sources(sources: dict[str, Path], cache: Path) -> dict[str, Path]:
    runtime = acquire_runtime(cache)
    assert_compatible_imports(
        [p for p in sources.values() if p.suffix.lower() in {".dll", ".exe"}], runtime
    )
    secured = dict(sources)
    for name in policy()["files"]:
        rel = f"bin/{name}" if name.endswith(".dll") else f"licenses/openssl-3.5.9/{name}"
        secured[rel] = runtime / name
    return secured
