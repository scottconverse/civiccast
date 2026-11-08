# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Policy checks for the Windows release artifact downloader."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DOWNLOADER = ROOT / "scripts" / "download_windows_release_artifacts.ps1"
INSTALL_DOC = ROOT / "INSTALL-WINDOWS.md"
RELEASE_TRUTH = ROOT / "docs" / "releases" / "release-truth.yaml"
# Downloader defaults and its archive fixture follow the published release,
# even while the source version advances to an unpublished candidate.
PUBLISHED_RELEASE_TAG = yaml.safe_load(RELEASE_TRUTH.read_text(encoding="utf-8"))["current"]
PUBLISHED_RELEASE_VERSION = PUBLISHED_RELEASE_TAG.removeprefix("v")


@pytest.mark.parametrize("reply", ["range", "ignore", "bad-range", "disconnect"])
def test_downloader_resumes_partial_bytes_and_refuses_bad_ranges(tmp_path, reply):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell required for the shipped Windows downloader")
    content = b"release-payload-for-range-test"
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.headers.get("Range"))
            ranged = reply != "ignore"
            start = 6 if reply == "bad-range" else 5
            body = content[start:] if ranged else content
            self.send_response(206 if ranged else 200)
            self.send_header("Content-Length", str(len(body)))
            if ranged:
                self.send_header(
                    "Content-Range", f"bytes {start}-{len(content) - 1}/{len(content)}"
                )
            self.end_headers()
            self.wfile.write(body[:3] if reply == "disconnect" else body)
            self.close_connection = True

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    target = tmp_path / "asset.bin"
    partial = tmp_path / "asset.bin.partial"
    partial.write_bytes(content[:5])
    definitions = DOWNLOADER.read_text(encoding="utf8").split(
        "New-Item -ItemType Directory -Force -Path $Destination | Out-Null", 1
    )[0]
    runner = tmp_path / "resume.ps1"
    runner.write_text(
        definitions
        + f"\nSave-ReleaseAsset -Asset ([pscustomobject]@{{name='asset.bin';size={len(content)};browser_download_url='http://127.0.0.1:{server.server_port}/asset';digest='sha256:{hashlib.sha256(content).hexdigest()}'}}) -Target '{target.as_posix()}'\n",
        encoding="utf8",
    )
    try:
        result = subprocess.run(
            [powershell, "-NoProfile", "-File", str(runner)],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert requests == ["bytes=5-"]
    if reply in {"range", "ignore"}:
        assert result.returncode == 0, result.stderr
        assert target.read_bytes() == content
        assert not partial.exists()
    else:
        assert result.returncode != 0
        assert not target.exists()
        assert partial.read_bytes().startswith(content[:5])


def test_windows_release_downloader_verifies_manifest_hashes() -> None:
    source = DOWNLOADER.read_text(encoding="utf-8")

    assert f'$Tag = "{PUBLISHED_RELEASE_TAG}"' in source
    assert '$Version = ""' in source
    assert "$Version = $Tag -replace" in source
    assert '$Tag -ne "v$Version"' in source
    assert 'release-artifacts-manifest.json"' in source
    assert 'clean-windows-proof-kit.zip"' in source
    assert 'windows-tester-package.zip"' in source
    assert "Get-FileHash" in source
    assert "Expand-Archive" in source
    assert "browser_download_url" in source
    assert "sha256" in source


def test_windows_install_doc_matches_current_release_posture() -> None:
    source = INSTALL_DOC.read_text(encoding="utf-8")
    assert f"`{PUBLISHED_RELEASE_TAG}`" in source
    assert f"scottconverse/civiccast/releases/tag/{PUBLISHED_RELEASE_TAG}" in source
    assert f"-Tag {PUBLISHED_RELEASE_TAG} -Version {PUBLISHED_RELEASE_VERSION} -Install" in source


def test_downloader_falls_back_to_release_asset_digest_when_unlisted() -> None:
    """Regression guard for the rc.3 clean-machine-proof gap.

    The release-artifacts manifest is built by one CI job (Linux/portable
    outputs) and never lists artifacts a separate job uploads directly (the
    Windows installer, proof kit, tester package). The downloader must not
    hard-fail when a requested file isn't in that manifest -- it must fall
    back to the GitHub Release asset's own server-computed digest, which is
    what a tester's local `Get-FileHash` will actually match against.
    """
    source = DOWNLOADER.read_text(encoding="utf-8")

    assert "function Get-ExpectedSha256" in source
    assert "$Asset.digest" in source
    assert "not listed in the release-artifacts manifest" in source
    # Get-ManifestEntry must no longer throw on a miss -- callers branch on
    # a null return instead, which is what makes the fallback reachable.
    manifest_entry_fn = source[
        source.index("function Get-ManifestEntry") : source.index("function Get-ExpectedSha256")
    ]
    assert "throw" not in manifest_entry_fn


def test_native_candidate_mode_asset_names_match_the_publisher() -> None:
    """The downloader's NativeCandidate mode must name exactly the assets
    scripts/release/publish_beta_candidate.py uploads, pinned against that
    script's own constants so a rename on either side fails here.
    """
    from scripts.release import publish_beta_candidate as publisher

    source = DOWNLOADER.read_text(encoding="utf-8")

    assert '"NativeCandidate"' in source
    assert f'$NativeSetupName = "{publisher.SETUP_ASSET_NAME}"' in source
    assert f'$NativeSumsName = "{publisher.SHA256SUMS_ASSET_NAME}"' in source
    assert f'$NativeSidecarSuffix = "{publisher.SIDECAR_SUFFIX}"' in source
    assert f'$NativePackSuffix = "{publisher.PACK_SUFFIX}"' in source
    # Default repository is the native repo, and the native mode is the
    # default there; the rc-line modes are kept for the old repository.
    assert '$Repository = "scottconverse/civiccast"' in source
    # Both the current name and the pre-2026-11-08 name select native mode.
    assert (
        'if ($Repository -in @("scottconverse/civiccast", "scottconverse/civiccast-native")) {'
        in source
    )
    assert '$AssetSet = "NativeCandidate"' in source
    assert "[switch]$IncludePacks" in source
    # Every native asset is hash-verified against SHA256SUMS.txt, and the
    # sidecar cross-checked against the same setup.exe line.
    assert "function Read-Sha256Sums" in source
    assert "Assert-Sha256 -Path $setupPath -ExpectedHash $sums[$NativeSetupName]" in source
    assert "Assert-Sha256 -Path $packPath -ExpectedHash $sums[$name]" in source
    assert "[string]$sidecar.sha256 -ne $sums[$NativeSetupName]" in source


@pytest.mark.parametrize(
    "scenario",
    ["complete", "complete-reuse", "legacy-models", "traversal", "conflict", "index-mismatch"],
)
def test_station_archive_verification_and_safe_extraction(tmp_path: Path, scenario: str) -> None:
    """Execute the actual PS5.1 extraction path using a small six-pack ZIP."""
    powershell = shutil.which("powershell")
    if not powershell:
        pytest.skip("Windows PowerShell is required for the Windows downloader")
    files = {
        f"station/{name}.ccpack": name.encode()
        for name in (
            "core",
            "captions-floor",
            "captions-whistle",
            "summary-gemma4-12b",
            "summary-gemma4-e4b",
            "translation-translategemma-4b",
        )
    }
    index = {
        "manifest": {
            "product_version": "1.0.0-beta.20"
            if scenario == "legacy-models"
            else PUBLISHED_RELEASE_VERSION,
            "packs": [
                {
                    "filename": Path(path).name,
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "required": True,
                }
                for path, data in files.items()
            ],
        }
    }
    if scenario == "index-mismatch":
        index["manifest"]["packs"][0]["sha256"] = "0" * 64
    files["station/station-index.json"] = json.dumps(index).encode()
    index_hash = hashlib.sha256(files["station/station-index.json"]).hexdigest()
    files["station/SHA256SUMS.txt"] = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {Path(path).name}\n" for path, data in files.items()
    ).encode()
    manifest = {
        "schema_version": 1,
        "archive_format": "zip",
        "archive_name": "station-models.zip",
        "station_index_sha256": index_hash,
        "unpacked_files": [
            {"path": path, "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for path, data in files.items()
        ],
    }
    archive = tmp_path / "station-models.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for path, data in files.items():
            bundle.writestr(path, data)
        if scenario == "traversal":
            bundle.writestr("../escape.txt", b"unsafe")
    manifest_path = tmp_path / "station-models.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    root = tmp_path / "kit"
    root.mkdir()
    if scenario == "conflict":
        (root / "station").mkdir()
        (root / "station/core.ccpack").write_bytes(b"existing data")
    definitions = DOWNLOADER.read_text(encoding="utf-8").split(
        "New-Item -ItemType Directory -Force -Path $Destination | Out-Null", 1
    )[0]
    runner = tmp_path / "run.ps1"
    runner.write_text(
        definitions + "\n$m = Get-Content -LiteralPath $args[0] -Raw | ConvertFrom-Json\n"
        "Install-StationArchive -ArchivePath $args[1] -Root $args[2] -Manifest $m\n",
        encoding="utf-8",
    )
    # The downloader has named parameters; use a separate invocation body so
    # fixture paths cannot bind to the release downloader's positional options.
    body = (
        runner.read_text(encoding="utf-8")
        .replace("$args[0]", "'" + str(manifest_path).replace("'", "''") + "'")
        .replace("$args[1]", "'" + str(archive).replace("'", "''") + "'")
        .replace("$args[2]", "'" + str(root).replace("'", "''") + "'")
    )
    if scenario == "legacy-models":
        body = body.replace("-Manifest $m\n", "-Manifest $m -ExpectedVersion '1.0.0-beta.20'\n")
    # PowerShell Core can pass a module path that omits Windows PowerShell's
    # built-in Utility module. Bind the PS5.1 fixture to its own built-in module.
    body = body.replace(
        "$m = Get-Content",
        "Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Utility')\n$m = Get-Content",
        1,
    )
    if scenario == "complete-reuse":
        # After a successful extraction, remove the archive and make any archive
        # fetch fail. The complete station must be verified and reused locally.
        body += (
            "\nRemove-Item -LiteralPath '" + str(archive).replace("'", "''") + "'\n"
            "function Get-ReleaseAsset { return [pscustomobject]@{name='station-models.json'} }\n"
            "function Save-ReleaseAsset { param($Asset, $Target) "
            "Copy-Item -LiteralPath '"
            + str(manifest_path).replace("'", "''")
            + "' -Destination $Target }\n"
            "function Assert-AssetDigest {}\n"
            "Save-StationBundle -Release ([pscustomobject]@{}) -Root '"
            + str(root).replace("'", "''")
            + "'\n"
        )
    runner.write_text(body, encoding="utf-8")
    result = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
        text=True,
        capture_output=True,
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if scenario in {"complete", "complete-reuse", "legacy-models"}:
        assert result.returncode == 0, result.stdout + result.stderr
        assert {
            str(p.relative_to(root)).replace("\\", "/"): p.read_bytes()
            for p in root.rglob("*")
            if p.is_file() and p.name != "station-models.json"
        } == files
    else:
        assert result.returncode != 0, result.stdout + result.stderr
        assert not (tmp_path / "escape.txt").exists()
        if scenario == "conflict":
            assert (root / "station/core.ccpack").read_bytes() == b"existing data"
