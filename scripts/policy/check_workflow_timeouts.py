# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Fail if any workflow job can run without a ceiling.

Why this gate exists
--------------------
On 2026-08-19/20 four separate CI wedges cost hours of blocked merge queue,
and every one of them was the same defect: a step or job with no ceiling.

  * ``apt-get`` with no dpkg-lock timeout sat 3h49m on the release branch and
    ~2h on PRs #422 and #423 (runs 32294685985, 32303353858, 32303185157).
  * ``assemble-native-beta-kit`` declared no ``timeout-minutes`` at all, so it
    inherited GitHub's 360-minute default and sat over an hour inside a hung
    ``actions/download-artifact`` (run 32316394055).
  * A pinned model download with no retry killed the whole installable-kit
    build on one dropped socket (run 32314864434).

The common shape is worse than "slow": a wedged job reports **no failing
checks**. The PR sits BLOCKED behind something that will never finish, and
failure-only monitoring calls it clean. It is invisible until someone asks
why nothing has merged.

A promise not to do it again is not a control. This is the control: a job
without a declared ceiling fails the build, so the default can never be
inherited silently again.

Rules
-----
1. Every job must declare ``timeout-minutes``. Jobs that only ``uses:`` a
   reusable workflow are exempt -- the ceiling belongs to that workflow's own
   jobs, where this same check enforces it.
2. No job may exceed ``MAX_MINUTES`` unless it is in ``LONG_BY_DESIGN`` with a
   written reason. The allowlist is the place to argue for an exception; a
   bare large number is not.
3. Every ``actions/upload-artifact`` and ``actions/download-artifact`` step
   must declare its own ``timeout-minutes``. A job ceiling alone is not
   enough here: this repo's artifacts are enormous (the native station bundle
   is 18.6 GB, the assembled kit 21.9 GB), a healthy upload of that bundle
   takes ~12 minutes, and on 2026-08-20 run 32319075421 sat 50+ minutes on
   that exact step. Without a step ceiling the only thing that eventually
   stops a hung transfer is the job ceiling -- hours later.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

MAX_MINUTES = 180

#: actions whose steps move large payloads over the network.
TRANSFER_ACTIONS = ("actions/upload-artifact", "actions/download-artifact")

#: job key -> why it legitimately outlives MAX_MINUTES.
LONG_BY_DESIGN: dict[tuple[str, str], str] = {
    ("deterministic-detectors.yml", "mutation-report"): (
        "measured run 39666692394: 7 min generation, 63 min stats PASS, and a clean "
        "lane still live at 45 min when the old 120 cap cancelled it; the complete "
        "mutation phase remains unmeasured. 360 min is a bounded runtime allowance, "
        "not an acceptance waiver; diagnostics keep progress visible."
    ),
    ("gate-a-station-acceptance.yml", "station-acceptance-dirty"): (
        "2026-08-30: the dirty lane runs TWO full sandbox cycles in one job -- a real "
        "install + uninstall (phase 1, seeding true post-uninstall remnants) and then the "
        "full clean-lane acceptance flow on top of those remnants (phase 2). Each phase is "
        "sized like the existing 170-minute clean lane, so 340 minutes is the honest "
        "two-phase ceiling, and the in-job timing contract (dirty 210 < 230 < job 340) is "
        "asserted by tests/gate_a so the ceiling cannot silently drift."
    ),
    ("gate-a-station-acceptance.yml", "station-acceptance-download-only"): (
        "2026-09-02: the download-only lane <gate-a-download-only-lane> reuses the dirty "
        "lane's own two-phase cross-version upgrade shape byte-for-byte -- phase 1 installs "
        "a pinned previous candidate from its full kit, phase 2 runs the current candidate's "
        "setup.exe over it (from a filtered payload with no station\\, proving the "
        "download-only install/upgrade path the K1 fix silently broke). Same two 170-minute-"
        "class phases as station-acceptance-dirty above, so the same 340-minute ceiling "
        "applies for the identical reason, and the identical in-job timing contract "
        "(210 < 230 < job 340) is asserted by tests/gate_a/test_gate_a_harness_contract.py."
    ),
    ("six-hour-soak.yml", "six-hour-soak"): (
        "the six-hour soak IS the test; a shorter ceiling would defeat its entire purpose"
    ),
    ("publish-staged-kit.yml", "publish"): (
        "2026-08-26: uploads an already-staged ~25 GB kit (packs\\, station\\, the setup.exe) "
        "from C:\\CivicCastTester\\kit-staging\\<sha> as a workflow artifact. Healthy egress on "
        "this box's link runs well under the 20 GB/3h rate gate-a-station-acceptance.yml's own "
        "'ARTIFACT DOWNLOAD' header documents for the equivalent-size download, so a 180-minute "
        "job ceiling would fail a perfectly healthy upload partway through, not just a wedged "
        "one. 300 minutes gives the transfer real headroom above that baseline while still being "
        "a hard, finite ceiling -- the upload-artifact step itself carries its own "
        "timeout-minutes per this file's rule 3, so a wedge is still caught well before 300."
    ),
}


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    workflows = root / ".github" / "workflows"
    if not workflows.is_dir():
        print(f"::error::no workflows directory at {workflows}")
        return 1

    problems: list[str] = []
    checked = 0

    for path in sorted(workflows.glob("*.yml")) + sorted(workflows.glob("*.yaml")):
        try:
            doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            problems.append(f"{path.name}: unparseable YAML: {exc}")
            continue
        if not isinstance(doc, dict):
            continue

        for job_name, job in (doc.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            if "uses" in job and "runs-on" not in job:
                continue  # reusable-workflow call; ceiling lives in the callee
            checked += 1
            key = (path.name, job_name)
            timeout = job.get("timeout-minutes")

            if timeout is None:
                problems.append(
                    f"{path.name}: job '{job_name}' declares no timeout-minutes, "
                    f"so it inherits GitHub's 360-minute default. A wedged job "
                    f"reports no failing checks -- it just blocks the queue for "
                    f"six hours while every check reads green. Give it a ceiling."
                )
                continue

            if not isinstance(timeout, int):
                problems.append(
                    f"{path.name}: job '{job_name}' has a non-integer "
                    f"timeout-minutes ({timeout!r})."
                )
                continue

            if timeout > MAX_MINUTES and key not in LONG_BY_DESIGN:
                problems.append(
                    f"{path.name}: job '{job_name}' allows {timeout} minutes, over "
                    f"the {MAX_MINUTES}-minute cap. If it genuinely needs longer, "
                    f"add it to LONG_BY_DESIGN in {Path(__file__).name} with the "
                    f"reason -- do not just raise the number."
                )

            for step in job.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                uses = str(step.get("uses", ""))
                if not any(a in uses for a in TRANSFER_ACTIONS):
                    continue
                if step.get("timeout-minutes") is None:
                    label = step.get("name") or uses
                    problems.append(
                        f"{path.name}: job '{job_name}' step '{label}' transfers an "
                        f"artifact with no timeout-minutes. These payloads reach 20+ "
                        f"GB here and a stalled transfer is indistinguishable from a "
                        f"slow one -- bound the step, not just the job."
                    )

    if problems:
        print(f"::error::{len(problems)} workflow job(s) without a usable ceiling:")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"All {checked} workflow jobs declare a timeout at or under {MAX_MINUTES}m")
    for (fname, job), reason in LONG_BY_DESIGN.items():
        print(f"  allowed over cap: {fname}:{job} -- {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
