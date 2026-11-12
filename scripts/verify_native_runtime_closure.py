# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""D6 acceptance verification suite (AC2): proves a BUILT packaged tree is
self-sufficient.

`spec-packaging-closure` D6 requires this to run against the packaged tree
ONLY, inside a child process launched with a deliberately hostile
environment: PATH scrubbed to system directories plus `<tree>/bin`, plugin
search paths pointed exclusively at the tree, a fresh-per-run
`GST_REGISTRY` (never the caller's cached registry -- a stale registry is
exactly how you get a false green), and `GST_PLUGIN_SYSTEM_PATH` forced
empty so the operating system's own GStreamer install (if any) cannot be
loaded from instead of the tree.

Five checks, each PASS/FAIL/SKIPPED:

  1. Factory sweep -- every `REQUIRED_FACTORIES` entry resolves via
     `Gst.ElementFactory.find`. `mfh264enc`/`mfh265enc`/`nvh264enc`/
     `nvh265enc` register only on matching hardware/drivers, so a miss is
     split into two independent questions before it is ever excused: (a) is
     the PLUGIN FILE present in the tree (`FACTORY_PLUGIN[name]` under
     `<tree>/lib/gstreamer-1.0`) -- a packaging question, ALWAYS a hard FAIL
     when absent, for every required factory, hardware-gated or not; and
     (b) does the FACTORY register on this machine -- a hardware question,
     excused as HARDWARE-GATED only when the plugin file is genuinely
     present. A required factory whose plugin DLL is entirely missing from
     the tree is therefore always a genuine miss, never silently folded
     into a hardware-gated pass.
  2. Plugin origin check -- every loaded plugin's backing file is inside
     the tree. A plugin loaded from outside is a hard FAIL (AC5).
  3. Caption leg -- embeds CEA-608 captions into H.264 (modeled on
     `.agent-runs/native-windows/spike-gstreamer-bundle/evidence/
     run_caption_pipeline.py`) and decodes them back USING ONLY ELEMENTS
     THE PACKAGED TREE CONTAINS -- the tree deliberately ships no
     `ffmpeg.exe`, so decode-back runs entirely in GStreamer:
     `tsdemux` -> `h264parse` -> `h264ccextractor` (pulls the CEA-708 SEI
     back out of the H.264 stream) -> `ccconverter` (CEA-708 -> CEA-608) ->
     `cea608tott` (CEA-608 -> plain/WebVTT text) -> `appsink`. This is a
     genuine text content round trip, not a meta-presence stand-in: the
     probe text is asserted as a substring of the decoded text.
  4. GPL negative control (AC3) -- `x264enc`/`x265enc` are absent both as
     files under `lib/gstreamer-1.0` and as resolvable factories.
  5. Manifest verification -- delegates to
     `civiccast.native.runtime_manifest.verify_manifest` (owned by another
     WS5 worker). If that module does not exist yet, this check reports
     SKIPPED with the ImportError text rather than reimplementing it.

Checks 1-3 and 4's factory half run inside the hostile child process
(anything that touches `gi`/`Gst` must run there, never in this process,
so the parent's own environment can never leak in). Check 4's file half and
check 5 are plain filesystem/hashing operations and run directly in the
parent -- no GStreamer involved, no need to pay the child-process cost.

CLI: ``--tree <dir>`` (required), ``--json <path>`` (optional). Exit code 0
only if every non-skipped check passed.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import threading
from collections.abc import Iterable, Mapping, Sequence
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict, cast

from civiccast.native.runtime_closure import (
    EXCLUDED_GPL_FACTORIES,
    FACTORY_PLUGIN,
    HARDWARE_GATED_FACTORIES,
    OS_DEPENDENCY_INVENTORY,
    REQUIRED_FACTORIES,
    classify_missing_factories,
)

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS_FILE = ROOT / "requirements-native-runtime.txt"
CLI_CONSUMER_RELATIVES = (
    "bin/gst-discoverer-1.0.exe",
    "bin/gst-inspect-1.0.exe",
)
CLI_CONSUMER_DISTRIBUTION = "gstreamer_cli"
_PINNED_CLI_LOCK_LINE = "gstreamer-cli==1.28.5"
_PINNED_CLI_WINDOWS_SHA256 = "ef562bfc43817e7f497b11456be50609184672e4f276b77b8c126e5b247544ca"

if TYPE_CHECKING:
    from civiccast.native.runtime_manifest import FileEntry

__all__ = [
    "GPL_PLUGIN_FILENAMES",
    "HARDWARE_GATED_FACTORIES",
    "CheckResult",
    "CheckStatus",
    "aggregate_exit_code",
    "build_hostile_environment",
    "check_cli_consumer_verification",
    "classify_missing_factories",
    "find_gpl_plugin_files",
    "find_missing_required_plugin_files",
    "find_plugins_outside_tree",
    "find_present_gpl_factories",
    "is_inside_tree",
    "main",
]

# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------

CheckStatus = Literal["PASS", "FAIL", "SKIPPED"]
_VALID_STATUSES = frozenset({"PASS", "FAIL", "SKIPPED"})


@dataclass(frozen=True)
class CheckResult:
    """One check's outcome. ``detail`` is the real evidence -- the reason a
    reader can trust the status without re-running the check themselves."""

    name: str
    status: CheckStatus
    detail: str

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUSES:
            raise ValueError(
                f"CheckResult status must be one of {sorted(_VALID_STATUSES)}, got {self.status!r}"
            )


def aggregate_exit_code(results: Iterable[CheckResult]) -> int:
    """0 if every check is PASS or SKIPPED; 1 if any check FAILed. SKIPPED
    is deliberately neither a pass nor a fail -- a module another worker
    hasn't landed yet must not gate this worker's suite, but it must also
    never be silently counted as evidence of success."""

    return 1 if any(result.status == "FAIL" for result in results) else 0


# ---------------------------------------------------------------------------
# Hostile environment builder
# ---------------------------------------------------------------------------

#: Directories (relative to %SystemRoot%) that stay on PATH in the hostile
#: environment so the OS loader/subprocess machinery itself keeps working.
#: Everything else -- most importantly any GStreamer install elsewhere on
#: the dev box's real PATH -- is scrubbed.
_SYSTEM_PATH_RELATIVE_DIRS: tuple[str, ...] = (
    "",  # %SystemRoot% itself
    "System32",
    "System32/Wbem",
    "System32/WindowsPowerShell/v1.0",
)

#: Environment variables preserved from the caller for process bootstrap
#: only (temp dirs, the shell used for spawning, locale). Never PATH, never
#: any GST_*/GI_*/PYTHONPATH/GIO_* variable -- those are ALWAYS set to their
#: hostile values below, regardless of what the caller happened to have.
_PASSTHROUGH_ENV_VARS: tuple[str, ...] = (
    "SystemDrive",
    # Windows known-folder and cache discovery reads these directly. Dropping
    # them made a real self-hosted run fall back to unexpanded registry text
    # such as ``%SystemDrive%\\ProgramData`` and create relative cache paths
    # under the checkout. They are process-bootstrap locations, not media or
    # plugin search paths, so preserving them does not weaken the hostile PATH
    # or any GStreamer/Python isolation below.
    "LOCALAPPDATA",
    "PROGRAMDATA",
    "TEMP",
    "TMP",
    "USERPROFILE",
    "ComSpec",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PROCESSOR_ARCHITECTURE",
)


def _system_path_dirs(system_root: Path) -> tuple[str, ...]:
    return tuple(
        str(system_root / relative) if relative else str(system_root)
        for relative in _SYSTEM_PATH_RELATIVE_DIRS
    )


def build_hostile_environment(
    tree: Path, *, base_env: Mapping[str, str], registry_path: Path
) -> dict[str, str]:
    """Build the D6 child-process environment (AC5's poisoned-environment
    control): scrubbed PATH, plugin/typelib/gio/python search paths pinned
    exclusively to ``tree``, and a fresh ``registry_path`` per call.

    Returns a brand-new dict; ``base_env`` is never mutated.
    """

    system_root = Path(base_env.get("SystemRoot", r"C:\Windows"))
    path_dirs = (*_system_path_dirs(system_root), str(tree / "bin"))

    env: dict[str, str] = {
        "SystemRoot": str(system_root),
        "PATH": ";".join(path_dirs),
        "GST_PLUGIN_PATH": str(tree / "lib" / "gstreamer-1.0"),
        "GST_PLUGIN_SYSTEM_PATH": "",
        "GST_REGISTRY": str(registry_path),
        "GI_TYPELIB_PATH": str(tree / "lib" / "girepository-1.0"),
        "GIO_MODULE_DIR": str(tree / "lib" / "gio" / "modules"),
        "PYTHONPATH": str(tree / "python"),
        # PyGObject on Windows normally deduces its DLL directories from the
        # upstream wheel layout (a .pth shim plus gi sitting under the
        # gstreamer_python package). The packaged tree deliberately flattens
        # that layout, so the deduction fails with "Could not deduce DLL
        # directories" and NOTHING loads. Found by the first real D6 run
        # against a built tree -- exactly what this suite exists to catch.
        #
        # This is not a test-only fixup: it is part of the runtime contract the
        # supervisor/installer must establish for the shipped product too. The
        # verifier deliberately sets no more than the product will set, so a
        # green D6 run means the product's own environment is sufficient.
        "PYGI_DLL_DIRS": str(tree / "bin"),
        # Verification must not mutate what it verifies. Importing from
        # <tree>/python writes __pycache__ directories INTO the tree, which
        # then show up as orphan files and fail manifest_verification on the
        # very next run -- a self-inflicted red that looks exactly like real
        # tamper detection. Observed on the second real D6 run.
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name in _PASSTHROUGH_ENV_VARS:
        if name in base_env:
            env.setdefault(name, base_env[name])
    return env


# ---------------------------------------------------------------------------
# Tree-containment predicate (check 2's core logic)
# ---------------------------------------------------------------------------


def is_inside_tree(tree: Path, candidate: Path) -> bool:
    """Is ``candidate`` inside ``tree``, or ``tree`` itself?

    Uses ``Path.relative_to`` (component-wise comparison) rather than string
    prefix matching -- ``str(candidate).startswith(str(tree))`` would wrongly
    accept a sibling directory whose name happens to share a prefix, e.g.
    tree ``C:/x/rt`` and candidate ``C:/x/rt-evil/a.dll``: the strings share
    a prefix but ``rt-evil`` is not a path component equal to ``rt``.
    """

    tree_resolved = tree.resolve()
    candidate_resolved = candidate.resolve()
    try:
        candidate_resolved.relative_to(tree_resolved)
    except ValueError:
        return False
    return True


def find_plugins_outside_tree(
    tree: Path, plugins: Iterable[tuple[str, str | None]]
) -> tuple[tuple[str, str], ...]:
    """``plugins`` is ``(plugin_name, filename)`` pairs, e.g. sourced from
    ``Gst.Registry.get().get_plugin_list()``. ``filename`` is ``None`` for
    plugins with no backing file (statically-linked/basetype plugins) --
    there is no file to be outside of, so those are never flagged. Returns
    the ``(name, filename)`` pairs whose file is NOT inside ``tree``, sorted
    by name.
    """

    outside = [
        (name, filename)
        for name, filename in plugins
        if filename is not None and not is_inside_tree(tree, Path(filename))
    ]
    return tuple(sorted(outside))


# ---------------------------------------------------------------------------
# Hardware-gated factory classification (check 1)
# ---------------------------------------------------------------------------

# HARDWARE_GATED_FACTORIES and classify_missing_factories moved to
# `civiccast.native.runtime_closure` (2026-08-07, candidate run 31190955761):
# the installed-product smoke ships in the payload wheel and needed the same
# hardware-gating rule, but this script does not ship -- the shared, pure
# logic now lives in the product module and is imported above. The full
# lesson history (the mfh264enc/nvh264enc brief, the d3d12h264dec
# misclassification on run 31136493481) is preserved on the definitions
# there. The filesystem half (find_missing_required_plugin_files below)
# stays here: runtime_closure is deliberately filesystem-free.


def find_missing_required_plugin_files(
    tree: Path, factory_names: Iterable[str], *, factory_plugin: Mapping[str, str] = FACTORY_PLUGIN
) -> tuple[str, ...]:
    """Which of ``factory_names`` have NO backing plugin file under
    ``tree/lib/gstreamer-1.0``? This is the packaging half of check 1's
    fix -- a plain filesystem question, answered without touching `gi`/Gst,
    so it runs directly in the parent process (like `find_gpl_plugin_files`)
    rather than inside the hostile child. An empty ``factory_names`` never
    touches the filesystem at all. Returns factory names (not plugin
    filenames), sorted.
    """

    names = tuple(factory_names)
    if not names:
        return ()
    plugin_dir = tree / "lib" / "gstreamer-1.0"
    return tuple(
        sorted(
            name
            for name in set(names)
            if name in factory_plugin and not (plugin_dir / factory_plugin[name]).exists()
        )
    )


# ---------------------------------------------------------------------------
# GPL negative control (check 4)
# ---------------------------------------------------------------------------

#: The plugin DLL filenames that must never exist in the shipped tree,
#: derived from `runtime_closure.FACTORY_PLUGIN` so this stays in lockstep
#: with the single source of truth for factory->plugin mapping.
GPL_PLUGIN_FILENAMES: tuple[str, ...] = tuple(
    sorted({FACTORY_PLUGIN[name] for name in EXCLUDED_GPL_FACTORIES})
)


def find_gpl_plugin_files(tree: Path, plugin_filenames: Iterable[str]) -> tuple[str, ...]:
    """Which of ``plugin_filenames`` exist under ``tree/lib/gstreamer-1.0``?
    An empty result is the AC3 pass condition for the file half of the GPL
    negative control."""

    plugin_dir = tree / "lib" / "gstreamer-1.0"
    return tuple(sorted(name for name in set(plugin_filenames) if (plugin_dir / name).exists()))


def find_present_gpl_factories(
    gpl_factories: Iterable[str], resolved: Mapping[str, bool]
) -> tuple[str, ...]:
    """``resolved`` maps a factory name to whether
    ``Gst.ElementFactory.find`` returned non-``None`` for it. Returns the
    GPL-excluded factory names that DID resolve, sorted -- an empty result
    is the AC3 pass condition for the factory half."""

    return tuple(sorted(name for name in gpl_factories if resolved.get(name, False)))


# ---------------------------------------------------------------------------
# Manifest verification (check 5) -- parent process, no gi involved
# ---------------------------------------------------------------------------


#: `scripts/build_native_runtime_closure.py`'s own `build()` writes these
#: three files to `out` via `hash_directory_tree(out, distribution_of=...)`
#: called BEFORE they exist -- they are never part of `distribution_of` or
#: the manifest's own `files` list, by the real build script's own sequencing,
#: not a guess made here. `hash_directory_tree` (a build-time helper that
#: also enforces AC7's license gate) would raise `UnknownLicenseError` on
#: them if handed the whole tree at verification time, so this check hashes
#: the tree itself (see `_hash_tree_for_manifest_verification`) rather than
#: calling that build-time helper, skipping exactly these three -- and only
#: when the manifest itself does not claim them, so a future manifest that
#: does start tracking one of these paths is unaffected.
_MANIFEST_SIBLING_ARTIFACT_NAMES = frozenset(
    {"runtime-manifest.json", "SHA256SUMS", "LICENSE-BOM.md"}
)


def _verify_sibling_trust_artifacts(tree: Path, manifest: dict[str, Any]) -> str | None:
    """Verify the three trust artifacts themselves. Returns a problem, or None.

    CC-WS5-PKG-009 (Codex r2, Critical). `SHA256SUMS` and `LICENSE-BOM.md` were
    excluded from manifest verification because a manifest cannot contain a hash
    of itself -- correct reasoning, followed by never asking what verifies THEM.
    Both could be deleted or rewritten and the suite still reported 6 PASS. The
    files whose entire job is to let an operator check the payload were the only
    files nothing checked.

    They are DERIVED from the manifest, which is what makes this tractable:
    recompute each from the manifest's own entries and require a byte-for-byte
    match. A tampered or missing artifact then cannot survive.

    `runtime-manifest.json` itself is the root of trust and cannot be verified
    from inside the tree -- by construction, nothing in the tree is more
    authoritative than it. Per spec D5 its integrity chains to the Authenticode
    signature of the installer that carries it. That is stated here rather than
    silently assumed, because "we verified everything except the root" is only
    honest if the exception is named.
    """
    from civiccast.native.runtime_manifest import (
        FileEntry,
        render_license_bom,
        render_sha256sums,
    )

    try:
        entries = tuple(
            FileEntry(
                path=record["path"],
                sha256=record["sha256"],
                bytes=record["bytes"],
                distribution=record["distribution"],
                license=record["license"],
            )
            for record in manifest.get("files", [])
        )
    except (KeyError, TypeError) as exc:
        return f"runtime-manifest.json entries are malformed: {type(exc).__name__}: {exc}"

    for name, rendered in (
        ("SHA256SUMS", render_sha256sums(entries)),
        ("LICENSE-BOM.md", render_license_bom(entries)),
    ):
        path = tree / name
        if not path.is_file():
            return (
                f"{name} is MISSING from the tree. It is the artifact an operator uses to "
                "check the payload; its absence is a tampering signal, not a detail."
            )
        actual = path.read_text(encoding="utf-8")
        if actual != rendered:
            return (
                f"{name} does not match what runtime-manifest.json implies. It was "
                "modified after the build, or the manifest was. Either way the payload's "
                "own integrity record disagrees with itself and must not be trusted."
            )
    return None


def _hash_tree_for_manifest_verification(
    tree: Path, *, manifest_paths: frozenset[str]
) -> tuple[FileEntry, ...]:
    """Real, independently-computed on-disk hashes for `verify_manifest`'s
    ``entries`` argument. `verify_manifest` only ever compares ``path`` and
    ``sha256`` (see its diff logic) -- ``distribution``/``bytes``/``license``
    are irrelevant to it, so those fields carry an explicit placeholder here
    rather than re-deriving a real distribution mapping this check has no
    independent source for. Any on-disk file that is NOT one of the known
    sibling trust artifacts is included even when absent from the manifest,
    so `verify_manifest`'s own ORPHAN detection still fires for a real bug.
    """

    from civiccast.native.runtime_manifest import FileEntry

    entries: list[FileEntry] = []
    for file_path in sorted(p for p in tree.rglob("*") if p.is_file()):
        rel = file_path.relative_to(tree).as_posix()
        if rel in _MANIFEST_SIBLING_ARTIFACT_NAMES and rel not in manifest_paths:
            continue
        digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
        entries.append(
            FileEntry(
                path=rel,
                sha256=digest,
                bytes=file_path.stat().st_size,
                distribution="<verification-only, unused by verify_manifest's diff>",
                license="<verification-only, unused by verify_manifest's diff>",
            )
        )
    return tuple(entries)


def check_manifest_verification(tree: Path) -> CheckResult:
    """Delegates to `civiccast.native.runtime_manifest.verify_manifest`
    (D5), owned by another WS5 worker. Imported lazily and caught narrowly
    so a not-yet-landed module degrades to SKIPPED instead of blocking this
    worker's suite or growing a duplicate copy of that module's logic here.
    """

    try:
        from civiccast.native.runtime_manifest import (
            DuplicatePathError,
            ManifestMismatchError,
            verify_manifest,
        )
    except ImportError as exc:
        return CheckResult(
            name="manifest_verification",
            status="SKIPPED",
            detail=(
                "civiccast.native.runtime_manifest is not available yet "
                f"(ImportError: {exc}). This is a separate WS5 slice being built in "
                "parallel -- not reimplemented here."
            ),
        )

    manifest_path = tree / "runtime-manifest.json"
    try:
        manifest_text = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        return CheckResult(
            name="manifest_verification",
            status="FAIL",
            detail=f"could not read {manifest_path}: {type(exc).__name__}: {exc}",
        )
    try:
        manifest: dict[str, Any] = json.loads(manifest_text)
    except json.JSONDecodeError as exc:
        return CheckResult(
            name="manifest_verification",
            status="FAIL",
            detail=f"{manifest_path} is not valid JSON: {exc}",
        )

    manifest_paths = frozenset(record["path"] for record in manifest.get("files", []))
    entries = _hash_tree_for_manifest_verification(tree, manifest_paths=manifest_paths)

    sibling_problem = _verify_sibling_trust_artifacts(tree, manifest)
    if sibling_problem is not None:
        return CheckResult(name="manifest_verification", status="FAIL", detail=sibling_problem)

    try:
        verify_manifest(manifest, entries)
    except (ManifestMismatchError, DuplicatePathError) as exc:
        # DuplicatePathError is caught alongside the mismatch case deliberately.
        # The builder now refuses to EMIT a duplicate-path manifest, so reaching
        # this branch means runtime-manifest.json was edited after the build --
        # precisely the tamper scenario this check exists for. Letting it escape
        # as an uncaught traceback would still fail safe (non-zero exit), but it
        # would report a crash rather than a verdict, and a verdict is what an
        # operator staring at a failed install actually needs.
        return CheckResult(name="manifest_verification", status="FAIL", detail=f"{exc}")
    if "security_overlays" in manifest:
        try:
            verify_security_overlays(tree, manifest["security_overlays"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return CheckResult(name="manifest_verification", status="FAIL", detail=str(exc))
    return CheckResult(
        name="manifest_verification",
        status="PASS",
        detail=(
            f"runtime-manifest.json verified against {len(entries)} on-disk file(s) "
            "(sibling trust artifacts excluded)"
        ),
    )


def verify_security_overlays(tree: Path, declared: dict[str, str]) -> None:
    """Reconcile RC1 shared-library inventory with actual final bytes.

    Older signed manifests remain verifiable. RC1's builder emits all four
    reviewed lock identities and this check requires their exact complete set.
    """
    from scripts import (
        native_glib_soup_overlay,
        native_gstreamer_ffmpeg_overlay,
        native_openssl_overlay,
        native_security_overlay,
    )

    modules = (
        native_openssl_overlay,
        native_glib_soup_overlay,
        native_gstreamer_ffmpeg_overlay,
        native_security_overlay,
    )
    expected = {
        module.LOCK_PATH.name: hashlib.sha256(module.LOCK_PATH.read_bytes()).hexdigest()
        for module in modules
    }
    if declared != expected:
        raise ValueError("native security overlay inventory differs from reviewed source locks")
    for module in modules:
        module.verify_overlay(tree)


def check_cli_consumer_verification(tree: Path) -> CheckResult:
    """Require both hashed gstreamer-cli consumers bound to the pinned lock."""
    for relative in CLI_CONSUMER_RELATIVES:
        consumer = tree / relative
        if not consumer.is_file():
            return CheckResult(
                name="cli_consumer_verification",
                status="FAIL",
                detail=f"required pinned gstreamer-cli consumer is missing: {consumer}",
            )
    lock_text = REQUIREMENTS_FILE.read_text(encoding="utf-8")
    if _PINNED_CLI_LOCK_LINE not in lock_text or _PINNED_CLI_WINDOWS_SHA256 not in lock_text:
        return CheckResult(
            name="cli_consumer_verification",
            status="FAIL",
            detail="current runtime lock does not pin gstreamer-cli==1.28.5 Windows artifact",
        )
    try:
        manifest = json.loads((tree / "runtime-manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return CheckResult(
            name="cli_consumer_verification",
            status="FAIL",
            detail=f"could not read runtime manifest for CLI consumer: {type(exc).__name__}: {exc}",
        )
    lock_sha256 = hashlib.sha256(REQUIREMENTS_FILE.read_bytes()).hexdigest()
    if manifest.get("lock_sha256") != lock_sha256:
        return CheckResult(
            name="cli_consumer_verification",
            status="FAIL",
            detail="runtime manifest lock SHA-256 does not bind the pinned gstreamer-cli lock",
        )
    for relative in CLI_CONSUMER_RELATIVES:
        consumer = tree / relative
        expected = next(
            (entry for entry in manifest.get("files", []) if entry.get("path") == relative),
            None,
        )
        actual_hash = hashlib.sha256(consumer.read_bytes()).hexdigest()
        if (
            not isinstance(expected, dict)
            or expected.get("distribution") != CLI_CONSUMER_DISTRIBUTION
            or expected.get("sha256") != actual_hash
            or expected.get("bytes") != consumer.stat().st_size
        ):
            return CheckResult(
                name="cli_consumer_verification",
                status="FAIL",
                detail=(
                    f"gstreamer-cli consumer {relative} is absent from, or disagrees with, "
                    "runtime-manifest.json"
                ),
            )
    return CheckResult(
        name="cli_consumer_verification",
        status="PASS",
        detail="both gstreamer-cli 1.28.5 consumers are present, hashed, and lock-bound",
    )


def _gpl_negative_control_result(
    present_gpl_files: tuple[str, ...],
    present_factories: list[str] | None,
    factories_detail: str,
) -> CheckResult:
    problems: list[str] = []
    if present_gpl_files:
        problems.append(f"GPL plugin file(s) present in tree: {', '.join(present_gpl_files)}")
    if present_factories is None:
        problems.append(f"could not verify GPL factories are absent: {factories_detail}")
    elif present_factories:
        problems.append(f"GPL-excluded factories resolve: {', '.join(present_factories)}")
    if problems:
        return CheckResult(name="gpl_negative_control", status="FAIL", detail="; ".join(problems))
    return CheckResult(
        name="gpl_negative_control",
        status="PASS",
        detail=f"no GPL plugin files present ({', '.join(GPL_PLUGIN_FILENAMES)} absent); {factories_detail}",
    )


# ---------------------------------------------------------------------------
# Child-process JSON payload shape
# ---------------------------------------------------------------------------


class _FactorySweepPayload(TypedDict):
    missing: list[str]
    checked_count: int
    detail: str


class _PluginOriginPayload(TypedDict):
    outside_tree: list[list[str]]
    checked_count: int
    detail: str


class _CaptionLegPayload(TypedDict):
    ok: bool
    detail: str


class _GplFactoriesPayload(TypedDict):
    present: list[str]
    detail: str


class _ChildPayload(TypedDict):
    trace_pid: NotRequired[int]
    trace_stages: NotRequired[dict[str, list[str]]]
    # False when GStreamer itself never initialized (e.g. `gi`/PyGObject not
    # importable in the hostile environment, or `Gst.init` raised). When
    # False, the four per-check fields below carry EMPTY placeholder data
    # (no factories checked, nothing found outside the tree, no GPL factory
    # resolved) precisely because nothing ran -- the parent must never
    # interpret that emptiness as "checked and clean". See
    # `_interpret_child_output`: it short-circuits straight to a FAIL
    # quartet whenever `gst_init_ok` is False, before looking at any of the
    # per-check fields.
    gst_init_ok: bool
    gst_init_detail: str
    factory_sweep: _FactorySweepPayload
    plugin_origin: _PluginOriginPayload
    caption_leg: _CaptionLegPayload
    gpl_factories: _GplFactoriesPayload
    #: D2(d) dynamic trace: every path the child was observed to open, and
    #: every executable mapping observed when the suite finished. The parent
    #: diffs these against the tree.
    accessed_paths: list[str]
    loaded_modules: list[str]
    #: File-backed mappings with known non-executable permissions. Retained as
    #: data-access evidence so a narrow data-resource exception cannot excuse
    #: executable or unclassified mappings.
    mapped_data: list[str]
    #: Mappings whose permissions could not be classified. Kept on the strict
    #: side of module closure rather than treated as harmless data.
    unclassified_mappings: list[str]
    #: Third leg: file handles observed OPEN by sampling the process's own
    #: handle table. This is the only one of the three that can see a file
    #: opened by native code without going through Python. See
    #: `_OpenHandleSampler` for what sampling can and cannot catch.
    sampled_handles: list[str]
    #: How many times the handle table was polled. Reported, not just used,
    #: because a sampler that ran zero times would otherwise look exactly like
    #: a run in which nothing native was opened.
    handle_samples: int
    #: Absolute path of the POSITIVE CONTROL file held open via CreateFileW for
    #: the whole trace. The parent requires the sampler to have observed it; a
    #: sampler that misses a file deliberately held open in front of it has not
    #: earned the right to report that it saw nothing else either.
    native_canary: str


# ---------------------------------------------------------------------------
# Child process: everything that touches gi/Gst runs here, under the
# hostile environment built above. Never imported/called from the parent.
# ---------------------------------------------------------------------------

#: Known-content SRT caption cue embedded and decoded back by the caption
#: leg (check 3). A distinctive string, not a real caption, so a false
#: "survives" match against unrelated pipeline output is implausible.
_CAPTION_PROBE_TEXT = "CIVICCAST CLOSURE PROBE 3f9a"

_CAPTION_EMBED_PIPELINE = (
    "videotestsrc num-buffers=150 pattern=0 !"
    " video/x-raw,width=640,height=360,framerate=30/1 !"
    " videoconvert ! videoscale ! videorate !"
    " video/x-raw,width=640,height=360,framerate=30/1 !"
    " openh264enc bitrate=2000000 ! h264parse config-interval=-1 ! cc.sink"
    ' filesrc location="{srt}" ! subparse ! tttocea608 mode=pop-on ! ccconverter !'
    " closedcaption/x-cea-708,format=(string)cc_data,framerate=(fraction)30/1 !"
    " cc.caption"
    " cccombiner name=cc ! h264ccinserter remove-caption-meta=true !"
    " h264parse config-interval=-1 ! mux."
    " audiotestsrc num-buffers=235 wave=4 ! audioconvert ! audioresample !"
    " audio/x-raw,rate=48000 ! avenc_aac bitrate=128000 ! aacparse ! mux."
    ' mpegtsmux name=mux ! filesink location="{out}"'
)

_CAPTION_PIPELINE_TIMEOUT_SECONDS = 120

#: The decode-back leg reads directly from the .ts the embed leg just wrote --
#: much shorter than the embed leg's own timeout.
_CAPTION_DECODE_TIMEOUT_SECONDS = 60


def _pad_caps_name_is_video(caps_name: str) -> bool:
    """Is a pad's negotiated caps mime type a video type? `tsdemux` emits
    both a video pad (`video/x-h264`) and an audio pad (`audio/mpeg`) from
    the muxed `.ts` the embed leg wrote; only the video pad feeds the
    `h264ccextractor` decode chain -- linking the audio pad into it would
    simply fail to negotiate, but matching by mime type rather than by
    `tsdemux`'s PID-derived pad name (e.g. `video_0_0041`, not stable across
    runs) is the deliberate, correct selector.
    """

    return caps_name.startswith("video/")


def _caption_probe_survived(
    decoded_text: str,
    expected_text: str = _CAPTION_PROBE_TEXT,
) -> bool:
    """Did the CEA-608 embed+decode-back round trip preserve the probe text?
    `cea608tott` emits WebVTT-framed text (a `WEBVTT` header, a timecode
    line, then the cue text, each cue separated by a blank line) rather than
    a bare string equal to the input, so this is a substring check against
    the decoded output -- not an exact-equality text round trip, but a real
    one: the same probe bytes that went in via `tttocea608` are asserted
    present, byte-for-byte, in what `cea608tott` produced coming back out.
    """

    payload_lines = [
        line.strip()
        for line in decoded_text.splitlines()
        if line.strip()
        and line.strip() != "WEBVTT"
        and "-->" not in line
        and not line.strip().isdigit()
    ]
    decoded_payload = " ".join(" ".join(payload_lines).split())
    normalized_expected = " ".join(expected_text.split())
    return normalized_expected in decoded_payload


def _srt_timestamp(seconds: float) -> str:
    milliseconds = round(seconds * 1000)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"


def _render_caption_probe_srt(probe_text: str) -> str:
    """Paginate a canary exactly as the production CEA feed does."""

    lines = textwrap.wrap(
        " ".join(probe_text.split()),
        width=32,
        break_long_words=True,
        break_on_hyphens=False,
        drop_whitespace=True,
        replace_whitespace=True,
    )
    pages = [lines[index : index + 2] for index in range(0, len(lines), 2)]
    page_seconds = 4.0 / len(pages)
    rendered: list[str] = []
    for index, page in enumerate(pages, start=1):
        start = (index - 1) * page_seconds
        end = index * page_seconds
        rendered.extend(
            (
                str(index),
                f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}",
                *page,
                "",
            )
        )
    return "\n".join(rendered)


# ---------------------------------------------------------------------------
# D2(d) -- dynamic file-access trace
#
# The static PE-import walk cannot see what a process opens at RUNTIME: a
# plugin that calls LoadLibrary, a library that reads a data file by absolute
# path, a codec that falls back to a system copy of itself. The spec's D2(d)
# requires the D6 suite to run under a file-access trace and fail the build on
# anything loaded from outside the tree. Without it, a package can pass every
# other check and still depend on something that is not in the box (Codex r1
# finding CC-WS5-PKG-001, Blocker).
#
# The spec suggests "procmon boot-log or equivalent". Procmon needs a kernel
# driver and therefore Administrator, which this build path deliberately does
# not have. This is the equivalent, built from in-process mechanisms that need
# no privilege:
#
#   1. A CPython audit hook (`sys.addaudithook`). Installed before ANY media
#      code is imported, it sees `open`, `os.open`, `os.listdir`, `glob.glob`
#      and -- critically -- `ctypes.dlopen`, which is how a native library gets
#      pulled in dynamically.
#   2. A mapped-file scan taken after the suite has run. Executable mappings
#      remain strict module evidence; known non-executable mappings remain data
#      access evidence; unknown protections remain strict.
#
# HONEST LIMITS, stated rather than papered over: (1) catches Python-mediated
# access and dlopen but not a file opened purely inside native code via
# CreateFileW; (2) catches file mappings still present at the end but misses
# files opened and unmapped mid-run. Together with the sampled-handle leg,
# these cover the dominant closure risks without claiming kernel-trace
# completeness.
# ---------------------------------------------------------------------------

_TRACE_AUDIT_EVENTS = frozenset(
    {"open", "os.open", "os.listdir", "os.scandir", "glob.glob", "ctypes.dlopen"}
)


def _harness_baseline_modules() -> frozenset[tuple[int, int]]:
    """Files already loaded by the harness before any product code runs.

    Taken at trace-install time, before `gi`/`Gst` are imported. Everything in
    it is this verifier's own bootstrap -- the interpreter, its stdlib, pydantic
    pulled in by the `civiccast.native` package `__init__`, psutil used by the
    module enumeration below. None of it is the packaged runtime.

    This is a BASELINE, not an allowlist, and the distinction is the point:
    excluding "every module the harness ever loads" would also excuse a product
    library loaded from outside the tree, which is the exact thing this check
    exists to catch. Excluding only what was loaded BEFORE the product started
    cannot hide a product load, because a product load happens after.
    """
    # Imported eagerly so it lands in the baseline rather than appearing later
    # as if the packaged runtime had pulled it in.
    with contextlib.suppress(ImportError):
        import psutil  # noqa: F401

    baseline: set[tuple[int, int]] = set()
    for module in list(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if not filename:
            continue
        identity = _file_identity(Path(filename))
        if identity is not None:
            baseline.add(identity)
    # Native bootstrap can also map files absent from sys.modules. Snapshot
    # those exact file identities before gi/Gst loads; a later product mapping
    # remains subject to the ordinary strict closure rules.
    for mappings in _mapped_file_paths():
        for filename in mappings:
            identity = _file_identity(Path(filename))
            if identity is not None:
                baseline.add(identity)
    return frozenset(baseline)


def _file_identity(path: Path) -> tuple[int, int] | None:
    """(device, index) -- the identity of the FILE, not of a path to it.

    Package managers hardlink installed files, so one physical file legitimately
    has several names (a venv's copy and the shared cache entry, say) and the
    module loader and the OS module enumeration may each report a different one.
    Path comparison therefore reports the same harness dependency as an
    unexpected outside-tree load; `Path.resolve()` cannot help because hardlinks
    have no target to resolve to -- every name is equally real. Stat identity is
    the only thing that actually answers "is this the same file".
    """
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_dev, stat.st_ino)


def _drop_baseline(paths: Iterable[str], baseline: frozenset[tuple[int, int]]) -> list[str]:
    """Traced paths, minus the files the harness had already loaded.

    Compared by file identity, not by name, so a hardlinked harness dependency
    reported under a different path is still recognised as the same file.
    """
    kept: list[str] = []
    for raw in dict.fromkeys(paths):
        identity = _file_identity(Path(raw))
        if identity is not None and identity in baseline:
            continue
        kept.append(raw)
    return sorted(kept)


def _install_access_trace() -> list[str]:
    """Install the audit hook and return the list it accumulates into.

    The list is returned rather than kept global so the caller owns it, and so
    a failure to install is visible (an empty list that never grows) instead of
    silently producing a clean-looking trace.
    """

    accessed: list[str] = []

    def _hook(event: str, args: tuple[Any, ...]) -> None:
        if event not in _TRACE_AUDIT_EVENTS or not args:
            return
        target = args[0]
        if isinstance(target, (str, bytes, os.PathLike)):
            try:
                accessed.append(
                    os.fspath(target)
                    if not isinstance(target, bytes)
                    else target.decode("utf-8", "replace")
                )
            except Exception:
                return

    sys.addaudithook(_hook)
    return accessed


def _split_memory_mapping_paths(
    mappings: Iterable[Any],
) -> tuple[list[str], list[str], list[str]]:
    """Split backing files by the strongest protection seen for each file.

    psutil reports mapped regions, not just executable modules. A path is a
    module if ANY region has execute permission. A known non-executable map is
    data; missing or unfamiliar protection evidence stays strict. File
    identity groups hardlinks and path aliases before deciding, so an
    executable region cannot be hidden by a second read-only map of the same
    file.
    """
    groups: dict[tuple[Any, ...], tuple[set[str], int]] = {}
    windows_executable = frozenset(("x", "xr", "xrw", "xwc"))
    windows_data = frozenset(("r", "rw", "wc"))
    for mapping in mappings:
        path = getattr(mapping, "path", None)
        if not isinstance(path, str) or not path:
            continue
        permissions = getattr(mapping, "perms", None)
        normalized_permissions = permissions.casefold() if isinstance(permissions, str) else ""
        if normalized_permissions in windows_executable:
            state = 3
        elif normalized_permissions in windows_data:
            state = 1
        elif re.fullmatch(r"[r-][w-][x-][ps]", normalized_permissions):
            state = 3 if normalized_permissions[2] == "x" else 1
        else:
            state = 2
        identity = _file_identity(Path(path))
        key = (
            ("file", *identity)
            if identity is not None
            else (
                "path",
                os.path.normcase(os.path.normpath(path)),
            )
        )
        previous = groups.get(key)
        paths = {path} if previous is None else {*previous[0], path}
        strongest_state = state if previous is None else max(state, previous[1])
        groups[key] = (paths, strongest_state)

    executable: list[str] = []
    data: list[str] = []
    unclassified: list[str] = []
    for paths, state in groups.values():
        destination = executable if state == 3 else unclassified if state == 2 else data
        destination.extend(paths)
    return sorted(executable), sorted(data), sorted(unclassified)


def _mapped_file_paths() -> tuple[list[str], list[str], list[str]]:
    """Return executable, known-data, and unknown file-backed mappings."""
    try:
        import psutil
    except ImportError:
        return [], [], []
    try:
        process = psutil.Process()
        try:
            mappings = process.memory_maps(grouped=False)
        except TypeError:
            # Older psutil builds may lack ungrouped protection details. Their
            # paths remain strict because _split_memory_mapping_paths treats
            # missing permissions as unclassified.
            mappings = process.memory_maps()
        return _split_memory_mapping_paths(mappings)
    except Exception:
        return [], [], []


def _trace_path_key(raw: str) -> tuple[Any, ...]:
    """Compare evidence paths by file identity where possible, then by path."""
    path = Path(raw)
    identity = _file_identity(path)
    if identity is not None:
        return ("file", *identity)
    with contextlib.suppress(OSError, RuntimeError):
        path = path.resolve()
    return ("path", os.path.normcase(os.path.normpath(str(path))))


class _OpenHandleSampler:
    """Poll this process's OPEN FILE HANDLES from a background thread.

    Third leg of the D2(d) trace, and the one that reaches where the other two
    cannot. The CPython audit hook sees only opens that go through Python; the
    end-of-run module snapshot sees only things still MAPPED when the run ends.
    Neither observes native code calling `CreateFileW` directly -- which is
    precisely what GStreamer, fontconfig, GIO, Media Foundation and the GPU
    drivers do when they read registries, caches, config and codec resources
    (Codex CC-WS5-PKG-001, round 2).

    A handle, unlike a call, is *state*: while native code holds a file open,
    the OS will list it, so it can be observed without hooking anything and
    without administrator rights.

    STATED LIMITATION -- this SAMPLES, so it can only see an open that is held
    across at least one poll. A file opened and closed entirely between two
    samples is invisible to it. That is a real gap, not a theoretical one, and
    it is why the trace's own output prints which mechanisms ran rather than
    announcing "all file access observed". Closing it completely needs kernel
    ETW file-I/O tracing, which requires administrator rights this build
    environment does not have.
    """

    def __init__(self, interval: float = 0.005) -> None:
        self._interval = interval
        self._seen: set[str] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._samples = 0

    @staticmethod
    def _own_handle_values() -> list[int]:
        """Every handle value this process currently owns -- no fixed ceiling.

        The previous implementation probed handle values 4, 8, 12 ... up to a
        hardcoded 16384 and assumed nothing lived above it. Round 3
        (CC-WS5-PKG-001) falsified that in one line: after allocating ~4,300
        event handles, a native file sat at handle 18140 and was missed
        completely. A scan ceiling is an assumption about how many handles a
        process will ever open, and the whole point of this trace is to observe
        behaviour we did not predict.

        `NtQueryInformationProcess(ProcessHandleInformation)` asks the kernel
        for THIS process's actual handle table, so there is no ceiling to
        exceed. It is process-scoped, so unlike the system-wide query psutil
        uses it cannot overflow on a busy machine.
        """
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)

        class _HandleTableEntry(ctypes.Structure):
            _fields_ = (
                ("HandleValue", ctypes.c_size_t),
                ("HandleCount", ctypes.c_size_t),
                ("PointerCount", ctypes.c_size_t),
                ("GrantedAccess", ctypes.c_ulong),
                ("ObjectTypeIndex", ctypes.c_ulong),
                ("HandleAttributes", ctypes.c_ulong),
                ("Reserved", ctypes.c_ulong),
            )

        process_handle_information = 51  # PROCESSINFOCLASS
        status_info_length_mismatch = 0xC0000004
        size = 0x10000
        for _attempt in range(8):
            buffer = ctypes.create_string_buffer(size)
            returned = ctypes.c_ulong(0)
            status = ntdll.NtQueryInformationProcess(
                ctypes.c_void_p(-1),  # pseudo-handle for the current process
                ctypes.c_ulong(process_handle_information),
                buffer,
                ctypes.c_ulong(size),
                ctypes.byref(returned),
            )
            if status & 0xFFFFFFFF == status_info_length_mismatch:
                size *= 4  # the table grew between sizing and reading; retry bigger
                continue
            if status != 0:
                return []
            count = ctypes.c_size_t.from_buffer(buffer, 0).value
            header = ctypes.sizeof(ctypes.c_size_t) * 2
            entries = (_HandleTableEntry * count).from_buffer(buffer, header)
            return [entry.HandleValue for entry in entries]
        return []

    @staticmethod
    def _own_open_file_paths() -> list[str]:
        """Every on-disk file this process currently holds a handle to.

        Deliberately NOT `psutil.Process().open_files()`. That was the first
        implementation and it does not work here: psutil goes through
        `NtQuerySystemInformation(SystemExtendedHandleInformation)`, which
        enumerates handles for the WHOLE MACHINE, and on this box it dies with
        "SystemExtendedHandleInformation buffer too big" every single call. The
        sampler therefore recorded zero samples on the first real run -- caught
        only because taking zero samples is itself a hard failure here.

        We do not need the system-wide table. We are inspecting OUR OWN
        process, whose handles we can ask about directly: probe the handle
        value space, keep the ones `GetFileType` reports as disk files, and
        resolve each with `GetFinalPathNameByHandleW`. No administrator rights,
        no system-wide enumeration, nothing to overflow.

        Invalid handle values are not a hazard: `GetFileType` returns
        `FILE_TYPE_UNKNOWN` for them, which is simply skipped.
        """
        if not hasattr(ctypes, "WinDLL"):  # pragma: no cover - non-Windows dev host
            return []

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetFileType.argtypes = [wintypes.HANDLE]
        k32.GetFileType.restype = wintypes.DWORD
        k32.GetFinalPathNameByHandleW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        k32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        k32.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        k32.GetFileInformationByHandleEx.restype = wintypes.BOOL

        class _FileStandardInfo(ctypes.Structure):
            _fields_ = (
                ("AllocationSize", ctypes.c_longlong),
                ("EndOfFile", ctypes.c_longlong),
                ("NumberOfLinks", wintypes.DWORD),
                ("DeletePending", wintypes.BOOLEAN),
                ("Directory", wintypes.BOOLEAN),
            )

        file_standard_info = 1  # FILE_INFO_BY_HANDLE_CLASS::FileStandardInfo
        file_type_disk = 0x0001
        found: list[str] = []
        buffer = ctypes.create_unicode_buffer(32768)
        info = _FileStandardInfo()
        for raw in _OpenHandleSampler._own_handle_values():
            handle = wintypes.HANDLE(raw)
            if k32.GetFileType(handle) != file_type_disk:
                continue
            # Classify the object FROM THE HANDLE, never by stat-ing its
            # pathname (CC-WS5-PKG-013). A handle can carry a name the process
            # is not allowed to stat -- the auditor's host held one under the
            # NTFS deleted-object namespace, C:\$Extend\$Deleted\..., where
            # Path.is_dir() raises PermissionError. One unstatable handle must
            # cost exactly its own entry, not the whole poll.
            if not k32.GetFileInformationByHandleEx(
                handle, file_standard_info, ctypes.byref(info), ctypes.sizeof(info)
            ):
                # The handle closed between enumeration and this query, or the
                # object refuses even handle-based queries. Skip THIS entry.
                continue
            # DIRECTORY handles are not file dependencies. Windows holds one
            # open for every process's working directory, so without this the
            # very first real run failed on the REPO ROOT -- a true statement
            # ("a handle outside the tree is open") that answers the wrong
            # question. The closure question is whether the runtime LOADS
            # files from outside the tree; holding a directory open loads
            # nothing. Any file actually read from such a directory still
            # appears here in its own right, so this narrows noise, not
            # coverage.
            if info.Directory:
                continue
            # DELETE-PENDING files have already been unlinked from the
            # namespace (their only remaining name is the $Extend\$Deleted
            # placeholder, which cannot be stat-ed by name at all). A file
            # that no longer exists cannot be part of the shipped closure,
            # and its placeholder name is unactionable for the membership
            # check either way.
            if info.DeletePending:
                continue
            length = k32.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
            if not length or length >= len(buffer):
                continue
            # Strip the \\?\ extended-length prefix so these paths compare
            # against the tree the same way every other traced path does.
            path = buffer.value
            path = path[4:] if path.startswith("\\\\?\\") else path
            found.append(path)
        return found

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._seen.update(self._own_open_file_paths())
                self._samples += 1
            except Exception:
                # A handle can close between GetFileType and the path query;
                # that race is normal and must never take down the workload
                # being observed.
                pass
            self._stop.wait(self._interval)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="cc-handle-sampler")
        self._thread.start()

    def stop(self) -> tuple[list[str], int]:
        """Stop sampling; return (paths seen, number of samples taken)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        return sorted(self._seen), self._samples


def _open_native_canary(tree: Path) -> tuple[int, str]:
    """Hold a tree file open via CreateFileW, bypassing Python entirely.

    The sampler's whole justification is that it sees native file access. Round
    3 (CC-WS5-PKG-001) showed the verifier would report PASS -- while printing
    "via THREE mechanisms" -- on a payload where the sampler observed NOTHING.
    A mechanism that is silently dead is worse than one that is absent, because
    the absent one does not appear in the evidence.

    So the run now holds a POSITIVE CONTROL: a real file, inside the tree,
    opened through the Win32 API with no Python file object involved, held for
    the duration of the trace. If the sampler cannot see that, it cannot be
    trusted to have seen anything, and the check fails rather than passing on
    two mechanisms while claiming three.

    Opened read-only with full sharing so it can never interfere with the
    workload it is measuring.
    """
    canary = tree / "SHA256SUMS"
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
    generic_read = 0x80000000
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    handle = k32.CreateFileW(str(canary), generic_read, share_all, None, open_existing, 0x80, None)
    return handle, str(canary)


def _close_native_canary(handle: int) -> None:
    ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle(wintypes.HANDLE(handle))


def _child_init_gst() -> Any:
    """Lazily imports and initializes GStreamer's PyGObject binding. `gi` is
    a build/verification-time-only dependency (not shipped, not declared in
    pyproject) -- imported here, inside the child process, never at module
    level, so importing this module on a box without PyGObject (e.g. this
    dev box) still succeeds. Raises whatever the import/init raised; callers
    catch it."""

    import gi  # type: ignore[import-not-found]

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # type: ignore[import-not-found]

    Gst.init(None)
    return Gst


def _child_factory_sweep(gst: Any) -> _FactorySweepPayload:
    missing = sorted(name for name in REQUIRED_FACTORIES if gst.ElementFactory.find(name) is None)
    checked_count = len(REQUIRED_FACTORIES)
    detail = (
        "all required factories resolved"
        if not missing
        else f"{len(missing)} of {checked_count} required factories did not resolve: {', '.join(missing)}"
    )
    return _FactorySweepPayload(missing=missing, checked_count=checked_count, detail=detail)


def _child_plugin_origin(gst: Any, tree: Path) -> _PluginOriginPayload:
    registry = gst.Registry.get()
    plugins: list[tuple[str, str | None]] = [
        (plugin.get_name(), plugin.get_filename()) for plugin in registry.get_plugin_list()
    ]
    outside = find_plugins_outside_tree(tree, plugins)
    detail = (
        "all loaded plugins resolve inside the tree"
        if not outside
        else f"{len(outside)} plugin(s) loaded from outside the tree"
    )
    return _PluginOriginPayload(
        outside_tree=[[name, filename] for name, filename in outside],
        checked_count=len(plugins),
        detail=detail,
    )


def _child_gpl_factories(gst: Any) -> _GplFactoriesPayload:
    resolved = {name: gst.ElementFactory.find(name) is not None for name in EXCLUDED_GPL_FACTORIES}
    present = find_present_gpl_factories(EXCLUDED_GPL_FACTORIES, resolved)
    detail = (
        "no GPL-excluded factories resolve"
        if not present
        else f"GPL-excluded factories resolved: {', '.join(present)}"
    )
    return _GplFactoriesPayload(present=list(present), detail=detail)


def _child_caption_leg(
    gst: Any,
    tree: Path,
    probe_text: str = _CAPTION_PROBE_TEXT,
) -> _CaptionLegPayload:
    with tempfile.TemporaryDirectory(prefix="civiccast-caption-leg-") as tmp:
        srt_path = Path(tmp) / "probe.srt"
        out_path = Path(tmp) / "probe.ts"
        srt_path.write_text(_render_caption_probe_srt(probe_text), encoding="utf-8")

        pipeline_str = _CAPTION_EMBED_PIPELINE.format(
            srt=srt_path.resolve().as_posix(), out=out_path.resolve().as_posix()
        )
        pipeline = gst.parse_launch(pipeline_str)
        pipeline.set_state(gst.State.PLAYING)
        bus = pipeline.get_bus()
        message = bus.timed_pop_filtered(
            _CAPTION_PIPELINE_TIMEOUT_SECONDS * gst.SECOND,
            gst.MessageType.EOS | gst.MessageType.ERROR,
        )
        embed_result = message.type.first_value_name if message else "timeout"
        if message is not None and message.type == gst.MessageType.ERROR:
            error, debug = message.parse_error()
            embed_result = f"error: {error} debug: {debug}"
        pipeline.set_state(gst.State.NULL)

        if message is None or message.type != gst.MessageType.EOS:
            return _CaptionLegPayload(
                ok=False, detail=f"embed pipeline did not reach EOS: {embed_result}"
            )

        return _child_caption_decode_back(gst, out_path, probe_text=probe_text)


def _child_caption_decode_back(
    gst: Any,
    out_path: Path,
    *,
    probe_text: str = _CAPTION_PROBE_TEXT,
) -> _CaptionLegPayload:
    """Decode-back half of the caption leg, entirely in-tree GStreamer --
    the packaged tree ships no `ffmpeg.exe`, so a decode check built around
    shelling out to one can never pass (the bug an adversarial review
    caught: it failed with FileNotFoundError against every real build).
    `tsdemux` -> `h264parse` -> `h264ccextractor` pulls the CEA-708 SEI back
    out of the H.264 elementary stream `h264ccinserter` wrote it into;
    `ccconverter` converts CEA-708 -> CEA-608; `cea608tott` converts
    CEA-608 -> plain/WebVTT text. Every element here was confirmed present
    in the real packaged tree (`gstclosedcaption.dll`, `gstrsclosedcaption.dll`)
    before this was written -- this is a genuine text content round trip,
    not a caption-meta-presence stand-in.
    """

    pipeline = gst.Pipeline.new("civiccast-caption-decode")
    factory_names = (
        "filesrc",
        "tsdemux",
        "queue",
        "h264parse",
        "capsfilter",
        "h264ccextractor",
        "ccconverter",
        "cea608tott",
        "appsink",
    )
    made = {name: gst.ElementFactory.make(name, name) for name in factory_names}
    missing_elements = [name for name, el in made.items() if el is None]
    if missing_elements:
        return _CaptionLegPayload(
            ok=False,
            detail=(
                "decode-back pipeline could not be built -- element(s) failed to "
                f"instantiate: {', '.join(missing_elements)}"
            ),
        )

    filesrc = made["filesrc"]
    demux = made["tsdemux"]
    queue = made["queue"]
    h264parse = made["h264parse"]
    capsfilter = made["capsfilter"]
    extractor = made["h264ccextractor"]
    ccconverter = made["ccconverter"]
    cea608tott = made["cea608tott"]
    appsink = made["appsink"]
    elements = tuple(made[name] for name in factory_names)

    filesrc.set_property("location", str(out_path))
    capsfilter.set_property(
        "caps", gst.Caps.from_string("video/x-h264,alignment=au,stream-format=avc")
    )
    appsink.set_property("emit-signals", True)
    appsink.set_property("sync", False)

    for element in elements:
        pipeline.add(element)
    filesrc.link(demux)
    queue.link(h264parse)
    h264parse.link(capsfilter)
    capsfilter.link(extractor)
    extractor.link(ccconverter)
    ccconverter.link(cea608tott)
    cea608tott.link(appsink)

    def on_pad_added(_demux: Any, pad: Any) -> None:
        # tsdemux emits both a video and an audio pad from the muxed .ts;
        # only the video pad feeds the decode chain (see
        # `_pad_caps_name_is_video`'s docstring for why matching is done by
        # negotiated mime type, not by tsdemux's PID-derived pad name).
        caps = pad.get_current_caps() or pad.query_caps(None)
        if caps is None or caps.get_size() == 0:
            return
        caps_name = caps.get_structure(0).get_name()
        if not _pad_caps_name_is_video(caps_name):
            # Audio can arrive before the video pad exists. Leaving that first
            # pad unlinked makes tsdemux stop before it can expose the video.
            # Consume ignored streams while keeping caption extraction video-only.
            discard = gst.ElementFactory.make("fakesink", None)
            discard.set_property("sync", False)
            discard.set_property("async", False)
            pipeline.add(discard)
            discard.sync_state_with_parent()
            pad.link(discard.get_static_pad("sink"))
            return
        sink_pad = queue.get_static_pad("sink")
        if not sink_pad.is_linked():
            pad.link(sink_pad)

    demux.connect("pad-added", on_pad_added)

    collected = bytearray()

    def on_new_sample(sink: Any) -> Any:
        sample = sink.emit("pull-sample")
        buf = sample.get_buffer()
        ok, mapinfo = buf.map(gst.MapFlags.READ)
        if ok:
            collected.extend(bytes(mapinfo.data))
            buf.unmap(mapinfo)
        return gst.FlowReturn.OK

    appsink.connect("new-sample", on_new_sample)

    pipeline.set_state(gst.State.PLAYING)
    bus = pipeline.get_bus()
    message = bus.timed_pop_filtered(
        _CAPTION_DECODE_TIMEOUT_SECONDS * gst.SECOND,
        gst.MessageType.EOS | gst.MessageType.ERROR,
    )
    decode_result = message.type.first_value_name if message else "timeout"
    if message is not None and message.type == gst.MessageType.ERROR:
        error, debug = message.parse_error()
        decode_result = f"error: {error} debug: {debug}"
    pipeline.set_state(gst.State.NULL)

    if message is None or message.type != gst.MessageType.EOS:
        return _CaptionLegPayload(
            ok=False, detail=f"decode-back pipeline did not reach EOS: {decode_result}"
        )

    decoded_text = collected.decode("utf-8", errors="replace")
    if not _caption_probe_survived(decoded_text, probe_text):
        return _CaptionLegPayload(
            ok=False,
            detail=(
                f"caption text {probe_text!r} did not survive the embed+decode-back "
                f"round trip (tsdemux -> h264parse -> h264ccextractor -> ccconverter -> "
                f"cea608tott); decoded output: {decoded_text!r}"
            ),
        )
    return _CaptionLegPayload(
        ok=True,
        detail=(
            "caption text survived the embed+decode-back round trip, decoded entirely "
            "in-tree via tsdemux -> h264parse -> h264ccextractor -> ccconverter -> "
            f"cea608tott (no ffmpeg): {decoded_text!r}"
        ),
    )


def _child_main(
    tree: Path,
    *,
    caption_probe_text: str = _CAPTION_PROBE_TEXT,
) -> _ChildPayload:
    # Installed FIRST, before any media code is imported, so the trace covers
    # GStreamer's own start-up -- which is exactly where an outside-the-tree
    # load would happen.
    baseline = _harness_baseline_modules()
    trace_stages: dict[str, list[str]] = {}

    def snapshot(stage: str) -> None:
        trace_stages[stage] = sorted({p for paths in _mapped_file_paths() for p in paths})

    snapshot("before_gi_gst")
    accessed = _install_access_trace()
    # Started alongside the audit hook and BEFORE GStreamer loads, so native
    # opens during plugin-registry scanning -- the busiest native file-access
    # window in the whole run -- are inside the sampled window.
    sampler = _OpenHandleSampler()
    sampler.start()
    # Positive control: prove the sampler can see native access on THIS run,
    # rather than trusting that it can. Held open for the whole trace.
    canary_handle, canary_path = _open_native_canary(tree)

    try:
        gst = _child_init_gst()
    except Exception as exc:
        error_detail = f"GStreamer could not be initialized in the hostile environment: {type(exc).__name__}: {exc}"
        sampled, samples = sampler.stop()
        _close_native_canary(canary_handle)
        mapped_modules, mapped_data, unclassified_mappings = _mapped_file_paths()
        return _ChildPayload(
            trace_pid=os.getpid(),
            trace_stages=trace_stages,
            gst_init_ok=False,
            gst_init_detail=error_detail,
            factory_sweep=_FactorySweepPayload(missing=[], checked_count=0, detail=error_detail),
            plugin_origin=_PluginOriginPayload(
                outside_tree=[], checked_count=0, detail=error_detail
            ),
            caption_leg=_CaptionLegPayload(ok=False, detail=error_detail),
            gpl_factories=_GplFactoriesPayload(present=[], detail=error_detail),
            accessed_paths=_drop_baseline(accessed, baseline),
            loaded_modules=_drop_baseline(mapped_modules, baseline),
            mapped_data=_drop_baseline(mapped_data, baseline),
            unclassified_mappings=_drop_baseline(unclassified_mappings, baseline),
            sampled_handles=_drop_baseline(sampled, baseline),
            handle_samples=samples,
            native_canary=canary_path,
        )

    snapshot("after_gst_init")
    factory_sweep = _child_factory_sweep(gst)
    snapshot("after_factory_sweep")
    plugin_origin = _child_plugin_origin(gst, tree)
    caption_leg = _child_caption_leg(gst, tree, probe_text=caption_probe_text)
    snapshot("after_caption_leg")
    gpl_factories = _child_gpl_factories(gst)

    sampled, samples = sampler.stop()
    _close_native_canary(canary_handle)

    # Snapshot AFTER the suite has run, so the trace reflects everything the
    # real pipelines pulled in, not just import-time.
    mapped_modules, mapped_data, unclassified_mappings = _mapped_file_paths()
    return _ChildPayload(
        trace_pid=os.getpid(),
        trace_stages=trace_stages,
        gst_init_ok=True,
        gst_init_detail=f"GStreamer initialized: {gst.version_string()}",
        factory_sweep=factory_sweep,
        plugin_origin=plugin_origin,
        caption_leg=caption_leg,
        gpl_factories=gpl_factories,
        accessed_paths=_drop_baseline(accessed, baseline),
        loaded_modules=_drop_baseline(mapped_modules, baseline),
        mapped_data=_drop_baseline(mapped_data, baseline),
        unclassified_mappings=_drop_baseline(unclassified_mappings, baseline),
        sampled_handles=_drop_baseline(sampled, baseline),
        handle_samples=samples,
        native_canary=canary_path,
    )


# ---------------------------------------------------------------------------
# Parent process orchestration
# ---------------------------------------------------------------------------

_CHILD_FLAG = "--internal-child-runner"
_CHILD_PROCESS_TIMEOUT_SECONDS = 240.0


#: Roots a traced access may legitimately come from besides the tree itself.
#: Everything else is a closure miss. Deliberately NOT prefix-matched loosely:
#: each is resolved to an absolute path and compared with the same containment
#: predicate used for plugins, so `C:\\Windows-evil\\x.dll` cannot pass as
#: `C:\\Windows`.
def _classify_os_load(candidate: Path) -> str | None:
    """Classify an outside-tree load that the OS or our own interpreter owns.

    Returns a category name, or None if this is not an OS/host load at all.

    Three categories, kept distinct because they carry different obligations:

      "reviewed-os-dependency"
          Under %SystemRoot% AND in OS_DEPENDENCY_INVENTORY (or an API set).
          These are the OS components the closure deliberately does not ship,
          each checked to exist on a real Windows. Fully accounted for.

      "host-python-runtime"
          Under the interpreter root the child is actually running from. This
          is HOST_PYTHON_REQUIREMENT made observable: the installer must place a
          CPython beside the tree, and this is that CPython. Anchored to the
          real interpreter path, not to `sys.path` generally -- round 2's
          finding was that permitting the whole import path let a genuine
          product dependency falling back to the developer venv read as fine.

      "unreviewed-os-load"
          Under %SystemRoot% or a driver store, but NOT in the inventory.
          Windows loads a great deal of its own machinery into any process
          (locale tables, D3D12Core, DXCore, driver shims). These are not
          product dependencies in the closure sense, but they are also not
          things anyone reviewed -- so they are PERMITTED AND REPORTED, never
          silently absorbed. The check prints them so an unexpected codec or
          shim appearing here is visible rather than invisible.
    """
    system_root = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
    name = candidate.name.lower()

    if is_inside_tree(system_root, candidate):
        if name in OS_DEPENDENCY_INVENTORY or name.startswith(("api-ms-win-", "ext-ms-win-")):
            return "reviewed-os-dependency"
        return "unreviewed-os-load"

    interpreter_root = Path(sys.base_prefix)
    if is_inside_tree(interpreter_root, candidate):
        return "host-python-runtime"

    return None


def _permitted_trace_roots(tree: Path) -> tuple[Path, ...]:
    """Roots a traced access may legitimately come from besides the tree.

    Three categories, each here for a stated reason rather than because adding
    it made the check go green:

    1. The tree, %SystemRoot% (the OS DLLs SYSTEM_DLL_ALLOWLIST already accounts
       for) and the temp dir (the fresh GST_REGISTRY and the probe media this
       suite itself writes).
    2. THE TEST HARNESS ITSELF -- the interpreter running this verifier and
       everything on its import path. The question this check asks is "did the
       PRODUCT tree load anything from outside itself", and the harness's own
       dependencies (pydantic, psutil, pytest machinery) are not the product.
       They appear only because importing `civiccast.native.runtime_closure`
       executes the package `__init__`, which pulls in app dependencies the
       packaging tool does not need -- a real coupling, recorded as a follow-up,
       but not a closure defect.
    3. GPU shader caches. The graphics driver WRITES these during a real encode;
       they are driver-managed cache artifacts, not code loaded into the
       process.

    Deliberately NOT permitted: anything under `Program Files\\WindowsApps`.
    That is where Microsoft Store media extensions live, and a media extension
    loaded from there is a genuine runtime dependency the operator's machine may
    not have -- exactly what this check exists to surface.
    """
    local_appdata = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local")))

    roots = [
        tree,
        # -- driver-written caches --
        # Kept as directories deliberately: the graphics driver WRITES
        # per-machine shader caches with generated filenames during a real
        # encode. They are driver output, not code loaded into the process,
        # and their names cannot be enumerated in advance.
        local_appdata / "D3DSCache",
        local_appdata / "NVIDIA" / "DXCache",
        local_appdata / "AMD",
        local_appdata / "Intel" / "ShaderCache",
    ]
    # DELIBERATELY NOT ROOTS ANY MORE (CC-WS5-PKG-001, round 2):
    #   %SystemRoot%     -> a directory is not an inventory; OS files are now
    #                       matched against OS_DEPENDENCY_INVENTORY by name.
    #   sys.prefix       -> the harness is identified by FILE IDENTITY in the
    #   sys.base_prefix     pre-product baseline, which is exact. Permitting the
    #   sys.path            whole interpreter tree meant a genuine product
    #                       dependency that fell back to the developer's venv
    #                       read as "inside a permitted root".
    #   %TEMP%           -> only the exact per-run files this suite itself
    #                       creates are permitted, not the whole temp directory.

    # BOTH raw and resolved forms are kept. Package files are frequently
    # hardlinked or symlinked out of a package-manager cache, so resolving alone
    # relocates a harness import to a cache directory that matches no root and
    # then reads as an outside-tree load.
    permitted: list[Path] = []
    for root in roots:
        if not str(root):
            continue
        permitted.append(root)
        try:
            permitted.append(root.resolve())
        except OSError:
            continue
    return tuple(dict.fromkeys(permitted))


def _is_windows_cache_database(candidate: Path) -> bool:
    """Return whether ``candidate`` is one exact Windows cache DB file.

    Windows opens per-user and machine cache databases while initializing
    otherwise self-contained media processes. These are OS-managed data, not
    executable code the packaged GStreamer closure must ship. Keep this
    narrower than a permitted root: only an existing ``.db`` file immediately
    inside the canonical Windows cache directory qualifies. DLLs, executables,
    nested paths, lookalike directories, and unexpanded ``%SystemDrive%`` paths
    remain closure failures.
    """
    if candidate.suffix.lower() != ".db":
        return False

    cache_roots: list[Path] = []
    for variable in ("LOCALAPPDATA", "PROGRAMDATA"):
        value = os.environ.get(variable)
        if value:
            cache_roots.append(Path(value) / "Microsoft" / "Windows" / "Caches")

    try:
        parent = os.path.normcase(str(candidate.resolve().parent))
        return any(parent == os.path.normcase(str(root.resolve())) for root in cache_roots)
    except OSError:
        return False


def _classify_traced_accesses(
    accessed: Iterable[str], loaded: Iterable[str], *, tree: Path
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Split traced paths into accounted, outside, unreviewed OS and reported resources.

    Only EXISTING absolute paths are judged. A trace records attempts, and a
    probe for a file that is not there ("does gstreamer live in C:/gstreamer?")
    is not evidence the tree depends on it -- treating a failed probe as a
    closure miss would make the check cry wolf until someone stopped reading it.
    """
    roots = _permitted_trace_roots(tree)
    accessed_paths = tuple(dict.fromkeys(accessed))
    loaded_paths = tuple(dict.fromkeys(loaded))
    inside: list[str] = []
    outside: list[str] = []
    unreviewed: list[str] = []
    package_resources: list[str] = []
    loaded_path_keys: set[str] = set()
    loaded_file_ids: set[tuple[int, int]] = set()
    for raw in loaded_paths:
        try:
            loaded_path = Path(raw)
            if not loaded_path.is_absolute() or not loaded_path.exists():
                continue
            loaded_resolved = loaded_path.resolve()
            loaded_path_keys.add(os.path.normcase(str(loaded_resolved)))
            file_id = _file_identity(loaded_resolved)
            if file_id is not None:
                loaded_file_ids.add(file_id)
        except OSError:
            continue

    def classify(raw: str, *, access_only: bool) -> None:
        try:
            path = Path(raw)
            if not path.is_absolute() or not path.exists():
                return
            resolved = path.resolve()
        except OSError:
            return
        resolved_key = os.path.normcase(str(resolved))
        if (
            access_only
            and resolved_key not in loaded_path_keys
            and _is_reported_package_data_resource(path, resolved)
        ):
            file_id = _file_identity(resolved)
            if file_id is not None and file_id not in loaded_file_ids:
                package_resources.append(str(resolved))
                return
        # Both forms are tested. Package managers hardlink installed files out
        # of a shared cache, so a harness import that lives on the import path
        # can RESOLVE into a cache directory matching no root -- judging the
        # resolved form alone reports the test harness as an outside-tree load.
        # Argument order matters: is_inside_tree(TREE, CANDIDATE).
        candidates = (path, resolved)
        if any(_is_windows_cache_database(candidate) for candidate in candidates):
            unreviewed.append(str(resolved))
            return
        if any(is_inside_tree(root, candidate) for root in roots for candidate in candidates):
            inside.append(str(resolved))
            return
        category = next(
            (c for c in (_classify_os_load(x) for x in candidates) if c is not None), None
        )
        if category == "unreviewed-os-load":
            unreviewed.append(str(resolved))
        elif category is not None:
            inside.append(str(resolved))
        else:
            outside.append(str(resolved))

    for raw in accessed_paths:
        classify(raw, access_only=True)
    for raw in loaded_paths:
        classify(raw, access_only=False)
    return (
        sorted(set(inside)),
        sorted(set(outside)),
        sorted(set(unreviewed)),
        sorted(set(package_resources)),
    )


@dataclass(frozen=True)
class ExternalDependency:
    """A runtime dependency that genuinely cannot be brought inside the tree.

    `spec-packaging-closure`'s halt trigger for the dynamic trace: "loads from
    outside the tree that cannot be brought inside (OS-version-specific media
    DLLs) -> document as explicit OS dependency with version floor,
    owner-visible." This is that documentation, in executable form.

    Being on this list is NOT an excuse -- it is a DECLARATION with a stated
    consequence, and the consequence is the point. An entry here means the
    product has a dependency the installer does not satisfy, which is an owner
    decision, not a packaging detail.
    """

    path_fragment: str
    component: str
    why_not_shippable: str
    consequence_if_absent: str


#: Every outside-tree load must match one of these or the trace FAILS. Adding an
#: entry is a deliberate, reviewable act that states what breaks without it --
#: unlike a bare allowlist, which would silently absorb the next one too.
DECLARED_EXTERNAL_DEPENDENCIES: tuple[ExternalDependency, ...] = (
    ExternalDependency(
        path_fragment="Microsoft.HEVCVideoExtension",
        component="Microsoft HEVC Video Extension (Microsoft Store package)",
        why_not_shippable=(
            "a Store-delivered, Microsoft-licensed media extension; not redistributable "
            "and not part of a base Windows install"
        ),
        consequence_if_absent=(
            "none proven for CivicCast encoding (CC-WS5 retraction, r5 014). The DLL "
            "appears in the trace only because Media Foundation loads every registered "
            "extension while ENUMERATING transforms -- loaded is not used. mfh265enc "
            "binds the GPU DRIVER's HEVC encoder MFT (verified by a real encode: 'HEVC "
            "ENCODE OK -- 60 frames ... via Media Foundation NVIDIA HEVC Encoder MFT'), "
            "which ships with the graphics driver, not the Store. The Store extension "
            "could matter only on hardware exposing no HEVC encoder MFT at all, and the "
            "existing hardware-presence pre-flight already refuses that case with an "
            "operator-facing message. Declared so the enumeration load is accounted for "
            "rather than quietly ignored -- same posture as the VP9 entry below. "
            "Genuinely unmeasured and a HARDWARE question, not a Store one: whether the "
            "AMD iGPU target exposes an HEVC encoder MFT (tracked for the hardware "
            "phase)."
        ),
    ),
    ExternalDependency(
        path_fragment="Microsoft.VP9VideoExtensions",
        component="Microsoft VP9 Video Extensions (Microsoft Store package)",
        why_not_shippable="same posture as the HEVC extension",
        consequence_if_absent=(
            "none for CivicCast -- VP9 is not a codec this product uses. It appears in the "
            "trace only because Media Foundation enumerates every installed extension "
            "while registering encoders. Declared so it is accounted for rather than "
            "quietly ignored."
        ),
    ),
)


#: The only root a declared Store-package dependency may be loaded from.
#: A declaration names a package, not a filename pattern, so it must be anchored
#: to where Windows actually installs packages.
_STORE_PACKAGE_ROOT = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "WindowsApps"


def _match_declared_dependency(path: str) -> ExternalDependency | None:
    """Match a traced path against the declared dependencies -- strictly.

    Deliberately NOT a substring test. A substring test would excuse
    `C:\\anywhere\\Microsoft.HEVCVideoExtension\\evil.dll`, because the fragment
    appears in the string -- which is precisely how a declaration decays into an
    allowlist. Two conditions must both hold:

      1. the file is under the real Store package root, and
      2. some PATH COMPONENT begins with the declared package name

    Windows installs Store packages as
    `WindowsApps\\<Name>_<version>_<arch>__<publisher>`, so a component-prefix
    match on the package name is exact enough to identify the package and strict
    enough that a directory merely containing the name elsewhere does not
    qualify.
    """
    candidate = Path(path)
    if not is_inside_tree(_STORE_PACKAGE_ROOT, candidate):
        return None
    components = [part.lower() for part in candidate.parts]
    for declared in DECLARED_EXTERNAL_DEPENDENCIES:
        needle = declared.path_fragment.lower()
        if any(part == needle or part.startswith(needle + "_") for part in components):
            return declared
    return None


_APPINSTALLER_PACKAGE_COMPONENT = re.compile(
    r"^Microsoft\.DesktopAppInstaller_\d+(?:\.\d+){3}_x64__8wekyb3d8bbwe$",
    re.IGNORECASE,
)
_APPINSTALLER_RESOURCE_INDEX = re.compile(
    r"^S-1-5-21-(?:\d+-){3}\d+-MergedResources-\d+\.pri$",
    re.IGNORECASE,
)
_OBSERVED_PACKAGE_MANIFEST_COMPONENTS = frozenset(
    {
        "microsoft.sechealthui_1000.26100.33438.0_x64__8wekyb3d8bbwe",
        "microsoft.sechealthui_1000.26100.33648.0_x64__8wekyb3d8bbwe",
        "microsoft.windowsfeedbackhub_1.2512.16303.0_x64__8wekyb3d8bbwe",
        "microsoftcorporationii.windowssubsystemforlinux_3.0.3.0_x64__8wekyb3d8bbwe",
    }
)
#: Root ``AppxManifest.xml`` of Microsoft Store packages that update themselves,
#: so the version is not pinned: Windows Photos and the Windows App Runtime 1.7
#: it depends on. Observed as sampled open handles of the probe child on the
#: HALO-gate-a runner (candidate run 43259269511, 2026-11-12), where the six
#: other checks passed. Same rule as the exact identities above: the manifest
#: at the package root only, never a DLL or any other file of the package.
_OBSERVED_PACKAGE_MANIFEST_PATTERNS = (
    re.compile(
        r"^microsoft\.windows\.photos_\d+(?:\.\d+){3}_x64__8wekyb3d8bbwe$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^microsoft\.windowsappruntime\.1\.7_\d+(?:\.\d+){3}_(?:x64|x86)__8wekyb3d8bbwe$",
        re.IGNORECASE,
    ),
)
_OBSERVED_PACKAGE_METADATA_RESOURCE_COMPONENTS = frozenset(
    {
        "microsoft.windowsfeedbackhub_1.2512.16303.0_x64__8wekyb3d8bbwe",
    }
)
_OBSERVED_PACKAGE_METADATA_RESOURCE_INDEX = re.compile(
    r"^resources\.[0-9a-f]+\.pri$",
    re.IGNORECASE,
)
_OBSERVED_WSL_RESOURCE_COMPONENT = re.compile(
    r"^microsoftcorporationii\.windowssubsystemforlinux_\d+(?:\.\d+){3}_x64__8wekyb3d8bbwe$",
    re.IGNORECASE,
)
_OBSERVED_TERMINAL_RESOURCE_COMPONENT = re.compile(
    r"^microsoft\.windowsterminal_\d+(?:\.\d+){3}_x64__8wekyb3d8bbwe$",
    re.IGNORECASE,
)


def _is_reported_package_data_resource(path: Path, resolved: Path) -> bool:
    """Match only the observed package data-resource layouts.

    The original DesktopAppInstaller exception is unchanged: a PRI resource
    index directly under its ``microsoft.system.package.metadata`` directory.
    The additionally observed Microsoft package identities are accepted for
    ``AppxManifest.xml`` at the package root, and the observed
    WindowsFeedbackHub ``microsoft.system.package.metadata`` PRI resource index
    is accepted with its specific package identity. Windows Terminal's root
    resources.pri is also data-only (observed in CI run 39987938776).
    All cases require canonical
    original and resolved paths under the real package root, reject traversal,
    and leave executable/unknown mappings and loaded/hardlinked files strict.
    """
    if not path.is_absolute() or not resolved.is_absolute() or not path.is_file():
        return False
    try:
        canonical = path.resolve(strict=True)
        canonical_resolved = resolved.resolve(strict=True)
        package_root = _STORE_PACKAGE_ROOT.resolve()
        original_relative = path.relative_to(_STORE_PACKAGE_ROOT)
        resolved_relative = canonical.relative_to(package_root)
    except (OSError, RuntimeError, ValueError):
        return False

    def normalized(value: Path) -> str:
        return os.path.normcase(os.path.normpath(str(value)))

    if normalized(path.absolute()) != normalized(canonical):
        return False
    if normalized(canonical_resolved) != normalized(canonical):
        return False
    if normalized(original_relative) != normalized(resolved_relative):
        return False
    if any(part in {".", ".."} for part in original_relative.parts):
        return False
    if len(original_relative.parts) == 3:
        package_name, metadata_directory, filename = original_relative.parts
        if metadata_directory.casefold() != "microsoft.system.package.metadata":
            return False
        if (
            _APPINSTALLER_PACKAGE_COMPONENT.fullmatch(package_name) is not None
            and _APPINSTALLER_RESOURCE_INDEX.fullmatch(filename) is not None
        ):
            return True
        return (
            package_name.casefold() in _OBSERVED_PACKAGE_METADATA_RESOURCE_COMPONENTS
            and _OBSERVED_PACKAGE_METADATA_RESOURCE_INDEX.fullmatch(filename) is not None
        )
    if len(original_relative.parts) == 2:
        package_name, filename = original_relative.parts
        normalized_package = package_name.casefold()
        normalized_filename = filename.casefold()
        return (
            (
                normalized_package in _OBSERVED_PACKAGE_MANIFEST_COMPONENTS
                or any(
                    pattern.fullmatch(package_name) is not None
                    for pattern in _OBSERVED_PACKAGE_MANIFEST_PATTERNS
                )
            )
            and normalized_filename == "appxmanifest.xml"
        ) or (
            (
                _OBSERVED_WSL_RESOURCE_COMPONENT.fullmatch(package_name) is not None
                or _OBSERVED_TERMINAL_RESOURCE_COMPONENT.fullmatch(package_name) is not None
            )
            and normalized_filename == "resources.pri"
        )
    return False


def _dynamic_trace_result(payload: _ChildPayload, tree: Path) -> CheckResult:
    """D2(d): fail on anything the run actually loaded from outside the tree."""
    accessed = payload.get("accessed_paths") or []
    loaded = payload.get("loaded_modules") or []
    mapped_data = payload.get("mapped_data") or []
    unclassified_mappings = payload.get("unclassified_mappings") or []
    sampled = payload.get("sampled_handles") or []
    samples = payload.get("handle_samples") or 0

    if not accessed and not loaded and not mapped_data and not unclassified_mappings:
        return CheckResult(
            name="dynamic_trace",
            status="FAIL",
            detail=(
                "the dynamic trace captured NOTHING, so it proves nothing. An empty "
                "trace means the audit hook never installed or the module "
                "enumeration failed -- it is not evidence of a clean run."
            ),
        )

    if samples == 0:
        return CheckResult(
            name="dynamic_trace",
            status="FAIL",
            detail=(
                "the open-handle sampler took ZERO samples, so the only leg of this "
                "trace that can see native (non-Python) file access never ran. A "
                "sampler that never sampled is indistinguishable from a run in which "
                "native code opened nothing -- and the second reading would be a "
                "false clean bill. Failing rather than reporting the other two legs "
                "as if they were the whole trace."
            ),
        )

    # POSITIVE CONTROL. Sampling successfully is not the same as sampling
    # EFFECTIVELY: round 3 (CC-WS5-PKG-001) produced a PASS, advertising "three
    # mechanisms", on a payload where the third observed nothing at all. A file
    # was therefore held open via CreateFileW, in front of the sampler, for the
    # whole run. If that was missed, the sampler's silence about everything else
    # carries no information.
    canary = payload.get("native_canary") or ""
    canary_seen = any(_file_identity(Path(p)) == _file_identity(Path(canary)) for p in sampled)
    if not canary_seen:
        return CheckResult(
            name="dynamic_trace",
            status="FAIL",
            detail=(
                f"the native-access POSITIVE CONTROL was not observed. A real file "
                f"({canary}) was opened through CreateFileW -- no Python file object "
                f"involved -- and held open for the entire trace, yet the handle "
                f"sampler did not see it across {samples} poll(s). The sampler is "
                "therefore not observing native file access on this run, so its "
                "failure to report anything else proves nothing. Refusing to PASS "
                "on two mechanisms while claiming three."
            ),
        )

    # Sampled handles are traced accesses like any other: a path native code
    # held open is exactly as much a runtime dependency as one Python opened,
    # and folding it in here means it is subject to the SAME inside/outside
    # classification rather than a softer parallel rule.
    strict_mappings = [*loaded, *unclassified_mappings]
    inside, outside, unreviewed, package_resources = _classify_traced_accesses(
        [*accessed, *sampled, *mapped_data], strict_mappings, tree=tree
    )

    undeclared: list[str] = []
    declared_hits: dict[str, ExternalDependency] = {}
    for path in outside:
        declared = _match_declared_dependency(path)
        if declared is None:
            undeclared.append(path)
        else:
            declared_hits[path] = declared

    if undeclared:
        stage_keys = tuple(
            (stage, {_trace_path_key(raw) for raw in paths})
            for stage, paths in payload.get("trace_stages", {}).items()
        )

        def first_stage(path: str) -> str:
            key = _trace_path_key(path)
            return next((stage for stage, keys in stage_keys if key in keys), "not sampled")

        observed_legs = (
            ("Python-mediated access", accessed),
            ("executable mapping", loaded),
            ("unknown-protection mapping", unclassified_mappings),
            ("sampled open handle", sampled),
            ("known non-executable mapping", mapped_data),
        )
        observed_keys = tuple(
            (label, {_trace_path_key(raw) for raw in paths}) for label, paths in observed_legs
        )
        shown = "\n  ".join(
            f"{path} [observed via {', '.join(label for label, keys in observed_keys if _trace_path_key(path) in keys) or 'unattributed trace path'}; first mapped at {first_stage(path)}; child pid={payload.get('trace_pid', 'unknown')}]"
            for path in undeclared[:20]
        )
        more = f"\n  ... and {len(undeclared) - 20} more" if len(undeclared) > 20 else ""
        return CheckResult(
            name="dynamic_trace",
            status="FAIL",
            detail=(
                f"{len(undeclared)} path(s) were observed OUTSIDE the packaged "
                f"tree, outside every permitted root, and are NOT declared external "
                f"dependencies:\n  {shown}{more}"
            ),
        )

    unreviewed_note = ""
    if unreviewed:
        shown = "\n    ".join(unreviewed[:15])
        more = f"\n    ... and {len(unreviewed) - 15} more" if len(unreviewed) > 15 else ""
        unreviewed_note = (
            f"\n  {len(unreviewed)} UNREVIEWED OS/driver load(s). Windows pulls a great deal of "
            "its own machinery into any process (locale tables, D3D core, driver shims). These "
            "are not product dependencies and do not fail the check, but they are NOT in the "
            "reviewed OS inventory either -- listed so an unexpected codec or shim appearing "
            f"here is visible rather than absorbed:\n    {shown}{more}"
        )

    declared_note = ""
    if declared_hits:
        lines = []
        for path, dep in sorted(declared_hits.items()):
            lines.append(f"    {dep.component}")
            lines.append(f"      loaded from : {path}")
            lines.append(f"      not shipped : {dep.why_not_shippable}")
            lines.append(f"      if absent   : {dep.consequence_if_absent}")
        declared_note = (
            f"\n  {len(declared_hits)} DECLARED EXTERNAL DEPENDENCY(IES) were loaded from "
            "outside the tree. These are documented, owner-visible dependencies the "
            "installer does NOT satisfy -- not closure misses, and not resolved:\n"
            + "\n".join(lines)
        )

    package_resource_note = ""
    if package_resources:
        shown = "\n    ".join(package_resources)
        package_resource_note = (
            f"\n  {len(package_resources)} data-only Windows package resource file(s) "
            "were observed outside the payload. This narrow reported-data exception "
            "covers only observed DesktopAppInstaller package resource indexes (PRI), "
            "the observed WindowsFeedbackHub microsoft.system.package.metadata PRI "
            "index, observed SecHealthUI/WindowsFeedbackHub/WSL/Windows Photos/"
            "Windows App Runtime 1.7 root AppxManifest.xml "
            "manifests, and the observed WSL resources.pri and Windows Terminal "
            "resources.pri at their package roots. It "
            "does not apply to executable or unknown-protection mappings, DLLs, or "
            "any other WindowsApps path:\n    " + shown
        )

    return CheckResult(
        name="dynamic_trace",
        status="PASS",
        detail=(
            f"{len(inside)} traced path(s) inside the tree or a permitted root, via "
            f"the audit hook, mapped-file scan, and sampled open handles: "
            f"{len(accessed)} Python-mediated opens (incl. ctypes.dlopen), "
            f"{len(loaded)} executable mappings, {len(mapped_data)} known non-executable "
            f"data mappings, {len(unclassified_mappings)} unknown-protection mappings, "
            f"and {len(sampled)} file handle(s) caught open by "
            f"{samples} poll(s) of the process handle table -- the last of which is "
            "what observes native code opening files without going through Python. "
            "REMAINING BLIND SPOT, stated rather than glossed: handle polling is "
            "SAMPLED, so a file opened and closed entirely between two polls is not "
            "seen, and this covers only this process (a file opened by a spawned "
            "child would be missed). Closing both needs kernel ETW file-I/O tracing, "
            "which requires administrator rights."
            + unreviewed_note
            + declared_note
            + package_resource_note
        ),
    )


def _replace_result(results: list[CheckResult], replacement: CheckResult) -> list[CheckResult]:
    """Replace the result with the same name, by NAME rather than by position.

    The callers used to do `results[-1] = gpl_result`, which silently depended
    on the GPL entry being last. Appending `dynamic_trace` to the failure set
    broke that assumption in four places at once: `[-1]` then overwrote the
    TRACE, so the report lost a check and gained a duplicate GPL entry.

    The existing test could not see it, because it keys results by name and a
    duplicate name collapses into one key -- five entries still looked like
    four. Positional mutation of a list whose shape can change is the bug;
    replacing by name removes the class, not just the instance.
    """
    replaced = [replacement if r.name == replacement.name else r for r in results]
    if not any(r.name == replacement.name for r in results):
        replaced.append(replacement)
    return replaced


def _failure_quartet(detail: str) -> list[CheckResult]:
    """The four hostile-child-derived checks, all FAILed with the same
    ``detail`` -- used when the child process itself could not be trusted
    (crashed, timed out, or emitted unparseable output). Ambiguity about
    whether GStreamer's closure holds is never reported as a pass."""

    return [
        CheckResult("factory_sweep", "FAIL", detail),
        CheckResult("plugin_origin_check", "FAIL", detail),
        CheckResult("caption_leg", "FAIL", detail),
        _gpl_negative_control_result((), None, detail),
        # dynamic_trace belongs here too. Without it the report silently drops
        # from six checks to five whenever the child fails, and a reader
        # counting "everything reported passed" would not notice one had gone
        # missing. A check that DISAPPEARS is the same family of problem as one
        # that falsely passes -- the run is already red, but the report must
        # still account for every check by name.
        CheckResult("dynamic_trace", "FAIL", detail),
    ]


def _interpret_child_output(
    child: subprocess.CompletedProcess[str], *, present_gpl_files: tuple[str, ...], tree: Path
) -> list[CheckResult]:
    if child.returncode != 0:
        detail = (
            f"child process exited {child.returncode}\n"
            f"stdout:\n{child.stdout}\nstderr:\n{child.stderr}"
        )
        results = _failure_quartet(detail)
        # The file half of the GPL control is independent of the child and
        # already known -- fold it in even though the factory half failed.
        return _replace_result(
            results, _gpl_negative_control_result(present_gpl_files, None, detail)
        )

    try:
        last_line = child.stdout.strip().splitlines()[-1]
        payload = cast(_ChildPayload, json.loads(last_line))
    except (json.JSONDecodeError, IndexError) as exc:
        detail = (
            f"could not parse child JSON output ({type(exc).__name__}: {exc})\n"
            f"stdout:\n{child.stdout}\nstderr:\n{child.stderr}"
        )
        results = _failure_quartet(detail)
        return _replace_result(
            results, _gpl_negative_control_result(present_gpl_files, None, detail)
        )

    if not payload["gst_init_ok"]:
        # GStreamer never initialized: the four per-check fields above carry
        # empty placeholder data (nothing was actually checked), which must
        # never be read as "checked and clean". Go straight to a FAIL
        # quartet instead of interpreting that emptiness below.
        detail = payload["gst_init_detail"]
        results = _failure_quartet(detail)
        return _replace_result(
            results, _gpl_negative_control_result(present_gpl_files, None, detail)
        )

    final_results: list[CheckResult] = []

    missing = payload["factory_sweep"]["missing"]
    plugin_file_missing = frozenset(find_missing_required_plugin_files(tree, missing))
    hardware_gated, genuine = classify_missing_factories(
        missing, plugin_file_missing=plugin_file_missing
    )
    final_results.append(
        CheckResult(
            name="factory_sweep",
            status="FAIL" if genuine else "PASS",
            detail=(
                f"checked {payload['factory_sweep']['checked_count']} required factories. "
                f"genuine misses: {list(genuine) or 'none'}. "
                f"hardware-gated (expected absent without matching GPU/MFT, plugin file "
                f"confirmed present): {list(hardware_gated) or 'none'}."
                + (
                    f" plugin file entirely absent from tree (never excusable): "
                    f"{sorted(plugin_file_missing)}."
                    if plugin_file_missing
                    else ""
                )
            ),
        )
    )

    outside_tree = payload["plugin_origin"]["outside_tree"]
    final_results.append(
        CheckResult(
            name="plugin_origin_check",
            status="FAIL" if outside_tree else "PASS",
            detail=(
                payload["plugin_origin"]["detail"]
                + (f"; outside-tree plugins: {outside_tree}" if outside_tree else "")
            ),
        )
    )

    caption = payload["caption_leg"]
    final_results.append(
        CheckResult(
            name="caption_leg",
            status="PASS" if caption["ok"] else "FAIL",
            detail=caption["detail"],
        )
    )

    present_factories = payload["gpl_factories"]["present"]
    final_results.append(
        _gpl_negative_control_result(
            present_gpl_files, present_factories, payload["gpl_factories"]["detail"]
        )
    )

    final_results.append(_dynamic_trace_result(payload, tree))

    return final_results


def _run_verification(
    tree: Path,
    *,
    json_path: Path | None,
    caption_probe_text: str = _CAPTION_PROBE_TEXT,
) -> int:
    if not tree.is_dir():
        print(f"FATAL: --tree {tree} is not a directory", file=sys.stderr)
        return 2

    present_gpl_files = find_gpl_plugin_files(tree, GPL_PLUGIN_FILENAMES)

    results: list[CheckResult] = [
        check_manifest_verification(tree),
        check_cli_consumer_verification(tree),
    ]

    with tempfile.TemporaryDirectory(prefix="civiccast-closure-verify-") as tmp:
        registry_path = Path(tmp) / "gstreamer-registry.bin"
        hostile_env = build_hostile_environment(
            tree, base_env=dict(os.environ), registry_path=registry_path
        )
        try:
            child = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--tree",
                    str(tree),
                    "--caption-probe-text",
                    caption_probe_text,
                    _CHILD_FLAG,
                ],
                env=hostile_env,
                capture_output=True,
                text=True,
                timeout=_CHILD_PROCESS_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            detail = f"child process timed out after {_CHILD_PROCESS_TIMEOUT_SECONDS}s: {exc}"
            quartet = _failure_quartet(detail)
            quartet = _replace_result(
                quartet, _gpl_negative_control_result(present_gpl_files, None, detail)
            )
            results.extend(quartet)
        else:
            results.extend(
                _interpret_child_output(child, present_gpl_files=present_gpl_files, tree=tree)
            )

    _print_summary(results)
    if json_path is not None:
        json_path.write_text(
            json.dumps(
                [{"name": r.name, "status": r.status, "detail": r.detail} for r in results],
                indent=2,
            ),
            encoding="utf-8",
        )
    return aggregate_exit_code(results)


def _print_summary(results: Sequence[CheckResult]) -> None:
    print()
    print("=" * 78)
    print(f"{'CHECK':<24}{'STATUS':<10}DETAIL")
    print("-" * 78)
    for result in results:
        lines = result.detail.splitlines() if result.detail else [""]
        print(f"{result.name:<24}{result.status:<10}{lines[0]}")
        # A FAIL's continuation lines carry the offending PATHS. Printing only
        # the first line meant a failure said "1 path was loaded from outside
        # the tree" and then never said which -- a verdict with the evidence
        # deleted, and unactionable at exactly the moment it matters most.
        # PASS rows stay one line: their continuations are advisory context and
        # would bury the table.
        if result.status == "FAIL":
            for line in lines[1:]:
                print(f"{'':<34}{line}")
    print("=" * 78)
    passed = sum(1 for r in results if r.status == "PASS")
    failed = sum(1 for r in results if r.status == "FAIL")
    skipped = sum(1 for r in results if r.status == "SKIPPED")
    print(f"{passed} PASS, {failed} FAIL, {skipped} SKIPPED")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_native_runtime_closure",
        description=(
            "D6 acceptance verification suite (AC2): proves a BUILT packaged "
            "tree is self-sufficient."
        ),
    )
    parser.add_argument("--tree", required=True, type=Path, help="path to the packaged output tree")
    parser.add_argument(
        "--json", type=Path, default=None, help="optional path to write a machine-readable report"
    )
    parser.add_argument(
        "--caption-probe-text",
        default=_CAPTION_PROBE_TEXT,
        help=(
            "exact single-line caption text to embed and require during decode-back "
            "(defaults to the closure canary)"
        ),
    )
    parser.add_argument(_CHILD_FLAG, action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_arg_parser().parse_args(argv)
    tree: Path = args.tree.resolve()
    caption_probe_text = " ".join(args.caption_probe_text.split())
    if not caption_probe_text:
        print("FATAL: --caption-probe-text must contain visible text", file=sys.stderr)
        return 2
    if len(caption_probe_text) > 512:
        print("FATAL: --caption-probe-text exceeds 512 characters", file=sys.stderr)
        return 2

    if args.internal_child_runner:
        payload = _child_main(tree, caption_probe_text=caption_probe_text)
        print(json.dumps(payload))
        return 0

    return _run_verification(
        tree,
        json_path=args.json,
        caption_probe_text=caption_probe_text,
    )


if __name__ == "__main__":
    raise SystemExit(main())
