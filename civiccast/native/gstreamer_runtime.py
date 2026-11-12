# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Process-local environment and bootstrap for an installed GStreamer pack."""

from __future__ import annotations

import hashlib
import os
import stat
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

_REPARSE_POINT = 0x400
# Relative to whatever ``version_root`` a caller hands
# :func:`installed_gstreamer_environment` -- this module does no root
# discovery of its own. On a real native install that root is
# ``<install_root>/runtime`` (where the embedded ``python.exe`` also lives),
# NOT the install root itself: the GStreamer closure is composed into the
# native-app-payload pack (`scripts/build_native_app_payload_pack.py`), which
# extracts to ``<install_root>/runtime``
# (`native_pack_staging::pack_extraction_destination`), so
# ``dependencies/gstreamer`` only resolves under THAT directory. See
# `civiccast.native.station_runtime.load_native_station_environment`'s
# ``gstreamer_runtime_root`` for the caller that gets this right.
_RUNTIME = Path("dependencies/gstreamer")
_REQUIRED_DIRS = (
    Path("bin"),
    Path("lib/gstreamer-1.0"),
    Path("lib/girepository-1.0"),
    Path("python/gi"),
)
_REQUIRED_FILES = (Path("lib/girepository-1.0/Gst-1.0.typelib"), Path("bin/gst-discoverer-1.0.exe"))
_DLL_HANDLES: list[object] = []
#: The directories already registered with the Windows loader, so a repeat
#: bootstrap for the SAME ``bin`` directory is a no-op. ``add_dll_directory``
#: is NOT idempotent and NOT free: every call appends another entry to the
#: process-wide loader search path, whose buffer is a fixed 32,767 characters.
#: A long-running control plane re-bootstraps on every content-reload encoder
#: probe (``encoder_probe.gst_factory_klass``), so registering the 70-character
#: installed ``bin`` path again and again walked that buffer to exhaustion and
#: from then on EVERY reload probe raised
#: ``FileNotFoundError: [WinError 206] The filename or extension is too long``
#: -- permanently, because each further call also fails. That is the
#: 2026-10-26 station blackout: the reload dispatch died, the channel pinned
#: TRANSITIONING with a still-running mux emitting black/silent filler and no
#: source-chain input, and no retry could ever succeed. Mirrors
#: ``civiccast.captions.runtime._CUDA_DLL_DIRECTORY_HANDLES``, which already
#: keys its handles by directory for exactly this reason.
#:
#: Concurrency (reviewed 2026-10-27): no lock is taken because no real race
#: exists. In a running station this bootstrap is reached from exactly one
#: thread -- the single ``ChannelAutomationService.run_forever`` thread, which
#: pops queued egress commands and runs every strategy start/reload probe
#: (``daemon._process_queued_commands`` -> ``strategy.start``/``reload_content``);
#: the daemon only ever hands media conformance to its preparation pool, never
#: encoder probing. Other callers bootstrap once per process at import
#: (``gst/engine.py``) or in the single-threaded DR drill. Even if a future
#: caller added a second thread, the worst case is one duplicate registration
#: per concurrent thread -- bounded, and nowhere near the unbounded growth that
#: caused the outage.
_REGISTERED_DLL_DIRECTORIES: set[str] = set()


class GstreamerRuntimeError(RuntimeError):
    """The declared installed GStreamer runtime is unsafe or incomplete."""


def _reparse(path: Path) -> bool:
    data = path.lstat()
    return stat.S_ISLNK(data.st_mode) or bool(
        getattr(data, "st_file_attributes", 0) & _REPARSE_POINT
    )


def _owned(root: Path, relative: Path, *, directory: bool) -> Path:
    current = root
    for part in relative.parts:
        current /= part
        if not os.path.lexists(current):
            raise GstreamerRuntimeError(f"installed GStreamer path is missing: {current}")
        if _reparse(current):
            raise GstreamerRuntimeError(f"installed GStreamer path is a reparse point: {current}")
    resolved = current.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise GstreamerRuntimeError(
            f"installed GStreamer path resolves outside version root: {current}"
        ) from exc
    if (directory and not resolved.is_dir()) or (not directory and not resolved.is_file()):
        raise GstreamerRuntimeError(f"installed GStreamer path has wrong type: {current}")
    return resolved


def installed_gstreamer_environment(
    version_root: str | Path, *, base_environment: Mapping[str, str] | None = None
) -> dict[str, str]:
    root = Path(version_root).expanduser()
    if not root.is_absolute() or _reparse(root):
        raise GstreamerRuntimeError(
            "native GStreamer version root must be an absolute non-reparse path"
        )
    root = root.resolve(strict=True)
    runtime = _owned(root, _RUNTIME, directory=True)
    dirs = {part: _owned(runtime, part, directory=True) for part in _REQUIRED_DIRS}
    for part in _REQUIRED_FILES:
        _owned(runtime, part, directory=False)
    env = dict(os.environ if base_environment is None else base_environment)
    inherited = env.get("PATH", "")
    env.update(
        {
            "PATH": str(dirs[Path("bin")]) + (os.pathsep + inherited if inherited else ""),
            "GST_PLUGIN_PATH": str(dirs[Path("lib/gstreamer-1.0")]),
            "GI_TYPELIB_PATH": str(dirs[Path("lib/girepository-1.0")]),
            # The staged PyGObject's `gi/__init__.py` cannot deduce its DLL
            # directory from this product's installed layout
            # (`dependencies/gstreamer/python/gi` beside `bin`) and raises
            # ImportError unless PYGI_DLL_DIRS names it -- proven live on
            # candidate run 31187038070, where the installed smoke died on
            # exactly that ImportError while the closure verifier (which
            # already sets this var, scripts/verify_native_runtime_closure.py)
            # passed 7/7 against the same staged bytes in the same run.
            "PYGI_DLL_DIRS": str(dirs[Path("bin")]),
            "CIVICCAST_GSTREAMER_PYTHON": str(runtime / "python"),
            "CIVICCAST_GSTREAMER_RUNTIME_ROOT": str(root),
        }
    )
    if not env.get("GST_REGISTRY", "").strip():
        env["GST_REGISTRY"] = str(_registry_cache_path(dirs[Path("bin")], env))
    return env


#: FFmpeg DLLs that ``gstlibav.dll`` loads. Which ``avenc_*``/``avdec_*`` elements
#: exist is decided by THESE files, not by the plugin DLL.
_LIBAV_DLL_PATTERNS = ("avcodec-*.dll", "avformat-*.dll", "avutil-*.dll", "swresample-*.dll")
_FINGERPRINTS: dict[tuple[str, int, int], str] = {}


def _registry_cache_path(bin_dir: Path, env: Mapping[str, str]) -> Path:
    """A GStreamer registry cache file that is unique to this FFmpeg DLL set.

    GStreamer rebuilds its element cache only when a PLUGIN file changes. An
    upgrade that replaces the FFmpeg DLLs behind an unchanged ``gstlibav.dll``
    keeps the old cache, so new codecs stay invisible: measured on a station
    2026-11-12, AC-3-capable DLLs with the rc.5 cache still reported no
    ``avenc_ac3`` until the cache was removed (issue #324). Naming the cache
    after the DLL contents gives every DLL set a fresh scan and needs no cleanup
    step in the installer. An explicit ``GST_REGISTRY`` from the caller wins.
    """
    digest = hashlib.sha256()
    for pattern in _LIBAV_DLL_PATTERNS:
        for dll in sorted(bin_dir.glob(pattern)):
            info = dll.stat()
            key = (str(dll), info.st_size, info.st_mtime_ns)
            if key not in _FINGERPRINTS:
                _FINGERPRINTS[key] = hashlib.sha256(dll.read_bytes()).hexdigest()
            digest.update(f"{dll.name}:{_FINGERPRINTS[key]};".encode())
    base = (
        env.get("XDG_CACHE_HOME", "").strip()
        or env.get("LOCALAPPDATA", "").strip()
        or tempfile.gettempdir()
    )
    return Path(base) / "civiccast-gstreamer" / f"registry-{digest.hexdigest()[:16]}.bin"


def bootstrap_installed_gstreamer_runtime() -> bool:
    root = os.environ.get("CIVICCAST_GSTREAMER_RUNTIME_ROOT")
    if not root:
        return False
    env = installed_gstreamer_environment(root, base_environment=os.environ)
    python_root = env["CIVICCAST_GSTREAMER_PYTHON"]
    if python_root not in sys.path:
        sys.path.insert(0, python_root)
    # A process that reaches this bootstrap without the parent-provided
    # environment (CIVICCAST_GSTREAMER_RUNTIME_ROOT set by hand, an exec
    # that stripped env) must still be able to import the staged `gi` --
    # its `__init__` requires PYGI_DLL_DIRS in this layout, see
    # installed_gstreamer_environment.
    os.environ["PYGI_DLL_DIRS"] = env["PYGI_DLL_DIRS"]
    # PATH is LOAD-BEARING and `os.add_dll_directory` does NOT substitute for it
    # (Gate A T4 root cause, 2026-09). girepository resolves a namespace's GTypes
    # by asking GModule to open the typelib's shared library BY BARE NAME
    # (`gstreamer-1.0-0.dll`), which is a plain Win32 `LoadLibrary` and therefore
    # searches PATH -- not the per-process directory list `os.add_dll_directory`
    # feeds, which only CPython's own extension loader consults. With the bundled
    # `bin` missing from PATH the symbol lookup silently fails,
    # `Gst.URIHandler.__info__.get_g_type()` comes back G_TYPE_NONE, and the
    # `gi.overrides.Gst` import dies with the deeply unhelpful
    # `TypeError: must be an interface` -- which is exactly how the shipped
    # playout worker died on a clean box. Publish the whole computed environment
    # so any process holding CIVICCAST_GSTREAMER_RUNTIME_ROOT can import the
    # staged `gi` on its own, whatever its parent did or did not pass down.
    bin_dir = env["PATH"].split(os.pathsep, 1)[0]
    inherited = os.environ.get("PATH", "")
    if bin_dir not in inherited.split(os.pathsep):
        os.environ["PATH"] = bin_dir + (os.pathsep + inherited if inherited else "")
    os.environ.setdefault("GI_TYPELIB_PATH", env["GI_TYPELIB_PATH"])
    os.environ.setdefault("GST_PLUGIN_PATH", env["GST_PLUGIN_PATH"])
    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        # Register each directory with the loader AT MOST ONCE per process --
        # see _REGISTERED_DLL_DIRECTORIES. Recorded only after the call
        # succeeds, so a transient failure is retried rather than cached as
        # "registered", and the returned handle stays referenced by
        # _DLL_HANDLES (dropping it would unregister the directory).
        key = os.path.normcase(bin_dir)
        if key not in _REGISTERED_DLL_DIRECTORIES:
            _DLL_HANDLES.append(os.add_dll_directory(bin_dir))
            _REGISTERED_DLL_DIRECTORIES.add(key)
    return True
