# SPDX-License-Identifier: Apache-2.0
"""Verify the Windows media job's JUnit without rewriting it.

Every failure, error, skip and nonstandard pytest exit fails required CI. U63
removed the former owner-accepted rc.1 audio PES PTS step: the product now
holds the changeover segment until the mux pad has given up the retiring audio,
so no media failure has an accepted identity any more.

The signature keeps its ``(passed, accepted)`` shape so the workflow step reads
the same way; ``accepted`` is always ``0``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path


def classify_media_junit(junit: Path, pytest_exit: int, minimum_cases: int) -> tuple[int, int]:
    """Return ``(passed, 0)``, rejecting any failure or incomplete proof."""
    if pytest_exit not in (0, 1):
        raise ValueError(f"Nonstandard pytest exit: {pytest_exit}")
    root = ET.parse(junit).getroot()
    if any(list(root.iter(tag)) for tag in ("skipped", "error")):
        raise ValueError("Selected coverage was skipped or errored")
    cases = list(root.iter("testcase"))
    if len(cases) < minimum_cases:
        raise ValueError("Selected cases did not all execute")
    identities = [(case.get("classname"), case.get("name")) for case in cases]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate media testcase identities")
    for case, identity in zip(cases, identities, strict=True):
        if any(case.find(tag) is not None for tag in ("skipped", "error")):
            raise ValueError(f"Selected coverage was skipped or errored: {identity}")
        if case.findall("failure"):
            raise ValueError(f"Unaccepted media failure: {identity}")
    failures = len(list(root.iter("failure")))
    if failures:
        raise ValueError("Unclassified JUnit failures")
    if pytest_exit:
        raise ValueError("Pytest exit and JUnit failure evidence disagree")
    return len(cases), 0
