#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Policy: native release front doors stay coherent and non-overclaiming.

The filename is retained because CI and downstream tooling already invoke it.
The v1.7/WSL2 adoption line is retired; this gate now protects the native-only
repository's active release posture.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

try:
    from policy_utils import find_repo_root
except ModuleNotFoundError:  # pragma: no cover - package import in tests
    from scripts.policy.policy_utils import find_repo_root

REPO_ROOT = find_repo_root(__file__)
# Public front doors describe the published installer, which can lag the
# candidate version in source while a release is held for review.
PUBLISHED_RELEASE_TAG = yaml.safe_load(
    (REPO_ROOT / "docs/releases/release-truth.yaml").read_text(encoding="utf-8")
)["current"]
PUBLISHED_RELEASE_LINK = f"releases/tag/{PUBLISHED_RELEASE_TAG}"
REQUIRED_DOCS: dict[Path, tuple[str, ...]] = {
    Path("README.md"): (
        PUBLISHED_RELEASE_TAG,
        PUBLISHED_RELEASE_LINK,
    ),
    Path("INSTALL-WINDOWS.md"): (
        PUBLISHED_RELEASE_TAG,
        PUBLISHED_RELEASE_LINK,
    ),
    Path("ARCHITECTURE.md"): (
        PUBLISHED_RELEASE_TAG,
        PUBLISHED_RELEASE_LINK,
    ),
    Path("SUPPORT.md"): (
        PUBLISHED_RELEASE_TAG,
        PUBLISHED_RELEASE_LINK,
    ),
    Path("docs/index.html"): (
        PUBLISHED_RELEASE_TAG,
        PUBLISHED_RELEASE_LINK,
        # Marketing copy links to release evidence rather than repeating
        # historical measurements as front-page product claims.
        f"docs/releases/{PUBLISHED_RELEASE_TAG}-verification.md",
    ),
    Path("docs/install-windows.html"): (
        PUBLISHED_RELEASE_TAG,
        PUBLISHED_RELEASE_LINK,
        "Windows installation guide",
        "INSTALL-WINDOWS.md",
        "signed installer",
        "complete signed station model bundle",
        "verifies them",
    ),
}

HISTORICAL_DOCS = (
    Path("docs/adoption/early-adopter-quickstart.md"),
    Path("docs/tester/START-HERE.md"),
    Path("docs/tester/lpm-beta-test-handoff.md"),
    Path("docs/tester/known-limitations.md"),
    Path("docs/install/windows-release-trust.md"),
)
# docs/tester/technical-walkthrough.md and docs/tester/SMARTSCREEN-WALKTHROUGH.md
# were removed from HISTORICAL_DOCS 2026-09-02: the visitor-audit follow-up
# (PR #134) rewrote both as live, current-beta-line guidance -- the technical
# walkthrough now describes the native line's own install/verify path instead
# of the retired WSL2 line, and the SmartScreen walkthrough keeps its
# mechanics (Authenticode/SmartScreen clicks) current while its release-state
# banner now names v1.0.0-beta.1/beta.3 (updated 2026-09-02: beta.2 never
# published, see docs/releases/2026-09-02-beta1-to-beta2-fresh-install-only.md)
# instead of rc18. Neither file is
# "kept as historical reference" any more, so requiring a historical/retired
# classification on them would be reintroducing exactly the stale-claim
# pattern this gate exists to catch. The other five docs above still carry
# real retired-WSL2-line content behind a historical/retired banner and stay
# gated here.

PLACEHOLDER_PATTERN = re.compile(r"\b(?:TODO|TBD|fill in|placeholder)\b", re.IGNORECASE)
OVERCLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bRoku Channel Store (?:ready|certified|published|approved)\b", re.IGNORECASE),
    re.compile(r"\b(?:SDI|DeckLink|Comcast|headend) (?:proven|certified|ready)\b", re.IGNORECASE),
    re.compile(r"\blegal(?:ly)? certified\b", re.IGNORECASE),
    re.compile(r"\baccessibility certified\b", re.IGNORECASE),
    re.compile(r"\bone-for-one incumbent platform (?:replacement|parity)\b", re.IGNORECASE),
)


def evaluate_v17_adoption_gate(root: Path) -> list[str]:
    """Return native release-posture and adoption-surface violations."""

    root = root.resolve()
    violations: list[str] = []
    for relative, required_phrases in REQUIRED_DOCS.items():
        path = root / relative
        if not path.exists():
            violations.append(f"{relative.as_posix()}: missing required native release doc.")
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not text.strip():
            violations.append(f"{relative.as_posix()}: required native release doc is empty.")
            continue
        if PLACEHOLDER_PATTERN.search(text):
            violations.append(f"{relative.as_posix()}: contains placeholder language.")
        lower_text = " ".join(text.lower().split())
        for phrase in required_phrases:
            if " ".join(phrase.lower().split()) not in lower_text:
                violations.append(f"{relative.as_posix()}: missing required phrase {phrase!r}.")
        for pattern in OVERCLAIM_PATTERNS:
            if pattern.search(text):
                violations.append(
                    f"{relative.as_posix()}: contains overclaim pattern {pattern.pattern!r}."
                )

    for relative in HISTORICAL_DOCS:
        path = root / relative
        if not path.exists():
            violations.append(f"{relative.as_posix()}: missing retained historical doc.")
            continue
        lower_text = " ".join(path.read_text(encoding="utf-8", errors="ignore").lower().split())
        if "historical" not in lower_text and "retired" not in lower_text:
            violations.append(f"{relative.as_posix()}: missing historical/retired classification.")
        if "civiccast-native" not in lower_text:
            violations.append(f"{relative.as_posix()}: does not distinguish the native repository.")

    return violations


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]).resolve() if argv else REPO_ROOT
    violations = evaluate_v17_adoption_gate(root)
    if violations:
        print("NATIVE ADOPTION GATE: FAIL")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print("NATIVE ADOPTION GATE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
