# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Startup schema-currency check (audit ENG-004 / walkthrough F-001).

A deploy that restarts the server on new code WITHOUT running migrations
used to 500 the affected endpoints while every dashboard stayed green (it
happened twice during the CA-8 night). The app must self-diagnose: compare
the database's alembic revision against the code's expected head at
startup, log an ERROR on drift, and surface the state on /health.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from civiccast.schema_check import (
    SchemaStatus,
    check_schema_currency,
    evaluate_schema_currency,
    expected_migration_head,
    read_db_revision,
)


class TestEvaluate:
    def test_matching_revision_is_current(self) -> None:
        status = evaluate_schema_currency("0037_asset_meeting_body", "0037_asset_meeting_body")
        assert status == SchemaStatus(
            state="current",
            db_revision="0037_asset_meeting_body",
            expected_head="0037_asset_meeting_body",
        )

    def test_stale_revision_is_behind(self) -> None:
        status = evaluate_schema_currency("0036_sdi_relay_device", "0037_asset_meeting_body")
        assert status.state == "behind"
        assert status.db_revision == "0036_sdi_relay_device"

    def test_missing_revision_is_behind(self) -> None:
        # An empty alembic_version table = never migrated.
        assert evaluate_schema_currency(None, "0037_asset_meeting_body").state == "behind"


def test_expected_head_matches_the_single_migration_head() -> None:
    # Repo-global single head; advance with every migration (the live
    # Postgres full-chain test derives the same head). 0057_underwriting_spots
    # adds the S24 underwriting / sponsorship-spot management tables on top of
    # 0055_asrun_and_epg (S23 as-run / proof-of-performance + EPG-export
    # configs) — the 0056 slot is RESERVED for S21 scheduled-recording per
    # RECONCILIATION D17 + the W-8 reconciliation footer; when S21 lands its
    # migration sequences after 0055 alongside 0057 and an Alembic merge
    # revision unifies the two heads. 0054_custom_metadata_fields (S22) /
    # 0053_ai_model_configuration (S13) / 0052_secondary_audio (S11) /
    # 0051_public_safety_eas (S11c) / 0050_caption_proof_samples (S11a) /
    # 0049_per_sink_loudness (S11b) are upstream.
    # S21 (scheduled recording) lands as a SIBLING off 0055 (the long-reserved
    # 0056 slot), creating a brief two-headed state (0056 + 0059) that the
    # merge revision 0060_recording_paywall_merge unifies. After the merge,
    # the chain shape is:
    #     0054 → 0055 ─┬→ 0056 ─────┐
    #                  │             ↓
    #                  └→ 0057 → 0058 → 0059 → 0060 (HEAD)
    # The head is 0060_recording_paywall_merge — the merge revision itself.
    # Every S18 parity gap is now closed on disk.
    # 0061_control_room_mode_gate (3.1 LPM control-room proof) landed next.
    # 0062_media_integrity_columns (CivicCast 4.0 media-library-hardening,
    # scope item 5): content_hash, thumbnail_path, file_status,
    # file_status_checked_at on assets.
    # 0063_producer_ops (item 23: producer/volunteer/equipment operations)
    # chains after 0062_media_integrity_columns.
    # 0064_control_room_health_and_versioning (item 7: control-room device
    # health + cue versioning) chains after 0063_producer_ops.
    # 0065_recording_dropout_fields (item 6: recording/ingest hardening —
    # mid-recording source-dropout tracking on recording_jobs) chains after
    # 0064_control_room_health_and_versioning.
    # 0068_migrate_batches (agenda-import provenance tables, 0.3.0 Phase 1)
    # chains after 0065_recording_dropout_fields.
    # 0070_grandfather_scheduled_to_published (Commit-to-Air enforcement,
    # owner decision 2026-07-08) chains after 0068_migrate_batches. (0069 is
    # reserved by an in-flight control_room branch not yet merged into this
    # chain.) 0074_caption_review_audio_evidence (same owner decision; widens the
    # schedule_items_no_overlap EXCLUDE to also block on published items)
    # chains after 0070. 0075_offline_caption_jobs (CivicCast One keystone K3
    # — the durable offline caption job for published recordings) chains
    # after 0074. 0076_analytics_viewership (S14 — durable
    # viewership_events/viewership_rollups/analytics_report_snapshots,
    # promoting the analytics-events.json store to Postgres) chains after
    # 0075. 0078_agenda_item_confidence (product-hole fix: adds
    # agenda_items.confidence for the PDF-agenda-import heuristic) chains
    # after 0076_analytics_viewership. Renumbered from its original 0076
    # after PR #20 merged first and independently claimed that slot; 0077
    # is reserved for feat/s7-media-lifecycle. 0079_media_lifecycle (S7
    # media lifecycle & readiness: the five net-new S7 tables +
    # asset_archive_proofs + media_lifecycle_audit_log, plus
    # assets.legal_hold/legal_hold_reason) is rechained after
    # 0078_agenda_item_confidence (rather than the original 0076) so it
    # lands after the already-merged 0078. 0080_watch_folder_daemon (S7
    # watch-folder poll daemon: poll_interval_seconds/processed_file_mode/
    # health_status/degraded_*/last_poll_at/last_ingest_at columns on
    # watch_folder_configs + the new watch_folder_file_state ledger table)
    # chains after 0079_media_lifecycle and is the current head.
    # 0082_egress_graphics_overlay (async summary generation job -- field
    # evidence, candidate #17: a legitimate multi-minute CPU-only summary
    # generation must not block or discard an HTTP request; see
    # civiccast/summary/job.py) chains after 0080_watch_folder_daemon.
    # 0083_caption_review_language (recorded-Spanish captions: a language
    # column on caption_review_items so English transcription and Spanish
    # translation are reviewed as two separate passes on a shared asset)
    # chains after 0082_egress_graphics_overlay. 0086_live_source_probe_state
    # (WP-07 / audit ENG-003: probe_state/probe_observed_at/probe_detail/
    # probe_error_code/probe_last_success_at/row_version on live_sources, so
    # readiness is an observed fact instead of an assumption) chains after
    # 0083_caption_review_language -- WP-05's 0085 is parked by owner
    # decision and will not land, and 0084 never materialized, so 0083 was
    # the sole other head when 0086 re-parented onto it. 0087 adds asset
    # retention terms and 0088 seeds live-caption health alert rules. 0089 adds
    # persisted, explicitly unverified remote-guest media-control requests.
    # 0090 indexes consumed egress commands for bounded history pruning.
    # Beta 19 advances through 0092 notification delivery outcomes, 0093 board
    # audio gain and 0094 board live-video zones, retaining one exact head.
    assert expected_migration_head() == "0099_audio_description_model"


def test_expected_head_does_not_depend_on_current_working_directory(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    expected_migration_head.cache_clear()
    monkeypatch.chdir(tmp_path)
    try:
        assert expected_migration_head() == "0099_audio_description_model"
    finally:
        expected_migration_head.cache_clear()


def test_schema_check_reports_current_from_non_repo_working_directory(
    tmp_path, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    import sqlite3

    db_path = tmp_path / "civiccast.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE alembic_version (version_num VARCHAR(255) NOT NULL)")
        conn.execute(
            "INSERT INTO alembic_version (version_num) VALUES (?)",
            ("0099_audio_description_model",),
        )
        conn.commit()

    expected_migration_head.cache_clear()
    monkeypatch.chdir(tmp_path)
    try:
        status = check_schema_currency(f"sqlite:///{db_path.as_posix()}")
    finally:
        expected_migration_head.cache_clear()

    assert status == SchemaStatus(
        state="current",
        db_revision="0099_audio_description_model",
        expected_head="0099_audio_description_model",
    )


def test_read_db_revision_normalizes_bare_postgresql_scheme(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """beta BLOCKER #51 regression: read_db_revision is the SHARED call site
    for both the startup schema-currency check (civiccast/app.py) and the D3
    upgrade engine's schema_revision seam. Its create_engine call must
    receive the NORMALIZED (+psycopg) url, not the bare ``postgresql://``
    scheme the installer persists (SQLAlchemy maps that to the uninstalled
    psycopg2 dialect -- ADR 0008 ships psycopg v3 only). Asserts at the call
    boundary (monkeypatched ``sqlalchemy.create_engine``), never internals --
    no real DB round trip."""

    import sqlalchemy

    captured: dict[str, str] = {}

    class _FakeConnection:
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *args):  # type: ignore[no-untyped-def]
            return False

        def execute(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            # No alembic_version table in either namespace -- matches the
            # existing "table absent" except/continue path, ending in None.
            raise RuntimeError("no such table: alembic_version")

        def rollback(self) -> None:
            pass

    class _FakeEngine:
        def connect(self):  # type: ignore[no-untyped-def]
            return _FakeConnection()

        def dispose(self) -> None:
            pass

    def _fake_create_engine(url, **kwargs):  # type: ignore[no-untyped-def]
        captured["url"] = url
        return _FakeEngine()

    monkeypatch.setattr(sqlalchemy, "create_engine", _fake_create_engine)

    result = read_db_revision("postgresql://civiccast:tr0ub4dor@127.0.0.1:5432/civiccast")

    assert result is None
    assert captured["url"].startswith("postgresql+psycopg://")
    assert "tr0ub4dor" in captured["url"]  # password must survive, not be corrupted


def test_read_db_revision_raises_database_missing_error_on_invalid_catalog_name(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Task #55 (audit-lite FINDING-002): read_db_revision's own
    ``engine.connect()`` -- the D3 engine's actual first DB touch via
    ``seams.schema_revision()`` -- previously let a missing-target-database
    ``OperationalError`` (psycopg's InvalidCatalogName / SQLSTATE 3D000)
    reach ``civiccast.native.upgrade.orchestrator.run_upgrade``'s unguarded
    ``journal.pre_schema_revision`` call completely unclassified (a raw
    traceback, not BLOCKER #52's actionable message). It must now raise
    ``civiccast.db.guarded_connect.DatabaseMissingError``, the SAME
    classification :mod:`civiccast.native.upgrade.pg_lifecycle`'s
    reachability pre-check already applies."""

    import psycopg.errors as psycopg_errors
    import sqlalchemy
    from sqlalchemy.exc import OperationalError

    from civiccast.db.guarded_connect import DatabaseMissingError

    class _FailingEngine:
        def connect(self):  # type: ignore[no-untyped-def]
            orig = psycopg_errors.InvalidCatalogName('database "civiccast" does not exist')
            raise OperationalError("connect", {}, orig)

        def dispose(self) -> None:
            pass

    monkeypatch.setattr(sqlalchemy, "create_engine", lambda url, **kwargs: _FailingEngine())

    with pytest.raises(DatabaseMissingError, match="does not exist"):
        read_db_revision("postgresql://civiccast:tr0ub4dor@127.0.0.1:5432/civiccast")


def test_read_db_revision_reraises_ordinary_connect_failures_unclassified(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """A connect failure that is NOT a missing-database condition (refused,
    auth failure, ...) must propagate exactly as before -- never
    misclassified as DatabaseMissingError, never swallowed."""

    import sqlalchemy
    from sqlalchemy.exc import OperationalError

    class _RefusingEngine:
        def connect(self):  # type: ignore[no-untyped-def]
            raise OperationalError("connect", {}, RuntimeError("connection refused"))

        def dispose(self) -> None:
            pass

    monkeypatch.setattr(sqlalchemy, "create_engine", lambda url, **kwargs: _RefusingEngine())

    with pytest.raises(OperationalError):
        read_db_revision("postgresql://civiccast:tr0ub4dor@127.0.0.1:5432/civiccast")


def test_read_db_revision_bounded_even_when_connect_hangs_past_its_own_timeout(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    """Task #55 (audit-lite FINDING-002): the ``connect_timeout=10`` hint
    passed to ``create_engine`` was already measured (BLOCKER #52,
    ``civiccast.native.upgrade.pg_lifecycle``) NOT to reliably bound a
    blackholed connect on this platform -- read_db_revision now enforces its
    OWN independent hard ceiling (``_READ_DB_REVISION_CEILING_SECONDS``,
    shrunk here so the test stays fast) via
    ``civiccast.db.guarded_connect.run_bounded``, proven directly with a
    connect that never returns at all."""

    import threading
    import time

    import sqlalchemy

    import civiccast.schema_check as schema_check_module

    monkeypatch.setattr(schema_check_module, "_READ_DB_REVISION_CEILING_SECONDS", 0.3)

    release = threading.Event()

    class _HangingConnection:
        def __enter__(self):  # type: ignore[no-untyped-def]
            release.wait(timeout=5.0)
            return self

        def __exit__(self, *args):  # type: ignore[no-untyped-def]
            return False

    class _HangingEngine:
        def connect(self):  # type: ignore[no-untyped-def]
            return _HangingConnection()

        def dispose(self) -> None:
            pass

    monkeypatch.setattr(sqlalchemy, "create_engine", lambda url, **kwargs: _HangingEngine())

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        read_db_revision("postgresql://u:p@127.0.0.1:1/db")
    elapsed = time.monotonic() - started

    release.set()  # let the abandoned worker thread unblock so it can exit
    assert elapsed < 2.0, f"expected the hard ceiling (0.3s) to bound the wait, took {elapsed:.1f}s"


def test_health_reports_schema_not_configured_without_a_database(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The schema state reaches /health -- and drags readiness down with it.

    This test used to assert ``status == "healthy"`` alongside
    ``schema == "not-configured"``, which encoded walkthrough finding W-1 as
    the expected contract (gate finding T1): fixing /health would have broken a
    named, intentional test, inviting a future engineer under CI pressure to
    "fix" it by reverting. The readiness contract itself is pinned in
    tests/test_health_readiness.py; this test keeps its original job of proving
    the startup check's verdict is the one /health reports.
    """

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("CIVICCAST_ALLOW_EPHEMERAL_STORES", "1")
    monkeypatch.setenv("CIVICCAST_AUTH_ACK", "1")
    from civiccast.app import create_app

    # The check runs at LIFESPAN startup (create_app must not touch the
    # DB), so the client must enter the lifespan context.
    with TestClient(create_app()) as client:
        response = client.get("/health")
    body = response.json()
    assert response.status_code == 200  # liveness is not gated on readiness
    assert body["schema"] == "not-configured"
    assert body["status"] == "degraded"
