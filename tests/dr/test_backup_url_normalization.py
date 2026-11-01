# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Regression coverage for civiccast.dr.backup's Postgres engine construction
(the FINAL unnormalized engine call, Sandbox run 16 row-4b: ``run_full_backup``
crashed ``No module named 'psycopg2'`` on the backup step, AFTER
writers_drained -- beta BLOCKER #51 normalized native/ + alerting/ but never
grepped civiccast/dr/, so backup.py's two ``create_engine(database_url)``
calls kept the bare ``postgresql://`` scheme SQLAlchemy maps to the
uninstalled psycopg2 dialect).

Same call-boundary pattern as ``tests/native/test_upgrade_seams.py``: assert
at the call boundary this module owns (``sqlalchemy.create_engine``), never
internals, and never touch a real database. Both engine calls in
``run_full_backup``'s Postgres branch are covered:

* the snapshot engine (``engine_for_snapshot or create_engine(database_url)``)
* the post-dump quiescence-check engine (``create_engine(database_url)``)
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import sqlalchemy

from civiccast.dr import backup
from civiccast.dr.models import TableSnapshot

_BARE_POSTGRES_URL = "postgresql://civiccast:tr0ub4dor@127.0.0.1:5432/civiccast"


class _StubResult:
    def scalar_one(self) -> str:
        return "fake-exported-snapshot-id"


class _StubConnection:
    def execution_options(self, **_kwargs: object) -> _StubConnection:
        return self

    def execute(self, *_args: object, **_kwargs: object) -> _StubResult:
        return _StubResult()

    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


class _StubEngine:
    def __init__(self) -> None:
        self.connect_calls = 0

    def connect(self) -> _StubConnection:
        self.connect_calls += 1
        return _StubConnection()

    def dispose(self) -> None:
        return None


def _fake_run_postgres_backup(
    *, database_url: str, dest_dir: Path, pg_dump_command: object = None, snapshot_id: object = None
) -> Path:
    del database_url, pg_dump_command, snapshot_id
    artifact = dest_dir / "database.pgdump"
    artifact.write_bytes(b"fake-dump")
    return artifact


def _fake_run_postgres_globals_backup(
    *, database_url: str, dest_dir: Path, pg_dumpall_command: object = None
) -> Path:
    del database_url, pg_dumpall_command
    artifact = dest_dir / "globals.sql"
    artifact.write_bytes(b"fake-globals")
    return artifact


def test_run_full_backup_normalizes_bare_postgresql_scheme_for_snapshot_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The FIRST engine construction in the Postgres branch (the snapshot
    engine, built when the caller supplies no ``engine_for_snapshot``) must
    hand ``create_engine`` a ``postgresql+psycopg://`` URL, never the bare
    ``postgresql://`` scheme -- RED today: ``run_full_backup`` passes
    ``database_url`` straight through unchanged."""

    captured: dict[str, str] = {}

    def _fake_create_engine(url: str, *args: object, **kwargs: object) -> _StubEngine:
        captured["url"] = url
        return _StubEngine()

    monkeypatch.setattr(sqlalchemy, "create_engine", _fake_create_engine)
    monkeypatch.setattr(backup, "snapshot_tables", lambda *a, **kw: [])
    monkeypatch.setattr(backup, "run_postgres_backup", _fake_run_postgres_backup)
    monkeypatch.setattr(backup, "run_postgres_globals_backup", _fake_run_postgres_globals_backup)

    backup.run_full_backup(
        database_url=_BARE_POSTGRES_URL,
        dest_dir=tmp_path / "dest",
    )

    assert captured["url"].startswith("postgresql+psycopg://")
    assert "tr0ub4dor" in captured["url"]  # password must survive, not be corrupted


def test_run_full_backup_normalizes_bare_postgresql_scheme_for_post_check_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SECOND engine construction (the post-dump quiescence-check engine)
    must ALSO hand ``create_engine`` a normalized URL. ``engine_for_snapshot``
    is supplied here so the first (already-covered) call site is bypassed
    entirely (``engine_for_snapshot or create_engine(...)``) and this test
    isolates the second one."""

    captured: dict[str, str] = {}

    def _fake_create_engine(url: str, *args: object, **kwargs: object) -> _StubEngine:
        captured["url"] = url
        return _StubEngine()

    monkeypatch.setattr(sqlalchemy, "create_engine", _fake_create_engine)
    monkeypatch.setattr(backup, "snapshot_tables", lambda *a, **kw: [])
    monkeypatch.setattr(backup, "run_postgres_backup", _fake_run_postgres_backup)
    monkeypatch.setattr(backup, "run_postgres_globals_backup", _fake_run_postgres_globals_backup)

    backup.run_full_backup(
        database_url=_BARE_POSTGRES_URL,
        dest_dir=tmp_path / "dest",
        engine_for_snapshot=_StubEngine(),
    )

    assert captured["url"].startswith("postgresql+psycopg://")
    assert "tr0ub4dor" in captured["url"]


def test_run_full_backup_leaves_explicit_driver_scheme_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit (if presently unsupported) driver choice always wins over
    normalization -- same guarantee as the sibling upgrade-seams coverage."""

    explicit_url = "postgresql+psycopg2://civiccast:secret@127.0.0.1:5432/civiccast"
    captured: dict[str, str] = {}

    def _fake_create_engine(url: str, *args: object, **kwargs: object) -> _StubEngine:
        captured["url"] = url
        return _StubEngine()

    monkeypatch.setattr(sqlalchemy, "create_engine", _fake_create_engine)
    monkeypatch.setattr(backup, "snapshot_tables", lambda *a, **kw: [])
    monkeypatch.setattr(backup, "run_postgres_backup", _fake_run_postgres_backup)
    monkeypatch.setattr(backup, "run_postgres_globals_backup", _fake_run_postgres_globals_backup)

    backup.run_full_backup(
        database_url=explicit_url,
        dest_dir=tmp_path / "dest",
    )

    assert captured["url"] == explicit_url


@pytest.mark.parametrize("changed", [False, True])
def test_quiescence_scan_can_outlast_connection_ceiling_without_waiving_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed: bool
) -> None:
    """A connected, slow fingerprint is valid; changed contents still refuse backup."""
    monkeypatch.setattr(backup, "_BACKUP_CONNECT_CEILING_SECONDS", 0.1)
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *a, **kw: _StubEngine())
    monkeypatch.setattr(backup, "run_postgres_backup", _fake_run_postgres_backup)
    monkeypatch.setattr(backup, "run_postgres_globals_backup", _fake_run_postgres_globals_backup)
    calls = 0

    def snapshot(engine: object, *, connection: object = None) -> list[TableSnapshot]:
        nonlocal calls
        calls += 1
        if calls == 2:
            time.sleep(0.2)
        checksum = "b" * 64 if calls == 2 and changed else "a" * 64
        return [TableSnapshot(name="history", row_count=1000, checksum_sha256=checksum)]

    monkeypatch.setattr(backup, "snapshot_tables", snapshot)
    if changed:
        with pytest.raises(RuntimeError, match="changed"):
            backup.run_full_backup(database_url=_BARE_POSTGRES_URL, dest_dir=tmp_path / "dest")
    else:
        manifest = backup.run_full_backup(
            database_url=_BARE_POSTGRES_URL, dest_dir=tmp_path / "dest"
        )
        assert manifest.tables[0].row_count == 1000
    assert calls == 2
