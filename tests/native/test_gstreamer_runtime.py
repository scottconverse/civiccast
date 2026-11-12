# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "version"
    for item in (
        "dependencies/gstreamer/bin/gst-discoverer-1.0.exe",
        "dependencies/gstreamer/lib/gstreamer-1.0/gstcoreelements.dll",
        "dependencies/gstreamer/lib/girepository-1.0/Gst-1.0.typelib",
        "dependencies/gstreamer/python/gi/__init__.py",
    ):
        path = root / item
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    return root


def test_runtime_missing_consumer_is_red_before_gi_import(tmp_path: Path) -> None:
    from civiccast.native.gstreamer_runtime import (
        GstreamerRuntimeError,
        installed_gstreamer_environment,
    )

    root = _root(tmp_path)
    (root / "dependencies/gstreamer/bin/gst-discoverer-1.0.exe").unlink()
    with pytest.raises(GstreamerRuntimeError, match="consumer"):
        installed_gstreamer_environment(root)


def test_encoder_probe_loads_gi_from_the_installed_closure(tmp_path: Path) -> None:
    """The control plane probes before the worker has bootstrapped its own GI."""
    import subprocess

    root = _root(tmp_path)
    gi_root = root / "dependencies/gstreamer/python/gi"
    (gi_root / "__init__.py").write_text(
        "def require_version(namespace, version):\n"
        "    assert (namespace, version) == ('Gst', '1.0')\n",
        encoding="utf-8",
    )
    (gi_root / "repository.py").write_text(
        "class Gst:\n"
        "    @staticmethod\n"
        "    def init(args): pass\n"
        "    class ElementFactory:\n"
        "        @staticmethod\n"
        "        def find(name):\n"
        "            return Gst.ElementFactory() if name == 'mfh264enc' else None\n"
        "        def get_metadata(self, key):\n"
        "            assert key == 'klass'\n"
        "            return 'Codec/Encoder/Video/Hardware'\n",
        encoding="utf-8",
    )
    env = dict(os.environ, CIVICCAST_GSTREAMER_RUNTIME_ROOT=str(root))
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r});"
            "from civiccast.egress.gst.encoder_probe import "
            "probe_hardware_encoder, probe_element_registered;"
            "assert probe_hardware_encoder('mfh264enc');"
            "assert not probe_hardware_encoder('absent');"
            "assert probe_element_registered('mfh264enc');"
            "assert not probe_element_registered('absent')",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_runtime_preserves_inherited_path_without_mutating_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from civiccast.native.gstreamer_runtime import installed_gstreamer_environment

    root = _root(tmp_path)
    monkeypatch.setenv("PATH", "inherited")
    before = dict(os.environ)
    env = installed_gstreamer_environment(root)
    assert env["PATH"] == f"{root / 'dependencies/gstreamer/bin'}{os.pathsep}inherited"
    assert env["GI_TYPELIB_PATH"] == str(root / "dependencies/gstreamer/lib/girepository-1.0")
    assert dict(os.environ) == before


def test_runtime_environment_names_the_pygi_dll_directory(tmp_path: Path) -> None:
    # Candidate run 31187038070: the installed smoke's `import gi` died with
    # "Could not deduce DLL directories, please set PYGI_DLL_DIRS" -- the
    # staged PyGObject cannot deduce the DLL directory from this product's
    # installed layout, so the environment must name it explicitly, the same
    # way scripts/verify_native_runtime_closure.py already does for the
    # closure tree.
    from civiccast.native.gstreamer_runtime import installed_gstreamer_environment

    root = _root(tmp_path)
    env = installed_gstreamer_environment(root)
    assert env["PYGI_DLL_DIRS"] == str(root / "dependencies/gstreamer/bin")


def test_registry_cache_follows_the_ffmpeg_dlls_so_an_upgrade_rescans(tmp_path: Path) -> None:
    """Issue #324, measured on a station: new FFmpeg DLLs behind an unchanged
    gstlibav.dll kept the old element cache, and avenc_ac3 stayed invisible."""
    from civiccast.native.gstreamer_runtime import installed_gstreamer_environment

    root = _root(tmp_path)
    avcodec = root / "dependencies/gstreamer/bin/avcodec-61.dll"
    avcodec.write_bytes(b"rc5 codec set")
    base = {"LOCALAPPDATA": str(tmp_path / "local")}

    first = installed_gstreamer_environment(root, base_environment=base)["GST_REGISTRY"]
    again = installed_gstreamer_environment(root, base_environment=base)["GST_REGISTRY"]
    avcodec.write_bytes(b"rc6 codec set with ac3")
    upgraded = installed_gstreamer_environment(root, base_environment=base)["GST_REGISTRY"]

    assert first == again
    assert upgraded != first
    assert Path(first).parent == tmp_path / "local" / "civiccast-gstreamer"
    assert Path(first).name.startswith("registry-") and first.endswith(".bin")
    assert not Path(first).parent.exists()  # the function writes nothing


def test_registry_cache_keeps_an_explicit_caller_choice(tmp_path: Path) -> None:
    from civiccast.native.gstreamer_runtime import installed_gstreamer_environment

    env = installed_gstreamer_environment(
        _root(tmp_path), base_environment={"GST_REGISTRY": "C:/chosen/registry.bin"}
    )
    assert env["GST_REGISTRY"] == "C:/chosen/registry.bin"


def _bootstrap_precedes_gi_import(source: str) -> bool:
    tree = ast.parse(source)
    bootstrap_line = next(
        (
            node.lineno
            for node in tree.body
            if isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "bootstrap_installed_gstreamer_runtime"
        ),
        None,
    )
    gi_import_line = next(
        (
            node.lineno
            for node in tree.body
            if (isinstance(node, ast.Import) and any(alias.name == "gi" for alias in node.names))
            or (isinstance(node, ast.ImportFrom) and node.module == "gi")
        ),
        None,
    )
    return (
        bootstrap_line is not None
        and gi_import_line is not None
        and bootstrap_line < gi_import_line
    )


def test_engine_bootstrap_call_precedes_the_actual_gi_import() -> None:
    source = (Path(__file__).resolve().parents[2] / "civiccast/egress/gst/engine.py").read_text(
        encoding="utf-8"
    )
    assert _bootstrap_precedes_gi_import(source)


def test_engine_ordering_proof_rejects_a_bootstrap_call_moved_after_gi_import() -> None:
    source = "import gi\nbootstrap_installed_gstreamer_runtime()\n"
    assert _bootstrap_precedes_gi_import(source) is False


def test_station_activation_uses_the_embedded_app_payload_tree_not_a_third_component() -> None:
    # K2 fix: the closure rides inside the native-app-payload pack, which
    # extracts to `<root>/runtime` (native_pack_staging::
    # pack_extraction_destination), so the gate must probe
    # `<root>/runtime/dependencies/gstreamer` -- not a fourth top-level
    # `<root>/dependencies/gstreamer` pack, and not a separately staged
    # `native-gstreamer-runtime` component either.
    source = (
        Path(__file__).resolve().parents[2] / "civiccast/native/station_runtime.py"
    ).read_text(encoding="utf-8")
    assert 'gstreamer_runtime_root / "dependencies" / "gstreamer"' in source
    assert "native-gstreamer-runtime" not in source


# ---------------------------------------------------------------------------
# Gate A T4 root cause (2026-09): PATH is load-bearing for the bundled `gi`.
#
# girepository resolves a namespace's GTypes by asking GModule to open the
# typelib's shared library BY BARE NAME (`gstreamer-1.0-0.dll`) -- a plain
# Win32 LoadLibrary, which searches PATH and NOT the per-process directory
# list `os.add_dll_directory` feeds (only CPython's own extension loader reads
# that one). With `<runtime>/dependencies/gstreamer/bin` missing from PATH the
# symbol lookup fails silently, `Gst.URIHandler.__info__.get_g_type()` returns
# G_TYPE_NONE, and importing `gi.overrides.Gst` dies with
# `TypeError: must be an interface` -- exactly how the shipped playout worker
# died in Gate A run 33790253168. Reproduced with the b78b9c7 kit's own
# runtime: with the bin dir on PATH the worker imports; with only
# PYGI_DLL_DIRS + os.add_dll_directory and no PATH entry, it does not.
# ---------------------------------------------------------------------------


def test_bootstrap_puts_the_gstreamer_bin_on_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The bootstrap inserts the runtime's python dir into sys.path (real behaviour);
    # restore sys.path on teardown so this fixture tree's stub ``gi`` package can
    # never shadow the real one for later tests (mutation job 2026-09-03: NameError
    # from the stub in test_worker_pipe_seam).
    monkeypatch.setattr(sys, "path", list(sys.path))
    from civiccast.native.gstreamer_runtime import bootstrap_installed_gstreamer_runtime

    root = _root(tmp_path)
    bin_dir = str(root / "dependencies/gstreamer/bin")
    monkeypatch.setenv("CIVICCAST_GSTREAMER_RUNTIME_ROOT", str(root))
    monkeypatch.setenv("PATH", "inherited")
    monkeypatch.delenv("GI_TYPELIB_PATH", raising=False)
    monkeypatch.delenv("GST_PLUGIN_PATH", raising=False)

    assert bootstrap_installed_gstreamer_runtime() is True

    assert os.environ["PATH"].split(os.pathsep)[0] == bin_dir
    assert "inherited" in os.environ["PATH"], "the inherited PATH must survive"
    assert os.environ["GI_TYPELIB_PATH"] == str(
        root / "dependencies/gstreamer/lib/girepository-1.0"
    )
    assert os.environ["GST_PLUGIN_PATH"] == str(root / "dependencies/gstreamer/lib/gstreamer-1.0")


def test_bootstrap_does_not_duplicate_the_bin_dir_on_repeat_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The bootstrap inserts the runtime's python dir into sys.path (real behaviour);
    # restore sys.path on teardown so this fixture tree's stub ``gi`` package can
    # never shadow the real one for later tests (mutation job 2026-09-03: NameError
    # from the stub in test_worker_pipe_seam).
    monkeypatch.setattr(sys, "path", list(sys.path))
    from civiccast.native.gstreamer_runtime import bootstrap_installed_gstreamer_runtime

    root = _root(tmp_path)
    bin_dir = str(root / "dependencies/gstreamer/bin")
    monkeypatch.setenv("CIVICCAST_GSTREAMER_RUNTIME_ROOT", str(root))
    monkeypatch.setenv("PATH", "inherited")

    bootstrap_installed_gstreamer_runtime()
    bootstrap_installed_gstreamer_runtime()

    assert os.environ["PATH"].split(os.pathsep).count(bin_dir) == 1


def test_bootstrap_leaves_an_operator_supplied_typelib_path_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The bootstrap inserts the runtime's python dir into sys.path (real behaviour);
    # restore sys.path on teardown so this fixture tree's stub ``gi`` package can
    # never shadow the real one for later tests (mutation job 2026-09-03: NameError
    # from the stub in test_worker_pipe_seam).
    monkeypatch.setattr(sys, "path", list(sys.path))
    from civiccast.native.gstreamer_runtime import bootstrap_installed_gstreamer_runtime

    root = _root(tmp_path)
    monkeypatch.setenv("CIVICCAST_GSTREAMER_RUNTIME_ROOT", str(root))
    monkeypatch.setenv("GI_TYPELIB_PATH", "operator-choice")

    bootstrap_installed_gstreamer_runtime()

    assert os.environ["GI_TYPELIB_PATH"] == "operator-choice"


def test_bootstrap_gives_a_fresh_subprocess_the_bin_dir_with_no_inherited_gst_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failing production shape: a child process launched with only
    CIVICCAST_GSTREAMER_RUNTIME_ROOT (the rest of the GStreamer environment
    lost on the way down) must still end up with the bundled bin directory on
    its own PATH before anything imports `gi`."""
    import subprocess
    import sys

    root = _root(tmp_path)
    bin_dir = str(root / "dependencies/gstreamer/bin")
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in ("GI_TYPELIB_PATH", "GST_PLUGIN_PATH", "PYGI_DLL_DIRS", "PATH")
    }
    env["CIVICCAST_GSTREAMER_RUNTIME_ROOT"] = str(root)
    env["PATH"] = "inherited"
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os;"
            "from civiccast.native.gstreamer_runtime import "
            "bootstrap_installed_gstreamer_runtime as b;"
            "b();"
            "print(os.environ['PATH'])",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().split(os.pathsep)[0] == bin_dir


def test_repeated_reload_probes_register_the_dll_directory_only_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 2026-10-26 black/silent station outage, at the seam that caused it.

    ``bootstrap_installed_gstreamer_runtime`` is re-entered on the control
    plane's every content-reload encoder probe (``encoder_probe.
    gst_factory_klass`` -> ``bootstrap_installed_gstreamer_runtime``). Each
    ``os.add_dll_directory`` call appends another entry to a process-wide
    loader search path whose buffer is a fixed 32,767 characters, so a
    long-running control plane eventually exhausted it: after that, EVERY
    bootstrap -- and so every reload probe -- raised
    ``FileNotFoundError: [WinError 206] The filename or extension is too long``
    with the (perfectly valid, 70-character) installed ``bin`` path, for the
    life of the process. The reload dispatch died, the channel pinned
    TRANSITIONING, and each retry re-entered the same poisoned probe.

    Measured on the installed runtime (probe #460 raised, byte-identical
    traceback) before this test's fix; 600 probes all succeed after it.
    """
    import types

    import civiccast.native.gstreamer_runtime as runtime

    root = _root(tmp_path)
    bin_dir = str(root / "dependencies/gstreamer/bin")
    monkeypatch.setenv("CIVICCAST_GSTREAMER_RUNTIME_ROOT", str(root))
    monkeypatch.setattr(runtime, "_DLL_HANDLES", [])
    monkeypatch.setattr(runtime, "_REGISTERED_DLL_DIRECTORIES", set())

    # Faithful model of the Windows loader, calibrated against this host with
    # the installed GStreamer bin path: the search path is a fixed 32,767
    # character buffer, a call that would overflow it fails with WinError 206,
    # and once the buffer is full every later call fails too.
    budget = 32767
    registered: list[str] = []
    handles: list[object] = []

    def add_dll_directory(path: str) -> object:
        if sum(len(item) + 1 for item in registered) + len(path) + 1 > budget:
            raise OSError(206, "The filename or extension is too long", path)
        registered.append(path)
        handle = object()
        handles.append(handle)
        return handle

    # A stand-in for the `os` module whose loader hooks are Windows-shaped, so the
    # Windows-only tail of the bootstrap is exercised on every CI runner. It must be
    # a shim rather than `monkeypatch.setattr(os, "name", "nt")`: `pathlib.Path` picks
    # WindowsPath from `os.name` at construction time, which breaks on POSIX.
    shim = types.SimpleNamespace(
        name="nt",
        path=os.path,
        pathsep=os.pathsep,
        environ=dict(os.environ),
        add_dll_directory=add_dll_directory,
        lexists=os.path.lexists,
    )
    monkeypatch.setattr(runtime, "os", shim)

    for _ in range(600):
        assert runtime.bootstrap_installed_gstreamer_runtime() is True

    assert registered == [bin_dir], "the bin directory must be registered exactly once"
    assert len(handles) == 1, "the single returned handle must stay referenced"
