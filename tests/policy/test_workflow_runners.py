# SPDX-License-Identifier: Apache-2.0
"""Runner policy tests.

Scott's directive (2026-06-12, repo public): GitHub-hosted runners
everywhere; self-hosted labels only for the explicit hardware/duration
allowlist (GPU proofs, the local Hyper-V cleanroom, the >6h soak).
"""

import ast
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from civiccast.native.app_payload import INTERPRETER_SHA256, INTERPRETER_ZIP_BYTES
from scripts.build_native_pyav_wheel import EXPECTED_WHEEL_BYTES, EXPECTED_WHEEL_SHA256
from scripts.policy.check_workflow_runners import (
    SELF_HOSTED_ALLOWLIST,
    check_workflow_runners,
    validate_workflow,
)


def test_existing_repo_workflows_use_allowed_runner_labels() -> None:
    assert check_workflow_runners() == []


def test_hosted_runner_labels_pass(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "good.yml"
    workflow.parent.mkdir(parents=True)
    for label in ("ubuntu-latest", "windows-latest", "macos-latest", "ubuntu-24.04"):
        workflow.write_text(
            f"""
name: good
jobs:
  test:
    runs-on: {label}
    steps: []
""",
            encoding="utf-8",
        )
        assert validate_workflow(workflow, workflow.read_text(encoding="utf-8")) == []


def test_self_hosted_fails_with_directive_message(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "bad.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        """
name: bad
jobs:
  test:
    runs-on: [self-hosted, linux, x64, scott-desktop]
    steps: []
""",
        encoding="utf-8",
    )

    violations = validate_workflow(workflow, workflow.read_text(encoding="utf-8"))

    assert violations
    assert "self-hosted" in violations[0]
    assert "hosted-runners directive" in violations[0]
    assert "ubuntu-latest" in violations[0]


def test_allowlisted_hardware_lanes_may_stay_self_hosted(tmp_path: Path) -> None:
    parent = tmp_path / ".github" / "workflows"
    parent.mkdir(parents=True)
    text = """
name: gpu
jobs:
  proof:
    runs-on: [self-hosted, Linux, X64, scott-desktop, rtx5070, ubuntu-2404]
    steps: []
"""
    for name in SELF_HOSTED_ALLOWLIST:
        allowed = parent / name
        assert validate_workflow(allowed, text) == [], name

    ordinary = parent / "ordinary.yml"
    violations = validate_workflow(ordinary, text)
    assert violations
    assert "self-hosted" in violations[0]


def test_every_allowlist_entry_carries_a_reason() -> None:
    # The allowlist is an exception ledger, not a loophole: each entry must
    # say WHY hosted runners cannot serve it.
    for name, reason in SELF_HOSTED_ALLOWLIST.items():
        assert name.endswith(".yml")
        assert reason.strip(), name


def test_matrix_runner_expressions_are_flagged(tmp_path: Path) -> None:
    workflow = tmp_path / ".github" / "workflows" / "matrix.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        """
name: matrix
jobs:
  test:
    runs-on: ${{ matrix.os }}
    steps: []
""",
        encoding="utf-8",
    )

    violations = validate_workflow(workflow, workflow.read_text(encoding="utf-8"))

    assert violations
    assert "matrix runner target" in violations[0]


def test_native_beta_hosted_selector_is_allowed_only_for_installer_workflow(
    tmp_path: Path,
) -> None:
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    text = """
name: installer
jobs:
  frontend:
    runs-on: ${{ inputs.native_beta_windows_only && 'windows-latest' || 'ubuntu-24.04' }}
    steps: []
"""

    installer = workflow_dir / "ci-installer-compile.yml"
    assert validate_workflow(installer, text) == []

    ordinary = workflow_dir / "ordinary.yml"
    violations = validate_workflow(ordinary, text)
    assert violations
    assert "matrix runner target" in violations[0]


def test_reviewed_input_transfer_is_source_free_internal_and_bounded() -> None:
    workflow = yaml.load(
        Path(".github/workflows/native-app-reproducibility.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    stage = workflow["jobs"]["stage-reviewed-inputs"]
    assert stage["if"] == (
        "github.event_name == 'workflow_dispatch' || "
        "(github.event_name == 'pull_request' && "
        "github.event.pull_request.head.repo.full_name == github.repository)"
    )
    assert stage["concurrency"] == {"group": "sandbox-lab", "cancel-in-progress": "false"}
    assert stage["timeout-minutes"] == "10"
    assert len(stage["steps"]) == 2
    copy, upload = stage["steps"]
    assert "uses" not in copy  # No checkout or repository script execution internally.
    assert "python" not in copy["run"].replace(
        "python-3.12.16-expat-2.10.0-civiccast-embed-amd64.zip", ""
    )
    for value in (
        str(INTERPRETER_ZIP_BYTES),
        INTERPRETER_SHA256,
        str(EXPECTED_WHEEL_BYTES),
        EXPECTED_WHEEL_SHA256,
    ):
        assert value in copy["run"]
    assert "Get-FileHash" in copy["run"]
    assert "Copy-Item -LiteralPath $source -Destination $target" in copy["run"]
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["with"]["retention-days"] == "1"
    repeat = workflow["jobs"]["repeat-build"]
    assert repeat["needs"] == "stage-reviewed-inputs"
    assert repeat["runs-on"] == "windows-latest"
    steps = {step["name"]: step for step in repeat["steps"]}
    verify = steps["Recheck source pins and embedded provenance on hosted runner"]["run"]
    assert "PROVENANCE_SHA256" in verify
    assert "pyav['ffmpeg_provenance']()" in verify
    embedded_python = verify.split("@'\n", 1)[1].split("\n'@", 1)[0]
    compile(embedded_python, "hosted-reviewed-input-verification", "exec")
    proof = steps["Build twice and require byte equality"]["run"]
    assert '--interpreter-zip "$env:CIVICCAST_INTERPRETER_ZIP"' in proof
    assert '--reviewed-pyav-wheel "$env:CIVICCAST_REVIEWED_PYAV_WHEEL"' in proof
    assert "--workspace-a workspace-a" in proof and "--workspace-b workspace-b" in proof
    assert "advisory" not in proof


def test_reviewed_input_runner_expression_does_not_allow_other_workflows(tmp_path: Path) -> None:
    text = 'runs-on: ${{ fromJSON(\'["self-hosted","windows","sandbox-lab"]\') }}'
    assert validate_workflow(tmp_path / "native-app-reproducibility.yml", text) == []
    assert validate_workflow(tmp_path / "ci-test.yml", text) == []
    assert validate_workflow(tmp_path / "ordinary.yml", text)


def _ci_runtime_transfer() -> tuple[dict, dict, str, tuple]:
    workflow = yaml.load(
        Path(".github/workflows/ci-test.yml").read_text(encoding="utf-8"), Loader=yaml.BaseLoader
    )
    windows = workflow["jobs"]["windows-native"]
    verification = next(
        step["run"]
        for step in windows["steps"]
        if step["name"] == "Recheck reviewed native runtime archives against source locks"
    )
    code = verification.split("@'\n", 1)[1].split("\n'@", 1)[0]
    reviewed = next(
        ast.literal_eval(node.value)
        for node in ast.parse(code).body
        if isinstance(node, ast.Assign) and node.targets[0].id == "reviewed"
    )
    return workflow, windows, code, reviewed


def test_ci_native_runtime_transfer_is_source_free_internal_and_locked() -> None:
    workflow, windows, code, reviewed = _ci_runtime_transfer()
    stage = workflow["jobs"]["stage-native-runtime-inputs"]
    assert stage["if"] == (
        "github.event_name == 'pull_request' && "
        "github.event.pull_request.head.repo.full_name == github.repository"
    )
    assert stage["timeout-minutes"] == "10"
    assert stage["concurrency"] == {"group": "sandbox-lab", "cancel-in-progress": "false"}
    copy, upload = stage["steps"]
    assert "uses" not in copy
    assert "python" not in copy["run"] and "checkout" not in copy["run"]
    assert "Copy-Item -LiteralPath $source -Destination $target" in copy["run"]
    assert "Get-FileHash" in copy["run"]
    assert len(reviewed) == 4
    for lockfile, filename, variable in reviewed:
        pin = json.loads(Path(lockfile).read_text(encoding="utf-8"))["archive"]
        assert filename == pin.get("filename", pin.get("name"))
        for value in (filename, str(pin["bytes"]), pin["sha256"]):
            assert value in copy["run"]
        assert filename in upload["with"]["path"]
        assert variable in code
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["with"]["retention-days"] == "1"
    assert upload["with"]["if-no-files-found"] == "error"
    assert windows["needs"] == "stage-native-runtime-inputs"
    assert windows["runs-on"] == "windows-latest"
    steps = windows["steps"]
    names = [step["name"] for step in steps]
    assert names.index(
        "Recheck reviewed native runtime archives against source locks"
    ) < names.index("Provision pinned GStreamer closure, TSDuck and NSIS test tools")
    download = next(step for step in steps if step.get("uses") == "actions/download-artifact@v4")
    assert download["with"]["name"] == upload["with"]["name"]
    assert workflow["jobs"]["test"].get("needs") is None


def test_ci_hosted_archive_verification_rejects_tampering_before_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, code, reviewed = _ci_runtime_transfer()
    root = Path.cwd()
    inputs = tmp_path / "native-runtime-reviewed-inputs"
    inputs.mkdir()
    for lockfile, filename, _ in reviewed:
        lock = json.loads((root / lockfile).read_text(encoding="utf-8"))
        data = filename.encode()
        lock["archive"]["bytes"] = len(data)
        lock["archive"]["sha256"] = hashlib.sha256(data).hexdigest()
        (tmp_path / lockfile).write_text(json.dumps(lock), encoding="utf-8")
        (inputs / filename).write_bytes(data)
    monkeypatch.chdir(tmp_path)
    environment = tmp_path / "good.env"
    monkeypatch.setenv("GITHUB_ENV", str(environment))
    exec(compile(code, "ci-hosted-reviewed-input-verification", "exec"), {})
    exported = environment.read_text(encoding="utf-8")
    assert all(f"{variable}=" in exported for _, _, variable in reviewed)
    # Same length tampering must fail the hash check before any env vars are exported.
    archive = inputs / reviewed[-1][1]
    archive.write_bytes(b"x" * archive.stat().st_size)
    rejected = tmp_path / "rejected.env"
    monkeypatch.setenv("GITHUB_ENV", str(rejected))
    with pytest.raises(AssertionError, match="identity mismatch"):
        exec(compile(code, "ci-hosted-reviewed-input-verification", "exec"), {})
    assert not rejected.exists()
