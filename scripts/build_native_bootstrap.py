#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Build and size-gate the small signed-pack CivicCast native bootstrap."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

from civiccast._native_version import __version__

ROOT = Path(__file__).resolve().parent.parent
INSTALLER_DIR = ROOT / "civiccast" / "apps" / "installer"
SRC_TAURI = INSTALLER_DIR / "src-tauri"
NATIVE_CONFIG = SRC_TAURI / "tauri.native.conf.json"
VC_REDIST_RESOURCE = SRC_TAURI / "resources" / "vc_redist.x64.exe"
VC_REDIST_EXPECTED_BYTES = 25_635_768
VC_REDIST_EXPECTED_SHA256 = "cc0ff0eb1dc3f5188ae6300faef32bf5beeba4bdd6e8e445a9184072096b713b"
# Tauri's `offlineInstaller` mode has no local-path option: it resolves the
# Microsoft fwlink with a HEAD request to learn the CDN GUID/filename, then
# downloads the runtime unless that exact cache entry already exists
# (tauri-bundler's `download_webview2_offline_installer`). The unpinned fwlink
# silently returns a different build over time (294,099,168 bytes on
# 2026-11-20 vs the 215,195,856 bytes rc.7 shipped), which pushed setup.exe
# past the 300 MB gate. So pin the bytes here, seed Tauri's cache entry
# before the build, and fail if the bundler would have downloaded instead.
WEBVIEW2_INSTALLER_FILENAME = "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"
WEBVIEW2_OFFLINE_INSTALLER_URL = "https://go.microsoft.com/fwlink/?linkid=2124701"
WEBVIEW2_URL_PREFIX = "https://msedge.sf.dl.delivery.mp.microsoft.com/filestreamingservice/files/"
WEBVIEW2_OFFLINE_INSTALLER_EXPECTED_BYTES = 215_195_856
WEBVIEW2_OFFLINE_INSTALLER_EXPECTED_SHA256 = (
    "1d1c838e5971343c060415da7c630c3c1ca7ad33fbb61cca660f3c1122f4cca8"
)
# The station resources the installer embeds so a DOWNLOAD-ONLY install or
# upgrade of setup.exe alone carries the signed station index, placeholder
# core pack, and mandatory local Whistle model/engine needed by activation.
# They are produced by `build-native-station-bundle` and staged by the
# workflow; this script never fabricates them, it proves their identity.
STATION_RESOURCE_DIR = SRC_TAURI / "resources" / "station"
STATION_INDEX_RESOURCE = STATION_RESOURCE_DIR / "station-index.json"
STATION_CORE_PACK_RESOURCE = STATION_RESOURCE_DIR / "core.ccpack"
STATION_WHISTLE_PACK_RESOURCE = STATION_RESOURCE_DIR / "captions-whistle.ccpack"
# Bound the locally required Whistle pack while keeping the larger caption and
# Ollama model packs out of setup.exe. The pinned model and DLL total 18.4 MB;
# 25 MB leaves room for their signed ZIP metadata without admitting a full
# model-bundle artifact.
STATION_EMBEDDED_RESOURCE_LIMIT_EXCLUSIVE = 25_000_000
BOOTSTRAP_RESOURCES = {
    "resources/vc_redist.x64.exe": "vc_redist.x64.exe",
    "resources/station/station-index.json": "station/station-index.json",
    "resources/station/core.ccpack": "station/core.ccpack",
    "resources/station/captions-whistle.ccpack": "station/captions-whistle.ccpack",
}
# Tauri names the generated NSIS installer "<productName>_<version>_x64-setup.exe"
# from the EFFECTIVE merged native config. productName ("CivicCast (Native)") is
# stable across releases and stays a literal here; the version segment is NOT --
# chain J (2026-08-02) found this was a hardcoded "1.0.0-rc15" that would have
# silently gone stale (and made this script unable to find its own build output)
# on every future version bump. Sourced from civiccast._native_version, the
# same place tauri.native.conf.json's own "version" field is required to match
# (scripts/policy/check_release_identity.py) -- deliberately NOT
# civiccast._version, which is the separate WSL product line's own identity.
SETUP_ARTIFACT = (
    SRC_TAURI
    / "target"
    / "release"
    / "bundle"
    / "nsis"
    / f"CivicCast (Native)_{__version__}_x64-setup.exe"
)
MAIN_BINARY = SRC_TAURI / "target" / "release" / "CivicCast Native.exe"
GENERATED_NSIS_DIR = SRC_TAURI / "target" / "release" / "nsis" / "x64"
GENERATED_NSIS_SCRIPT = GENERATED_NSIS_DIR / "installer.nsi"
GENERATED_NSIS_OUTPUT = GENERATED_NSIS_DIR / "nsis-output.exe"
BOOTSTRAP_SIZE_LIMIT_EXCLUSIVE = 300_000_000
SOURCE_DATE_EPOCH = 1_704_067_200
TAURI_UNKNOWN_BUNDLE_MARKER = b"__TAURI_BUNDLE_TYPE_VAR_UNK"
TAURI_NSIS_BUNDLE_MARKER = b"__TAURI_BUNDLE_TYPE_VAR_NSS"


def enforce_bootstrap_size(
    observed_bytes: int,
    *,
    limit_exclusive: int = BOOTSTRAP_SIZE_LIMIT_EXCLUSIVE,
) -> int:
    """Return headroom or fail when the bootstrap reaches the strict limit."""

    if observed_bytes <= 0:
        raise ValueError("bootstrap byte length must be positive")
    if observed_bytes >= limit_exclusive:
        raise ValueError(
            "native bootstrap size gate failed: "
            f"{observed_bytes} bytes is not smaller than {limit_exclusive}"
        )
    return limit_exclusive - observed_bytes


def validate_pack_public_key(encoded: str) -> bytes:
    """Require one raw Ed25519 public key for the compiled trust root."""

    try:
        decoded = base64.b64decode(encoded, validate=True)
    except ValueError as exc:
        raise ValueError("pack public key must be canonical base64") from exc
    if len(decoded) != 32:
        raise ValueError("pack public key must decode to exactly 32 Ed25519 bytes")
    return decoded


def validate_native_bootstrap_config(path: Path = NATIVE_CONFIG) -> None:
    """Prove the native overlay embeds no multi-gigabyte station payload."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    bundle = payload.get("bundle")
    if not isinstance(bundle, dict) or bundle.get("resources") != BOOTSTRAP_RESOURCES:
        raise ValueError(
            "native bootstrap resources must contain the pinned VC++ runtime "
            "prerequisite and the reviewed station index/core/Whistle resources"
        )
    windows = bundle.get("windows")
    nsis = windows.get("nsis") if isinstance(windows, dict) else None
    if not isinstance(nsis, dict) or nsis.get("installerHooks") != ("nsis-hooks-bootstrap.nsh"):
        raise ValueError("native bootstrap must use the pack-only NSIS hooks")
    webview = windows.get("webviewInstallMode") if isinstance(windows, dict) else None
    if not isinstance(webview, dict) or webview != {
        "type": "offlineInstaller",
        "silent": True,
    }:
        raise ValueError(
            "native bootstrap must embed the silent offline WebView2 installer; "
            "air-gapped setup must not depend on a download"
        )


def validate_embedded_station_resources(
    *,
    index_path: Path = STATION_INDEX_RESOURCE,
    core_pack_path: Path = STATION_CORE_PACK_RESOURCE,
    whistle_pack_path: Path | None = None,
    product_version: str = __version__,
    size_limit_exclusive: int = STATION_EMBEDDED_RESOURCE_LIMIT_EXCLUSIVE,
) -> dict[str, object]:
    """Fail closed unless the index, core, and required Whistle pack match.

    The activation CLI verifies the signed index's ``product_version`` and
    ``compatible_core`` against ``main.rs``'s ``CIVICCAST_VERSION`` (see
    ``run_native_flat_activation_cli``, which passes that constant for both),
    and ``scripts/policy/check_release_identity.py`` already binds that
    constant to ``civiccast._native_version.__version__``. So an index built
    for a different product version would install fine and then fail
    activation on every machine -- exactly the fail-late shape this gate
    turns into a build-time failure.

    Returns the verified manifest identity for the build report.
    """

    if whistle_pack_path is None:
        whistle_pack_path = index_path.parent / "captions-whistle.ccpack"
    for required in (index_path, core_pack_path, whistle_pack_path):
        if not required.is_file():
            raise ValueError(
                "embedded station resource is missing: "
                f"{required}. The station index, core pack, and Whistle pack are produced by "
                "the build-native-station-bundle job and must be staged under "
                f"{STATION_RESOURCE_DIR} before the Tauri build runs."
            )
        observed = required.stat().st_size
        if observed <= 0:
            raise ValueError(f"embedded station resource is empty: {required}")
        if observed >= size_limit_exclusive:
            raise ValueError(
                "embedded station resource is too large: "
                f"{required} is {observed} bytes, which is not smaller than "
                f"{size_limit_exclusive}; station payload belongs in signed packs"
            )

    envelope = json.loads(index_path.read_text(encoding="utf-8"))
    manifest = envelope.get("manifest") if isinstance(envelope, dict) else None
    if not isinstance(manifest, dict):
        raise ValueError(f"embedded station index is not a signed envelope: {index_path}")
    if not isinstance(envelope.get("signature"), str) or not envelope["signature"]:
        raise ValueError(f"embedded station index carries no signature: {index_path}")
    if manifest.get("kind") != "station-index":
        raise ValueError(f"embedded station index is not a station-index: {manifest.get('kind')!r}")
    for field in ("product_version", "compatible_core"):
        observed_version = manifest.get(field)
        if observed_version != product_version:
            raise ValueError(
                f"embedded station index {field} {observed_version!r} does not match the "
                f"native product version {product_version!r} the activation CLI verifies "
                "against; rebuild the station bundle at this product version"
            )

    packs = manifest.get("packs")
    if not isinstance(packs, list) or not packs:
        raise ValueError(f"embedded station index names no component packs: {index_path}")
    core = next(
        (entry for entry in packs if isinstance(entry, dict) and entry.get("component") == "core"),
        None,
    )
    if core is None:
        raise ValueError(f"embedded station index names no `core` component: {index_path}")
    if core.get("filename") != core_pack_path.name:
        raise ValueError(
            f"embedded station index names core pack {core.get('filename')!r}, but the "
            f"embedded core pack is {core_pack_path.name!r}"
        )
    observed_core_bytes = core_pack_path.stat().st_size
    if core.get("bytes") != observed_core_bytes:
        raise ValueError(
            f"embedded core pack is {observed_core_bytes} bytes, but the signed index "
            f"declares {core.get('bytes')!r}"
        )
    observed_core_sha256 = _sha256(core_pack_path)
    if core.get("sha256") != observed_core_sha256:
        raise ValueError(
            f"embedded core pack SHA-256 {observed_core_sha256} does not match the signed "
            f"index entry {core.get('sha256')!r}"
        )

    whistle_entries = [
        entry
        for entry in packs
        if isinstance(entry, dict) and entry.get("component") == "captions-whistle"
    ]
    if len(whistle_entries) != 1:
        raise ValueError(
            "embedded station index must name exactly one required captions-whistle pack"
        )
    whistle = whistle_entries[0]
    if (
        whistle.get("filename") != whistle_pack_path.name
        or whistle.get("required") is not True
        or whistle.get("urls") != []
    ):
        raise ValueError(
            "embedded station index must require the embedded captions-whistle.ccpack "
            "local sidecar and must not carry URLs"
        )
    observed_whistle_bytes = whistle_pack_path.stat().st_size
    if whistle.get("bytes") != observed_whistle_bytes:
        raise ValueError(
            f"embedded Whistle pack is {observed_whistle_bytes} bytes, but the signed index "
            f"declares {whistle.get('bytes')!r}"
        )
    observed_whistle_sha256 = _sha256(whistle_pack_path)
    if whistle.get("sha256") != observed_whistle_sha256:
        raise ValueError(
            f"embedded Whistle pack SHA-256 {observed_whistle_sha256} does not match the "
            f"signed index entry {whistle.get('sha256')!r}"
        )

    return {
        "product_version": manifest["product_version"],
        "compatible_core": manifest["compatible_core"],
        "channel": manifest.get("channel"),
        "signing_key_id": manifest.get("signing_key_id"),
        "index_sha256": _sha256(index_path),
        "index_bytes": index_path.stat().st_size,
        "core_pack_sha256": observed_core_sha256,
        "core_pack_bytes": observed_core_bytes,
        "whistle_pack_sha256": observed_whistle_sha256,
        "whistle_pack_bytes": observed_whistle_bytes,
        "component_count": len(packs),
    }


def require_allowed_signing_key(key_id: str, *, allow_development_key: bool) -> None:
    if not key_id.strip():
        raise ValueError("pack signing key id must not be empty")
    if key_id.startswith("development-") and not allow_development_key:
        raise ValueError(
            "development trust roots require --allow-development-key; "
            "release builds require Scott-approved production key custody"
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_vc_redist(
    path: Path,
    *,
    expected_bytes: int = VC_REDIST_EXPECTED_BYTES,
    expected_sha256: str = VC_REDIST_EXPECTED_SHA256,
) -> Path:
    """Require the reviewed Microsoft x64 runtime prerequisite exactly."""

    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"VC++ redistributable is not a regular file: {resolved}")
    observed_bytes = resolved.stat().st_size
    if observed_bytes != expected_bytes:
        raise ValueError(
            "VC++ redistributable byte length mismatch: "
            f"expected {expected_bytes}, observed {observed_bytes}"
        )
    observed_sha256 = _sha256(resolved)
    if observed_sha256 != expected_sha256:
        raise ValueError(
            "VC++ redistributable SHA-256 mismatch: "
            f"expected {expected_sha256}, observed {observed_sha256}"
        )
    return resolved


def validate_webview2_offline_installer(
    path: Path,
    *,
    expected_bytes: int | None = None,
    expected_sha256: str | None = None,
) -> Path:
    """Require the reviewed silent WebView2 offline installer exactly."""

    if expected_bytes is None:
        expected_bytes = WEBVIEW2_OFFLINE_INSTALLER_EXPECTED_BYTES
    if expected_sha256 is None:
        expected_sha256 = WEBVIEW2_OFFLINE_INSTALLER_EXPECTED_SHA256
    resolved = path.expanduser().resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"WebView2 offline installer is not a regular file: {resolved}")
    observed_bytes = resolved.stat().st_size
    if observed_bytes != expected_bytes:
        raise ValueError(
            "WebView2 offline installer byte length mismatch: "
            f"expected {expected_bytes}, observed {observed_bytes}"
        )
    observed_sha256 = _sha256(resolved)
    if observed_sha256 != expected_sha256:
        raise ValueError(
            "WebView2 offline installer SHA-256 mismatch: "
            f"expected {expected_sha256}, observed {observed_sha256}"
        )
    return resolved


def tauri_tools_directory(environ: dict[str, str] | None = None) -> Path:
    """Return the build-tool cache Tauri bundles WebView2 from."""

    env = os.environ if environ is None else environ
    local_app_data = env.get("LOCALAPPDATA")
    if not local_app_data:
        raise ValueError("LOCALAPPDATA is required to locate Tauri's WebView2 installer cache")
    return Path(local_app_data) / "tauri"


def webview2_cache_entry(
    url: str = WEBVIEW2_OFFLINE_INSTALLER_URL,
) -> tuple[str, str]:
    """Resolve the CDN GUID/filename exactly as tauri-bundler does."""

    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            final_url = response.geturl()
    except OSError as exc:
        raise ValueError(
            "cannot resolve the WebView2 offline installer cache entry without "
            "network access; the build must use the pinned local installer, not "
            f"download one ({exc})"
        ) from exc
    if not final_url.startswith(WEBVIEW2_URL_PREFIX):
        raise ValueError(
            f"WebView2 URL prefix mismatch; expected {WEBVIEW2_URL_PREFIX}, found {final_url}"
        )
    guid, separator, filename = final_url[len(WEBVIEW2_URL_PREFIX) :].partition("/")
    if not separator or not guid or not filename or "/" in filename:
        raise ValueError(
            "WebView2 URL format mismatch; expected <GUID>/<FILENAME>, found "
            f"{final_url[len(WEBVIEW2_URL_PREFIX) :]}"
        )
    return guid, filename


def seed_webview2_offline_installer(
    source: Path,
    *,
    tools_dir: Path | None = None,
    url: str = WEBVIEW2_OFFLINE_INSTALLER_URL,
    resolve: Callable[[str], tuple[str, str]] = webview2_cache_entry,
) -> Path:
    """Place the pinned installer where Tauri will reuse it, never download it.

    Tauri only downloads when the resolved cache entry is absent, so seeding
    that exact path means the bundler embeds the reviewed bytes. A stale entry
    (Microsoft rotates the GUID target) is replaced with the pin.
    """

    reviewed = validate_webview2_offline_installer(source)
    root = tauri_tools_directory() if tools_dir is None else tools_dir
    guid, filename = resolve(url)
    entry = root / "x64" / guid / filename
    entry.parent.mkdir(parents=True, exist_ok=True)
    if entry.exists() and _sha256(entry) != WEBVIEW2_OFFLINE_INSTALLER_EXPECTED_SHA256:
        entry.unlink()
    shutil.copyfile(reviewed, entry)
    os.utime(entry, (SOURCE_DATE_EPOCH, SOURCE_DATE_EPOCH))
    observed_sha256 = _sha256(entry)
    if observed_sha256 != WEBVIEW2_OFFLINE_INSTALLER_EXPECTED_SHA256:
        raise ValueError(
            "seeded WebView2 cache entry SHA-256 mismatch: "
            f"expected {WEBVIEW2_OFFLINE_INSTALLER_EXPECTED_SHA256}, "
            f"observed {observed_sha256}"
        )
    return entry


def webview2_installer_snapshot(tools_dir: Path) -> dict[Path, tuple[int, int]]:
    """Record every cached WebView2 installer so a download can be detected."""

    snapshot: dict[Path, tuple[int, int]] = {}
    if not tools_dir.is_dir():
        return snapshot
    for installer in tools_dir.rglob(WEBVIEW2_INSTALLER_FILENAME):
        stat = installer.stat()
        snapshot[installer] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def verify_webview2_was_not_downloaded(
    before: dict[Path, tuple[int, int]],
    tools_dir: Path,
    seeded_entry: Path,
) -> None:
    """Fail the build if Tauri fetched a WebView2 installer instead of the pin."""

    downloaded = [
        str(path)
        for path, identity in webview2_installer_snapshot(tools_dir).items()
        if before.get(path) != identity
    ]
    if downloaded:
        raise ValueError(
            "Tauri downloaded the WebView2 offline installer instead of using the "
            f"pinned local copy: {', '.join(downloaded)}"
        )
    observed_sha256 = _sha256(seeded_entry)
    if observed_sha256 != WEBVIEW2_OFFLINE_INSTALLER_EXPECTED_SHA256:
        raise ValueError(
            "Tauri's WebView2 cache entry is not the pinned installer: "
            f"expected {WEBVIEW2_OFFLINE_INSTALLER_EXPECTED_SHA256}, "
            f"observed {observed_sha256}"
        )


def _run(
    command: list[str],
    *,
    cwd: Path = INSTALLER_DIR,
    env: dict[str, str] | None = None,
) -> None:
    completed = subprocess.run(command, cwd=cwd, env=env, check=False)
    if completed.returncode != 0:
        rendered = subprocess.list2cmdline(command)
        raise RuntimeError(
            f"native bootstrap build command failed ({completed.returncode}): {rendered}"
        )


def reproducible_build_environment(base: dict[str, str]) -> dict[str, str]:
    """Return a controlled Rust/PE build environment for repeatable bytes."""

    env = base.copy()
    remapped_source = "C:/civiccast-src"
    remapped_cargo = "C:/cargo-home"
    rust_flags = [
        "-C",
        "link-arg=/Brepro",
        f"--remap-path-prefix={ROOT}={remapped_source}",
        f"--remap-path-prefix={Path.home() / '.cargo'}={remapped_cargo}",
    ]
    env.pop("RUSTFLAGS", None)
    env["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join(rust_flags)
    env["SOURCE_DATE_EPOCH"] = str(SOURCE_DATE_EPOCH)
    return env


def patch_tauri_bundle_type(binary: bytes) -> bytes:
    """Patch Tauri's fixed marker to the NSIS bundle type exactly once."""

    occurrences = binary.count(TAURI_UNKNOWN_BUNDLE_MARKER)
    if occurrences != 1:
        raise ValueError(
            "Tauri bundle marker contract failed: expected exactly one "
            f"{TAURI_UNKNOWN_BUNDLE_MARKER!r}, found {occurrences}"
        )
    return binary.replace(
        TAURI_UNKNOWN_BUNDLE_MARKER,
        TAURI_NSIS_BUNDLE_MARKER,
        1,
    )


def _find_makensis() -> Path:
    candidates: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        nsis = Path(local_app_data) / "tauri" / "NSIS"
        candidates.extend((nsis / "makensis.exe", nsis / "Bin" / "makensis.exe"))
    discovered = shutil.which("makensis")
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("makensis was not found after Tauri's NSIS build completed")


def add_nsis_version_preflight(script: str) -> str:
    """Recover interrupted setup, then guard version before pages/prerequisites."""
    # Keep Tauri's optional UTF-8 signature at byte zero when prepending code.
    bom = "\ufeff" if script.startswith("\ufeff") else ""
    script = script.removeprefix("\ufeff")
    marker = "Function .onInit\n"
    guard = (
        "  !insertmacro CIVICCAST_TRANSACTION_RECOVER_PENDING\n"
        "  !insertmacro CIVICCAST_VERSION_PREFLIGHT\n"
    )
    if script.count(marker) != 1:
        raise ValueError("generated NSIS onInit entrypoint changed; cannot place version preflight")
    if marker + guard not in script:
        script = script.replace(marker, marker + guard, 1)
    # Native setup upgrades in place. Tauri's generic reinstall page can run
    # the old uninstaller BEFORE PREINSTALL, destroying the recovery input.
    # Skip that page entirely; installed products use our transaction instead.
    reinstall = "Function PageReinstall\n"
    skip = "  Abort ; Native upgrades use the recoverable PREINSTALL transaction.\n"
    if reinstall in script and reinstall + skip not in script:
        script = script.replace(reinstall, reinstall + skip, 1)
    # Only the normalized template expands our recovery callbacks. Tauri's
    # initial packaging pass must not reference functions it never defines.
    # Define this before MUI expands its pages and generates .onUserAbort.
    abort_callback = "!define MUI_CUSTOMFUNCTION_ABORT CivicCastTransactionAbort\n"
    if abort_callback not in script:
        script = abort_callback + script
    callbacks = "!insertmacro CIVICCAST_TRANSACTION_CALLBACKS\n"
    if callbacks not in script:
        script = script.rstrip() + "\n\n" + callbacks
    return bom + script


def _installed_signing_command(path: str) -> list[str]:
    pwsh = shutil.which("pwsh")
    if not pwsh:
        raise FileNotFoundError("PowerShell 7 is required to sign installed executables")
    return [
        pwsh,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-WindowStyle",
        "Hidden",
        "-File",
        str(ROOT / "scripts" / "sign_native_executable.ps1"),
        "-LiteralPath",
        path,
    ]


def add_nsis_uninstaller_signing(script: str) -> str:
    """Sign NSIS's final uninstaller bytes; any callback failure aborts packing."""
    # Tauri emits this conditional even for --no-sign. Replace that known hook
    # so only our checked callback signs the final generated uninstaller.
    tauri_hook = (
        '!if "${UNINSTALLERSIGNCOMMAND}" != ""\n'
        "  !uninstfinalize '${UNINSTALLERSIGNCOMMAND}'\n"
        "!endif\n"
    )
    script = script.replace(tauri_hook, "", 1)
    if "!uninstfinalize" in script:
        raise ValueError("generated NSIS script already has an uninstaller finalizer")
    command = subprocess.list2cmdline(_installed_signing_command("%1")[:-1]) + ' "%1"'
    # NSIS single quotes delimit the complete Windows command. Dollar signs
    # would otherwise be interpreted by the NSIS preprocessor, not the shell.
    if "'" in command or "\n" in command or "\r" in command:
        raise ValueError("signing tool paths cannot contain quotes or newlines")
    command = command.replace("$", "$$")
    bom = "\ufeff" if script.startswith("\ufeff") else ""
    return bom + "!uninstfinalize '" + command + "' = 0\n" + script.removeprefix("\ufeff")


def normalize_nsis_bootstrap(*, sign_installed_executables: bool = False) -> None:
    """Repack NSIS with deterministic app bytes and source-file timestamp."""

    for required in (MAIN_BINARY, GENERATED_NSIS_SCRIPT):
        if not required.is_file():
            raise FileNotFoundError(f"generated native bootstrap input is missing: {required}")

    script = GENERATED_NSIS_SCRIPT.read_text(encoding="utf-8")
    script = add_nsis_version_preflight(script)
    if sign_installed_executables:
        script = add_nsis_uninstaller_signing(script)
    GENERATED_NSIS_SCRIPT.write_text(script, encoding="utf-8")
    original = MAIN_BINARY.read_bytes()
    patched = patch_tauri_bundle_type(original)
    original_stat = MAIN_BINARY.stat()
    temporary_setup = SETUP_ARTIFACT.with_suffix(".normalized.tmp")
    if temporary_setup.exists():
        temporary_setup.unlink()

    try:
        MAIN_BINARY.write_bytes(patched)
        if sign_installed_executables:
            _run(_installed_signing_command(str(MAIN_BINARY.resolve())), cwd=GENERATED_NSIS_DIR)
        os.utime(MAIN_BINARY, (SOURCE_DATE_EPOCH, SOURCE_DATE_EPOCH))
        if GENERATED_NSIS_OUTPUT.exists():
            GENERATED_NSIS_OUTPUT.unlink()
        _run(
            [str(_find_makensis()), "/V2", str(GENERATED_NSIS_SCRIPT)],
            cwd=GENERATED_NSIS_DIR,
        )
        if not GENERATED_NSIS_OUTPUT.is_file():
            raise FileNotFoundError(f"normalized NSIS output is missing: {GENERATED_NSIS_OUTPUT}")
        temporary_setup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(GENERATED_NSIS_OUTPUT, temporary_setup)
        temporary_setup.replace(SETUP_ARTIFACT)
    finally:
        MAIN_BINARY.write_bytes(original)
        os.utime(
            MAIN_BINARY,
            ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
        )
        if temporary_setup.exists():
            temporary_setup.unlink()


def _build(
    public_key: str,
    key_id: str,
    vc_redist: Path,
    webview2_offline_installer: Path,
    *,
    sign_installed_executables: bool = False,
) -> None:
    npm = "npm.cmd" if os.name == "nt" else "npm"
    tauri = INSTALLER_DIR / "node_modules" / ".bin" / ("tauri.cmd" if os.name == "nt" else "tauri")
    reviewed_redist = validate_vc_redist(vc_redist)
    reviewed_webview2 = validate_webview2_offline_installer(webview2_offline_installer)
    VC_REDIST_RESOURCE.parent.mkdir(parents=True, exist_ok=True)
    if VC_REDIST_RESOURCE.exists():
        VC_REDIST_RESOURCE.unlink()
    try:
        shutil.copyfile(reviewed_redist, VC_REDIST_RESOURCE)
        os.utime(VC_REDIST_RESOURCE, (SOURCE_DATE_EPOCH, SOURCE_DATE_EPOCH))
        # Same reproducibility normalization the redistributable already gets:
        # these three resources are staged by CI (not by this script), so their
        # mtimes carry the runner's clock into the NSIS archive unless pinned.
        for station_resource in (
            STATION_INDEX_RESOURCE,
            STATION_CORE_PACK_RESOURCE,
            STATION_WHISTLE_PACK_RESOURCE,
        ):
            os.utime(station_resource, (SOURCE_DATE_EPOCH, SOURCE_DATE_EPOCH))
        _run([npm, "ci"])
        if not tauri.is_file():
            raise RuntimeError("Tauri CLI was not installed by npm ci")
        env = reproducible_build_environment(os.environ.copy())
        env["CIVICCAST_PACK_PUBLIC_KEY_BASE64"] = public_key
        env["CIVICCAST_PACK_SIGNING_KEY_ID"] = key_id
        if key_id.startswith("development-"):
            env["CIVICCAST_ALLOW_DEVELOPMENT_PACK_KEY"] = "1"
        else:
            env.pop("CIVICCAST_ALLOW_DEVELOPMENT_PACK_KEY", None)
        # Seed Tauri's cache with the reviewed installer BEFORE the bundle so
        # the bundler embeds it instead of fetching today's (larger) build.
        tools_dir = tauri_tools_directory()
        seeded_webview2 = seed_webview2_offline_installer(reviewed_webview2, tools_dir=tools_dir)
        webview2_before = webview2_installer_snapshot(tools_dir)
        _run(
            [
                str(tauri),
                "build",
                "--config",
                "src-tauri/tauri.native.conf.json",
                "--features",
                "native-packs",
                "--bundles",
                "nsis",
                "--no-sign",
                "--ci",
            ],
            env=env,
        )
        verify_webview2_was_not_downloaded(webview2_before, tools_dir, seeded_webview2)
        normalize_nsis_bootstrap(sign_installed_executables=sign_installed_executables)
    finally:
        VC_REDIST_RESOURCE.unlink(missing_ok=True)


def build_report(
    setup: Path,
    *,
    key_id: str,
    embedded_station: dict[str, object] | None = None,
) -> dict[str, object]:
    """Measure an already-built unsigned bootstrap and enforce the size gate."""

    if not setup.is_file():
        raise FileNotFoundError(f"native bootstrap artifact is missing: {setup}")
    observed_bytes = setup.stat().st_size
    headroom = enforce_bootstrap_size(observed_bytes)
    return {
        "artifact": str(setup.resolve()),
        "bytes": observed_bytes,
        "limit_exclusive": BOOTSTRAP_SIZE_LIMIT_EXCLUSIVE,
        "headroom_bytes": headroom,
        "pack_signing_key_id": key_id,
        "sha256": _sha256(setup),
        "signed": False,
        "status": "PASS",
        "embedded_station": embedded_station,
    }


def _fail(message: str) -> NoReturn:
    raise SystemExit(f"build_native_bootstrap: {message}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack-public-key-base64", required=True)
    parser.add_argument("--pack-signing-key-id", required=True)
    parser.add_argument("--vc-redist-x64", required=True, type=Path)
    parser.add_argument("--webview2-offline-installer-x64", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--allow-development-key", action="store_true")
    parser.add_argument(
        "--sign-installed-executables",
        action="store_true",
        help="Sign the patched launcher and generated uninstaller with the existing Azure profile",
    )
    args = parser.parse_args()
    try:
        validate_pack_public_key(args.pack_public_key_base64)
        require_allowed_signing_key(
            args.pack_signing_key_id,
            allow_development_key=args.allow_development_key,
        )
        validate_native_bootstrap_config()
        embedded_station = validate_embedded_station_resources()
        _build(
            args.pack_public_key_base64,
            args.pack_signing_key_id,
            args.vc_redist_x64,
            args.webview2_offline_installer_x64,
            sign_installed_executables=args.sign_installed_executables,
        )
        report = build_report(
            SETUP_ARTIFACT,
            key_id=args.pack_signing_key_id,
            embedded_station=embedded_station,
        )
        report_path = args.report.resolve()
        if report_path.exists():
            raise FileExistsError(f"bootstrap report already exists: {report_path}")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(report, indent=2) + "\n"
        report_path.write_text(rendered, encoding="utf-8", newline="\n")
        print(rendered, end="")
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        _fail(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
