# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Real backup tests: SQLite backup-API snapshot + media manifest + integrity manifest."""

from __future__ import annotations

from pathlib import Path

import pytest

from civiccast.dr.backup import (
    _parse_postgres_url,
    build_media_manifest,
    build_sqlite_engine,
    create_fresh_postgres_database,
    read_backup_manifest,
    run_full_backup,
    snapshot_tables,
    verify_backup_integrity,
    write_integrity_manifest,
)


def test_snapshot_matches_between_source_and_backup_copy(
    seeded_station_db: Path, tmp_path: Path
) -> None:
    """The whole drill hinges on this: source and backup-copy checksums must agree."""

    source_engine = build_sqlite_engine(seeded_station_db)
    source_snapshot = {t.name: t for t in snapshot_tables(source_engine)}
    source_engine.dispose()
    assert source_snapshot["assets"].row_count == 2
    assert source_snapshot["schedule_items"].row_count == 1
    assert source_snapshot["egress_configs"].row_count == 1

    manifest = run_full_backup(
        database_url=f"sqlite:///{seeded_station_db}", dest_dir=tmp_path / "backup"
    )
    backup_snapshot = {t.name: t for t in manifest.tables}
    for name in ("assets", "schedule_items", "egress_configs"):
        assert backup_snapshot[name].row_count == source_snapshot[name].row_count
        assert backup_snapshot[name].checksum_sha256 == source_snapshot[name].checksum_sha256


def test_snapshot_reuses_the_supplied_connection_for_metadata_and_rows(
    seeded_station_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = build_sqlite_engine(seeded_station_db)
    try:
        with engine.connect() as connection:

            def refuse_extra_connection() -> None:
                pytest.fail("snapshot opened a second connection outside the connection bound")

            monkeypatch.setattr(engine, "connect", refuse_extra_connection)
            tables = {table.name: table for table in snapshot_tables(engine, connection=connection)}
            assert tables["assets"].row_count == 2
            assert tables["schedule_items"].row_count == 1
    finally:
        engine.dispose()


def test_backup_is_not_a_raw_copy_of_a_live_writer(seeded_station_db: Path, tmp_path: Path) -> None:
    """The backup API must produce a valid, openable snapshot even with a concurrent writer.

    A raw ``shutil.copy2`` of a live SQLite file can copy a half-written page;
    the backup API takes its own read lock instead. This proves the artifact
    it produces opens cleanly and contains the expected tables.
    """

    manifest = run_full_backup(
        database_url=f"sqlite:///{seeded_station_db}", dest_dir=tmp_path / "backup"
    )
    engine = build_sqlite_engine(tmp_path / "backup" / manifest.db_artifact)
    names = {t.name for t in snapshot_tables(engine)}
    engine.dispose()
    assert {"assets", "schedule_items", "egress_configs"} <= names


def test_media_manifest_bounds_the_hashed_sample(tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    small = media_root / "small.ts"
    small.write_bytes(b"x" * 100)
    big = media_root / "big.ts"
    big.write_bytes(b"y" * 500_000)

    entries = build_media_manifest(media_root, sample_bytes=4096)
    by_path = {e.path: e for e in entries}

    assert by_path["small.ts"].size_bytes == 100
    assert by_path["small.ts"].sample_bytes == 100  # whole file, smaller than the bound

    assert by_path["big.ts"].size_bytes == 500_000
    assert by_path["big.ts"].sample_bytes <= 4096  # bounded — never the whole 500KB file


def test_media_manifest_detects_content_change(tmp_path: Path) -> None:
    media_root = tmp_path / "media"
    media_root.mkdir()
    f = media_root / "clip.ts"
    f.write_bytes(b"a" * 200_000 + b"ORIGINAL-TAIL")
    before = build_media_manifest(media_root, sample_bytes=4096)[0]

    f.write_bytes(b"a" * 200_000 + b"CHANGED-TAIL!")  # only the tail changed
    after = build_media_manifest(media_root, sample_bytes=4096)[0]

    assert before.sampled_sha256 != after.sampled_sha256


def test_parse_postgres_url_decodes_percent_encoded_credentials() -> None:
    """FALSIFICATION: a percent-encoded password (required by RFC 3986 for hosted/managed
    Postgres credentials containing '@', ':' or '/') must come out decoded, matching what
    SQLAlchemy's own URL parser does for the run_full_backup snapshot connection."""

    conn = _parse_postgres_url("postgresql://svc:p%40ss%3Aw%2Frd@db.host:5432/civiccast")
    assert conn["user"] == "svc"
    assert conn["password"] == "p@ss:w/rd"
    assert conn["dbname"] == "civiccast"


def test_snapshot_checksum_distinguishes_null_from_literal_none_string(tmp_path: Path) -> None:
    """FALSIFICATION: a real SQL NULL and the literal text "None" must not hash identically,
    or a restore drill would report matched=True for a table that actually changed."""

    from sqlalchemy import create_engine, text

    db_path = tmp_path / "null-check.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE widgets (id INTEGER PRIMARY KEY, note TEXT)"))
        conn.execute(text("INSERT INTO widgets (id, note) VALUES (1, NULL)"))
    null_checksum = snapshot_tables(engine)[0].checksum_sha256

    with engine.begin() as conn:
        conn.execute(text("UPDATE widgets SET note = 'None' WHERE id = 1"))
    literal_checksum = snapshot_tables(engine)[0].checksum_sha256

    engine.dispose()
    assert null_checksum != literal_checksum


def test_integrity_manifest_covers_every_backed_up_file(
    seeded_station_db: Path, tmp_path: Path
) -> None:
    dest_dir = tmp_path / "backup"
    run_full_backup(database_url=f"sqlite:///{seeded_station_db}", dest_dir=dest_dir)
    entries = write_integrity_manifest(dest_dir)
    members = {e.member for e in entries}
    on_disk = {
        str(p.relative_to(dest_dir))
        for p in dest_dir.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    assert members == on_disk
    assert len(entries) > 0


# ---------------------------------------------------------------------------
# <installer-path-audit MA-09> Integrity that actually re-hashes bytes.
#
# ``BackupRef.verified`` was ``bool(manifest.integrity)`` -- a NON-EMPTINESS
# check -- while the orchestrator gated on it and wrote the journal detail
# "hash + restore-drill spot check". ``_manifest_blob_hash`` folds the
# manifest's OWN recorded hashes, so both sides of any later tamper check came
# from the same manifest, and a grep across ``civiccast/`` found NO
# re-derivation anywhere in the product. A dump truncated after the manifest
# was written passed ``verified=True``, and the only thing that would catch it
# -- the drill -- restores the same bytes.
# ---------------------------------------------------------------------------


def test_a_faithful_backup_verifies_clean(seeded_station_db: Path, tmp_path: Path) -> None:
    dest = tmp_path / "backup"
    manifest = run_full_backup(
        database_url=f"sqlite:///{seeded_station_db}", dest_dir=dest, media_root=None
    )
    assert manifest.integrity, "this fixture must produce integrity entries to be meaningful"
    assert verify_backup_integrity(dest, manifest) == []


def test_a_truncated_artifact_is_reported_even_though_the_manifest_is_intact(
    seeded_station_db: Path, tmp_path: Path
) -> None:
    """THE finding: the manifest still says what it always said."""
    dest = tmp_path / "backup"
    manifest = run_full_backup(
        database_url=f"sqlite:///{seeded_station_db}", dest_dir=dest, media_root=None
    )
    artifact = dest / manifest.db_artifact
    artifact.write_bytes(artifact.read_bytes()[: len(artifact.read_bytes()) // 2])

    errors = verify_backup_integrity(dest, manifest)
    assert errors, "a truncated dump must not verify"
    assert manifest.db_artifact in errors[0]
    assert "does not match the manifest" in errors[0]
    # ...and the old non-emptiness check would still have said "verified".
    assert bool(manifest.integrity) is True


def test_a_missing_member_is_reported(seeded_station_db: Path, tmp_path: Path) -> None:
    dest = tmp_path / "backup"
    manifest = run_full_backup(
        database_url=f"sqlite:///{seeded_station_db}", dest_dir=dest, media_root=None
    )
    (dest / manifest.db_artifact).unlink()
    errors = verify_backup_integrity(dest, manifest)
    assert errors and "missing from the backup directory" in errors[0]


def test_an_empty_integrity_block_is_itself_a_finding(
    seeded_station_db: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "backup"
    manifest = run_full_backup(
        database_url=f"sqlite:///{seeded_station_db}", dest_dir=dest, media_root=None
    )
    stripped = manifest.model_copy(update={"integrity": []})
    errors = verify_backup_integrity(dest, stripped)
    assert errors and "nothing about these bytes has been verified" in errors[0]


def test_the_manifest_round_trips_through_the_reader(
    seeded_station_db: Path, tmp_path: Path
) -> None:
    dest = tmp_path / "backup"
    written = run_full_backup(
        database_url=f"sqlite:///{seeded_station_db}", dest_dir=dest, media_root=None
    )
    assert read_backup_manifest(dest).backup_id == written.backup_id


# ---------------------------------------------------------------------------
# <installer-path-audit MN-09> DROP DATABASE ... WITH (FORCE) runs against the
# PRODUCTION cluster, and nothing refused when the name equalled the source
# database's own -- so a station provisioned with the default drill name would
# have lost production data to a drill.
# ---------------------------------------------------------------------------


def test_dropping_the_connection_urls_own_database_is_refused_by_default() -> None:
    with pytest.raises(RuntimeError, match="refusing to DROP DATABASE"):
        create_fresh_postgres_database(
            database_url="postgresql://u:p@127.0.0.1:5432/civiccast_drill_restore",
            database_name="civiccast_drill_restore",
        )


def test_the_guard_can_be_opted_out_of_deliberately(monkeypatch) -> None:
    """The D3 rollback restore is the one caller that MUST drop the live
    database -- under the held interlock, with a verified backup in hand."""
    import civiccast.dr.backup as backup_module

    class _Result:
        returncode = 0
        stdout = b""
        stderr = b""

    seen: dict[str, object] = {}

    def _fake_spawn(argv, **kwargs):  # type: ignore[no-untyped-def]
        seen["argv"] = argv
        return _Result()

    monkeypatch.setattr(backup_module, "_spawn_pg_tool", _fake_spawn)
    monkeypatch.setattr(
        backup_module,
        "read_database_locale",
        lambda **kwargs: backup_module.DatabaseLocale("UTF8", "en_US.UTF-8", "en_US.UTF-8"),
    )

    url = create_fresh_postgres_database(
        database_url="postgresql://u:p@127.0.0.1:5432/civiccast",
        database_name="civiccast",
        allow_dropping_the_connection_url_database=True,
    )
    assert url.endswith("/civiccast")
    argv = " ".join(str(part) for part in seen["argv"])
    # <installer-path-audit MN-08> The clone must copy the SOURCE's encoding
    # and collation, not inherit template1's. On a Windows-installed cluster
    # template1 is commonly SQL_ASCII/C while the product's database is UTF-8,
    # and snapshot_tables' primary-key ORDER BY then differs between source
    # and copy -- an unexplained checksum mismatch on an otherwise perfect
    # backup, which (because the drill gates the pre-upgrade backup) fails the
    # whole upgrade.
    assert "TEMPLATE template0" in argv
    assert "ENCODING 'UTF8'" in argv
    assert "LC_COLLATE 'en_US.UTF-8'" in argv


def test_a_win1252_source_locale_is_carried_through_not_replaced_by_the_utf8_fallback(
    monkeypatch,
) -> None:
    """The encoding branch's addendum: a source database provisioned before the
    initdb UTF8 fix (WIN1252, the OS-codepage default on Windows) must be
    CLONED as WIN1252 by the drill/restore path, not silently upgraded to the
    ``_FALLBACK_DATABASE_LOCALE`` UTF8 constant -- that fallback is for an
    UNREADABLE row only (see its updated docstring), never a substitute for a
    successfully measured non-UTF8 locale. Goes through the REAL
    read_database_locale (via a fake _spawn_pg_tool returning psql's own
    tuples-only/no-align/pipe-separated output shape), not a monkeypatched
    stand-in for read_database_locale itself, so this proves the measurement
    path end to end."""
    import civiccast.dr.backup as backup_module

    class _LocaleRowResult:
        returncode = 0
        stdout = b"WIN1252|French_France.1252|French_France.1252\n"
        stderr = b""

    class _CreateResult:
        returncode = 0
        stdout = b""
        stderr = b""

    seen: dict[str, object] = {}

    def _fake_spawn(argv, *, tool, **kwargs):  # type: ignore[no-untyped-def]
        if tool == "psql (source database locale)":
            return _LocaleRowResult()
        seen["create_argv"] = argv
        return _CreateResult()

    monkeypatch.setattr(backup_module, "_spawn_pg_tool", _fake_spawn)

    url = create_fresh_postgres_database(
        database_url="postgresql://u:p@127.0.0.1:5432/civiccast",
        database_name="civiccast_drill_restore",
    )
    assert url.endswith("/civiccast_drill_restore")
    argv = " ".join(str(part) for part in seen["create_argv"])
    assert "ENCODING 'WIN1252'" in argv, "the measured source locale must win over the fallback"
    assert "UTF8" not in argv
    assert "LC_COLLATE 'French_France.1252'" in argv


def test_an_unreadable_source_locale_falls_back_rather_than_failing(monkeypatch) -> None:
    import civiccast.dr.backup as backup_module

    def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise OSError("psql not found")

    monkeypatch.setattr(backup_module, "_spawn_pg_tool", _boom)
    locale = backup_module.read_database_locale(
        database_url="postgresql://u:p@127.0.0.1:5432/civiccast",
        source_database_name="civiccast",
    )
    assert locale == backup_module._FALLBACK_DATABASE_LOCALE


def test_a_hostile_source_database_name_never_reaches_the_ddl(monkeypatch) -> None:
    import civiccast.dr.backup as backup_module

    spawned: list[object] = []
    monkeypatch.setattr(backup_module, "_spawn_pg_tool", lambda *a, **k: spawned.append(a) or None)
    locale = backup_module.read_database_locale(
        database_url="postgresql://u:p@127.0.0.1:5432/civiccast",
        source_database_name="civiccast'; DROP DATABASE civiccast; --",
    )
    assert locale == backup_module._FALLBACK_DATABASE_LOCALE
    assert spawned == [], "a name that fails validation must not reach psql at all"
