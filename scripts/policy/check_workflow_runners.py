#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Ensure GitHub Actions jobs target GitHub-hosted runners.

Scott's directive (2026-06-12, repo public): hosted runners everywhere —
public-repo minutes are free and CI must not depend on any one person's
hardware. Self-hosted labels are allowed ONLY for the explicit allowlist of
lanes that physically cannot run hosted (GPU-bound proofs, the local
Hyper-V cleanroom) or exceed the hosted 6-hour job ceiling (the soak).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

try:
    from policy_utils import find_repo_root
except ModuleNotFoundError:  # pragma: no cover - package import in tests
    from scripts.policy.policy_utils import find_repo_root

REPO_ROOT = find_repo_root(__file__)
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
HOSTED_RE = re.compile(r"^(?:ubuntu|windows|macos)(?:-latest|-\d+(?:\.\d+)*)$", re.I)
# Lanes that cannot run on hosted runners. Every entry needs a reason.
SELF_HOSTED_ALLOWLIST = {
    "ai-release-proof.yml": "needs the local RTX GPU",
    "benchmark-caption-runtime.yml": "needs the local RTX GPU",
    "diagnose-blackwell-runtime.yml": "needs the local RTX GPU",
    "vm-cleanroom-release.yml": "needs the local Hyper-V VM harness",
    "six-hour-soak.yml": "exceeds the hosted 6-hour job ceiling",
    "gate-a-station-acceptance.yml": "needs Windows Sandbox on the local sandbox-lab runner for the clean-box station-acceptance gate",
    "publish-staged-kit.yml": (
        "publishes the locally staged, Gate-A-passed kit bytes from "
        "C:\\CivicCastTester\\kit-staging\\<sha> as a workflow artifact; "
        "hosted runners cannot reach this box's disk, so the upload must "
        "run on the same self-hosted sandbox-lab box the kit is staged on"
    ),
}
HOSTED_EXPRESSION_ALLOWLIST = {
    # Source-free transfer of four locally reviewed runtime archives only.
    # ci-test's Windows tests stay hosted; fork PRs cannot use the mirror job.
    (
        "ci-test.yml",
        '${{ fromJSON(\'["self-hosted","windows","sandbox-lab"]\') }}',
    ),
    # Source-free transfer of reviewed immutable inputs from a local-only mirror.
    # The two reproducibility builds remain on windows-latest; forks skip transfer.
    (
        "native-app-reproducibility.yml",
        '${{ fromJSON(\'["self-hosted","windows","sandbox-lab"]\') }}',
    ),
    (
        "ci-installer-compile.yml",
        "${{ inputs.native_beta_windows_only && 'windows-latest' || 'ubuntu-24.04' }}",
    ),
    # native-beta-candidate-artifacts.yml's build_target: self-hosted lane
    # (see that workflow's header) keeps the assembled kit local for Gate A
    # instead of uploading ~21 GB just to have gate-a-station-acceptance.yml
    # (already self-hosted-allowlisted below) download it back on the same
    # box at ~1-2 MB/s. Same hardware/duration rationale as
    # gate-a-station-acceptance.yml itself; the expression form (rather than
    # a plain self-hosted runs-on list) is what lets this same workflow keep
    # running on windows-latest for its default/push-triggered builds.
    (
        "native-beta-candidate-artifacts.yml",
        "${{ (github.event_name == 'workflow_dispatch' && inputs.build_target == 'self-hosted' && !inputs.prepare_only) && fromJSON('[\"self-hosted\",\"windows\",\"sandbox-lab\"]') || 'windows-latest' }}",
    ),
}


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return path.name


def _parse_runs_on(value: str) -> list[str]:
    stripped = value.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        return [
            item.strip().strip("\"'")
            for item in stripped[1:-1].split(",")
            if item.strip().strip("\"'")
        ]
    if stripped.startswith("${{"):
        return [stripped]
    return [stripped.strip("\"'")]


def _runs_on_entries(text: str) -> list[tuple[int, list[str], str]]:
    entries: list[tuple[int, list[str], str]] = []
    lines = text.splitlines()
    for index, raw in enumerate(lines):
        match = re.match(r"^(?P<indent>\s*)runs-on:\s*(?P<value>.*)$", raw)
        if not match:
            continue
        value = match.group("value").strip()
        if value:
            entries.append((index + 1, _parse_runs_on(value), raw.strip()))
            continue
        labels: list[str] = []
        base_indent = len(match.group("indent"))
        for later in lines[index + 1 :]:
            indent = len(later) - len(later.lstrip())
            stripped = later.strip()
            if not stripped:
                continue
            if indent <= base_indent:
                break
            if stripped.startswith("- "):
                labels.append(stripped[2:].strip().strip("\"'"))
        entries.append((index + 1, labels, raw.strip()))
    return entries


def _is_self_hosted(labels: list[str]) -> bool:
    return any(label.lower() == "self-hosted" for label in labels)


def validate_workflow(path: Path, text: str) -> list[str]:
    violations: list[str] = []
    for line_number, labels, raw in _runs_on_entries(text):
        if _is_self_hosted(labels):
            if path.name in SELF_HOSTED_ALLOWLIST:
                continue
            violations.append(
                f"{_display_path(path)}:{line_number}: self-hosted runner {labels!r} is "
                "forbidden (hosted-runners directive 2026-06-12); use runs-on: "
                "ubuntu-latest, or add the file to SELF_HOSTED_ALLOWLIST with a "
                "hardware/duration reason"
            )
            continue
        if labels and all(HOSTED_RE.match(label) for label in labels):
            continue
        if labels and labels[0].startswith("${{"):
            if (path.name, labels[0]) in HOSTED_EXPRESSION_ALLOWLIST:
                continue
            violations.append(
                f"{_display_path(path)}:{line_number}: matrix runner target `{labels[0]}` "
                "is forbidden unless it resolves only to hosted runner labels"
            )
            continue
        violations.append(
            f"{_display_path(path)}:{line_number}: unsupported runner target `{raw}`; "
            "use runs-on: ubuntu-latest (or windows-latest/macos-latest where required)"
        )
    return violations


def check_workflow_runners(root: Path = REPO_ROOT) -> list[str]:
    workflow_dir = root / ".github" / "workflows"
    if not workflow_dir.exists():
        return []
    violations: list[str] = []
    for path in sorted(workflow_dir.glob("*.yml")):
        violations.extend(validate_workflow(path, path.read_text(encoding="utf-8-sig")))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    violations = check_workflow_runners()
    if violations:
        print("check_workflow_runners: FAIL")
        for violation in violations:
            print(f"  - {violation}")
        return 1
    print(
        "check_workflow_runners: PASS - workflows use GitHub-hosted runners "
        "(allowlisted hardware/duration lanes excepted)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
