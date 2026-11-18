# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Audit item #27: the CI security-scan workflow covers all three scanners
and never runs unallowlisted findings without failing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from scripts import check_pip_audit_allowlist


def test_pip_audit_only_permits_own_editable_source_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(check_pip_audit_allowlist, "load_allowlist", lambda: set())
    report = tmp_path / "audit.json"
    own = {"name": "civiccast", "skip_reason": "distribution marked as editable"}
    dependency = {"name": "requests", "version": "2.32.0", "vulns": []}

    def check(entries):
        report.write_text(json.dumps({"dependencies": entries}))
        return check_pip_audit_allowlist.main(["check", str(report)])

    assert check([own, dependency]) == 0
    assert check([own]) == 2
    assert check([{**own, "name": "third-party"}, dependency]) == 2
    assert check([own, {**dependency, "vulns": [{"id": "CVE-named-defect"}]}]) == 1


def _load_workflow() -> dict[str, Any]:
    return yaml.safe_load(
        Path(".github/workflows/ci-security-scan.yml").read_text(encoding="utf-8")
    )


def _job_run_commands(job: dict[str, Any]) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def test_security_scan_workflow_covers_pip_audit_bandit_and_both_npm_audits() -> None:
    workflow = _load_workflow()
    jobs = workflow["jobs"]

    assert set(jobs) == {
        "pip-audit",
        "bandit",
        "npm-audit-operator",
        "npm-audit-public",
        "npm-audit-tsr",
    }
    assert "pip-audit" in _job_run_commands(jobs["pip-audit"])
    assert "check_pip_audit_allowlist.py" in _job_run_commands(jobs["pip-audit"])
    assert "bandit -ll -r civiccast" in _job_run_commands(jobs["bandit"])
    for npm_job in ("npm-audit-operator", "npm-audit-public", "npm-audit-tsr"):
        assert "npm audit --audit-level=high" in _job_run_commands(jobs[npm_job])
    # Both portal audits triage through the allowlist instead of failing the
    # job on the report itself; the TSR sidecar is still audited directly.
    for npm_job, app in (
        ("npm-audit-operator", "portal-operator"),
        ("npm-audit-public", "portal-public"),
    ):
        commands = _job_run_commands(jobs[npm_job])
        assert (
            f"python ../../../scripts/check_npm_audit_allowlist.py {app} npm-audit-report.json" in commands
        )
        # --omit=dev would hide every dev advisory and defeat the gate.
        assert "--omit=dev" not in commands
    assert "check_npm_audit_allowlist.py" not in _job_run_commands(jobs["npm-audit-tsr"])
    # RC4 F8: the shipped TSR control sidecar lockfile is audited without installing it.
    assert "--package-lock-only" in _job_run_commands(jobs["npm-audit-tsr"])
    assert jobs["npm-audit-tsr"]["defaults"]["run"]["working-directory"] == (
        "civiccast/control_room/tsr_service"
    )
    assert jobs["npm-audit-operator"]["defaults"]["run"]["working-directory"] == (
        "civiccast/apps/portal-operator"
    )
    assert jobs["npm-audit-public"]["defaults"]["run"]["working-directory"] == (
        "civiccast/apps/portal-public"
    )
    # Weekly drift catcher stays scheduled. PyYAML parses the bare `on:`
    # key as boolean True (YAML 1.1), hence the lookup shape.
    triggers = workflow.get("on", workflow.get(True))
    assert "schedule" in triggers
    assert "pull_request" in triggers


def test_security_scan_workflow_never_soft_fails() -> None:
    """A gate that runs with continue-on-error is theater: it goes green no
    matter what the scanners find. No job or step may set it."""

    workflow = _load_workflow()
    for job_name, job in workflow["jobs"].items():
        assert not job.get("continue-on-error"), f"{job_name} soft-fails at job level"
        for step in job["steps"]:
            assert not step.get("continue-on-error"), (
                f"{job_name} step {step.get('name', '?')!r} soft-fails"
            )


def test_security_scan_workflow_is_not_in_the_required_checks_list() -> None:
    """This gate must stay separate from the 5 branch-protection-required
    checks (Unit tests, Lint, both a11y jobs, Operator portal build) — a
    scanner false positive should never block every PR the day it's added."""

    workflow = _load_workflow()
    assert workflow["name"] == "ci-security-scan"


def test_pip_audit_allowlist_entries_are_dated_and_reasoned() -> None:
    data = json.loads(Path("security/pip-audit-allowlist.json").read_text(encoding="utf-8"))

    assert data["allowed"], "allowlist should document at least the known nltk finding"
    for entry in data["allowed"]:
        assert entry["package"]
        assert entry["id"]
        assert entry["reviewed"]
        assert len(entry["reason"]) > 20


def test_pip_audit_allowlist_checker_fails_on_unlisted_findings(tmp_path: Path) -> None:
    import subprocess
    import sys

    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "dependencies": [
                    {
                        "name": "totally-made-up-package",
                        "version": "0.0.1",
                        "vulns": [{"id": "FAKE-1"}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "scripts/check_pip_audit_allowlist.py", str(report)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "totally-made-up-package" in result.stderr


def test_pip_audit_allowlist_checker_passes_on_known_findings(tmp_path: Path) -> None:
    import subprocess
    import sys

    allowlist = json.loads(Path("security/pip-audit-allowlist.json").read_text(encoding="utf-8"))
    entry = allowlist["allowed"][0]

    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "dependencies": [
                    {
                        "name": entry["package"],
                        "version": "0.0.0",
                        "vulns": [{"id": entry["id"]}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, "scripts/check_pip_audit_allowlist.py", str(report)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0


def _npm_report(vulnerabilities: dict[str, Any], total: int = 300) -> dict[str, Any]:
    return {
        "auditReportVersion": 2,
        "vulnerabilities": vulnerabilities,
        "metadata": {"dependencies": {"prod": 9, "dev": 300, "total": total}},
    }


def _npm_advisory(name: str, ghsa: str, *, severity: str = "high") -> dict[str, Any]:
    return {
        "name": name,
        "severity": severity,
        "isDirect": False,
        "via": [
            {
                "source": 1,
                "name": name,
                "dependency": name,
                "title": "made up",
                "url": f"https://github.com/advisories/{ghsa}",
                "severity": severity,
                "range": "<=1.0.0",
            }
        ],
        "effects": [],
        "range": "<=1.0.0",
        "nodes": [f"node_modules/{name}"],
        "fixAvailable": False,
    }


def test_npm_audit_allowlist_entries_are_dated_and_reasoned() -> None:
    data = json.loads(Path("security/npm-audit-allowlist.json").read_text(encoding="utf-8"))

    assert data["allowed"], "allowlist should document at least the known picomatch finding"
    for entry in data["allowed"]:
        assert entry["package"]
        assert entry["id"]
        assert entry["apps"], "an entry must name the apps it applies to"
        assert entry["reviewed"]
        assert entry["review_by"]
        assert len(entry["reason"]) > 20


def test_npm_audit_allowlist_checker_fails_on_unlisted_findings(tmp_path: Path) -> None:
    import subprocess
    import sys

    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            _npm_report(
                {
                    "totally-made-up-package": _npm_advisory(
                        "totally-made-up-package", "GHSA-aaaa-bbbb-cccc"
                    )
                }
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_npm_audit_allowlist.py",
            "portal-operator",
            str(report),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "totally-made-up-package" in result.stderr
    assert "GHSA-aaaa-bbbb-cccc" in result.stderr


def test_npm_audit_allowlist_checker_passes_on_known_findings(tmp_path: Path) -> None:
    import subprocess
    import sys

    entry = json.loads(Path("security/npm-audit-allowlist.json").read_text(encoding="utf-8"))[
        "allowed"
    ][0]

    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(_npm_report({entry["package"]: _npm_advisory(entry["package"], entry["id"])})),
        encoding="utf-8",
    )

    for app in entry["apps"]:
        result = subprocess.run(
            [sys.executable, "scripts/check_npm_audit_allowlist.py", app, str(report)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

    # The same advisory is not allowlisted for an app it was not reviewed for.
    unlisted_app = (
        next(app for app in ("portal-operator", "portal-public") if app not in entry["apps"])
        if len(entry["apps"]) < 2
        else "some-other-app"
    )
    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_npm_audit_allowlist.py",
            unlisted_app,
            str(report),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1


def test_npm_audit_allowlist_checker_ignores_below_high(tmp_path: Path) -> None:
    import subprocess
    import sys

    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            _npm_report(
                {
                    "minimatch": _npm_advisory(
                        "minimatch", "GHSA-dddd-eeee-ffff", severity="moderate"
                    )
                }
            )
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/check_npm_audit_allowlist.py",
            "portal-public",
            str(report),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_npm_audit_allowlist_checker_follows_propagation_to_the_advisory(
    tmp_path: Path,
) -> None:
    """npm marks every affected parent high too, with only the child's name in
    `via`; those entries are not separate findings and must not need entries."""
    import subprocess
    import sys

    entry = json.loads(Path("security/npm-audit-allowlist.json").read_text(encoding="utf-8"))[
        "allowed"
    ][0]
    app = entry["apps"][0]
    parent = {
        "name": "some-build-tool",
        "severity": "high",
        "via": [entry["package"]],
        "effects": [],
        "range": "*",
        "fixAvailable": False,
    }

    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            _npm_report(
                {
                    entry["package"]: _npm_advisory(entry["package"], entry["id"]),
                    "some-build-tool": parent,
                }
            )
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "scripts/check_npm_audit_allowlist.py", app, str(report)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_npm_audit_allowlist_checker_refuses_empty_or_error_reports(
    tmp_path: Path,
) -> None:
    import subprocess
    import sys

    cases = [
        {"error": {"code": "ENOLOCK", "summary": "no lockfile"}},
        _npm_report({}, total=0),
        {"auditReportVersion": 2, "vulnerabilities": {}},
    ]
    for case in cases:
        report = tmp_path / "report.json"
        report.write_text(json.dumps(case), encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "scripts/check_npm_audit_allowlist.py",
                "portal-operator",
                str(report),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2, case
