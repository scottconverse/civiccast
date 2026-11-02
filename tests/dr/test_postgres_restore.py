# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Postgres restore drill: same testcontainers gate as tests/dr/test_postgres_backup.py.

End-to-end in a single container: migrate -> seed two tables -> pg_dump (in-
container) -> create a fresh database (in-container) -> pg_restore (in-
container) -> verify alembic revision equality, per-table row/checksum
equality (the same comparison the SQLite restore drill uses), installed-
extension equality, and sequence-name equality.

Two negative controls prove the verification is real, not decorative:
mutating a restored row must be DETECTED by the table-snapshot comparison,
and a corrupted/truncated dump artifact must make ``run_postgres_restore``
raise rather than silently produce a partial restore.

Boundary: this suite runs only when a container engine (Docker or podman) is
reachable. CI (Linux + Docker) always exercises the Postgres path; locally it
runs wherever an engine is up and skips otherwise -- see
``.agent-runs/native-windows/ws2-postgres-restore/evidence/README.md`` for
what was and was not executed on which machine.

The live-write snapshot case also supports CIVICCAST_TEST_POSTGRES_BIN pointing
to native Windows tools; it creates/stops its own loopback-only temporary cluster.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

import civiccast.dr.report as report_module
from civiccast.db import bind_engine, reset_engine
from civiccast.dr.backup import (
    _assert_backup_quiescent,
    create_fresh_postgres_database,
    run_full_backup,
    run_postgres_backup,
    run_postgres_globals_backup,
    run_postgres_restore,
    snapshot_tables,
)
from civiccast.dr.models import BackupManifest, TableSnapshot
from civiccast.dr.restore_drill import (
    _canonicalize_defs,
    _def_lists_mismatch,
    _pg_constraint_defs,
    _pg_role_attributes,
    _pg_sequence_states,
    _replay_globals_sql,
    _role_captured_in_globals,
    _table_results,
    run_postgres_cold_standby_drill,
    run_postgres_restore_drill,
)
from civiccast.egress.models import CanonicalProfile
from civiccast.installer.storage import _run_migrations
from tests.support.docker_engine import container_cli, docker_engine_available

try:
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

    _TESTCONTAINERS_OK = True
except ImportError:
    _TESTCONTAINERS_OK = False
    PostgresContainer = None  # type: ignore[misc,assignment]


def _skip_if_no_postgres() -> None:
    if not _TESTCONTAINERS_OK or not docker_engine_available():
        if os.environ.get("CIVICCAST_RUN_POSTGRES_TESTS"):
            pytest.fail("Postgres DR-drill test required by env but Docker unavailable")
        pytest.skip(
            "Docker unavailable; the Postgres restore path is not exercised in this sandbox"
        )


@pytest.fixture
def postgres_container(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    """Yields (host-side connection url, container id)."""
    # Explicit native override proves the same snapshot boundary on Windows,
    # using an owned temporary cluster rather than the installed station DB.
    native_bin = os.environ.get("CIVICCAST_TEST_POSTGRES_BIN")
    if native_bin:
        binaries = Path(native_bin).resolve(strict=True)
        cluster = tmp_path_factory.mktemp("native-postgres")
        data = cluster / "data"
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]

        def run(binary: str, *arguments: str) -> None:
            subprocess.run(
                [str(binaries / f"{binary}.exe"), *arguments],
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

        run("initdb", "-D", str(data), "-U", "test", "--auth=trust", "--locale=C", "-E", "UTF8")
        try:
            run(
                "pg_ctl",
                "-D",
                str(data),
                "-l",
                str(cluster / "postgres.log"),
                "-o",
                f"-h 127.0.0.1 -p {port}",
                "-w",
                "start",
            )
            run(
                "psql",
                "-h",
                "127.0.0.1",
                "-p",
                str(port),
                "-U",
                "test",
                "-d",
                "postgres",
                "-c",
                "CREATE DATABASE test",
            )
            yield f"postgresql+psycopg://test@127.0.0.1:{port}/test", f"native:{binaries}"
        finally:
            if (data / "postmaster.pid").exists():
                run("pg_ctl", "-D", str(data), "-m", "fast", "-w", "stop")
        return
    _skip_if_no_postgres()
    assert PostgresContainer is not None
    container = PostgresContainer("postgres:17", driver="psycopg")
    container.start()
    try:
        yield container.get_connection_url(), container.get_wrapped_container().id
    finally:
        container.stop()


def test_native_postgres_fixture_stops_its_partial_start(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    binaries = tmp_path / "postgres-bin"
    binaries.mkdir()
    monkeypatch.setenv("CIVICCAST_TEST_POSTGRES_BIN", str(binaries))
    calls: list[list[str]] = []

    def partial_start(argv, **kwargs):
        calls.append(argv)
        if argv[-1] == "start":
            data = Path(argv[argv.index("-D") + 1])
            data.mkdir()
            (data / "postmaster.pid").write_text("disposable startup marker")
            raise subprocess.TimeoutExpired(argv, 60)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", partial_start)
    with pytest.raises(subprocess.TimeoutExpired):
        next(postgres_container.__wrapped__(tmp_path_factory))
    assert calls[-1][-4:] == ["-m", "fast", "-w", "stop"]
    assert calls[-1][1:3] == calls[-2][1:3]


@pytest.fixture
def postgres_standby_container() -> Iterator[tuple[str, str]]:
    """A SECOND, independently-fresh Postgres cluster -- has NEVER seen the
    ``postgres_container`` fixture's roles. Same shape as ``postgres_container``;
    a distinct fixture (not a parametrization) because the cold-standby
    drill's tests need BOTH containers alive at once.
    """
    _skip_if_no_postgres()
    assert PostgresContainer is not None
    container = PostgresContainer("postgres:17", driver="psycopg")
    container.start()
    try:
        yield container.get_connection_url(), container.get_wrapped_container().id
    finally:
        container.stop()


def _exec_prefix(container_id: str, binary: str) -> list[str]:
    """``<docker|podman> exec -i -e PGPASSWORD=test <container> <binary>``.

    ``-e PGPASSWORD=test`` is a ``docker exec`` flag, not a local env var --
    the subprocess's own ``env=`` only reaches the local ``docker``/``podman``
    client, never the process spawned inside the container.
    """

    if container_id.startswith("native:"):
        return [str(Path(container_id.removeprefix("native:")) / f"{binary}.exe")]
    return [
        container_cli() or "docker",
        "exec",
        "-i",
        "-e",
        "PGPASSWORD=test",
        container_id,
        binary,
    ]


def test_native_postgres_reporting_window_keeps_open_airings_provisional(
    postgres_container: tuple[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise changed report SQL on the existing isolated PostgreSQL fixture."""
    from contextlib import contextmanager

    from sqlalchemy.orm import Session

    from civiccast.metadata.store import CustomFieldStore
    from civiccast.reporting.service import ReportingService
    from civiccast.reporting.store import ReportingStore
    from tests.reporting.test_service import (
        _ts,
        test_open_airings_clip_to_window_and_now_without_mutating_ledger,
    )

    url, _ = postgres_container
    _run_migrations(url)
    engine = create_engine(url, future=True)

    @contextmanager
    def factory():
        with Session(bind=engine) as session:
            yield session

    try:
        env = ReportingService(factory), ReportingStore(factory), CustomFieldStore(factory)
        # Reuse the existing complete assertions: aggregates, CSV view,
        # repeat counts, future refusal and unchanged zero-duration open ledger.
        # The 09:15 successor caps provisional airtime to 09:00–09:15.
        test_open_airings_clip_to_window_and_now_without_mutating_ledger(
            env, monkeypatch, _ts(11), 900
        )
    finally:
        engine.dispose()


def _seed_two_tables(engine) -> None:
    """Seed egress_configs (the established trick) + assets (a second table).

    The config gets a companion egress_sinks row because the drill's
    app-store read-through validates rows into ``EgressConfig``, which
    requires at least one sink â€” a sinkless config is app-level invalid
    and would (correctly) fail the read-through. CI's first real
    execution of this suite caught exactly that (run 29557846920).
    """

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO civiccast.egress_configs "
                "(channel_id, enabled, auto_start, fill_policy, slate_message, "
                "loudness_target_lufs, loudness_tolerance_lufs, "
                "canonical_profile_json, created_at, updated_at) "
                "VALUES ('gov', true, false, 'slate', 'x', -23.0, 1.0, "
                ":profile, now(), now())"
            ),
            {"profile": CanonicalProfile().model_dump_json()},
        )
        conn.execute(
            text(
                "INSERT INTO civiccast.egress_sinks "
                "(channel_id, label, position, kind, uri, latency_ms, "
                "extra_output_args_json, loudness_regime, eas_tone_strip_enabled) "
                "VALUES ('gov', 'drill-hls', 0, 'hls', "
                "'/var/lib/civiccast/hls/gov', 2000, '[]', 'inherit', true)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO civiccast.assets (asset_id, title, state, manifest_url) "
                "VALUES ('pg-restore-drill-asset', 'Postgres restore drill asset', "
                "'validated', 'https://cdn.example/pg-restore-drill/master.m3u8')"
            )
        )


def _seed_extra_role_and_grant(engine) -> None:
    """A NON-bootstrap role that owns nothing but holds a grant.

    The fixture's bootstrap role (``test``) pre-exists on every fresh
    container (Postgres itself creates it at ``initdb`` time), so it can
    never be "missing" after a globals replay regardless of that replay's
    content -- a role-restorability negative control needs a role that
    genuinely only exists because ``globals.sql`` said so. ``drill_grantee``
    is that role: it owns nothing (so it never blocks a DROP/restore on its
    own) but holds a real table grant, which is enough for
    ``_pg_relevant_roles`` to pick it up as "relevant".
    """

    with engine.begin() as conn:
        conn.execute(text("DROP ROLE IF EXISTS drill_grantee"))
        conn.execute(text("CREATE ROLE drill_grantee LOGIN"))
        conn.execute(text("GRANT SELECT ON civiccast.assets TO drill_grantee"))


def test_postgres_restore_drill_verifies_full_round_trip(
    postgres_container: tuple[str, str], tmp_path: Path
) -> None:
    postgres_url, container_id = postgres_container
    _run_migrations(postgres_url)
    engine = create_engine(postgres_url, future=True)
    bind_engine(engine)
    try:
        _seed_two_tables(engine)
        source_snapshot = {t.name: t for t in snapshot_tables(engine)}
        assert source_snapshot["egress_configs"].row_count == 1
        assert source_snapshot["assets"].row_count == 1

        in_container_url = "postgresql://test:test@localhost:5432/test"
        backup_dir = tmp_path / "backup"
        artifact = run_postgres_backup(
            database_url=in_container_url,
            dest_dir=backup_dir,
            pg_dump_command=_exec_prefix(container_id, "pg_dump"),
        )
        assert artifact.exists()
        assert artifact.stat().st_size > 0

        # This test builds the manifest by hand (it calls the backup helper
        # directly, not run_full_backup), so the globals capture that
        # run_full_backup would normally trigger has to be taken the same
        # way here -- otherwise run_postgres_restore_drill would (correctly)
        # report "backup manifest has no globals_artifact" as an error below.
        globals_artifact = run_postgres_globals_backup(
            database_url=in_container_url,
            dest_dir=backup_dir,
            pg_dumpall_command=_exec_prefix(container_id, "pg_dumpall"),
        )
        assert globals_artifact.exists()

        manifest = BackupManifest(
            backup_id="pg-restore-drill-round-trip",
            created_at=datetime.now(UTC),
            engine="postgres",
            db_artifact=artifact.name,
            tables=snapshot_tables(engine),
            globals_artifact=globals_artifact.name,
        )
        assert manifest.globals_artifact == "globals.sql"

        report = run_postgres_restore_drill(
            backup_dir=backup_dir,
            manifest=manifest,
            source_database_url=in_container_url,
            verification_database_url=postgres_url,
            restore_database_name="civiccast_drill_restore_round_trip",
            pg_restore_command=_exec_prefix(container_id, "pg_restore"),
            psql_command=_exec_prefix(container_id, "psql"),
        )

        assert report.errors == [], report.errors
        assert report.schema_ok
        assert report.ok
        assert len(report.tables) >= 2
        assert all(t.matched for t in report.tables), report.tables
        assert report.app_store_reads.get("assets") == 1
        assert report.app_store_reads.get("egress_configs") == 1

        # Non-vacuous: the equality checks above are only meaningful proof if
        # both lists actually have content. btree_gist ships via migration
        # 0003 (schedule EXCLUDE constraint); several tables have
        # Integer/autoincrement primary keys, which own a sequence on Postgres.
        with engine.connect() as conn:
            extensions = [
                r[0]
                for r in conn.execute(
                    text("SELECT extname FROM pg_extension ORDER BY extname")
                ).fetchall()
            ]
            sequences = [
                r[0]
                for r in conn.execute(
                    text(
                        "SELECT sequence_name FROM information_schema.sequences "
                        "WHERE sequence_schema = 'civiccast' ORDER BY sequence_name"
                    )
                ).fetchall()
            ]
        assert "btree_gist" in extensions
        assert len(sequences) > 0
    finally:
        reset_engine()
        engine.dispose()


def test_postgres_restore_drill_expected_revision_overrides_code_head(
    postgres_container: tuple[str, str], tmp_path: Path
) -> None:
    """Gate A run 33681670855 regression test (D3 root cause, Fix A).

    A PRE-UPGRADE backup drill restores a dump taken from the OLD version's
    database, before the new version's migrations have run. Comparing that
    restored copy's revision against the running CODE's migration head (the
    default, and the ONLY behavior before this fix) is always false the
    moment the running code has shipped any migration since the dump was
    taken -- this is the exact false-negative that made D3 roll back a
    healthy upgrade on kit 7971815 (beta.2 -> beta.3).

    This test stamps the source database's ``alembic_version`` at a
    synthetic PREVIOUS revision after migrating to head (the schema itself
    is unaffected -- only the bookkeeping marker changes -- so seeding real
    rows through the current ORM/table shapes stays valid), then proves:

    * without ``expected_revision`` (the pre-fix default / old behavior),
      the drill's ``schema_ok`` is False and ``report.ok`` is False, even
      though the restore itself is byte-for-byte faithful; and
    * with ``expected_revision=<the source's own stamped revision>``, the
      SAME restored artifact reports ``schema_ok=True`` and ``report.ok``
      True -- proving the fix asks the honest question for a pre-upgrade
      drill (does the restored copy match what was actually dumped) rather
      than the DR-drill question (does it match today's code).
    """
    postgres_url, container_id = postgres_container
    _run_migrations(postgres_url)
    engine = create_engine(postgres_url, future=True)
    bind_engine(engine)
    try:
        _seed_two_tables(engine)

        synthetic_previous_revision = "0001_synthetic_previous_head"
        with engine.begin() as conn:
            real_head = conn.execute(
                text("SELECT version_num FROM civiccast.alembic_version")
            ).scalar_one()
            assert real_head != synthetic_previous_revision
            conn.execute(
                text("UPDATE civiccast.alembic_version SET version_num = :rev"),
                {"rev": synthetic_previous_revision},
            )

        in_container_url = "postgresql://test:test@localhost:5432/test"
        backup_dir = tmp_path / "backup"
        artifact = run_postgres_backup(
            database_url=in_container_url,
            dest_dir=backup_dir,
            pg_dump_command=_exec_prefix(container_id, "pg_dump"),
        )
        globals_artifact = run_postgres_globals_backup(
            database_url=in_container_url,
            dest_dir=backup_dir,
            pg_dumpall_command=_exec_prefix(container_id, "pg_dumpall"),
        )
        manifest = BackupManifest(
            backup_id="pg-restore-drill-expected-revision",
            created_at=datetime.now(UTC),
            engine="postgres",
            db_artifact=artifact.name,
            tables=snapshot_tables(engine),
            globals_artifact=globals_artifact.name,
        )

        # Without expected_revision: the pre-fix / DR-drill default question
        # ("does the restore match today's CODE head") is false, because the
        # dump was stamped at the synthetic previous revision, not the real
        # code head.
        default_report = run_postgres_restore_drill(
            backup_dir=backup_dir,
            manifest=manifest,
            source_database_url=in_container_url,
            verification_database_url=postgres_url,
            restore_database_name="civiccast_drill_restore_default_head",
            pg_restore_command=_exec_prefix(container_id, "pg_restore"),
            psql_command=_exec_prefix(container_id, "psql"),
        )
        assert default_report.db_revision == synthetic_previous_revision
        assert default_report.expected_head == real_head
        assert default_report.schema_ok is False
        assert default_report.ok is False

        # With expected_revision passed explicitly: the honest pre-upgrade
        # question ("does the restore match what was actually dumped") is
        # true for the exact same restored artifact.
        override_report = run_postgres_restore_drill(
            backup_dir=backup_dir,
            manifest=manifest,
            source_database_url=in_container_url,
            verification_database_url=postgres_url,
            restore_database_name="civiccast_drill_restore_override_head",
            pg_restore_command=_exec_prefix(container_id, "pg_restore"),
            psql_command=_exec_prefix(container_id, "psql"),
            expected_revision=synthetic_previous_revision,
        )
        assert override_report.db_revision == synthetic_previous_revision
        assert override_report.expected_head == synthetic_previous_revision
        assert override_report.schema_ok is True
        assert override_report.errors == [], override_report.errors
        assert override_report.ok is True
    finally:
        reset_engine()
        engine.dispose()


def test_restore_detects_row_mutation(postgres_container: tuple[str, str], tmp_path: Path) -> None:
    """Negative control (i): a post-restore row mutation must be DETECTED.

    If the checksum/row-count comparison in
    ``civiccast.dr.restore_drill._table_results`` were stubbed to always
    report ``matched=True``, this test goes red -- it exercises the exact
    comparison function the drill uses, not a reimplementation of it.
    """

    postgres_url, container_id = postgres_container
    _run_migrations(postgres_url)
    engine = create_engine(postgres_url, future=True)
    bind_engine(engine)
    try:
        _seed_two_tables(engine)

        in_container_url = "postgresql://test:test@localhost:5432/test"
        backup_dir = tmp_path / "backup"
        artifact = run_postgres_backup(
            database_url=in_container_url,
            dest_dir=backup_dir,
            pg_dump_command=_exec_prefix(container_id, "pg_dump"),
        )
        manifest = BackupManifest(
            backup_id="pg-restore-drill-mutation-control",
            created_at=datetime.now(UTC),
            engine="postgres",
            db_artifact=artifact.name,
            tables=snapshot_tables(engine),
        )

        restore_db_name = "civiccast_drill_restore_mutation"
        restore_cli_url = create_fresh_postgres_database(
            database_url=in_container_url,
            database_name=restore_db_name,
            psql_command=_exec_prefix(container_id, "psql"),
        )
        run_postgres_restore(
            backup_dir / manifest.db_artifact,
            restore_cli_url,
            pg_restore_command=_exec_prefix(container_id, "pg_restore"),
        )

        restored_url = postgres_url.rsplit("/", 1)[0] + f"/{restore_db_name}"
        restored_engine = create_engine(restored_url, future=True)
        try:
            # Sanity: immediately after restore, before any mutation, the
            # comparison must report a clean match -- otherwise a "detects
            # mutation" assertion below would be meaningless (it might just
            # always report mismatched).
            clean_results = _table_results(manifest.tables, snapshot_tables(restored_engine))
            assert all(t.matched for t in clean_results), clean_results

            with restored_engine.begin() as conn:
                conn.execute(
                    text(
                        "UPDATE civiccast.assets SET title = 'MUTATED AFTER RESTORE' "
                        "WHERE asset_id = 'pg-restore-drill-asset'"
                    )
                )

            mutated_results = _table_results(manifest.tables, snapshot_tables(restored_engine))
            mutated_by_name = {r.name: r for r in mutated_results}
            assert mutated_by_name["assets"].matched is False, (
                "Table-snapshot comparison failed to detect a post-restore row "
                "mutation -- the restore drill's core proof mechanism is broken."
            )
            # The untouched table must still match -- proves the comparison is
            # per-table, not a suite-wide false alarm.
            assert mutated_by_name["egress_configs"].matched is True
        finally:
            restored_engine.dispose()
    finally:
        reset_engine()
        engine.dispose()


def test_restore_rejects_corrupted_artifact(
    postgres_container: tuple[str, str], tmp_path: Path
) -> None:
    """Negative control (ii): a truncated dump artifact must make
    ``run_postgres_restore`` raise, not silently restore a partial database."""

    postgres_url, container_id = postgres_container
    _run_migrations(postgres_url)
    engine = create_engine(postgres_url, future=True)
    bind_engine(engine)
    try:
        _seed_two_tables(engine)

        in_container_url = "postgresql://test:test@localhost:5432/test"
        backup_dir = tmp_path / "backup"
        artifact = run_postgres_backup(
            database_url=in_container_url,
            dest_dir=backup_dir,
            pg_dump_command=_exec_prefix(container_id, "pg_dump"),
        )

        good_bytes = artifact.read_bytes()
        assert len(good_bytes) > 100, "fixture dump is too small to meaningfully truncate"
        corrupted_path = backup_dir / "database-corrupted.pgdump"
        corrupted_path.write_bytes(good_bytes[: len(good_bytes) // 2])

        restore_cli_url = create_fresh_postgres_database(
            database_url=in_container_url,
            database_name="civiccast_drill_restore_corrupt",
            psql_command=_exec_prefix(container_id, "psql"),
        )

        with pytest.raises(RuntimeError, match="pg_restore failed"):
            run_postgres_restore(
                corrupted_path,
                restore_cli_url,
                pg_restore_command=_exec_prefix(container_id, "pg_restore"),
            )
    finally:
        reset_engine()
        engine.dispose()


def test_run_full_drill_postgres_end_to_end(
    postgres_container: tuple[str, str], tmp_path: Path
) -> None:
    """Finding 2: CI must prove the SHIPPING entry point, not just the helpers it's
    built from.

    Every test above calls ``run_postgres_backup``/``run_postgres_restore_drill``
    directly -- real proof that those functions work, but not proof that
    ``civiccast.dr.report.run_full_drill`` (what the CLI and an operator's cron
    job actually call) wires them together correctly end-to-end against a real
    Postgres server. This test drives ``run_full_drill`` itself, container
    execution parameters and all, the same way an operator running a
    containerized station database would.
    """
    postgres_url, container_id = postgres_container
    _run_migrations(postgres_url)
    engine = create_engine(postgres_url, future=True)
    bind_engine(engine)
    try:
        _seed_two_tables(engine)

        report = report_module.run_full_drill(
            database_url=postgres_url,
            backup_dir=tmp_path / "backup",
            work_dir=tmp_path / "work",
            command_database_url="postgresql://test:test@localhost:5432/test",
            pg_dump_command=_exec_prefix(container_id, "pg_dump"),
            pg_dumpall_command=_exec_prefix(container_id, "pg_dumpall"),
            pg_restore_command=_exec_prefix(container_id, "pg_restore"),
            psql_command=_exec_prefix(container_id, "psql"),
        )

        assert report.restore.errors == [], report.restore.errors
        assert report.restore.ok
        assert report.backup.globals_artifact == "globals.sql"
        globals_path = tmp_path / "backup" / "globals.sql"
        assert globals_path.exists()
        assert "test" in globals_path.read_text(encoding="utf-8")
        assert all(t.matched for t in report.restore.tables), report.restore.tables

        # The crash-recovery leg (civiccast.dr.crash_drill) spawns a real local
        # Python subprocess under the real EgressDaemon -- it has no dependency
        # on the Postgres container, so nothing here stops it from running in
        # this test context. report.ok (backup + restore + crash together) is
        # therefore the honest full-report assertion, not a narrowed one.
        assert report.ok, (report.restore.errors, report.crash.results)
    finally:
        reset_engine()
        engine.dispose()


def test_unexpected_extra_table_is_detected(
    postgres_container: tuple[str, str], tmp_path: Path
) -> None:
    """Negative control (iii): an UNEXPECTED extra table in the restored copy must
    be DETECTED, not silently passed.

    Before this fix, ``_table_results`` only ever iterated the manifest's
    expected tables -- a for-loop that, by construction, can never visit a
    table the manifest doesn't already know about. A restored database that
    somehow gained a table the backup never had (a stale drill-target
    database reused across runs, a migration replayed twice, a restore
    pointed at the wrong artifact) would have passed every prior check
    silently. This proves the fix: the comparison now also walks the actual
    side and flags anything the manifest didn't expect.
    """
    postgres_url, container_id = postgres_container
    _run_migrations(postgres_url)
    engine = create_engine(postgres_url, future=True)
    bind_engine(engine)
    try:
        _seed_two_tables(engine)

        in_container_url = "postgresql://test:test@localhost:5432/test"
        backup_dir = tmp_path / "backup"
        artifact = run_postgres_backup(
            database_url=in_container_url,
            dest_dir=backup_dir,
            pg_dump_command=_exec_prefix(container_id, "pg_dump"),
        )
        manifest = BackupManifest(
            backup_id="pg-restore-drill-extra-table",
            created_at=datetime.now(UTC),
            engine="postgres",
            db_artifact=artifact.name,
            tables=snapshot_tables(engine),
        )

        restore_db_name = "civiccast_drill_restore_extra_table"
        restore_cli_url = create_fresh_postgres_database(
            database_url=in_container_url,
            database_name=restore_db_name,
            psql_command=_exec_prefix(container_id, "psql"),
        )
        run_postgres_restore(
            backup_dir / manifest.db_artifact,
            restore_cli_url,
            pg_restore_command=_exec_prefix(container_id, "pg_restore"),
        )

        restored_url = postgres_url.rsplit("/", 1)[0] + f"/{restore_db_name}"
        restored_engine = create_engine(restored_url, future=True)
        try:
            with restored_engine.begin() as conn:
                conn.execute(text("CREATE TABLE civiccast.unexpected_extra (id int primary key)"))

            results = _table_results(manifest.tables, snapshot_tables(restored_engine))
            extra = next((r for r in results if r.name == "unexpected_extra"), None)
            assert extra is not None, (
                "the unexpected table never showed up in the comparison at all"
            )
            assert extra.matched is False
            assert extra.expected_row_count is None
            assert extra.expected_checksum is None
        finally:
            restored_engine.dispose()
    finally:
        reset_engine()
        engine.dispose()


def test_sequence_state_drift_is_detected(
    postgres_container: tuple[str, str], tmp_path: Path
) -> None:
    """Negative control (iv): a sequence whose STATE drifted after restore (same
    name, different value) must be DETECTED.

    Direct helper-level control on ``_pg_sequence_states`` --
    ``run_postgres_restore_drill`` consumes exactly this comparison, so a
    control at this level is proof about the mechanism the shipping drill
    actually runs, not a reimplementation of it. The predecessor
    name-only sequence check would have reported this scenario as a clean
    match: the name is unchanged, only its value moved.
    """
    postgres_url, container_id = postgres_container
    _run_migrations(postgres_url)
    engine = create_engine(postgres_url, future=True)
    bind_engine(engine)
    try:
        _seed_two_tables(engine)

        in_container_url = "postgresql://test:test@localhost:5432/test"
        backup_dir = tmp_path / "backup"
        artifact = run_postgres_backup(
            database_url=in_container_url,
            dest_dir=backup_dir,
            pg_dump_command=_exec_prefix(container_id, "pg_dump"),
        )

        restore_db_name = "civiccast_drill_restore_sequence_drift"
        restore_cli_url = create_fresh_postgres_database(
            database_url=in_container_url,
            database_name=restore_db_name,
            psql_command=_exec_prefix(container_id, "psql"),
        )
        run_postgres_restore(
            artifact,
            restore_cli_url,
            pg_restore_command=_exec_prefix(container_id, "pg_restore"),
        )

        restored_url = postgres_url.rsplit("/", 1)[0] + f"/{restore_db_name}"
        restored_engine = create_engine(restored_url, future=True)
        try:
            source_states = _pg_sequence_states(engine)
            assert source_states, "fixture has no sequences to drift -- control would be vacuous"
            drift_sequence = source_states[0][0]

            # Sanity: immediately after restore, before the drift below, state
            # must match -- otherwise the inequality asserted below could just
            # be a permanent, meaningless mismatch rather than a real control.
            assert _pg_sequence_states(restored_engine) == source_states

            with restored_engine.begin() as conn:
                conn.execute(text(f'SELECT setval(\'"civiccast"."{drift_sequence}"\', 999999)'))

            assert _pg_sequence_states(restored_engine) != source_states
        finally:
            restored_engine.dispose()
    finally:
        reset_engine()
        engine.dispose()


def test_constraint_drop_is_detected(postgres_container: tuple[str, str], tmp_path: Path) -> None:
    """Negative control (v): a dropped constraint on the restored copy must be DETECTED.

    Row/checksum equality proves the DATA came back; it says nothing about
    the constraints that were supposed to keep protecting that data going
    forward. This drops a real CHECK constraint (``egress_sinks_kind_check``,
    added by migration 0020) on the restored copy only, and proves
    ``_pg_constraint_defs`` -- the exact function ``run_postgres_restore_drill``
    calls -- notices.
    """
    postgres_url, container_id = postgres_container
    _run_migrations(postgres_url)
    engine = create_engine(postgres_url, future=True)
    bind_engine(engine)
    try:
        _seed_two_tables(engine)

        in_container_url = "postgresql://test:test@localhost:5432/test"
        backup_dir = tmp_path / "backup"
        artifact = run_postgres_backup(
            database_url=in_container_url,
            dest_dir=backup_dir,
            pg_dump_command=_exec_prefix(container_id, "pg_dump"),
        )

        restore_db_name = "civiccast_drill_restore_constraint_drop"
        restore_cli_url = create_fresh_postgres_database(
            database_url=in_container_url,
            database_name=restore_db_name,
            psql_command=_exec_prefix(container_id, "psql"),
        )
        run_postgres_restore(
            artifact,
            restore_cli_url,
            pg_restore_command=_exec_prefix(container_id, "pg_restore"),
        )

        restored_url = postgres_url.rsplit("/", 1)[0] + f"/{restore_db_name}"
        restored_engine = create_engine(restored_url, future=True)
        try:
            source_constraints = _pg_constraint_defs(engine)
            # Sanity: right after restore, before dropping anything, the two
            # sides must already agree UNDER THE DRILL'S OWN COMPARISON
            # (_def_lists_mismatch, same-server canonicalization included --
            # raw string equality is NOT guaranteed across dump/restore; CI
            # run 29562914698 proved deparse parenthesization differs) --
            # otherwise the mismatch asserted below wouldn't prove the DROP
            # caused it.
            baseline = _def_lists_mismatch(
                source_constraints, _pg_constraint_defs(restored_engine), restored_engine
            )
            assert baseline is None, baseline

            with restored_engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE civiccast.egress_sinks DROP CONSTRAINT egress_sinks_kind_check"
                    )
                )

            diff = _def_lists_mismatch(
                source_constraints, _pg_constraint_defs(restored_engine), restored_engine
            )
            assert diff is not None
            assert "egress_sinks_kind_check" in diff
            assert "missing from restore" in diff
        finally:
            restored_engine.dispose()
    finally:
        reset_engine()
        engine.dispose()


def test_quiescence_guard_raises() -> None:
    """Pure unit -- no Docker fixture, runs everywhere.

    Finding 3 extracted ``_assert_backup_quiescent`` specifically so the
    manifest-not-bound-to-the-dump precondition is testable without a real
    database: two snapshot lists differing in a single checksum must raise;
    identical lists must not.
    """

    before = [
        TableSnapshot(name="widgets", row_count=1, checksum_sha256="a" * 64),
        TableSnapshot(name="gadgets", row_count=2, checksum_sha256="b" * 64),
    ]
    after_changed = [
        TableSnapshot(name="widgets", row_count=1, checksum_sha256="c" * 64),
        TableSnapshot(name="gadgets", row_count=2, checksum_sha256="b" * 64),
    ]
    with pytest.raises(RuntimeError, match="changed during backup"):
        _assert_backup_quiescent(before, after_changed)

    after_same = [
        TableSnapshot(name="widgets", row_count=1, checksum_sha256="a" * 64),
        TableSnapshot(name="gadgets", row_count=2, checksum_sha256="b" * 64),
    ]
    _assert_backup_quiescent(before, after_same)  # must not raise


def test_role_capture_match_is_word_bounded() -> None:
    """Pure unit -- no Docker fixture, runs everywhere.

    The globals-coverage check must not count role "test" as captured just
    because "CREATE ROLE testing" appears in globals.sql -- an unrelated
    role that happens to share a prefix would otherwise mask a genuinely
    missing capture. Quoted spellings and the bootstrap-superuser ALTER
    ROLE form both still count.
    """

    assert not _role_captured_in_globals("CREATE ROLE testing;", "test")
    assert not _role_captured_in_globals("ALTER ROLE tester WITH LOGIN;", "test")
    assert _role_captured_in_globals("CREATE ROLE test;", "test")
    assert _role_captured_in_globals("ALTER ROLE test WITH SUPERUSER;", "test")
    assert _role_captured_in_globals('CREATE ROLE "test";', "test")
    assert not _role_captured_in_globals("-- no roles here", "test")
    # Round 3 fix (comment false-positive): a line whose first non-whitespace
    # characters are "--" is a SQL comment and must never count, even though
    # it names a real CREATE/ALTER ROLE statement in prose.
    assert not _role_captured_in_globals("-- CREATE ROLE test;", "test")
    assert not _role_captured_in_globals("  -- ALTER ROLE test WITH SUPERUSER;", "test")
    # An indented (non-comment) statement must still count -- only a leading
    # "--" disqualifies a line, not leading whitespace by itself.
    assert _role_captured_in_globals("  CREATE ROLE test;", "test")


def test_canonicalization_replaces_text_normalization(
    postgres_container: tuple[str, str], tmp_path: Path
) -> None:
    """Fix 1 (CC-WS2-001a): same-server canonicalization replaces the deleted
    text-stripping ``_normalize_ddl`` comparison. Docker-gated (canonicalization
    needs a real server to re-deparse through) -- drives
    ``_canonicalize_defs``/``_def_lists_mismatch`` directly against a small
    scratch table, per the audit's own test recipe.

    Two halves:

    1. Equivalent-but-differently-parenthesized defs (the CI-observed
       drift classes _normalize_ddl was originally built for -- runs
       29562914698 and 29564258214) still compare CLEAN under
       canonicalization, via a totally different mechanism: both sides
       re-parse through the SAME live server, so equivalent expressions
       deparse identically regardless of original parenthesization.
    2. The auditor's three counterexamples -- each a case _normalize_ddl's
       regex-based cast/paren/whitespace stripping would have compared
       EQUAL (a false "clean" restore), because none of them differ ONLY
       in casts/parens/whitespace -- must each be REPORTED as changed:
       (a) OR/AND operator-precedence regrouping (different truth tables),
       (b) IS NULL vs IS NOT NULL (negation, not decoration),
       (c) an index expression's cast TARGET type (integer vs numeric,
       which truncate ``amount`` differently).
    """
    postgres_url, _container_id = postgres_container
    engine = create_engine(postgres_url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(text('CREATE SCHEMA IF NOT EXISTS "civiccast"'))
            conn.execute(
                text(
                    'CREATE TABLE "civiccast"."canon_drift" '
                    "(a int, b int, c bool, x varchar, amount varchar)"
                )
            )

        # -- 1. equivalent reparenthesization compares clean --------------
        src_equiv = [("civiccast.canon_drift", "chk_equiv", "CHECK ((((a > 0) OR (b > 0))))")]
        dst_equiv = [("civiccast.canon_drift", "chk_equiv", "CHECK ((a > 0) OR (b > 0))")]
        assert _def_lists_mismatch(src_equiv, dst_equiv, engine) is None
        # Direct helper-level check too, per the audit's own recipe.
        canon = _canonicalize_defs(
            engine,
            [*src_equiv, *dst_equiv],
            kind="constraint",
        )
        assert canon[0] == canon[1], canon

        # -- 2(a). operator precedence: OR/AND grouping is NOT interchangeable --
        src_a = [("civiccast.canon_drift", "chk_a", "CHECK ((((a > 0) OR (b > 0)) AND c))")]
        dst_a = [("civiccast.canon_drift", "chk_a", "CHECK (((a > 0) OR ((b > 0) AND c)))")]
        diff_a = _def_lists_mismatch(src_a, dst_a, engine)
        assert diff_a is not None and "definition changed" in diff_a, diff_a

        # -- 2(b). IS NULL vs IS NOT NULL: negation, not a cast/paren difference --
        src_b = [("civiccast.canon_drift", "chk_b", "CHECK (((x)::text IS NULL))")]
        dst_b = [("civiccast.canon_drift", "chk_b", "CHECK (((x)::text IS NOT NULL))")]
        diff_b = _def_lists_mismatch(src_b, dst_b, engine)
        assert diff_b is not None and "definition changed" in diff_b, diff_b

        # -- 2(c). index expression cast target: integer vs numeric truncate differently --
        src_c = [
            (
                "canon_drift",
                "idx_amount",
                "CREATE INDEX idx_amount ON civiccast.canon_drift "
                "USING btree (((amount)::integer))",
            )
        ]
        dst_c = [
            (
                "canon_drift",
                "idx_amount",
                "CREATE INDEX idx_amount ON civiccast.canon_drift "
                "USING btree (((amount)::numeric))",
            )
        ]
        diff_c = _def_lists_mismatch(src_c, dst_c, engine)
        assert diff_c is not None and "definition changed" in diff_c, diff_c

        # Compact-diff regression, same shape as before: missing/unexpected
        # keys are still reported, and matching entries stay OUT of the report.
        dropped = _def_lists_mismatch(src_equiv, [], engine)
        assert dropped is not None and "missing from restore" in dropped

        extra = _def_lists_mismatch([], dst_equiv, engine)
        assert extra is not None and "unexpected in restore" in extra
    finally:
        engine.dispose()


def test_index_literal_matching_scratch_tokens_is_not_collapsed(
    postgres_container: tuple[str, str], tmp_path: Path
) -> None:
    """Round-4 acceptance criterion (CC-WS2-001, Critical, auditor-EXECUTED
    false-pass): two REAL indexes whose WHERE predicates contain the string
    literals ``'__dr_canon'`` and ``'civiccast'`` respectively -- the
    auditor's own exact recipe -- must be reported as ``"definition
    changed"`` by the production ``_def_lists_mismatch``, not collapsed to a
    false clean compare.

    Round-3's ``_canonicalize_defs`` did an unrestricted ``str.replace`` of
    the scratch schema name (``__dr_canon``) and the scratch index name over
    the WHOLE ``pg_get_indexdef`` output before comparing -- so a predicate
    literal that happened to equal one of those tokens got silently
    rewritten too, and two semantically DIFFERENT partial indexes compared
    byte-identical. Real DDL, real ``pg_indexes.indexdef`` reads (not
    hand-crafted defs like the operator-precedence/null/cast-target controls
    in ``test_canonicalization_replaces_text_normalization`` above) --
    created and dropped SEQUENTIALLY on one scratch table, since a single
    schema cannot hold two indexes under the same name at once (which is
    also exactly why ``_canonicalize_defs`` itself now creates, reads, and
    DROPS each scratch object before moving to the next entry -- see that
    function's docstring).
    """
    postgres_url, _container_id = postgres_container
    engine = create_engine(postgres_url, future=True)
    try:
        with engine.begin() as conn:
            conn.execute(text('CREATE SCHEMA IF NOT EXISTS "civiccast"'))
            conn.execute(
                text('CREATE TABLE "civiccast"."canon_literal_collision" (a int, note varchar)')
            )

        def _create_and_read(predicate_literal: str) -> tuple[str, str, str]:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "CREATE INDEX idx_literal_collision ON "
                        "civiccast.canon_literal_collision (a) "
                        f"WHERE (note <> '{predicate_literal}')"
                    )
                )
            with engine.connect() as conn:
                row = conn.execute(
                    text(
                        "SELECT tablename, indexname, indexdef FROM pg_indexes "
                        "WHERE schemaname = 'civiccast' "
                        "AND indexname = 'idx_literal_collision'"
                    )
                ).fetchone()
            assert row is not None
            captured = (row[0], row[1], row[2])
            with engine.begin() as conn:
                conn.execute(text("DROP INDEX civiccast.idx_literal_collision"))
            return captured

        # "source" and "restored" here are two REAL, independently created
        # and read indexes -- the auditor's own counterexample literals.
        source_def = _create_and_read("__dr_canon")
        restored_def = _create_and_read("civiccast")

        diff = _def_lists_mismatch([source_def], [restored_def], engine)
        assert diff is not None and "definition changed" in diff, diff
    finally:
        with engine.begin() as conn:
            conn.execute(text('DROP TABLE IF EXISTS "civiccast"."canon_literal_collision"'))
        engine.dispose()


def test_cold_standby_fresh_cluster_round_trip(
    postgres_container: tuple[str, str],
    postgres_standby_container: tuple[str, str],
    tmp_path: Path,
) -> None:
    """Fix 2 (CC-WS2-001b): prove a backup restores onto a cluster that has
    NEVER seen the source's roles. The same-cluster drill's restore target
    lives on the SAME cluster as the source, so every owner/grantee role
    already exists there by construction and role restorability is never
    actually exercised -- this test is that missing exercise, against a
    genuinely independent second testcontainer.
    """
    source_url, source_cid = postgres_container
    standby_url, standby_cid = postgres_standby_container

    _run_migrations(source_url)
    source_engine = create_engine(source_url, future=True)
    bind_engine(source_engine)
    try:
        _seed_two_tables(source_engine)
        _seed_extra_role_and_grant(source_engine)

        in_container_source_url = "postgresql://test:test@localhost:5432/test"
        backup_dir = tmp_path / "backup"
        artifact = run_postgres_backup(
            database_url=in_container_source_url,
            dest_dir=backup_dir,
            pg_dump_command=_exec_prefix(source_cid, "pg_dump"),
        )
        globals_artifact = run_postgres_globals_backup(
            database_url=in_container_source_url,
            dest_dir=backup_dir,
            pg_dumpall_command=_exec_prefix(source_cid, "pg_dumpall"),
        )
        manifest = BackupManifest(
            backup_id="pg-cold-standby-round-trip",
            created_at=datetime.now(UTC),
            engine="postgres",
            db_artifact=artifact.name,
            tables=snapshot_tables(source_engine),
            globals_artifact=globals_artifact.name,
        )

        in_container_standby_url = "postgresql://test:test@localhost:5432/test"
        report = run_postgres_cold_standby_drill(
            backup_dir=backup_dir,
            manifest=manifest,
            standby_database_url=in_container_standby_url,
            standby_verification_database_url=standby_url,
            source_engine_url=source_url,
            restore_database_name="civiccast_cold_standby_round_trip",
            standby_psql_command=_exec_prefix(standby_cid, "psql"),
            standby_pg_restore_command=_exec_prefix(standby_cid, "pg_restore"),
        )

        assert report.errors == [], report.errors
        assert report.schema_ok
        assert report.ok
        assert all(t.matched for t in report.tables), report.tables
        assert report.app_store_reads.get("assets") == 1
        assert report.app_store_reads.get("egress_configs") == 1
    finally:
        reset_engine()
        source_engine.dispose()


def test_cold_standby_detects_missing_role_from_corrupted_globals(
    postgres_container: tuple[str, str],
    postgres_standby_container: tuple[str, str],
    tmp_path: Path,
) -> None:
    """Negative control (i): a truncated/mutated globals.sql must make the
    cold-standby drill report the missing role, not silently pass.
    """
    source_url, source_cid = postgres_container
    standby_url, standby_cid = postgres_standby_container

    _run_migrations(source_url)
    engine = create_engine(source_url, future=True)
    bind_engine(engine)
    try:
        _seed_two_tables(engine)
        _seed_extra_role_and_grant(engine)

        in_container_source_url = "postgresql://test:test@localhost:5432/test"
        backup_dir = tmp_path / "backup"
        artifact = run_postgres_backup(
            database_url=in_container_source_url,
            dest_dir=backup_dir,
            pg_dump_command=_exec_prefix(source_cid, "pg_dump"),
        )
        globals_artifact = run_postgres_globals_backup(
            database_url=in_container_source_url,
            dest_dir=backup_dir,
            pg_dumpall_command=_exec_prefix(source_cid, "pg_dumpall"),
        )
        # Corrupt: strip every line mentioning drill_grantee out of
        # globals.sql, simulating a truncated/incomplete globals capture.
        corrupted = "\n".join(
            line
            for line in globals_artifact.read_text(encoding="utf-8").splitlines()
            if "drill_grantee" not in line
        )
        globals_artifact.write_text(corrupted, encoding="utf-8")

        manifest = BackupManifest(
            backup_id="pg-cold-standby-missing-role",
            created_at=datetime.now(UTC),
            engine="postgres",
            db_artifact=artifact.name,
            tables=snapshot_tables(engine),
            globals_artifact=globals_artifact.name,
        )

        in_container_standby_url = "postgresql://test:test@localhost:5432/test"
        report = run_postgres_cold_standby_drill(
            backup_dir=backup_dir,
            manifest=manifest,
            standby_database_url=in_container_standby_url,
            standby_verification_database_url=standby_url,
            source_engine_url=source_url,
            restore_database_name="civiccast_cold_standby_missing_role",
            standby_psql_command=_exec_prefix(standby_cid, "psql"),
            standby_pg_restore_command=_exec_prefix(standby_cid, "pg_restore"),
        )

        assert any("drill_grantee" in e and "missing on standby" in e for e in report.errors), (
            report.errors
        )
    finally:
        reset_engine()
        engine.dispose()


def test_cold_standby_detects_role_attribute_tamper(
    postgres_container: tuple[str, str],
    postgres_standby_container: tuple[str, str],
    tmp_path: Path,
) -> None:
    """Negative control (ii): a role attribute tampered on the standby AFTER
    globals replay must be DETECTED by the REAL cold-standby REPORT
    (``report.ok is False`` with the expected error), not just a
    helper-level ``_pg_role_attributes`` comparison -- CC-WS2-001's round-3
    finding was that this test asserted only the helper's output, so
    deleting the corresponding production comparison would not have made it
    red.

    Technique note: ``run_postgres_cold_standby_drill``'s OWN replay step
    (``_replay_globals_sql``) re-applies globals.sql's full ``ALTER ROLE ...
    WITH <every attribute flag>`` statement for every role, on EVERY
    invocation -- that is what proves role restorability, but it also means
    a standby-side attribute tamper applied BEFORE calling the drill would
    just get overwritten by the drill's own replay before its comparison
    ever runs. So this control: (1) replays globals.sql onto the standby
    directly, mirroring the drill's own step 1, (2) tampers the standby's
    role attribute directly, then (3) invokes the REAL
    ``run_postgres_cold_standby_drill`` with a manifest whose
    ``globals_artifact`` is ``None`` -- which makes the drill's OWN replay a
    no-op (it still reports that omission as its own, separate error) while
    the role ATTRIBUTE COMPARISON (unconditional -- it reads whatever is
    currently on the standby, replay or not) runs for real and sees the
    tamper. This is a single "fresh" invocation of the real production
    function; nothing about it re-implements the comparison.
    """
    source_url, source_cid = postgres_container
    standby_url, standby_cid = postgres_standby_container

    _run_migrations(source_url)
    engine = create_engine(source_url, future=True)
    bind_engine(engine)
    try:
        _seed_two_tables(engine)
        _seed_extra_role_and_grant(engine)

        in_container_source_url = "postgresql://test:test@localhost:5432/test"
        backup_dir = tmp_path / "backup"
        artifact = run_postgres_backup(
            database_url=in_container_source_url,
            dest_dir=backup_dir,
            pg_dump_command=_exec_prefix(source_cid, "pg_dump"),
        )
        globals_artifact = run_postgres_globals_backup(
            database_url=in_container_source_url,
            dest_dir=backup_dir,
            pg_dumpall_command=_exec_prefix(source_cid, "pg_dumpall"),
        )

        in_container_standby_url = "postgresql://test:test@localhost:5432/test"
        _replay_globals_sql(
            globals_artifact.read_text(encoding="utf-8"),
            database_url=in_container_standby_url,
            psql_command=_exec_prefix(standby_cid, "psql"),
        )

        standby_engine = create_engine(standby_url, future=True)
        try:
            relevant = {"drill_grantee"}
            source_attrs = _pg_role_attributes(engine, relevant)
            baseline_standby_attrs = _pg_role_attributes(standby_engine, relevant)
            assert source_attrs == baseline_standby_attrs, (source_attrs, baseline_standby_attrs)

            with standby_engine.begin() as conn:
                conn.execute(text("ALTER ROLE drill_grantee NOLOGIN"))
        finally:
            standby_engine.dispose()

        manifest = BackupManifest(
            backup_id="pg-cold-standby-attribute-tamper",
            created_at=datetime.now(UTC),
            engine="postgres",
            db_artifact=artifact.name,
            tables=snapshot_tables(engine),
            globals_artifact=None,
        )

        report = run_postgres_cold_standby_drill(
            backup_dir=backup_dir,
            manifest=manifest,
            standby_database_url=in_container_standby_url,
            standby_verification_database_url=standby_url,
            source_engine_url=source_url,
            restore_database_name="civiccast_cold_standby_attribute_tamper",
            standby_psql_command=_exec_prefix(standby_cid, "psql"),
            standby_pg_restore_command=_exec_prefix(standby_cid, "pg_restore"),
        )

        assert report.ok is False
        assert any(
            "role attribute mismatch" in e and "drill_grantee" in e for e in report.errors
        ), report.errors
    finally:
        reset_engine()
        engine.dispose()


def test_cold_standby_detects_membership_drop(
    postgres_container: tuple[str, str],
    postgres_standby_container: tuple[str, str],
    tmp_path: Path,
) -> None:
    """Negative control: a role MEMBERSHIP edge dropped on the standby AFTER
    globals replay must be DETECTED by the REAL cold-standby report.

    Exercises the round-4 membership-CLOSURE fix
    (:func:`civiccast.dr.restore_drill._close_roles_over_memberships`)
    end-to-end: ``test`` is already in the relevant-role seed (it owns the
    ``civiccast`` tables), but ``dr_test_group`` owns and is granted
    NOTHING of its own -- it is reachable ONLY via the ``GRANT dr_test_group
    TO test`` membership edge, exactly the auditor's own executed
    false-pass shape (an ``app_owner -> ops_admin`` edge invisible because
    ``ops_admin`` owned/granted nothing directly). Without the closure fix,
    ``dr_test_group`` would never enter ``_pg_relevant_roles``'s output and
    this edge's drop would be silently invisible to
    ``_pg_role_memberships``, which only compares edges between roles
    ALREADY in the set it is given.

    Same globals-artifact-omission technique as
    ``test_cold_standby_detects_role_attribute_tamper`` above, for the same
    structural reason: the drill's own replay would otherwise re-establish
    the membership edge (``pg_dumpall --globals-only`` captures ``GRANT
    <group> TO <member>;`` statements) before the comparison ever ran.
    """
    source_url, source_cid = postgres_container
    standby_url, standby_cid = postgres_standby_container

    _run_migrations(source_url)
    engine = create_engine(source_url, future=True)
    bind_engine(engine)
    try:
        _seed_two_tables(engine)
        with engine.begin() as conn:
            conn.execute(text("DROP ROLE IF EXISTS dr_test_group"))
            conn.execute(text("CREATE ROLE dr_test_group NOLOGIN"))
            conn.execute(text("GRANT dr_test_group TO test"))

        in_container_source_url = "postgresql://test:test@localhost:5432/test"
        backup_dir = tmp_path / "backup"
        artifact = run_postgres_backup(
            database_url=in_container_source_url,
            dest_dir=backup_dir,
            pg_dump_command=_exec_prefix(source_cid, "pg_dump"),
        )
        globals_artifact = run_postgres_globals_backup(
            database_url=in_container_source_url,
            dest_dir=backup_dir,
            pg_dumpall_command=_exec_prefix(source_cid, "pg_dumpall"),
        )

        in_container_standby_url = "postgresql://test:test@localhost:5432/test"
        _replay_globals_sql(
            globals_artifact.read_text(encoding="utf-8"),
            database_url=in_container_standby_url,
            psql_command=_exec_prefix(standby_cid, "psql"),
        )

        standby_engine = create_engine(standby_url, future=True)
        try:
            with standby_engine.begin() as conn:
                edge_exists = conn.execute(
                    text(
                        "SELECT 1 FROM pg_auth_members am "
                        "JOIN pg_roles m ON m.oid = am.member "
                        "JOIN pg_roles r ON r.oid = am.roleid "
                        "WHERE m.rolname = 'test' AND r.rolname = 'dr_test_group'"
                    )
                ).fetchone()
                assert edge_exists is not None, (
                    "globals replay did not establish the membership edge -- "
                    "the control below would be vacuous"
                )
                conn.execute(text("REVOKE dr_test_group FROM test"))
        finally:
            standby_engine.dispose()

        manifest = BackupManifest(
            backup_id="pg-cold-standby-membership-drop",
            created_at=datetime.now(UTC),
            engine="postgres",
            db_artifact=artifact.name,
            tables=snapshot_tables(engine),
            globals_artifact=None,
        )

        report = run_postgres_cold_standby_drill(
            backup_dir=backup_dir,
            manifest=manifest,
            standby_database_url=in_container_standby_url,
            standby_verification_database_url=standby_url,
            source_engine_url=source_url,
            restore_database_name="civiccast_cold_standby_membership_drop",
            standby_psql_command=_exec_prefix(standby_cid, "psql"),
            standby_pg_restore_command=_exec_prefix(standby_cid, "pg_restore"),
        )

        assert report.ok is False
        assert any(
            "role membership mismatch" in e and "dr_test_group" in e for e in report.errors
        ), report.errors
    finally:
        reset_engine()
        engine.dispose()


def test_cold_standby_detects_revoked_grant(
    postgres_container: tuple[str, str],
    postgres_standby_container: tuple[str, str],
    tmp_path: Path,
) -> None:
    """Negative control (iii)+(iv): permissions that drift on the SOURCE
    after a backup was taken -- a table grant revoked, a sequence privilege
    revoked, a database-level privilege granted -- must be DETECTED by the
    REAL cold-standby REPORT (``report.ok is False`` with the expected
    error), not a helper-level comparison. Covers CC-WS2-001's required
    "revoked table grant" control plus "revoked schema USAGE or sequence
    privilege" (sequence chosen here) plus a database-level privilege
    control.

    Technique note (why this mutates the SOURCE, not literally "the
    standby's restored DB" bytes): ``run_postgres_cold_standby_drill``'s OWN
    restore step (``create_fresh_postgres_database`` + ``pg_restore``)
    unconditionally rebuilds the standby's database from the SAME dump
    artifact on EVERY invocation, and table/sequence ACLs are part of that
    dump (``pg_dump`` includes ``GRANT``/``REVOKE`` statements by default) --
    so a standby-side ACL tamper can never survive into a SUBSEQUENT call's
    comparison; the very next invocation's restore silently reasserts the
    dump's original grants before anything is compared. A database's OWN
    ACL is an even more structural case: neither ``pg_dump`` (single-database
    scope) nor ``pg_dumpall --globals-only`` (roles and tablespaces only)
    ever capture or replay a database's ``datacl`` at all, so it is never
    restored by ANY step of this drill regardless of tampering order --
    demonstrating a database-privilege mismatch therefore only requires
    granting something extra on the source (it can never be replicated to
    the standby through any code path this drill has), exactly the
    documented fallback for this control.

    The failure mode this control actually proves is real and important in
    its own right: the SOURCE database's live permissions drifted after the
    last backup was taken, so restoring from that (now-stale) backup no
    longer matches what is actually running today -- precisely the kind of
    silent-until-3am gap a disaster-recovery drill exists to catch. This
    mutates the SOURCE (live) after the dump/globals were captured, then
    invokes the REAL ``run_postgres_cold_standby_drill`` exactly once -- an
    ordinary, single "fresh" call, no manual pre-replay/pre-restore
    choreography needed (unlike the role-attribute/membership controls
    above), precisely because this technique does not fight the drill's own
    unconditional restore.
    """
    source_url, source_cid = postgres_container
    standby_url, standby_cid = postgres_standby_container

    _run_migrations(source_url)
    engine = create_engine(source_url, future=True)
    bind_engine(engine)
    try:
        _seed_two_tables(engine)
        _seed_extra_role_and_grant(engine)  # drill_grantee: SELECT on civiccast.assets

        source_sequences = _pg_sequence_states(engine)
        assert source_sequences, (
            "fixture has no sequences -- sequence-privilege control would be vacuous"
        )
        target_sequence = source_sequences[0][0]
        with engine.begin() as conn:
            conn.execute(
                text(f'GRANT USAGE ON SEQUENCE "civiccast"."{target_sequence}" TO drill_grantee')
            )

        in_container_source_url = "postgresql://test:test@localhost:5432/test"
        backup_dir = tmp_path / "backup"
        artifact = run_postgres_backup(
            database_url=in_container_source_url,
            dest_dir=backup_dir,
            pg_dump_command=_exec_prefix(source_cid, "pg_dump"),
        )
        globals_artifact = run_postgres_globals_backup(
            database_url=in_container_source_url,
            dest_dir=backup_dir,
            pg_dumpall_command=_exec_prefix(source_cid, "pg_dumpall"),
        )
        manifest = BackupManifest(
            backup_id="pg-cold-standby-revoked-grant",
            created_at=datetime.now(UTC),
            engine="postgres",
            db_artifact=artifact.name,
            tables=snapshot_tables(engine),
            globals_artifact=globals_artifact.name,
        )

        # Drift the SOURCE (live) AFTER the backup was taken -- the dump and
        # globals capture above are already frozen at the pre-drift state.
        with engine.begin() as conn:
            conn.execute(text("REVOKE SELECT ON civiccast.assets FROM drill_grantee"))
            conn.execute(
                text(f'REVOKE USAGE ON SEQUENCE "civiccast"."{target_sequence}" FROM drill_grantee')
            )
            conn.execute(text("GRANT CONNECT ON DATABASE test TO drill_grantee"))

        in_container_standby_url = "postgresql://test:test@localhost:5432/test"
        report = run_postgres_cold_standby_drill(
            backup_dir=backup_dir,
            manifest=manifest,
            standby_database_url=in_container_standby_url,
            standby_verification_database_url=standby_url,
            source_engine_url=source_url,
            restore_database_name="civiccast_cold_standby_revoked_grant",
            standby_psql_command=_exec_prefix(standby_cid, "psql"),
            standby_pg_restore_command=_exec_prefix(standby_cid, "pg_restore"),
        )

        assert report.ok is False
        assert any("table grant mismatch" in e for e in report.errors), report.errors
        assert any("sequence privilege" in e for e in report.errors), report.errors
        assert any("database privilege" in e for e in report.errors), report.errors
    finally:
        reset_engine()
        engine.dispose()


def test_cold_standby_detects_standby_only_membership_edge(
    postgres_container: tuple[str, str],
    postgres_standby_container: tuple[str, str],
    tmp_path: Path,
) -> None:
    """Negative control: a role MEMBERSHIP edge that exists ONLY on the
    standby, but touches a source-relevant role, must be DETECTED by the
    REAL cold-standby report.

    Exercises the EITHER-endpoint filter in ``_pg_role_memberships``
    (round-4 auditor finding): a standby-only edge whose ONE endpoint is a
    source-relevant role (``drill_grantee``, relevant because it holds a
    real table grant -- see ``_seed_extra_role_and_grant``) and whose OTHER
    endpoint (``standby_only_group``) exists ONLY on the standby -- never on
    the source, never captured by globals.sql -- must still be reported,
    because the filter retains an edge if EITHER endpoint is in the
    relevant set, not only when BOTH are. Requiring both endpoints (the
    pre-round-4 behavior) would have silently accepted this edge, since
    ``standby_only_group`` is never a member of the source-derived relevant
    set.

    Same globals-artifact-omission technique as
    ``test_cold_standby_detects_role_attribute_tamper``/
    ``test_cold_standby_detects_membership_drop`` above: replay globals.sql
    onto the standby directly (mirroring the drill's own step 1), THEN
    create the standby-only group and edge, THEN invoke the REAL
    ``run_postgres_cold_standby_drill`` with ``globals_artifact=None`` so
    its own replay is a no-op and cannot undo the tamper before the
    comparison runs.
    """
    source_url, source_cid = postgres_container
    standby_url, standby_cid = postgres_standby_container

    _run_migrations(source_url)
    engine = create_engine(source_url, future=True)
    bind_engine(engine)
    try:
        _seed_two_tables(engine)
        _seed_extra_role_and_grant(engine)  # drill_grantee: relevant via table grant

        in_container_source_url = "postgresql://test:test@localhost:5432/test"
        backup_dir = tmp_path / "backup"
        artifact = run_postgres_backup(
            database_url=in_container_source_url,
            dest_dir=backup_dir,
            pg_dump_command=_exec_prefix(source_cid, "pg_dump"),
        )
        globals_artifact = run_postgres_globals_backup(
            database_url=in_container_source_url,
            dest_dir=backup_dir,
            pg_dumpall_command=_exec_prefix(source_cid, "pg_dumpall"),
        )

        in_container_standby_url = "postgresql://test:test@localhost:5432/test"
        _replay_globals_sql(
            globals_artifact.read_text(encoding="utf-8"),
            database_url=in_container_standby_url,
            psql_command=_exec_prefix(standby_cid, "psql"),
        )

        standby_engine = create_engine(standby_url, future=True)
        try:
            with standby_engine.begin() as conn:
                conn.execute(text("DROP ROLE IF EXISTS standby_only_group"))
                conn.execute(text("CREATE ROLE standby_only_group NOLOGIN"))
                conn.execute(text("GRANT standby_only_group TO drill_grantee"))
                edge_exists = conn.execute(
                    text(
                        "SELECT 1 FROM pg_auth_members am "
                        "JOIN pg_roles m ON m.oid = am.member "
                        "JOIN pg_roles r ON r.oid = am.roleid "
                        "WHERE m.rolname = 'drill_grantee' "
                        "AND r.rolname = 'standby_only_group'"
                    )
                ).fetchone()
                assert edge_exists is not None, (
                    "standby-only membership edge was not established -- "
                    "the control below would be vacuous"
                )
        finally:
            standby_engine.dispose()

        manifest = BackupManifest(
            backup_id="pg-cold-standby-standby-only-edge",
            created_at=datetime.now(UTC),
            engine="postgres",
            db_artifact=artifact.name,
            tables=snapshot_tables(engine),
            globals_artifact=None,
        )

        report = run_postgres_cold_standby_drill(
            backup_dir=backup_dir,
            manifest=manifest,
            standby_database_url=in_container_standby_url,
            standby_verification_database_url=standby_url,
            source_engine_url=source_url,
            restore_database_name="civiccast_cold_standby_standby_only_edge",
            standby_psql_command=_exec_prefix(standby_cid, "psql"),
            standby_pg_restore_command=_exec_prefix(standby_cid, "pg_restore"),
        )

        assert report.ok is False
        assert any(
            "role membership mismatch" in e and "standby_only_group" in e for e in report.errors
        ), report.errors
    finally:
        reset_engine()
        engine.dispose()


def test_cold_standby_detects_public_table_grant_difference(
    postgres_container: tuple[str, str],
    postgres_standby_container: tuple[str, str],
    tmp_path: Path,
) -> None:
    """Negative control: a PUBLIC table grant present on the SOURCE but not
    captured in the backup artifact must be DETECTED by the REAL
    cold-standby report's table-grant comparison.

    Same "drift the SOURCE after the backup was taken" technique as
    ``test_cold_standby_detects_revoked_grant`` above, for the identical
    structural reason: ``run_postgres_cold_standby_drill``'s OWN restore
    step unconditionally rebuilds the standby from the dump artifact on
    every invocation, so a standby-side ACL tamper can never survive past a
    single call -- only a SOURCE-side drift after the artifact was captured
    stays visible for the comparison to catch. ``_pg_table_grants`` reads
    PUBLIC grants via ``aclexplode`` (grantee oid 0 renders as the literal
    string ``'PUBLIC'`` -- see that function's docstring), so a PUBLIC grant
    the artifact does not contain is exactly the kind of drift this control
    proves is caught, not silently ignored the way the predecessor
    ``information_schema.role_table_grants``-based comparison would have
    (round-4 auditor finding: that view omits PUBLIC grants entirely).
    """
    source_url, source_cid = postgres_container
    standby_url, standby_cid = postgres_standby_container

    _run_migrations(source_url)
    engine = create_engine(source_url, future=True)
    bind_engine(engine)
    try:
        _seed_two_tables(engine)
        _seed_extra_role_and_grant(engine)

        in_container_source_url = "postgresql://test:test@localhost:5432/test"
        backup_dir = tmp_path / "backup"
        artifact = run_postgres_backup(
            database_url=in_container_source_url,
            dest_dir=backup_dir,
            pg_dump_command=_exec_prefix(source_cid, "pg_dump"),
        )
        globals_artifact = run_postgres_globals_backup(
            database_url=in_container_source_url,
            dest_dir=backup_dir,
            pg_dumpall_command=_exec_prefix(source_cid, "pg_dumpall"),
        )
        manifest = BackupManifest(
            backup_id="pg-cold-standby-public-grant",
            created_at=datetime.now(UTC),
            engine="postgres",
            db_artifact=artifact.name,
            tables=snapshot_tables(engine),
            globals_artifact=globals_artifact.name,
        )

        # Drift the SOURCE (live) AFTER the backup was taken -- the dump
        # above is already frozen at the pre-drift state and does not
        # contain this PUBLIC grant.
        with engine.begin() as conn:
            conn.execute(text("GRANT SELECT ON civiccast.assets TO PUBLIC"))

        in_container_standby_url = "postgresql://test:test@localhost:5432/test"
        report = run_postgres_cold_standby_drill(
            backup_dir=backup_dir,
            manifest=manifest,
            standby_database_url=in_container_standby_url,
            standby_verification_database_url=standby_url,
            source_engine_url=source_url,
            restore_database_name="civiccast_cold_standby_public_grant",
            standby_psql_command=_exec_prefix(standby_cid, "psql"),
            standby_pg_restore_command=_exec_prefix(standby_cid, "pg_restore"),
        )

        assert report.ok is False
        grant_errors = [e for e in report.errors if "table grant mismatch" in e]
        assert grant_errors, report.errors
        assert any("PUBLIC" in e for e in grant_errors), grant_errors
    finally:
        reset_engine()
        engine.dispose()


def test_cold_standby_detects_grant_option_difference(
    postgres_container: tuple[str, str],
    postgres_standby_container: tuple[str, str],
    tmp_path: Path,
) -> None:
    """Negative control: an otherwise-identical table privilege that gains
    WITH GRANT OPTION on the SOURCE after the backup was captured must be
    DETECTED by the REAL cold-standby report's table-grant comparison.

    ``_pg_table_grants`` returns ``is_grantable`` as part of each grant
    tuple (grantee, grantor, table, privilege, is_grantable) -- two grants
    identical in every OTHER field but differing only in whether the
    grantee can re-grant the privilege are a real permission difference
    (the grantee gains the ability to propagate access to others), not
    cosmetic. Same "drift the SOURCE after the backup was taken" technique
    as ``test_cold_standby_detects_public_table_grant_difference`` and
    ``test_cold_standby_detects_revoked_grant`` above: ``drill_grantee``
    already holds a plain (non-grantable) SELECT on ``civiccast.assets``
    from ``_seed_extra_role_and_grant``, captured that way in the dump;
    adding WITH GRANT OPTION on the live source afterward flips only the
    ``is_grantable`` field of that SAME (grantee, grantor, table,
    privilege) entry, which the artifact -- and therefore the restored
    standby -- does not have.
    """
    source_url, source_cid = postgres_container
    standby_url, standby_cid = postgres_standby_container

    _run_migrations(source_url)
    engine = create_engine(source_url, future=True)
    bind_engine(engine)
    try:
        _seed_two_tables(engine)
        _seed_extra_role_and_grant(engine)  # drill_grantee: SELECT, NOT grantable

        in_container_source_url = "postgresql://test:test@localhost:5432/test"
        backup_dir = tmp_path / "backup"
        artifact = run_postgres_backup(
            database_url=in_container_source_url,
            dest_dir=backup_dir,
            pg_dump_command=_exec_prefix(source_cid, "pg_dump"),
        )
        globals_artifact = run_postgres_globals_backup(
            database_url=in_container_source_url,
            dest_dir=backup_dir,
            pg_dumpall_command=_exec_prefix(source_cid, "pg_dumpall"),
        )
        manifest = BackupManifest(
            backup_id="pg-cold-standby-grant-option",
            created_at=datetime.now(UTC),
            engine="postgres",
            db_artifact=artifact.name,
            tables=snapshot_tables(engine),
            globals_artifact=globals_artifact.name,
        )

        # Drift the SOURCE (live) AFTER the backup was taken: same grantee,
        # same table, same privilege -- only is_grantable flips.
        with engine.begin() as conn:
            conn.execute(
                text("GRANT SELECT ON civiccast.assets TO drill_grantee WITH GRANT OPTION")
            )

        in_container_standby_url = "postgresql://test:test@localhost:5432/test"
        report = run_postgres_cold_standby_drill(
            backup_dir=backup_dir,
            manifest=manifest,
            standby_database_url=in_container_standby_url,
            standby_verification_database_url=standby_url,
            source_engine_url=source_url,
            restore_database_name="civiccast_cold_standby_grant_option",
            standby_psql_command=_exec_prefix(standby_cid, "psql"),
            standby_pg_restore_command=_exec_prefix(standby_cid, "pg_restore"),
        )

        assert report.ok is False
        assert any("table grant mismatch" in e for e in report.errors), report.errors
    finally:
        reset_engine()
        engine.dispose()


@pytest.mark.skipif(
    not os.environ.get("CIVICCAST_TEST_POSTGRES_BIN"),
    reason="native station archive proof needs explicit disposable PostgreSQL tools",
)
def test_station_archive_roundtrip_uses_replacement_database(
    postgres_container: tuple[str, str], tmp_path: Path
) -> None:
    """Exercise the encrypted station archive, not only the underlying dump."""
    from civiccast.dr.station_backup import StationBackupService

    source_url, native_tools = postgres_container
    _run_migrations(source_url)
    source = create_engine(source_url, future=True)
    bind_engine(source)
    replacement = None
    try:
        _seed_two_tables(source)
        source_before = snapshot_tables(source)
        replacement_cli = create_fresh_postgres_database(
            database_url=source_url.replace("postgresql+psycopg://", "postgresql://"),
            database_name="station_archive_replacement",
            psql_command=_exec_prefix(native_tools, "psql"),
        )
        replacement_url = source_url.rsplit("/", 1)[0] + "/station_archive_replacement"
        _run_migrations(replacement_url)
        replacement = create_engine(replacement_url, future=True)
        _seed_two_tables(replacement)
        with replacement.begin() as connection:
            connection.execute(text("UPDATE civiccast.assets SET title='replacement sentinel'"))
        replacement_before = snapshot_tables(replacement)

        config = tmp_path / "source-config" / "app-platform-config.json"
        config.parent.mkdir()
        config.write_text('{"station_name":"Archived station"}', encoding="utf-8")
        replacement_config = tmp_path / "replacement-config" / config.name
        imported = {}
        service = StationBackupService(
            database_url=source_url,
            destination=tmp_path / "archives",
            config_paths=[config],
            credential_exporter=lambda: {"test-only-secret": b"disposable-secret"},
            credential_importer=imported.update,
            credential_destination="disposable replacement vault",
            pg_dump_command=_exec_prefix(native_tools, "pg_dump"),
            pg_dumpall_command=_exec_prefix(native_tools, "pg_dumpall"),
            pg_restore_command=_exec_prefix(native_tools, "pg_restore"),
        )
        artifact = service.create_backup("disposable archive passphrase")
        assert b"disposable-secret" not in artifact.path.read_bytes()
        assert service.verify_backup(artifact.path, "disposable archive passphrase").valid
        restore_args = {
            "target_database_url": replacement_cli,
            "target_paths": {str(config): replacement_config},
            "writers_stopped": True,
        }
        with pytest.raises(RuntimeError, match="acknowledgement"):
            service.restore_backup(artifact.path, "disposable archive passphrase", **restore_args)
        assert not imported and not replacement_config.exists()
        assert all(
            row.matched for row in _table_results(replacement_before, snapshot_tables(replacement))
        )

        result = service.restore_backup(
            artifact.path,
            "disposable archive passphrase",
            restore_local_station_settings=True,
            **restore_args,
        )
        assert result.database_restored and result.database_engine == "postgres"
        assert all(
            row.matched for row in _table_results(source_before, snapshot_tables(replacement))
        )
        assert all(row.matched for row in _table_results(source_before, snapshot_tables(source)))
        assert replacement_config.read_text(encoding="utf-8") == config.read_text(encoding="utf-8")
        assert imported == {"test-only-secret": b"disposable-secret"}
    finally:
        reset_engine()
        source.dispose()
        if replacement is not None:
            replacement.dispose()


@pytest.mark.parametrize("live_write", [False, True])
def test_full_backup_snapshot_binding_survives_aba_write_during_dump(
    postgres_container: tuple[str, str], tmp_path: Path, live_write: bool
) -> None:
    """Fix 3 (CC-WS2-003) ABA negative control: an INSERT-then-DELETE landing
    on the source database WHILE ``pg_dump`` is running (but AFTER the
    manifest's exported snapshot was taken) must NOT be visible in the dump,
    and the manifest must still describe exactly what the dump artifact
    contains -- proving the snapshot BINDING, not merely detecting drift
    after the fact the way the pre-fix before/after-diff approach could only
    ever do (an insert-then-delete nets out to zero row-count/checksum
    change, so a before/after diff would see nothing wrong even if the
    write leaked into -- or the delete leaked out of -- the dump).
    """
    postgres_url, container_id = postgres_container
    _run_migrations(postgres_url)
    engine = create_engine(postgres_url, future=True)
    bind_engine(engine)
    try:
        _seed_two_tables(engine)

        cli = container_cli() or "docker"
        # sh -c reads argv appended AFTER "--" as "$@" -- run_postgres_backup
        # appends its own flags (--host/--port/.../--snapshot=<id>/dbname)
        # AFTER this whole prefix list, so they land in "$@" and are threaded
        # through to the real pg_dump by `exec pg_dump "$@"`.
        sleepy_pg_dump = [
            cli,
            "exec",
            "-i",
            "-e",
            "PGPASSWORD=test",
            container_id,
            "sh",
            "-c",
            'sleep 3; exec pg_dump "$@"',
            "--",
        ]
        if container_id.startswith("native:"):
            sleepy_pg_dump = [
                sys.executable,
                "-c",
                "import subprocess,sys,time; time.sleep(3); "
                "raise SystemExit(subprocess.call(sys.argv[1:], "
                "stdout=sys.stdout.buffer, stderr=sys.stderr.buffer, "
                "creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)))",
                *_exec_prefix(container_id, "pg_dump"),
            ]
        command_url = (
            postgres_url.replace("postgresql+psycopg://", "postgresql://")
            if container_id.startswith("native:")
            else "postgresql://test:test@localhost:5432/test"
        )

        backup_dir = tmp_path / "backup"
        result_holder: dict[str, object] = {}

        def _run_backup() -> None:
            result_holder["manifest"] = run_full_backup(
                database_url=postgres_url,
                dest_dir=backup_dir,
                command_database_url=command_url,
                pg_dump_command=sleepy_pg_dump,
                pg_dumpall_command=_exec_prefix(container_id, "pg_dumpall"),
                require_quiescent=not live_write,
            )

        thread = threading.Thread(target=_run_backup)
        thread.start()
        time.sleep(1.0)  # let the snapshot export + "sleep 3" start before the ABA write
        aba_engine = create_engine(postgres_url, future=True)
        try:
            with aba_engine.begin() as conn:
                conn.execute(
                    text(
                        "INSERT INTO civiccast.assets (asset_id, title, state, manifest_url) "
                        "VALUES ('aba-ghost-asset', 'ABA ghost', 'validated', "
                        "'https://cdn.example/aba/master.m3u8')"
                    )
                )
            if not live_write:
                with aba_engine.begin() as conn:
                    conn.execute(
                        text("DELETE FROM civiccast.assets WHERE asset_id = 'aba-ghost-asset'")
                    )
        finally:
            aba_engine.dispose()
        thread.join(timeout=60)
        assert not thread.is_alive(), "backup thread did not finish -- pg_dump likely hung"

        manifest = result_holder["manifest"]
        assert isinstance(manifest, BackupManifest)
        assets_snapshot = next(t for t in manifest.tables if t.name == "assets")
        # The drill's own seeded asset only -- the ABA row must never be seen,
        # in either direction, by the exported snapshot.
        assert assets_snapshot.row_count == 1

        restore_cli_url = create_fresh_postgres_database(
            database_url=command_url,
            database_name="civiccast_backup_aba_restore",
            psql_command=_exec_prefix(container_id, "psql"),
        )
        run_postgres_restore(
            backup_dir / manifest.db_artifact,
            restore_cli_url,
            pg_restore_command=_exec_prefix(container_id, "pg_restore"),
        )
        restored_url = postgres_url.rsplit("/", 1)[0] + "/civiccast_backup_aba_restore"
        restored_engine = create_engine(restored_url, future=True)
        try:
            # The manifest and the just-restored artifact must agree exactly
            # -- proof the manifest and the dump describe ONE state, not two
            # states that happened to net out the same row count.
            restored_results = _table_results(manifest.tables, snapshot_tables(restored_engine))
            assert all(r.matched for r in restored_results), restored_results
        finally:
            restored_engine.dispose()
    finally:
        reset_engine()
        engine.dispose()
