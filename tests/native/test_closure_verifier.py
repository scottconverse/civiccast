# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Red-first tests for the D6 closure-verification suite's pure logic.

`scripts/verify_native_runtime_closure.py` runs its GStreamer-dependent
checks (factory sweep, plugin origin, caption round-trip, GPL factory
absence) inside a child process launched with a hostile environment -- that
part needs a real built tree and PyGObject and is not exercised here. This
module tests everything that does NOT need either: the hostile-environment
builder, the tree-containment predicate (with the classic prefix-matching
bug as an explicit regression case), the pass/fail/skip aggregator, and the
hardware-gated-vs-genuine-miss classifier.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
from ctypes import wintypes
from pathlib import Path

import pytest

from scripts.verify_native_runtime_closure import (
    HARDWARE_GATED_FACTORIES,
    CheckResult,
    _caption_probe_survived,
    _classify_traced_accesses,
    _interpret_child_output,
    _match_declared_dependency,
    _OpenHandleSampler,
    _pad_caps_name_is_video,
    aggregate_exit_code,
    build_hostile_environment,
    check_cli_consumer_verification,
    classify_missing_factories,
    find_gpl_plugin_files,
    find_missing_required_plugin_files,
    find_plugins_outside_tree,
    find_present_gpl_factories,
    is_inside_tree,
)

_WINDOWS_PATH_TEST = pytest.mark.skipif(
    os.name != "nt", reason="requires native Windows handle and path semantics"
)

# ---------------------------------------------------------------------------
# Hostile environment builder
# ---------------------------------------------------------------------------


def test_hostile_environment_sets_every_documented_variable(tmp_path: Path) -> None:
    tree = tmp_path / "out"
    registry_path = tmp_path / "registry.bin"
    base_env = {"SystemRoot": r"C:\Windows", "TEMP": r"C:\Users\someone\AppData\Local\Temp"}

    env = build_hostile_environment(tree, base_env=base_env, registry_path=registry_path)

    assert env["GST_PLUGIN_PATH"] == str(tree / "lib" / "gstreamer-1.0")
    assert env["GST_PLUGIN_SYSTEM_PATH"] == ""
    assert env["GST_REGISTRY"] == str(registry_path)
    assert env["GI_TYPELIB_PATH"] == str(tree / "lib" / "girepository-1.0")
    assert env["GIO_MODULE_DIR"] == str(tree / "lib" / "gio" / "modules")
    assert env["PYTHONPATH"] == str(tree / "python")


def test_hostile_environment_preserves_windows_known_cache_roots(tmp_path: Path) -> None:
    tree = tmp_path / "out"
    base_env = {
        "SystemRoot": r"C:\Windows",
        "LOCALAPPDATA": r"C:\Users\station\AppData\Local",
        "PROGRAMDATA": r"C:\ProgramData",
    }

    env = build_hostile_environment(tree, base_env=base_env, registry_path=tmp_path / "r.bin")

    assert env["LOCALAPPDATA"] == base_env["LOCALAPPDATA"]
    assert env["PROGRAMDATA"] == base_env["PROGRAMDATA"]


def test_consumer_check_fails_when_the_verified_tree_has_no_cli_consumer(tmp_path: Path) -> None:
    result = check_cli_consumer_verification(tmp_path)

    assert result.status == "FAIL"
    assert "gst-discoverer-1.0.exe" in result.detail


def test_consumer_check_refuses_an_unhashed_consumer(tmp_path: Path) -> None:
    from scripts import verify_native_runtime_closure as verifier

    for name in ("gst-discoverer-1.0.exe", "gst-inspect-1.0.exe"):
        consumer = tmp_path / "bin" / name
        consumer.parent.mkdir(exist_ok=True)
        consumer.write_bytes(b"consumer")
    (tmp_path / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "lock_sha256": hashlib.sha256(verifier.REQUIREMENTS_FILE.read_bytes()).hexdigest(),
                "files": [],
            }
        ),
        encoding="utf-8",
    )

    result = check_cli_consumer_verification(tmp_path)

    assert result.status == "FAIL"
    assert "runtime-manifest.json" in result.detail


def _consumer_tree(tmp_path: Path, names: tuple[str, ...]) -> Path:
    from scripts import verify_native_runtime_closure as verifier

    files = []
    for name in names:
        consumer = tmp_path / "bin" / name
        consumer.parent.mkdir(exist_ok=True)
        consumer.write_bytes(name.encode("ascii"))
        files.append(
            {
                "path": f"bin/{name}",
                "sha256": hashlib.sha256(consumer.read_bytes()).hexdigest(),
                "bytes": consumer.stat().st_size,
                "distribution": "gstreamer_cli",
            }
        )
    (tmp_path / "runtime-manifest.json").write_text(
        json.dumps(
            {
                "lock_sha256": hashlib.sha256(verifier.REQUIREMENTS_FILE.read_bytes()).hexdigest(),
                "files": files,
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_consumer_check_rejects_a_tree_with_gst_inspect_missing(tmp_path: Path) -> None:
    result = check_cli_consumer_verification(_consumer_tree(tmp_path, ("gst-discoverer-1.0.exe",)))

    assert result.status == "FAIL"
    assert "gst-inspect-1.0.exe" in result.detail


@pytest.mark.parametrize("name", ("gst-discoverer-1.0.exe", "gst-inspect-1.0.exe"))
def test_consumer_check_rejects_wrong_owner_or_hash_for_each_consumer(
    tmp_path: Path, name: str
) -> None:
    tree = _consumer_tree(tmp_path, ("gst-discoverer-1.0.exe", "gst-inspect-1.0.exe"))
    manifest = json.loads((tree / "runtime-manifest.json").read_text(encoding="utf-8"))
    entry = next(item for item in manifest["files"] if item["path"] == f"bin/{name}")
    entry["distribution"] = "wrong-owner"
    (tree / "runtime-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = check_cli_consumer_verification(tree)

    assert result.status == "FAIL"
    assert name in result.detail or "runtime-manifest.json" in result.detail


@pytest.mark.parametrize("name", ("gst-discoverer-1.0.exe", "gst-inspect-1.0.exe"))
def test_consumer_check_rejects_wrong_hash_for_each_consumer(tmp_path: Path, name: str) -> None:
    tree = _consumer_tree(tmp_path, ("gst-discoverer-1.0.exe", "gst-inspect-1.0.exe"))
    manifest = json.loads((tree / "runtime-manifest.json").read_text(encoding="utf-8"))
    entry = next(item for item in manifest["files"] if item["path"] == f"bin/{name}")
    entry["sha256"] = "0" * 64
    (tree / "runtime-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = check_cli_consumer_verification(tree)

    assert result.status == "FAIL"
    assert name in result.detail


def test_hostile_environment_path_is_scrubbed_to_system_dirs_plus_tree_bin(tmp_path: Path) -> None:
    tree = tmp_path / "out"
    base_env = {
        "SystemRoot": r"C:\Windows",
        # A contaminated dev box PATH the hostile env must NOT inherit.
        "PATH": r"C:\Tools\SomeOtherGStreamer\bin;C:\Windows\System32;C:\Users\someone\bin",
    }

    env = build_hostile_environment(tree, base_env=base_env, registry_path=tmp_path / "r.bin")

    path_entries = env["PATH"].split(";")
    assert str(tree / "bin") in path_entries
    normalized_path_entries = {entry.replace("/", "\\").casefold() for entry in path_entries}
    assert r"c:\windows\system32" in normalized_path_entries
    # The contaminated entries from the caller's real PATH must be absent.
    assert r"C:\Tools\SomeOtherGStreamer\bin" not in path_entries
    assert r"C:\Users\someone\bin" not in path_entries


def test_hostile_environment_does_not_leak_callers_gst_plugin_system_path(tmp_path: Path) -> None:
    tree = tmp_path / "out"
    base_env = {
        "SystemRoot": r"C:\Windows",
        "GST_PLUGIN_SYSTEM_PATH": r"C:\Program Files\GStreamer\1.0\msvc_x86_64\lib\gstreamer-1.0",
    }

    env = build_hostile_environment(tree, base_env=base_env, registry_path=tmp_path / "r.bin")

    assert env["GST_PLUGIN_SYSTEM_PATH"] == ""


def test_hostile_environment_does_not_leak_callers_gst_registry(tmp_path: Path) -> None:
    tree = tmp_path / "out"
    stale_registry = tmp_path / "cached-registry.bin"
    base_env = {"SystemRoot": r"C:\Windows", "GST_REGISTRY": str(stale_registry)}
    fresh_registry = tmp_path / "fresh-registry.bin"

    env = build_hostile_environment(tree, base_env=base_env, registry_path=fresh_registry)

    assert env["GST_REGISTRY"] == str(fresh_registry)
    assert env["GST_REGISTRY"] != str(stale_registry)


def test_hostile_environment_is_a_fresh_dict_not_a_mutated_base_env(tmp_path: Path) -> None:
    """Regression guard: the builder must never mutate the caller's mapping
    in place -- a mutated `os.environ`-derived dict would corrupt the
    parent verifier process's own environment for the rest of the run."""
    tree = tmp_path / "out"
    base_env = {"SystemRoot": r"C:\Windows", "PATH": r"C:\Windows\System32"}
    original = dict(base_env)

    build_hostile_environment(tree, base_env=base_env, registry_path=tmp_path / "r.bin")

    assert base_env == original


# ---------------------------------------------------------------------------
# Tree-containment predicate -- the classic prefix-matching bug
# ---------------------------------------------------------------------------


def test_is_inside_tree_accepts_a_real_child_path(tmp_path: Path) -> None:
    tree = tmp_path / "rt"
    (tree / "bin").mkdir(parents=True)
    candidate = tree / "bin" / "glib-2.0-0.dll"
    candidate.touch()

    assert is_inside_tree(tree, candidate) is True


def test_is_inside_tree_rejects_a_sibling_directory_sharing_a_name_prefix(tmp_path: Path) -> None:
    """The classic bug: naive `str(candidate).startswith(str(tree))` treats
    `C:/x/rt-evil/a.dll` as inside `C:/x/rt` because the strings share a
    prefix. A correct implementation must reject it."""
    tree = tmp_path / "x" / "rt"
    evil = tmp_path / "x" / "rt-evil"
    tree.mkdir(parents=True)
    evil.mkdir(parents=True)
    candidate = evil / "a.dll"
    candidate.touch()

    assert is_inside_tree(tree, candidate) is False


def test_is_inside_tree_rejects_an_unrelated_directory(tmp_path: Path) -> None:
    tree = tmp_path / "rt"
    other = tmp_path / "somewhere" / "else"
    tree.mkdir(parents=True)
    other.mkdir(parents=True)
    candidate = other / "decoy.dll"
    candidate.touch()

    assert is_inside_tree(tree, candidate) is False


def test_is_inside_tree_accepts_the_tree_root_itself(tmp_path: Path) -> None:
    tree = tmp_path / "rt"
    tree.mkdir()

    assert is_inside_tree(tree, tree) is True


# ---------------------------------------------------------------------------
# Result aggregator
# ---------------------------------------------------------------------------


def test_aggregate_exit_code_is_zero_when_every_check_passes() -> None:
    results = [
        CheckResult("a", "PASS", "ok"),
        CheckResult("b", "PASS", "ok"),
    ]
    assert aggregate_exit_code(results) == 0


def test_aggregate_exit_code_is_nonzero_when_any_check_fails() -> None:
    results = [
        CheckResult("a", "PASS", "ok"),
        CheckResult("b", "FAIL", "boom"),
    ]
    assert aggregate_exit_code(results) != 0


def test_aggregate_exit_code_treats_skipped_as_neither_pass_nor_fail() -> None:
    """A SKIPPED check (e.g. the manifest verifier module not existing yet)
    must not flip the exit code on its own."""
    all_pass_and_skip = [
        CheckResult("a", "PASS", "ok"),
        CheckResult("b", "SKIPPED", "module not available yet"),
    ]
    assert aggregate_exit_code(all_pass_and_skip) == 0

    skip_and_fail = [
        CheckResult("a", "SKIPPED", "module not available yet"),
        CheckResult("b", "FAIL", "boom"),
    ]
    assert aggregate_exit_code(skip_and_fail) != 0


def test_aggregate_exit_code_of_empty_results_is_zero() -> None:
    assert aggregate_exit_code([]) == 0


# ---------------------------------------------------------------------------
# Hardware-gated factory classification
# ---------------------------------------------------------------------------


def test_hardware_gated_factories_includes_the_documented_examples() -> None:
    """mfh264enc/mfh265enc/nvh264enc are the spec's named examples of
    factories that only register on matching hardware/drivers."""
    assert {"mfh264enc", "mfh265enc", "nvh264enc"} <= HARDWARE_GATED_FACTORIES


def test_classify_missing_factories_separates_hardware_gated_from_genuine() -> None:
    missing = ["mfh264enc", "nvh264enc", "h264parse", "queue"]

    hardware_gated, genuine = classify_missing_factories(missing)

    assert hardware_gated == ("mfh264enc", "nvh264enc")
    assert genuine == ("h264parse", "queue")


def test_classify_missing_factories_excuses_a_hardware_gated_miss_only_when_its_plugin_file_is_present() -> (
    None
):
    """Repurposed from the old
    `test_classify_missing_factories_handles_an_all_hardware_gated_miss_list`,
    which pinned the masking bug (a REQUIRED factory whose plugin DLL is
    entirely absent from the tree still reported as a harmless
    hardware-gated miss) as if it were a feature. `plugin_file_missing=()`
    here means gstmediafoundation.dll genuinely IS present in the tree, so
    mfh265enc's factory not registering really is an expected hardware gate
    (no matching Media Foundation MFT on this build box) -- excused. See the
    sibling test below for the case this one used to hide."""
    hardware_gated, genuine = classify_missing_factories(
        ["mfh265enc"], plugin_file_missing=frozenset()
    )
    assert hardware_gated == ("mfh265enc",)
    assert genuine == ()


def test_classify_missing_factories_never_excuses_a_hardware_gated_miss_whose_plugin_file_is_absent() -> (
    None
):
    """The bug an adversarial review caught: mfh264enc/mfh265enc are named
    in HARDWARE_GATED_FACTORIES, but if gstmediafoundation.dll -- the plugin
    that provides them -- is entirely absent from the packaged tree, that is
    a packaging closure bug, not an expected hardware gate. Being named
    hardware-gated must never excuse a missing PLUGIN FILE; only a missing
    FACTORY REGISTRATION with the plugin file genuinely present may be
    excused."""
    hardware_gated, genuine = classify_missing_factories(
        ["mfh264enc", "mfh265enc"],
        plugin_file_missing=frozenset({"mfh264enc", "mfh265enc"}),
    )
    assert hardware_gated == ()
    assert genuine == ("mfh264enc", "mfh265enc")


def test_classify_missing_factories_excuses_only_the_names_whose_plugin_file_is_present() -> None:
    """Mixed case: two hardware-gated names miss, only one's plugin file is
    actually absent -- only that one loses its excuse."""
    hardware_gated, genuine = classify_missing_factories(
        ["mfh264enc", "mfh265enc"], plugin_file_missing=frozenset({"mfh264enc"})
    )
    assert hardware_gated == ("mfh265enc",)
    assert genuine == ("mfh264enc",)


def test_classify_missing_factories_handles_an_empty_miss_list() -> None:
    hardware_gated, genuine = classify_missing_factories([])
    assert hardware_gated == ()
    assert genuine == ()


def test_classify_missing_factories_does_not_silently_drop_a_genuine_miss() -> None:
    """A real closure bug (a required, non-hardware-gated factory missing)
    must never be classified away as hardware-gated."""
    hardware_gated, genuine = classify_missing_factories(["mfh264enc", "openh264enc"])
    assert "openh264enc" in genuine
    assert "openh264enc" not in hardware_gated


# ---------------------------------------------------------------------------
# Plugin-file presence -- the packaging question, distinct from whether a
# factory registers (a hardware question). An absent plugin file is ALWAYS a
# hard fail for a required factory, hardware-gated or not.
# ---------------------------------------------------------------------------


def test_find_missing_required_plugin_files_flags_an_absent_plugin_dll(tmp_path: Path) -> None:
    tree = tmp_path / "rt"
    plugin_dir = tree / "lib" / "gstreamer-1.0"
    plugin_dir.mkdir(parents=True)
    # gstmediafoundation.dll (mfh264enc/mfh265enc's plugin) is deliberately
    # NOT created -- this is the entirely-absent-plugin scenario.
    (plugin_dir / "gstopenh264.dll").touch()

    missing = find_missing_required_plugin_files(tree, ["mfh264enc", "mfh265enc", "openh264enc"])

    assert missing == ("mfh264enc", "mfh265enc")


def test_find_missing_required_plugin_files_is_empty_when_the_plugin_file_is_present(
    tmp_path: Path,
) -> None:
    tree = tmp_path / "rt"
    plugin_dir = tree / "lib" / "gstreamer-1.0"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "gstmediafoundation.dll").touch()

    missing = find_missing_required_plugin_files(tree, ["mfh264enc", "mfh265enc"])

    assert missing == ()


def test_find_missing_required_plugin_files_handles_an_empty_factory_list(tmp_path: Path) -> None:
    tree = tmp_path / "rt"
    # lib/gstreamer-1.0 deliberately does not exist -- must not be touched
    # when there is nothing to check.
    assert find_missing_required_plugin_files(tree, []) == ()


# ---------------------------------------------------------------------------
# Plugin-origin pure logic (check 2's decision function, no gi required)
# ---------------------------------------------------------------------------


def test_find_plugins_outside_tree_flags_a_plugin_loaded_from_outside(tmp_path: Path) -> None:
    tree = tmp_path / "rt"
    tree.mkdir()
    inside = tree / "lib" / "gstreamer-1.0" / "gstcoreelements.dll"
    inside.parent.mkdir(parents=True)
    inside.touch()
    outside = tmp_path / "decoy" / "gstcoreelements.dll"
    outside.parent.mkdir(parents=True)
    outside.touch()

    result = find_plugins_outside_tree(
        tree, [("coreelements", str(inside)), ("decoy", str(outside))]
    )

    assert result == (("decoy", str(outside)),)


def test_find_plugins_outside_tree_ignores_plugins_with_no_backing_file(tmp_path: Path) -> None:
    """Statically-linked/basetype plugins report `get_filename() -> None` --
    there is no file to be outside of, so they must never be flagged."""
    tree = tmp_path / "rt"
    tree.mkdir()

    result = find_plugins_outside_tree(tree, [("staticbasetype", None)])

    assert result == ()


def test_find_plugins_outside_tree_rejects_the_sibling_prefix_bug(tmp_path: Path) -> None:
    tree = tmp_path / "x" / "rt"
    tree.mkdir(parents=True)
    evil_dir = tmp_path / "x" / "rt-evil"
    evil_dir.mkdir(parents=True)
    evil_plugin = evil_dir / "gstsneaky.dll"
    evil_plugin.touch()

    result = find_plugins_outside_tree(tree, [("sneaky", str(evil_plugin))])

    assert result == (("sneaky", str(evil_plugin)),)


# ---------------------------------------------------------------------------
# GPL negative control pure logic (check 4, no gi required)
# ---------------------------------------------------------------------------


def test_find_gpl_plugin_files_detects_a_present_gpl_dll(tmp_path: Path) -> None:
    tree = tmp_path / "rt"
    plugin_dir = tree / "lib" / "gstreamer-1.0"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "gstx264.dll").touch()

    present = find_gpl_plugin_files(tree, ["gstx264.dll", "gstx265.dll"])

    assert present == ("gstx264.dll",)


def test_find_gpl_plugin_files_is_empty_for_a_clean_tree(tmp_path: Path) -> None:
    tree = tmp_path / "rt"
    plugin_dir = tree / "lib" / "gstreamer-1.0"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "gstopenh264.dll").touch()

    present = find_gpl_plugin_files(tree, ["gstx264.dll", "gstx265.dll"])

    assert present == ()


def test_find_present_gpl_factories_flags_a_resolvable_excluded_factory() -> None:
    present = find_present_gpl_factories(
        ["x264enc", "x265enc"], {"x264enc": True, "x265enc": False}
    )
    assert present == ("x264enc",)


def test_find_present_gpl_factories_empty_when_none_resolve() -> None:
    present = find_present_gpl_factories(
        ["x264enc", "x265enc"], {"x264enc": False, "x265enc": False}
    )
    assert present == ()


# ---------------------------------------------------------------------------
# Child-output interpretation -- must never mistake "nothing ran" for "ran
# clean". Regression coverage for a real bug found by an end-to-end run: a
# child that failed to initialize GStreamer at all reported empty
# missing/outside_tree/present lists, which the interpreter was reading as
# a trivial PASS on every one of the four checks that never actually ran.
# ---------------------------------------------------------------------------


def _fake_child(
    stdout: str, *, returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["fake"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _clean_child_payload() -> dict[str, object]:
    return {
        "gst_init_ok": True,
        "gst_init_detail": "GStreamer initialized: GStreamer 1.28.5",
        "factory_sweep": {
            "missing": [],
            "checked_count": 51,
            "detail": "all required factories resolved",
        },
        "plugin_origin": {
            "outside_tree": [],
            "checked_count": 40,
            "detail": "all loaded plugins resolve inside the tree",
        },
        "caption_leg": {
            "ok": True,
            "detail": "caption text survived the embed+decode-back round trip",
        },
        "gpl_factories": {"present": [], "detail": "no GPL-excluded factories resolve"},
        # A clean sweep still has to have TRACED something. An empty trace is
        # treated as a FAIL, not a pass, because an empty trace means the hook
        # never installed -- it is not evidence that nothing was loaded.
        #
        # Filled in per-test by `_with_traced_file` with a path INSIDE the tree.
        # It used to be `__file__`, which passed only because `sys.path` was a
        # permitted root; removing that root (r2-001) correctly made this test
        # file an outside-tree load.
        "accessed_paths": [],
        "loaded_modules": [],
        "sampled_handles": [],
        # Non-zero: a clean sweep is one where the handle sampler ACTUALLY RAN.
        # Zero is its own FAIL, because a sampler that never sampled looks
        # exactly like a run in which native code opened nothing, and only one
        # of those two readings is safe.
        "handle_samples": 128,
        # The positive control. A clean sweep is one where the sampler PROVED it
        # can see native access, not merely one where it ran.
        "native_canary": "",
    }


def _with_traced_file(payload: dict[str, object], tree: Path) -> dict[str, object]:
    """Give a payload one traced path that is genuinely inside ``tree``."""
    tree.mkdir(parents=True, exist_ok=True)
    traced = tree / "bin" / "glib-2.0-0.dll"
    traced.parent.mkdir(parents=True, exist_ok=True)
    traced.write_bytes(b"")
    payload["accessed_paths"] = [str(traced)]
    payload["loaded_modules"] = [str(traced)]
    # The canary must be a real file the sampler observed, or every payload
    # would fail the positive-control gate for the wrong reason.
    canary = tree / "SHA256SUMS"
    canary.write_text("", encoding="utf-8")
    payload["native_canary"] = str(canary)
    payload["sampled_handles"] = [str(canary)]
    return payload


def test_interpret_child_output_fails_every_check_when_gst_never_initialized(
    tmp_path: Path,
) -> None:
    """The regression case: gst_init_ok=False with empty per-check payload
    data must FAIL factory_sweep, plugin_origin_check, caption_leg, and
    gpl_negative_control -- never read the emptiness as a clean sweep."""
    payload = _clean_child_payload()
    payload["gst_init_ok"] = False
    payload["gst_init_detail"] = (
        "GStreamer could not be initialized: ModuleNotFoundError: No module named 'gi'"
    )
    # Even though nothing ran, the per-check fields still carry the shape
    # they'd have on a genuinely clean sweep -- that emptiness is exactly
    # what must NOT be trusted.
    child = _fake_child(json.dumps(payload))

    results = _interpret_child_output(child, present_gpl_files=(), tree=tmp_path)

    statuses = {r.name: r.status for r in results}
    assert statuses == {
        "factory_sweep": "FAIL",
        "plugin_origin_check": "FAIL",
        "caption_leg": "FAIL",
        "gpl_negative_control": "FAIL",
        "dynamic_trace": "FAIL",
    }
    for result in results:
        assert "gi" in result.detail or "not be initialized" in result.detail


def test_interpret_child_output_passes_every_check_on_a_genuinely_clean_sweep(
    tmp_path: Path,
) -> None:
    """Sanity counterpart: a real clean payload (gst_init_ok=True, nothing
    missing/outside/unresolved) must still pass -- the fix must not make
    every run FAIL unconditionally."""
    child = _fake_child(json.dumps(_with_traced_file(_clean_child_payload(), tmp_path)))

    results = _interpret_child_output(child, present_gpl_files=(), tree=tmp_path)

    statuses = {r.name: r.status for r in results}
    assert statuses == {
        "factory_sweep": "PASS",
        "plugin_origin_check": "PASS",
        "caption_leg": "PASS",
        "gpl_negative_control": "PASS",
        # This test file lives on the import path, so it counts as a permitted
        # harness root rather than an outside-tree load.
        "dynamic_trace": "PASS",
    }


def test_every_check_is_reported_exactly_once_even_when_the_child_dies(
    tmp_path: Path,
) -> None:
    """The test that would have caught a bug the existing ones could not.

    The failure paths used to write `results[-1] = gpl_result`, which silently
    assumed the GPL entry was last. Appending `dynamic_trace` to the failure set
    broke that in four places at once: `[-1]` overwrote the TRACE, so the report
    lost a check and gained a DUPLICATE gpl entry.

    Every existing test keyed results by NAME into a dict -- and a duplicate name
    collapses into one key, so five entries still looked like four. The bug was
    invisible to the whole suite. Asserting on the LIST, not the dict, is what
    makes it visible.
    """
    payload = _clean_child_payload()
    payload["gst_init_ok"] = False
    payload["gst_init_detail"] = "GStreamer could not be initialized: no gi"
    child = _fake_child(json.dumps(payload))

    results = _interpret_child_output(child, present_gpl_files=(), tree=tmp_path)

    names = [r.name for r in results]
    assert sorted(names) == sorted(set(names)), f"a check was reported twice: {names}"
    assert set(names) == {
        "factory_sweep",
        "plugin_origin_check",
        "caption_leg",
        "gpl_negative_control",
        "dynamic_trace",
    }, "a check silently vanished from the report"
    assert all(r.status == "FAIL" for r in results)


def test_dynamic_trace_fails_when_it_captured_nothing(tmp_path: Path) -> None:
    """An empty trace is a broken trace, not a clean run.

    If the audit hook never installs or the module enumeration fails, the
    natural result is "no outside-tree paths found" -- which reads exactly like
    success. That silent-pass is the failure mode this check exists to prevent,
    so emptiness is reported as FAIL with the reason.
    """
    payload = _clean_child_payload()
    payload["accessed_paths"] = []
    payload["loaded_modules"] = []
    child = _fake_child(json.dumps(payload))

    results = _interpret_child_output(child, present_gpl_files=(), tree=tmp_path)

    trace = next(r for r in results if r.name == "dynamic_trace")
    assert trace.status == "FAIL"
    assert "captured NOTHING" in trace.detail


def test_a_natively_opened_outside_file_is_caught_by_the_handle_sampler(
    tmp_path: Path,
) -> None:
    """CC-WS5-PKG-001 (Codex r2, Blocker) -- the native-access coverage gap.

    The audit hook only sees opens that go through Python, and the end-of-run
    module snapshot only sees things still mapped. Neither observes GStreamer,
    fontconfig, GIO or Media Foundation calling CreateFileW directly, which is
    exactly the behaviour D2(d)'s trace exists to watch.

    Here the Python-visible legs are entirely clean and the ONLY evidence of
    the outside dependency is a handle the sampler caught open. It must fail,
    and it must name the file -- otherwise the sampler is decoration.
    """
    tree = tmp_path / "tree"
    outside = tmp_path / "elsewhere" / "native-only-dependency.dll"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_bytes(b"")

    payload = _with_traced_file(_clean_child_payload(), tree)
    # APPEND, so the positive control stays observed -- otherwise this would
    # fail because the canary went missing, not because the outside file was
    # caught, and the test would pass for the wrong reason.
    payload["sampled_handles"] = [*payload["sampled_handles"], str(outside)]

    child = _fake_child(json.dumps(payload))
    results = _interpret_child_output(child, present_gpl_files=(), tree=tree)
    result = next(r for r in results if r.name == "dynamic_trace")
    assert result.status == "FAIL"
    assert "native-only-dependency.dll" in result.detail


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_own_handle_enumeration_sees_a_real_open_file_and_skips_directories(
    tmp_path: Path,
) -> None:
    """The sampler's actual OS mechanism, exercised for real.

    Two properties, both learned the hard way on the first live run:

    1. It must SEE an open file. The original implementation used
       `psutil.Process().open_files()`, which enumerates the whole machine's
       handle table and dies on this box with "SystemExtendedHandleInformation
       buffer too big" -- so it silently saw nothing at all.
    2. It must SKIP directories. `GetFileType` reports a directory handle as a
       disk object, and Windows keeps one open for every process's working
       directory, so the first working version failed the whole suite on the
       repo root -- true, but not a file dependency.

    The directory assertion holds a REAL directory handle open and proves that
    exact directory is excluded. The original version stat-ed every returned
    path with Path.is_dir(), which is the same by-name stat CC-WS5-PKG-013
    proved can raise PermissionError on a handle path the process cannot stat
    -- the assertion mechanism itself was a crash site.
    """
    probe = tmp_path / "held-open.bin"
    probe.write_bytes(b"x")

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    file_flag_backup_semantics = 0x02000000  # required to open a DIRECTORY handle
    dir_handle = k32.CreateFileW(
        str(tmp_path), 0x80000000, 0x1 | 0x2 | 0x4, None, 3, file_flag_backup_semantics, None
    )
    assert dir_handle not in (None, ctypes.c_void_p(-1).value), (
        "precondition: the directory handle must open"
    )
    try:
        with probe.open("rb"):
            paths = _OpenHandleSampler._own_open_file_paths()
    finally:
        k32.CloseHandle(wintypes.HANDLE(dir_handle))

    assert any(Path(p) == probe for p in paths), "an open file must be observable"
    assert not any(Path(p) == tmp_path for p in paths), (
        "a directory held open through a real handle must be excluded"
    )


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_handle_enumeration_has_no_fixed_ceiling(tmp_path: Path) -> None:
    """CC-WS5-PKG-001 (Codex r3, Blocker) -- the auditor's exact falsification.

    The first sampler probed handle values 4, 8, 12 ... up to a hardcoded
    16384 and assumed nothing lived above. The auditor allocated ~4,300 event
    handles, opened a native file that landed at handle 18140, and watched the
    sampler miss it entirely.

    A scan ceiling is an assumption about how many handles a process will ever
    open -- and the entire purpose of this trace is to observe behaviour nobody
    predicted. Enumerating the real handle table removes the assumption instead
    of raising it.
    """
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateEventW.restype = wintypes.HANDLE
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]

    probe = tmp_path / "high-handle-native.bin"
    probe.write_bytes(b"x")

    burn = [k32.CreateEventW(None, True, False, None) for _ in range(4300)]
    handle = k32.CreateFileW(str(probe), 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    try:
        assert handle > 16384, (
            f"precondition: the probe must land above the OLD 16384 ceiling, got {handle}"
        )
        observed = _OpenHandleSampler._own_open_file_paths()
        assert any(Path(p) == probe for p in observed), (
            f"a file held open at handle {handle} was missed -- a ceiling is back"
        )
    finally:
        k32.CloseHandle(wintypes.HANDLE(handle))
        for burned in burn:
            k32.CloseHandle(wintypes.HANDLE(burned))


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_delete_pending_handle_costs_only_its_own_entry(tmp_path: Path) -> None:
    """CC-WS5-PKG-013 (Codex r5, Blocker) -- deterministic regression.

    A disk handle can carry a name the process cannot stat by pathname: the
    auditor's host held one under the NTFS deleted-object namespace,
    C:\\$Extend\\$Deleted\\..., where `Path(path).is_dir()` raised
    PermissionError. That crashed both real-handle tests, and inside the
    sampler thread the poll-level except discarded EVERY path from any poll
    containing one such handle -- so one unrelated handle silently blinded
    whole polls. Classification now comes from the handle itself
    (GetFileInformationByHandleEx / FileStandardInfo), so an unstatable or
    delete-pending handle costs exactly its own entry.

    Deterministic reproduction: open a file with FILE_SHARE_DELETE and unlink
    it while the handle is held. The handle enters the delete-pending state --
    on NTFS with POSIX delete semantics its only remaining name IS the
    $Extend\\$Deleted placeholder, the auditor's exact condition. The
    enumeration must not raise, must not report the deleted file, and must
    still see a canary held open in the SAME poll.
    """
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]

    canary = tmp_path / "canary-held-open.bin"
    canary.write_bytes(b"x")
    doomed = tmp_path / "deleted-while-open.bin"
    doomed.write_bytes(b"x")

    generic_read = 0x80000000
    share_read_write_delete = 0x1 | 0x2 | 0x4
    open_existing = 3
    handle = k32.CreateFileW(
        str(doomed), generic_read, share_read_write_delete, None, open_existing, 0x80, None
    )
    assert handle not in (None, ctypes.c_void_p(-1).value), (
        "precondition: the doomed file must open with FILE_SHARE_DELETE"
    )
    try:
        doomed.unlink()  # the held handle is now delete-pending
        with canary.open("rb"):
            observed = _OpenHandleSampler._own_open_file_paths()
        assert any(Path(p) == canary for p in observed), (
            "a healthy file in the SAME poll as a delete-pending handle must survive"
        )
        assert not any(Path(p) == doomed for p in observed), (
            "a file already unlinked from the namespace is not a shipped dependency"
        )
    finally:
        k32.CloseHandle(wintypes.HANDLE(handle))


def test_a_sampler_that_saw_nothing_native_fails_even_with_successful_polls(
    tmp_path: Path,
) -> None:
    """CC-WS5-PKG-001 (Codex r3, Blocker) -- the silently-ineffective sampler.

    The auditor built a payload with one Python access, zero mapped modules,
    zero sampled handles and 100 SUCCESSFUL polls. It returned PASS while the
    detail line advertised "via THREE mechanisms" -- two of which had produced
    nothing. Polling successfully is not the same as observing anything.

    The positive control makes that state unreachable: a real file is held open
    through CreateFileW for the whole run, so a sampler that reports nothing has
    demonstrably failed rather than found a clean tree.
    """
    tree = tmp_path / "tree"
    payload = _with_traced_file(_clean_child_payload(), tree)
    payload["sampled_handles"] = []  # 100 polls, nothing seen -- the auditor's shape
    payload["handle_samples"] = 100

    child = _fake_child(json.dumps(payload))
    results = _interpret_child_output(child, present_gpl_files=(), tree=tree)
    result = next(r for r in results if r.name == "dynamic_trace")

    assert result.status == "FAIL"
    assert "POSITIVE CONTROL" in result.detail


def test_a_sampler_that_never_sampled_fails_instead_of_reporting_a_clean_trace(
    tmp_path: Path,
) -> None:
    """Zero samples must not read as 'native code opened nothing'.

    These two states produce identical evidence -- an empty list of sampled
    handles -- and only one of them is safe. Treating them the same would let a
    broken sampler hand back a clean bill of health, which is the same class of
    bug as an empty trace being read as a clean run.
    """
    tree = tmp_path / "tree"
    payload = _with_traced_file(_clean_child_payload(), tree)
    payload["handle_samples"] = 0

    child = _fake_child(json.dumps(payload))
    results = _interpret_child_output(child, present_gpl_files=(), tree=tree)
    result = next(r for r in results if r.name == "dynamic_trace")
    assert result.status == "FAIL"
    assert "ZERO samples" in result.detail


def test_the_trace_states_its_remaining_blind_spot_rather_than_claiming_completeness(
    tmp_path: Path,
) -> None:
    """A passing trace must say what it still cannot see.

    Handle polling is sampled and single-process, so a file opened and closed
    between polls, or opened by a spawned child, is still missed. A reader who
    takes PASS to mean 'every file access was observed' would be wrong, so the
    detail line has to say so on the PASS path -- where it is actually read --
    not only in a docstring.
    """
    tree = tmp_path / "tree"
    payload = _with_traced_file(_clean_child_payload(), tree)

    child = _fake_child(json.dumps(payload))
    results = _interpret_child_output(child, present_gpl_files=(), tree=tree)
    result = next(r for r in results if r.name == "dynamic_trace")
    assert result.status == "PASS"
    assert "BLIND SPOT" in result.detail
    assert "SAMPLED" in result.detail
    assert "handle table" in result.detail


def _appinstaller_resource_path(
    package_root: Path,
    *,
    package: str = "Microsoft.DesktopAppInstaller_1.0.0.0_x64__8wekyb3d8bbwe",
    relative: tuple[str, ...] = (
        "microsoft.system.package.metadata",
        "S-1-5-21-1-1-1-1-MergedResources-0.pri",
    ),
) -> Path:
    path = package_root / package
    for part in relative:
        path /= part
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"resource index")
    return path


def _observed_package_manifest_path(
    package_root: Path,
    *,
    package: str = "Microsoft.SecHealthUI_1000.26100.33648.0_x64__8wekyb3d8bbwe",
    relative: tuple[str, ...] = ("AppxManifest.xml",),
) -> Path:
    path = package_root / package
    for part in relative:
        path /= part
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"package manifest")
    return path


@pytest.mark.parametrize("trace_field", ("accessed_paths", "sampled_handles", "mapped_data"))
def test_desktop_app_installer_resource_access_is_reported_without_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trace_field: str
) -> None:
    from scripts import verify_native_runtime_closure as verifier

    package_root = tmp_path / "W"
    monkeypatch.setattr(verifier, "_STORE_PACKAGE_ROOT", package_root)
    tree = tmp_path / "tree"
    resource = _appinstaller_resource_path(package_root)
    payload = _with_traced_file(_clean_child_payload(), tree)
    payload.setdefault(trace_field, []).append(str(resource))
    child = _fake_child(json.dumps(payload))

    results = _interpret_child_output(child, present_gpl_files=(), tree=tree)
    trace = next(result for result in results if result.name == "dynamic_trace")

    assert trace.status == "PASS"
    assert "DesktopAppInstaller package resource" in trace.detail
    assert str(resource.resolve()) in trace.detail


@pytest.mark.parametrize(
    "package",
    (
        "Microsoft.SecHealthUI_1000.26100.33648.0_x64__8wekyb3d8bbwe",
        "Microsoft.SecHealthUI_1000.26100.33438.0_x64__8wekyb3d8bbwe",
        "Microsoft.WindowsFeedbackHub_1.2512.16303.0_x64__8wekyb3d8bbwe",
        "MicrosoftCorporationII.WindowsSubsystemForLinux_3.0.3.0_x64__8wekyb3d8bbwe",
    ),
)
@pytest.mark.parametrize("trace_field", ("accessed_paths", "sampled_handles", "mapped_data"))
def test_observed_package_manifest_access_is_reported_without_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, package: str, trace_field: str
) -> None:
    from scripts import verify_native_runtime_closure as verifier

    package_root = tmp_path / "W"
    monkeypatch.setattr(verifier, "_STORE_PACKAGE_ROOT", package_root)
    tree = tmp_path / "tree"
    resource = _observed_package_manifest_path(package_root, package=package)
    payload = _with_traced_file(_clean_child_payload(), tree)
    payload.setdefault(trace_field, []).append(str(resource))
    child = _fake_child(json.dumps(payload))

    results = _interpret_child_output(child, present_gpl_files=(), tree=tree)
    trace = next(result for result in results if result.name == "dynamic_trace")

    assert trace.status == "PASS"
    assert "AppxManifest.xml" in trace.detail
    assert str(resource.resolve()) in trace.detail


@pytest.mark.parametrize(
    ("permissions", "expected_module", "expected_data", "expected_unknown"),
    (
        (("r",), False, True, False),
        (("xr",), True, False, False),
        ((None,), False, False, True),
        (("rr",), False, False, True),
        (("r", "?"), False, False, True),
        (("r", "xr"), True, False, False),
    ),
    ids=(
        "read-only-data",
        "executable",
        "unknown-is-strict",
        "malformed-is-strict",
        "mixed-unknown-wins",
        "mixed-executable-wins",
    ),
)
def test_memory_maps_separate_data_from_executable_or_unknown_regions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    permissions: tuple[str | None, ...],
    expected_module: bool,
    expected_data: bool,
    expected_unknown: bool,
) -> None:
    from types import SimpleNamespace

    from scripts import verify_native_runtime_closure as verifier

    package_root = tmp_path / "W"
    monkeypatch.setattr(verifier, "_STORE_PACKAGE_ROOT", package_root)
    resource = _appinstaller_resource_path(package_root)
    mappings = [SimpleNamespace(path=str(resource), perms=value) for value in permissions]

    modules, data, unknown = verifier._split_memory_mapping_paths(mappings)

    assert (str(resource) in modules) is expected_module
    assert (str(resource) in data) is expected_data
    assert (str(resource) in unknown) is expected_unknown


def test_native_bootstrap_baseline_excludes_only_preexisting_file_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import verify_native_runtime_closure as verifier

    initial = tmp_path / "bootstrap-resource.pri"
    later = tmp_path / "product-resource.pri"
    initial.write_bytes(b"existing native bootstrap mapping")
    later.write_bytes(b"new product mapping")
    monkeypatch.setattr(verifier, "_mapped_file_paths", lambda: ([], [str(initial)], []))
    baseline = verifier._harness_baseline_modules()
    assert verifier._drop_baseline([str(initial), str(later)], baseline) == [str(later)]

    tree = tmp_path / "tree"
    payload = _with_traced_file(_clean_child_payload(), tree)
    payload["mapped_data"] = verifier._drop_baseline([str(initial), str(later)], baseline)
    payload["trace_pid"] = 1234
    payload["trace_stages"] = {
        "before_gi_gst": [str(initial)],
        "after_factory_sweep": [str(initial), str(later)],
    }
    results = _interpret_child_output(
        _fake_child(json.dumps(payload)), present_gpl_files=(), tree=tree
    )
    trace = next(result for result in results if result.name == "dynamic_trace")
    assert trace.status == "FAIL"
    assert str(later) in trace.detail
    assert "first mapped at after_factory_sweep; child pid=1234" in trace.detail


def test_executable_mapping_classification_keeps_every_file_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from scripts import verify_native_runtime_closure as verifier

    package_root = tmp_path / "W"
    resource = _appinstaller_resource_path(package_root)
    alias = tmp_path / "alias" / resource.name
    alias.parent.mkdir()
    alias.write_bytes(resource.read_bytes())
    real_file_identity = verifier._file_identity
    shared_identity = real_file_identity(resource)
    assert shared_identity is not None
    monkeypatch.setattr(
        verifier,
        "_file_identity",
        lambda path: shared_identity if path in (resource, alias) else real_file_identity(path),
    )
    mappings = [
        SimpleNamespace(path=str(resource), perms="r"),
        SimpleNamespace(path=str(alias), perms="xr"),
    ]

    modules, data, unknown = verifier._split_memory_mapping_paths(mappings)

    assert modules == sorted((str(resource), str(alias)))
    assert data == []
    assert unknown == []


@pytest.mark.parametrize(
    ("strict_field", "expected_leg"),
    (
        ("loaded_modules", "executable mapping"),
        ("unclassified_mappings", "unknown-protection mapping"),
    ),
)
def test_desktop_app_installer_resource_listed_as_strict_mapping_still_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    strict_field: str,
    expected_leg: str,
) -> None:
    from scripts import verify_native_runtime_closure as verifier

    package_root = tmp_path / "W"
    monkeypatch.setattr(verifier, "_STORE_PACKAGE_ROOT", package_root)
    tree = tmp_path / "tree"
    resource = _appinstaller_resource_path(package_root)
    payload = _with_traced_file(_clean_child_payload(), tree)
    payload["accessed_paths"] = [*payload["accessed_paths"], str(resource)]
    payload[strict_field] = [*payload.get(strict_field, []), str(resource)]
    child = _fake_child(json.dumps(payload))

    results = _interpret_child_output(child, present_gpl_files=(), tree=tree)
    trace = next(result for result in results if result.name == "dynamic_trace")

    assert trace.status == "FAIL"
    assert "NOT declared external dependencies" in trace.detail
    assert expected_leg in trace.detail
    assert str(resource.resolve()) in trace.detail


@pytest.mark.parametrize(
    "shape",
    ("dll", "exe", "publisher", "otherpackage", "nested", "lookalike", "escape"),
)
@pytest.mark.parametrize("trace_field", ("accessed_paths", "mapped_data"))
def test_desktop_app_installer_resource_exception_rejects_near_misses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str, trace_field: str
) -> None:
    from scripts import verify_native_runtime_closure as verifier

    package_root = tmp_path / "W"
    monkeypatch.setattr(verifier, "_STORE_PACKAGE_ROOT", package_root)
    tree = tmp_path / "tree"
    filename = "S-1-5-21-1-1-1-1-MergedResources-0.pri"
    if shape == "dll":
        resource = _appinstaller_resource_path(
            package_root, relative=("microsoft.system.package.metadata", "resource.dll")
        )
    elif shape == "exe":
        resource = _appinstaller_resource_path(
            package_root, relative=("microsoft.system.package.metadata", "resource.exe")
        )
    elif shape == "publisher":
        resource = _appinstaller_resource_path(
            package_root,
            package="Microsoft.DesktopAppInstaller_1.0.0.0_x64__8wekyb3d8bbbe",
            relative=("microsoft.system.package.metadata", filename),
        )
    elif shape == "otherpackage":
        resource = _appinstaller_resource_path(
            package_root,
            package="Microsoft.OtherPackage_1.0.0.0_x64__8wekyb3d8bbwe",
            relative=("microsoft.system.package.metadata", filename),
        )
    elif shape == "nested":
        resource = _appinstaller_resource_path(
            package_root,
            relative=("microsoft.system.package.metadata", "nested", filename),
        )
    elif shape == "lookalike":
        resource = _appinstaller_resource_path(
            package_root,
            package="Microsoft.DesktopAppInstallerEvil_1.0.0.0_x64__8wekyb3d8bbwe",
            relative=("microsoft.system.package.metadata", filename),
        )
    else:
        outside = tmp_path / "o" / filename
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_bytes(b"resource index")
        canonical_metadata = (
            package_root
            / "Microsoft.DesktopAppInstaller_1.0.0.0_x64__8wekyb3d8bbwe"
            / "microsoft.system.package.metadata"
        )
        canonical_metadata.mkdir(parents=True)
        resource = canonical_metadata / Path(os.path.relpath(outside, start=canonical_metadata))
        assert resource.exists()

    payload = _with_traced_file(_clean_child_payload(), tree)
    payload.setdefault(trace_field, []).append(str(resource))
    child = _fake_child(json.dumps(payload))

    results = _interpret_child_output(child, present_gpl_files=(), tree=tree)
    trace = next(result for result in results if result.name == "dynamic_trace")

    assert trace.status == "FAIL"
    assert "NOT declared external dependencies" in trace.detail


@pytest.mark.parametrize(
    "package",
    (
        "Microsoft.SecHealthUI_1000.26100.33648.0_x64__8wekyb3d8bbwe",
        "Microsoft.SecHealthUI_1000.26100.33438.0_x64__8wekyb3d8bbwe",
        "Microsoft.WindowsFeedbackHub_1.2512.16303.0_x64__8wekyb3d8bbwe",
        "MicrosoftCorporationII.WindowsSubsystemForLinux_3.0.3.0_x64__8wekyb3d8bbwe",
    ),
)
@pytest.mark.parametrize(
    ("strict_field", "expected_leg"),
    (
        ("loaded_modules", "executable mapping"),
        ("unclassified_mappings", "unknown-protection mapping"),
    ),
)
def test_observed_package_manifest_listed_as_strict_mapping_still_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package: str,
    strict_field: str,
    expected_leg: str,
) -> None:
    from scripts import verify_native_runtime_closure as verifier

    package_root = tmp_path / "W"
    monkeypatch.setattr(verifier, "_STORE_PACKAGE_ROOT", package_root)
    tree = tmp_path / "tree"
    resource = _observed_package_manifest_path(package_root, package=package)
    payload = _with_traced_file(_clean_child_payload(), tree)
    payload["accessed_paths"] = [*payload["accessed_paths"], str(resource)]
    payload[strict_field] = [*payload.get(strict_field, []), str(resource)]
    child = _fake_child(json.dumps(payload))

    results = _interpret_child_output(child, present_gpl_files=(), tree=tree)
    trace = next(result for result in results if result.name == "dynamic_trace")

    assert trace.status == "FAIL"
    assert "NOT declared external dependencies" in trace.detail
    assert expected_leg in trace.detail
    assert str(resource.resolve()) in trace.detail


@pytest.mark.parametrize(
    ("package", "relative"),
    (
        (
            "Microsoft.SecHealthUI_1000.26100.33648.0_x64__8wekyb3d8bbbf",
            ("AppxManifest.xml",),
        ),
        (
            "Microsoft.OtherPackage_x64__8wekyb3d8bbwe",
            ("AppxManifest.xml",),
        ),
        (
            "Microsoft.SecHealthUI_1000.26100.33648.0_x64__8wekyb3d8bbwe",
            ("nested", "AppxManifest.xml"),
        ),
        (
            "Microsoft.WindowsFeedbackHub_1.2512.16303.0_x64__8wekyb3d8bbwe",
            ("AppxManifest.xml.other",),
        ),
    ),
    ids=("foreign-publisher", "malformed-package-name", "nested", "wrong-filename"),
)
@pytest.mark.parametrize("trace_field", ("accessed_paths", "mapped_data"))
def test_observed_package_manifest_exception_rejects_near_misses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package: str,
    relative: tuple[str, ...],
    trace_field: str,
) -> None:
    from scripts import verify_native_runtime_closure as verifier

    package_root = tmp_path / "W"
    monkeypatch.setattr(verifier, "_STORE_PACKAGE_ROOT", package_root)
    tree = tmp_path / "tree"
    resource = _observed_package_manifest_path(package_root, package=package, relative=relative)
    payload = _with_traced_file(_clean_child_payload(), tree)
    payload.setdefault(trace_field, []).append(str(resource))
    child = _fake_child(json.dumps(payload))

    results = _interpret_child_output(child, present_gpl_files=(), tree=tree)
    trace = next(result for result in results if result.name == "dynamic_trace")

    assert trace.status == "FAIL"
    assert "NOT declared external dependencies" in trace.detail


def test_any_microsoft_published_package_manifest_is_accepted_as_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HALO-gate-a, candidate runs 43259269511 and 43274770373: Windows read the
    root manifest of a DIFFERENT Microsoft Store package on each run (Photos,
    Windows App Runtime, Desktop App Installer). The rule names the file, not
    the package: a package never seen before passes, its other files do not."""
    from scripts import verify_native_runtime_closure as verifier

    package_root = tmp_path / "W"
    monkeypatch.setattr(verifier, "_STORE_PACKAGE_ROOT", package_root)
    tree = tmp_path / "tree"
    package = "Microsoft.NeverSeenBefore_9.8.7.6_x64__8wekyb3d8bbwe"
    manifest = _observed_package_manifest_path(package_root, package=package)
    payload = _with_traced_file(_clean_child_payload(), tree)
    payload["accessed_paths"] = [*payload["accessed_paths"], str(manifest)]
    results = _interpret_child_output(
        _fake_child(json.dumps(payload)), present_gpl_files=(), tree=tree
    )
    trace = next(result for result in results if result.name == "dynamic_trace")
    assert trace.status == "PASS", trace.detail

    other = _observed_package_manifest_path(
        package_root, package=package, relative=("resources.pri",)
    )
    payload = _with_traced_file(_clean_child_payload(), tree)
    payload["accessed_paths"] = [*payload["accessed_paths"], str(other)]
    results = _interpret_child_output(
        _fake_child(json.dumps(payload)), present_gpl_files=(), tree=tree
    )
    trace = next(result for result in results if result.name == "dynamic_trace")
    assert trace.status == "FAIL"


def test_loaded_module_generator_is_classified_once(tmp_path: Path) -> None:
    outside_file = tmp_path / "outside" / "unexpected-module.dll"
    outside_file.parent.mkdir()
    outside_file.write_bytes(b"mapped module")

    inside, outside, unreviewed, resources = _classify_traced_accesses(
        [], iter((str(outside_file),)), tree=tmp_path / "tree"
    )

    assert inside == []
    assert outside == [str(outside_file.resolve())]
    assert unreviewed == []
    assert resources == []


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
@pytest.mark.parametrize("variable", ("LOCALAPPDATA", "PROGRAMDATA"))
def test_exact_windows_cache_database_is_reported_as_os_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    local_root = tmp_path / "local-appdata"
    program_root = tmp_path / "programdata"
    monkeypatch.setenv("LOCALAPPDATA", str(local_root))
    monkeypatch.setenv("PROGRAMDATA", str(program_root))
    selected_root = local_root if variable == "LOCALAPPDATA" else program_root
    cache = selected_root / "Microsoft" / "Windows" / "Caches" / "{GUID}.1.ver.db"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"windows-cache")

    inside, outside, unreviewed, resources = _classify_traced_accesses([str(cache)], [], tree=tree)

    assert inside == []
    assert outside == []
    assert unreviewed == [str(cache.resolve())]
    assert resources == []


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
@pytest.mark.parametrize("shape", ("dll", "nested-db", "lookalike", "literal-systemdrive"))
def test_windows_cache_exception_rejects_non_database_and_lookalike_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    local_root = tmp_path / "local-appdata"
    program_root = tmp_path / "programdata"
    monkeypatch.setenv("LOCALAPPDATA", str(local_root))
    monkeypatch.setenv("PROGRAMDATA", str(program_root))
    canonical = local_root / "Microsoft" / "Windows" / "Caches"
    candidates = {
        "dll": canonical / "outside.dll",
        "nested-db": canonical / "nested" / "outside.db",
        "lookalike": tmp_path / "lookalike" / "Microsoft" / "Windows" / "Caches" / "outside.db",
        "literal-systemdrive": (
            tmp_path
            / "%SystemDrive%"
            / "ProgramData"
            / "Microsoft"
            / "Windows"
            / "Caches"
            / "outside.db"
        ),
    }
    candidate = candidates[shape]
    candidate.parent.mkdir(parents=True)
    candidate.write_bytes(b"outside")

    inside, outside, unreviewed, resources = _classify_traced_accesses(
        [str(candidate)], [], tree=tree
    )

    assert inside == []
    assert unreviewed == []
    assert outside == [str(candidate.resolve())]
    assert resources == []


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_declared_store_extension_passes_but_reports_its_consequence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A declared Store-extension load passes WITH its true consequence stated.

    History, kept honest (CC-WS5 retraction; executable half closed in r5 014):
    the trace sees `Microsoft.HEVCVideoExtension` because Media Foundation
    loads every registered extension while ENUMERATING transforms -- loaded is
    not used. mfh265enc actually binds the GPU driver's HEVC encoder MFT; the
    earlier claim that HEVC encoding DEPENDS on the Store package was wrong and
    is retracted (evidence/hevc-store-extension-finding.md). The declaration
    stays so the enumeration load is accounted for rather than quietly ignored.

    The property under test is unchanged: a DECLARED dependency passes, but the
    check must still SAY what was loaded and what its declared consequence is.
    A declaration that reads like a clean pass would be an allowlist wearing a
    costume -- and a declaration enforcing a RETRACTED consequence would be
    worse: executable misinformation."""
    store_dll = (
        r"C:\Program Files\WindowsApps"
        r"\Microsoft.HEVCVideoExtension_2.5.10.0_x64__8wekyb3d8bbwe\x64\mfH265Enc.dll"
    )
    payload = _with_traced_file(_clean_child_payload(), tmp_path)
    payload["loaded_modules"] = [*payload["loaded_modules"], store_dll]
    child = _fake_child(json.dumps(payload))

    # This is a synthetic loader trace. Model the traced Store path as existing
    # without requiring an optional Store package on the clean runner; all other
    # path-existence checks retain their real behavior.
    real_exists = Path.exists
    monkeypatch.setattr(
        Path,
        "exists",
        lambda candidate: str(candidate) == store_dll or real_exists(candidate),
    )

    results = _interpret_child_output(child, present_gpl_files=(), tree=tmp_path)
    trace = next(r for r in results if r.name == "dynamic_trace")

    assert trace.status == "PASS"
    assert "DECLARED EXTERNAL DEPENDENCY" in trace.detail
    assert "HEVC Video Extension" in trace.detail
    assert "loaded is not used" in trace.detail, (
        "a declared dependency must carry its consequence, not just its name -- "
        "and the consequence must be the PROVEN one (enumeration load), not the "
        "retracted claim that HEVC encoding depends on the Store package"
    )
    assert "OWNER DECISION OWED" not in trace.detail, (
        "the retracted owner-decision claim must not survive in executable output"
    )


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_a_declaration_cannot_be_spoofed_by_a_lookalike_directory() -> None:
    """Self-caught before round 2: the declaration matcher was a SUBSTRING test.

    That meant any path merely CONTAINING the declared package name was excused
    -- `C:\\anywhere\\Microsoft.HEVCVideoExtension\\evil.dll` would have matched.
    That is exactly how a declaration decays into the allowlist it was built to
    avoid.

    Exercised against `_match_declared_dependency` directly rather than through
    the whole trace, because a path under a temp directory is a PERMITTED root
    and so never reaches the matcher at all -- testing it end-to-end would have
    passed for the wrong reason and proved nothing.
    """
    real = (
        r"C:\Program Files\WindowsApps"
        r"\Microsoft.HEVCVideoExtension_2.5.10.0_x64__8wekyb3d8bbwe\x64\mfH265Enc.dll"
    )
    assert _match_declared_dependency(real) is not None, "the genuine package must still match"

    # Same name, wrong place.
    assert _match_declared_dependency(r"C:\anywhere\Microsoft.HEVCVideoExtension\evil.dll") is None
    assert (
        _match_declared_dependency(r"C:\Users\public\Microsoft.HEVCVideoExtension_9.9\x64\evil.dll")
        is None
    )
    # Right place, undeclared package.
    assert (
        _match_declared_dependency(
            r"C:\Program Files\WindowsApps\Some.Other.Package_1.0_x64__abc\x64\whatever.dll"
        )
        is None
    )
    # Right place, but the name merely CONTAINS the declared one as a substring
    # of a longer component rather than being that package.
    assert (
        _match_declared_dependency(
            r"C:\Program Files\WindowsApps\NotMicrosoft.HEVCVideoExtensionEvil_1.0\x64\e.dll"
        )
        is None
    )


def test_an_undeclared_store_package_still_fails(tmp_path: Path) -> None:
    """Declaring the HEVC extension must NOT open the whole WindowsApps folder.

    This is the difference between a declaration and an allowlist: the next
    Store package to appear in a trace has to be looked at by a person, not
    absorbed because something else in the same directory was once approved.
    """
    payload = _with_traced_file(_clean_child_payload(), tmp_path)
    payload["loaded_modules"] = [
        *payload["loaded_modules"],
        r"C:\Program Files\WindowsApps\SomeOther.Package_1.0_x64__abc\x64\whatever.dll",
    ]
    child = _fake_child(json.dumps(payload))

    results = _interpret_child_output(child, present_gpl_files=(), tree=tmp_path)
    trace = next(r for r in results if r.name == "dynamic_trace")

    # The path need not exist for the point to hold, but if it does not the
    # classifier skips it (a failed probe is not a dependency), so assert the
    # meaningful case only when the directory is real.
    if (
        Path(r"C:\Program Files\WindowsApps").exists()
        and Path(
            r"C:\Program Files\WindowsApps\SomeOther.Package_1.0_x64__abc\x64\whatever.dll"
        ).exists()
    ):  # pragma: no cover - depends on the host
        assert trace.status == "FAIL"
        assert "NOT declared" in trace.detail


def test_interpret_child_output_fails_when_gpl_files_present_even_if_gst_clean(
    tmp_path: Path,
) -> None:
    """The file half of the GPL control is independent of the child process
    and must still fail the check even when the factory half is clean."""
    child = _fake_child(json.dumps(_clean_child_payload()))

    results = _interpret_child_output(child, present_gpl_files=("gstx264.dll",), tree=tmp_path)

    gpl_result = next(r for r in results if r.name == "gpl_negative_control")
    assert gpl_result.status == "FAIL"
    assert "gstx264.dll" in gpl_result.detail


def test_interpret_child_output_fails_every_check_when_child_process_crashed(
    tmp_path: Path,
) -> None:
    child = _fake_child("", returncode=1, stderr="segfault")

    results = _interpret_child_output(child, present_gpl_files=(), tree=tmp_path)

    assert {r.status for r in results} == {"FAIL"}
    assert all("segfault" in r.detail or "exited 1" in r.detail for r in results)


def test_interpret_child_output_fails_factory_sweep_when_required_plugin_file_is_absent(
    tmp_path: Path,
) -> None:
    """End-to-end regression for the masking bug caught in adversarial
    review: a REQUIRED factory (mfh264enc/mfh265enc) missing from the sweep
    AND its plugin file entirely absent from the tree must FAIL
    factory_sweep -- never PASS as an excused hardware-gated miss."""
    tree = tmp_path / "rt"
    (tree / "lib" / "gstreamer-1.0").mkdir(parents=True)
    # gstmediafoundation.dll deliberately not created.
    payload = _clean_child_payload()
    payload["factory_sweep"] = {
        "missing": ["mfh264enc", "mfh265enc"],
        "checked_count": 51,
        "detail": "2 of 51 required factories did not resolve: mfh264enc, mfh265enc",
    }
    child = _fake_child(json.dumps(payload))

    results = _interpret_child_output(child, present_gpl_files=(), tree=tree)

    factory_result = next(r for r in results if r.name == "factory_sweep")
    assert factory_result.status == "FAIL"
    assert "mfh264enc" in factory_result.detail
    assert "mfh265enc" in factory_result.detail


def test_interpret_child_output_passes_factory_sweep_when_plugin_file_present_but_factory_absent(
    tmp_path: Path,
) -> None:
    """Sanity counterpart: when gstmediafoundation.dll genuinely IS in the
    tree (a real packaging closure) and only the FACTORY fails to register
    (no matching Media Foundation MFT on this build box), that miss is still
    excused as hardware-gated -- the fix must not turn every hardware-gated
    miss into a FAIL."""
    tree = tmp_path / "rt"
    plugin_dir = tree / "lib" / "gstreamer-1.0"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "gstmediafoundation.dll").touch()
    payload = _clean_child_payload()
    payload["factory_sweep"] = {
        "missing": ["mfh264enc", "mfh265enc"],
        "checked_count": 51,
        "detail": "2 of 51 required factories did not resolve: mfh264enc, mfh265enc",
    }
    child = _fake_child(json.dumps(payload))

    results = _interpret_child_output(child, present_gpl_files=(), tree=tree)

    factory_result = next(r for r in results if r.name == "factory_sweep")
    assert factory_result.status == "PASS"


# ---------------------------------------------------------------------------
# Caption leg -- pure decision points extracted from the GStreamer-dependent
# child code so they are unit-testable without gi/PyGObject. The GStreamer
# plumbing itself (h264ccextractor -> ccconverter -> cea608tott) is proven
# only by a real run against a built tree -- see the worker report for the
# verbatim `caption_leg` PASS output.
# ---------------------------------------------------------------------------


def test_pad_caps_name_is_video_accepts_a_video_mime_type() -> None:
    assert _pad_caps_name_is_video("video/x-h264") is True


def test_pad_caps_name_is_video_rejects_an_audio_mime_type() -> None:
    """tsdemux emits both a video and an audio pad from the muxed .ts; only
    the video pad may be linked into the h264ccextractor decode chain."""
    assert _pad_caps_name_is_video("audio/mpeg") is False


def test_caption_probe_survived_true_when_the_probe_text_is_a_substring() -> None:
    """cea608tott emits WebVTT-framed text, not a bare string -- the probe
    text must be found as a substring of the decoded output, not an exact
    match."""
    decoded = "WEBVTT\r\n\r\n00:00:00.691 --> 00:00:04.691\r\nCIVICCAST CLOSURE PROBE 3f9a\r\n\r\n"
    assert _caption_probe_survived(decoded) is True


def test_caption_probe_survived_accepts_an_exact_runtime_caption() -> None:
    decoded = (
        "WEBVTT\r\n\r\n00:00:00.691 --> 00:00:04.691\r\n"
        "The Council meeting will come to\r\norder.\r\n\r\n"
    )
    assert (
        _caption_probe_survived(
            decoded,
            "The Council meeting will come to order.",
        )
        is True
    )


def test_caption_probe_survived_false_when_the_probe_text_is_absent() -> None:
    assert _caption_probe_survived("WEBVTT\r\n\r\n") is False


def test_caption_probe_survived_false_for_empty_output() -> None:
    """An appsink that never received a buffer must not be misread as a
    pass -- empty output is exactly the failure this check exists to catch."""
    assert _caption_probe_survived("") is False


# ---------------------------------------------------------------------------
# CheckResult basics
# ---------------------------------------------------------------------------


def test_check_result_status_is_restricted_to_the_three_documented_values() -> None:
    with pytest.raises(ValueError):
        CheckResult("x", "MAYBE", "nonsense")  # type: ignore[arg-type]


class TestD3d12DecoderHardwareGate:
    """`d3d12h264dec` is hardware-gated, but only when its DLL actually shipped.

    Added 2026-08-07. The first candidate build to reach the closure verifier
    on a GPU-less `windows-latest` runner reported `d3d12h264dec` as a genuine
    packaging miss (run 31136493481), while the identical tree verified 7/7 on
    a developer box with a GPU. GStreamer's Direct3D 12 decoder registers only
    where a D3D12 video-decode adapter exists, so the hardware -- not the
    package -- was the variable. It was absent from HARDWARE_GATED_FACTORIES
    only because the original brief enumerated encoders.

    The pair of tests below is the point: excusing it must NOT excuse a build
    that failed to package `gstd3d12.dll`.
    """

    def test_absent_on_a_gpu_less_machine_is_excused_when_the_dll_shipped(self) -> None:
        gated, genuine = classify_missing_factories(
            ["d3d12h264dec"],
            plugin_file_missing=frozenset(),
        )
        assert gated == ("d3d12h264dec",)
        assert genuine == ()

    def test_absent_dll_is_still_a_genuine_miss_not_a_hardware_excuse(self) -> None:
        """The safety property. If this ever inverts, the gate is decorative."""
        gated, genuine = classify_missing_factories(
            ["d3d12h264dec"],
            plugin_file_missing=frozenset({"d3d12h264dec"}),
        )
        assert genuine == ("d3d12h264dec",)
        assert gated == ()

    def test_the_factory_is_mapped_to_a_plugin_file_so_the_guard_can_run(self) -> None:
        """Gating a factory with no FACTORY_PLUGIN entry would be a real
        weakening: `find_missing_required_plugin_files` could never report its
        DLL absent, so the safety property above would be unreachable."""
        from civiccast.native.runtime_closure import FACTORY_PLUGIN

        assert FACTORY_PLUGIN["d3d12h264dec"] == "gstd3d12.dll"

    def test_every_hardware_gated_factory_has_a_plugin_file_mapping(self) -> None:
        """Generalises the check above to the whole set, so a future addition
        cannot quietly become unfalsifiable."""
        from civiccast.native.runtime_closure import FACTORY_PLUGIN

        unmapped = sorted(f for f in HARDWARE_GATED_FACTORIES if f not in FACTORY_PLUGIN)
        assert not unmapped, (
            "hardware-gated factories with no FACTORY_PLUGIN entry can never be "
            f"reported as a missing DLL, making the excuse unfalsifiable: {unmapped}"
        )


# ---------------------------------------------------------------------------
# WSL resources.pri -- observed data-only package resource exception
# ---------------------------------------------------------------------------

_WSL_PACKAGE = "MicrosoftCorporationII.WindowsSubsystemForLinux_3.0.3.0_x64__8wekyb3d8bbwe"
_TERMINAL_PACKAGE = "Microsoft.WindowsTerminal_1.23.20211.0_x64__8wekyb3d8bbwe"


@pytest.mark.parametrize("trace_field", ("accessed_paths", "sampled_handles", "mapped_data"))
@pytest.mark.parametrize("package", (_WSL_PACKAGE, _TERMINAL_PACKAGE))
def test_wsl_resources_pri_access_is_reported_without_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trace_field: str, package: str
) -> None:
    from scripts import verify_native_runtime_closure as verifier

    package_root = tmp_path / "W"
    monkeypatch.setattr(verifier, "_STORE_PACKAGE_ROOT", package_root)
    tree = tmp_path / "tree"
    resource = _appinstaller_resource_path(
        package_root, package=package, relative=("resources.pri",)
    )
    payload = _with_traced_file(_clean_child_payload(), tree)
    payload.setdefault(trace_field, []).append(str(resource))
    child = _fake_child(json.dumps(payload))

    results = _interpret_child_output(child, present_gpl_files=(), tree=tree)
    trace = next(result for result in results if result.name == "dynamic_trace")

    assert trace.status == "PASS"
    assert "WSL resources.pri" in trace.detail
    assert "Windows Terminal resources.pri" in trace.detail
    assert "DesktopAppInstaller package resource" in trace.detail
    assert "AppxManifest.xml" in trace.detail
    assert str(resource.resolve()) in trace.detail


@pytest.mark.parametrize(
    ("strict_field", "expected_leg"),
    (
        ("loaded_modules", "executable mapping"),
        ("unclassified_mappings", "unknown-protection mapping"),
    ),
)
@pytest.mark.parametrize("package", (_WSL_PACKAGE, _TERMINAL_PACKAGE))
def test_wsl_resources_pri_listed_as_strict_mapping_still_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    strict_field: str,
    expected_leg: str,
    package: str,
) -> None:
    from scripts import verify_native_runtime_closure as verifier

    package_root = tmp_path / "W"
    monkeypatch.setattr(verifier, "_STORE_PACKAGE_ROOT", package_root)
    tree = tmp_path / "tree"
    resource = _appinstaller_resource_path(
        package_root, package=package, relative=("resources.pri",)
    )
    payload = _with_traced_file(_clean_child_payload(), tree)
    payload["accessed_paths"] = [*payload["accessed_paths"], str(resource)]
    payload[strict_field] = [*payload.get(strict_field, []), str(resource)]
    child = _fake_child(json.dumps(payload))

    results = _interpret_child_output(child, present_gpl_files=(), tree=tree)
    trace = next(result for result in results if result.name == "dynamic_trace")

    assert trace.status == "FAIL"
    assert "NOT declared external dependencies" in trace.detail
    assert expected_leg in trace.detail
    assert str(resource.resolve()) in trace.detail


# ---------------------------------------------------------------------------
# FeedbackHub metadata resource -- observed data-only package resource
# ---------------------------------------------------------------------------

_FEEDBACKHUB_PACKAGE = "Microsoft.WindowsFeedbackHub_1.2512.16303.0_x64__8wekyb3d8bbwe"
_FEEDBACKHUB_METADATA_RESOURCE = ("microsoft.system.package.metadata", "resources.0ff121e3.pri")


def test_feedbackhub_metadata_resource_mapped_data_is_reported_without_failing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts import verify_native_runtime_closure as verifier

    package_root = tmp_path / "W"
    monkeypatch.setattr(verifier, "_STORE_PACKAGE_ROOT", package_root)
    tree = tmp_path / "tree"
    resource = _appinstaller_resource_path(
        package_root,
        package=_FEEDBACKHUB_PACKAGE,
        relative=_FEEDBACKHUB_METADATA_RESOURCE,
    )
    payload = _with_traced_file(_clean_child_payload(), tree)
    payload["mapped_data"] = [*payload.get("mapped_data", []), str(resource)]
    child = _fake_child(json.dumps(payload))

    results = _interpret_child_output(child, present_gpl_files=(), tree=tree)
    trace = next(result for result in results if result.name == "dynamic_trace")

    assert trace.status == "PASS"
    assert str(resource.resolve()) in trace.detail


@pytest.mark.parametrize(
    ("strict_field", "expected_leg"),
    (
        ("loaded_modules", "executable mapping"),
        ("unclassified_mappings", "unknown-protection mapping"),
    ),
)
def test_feedbackhub_metadata_resource_listed_as_strict_mapping_still_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    strict_field: str,
    expected_leg: str,
) -> None:
    from scripts import verify_native_runtime_closure as verifier

    package_root = tmp_path / "W"
    monkeypatch.setattr(verifier, "_STORE_PACKAGE_ROOT", package_root)
    tree = tmp_path / "tree"
    resource = _appinstaller_resource_path(
        package_root,
        package=_FEEDBACKHUB_PACKAGE,
        relative=_FEEDBACKHUB_METADATA_RESOURCE,
    )
    payload = _with_traced_file(_clean_child_payload(), tree)
    payload["accessed_paths"] = [*payload["accessed_paths"], str(resource)]
    payload[strict_field] = [*payload.get(strict_field, []), str(resource)]
    child = _fake_child(json.dumps(payload))

    results = _interpret_child_output(child, present_gpl_files=(), tree=tree)
    trace = next(result for result in results if result.name == "dynamic_trace")

    assert trace.status == "FAIL"
    assert "NOT declared external dependencies" in trace.detail
    assert expected_leg in trace.detail
    assert str(resource.resolve()) in trace.detail


@pytest.mark.parametrize(
    ("package", "relative"),
    (
        (_WSL_PACKAGE[:-1] + "f", ("resources.pri",)),
        (_WSL_PACKAGE, ("resources.pri.other",)),
        (_WSL_PACKAGE, ("nested", "resources.pri")),
        (_TERMINAL_PACKAGE[:-1] + "f", ("resources.pri",)),
        (_TERMINAL_PACKAGE, ("resources.dll",)),
        (_TERMINAL_PACKAGE, ("nested", "resources.pri")),
        (
            _TERMINAL_PACKAGE.replace("WindowsTerminal", "WindowsTerminalPreview"),
            ("resources.pri",),
        ),
        ("Microsoft.OtherPackage_1.0.0.0_x64__8wekyb3d8bbwe", ("resources.pri",)),
    ),
    ids=(
        "wrong-publisher",
        "wrong-filename",
        "subdirectory",
        "terminal-wrong-publisher",
        "terminal-dll",
        "terminal-subdirectory",
        "terminal-other-package",
        "foreign-package",
    ),
)
@pytest.mark.parametrize("trace_field", ("accessed_paths", "mapped_data"))
def test_wsl_resources_pri_exception_rejects_near_misses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    package: str,
    relative: tuple[str, ...],
    trace_field: str,
) -> None:
    from scripts import verify_native_runtime_closure as verifier

    package_root = tmp_path / "W"
    monkeypatch.setattr(verifier, "_STORE_PACKAGE_ROOT", package_root)
    tree = tmp_path / "tree"
    resource = _appinstaller_resource_path(package_root, package=package, relative=relative)
    payload = _with_traced_file(_clean_child_payload(), tree)
    payload.setdefault(trace_field, []).append(str(resource))
    child = _fake_child(json.dumps(payload))

    results = _interpret_child_output(child, present_gpl_files=(), tree=tree)
    trace = next(result for result in results if result.name == "dynamic_trace")

    assert trace.status == "FAIL"
    assert "NOT declared external dependencies" in trace.detail
