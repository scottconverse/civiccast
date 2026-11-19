# SPDX-License-Identifier: Apache-2.0
"""Fail-closed contract for the native-beta Windows pull-request gate."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from scripts.policy.check_actions_budget import validate_workflow

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / ".github" / "workflows" / "ci-blob-size-guard.yml"
INSTALLER = ROOT / ".github" / "workflows" / "ci-installer-compile.yml"
LINT = ROOT / ".github" / "workflows" / "ci-lint.yml"
BASE_BRANCH = "release/native-beta-1.0.0-beta.1-rc1"
SOURCE_SHA = "${{ github.event.pull_request.head.sha }}"
CHECKOUT_REF = "${{ inputs.source_sha || github.event.pull_request.head.sha || github.sha }}"
FRONTEND_RUNNER = "${{ inputs.native_beta_windows_only && 'windows-latest' || 'ubuntu-24.04' }}"
REQUIRED_GATE_JOBS = ["native-beta-pack-contract", "native-beta-installer"]
PACK_TESTS = (
    "tests/native",
    "tests/installer/test_native_packs.py",
    "tests/installer/test_native_distribution.py",
    "tests/installer/test_native_distribution_builder.py",
    "tests/installer/test_stage_native_server_pack.py",
    "tests/policy/test_native_beta_candidate_workflow.py",
    "tests/test_collect_source_state.py",
    "tests/policy/test_native_beta_pr_gate_workflow.py",
)


def _workflow(path: Path) -> tuple[str, dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    return text, yaml.load(text, Loader=yaml.BaseLoader)


def _triggers(workflow: dict[str, object]) -> dict[str, object]:
    return workflow.get("on", workflow.get(True))


def _steps(job: dict[str, object]) -> dict[str, dict[str, object]]:
    return {step["name"]: step for step in job["steps"]}


def _assert_windows_release_gate(workflow: dict[str, object]) -> None:
    jobs = workflow["jobs"]
    release_if = "github.event.pull_request.base.ref == 'release/native-beta-1.0.0-beta.1-rc1'"

    assert "blob-size" in jobs
    assert jobs["blob-size"]["runs-on"] == "ubuntu-latest"
    assert "native-beta-lint" not in jobs

    pack = jobs["native-beta-pack-contract"]
    assert pack["if"].strip() == release_if
    assert pack["runs-on"] == "windows-latest"
    pack_steps = _steps(pack)
    assert "Install Linux media prerequisites" not in pack_steps
    assert pack_steps["Set up Python"]["with"]["python-version"] == "3.12"
    assert pack_steps["Install uv"]["uses"] == "astral-sh/setup-uv@v8.1.0"
    assert pack_steps["Install project (dev)"]["run"] == "uv sync --group dev"
    pack_run = pack_steps["Run exact native beta pack contract tests"]["run"]
    for required in PACK_TESTS:
        assert required in pack_run
    # <installer-path-audit MA-39> The blanket `-m ` ban is narrowed to the
    # filter it was actually written to forbid.
    #
    # This is the WINDOWS job, and its point is that windows_only tests really
    # run here -- which is why "not windows_only" is forbidden. The bare "-m "
    # caught EVERY marker expression, including the one this audit requires:
    # `-m "not integration"`, which makes the job's existing behaviour
    # EXPLICIT. tests/native/test_upgrade_engine_postgres.py -- the only test
    # in this repository that crosses a real version boundary through the real
    # production seam bundle -- was ALREADY being skipped here, silently,
    # because windows-latest cannot run this project's Linux Postgres
    # container. The filter removes no coverage; it names a gap that was
    # invisible. Where the lane actually runs fail-closed is pinned by
    # test_ma39_the_ubuntu_job_still_runs_the_integration_lane_fail_closed.
    for forbidden in ("apt-get", "gstreamer1.0", "not windows_only", "wsl"):
        assert forbidden not in pack_run.lower()
    markers = re.findall(r'-m\s+"([^"]+)"', pack_run)
    assert markers == ["not integration"], (
        "the only marker filter this Windows job may carry is the explicit "
        f"integration exclusion; found {markers}"
    )

    installer = jobs["native-beta-installer"]
    assert installer["if"].strip() == release_if
    assert installer["uses"] == "./.github/workflows/ci-installer-compile.yml"
    assert installer["with"] == {
        "native_beta_windows_only": "true",
        "source_sha": SOURCE_SHA,
    }

    aggregate = jobs["native-beta-pr-gate"]
    assert aggregate["name"] == "Native beta Windows PR gate"
    assert BASE_BRANCH in aggregate["if"]
    assert "always()" in aggregate["if"]
    assert aggregate["needs"] == REQUIRED_GATE_JOBS
    assert aggregate["runs-on"] == "windows-latest"
    aggregate_step = aggregate["steps"][0]
    assert aggregate_step["shell"] == "pwsh"
    run = aggregate_step["run"]
    for required in REQUIRED_GATE_JOBS:
        assert f"needs.{required}.result" in run
    for forbidden in ("needs.blob-size.result", "needs.native-beta-lint.result"):
        assert forbidden not in run
    assert '-ne "success"' in run


def _assert_installer_modes(workflow: dict[str, object]) -> None:
    triggers = _triggers(workflow)
    assert {"pull_request", "workflow_call"} <= set(triggers)
    assert triggers["pull_request"]["branches"] == ["main"]
    inputs = triggers["workflow_call"]["inputs"]
    assert inputs["native_beta_windows_only"] == {
        "description": "Run the native-beta frontend contract on Windows without browser E2E.",
        "default": "false",
        "required": "false",
        "type": "boolean",
    }
    assert inputs["source_sha"]["type"] == "string"

    jobs = workflow["jobs"]
    assert set(jobs) == {"installer-compile", "installer-frontend"}
    assert jobs["installer-compile"]["runs-on"] == "windows-latest"
    compile_steps = _steps(jobs["installer-compile"])
    assert compile_steps["cargo test (compile guard + unit tests)"]["run"] == (
        "cargo test --locked"
    )
    # The Pester step is gone with headless-bootstrap.ps1 and its suite (the
    # WSL2 install lane). This assertion referenced the STEP NAME, not the
    # file, so grepping for HeadlessBootstrap before removing the step did
    # not surface it -- the same one-form-of-the-reference miss as the e2e
    # heading rename earlier. cargo test above still guards the job.

    frontend = jobs["installer-frontend"]
    assert frontend["runs-on"] == FRONTEND_RUNNER
    frontend_steps = _steps(frontend)
    assert frontend_steps["Checkout"]["with"]["ref"] == CHECKOUT_REF
    assert frontend_steps["Install dependencies"]["run"] == "npm ci"
    assert frontend_steps["Unit tests (vitest)"]["run"] == "npm run test:unit"
    assert "Production build (native beta Windows)" in frontend_steps
    assert frontend_steps["Production build (native beta Windows)"] == {
        "name": "Production build (native beta Windows)",
        "if": "inputs.native_beta_windows_only",
        "run": "npm run build",
    }

    playwright_install = frontend_steps["Install Playwright browser"]
    playwright_e2e = frontend_steps["End-to-end tests (Playwright, mocked Tauri bridge)"]
    for step in (playwright_install, playwright_e2e):
        assert "if" in step
        assert step["if"] == "inputs.native_beta_windows_only != true"
    assert playwright_install["run"] == "npx playwright install --with-deps chromium"
    assert playwright_e2e["run"] == "npm run test:e2e"

    native_steps = [
        step
        for step in frontend["steps"]
        if "if" not in step or step["if"] == "inputs.native_beta_windows_only"
    ]
    native_commands = "\n".join(str(step.get("run", "")) for step in native_steps)
    assert "npm ci" in native_commands
    assert "npm run test:unit" in native_commands
    assert "npm run build" in native_commands
    assert "playwright" not in native_commands.lower()
    assert "test:e2e" not in native_commands
    assert "wsl" not in native_commands.lower()


def test_host_is_unfiltered_but_release_context_is_windows_only() -> None:
    text, workflow = _workflow(HOST)
    triggers = _triggers(workflow)

    assert triggers == {"pull_request": ""}
    assert "branches:" not in text.split("concurrency:", maxsplit=1)[0]
    _assert_windows_release_gate(workflow)


def test_installer_reusable_supports_native_windows_and_preserves_direct_main() -> None:
    _text, workflow = _workflow(INSTALLER)
    _assert_installer_modes(workflow)


def test_ci_lint_is_standard_main_only_without_release_reuse() -> None:
    text, workflow = _workflow(LINT)
    triggers = _triggers(workflow)

    # PR #369: agent-tooling-only changes (.claude/, vlite.json) start no lint
    # run. The filter must stay exactly this narrow; docs still run here.
    assert triggers == {
        "pull_request": {
            "paths-ignore": [".claude/**", "**/.claude/**", "vlite.json"],
            "branches": ["main"],
        }
    }
    assert "workflow_call" not in text
    assert "lint_scope" not in text
    assert "source_sha" not in text
    assert workflow["concurrency"]["group"] == "${{ github.workflow }}-${{ github.ref }}"
    assert set(workflow["jobs"]) == {"lint", "workflows"}
    # Was: every ci-lint job on ubuntu-latest. The lint job moved to Windows
    # deliberately -- mypy's answer is platform-dependent and only the
    # Windows one is actionable for a Windows product. The workflows job
    # stays on ubuntu: it lints workflow TEXT and fetches actionlint
    # through process substitution. Pin the split rather than dropping the
    # check, so neither job drifts platform silently.
    assert workflow["jobs"]["lint"]["runs-on"] == "windows-latest"
    assert workflow["jobs"]["workflows"]["runs-on"] == "ubuntu-latest"


def test_mutations_reject_linux_jobs_and_mixed_gate_dependencies() -> None:
    text, _workflow_data = _workflow(HOST)
    linux_pack = text.replace("runs-on: windows-latest", "runs-on: ubuntu-latest", 1)
    assert linux_pack != text
    with pytest.raises(AssertionError):
        _assert_windows_release_gate(yaml.load(linux_pack, Loader=yaml.BaseLoader))

    linux_marker = text.replace(
        "uv run pytest -q tests/native", 'uv run pytest -q -m "not windows_only" tests/native', 1
    )
    assert linux_marker != text
    with pytest.raises(AssertionError):
        _assert_windows_release_gate(yaml.load(linux_marker, Loader=yaml.BaseLoader))

    mixed_needs = text.replace(
        "needs: [native-beta-pack-contract, native-beta-installer]",
        "needs: [blob-size, native-beta-pack-contract, native-beta-installer]",
        1,
    )
    assert mixed_needs != text
    with pytest.raises(AssertionError):
        _assert_windows_release_gate(yaml.load(mixed_needs, Loader=yaml.BaseLoader))


def test_mutations_reject_missing_native_mode_and_browser_leakage() -> None:
    host_text, _workflow_data = _workflow(HOST)
    missing_mode = host_text.replace("      native_beta_windows_only: true\n", "", 1)
    assert missing_mode != host_text
    with pytest.raises(AssertionError):
        _assert_windows_release_gate(yaml.load(missing_mode, Loader=yaml.BaseLoader))

    installer_text, _workflow_data = _workflow(INSTALLER)
    browser_leak = installer_text.replace(
        "        if: inputs.native_beta_windows_only != true\n", "", 1
    )
    assert browser_leak != installer_text
    with pytest.raises(AssertionError):
        _assert_installer_modes(yaml.load(browser_leak, Loader=yaml.BaseLoader))

    missing_build = installer_text.replace(
        "      - name: Production build (native beta Windows)",
        "      - name: Production build removed",
        1,
    )
    assert missing_build != installer_text
    with pytest.raises(AssertionError):
        _assert_installer_modes(yaml.load(missing_build, Loader=yaml.BaseLoader))


def test_gate_excludes_release_lint_and_signing_authority() -> None:
    text, _workflow_data = _workflow(HOST)
    for forbidden in (
        "native-beta-lint:",
        "ci-lint.yml",
        "ci-test.yml",
        "native-beta-candidate-artifacts.yml",
        "check_claims_evidence.py",
        "CIVICCAST_PACK_SIGNING_PRIVATE_KEY",
        "secrets.",
    ):
        assert forbidden not in text


def test_changed_workflows_remain_valid_under_actions_budget_policy() -> None:
    for path in (HOST, INSTALLER, LINT):
        text, _workflow_data = _workflow(path)
        assert validate_workflow(path, text) == []


def test_ma39_the_release_branch_job_names_its_integration_exclusion() -> None:
    """<installer-path-audit MA-39> On release-branch PRs -- the ones that
    matter most -- this job ran ``pytest -q tests/native`` on windows-latest
    with no marker filter, no Docker, no ``CIVICCAST_RUN_POSTGRES_TESTS`` and
    no junit floor. So ``tests/native/test_upgrade_engine_postgres.py``, the
    ONLY test in this repository that crosses a real version boundary through
    the real production seam bundle, silently SKIPPED.

    It cannot run here (windows-latest cannot run this project's Linux
    Postgres container), so the fix is to say so rather than to appear to run
    it. This pins the saying-so.
    """
    text, _workflow_data = _workflow(HOST)
    assert 'uv run pytest -q tests/native -m "not integration"' in text, (
        "the release-branch native job must EXCLUDE the integration lane "
        "explicitly, not skip it by accident"
    )
    assert "MA-39" in text, "the exclusion must carry the finding it answers"
    # It must point at the job that DOES run the lane, or the exclusion reads
    # as a gap nobody covers. It cannot name that workflow's FILE, though:
    # test_gate_excludes_release_lint_and_signing_authority forbids that
    # literal here on purpose (this gate must not appear to depend on the
    # release lint/test authority), so the reference is by job name.
    assert 'ubuntu "Unit tests" job' in text
    assert "ci-test.yml" not in text, (
        "the release gate must not name the release test workflow's file -- see "
        "test_gate_excludes_release_lint_and_signing_authority"
    )


def test_ma39_the_ubuntu_job_still_runs_the_integration_lane_fail_closed() -> None:
    """The other half: naming the boundary is only honest if the lane really
    is covered somewhere. ci-test.yml's ubuntu 'Unit tests' job runs that
    module non-skipped with a real Postgres, fail-closed three ways."""
    ci_test = ROOT / ".github" / "workflows" / "ci-test.yml"
    text = ci_test.read_text(encoding="utf-8")
    assert 'CIVICCAST_RUN_POSTGRES_TESTS: "1"' in text, (
        "the job-level flag is what turns the module's own skip into a pytest.fail"
    )
    assert "Probe Docker" in text, "an unreachable Docker socket must fail the job"
    assert "verify_native_junit_floor.py" in text, (
        "and the junit floor must reject any skipped/failed/errored tests.native case"
    )


def test_ma38_the_restore_drill_floor_is_derived_not_typed() -> None:
    """<installer-path-audit MA-38> The floor was the literal ``20`` while
    ``grep -c "^def test_"`` returned 21: PR #143 added the file's ONLY proof
    of its own fix and did not bump it, so that test could be skipped or
    deleted with the guard still green.

    Deriving the floor from the source closes the class rather than the
    instance -- a stale floor becomes impossible by construction.
    """
    ci_test = ROOT / ".github" / "workflows" / "ci-test.yml"
    text = ci_test.read_text(encoding="utf-8")
    assert "floor = 20" not in text, "the hardcoded floor must be gone"
    assert 'floor = len(re.findall(r"^def test_", source, re.MULTILINE))' in text
    assert "MA-38" in text


def test_actions_budget_rejects_unreviewed_static_concurrency_identity() -> None:
    text, _workflow_data = _workflow(INSTALLER)
    mutated = text.replace(
        "group: ci-installer-compile-reusable-${{ github.ref }}",
        "group: arbitrary-reusable-${{ github.ref }}",
        1,
    )
    assert mutated != text
    violations = validate_workflow(INSTALLER, mutated)
    assert "missing required concurrency block with cancel-in-progress: true" in violations
