# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Automation and cost policy for the WP1 native caption proof workflows."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

from scripts.policy.check_actions_budget import validate_workflow

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = (
    ROOT / ".github" / "workflows" / "native-app-reproducibility.yml",
    ROOT / ".github" / "workflows" / "ci-test.yml",
    ROOT / ".github" / "workflows" / "deterministic-detectors.yml",
)

# How far ci-test.yml's `--floor` values are allowed to sit below the ACTUAL,
# freshly-collected tests/native count before this policy suite fails the
# build. Chosen 2026-08-30 after an audit found the floors unmoved since the
# repo's first commit while the real collection grew to (1683, 1877) -- 246
# and 304 tests of undetected slack. Every real batch in this file's own
# history comment trail below adds low tens of tests at a time (the largest
# single jump on record is +19); 50 comfortably absorbs a normal PR or two of
# legitimate test consolidation without a floor-bump commit, while still
# catching anything on the order of a whole test module going missing from a
# run. This is intentionally NOT zero slack (a literal exact-pin, like
# `test_native_marker_collections_match_the_workflow_floors` below keeps for
# the collection count itself) because ci-test.yml's floor is meant to
# survive routine test churn between the discipline-enforced updates to that
# exact count -- it is a tripwire, not a duplicate of the exact assertion.
NATIVE_JUNIT_FLOOR_MARGIN = 50


def _collect_native_tests(marker: str | None = None) -> int:
    """Real `pytest --collect-only` count for tests/native, optionally marker-filtered.

    Shared by both floor-policy tests below so neither one ever trusts a
    hardcoded number instead of an actual collection run.
    """
    command = [
        sys.executable,
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "--disable-warnings",
    ]
    if marker is not None:
        command.extend(["-m", marker])
    command.append("tests/native")
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    return sum(
        line.startswith("tests/native/") and "::" in line for line in result.stdout.splitlines()
    )


def _workflow(path: Path) -> tuple[str, dict[str, object]]:
    text = path.read_text(encoding="utf-8")
    return text, yaml.load(text, Loader=yaml.BaseLoader)


def test_reproducibility_proof_runs_for_relevant_pull_requests_exactly_once() -> None:
    workflow_path = WORKFLOWS[0]
    _text, workflow = _workflow(workflow_path)
    triggers = workflow.get("on", workflow.get(True))

    assert set(triggers) >= {"pull_request", "workflow_dispatch", "workflow_call"}
    assert "push" not in triggers, "direct PR triggering must not duplicate a push caller"
    pull_request = triggers["pull_request"]
    assert set(pull_request["paths"]) >= {
        "requirements-native-app.txt",
        "scripts/build_native_app_payload.py",
        "civiccast/**",
        "tests/native/**",
    }
    callers = [
        path
        for path in (ROOT / ".github" / "workflows").glob("*.yml")
        if path != workflow_path and workflow_path.name in path.read_text(encoding="utf-8")
    ]
    assert callers == [], "direct PR triggering must not also have a reusable-workflow caller"


def test_only_native_reproducibility_is_path_filtered_on_pull_requests() -> None:
    for workflow_path in WORKFLOWS[1:]:
        _text, workflow = _workflow(workflow_path)
        triggers = workflow.get("on", workflow.get(True))

        assert "paths" not in triggers["pull_request"], (
            f"{workflow_path.name} is a global gate and must not suppress checks "
            "for non-native pull-request changes"
        )


def test_expensive_python_jobs_keep_setup_uv_caching() -> None:
    for workflow_path in WORKFLOWS[1:]:
        _text, workflow = _workflow(workflow_path)
        setup_uv_steps = [
            step
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
            if str(step.get("uses", "")).startswith("astral-sh/setup-uv@")
        ]

        assert setup_uv_steps, f"{workflow_path.name} has no setup-uv cache owner"
        assert all(step["with"].get("enable-cache") == "true" for step in setup_uv_steps)


def test_global_gates_remain_valid_under_the_actions_budget_policy() -> None:
    for workflow_path in WORKFLOWS[1:]:
        assert validate_workflow(workflow_path, workflow_path.read_text(encoding="utf-8")) == []


def test_native_junit_workflow_floors_match_current_exact_collections() -> None:
    """ci-test.yml's `--floor` values must track a REAL collection, not drift silently.

    This does not hardcode an expected floor (that was the bug: two magic
    numbers -- 1429/1565 -- sat unmoved since the repo's first commit while
    the real tests/native count grew to 1675/1869, leaving 246 and 304 tests
    of undetected slack). Instead it re-derives the current truth the exact
    same way `test_native_marker_collections_match_the_workflow_floors` does
    (a live `pytest --collect-only` run) and checks the workflow's floor sits
    within NATIVE_JUNIT_FLOOR_MARGIN of it -- never above it (a floor higher
    than the real count would fail every run) and never more than the margin
    below it (a floor that far below the real count stops guarding
    anything).
    """
    _text, workflow = _workflow(WORKFLOWS[1])
    steps = {
        step["name"]: step["run"]
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "name" in step and "run" in step
    }

    pure_step = steps["Assert tests/native's platform-independent (pure) suite actually ran"]
    win_step = steps["Assert the full tests/native suite ran on Windows -- nothing may skip"]

    assert "--junit junit-native-pure.xml" in pure_step
    assert "--junit junit-native-win.xml" in win_step

    pure_floor_match = re.search(r"--floor (\d+)", pure_step)
    win_floor_match = re.search(r"--floor (\d+)", win_step)
    assert pure_floor_match, f"no --floor N found in the pure-suite step:\n{pure_step}"
    assert win_floor_match, f"no --floor N found in the Windows-suite step:\n{win_step}"
    pure_floor = int(pure_floor_match.group(1))
    win_floor = int(win_floor_match.group(1))

    pure_count = _collect_native_tests("not windows_only")
    # PR #143 review fix: the Windows step now filters -m "not integration"
    # (test_upgrade_engine_postgres.py's Docker/Postgres-gated D3 engine
    # proof would otherwise violate this job's own "nothing may skip"
    # contract on a runner that cannot reliably run Linux containers) --
    # count the same way the workflow step actually will.
    win_count = _collect_native_tests("not integration")

    assert pure_floor <= pure_count, (
        f"ci-test.yml's pure-suite --floor {pure_floor} exceeds the real "
        f"current collection ({pure_count}) -- every run would fail closed."
    )
    assert pure_count - pure_floor <= NATIVE_JUNIT_FLOOR_MARGIN, (
        f"ci-test.yml's pure-suite --floor {pure_floor} is {pure_count - pure_floor} "
        f"tests below the real current collection ({pure_count}), past the "
        f"documented NATIVE_JUNIT_FLOOR_MARGIN of {NATIVE_JUNIT_FLOOR_MARGIN}. "
        "Bump --floor in ci-test.yml (see the margin comment above this test)."
    )
    assert win_floor <= win_count, (
        f"ci-test.yml's Windows-suite --floor {win_floor} exceeds the real "
        f"current collection ({win_count}) -- every run would fail closed."
    )
    assert win_count - win_floor <= NATIVE_JUNIT_FLOOR_MARGIN, (
        f"ci-test.yml's Windows-suite --floor {win_floor} is {win_count - win_floor} "
        f"tests below the real current collection ({win_count}), past the "
        f"documented NATIVE_JUNIT_FLOOR_MARGIN of {NATIVE_JUNIT_FLOOR_MARGIN}. "
        "Bump --floor in ci-test.yml (see the margin comment above this test)."
    )


def test_linux_pure_native_lane_uses_the_explicit_platform_marker() -> None:
    _text, workflow = _workflow(WORKFLOWS[1])
    pure_run = next(
        step["run"]
        for step in workflow["jobs"]["test"]["steps"]
        if step.get("name") == "Run tests/native's platform-independent suite (junit)"
    )

    assert '-m "not windows_only"' in pure_run
    assert '-k "not win"' not in pure_run


def test_windows_native_lane_excludes_docker_gated_integration_tests() -> None:
    """PR #143 review fix companion to the pure-lane marker test above: the
    Windows job's own next step asserts "nothing may skip" across all of
    tests/native, so anything that can legitimately skip on that platform
    (Docker/Postgres-gated tests, currently only
    test_upgrade_engine_postgres.py) must be filtered OUT of what it runs,
    not left to skip and trip that assertion."""
    _text, workflow = _workflow(WORKFLOWS[1])
    win_run = next(
        step["run"]
        for step in workflow["jobs"]["windows-native"]["steps"]
        if step.get("name") == "Run tests/native (junit)"
    )

    assert '-m "not integration"' in win_run


def test_native_marker_collections_match_the_workflow_floors() -> None:
    collect = _collect_native_tests

    # 2026-07-31 stop-path fix batch: +9 pure (StopWatchdog + stop-position
    # breadcrumb, test_supervisor_service.py) and +6 windows_only (3 SvcStop
    # watchdog wiring in test_supervisor_service_win.py, 3 cancellable
    # accept-loop shutdown in test_supervisor_pipe_server_win.py).
    # 2026-07-31 gate/run-17 fix batch (G1-G5): +9 pure (1 core.py G1 repro
    # test_supervisor_core.py; 3 core.py G2 once-per-reason latch tests,
    # test_supervisor_core.py; 2 service.py G2 probe-wrapper-detail tests +
    # 2 service.py G4(b) stop-command-runner tests, test_supervisor_service.py;
    # 1 provision/conf.py G4(a) lame-duck config test,
    # test_provision_conf.py) and +1 windows_only (1 service.py G3 per-child
    # log redirection real-spawn test, test_supervisor_service_win.py).
    # 2026-07-31 pre-launch audit fix batch (F1/F2/F4): +12 pure (3 F1
    # poll_until_ready abort-seam tests, test_supervisor_children.py; 4 F1
    # Supervisor abort tests, test_supervisor_core.py; 3 F2 stop-watchdog
    # report-then-exit tests + 1 F4 file-backed stop-command test + 1 F1
    # shared-stop-event wiring test, test_supervisor_service.py) and +2
    # windows_only (F2 watchdog status-reporter wiring in BOTH ServiceFramework
    # subclasses, test_supervisor_service_win.py).
    # 2026-07-31 nats_broker.py set_wakeup_fd fix (Windows Sandbox service-run
    # diagnosis): +1 pure, +1 windows suite (no windows_only marker --
    # test_jetstream_publish_ack_survives_pythonservice_set_wakeup_fd_rejection,
    # test_supervisor_wiring_batch.py -- reproduces on any platform's real main
    # thread by patching signal.set_wakeup_fd, so it collects into both lanes).
    # 2026-07-31 app.py durable-store engine psycopg2->psycopg normalization
    # fix (Sandbox run 19 control-plane crash-loop, ModuleNotFoundError):
    # +5 pure, +5 windows suite (new tests/native/test_app_engine_url.py, no
    # windows_only marker -- engine construction only, no OS dependency, so
    # it collects into both lanes like the set_wakeup_fd fix above).
    # 2026-07-31 pg_ctl output-tail stream-precedence fix (forensically
    # proven: `err_text or out_text` / `_decode_output(stderr) or
    # _decode_output(stdout)` deterministically discarded the real FATAL
    # diagnosis when it landed on the OTHER stream): +4 pure, +4 windows
    # suite (1 pg_ctl_exec.py both-streams regression test,
    # test_pg_ctl_exec.py; 3 provision/seams.py both-streams-surface tests
    # for initdb + the two psql call sites, test_provision_seams.py -- no
    # windows_only marker, so all four collect into both lanes).
    # 2026-08-01 row-4b pgdata ACL normalization (Sandbox run 21: the update
    # path's D3 `pg_ctl start` died with `FATAL: could not open file
    # "pg_wal/000000010000000000000002": Permission denied`, because pg_ctl
    # runs the postmaster under a restricted token with BUILTIN\Administrators
    # deny-only and the WAL segments were created by the LocalSystem service):
    # +20 pure, +22 windows suite. 15 pure tests in the new
    # tests/native/test_pgdata_acl.py (descriptor + injected-seam decision
    # logic, no OS dependency, so they collect into both lanes), 2 in
    # tests/native/test_pgdata_acl_win.py (real SetNamedSecurityInfo +
    # propagation -- windows_only, so the WINDOWS lane only), and 5 wiring
    # tests split across test_upgrade_pg_lifecycle.py (2) and
    # test_provision_seams.py (3), which collect into both lanes.
    # 2026-08-01 pgdata-DACL audit follow-up (F1/F2/F4/F5/F6 accuracy and
    # test-quality closure): +1 pure, +5 windows suite. 1 pure test in
    # test_provision_cli.py (F6: PgDataAclError from migrate_provisioned_
    # schema's second normalize call gets its own exit code/message, not
    # alembic's -- no OS dependency, collects into both lanes) and 4 new
    # windows_only tests in test_pgdata_acl_win.py (F1: an explicit child
    # BUILTIN\Users ACE survives propagation; F2: propagation stops at a
    # protected child directory and the call still succeeds; F5 x2: a
    # pg_ctl-shaped restricted token's effective READ+WRITE access, positive
    # and negative control), all four windows_only, so the WINDOWS lane only.
    # 2026-08-01 control-plane media wiring (installer rehearsal flow was
    # missing CIVICCAST_UPLOAD_DIR and a resolvable ffmpeg/ffprobe): +14
    # pure, +14 windows suite -- none windows_only, so every new test
    # collects into both lanes. install_layout.py gained ffmpeg_bin_dir/
    # ffmpeg_exe_path/ffprobe_exe_path/upload_dir (pure path arithmetic, no
    # dedicated new tests, folded into the existing A1 ground-truth test);
    # children.py gained default_upload_dir() + control_plane_child_spec's
    # unconditional CIVICCAST_UPLOAD_DIR (4 new tests,
    # test_supervisor_children.py); service.py gained
    # build_control_plane_media_env (the ffmpeg PATH-prepend gate, 3 tests)
    # and build_production_service's control_plane_env merge + the
    # no-eager-filesystem-I/O regression pin (3 tests), all in
    # test_supervisor_wiring_batch.py -- 10 new test functions authored this
    # slice.
    # 2026-08-01 D3 pg-client wiring fix (Sandbox run 22: the upgrade engine
    # aborted at step 3 BACKUP_VERIFIED and rolled back on every real machine
    # because __main__ never passed pg_dump/pg_dumpall/pg_restore/psql into
    # build_default_seams, leaving dr/backup.py on bare names resolved through
    # a PATH that never contains the staged pack bin directory): +4 pure, +4
    # windows suite -- 4 new tests in test_upgrade_cli.py (absolute-path
    # wiring at the build_default_seams call boundary; the resolver reusing
    # D4 provisioning's single path convention; the step-identifying
    # missing-binary error; the backup-seam guard firing before the backup
    # runs). String/path arithmetic and monkeypatched seams only, no OS
    # dependency, so all four collect into both lanes. These +4 in each lane
    # are the remainder the media-wiring slice above could not attribute; the
    # test_provision_cli.py / test_pgdata_acl_win.py additions it named were
    # already inside the previous 1397/1533 floors.
    # 2026-08-01 ffmpeg native packaging (concurrent worker, this same shared
    # working tree): +18 pure, +18 windows suite -- scripts/build_native_ffmpeg_pack.py
    # + tests/native/test_build_native_ffmpeg_pack.py + tests/dr/test_pg_tool_spawn_diagnostics.py
    # and related acquisition_catalog.rs/native_pack_staging.rs coverage,
    # authored by the parallel ffmpeg-packaging worker (out of this slice's
    # scope -- see that worker's own accounting for the precise per-file
    # split). Re-derived from an actual `pytest --collect-only` run rather
    # than assumed, per this slice's own coordination note.
    # 2026-08-01 native front door (chain B-min: the control plane served
    # neither /operator/ nor / on a native station, and had no setup nonce at
    # all, so first-run setup could not be completed): +13 pure, +13 windows
    # suite -- none windows_only, so every new test collects into both lanes.
    # 4 in the new tests/native/test_native_front_door.py (both portals served
    # when the station env points at them; _configured_static_dir now reports
    # an unset OR missing dist at ERROR level instead of silently returning
    # None; the present-directory control). 3 in test_station_runtime.py
    # (load_native_station_environment emits CIVICCAST_OPERATOR_CONSOLE_DIST /
    # CIVICCAST_PUBLIC_PORTAL_DIST, and CIVICCAST_SETUP_NONCE when one is
    # persisted -- omitted, never blank, when it is not). 6 in
    # test_provision_cli.py (the new civiccast/native/setup_nonce.py generator
    # + shared validation envelope, and the SETUP_NONCE marker line's
    # round-trip / non-collision with the DatabaseUrl marker). Path arithmetic,
    # env dicts, and TestClient app-factory assertions only -- no OS
    # dependency. Re-derived from an actual `pytest --collect-only` run.
    # 2026-08-01 native front door, part 2 (chains G + H, from the R7
    # real-hardware install of the fixed candidate): +6 pure, +9 windows
    # suite.
    #   Chain G (a native install never wrote the ActiveRuntime selector its
    #   own dual-runtime guard starts on): +3, ALL windows_only -- they live
    #   in test_win_probes.py, whose module-level pytestmark is windows_only,
    #   so they collect into the windows lane ONLY. They restate the Rust
    #   installer's registry write with raw winreg and read it back through
    #   win_probes.read_selector into runtime_guard.decide.
    #   Chain H1 (first-run downloads targeted Program Files, which the
    #   non-elevated GUI cannot write): +6 pure, collecting into BOTH lanes --
    #   4 in test_station_runtime.py (the two-root caption-tier search: the
    #   preference order, a tier found under the writable acquisition root, an
    #   installer-staged tier still winning, and no implicit ProgramData probe
    #   when no acquisition root is supplied) and 2 in
    #   test_supervisor_wiring_batch.py (InstallLayout's acquisition roots, and
    #   the ollama store search including the writable root last). Path
    #   arithmetic and tmp_path trees only, no OS dependency.
    # 2026-08-01 dual-runtime guard, D3 row 4 amendment (chain I: an explicit,
    # validly-written ActiveRuntime=native IS the authority basis for a native
    # start, so an UNREADABLE A2 under it degrades and logs instead of
    # withholding the station -- R7's WSL-less box can never produce a readable
    # A2, because wsl.exe there is the OS inbox stub): +9 pure, +9 windows
    # suite. None windows_only, so every new test collects into both lanes.
    # 4 in test_guard_table.py (the unchanged absent-as-native block; the new
    # P6 property over the 18-point cell; the differential test asserting the
    # 3888-point table changed EXACTLY those 18 points against a frozen
    # pre-chain-I oracle; that differential's own non-vacuity control). 3 in
    # test_guard_monitor.py (an A2 probe RAISE under an explicit native
    # selector degrades rather than blocks; a degraded start still
    # controlled-stops within one interval when A2 turns readable-POSITIVE, so
    # D5 is not weakened; the degraded decision is recorded in the monitor log
    # with probe and reason and never advances AC9's alert counter). 2 in
    # test_supervisor_core.py (the structured degraded WARNING fires once per
    # spawn with action/named_probe/reason, and its negative control -- a plain
    # start logs no such line). Existing tests were RETARGETED rather than
    # deleted: AC9's alert-after-3, the F6 A2-raise mapping, and the
    # alert-threshold mutation control now run on the still-blocking
    # absent-as-native cell, so the alert mechanism keeps its proof.
    # 2026-08-01 chain L (the operator console 404'd on a real installed
    # station -- TESTER2 request-0050c): +4 pure, +4 windows suite. None
    # windows_only. 2 in test_native_front_door.py (the TESTER2 reproduction:
    # a not-yet-activated station's control-plane env, driven end to end
    # through the real provider and an HTTP GET /operator/; and the packaged
    # portal paths resolving on a miniature EXTRACT-shaped layout). 2 in
    # test_station_runtime.py (the pre-activation overlay carries the front
    # door and the D4 setup nonce; and never carries an activated-station
    # marker). Two existing tests were RETARGETED rather than deleted --
    # test_station_runtime.py's portal-path pin now asserts the
    # package-derived path AND its existence, and
    # test_supervisor_wiring_batch.py's A2 pre-activation test now asserts the
    # narrowed overlay instead of a wholly empty env.
    # 2026-08-01 chain M4 (F-13, sandbox newcomer re-walk dd7f835f: the blank
    # black runtime\python.exe console left on the operator's desktop WAS the
    # live control plane): +2 pure, +2 windows suite. None windows_only -- both
    # skip at RUNTIME when subprocess has no CREATE_NO_WINDOW (i.e. off
    # Windows) rather than being deselected by marker, matching how the other
    # creationflags pins in this repo are written. Both in
    # test_supervisor_service.py: every supervisor child is spawned windowless,
    # and the anti-regression half -- suppressing the window disturbs neither
    # RAT-004's control-plane-only process group nor G3's file-backed
    # stdout/stderr capture.
    # Re-derived from an actual `pytest --collect-only` run, not assumed.
    # 2026-08-01 upgrade-path fixes (chain K: the D3 upgrade engine imported
    # psycopg2, a driver the product has never shipped, and an uninstalled
    # machine's preserved data was mistaken for an installed product --
    # real-hardware R7 request 0053b): +26 pure, +26 windows suite. None
    # windows_only, so every new test collects into both lanes and the two
    # deltas are identical.
    #   K1: +6 in the new tests/native/test_upgrade_backup_psycopg2_free.py
    #   (the psycopg2-absent execution of D3 step 3's restore-drill DB layer;
    #   the create_engine-boundary driver pin; the credential-preservation pin
    #   on the new _verification_engine_url; the proof that step 3's backup
    #   seam actually reaches the drill; and the two environment controls that
    #   keep those from going vacuous -- the import block is effective, and
    #   psycopg v3 is importable). sys.meta_path blocking and a refused
    #   127.0.0.1:1 connect only, no OS dependency.
    #   K2: +20 in the new tests/native/test_upgrade_routing.py -- 14 test
    #   functions, 6 of the collected items coming from three parametrized
    #   ones (3 data-remnant combinations that must never select the upgrade
    #   route, 4 documented sc-query exit codes, 2 probe-failure modes). Pure
    #   decision-function calls plus in-process main() runs with the engine
    #   monkeypatched; the one test that uses the real SCM probe carries its
    #   own skip guard, so it collects unconditionally in both lanes.
    #   NOT counted here: tests/policy/test_shipped_payload_db_driver.py (K1's
    #   shipped-wheel guard). It lives in tests/policy, which neither lane
    #   collects, so it cannot move these numbers; and
    #   tests/native/test_upgrade_cli.py gained only an autouse fixture --
    #   its test-function count is unchanged at 17, base and tip.
    # Arithmetic check, not assumption: 1457 + 26 = 1483 and 1596 + 26 = 1622,
    # and 6 + 20 = 26 accounts for every added item by file.
    # Both numbers below come from an actual `pytest --collect-only -q
    # --disable-warnings [-m "not windows_only"] tests/native` run in this
    # worktree, counted with this test's own line-matching rule.
    #
    # MERGE RESOLUTION (coordinator, 2026-08-02): chain K branched from
    # 947816fc, which predates chain L's +4/+4, so K's own numbers read
    # (1483, 1622) on its branch while L had moved the same line to
    # (1461, 1600). The resolution is ADDITIVE -- L's four tests and K's
    # twenty-six both exist on the merged tree -- and the numbers below were
    # re-derived by an actual `--collect-only` run ON THE MERGED TREE, not by
    # adding the two branches' figures together. They happen to agree with
    # that arithmetic; the run is what they are pinned to.
    #
    # Chain M (the four criticals from the newcomer walkthrough) branched from
    # 076c35f6 and re-derived its own floor to (1463, 1602) there; on this
    # merged tree, which also carries chains K and N, the same additive
    # resolution applies. Re-derived by `--collect-only` ON THE MERGED TREE:
    # was (1489, 1628).
    #
    # 2026-08-02 walk-blocker batch (b1c6fe4d re-walk findings): the four
    # merged fixes added +11 tests/native items, ALL pure (not windows_only) --
    # entirely from N-15 (fix/reinstall-provision-n15): +10 provision
    # adopt/decision/orchestrator/seams tests plus +1 reset_cluster_credential
    # finally-contract test. N-01's new tests live in tests/certs, N-02's in
    # main.rs (Rust), F-11's in tests/policy -- none touch tests/native. So both
    # numbers move by exactly +11: (1489, 1628) -> (1500, 1639). Re-derived by
    # an actual `--collect-only` run ON THE MERGED TREE, not by arithmetic.
    #
    # 2026-08-05 native payload reproducibility repair: +4 pure / +4 Windows
    # suite. The four tests prove the reviewed PyAV diagnostic, native app lock,
    # configured MSVC redist root, and build-toolchain lock identity; none need
    # Windows-only system behavior. Re-derived by this test's own collection
    # commands in the release worktree: (1500, 1639) -> (1504, 1643).
    #
    # 2026-08-05 detector routing repair: 30 tests that call Win32 APIs or
    # require Windows path semantics were misclassified as pure. They now run
    # only in the full native-Windows suite, so the accurate collections are
    # (1504, 1643) -> (1474, 1643). Three further tests carried runtime
    # skips for native Windows-only APIs; they are now marked accurately, so
    # the Linux pure lane does not treat a skip as execution evidence.
    # main-reconcile (2026-08-06): the rc18/native union added tests on both
    # sides of the July fork: (1471, 1643) -> (1508, 1680).
    # 2026-08-09 CI re-derivation: #380/#381 pin tests plus the 2026-08-07..08
    # release-lane additions added +24 pure / +24 Windows suite:
    # (1508, 1680) -> (1532, 1704).
    # 2026-08-09 (same day, slice-b0): the two connect-timeout pin regression
    # tests (sol audit R2-F3) collect in both lanes: (1532,1704) -> (1534,1706).
    # 2026-08-11 B3-B5 integration added 14 platform-independent native tests,
    # moving both collection floors: (1534, 1706) -> (1548, 1720).
    # 2026-08-12 B5 supervisor reconciliation fix (TESTER2's
    # b5-failed-supervisor-ollama-reconciliation-timeout): net +6 in
    # tests/native/test_supervisor_ollama_child.py -- seven new tests for the
    # throttled re-check of a SKIPPED optional child, less the deleted
    # test_skip_is_durable_across_ticks, which asserted the very latch that
    # produced the field failure. All pure (fake runner/clock/provider, no OS
    # dependency), so both lanes move by the same +6:
    # (1548, 1720) -> (1554, 1726). Re-derived by this test's own collection
    # commands in the fix worktree, not by arithmetic.
    # 2026-08-12 TESTER4 adversarial review of PR #389: +2 for the shared
    # stop-intent boundary (CC-WS5-016) -- the mid-tick operator-drain
    # interleaving regression and the pipe-vs-SCM stop-signal symmetry guard.
    # Both pure: (1554, 1726) -> (1556, 1728). Re-derived by an actual
    # --collect-only run, not by arithmetic.
    # 2026-08-13 setup-handoff recovery: tests/native/test_setup_handoff_recovery.py
    # adds 11 tests for the operator-console handoff recovery path
    # (`civiccast runtime setup-handoff`). Every registry interaction there goes
    # through an injected fake, so all 11 collect in BOTH lanes -- exactly the
    # tests/native/test_runtime_cli.py convention.
    #
    # MERGE RESOLUTION (2026-08-13): this branch forked at (1548, 1720) and
    # added its own +11/+11 there. Meanwhile the base picked up PR #389's +6/+6
    # and TESTER4's +2/+2, landing at (1556, 1728). The resolution is ADDITIVE
    # -- this PR's 11 tests and the base's 8 both exist on the merged tree --
    # and the numbers below were re-derived by an actual `--collect-only` run
    # ON THE MERGED TREE, not by adding the two sides' figures together.
    #
    # 2026-08-14 (1567, 1739) -> (1579, 1770). TWO separate deltas, and the
    # floor was already stale before this branch touched it:
    #
    #   +0 pure / +19 windows_only comes from the BASE, not from here. Commit
    #   82b7a4fb (CC-WS4-002, LocalSystem-safe WSL distro inventory) added 19
    #   tests to tests/native/test_win_probes.py, a windows_only file, and did
    #   not move this floor -- so release/native-beta-1.0.0-beta.1-rc1 has been
    #   failing this check on its own since 2026-08-13, at (1567, 1758). That
    #   red was inherited here, not caused here.
    #
    #   +12 pure / +12 windows_only is this branch (CC-PG-JOB): 3 in
    #   test_supervisor_core.py (containment-fault rollback ordering, forensics
    #   logging, foreign-job membership reaching ready) and 9 in
    #   test_supervisor_job_object.py (containment diagnostics, and the
    #   provenance-gated acceptance fail-closed cases). Both files are pure --
    #   FakeJobObjectApi never imports win32job -- so they collect in BOTH
    #   lanes and move the two numbers identically.
    #
    #   1567 + 12 = 1579 pure; 1739 + 19 + 12 = 1770 total.
    #
    #   Then +0 pure / +1 windows_only, same day, same branch: the no-registry
    #   guard test added to tests/native/test_win_probes.py for the
    #   ModuleNotFoundError that CC-WS4-002 introduced alongside the 19 above.
    #   1770 + 1 = 1771 total.
    #
    # 2026-08-16 (1579, 1771) -> (1607, 1799). TWO separate deltas here too,
    # and the floor was ALREADY stale before this task touched it:
    #
    #   +9 pure / +9 windows_only comes from the BASE (commits 6217c965/
    #   b3cb5977, hardware-adaptive captions + presence-gated GPU selection,
    #   both already landed on this branch before this task started): 9 new
    #   pure tests in tests/native/test_station_runtime.py (none marked
    #   `windows_only`), and this floor was never moved for them. Confirmed
    #   both by `git diff 9a3c8bda..b3cb5977 -- tests/native/
    #   test_station_runtime.py | grep -c '^+def test_'` (= 9) and by an
    #   actual `--collect-only` run on the tree exactly as those two commits
    #   left it (before this task's own file existed): (1588, 1780).
    #
    #   +19 pure / +19 windows_only is this task's own native-cuda-runtime
    #   pack builder work: tests/native/test_build_native_cuda_pack.py adds
    #   19 tests, none marked `windows_only` (pure zipfile/hash fixtures, no
    #   OS dependency), so all 19 collect in both lanes.
    #
    #   1588 + 19 = 1607 pure; 1780 + 19 = 1799 total.
    #
    # 2026-08-16 (1607, 1799) -> (1608, 1800). K1 fix (flat-layout station
    # activation, native_activation.rs::activate_flat_station_with): +1 pure,
    # +0 windows_only -- tests/native/test_station_runtime.py gains
    # `test_flat_layout_activation_files_validate_directly_at_install_root`
    # (a pure fixture/JSON test, no OS dependency, not marked `windows_only`),
    # proving the flat-layout station-set.json/activation-self-test.json
    # schema `native_activation.rs`'s new writer emits is accepted by
    # `load_native_station_environment`. Confirmed by `git diff
    # HEAD -- tests/native/test_station_runtime.py | grep -c '^+def test_'`
    # (= 1).
    #
    # 2026-08-16 (1608, 1800) -> (1620, 1812). K1 follow-up (station-bundle
    # publisher, scripts/build_native_station_bundle.py): +12 pure,
    # +0 windows_only -- the new tests/native/test_build_native_station_bundle.py
    # adds 12 tests (pure zipfile/JSON/ed25519 fixtures, no OS dependency, none
    # marked `windows_only`), so all 12 collect in both lanes. Confirmed by
    # `git diff HEAD -- tests/native/test_build_native_station_bundle.py |
    # grep -c '^+def test_'` (= 12).
    #
    # 2026-08-16 (1620, 1812) -> (1625, 1817). K1 CI round-trip fix (run
    # 31979342933 failed self-verification of the first Ollama model pack
    # with "missing model_name metadata" -- build_native_station_bundle.py
    # built those three packs' metadata as a bare {"source_root": ...},
    # never satisfying native_packs._validate_ollama_model_contract's
    # model_name/manifest_sha256/ollama_runtime_version requirement): +5
    # pure, +0 windows_only -- tests/native/test_build_native_station_bundle.py
    # gains 5 tests proving the new `_ollama_model_pack_metadata` helper
    # (sourced from MODEL-PROVENANCE.json) both fails loud on a missing/
    # mismatched provenance file AND, via a monkeypatched reviewed lock
    # (pure fixture bytes, no OS dependency, none marked `windows_only`),
    # actually PASSES `verify_native_pack` for a correctly-provenanced pack
    # -- the positive path the prior fixture-rejection test alone could not
    # prove. Confirmed by `git diff HEAD -- tests/native/
    # test_build_native_station_bundle.py | grep -c '^+def test_'` (= 5).
    #
    # Re-derived by an actual `--collect-only` run on this tree, not by
    # arithmetic: local collection returned (1607, 1799).
    #
    # K2 fix (2026-08-16): +2 pure / +2 windows_only --
    # tests/native/test_station_runtime.py gained
    # test_station_environment_injects_gstreamer_at_the_real_install_path and
    # test_station_environment_does_not_inject_gstreamer_at_the_pre_fix_wrong_path,
    # both plain tmp_path fixtures with no OS-specific behavior (not marked
    # `windows_only`), so both collect in both lanes: (1607, 1799) -> (1609,
    # 1801). Re-derived by an actual `--collect-only` run on this tree, not by
    # arithmetic.
    #
    # K2 crash-degrade follow-up fix (2026-08-16, fix/k2-gstreamer-degrade,
    # this task): +2 pure / +2 windows_only --
    # tests/native/test_station_runtime.py gained
    # test_station_environment_degrades_past_a_corrupt_gstreamer_closure and
    # test_station_environment_for_python_degrades_past_a_corrupt_gstreamer_closure,
    # proving the CRITICAL finding that an unwrapped
    # `installed_gstreamer_environment` call let a partial/corrupt GStreamer
    # closure's `GstreamerRuntimeError` crash the whole supervisor instead of
    # degrading to the FFmpeg egress path. Both are plain tmp_path fixtures
    # with no OS-specific behavior (not marked `windows_only`), so both
    # collect in both lanes. Re-derived after rebasing onto release with #407
    # (nvblas test, base 1610/1802) and #405 (K3): (1610, 1802) -> (1612, 1804).
    # Re-derived by an actual `--collect-only` run on this tree, not by arithmetic.
    #
    # K2 degraded-mode + recovery state machine (2026-08-16, this task's real
    # implementation superseding the no-crash floor): +3 pure / +3 windows_only
    # -- tests/native/test_station_runtime.py gained
    # test_corrupt_closure_self_repair_success_runs_gstreamer_normally (tier 2),
    # test_unrepaired_corrupt_closure_switches_the_selector_to_ffmpeg (tier 3 /
    # the Codex P1: the selector actually switches to FFmpeg), and
    # test_corrupt_closure_self_repair_hook_that_raises_falls_back_to_ffmpeg
    # (a raising repair hook never crashes the supervisor). The two pre-existing
    # degrade tests were strengthened in place, not added, so they do not move
    # the count. All three are plain tmp_path fixtures with no OS-specific
    # behavior (not marked `windows_only`), so all three collect in both lanes:
    # (1612, 1804) -> (1615, 1807). Re-derived by an actual `--collect-only` run
    # on this tree, not by arithmetic. (The tier-3 alert, tier-4 slate, and
    # tier-5 recovery tests live under tests/egress, which this floor does not
    # cover.)
    #
    # K1 flat-layout activation + station-bundle publisher (2026-08-17, this
    # branch, rebased onto release at 10b6b2cb5 which already carries K2/K3):
    # tests/native gained the flat-layout activation test plus the native
    # station-bundle / CUDA-pack / setup-handoff / win-probe suites this slice
    # ships. Re-derived by an actual `--collect-only -m "not windows_only"` and
    # unmarked run on THIS rebased tree, not by arithmetic: (1615, 1807) ->
    # (1633, 1825).
    #
    # W-5 audit-UX-wave fix (2026-08-19, fix/audit-ux-wave): +1 pure / +1
    # windows_only -- tests/native/test_native_front_door.py gained
    # test_unknown_api_path_gets_a_json_404_not_the_spa_shell, pinning the
    # SpaStaticFiles catch-all fix (an unmatched `/api/*` path now gets a
    # real `application/json` 404 instead of the SPA's index.html at status
    # 200; `/` and `/operator/` still serve their shells). Plain
    # `TestClient(create_app())` + `tmp_path` fixture, no OS-specific
    # behavior, not marked `windows_only`, so it collects in both lanes:
    # (1633, 1825) -> (1634, 1826). Re-derived by an actual `--collect-only`
    # run on this tree, not by arithmetic.
    #
    # Revival of PR #390 (2026-08-19, fix/supervisor-log-durability): the
    # supervisor-logging diagnosability fix (fsync-durability + startup
    # canary for supervisor.log, postgres/nats -l flag wiring, control-plane
    # -u flag) ported onto this evolved tree. 12 new platform-independent
    # tests land in tests/native/test_supervisor_service.py (3),
    # test_supervisor_children.py (5), and test_supervisor_core.py (3) --
    # none marked `windows_only`, so both lanes move by +12. Re-derived by an
    # actual `--collect-only` run on this tree, not by arithmetic: (1634,
    # 1826) -> (1646, 1838).
    #
    # WSL/Linux-leftover purge wave 2 (2026-08-21): -9 pure, 0 windows_only --
    # tests/native/test_inventory_reconciliation.py deleted along with
    # scripts/prove_native_inventory_reconciliation.py, which required
    # --wsl-installer/--wsl-extracted-root inputs and reconciled the shipped
    # WSL runtime inventory against the native plan. The WSL side's required
    # inputs (the rc18 installer's bootstrap, resolved Linux requirements,
    # wheelhouse manifest) do not exist in this repository, so the script and
    # its 9 tests (none marked windows_only, all plain-fixture) could never
    # run here. Re-derived by an actual `--collect-only` run on this tree,
    # not by arithmetic: (1646, 1838) -> (1637, 1829).
    #
    # PR #13 (2026-08-21, fix/postgres-launcher-log-sharing): 5 new
    # platform-independent pinning tests for the pg_ctl launcher-stdio split
    # (tests/native/test_supervisor_children.py, test_supervisor_service.py);
    # none marked `windows_only`, so both lanes move by +5. Re-derived by an
    # actual `--collect-only` run on this tree: on top of wave 2: (1637, 1829) -> (1642, 1834).
    #
    # perf/self-hosted-candidate-build (2026-08-24): +6 pure, 0 windows_only --
    # the --advisory-pyav-wheel-hash plumbing for native-beta-candidate-
    # artifacts.yml's self-hosted build lane gained test coverage in
    # tests/native/test_pyav_wheel_builder.py (5: verify_artifact's advisory
    # mode warns instead of raising on a mismatch; is silent on a match;
    # advisory=False still raises by default; main() forwards
    # --advisory-wheel-hash through to build(); main() defaults it to False)
    # and tests/native/test_app_payload_builder.py (1:
    # build_reviewed_pyav_wheel forwards advisory_wheel_hash=True as
    # --advisory-wheel-hash to the PyAV subprocess). Plain function calls,
    # monkeypatched subprocess.run, and captured stdout only -- no OS
    # dependency, no windows_only marker, so all six collect into both
    # lanes. Re-derived by an actual `--collect-only` run on this tree, not
    # by arithmetic: (1642, 1834) -> (1648, 1840).
    #
    # NATS removal (2026-08-21, owner decision 2026-08-20; ADR 0023 supersedes
    # ADR 0001): -31 pure, -31 windows_only -- NATS JetStream is cut from the
    # product, and every test that pinned NATS-specific behavior under
    # tests/native went with it: the ``nats`` child spec and its JetStream
    # readiness check (test_supervisor_children.py), the NATS store/config
    # provisioning seams (test_provision_seams.py) and their orchestrator
    # wiring (test_provision_orchestrator.py), the ``render_nats_conf``
    # config renderer (test_provision_conf.py), ``NatsTlsFiles`` /
    # ``evaluate_nats_store`` (test_provision_models.py), and two
    # NATS-phase parametrize cases dropped from an existing journal test
    # (test_provision_cli.py). None were marked `windows_only` (NATS's
    # Windows behavior was covered by the same plain-fixture tests as every
    # other platform), so both lanes move by the same -31. Re-derived by an
    # actual `--collect-only` run on this tree, not by arithmetic: (1642, 1834)
    # -> (1611, 1803).
    #
    # NATS removal, continued (2026-08-21): -7 pure, -7 windows_only -- the
    # same removal finished across the four supervisor-service-layer test
    # files that construct/wire a live Supervisor end to end
    # (test_supervisor_service.py, test_supervisor_service_win.py,
    # test_supervisor_wiring_batch.py, test_supervisor_ollama_child.py):
    # the `nats_probe`/`nats_server_path`/`nats_config_path` wiring and the
    # JetStream publish-ack readiness-probe tests these files pinned went
    # with the rest of NATS. None were marked `windows_only`, so both lanes
    # move by the same -7. Re-derived by an actual `--collect-only` run on
    # this tree, not by arithmetic: (1611, 1803) -> (1604, 1796).
    #
    # Merge of chore/cut-nats-broker into main (2026-08-21): the NATS cut
    # removed nats-only tests while mainline PRs added S14/S1/S3/holes tests;
    # re-derived on the MERGED tree by an actual --collect-only run:
    # -> (1610, 1802).
    #
    # fix/self-hosted-lane-av-wheel (2026-08-24): +4 pure, 0 windows_only --
    # candidate run 32806127399 failed self-hosted with "Failed to download
    # `av==18.0.0` / Hash mismatch": --advisory-pyav-wheel-hash was wired
    # into build_native_pyav_wheel.py's own byte-exact check on the compiled
    # wheel, but build_native_app_payload.py's install_pinned_dependencies()
    # still ran one unconditional `uv pip install --require-hashes -r
    # requirements-native-app.txt`, re-enforcing the same hosted-reviewed
    # hash the build step had just accepted a self-hosted wheel against with
    # only a warning. install_pinned_dependencies() now takes the same
    # advisory_pyav_wheel_hash flag build() receives and splits the install
    # in two when set: av by verified-unique filename with no hash check of
    # its own, everything else still --require-hashes against the unmodified
    # lock. 4 new platform-independent tests land in
    # tests/native/test_app_payload_builder.py: the advisory split-install
    # path (av installs separately, unhashed; the rest still hash-checked via
    # a filtered, then-deleted, lock copy), the unset-flag path staying the
    # single unified hosted-lane invocation byte-for-byte, and two direct
    # unit tests for the shared _requirements_lock_without_av() helper (the
    # av-filtering regex extracted out of download_pinned_dependency_wheels()
    # so both call sites share one implementation). Plain function calls and
    # monkeypatched subprocess.run only -- no OS dependency, no
    # windows_only marker, so all four collect into both lanes. Re-derived by
    # an actual `--collect-only` run on this tree, not by arithmetic: (1610,
    # 1802) -> (1614, 1806).
    #
    # fix/self-hosted-lane-idempotent-scratch (2026-08-25): +5 pure, 0
    # windows_only -- candidate run 32810709045 failed self-hosted at
    # "Bootstrap the reviewed Python build environment": `uv sync` refused
    # `civiccast-build-venv` as "not a valid Python environment (no Python
    # executable was found)" because the PREVIOUS failed self-hosted run
    # (32806127399) died mid-`uv sync` and left a half-created venv there --
    # self-hosted's `_work\_temp` persists across runs (a hosted runner is
    # always fresh), so every self-hosted-lane scratch dir under it needs to
    # be idempotent, not just this one. Inventoried every RUNNER_TEMP-scoped
    # scratch dir across both build jobs against the workflow + the scripts
    # it calls and found the same shape twice more: build_native_app_payload
    # .py's build(), build_native_pyav_wheel.py's build(), and
    # build_native_runtime_closure.py's build() all refuse a non-empty
    # output directory, which a hosted runner's always-fresh RUNNER_TEMP
    # never triggers but a self-hosted rerun after a mid-build failure
    # would. civiccast-msvc-build-tools is the one persistence WORTH
    # keeping (a real MSVC Build Tools install, not cheap to redo) --
    # install_msvc() now verifies (real cl.exe/link.exe launch-and-version
    # check, the same one a fresh install already trusts) and reuses a
    # valid existing tree instead of always reinstalling, clears an invalid
    # one, and -- a case a live follow-up on this exact candidate surfaced,
    # where the runner's own attempt to clear an invalid MSVC tree itself
    # left an undeletable, unknown-completeness leftover behind
    # (vctip.exe/mspdbsrv.exe still holding files open) -- falls back to a
    # uniquely suffixed sibling directory rather than failing the job when
    # the invalid tree cannot be removed, with main() re-exporting the
    # actual resolved path to GITHUB_ENV so every later step that reads
    # $env:CIVICCAST_MSVC_INSTALLATION_PATH picks it up automatically. The
    # cheap-to-rebuild scratch dirs (civiccast-app-payload, civiccast-
    # app-payload-scratch, civiccast-gstreamer-closure) get the simpler fix:
    # a new self-hosted-only step always clears them first, hard-failing
    # with a clear diagnostic in the (believed novel, unlike MSVC) case
    # where something still holds one open. Ollama/captions-floor caches
    # (build-native-station-bundle) and the toolchain/pack-build download
    # caches were checked and are already safe as-is: every one of them
    # downloads to a `.partial` file, hash-verifies it, and only THEN
    # atomically renames it into place (confirmed by reading
    # fetch_locked_artifact() and provision_native_ollama_models.py's
    # _verify_file() call sites) -- a killed download can never leave a
    # cache entry a later run would wrongly trust. 5 new
    # platform-independent tests land in
    # tests/native/test_build_toolchain_provisioner.py: install_msvc()
    # reuses an already-verified install without reinstalling, replaces an
    # invalid one at the same canonical path, relocates to a sibling path
    # when the invalid one cannot be removed, and main() re-exports the
    # relocated path to GITHUB_ENV (or leaves GITHUB_ENV untouched when
    # nothing relocated). Plain function calls and monkeypatched subprocess
    # .run/shutil.rmtree only -- no OS dependency, no windows_only marker,
    # so all five collect into both lanes. Re-derived by an actual
    # `--collect-only` run on this tree, not by arithmetic: (1614, 1806) ->
    # (1619, 1811).
    #
    # Same branch, fixup (2026-08-25): +1 pure, 0 windows_only -- PR #31's
    # CI (not local: CI's tests/native "pure" lane runs on a Linux runner,
    # this box is Windows) failed test_main_reexports_a_relocated_msvc_path_
    # to_github_env and test_main_does_not_touch_github_env_when_msvc_
    # install_is_not_relocated with "the native Windows toolchain must be
    # provisioned on Windows" -- both called provisioner.main(), which is
    # correctly, unconditionally Windows-gated (os.name != "nt"), so they
    # never reached the GITHUB_ENV logic they meant to test at all. Not a
    # GITHUB_ENV ambient-variable collision (both already isolated it via
    # monkeypatch.setenv). Fixed at the root: the GITHUB_ENV re-export logic
    # is now its own function, reexport_relocated_msvc_install() -- pure,
    # no os.name/os.environ read, GITHUB_ENV passed as an explicit argument
    # -- and main() is three lines calling it. The two OS-gated tests are
    # replaced by three tests against the extracted function directly
    # (relocated writes GITHUB_ENV; not-relocated is a no-op; a None
    # github_env does not raise), net +1 over the two removed. Re-derived by
    # an actual `--collect-only` run on this tree, not by arithmetic: (1619,
    # 1811) -> (1620, 1812).
    #
    # fix/self-hosted-lane-av-provenance (2026-08-25): +6 pure, 0
    # windows_only -- candidate run 32822175257 (self-hosted): #30's
    # advisory posture got the locally-built av wheel through the uv
    # install step, but the pack build's INDEPENDENT deny-by-default
    # provenance sweep in scripts/verify_native_app_payload.py (run AFTER
    # the build, from the assembled tree on disk -- a separate check from
    # install_pinned_dependencies) still required the retained WHEELS/
    # av-*.whl to match the reviewed byte hash exactly, so it failed with
    # "WHEELS/av-18.0.0-cp311-abi3-win_amd64.whl is not an authorized
    # retained dependency wheel" plus every one of av's installed files
    # "named by no wheel RECORD" (the wheel was never authorized, so none
    # of its members were ever added to the ownership map).
    # advisory_pyav_wheel_hash now threads one layer deeper: on a
    # byte-hash miss for `av` specifically (name/version pin still
    # enforced), _retained_dependency_wheel_provenance() authorizes it
    # instead by BUILD PROVENANCE -- re-asserting the wheel's own embedded
    # FFMPEG-PROVENANCE.json (extended to also record the PyAV sdist's
    # hash/bytes, not just FFmpeg's) against the SAME pinned
    # PYAV_SDIST_SHA256/BYTES and FFMPEG_SOURCE_SHA256/BYTES constants
    # build_native_pyav_wheel.py's own acquire_verified_artifact calls
    # already hard-fail on every lane. Once authorized this way the
    # existing per-member ownership walk needs no further change: it
    # already trusts the IN-RUN wheel's own bytes/RECORD as the ownership
    # source, never the reviewed reference's. 6 new platform-independent
    # tests land in tests/native/test_app_payload_builder.py: authorizes a
    # provenance-matching av wheel; the hosted lane (flag unset) still
    # fails the same wheel outright; a wrong version pin still fails even
    # advisory; a TAMPERED provenance claim still fails (not a blind
    # bypass); a MISSING provenance file still fails with a clear reason;
    # and the flag does not relax authorization for any other
    # distribution (fastapi). Plain function calls, real zipfile fixtures,
    # and monkeypatched APP_REQUIREMENTS_FILE/SHA256 only -- no OS
    # dependency, no windows_only marker, so all six collect into both
    # lanes. Re-derived by an actual `--collect-only` run on this tree,
    # not by arithmetic: (1620, 1812) -> (1626, 1818).
    #
    # fix/self-hosted-lane-msys2-keyring (2026-08-25): +4 pure, 0
    # windows_only -- candidate run 32845198987 (self-hosted) failed
    # identically in BOTH attempts at "Build and verify signed component
    # packs" with "pinned PostgreSQL initdb.exe is missing:
    # civiccast-server-pack-cache\extracted\postgres\bin\initdb.exe".
    # acquire_server_pack_sources()'s bare `destination.exists()` check
    # trusted a self-hosted `--cache`'s persisted, interrupted-mid-
    # extraction `extracted/postgres` tree instead of re-extracting it --
    # the same idempotent-scratch bug class as civiccast-build-venv/
    # civiccast-msvc-build-tools, applied here to a DIFFERENT cache. Fixed
    # by re-verifying a pre-existing extraction against the same pinned
    # bin/lib/share file set build_server_pack() itself requires
    # (_extracted_tree_is_complete, dispatching to the existing
    # _postgres_sources/_tsduck_sources validators) before trusting it; an
    # incomplete tree is cleared and re-extracted from the already hash-
    # verified archive. Separately, attempt 1's MSYS2 pacman-key keyserver
    # refresh errors ("Could not update key: <id>", ~18 minutes wasted,
    # non-fatal that run) were investigated and fixed too, per the task:
    # build_minimal_ffmpeg() now pre-populates the pacman keyring itself,
    # offline, via a non-login bash invocation (`pacman-key --init` +
    # `--populate msys2`, both sourced from the pinned, hash-verified
    # MSYS2 base archive already on disk -- never a keyserver) before the
    # first login-shell `pacman -U`, whose own copy of MSYS2's `07-pacman-
    # key.post` hook then sees its trust directory already populated and
    # skips the network `--refresh-keys` step entirely. Verified locally,
    # outside any runner tree, against the real pinned MSYS2 base and a
    # real pinned package (nasm) -- not self-hosted-only: this code has no
    # lane branch, so the fix applies to hosted too (hosted merely had
    # better keyserver luck, not immunity). 4 new platform-independent
    # tests land in tests/native/test_build_native_server_pack.py: a
    # complete pre-existing extraction is reused (no wasted re-extract); an
    # incomplete one (missing initdb.exe, the exact observed shape) is
    # cleared and re-extracted; the ordinary no-cache-yet path is
    # unaffected; and direct unit coverage of the completeness check for
    # both artifact kinds. Plain function calls and monkeypatched
    # fetch_locked_artifact/safe_extract_zip/load_lock only -- no OS
    # dependency, no windows_only marker, so all four collect into both
    # lanes. Re-derived by an actual `--collect-only` run on this tree, not
    # by arithmetic: (1626, 1818) -> (1630, 1822).
    #
    # fix/self-hosted-lane-layer10 (2026-08-25): +4 pure, 0 windows_only --
    # candidate run 32858543561 (self-hosted, main=7bf705a) got past the
    # PostgreSQL cache fix (#41) and K1, then failed identically at
    # "build_native_ffmpeg_pack: FFmpeg closure seed bin/ffmpeg.exe is
    # missing: ...\civiccast-ffmpeg-pack-cache\extracted\ffmpeg\bin\
    # ffmpeg.exe" -- the same persisted-incomplete-extraction bug as #41,
    # one script over. Fixed identically:
    # _extracted_ffmpeg_is_complete() re-verifies a pre-existing extraction
    # against the same pinned bin/license file set build_ffmpeg_pack()
    # itself requires (reusing the existing _ffmpeg_sources() validator)
    # before trusting it; an incomplete tree is cleared and re-extracted.
    # A static sweep of every remaining script build-native-beta's pack
    # step calls (ollama, cuda, bootstrap) found each already immune to
    # this bug class (atomic-replace, per-file re-verify-or-redownload,
    # and npm's own clean-install respectively) -- no further code changes
    # needed there. 4 new platform-independent tests land in
    # tests/native/test_build_native_ffmpeg_pack.py: complete extraction
    # reused; incomplete extraction (missing ffmpeg.exe, the exact
    # observed shape) cleared and re-extracted; no-cache-yet path
    # unaffected; direct unit coverage of the completeness check. Plain
    # function calls and monkeypatched fetch_locked_artifact/
    # safe_extract_zip/load_lock only -- no OS dependency, no
    # windows_only marker, so all four collect into both lanes.
    # Re-derived by an actual `--collect-only` run on this tree, not by
    # arithmetic: (1630, 1822) -> (1634, 1826).
    #
    # fix/provision-adopt-stale-journal (2026-08-26): +9 pure, 0 windows_only --
    # fleet-tester candidate 99db2c6 (soak/INSTALL-FAILED.md) hit an
    # uninstall-then-reinstall-to-a-different-install-root path where the
    # adopted D4 provisioning journal's server_pack_path was never revalidated
    # against the current install root, and several provisioning-failure
    # paths never wrote PROVISION-RECOVERY.md despite the installer's static
    # failure message always referencing it. 9 new tests land in
    # tests/native/test_provision_cli.py: journal_stale_reason unit coverage
    # (matching vs mismatched server_pack_path), an end-to-end main() test
    # reproducing the exact fleet-tester shape (stale COMPLETE journal from a
    # different install_root, adoption still succeeds, station data
    # preserved), a control test for a matching (non-stale) journal, a
    # RUN-branch test proving a stale FAILED journal no longer wedges a fresh
    # run, and recovery-document-written assertions for the pack-verification,
    # credential-reset-fault, pack-key-decode, and corrupt-adopted-journal
    # failure points. All monkeypatched-seam/tmp_path fixtures, no OS
    # dependency, no windows_only marker, so all nine collect into both
    # lanes. Re-derived by an actual `--collect-only` run on this tree, not by
    # arithmetic: (1634, 1826) -> (1643, 1835).
    #
    # fix/ffmpeg-acquire-mirror-fallback (2026-08-27): +14 pure, 0
    # windows_only -- candidate run 33094460301 died on a 404 because
    # BtbN/FFmpeg-Builds pruned the pinned autobuild release (repinned in
    # #52, but the new pin is equally prunable). fetch_locked_artifact now
    # consults a persistent hash-addressed archive mirror
    # (CIVICCAST_RUNTIME_ARTIFACT_MIRROR / mirror=) before any network,
    # writes every verified download back through to it, and tries the
    # lock's optional reviewed fallback_urls in order after the primary URL
    # fails; the size+SHA-256 pin stays the sole admission authority on
    # every path. 14 new tests land in
    # tests/native/test_runtime_dependency_provisioner.py: mirror hit with
    # zero network, env-var configuration, corrupt-mirror-entry
    # ignore-then-repair, write-through admission, mirror-write-failure
    # tolerance, primary-404-to-fallback ordering, all-sources-fail
    # reporting, fallback_urls schema acceptance, and 6 parametrized
    # fallback_urls schema rejections. All injected-opener/tmp_path
    # fixtures, no OS dependency, no windows_only marker, so all fourteen
    # collect into both lanes. Re-derived by an actual `--collect-only` run
    # on this tree, not by arithmetic: (1643, 1835) -> (1657, 1849).
    #
    # fix/provision-port-reservation (2026-08-27): +27 pure, 0 windows_only --
    # two real LPM deployment failures (candidate 75cc13f) both died at
    # d4-provision because pg_ctl could not bind 127.0.0.1:5432 (a
    # Windows-excluded TCP port range, Hyper-V/WSL winnat). New
    # civiccast.native.provision.port_select test-binds the intended port
    # before pg_ctl ever runs and falls forward through a documented
    # candidate list, skipping excluded ranges and honest-failing when none
    # bind; the consumer-side gap (the running supervisor's postgres child
    # never read the port back out of DATABASE_URL, unlike the D3 upgrade
    # engine) is closed too. 27 new tests: 20 in the new
    # tests/native/test_provision_port_select.py (excluded-range parsing
    # against realistic netsh output, the pure fallback-selection decision
    # logic, real local TCP bind-test coverage against genuinely free/held
    # ports -- no real postgres/netsh process -- and the LPM failure-shape
    # regression), 2 in test_provision_cli.py (the honest no-port-available
    # recovery document, and the fallback port flowing into the provisioned
    # context), and 5 in test_supervisor_service.py (db_host/db_port wiring
    # through build_production_service, default_dependency_provider's
    # DATABASE_URL parsing plus its unparsable-URL fallback, and the
    # factory's pass-through). All monkeypatched-seam/tmp_path/real-local-
    # socket fixtures, no OS dependency, no windows_only marker, so all
    # twenty-seven collect into both lanes. Re-derived by an actual
    # `--collect-only` run on this tree, not by arithmetic:
    # (1657, 1849) -> (1684, 1876).
    #
    # fix/setup-nonce-handoff (2026-08-29, owner decision): -18 pure, -18
    # windows_only -- the installer-handoff setup nonce is retired. The
    # control plane binds 127.0.0.1 only (verified live via netstat), so
    # first setup is unreachable from the network by construction and the
    # nonce was a redundant, failure-prone gate (nonce unreadable across the
    # elevated-installer/normal-user split, the recovery code file never
    # written, "Get a new code" silently no-op'ing, the elevated CLI restore
    # printing nothing -- 4 field failures in 2 days). First setup is now
    # admitted by loopback alone (civiccast.installer.router.
    # _require_local_setup_request). tests/native/test_setup_handoff_
    # recovery.py (the CLI recovery-URL report tests, 11) is deleted
    # entirely; tests/native/test_provision_cli.py loses 6 setup-nonce
    # generation/marker-line tests; tests/native/test_station_runtime.py's 4
    # setup-nonce env-var tests collapse to 3 (proving the env var is never
    # set at all). None were marked windows_only, so both lanes move by the
    # same -18. Re-derived by an actual `--collect-only` run on this tree,
    # not by arithmetic: (1684, 1876) -> (1666, 1858).
    #
    # fix/component-acquisition-receipt-wipe (2026-08-29): +11 total, +2
    # windows_only -- field evidence (candidate 4eca729): a station activated
    # on the mandatory floor caption tier crashed on every restart after the
    # operator acquired the OPTIONAL large-v3 tier through the post-install
    # GUI, because `_resolve_caption_tier` auto-promotes to the highest
    # STAGED tier across both search roots while the activation receipt was
    # only ever written, once, for whichever tier was staged at ELEVATED
    # install time -- so the runtime resolved large-v3 but validated it
    # against a receipt that could only ever describe the floor tier
    # (`NativeStationConfigurationError: ... receipt does not match this
    # distribution`). Fixed by reading the receipt from the SAME root the
    # resolved tier's own model came from (`load_native_station_environment`,
    # reusing the existing `_tier_base_root` two-root search rather than
    # hardcoding the version root), paired with a new Rust-side addendum
    # writer (`main.rs::finalize_captions_large_acquisition`) that proves the
    # newly acquired tier with a real inference self-test and writes ITS OWN
    # receipt into the acquisition root -- never touching the primary
    # receipt, station-set.json, or any other already-activated component.
    # Separately, the supervisor's own crash was invisible in its own log
    # (only the "logging initialized" canary; the real exception reached only
    # the Windows Event Log) -- `service_host.SvcDoRun` now logs the real
    # reason via the new `civiccast.native.supervisor.start_failure_marker`
    # and, after 3 consecutive failed starts with the SAME condition, writes
    # an operator-readable `STATION-START-FAILED.md` marker alongside
    # install-progress.log. 11 new tests: 2 in test_station_runtime.py (the
    # fail-closed floor with no addendum receipt, and the regression proof
    # with one), 7 in the new test_start_failure_marker.py (pure counter/
    # marker decision logic, no OS dependency, no windows_only marker), and 2
    # in test_supervisor_service_win.py (`windows_only`: the real
    # SvcDoRun-driven crash-loop-then-marker sequence, and the marker/counter
    # clearing on a clean start). The 9 non-windows_only tests collect into
    # both lanes; the 2 windows_only tests collect only into the full lane.
    # Rebased onto fix/setup-nonce-handoff's own floor (1666, 1858) after
    # that PR landed first; re-derived by an actual `--collect-only` run on
    # this POST-REBASE tree, not by arithmetic: (1666, 1858) -> (1683, 1877).
    # fix/gate-a-cross-version-upgrade (2026-08-31): the upgrade routing,
    # scoped PostgreSQL lifecycle, Gate A, and installer-policy coverage added
    # 9 platform-independent native cases and 10 full-Windows cases after the
    # previous pin. This same correction removes two Windows-only descendant
    # Job Object tests introduced by PR #117: one failed on its own PR because
    # hosted runners impose a foreign Job Object topology, while the other
    # selected its parent process as the alleged grandchild and was therefore
    # a false positive. Re-derived by real collection on this tree:
    # (1683, 1877) -> (1692, 1885).
    # The flat-installer-layout repair adds two platform-independent upgrade
    # seam/CLI collection cases, so both lanes advance by two. Re-derived by
    # real collection on this tree: (1692, 1885) -> (1694, 1887).
    # feat/station-models-reuse-across-versions: the station bundle publisher's
    # stable MODEL-pack identity adds one platform-independent case pinning the
    # two identity constants (the Rust-side proofs live in
    # native_distribution.rs / native_activation.rs and do not collect here), so
    # both lanes advance by one. Re-derived by real collection on this tree:
    # (1694, 1887) -> (1695, 1888).
    # Review follow-up on the same branch: the station bundle publisher's
    # signed metadata no longer carries a build-input path, and two
    # platform-independent cases now enforce that (packs reproducible across
    # build roots; no pack metadata embeds its build path). Both lanes
    # advance by two. Re-derived by real collection on this tree:
    # (1695, 1888) -> (1697, 1890).
    # feat/installer-embeds-station-index (2026-09-02): the installer now
    # embeds the signed station index and the tiny `core` pack, and
    # scripts/build_native_bootstrap.py grew a fail-closed gate on them
    # (validate_embedded_station_resources). Six platform-independent cases in
    # tests/native/test_native_bootstrap_builder.py cover that gate. Rebased
    # onto PR #127's own floor (1697, 1890) after that PR landed first, and
    # re-derived by an actual `--collect-only` run on this POST-REBASE tree,
    # NOT by adding six to it: (1697, 1890) -> (1703, 1896).
    # fix/d3-preupgrade-drill-and-rollback-containment (Gate A run 33681670855):
    # one new platform-independent end-to-end proof
    # (tests/native/test_upgrade_engine_postgres.py, Postgres-gated but not
    # windows_only) that the real D3 upgrade engine migrates a database from
    # N-1 to head through the actual production seam bundle. Both lanes
    # advance by one. Re-derived by an actual `--collect-only` run on this
    # tree: (1703, 1896) -> (1704, 1897).
    # fix/installer-upgrade-batch-2026-09-03 (the installer-path audit's
    # 84-finding batch): the D3 engine, its seams, the interlock, the routing
    # decision and the station-bundle publisher all gained real behavioural
    # coverage for findings that previously had none -- BL-01's real-Postgres
    # rollback proof, BL-03's no-op migration, BL-04's identity gate, BL-05's
    # concurrent second instance, BL-06's stale journal, BL-07's contained
    # halt/rollback, MA-01's flat-layout claims, MA-02's persist-before-flip,
    # MA-05's downgrade guard, MA-06's ahead state, MA-09/MA-11/MA-12's
    # non-vacuous verdicts, MA-38/MA-39's CI floors, and the batch list's own
    # test blind spots (11-16). Re-derived by an actual `--collect-only` run
    # on this tree, NOT by adding a delta to the previous pin:
    # (1704, 1897) -> (1771, 1971).
    # fix/bl01-restore-seam-ci: three tests pinning the restore seam's own
    # tool wiring (the URL psql/pg_restore parse, and the resolved psql path)
    # -- the two defects that made BL-01's Postgres proof unrunnable in CI.
    # Re-derived by an actual `--collect-only` run: (1771, 1971) -> (1774, 1974).
    # fix/egress-engine-visibility (Gate A T4 diagnosability): seven new
    # platform-independent cases in the new
    # tests/native/test_supervisor_control_plane_logging.py (none
    # windows_only), covering configure_control_plane_logging, the
    # CIVICCAST_SUPERVISED env-var signal, and the guarded call site in
    # civiccast.app.create_app. Both lanes advance by seven. Re-derived by an
    # actual `--collect-only` run: (1774, 1974) -> (1781, 1981).
    # 2026-09-03 GStreamer worker PATH fix (Gate A T4 "must be an interface"):
    # +5 pure, +6 total (tests/native/test_gstreamer_runtime.py +
    # test_supervisor_wiring_batch.py): (1781, 1981) -> (1786, 1987).
    # fix/tsduck-pack-config (2026-09-03): the TSDuck pack builder now stages
    # 17 pinned TSDUCK_DATA_PINS config data files beside the existing
    # TSDUCK_BIN_PINS binaries and locates the shipped tsp.exe. One new
    # platform-independent case, test_tsduck_data_files_are_staged_beside_
    # the_pinned_binaries in tests/native/test_build_native_server_pack.py
    # (no windows_only marker, no OS dependency -- plain sources-dict
    # assertions), so both lanes advance by one. Re-derived by an actual
    # `--collect-only` run on this tree, not by arithmetic:
    # (1786, 1987) -> (1787, 1988).
    # 2026-09-04 upgrade-over-running-station provision fix (#159): +2 pure, +2 total
    # (tests/native/test_provision_cli.py): (1787, 1988) -> (1789, 1990).
    # fix/state-write-encoding: one new platform-independent case,
    # test_initdb_argv_pins_utf8_encoding_and_c_locale_for_new_clusters in
    # tests/native/test_provision_seams.py, pinning initdb_argv's new
    # --encoding/--locale flags (the rest of that PR's tests live under
    # tests/egress and tests/dr, outside this collector's tests/native scope,
    # so they do not move this floor). Both lanes advance by one. Re-derived
    # by an actual `--collect-only` run on this tree, not by arithmetic:
    # (1789, 1990) -> (1790, 1991).
    # fix/caption-tap-overload-starves-playout: four new platform-independent
    # cases in tests/native/test_caption_capacity_proof.py, added because the
    # capacity proof's fail-closed overload control was verified only against a
    # HAND-TYPED report dict -- so it did not notice that the evaluator pinned
    # runtime_state == "overloaded" while the tap worker had started publishing
    # "paused", which would have failed the proof on every station. The new
    # cases drive the REAL producer (_overload_negative_control) into the
    # evaluator, cover both accepted refusal states, and pin that a
    # "within-capacity" state still fails so widening the set did not turn the
    # control into a no-op. No windows_only marker and no OS dependency (a
    # scripted CaptionRuntime and a tmp_path WAV), so both lanes advance by
    # four. Re-derived by an actual `--collect-only` run on this tree, not by
    # arithmetic: (1790, 1991) -> (1794, 1995).
    # fix/caption-tap-caps-item79 review round 4: three new
    # platform-independent cases in tests/native/test_caption_capacity_proof.py
    # (TestArgParserDefaultsTrackTheLiveTap), added after review caught the
    # capacity proof constructing FasterWhisperRuntime WITHOUT live=True --
    # so --cpu-threads's live-tap default (a prior fix) paired with a beam
    # width that never followed it, measuring a sizing nobody ships. The new
    # cases pin the --cpu-threads and --beam-size argparse defaults against
    # civiccast.captions.runtime's own default_live_tap_cpu_threads()/
    # LIVE_TAP_CPU_BEAM_SIZE, plus that the constructed runtime actually
    # receives live=True. No windows_only marker and no OS dependency (an
    # argparse.ArgumentParser and a monkeypatched constructor), so both lanes
    # advance by three. Re-derived by an actual `--collect-only` run on this
    # tree, not by arithmetic: (1794, 1995) -> (1797, 1998).
    # fix/caption-tap-caps-item79 review round 5: one new platform-independent
    # case in tests/native/test_caption_capacity_proof.py,
    # test_capacity_proof_identity_records_post_construction_beam_and_threads,
    # added after review caught build_caption_runtime's identity dict
    # recording the PRE-construction --beam-size/--cpu-threads args instead
    # of the runtime's own post-construction values, which
    # FasterWhisperRuntime(live=True) can override via
    # CIVICCAST_WHISPER_BEAM_SIZE/CIVICCAST_WHISPER_CPU_THREADS and the live
    # ceiling cap (measured: env beam 7 + --beam-size 1 -> runtime.beam_size
    # 7; env threads 8 + --cpu-threads 1 -> runtime.cpu_threads 2). The new
    # case fakes a runtime whose resolved attributes differ from the
    # constructor arguments and asserts the identity dict follows the
    # runtime. No windows_only marker and no OS dependency (a monkeypatched
    # constructor and a SimpleNamespace fake), so both lanes advance by one.
    # Re-derived by an actual `--collect-only` run on this tree, not by
    # arithmetic: (1797, 1998) -> (1798, 1999).
    # feat/caption-tap-off-switch-item91: six new platform-independent cases
    # in tests/native/test_station_runtime.py --
    # test_station_environment_caption_tap_off_switch_disables_the_tap_leg
    # (1) plus test_station_environment_caption_tap_non_off_values_keep_forcing_inline
    # parametrized over 5 values (5) -- covering the env-composition fix that
    # makes CIVICCAST_CAPTION_TAP=off actually reach an activated native
    # station's child environment instead of always being overwritten back
    # to "inline". No windows_only marker and no OS dependency (tmp_path +
    # monkeypatch env only), so both lanes advance by six. Re-derived by an
    # actual `--collect-only` run on this tree, not by arithmetic:
    # (1798, 1999) -> (1804, 2005).
    # 2026-09-08 build-probe profile isolation: two platform-independent
    # success/failure cases in tests/native/test_app_payload_builder.py.
    # Actual collect-only runs on this tree returned (1806, 2007), including
    # the CI failure and the local red reproduction of this exact assertion.
    # The workflow's minimum floors and allowed margin remain unchanged.
    # 2026-09-10 beta.5.1 legacy-journal tolerance (PR #206): +13 platform-
    # independent cases across tests/native/test_provision_{journal,models,
    # orchestrator,cli}.py. Actual collect-only run on this tree returned
    # (1819, 2020) -- CI failed on this exact assertion at (1806, 2007).
    # 2026-09-10 F-1 second-cycle CI repair: four new platform-independent
    # launcher-normalization cases in tests/native/test_app_payload_builder.py
    # cover malformed/ambiguous retained script resources, bounded cleanup of
    # complete stale ZIP payloads, a larger replacement, and idempotence. The
    # Linux mutation baseline collected the exact new tuple (1823, 2024), so
    # both lanes advance by four.
    # 2026-09-12 Windows cache trace classification (PR #222): one platform-
    # independent hostile-environment case plus six windows_only parameterized
    # cache-boundary cases. Actual collect-only runs returned (1824, 2031), so
    # the pure lane advances by one and the full lane advances by seven.
    # 2026-10-09 Beta 12 candidate follow-up: the two real Windows ACL/mutex
    # cases in test_admin_recovery.py now carry windows_only in addition to
    # their runtime skip guards. That checkpoint collected 1957 pure and
    # 2168 total tests.
    # 2026-10-13 station-operations collection: six Windows service/DPAPI
    # tests in identity migration and station secrets are explicitly
    # windows_only, leaving 2024 platform-independent tests and 2259 total.
    # 2026-10-14 Beta16 license-closure assertion adds one platform-
    # independent test: 2025 platform-independent and 2260 total; Windows
    # non-integration collection is 2257. Floors 1975 and 2207 leave the
    # standard 50-test margin in both executed lanes.
    # 2026-10-19 private-file storage adds eight tests; four Windows ACL/DPAPI
    # cases carry windows_only: 2029 pure, 2268 total, 2265 non-integration.
    # Floors 1983 and 2215 retain margins of 46 pure and 50 Windows tests.
    # Two real Windows junction cases add no portable cases: 2029 pure,
    # 2270 total, 2267 non-integration; Windows floor 2217 keeps the margin.
    # The running-image ACL regression adds one Windows case: 2271 total,
    # 2268 non-integration; Windows floor 2218 retains the margin.
    # Exact native closure-baseline regression adds one portable case.
    # 2026-10-27 outage fix adds one portable case in
    # tests/native/test_gstreamer_runtime.py: the repeated-reload-probe DLL
    # registration guard, which exercises the Windows-only bootstrap tail
    # through an `os` shim so it runs (never skips) in every lane. Its
    # real-loader twin was removed again because it must skip anywhere the
    # installed runtime is absent, and both native lanes forbid skips.
    # Re-derived by an actual collect-only run on this tree, not by
    # arithmetic: (2084, 2326) -> (2085, 2327).
    # RC2 model-data permissions adds one Windows-only case: actual collection
    # is 2145 pure, 2388 total. Keep the maximum 50-test floor margin.
    # Operator safety/transport regressions: fresh collection retained in CURRENT.
    # Five WSL manifest access/mapping cases: measured 2153 pure/2397 total.
    # Pinned HTTP parser backport: 2177 pure / 2422 total, measured collect-only.
    # Prior CI observed 2185 pure / 2430 total; one portable npm regression added.
    # RC4 Whistle live profile adds two portable native env cases, measured
    # by this test's own collection: 2188 pure / 2433 total.
    # RC4 OpenSSL stale-cache rebuild adds four portable cases: 2192 / 2437.
    # RC4 service-identity sharing-violation fix adds one Windows case: 2192 / 2438.
    assert (collect("not windows_only"), collect()) == (2192, 2438)


def test_linux_unit_job_runs_native_tests_once_in_the_dedicated_pure_lane() -> None:
    _text, workflow = _workflow(WORKFLOWS[1])
    steps = workflow["jobs"]["test"]["steps"]
    full_suite_index = next(index for index, step in enumerate(steps) if step["name"] == "pytest")
    native_suite_index = next(
        index
        for index, step in enumerate(steps)
        if step["name"] == "Run tests/native's platform-independent suite (junit)"
    )
    full_suite_command = steps[full_suite_index]["run"]
    native_suite_commands = [
        step["run"] for step in steps if "run" in step and "pytest tests/native" in step["run"]
    ]

    assert full_suite_index < native_suite_index
    assert "--ignore=tests/native" in full_suite_command
    assert len(native_suite_commands) == 1


def test_windows_native_checkout_is_bound_to_the_pull_request_head() -> None:
    _text, workflow = _workflow(WORKFLOWS[1])
    checkout = next(
        step
        for step in workflow["jobs"]["windows-native"]["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )

    assert checkout.get("with", {}).get("ref") == "${{ github.event.pull_request.head.sha }}"


def test_junit_artifact_uploads_fail_when_the_proof_file_is_missing() -> None:
    _text, workflow = _workflow(WORKFLOWS[1])
    junit_uploads = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
        and "junit" in str(step.get("with", {}).get("path", "")).lower()
    ]

    assert junit_uploads
    assert all(step["with"].get("if-no-files-found") == "error" for step in junit_uploads)


def test_reproducibility_cache_key_covers_every_payload_dependency_lock() -> None:
    _text, workflow = _workflow(WORKFLOWS[0])
    cache_step = next(
        step
        for step in workflow["jobs"]["repeat-build"]["steps"]
        if str(step.get("uses", "")).startswith("actions/cache@")
    )
    cache_key = cache_step["with"]["key"]

    for lock_name in (
        "native-windows-build-toolchain.lock.json",
        "requirements-native-app.txt",
        "requirements-native-pyav-build.txt",
        "native-windows-runtime-dependencies.lock.json",
        "native-windows-ollama-models.lock.json",
    ):
        assert f"workspace-a/{lock_name}" in cache_key


def test_reproducibility_cache_key_covers_the_app_build_requirements() -> None:
    _text, workflow = _workflow(WORKFLOWS[0])
    cache_step = next(
        step
        for step in workflow["jobs"]["repeat-build"]["steps"]
        if str(step.get("uses", "")).startswith("actions/cache@")
    )

    assert "workspace-a/requirements-native-app-build.txt" in cache_step["with"]["key"]


def test_reproducibility_checkouts_are_bound_to_the_event_source_sha() -> None:
    _text, workflow = _workflow(WORKFLOWS[0])
    checkout_steps = [
        step
        for step in workflow["jobs"]["repeat-build"]["steps"]
        if str(step.get("uses", "")).startswith("actions/checkout@")
    ]

    assert len(checkout_steps) == 2
    assert all(
        step.get("with", {}).get("ref") == "${{ github.event.pull_request.head.sha || github.sha }}"
        for step in checkout_steps
    )


def test_reproducibility_pull_request_paths_cover_every_payload_input() -> None:
    _text, workflow = _workflow(WORKFLOWS[0])
    triggers = workflow.get("on", workflow.get(True))
    paths = set(triggers["pull_request"]["paths"])

    assert paths >= {
        ".github/workflows/native-app-reproducibility.yml",
        "civiccast/**",
        "pyproject.toml",
        "README.md",
        "LICENSE-CODE",
        "scripts/collect_source_state.py",
        "native-windows-build-toolchain.lock.json",
        "native-windows-ollama-models.lock.json",
        "native-windows-runtime-dependencies.lock.json",
        "uv.lock",
        "requirements-native-app.txt",
        "requirements-native-app-build.txt",
        "requirements-native-pyav-build.txt",
        "scripts/build_native_app_payload.py",
        "scripts/build_native_*.py",
        "scripts/prove_native_app_reproducible.py",
        "scripts/provision_native_*.py",
        "scripts/verify_native_app_payload.py",
        "tests/native/**",
        "tests/captions/**",
        "tests/egress/**",
        "tests/stream/**",
    }


def test_reproducibility_cache_paths_include_only_real_build_consumers() -> None:
    _text, workflow = _workflow(WORKFLOWS[0])
    cache_step = next(
        step
        for step in workflow["jobs"]["repeat-build"]["steps"]
        if str(step.get("uses", "")).startswith("actions/cache@")
    )

    assert set(cache_step["with"]["path"].splitlines()) == {
        "toolchain-cache",
        "workspace-a/build/native-pyav-cache",
        "workspace-b/build/native-pyav-cache",
    }


def test_actions_budget_exempts_only_named_global_gates_from_paths_requirement(
    tmp_path: Path,
) -> None:
    heavy_pr_workflow = """
on:
  pull_request:
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - run: sudo apt-get install -y ffmpeg
"""

    for name in ("ci-test.yml", "deterministic-detectors.yml"):
        violations = validate_workflow(tmp_path / name, heavy_pr_workflow)
        assert "heavy workflow is missing paths filters" not in violations

    violations = validate_workflow(tmp_path / "ordinary-heavy.yml", heavy_pr_workflow)
    assert "heavy workflow is missing paths filters" in violations


def test_actions_budget_recognizes_setup_uv_cache_without_accepting_no_cache(
    tmp_path: Path,
) -> None:
    cached = """
on:
  pull_request:
    paths: ["civiccast/**"]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: astral-sh/setup-uv@v8.1.0
        with:
          enable-cache: true
      - run: sudo apt-get install -y ffmpeg
"""
    uncached = cached.replace("        with:\n          enable-cache: true\n", "")

    assert (
        "heavy workflow is missing cache coverage for expensive installs/downloads"
        not in validate_workflow(tmp_path / "cached-heavy.yml", cached)
    )
    assert (
        "heavy workflow is missing cache coverage for expensive installs/downloads"
        in validate_workflow(tmp_path / "uncached-heavy.yml", uncached)
    )


def test_native_workflows_cancel_superseded_candidate_runs_without_schedules() -> None:
    for path in WORKFLOWS:
        _text, workflow = _workflow(path)
        triggers = workflow.get("on", workflow.get(True))

        assert "schedule" not in triggers, path.name
        assert workflow["concurrency"] == {
            "group": "${{ github.workflow }}-${{ github.ref }}",
            "cancel-in-progress": "true",
        }


def test_windows_pr_jobs_state_their_native_justification_and_cache_expensive_downloads() -> None:
    text, workflow = _workflow(WORKFLOWS[0])
    jobs = workflow["jobs"]

    assert "workflow-cost: windows-pr-justification" in text
    assert any(
        step.get("uses") == "actions/cache@v4"
        or (
            str(step.get("uses", "")).startswith("actions/setup-")
            and bool(step.get("with", {}).get("cache"))
        )
        for step in jobs["repeat-build"]["steps"]
    )


def test_nonrelease_artifacts_expire_after_one_day() -> None:
    """Every artifact upload must declare retention-days: 1.

    Was 7. Cut to 1 on 2026-08-20 after Actions artifact storage reached 100%
    of the account's 0.5 GB allowance: 990 live artifacts, 542.5 GB, 93% of it
    from one workflow storing a 2.3 GB candidate, a 19.5 GB station bundle and
    a 23 GB kit on EVERY push to the release branch. At 7-day retention
    nothing aged out before the next push landed.

    The assertion is on the DECLARATION, not the effective value, on purpose:
    the repo-level cap is also set to 1 and would silently clamp a larger
    number here, so a workflow could declare 30 and look fine in practice
    while being wrong the moment that cap is raised.
    """

    for path in WORKFLOWS:
        _text, workflow = _workflow(path)
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if str(step.get("uses", "")).startswith("actions/upload-artifact"):
                    assert step["with"]["retention-days"] == "1", path.name


def test_windows_media_owner_acceptance_keeps_the_failed_junit(tmp_path: Path) -> None:
    import xml.etree.ElementTree as ET
    from unittest.mock import patch

    import pytest

    from scripts.verify_windows_media_junit import classify_media_junit

    root = ET.Element("testsuite")
    case = ET.SubElement(
        root,
        "testcase",
        classname="tests.egress.test_gst_engine_wsl",
        name="test_immediate_finite_playlist_reload_holds_rebases_and_stays_on_air",
    )
    failure = ET.SubElement(
        case,
        "failure",
        message=(
            "AssertionError: CTRL reload committed\n"
            "WORKER_RESULT {'error': None, 'teardown_clean': True}\n"
            "assert {66: 1} == {}"
        ),
    )
    failure.text = ">       assert _pes_pts_backward_steps(out_ts) == {}, text"
    junit = tmp_path / "media.xml"
    ET.ElementTree(root).write(junit)
    original = junit.read_bytes()
    with patch("scripts.verify_windows_media_junit.__version__", "1.0.0-rc.1"):
        assert classify_media_junit(junit, 1, 1) == (0, 1)
    assert junit.read_bytes() == original
    with (
        patch("scripts.verify_windows_media_junit.__version__", "1.0.0-rc.2"),
        pytest.raises(ValueError, match="Unaccepted media failure"),
    ):
        classify_media_junit(junit, 1, 1)
    case.remove(failure)
    ET.ElementTree(root).write(junit)
    assert classify_media_junit(junit, 0, 1) == (1, 0)
    workflow, _ = _workflow(WORKFLOWS[1])
    assert (
        'classify_media_junit(Path("junit-windows-media.xml"), int(code), len(nodes))' in workflow
    )
    assert "::warning::owner-accepted RC1 limitation" in workflow
    assert "{passed} passed, {accepted} failed-owner-accepted, no skips" in workflow
    assert "assert _pes_pts_backward_steps(out_ts) == {}, text" in (
        ROOT / "tests/egress/test_gst_engine_wsl.py"
    ).read_text(encoding="utf-8")


def test_windows_media_owner_acceptance_rejects_other_outcomes(tmp_path: Path) -> None:
    import copy
    import xml.etree.ElementTree as ET

    import pytest

    from scripts.verify_windows_media_junit import classify_media_junit

    root = ET.Element("testsuite")
    case = ET.SubElement(
        root,
        "testcase",
        classname="tests.egress.test_gst_engine_wsl",
        name="test_immediate_finite_playlist_reload_holds_rebases_and_stays_on_air",
    )
    failure = ET.SubElement(
        case,
        "failure",
        message=(
            "AssertionError: CTRL reload committed\n"
            "WORKER_RESULT {'error': None, 'teardown_clean': True}\n"
            "assert {66: 1} == {}"
        ),
    )
    failure.text = ">       assert _pes_pts_backward_steps(out_ts) == {}, text"
    junit = tmp_path / "media.xml"
    for scenario in (
        "classname",
        "name",
        "repeated-pts",
        "other-pid",
        "other-assertion",
        "other-source",
        "worker-error",
        "not-committed",
        "skip",
        "error",
        "second-failure",
        "duplicate-case",
        "exit0",
        "exit2",
        "exit3",
        "exit4",
        "exit5",
        "incomplete",
    ):
        changed = copy.deepcopy(root)
        node = changed.find("testcase")
        failed = node.find("failure")
        code, minimum = 1, 1
        if scenario in ("classname", "name"):
            node.set(scenario, "some_other_test")
        elif scenario in ("repeated-pts", "other-pid", "other-assertion"):
            replacement = {
                "repeated-pts": "assert {66: 2} == {}",
                "other-pid": "assert {65: 1} == {}",
                "other-assertion": "assert False",
            }[scenario]
            failed.set(
                "message", failed.get("message").replace("assert {66: 1} == {}", replacement)
            )
        elif scenario == "other-source":
            failed.text = ">       assert False"
        elif scenario in ("worker-error", "not-committed"):
            failed.set(
                "message",
                failed.get("message").replace(
                    "'error': None" if scenario == "worker-error" else "CTRL reload committed",
                    "absent",
                ),
            )
        elif scenario in ("skip", "error"):
            ET.SubElement(node, "skipped" if scenario == "skip" else "error")
        elif scenario == "second-failure":
            ET.SubElement(node, "failure", message="another failure")
        elif scenario == "duplicate-case":
            changed.append(copy.deepcopy(node))
        elif scenario.startswith("exit"):
            code = int(scenario[-1])
        else:
            minimum = 2
        ET.ElementTree(changed).write(junit)
        with pytest.raises(ValueError):
            classify_media_junit(junit, code, minimum)
