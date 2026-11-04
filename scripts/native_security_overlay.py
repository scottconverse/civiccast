# SPDX-License-Identifier: Apache-2.0
"""Verified security-library replacements for the existing native pack builders."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "native-windows-security-libraries.lock.json"
ARCHIVE_NAME = "civiccast-rc4-expat-2.10.0-security-libraries-windows-x64.zip"
EXPAT_ORDINAL_CONTRACT = ROOT / "security/native-windows-expat-2.7.1-ordinals.json"


def policy() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def _verify_file(path: Path, pin: dict) -> None:
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size != pin["bytes"]
        or hashlib.sha256(path.read_bytes()).hexdigest() != pin["sha256"]
    ):
        raise ValueError(f"unverified security-library input: {path}")


def verify_expat_ordinal_contract(path: Path) -> None:
    import pefile

    contract = json.loads(EXPAT_ORDINAL_CONTRACT.read_text(encoding="utf-8"))
    expected = {entry["ordinal"]: entry for entry in contract["exports"]}
    if len(expected) != 69:
        raise ValueError("Expat ordinal contract must cover all 69 legacy exports")
    with pefile.PE(str(path)) as pe:
        actual = {
            symbol.ordinal: (
                symbol.name if symbol.name else None,
                symbol.forwarder if symbol.forwarder else None,
            )
            for symbol in pe.DIRECTORY_ENTRY_EXPORT.symbols
        }
    for ordinal, entry in expected.items():
        if ordinal not in actual:
            raise ValueError(f"missing Expat export ordinal {ordinal}: {entry['name']}")
        name, forwarder = actual[ordinal]
        if name != entry["name"].encode("ascii"):
            raise ValueError(
                f"Expat ordinal {ordinal} name mismatch: {name!r} != {entry['name']!r}"
            )
        expected_forwarder = entry["forwarder"].encode("ascii") if entry["forwarder"] else None
        if forwarder != expected_forwarder:
            raise ValueError(
                f"Expat ordinal {ordinal} forwarder mismatch: {forwarder!r} != {entry['forwarder']!r}"
            )


def acquire_runtime(cache: Path) -> Path:
    reviewed = policy()
    out = cache / f"security-libraries-{reviewed['archive']['sha256'][:16]}"
    if out.is_dir():
        try:
            for name, pin in reviewed["files"].items():
                _verify_file(out / name, pin)
            if {p.name for p in out.iterdir()} == set(reviewed["files"]):
                return out
        except ValueError:
            pass
        # A cache that fails verification (the runner's temp cleanup can leave
        # it emptied, or it was modified) is never used: rebuild it below from
        # the reviewed archive, which is itself hash-checked first.
    archive = Path(
        os.environ.get(
            "CIVICCAST_SECURITY_LIBRARIES_ZIP",
            str(Path("C:/CivicCastTester/runtime-artifact-mirror") / ARCHIVE_NAME),
        )
    )
    _verify_file(archive, reviewed["archive"])
    cache.mkdir(parents=True, exist_ok=True)
    with (
        zipfile.ZipFile(archive) as bundle,
        tempfile.TemporaryDirectory(prefix=".security-", dir=cache) as temporary,
    ):
        names = bundle.namelist()
        if len(names) != len(set(names)) or set(names) != set(reviewed["files"]):
            raise ValueError("unexpected security-library archive members")
        for name in names:
            if "/" in name or "\\" in name or ":" in name or name in {".", ".."}:
                raise ValueError("unsafe security-library archive path")
        fresh = Path(temporary) / "runtime"
        bundle.extractall(fresh)
        for name, pin in reviewed["files"].items():
            _verify_file(fresh / name, pin)
        # Swap in only a verified, complete tree.
        if out.exists():
            shutil.rmtree(out)
        fresh.rename(out)
    for name, pin in reviewed["files"].items():
        _verify_file(out / name, pin)
    if {p.name for p in out.iterdir()} != set(reviewed["files"]):
        raise ValueError("unexpected cached security-library files")
    return out


def assert_compatible_imports(consumers: list[Path], replacements: dict[str, Path]) -> None:
    import pefile

    exports = {}
    for name, path in replacements.items():
        if Path(name).name.lower() == "libexpat.dll":
            verify_expat_ordinal_contract(path)
        with pefile.PE(str(path)) as pe:
            exports[name.lower()] = {
                symbol.name if symbol.name else symbol.ordinal
                for symbol in pe.DIRECTORY_ENTRY_EXPORT.symbols
            } | {symbol.ordinal for symbol in pe.DIRECTORY_ENTRY_EXPORT.symbols}
    for path in consumers:
        with pefile.PE(str(path)) as pe:
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
                    raise ValueError(f"security-library ABI mismatch in {path}: {name}: {missing}")


def _sources(kind: str, runtime: Path) -> dict[str, Path]:
    return {pin[kind]: runtime / name for name, pin in policy()["files"].items() if kind in pin}


def verify_overlay(out: Path, kind: str = "gstreamer") -> None:
    for pin in policy()["files"].values():
        if kind in pin:
            _verify_file(out / pin[kind], pin)


def overlay_directory(out: Path, cache: Path) -> dict[str, tuple[str, str, str]]:
    runtime = acquire_runtime(cache)
    sources = _sources("gstreamer", runtime)
    dlls = {Path(rel).name: source for rel, source in sources.items() if rel.endswith(".dll")}
    consumers = [
        p
        for p in out.rglob("*")
        if p.suffix.lower() in {".dll", ".pyd", ".exe"} and p.name not in dlls
    ]
    assert_compatible_imports([*consumers, *dlls.values()], dlls)
    attribution = {}
    reviewed = policy()
    for name, pin in reviewed["files"].items():
        if "gstreamer" not in pin:
            continue
        relative = pin["gstreamer"]
        destination = out / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(runtime / name, destination)
        attribution[relative] = (
            pin["component"],
            reviewed["components"][pin["component"]]["version"],
            pin["license"],
        )
    return attribution


def secured_postgres_sources(sources: dict[str, Path], cache: Path) -> dict[str, Path]:
    runtime = acquire_runtime(cache)
    replacements = _sources("postgres", runtime)
    dlls = {Path(rel).name: source for rel, source in replacements.items() if rel.endswith(".dll")}
    consumers = [
        p
        for rel, p in sources.items()
        if p.suffix.lower() in {".dll", ".exe"} and rel not in replacements
    ]
    assert_compatible_imports([*consumers, *dlls.values()], dlls)
    return {**sources, **replacements}
