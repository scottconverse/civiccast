# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Tests for scripts/release/publish_beta_candidate.py.

Every `gh`, `git`, and PowerShell subprocess call is faked via
``publish_beta_candidate.run_command`` (the single seam the script shells
out through) -- no real network, no real git remote, no real GitHub. The
script's live (non-dry-run) publish path against a real GitHub remote is
exercised here only through these fakes; it has never run end to end
against a real kit or real ``gh``/GitHub (see the task's final report).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import scripts.release.publish_beta_candidate as m
from civiccast.installer.native_distribution import (
    STATION_REQUIRED_COMPONENTS,
    build_distribution_index,
    verify_station_media,
)
from scripts.release.candidate_manual import (
    DOCX_FILENAME,
    MANIFEST_FILENAME,
    PDF_FILENAME,
    create_candidate_manual_receipt,
)

SOURCE_SHA = "a" * 40
# Historical publisher fixtures exercise Beta 12's supported evidence contracts.
# Pin their identity independently of the source tree's next release version;
# the explicit Beta 13 case below exercises its separate upgrade/repair contract.
VERSION = "1.0.0-beta.12"
TAG = f"v{VERSION}"


@pytest.fixture(autouse=True)
def _historical_source_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "get_native_source_version", lambda: VERSION)


def test_run_powershell_isolates_only_psmodulepath(monkeypatch: pytest.MonkeyPatch) -> None:
    module_path_key = "PSModulePath"  # Exercise the conventional mixed-case Windows spelling.
    monkeypatch.setenv(module_path_key, "inherited-module-path-must-not-cross-boundary")
    monkeypatch.setenv("CIVICCAST_TEST_CHILD_ENV", "preserved")
    observed: dict[str, object] = {}

    def fake_run_command(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 0, stdout="Valid\n", stderr="")

    monkeypatch.setattr(m, "run_command", fake_run_command)
    result = m.run_powershell("Get-AuthenticodeSignature")

    child_env = observed["env"]
    assert isinstance(child_env, dict)
    assert not any(name.casefold() == "psmodulepath" for name in child_env)
    assert child_env["CIVICCAST_TEST_CHILD_ENV"] == "preserved"
    assert os.environ[module_path_key] == "inherited-module-path-must-not-cross-boundary"
    assert observed["command"][:3] == ["powershell", "-NoProfile", "-NonInteractive"]
    assert result.stdout == "Valid\n"


def _write_kit(
    kit_dir: Path,
    *,
    with_packs: bool = True,
    with_station: bool = True,
    with_manual: bool = True,
) -> Path:
    kit_dir.mkdir(parents=True, exist_ok=True)
    setup = kit_dir / "setup.exe"
    setup.write_bytes(b"fake signed installer bytes")
    if with_packs:
        packs_dir = kit_dir / "packs"
        packs_dir.mkdir(exist_ok=True)
        (packs_dir / "native-app-payload.ccpack").write_bytes(b"pack-a")
        (packs_dir / "native-server-binaries.ccpack").write_bytes(b"pack-b")
    if with_station:
        station_dir = kit_dir / "station"
        station_dir.mkdir(exist_ok=True)
        station_packs = {
            name: station_dir / f"{name}.ccpack" for name in STATION_REQUIRED_COMPONENTS
        }
        for name, path in station_packs.items():
            path.write_bytes(f"fixture station pack {name}".encode())
        station_index_path = station_dir / "station-index.json"
        build_distribution_index(
            output=station_index_path,
            kind="station-index",
            channel="beta",
            product_version=VERSION,
            compatible_core=VERSION,
            packs=station_packs,
            urls={component: [] for component in station_packs},
            signing_private_key=Ed25519PrivateKey.from_private_bytes(bytes(range(32))),
            signing_key_id="civiccast-production-test-key",
            created_epoch=1_700_000_000,
        )
        (station_dir / "SHA256SUMS.txt").write_text("fixture\\n", encoding="utf-8")
    if with_manual:
        manual_dir = kit_dir / "manual"
        manual_dir.mkdir(exist_ok=True)
        pdf = manual_dir / PDF_FILENAME
        docx = manual_dir / DOCX_FILENAME
        pdf.write_bytes(b"fixture candidate pdf")
        docx.write_bytes(b"fixture candidate docx")
        artifacts = [
            {
                "path": f"artifacts/release-preparation/manual/{path.name}",
                "sha256": m.sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
            for path in (pdf, docx)
        ]
        source = m.REPO_ROOT / "docs" / "USER-MANUAL.md"
        source_hash = source.read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        manifest = {
            "source": "docs/USER-MANUAL.md",
            "source_sha256": hashlib.sha256(source_hash.encode("utf-8")).hexdigest(),
            "artifacts": artifacts,
        }
        (manual_dir / MANIFEST_FILENAME).write_text(json.dumps(manifest), encoding="utf-8")
        with patch("scripts.release.candidate_manual._candidate_version", return_value=VERSION):
            create_candidate_manual_receipt(
                manual_dir=manual_dir,
                source_sha=SOURCE_SHA,
                workflow_run_id="111",
                repo_root=m.REPO_ROOT,
            )
    return setup


def _gate_a_doc(*, lane: str, verdict: str = "PASS", source_sha: str = SOURCE_SHA) -> dict:
    doc: dict = {"verdict": verdict, "source_sha": source_sha}
    if lane != "clean":
        doc["lane"] = lane
    return doc


def _fake_command_factory(
    *,
    product_version: str = VERSION,
    signature_status: str = "Valid",
    gate_a_docs: dict[str, dict] | None = None,
    missing_lanes: tuple[str, ...] = (),
    gh_auth_fails: bool = False,
    gh_release_view_assets: list[dict] | None = None,
    mirror_uploaded_assets: bool = False,
    gh_release_view_is_draft: bool = True,
    gh_release_create_fails: bool = False,
    gh_release_edit_fails: bool = False,
    gh_release_delete_fails: bool = False,
):
    """Fake for every subprocess the publisher shells out through.

    ``mirror_uploaded_assets=True`` makes ``gh release view`` report exactly
    the names/sizes of the files passed to ``gh release create`` (a faithful
    happy path); ``gh_release_view_assets`` overrides that with a fixed list
    (to simulate a mismatch). ``git`` is never expected: the publisher must
    not run it, and any call is reported as unexpected.
    """

    if gate_a_docs is None:
        gate_a_docs = {lane: _gate_a_doc(lane=lane) for lane in m.GATE_A_LANES}
    calls: list[list[str]] = []
    uploaded: list[Path] = []

    class Result:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[0] == "powershell":
            script = cmd[-1]
            if "ProductVersion" in script:
                return Result(stdout=product_version)
            if "AuthenticodeSignature" in script:
                return Result(stdout=signature_status)
            return Result(returncode=1, stderr="unexpected powershell script")
        if cmd[0] == "gh":
            if cmd[1:3] == ["auth", "status"]:
                if gh_auth_fails:
                    return Result(
                        returncode=1,
                        stderr="You are not logged into any GitHub hosts. Run gh auth login",
                    )
                return Result()
            if cmd[1:3] == ["run", "download"]:
                name = cmd[cmd.index("-n") + 1]
                dest = Path(cmd[cmd.index("-D") + 1])
                lane = next(
                    (
                        lane
                        for lane in m.GATE_A_LANES
                        if name == m.GATE_A_ARTIFACT_NAMES[lane].format(run_id="111")
                    ),
                    None,
                )
                if lane in missing_lanes or lane not in gate_a_docs:
                    return Result(returncode=1, stderr=f"no such artifact {name}")
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "gate-a-verdict.json").write_text(json.dumps(gate_a_docs[lane]))
                return Result()
            if cmd[1] == "release" and cmd[2] == "create":
                if gh_release_create_fails:
                    return Result(returncode=1, stderr="gh release create failed")
                notes_idx = cmd.index("--notes-file")
                uploaded.extend(Path(p) for p in cmd[notes_idx + 2 :])
                return Result()
            if cmd[1] == "release" and cmd[2] == "view":
                if gh_release_view_assets is not None:
                    assets = gh_release_view_assets
                elif mirror_uploaded_assets:
                    assets = [{"name": p.name, "size": p.stat().st_size} for p in uploaded]
                else:
                    assets = []
                payload = {"assets": assets, "isDraft": gh_release_view_is_draft}
                return Result(stdout=json.dumps(payload))
            if cmd[1] == "release" and cmd[2] == "edit":
                if gh_release_edit_fails:
                    return Result(returncode=1, stderr="edit failed")
                return Result()
            if cmd[1] == "release" and cmd[2] == "delete":
                if gh_release_delete_fails:
                    return Result(returncode=1, stderr="delete failed")
                return Result()
            return Result(returncode=1, stderr=f"unexpected gh command {cmd}")
        return Result(returncode=1, stderr=f"unexpected command {cmd}")

    fake_run.calls = calls
    return fake_run


def _gh_calls(calls: list[list[str]], *prefix: str) -> list[list[str]]:
    return [c for c in calls if c[: len(prefix)] == list(prefix)]


def _assert_no_tag_or_public_release(calls: list[list[str]]) -> None:
    """The no-orphan invariant: nothing ever runs git, and un-draft never ran."""
    assert not any(c[0] == "git" for c in calls)
    assert not any("--draft=false" in c for c in _gh_calls(calls, "gh", "release", "edit"))


def _base_repo_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "docs" / "releases").mkdir(parents=True)
    (root / "docs" / "releases" / "release-truth.yaml").write_text(
        "schema_version: 1\n"
        "repository: scottconverse/civiccast-native\n"
        "current: v1.0.0-beta.1\n"
        "entries:\n"
        "  - tag: v1.0.0-beta.1\n"
        "    status: current\n"
        "    notes: USB-delivered only.\n",
        encoding="utf-8",
    )
    (root / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [Unreleased]\n\n### Added\n\n- Something new.\n\n## [1.0.0-beta.1] - 2026-08-01\n\nOld.\n",
        encoding="utf-8",
    )
    helper = root / "scripts" / "download_windows_release_artifacts.ps1"
    helper.parent.mkdir(parents=True)
    helper.write_bytes(
        (m.REPO_ROOT / "scripts" / "download_windows_release_artifacts.ps1").read_bytes()
    )
    return root


def _args(
    tmp_path: Path,
    kit_dir: Path,
    *,
    dry_run: bool = True,
    truth_status: str = "staging",
    repo_root: Path | None = None,
) -> list[str]:
    argv = [
        "--kit-dir",
        str(kit_dir),
        "--source-sha",
        SOURCE_SHA,
        "--build-run-id",
        "111",
        "--gate-a-run-id",
        "222",
        "--tag",
        TAG,
        "--truth-status",
        truth_status,
    ]
    if dry_run:
        argv.append("--dry-run")
    if repo_root is not None:
        argv.extend(["--repo-root", str(repo_root)])
    return argv


DIRECT_SOURCE_SHA = "b" * 40
DIRECT_BETA11_BASELINE_INSTALLER_SHA256 = (
    "c4dfb6a4a3b0495582bcf169f45af8b4bdae7025c822d1ea5782edf7442013a5"
)
DIRECT_BETA11_STATION_INDEX_SHA256 = (
    "9f107d1fc2f141bdc7ebd1b46aaca3639e624e3f24a4a34a34cc9d7e51952e84"
)
DIRECT_BETA11_VERSION = "1.0.0-beta.11"
DIRECT_BETA13_VERSION = "1.0.0-beta.13"
DIRECT_BETA12_CONSUMER_CONTRACT = "beta11-to-beta12-interrupted-upgrade-v1"
DIRECT_BETA12_SEPARATE_CONSUMER_CONTRACT = "beta11-to-beta12-upgrade-and-d4-repair-v1"


def _write_bound_json(path: Path, value: dict) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return {"path": str(path), "sha256": m.sha256_file(path)}


def _write_bound_text(path: Path, value: str) -> dict[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")
    return {"path": str(path), "sha256": m.sha256_file(path)}


def _write_direct_kit(
    kit_dir: Path,
    *,
    source_sha: str,
    build_run_id: str,
    consumer_mode: str = "sandbox",
    candidate_version: str = VERSION,
    sandbox_contract: str | None = None,
) -> tuple[Path, Path]:
    kit_dir.mkdir(parents=True)
    setup = kit_dir / f"CivicCast (Native)_{candidate_version}_x64-setup.exe"
    setup.write_bytes(f"fake signed {candidate_version} installer".encode())
    (kit_dir / "QUICKSTART-OPERATOR.md").write_text(
        f"{candidate_version} quickstart\n", encoding="utf-8"
    )
    packs_dir = kit_dir / "packs"
    packs_dir.mkdir()
    for name in (
        "native-app-payload",
        "native-cuda-runtime",
        "native-ffmpeg-runtime",
        "native-ollama-runtime",
        "native-server-binaries",
    ):
        (packs_dir / f"{name}.ccpack").write_bytes(name.encode())
    station_dir = kit_dir / "station"
    station_dir.mkdir()
    station_packs = {name: station_dir / f"{name}.ccpack" for name in STATION_REQUIRED_COMPONENTS}
    for name, path in station_packs.items():
        path.write_bytes(name.encode())
    station_index_path = station_dir / "station-index.json"
    station_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    station_key_id = "civiccast-production-test-key"
    build_distribution_index(
        output=station_index_path,
        kind="station-index",
        channel="beta",
        product_version=candidate_version,
        compatible_core=candidate_version,
        packs=station_packs,
        urls={component: [] for component in station_packs},
        signing_private_key=station_key,
        signing_key_id=station_key_id,
        created_epoch=1_700_000_000,
    )
    verified_station_index = verify_station_media(
        station_index_path,
        public_key=station_key.public_key(),
        expected_channel="beta",
        expected_product_version=candidate_version,
        expected_compatible_core=candidate_version,
        expected_signing_key_id=station_key_id,
    )
    (station_dir / "SHA256SUMS.txt").write_text("fixture\n", encoding="utf-8")
    manual_dir = kit_dir / "manual"
    manual_dir.mkdir()
    pdf = manual_dir / PDF_FILENAME
    docx = manual_dir / DOCX_FILENAME
    pdf.write_bytes(b"fixture Beta 11 PDF")
    docx.write_bytes(b"fixture Beta 11 DOCX")
    source = m.REPO_ROOT / "docs" / "USER-MANUAL.md"
    source_text = source.read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    (manual_dir / MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "source": "docs/USER-MANUAL.md",
                "source_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
                "artifacts": [
                    {
                        "path": f"artifacts/release-preparation/manual/{path.name}",
                        "sha256": m.sha256_file(path),
                        "size_bytes": path.stat().st_size,
                    }
                    for path in (pdf, docx)
                ],
            }
        ),
        encoding="utf-8",
    )
    with patch(
        "scripts.release.candidate_manual._candidate_version", return_value=candidate_version
    ):
        create_candidate_manual_receipt(
            manual_dir=manual_dir,
            source_sha=source_sha,
            workflow_run_id=build_run_id,
            repo_root=m.REPO_ROOT,
        )

    members = [
        {
            "path": path.relative_to(kit_dir).as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": m.sha256_file(path),
        }
        for path in sorted(kit_dir.rglob("*"))
        if path.is_file()
    ]
    assert len(members) == 19
    assembly = {
        "schema_version": 1,
        "source_sha": source_sha,
        "workflow_run_id": int(build_run_id),
        "candidate_version": candidate_version,
        "installer": {
            "path": setup.name,
            "size_bytes": setup.stat().st_size,
            "sha256": m.sha256_file(setup),
        },
        "membership": {
            "files": 19,
            "installer_pack_count": 5,
            "station_pack_count": 6,
            "station_folder_present": True,
        },
        "station_index": {
            "sha256": verified_station_index.sha256,
            "signing_key_id": verified_station_index.signing_key_id,
            "pack_count": len(verified_station_index.packs),
            "verified_against": len(verified_station_index.packs),
            "signature_verification": (
                {
                    "consumer_activation_verification": "pending exact signed host refresh and activation."
                }
                if consumer_mode == "physical-host"
                else (
                    {
                        "conclusion": "success",
                        "status": "passed",
                        "workflow_run_id": build_run_id,
                        "job_id": "114115463120",
                        "step_name": "Local station-index and adjacent-pack verification",
                        "method": "local canonical Ed25519 verification and exact six-pack size/SHA-256 comparison",
                    }
                    if sandbox_contract in {"beta12", "beta12-separate"}
                    else {
                        "consumer_activation_verification": {
                            "status": "passed",
                            "actual_sandbox_result": {
                                "baseline_install": {"status": "passed", "exit_code": 0},
                                "upgrade_install": {
                                    "status": "passed",
                                    "exit_code": 0,
                                    "payload": "setup.exe plus exactly five runtime packs; no station folder",
                                },
                                "verify_after_upgrade": {"status": "passed", "exit_code": 0},
                            },
                        }
                    }
                )
            ),
        },
        "kit_members": members,
    }
    assembly_path = kit_dir.parent / "kit-assembly-receipt.json"
    return setup, _write_bound_json(assembly_path, assembly)


def _write_direct_consumer_receipt(
    tmp_path: Path,
    kit_dir: Path,
    *,
    source_sha: str = DIRECT_SOURCE_SHA,
    build_run_id: str = "333",
    runtime_proof_scope: str | None = None,
    consumer_mode: str = "sandbox",
    previous_version: str = "1.0.0-beta.9",
    candidate_version: str = VERSION,
    sandbox_contract: str | None = None,
) -> tuple[Path, Path]:
    if sandbox_contract is None and candidate_version == VERSION:
        sandbox_contract = "beta12"
    setup, assembly_ref = _write_direct_kit(
        kit_dir,
        source_sha=source_sha,
        build_run_id=build_run_id,
        consumer_mode=consumer_mode,
        candidate_version=candidate_version,
        sandbox_contract=sandbox_contract,
    )
    evidence_dir = tmp_path / "consumer-evidence"
    installer_run = {"sha256": m.sha256_file(setup), "exit_code": 0}
    if consumer_mode == "physical-host":
        evidence_dir = tmp_path / "consumer-evidence"
        manifest = {
            "civiccast": {
                "version": m._pep440_version(candidate_version),
                "source_state": {"head": source_sha, "dirty": False},
            },
            "files": [],
        }
        manifest_ref = _write_bound_json(
            evidence_dir / "installed-app-payload-manifest.json", manifest
        )
        host_install = {
            "kind": "beta11-exact-host-install",
            "stage": "completed",
            "status": "passed",
            "source_sha": source_sha,
            "build_run_id": build_run_id,
            "setup_path": str(setup.resolve()),
            "setup_sha256": m.sha256_file(setup),
            "setup_authenticode": {
                "status": "Valid",
                "signer_subject": "CN=Scott Converse, O=Scott Converse, L=Longmont, S=co, C=US",
                "file_version": candidate_version,
            },
            "installer_exit_code": 0,
            "before": {
                "display_version": previous_version,
                "service_state": "Running",
                "schedule_loop_enabled": True,
                "install_location": "C:/Program Files/CivicCast (Native)",
                "health": {
                    "status": "healthy",
                    "version": previous_version,
                    "schema": "current",
                    "live_captions": "healthy",
                    # The published Beta 11 install is the pre-0088/0089
                    # baseline; only the candidate's after-state reaches 0089.
                    "schema_db_revision": "0087_retention_terms",
                },
            },
            "after": {
                "display_version": candidate_version,
                "service_state": "Running",
                "schedule_loop_enabled": True,
                "install_location": "C:/Program Files/CivicCast (Native)",
                "health": {
                    "status": "healthy",
                    "version": candidate_version,
                    "schema": "current",
                    "live_captions": "healthy",
                    "schema_db_revision": "0089_contribution_media_control_requests",
                },
                "app_payload_manifest_path": "C:/Program Files/CivicCast (Native)/runtime/app-payload-manifest.json",
                "app_payload_manifest_sha256": manifest_ref["sha256"],
                "app_payload_manifest_verified": True,
            },
        }
        host_install_ref = _write_bound_json(
            evidence_dir / "actual-host-install.json", host_install
        )
        sample_start = datetime(2026, 10, 8, 4, 0, tzinfo=UTC)
        snapshots = []
        for sample_index in range(2):
            sample_at = sample_start + timedelta(seconds=45 * sample_index)
            channels = []
            for channel in ("public", "government", "education"):
                channels.append(
                    {
                        "id": channel,
                        "state": {"state": "ON_AIR"},
                        "ffprobe_streams": [
                            {"codec_type": "video", "codec_name": "h264"},
                            {"codec_type": "audio", "codec_name": "aac"},
                        ],
                        "caption_runtime_status": {"state": "within-capacity"},
                        "caption_runtime_available": True,
                        "caption_runtime_unavailable_reason": None,
                        "vtt_available": True,
                        "vtt_cue_count": 1 + sample_index,
                        "vtt_sha256": hashlib.sha256(
                            f"{channel}-{sample_index}".encode()
                        ).hexdigest(),
                        "playlist_age_seconds": 1.0,
                        "playlist_mtime_utc": (sample_at - timedelta(seconds=1))
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "newest_segment": f"{channel}-{sample_index}.ts",
                    }
                )
            snapshots.append(
                _write_bound_json(
                    evidence_dir / f"host-runtime-{sample_index + 1}.json",
                    {
                        "utc": sample_at.isoformat().replace("+00:00", "Z"),
                        "health": {
                            "status": "healthy",
                            "version": candidate_version,
                            "schema": "current",
                            "live_captions": "healthy",
                        },
                        "pinned_whistle_error_seen": False,
                        "whistle_active_seen": dict.fromkeys(
                            ("public", "government", "education"), True
                        ),
                        "whistle_fallback_seen": dict.fromkeys(
                            ("public", "government", "education"), False
                        ),
                        "channels": channels,
                    },
                )
            )
        host_runtime_result = _write_bound_json(
            evidence_dir / "host-runtime-result.json",
            {"result": "PASS", "channels": ["public", "government", "education"], "samples": 2},
        )
        receipt_path = tmp_path / "direct-consumer-evidence.json"
        receipt_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "kind": "civiccast-native-beta-direct-consumer-evidence",
                    "consumer_mode": "physical-host",
                    "artifact": {
                        "source_sha": source_sha,
                        "build_run_id": build_run_id,
                        "assembly_receipt": assembly_ref,
                    },
                    "evidence": {
                        "host_install": {
                            "receipt": host_install_ref,
                            "installed_manifest": manifest_ref,
                        },
                        "host_three_channel_runtime": {
                            "result": host_runtime_result,
                            "snapshots": snapshots,
                        },
                    },
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return receipt_path, setup

    if sandbox_contract in {"beta12", "beta12-separate"}:
        receipt_path, setup = _write_beta12_sandbox_receipt(
            tmp_path,
            setup,
            assembly_ref,
            evidence_dir,
            source_sha=source_sha,
            build_run_id=build_run_id,
            candidate_version=candidate_version,
            runtime_proof_scope=runtime_proof_scope,
        )
        if sandbox_contract == "beta12-separate":
            _write_beta12_separate_upgrade_repair_contract(
                receipt_path,
                setup,
                evidence_dir,
                source_sha=source_sha,
                build_run_id=build_run_id,
                candidate_version=candidate_version,
                assembly_ref=assembly_ref,
            )
        return receipt_path, setup

    candidate_state = {
        "installed_version": candidate_version,
        "service_state": "Running",
        "health": {
            "status": "healthy",
            "version": candidate_version,
            "schema": "current",
            "live_captions": "healthy",
        },
        "receipt": {"product_version": candidate_version},
    }
    candidate_activation = {
        "product_version": candidate_version,
        "distribution_index_sha256": "c" * 64,
        "result": "passed",
    }
    beta10_state = {
        "installed_version": "1.0.0-beta.10",
        "service_state": "Running",
        "health": {"status": "healthy", "version": "1.0.0-beta.10"},
    }
    schedule_ids = ["schedule-1", "schedule-2", "schedule-3"]

    def install_proof(folder: str) -> dict[str, dict[str, str]]:
        root = evidence_dir / folder
        return {
            "installer_run": _write_bound_json(root / "installer-run.json", installer_run),
            "install_state": _write_bound_json(root / "install-state.json", candidate_state),
            "activation_self_test": _write_bound_json(
                root / "activation-self-test.json", candidate_activation
            ),
        }

    fresh = install_proof("FreshInstall")
    repair = install_proof("FailedInstallRepair")
    repair.update(
        {
            "fixture": _write_bound_json(
                evidence_dir / "repair-fixture.json",
                {
                    "previous_installed_version": candidate_version,
                    "missing_runtime_key": "whistle_assets_root",
                    "installed_version_marker_removed": True,
                    "upgrade_journal_exists": False,
                    "service_status": "Stopped",
                },
            ),
            "repair_install_log": _write_bound_text(
                evidence_dir / "repair-install-command.log", "PASS: installer exit 0\n"
            ),
            "repair_verify_log": _write_bound_text(
                evidence_dir / "verify-repair-command.log",
                "PASS: preserved account, asset, and schedule IDs\n",
            ),
            "post_repair_result": _write_bound_json(
                evidence_dir / "Beta11" / "VerifyAfterRepair" / "result.json",
                {
                    "result": "PASS",
                    "login": "PASS",
                    "preserve_install_and_database": True,
                    "product_version": candidate_version,
                    "health": {"status": "healthy", "version": candidate_version},
                    "preserved_asset_id": "asset-repair-1",
                    "preserved_schedule_ids": schedule_ids,
                },
            ),
            "post_repair_marker": _write_bound_json(
                evidence_dir / "Beta11" / "VerifyAfterRepair" / "preserve-marker.json",
                {
                    "preserve_install_and_database": True,
                    "product_version": candidate_version,
                    "preserved_baseline_asset_id": "asset-repair-1",
                    "schedule_ids": schedule_ids,
                    "runtime": {"whistle_assets_root": "packs/captions-whistle"},
                },
            ),
        }
    )
    baseline = {
        "installer_run": _write_bound_json(
            evidence_dir / "Beta10-Baseline-Install" / "installer-run.json",
            {
                "installer": "CivicCast (Native)_1.0.0-beta.10_x64-setup.exe",
                "sha256": "d" * 64,
                "exit_code": 0,
            },
        ),
        "install_state": _write_bound_json(
            evidence_dir / "Beta10-Baseline-Install" / "install-state.json", beta10_state
        ),
    }
    upgrade = {
        "installer_run": _write_bound_json(
            evidence_dir / "Beta10ToBeta11Upgrade" / "installer-run.json", installer_run
        ),
        "install_state": _write_bound_json(
            evidence_dir / "Beta10ToBeta11Upgrade" / "install-state.json", candidate_state
        ),
        "activation_self_test": _write_bound_json(
            evidence_dir / "Beta10ToBeta11Upgrade" / "activation-self-test.json",
            candidate_activation,
        ),
        "upgrade_engine_log": _write_bound_text(
            evidence_dir / "Beta10ToBeta11Upgrade" / "upgrade-engine.log.tail.txt",
            f"upgrade engine starting: old=1.0.0-beta.10 new={candidate_version}\nroute: upgrade\n",
        ),
    }
    verified = {
        "result": _write_bound_json(
            evidence_dir / "VerifyAfterUpgrade" / "result.json",
            {
                "result": "PASS",
                "login": "PASS",
                "preserve_install_and_database": True,
                "product_version": candidate_version,
                "health": {"status": "healthy", "version": candidate_version},
                "preserved_asset_id": "asset-baseline-1",
                "preserved_schedule_ids": schedule_ids,
            },
        ),
        "preserve_marker": _write_bound_json(
            evidence_dir / "VerifyAfterUpgrade" / "preserve-marker.json",
            {
                "preserve_install_and_database": True,
                "product_version": candidate_version,
                "preserved_baseline_asset_id": "asset-baseline-1",
                "baseline_schedule_ids": schedule_ids,
                "runtime": {"whistle_assets_root": "packs/captions-whistle"},
            },
        ),
    }
    runtime_channels = (
        ("public",)
        if runtime_proof_scope == "one-channel-install-smoke"
        else ("public", "government", "education")
    )
    runtime_group = (
        "one_channel_install_smoke"
        if runtime_proof_scope == "one-channel-install-smoke"
        else "three_channel_runtime"
    )
    runtime_root = evidence_dir / (
        "OnePublicInstallSmoke"
        if runtime_proof_scope == "one-channel-install-smoke"
        else "AllThreeAfterReporterFix"
    )
    runtime = {
        "result": _write_bound_json(
            runtime_root / "result.json",
            {
                "result": "PASS",
                "channels": list(runtime_channels),
                "observed_minutes": 5,
                "preserve_install_and_database": True,
            },
        ),
        "preserve_marker": _write_bound_json(
            runtime_root / "preserve-marker.json",
            {"preserve_install_and_database": True, "product_version": candidate_version},
        ),
        "snapshots": [],
    }
    sample_start = datetime(2026, 10, 8, 4, 0, tzinfo=UTC)
    for minute in range(1, 6):
        sampled_at = sample_start + timedelta(minutes=minute - 1)
        sampled_at_text = sampled_at.isoformat().replace("+00:00", "Z")
        playlist_mtime_text = (sampled_at - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        channels = []
        for channel_index, channel in enumerate(runtime_channels):
            vtt_text = f"WEBVTT\n\n{channel} fellow Americans\n"
            channels.append(
                {
                    "id": channel,
                    "vtt_available": True,
                    "vtt_sha256": hashlib.sha256(f"{channel}-{minute}".encode()).hexdigest(),
                    "vtt_text_snapshot": vtt_text,
                    "vtt_text_snapshot_utf8_bytes": len(vtt_text.encode("utf-8")),
                    "state": {"state": "ON_AIR"},
                    "ffprobe_streams": [
                        {"codec_type": "video", "codec_name": "h264"},
                        {"codec_type": "audio", "codec_name": "aac"},
                    ],
                    "caption_runtime_status": {"state": "within-capacity"},
                    "caption_runtime_available": True,
                    "caption_runtime_unavailable_reason": None,
                    "vtt_cue_count": 1,
                    "playlist_age_seconds": 1.0,
                    "playlist_mtime_utc": playlist_mtime_text,
                    "segment_count": 6,
                    "newest_segment": f"seg{minute:06d}{channel_index}.ts",
                    "expected_jfk_words_seen": {"fellow": True, "americans": True, "country": True},
                }
            )
        runtime["snapshots"].append(
            _write_bound_json(
                runtime_root / f"minute-{minute}.json",
                {
                    "utc": sampled_at_text,
                    "minute": minute,
                    "health": {"status": "healthy", "version": candidate_version},
                    "pinned_whistle_error_seen": False,
                    "whistle_active_seen": dict.fromkeys(runtime_channels, True),
                    "whistle_fallback_seen": dict.fromkeys(runtime_channels, False),
                    "channels": channels,
                },
            )
        )

    receipt = {
        "schema_version": 1,
        "kind": "civiccast-native-beta-direct-consumer-evidence",
        "artifact": {
            "source_sha": source_sha,
            "build_run_id": build_run_id,
            "assembly_receipt": assembly_ref,
        },
        "evidence": {
            "fresh_install": fresh,
            "failed_install_repair": repair,
            "beta10_baseline_install": baseline,
            "beta10_to_beta11_upgrade": upgrade,
            "verify_after_upgrade": verified,
            runtime_group: runtime,
        },
    }
    if runtime_proof_scope is not None:
        receipt["runtime_proof_scope"] = runtime_proof_scope
    receipt_path = tmp_path / "direct-consumer-evidence.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt_path, setup


def _write_beta12_sandbox_receipt(
    tmp_path: Path,
    setup: Path,
    assembly_ref: dict[str, str],
    evidence_dir: Path,
    *,
    source_sha: str,
    build_run_id: str,
    candidate_version: str,
    runtime_proof_scope: str | None,
) -> tuple[Path, Path]:
    assembly = json.loads(Path(assembly_ref["path"]).read_text(encoding="utf-8"))
    candidate_index_sha256 = assembly["station_index"]["sha256"]
    installer_run = {
        "installer": setup.name,
        "sha256": m.sha256_file(setup),
        "expected_version": candidate_version,
        "exit_code": 0,
        "install_root": "C:\\TestStore\\install",
    }
    candidate_state = {
        "installed_version": candidate_version,
        "service_state": "Running",
        "health": {
            "status": "healthy",
            "version": candidate_version,
            "schema": "current",
            "live_captions": "healthy",
        },
        "receipt": {"product_version": candidate_version},
    }
    candidate_activation = {
        "product": "civiccast-native",
        "product_version": candidate_version,
        "schema_version": 1,
        "distribution_index_sha256": candidate_index_sha256,
        "ai_inference": {"result": "passed", "offline_only": True},
        "caption_inference": {"result": "passed", "device": "cpu"},
    }

    def install_proof(folder: str) -> dict[str, dict[str, str]]:
        root = evidence_dir / folder
        return {
            "installer_run": _write_bound_json(root / "installer-run.json", installer_run),
            "install_state": _write_bound_json(root / "install-state.json", candidate_state),
            "activation_self_test": _write_bound_json(
                root / "activation-self-test.json", candidate_activation
            ),
        }

    fresh = install_proof("FreshInstall")
    baseline_version = "1.0.0-beta.11"
    baseline_install = {
        "installer_run": _write_bound_json(
            evidence_dir / "Beta11BaselineInstall" / "installer-run.json",
            {
                "installer": "CivicCast (Native)_1.0.0-beta.11_x64-setup.exe",
                "sha256": DIRECT_BETA11_BASELINE_INSTALLER_SHA256,
                "expected_version": baseline_version,
                "exit_code": 0,
            },
        ),
        "install_state": _write_bound_json(
            evidence_dir / "Beta11BaselineInstall" / "install-state.json",
            {
                "installed_version": baseline_version,
                "service_state": "Running",
                "health": {"status": "healthy", "version": baseline_version},
                "receipt": {"product_version": baseline_version},
            },
        ),
        "activation_self_test": _write_bound_json(
            evidence_dir / "Beta11BaselineInstall" / "activation-self-test.json",
            {
                "product": "civiccast-native",
                "product_version": baseline_version,
                "schema_version": 1,
                "distribution_index_sha256": DIRECT_BETA11_STATION_INDEX_SHA256,
                "ai_inference": {"result": "passed", "offline_only": True},
                "caption_inference": {"result": "passed", "device": "cpu"},
            },
        ),
    }
    schedule_ids = ["beta11-schedule-1", "beta11-schedule-2", "beta11-schedule-3"]
    asset_id = "beta11-existing-asset"
    baseline_install["preserve_marker"] = _write_bound_json(
        evidence_dir / "Beta11Baseline" / "preserve-marker.json",
        {
            "mode": "PrepareBaseline",
            "product_version": baseline_version,
            "preserve_install_and_database": True,
            "asset_id": asset_id,
            "schedule_ids": schedule_ids,
        },
    )

    observed_bytes = 524_288
    expected_bytes = 1_893_729_051
    partial_after_kill = 528_384
    interrupted = _write_bound_json(
        evidence_dir / "interrupted-install.json",
        {
            "result": "INTERRUPTED_CONFIRMED",
            "candidate_source_sha": source_sha,
            "installer_sha256": m.sha256_file(setup),
            "phase": "stage-packs",
            "progress_marker": "step stage-packs: begin",
            "process_id": 1204,
            "process_exit_code": 1,
            "kill_command_exit_code": 0,
            "partial_path": "C:\\TestStore\\install\\packs\\native-cuda-runtime.ccpack.partial",
            "bytes_before_kill": observed_bytes,
            "expected_bytes": expected_bytes,
            "partial_bytes_after_kill": partial_after_kill,
            "baseline_health_after_interruption": {"result": "responded", "status": "healthy"},
        },
    )
    recovery = _write_bound_json(
        evidence_dir / "upgrade-recovery-result.json",
        {
            "result": "PASS",
            "candidate_source_sha": source_sha,
            "candidate_version": candidate_version,
            "workflow_run_id": build_run_id,
            "interrupted_stage": "stage-packs",
            "repair_installer_sha256": m.sha256_file(setup),
            "baseline_version": baseline_version,
            "interruption_bytes": observed_bytes,
            "interruption_expected_bytes": expected_bytes,
            "remaining_partial_pack_count": 0,
            "preservation_result": "C:\\TestEvidence\\Beta12\\Beta12PostUpgrade\\preserve-marker.json",
        },
    )
    repair_log = _write_bound_text(
        evidence_dir / "interrupted-repair-install-command.log",
        "PASS: same candidate repaired partial pack.\n",
    )
    post_upgrade_log = _write_bound_text(
        evidence_dir / "post-upgrade-preservation-command.log",
        "PASS: three channels, Whistle-primary captions, and preserved baseline data.\n",
    )
    interrupted_upgrade = {
        **install_proof("Beta12InterruptedRepair"),
        "interrupted_install": interrupted,
        "repair_install_log": repair_log,
        "recovery_result": recovery,
        "post_upgrade_command_log": post_upgrade_log,
    }

    marker_value = {
        "mode": "PostUpgrade",
        "product_version": candidate_version,
        "preserve_install_and_database": True,
        "asset_id": asset_id,
        "preserved_baseline_asset_id": asset_id,
        "baseline_schedule_ids": schedule_ids,
        "schedule_ids": ["beta12-current-schedule-1", "beta12-current-schedule-2"],
        "runtime": {"whistle_assets_root": "packs/captions-whistle"},
    }
    verify_marker = _write_bound_json(
        evidence_dir / "Beta12PostUpgrade" / "preserve-marker.json", marker_value
    )
    runtime_result_value = {
        "result": "PASS",
        "mode": "PostUpgrade",
        "observed_minutes": 5,
        "channels": ["public", "government", "education"],
        "preserve_install_and_database": True,
        "fixture_sha256": hashlib.sha256(b"synthetic fixture").hexdigest(),
    }
    runtime_result = _write_bound_json(
        evidence_dir / "Beta12PostUpgrade" / "result.json", runtime_result_value
    )
    verify_after_upgrade = {"preserve_marker": verify_marker, "command_log": post_upgrade_log}
    runtime = {
        "result": runtime_result,
        "preserve_marker": verify_marker,
        "snapshots": [],
    }
    sample_start = datetime(2026, 10, 9, 4, 0, tzinfo=UTC)
    for minute in range(1, 6):
        sampled_at = sample_start + timedelta(minutes=minute - 1)
        playlist_mtime = (sampled_at - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        channels = []
        for channel_index, channel in enumerate(("public", "government", "education")):
            vtt_text = f"WEBVTT\n\n{channel} fellow Americans\n"
            channels.append(
                {
                    "id": channel,
                    "vtt_available": True,
                    "vtt_sha256": hashlib.sha256(f"{channel}-{minute}".encode()).hexdigest(),
                    "vtt_text_snapshot": vtt_text,
                    "vtt_text_snapshot_utf8_bytes": len(vtt_text.encode()),
                    "state": {"state": "ON_AIR"},
                    "ffprobe_streams": [
                        {"codec_type": "video", "codec_name": "h264"},
                        {"codec_type": "audio", "codec_name": "aac"},
                    ],
                    "caption_runtime_status": {"state": "within-capacity"},
                    "caption_runtime_available": True,
                    "caption_runtime_unavailable_reason": None,
                    "vtt_cue_count": 1,
                    "playlist_age_seconds": 1.0,
                    "playlist_mtime_utc": playlist_mtime,
                    "segment_count": 6,
                    "newest_segment": f"seg{minute:06d}{channel_index}.ts",
                    "expected_jfk_words_seen": {
                        "fellow": True,
                        "americans": True,
                        "country": False,
                    },
                    "expected_jfk_words_observation": "observed",
                }
            )
        runtime["snapshots"].append(
            _write_bound_json(
                evidence_dir / "Beta12PostUpgrade" / f"minute-{minute}.json",
                {
                    "utc": sampled_at.isoformat().replace("+00:00", "Z"),
                    "minute": minute,
                    "health": {
                        "status": "healthy",
                        "version": candidate_version,
                        "schema": "current",
                        "live_captions": "healthy",
                    },
                    "pinned_whistle_error_seen": False,
                    "whistle_active_seen": dict.fromkeys(
                        ("public", "government", "education"), True
                    ),
                    "whistle_fallback_seen": dict.fromkeys(
                        ("public", "government", "education"), False
                    ),
                    "channels": channels,
                },
            )
        )

    receipt = {
        "schema_version": 1,
        "kind": "civiccast-native-beta-direct-consumer-evidence",
        "consumer_contract": DIRECT_BETA12_CONSUMER_CONTRACT,
        "artifact": {
            "source_sha": source_sha,
            "build_run_id": build_run_id,
            "assembly_receipt": assembly_ref,
        },
        "evidence": {
            "fresh_install": fresh,
            "beta11_baseline_install": baseline_install,
            "beta11_to_beta12_interrupted_upgrade": interrupted_upgrade,
            "verify_after_upgrade": verify_after_upgrade,
            "three_channel_runtime": runtime,
        },
    }
    if runtime_proof_scope is not None:
        receipt["runtime_proof_scope"] = runtime_proof_scope
    receipt_path = tmp_path / "direct-consumer-evidence.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt_path, setup


def _write_beta12_separate_upgrade_repair_contract(
    receipt_path: Path,
    setup: Path,
    evidence_dir: Path,
    *,
    source_sha: str,
    build_run_id: str,
    candidate_version: str,
    assembly_ref: dict[str, str],
) -> None:
    """Use separate normal-upgrade and D4-repair records for current Beta 12 evidence."""

    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    evidence = receipt["evidence"]
    legacy_upgrade = evidence.pop("beta11_to_beta12_interrupted_upgrade")
    assembly_sha256 = assembly_ref["sha256"]
    installer_sha256 = m.sha256_file(setup)

    launch = _write_bound_json(
        evidence_dir / "upgrade-launch.json",
        {
            "source_sha": source_sha,
            "workflow_run_id": build_run_id,
            "kit_assembly_receipt_sha256": assembly_sha256,
            "candidate_version": candidate_version,
            "candidate_installer_sha256": installer_sha256,
            "candidate_signature_status": "Valid",
            "baseline_installer_sha256": DIRECT_BETA11_BASELINE_INSTALLER_SHA256,
            "baseline_signature_status": "Valid",
            "sandbox_ids": ["sandbox-beta12-upgrade"],
            "registered_one_sandbox": True,
        },
    )
    normal_progress = _write_bound_text(
        evidence_dir / "normal-upgrade-install-progress-final.txt",
        f"""[2026-10-09 05:20:00] step d3-engine: begin (old=1.0.0-beta.11)
[2026-10-09 05:22:00] step d3-engine: evidence route=UPGRADE engine_exit=0
[2026-10-09 05:25:00] step d4-provision: returned 0
[2026-10-09 05:26:00] step d4-service-registration: returned 0
[2026-10-09 05:27:00] step d4-firewall-rule: returned 0
[2026-10-09 05:28:00] postinstall: SUCCESS (InstalledVersion {candidate_version} recorded)
""",
    )
    evidence["beta11_to_beta12_upgrade"] = {
        "launch_receipt": launch,
        "install_progress_log": normal_progress,
    }

    setup_path = r"C:\TestKit\CivicCast (Native)_1.0.0-beta.12_x64-setup.exe"
    progress_log_path = r"C:\ProgramData\CivicCast\install-progress.log"
    delta_path = evidence_dir / "d4-repair-progress-delta.txt"
    delta = """[2026-10-09 06:00:00] step d4-activate-station: begin
[2026-10-09 06:00:01] step d4-activate-station: source EXEDIR
"""
    delta_ref = _write_bound_text(delta_path, delta)
    interruption = _write_bound_json(
        evidence_dir / "d4-repair-interruption.json",
        {
            "result": "INTERRUPTED_CONFIRMED",
            "candidate_source_sha": source_sha,
            "workflow_run_id": build_run_id,
            "candidate_version": candidate_version,
            "installer_path": setup_path,
            "installer_sha256": installer_sha256,
            "started_utc": "2026-10-09T05:59:50Z",
            "installed_before": {
                "product_version": candidate_version,
                "registry_version": candidate_version,
                "service_state": "Running",
                "health_status": "healthy",
                "health_schema": "current",
                "full_public_health": {
                    "status": "healthy",
                    "version": candidate_version,
                    "schema": "current",
                    "live_captions": "healthy",
                    "schema_db_revision": "0089_contribution_media_control_requests",
                    "schema_expected_head": "0089_contribution_media_control_requests",
                },
                "station_set_sha256": "a" * 64,
                "read_errors": {},
            },
            "precondition_guard_checks": {
                "station_product_version_matches": True,
                "registry_version_matches": True,
                "service_running": True,
                "health_status_healthy": True,
                "health_schema_current": True,
            },
            "precondition_health_assessment": {
                "health_status_caption_only_degraded_advisory": False,
                "health_status_consistent": True,
                "health_version_matches": True,
                "health_schema_current": True,
                "schema_db_revision": "0089_contribution_media_control_requests",
                "schema_expected_head": "0089_contribution_media_control_requests",
                "schema_revision_pair_matches": True,
                "live_captions_value_is_string": True,
                "live_captions_enum_valid": True,
                "live_captions_state": "healthy",
                "accepted": True,
            },
            "precondition_captured_utc": "2026-10-09T05:59:55Z",
            "signature_status": "Valid",
            "progress_log_path": progress_log_path,
            "progress_offset_bytes": 4096,
            "progress_delta_path": r"C:\TestEvidence\d4-repair-progress-delta.txt",
            "phase": "observing-d4-activate-station",
            "setup_pid": 1205,
            "observation_started_utc": "2026-10-09T05:59:59Z",
            "d4_begin_line": "step d4-activate-station: begin",
            "d4_begin_observed_utc": "2026-10-09T06:00:00Z",
            "d4_return_observed": False,
            "setup_image_path": setup_path,
            "setup_image_sha256": installer_sha256,
            "setup_signature_status": "Valid",
            "live_pid_verified_utc": "2026-10-09T06:00:00.500000Z",
            "kill_target_pid": 1205,
            "kill_tree": True,
            "taskkill_exit_code": 0,
            "process_exited": True,
            "process_exit_code": 1,
            "interrupted_utc": "2026-10-09T06:00:01Z",
            "observation_ended_utc": "2026-10-09T06:00:02Z",
            "progress_delta_sha256": delta_ref["sha256"],
        },
    )
    repair_install = {
        **legacy_upgrade,
        "interruption": interruption,
        "progress_delta": delta_ref,
    }
    repair_install.pop("interrupted_install", None)
    repair_install.pop("repair_install_log", None)
    repair_install.pop("recovery_result", None)
    repair_install.pop("post_upgrade_command_log", None)
    repair_run_ref = repair_install["installer_run"]
    repair_run_path = Path(repair_run_ref["path"])
    repair_run = json.loads(repair_run_path.read_text(encoding="utf-8"))
    # install-corrected.ps1 records local timestamps with an explicit offset.
    repair_run["finished"] = "2026-10-09T00:05:00-06:00"
    repair_run_ref.update(_write_bound_json(repair_run_path, repair_run))
    evidence["beta12_interrupted_install_repair"] = repair_install

    normal_result_ref = evidence["three_channel_runtime"]["result"]
    normal_marker_ref = evidence["verify_after_upgrade"]["preserve_marker"]
    normal_result = json.loads(Path(normal_result_ref["path"]).read_text(encoding="utf-8"))
    normal_marker = json.loads(Path(normal_marker_ref["path"]).read_text(encoding="utf-8"))
    normal_marker["station_set_sha256"] = "a" * 64
    normal_marker_ref.update(_write_bound_json(Path(normal_marker_ref["path"]), normal_marker))
    evidence["three_channel_runtime"]["preserve_marker"].update(normal_marker_ref)
    # The real manual PostUpgrade invocation saved JSON proof, not a command log.
    evidence["verify_after_upgrade"] = {
        "result": normal_result_ref,
        "preserve_marker": normal_marker_ref,
    }
    repair_result = _write_bound_json(
        evidence_dir / "Beta12InterruptedRepairPostUpgrade" / "result.json",
        normal_result,
    )
    repair_marker = _write_bound_json(
        evidence_dir / "Beta12InterruptedRepairPostUpgrade" / "preserve-marker.json",
        normal_marker,
    )
    repair_snapshots = []
    for index, snapshot_ref in enumerate(evidence["three_channel_runtime"]["snapshots"], start=1):
        snapshot = json.loads(Path(snapshot_ref["path"]).read_text(encoding="utf-8"))
        sampled_at = datetime.fromisoformat(snapshot["utc"].replace("Z", "+00:00")) + timedelta(
            hours=3
        )
        snapshot["utc"] = sampled_at.isoformat().replace("+00:00", "Z")
        for channel in snapshot["channels"]:
            playlist_mtime = datetime.fromisoformat(
                channel["playlist_mtime_utc"].replace("Z", "+00:00")
            ) + timedelta(hours=3)
            channel["playlist_mtime_utc"] = playlist_mtime.isoformat().replace("+00:00", "Z")
        repair_snapshots.append(
            _write_bound_json(
                evidence_dir / "Beta12InterruptedRepairPostUpgrade" / f"minute-{index}.json",
                snapshot,
            )
        )
    evidence["verify_after_repair"] = {"result": repair_result, "preserve_marker": repair_marker}
    evidence["repair_three_channel_runtime"] = {
        "result": repair_result,
        "preserve_marker": repair_marker,
        "snapshots": repair_snapshots,
    }
    receipt["consumer_contract"] = DIRECT_BETA12_SEPARATE_CONSUMER_CONTRACT
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")


def _direct_args(
    tmp_path: Path,
    kit_dir: Path,
    receipt: Path,
    *,
    dry_run: bool = True,
    repo_root: Path | None = None,
) -> list[str]:
    argv = _args(tmp_path, kit_dir, dry_run=False, repo_root=repo_root)
    receipt_doc = json.loads(receipt.read_text(encoding="utf-8"))
    build_run_id = str(receipt_doc["artifact"]["build_run_id"])
    build_arg = argv.index("--build-run-id")
    argv[build_arg + 1] = build_run_id
    gate_arg = argv.index("--gate-a-run-id")
    del argv[gate_arg : gate_arg + 2]
    argv.extend(
        ["--artifact-source-sha", DIRECT_SOURCE_SHA, "--consumer-evidence-receipt", str(receipt)]
    )
    if dry_run:
        argv.append("--dry-run")
    return argv


# ---------------------------------------------------------------------------
# (a) layout
# ---------------------------------------------------------------------------
def test_beta12_direct_receipt_verifies_real_interrupted_upgrade_contract(tmp_path):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(tmp_path, kit_dir)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    setup, packs, summary = m.verify_consumer_evidence_receipt(
        receipt_path=receipt_path,
        kit_dir=kit_dir,
        artifact_source_sha=DIRECT_SOURCE_SHA,
        build_run_id="333",
        candidate_version=VERSION,
    )

    assert setup.is_file()
    assert len(packs) == 5
    assert set(receipt["evidence"]) == {
        "fresh_install",
        "beta11_baseline_install",
        "beta11_to_beta12_interrupted_upgrade",
        "verify_after_upgrade",
        "three_channel_runtime",
    }
    assert "failed_install_repair" not in receipt["evidence"]
    assert "beta10_baseline_install" not in receipt["evidence"]
    assert summary == {
        "consumer_mode": "sandbox",
        "fresh_install": "PASS",
        "beta11_baseline_install": "PASS",
        "beta11_to_beta12_interrupted_upgrade": "PASS",
        "preservation": "PASS",
        "runtime": "PASS (five minutes, three channels)",
        "caption_performance_advisory": "No caption-performance findings observed; these observations are not an automatic publisher gate.",
    }


@pytest.mark.parametrize("sandbox_contract", ("beta12", "beta12-separate"))
def test_beta12_caption_performance_findings_are_advisory_and_reported(tmp_path, sandbox_contract):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path, kit_dir, sandbox_contract=sandbox_contract
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    runtime_groups = ["three_channel_runtime"]
    if sandbox_contract == "beta12-separate":
        runtime_groups.append("repair_three_channel_runtime")
    unchanged_hash = hashlib.sha256(b"WEBVTT\n\n").hexdigest()
    for group in runtime_groups:
        for snapshot_ref in receipt["evidence"][group]["snapshots"]:
            snapshot_path = Path(snapshot_ref["path"])
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            snapshot["health"] = {
                "status": "degraded",
                "version": VERSION,
                "schema": "current",
                "live_captions": "degraded",
            }
            snapshot["pinned_whistle_error_seen"] = True
            snapshot["whistle_active_seen"] = dict.fromkeys(
                ("public", "government", "education"), False
            )
            snapshot["whistle_fallback_seen"] = dict.fromkeys(
                ("public", "government", "education"), True
            )
            for channel in snapshot["channels"]:
                channel["caption_runtime_status"] = {"state": "overloaded"}
                channel["vtt_cue_count"] = 0
                channel["vtt_sha256"] = unchanged_hash
                channel["vtt_text_snapshot"] = "WEBVTT\n\n"
                channel["vtt_text_snapshot_utf8_bytes"] = len(b"WEBVTT\n\n")
                channel["expected_jfk_words_seen"] = {
                    "fellow": False,
                    "americans": False,
                    "country": False,
                }
                channel["expected_jfk_words_observation"] = "observed"
            snapshot_ref.update(_write_bound_json(snapshot_path, snapshot))
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    _, _, summary = m.verify_consumer_evidence_receipt(
        receipt_path=receipt_path,
        kit_dir=kit_dir,
        artifact_source_sha=DIRECT_SOURCE_SHA,
        build_run_id="333",
        candidate_version=VERSION,
    )

    advisory = summary["caption_performance_advisory"]
    assert "not an automatic publisher gate" in advisory
    assert "fallback" in advisory
    assert "zero" in advisory
    assert "JFK" in advisory
    assert "distinct sample hashes" in advisory


def test_beta12_unavailable_caption_files_are_bound_advisory_observations(tmp_path):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(tmp_path, kit_dir)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    for snapshot_ref in receipt["evidence"]["three_channel_runtime"]["snapshots"]:
        snapshot_path = Path(snapshot_ref["path"])
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        snapshot["health"] = {
            "status": "degraded",
            "version": VERSION,
            "schema": "current",
            "live_captions": "unknown",
        }
        for channel in snapshot["channels"]:
            channel.update(
                {
                    "vtt_available": False,
                    "vtt_sha256": None,
                    "vtt_text_snapshot": None,
                    "vtt_text_snapshot_utf8_bytes": None,
                    "vtt_cue_count": None,
                    "caption_runtime_available": False,
                    "caption_runtime_unavailable_reason": "caption runtime status was absent",
                    "caption_runtime_status": None,
                    "expected_jfk_words_observation": "unavailable",
                    "expected_jfk_words_seen": {
                        "fellow": False,
                        "americans": False,
                        "country": False,
                    },
                }
            )
        snapshot_ref.update(_write_bound_json(snapshot_path, snapshot))
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    _, _, summary = m.verify_consumer_evidence_receipt(
        receipt_path=receipt_path,
        kit_dir=kit_dir,
        artifact_source_sha=DIRECT_SOURCE_SHA,
        build_run_id="333",
        candidate_version=VERSION,
    )

    assert "unavailable" in summary["caption_performance_advisory"]


@pytest.mark.parametrize("tamper", ("missing-availability", "unavailable-with-hash"))
def test_beta12_malformed_caption_availability_still_refuses(tmp_path, tamper):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(tmp_path, kit_dir)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    snapshot_ref = receipt["evidence"]["three_channel_runtime"]["snapshots"][0]
    snapshot_path = Path(snapshot_ref["path"])
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    channel = snapshot["channels"][0]
    if tamper == "missing-availability":
        del channel["vtt_available"]
    else:
        channel["vtt_available"] = False
    snapshot_ref.update(_write_bound_json(snapshot_path, snapshot))
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(m.PublishError, match="VTT availability"):
        m.verify_consumer_evidence_receipt(
            receipt_path=receipt_path,
            kit_dir=kit_dir,
            artifact_source_sha=DIRECT_SOURCE_SHA,
            build_run_id="333",
            candidate_version=VERSION,
        )


def test_beta12_station_signature_record_rejects_compiled_bootstrap_only_claim(tmp_path):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(tmp_path, kit_dir)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assembly_ref = receipt["artifact"]["assembly_receipt"]
    assembly_path = Path(assembly_ref["path"])
    assembly = json.loads(assembly_path.read_text(encoding="utf-8"))
    assembly["station_index"]["signature_verification"]["method"] = (
        "compiled candidate bootstrap trust verification"
    )
    assembly_ref.update(_write_bound_json(assembly_path, assembly))
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(m.PublishError, match="passed signed six-pack index"):
        m.verify_consumer_evidence_receipt(
            receipt_path=receipt_path,
            kit_dir=kit_dir,
            artifact_source_sha=DIRECT_SOURCE_SHA,
            build_run_id="333",
            candidate_version=VERSION,
        )


def test_beta12_direct_receipt_verifies_separate_upgrade_and_d4_repair_contract(tmp_path):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path, kit_dir, sandbox_contract="beta12-separate"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    setup, packs, summary = m.verify_consumer_evidence_receipt(
        receipt_path=receipt_path,
        kit_dir=kit_dir,
        artifact_source_sha=DIRECT_SOURCE_SHA,
        build_run_id="333",
        candidate_version=VERSION,
    )

    assert setup.is_file()
    assert len(packs) == 5
    assert receipt["consumer_contract"] == DIRECT_BETA12_SEPARATE_CONSUMER_CONTRACT
    assert set(receipt["evidence"]) == {
        "fresh_install",
        "beta11_baseline_install",
        "beta11_to_beta12_upgrade",
        "beta12_interrupted_install_repair",
        "verify_after_upgrade",
        "three_channel_runtime",
        "verify_after_repair",
        "repair_three_channel_runtime",
    }
    assert "beta11_to_beta12_interrupted_upgrade" not in receipt["evidence"]
    assert summary == {
        "consumer_mode": "sandbox",
        "fresh_install": "PASS",
        "beta11_baseline_install": "PASS",
        "beta11_to_beta12_upgrade": "PASS",
        "beta12_interrupted_install_repair": "PASS",
        "preservation": "PASS",
        "repair_preservation": "PASS",
        "runtime": "PASS (five minutes, three channels before and after repair)",
        "caption_performance_advisory": "No caption-performance findings observed; these observations are not an automatic publisher gate.",
    }


def test_beta13_direct_receipt_accepts_the_stable_separate_upgrade_and_repair_contract(
    tmp_path,
):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path,
        kit_dir,
        candidate_version=DIRECT_BETA13_VERSION,
        sandbox_contract="beta12-separate",
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

    setup, packs, summary = m.verify_consumer_evidence_receipt(
        receipt_path=receipt_path,
        kit_dir=kit_dir,
        artifact_source_sha=DIRECT_SOURCE_SHA,
        build_run_id="333",
        candidate_version=DIRECT_BETA13_VERSION,
    )

    assert setup.name == f"CivicCast (Native)_{DIRECT_BETA13_VERSION}_x64-setup.exe"
    assert len(packs) == 5
    assert receipt["consumer_contract"] == DIRECT_BETA12_SEPARATE_CONSUMER_CONTRACT
    assert summary["beta11_to_beta12_upgrade"] == "PASS"
    assert summary["beta12_interrupted_install_repair"] == "PASS"


def test_beta12_d4_repair_accepts_caption_only_degraded_precondition_health(tmp_path):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path, kit_dir, sandbox_contract="beta12-separate"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    interruption_ref = receipt["evidence"]["beta12_interrupted_install_repair"]["interruption"]
    interruption_path = Path(interruption_ref["path"])
    interruption = json.loads(interruption_path.read_text(encoding="utf-8"))
    interruption["installed_before"].update(
        {
            "health_status": "degraded",
            "full_public_health": {
                "status": "degraded",
                "version": VERSION,
                "schema": "current",
                "live_captions": "unknown",
                "schema_db_revision": "0089_contribution_media_control_requests",
                "schema_expected_head": "0089_contribution_media_control_requests",
            },
        }
    )
    interruption["precondition_guard_checks"]["health_status_healthy"] = False
    interruption["precondition_health_assessment"] = {
        "health_status_caption_only_degraded_advisory": True,
        "health_status_consistent": True,
        "health_version_matches": True,
        "health_schema_current": True,
        "schema_db_revision": "0089_contribution_media_control_requests",
        "schema_expected_head": "0089_contribution_media_control_requests",
        "schema_revision_pair_matches": True,
        "live_captions_value_is_string": True,
        "live_captions_enum_valid": True,
        "live_captions_state": "unknown",
        "accepted": True,
    }
    interruption_ref.update(_write_bound_json(interruption_path, interruption))
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    _, _, summary = m.verify_consumer_evidence_receipt(
        receipt_path=receipt_path,
        kit_dir=kit_dir,
        artifact_source_sha=DIRECT_SOURCE_SHA,
        build_run_id="333",
        candidate_version=VERSION,
    )

    assert (
        "D4 repair installed-before health reports live_captions=unknown"
        in summary["caption_performance_advisory"]
    )


def test_beta12_separate_contract_rejects_reused_pre_repair_runtime(tmp_path):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path, kit_dir, sandbox_contract="beta12-separate"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    evidence = receipt["evidence"]
    evidence["repair_three_channel_runtime"] = evidence["three_channel_runtime"].copy()
    evidence["verify_after_repair"] = {
        "result": evidence["three_channel_runtime"]["result"],
        "preserve_marker": evidence["three_channel_runtime"]["preserve_marker"],
    }
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(m.PublishError, match="repair runtime sample predates completed repair"):
        m.verify_consumer_evidence_receipt(
            receipt_path=receipt_path,
            kit_dir=kit_dir,
            artifact_source_sha=DIRECT_SOURCE_SHA,
            build_run_id="333",
            candidate_version=VERSION,
        )


@pytest.mark.parametrize("tamper", ("result", "mode", "preservation", "reference"))
def test_beta12_separate_upgrade_requires_its_saved_postupgrade_result(tmp_path, tamper):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path, kit_dir, sandbox_contract="beta12-separate"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    reference = receipt["evidence"]["verify_after_upgrade"]["result"]
    path = Path(reference["path"])
    result = json.loads(path.read_text(encoding="utf-8"))
    if tamper == "reference":
        path = tmp_path / "unrelated-result.json"
    else:
        field = {
            "result": "result",
            "mode": "mode",
            "preservation": "preserve_install_and_database",
        }[tamper]
        result[field] = False if tamper == "preservation" else "FAIL"
    reference.update(_write_bound_json(path, result))
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(m.PublishError, match="post-upgrade verification does not preserve"):
        m.verify_consumer_evidence_receipt(
            receipt_path=receipt_path,
            kit_dir=kit_dir,
            artifact_source_sha=DIRECT_SOURCE_SHA,
            build_run_id="333",
            candidate_version=VERSION,
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("health_schema", "0089_contribution_media_control_requests", "was not confirmed"),
        ("read_errors", {"health": "TimeoutException"}, "was not confirmed"),
        ("kill_target_pid", 9999, "was not confirmed"),
        ("phase", "stage-packs", "was not confirmed"),
        ("progress_delta_path", r"C:\TestEvidence\different.txt", "filenames differ"),
        ("precondition_captured_utc", "2026-10-09T06:01:00Z", "timestamps are out of order"),
        (
            "precondition_health_assessment",
            {"accepted": False},
            "precondition health assessment is inconsistent",
        ),
        ("repair_finished", "2026-10-09T05:59:00Z", "finished before the confirmed interruption"),
    ),
)
def test_beta12_separate_contract_rejects_false_d4_observation(tmp_path, field, value, message):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path, kit_dir, sandbox_contract="beta12-separate"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    reference = receipt["evidence"]["beta12_interrupted_install_repair"][
        "installer_run" if field == "repair_finished" else "interruption"
    ]
    path = Path(reference["path"])
    record = json.loads(path.read_text(encoding="utf-8"))
    if field == "repair_finished":
        record["finished"] = value
    elif field in {"health_schema", "read_errors"}:
        record["installed_before"][field] = value
    else:
        record[field] = value
    reference.update(_write_bound_json(path, record))
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(m.PublishError, match=message):
        m.verify_consumer_evidence_receipt(
            receipt_path=receipt_path,
            kit_dir=kit_dir,
            artifact_source_sha=DIRECT_SOURCE_SHA,
            build_run_id="333",
            candidate_version=VERSION,
        )


def test_beta12_separate_contract_release_notes_name_upgrade_and_repair_truthfully(tmp_path):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path, kit_dir, sandbox_contract="beta12-separate"
    )
    _, _, summary = m.verify_consumer_evidence_receipt(
        receipt_path=receipt_path,
        kit_dir=kit_dir,
        artifact_source_sha=DIRECT_SOURCE_SHA,
        build_run_id="333",
        candidate_version=VERSION,
    )

    notes = m.render_native_beta_candidate_notes(
        tag=TAG,
        source_sha=SOURCE_SHA,
        build_run_url="https://github.com/example/run/333",
        gate_a_run_url=None,
        lane_verdicts=None,
        changelog_unreleased="",
        assets=[{"filename": "setup.exe", "bytes": 12, "sha256": "a" * 64}],
        smartscreen_note="Verify the exact publisher and hash.",
        artifact_source_sha=DIRECT_SOURCE_SHA,
        direct_verification=summary,
    )

    assert "Beta 11 to Beta 12 normal in-place upgrade: PASS" in notes
    assert "Separate Beta 12 D4 interrupted-install repair: PASS" in notes
    assert "Beta 11 to Beta 12 interrupted-upgrade recovery" not in notes
    assert "Caption-performance observations:" in notes
    assert "not an automatic publisher gate" in notes


@pytest.mark.parametrize(
    ("tamper", "message"),
    (
        ("launch-installer", "launch receipt does not bind this candidate"),
        ("normal-engine-exit", "normal Beta 11 to Beta 12 upgrade log"),
        ("d4-invalid-attempt", "D4 repair interruption was not confirmed"),
        ("d4-return-observed", "D4 repair interruption was not confirmed"),
        ("d4-delta-hash", "D4 repair progress delta sha256 mismatch"),
        ("repair-installer", "did not run this 1.0.0-beta.12 installer successfully"),
        ("repair-preservation", "does not preserve the Beta 11 asset and schedules"),
    ),
)
def test_beta12_separate_contract_rejects_unproven_upgrade_or_repair(tmp_path, tamper, message):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path, kit_dir, sandbox_contract="beta12-separate"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    evidence = receipt["evidence"]
    if tamper == "launch-installer":
        reference = evidence["beta11_to_beta12_upgrade"]["launch_receipt"]
        path = Path(reference["path"])
        launch = json.loads(path.read_text(encoding="utf-8"))
        launch["candidate_installer_sha256"] = "0" * 64
        reference.update(_write_bound_json(path, launch))
    elif tamper == "normal-engine-exit":
        reference = evidence["beta11_to_beta12_upgrade"]["install_progress_log"]
        path = Path(reference["path"])
        log = path.read_text(encoding="utf-8").replace("engine_exit=0", "engine_exit=1")
        reference.update(_write_bound_text(path, log))
    elif tamper == "d4-invalid-attempt":
        reference = evidence["beta12_interrupted_install_repair"]["interruption"]
        path = Path(reference["path"])
        record = json.loads(path.read_text(encoding="utf-8"))
        invalid = {
            field: record[field]
            for field in (
                "candidate_source_sha",
                "workflow_run_id",
                "candidate_version",
                "installer_path",
                "installer_sha256",
                "started_utc",
                "observation_ended_utc",
            )
        }
        invalid["result"] = "INVALID"
        invalid["error"] = "Current installed Beta12 state is not healthy enough."
        reference.update(_write_bound_json(path, invalid))
    elif tamper == "d4-return-observed":
        reference = evidence["beta12_interrupted_install_repair"]["interruption"]
        path = Path(reference["path"])
        record = json.loads(path.read_text(encoding="utf-8"))
        record["d4_return_observed"] = True
        reference.update(_write_bound_json(path, record))
    elif tamper == "d4-delta-hash":
        reference = evidence["beta12_interrupted_install_repair"]["interruption"]
        path = Path(reference["path"])
        record = json.loads(path.read_text(encoding="utf-8"))
        record["progress_delta_sha256"] = "0" * 64
        reference.update(_write_bound_json(path, record))
    elif tamper == "repair-installer":
        reference = evidence["beta12_interrupted_install_repair"]["installer_run"]
        path = Path(reference["path"])
        record = json.loads(path.read_text(encoding="utf-8"))
        record["sha256"] = "0" * 64
        reference.update(_write_bound_json(path, record))
    else:
        reference = evidence["repair_three_channel_runtime"]["preserve_marker"]
        path = Path(reference["path"])
        marker = json.loads(path.read_text(encoding="utf-8"))
        marker["baseline_schedule_ids"][0] = "other-schedule"
        replacement = _write_bound_json(path, marker)
        reference.update(replacement)
        evidence["verify_after_repair"]["preserve_marker"].update(replacement)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(m.PublishError, match=message):
        m.verify_consumer_evidence_receipt(
            receipt_path=receipt_path,
            kit_dir=kit_dir,
            artifact_source_sha=DIRECT_SOURCE_SHA,
            build_run_id="333",
            candidate_version=VERSION,
        )


@pytest.mark.parametrize("inference", ("ai_inference", "caption_inference"))
def test_beta12_activation_self_test_requires_both_installed_inference_checks(tmp_path, inference):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(tmp_path, kit_dir)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    reference = receipt["evidence"]["fresh_install"]["activation_self_test"]
    activation_path = Path(reference["path"])
    activation = json.loads(activation_path.read_text(encoding="utf-8"))
    activation[inference]["result"] = "failed"
    reference.update(_write_bound_json(activation_path, activation))
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(m.PublishError, match="fresh_install activation self-test did not pass"):
        m.verify_consumer_evidence_receipt(
            receipt_path=receipt_path,
            kit_dir=kit_dir,
            artifact_source_sha=DIRECT_SOURCE_SHA,
            build_run_id="333",
            candidate_version=VERSION,
        )


@pytest.mark.parametrize(
    "proof_name",
    ("fresh_install", "beta11_to_beta12_interrupted_upgrade"),
)
def test_beta12_candidate_activation_requires_signed_station_index_hash(tmp_path, proof_name):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(tmp_path, kit_dir)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    reference = receipt["evidence"][proof_name]["activation_self_test"]
    activation_path = Path(reference["path"])
    activation = json.loads(activation_path.read_text(encoding="utf-8"))
    activation["distribution_index_sha256"] = "0" * 64
    reference.update(_write_bound_json(activation_path, activation))
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(
        m.PublishError,
        match=f"{proof_name} activation self-test distribution index does not match the signed station index",
    ):
        m.verify_consumer_evidence_receipt(
            receipt_path=receipt_path,
            kit_dir=kit_dir,
            artifact_source_sha=DIRECT_SOURCE_SHA,
            build_run_id="333",
            candidate_version=VERSION,
        )


def test_beta12_baseline_activation_requires_its_pinned_station_index_hash(tmp_path):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(tmp_path, kit_dir)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    reference = receipt["evidence"]["beta11_baseline_install"]["activation_self_test"]
    activation_path = Path(reference["path"])
    activation = json.loads(activation_path.read_text(encoding="utf-8"))
    activation["distribution_index_sha256"] = "d" * 64
    reference.update(_write_bound_json(activation_path, activation))
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(
        m.PublishError,
        match="Beta 11 baseline activation self-test distribution index does not match its pinned station index",
    ):
        m.verify_consumer_evidence_receipt(
            receipt_path=receipt_path,
            kit_dir=kit_dir,
            artifact_source_sha=DIRECT_SOURCE_SHA,
            build_run_id="333",
            candidate_version=VERSION,
        )


def test_beta12_direct_receipt_accepts_only_hash_bound_windows_powershell_json(tmp_path):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(tmp_path, kit_dir)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    bom = b"\xef\xbb\xbf"
    records = (
        ("fresh_install", "installer_run"),
        ("fresh_install", "install_state"),
        ("beta11_baseline_install", "preserve_marker"),
    )
    for proof_name, field in records:
        reference = receipt["evidence"][proof_name][field]
        path = Path(reference["path"])
        value = json.loads(path.read_text(encoding="utf-8"))
        path.write_bytes(bom + (json.dumps(value, indent=2) + "\n").encode("utf-8"))
        reference["sha256"] = m.sha256_file(path)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    _, _, summary = m.verify_consumer_evidence_receipt(
        receipt_path=receipt_path,
        kit_dir=kit_dir,
        artifact_source_sha=DIRECT_SOURCE_SHA,
        build_run_id="333",
        candidate_version=VERSION,
    )

    assert summary["fresh_install"] == "PASS"
    assert summary["beta11_baseline_install"] == "PASS"

    installer_run = receipt["evidence"]["fresh_install"]["installer_run"]
    installer_run_path = Path(installer_run["path"])
    installer_run_path.write_bytes(installer_run_path.read_bytes() + b" ")
    with pytest.raises(m.PublishError, match=r"fresh_install\.installer_run sha256 mismatch"):
        m.verify_consumer_evidence_receipt(
            receipt_path=receipt_path,
            kit_dir=kit_dir,
            artifact_source_sha=DIRECT_SOURCE_SHA,
            build_run_id="333",
            candidate_version=VERSION,
        )


@pytest.mark.parametrize(
    ("tamper", "message"),
    (
        (
            "completed-partial",
            "does not prove incomplete stage-packs interruption",
        ),
        (
            "wrong-repair-installer",
            "does not prove incomplete stage-packs interruption",
        ),
        (
            "baseline-schedule-mismatch",
            "does not preserve the Beta 11 asset and schedules",
        ),
    ),
)
def test_beta12_interrupted_upgrade_receipt_rejects_incomplete_or_unpreserved_proofs(
    tmp_path, tamper, message
):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(tmp_path, kit_dir)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    evidence = receipt["evidence"]
    upgrade = evidence["beta11_to_beta12_interrupted_upgrade"]
    if tamper == "completed-partial":
        reference = upgrade["interrupted_install"]
        path = Path(reference["path"])
        record = json.loads(path.read_text(encoding="utf-8"))
        record["partial_bytes_after_kill"] = record["expected_bytes"]
        reference.update(_write_bound_json(path, record))
    elif tamper == "wrong-repair-installer":
        reference = upgrade["recovery_result"]
        path = Path(reference["path"])
        record = json.loads(path.read_text(encoding="utf-8"))
        record["repair_installer_sha256"] = "0" * 64
        reference.update(_write_bound_json(path, record))
    else:
        references = (
            evidence["verify_after_upgrade"]["preserve_marker"],
            evidence["three_channel_runtime"]["preserve_marker"],
        )
        marker_path = Path(references[0]["path"])
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["baseline_schedule_ids"][0] = "other-schedule"
        replacement = _write_bound_json(marker_path, marker)
        for reference in references:
            reference.update(replacement)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(m.PublishError, match=message):
        m.verify_consumer_evidence_receipt(
            receipt_path=receipt_path,
            kit_dir=kit_dir,
            artifact_source_sha=DIRECT_SOURCE_SHA,
            build_run_id="333",
            candidate_version=VERSION,
        )


def test_direct_consumer_evidence_dry_run_names_producer_and_tag_target(tmp_path, monkeypatch):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(tmp_path, kit_dir)
    fake = _fake_command_factory()
    monkeypatch.setattr(m, "run_command", fake)

    rc = m.main(_direct_args(tmp_path, kit_dir, receipt_path, repo_root=repo_root))

    assert rc == 0
    notes = (repo_root / "artifacts" / "release" / TAG / "RELEASE-NOTES.md").read_text(
        encoding="utf-8"
    )
    assert f"Artifact producer commit: {DIRECT_SOURCE_SHA}" in notes
    assert f"Tag target commit: {SOURCE_SHA}" in notes
    assert f"fresh {VERSION} install" in notes.lower()
    assert "Beta 11 to Beta 12 interrupted-upgrade recovery" in notes
    assert "three-channel" in notes.lower() and "five minutes" in notes.lower()
    assert "Gate A workflow lanes: not run" in notes
    assert "download-only network route: not tested" in notes.lower()
    assert not _gh_calls(fake.calls, "gh", "run", "download")
    assert not _gh_calls(fake.calls, "gh", "release")
    assert (repo_root / "artifacts" / "release" / TAG / "assets" / "setup.exe").is_file()


def test_beta11_legacy_one_channel_install_smoke_remains_supported(tmp_path):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path,
        kit_dir,
        runtime_proof_scope="one-channel-install-smoke",
        candidate_version=DIRECT_BETA11_VERSION,
    )

    _, _, summary = m.verify_consumer_evidence_receipt(
        receipt_path=receipt_path,
        kit_dir=kit_dir,
        artifact_source_sha=DIRECT_SOURCE_SHA,
        build_run_id="333",
        candidate_version=DIRECT_BETA11_VERSION,
    )

    assert summary["runtime"].startswith("PASS (five-minute one-channel install-smoke")
    assert "three-channel capacity unproven" in summary["runtime"]


@pytest.mark.parametrize("contract_issue", ("missing-contract", "one-channel-scope"))
def test_beta12_direct_receipt_rejects_legacy_or_under_scoped_contract(tmp_path, contract_issue):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(tmp_path, kit_dir)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if contract_issue == "missing-contract":
        receipt.pop("consumer_contract")
        message = "must use the Beta 11 interrupted-upgrade contract"
    else:
        receipt["runtime_proof_scope"] = "one-channel-install-smoke"
        message = "requires the three-channel runtime proof"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(m.PublishError, match=message):
        m.verify_consumer_evidence_receipt(
            receipt_path=receipt_path,
            kit_dir=kit_dir,
            artifact_source_sha=DIRECT_SOURCE_SHA,
            build_run_id="333",
            candidate_version=VERSION,
        )


@pytest.mark.parametrize("previous_version", ("1.0.0-beta.9", VERSION))
def test_direct_physical_host_install_uses_host_evidence_without_sandbox_claims(
    tmp_path, monkeypatch, previous_version
):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path, kit_dir, consumer_mode="physical-host", previous_version=previous_version
    )
    monkeypatch.setattr(m, "run_command", _fake_command_factory())

    assert m.main(_direct_args(tmp_path, kit_dir, receipt_path, repo_root=repo_root)) == 0

    notes = (repo_root / "artifacts" / "release" / TAG / "RELEASE-NOTES.md").read_text(
        encoding="utf-8"
    )
    assert "Physical-host consumer verification" in notes
    assert "in-place update on an existing host" in notes.lower()
    assert "three-channel host output" in notes.lower()
    assert "not a three-channel capacity claim" in notes.lower()
    assert "direct sandbox consumer checks passed" not in notes.lower()
    assert "accepted development-station soak is reported separately" in notes.lower()
    assert "Three-channel Whistle/HLS/caption observation" not in notes
    assert "Caption-performance observations:" in notes
    assert "not an automatic publisher gate" in notes


@pytest.mark.parametrize(
    "tamper", (None, "missing-check", "false-check", "unbound-driver", "wrong-process")
)
def test_physical_host_accepts_only_bound_original_precondition_attestation(
    tmp_path, monkeypatch, tamper
):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path, kit_dir, consumer_mode="physical-host", candidate_version="1.0.0-beta.14"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    install_ref = receipt["evidence"]["host_install"]["receipt"]
    install_path = Path(install_ref["path"])
    install = json.loads(install_path.read_text(encoding="utf-8"))
    # The preserved driver is source evidence, not a fabricated before snapshot.
    driver = tmp_path / "original-driver.ps1"
    driver.write_text("preserved original observer source", encoding="utf-8")
    monkeypatch.setattr(m, "_BETA14_ORIGINAL_HOST_DRIVER_SHA256", m.sha256_file(driver))
    failure = {
        "kind": "civiccast-native-exact-host-install",
        "stage": "failed",
        "candidate_version": "1.0.0-beta.14",
        "source_sha": DIRECT_SOURCE_SHA,
        "build_run_id": "333",
        "script_stack_trace": "at <ScriptBlock>, run-host-install.ps1: line 266",
        "error": "Installed native app-payload verification failed with exit code 1. See original log",
    }
    process = {
        "kind": "civiccast-native-exact-host-install",
        "stage": "installer-exited",
        "candidate_version": "1.0.0-beta.14",
        "source_sha": DIRECT_SOURCE_SHA,
        "build_run_id": "333",
        "setup_sha256": install["setup_sha256"],
        "installer_exit_code": 0,
    }
    if tamper == "wrong-process":
        process["setup_sha256"] = "f" * 64
    attestation = {
        "kind": "civiccast-host-preconditions-from-original-control-flow",
        "expected_before_version": "1.0.0-beta.11",
        "original_driver": {"path": str(driver), "sha256": m.sha256_file(driver)},
        "original_failure": _write_bound_json(tmp_path / "original-failure.json", failure),
        "installer_process": _write_bound_json(tmp_path / "original-process.json", process),
        "checks": dict.fromkeys(
            (
                "service_running",
                "schedule_loop_enabled",
                "accepted_health",
                "schema_current",
                "schema_revision_pair_matches",
                "install_location_unchanged",
                "database_data_path_unchanged",
                "database_data_present_before_and_after",
            ),
            True,
        ),
    }
    if tamper == "missing-check":
        attestation["checks"].pop("accepted_health")
    elif tamper == "false-check":
        attestation["checks"]["schema_current"] = False
    elif tamper == "unbound-driver":
        attestation["original_driver"]["sha256"] = "f" * 64
    install["before"] = {"precondition_attestation": attestation}
    install_ref.update(_write_bound_json(install_path, install))
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    kwargs = {
        "receipt_path": receipt_path,
        "kit_dir": kit_dir,
        "artifact_source_sha": DIRECT_SOURCE_SHA,
        "build_run_id": "333",
        "candidate_version": "1.0.0-beta.14",
    }
    if tamper:
        with pytest.raises(m.PublishError):
            m.verify_consumer_evidence_receipt(**kwargs)
    else:
        _, _, summary = m.verify_consumer_evidence_receipt(**kwargs)
        assert summary["consumer_mode"] == "physical-host"


def test_direct_physical_host_accepts_runtime_status_without_channel_state(tmp_path, monkeypatch):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path, kit_dir, consumer_mode="physical-host"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    install_ref = receipt["evidence"]["host_install"]["receipt"]
    install_path = Path(install_ref["path"])
    install = json.loads(install_path.read_text(encoding="utf-8"))
    install["before"]["health"].pop("schema")
    install["before"]["health"].pop("live_captions")
    install_ref.update(_write_bound_json(install_path, install))
    for snapshot_ref in receipt["evidence"]["host_three_channel_runtime"]["snapshots"]:
        snapshot_path = Path(snapshot_ref["path"])
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        for channel in snapshot["channels"]:
            channel.pop("state")
        snapshot_path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        snapshot_ref["sha256"] = m.sha256_file(snapshot_path)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(m, "run_command", _fake_command_factory())

    assert m.main(_direct_args(tmp_path, kit_dir, receipt_path, repo_root=repo_root)) == 0


@pytest.mark.parametrize("candidate_version", (VERSION, "1.0.0-beta.14"))
@pytest.mark.parametrize("runtime_state", ("overloaded", "delayed"))
def test_beta12_physical_host_caption_performance_is_advisory(
    tmp_path, candidate_version, runtime_state
):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path, kit_dir, consumer_mode="physical-host", candidate_version=candidate_version
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    install_ref = receipt["evidence"]["host_install"]["receipt"]
    install_path = Path(install_ref["path"])
    install = json.loads(install_path.read_text(encoding="utf-8"))
    install["before"]["health"].update(
        {"status": "degraded", "schema": "current", "live_captions": "unknown"}
    )
    install["after"]["health"].update(
        {"status": "degraded", "schema": "current", "live_captions": "degraded"}
    )
    install_ref.update(_write_bound_json(install_path, install))
    repeated_hash = hashlib.sha256(b"same observed captions").hexdigest()
    for snapshot_ref in receipt["evidence"]["host_three_channel_runtime"]["snapshots"]:
        snapshot_path = Path(snapshot_ref["path"])
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        snapshot["health"].update(
            {"status": "degraded", "schema": "current", "live_captions": "unknown"}
        )
        for channel in snapshot["channels"]:
            channel.update(
                {
                    "caption_runtime_status": {"state": runtime_state},
                    "caption_runtime_available": True,
                    "caption_runtime_unavailable_reason": None,
                    "vtt_available": True,
                    "vtt_sha256": repeated_hash,
                    "vtt_cue_count": 0,
                }
            )
        snapshot_ref.update(_write_bound_json(snapshot_path, snapshot))
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    _, _, summary = m.verify_consumer_evidence_receipt(
        receipt_path=receipt_path,
        kit_dir=kit_dir,
        artifact_source_sha=DIRECT_SOURCE_SHA,
        build_run_id="333",
        candidate_version=candidate_version,
    )

    advisory = summary["caption_performance_advisory"]
    assert "not an automatic publisher gate" in advisory
    assert runtime_state in advisory
    assert "physical-host pre-install health reports live_captions=unknown" in advisory
    assert "zero cues" in advisory
    assert "VTT hash did not change" in advisory


def test_beta12_physical_host_missing_captions_can_be_reported(tmp_path):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path, kit_dir, consumer_mode="physical-host"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    for snapshot_ref in receipt["evidence"]["host_three_channel_runtime"]["snapshots"]:
        snapshot_path = Path(snapshot_ref["path"])
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        for channel in snapshot["channels"]:
            channel.update(
                {
                    "caption_runtime_status": None,
                    "caption_runtime_available": False,
                    "caption_runtime_unavailable_reason": "caption runtime status was absent",
                    "vtt_available": False,
                    "vtt_sha256": None,
                    "vtt_cue_count": None,
                }
            )
        snapshot_ref.update(_write_bound_json(snapshot_path, snapshot))
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    _, _, summary = m.verify_consumer_evidence_receipt(
        receipt_path=receipt_path,
        kit_dir=kit_dir,
        artifact_source_sha=DIRECT_SOURCE_SHA,
        build_run_id="333",
        candidate_version=VERSION,
    )

    assert "unavailable" in summary["caption_performance_advisory"]


@pytest.mark.parametrize(
    "tamper", ["wrong-source", "wrong-manifest-source", "stale-hls-output", "contradictory-state"]
)
def test_direct_physical_host_refuses_unbound_or_stale_evidence(tmp_path, monkeypatch, tamper):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path, kit_dir, consumer_mode="physical-host"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    evidence = receipt["evidence"]
    install_ref = evidence["host_install"]["receipt"]
    install_path = Path(install_ref["path"])
    install = json.loads(install_path.read_text(encoding="utf-8"))
    manifest_ref = evidence["host_install"]["installed_manifest"]
    manifest_path = Path(manifest_ref["path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if tamper == "wrong-source":
        install["source_sha"] = "f" * 40
        install_path.write_text(json.dumps(install, indent=2) + "\n", encoding="utf-8")
        install_ref["sha256"] = m.sha256_file(install_path)
    elif tamper == "wrong-manifest-source":
        manifest["civiccast"]["source_state"]["head"] = "f" * 40
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        manifest_ref["sha256"] = m.sha256_file(manifest_path)
        install["after"]["app_payload_manifest_sha256"] = manifest_ref["sha256"]
        install_path.write_text(json.dumps(install, indent=2) + "\n", encoding="utf-8")
        install_ref["sha256"] = m.sha256_file(install_path)
    elif tamper == "stale-hls-output":
        snapshots = evidence["host_three_channel_runtime"]["snapshots"]
        first_ref, second_ref = snapshots
        first_path = Path(first_ref["path"])
        second_path = Path(second_ref["path"])
        first = json.loads(first_path.read_text(encoding="utf-8"))
        second = json.loads(second_path.read_text(encoding="utf-8"))
        second["channels"][0]["newest_segment"] = first["channels"][0]["newest_segment"]
        second_path.write_text(json.dumps(second, indent=2) + "\n", encoding="utf-8")
        second_ref["sha256"] = m.sha256_file(second_path)
    else:
        snapshot_ref = evidence["host_three_channel_runtime"]["snapshots"][1]
        snapshot_path = Path(snapshot_ref["path"])
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        snapshot["channels"][0]["state"] = {"state": "STOPPED"}
        snapshot_path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        snapshot_ref["sha256"] = m.sha256_file(snapshot_path)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    fake = _fake_command_factory()
    monkeypatch.setattr(m, "run_command", fake)

    assert m.main(_direct_args(tmp_path, kit_dir, receipt_path, dry_run=False)) == 1
    _assert_no_tag_or_public_release(fake.calls)


@pytest.mark.parametrize(
    "tamper",
    [
        "unknown-scope",
        "wrong-runtime-group",
        "missing-public-channel",
        "wrong-channel",
        "short-snapshot-list",
        "failed-runtime",
        "whistle-primary-lost",
        "whistle-fallback-seen",
        "degraded-caption-runtime",
    ],
)
def test_direct_one_channel_install_smoke_refuses_incomplete_or_failed_evidence(tmp_path, tamper):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path,
        kit_dir,
        runtime_proof_scope="one-channel-install-smoke",
        candidate_version=DIRECT_BETA11_VERSION,
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if tamper == "unknown-scope":
        receipt["runtime_proof_scope"] = "arbitrary-subset"
    elif tamper == "wrong-runtime-group":
        receipt["evidence"]["three_channel_runtime"] = receipt["evidence"].pop(
            "one_channel_install_smoke"
        )
    elif tamper == "short-snapshot-list":
        receipt["evidence"]["one_channel_install_smoke"]["snapshots"].pop()
    else:
        runtime = receipt["evidence"]["one_channel_install_smoke"]
        if tamper == "failed-runtime":
            ref = runtime["result"]
            result_path = Path(ref["path"])
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["result"] = "FAIL"
            result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
            ref["sha256"] = m.sha256_file(result_path)
        else:
            ref = runtime["snapshots"][0]
            snapshot_path = Path(ref["path"])
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            if tamper == "missing-public-channel":
                runtime["result"] = _write_bound_json(
                    snapshot_path.parent / "result.json",
                    {
                        "result": "PASS",
                        "channels": [],
                        "observed_minutes": 5,
                        "preserve_install_and_database": True,
                    },
                )
            elif tamper == "wrong-channel":
                result_ref = runtime["result"]
                result_path = Path(result_ref["path"])
                result = json.loads(result_path.read_text(encoding="utf-8"))
                result["channels"] = ["government"]
                result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
                result_ref["sha256"] = m.sha256_file(result_path)
            elif tamper == "whistle-primary-lost":
                snapshot["whistle_active_seen"]["public"] = False
                snapshot_path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
                ref["sha256"] = m.sha256_file(snapshot_path)
            elif tamper == "whistle-fallback-seen":
                snapshot["whistle_fallback_seen"]["public"] = True
                snapshot_path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
                ref["sha256"] = m.sha256_file(snapshot_path)
            elif tamper == "degraded-caption-runtime":
                snapshot["channels"][0]["caption_runtime_status"]["state"] = "overloaded"
                snapshot_path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
                ref["sha256"] = m.sha256_file(snapshot_path)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(m.PublishError):
        m.verify_consumer_evidence_receipt(
            receipt_path=receipt_path,
            kit_dir=kit_dir,
            artifact_source_sha=DIRECT_SOURCE_SHA,
            build_run_id="333",
            candidate_version=DIRECT_BETA11_VERSION,
        )


@pytest.mark.parametrize("tamper", ["source", "hash", "proof"])
def test_direct_evidence_refusals_happen_before_remote_mutation(tmp_path, monkeypatch, tamper):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(tmp_path, kit_dir)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if tamper == "source":
        receipt["artifact"]["source_sha"] = SOURCE_SHA
    elif tamper == "hash":
        evidence_path = Path(receipt["evidence"]["fresh_install"]["install_state"]["path"])
        evidence_path.write_text("{}\n", encoding="utf-8")
    else:
        del receipt["evidence"]["three_channel_runtime"]
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    fake = _fake_command_factory(mirror_uploaded_assets=True)
    monkeypatch.setattr(m, "run_command", fake)

    rc = m.main(_direct_args(tmp_path, kit_dir, receipt_path, dry_run=False, repo_root=repo_root))

    assert rc == 1
    assert _gh_calls(fake.calls, "gh", "auth", "status")
    assert not _gh_calls(fake.calls, "gh", "run", "download")
    assert not _gh_calls(fake.calls, "gh", "release")
    assert not (repo_root / "artifacts" / "release" / TAG / "RELEASE-NOTES.md").exists()


def test_direct_runtime_rejects_stale_hls_before_remote_mutation(tmp_path, monkeypatch):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(tmp_path, kit_dir)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    snapshot_ref = receipt["evidence"]["three_channel_runtime"]["snapshots"][0]
    snapshot_path = Path(snapshot_ref["path"])
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["channels"][0]["playlist_age_seconds"] = 90.0
    snapshot["channels"][0]["playlist_mtime_utc"] = "2026-10-08T03:58:30Z"
    snapshot_path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    snapshot_ref["sha256"] = m.sha256_file(snapshot_path)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    fake = _fake_command_factory(mirror_uploaded_assets=True)
    monkeypatch.setattr(m, "run_command", fake)

    assert (
        m.main(_direct_args(tmp_path, kit_dir, receipt_path, dry_run=False, repo_root=repo_root))
        == 1
    )
    assert not _gh_calls(fake.calls, "gh", "release")


def test_direct_runtime_accepts_two_accumulated_jfk_words_per_channel(tmp_path, monkeypatch):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(tmp_path, kit_dir)
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    for index, snapshot_ref in enumerate(receipt["evidence"]["three_channel_runtime"]["snapshots"]):
        snapshot_path = Path(snapshot_ref["path"])
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        for channel in snapshot["channels"]:
            channel["expected_jfk_words_seen"] = {
                "fellow": True,
                "americans": index >= 1,
                "country": False,
            }
        snapshot_path.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
        snapshot_ref["sha256"] = m.sha256_file(snapshot_path)
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(m, "run_command", _fake_command_factory())

    assert m.main(_direct_args(tmp_path, kit_dir, receipt_path, repo_root=repo_root)) == 0


def test_layout_missing_setup_exe_refuses(tmp_path):
    kit_dir = tmp_path / "kit"
    kit_dir.mkdir()
    (kit_dir / "packs").mkdir()
    (kit_dir / "station").mkdir()
    with pytest.raises(m.PublishError, match=r"missing setup\.exe"):
        m.verify_layout(kit_dir)


def test_layout_missing_packs_refuses(tmp_path):
    kit_dir = tmp_path / "kit"
    _write_kit(kit_dir, with_packs=False)
    with pytest.raises(m.PublishError, match="packs"):
        m.verify_layout(kit_dir)


def test_layout_empty_packs_dir_refuses(tmp_path):
    kit_dir = tmp_path / "kit"
    _write_kit(kit_dir, with_packs=False)
    (kit_dir / "packs").mkdir()
    with pytest.raises(m.PublishError, match=r"no \*\.ccpack"):
        m.verify_layout(kit_dir)


def test_layout_missing_station_dir_refuses(tmp_path):
    kit_dir = tmp_path / "kit"
    _write_kit(kit_dir, with_station=False)
    with pytest.raises(m.PublishError, match="station"):
        m.verify_layout(kit_dir)


def test_layout_missing_kit_dir_refuses(tmp_path):
    with pytest.raises(m.PublishError, match="does not exist"):
        m.verify_layout(tmp_path / "nope")


def test_end_to_end_refuses_on_bad_layout(tmp_path, monkeypatch):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "kit"
    kit_dir.mkdir()  # empty: no setup.exe, no packs, no station
    monkeypatch.setattr(m, "run_command", _fake_command_factory())
    argv = _args(tmp_path, kit_dir, repo_root=repo_root)
    assert m.main(argv) == 1


# ---------------------------------------------------------------------------
# (a) version identity
# ---------------------------------------------------------------------------
def test_version_mismatch_setup_exe_refuses(monkeypatch):
    monkeypatch.setattr(m, "run_command", _fake_command_factory(product_version="9.9.9"))
    with pytest.raises(m.PublishError, match="ProductVersion"):
        m.verify_version_identity(Path("setup.exe"), TAG)


def test_version_mismatch_source_tree_refuses(monkeypatch):
    monkeypatch.setattr(m, "run_command", _fake_command_factory(product_version=VERSION))
    monkeypatch.setattr(m, "get_native_source_version", lambda: "9.9.9")
    with pytest.raises(m.PublishError, match="_native_version"):
        m.verify_version_identity(Path("setup.exe"), TAG)


def test_tag_without_v_prefix_refuses(monkeypatch):
    monkeypatch.setattr(m, "run_command", _fake_command_factory())
    with pytest.raises(m.PublishError, match="must start with 'v'"):
        m.verify_version_identity(Path("setup.exe"), "1.0.0-beta.2")


def test_end_to_end_refuses_on_version_mismatch(tmp_path, monkeypatch):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "kit"
    _write_kit(kit_dir)
    monkeypatch.setattr(m, "run_command", _fake_command_factory(product_version="9.9.9"))
    argv = _args(tmp_path, kit_dir, repo_root=repo_root)
    assert m.main(argv) == 1


# ---------------------------------------------------------------------------
# (b) signature
# ---------------------------------------------------------------------------
def test_signature_not_valid_refuses(monkeypatch):
    monkeypatch.setattr(m, "run_command", _fake_command_factory(signature_status="NotSigned"))
    with pytest.raises(m.PublishError, match="Valid"):
        m.verify_signature(Path("setup.exe"))


def test_end_to_end_refuses_on_bad_signature(tmp_path, monkeypatch):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "kit"
    _write_kit(kit_dir)
    monkeypatch.setattr(m, "run_command", _fake_command_factory(signature_status="HashMismatch"))
    argv = _args(tmp_path, kit_dir, repo_root=repo_root)
    assert m.main(argv) == 1


def test_quoted_kit_path_stays_literal_and_bad_signature_refuses_before_mutation(
    tmp_path, monkeypatch
):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "O'Hare kit"
    _write_kit(kit_dir)
    fake = _fake_command_factory(signature_status="HashMismatch")
    monkeypatch.setattr(m, "run_command", fake)

    assert m.main(_args(tmp_path, kit_dir, repo_root=repo_root)) == 1

    literal_path = str(kit_dir / "setup.exe").replace("'", "''")
    powershell_scripts = [call[-1] for call in fake.calls if call[0] == "powershell"]
    assert powershell_scripts == [
        f"(Get-Item -LiteralPath '{literal_path}').VersionInfo.ProductVersion",
        f"(Get-AuthenticodeSignature -LiteralPath '{literal_path}').Status",
    ]
    assert not _gh_calls(fake.calls, "gh", "run", "download")
    assert not _gh_calls(fake.calls, "gh", "release")


# ---------------------------------------------------------------------------
# (c) Gate A verdicts
# ---------------------------------------------------------------------------
def test_gate_a_missing_lane_refuses(tmp_path, monkeypatch):
    fake = _fake_command_factory(missing_lanes=("dirty",))
    monkeypatch.setattr(m, "run_command", fake)
    with pytest.raises(m.PublishError, match="dirty"):
        m.download_gate_a_verdicts(
            repository="scottconverse/civiccast-native",
            gate_a_run_id="222",
            build_run_id="111",
            dest_dir=tmp_path / "gate-a",
        )


def test_gate_a_artifact_name_uses_build_run_id_not_gate_a_run_id(tmp_path, monkeypatch):
    """Regression: gate-a-station-acceptance.yml's uploaded artifact names
    are suffixed with the BUILD run id (github.event.inputs.run_id), not the
    Gate A workflow's own run id -- confirmed live on Gate A run 33713004718
    validating build 33711079441, whose artifacts are named
    gate-a-verdict-33711079441 etc, never *-33713004718. A distinct
    gate_a_run_id must still select which run's artifacts `gh run download`
    fetches from, but must NOT be used to format the artifact name."""

    fake = _fake_command_factory()
    monkeypatch.setattr(m, "run_command", fake)
    verdicts = m.download_gate_a_verdicts(
        repository="scottconverse/civiccast-native",
        gate_a_run_id="222",
        build_run_id="111",
        dest_dir=tmp_path / "gate-a",
    )
    assert set(verdicts) == set(m.GATE_A_LANES)
    download_calls = _gh_calls(fake.calls, "gh", "run", "download")
    assert len(download_calls) == len(m.GATE_A_LANES)
    for call in download_calls:
        # gh run download <run-id> ... selects the Gate A run, not the build run.
        assert call[3] == "222"
        artifact_name = call[call.index("-n") + 1]
        assert artifact_name.endswith("111")


def test_gate_a_repeated_downloads_archive_fresh_attempts(tmp_path, monkeypatch):
    """A repeated dry-run/live preparation must never overwrite old proofs."""
    base_fake = _fake_command_factory()

    def refusing_fake(cmd, **kwargs):
        if cmd[:3] == ["gh", "run", "download"]:
            destination = Path(cmd[cmd.index("-D") + 1])
            assert not (destination / "gate-a-verdict.json").exists()
        return base_fake(cmd, **kwargs)

    monkeypatch.setattr(m, "run_command", refusing_fake)
    destination = tmp_path / "gate-a-verdicts"
    first = m.download_gate_a_verdicts(
        repository="scottconverse/civicast-native",
        gate_a_run_id="222",
        build_run_id="111",
        dest_dir=destination,
    )
    second = m.download_gate_a_verdicts(
        repository="scottconverse/civicast-native",
        gate_a_run_id="222",
        build_run_id="111",
        dest_dir=destination,
    )

    assert set(first) == set(second) == set(m.GATE_A_LANES)
    assert {doc["source_sha"] for doc in first.values()} == {SOURCE_SHA}
    assert {doc["source_sha"] for doc in second.values()} == {SOURCE_SHA}
    download_calls = _gh_calls(base_fake.calls, "gh", "run", "download")
    destinations = [Path(call[call.index("-D") + 1]) for call in download_calls]
    assert len(destinations) == 2 * len(m.GATE_A_LANES)
    assert len({path.parents[0] for path in destinations}) == 2
    assert all(path.name in m.GATE_A_LANES for path in destinations)


def test_gate_a_non_pass_verdict_refuses():
    verdicts = {lane: _gate_a_doc(lane=lane) for lane in m.GATE_A_LANES}
    verdicts["clean"]["verdict"] = "FAIL"
    with pytest.raises(m.PublishError, match="did not PASS"):
        m.verify_gate_a_verdicts(verdicts, source_sha=SOURCE_SHA)


def test_gate_a_sha_mismatch_refuses():
    verdicts = {lane: _gate_a_doc(lane=lane) for lane in m.GATE_A_LANES}
    verdicts["dirty"]["source_sha"] = "b" * 40
    with pytest.raises(m.PublishError, match="source_sha"):
        m.verify_gate_a_verdicts(verdicts, source_sha=SOURCE_SHA)


def test_gate_a_missing_lane_key_refuses():
    verdicts = {lane: _gate_a_doc(lane=lane) for lane in m.GATE_A_LANES}
    del verdicts["download-only"]
    with pytest.raises(m.PublishError, match="download-only"):
        m.verify_gate_a_verdicts(verdicts, source_sha=SOURCE_SHA)


def test_end_to_end_refuses_on_gate_a_failure(tmp_path, monkeypatch):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "kit"
    _write_kit(kit_dir)
    docs = {lane: _gate_a_doc(lane=lane) for lane in m.GATE_A_LANES}
    docs["download-only"]["verdict"] = "FAIL"
    monkeypatch.setattr(m, "run_command", _fake_command_factory(gate_a_docs=docs))
    argv = _args(tmp_path, kit_dir, repo_root=repo_root)
    assert m.main(argv) == 1


def test_gate_a_dirty_artifact_reporting_download_only_lane_refuses():
    verdicts = {lane: _gate_a_doc(lane=lane) for lane in m.GATE_A_LANES}
    verdicts["dirty"]["lane"] = "download-only"
    with pytest.raises(
        m.PublishError, match=r"lane 'dirty' verdict document reports lane 'download-only'"
    ):
        m.verify_gate_a_verdicts(verdicts, source_sha=SOURCE_SHA)


def test_gate_a_download_only_artifact_reporting_dirty_lane_refuses():
    verdicts = {lane: _gate_a_doc(lane=lane) for lane in m.GATE_A_LANES}
    verdicts["download-only"]["lane"] = "dirty"
    with pytest.raises(
        m.PublishError, match=r"lane 'download-only' verdict document reports lane 'dirty'"
    ):
        m.verify_gate_a_verdicts(verdicts, source_sha=SOURCE_SHA)


def test_end_to_end_lane_mismatch_refuses_with_no_tag_or_release(tmp_path, monkeypatch):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "kit"
    _write_kit(kit_dir)
    docs = {lane: _gate_a_doc(lane=lane) for lane in m.GATE_A_LANES}
    docs["dirty"]["lane"] = "download-only"
    fake = _fake_command_factory(gate_a_docs=docs, mirror_uploaded_assets=True)
    monkeypatch.setattr(m, "run_command", fake)
    rc = m.main(_args(tmp_path, kit_dir, dry_run=False, repo_root=repo_root))
    assert rc == 1
    assert not _gh_calls(fake.calls, "gh", "release", "create")
    _assert_no_tag_or_public_release(fake.calls)


# ---------------------------------------------------------------------------
# pre-flight: gh auth, 2 GiB cap
# ---------------------------------------------------------------------------
def test_gh_auth_failure_refuses_before_anything_else(tmp_path, monkeypatch):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "kit"
    _write_kit(kit_dir)
    fake = _fake_command_factory(gh_auth_fails=True)
    monkeypatch.setattr(m, "run_command", fake)
    rc = m.main(_args(tmp_path, kit_dir, dry_run=False, repo_root=repo_root))
    assert rc == 1
    # The auth probe must be the ONLY thing that ran.
    assert fake.calls == [["gh", "auth", "status"]]


def test_gh_auth_failure_message_is_specific(monkeypatch):
    monkeypatch.setattr(m, "run_command", _fake_command_factory(gh_auth_fails=True))
    with pytest.raises(m.PublishError, match=r"gh is not authenticated .*gh auth login"):
        m.verify_gh_auth()


def test_preflight_refuses_asset_at_or_above_limit(tmp_path, monkeypatch):
    big = tmp_path / "native-server-binaries.ccpack"
    big.write_bytes(b"x" * 16)
    small = tmp_path / "setup.exe"
    small.write_bytes(b"x" * 4)
    monkeypatch.setattr(m, "GITHUB_ASSET_LIMIT_BYTES", 16)
    with pytest.raises(
        m.PublishError,
        match=r"2 GiB.*refusing before any remote mutation.*native-server-binaries\.ccpack",
    ):
        m.preflight_asset_limits([small, big])


def test_preflight_limit_is_githubs_documented_2_gib():
    assert m.GITHUB_ASSET_LIMIT_BYTES == 2 * 1024**3


def test_end_to_end_oversize_asset_refuses_before_any_remote_mutation(tmp_path, monkeypatch):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "kit"
    _write_kit(kit_dir)
    (kit_dir / "packs" / "native-server-binaries.ccpack").write_bytes(b"x" * 64)
    monkeypatch.setattr(m, "GITHUB_ASSET_LIMIT_BYTES", 64)
    fake = _fake_command_factory(mirror_uploaded_assets=True)
    monkeypatch.setattr(m, "run_command", fake)
    rc = m.main(_args(tmp_path, kit_dir, dry_run=False, repo_root=repo_root))
    assert rc == 1
    assert not _gh_calls(fake.calls, "gh", "release")  # no create/view/edit/delete at all
    _assert_no_tag_or_public_release(fake.calls)


# ---------------------------------------------------------------------------
# (f) draft -> verify -> un-draft; every failure leaves no tag / no release
# ---------------------------------------------------------------------------
def test_draft_asset_size_mismatch_deletes_draft_and_refuses(tmp_path, monkeypatch):
    asset = tmp_path / "setup.exe"
    asset.write_bytes(b"12345")  # 5 bytes locally
    fake = _fake_command_factory(gh_release_view_assets=[{"name": "setup.exe", "size": 999}])
    monkeypatch.setattr(m, "run_command", fake)
    with pytest.raises(m.PublishError, match=r"draft deleted, no tag created.*size mismatch"):
        m.verify_draft_assets(
            repository="scottconverse/civiccast-native", tag=TAG, asset_paths=[asset]
        )
    assert _gh_calls(fake.calls, "gh", "release", "delete") == [
        ["gh", "release", "delete", TAG, "-R", "scottconverse/civiccast-native", "--yes"]
    ]
    _assert_no_tag_or_public_release(fake.calls)


def test_draft_missing_asset_deletes_draft_and_refuses(tmp_path, monkeypatch):
    asset = tmp_path / "setup.exe"
    asset.write_bytes(b"12345")
    fake = _fake_command_factory(gh_release_view_assets=[])
    monkeypatch.setattr(m, "run_command", fake)
    with pytest.raises(m.PublishError, match=r"draft deleted.*missing asset 'setup\.exe'"):
        m.verify_draft_assets(
            repository="scottconverse/civiccast-native", tag=TAG, asset_paths=[asset]
        )
    assert _gh_calls(fake.calls, "gh", "release", "delete")
    _assert_no_tag_or_public_release(fake.calls)


def test_draft_that_is_not_a_draft_refuses(tmp_path, monkeypatch):
    asset = tmp_path / "setup.exe"
    asset.write_bytes(b"12345")
    fake = _fake_command_factory(
        gh_release_view_assets=[{"name": "setup.exe", "size": 5}], gh_release_view_is_draft=False
    )
    monkeypatch.setattr(m, "run_command", fake)
    with pytest.raises(m.PublishError, match=r"not a draft"):
        m.verify_draft_assets(
            repository="scottconverse/civiccast-native", tag=TAG, asset_paths=[asset]
        )


def test_draft_delete_failure_is_reported_loudly(tmp_path, monkeypatch):
    asset = tmp_path / "setup.exe"
    asset.write_bytes(b"12345")
    fake = _fake_command_factory(gh_release_view_assets=[], gh_release_delete_fails=True)
    monkeypatch.setattr(m, "run_command", fake)
    with pytest.raises(m.PublishError, match=r"DRAFT DELETE FAILED -- remove it by hand"):
        m.verify_draft_assets(
            repository="scottconverse/civiccast-native", tag=TAG, asset_paths=[asset]
        )


def test_end_to_end_gh_release_create_failure_refuses_and_cleans_up(tmp_path, monkeypatch):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "kit"
    _write_kit(kit_dir)
    fake = _fake_command_factory(gh_release_create_fails=True)
    monkeypatch.setattr(m, "run_command", fake)
    rc = m.main(_args(tmp_path, kit_dir, dry_run=False, repo_root=repo_root))
    assert rc == 1
    create = _gh_calls(fake.calls, "gh", "release", "create")
    assert len(create) == 1 and "--draft" in create[0] and "--target" in create[0]
    assert _gh_calls(fake.calls, "gh", "release", "delete")  # best-effort cleanup ran
    assert not _gh_calls(fake.calls, "gh", "release", "view")
    _assert_no_tag_or_public_release(fake.calls)
    truth = (repo_root / "docs" / "releases" / "release-truth.yaml").read_text(encoding="utf-8")
    assert TAG not in truth


def test_gh_release_create_failure_message_is_specific(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "run_command", _fake_command_factory(gh_release_create_fails=True))
    notes = tmp_path / "notes.md"
    notes.write_text("x", encoding="utf-8")
    with pytest.raises(
        m.PublishError,
        match=r"gh release create \(draft\) failed .*draft deleted.*no tag was created",
    ):
        m.create_draft_release(
            repository="scottconverse/civiccast-native",
            tag=TAG,
            source_sha=SOURCE_SHA,
            title="t",
            notes_file=notes,
            asset_paths=[],
        )


def test_end_to_end_draft_verify_mismatch_deletes_draft_no_undraft(tmp_path, monkeypatch):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "kit"
    _write_kit(kit_dir)
    fake = _fake_command_factory(gh_release_view_assets=[{"name": "setup.exe", "size": 1}])
    monkeypatch.setattr(m, "run_command", fake)
    rc = m.main(_args(tmp_path, kit_dir, dry_run=False, repo_root=repo_root))
    assert rc == 1
    assert _gh_calls(fake.calls, "gh", "release", "create")
    assert _gh_calls(fake.calls, "gh", "release", "delete")
    _assert_no_tag_or_public_release(fake.calls)
    truth = (repo_root / "docs" / "releases" / "release-truth.yaml").read_text(encoding="utf-8")
    assert TAG not in truth


def test_undraft_failure_leaves_draft_and_refuses(tmp_path, monkeypatch):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "kit"
    _write_kit(kit_dir)
    fake = _fake_command_factory(mirror_uploaded_assets=True, gh_release_edit_fails=True)
    monkeypatch.setattr(m, "run_command", fake)
    rc = m.main(_args(tmp_path, kit_dir, dry_run=False, repo_root=repo_root))
    assert rc == 1
    # Un-draft may have partially applied: the draft is NOT deleted here.
    assert not _gh_calls(fake.calls, "gh", "release", "delete")
    truth = (repo_root / "docs" / "releases" / "release-truth.yaml").read_text(encoding="utf-8")
    assert TAG not in truth


def test_undraft_failure_message_is_specific(monkeypatch):
    monkeypatch.setattr(m, "run_command", _fake_command_factory(gh_release_edit_fails=True))
    with pytest.raises(m.PublishError, match=r"--draft=false failed .*left in place"):
        m.undraft_release(repository="scottconverse/civiccast-native", tag=TAG)


# ---------------------------------------------------------------------------
# dry-run end-to-end
# ---------------------------------------------------------------------------
def test_dry_run_produces_expected_artifacts(tmp_path, monkeypatch):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "kit"
    setup = _write_kit(kit_dir)
    monkeypatch.setattr(m, "run_command", _fake_command_factory())

    argv = _args(tmp_path, kit_dir, repo_root=repo_root)
    rc = m.main(argv)
    assert rc == 0

    out_dir = repo_root / "artifacts" / "release" / TAG
    notes = (out_dir / "RELEASE-NOTES.md").read_text(encoding="utf-8")
    sidecar = json.loads((out_dir / f"{setup.name}.sidecar.json").read_text(encoding="utf-8"))
    sums = (out_dir / "SHA256SUMS.txt").read_text(encoding="utf-8")

    # sidecar shape matches check_sidecar_attestation_integrity.py's contract
    assert sidecar["attestation"] is None
    assert sidecar["install_manifest"]["signed"] is True
    assert sidecar["sha256"] == m.sha256_file(setup)

    # SHA256SUMS.txt lists setup.exe, packs, the release helper and model bundle.
    assert f"{m.sha256_file(setup)}  setup.exe" in sums
    assert "native-app-payload.ccpack" in sums
    assert "native-server-binaries.ccpack" in sums
    helper = repo_root / "scripts" / "download_windows_release_artifacts.ps1"
    assert f"{m.sha256_file(helper)}  download_windows_release_artifacts.ps1" in sums
    assert "station-models.zip.001" in sums
    assert "station-models.json" in sums

    # notes contain the source sha, all three lane verdicts, and every asset hash
    assert SOURCE_SHA in notes
    assert "| clean | PASS |" in notes
    assert "| dirty | PASS |" in notes
    assert "| download-only | PASS |" in notes
    for path in [
        setup,
        kit_dir / "packs" / "native-app-payload.ccpack",
        kit_dir / "packs" / "native-server-binaries.ccpack",
        helper,
        *(
            kit_dir / "manual" / name
            for name in (
                PDF_FILENAME,
                DOCX_FILENAME,
                MANIFEST_FILENAME,
                "candidate-manual-receipt.json",
            )
        ),
    ]:
        assert m.sha256_file(path) in notes
        assert f"{m.sha256_file(path)}  {path.name}" in sums
    # The asset table must also list SHA256SUMS.txt and the sidecar, with
    # their real hashes.
    assert (
        f"| SHA256SUMS.txt | {len(sums.encode()):,} bytes | {m.sha256_file(out_dir / 'SHA256SUMS.txt')} |"
        in notes
    )
    assert f"| {setup.name}.sidecar.json |" in notes
    assert m.sha256_file(out_dir / f"{setup.name}.sidecar.json") in notes
    assert "beta candidate, not a production release" in notes.lower()
    assert "download_windows_release_artifacts.ps1" in notes
    assert "runs setup with `/S`" in notes
    assert "verify the exact sha-256" in notes.lower()
    assert "warning alone proves neither signature failure nor a valid publisher" in notes.lower()
    assert "missing, or the publisher/hash differs, stop" in notes.lower()

    # dry run must not have touched the real release-truth.yaml on disk
    # (only via update_release_truth called on a scratch copy for the summary)
    truth_text = (repo_root / "docs" / "releases" / "release-truth.yaml").read_text(
        encoding="utf-8"
    )
    assert TAG not in truth_text


def test_beta11_refuses_to_publish_without_candidate_manual_bundle(tmp_path, monkeypatch, capsys):
    """Beta 11 release assets must include the exact build's rendered manual."""
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "kit"
    _write_kit(kit_dir, with_manual=False)
    fake = _fake_command_factory()
    monkeypatch.setattr(m, "run_command", fake)

    rc = m.main(_args(tmp_path, kit_dir, dry_run=True, repo_root=repo_root))

    assert rc == 1
    assert "manual" in capsys.readouterr().err.lower()
    assert not _gh_calls(fake.calls, "gh", "run", "download"), (
        "missing manual must fail before fetching Gate A or attempting publication"
    )
    assert not _gh_calls(fake.calls, "gh", "release", "create")

    # no git/gh mutating calls were made
    calls = m.run_command.calls
    assert not _gh_calls(calls, "gh", "release")
    _assert_no_tag_or_public_release(calls)


def test_beta11_refuses_manual_bytes_that_do_not_match_the_candidate_receipt(
    tmp_path, monkeypatch, capsys
):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "kit"
    _write_kit(kit_dir)
    with (kit_dir / "manual" / PDF_FILENAME).open("ab") as handle:
        handle.write(b"tampered")
    fake = _fake_command_factory()
    monkeypatch.setattr(m, "run_command", fake)

    rc = m.main(_args(tmp_path, kit_dir, dry_run=True, repo_root=repo_root))

    assert rc == 1
    assert "manual" in capsys.readouterr().err.lower()
    assert not _gh_calls(fake.calls, "gh", "run", "download")
    assert not _gh_calls(fake.calls, "gh", "release")
    _assert_no_tag_or_public_release(fake.calls)


def test_dry_run_with_current_status_does_not_touch_real_file(tmp_path, monkeypatch):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "kit"
    _write_kit(kit_dir)
    monkeypatch.setattr(m, "run_command", _fake_command_factory())

    argv = _args(tmp_path, kit_dir, truth_status="current", repo_root=repo_root)
    rc = m.main(argv)
    assert rc == 0

    truth_text = (repo_root / "docs" / "releases" / "release-truth.yaml").read_text(
        encoding="utf-8"
    )
    assert "current: v1.0.0-beta.1" in truth_text  # unchanged


# ---------------------------------------------------------------------------
# live (non-dry-run) happy path -- fully faked, exercises the wiring only
# ---------------------------------------------------------------------------
def test_live_publish_draft_verify_undraft_order_and_release_truth(tmp_path, monkeypatch):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "kit"
    setup = _write_kit(kit_dir)
    fake = _fake_command_factory(mirror_uploaded_assets=True)
    monkeypatch.setattr(m, "run_command", fake)

    rc = m.main(
        _args(tmp_path, kit_dir, dry_run=False, truth_status="current", repo_root=repo_root)
    )
    assert rc == 0

    calls = fake.calls
    assert not any(c[0] == "git" for c in calls)  # no manual tag, ever
    create = _gh_calls(calls, "gh", "release", "create")[0]
    assert "--draft" in create and create[create.index("--target") + 1] == SOURCE_SHA
    assert "--prerelease" in create
    uploaded = create[create.index("--notes-file") + 2 :]
    assert [Path(p).name for p in uploaded] == [
        setup.name,
        "native-app-payload.ccpack",
        "native-server-binaries.ccpack",
        PDF_FILENAME,
        DOCX_FILENAME,
        MANIFEST_FILENAME,
        "candidate-manual-receipt.json",
        "station-models.zip.001",
        "station-models.json",
        "download_windows_release_artifacts.ps1",
        "SHA256SUMS.txt",
        f"{setup.name}.sidecar.json",
    ]
    view = _gh_calls(calls, "gh", "release", "view")[0]
    assert "assets,isDraft" in view
    edit = _gh_calls(calls, "gh", "release", "edit")[0]
    assert "--draft=false" in edit
    assert not _gh_calls(calls, "gh", "release", "delete")
    # Order: create -> view -> edit
    order = [c[2] for c in _gh_calls(calls, "gh", "release")]
    assert order == ["create", "view", "edit"]

    truth = (repo_root / "docs" / "releases" / "release-truth.yaml").read_text(encoding="utf-8")
    assert f"current: {TAG}" in truth
    assert f"superseded_by: {TAG}" in truth


# ---------------------------------------------------------------------------
# release-truth.yaml update
# ---------------------------------------------------------------------------
def test_update_release_truth_staging_adds_entry_without_flipping_current(tmp_path):
    truth_path = tmp_path / "release-truth.yaml"
    truth_path.write_text(
        "schema_version: 1\nrepository: scottconverse/civiccast-native\ncurrent: v1.0.0-beta.1\nentries:\n"
        "  - tag: v1.0.0-beta.1\n    status: current\n    notes: USB only.\n",
        encoding="utf-8",
    )
    summary = m.update_release_truth(
        truth_path=truth_path, tag=TAG, status="staging", notes="staging candidate"
    )
    text = truth_path.read_text(encoding="utf-8")
    assert f"tag: {TAG}" in text
    assert "status: staging" in text
    assert "current: v1.0.0-beta.1" in text  # unchanged
    assert TAG in summary


def test_update_release_truth_current_flips_previous_entry(tmp_path):
    truth_path = tmp_path / "release-truth.yaml"
    truth_path.write_text(
        "schema_version: 1\nrepository: scottconverse/civiccast-native\ncurrent: v1.0.0-beta.1\nentries:\n"
        "  - tag: v1.0.0-beta.1\n    status: current\n    notes: USB only.\n",
        encoding="utf-8",
    )
    m.update_release_truth(truth_path=truth_path, tag=TAG, status="current", notes="published")
    text = truth_path.read_text(encoding="utf-8")
    assert f"current: {TAG}" in text
    assert "- tag: v1.0.0-beta.1\n    status: superseded" in text
    assert f"superseded_by: {TAG}" in text


def test_update_release_truth_rejects_bad_status(tmp_path):
    truth_path = tmp_path / "release-truth.yaml"
    truth_path.write_text("schema_version: 1\ncurrent: x\nentries:\n", encoding="utf-8")
    with pytest.raises(m.PublishError, match="truth-status"):
        m.update_release_truth(truth_path=truth_path, tag=TAG, status="bogus", notes="x")


# ---------------------------------------------------------------------------
# hashing / sidecar / SHA256SUMS shape
# ---------------------------------------------------------------------------
def test_build_sidecar_shape(tmp_path):
    f = tmp_path / "setup.exe"
    f.write_bytes(b"hello")
    sidecar = m.build_sidecar(f, signed=True)
    assert set(sidecar) == {"sha256", "attestation", "install_manifest"}
    assert sidecar["attestation"] is None
    assert sidecar["install_manifest"] == {"signed": True}
    assert sidecar["sha256"] == m.sha256_file(f)


def test_build_sha256sums_format(tmp_path):
    f1 = tmp_path / "a.ccpack"
    f1.write_bytes(b"aaa")
    f2 = tmp_path / "b.ccpack"
    f2.write_bytes(b"bbb")
    text = m.build_sha256sums([f1, f2])
    lines = text.splitlines()
    assert lines[0] == f"{m.sha256_file(f1)}  a.ccpack"
    assert lines[1] == f"{m.sha256_file(f2)}  b.ccpack"


def test_extract_changelog_unreleased():
    changelog = "# Changelog\n\n## [Unreleased]\n\nSome new stuff.\n\n## [1.0.0-beta.1] - 2026-08-01\n\nOld stuff.\n"
    section = m.extract_changelog_unreleased(changelog)
    assert "Some new stuff." in section
    assert "Old stuff." not in section


def test_extract_changelog_unreleased_missing_section_returns_empty():
    assert m.extract_changelog_unreleased("# Changelog\n\nNo sections here.\n") == ""


def test_asset_naming_constants_are_the_contract():
    """The downloader's NativeCandidate mode pins its literals against these."""
    assert m.SETUP_ASSET_NAME == "setup.exe"
    assert m.SHA256SUMS_ASSET_NAME == "SHA256SUMS.txt"
    assert m.SIDECAR_SUFFIX == ".sidecar.json"
    assert m.PACK_SUFFIX == ".ccpack"


@pytest.mark.parametrize("field", ("setup-signature", "after-health", "manifest", "runtime-health"))
def test_physical_host_refuses_stale_candidate_version_with_valid_hashes(
    tmp_path, monkeypatch, field
):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path, kit_dir, consumer_mode="physical-host", previous_version="1.0.0-beta.11"
    )
    receipt = json.loads(receipt_path.read_text())
    evidence = receipt["evidence"]
    install_ref = evidence["host_install"]["receipt"]
    install_path = Path(install_ref["path"])
    install = json.loads(install_path.read_text())
    stale = "0.0.0-beta.1"
    if field == "setup-signature":
        install["setup_authenticode"]["file_version"] = stale
    elif field == "after-health":
        install["after"]["health"]["version"] = stale
    elif field == "manifest":
        manifest_ref = evidence["host_install"]["installed_manifest"]
        manifest_path = Path(manifest_ref["path"])
        manifest = json.loads(manifest_path.read_text())
        manifest["civiccast"]["version"] = "0.0.0b1"
        manifest_ref.update(_write_bound_json(manifest_path, manifest))
        install["after"]["app_payload_manifest_sha256"] = manifest_ref["sha256"]
    else:
        snapshot_ref = evidence["host_three_channel_runtime"]["snapshots"][0]
        snapshot_path = Path(snapshot_ref["path"])
        snapshot = json.loads(snapshot_path.read_text())
        snapshot["health"]["version"] = stale
        snapshot_ref.update(_write_bound_json(snapshot_path, snapshot))
    install_ref.update(_write_bound_json(install_path, install))
    receipt_path.write_text(json.dumps(receipt))
    fake = _fake_command_factory()
    monkeypatch.setattr(m, "run_command", fake)
    assert m.main(_direct_args(tmp_path, kit_dir, receipt_path, dry_run=False)) == 1
    _assert_no_tag_or_public_release(fake.calls)


@pytest.mark.parametrize(
    "proof,member,field",
    (
        ("fresh_install", "activation_self_test", "product_version"),
        (
            "beta11_to_beta12_interrupted_upgrade",
            "activation_self_test",
            "product_version",
        ),
        ("verify_after_upgrade", "preserve_marker", "product_version"),
        ("three_channel_runtime", "preserve_marker", "product_version"),
    ),
)
def test_sandbox_refuses_stale_candidate_version_with_valid_hashes(
    tmp_path, monkeypatch, proof, member, field
):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(tmp_path, kit_dir)
    receipt = json.loads(receipt_path.read_text())
    reference = receipt["evidence"][proof][member]
    path = Path(reference["path"])
    record = json.loads(path.read_text())
    record[field] = "0.0.0-beta.1"
    reference.update(_write_bound_json(path, record))
    receipt_path.write_text(json.dumps(receipt))
    fake = _fake_command_factory()
    monkeypatch.setattr(m, "run_command", fake)
    assert m.main(_direct_args(tmp_path, kit_dir, receipt_path, dry_run=False)) == 1
    _assert_no_tag_or_public_release(fake.calls)


@pytest.mark.parametrize(
    "kind",
    ("civiccast-native-exact-host-install", f"beta{VERSION.rsplit('.', 1)[-1]}-exact-host-install"),
)
def test_physical_host_current_receipt_kind_preserves_previous_version(tmp_path, monkeypatch, kind):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path, kit_dir, consumer_mode="physical-host", previous_version="1.0.0-beta.11"
    )
    receipt = json.loads(receipt_path.read_text())
    reference = receipt["evidence"]["host_install"]["receipt"]
    path = Path(reference["path"])
    install = json.loads(path.read_text())
    install["kind"] = kind
    reference.update(_write_bound_json(path, install))
    receipt_path.write_text(json.dumps(receipt))
    fake = _fake_command_factory()
    monkeypatch.setattr(m, "run_command", fake)
    assert m.main(_direct_args(tmp_path, kit_dir, receipt_path)) == 0
    _assert_no_tag_or_public_release(fake.calls)


# ---------------------------------------------------------------------------
# Release-candidate (-rc.N) support: PEP 440 identity, oversize runtime-pack
# zip wrapping, per-channel repeat schedules, and release title.
# ---------------------------------------------------------------------------
RC_VERSION = "1.0.0-rc.4"


@pytest.mark.parametrize(
    ("version", "expected"),
    (
        ("1.0.0", "1.0.0"),
        ("1.0.0-beta.12", "1.0.0b12"),
        ("1.0.0-rc.4", "1.0.0rc4"),
        ("2.10.3-rc.11", "2.10.3rc11"),
    ),
)
def test_pep440_version_maps_supported_prerelease_forms(version, expected):
    assert m._pep440_version(version) == expected


@pytest.mark.parametrize(
    "version", ("1.0.0-alpha.1", "1.0.0-rc4", "1.0.0rc4", "v1.0.0-rc.4", "1.0-rc.4", "")
)
def test_pep440_version_refuses_unsupported_forms(version):
    with pytest.raises(m.PublishError):
        m._pep440_version(version)


def _rebind_installed_manifest_version(receipt_path: Path, manifest_version: str) -> None:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    host_install = receipt["evidence"]["host_install"]
    install_path = Path(host_install["receipt"]["path"])
    install = json.loads(install_path.read_text(encoding="utf-8"))
    manifest_ref = host_install["installed_manifest"]
    manifest_path = Path(manifest_ref["path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["civiccast"]["version"] = manifest_version
    manifest_ref.update(_write_bound_json(manifest_path, manifest))
    install["after"]["app_payload_manifest_sha256"] = manifest_ref["sha256"]
    host_install["receipt"].update(_write_bound_json(install_path, install))
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")


@pytest.mark.parametrize(
    ("manifest_version", "accepted"),
    (("1.0.0rc4", True), ("1.0.0-rc.4", False), ("1.0.0rc3", False)),
)
def test_physical_host_rc_candidate_requires_pep440_installed_manifest_version(
    tmp_path, manifest_version, accepted
):
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path,
        kit_dir,
        consumer_mode="physical-host",
        previous_version="1.0.0-rc.3",
        candidate_version=RC_VERSION,
    )
    _rebind_installed_manifest_version(receipt_path, manifest_version)
    kwargs = {
        "receipt_path": receipt_path,
        "kit_dir": kit_dir,
        "artifact_source_sha": DIRECT_SOURCE_SHA,
        "build_run_id": "333",
        "candidate_version": RC_VERSION,
    }
    if accepted:
        _, _, summary = m.verify_consumer_evidence_receipt(**kwargs)
        assert summary["consumer_mode"] == "physical-host"
    else:
        with pytest.raises(m.PublishError, match="installed app payload manifest"):
            m.verify_consumer_evidence_receipt(**kwargs)


def _verify_with_host_states(tmp_path: Path, mutate) -> dict[str, str]:
    kit_dir = tmp_path / "direct-kit"
    receipt_path, _ = _write_direct_consumer_receipt(
        tmp_path, kit_dir, consumer_mode="physical-host", previous_version="1.0.0-beta.11"
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    install_ref = receipt["evidence"]["host_install"]["receipt"]
    install_path = Path(install_ref["path"])
    install = json.loads(install_path.read_text(encoding="utf-8"))
    mutate(install["before"], install["after"])
    install_ref.update(_write_bound_json(install_path, install))
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    _, _, summary = m.verify_consumer_evidence_receipt(
        receipt_path=receipt_path,
        kit_dir=kit_dir,
        artifact_source_sha=DIRECT_SOURCE_SHA,
        build_run_id="333",
        candidate_version=VERSION,
    )
    return summary


_ALL_CHANNELS_REPEAT = {"public": True, "government": True, "education": True}


@pytest.mark.parametrize("global_flag", ("absent", False))
def test_physical_host_accepts_per_channel_repeat_schedules(tmp_path, global_flag):
    def mutate(before, after):
        for state in (before, after):
            if global_flag == "absent":
                state.pop("schedule_loop_enabled")
            else:
                state["schedule_loop_enabled"] = global_flag
            state["channel_repeat_schedules"] = dict(_ALL_CHANNELS_REPEAT)

    assert _verify_with_host_states(tmp_path, mutate)["consumer_mode"] == "physical-host"


def _without(mapping: dict, key: str) -> dict:
    return {k: v for k, v in mapping.items() if k != key}


@pytest.mark.parametrize(
    ("before_schedules", "after_schedules"),
    (
        (_without(_ALL_CHANNELS_REPEAT, "education"), _ALL_CHANNELS_REPEAT),
        (_ALL_CHANNELS_REPEAT, _without(_ALL_CHANNELS_REPEAT, "public")),
        ({**_ALL_CHANNELS_REPEAT, "government": False}, _ALL_CHANNELS_REPEAT),
        (_ALL_CHANNELS_REPEAT, {**_ALL_CHANNELS_REPEAT, "education": False}),
        ({**_ALL_CHANNELS_REPEAT, "public": 1}, _ALL_CHANNELS_REPEAT),
        (_ALL_CHANNELS_REPEAT, {**_ALL_CHANNELS_REPEAT, "public": "true"}),
        ({**_ALL_CHANNELS_REPEAT, "extra": True}, _ALL_CHANNELS_REPEAT),
        (["public", "government", "education"], _ALL_CHANNELS_REPEAT),
        (_ALL_CHANNELS_REPEAT, None),
        (None, _ALL_CHANNELS_REPEAT),
        (None, None),
    ),
)
def test_physical_host_refuses_incomplete_per_channel_repeat_schedules(
    tmp_path, before_schedules, after_schedules
):
    def mutate(before, after):
        for state, schedules in ((before, before_schedules), (after, after_schedules)):
            state.pop("schedule_loop_enabled")
            if schedules is not None:
                state["channel_repeat_schedules"] = schedules

    with pytest.raises(m.PublishError, match="healthy in-place"):
        _verify_with_host_states(tmp_path, mutate)


@pytest.mark.parametrize("side", ("before", "after"))
def test_physical_host_refuses_mixed_global_and_per_channel_proof(tmp_path, side):
    """Each route must hold in BOTH snapshots; one of each is not a proof."""

    def mutate(before, after):
        target = before if side == "before" else after
        target.pop("schedule_loop_enabled")
        target["channel_repeat_schedules"] = dict(_ALL_CHANNELS_REPEAT)

    with pytest.raises(m.PublishError, match="healthy in-place"):
        _verify_with_host_states(tmp_path, mutate)


def _write_oversize_ollama_kit(kit_dir: Path, payload: bytes) -> Path:
    _write_kit(kit_dir)
    pack = kit_dir / "packs" / "native-ollama-runtime.ccpack"
    pack.write_bytes(payload)
    return pack


def test_oversize_runtime_pack_is_staged_as_single_entry_zip(tmp_path, monkeypatch):
    import zipfile

    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "kit"
    limit = 64 * 1024
    pack = _write_oversize_ollama_kit(kit_dir, b"civiccast ollama runtime " * 8192)
    assert pack.stat().st_size >= limit
    monkeypatch.setattr(m, "GITHUB_ASSET_LIMIT_BYTES", limit)
    monkeypatch.setattr(m, "run_command", _fake_command_factory())

    assert m.main(_args(tmp_path, kit_dir, repo_root=repo_root)) == 0

    out_dir = repo_root / "artifacts" / "release" / TAG
    zip_path = out_dir / "assets" / "native-ollama-runtime.ccpack.zip"
    assert zip_path.is_file()
    assert zip_path.stat().st_size < limit
    with zipfile.ZipFile(zip_path) as archive:
        infos = archive.infolist()
        assert [info.filename for info in infos] == ["native-ollama-runtime.ccpack"]
        assert infos[0].file_size == pack.stat().st_size
        assert infos[0].compress_type == zipfile.ZIP_DEFLATED
        assert archive.read(infos[0]) == pack.read_bytes()

    sums = {}
    for line in (out_dir / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines():
        digest, name = line.split("  ", 1)
        sums[name] = digest
    # The downloader verifies the zip asset AND the extracted inner pack.
    assert sums["native-ollama-runtime.ccpack"] == m.sha256_file(pack)
    assert sums["native-ollama-runtime.ccpack.zip"] == m.sha256_file(zip_path)
    assert sums["native-app-payload.ccpack"] == m.sha256_file(
        kit_dir / "packs" / "native-app-payload.ccpack"
    )
    assert "native-app-payload.ccpack.zip" not in sums

    notes = (out_dir / "RELEASE-NOTES.md").read_text(encoding="utf-8")
    assert "native-ollama-runtime.ccpack.zip" in notes
    assert "native-app-payload.ccpack" in notes


def test_runtime_pack_zip_that_stays_oversize_refuses_before_remote_mutation(tmp_path, monkeypatch):
    repo_root = _base_repo_root(tmp_path)
    kit_dir = tmp_path / "kit"
    limit = 64 * 1024
    _write_oversize_ollama_kit(kit_dir, os.urandom(128 * 1024))
    monkeypatch.setattr(m, "GITHUB_ASSET_LIMIT_BYTES", limit)
    fake = _fake_command_factory(mirror_uploaded_assets=True)
    monkeypatch.setattr(m, "run_command", fake)

    assert m.main(_args(tmp_path, kit_dir, dry_run=False, repo_root=repo_root)) == 1
    assert not _gh_calls(fake.calls, "gh", "release")
    _assert_no_tag_or_public_release(fake.calls)


@pytest.mark.parametrize(
    ("tag", "title"),
    (
        ("v1.0.0-rc.4", "CivicCast v1.0.0-rc.4 (Release Candidate)"),
        ("v1.0.0-beta.12", "CivicCast v1.0.0-beta.12 (Beta Candidate)"),
    ),
)
def test_release_title_names_release_candidates(tag, title):
    assert m.release_title(tag) == title
