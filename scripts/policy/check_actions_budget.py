#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Validate GitHub Actions workflow-cost discipline.

By default this check inspects workflow files changed in the current working
tree. In pipeline mode (``--run``), it also compares the current HEAD against
the branch upstream or an explicit base ref so committed workflow changes
cannot silently bypass the budget gate.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

WORKFLOW_RE = re.compile(r"^\.github/workflows/.*\.ya?ml$")
CRON_RE = re.compile(r"cron:\s*['\"]([^'\"]+)['\"]")
UPLOAD_RE = re.compile(r"uses:\s*actions/upload-artifact@")
HEAVY_MARKERS = (
    "apt-get install",
    "docker build",
    "docker/build-push-action",
    "install browsers",
    "playwright install",
    "setup-texlive",
    "texlive",
    "ollama pull",
    "cleanroom",
    "e2e",
)
GLOBAL_COVERAGE_GATES = {"ci-test.yml", "deterministic-detectors.yml"}
RELEASE_CANDIDATE_WORKFLOWS = {"native-beta-candidate-artifacts.yml"}
STATIC_CONCURRENCY_GROUPS = {
    "ci-installer-compile.yml": "ci-installer-compile-reusable-${{ github.ref }}"
}
# Workflows exempt from this check's two default-budget rules (the
# `${{ github.workflow }}-${{ github.ref }}` + cancel-in-progress: true
# concurrency shape, and the 1-day artifact retention cap) for a documented,
# reviewed reason that is NOT "budget neglect" -- each reason is also stated
# in the workflow's own header/step comments, this is not the only place it
# lives.
DOCUMENTED_BUDGET_EXCEPTIONS: dict[str, str] = {
    "gate-a-station-acceptance.yml": (
        "Gate A intentionally sets cancel-in-progress: false: a queued Gate A "
        "run must never be killed mid-soak by a newer candidate arriving, or "
        "the interrupted run's hours of station-acceptance work are lost for "
        "nothing. Its own concurrency group ('sandbox-lab', shared with "
        "native-beta-candidate-artifacts.yml's self-hosted build lane so the "
        "two never contend for the one physical box) is deliberately NOT the "
        "per-ref '${{ github.workflow }}-${{ github.ref }}' shape this check "
        "otherwise requires, for the same reason. Its verdict/evidence "
        "artifacts are also kept 90/14 days, not this check's 1-day default: "
        "a FAIL needs its evidence preserved for debugging at least as long "
        "as a PASS's, and both predate the 2026-08-20 storage-budget "
        "incident this check's 1-day default was written to prevent (see "
        "this check's own retention-days comment) without ever contributing "
        "to it -- Gate A's own artifacts are small (a JSON verdict + logs), "
        "not the multi-GB station-bundle/kit artifacts that incident was "
        "about."
    ),
    "publish-staged-kit.yml": (
        "2026-08-26: shares Gate A's 'sandbox-lab' concurrency "
        "group with cancel-in-progress: false rather than this check's "
        "default per-ref '${{ github.workflow }}-${{ github.ref }}' + "
        "cancel-in-progress: true shape, for the identical hardware-"
        "contention reason -- this workflow, gate-a-station-acceptance.yml, "
        "and native-beta-candidate-artifacts.yml's self-hosted lane all run "
        "on the ONE physical sandbox-lab box, and a newer dispatch must "
        "queue behind an in-flight ~25 GB artifact upload rather than "
        "cancel it partway through (a cancelled multi-hour upload cannot "
        "resume, it restarts from zero). Its upload-artifact step also uses "
        "retention-days: 3, not this check's 1-day default: it is a "
        "one-shot operator-invoked publish of a kit that already passed "
        "Gate A, meant to give a remote fleet tester a real window to "
        "start the download, not a routine CI artifact regenerated on "
        "every push."
    ),
}


def _repo_root() -> Path:
    proc = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return Path(proc.stdout.strip())
    script_dir = Path(__file__).resolve().parent
    if script_dir.name == "policy" and script_dir.parent.name == "scripts":
        return script_dir.parents[1]
    return script_dir.parent


REPO_ROOT = _repo_root()


def _git_status_paths() -> list[Path]:
    proc = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return []

    paths: list[Path] = []
    for line in proc.stdout.splitlines():
        raw = line[3:].strip()
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1].strip()
        raw = raw.strip('"')
        normalized = raw.replace("\\", "/")
        if WORKFLOW_RE.match(normalized):
            paths.append(REPO_ROOT / raw)
    return paths


def _git_diff_paths(base_ref: str) -> list[Path]:
    proc = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        proc = subprocess.run(
            ["git", "diff", "--name-only", base_ref, "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    if proc.returncode != 0:
        return []

    paths: list[Path] = []
    for raw in proc.stdout.splitlines():
        normalized = raw.strip().replace("\\", "/")
        if WORKFLOW_RE.match(normalized):
            paths.append(REPO_ROOT / normalized)
    return paths


def _discover_base_ref(explicit_base: str | None) -> str | None:
    if explicit_base:
        return explicit_base

    for args in (
        ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
        ["rev-parse", "--verify", "origin/main"],
    ):
        proc = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout.strip()
    return None


def _changed_workflows_for_run(base_ref: str | None) -> tuple[list[Path], str | None]:
    discovered = _discover_base_ref(base_ref)
    paths = _git_status_paths()
    if discovered:
        paths.extend(_git_diff_paths(discovered))
    return sorted(set(paths)), discovered


def _all_workflows() -> list[Path]:
    workflow_dir = REPO_ROOT / ".github" / "workflows"
    if not workflow_dir.exists():
        return []
    return sorted(
        path
        for path in workflow_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".yml", ".yaml"}
    )


def _has_pr_trigger(text: str) -> bool:
    return bool(re.search(r"(?m)^\s*pull_request\s*:", text))


def _has_push_main(text: str) -> bool:
    push_match = re.search(r"(?m)^  push\s*:", text)
    if not push_match:
        return False
    next_top_level = re.search(r"(?m)^  [a-zA-Z_][a-zA-Z0-9_-]*\s*:", text[push_match.end() :])
    push_body_end = push_match.end() + next_top_level.start() if next_top_level else len(text)
    push_body = text[push_match.end() : push_body_end]
    return bool(
        re.search(r"branches:\s*\[\s*main\s*\]", push_body)
        or re.search(r"(?m)^\s*-\s*main\s*$", push_body)
    )


def _is_release_or_tag_workflow(path: Path, text: str) -> bool:
    name = path.name.lower()
    has_tag_trigger = bool(
        re.search(r"(?m)^\s*tags\s*:", text) or re.search(r"(?m)^\s*-\s*['\"]?v?\*['\"]?\s*$", text)
    )
    return (
        path.name in RELEASE_CANDIDATE_WORKFLOWS
        or "release" in name
        or "tag" in name
        or has_tag_trigger
    )


def _has_concurrency(path: Path, text: str) -> bool:
    static_group = STATIC_CONCURRENCY_GROUPS.get(path.name)
    return (
        "concurrency:" in text
        and (
            "group: ${{ github.workflow }}-${{ github.ref }}" in text
            or (static_group is not None and f"group: {static_group}" in text)
        )
        and re.search(r"cancel-in-progress:\s*true", text) is not None
    )


def _is_daily_cron(expr: str) -> bool:
    fields = expr.split()
    if len(fields) != 5:
        return False
    return fields[2] == "*" and fields[3] == "*" and fields[4] == "*"


def _has_heavy_marker(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in HEAVY_MARKERS)


def _has_cache(text: str) -> bool:
    lowered = text.lower()
    return (
        "actions/cache@" in lowered
        or ("astral-sh/setup-uv@" in lowered and "enable-cache: true" in lowered)
        or "rust-cache@" in lowered
        or "cache: pip" in lowered
        or "cache: npm" in lowered
        or "cache-from:" in lowered
        or "cache-to:" in lowered
        or "workflow-cost: local-docker-cache" in lowered
    )


def _has_python_pr_matrix(text: str) -> bool:
    if not _has_pr_trigger(text) or "python-version" not in text:
        return False
    matrix_block = re.search(r"matrix:\s*(?P<body>.*?)(?=^\S|\Z)", text, re.M | re.S)
    if not matrix_block:
        return False
    body = matrix_block.group("body")
    return "python-version" in body and (
        "[" in body or "3.11" in body or "3.13" in body or len(re.findall(r"3\.\d+", body)) > 1
    )


def _artifact_blocks(text: str) -> list[str]:
    lines = text.splitlines()
    blocks: list[str] = []
    for index, line in enumerate(lines):
        if not UPLOAD_RE.search(line):
            continue
        block = [line]
        base_indent = len(line) - len(line.lstrip())
        for later in lines[index + 1 :]:
            indent = len(later) - len(later.lstrip())
            if later.lstrip().startswith("- ") and indent <= base_indent:
                break
            block.append(later)
        blocks.append("\n".join(block))
    return blocks


def validate_workflow(path: Path, text: str) -> list[str]:
    violations: list[str] = []
    release_or_tag = _is_release_or_tag_workflow(path, text)
    # Exempts only the two default-budget rules below (concurrency shape,
    # artifact retention) -- every other rule in this function (cron, heavy
    # markers, PR matrix, macOS/Windows-on-PR) still applies in full even to
    # a documented-exception workflow.
    budget_exempt = release_or_tag or path.name in DOCUMENTED_BUDGET_EXCEPTIONS
    pr_trigger = _has_pr_trigger(text)

    if "@daily" in text:
        violations.append("daily cron is forbidden without explicit Scott approval")
    for expr in CRON_RE.findall(text):
        if _is_daily_cron(expr):
            violations.append(f"daily cron `{expr}` is forbidden; weekly is the maximum default")

    if not budget_exempt and not _has_concurrency(path, text):
        violations.append("missing required concurrency block with cancel-in-progress: true")

    if pr_trigger and _has_push_main(text):
        violations.append(
            "duplicates pull_request main and push main for the same validation workflow"
        )

    if _has_heavy_marker(text):
        if "paths:" not in text and path.name not in GLOBAL_COVERAGE_GATES:
            violations.append("heavy workflow is missing paths filters")
        if not _has_cache(text):
            violations.append(
                "heavy workflow is missing cache coverage for expensive installs/downloads"
            )

    if pr_trigger and "macos-latest" in text:
        violations.append(
            "macOS jobs are forbidden on PR-fired workflows without explicit Scott approval"
        )

    if (
        pr_trigger
        and "windows-latest" in text
        and "workflow-cost: windows-pr-justification" not in text
    ):
        violations.append(
            "Windows PR jobs require workflow-cost: windows-pr-justification evidence"
        )

    if _has_python_pr_matrix(text):
        violations.append(
            "PR CI must use one production Python version by default, currently Python 3.12"
        )

    if not budget_exempt:
        # 1 day, not 7. Cut on 2026-08-20 after artifact storage hit 100%
        # of the account's 0.5 GB allowance -- 990 live artifacts, 542.5 GB,
        # 93% of it from one workflow storing ~45 GB per push (about half of
        # that a second copy of its own inputs). At 7 days nothing aged out
        # before the next push landed.
        for block in _artifact_blocks(text):
            if not re.search(r"retention-days:\s*1\b", block):
                violations.append("upload-artifact step is missing retention-days: 1")

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all", action="store_true", help="Check every workflow file, not only changed workflows."
    )
    parser.add_argument(
        "--run", help="Pipeline run id. Enables committed diff detection for workflow edits."
    )
    parser.add_argument("--base-ref", help="Base ref or SHA for committed workflow diff detection.")
    args = parser.parse_args()

    base_ref: str | None = None
    if args.all:
        paths = _all_workflows()
    elif args.run:
        paths, base_ref = _changed_workflows_for_run(args.base_ref)
        if not base_ref and not paths:
            print(
                "check_actions_budget: FAIL (pipeline mode cannot prove whether committed workflow files changed; "
                "pass --base-ref or configure an upstream branch)"
            )
            return 1
    else:
        paths = _git_status_paths()
    if not paths:
        suffix = f" against {base_ref}" if base_ref else ""
        print(f"check_actions_budget: PASS (no changed workflow files{suffix})")
        return 0

    failures: list[tuple[Path, list[str]]] = []
    for path in paths:
        if not path.exists():
            continue
        violations = validate_workflow(path, path.read_text(encoding="utf-8-sig"))
        if violations:
            failures.append((path, violations))

    if failures:
        print("check_actions_budget: FAIL")
        for path, violations in failures:
            rel = path.relative_to(REPO_ROOT)
            print(f"  - {rel}")
            for violation in violations:
                print(f"    - {violation}")
        return 1

    suffix = f" against {base_ref}" if base_ref else ""
    print(f"check_actions_budget: PASS ({len(paths)} workflow file(s) checked{suffix})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
