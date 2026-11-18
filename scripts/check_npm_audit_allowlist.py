#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Fail the security-scan gate on any npm audit advisory not in the allowlist.

Audit item #27. ``npm audit --json`` reports every known advisory, fixable or
not; this script is the triage step, the npm counterpart of
``check_pip_audit_allowlist.py``. A high/critical advisory is either fixed
(bump the dependency) or explicitly reviewed and pinned in
``security/npm-audit-allowlist.json`` for that app with a dated reason.
Allowlisting per app, not globally, keeps a dev-only finding in one portal
from silently covering a runtime finding in another. Nothing is silently
permitted: every high/critical advisory in the report must appear in the
allowlist by (package, advisory id) or the gate fails.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = ROOT / "security" / "npm-audit-allowlist.json"
BLOCKING_SEVERITIES = frozenset({"high", "critical"})


def load_allowlist(app: str) -> set[tuple[str, str]]:
    data = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    return {
        (entry["package"], entry["id"]) for entry in data["allowed"] if app in entry.get("apps", [])
    }


def advisory_id(url: str) -> str:
    """npm audit exposes the GHSA/CVE id only in the advisory URL."""
    return url.rstrip("/").rsplit("/", 1)[-1]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(
            "usage: check_npm_audit_allowlist.py <app> <npm-audit-report.json>",
            file=sys.stderr,
        )
        return 2

    app = argv[1]
    report_path = Path(argv[2])
    report = json.loads(report_path.read_text(encoding="utf-8"))
    vulnerabilities = report.get("vulnerabilities")
    audited = (report.get("metadata") or {}).get("dependencies", {}).get("total")
    if (
        report.get("auditReportVersion") is None
        or not isinstance(vulnerabilities, dict)
        or not isinstance(audited, int)
        or audited <= 0
    ):
        print(
            "npm audit did not return a dependency scan; refusing an empty/error report.",
            file=sys.stderr,
        )
        return 2

    allowlist = load_allowlist(app)

    def reaches_advisory(package: str, visited: set[str]) -> bool:
        """npm marks every affected parent high too, with only the child's name
        in ``via``. Follow those links to the package the advisory was
        published for; if no link leads anywhere, the finding is untriageable."""
        if package in visited:
            return False
        visited.add(package)
        vulnerability = vulnerabilities.get(package)
        if not isinstance(vulnerability, dict):
            return False
        for via in vulnerability.get("via", []):
            if isinstance(via, dict) and via.get("url"):
                return True
            if isinstance(via, str) and reaches_advisory(via, visited):
                return True
        return False

    unallowed: list[str] = []
    unattributed: list[str] = []
    seen: set[tuple[str, str]] = set()
    for vulnerability in vulnerabilities.values():
        if vulnerability.get("severity") not in BLOCKING_SEVERITIES:
            continue
        package = vulnerability.get("name", "")
        found_advisory = False
        for via in vulnerability.get("via", []):
            # Transitive entries list the parent package by name; the advisory
            # itself is reported under the package it was published for.
            if not isinstance(via, dict) or not via.get("url"):
                continue
            found_advisory = True
            advisory = advisory_id(via["url"])
            key = (package, advisory)
            if key in seen:
                continue
            seen.add(key)
            if key not in allowlist:
                unallowed.append(f"{package}: {advisory}")
        if not found_advisory and not reaches_advisory(package, set()):
            unattributed.append(f"{package} ({vulnerability.get('severity')})")

    if unattributed:
        print(
            "npm audit reported blocking findings with no advisory id; cannot triage:",
            file=sys.stderr,
        )
        for line in unattributed:
            print(f"  - {line}", file=sys.stderr)
        return 2
    if unallowed:
        print(
            f"npm audit ({app}) found findings not in security/npm-audit-allowlist.json:",
            file=sys.stderr,
        )
        for line in unallowed:
            print(f"  - {line}", file=sys.stderr)
        print(
            "\nFix by upgrading the dependency, or add a dated, reasoned entry "
            "for this app to security/npm-audit-allowlist.json if there is "
            "genuinely no fix and the finding is not reachable.",
            file=sys.stderr,
        )
        return 1

    print(f"npm audit ({app}): all high/critical findings are absent or explicitly allowlisted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
