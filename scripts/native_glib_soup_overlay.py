# SPDX-License-Identifier: Apache-2.0
"""Apply the reviewed GLib, libsoup and nghttp2 DLL overlay to a GStreamer tree."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "native-windows-glib-soup.lock.json"
ARCHIVE_NAME = "glib-2.88.4-libsoup-3.8.1-nghttp2-1.70.0-civiccast-windows-x64.zip"


def policy() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_runtime(root: Path) -> None:
    for name, pin in policy()["files"].items():
        path = root / name
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != pin["bytes"]
            or _sha(path) != pin["sha256"]
        ):
            raise ValueError(f"unverified GLib/libsoup/nghttp2 runtime file: {path}")


def verify_overlay(out: Path) -> None:
    """Recheck installed closure bytes using only committed pins; acquire nothing."""
    for name, pin in policy()["files"].items():
        rel = f"bin/{name}" if name.endswith(".dll") else f"licenses/glib-soup/{name}"
        path = out / rel
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != pin["bytes"]
            or _sha(path) != pin["sha256"]
        ):
            raise ValueError(f"unverified installed GLib/libsoup/nghttp2 overlay file: {path}")


def acquire_runtime(cache: Path) -> Path:
    out = cache / "glib-2.88.4-libsoup-3.8.1-nghttp2-1.70.0-runtime"
    if out.is_dir():
        try:
            verify_runtime(out)
            if {p.name for p in out.iterdir()} == set(policy()["files"]):
                return out
        except ValueError:
            pass
        # A cache that fails verification (the runner's temp cleanup can leave
        # it emptied, or it was modified) is never used: rebuild it below from
        # the reviewed archive, which is itself hash-checked first.
    archive = Path(
        os.environ.get(
            "CIVICCAST_GLIB_SOUP_RUNTIME_ZIP",
            str(Path("C:/CivicCastTester/runtime-artifact-mirror") / ARCHIVE_NAME),
        )
    )
    pin = policy()["archive"]
    if (
        not archive.is_file()
        or archive.stat().st_size != pin["bytes"]
        or _sha(archive) != pin["sha256"]
    ):
        raise ValueError(f"GLib/libsoup/nghttp2 archive missing or unverified: {archive}")
    cache.mkdir(parents=True, exist_ok=True)
    with (
        zipfile.ZipFile(archive) as bundle,
        tempfile.TemporaryDirectory(dir=cache, prefix=".glib-soup-") as temporary,
    ):
        names = bundle.namelist()
        if len(names) != len(set(names)) or set(names) != set(policy()["files"]):
            raise ValueError("unexpected GLib/libsoup/nghttp2 archive members")
        for name in names:
            if name.startswith("/") or "\\" in name or ".." in name.split("/") or ":" in name:
                raise ValueError("unsafe GLib/libsoup/nghttp2 archive member")
        fresh = Path(temporary) / "runtime"
        bundle.extractall(fresh)
        verify_runtime(fresh)
        # os.replace cannot overwrite a directory on Windows; swap in only a
        # verified, complete tree.
        if out.exists():
            shutil.rmtree(out)
        fresh.replace(out)
    return out


def assert_compatible_imports(consumers: list[Path], runtime: Path) -> None:
    """Every existing normal/delay import must resolve against the new DLL exports."""
    import pefile

    exports = {}
    for name in policy()["files"]:
        if not name.endswith(".dll"):
            continue
        with pefile.PE(str(runtime / name)) as pe:
            exports[Path(name).name.lower()] = {
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
                    s.name if s.name else s.ordinal
                    for s in imported.imports
                    if (s.name if s.name else s.ordinal) not in exports[name]
                ]
                if missing:
                    raise ValueError(
                        f"GLib/libsoup/nghttp2 ABI mismatch in {consumer}: {name}: {missing}"
                    )


def overlay_directory(out: Path, cache: Path) -> dict[str, tuple[str, str, str]]:
    runtime = acquire_runtime(cache)
    assert_compatible_imports(
        [p for p in out.rglob("*") if p.suffix.lower() in {".dll", ".exe", ".pyd"}], runtime
    )
    attribution = {}
    for name, pin in policy()["files"].items():
        rel = f"bin/{name}" if name.endswith(".dll") else f"licenses/glib-soup/{name}"
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(runtime / name, target)
        attribution[rel] = (pin["component"], pin["version"], pin["license"])
    return attribution
