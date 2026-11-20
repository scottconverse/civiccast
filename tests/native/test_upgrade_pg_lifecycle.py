# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""BLOCKER #49: the D3 upgrade engine's scoped postgres lifecycle.

Live-proven defect (gauntlet-run11, row-4b): the documented update path
(uninstall preserving the DB cluster, then reinstall) runs D3 BEFORE D4
provisioning, so an uninstall that removed the CivicCastSupervisor service
leaves nothing running postgres when D3's first DB-touching seam
(``schema_revision``) fires -- an uncaught connection fault maps to engine
exit 40 / installer exit 115.

These tests prove the PURE decision logic (:func:`wrap_schema_revision`) over
fully injected fakes -- no subprocess, no SQLAlchemy, no SCM -- plus the real
primitives' message/argv shape via monkeypatched ``subprocess.run`` /
``sqlalchemy.create_engine``, and the path-derivation convention reuse.
"""

from __future__ import annotations

import os
import subprocess

import pytest

from civiccast.native.upgrade import pg_lifecycle
from civiccast.native.upgrade.models import UpgradeContext
from civiccast.native.upgrade.pg_lifecycle import (
    FAIL_CLOSED_DETAIL,
    DatabaseMissingError,
    PgLifecycleState,
    PostgresLifecycleError,
    attach_pg_lifecycle,
    derive_pg_lifecycle_paths,
    real_database_reachable,
    real_start_postgres,
    real_stop_postgres,
    service_allows_scoped_postgres_start,
    wrap_health_gate,
    wrap_restore_backup,
    wrap_schema_revision,
)

_WINDOWS_PATH_TEST = pytest.mark.skipif(
    os.name != "nt", reason="requires native Windows path semantics"
)

# ---------------------------------------------------------------------------
# wrap_schema_revision -- pure decision logic
# ---------------------------------------------------------------------------


def _harness():
    calls: list[str] = []
    state = PgLifecycleState()
    return calls, state


def test_reachable_never_starts_and_never_checks_service() -> None:
    calls, state = _harness()

    def service_probe() -> bool | None:
        calls.append("service_probe")
        return False

    wrapped = wrap_schema_revision(
        lambda: (calls.append("schema"), "rev-1")[1],
        database_reachable=lambda: (calls.append("reachable"), True)[1],
        service_registered_probe=service_probe,
        start_postgres=lambda: calls.append("start"),
        state=state,
    )

    assert wrapped() == "rev-1"
    assert calls == ["reachable", "schema"]
    assert state.started_by_us is False


def test_unreachable_service_absent_starts_and_stop_owed() -> None:
    calls, state = _harness()

    wrapped = wrap_schema_revision(
        lambda: (calls.append("schema"), "rev-2")[1],
        database_reachable=lambda: False,
        service_registered_probe=lambda: False,
        start_postgres=lambda: calls.append("start"),
        state=state,
    )

    assert wrapped() == "rev-2"
    assert calls == ["start", "schema"]
    assert state.started_by_us is True


def test_registered_but_stopped_service_allows_scoped_postgres_start() -> None:
    """Install-over-existing preserves SCM registration for D3 routing, but
    PREINSTALL has already proved the service STOPPED. That state has no
    service-owned postgres process and is safe for the scoped pg_ctl owner."""

    assert service_allows_scoped_postgres_start(registered=True, stopped=True) is True
    assert service_allows_scoped_postgres_start(registered=False, stopped=None) is True
    assert service_allows_scoped_postgres_start(registered=True, stopped=False) is False
    assert service_allows_scoped_postgres_start(registered=None, stopped=None) is False


def test_real_scoped_start_probe_accepts_registered_but_stopped_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import civiccast.native.upgrade.service_control as service_control

    monkeypatch.setattr(pg_lifecycle, "real_service_registered_probe", lambda: None)
    monkeypatch.setattr(service_control, "_real_service_stopped_probe", lambda: True)

    assert pg_lifecycle.real_scoped_postgres_start_allowed() is True


def test_real_scoped_start_probe_rejects_registered_running_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import civiccast.native.upgrade.service_control as service_control

    monkeypatch.setattr(pg_lifecycle, "real_service_registered_probe", lambda: None)
    monkeypatch.setattr(service_control, "_real_service_stopped_probe", lambda: False)

    assert pg_lifecycle.real_scoped_postgres_start_allowed() is False


def test_health_gate_hands_scoped_postgres_back_to_supervisor_first() -> None:
    calls: list[str] = []
    state = PgLifecycleState(checked=True, started_by_us=True)
    wrapped = wrap_health_gate(
        lambda: (calls.append("health"), True)[1],
        stop_postgres=lambda: calls.append("stop_pg"),
        state=state,
    )

    assert wrapped() is True
    assert calls == ["stop_pg", "health"]
    assert state.started_by_us is False


def test_restore_restarts_scoped_postgres_after_failed_service_handoff() -> None:
    calls: list[str] = []
    state = PgLifecycleState(checked=True, started_by_us=False)
    wrapped = wrap_restore_backup(
        lambda _backup: calls.append("restore"),
        database_reachable=lambda: False,
        scoped_start_allowed=lambda: True,
        start_postgres=lambda: calls.append("start_pg"),
        state=state,
    )

    wrapped(object())  # type: ignore[arg-type]
    assert calls == ["start_pg", "restore"]
    assert state.started_by_us is True


def test_unreachable_service_absent_stop_called_even_when_work_raises() -> None:
    """The finally-block contract __main__.py implements: work() (standing in
    for run_upgrade) raises AFTER schema_revision started postgres, and the
    stop must still fire because state.started_by_us is True.

    <batch-fix-list item 13> Kept as the unit-level statement of the contract,
    but it is NOT the proof: the ``try/finally`` below is written INSIDE this
    test, so it demonstrates that a try/finally works, not that
    ``__main__.main`` has one. The real proof is
    ``test_the_production_finally_stops_a_postgres_this_process_started``
    below, which drives ``upgrade_main.main`` itself.
    """

    calls, state = _harness()

    wrapped = wrap_schema_revision(
        lambda: (calls.append("schema"), "rev-3")[1],
        database_reachable=lambda: False,
        service_registered_probe=lambda: False,
        start_postgres=lambda: calls.append("start"),
        state=state,
    )

    def work() -> None:
        wrapped()
        raise RuntimeError("engine blew up after schema_revision")

    def stop_if_started() -> None:
        if state.started_by_us:
            calls.append("stop")

    with pytest.raises(RuntimeError, match="engine blew up"):
        try:
            work()
        finally:
            stop_if_started()

    assert calls == ["start", "schema", "stop"]


def test_the_production_finally_stops_a_postgres_this_process_started(
    tmp_path, monkeypatch
) -> None:
    """<batch-fix-list item 13> Drive ``upgrade_main.main``, not a hand-written
    ``try/finally``.

    ``attach_pg_lifecycle`` returns a stop callable the CLI ``MUST`` run in a
    finally, so that a postgres THIS process started is never left running for
    D4 provisioning to trip over. Nothing asserted that ``main`` actually has
    that finally: the existing test wrote its own. This one deletes the
    production ``try/finally``'s alibi by making ``run_upgrade`` raise and
    asserting the stop still fired -- through the real entry point NSIS
    invokes.
    """
    import civiccast.native.upgrade.__main__ as upgrade_main

    calls: list[str] = []

    def _fake_attach(seams, context):  # type: ignore[no-untyped-def]
        calls.append("attach")
        return seams, lambda: calls.append("stop_postgres")

    def _boom(*args: object, **kwargs: object) -> None:
        calls.append("run_upgrade")
        raise RuntimeError("engine blew up inside run_upgrade")

    monkeypatch.setattr(upgrade_main, "installed_product_probe", lambda: True)
    monkeypatch.setattr(upgrade_main, "attach_pg_lifecycle", _fake_attach)
    monkeypatch.setattr(upgrade_main, "run_upgrade", _boom)
    monkeypatch.setattr(
        upgrade_main,
        "_resolve_pg_client_commands",
        lambda context: dict.fromkeys(upgrade_main._PG_CLIENT_EXECUTABLES, "pg.exe"),
    )

    argv = [
        "--old-version",
        "1.0.0-rc15",
        "--new-version",
        "1.0.0-rc16",
        "--install-root",
        str(tmp_path / "install"),
        "--state-root",
        str(tmp_path / "state"),
        "--database-url",
        "postgresql://u:p@127.0.0.1:5432/civiccast",
        "--owner-run-id",
        "run-1",
        "--payload-source",
        str(tmp_path / "payload"),
    ]

    # An uncaught engine fault is exit 40 by the CLI's documented contract.
    assert upgrade_main.main(argv) == 40
    assert calls == ["attach", "run_upgrade", "stop_postgres"], (
        "the production finally in __main__.main must run the stop callable even when "
        f"run_upgrade raises; observed {calls}"
    )


@pytest.mark.parametrize("registered", [True, None])
def test_unreachable_service_present_or_ambiguous_fails_closed(registered) -> None:
    calls, state = _harness()

    wrapped = wrap_schema_revision(
        lambda: calls.append("schema"),
        database_reachable=lambda: False,
        service_registered_probe=lambda: registered,
        start_postgres=lambda: calls.append("start"),
        state=state,
    )

    with pytest.raises(PostgresLifecycleError) as excinfo:
        wrapped()

    assert str(excinfo.value) == FAIL_CLOSED_DETAIL
    assert "service is not confirmed stopped" in str(excinfo.value)
    assert calls == []  # neither start nor schema_revision (inner) ran
    assert state.started_by_us is False


def test_start_failure_is_loud_and_no_engine_work_attempted() -> None:
    calls, state = _harness()

    def failing_start() -> None:
        raise PostgresLifecycleError("pg_ctl start failed (exit 1): boom")

    wrapped = wrap_schema_revision(
        lambda: calls.append("schema"),
        database_reachable=lambda: False,
        service_registered_probe=lambda: False,
        start_postgres=failing_start,
        state=state,
    )

    with pytest.raises(PostgresLifecycleError, match="pg_ctl start failed"):
        wrapped()

    assert calls == []  # schema_revision (the engine's first real DB work) never ran
    assert state.started_by_us is False  # nothing to stop


def test_checked_once_guards_the_second_schema_revision_call() -> None:
    """schema_revision is called TWICE in a real run (pre- and
    post-migration revision). The reachability/start decision must only
    happen once."""

    calls, state = _harness()

    wrapped = wrap_schema_revision(
        lambda: (calls.append("schema"), "rev")[1],
        database_reachable=lambda: (calls.append("reachable"), False)[1],
        service_registered_probe=lambda: (calls.append("service_probe"), False)[1],
        start_postgres=lambda: calls.append("start"),
        state=state,
    )

    wrapped()
    wrapped()

    assert calls == ["reachable", "service_probe", "start", "schema", "schema"]


def test_database_missing_error_from_reachable_check_propagates_immediately() -> None:
    """BLOCKER #52: when ``database_reachable`` raises DatabaseMissingError
    (postgres is up, but the database itself does not exist), that must
    propagate straight out -- never treated as ordinary unreachability, never
    retried, and neither ``service_registered_probe`` nor ``start_postgres``
    nor the real ``schema_revision`` may run. The reachable/connect seam is
    called exactly once."""

    calls, state = _harness()

    def _raise_missing() -> bool:
        calls.append("reachable")
        raise DatabaseMissingError("database 'civiccast' does not exist")

    wrapped = wrap_schema_revision(
        lambda: calls.append("schema"),
        database_reachable=_raise_missing,
        service_registered_probe=lambda: calls.append("service_probe"),
        start_postgres=lambda: calls.append("start"),
        state=state,
    )

    with pytest.raises(DatabaseMissingError, match="does not exist"):
        wrapped()

    assert calls == ["reachable"]  # called exactly once; nothing else ran
    assert state.started_by_us is False


# ---------------------------------------------------------------------------
# derive_pg_lifecycle_paths -- reuses D4 provisioning's path convention
# ---------------------------------------------------------------------------


@pytest.mark.windows_only
@_WINDOWS_PATH_TEST
def test_derive_paths_reuses_provisioning_convention(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("PROGRAMDATA", raising=False)
    install_root = str(tmp_path / "Program Files" / "CivicCast (Native)")
    context = UpgradeContext(
        install_root=install_root,
        state_root=str(tmp_path / "state"),
        database_url="postgresql://civiccast:secret@10.0.0.5:6543/civiccast",
        owner_run_id="run-1",
    )

    paths = derive_pg_lifecycle_paths(context)

    assert paths.data_dir == r"C:\ProgramData\CivicCast\data\pgdata"
    assert paths.pg_ctl_path == (
        f"{install_root}\\packs\\native-server-binaries\\payload\\bin\\pg_ctl.exe"
    )
    assert paths.host == "10.0.0.5"
    assert paths.port == 6543


def test_derive_paths_falls_back_on_unparsable_url(tmp_path) -> None:
    context = UpgradeContext(
        install_root=str(tmp_path / "install"),
        state_root=str(tmp_path / "state"),
        database_url="",
        owner_run_id="run-1",
    )

    paths = derive_pg_lifecycle_paths(context)

    assert paths.host == "127.0.0.1"
    assert paths.port == 5432


# ---------------------------------------------------------------------------
# real_* primitives -- message/argv shape via monkeypatched subprocess/SQLAlchemy
# ---------------------------------------------------------------------------


class _FakeCompleted:
    def __init__(self, returncode: int, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


@pytest.fixture
def acl_normalizer(monkeypatch):
    """Row-4b: ``real_start_postgres`` normalizes the data dir's DACL before
    it runs pg_ctl (see :mod:`civiccast.native.pgdata_acl`). These tests use
    non-existent placeholder paths, so the real normalizer would fail-loud on
    them; this records the call instead. Returns the recorded data_dirs."""

    seen: list[str] = []
    monkeypatch.setattr(
        pg_lifecycle, "normalize_pgdata_acl", lambda data_dir: seen.append(data_dir)
    )
    # The binary-tree grant (rc.6 -> rc.7 Dell fix) runs from the same place
    # and would fail-loud on these placeholder paths too.
    monkeypatch.setattr(pg_lifecycle, "normalize_postgres_bin_acl", lambda pg_ctl_path: None)
    return seen


def test_real_start_postgres_normalizes_the_pgdata_acl_before_pg_ctl(monkeypatch) -> None:
    """The row-4b fix's wiring: the DACL is normalized FIRST, otherwise
    ``pg_ctl start`` dies on the LocalSystem-created WAL segments with
    ``Permission denied`` (Sandbox run 21)."""

    order: list[str] = []
    monkeypatch.setattr(
        pg_lifecycle, "normalize_pgdata_acl", lambda data_dir: order.append(f"acl:{data_dir}")
    )
    monkeypatch.setattr(
        pg_lifecycle,
        "normalize_postgres_bin_acl",
        lambda pg_ctl_path: order.append(f"bin-acl:{pg_ctl_path}"),
    )
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: (order.append("pg_ctl"), _FakeCompleted(0))[1]
    )
    paths = pg_lifecycle.PgLifecyclePaths(
        pg_ctl_path=r"C:\pg\bin\pg_ctl.exe", data_dir=r"C:\data\pg", host="127.0.0.1", port=5432
    )

    real_start_postgres(paths)

    # rc.6 -> rc.7 Dell fix: the installing account must be granted on the
    # PostgreSQL binary tree too, or the restricted-token postmaster cannot
    # load postgres.exe and pg_ctl prints the bare "Access is denied."
    assert order == [r"acl:C:\data\pg", r"bin-acl:C:\pg\bin\pg_ctl.exe", "pg_ctl"]


def test_real_start_postgres_fails_loud_and_skips_pg_ctl_when_the_acl_cannot_be_fixed(
    monkeypatch,
) -> None:
    """Fail-loud, not best-effort: an un-normalizable data directory must
    stop the engine with a message naming the ACL step, and must NOT go on to
    attempt a start that would either fail opaquely or run against a cluster
    still readable by every local account."""

    from civiccast.native.pgdata_acl import PgDataAclError

    def _boom(data_dir: str) -> None:
        raise PgDataAclError("pgdata-acl-normalize: access denied")

    def _pg_ctl_must_not_run(*a, **k):  # pragma: no cover - asserted not to run
        raise AssertionError("pg_ctl must not be attempted when ACL normalization failed")

    monkeypatch.setattr(pg_lifecycle, "normalize_pgdata_acl", _boom)
    monkeypatch.setattr(subprocess, "run", _pg_ctl_must_not_run)
    paths = pg_lifecycle.PgLifecyclePaths(
        pg_ctl_path=r"C:\pg\bin\pg_ctl.exe", data_dir=r"C:\data\pg", host="127.0.0.1", port=5432
    )

    with pytest.raises(PostgresLifecycleError) as excinfo:
        real_start_postgres(paths)

    message = str(excinfo.value)
    assert "pg_ctl start was not attempted" in message
    assert "pgdata-acl-normalize" in message
    assert repr(paths.data_dir) in message


def test_real_start_postgres_fails_loud_and_skips_pg_ctl_when_the_bin_acl_cannot_be_fixed(
    monkeypatch, acl_normalizer
) -> None:
    """rc.6 -> rc.7 Dell fix: a binary tree the installing account cannot be
    granted on must stop the engine BEFORE pg_ctl, with a message naming the
    step -- not the bare "Access is denied." the postmaster would produce."""

    from civiccast.native.pgdata_acl import PgDataAclError

    def _boom(pg_ctl_path: str) -> None:
        raise PgDataAclError("postgres-bin-acl: access denied")

    def _pg_ctl_must_not_run(*a, **k):  # pragma: no cover - asserted not to run
        raise AssertionError("pg_ctl must not be attempted when the bin ACL failed")

    monkeypatch.setattr(pg_lifecycle, "normalize_postgres_bin_acl", _boom)
    monkeypatch.setattr(subprocess, "run", _pg_ctl_must_not_run)
    paths = pg_lifecycle.PgLifecyclePaths(
        pg_ctl_path=r"C:\pg\bin\pg_ctl.exe", data_dir=r"C:\data\pg", host="127.0.0.1", port=5432
    )

    with pytest.raises(PostgresLifecycleError) as excinfo:
        real_start_postgres(paths)

    message = str(excinfo.value)
    assert "pg_ctl start was not attempted" in message
    assert "postgres-bin-acl" in message
    assert repr(paths.pg_ctl_path) in message


def test_real_start_postgres_success_does_not_raise(monkeypatch, acl_normalizer) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _FakeCompleted(0))
    paths = pg_lifecycle.PgLifecyclePaths(
        pg_ctl_path=r"C:\pg\bin\pg_ctl.exe", data_dir=r"C:\data\pg", host="127.0.0.1", port=5432
    )
    real_start_postgres(paths)  # must not raise
    assert acl_normalizer == [r"C:\data\pg"]


def test_real_start_postgres_failure_is_loud_and_omits_database_url(
    monkeypatch, acl_normalizer
) -> None:
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: _FakeCompleted(1, stderr="FATAL: could not bind")
    )
    paths = pg_lifecycle.PgLifecyclePaths(
        pg_ctl_path=r"C:\pg\bin\pg_ctl.exe", data_dir=r"C:\data\pg", host="127.0.0.1", port=5432
    )
    with pytest.raises(PostgresLifecycleError) as excinfo:
        real_start_postgres(paths)
    message = str(excinfo.value)
    assert "pg_ctl start failed" in message
    assert "exit 1" in message
    assert "FATAL: could not bind" in message
    assert repr(paths.data_dir) in message
    assert "secret" not in message  # never a password/database_url fragment


def test_real_stop_postgres_failure_is_loud(monkeypatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: _FakeCompleted(1, stderr="pg_ctl: server does not shut down"),
    )
    paths = pg_lifecycle.PgLifecyclePaths(
        pg_ctl_path=r"C:\pg\bin\pg_ctl.exe", data_dir=r"C:\data\pg", host="127.0.0.1", port=5432
    )
    with pytest.raises(PostgresLifecycleError, match="pg_ctl stop failed"):
        real_stop_postgres(paths)


def test_real_database_reachable_false_on_connection_error() -> None:
    # A real SQLAlchemy engine against an unroutable/refused endpoint: fails
    # closed (never raises). BLOCKER #52: this used to take ~130s wall-clock
    # on this box (psycopg v3's own connect_timeout=5 was measured NOT to
    # bound a refused connect reliably here -- see
    # pg_lifecycle._REACHABLE_HARD_WAIT_CEILING_SECONDS's docstring for the
    # full disclosed finding) -- real_database_reachable now enforces its
    # OWN hard wall-clock ceiling independent of the driver, so this test
    # itself is proof the call returns in single-digit seconds, not minutes.
    import time

    started = time.monotonic()
    assert real_database_reachable("postgresql://u:p@127.0.0.1:1/nonexistent-db") is False
    elapsed = time.monotonic() - started
    assert elapsed < pg_lifecycle._REACHABLE_HARD_WAIT_CEILING_SECONDS + 5.0, (
        f"real_database_reachable must be bounded by its own hard ceiling "
        f"regardless of driver behavior; took {elapsed:.1f}s"
    )


def test_real_database_reachable_bounded_even_when_connect_hangs_past_its_own_timeout(
    monkeypatch,
) -> None:
    """BLOCKER #52 audit finding: connect_timeout alone was measured NOT to
    reliably bound a stalled connect on this platform (see
    test_real_database_reachable_false_on_connection_error above). This test
    proves the INDEPENDENT enforcement mechanism directly: even a connect
    that never returns at all is still bounded by
    ``_REACHABLE_HARD_WAIT_CEILING_SECONDS`` -- shrunk here so the test
    itself stays fast."""

    import threading
    import time

    import sqlalchemy

    monkeypatch.setattr(pg_lifecycle, "_REACHABLE_HARD_WAIT_CEILING_SECONDS", 0.3)

    release = threading.Event()

    class _HangingConnection:
        def __enter__(self):  # type: ignore[no-untyped-def]
            # Blocks far longer than the shrunk ceiling -- simulates a
            # connect that never respects connect_timeout at all.
            release.wait(timeout=5.0)
            return self

        def __exit__(self, *args):  # type: ignore[no-untyped-def]
            return False

        def exec_driver_sql(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return None

    class _HangingEngine:
        def connect(self):  # type: ignore[no-untyped-def]
            return _HangingConnection()

        def dispose(self):  # type: ignore[no-untyped-def]
            pass

    monkeypatch.setattr(sqlalchemy, "create_engine", lambda url, **kwargs: _HangingEngine())

    started = time.monotonic()
    result = real_database_reachable("postgresql://u:p@127.0.0.1:1/db")
    elapsed = time.monotonic() - started

    release.set()  # let the abandoned worker thread unblock so it can exit
    assert result is False
    assert elapsed < 2.0, f"expected the hard ceiling (0.3s) to bound the wait, took {elapsed:.1f}s"


def test_real_database_reachable_normalizes_bare_postgresql_scheme(monkeypatch) -> None:
    """beta BLOCKER #51 regression: the call boundary this seam controls is
    create_engine -- it must receive the NORMALIZED url (+psycopg), not the
    bare ``postgresql://`` scheme the installer persists (which SQLAlchemy
    maps to the uninstalled psycopg2 dialect). Monkeypatches
    ``sqlalchemy.create_engine`` directly (the call boundary, not internals)
    so this proves the fix without a real, slow/flaky network connection
    attempt -- see test_real_database_reachable_false_on_connection_error
    above, which is now bounded by the module's own hard wait ceiling
    (BLOCKER #52) rather than a slow real connect."""

    import sqlalchemy

    captured: dict[str, str] = {}

    class _FakeConnection:
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *args):  # type: ignore[no-untyped-def]
            return False

        def exec_driver_sql(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            return None

    class _FakeEngine:
        def connect(self):  # type: ignore[no-untyped-def]
            return _FakeConnection()

        def dispose(self):  # type: ignore[no-untyped-def]
            pass

    def _fake_create_engine(url, **kwargs):  # type: ignore[no-untyped-def]
        captured["url"] = url
        return _FakeEngine()

    monkeypatch.setattr(sqlalchemy, "create_engine", _fake_create_engine)

    assert real_database_reachable("postgresql://u:secret@127.0.0.1:1/db") is True
    assert captured["url"].startswith("postgresql+psycopg://")
    assert "secret" in captured["url"]  # password must survive, not be corrupted


# ---------------------------------------------------------------------------
# BLOCKER #52: missing-database detection, grounded on the real psycopg
# exception class/SQLSTATE (never a string match on the message).
# ---------------------------------------------------------------------------


def _invalid_catalog_name_error(message: str = 'database "civiccast" does not exist'):  # type: ignore[no-untyped-def]
    """Build the REAL psycopg exception BLOCKER #52 grounds on -- not a
    hand-rolled stand-in -- so the detection tests exercise the actual
    driver class/sqlstate this codebase measured
    (psycopg 3.3.4: InvalidCatalogName.sqlstate == '3D000')."""

    import psycopg.errors as psycopg_errors

    return psycopg_errors.InvalidCatalogName(message)


def test_is_missing_database_error_true_for_real_psycopg_invalid_catalog_name() -> None:
    from civiccast.native.upgrade.pg_lifecycle import _is_missing_database_error

    exc = _invalid_catalog_name_error()
    assert exc.sqlstate == "3D000"  # ground truth this detection relies on
    assert _is_missing_database_error(exc) is True


def test_is_missing_database_error_true_through_sqlalchemy_dbapierror_orig_wrapper() -> None:
    """SQLAlchemy wraps the raw driver exception in ``.orig`` on its own
    ``OperationalError`` -- this is the shape ``engine.connect()`` actually
    raises in production, not the bare psycopg exception."""

    from sqlalchemy.exc import OperationalError

    from civiccast.native.upgrade.pg_lifecycle import _is_missing_database_error

    orig = _invalid_catalog_name_error()
    wrapped = OperationalError("connect", {}, orig)
    assert _is_missing_database_error(wrapped) is True


def test_is_missing_database_error_false_for_unrelated_errors() -> None:
    from sqlalchemy.exc import OperationalError

    from civiccast.native.upgrade.pg_lifecycle import _is_missing_database_error

    assert _is_missing_database_error(RuntimeError("connection refused")) is False
    assert (
        _is_missing_database_error(OperationalError("connect", {}, RuntimeError("boom"))) is False
    )


def test_real_database_reachable_raises_database_missing_error_immediately(monkeypatch) -> None:
    """BLOCKER #52: postgres reachable, but the database itself missing ->
    DatabaseMissingError, not swallowed as ordinary unreachability."""

    import sqlalchemy
    from sqlalchemy.exc import OperationalError

    class _FailingConnection:
        def __enter__(self):  # type: ignore[no-untyped-def]
            raise OperationalError("connect", {}, _invalid_catalog_name_error())

        def __exit__(self, *args):  # type: ignore[no-untyped-def]
            return False

    class _FakeEngine:
        def connect(self):  # type: ignore[no-untyped-def]
            return _FailingConnection()

        def dispose(self):  # type: ignore[no-untyped-def]
            pass

    monkeypatch.setattr(sqlalchemy, "create_engine", lambda url, **kwargs: _FakeEngine())

    with pytest.raises(DatabaseMissingError, match="does not exist"):
        real_database_reachable("postgresql://u:p@127.0.0.1:1/civiccast")


def test_derive_paths_calls_make_url_with_normalized_scheme(tmp_path, monkeypatch) -> None:
    """beta BLOCKER #51 regression: ``make_url`` (the other consumer in this
    module) must also see the normalized scheme. Spies on the REAL
    ``sqlalchemy.engine.make_url`` (delegates, does not fake parsing) so the
    host/port extraction this function performs is still exercised for
    real."""

    import sqlalchemy.engine as sa_engine

    real_make_url = sa_engine.make_url
    captured: dict[str, str] = {}

    def _spy_make_url(url):  # type: ignore[no-untyped-def]
        captured["url"] = url
        return real_make_url(url)

    monkeypatch.setattr(sa_engine, "make_url", _spy_make_url)

    context = UpgradeContext(
        install_root=str(tmp_path / "install"),
        state_root=str(tmp_path / "state"),
        database_url="postgresql://civiccast:secret@10.0.0.5:6543/civiccast",
        owner_run_id="run-1",
    )

    paths = derive_pg_lifecycle_paths(context)

    assert captured["url"].startswith("postgresql+psycopg://")
    # And the parse still produced the right host/port (the normalization
    # only rewrites the driver name, never the rest of the URL).
    assert paths.host == "10.0.0.5"
    assert paths.port == 6543


# ---------------------------------------------------------------------------
# attach_pg_lifecycle -- wiring: inert construction, refusal-safe
# ---------------------------------------------------------------------------


def test_attach_is_inert_construction(tmp_path) -> None:
    """Deriving paths and wrapping the seam must never itself touch
    subprocess/SQL/SCM -- only invoking the wrapped schema_revision does."""

    from civiccast.native.upgrade.models import UpgradeSeams

    context = UpgradeContext(
        install_root=str(tmp_path / "install"),
        state_root=str(tmp_path / "state"),
        database_url="postgresql://u@localhost/db",
        owner_run_id="run-1",
    )
    seams = UpgradeSeams(
        acquire_interlock=lambda: None,
        release_interlock=lambda: None,
        drain_and_verify_quiescence=lambda: True,
        backup=lambda backup_dir: (_ for _ in ()).throw(AssertionError("unused")),
        restore_backup=lambda backup: None,
        lay_tree=lambda new_version: "unused",
        flip_junction=lambda target: None,
        read_junction=lambda: None,
        migrate=lambda: None,
        health_gate=lambda: True,
        schema_revision=lambda: "orig-rev",
        stop_service=lambda: None,
    )

    new_seams, stop_if_started = attach_pg_lifecycle(seams, context)

    assert new_seams.schema_revision is not seams.schema_revision
    assert new_seams.drain_and_verify_quiescence is not seams.drain_and_verify_quiescence
    assert new_seams.backup is not seams.backup
    assert new_seams.migrate is not seams.migrate
    assert new_seams.health_gate is not seams.health_gate
    assert new_seams.restore_backup is not seams.restore_backup
    assert callable(stop_if_started)
    # Never started (schema_revision was never invoked) -> a no-op stop.
    stop_if_started()


def test_attach_never_raises_on_unparsable_context(tmp_path) -> None:
    from civiccast.native.upgrade.models import UpgradeSeams

    context = UpgradeContext(
        install_root=str(tmp_path / "install"),
        state_root=str(tmp_path / "state"),
        database_url="",
        owner_run_id="run-1",
    )
    seams = UpgradeSeams(
        acquire_interlock=lambda: None,
        release_interlock=lambda: None,
        drain_and_verify_quiescence=lambda: True,
        backup=lambda backup_dir: (_ for _ in ()).throw(AssertionError("unused")),
        restore_backup=lambda backup: None,
        lay_tree=lambda new_version: "unused",
        flip_junction=lambda target: None,
        read_junction=lambda: None,
        migrate=lambda: None,
        health_gate=lambda: True,
        schema_revision=lambda: None,
        stop_service=lambda: None,
    )

    # Must not raise -- construction is inert regardless of a malformed URL.
    attach_pg_lifecycle(seams, context)
