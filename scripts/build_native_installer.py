#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Build the CivicCast (Native) unsigned installer `.exe` with the verified
runtime closure embedded (WP-4 Part B — the D2 build-time half).

This is the reproducible path from the hash-pinned wheel set to a native NSIS
installer whose bundle carries the audited runtime tree. It does NOT sign
(signing is a later WP; the `.exe` is unsigned here by design) and it never
commits the 75 MB payload — the staging directory is git-ignored, and this
script is the way to (re)produce it.

Pipeline:

  1. **Build the closure.** Reuse `scripts.build_native_runtime_closure.build`
     verbatim — stage the pinned wheels (`requirements-native-runtime.txt`),
     walk the real PE import closure, copy the SHARED-CONTRACT tree, bundle the
     license notices + texts, and emit the trust artifacts
     (`runtime-manifest.json`, `SHA256SUMS`, `LICENSE-BOM.md`).
  2. **Verify the freshly built tree against its manifest — byte for byte,
     fail loud.** Reuse `scripts.verify_native_runtime_closure.check_manifest_verification`,
     which independently re-hashes every on-disk file and diffs it against
     `runtime-manifest.json`, and also proves `SHA256SUMS` / `LICENSE-BOM.md`
     match what the manifest implies. A FAIL aborts before anything is staged.
  3. **Stage the verified tree into the native installer's bundle resources.**
     Clean-copy the whole tree (trust artifacts included) into
     `<installer>/src-tauri/native-runtime/`, which the native Tauri config
     binds as `bundle.resources` so it lands INSIDE the bundle per D2.
  4. **Re-verify the STAGED copy against the manifest — fail loud.** This is
     the D2 assurance that the bytes about to be embedded are the audited bytes
     (the manifest's own integrity, once signed, chains to Authenticode).
  5. **`tauri build --config src-tauri/tauri.native.conf.json`** (unsigned) to
     produce the native NSIS setup `.exe`.

Each stage is individually skippable (`--skip-closure-build` to reuse a tree,
`--stage-only` to stop before the Tauri build) so the heavy Rust/NSIS build can
be run — or its failure reported — independently of the closure build.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.build_native_app_payload import build as build_app_payload  # noqa: E402
from scripts.build_native_runtime_closure import build as build_closure  # noqa: E402
from scripts.verify_native_app_payload import (  # noqa: E402
    check_app_payload_verification,
)
from scripts.verify_native_runtime_closure import (  # noqa: E402
    check_manifest_verification,
)

#: The native installer app + its src-tauri config directory.
INSTALLER_DIR = ROOT / "civiccast" / "apps" / "installer"
SRC_TAURI = INSTALLER_DIR / "src-tauri"
NATIVE_TAURI_CONFIG = SRC_TAURI / "tauri.native.conf.json"

#: Where the verified MEDIA closure tree is staged so the native Tauri bundle
#: embeds it (bound by `tauri.native.conf.json`'s `bundle.resources`). Its OWN
#: directory, NOT the shared `src-tauri/resources/` the WSL product bundles, so
#: the two products never drag each other's payload in. Git-ignored: 75 MB is
#: never committed; this script is the reproducible path to it. Lands at
#: `$INSTDIR\native-runtime\` (Tauri preserves the resource glob's directory).
STAGING_DIR = SRC_TAURI / "native-runtime"

#: Where the verified APPLICATION payload (CPython 3.12 + civiccast + deps) is
#: staged. Named `runtime` so Tauri lays it at `$INSTDIR\runtime\` — exactly the
#: path the NSIS POSTINSTALL gate checks for `python.exe` and where it invokes
#: the D3 engine (`nsis-hooks-native.nsh`). Git-ignored: ~144 MB, never committed.
APP_STAGING_DIR = SRC_TAURI / "runtime"

#: Default location for the freshly built closure tree (outside the installer so
#: a failed Tauri build never leaves a half-tree in the bundle path).
DEFAULT_TREE_OUT = ROOT / "build" / "native-runtime-tree"

#: Default location for the freshly built application-payload tree.
DEFAULT_APP_TREE_OUT = ROOT / "build" / "native-app-tree"


def _fail(message: str) -> NoReturn:
    raise SystemExit(f"build_native_installer: {message}")


def build_and_verify_tree(tree_out: Path, *, skip_build: bool) -> None:
    """Stage 1 + 2: build the closure into ``tree_out`` and verify it against
    its own manifest byte-for-byte (fail loud on any mismatch)."""

    if skip_build:
        if not (tree_out / "runtime-manifest.json").is_file():
            _fail(
                f"--skip-closure-build set but no built tree at {tree_out} (no runtime-manifest.json)"
            )
        print(f"[1/7] Reusing existing closure tree at {tree_out}")
    else:
        if tree_out.exists() and any(tree_out.iterdir()):
            print(f"[1/7] Clearing prior tree at {tree_out}")
            shutil.rmtree(tree_out)
        tree_out.parent.mkdir(parents=True, exist_ok=True)
        print(f"[1/7] Building the native runtime closure into {tree_out} ...")
        build_closure(stage=Path(f"{tree_out}-stage"), out=tree_out)

    print(
        "[2/7] Verifying the freshly built tree against runtime-manifest.json (byte-for-byte) ..."
    )
    result = check_manifest_verification(tree_out)
    print(f"      manifest_verification: {result.status} - {result.detail}")
    if result.status != "PASS":
        _fail(f"tree verification did not PASS ({result.status}): {result.detail}")


def build_and_verify_app_tree(app_tree_out: Path, *, skip_build: bool) -> None:
    """Build the application payload (CPython 3.12 + civiccast + hash-pinned
    deps) into ``app_tree_out`` and verify it against its own manifest.

    This is the WP-6 half that makes the install BOOTABLE (resolves the WP-5
    finding): without it the installer embeds a media runtime but no interpreter
    or app, so the D3 engine cannot run and the NSIS gate fails loud."""

    if skip_build:
        if not (app_tree_out / "app-payload-manifest.json").is_file():
            _fail(
                f"--skip-app-build set but no built app payload at {app_tree_out} "
                "(no app-payload-manifest.json)"
            )
        print(f"[3/7] Reusing existing app payload tree at {app_tree_out}")
    else:
        if app_tree_out.exists() and any(app_tree_out.iterdir()):
            print(f"[3/7] Clearing prior app payload tree at {app_tree_out}")
            shutil.rmtree(app_tree_out)
        app_tree_out.parent.mkdir(parents=True, exist_ok=True)
        print(f"[3/7] Building the application payload into {app_tree_out} ...")
        build_app_payload(
            out=app_tree_out,
            interpreter_zip=(
                ROOT
                / "build"
                / "native-app-cache"
                / "python-3.12.16-expat-2.10.0-civiccast-embed-amd64.zip"
            ),
            scratch=Path(f"{app_tree_out}-scratch"),
        )

    print("[4/7] Verifying the freshly built app payload against its manifest ...")
    result = check_app_payload_verification(app_tree_out)
    print(f"      app_payload_verification: {result.status} - {result.detail[:120]}")
    if result.status != "PASS":
        _fail(f"app payload verification did not PASS ({result.status}): {result.detail}")


def stage_and_verify_payload(tree_out: Path) -> None:
    """Stage 5a: clean-copy the verified MEDIA tree into the native bundle
    resources dir and re-verify the STAGED copy against the manifest (D2)."""

    print(f"[5/7] Staging the verified media closure into {STAGING_DIR} ...")
    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    shutil.copytree(tree_out, STAGING_DIR)

    print("      Re-verifying the STAGED media payload against runtime-manifest.json (D2) ...")
    result = check_manifest_verification(STAGING_DIR)
    print(f"      staged manifest_verification: {result.status} - {result.detail}")
    if result.status != "PASS":
        _fail(f"staged media payload verification did not PASS ({result.status}): {result.detail}")


def stage_and_verify_app_payload(app_tree_out: Path) -> None:
    """Stage 5b: clean-copy the verified APP payload into `src-tauri/runtime/`
    (lands at `$INSTDIR\\runtime\\`) and re-verify the STAGED copy (D2)."""

    print(f"[6/7] Staging the verified app payload into {APP_STAGING_DIR} ...")
    if APP_STAGING_DIR.exists():
        shutil.rmtree(APP_STAGING_DIR)
    # Ignore __pycache__ defensively: a smoke test or manual run against the
    # source tree can leave runtime .pyc caches that are not in the manifest;
    # they regenerate at runtime and must never reach the shipped bundle.
    shutil.copytree(app_tree_out, APP_STAGING_DIR, ignore=shutil.ignore_patterns("__pycache__"))

    print("      Re-verifying the STAGED app payload against app-payload-manifest.json (D2) ...")
    result = check_app_payload_verification(APP_STAGING_DIR)
    print(f"      staged app_payload_verification: {result.status} - {result.detail[:120]}")
    if result.status != "PASS":
        _fail(f"staged app payload verification did not PASS ({result.status}): {result.detail}")


def run_tauri_build() -> None:
    """Stage 5: run the unsigned native Tauri/NSIS build.

    Invokes the Tauri CLI directly with the native config overlay (NOT the
    `npm run tauri:build` script) because `tauri.native.conf.json` stages the
    audited native runtime tree into `bundle.resources` itself, per D2 -- see
    this module's docstring. No signing is configured, so the emitted `.exe`
    is unsigned by design for this work package."""

    rel_config = NATIVE_TAURI_CONFIG.relative_to(SRC_TAURI).as_posix()
    npx = shutil.which("npx")
    if npx is None:
        _fail(
            "npx not found on PATH; the Tauri CLI (@tauri-apps/cli) is an installer devDependency — run `npm ci` in the installer app first"
        )
    cmd = [npx, "tauri", "build", "--config", f"src-tauri/{rel_config}"]
    print(f"[7/7] Running: {' '.join(cmd)}  (cwd={INSTALLER_DIR})")
    completed = subprocess.run(cmd, cwd=INSTALLER_DIR)
    if completed.returncode != 0:
        _fail(f"tauri build failed with exit code {completed.returncode}")
    print("[7/7] Tauri build complete.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build the CivicCast (Native) unsigned installer .exe with the verified runtime payload embedded."
    )
    parser.add_argument(
        "--tree-out",
        type=Path,
        default=DEFAULT_TREE_OUT,
        help=f"where to build the closure tree (default: {DEFAULT_TREE_OUT})",
    )
    parser.add_argument(
        "--skip-closure-build",
        action="store_true",
        help="reuse an already-built tree at --tree-out (verify + stage + build only)",
    )
    parser.add_argument(
        "--app-tree-out",
        type=Path,
        default=DEFAULT_APP_TREE_OUT,
        help=f"where to build the application payload (default: {DEFAULT_APP_TREE_OUT})",
    )
    parser.add_argument(
        "--skip-app-build",
        action="store_true",
        help="reuse an already-built app payload at --app-tree-out",
    )
    parser.add_argument(
        "--stage-only",
        action="store_true",
        help="stop after staging + verifying both payloads; do NOT run the Tauri build",
    )
    args = parser.parse_args(argv)

    tree_out = args.tree_out.resolve()
    app_tree_out = args.app_tree_out.resolve()
    # Build + verify BOTH payloads before staging either, so a failure in one
    # never leaves a half-staged bundle.
    build_and_verify_tree(tree_out, skip_build=args.skip_closure_build)
    build_and_verify_app_tree(app_tree_out, skip_build=args.skip_app_build)
    stage_and_verify_payload(tree_out)
    stage_and_verify_app_payload(app_tree_out)

    if args.stage_only:
        print("--stage-only set: both verified payloads are staged; skipping the Tauri build.")
        return 0

    run_tauri_build()
    print(
        "Done. The unsigned CivicCast (Native) setup .exe is under "
        f"{SRC_TAURI / 'target' / 'release' / 'bundle' / 'nsis'}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
