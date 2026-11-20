# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Tests for the real seam wiring's PURE pieces only.

HARD RULE: no real PostgreSQL process is spawned here. Two things are
legitimately testable without one:

* :func:`initdb_argv` -- pure argv construction, never executed.
* the atomic file-write helper + the filesystem-probe seams -- ordinary file
  I/O against tmp_path, no external binary involved.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from civiccast.native.provision.models import ProvisionContext, ProvisionPlan
from civiccast.native.provision.seams import (
    INITDB_AUTH_HOST_METHOD,
    AdoptionForeignClusterError,
    _initdb_pwfile,
    _restrict_windows_pwfile_acl,
    _windows_pwfile_acl_command,
    alter_role_password_sql,
    default_detect_postgres_cluster,
    default_ensure_database,
    default_run_initdb,
    default_write_pg_hba_conf,
    default_write_postgres_conf,
    initdb_argv,
    pg_ctl_path_for,
    psql_path_for,
    reset_cluster_credential,
)

_WINDOWS_PATH_TEST = pytest.mark.skipif(
    os.name != "nt", reason="requires native Windows path semantics"
)

# --- initdb_argv (pure, never spawned) -----------------------------------------


def test_initdb_argv_shape() -> None:
    argv = initdb_argv(
        initdb_path="initdb.exe",
        data_dir=r"C:\pgdata",
        username="civiccast_svc",
        pwfile=r"C:\state\tmp\pwfile.tmp",
    )
    assert argv[0] == "initdb.exe"
    assert "--pgdata" in argv
    assert argv[argv.index("--pgdata") + 1] == r"C:\pgdata"
    assert "--username" in argv
    assert argv[argv.index("--username") + 1] == "civiccast_svc"
    assert "--pwfile" in argv
    assert argv[argv.index("--pwfile") + 1] == r"C:\state\tmp\pwfile.tmp"
    assert f"--auth-host={INITDB_AUTH_HOST_METHOD}" in argv
    # The password must never be embedded directly in argv -- only a path to
    # the short-lived pwfile that carries it.
    assert "hunter2" not in " ".join(argv)


def test_initdb_argv_pins_utf8_encoding_and_c_locale_for_new_clusters() -> None:
    """Every NEW cluster must be initdb'd UTF8/C, never inheriting the
    platform default (WIN1252 on Windows) -- see the encoding defect this
    closes in the module docstring and civiccast/egress/_text.py. Existing
    clusters are never touched by this (initdb only runs when
    detect_postgres_cluster finds none, see orchestrator.py)."""

    argv = initdb_argv(
        initdb_path="initdb.exe",
        data_dir=r"C:\pgdata",
        username="civiccast_svc",
        pwfile=r"C:\state\tmp\pwfile.tmp",
    )
    assert "--encoding" in argv
    assert argv[argv.index("--encoding") + 1] == "UTF8"
    assert "--locale" in argv
    assert argv[argv.index("--locale") + 1] == "C"


def test_initdb_argv_auth_matches_pg_hba_conf_method() -> None:
    # Drift guard: the provisioned pg_hba.conf's "host" rule and initdb's
    # --auth-host default must name the EXACT SAME auth method string, pulled
    # from render_pg_hba_conf's real rendered output (not a hand-copied
    # literal), so one changing without the other fails this test.
    from civiccast.native.provision.conf import render_pg_hba_conf

    argv = initdb_argv(
        initdb_path="initdb", data_dir="/pgdata", username="u", pwfile="/state/tmp/pwfile"
    )
    hba = render_pg_hba_conf(host="127.0.0.1")

    hba_rule_line = next(line for line in hba.splitlines() if line.startswith("host"))
    hba_method = hba_rule_line.split()[-1]

    auth_host_flag = next(arg for arg in argv if arg.startswith("--auth-host="))
    argv_method = auth_host_flag.split("=", 1)[1]

    assert argv_method == hba_method == INITDB_AUTH_HOST_METHOD


@pytest.mark.parametrize(
    "kwargs",
    [
        {"initdb_path": "", "data_dir": "/pgdata", "username": "u", "pwfile": "/x"},
        {"initdb_path": "initdb", "data_dir": "", "username": "u", "pwfile": "/x"},
        {"initdb_path": "initdb", "data_dir": "/pgdata", "username": "", "pwfile": "/x"},
        {"initdb_path": "initdb", "data_dir": "/pgdata", "username": "u", "pwfile": ""},
    ],
)
def test_initdb_argv_rejects_empty_fields(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        initdb_argv(**kwargs)


# --- atomic write + filesystem probe seams -------------------------------------


def _context(tmp_path: Path) -> ProvisionContext:
    return ProvisionContext(
        postgres_data_dir=str(tmp_path / "pgdata"),
        postgres_config_path=str(tmp_path / "pgdata" / "postgresql.conf"),
        postgres_hba_path=str(tmp_path / "pgdata" / "pg_hba.conf"),
        database_password="hunter2",
        server_pack_path=str(tmp_path / "server-binaries.ccpack"),
        state_root=str(tmp_path / "state"),
        owner_run_id="run-1",
    )


@pytest.fixture(autouse=True)
def pgdata_acl_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Row-4b: every INSTALL-TIME ``pg_ctl start`` in this module normalizes
    the data directory's DACL first (:mod:`civiccast.native.pgdata_acl`).

    These tests never create a real ``pgdata`` on disk, so the production
    normalizer would fail loud on every one of them. Record the call instead
    and hand the recording to the test -- the wiring tests below assert on
    exactly this list, so the seam is still PROVEN, not merely stubbed away.
    """

    import civiccast.native.provision.seams as seams_module

    seen: list[str] = []
    monkeypatch.setattr(
        seams_module, "normalize_pgdata_acl", lambda data_dir: seen.append(data_dir)
    )
    return seen


@pytest.fixture(autouse=True)
def bin_acl_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """rc.6 -> rc.7 Dell fix: the same INSTALL-TIME starts also grant the
    installing account on the PostgreSQL binary tree
    (:mod:`civiccast.native.postgres_bin_acl`).

    These tests use a placeholder ``C:\\pg\\bin\\pg_ctl.exe`` that does not
    exist on disk, so the production normalizer would fail loud; record the
    call instead (the same reasoning as ``pgdata_acl_calls`` above).
    """

    import civiccast.native.provision.seams as seams_module

    seen: list[str] = []
    monkeypatch.setattr(
        seams_module,
        "normalize_postgres_bin_acl",
        lambda pg_ctl_path: seen.append(pg_ctl_path),
    )
    return seen


def test_default_write_postgres_conf_creates_parent_dirs_and_writes_exact_content(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    write = default_write_postgres_conf(context)
    write("listen_addresses = '127.0.0.1'\n")
    assert Path(context.postgres_config_path).read_text(encoding="utf-8") == (
        "listen_addresses = '127.0.0.1'\n"
    )


def test_default_write_postgres_conf_leaves_no_tmp_file_behind(tmp_path: Path) -> None:
    context = _context(tmp_path)
    default_write_postgres_conf(context)("x")
    leftovers = list(Path(context.postgres_config_path).parent.glob("*.tmp"))
    assert leftovers == []


def test_default_write_pg_hba_conf_writes_exact_content(tmp_path: Path) -> None:
    context = _context(tmp_path)
    default_write_pg_hba_conf(context)("host all all 127.0.0.1/32 scram-sha-256\n")
    assert (
        Path(context.postgres_hba_path).read_text(encoding="utf-8")
        == "host all all 127.0.0.1/32 scram-sha-256\n"
    )


def test_default_detect_postgres_cluster_returns_none_when_pg_version_absent(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    detect = default_detect_postgres_cluster(context)
    assert detect() is None


def test_default_detect_postgres_cluster_returns_stripped_version(tmp_path: Path) -> None:
    context = _context(tmp_path)
    Path(context.postgres_data_dir).mkdir(parents=True)
    (Path(context.postgres_data_dir) / "PG_VERSION").write_text("17\n", encoding="utf-8")
    detect = default_detect_postgres_cluster(context)
    assert detect() == "17"


# --- Windows pwfile ACL restriction (icacls mocked; no real ACL call) ----------
#
# Mirrors tests/certs/test_private_key_acl.py's contract for the same class
# of credential-bearing temp file: the Windows-specific restrictor is exposed
# as its own non-branching function, so it is exercised directly here
# regardless of the host OS actually running pytest (CI runs the "pure" test
# suite on both ubuntu-latest and windows-latest).


def test_windows_pwfile_acl_command_removes_inheritance() -> None:
    path = Path(r"C:\ProgramData\CivicCast\provision\tmp\initdb-pwfile-123-abc.tmp")

    command = _windows_pwfile_acl_command(path, caller_sid="S-1-5-21-123-456-789-1001")

    assert command == [
        "icacls",
        str(path),
        "/inheritance:r",
        "/grant:r",
        "*S-1-5-21-123-456-789-1001:F",
        "*S-1-5-18:F",
        "*S-1-5-32-544:F",
    ]


@pytest.mark.parametrize(
    "username, sid",
    [("machine-account$", "S-1-5-18"), ("operator", "S-1-5-21-123-456-789-1001"), ("", "S-1-5-18")],
)
def test_restrict_windows_pwfile_acl_uses_token_not_environment(
    monkeypatch, tmp_path, username, sid
):
    import civiccast.native.provision.seams as seams_module

    monkeypatch.setenv("USERNAME", username)
    monkeypatch.setattr(seams_module, "_current_process_sid", lambda: sid, raising=False)
    calls = []
    monkeypatch.setattr(
        seams_module.subprocess, "run", lambda command, **kwargs: calls.append(command)
    )
    _restrict_windows_pwfile_acl(tmp_path / "pwfile.tmp")
    assert calls[0][4:] == [f"*{sid}:F", "*S-1-5-18:F", "*S-1-5-32-544:F"]


def test_restrict_windows_pwfile_acl_runs_icacls_without_shell(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import civiccast.native.provision.seams as seams_module

    calls: list[dict[str, object]] = []
    pwfile = tmp_path / "pwfile.tmp"
    monkeypatch.setenv("USERNAME", "operator")
    monkeypatch.setattr(
        seams_module, "_current_process_sid", lambda: "S-1-5-21-123-456-789-1001", raising=False
    )

    def fake_run(command, **kwargs):  # type: ignore[no-untyped-def]
        calls.append({"command": command, **kwargs})

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)

    _restrict_windows_pwfile_acl(pwfile)

    assert calls == [
        {
            "command": _windows_pwfile_acl_command(pwfile, caller_sid="S-1-5-21-123-456-789-1001"),
            "check": True,
            "capture_output": True,
            "text": True,
            # C2: every install-time subprocess carries a HARD deadline --
            # an icacls that never returns must fail the run, not hang it.
            "timeout": seams_module.ICACLS_TIMEOUT_SECONDS,
            "creationflags": getattr(seams_module.subprocess, "CREATE_NO_WINDOW", 0),
        }
    ]


# --- _initdb_pwfile lifecycle (real filesystem, tmp_path; no icacls call) ------


def test_initdb_pwfile_content_is_password_plus_newline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Skip the real ACL call on either platform branch -- this test is about
    # file CONTENT, not ACL enforcement (covered separately above/below).
    monkeypatch.setattr(
        "civiccast.native.provision.seams._restrict_windows_pwfile_acl", lambda path: None
    )
    monkeypatch.setattr(Path, "chmod", lambda self, mode: None)

    captured: Path | None = None
    with _initdb_pwfile(tmp_path / "state", "hunter2") as pwfile:
        captured = pwfile
        assert pwfile.read_text(encoding="utf-8") == "hunter2\n"

    assert captured is not None
    assert not captured.exists()  # deleted on clean exit


def test_initdb_pwfile_lives_under_work_root_not_system_temp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "civiccast.native.provision.seams._restrict_windows_pwfile_acl", lambda path: None
    )
    monkeypatch.setattr(Path, "chmod", lambda self, mode: None)

    # A caller-supplied state_root that is deliberately NOT anywhere near the
    # OS temp directory (unlike pytest's own tmp_path fixture, which itself
    # happens to live under the system temp root -- so a plain
    # is_relative_to(tempfile.gettempdir()) assertion here would be
    # meaningless/always-true regardless of what the code under test does).
    state_root = tmp_path / "not-the-system-temp-dir" / "provision-state"
    with _initdb_pwfile(state_root, "hunter2") as pwfile:
        # Lands exactly under the caller-supplied work root's own "tmp"
        # subdirectory -- never anywhere the caller didn't name.
        assert pwfile.parent == state_root / "tmp"

    # The seam module itself never reaches for the stdlib tempfile module
    # (mkstemp/NamedTemporaryFile/gettempdir) -- the ONLY source of the
    # pwfile's location is the caller-supplied work_root argument.
    import civiccast.native.provision.seams as seams_module

    assert "tempfile" not in vars(seams_module)


def test_initdb_pwfile_deleted_when_body_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "civiccast.native.provision.seams._restrict_windows_pwfile_acl", lambda path: None
    )
    monkeypatch.setattr(Path, "chmod", lambda self, mode: None)

    captured: Path | None = None
    with (
        pytest.raises(RuntimeError, match="boom"),
        _initdb_pwfile(tmp_path / "state", "hunter2") as pwfile,
    ):
        captured = pwfile
        assert captured.exists()
        raise RuntimeError("boom")

    assert captured is not None
    assert not captured.exists()  # deleted even though the body raised


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_initdb_pwfile_deleted_when_acl_restriction_itself_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # If ACL restriction fails (e.g. USERNAME unset mid-run), the plaintext
    # password file must still never be left behind.
    def _boom(path: Path) -> None:
        raise RuntimeError("icacls exploded")

    monkeypatch.setattr("civiccast.native.provision.seams._restrict_windows_pwfile_acl", _boom)
    monkeypatch.setattr("civiccast.native.provision.seams.os.name", "nt")

    state_root = tmp_path / "state"
    with (
        pytest.raises(RuntimeError, match="icacls exploded"),
        _initdb_pwfile(state_root, "hunter2"),
    ):
        pass  # pragma: no cover - never reached, ACL restriction raises first

    leftovers = list((state_root / "tmp").glob("initdb-pwfile-*"))
    assert leftovers == []


# --- default_run_initdb (subprocess mocked; NO real initdb spawn) --------------


def _no_acl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize the real ACL call for tests that only care about the
    subprocess/pwfile-lifecycle contract, not ACL enforcement itself."""

    monkeypatch.setattr(
        "civiccast.native.provision.seams._restrict_windows_pwfile_acl", lambda path: None
    )
    monkeypatch.setattr(Path, "chmod", lambda self, mode: None)


def test_default_run_initdb_command_includes_pwfile_and_pinned_auth_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import civiccast.native.provision.seams as seams_module

    _no_acl(monkeypatch)
    context = _context(tmp_path)
    captured_argv: list[str] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured_argv.extend(argv)

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)

    default_run_initdb(context, initdb_path="initdb.exe", database_username="civiccast_svc")()

    assert "--pwfile" in captured_argv
    pwfile_arg = captured_argv[captured_argv.index("--pwfile") + 1]
    assert Path(context.state_root) in Path(pwfile_arg).parents
    assert f"--auth-host={INITDB_AUTH_HOST_METHOD}" in captured_argv
    assert "hunter2" not in captured_argv  # the password itself never hits argv


def test_default_run_initdb_writes_password_to_pwfile_before_spawning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import civiccast.native.provision.seams as seams_module

    _no_acl(monkeypatch)
    context = _context(tmp_path)
    observed_content: list[str] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        pwfile_arg = argv[argv.index("--pwfile") + 1]
        observed_content.append(Path(pwfile_arg).read_text(encoding="utf-8"))

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)

    default_run_initdb(context, initdb_path="initdb.exe", database_username="civiccast_svc")()

    assert observed_content == ["hunter2\n"]


def test_default_run_initdb_deletes_pwfile_after_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import civiccast.native.provision.seams as seams_module

    _no_acl(monkeypatch)
    context = _context(tmp_path)
    captured_path: list[Path] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured_path.append(Path(argv[argv.index("--pwfile") + 1]))

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)

    default_run_initdb(context, initdb_path="initdb.exe", database_username="civiccast_svc")()

    assert len(captured_path) == 1
    assert not captured_path[0].exists()


def test_default_run_initdb_deletes_pwfile_after_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import civiccast.native.provision.seams as seams_module

    _no_acl(monkeypatch)
    context = _context(tmp_path)
    captured_path: list[Path] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured_path.append(Path(argv[argv.index("--pwfile") + 1]))

        class Result:
            returncode = 1
            stdout = ""
            stderr = "initdb: error: could not access directory"

        return Result()

    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)

    run = default_run_initdb(context, initdb_path="initdb.exe", database_username="civiccast_svc")
    with pytest.raises(RuntimeError, match="initdb failed"):
        run()

    assert len(captured_path) == 1
    assert not captured_path[0].exists()  # deleted even though initdb reported failure


def test_default_run_initdb_deletes_pwfile_when_spawn_itself_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import civiccast.native.provision.seams as seams_module

    _no_acl(monkeypatch)
    context = _context(tmp_path)
    captured_path: list[Path] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured_path.append(Path(argv[argv.index("--pwfile") + 1]))
        raise FileNotFoundError("initdb.exe not found")

    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)

    run = default_run_initdb(context, initdb_path="initdb.exe", database_username="civiccast_svc")
    with pytest.raises(FileNotFoundError):
        run()

    assert len(captured_path) == 1
    assert not captured_path[0].exists()  # deleted even though subprocess spawn itself raised


def test_default_run_initdb_error_includes_both_stdout_and_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same precedence-bug class as pg_ctl_exec.run_pg_ctl_argv (see that
    module's docstring): the old `_decode_output(stderr) or
    _decode_output(stdout)` silently discarded whichever stream carried the
    real diagnosis whenever the OTHER stream was also non-empty. Both must
    surface in the raised error."""
    import civiccast.native.provision.seams as seams_module

    _no_acl(monkeypatch)
    context = _context(tmp_path)

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        class Result:
            returncode = 1
            stdout = "DIAGNOSIS: could not access directory /pgdata: Permission denied"
            stderr = "initdb: error: could not access directory"

        return Result()

    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)

    run = default_run_initdb(context, initdb_path="initdb.exe", database_username="civiccast_svc")
    with pytest.raises(RuntimeError) as exc_info:
        run()

    message = str(exc_info.value)
    assert "DIAGNOSIS: could not access directory /pgdata" in message
    assert "initdb: error: could not access directory" in message


def test_default_run_initdb_pwfile_never_lands_under_postgres_data_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # initdb refuses to run against a non-empty target directory; a pwfile
    # written inside postgres_data_dir would break every fresh initdb call.
    import civiccast.native.provision.seams as seams_module

    _no_acl(monkeypatch)
    context = _context(tmp_path)
    captured_path: list[Path] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured_path.append(Path(argv[argv.index("--pwfile") + 1]))

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)

    default_run_initdb(context, initdb_path="initdb.exe", database_username="civiccast_svc")()

    assert Path(context.postgres_data_dir) not in captured_path[0].parents
    assert captured_path[0].parent != Path(context.postgres_data_dir)


# --- default_ensure_database (BLOCKER #52; subprocess mocked, NO real pg_ctl/psql spawn) ---


def _plan() -> ProvisionPlan:
    return ProvisionPlan(
        postgres_major_version="17",
        database_name="civiccast",
        database_username="civiccast_svc",
        server_pack_product_version="1.0.0",
        server_pack_compatible_core="1.0.0",
        server_pack_signing_key_id="key-1",
    )


class _FakeCompletedProcess:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _fake_run_factory(
    *, database_present: bool, fail_create: bool = False, fail_stop: bool = False
):  # type: ignore[no-untyped-def]
    """Build a fake subprocess.run that answers pg_ctl start/stop and the two
    psql invocations (existence check, create) by inspecting argv shape --
    mirrors the argv-index-based fakes already used for default_run_initdb
    above, at one level of dispatch higher (multiple distinct commands)."""

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(argv))
        binary = Path(argv[0]).name
        if binary == "pg_ctl.exe":
            if argv[1] == "stop" and fail_stop:
                return _FakeCompletedProcess(1, stderr="pg_ctl: server does not shut down")
            return _FakeCompletedProcess(0)
        if binary == "psql.exe":
            sql = argv[argv.index("-c") + 1]
            if sql.startswith("SELECT 1 FROM pg_database"):
                return _FakeCompletedProcess(0, stdout="1\n" if database_present else "")
            if sql.startswith("CREATE DATABASE"):
                if fail_create:
                    return _FakeCompletedProcess(
                        1, stderr='ERROR:  permission denied to create database "civiccast"'
                    )
                return _FakeCompletedProcess(0)
        raise AssertionError(f"unexpected argv: {argv}")

    return fake_run, calls


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_ensure_database_absent_creates_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import civiccast.native.provision.seams as seams_module

    fake_run, calls = _fake_run_factory(database_present=False)
    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)

    context = _context(tmp_path)
    ensure = default_ensure_database(
        context, _plan(), pg_ctl_path=r"C:\pg\bin\pg_ctl.exe", psql_path=r"C:\pg\bin\psql.exe"
    )
    decision = ensure()

    assert decision.outcome == "created"
    assert "created" in decision.detail
    binaries = [Path(call[0]).name for call in calls]
    assert binaries == ["pg_ctl.exe", "psql.exe", "psql.exe", "pg_ctl.exe"]
    assert calls[0][1] == "start"
    assert calls[-1][1] == "stop"


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_ensure_database_present_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import civiccast.native.provision.seams as seams_module

    fake_run, calls = _fake_run_factory(database_present=True)
    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)

    context = _context(tmp_path)
    ensure = default_ensure_database(
        context, _plan(), pg_ctl_path=r"C:\pg\bin\pg_ctl.exe", psql_path=r"C:\pg\bin\psql.exe"
    )
    decision = ensure()

    assert decision.outcome == "already_exists"
    # start, the existence check, then stop -- CREATE DATABASE never runs.
    binaries = [Path(call[0]).name for call in calls]
    assert binaries == ["pg_ctl.exe", "psql.exe", "pg_ctl.exe"]
    create_calls = [
        c for c in calls if c[0].endswith("psql.exe") and "CREATE DATABASE" in " ".join(c)
    ]
    assert create_calls == []


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_ensure_database_creation_failure_is_loud_and_still_stops_postgres(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import civiccast.native.provision.seams as seams_module

    fake_run, calls = _fake_run_factory(database_present=False, fail_create=True)
    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)

    context = _context(tmp_path)
    ensure = default_ensure_database(
        context, _plan(), pg_ctl_path=r"C:\pg\bin\pg_ctl.exe", psql_path=r"C:\pg\bin\psql.exe"
    )

    with pytest.raises(RuntimeError, match="psql failed while creating database"):
        ensure()

    # The finally-block contract: pg_ctl stop still ran even though create failed.
    binaries = [Path(call[0]).name for call in calls]
    assert binaries == ["pg_ctl.exe", "psql.exe", "psql.exe", "pg_ctl.exe"]
    assert calls[-1][1] == "stop"


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_ensure_database_creation_failure_includes_both_stdout_and_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same precedence-bug class as default_run_initdb above, at the CREATE
    DATABASE psql call site: both streams must surface, not just stderr."""
    import civiccast.native.provision.seams as seams_module

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        binary = Path(argv[0]).name
        if binary == "pg_ctl.exe":
            return _FakeCompletedProcess(0)
        sql = argv[argv.index("-c") + 1]
        if sql.startswith("SELECT 1 FROM pg_database"):
            return _FakeCompletedProcess(0, stdout="")
        if sql.startswith("CREATE DATABASE"):
            return _FakeCompletedProcess(
                1,
                stdout="DIAGNOSIS: connection authorized",
                stderr='ERROR:  permission denied to create database "civiccast"',
            )
        raise AssertionError(f"unexpected argv: {argv}")

    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)

    context = _context(tmp_path)
    ensure = default_ensure_database(
        context, _plan(), pg_ctl_path=r"C:\pg\bin\pg_ctl.exe", psql_path=r"C:\pg\bin\psql.exe"
    )

    with pytest.raises(RuntimeError) as exc_info:
        ensure()

    message = str(exc_info.value)
    assert "DIAGNOSIS: connection authorized" in message
    assert 'permission denied to create database "civiccast"' in message


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_ensure_database_existence_check_failure_includes_both_stdout_and_stderr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Same precedence-bug class, at the pg_database existence-check psql
    call site: both streams must surface, not just stderr."""
    import civiccast.native.provision.seams as seams_module

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        binary = Path(argv[0]).name
        if binary == "pg_ctl.exe":
            return _FakeCompletedProcess(0)
        sql = argv[argv.index("-c") + 1]
        if sql.startswith("SELECT 1 FROM pg_database"):
            return _FakeCompletedProcess(
                1,
                stdout="DIAGNOSIS: server closed the connection unexpectedly",
                stderr="psql: error: connection to server was lost",
            )
        raise AssertionError(f"unexpected argv: {argv}")

    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)

    context = _context(tmp_path)
    ensure = default_ensure_database(
        context, _plan(), pg_ctl_path=r"C:\pg\bin\pg_ctl.exe", psql_path=r"C:\pg\bin\psql.exe"
    )

    with pytest.raises(RuntimeError) as exc_info:
        ensure()

    message = str(exc_info.value)
    assert "DIAGNOSIS: server closed the connection unexpectedly" in message
    assert "connection to server was lost" in message


def test_ensure_database_start_failure_never_touches_psql(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import civiccast.native.provision.seams as seams_module

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        return _FakeCompletedProcess(1, stderr="pg_ctl: could not start server")

    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)

    context = _context(tmp_path)
    ensure = default_ensure_database(
        context, _plan(), pg_ctl_path=r"C:\pg\bin\pg_ctl.exe", psql_path=r"C:\pg\bin\psql.exe"
    )

    with pytest.raises(RuntimeError, match="pg_ctl start failed"):
        ensure()


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_ensure_database_stop_failure_propagates_after_successful_create(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stop failure AFTER a successful create must still surface loudly --
    the database was created, but an operator needs to know postgres may
    still be running."""

    import civiccast.native.provision.seams as seams_module

    fake_run, _calls = _fake_run_factory(database_present=False, fail_stop=True)
    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)

    context = _context(tmp_path)
    ensure = default_ensure_database(
        context, _plan(), pg_ctl_path=r"C:\pg\bin\pg_ctl.exe", psql_path=r"C:\pg\bin\psql.exe"
    )

    with pytest.raises(RuntimeError, match="pg_ctl stop failed"):
        ensure()


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_ensure_database_passes_password_via_env_never_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import civiccast.native.provision.seams as seams_module

    captured_envs: list[dict[str, str]] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        env = kwargs.get("env")
        if env is not None:
            captured_envs.append(env)
            assert "hunter2" not in " ".join(argv)
        binary = Path(argv[0]).name
        if binary == "pg_ctl.exe":
            return _FakeCompletedProcess(0)
        sql = argv[argv.index("-c") + 1]
        if sql.startswith("SELECT 1 FROM pg_database"):
            return _FakeCompletedProcess(0, stdout="1\n")
        return _FakeCompletedProcess(0)

    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)

    context = _context(tmp_path)
    ensure = default_ensure_database(
        context, _plan(), pg_ctl_path=r"C:\pg\bin\pg_ctl.exe", psql_path=r"C:\pg\bin\psql.exe"
    )
    ensure()

    assert captured_envs, "expected at least one psql invocation with an env"
    assert all(env.get("PGPASSWORD") == "hunter2" for env in captured_envs)


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_ensure_database_create_argv_quotes_name_and_owner(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import civiccast.native.provision.seams as seams_module

    fake_run, calls = _fake_run_factory(database_present=False)
    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)

    context = _context(tmp_path)
    ensure = default_ensure_database(
        context, _plan(), pg_ctl_path=r"C:\pg\bin\pg_ctl.exe", psql_path=r"C:\pg\bin\psql.exe"
    )
    ensure()

    create_call = next(
        c for c in calls if Path(c[0]).name == "psql.exe" and "CREATE DATABASE" in " ".join(c)
    )
    sql = create_call[create_call.index("-c") + 1]
    assert sql == 'CREATE DATABASE "civiccast" OWNER "civiccast_svc"'


# --- C2 (2026-07-31): install-time children are file-backed + hard-bounded ----


def test_default_run_initdb_is_file_backed_with_a_hard_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """C2: initdb previously ran with capture_output=True and NO timeout --
    the live-proven Windows pipe-inheritance hang class (Sandbox runs
    14/15). The spawn must now receive real FILES for stdout/stderr (never
    capture_output pipes) and the pinned hard deadline."""

    import civiccast.native.provision.seams as seams_module

    _no_acl(monkeypatch)
    context = _context(tmp_path)
    captured_kwargs: list[dict[str, object]] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured_kwargs.append(kwargs)

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    # The one shared subprocess module object -- pg_ctl_exec resolves
    # subprocess.run at CALL time exactly so this seam keeps working.
    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)

    default_run_initdb(context, initdb_path="initdb.exe", database_username="civiccast_svc")()

    (kwargs,) = captured_kwargs
    assert "capture_output" not in kwargs, "pipes are the proven hang -- files only"
    assert hasattr(kwargs["stdout"], "fileno"), "stdout must be a real file"
    assert hasattr(kwargs["stderr"], "fileno"), "stderr must be a real file"
    assert kwargs["timeout"] == seams_module.INITDB_TIMEOUT_SECONDS


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_ensure_database_psql_calls_are_file_backed_with_a_hard_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """C2: both psql invocations (existence check + CREATE DATABASE) must be
    file-backed with the pinned hard deadline, and still carry PGPASSWORD
    via env."""

    import civiccast.native.provision.seams as seams_module

    psql_kwargs: list[dict[str, object]] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        if Path(argv[0]).name == "psql.exe":
            psql_kwargs.append(kwargs)
            sql = argv[argv.index("-c") + 1]
            if sql.startswith("SELECT 1 FROM pg_database"):
                return _FakeCompletedProcess(0, stdout="")
        return _FakeCompletedProcess(0)

    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)

    context = _context(tmp_path)
    ensure = default_ensure_database(
        context, _plan(), pg_ctl_path=r"C:\pg\bin\pg_ctl.exe", psql_path=r"C:\pg\bin\psql.exe"
    )
    decision = ensure()

    assert decision.outcome == "created"
    assert len(psql_kwargs) == 2  # existence check + create
    for kwargs in psql_kwargs:
        assert "capture_output" not in kwargs
        assert hasattr(kwargs["stdout"], "fileno")
        assert hasattr(kwargs["stderr"], "fileno")
        assert kwargs["timeout"] == seams_module.PSQL_TIMEOUT_SECONDS
        env = kwargs["env"]
        assert isinstance(env, dict) and env.get("PGPASSWORD") == "hunter2"


def test_restrict_windows_pwfile_acl_timeout_is_a_hard_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """C2: an icacls that never returns must surface as TimeoutExpired, not
    hang the install forever (the call previously had NO timeout at all)."""

    import subprocess as subprocess_module

    import civiccast.native.provision.seams as seams_module

    monkeypatch.setenv("USERNAME", "operator")
    monkeypatch.setattr(
        seams_module, "_current_process_sid", lambda: "S-1-5-21-123-456-789-1001", raising=False
    )

    def hanging_run(command, **kwargs):  # type: ignore[no-untyped-def]
        assert kwargs["timeout"] == seams_module.ICACLS_TIMEOUT_SECONDS
        raise subprocess_module.TimeoutExpired(cmd=command, timeout=kwargs["timeout"])

    monkeypatch.setattr(seams_module.subprocess, "run", hanging_run)

    with pytest.raises(subprocess_module.TimeoutExpired):
        _restrict_windows_pwfile_acl(tmp_path / "pwfile.tmp")


# --- C1 (2026-07-31): post-provision schema migration to alembic head ---------


def test_run_schema_migration_to_head_reuses_the_d3_upgrade_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C1: the migration must go through the SAME runner the D3 upgrade
    orchestrator uses (upgrade.seams.default_migrate -- the product's only
    alembic entry point), never a second hand-rolled alembic invocation."""

    import civiccast.native.provision.seams as seams_module
    import civiccast.native.upgrade.seams as upgrade_seams

    received: list[object] = []

    def fake_default_migrate(upgrade_context):  # type: ignore[no-untyped-def]
        received.append(upgrade_context)
        return lambda: None

    monkeypatch.setattr(upgrade_seams, "default_migrate", fake_default_migrate)

    seams_module.run_schema_migration_to_head(
        database_url="sqlite:///scratch.db",
        install_root=r"C:\install",
        state_root=r"C:\pd\CivicCast\provision",
        owner_run_id="run-1",
    )

    (upgrade_context,) = received
    assert upgrade_context.database_url == "sqlite:///scratch.db"
    assert upgrade_context.install_root == r"C:\install"
    assert upgrade_context.state_root == r"C:\pd\CivicCast\provision"
    assert upgrade_context.owner_run_id == "run-1"


def test_migrate_provisioned_schema_starts_migrates_then_stops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import civiccast.native.provision.seams as seams_module
    from civiccast.native.pg_ctl_exec import PgCtlResult

    pg_ctl_calls: list[list[str]] = []

    def fake_pg_ctl(argv, *, timeout_seconds, runner=None):  # type: ignore[no-untyped-def]
        pg_ctl_calls.append(list(argv))
        return PgCtlResult(returncode=0, output_tail="")

    monkeypatch.setattr(seams_module, "run_pg_ctl_argv", fake_pg_ctl)

    migrations: list[dict[str, str]] = []
    monkeypatch.setattr(
        seams_module,
        "run_schema_migration_to_head",
        lambda **kwargs: migrations.append(kwargs),
    )

    context = _context(tmp_path)
    seams_module.migrate_provisioned_schema(
        context,
        pg_ctl_path=r"C:\pg\bin\pg_ctl.exe",
        database_url="postgresql://u:p@127.0.0.1:5432/civiccast",
        install_root=r"C:\install",
    )

    assert [call[1] for call in pg_ctl_calls] == ["start", "stop"]
    assert migrations == [
        {
            "database_url": "postgresql://u:p@127.0.0.1:5432/civiccast",
            "install_root": r"C:\install",
            "state_root": context.state_root,
            "owner_run_id": context.owner_run_id,
        }
    ]


def test_migrate_provisioned_schema_stops_postgres_even_when_migration_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The same never-left-running finally-contract default_ensure_database
    holds: a postgres started for the migration must be stopped even when
    the migration itself fails."""

    import civiccast.native.provision.seams as seams_module
    from civiccast.native.pg_ctl_exec import PgCtlResult

    pg_ctl_calls: list[list[str]] = []

    def fake_pg_ctl(argv, *, timeout_seconds, runner=None):  # type: ignore[no-untyped-def]
        pg_ctl_calls.append(list(argv))
        return PgCtlResult(returncode=0, output_tail="")

    monkeypatch.setattr(seams_module, "run_pg_ctl_argv", fake_pg_ctl)

    def boom(**kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("alembic exploded")

    monkeypatch.setattr(seams_module, "run_schema_migration_to_head", boom)

    with pytest.raises(RuntimeError, match="alembic exploded"):
        seams_module.migrate_provisioned_schema(
            _context(tmp_path),
            pg_ctl_path=r"C:\pg\bin\pg_ctl.exe",
            database_url="postgresql://u:p@127.0.0.1:5432/civiccast",
            install_root=r"C:\install",
        )

    assert [call[1] for call in pg_ctl_calls] == ["start", "stop"]


# --- row-4b: pgdata ACL normalization precedes every install-time start -------


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_ensure_database_normalizes_the_pgdata_acl_before_starting_postgres(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pgdata_acl_calls: list[str]
) -> None:
    """Sandbox run 21's row-4b fix, provisioning side: the data directory's
    DACL is normalized BEFORE pg_ctl runs, so the cluster is born with a
    protected, inheritable SYSTEM+Administrators+installer descriptor -- which
    is what every WAL segment the LocalSystem service later creates inherits,
    and therefore what makes the NEXT update's D3 start possible at all."""

    import civiccast.native.provision.seams as seams_module

    order: list[str] = []

    def _record_acl(data_dir: str) -> None:
        order.append(f"acl:{data_dir}")

    fake_run, _calls = _fake_run_factory(database_present=True)

    def _tracking_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        order.append(Path(argv[0]).name + " " + argv[1])
        return fake_run(argv, **kwargs)

    monkeypatch.setattr(seams_module, "normalize_pgdata_acl", _record_acl)
    monkeypatch.setattr(seams_module.subprocess, "run", _tracking_run)

    context = _context(tmp_path)
    default_ensure_database(
        context, _plan(), pg_ctl_path=r"C:\pg\bin\pg_ctl.exe", psql_path=r"C:\pg\bin\psql.exe"
    )()

    assert order[0] == f"acl:{context.postgres_data_dir}"
    assert order[1] == "pg_ctl.exe start"
    assert pgdata_acl_calls == []  # the autouse recorder was overridden by this test


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_ensure_database_grants_the_bin_tree_before_starting_postgres(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, bin_acl_calls: list[str]
) -> None:
    """rc.6 -> rc.7 Dell fix, provisioning side: the PostgreSQL binary tree
    (and its ancestors up to the install root) is granted to the installing
    account before pg_ctl runs, alongside the pgdata ACL. Without it the
    restricted-token postmaster cannot load postgres.exe on a station whose
    install root has no entry for that account."""

    import civiccast.native.provision.seams as seams_module

    order: list[str] = []

    def _record_bin_acl(pg_ctl_path: str) -> None:
        order.append(f"bin-acl:{pg_ctl_path}")

    fake_run, _calls = _fake_run_factory(database_present=True)

    def _tracking_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        order.append(Path(argv[0]).name + " " + argv[1])
        return fake_run(argv, **kwargs)

    monkeypatch.setattr(seams_module, "normalize_postgres_bin_acl", _record_bin_acl)
    monkeypatch.setattr(seams_module.subprocess, "run", _tracking_run)

    context = _context(tmp_path)
    default_ensure_database(
        context, _plan(), pg_ctl_path=r"C:\pg\bin\pg_ctl.exe", psql_path=r"C:\pg\bin\psql.exe"
    )()

    assert order[0] == r"bin-acl:C:\pg\bin\pg_ctl.exe"
    assert order[1] == "pg_ctl.exe start"
    assert bin_acl_calls == []  # the autouse recorder was overridden by this test


def test_ensure_database_acl_failure_is_loud_and_never_starts_postgres(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fail-loud, never best-effort: a data directory whose ACL could not be
    normalized must stop provisioning, not be started anyway."""

    import civiccast.native.provision.seams as seams_module
    from civiccast.native.pgdata_acl import PgDataAclError

    def _boom(data_dir: str) -> None:
        raise PgDataAclError("pgdata-acl-normalize: access denied")

    def _must_not_spawn(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("no process may be spawned when ACL normalization failed")

    monkeypatch.setattr(seams_module, "normalize_pgdata_acl", _boom)
    monkeypatch.setattr(seams_module.subprocess, "run", _must_not_spawn)

    ensure = default_ensure_database(
        _context(tmp_path),
        _plan(),
        pg_ctl_path=r"C:\pg\bin\pg_ctl.exe",
        psql_path=r"C:\pg\bin\psql.exe",
    )

    with pytest.raises(PgDataAclError, match="pgdata-acl-normalize"):
        ensure()


def test_migrate_provisioned_schema_normalizes_the_pgdata_acl_before_starting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, pgdata_acl_calls: list[str]
) -> None:
    import civiccast.native.provision.seams as seams_module
    from civiccast.native.pg_ctl_exec import PgCtlResult

    monkeypatch.setattr(
        seams_module,
        "run_pg_ctl_argv",
        lambda argv, *, timeout_seconds, runner=None: PgCtlResult(returncode=0, output_tail=""),
    )
    monkeypatch.setattr(seams_module, "run_schema_migration_to_head", lambda **kwargs: None)

    context = _context(tmp_path)
    seams_module.migrate_provisioned_schema(
        context,
        pg_ctl_path=r"C:\pg\bin\pg_ctl.exe",
        database_url="postgresql://u:p@127.0.0.1:5432/civiccast",
        install_root=r"C:\install",
    )

    assert pgdata_acl_calls == [context.postgres_data_dir]


# --- N-15 adoption: pure builders (no real psql/pg_ctl spawned) ----------------


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_reset_cluster_credential_restores_scram_pg_hba_in_finally_on_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The finally-block contract for reset_cluster_credential: when
    _verify_adoptable_cluster raises AdoptionForeignClusterError (or any other
    fault), the scram-sha-256 pg_hba.conf is STILL restored and pg_ctl stop is
    STILL run, so the transient trust auth never persists on disk.

    This test verifies that moving the scram-restore out of the finally block
    (into a success-only path) would be caught as a regression -- an auth-bypass
    window where the trust pg_hba lingers after the function exits."""

    import civiccast.native.provision.seams as seams_module
    from civiccast.native.provision.conf import render_pg_hba_conf, render_pg_hba_trust_conf

    # Track all subprocess.run calls and the sequence of pg_hba writes.
    calls: list[list[str]] = []
    pg_hba_writes: list[str] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(argv))
        binary = Path(argv[0]).name
        if binary == "pg_ctl.exe":
            return _FakeCompletedProcess(0)
        raise AssertionError(f"unexpected argv: {argv}")

    def fake_atomic_write_text(path: str | Path, content: str) -> None:
        # Record what was written to pg_hba_path: trust or scram?
        if str(path).endswith("pg_hba.conf"):
            pg_hba_writes.append(content)

    monkeypatch.setattr(seams_module.subprocess, "run", fake_run)
    monkeypatch.setattr(seams_module, "_atomic_write_text", fake_atomic_write_text)

    # Make _verify_adoptable_cluster raise, simulating a foreign cluster that
    # was not product-provisioned. This forces the finally block to run while
    # still propagating the exception.
    def fake_verify_adoptable_cluster(
        psql_path: str, context: ProvisionContext, plan: ProvisionPlan
    ) -> None:
        raise AdoptionForeignClusterError(
            f"database {plan.database_name!r} is absent on the cluster at "
            f"{context.postgres_data_dir!r}; refusing to adopt"
        )

    monkeypatch.setattr(seams_module, "_verify_adoptable_cluster", fake_verify_adoptable_cluster)

    context = _context(tmp_path)
    plan = _plan()

    # Verify that the exception propagates.
    with pytest.raises(AdoptionForeignClusterError, match="refusing to adopt"):
        reset_cluster_credential(
            context,
            plan,
            pg_ctl_path=r"C:\pg\bin\pg_ctl.exe",
            psql_path=r"C:\pg\bin\psql.exe",
        )

    # The finally-block contract: pg_ctl stop ran and scram pg_hba was restored
    # even though verify failed. Verify the sequence:
    # 1. First pg_hba write is trust (the initial write)
    # 2. pg_ctl start was called
    # 3. pg_ctl stop was called (in the finally)
    # 4. Second pg_hba write is scram (the restore in the finally)

    # Check pg_hba writes: first should be trust, second should be scram.
    assert len(pg_hba_writes) == 2
    assert render_pg_hba_trust_conf(host=context.postgres_host) == pg_hba_writes[0]
    assert render_pg_hba_conf(host=context.postgres_host) == pg_hba_writes[1]

    # Check subprocess calls: pg_ctl start, then pg_ctl stop.
    binaries = [Path(call[0]).name for call in calls]
    assert binaries == ["pg_ctl.exe", "pg_ctl.exe"]
    assert calls[0][1] == "start"
    assert calls[1][1] == "stop"


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_psql_and_pg_ctl_path_for_resolve_siblings_of_initdb() -> None:
    initdb = (
        r"C:\Program Files\CivicCast (Native)\packs\native-server-binaries\payload\bin\initdb.exe"
    )
    assert psql_path_for(initdb).endswith(r"payload\bin\psql.exe")
    assert pg_ctl_path_for(initdb).endswith(r"payload\bin\pg_ctl.exe")


def test_alter_role_password_sql_shape_is_double_quoted_ident_single_quoted_literal() -> None:
    # DDL/PASSWORD cannot take a bind parameter, so the identifier is
    # double-quoted (safe-identifier validated) and the password is a
    # single-quoted literal (charset-guarded). The generated token_urlsafe
    # charset has no quote to escape.
    sql = alter_role_password_sql(username="civiccast_svc", password="Ab3-_xyz09")
    assert sql == "ALTER ROLE \"civiccast_svc\" PASSWORD 'Ab3-_xyz09';\n"


def test_alter_role_password_sql_rejects_a_password_outside_the_url_safe_charset() -> None:
    # Fail CLOSED rather than risk an unescaped single quote / injection in the
    # literal. A real generated password (secrets.token_urlsafe) never trips
    # this; a value with a quote or space MUST.
    for bad in ("has space", "has'quote", "semi;colon", "back\\slash", ""):
        with pytest.raises(ValueError, match="password"):
            alter_role_password_sql(username="civiccast_svc", password=bad)


def test_alter_role_password_sql_rejects_an_unsafe_identifier() -> None:
    with pytest.raises(ValueError, match="identifier"):
        alter_role_password_sql(username='civiccast"; DROP', password="Ab3xyz09")


@pytest.mark.skipif(os.name != "nt", reason="real Windows password-file ACL")
@pytest.mark.windows_only
def test_pwfile_acl_survives_machine_username_and_postgres_restricted_token(monkeypatch, tmp_path):
    import ntsecuritycon
    import win32security

    from civiccast.native.provision.seams import _current_process_sid
    from tests.native.test_pgdata_acl_win import (
        _build_pg_ctl_shaped_restricted_token,
        _impersonating,
    )

    monkeypatch.setenv("USERNAME", "nonexistent-machine-account$")
    target = tmp_path / "password.tmp"
    target.write_text("test-only-password\n", encoding="utf-8")
    _restrict_windows_pwfile_acl(target)

    allowed = {_current_process_sid(), "S-1-5-18", "S-1-5-32-544"}
    owner_rights_sid = "S-1-3-4"
    security_info = (
        win32security.DACL_SECURITY_INFORMATION | win32security.OWNER_SECURITY_INFORMATION
    )
    descriptor = win32security.GetNamedSecurityInfo(
        str(target), win32security.SE_FILE_OBJECT, security_info
    )
    owner_sid = win32security.ConvertSidToStringSid(descriptor.GetSecurityDescriptorOwner())
    dacl = descriptor.GetSecurityDescriptorDacl()
    sddl = win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(
        descriptor, win32security.SDDL_REVISION_1, security_info
    )
    diagnostic = f"path={target}, owner={owner_sid}, SDDL={sddl}"

    assert descriptor.GetSecurityDescriptorControl()[0] & win32security.SE_DACL_PROTECTED, (
        diagnostic
    )
    assert dacl is not None, diagnostic
    # S-1-3-4 is Owner Rights: it resolves to this object's owner. Count it
    # only when that owner is already one of the three explicitly permitted
    # identities, so the alias cannot expand the password file's readership.
    assert owner_sid in allowed, diagnostic
    actual_principals = set()
    ace_rows = []
    allowed_flags = {0, win32security.INHERITED_ACE}
    for index in range(dacl.GetAceCount()):
        ace = dacl.GetAce(index)
        assert len(ace) == 3 and len(ace[0]) == 2, f"{diagnostic}; ACE[{index}]={ace!r}"
        ace_type, ace_flags = ace[0]
        access_mask = ace[1]
        trustee_sid = win32security.ConvertSidToStringSid(ace[2])
        ace_rows.append((ace_type, ace_flags, hex(access_mask), trustee_sid))
        ace_diagnostic = f"{diagnostic}; ACEs={ace_rows!r}"
        assert ace_type == win32security.ACCESS_ALLOWED_ACE_TYPE, ace_diagnostic
        assert ace_flags in allowed_flags, ace_diagnostic
        assert access_mask == ntsecuritycon.FILE_ALL_ACCESS, ace_diagnostic
        actual_principals.add(owner_sid if trustee_sid == owner_rights_sid else trustee_sid)

    assert actual_principals == allowed, f"{diagnostic}; ACEs={ace_rows!r}"
    token = _build_pg_ctl_shaped_restricted_token()
    try:
        with _impersonating(token):
            assert target.read_text(encoding="utf-8") == "test-only-password\n"
    finally:
        token.Close()
