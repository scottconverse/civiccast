# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""Real-Postgres tests for the v0.4 live-broadcast spine migration.

Sprint 0.4 Slice 1 Commit 3. Proves migration 0007_live_sessions:

- ``alembic upgrade head`` creates the three live tables in the
  ``civiccast`` schema.
- The DB-level CHECK constraints reject invalid state / source_type
  values.
- ``alembic downgrade 0006_widen_asset_state_check`` drops the three
  live tables cleanly.
- ``alembic upgrade head`` is a single graph (one head) even though
  the new migration lives under ``civiccast/live/migrations/versions/``
  rather than ``civiccast/schedule/migrations/versions/``.

Skip-marked when Docker / testcontainers is unavailable, matching the
pattern in ``tests/schedule/test_real_postgres.py``.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

# Import both modules so SA models register against Base.metadata
# before any introspection.
import civiccast.live.models
import civiccast.schedule.models  # noqa: F401
from tests.support.docker_engine import docker_engine_available

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def _docker_available() -> bool:
    return docker_engine_available()


_TESTCONTAINERS_OK: bool
try:
    from testcontainers.postgres import PostgresContainer  # type: ignore[import-untyped]

    _TESTCONTAINERS_OK = True
except ImportError:
    _TESTCONTAINERS_OK = False
    PostgresContainer = None  # type: ignore[misc,assignment]


_SKIP_REASON = "Docker unavailable; live-spine real-Postgres test cannot run"


def _skip_if_no_postgres() -> None:
    import os

    if not _TESTCONTAINERS_OK or not _docker_available():
        if os.environ.get("CIVICCAST_RUN_POSTGRES_TESTS"):
            pytest.fail("Postgres tests required by env but Docker unavailable")
        pytest.skip(_SKIP_REASON)


@pytest.fixture(scope="module")
def postgres_url():
    from tests._postgres_harness import fresh_database_from_env

    with fresh_database_from_env() as external_url:
        if external_url is not None:
            yield external_url
            return
        _skip_if_no_postgres()
        container = PostgresContainer("postgres:17", driver="psycopg")
        container.start()
        try:
            yield container.get_connection_url()
        finally:
            container.stop()


def _make_cfg(database_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


class TestRealPostgresLiveSpineMigration:
    """Locks: 0007_live_sessions creates the three live tables in the
    ``civiccast`` schema, and downgrade drops them cleanly. Single
    Alembic head despite the per-module migration directory layout."""

    def test_upgrade_head_creates_three_live_tables(self, postgres_url) -> None:
        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)
        try:
            with eng.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'civiccast' "
                        "AND table_name IN "
                        "('live_sessions', 'live_sources', 'recording_targets') "
                        "ORDER BY table_name"
                    )
                ).fetchall()
            names = [r[0] for r in rows]
            assert names == [
                "live_sessions",
                "live_sources",
                "recording_targets",
            ], f"Expected three live tables; found {names}"
        finally:
            eng.dispose()

    def test_live_sessions_state_check_rejects_invalid_state(self, postgres_url) -> None:
        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)
        try:
            # Direct SQL bypassing the SA model so the DB-level CHECK
            # is what fails the insert.
            with eng.connect() as conn, pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO civiccast.live_sessions "
                        "(live_session_id, channel_id, title, state) "
                        "VALUES "
                        "('badstate-1', 'gov-ch12', 'X', 'paused')"
                    )
                )
                conn.commit()
        finally:
            eng.dispose()

    def test_live_sources_source_type_check_rejects_invalid(self, postgres_url) -> None:
        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)
        try:
            with eng.connect() as conn, pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO civiccast.live_sources "
                        "(live_source_id, channel_id, name, "
                        "source_type, endpoint_url) "
                        "VALUES "
                        "('badtype-1', 'gov-ch12', 'X', "
                        "'webrtc', 'webrtc://x')"
                    )
                )
                conn.commit()
        finally:
            eng.dispose()

    def test_live_sources_accepts_all_four_types(self, postgres_url) -> None:
        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)
        try:
            from civiccast.live.models import LiveSource

            with Session(bind=eng) as sess:
                for source_type in ("rtmp", "rtsp", "ndi", "srt"):
                    sess.add(
                        LiveSource(
                            live_source_id=f"room-a-{source_type}",
                            channel_id="gov-ch12",
                            name=f"Room A {source_type.upper()}",
                            source_type=source_type,
                            endpoint_url=f"{source_type}://camera/live",
                        )
                    )
                sess.commit()

                count = sess.scalar(
                    text(
                        "SELECT count(*) FROM civiccast.live_sources "
                        "WHERE live_source_id LIKE 'room-a-%'"
                    )
                )
                assert count == 4
        finally:
            eng.dispose()

    def test_downgrade_drops_live_tables(self, postgres_url) -> None:
        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)
        try:
            # This module's ``postgres_url`` fixture is module-scoped: every
            # test in this file shares one throwaway Postgres DB. Other
            # tests (e.g. TestRealPostgresFinalizationIdempotency's
            # concurrent-finalize race) create assets with
            # ``source_live_session_id`` set and ``live_session_events``
            # rows, and do not delete them (they are asserting on the
            # rows, not testing downgrade). Under a fixed file order this
            # test happens to run before those tests plant that data; under
            # pytest-randomly it can run after, and migration 0008's data
            # guards would then refuse to downgrade past 0008 on rows this
            # test neither created nor has any interest in. This test's
            # only claim is "downgrade drops the three live tables" — clear
            # the guarded data first so the test is order-independent
            # without weakening the migration's guards.
            with eng.begin() as conn:
                conn.execute(text("DELETE FROM civiccast.live_session_events"))
                conn.execute(
                    text(
                        "UPDATE civiccast.assets SET source_live_session_id = NULL "
                        "WHERE source_live_session_id IS NOT NULL"
                    )
                )
            command.downgrade(cfg, "0006_widen_asset_state_check")
            with eng.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'civiccast' "
                        "AND table_name IN "
                        "('live_sessions', 'live_sources', 'recording_targets')"
                    )
                ).fetchall()
            assert rows == [], f"Expected zero live tables after downgrade; found {rows}"
        finally:
            eng.dispose()
            # Restore head for session-scoped container cleanup.
            cfg2 = _make_cfg(postgres_url)
            command.upgrade(cfg2, "head")


class TestSingleAlembicHead:
    """Locks: the per-module migration layout produces exactly one
    Alembic head. Two heads would indicate accidental fork in the
    revision graph (e.g., 0007 with the wrong down_revision).

    HABIT NOTE (Tests lens of the 5-lens self-audit, 2026-05-12 after
    `14d1018` shipped a migration without updating the hardcoded head
    string below and the CI gate caught it):

    The string-equality assertion below is a deliberate tripwire. It
    fires whenever the live-module Alembic head advances. The fire is
    correct -- the test is doing its job. The fix is also mechanical:
    update the string + the inline comment to the new head's revision
    id. The lesson is in the 5-lens audit, not the test:

    **Any migration that advances or renames the head MUST grep the
    repo for the OUTGOING head string (e.g. `"0009_live_sources_index"`)
    before commit. Hits in `tests/` are tripwires that need the same
    diff; hits in `civiccast/` migrations are load-bearing
    `down_revision` chains that must not be touched.** A 60-second
    grep is the audit step that prevents this whole class of failure
    from reaching CI.

    Each rename leaves a one-line trail below ("Updated to ... in
    <commit-sha>") so future grep'ers see the history of advances and
    don't have to git-blame to reconstruct it."""

    def test_alembic_heads_returns_single_head(self) -> None:
        # Graph inspection does not connect to a database.
        cfg = Config(str(ALEMBIC_INI))
        script = ScriptDirectory.from_config(cfg)
        heads = script.get_heads()
        assert len(heads) == 1, (
            f"Expected exactly one Alembic head; got {heads!r}. "
            "Multiple heads usually mean a new migration's down_revision "
            "points at the wrong predecessor."
        )
        # The single head must be the most recent migration.
        # Updated to ``0008_finalization_spine`` in Slice 1 Commit 7.
        # Updated to ``0009_live_sources_index`` in hardening commit
        # ``14d1018`` (ENG-004, audit-team v0.4 Slice 1).
        # Updated to ``0010_fractional_asset_trim`` in Slice 4.
        # Updated to ``0013_publish_v07`` in v0.7 Three-tier publish.
        # Updated to ``0015_podcast_v08`` in v0.8 Subscribers + podcast.
        # Updated to ``0016_staff_tokens_v12`` in v1.2 staff-token lifecycle.
        # Updated to ``0017_activitypub_full_federation`` in v1.2 ActivityPub completion.
        # Updated to ``0018_manifest_url_nullable`` in v1.3 managed SQLite setup.
        # Updated to ``0019_merge_v2_live_relay_heads`` in v2.0 release readiness.
        # Updated to ``0022_egress_proof_source_ref`` in v2.0.9 egress proof work.
        # Updated to ``0023_live_finalization_jobs`` in the Stage B+D audit fix
        # sprint (ENG-001: re-parent + renumber of the mis-parented 0011).
        # Updated to ``0024_finalization_failure_codes`` in the Stage B+D
        # hardening pass (UX-002/UX-003 status contract).
        # Updated to ``0025_caption_review_items`` in audit sprint Stage E
        # (durable caption review store).
        # Updated to ``0027_asset_disposition_reviews`` in audit sprint Stage F
        # (ActivityPub retry queue 0026 + disposition reviews 0027).
        # Updated to ``0028_session_recording_target`` in beta sprint B1
        # (session<->recording-target provenance).
        # Updated to ``0029_packaged_trim_bookkeeping`` in beta sprint B3
        # (repackage-on-trim-update).
        # Updated to ``0030_webhook_retry_queue`` for issue #111
        # (subscriber webhook retry/dead-letter queue).
        # Updated to ``0031_program_log`` for cable automation CA-1
        # (recurring program slots + materialized occurrences).
        # Updated to ``0032_channel_automation`` for cable automation CA-2
        # (auto_start 24/7 channel intent on egress configs).
        # Updated to ``0033_cg_bulletins`` for cable automation CA-3
        # (durable community bulletins + per-channel fill policy).
        # Updated to ``0034_udp_ts_sink_kind`` for cable automation CA-6
        # (headend SPTS sink kind admitted by the egress_sinks CHECK).
        # Updated to ``0035_ndi_relay_name`` for issue #116 (BYO-NDI relay
        # intent on egress configs).
        # Updated to ``0036_sdi_relay_device`` for issue #117 (BYO-SDI relay
        # intent on egress configs).
        # Updated to ``0037_asset_meeting_body`` for the #107 remainder
        # (meeting-body category tag on assets, option b).
        # Updated to ``0039_alerting_and_sinkhealth`` for CivicCast 3.0 S8
        # (operational alerting tables + §6.2 default rule seed).
        # Updated to ``0040_commit_to_air_reports`` for CivicCast 3.0 S4
        # slice 1 (commit-to-air audit reports table).
        # Updated to ``0041_commit_rollback_fields`` for CivicCast 3.0 S4
        # slice 5 (rollback_reason + rolled_back_at audit columns).
        # Updated to ``0042_takeover_audit_and_command_action`` for CivicCast 3.0
        # S5 slice 1 (takeover_audit table + takeover/handback command actions).
        # Updated to ``0043_scheduling_automation`` for CivicCast 3.0 S18 slice 1
        # (saved_searches / schedule_blocks / auto_schedule_rules tables).
        # Updated to ``0044_cg_board_designer`` for CivicCast 3.0 S6 (build step 7)
        # (cg_boards / cg_zone_configs / cg_feed_sources / cg_board_audit /
        # cg_feed_item_approvals tables).
        # Updated to ``0045_cg_depth`` for CivicCast 3.0 S18 gap 6 (build step 7)
        # (bulletin_media / bulletin_audio / zone_tags + cg_zone_configs.allowed_tags).
        # Updated to ``0046_cg_feed_source_tags`` for CivicCast 3.0 S18 gap 6
        # (cg_feed_sources.tags column).
        # Updated to ``0047_production_control`` for CivicCast 3.0 build step 9
        # (the seven S16 production control-room tables).
        # Updated to ``0048_remote_contribution`` for CivicCast 3.0 build step 9
        # (the three S17 remote-contribution tables).
        # Advanced per S11 slice: 0049_per_sink_loudness (S11b) -> 0050_caption_proof_samples
        # (S11a) -> 0051_public_safety_eas (S11c) -> 0052_secondary_audio (SAP/descriptive).
        # Updated to ``0053_ai_model_configuration`` for CivicCast 3.0 S13 slice 2
        # (operator AI-model-selection: ai_model_configuration + feature_model_registry).
        # Updated to ``0054_custom_metadata_fields`` for CivicCast 3.0 S22 slice 1
        # (user-defined custom metadata: custom_field_defs + custom_field_values).
        # Updated to ``0055_asrun_and_epg`` for CivicCast 3.0 S23 (as-run log +
        # EPG export configs: as_run_log + epg_export_configs).
        # Updated to ``0057_underwriting_spots`` for CivicCast 3.0 S24 slice 1
        # (underwriting / sponsorship-spot management: underwriting_spots +
        # spot_flights + spot_placements). The 0056 slot is RESERVED for S21
        # scheduled-recording per RECONCILIATION D17 + the W-8 reconciliation
        # footer; when S21 lands, its migration sequences after 0055 alongside
        # 0057 and an Alembic merge revision unifies the two heads.
        # Updated to ``0060_recording_paywall_merge`` for CivicCast 3.0 S21
        # (scheduled recording — the LAST S18 parity gap). S21 landed as a
        # sibling off 0055 (the long-reserved 0056 slot); the merge
        # revision unifies head=0056 with head=0059 so the chain returns
        # to a single head. Every S18 parity gap is now closed on disk;
        # master step-12 flips from `partial` to `built`.
        # Updated to ``0062_media_integrity_columns`` for CivicCast 4.0
        # media-library-hardening (scope item 5): content_hash,
        # thumbnail_path, file_status, file_status_checked_at on assets.
        # Updated to ``0063_producer_ops`` for CivicCast 4.0 item 23
        # (producer/volunteer/equipment operations), chained after
        # 0062_media_integrity_columns.
        # Updated to ``0064_control_room_health_and_versioning`` for
        # CivicCast 4.0 item 7 (control-room device health + cue
        # versioning), chained after 0063_producer_ops.
        # Updated to ``0065_recording_dropout_fields`` for item 6
        # (recording/ingest hardening — mid-recording source-dropout
        # tracking on recording_jobs), chained after
        # 0064_control_room_health_and_versioning.
        # Updated to ``0068_migrate_batches`` for agenda import (0.3.0; was
        # "hls" egress sink kind), chained after
        # 0065_recording_dropout_fields.
        # Updated to ``0070_grandfather_scheduled_to_published`` for
        # Commit-to-Air enforcement (owner decision 2026-07-08), chained
        # after 0068_migrate_batches (0069 reserved by an in-flight
        # control_room branch). Updated to ``0071_published_blocks_overlap``
        # (same owner decision; widens the schedule_items_no_overlap
        # EXCLUDE to also block on published items), chained after 0070. Updated
        # to ``0073_egress_allow_software_fallback`` (win-encoder-remap slice;
        # adds allow_software_fallback to egress_configs), chained after 0071.
        # (Renumbered from 0072 -> 0073 on this branch to avoid colliding
        # with main's unmerged, independently-numbered
        # 0072_normalize_recording_file_uris; the 0072 slot is deliberately
        # unused here until the merge commit re-chains onto main's 0072.)
        # Updated to ``0075_offline_caption_jobs`` for the offline caption
        # job queue that captions published recordings (keystone K3), which
        # chains after ``0074_caption_review_audio_evidence``.
        # Updated to ``0076_analytics_viewership`` for S14 (Analytics /
        # Audience Measurement) — durable viewership_events /
        # viewership_rollups / analytics_report_snapshots tables, chained
        # after ``0075_offline_caption_jobs``.
        # Updated to ``0078_agenda_item_confidence`` (product-hole fix: adds
        # agenda_items.confidence for the PDF-agenda-import heuristic),
        # chained after ``0076_analytics_viewership``. Renumbered from its
        # original ``0076`` after PR #20 merged first and independently
        # claimed that slot; ``0077`` is reserved for feat/s7-media-lifecycle.
        # Updated to ``0080_watch_folder_daemon`` (S7 watch-folder daemon; prior
        # readiness: the five net-new S7 tables + asset_archive_proofs +
        # media_lifecycle_audit_log, plus assets.legal_hold/legal_hold_reason),
        # rechained after ``0078_agenda_item_confidence`` (rather than the
        # original 0076) so it lands after the already-merged 0078, and is
        # the current head.
        # ``0082_egress_graphics_overlay`` (field evidence, candidate #17:
        # async summary generation job so a legitimate multi-minute CPU-only
        # generation survives instead of blocking or discarding an HTTP
        # request -- civiccast/summary/job.py) chained after
        # ``0080_watch_folder_daemon``. Updated to
        # ``0083_caption_review_language`` (recorded-Spanish captions: a
        # language column on caption_review_items), chained after
        # ``0082_egress_graphics_overlay``. Updated to
        # ``0086_live_source_probe_state`` (WP-07 / ENG-003: observed
        # readiness columns on live_sources), chained after
        # ``0083_caption_review_language`` -- WP-05's ``0085`` is parked by
        # owner decision and will not land, and ``0084`` never materialized,
        # so 0083 was the sole other head when this branch re-parented onto
        # it. 0087 added retention-term authoring; 0088 seeds live-caption health.
        # 0089 adds unverified remote-guest media-control request state.
        # Beta 19 adds board-audio gain (0093) and live-video zones (0094).
        assert heads[0] == "0099_audio_description_model", (
            f"Expected head '0099_audio_description_model'; got {heads[0]!r}."
        )


class TestRealPostgresFinalizationJobsMigration:
    """Locks migration 0023_live_finalization_jobs against real Postgres.

    Clean-room acceptance coverage for the Stage B+D audit fixes
    (ENG-001 graph repair, ENG-003 BigInteger width):

    - ``upgrade head`` on the repaired linear graph creates
      ``civiccast.live_finalization_jobs``.
    - ``recording_size_bytes`` / ``last_observed_size_bytes`` round-trip a
      >2 GiB value (would overflow 32-bit Postgres INTEGER).
    - ``downgrade 0022_egress_proof_source_ref`` drops the table cleanly.
    """

    def test_upgrade_creates_finalization_jobs_table(self, postgres_url) -> None:
        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)
        try:
            with eng.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'civiccast' "
                        "AND table_name = 'live_finalization_jobs'"
                    )
                ).fetchall()
            assert len(rows) == 1, "live_finalization_jobs missing after upgrade head"
        finally:
            eng.dispose()

    def test_byte_size_columns_round_trip_values_over_2_gib(self, postgres_url) -> None:
        from civiccast.live.models import LiveFinalizationJob

        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)
        big = 3 * 2**31  # 6 GiB; overflows 32-bit INTEGER, fits BIGINT
        try:
            with Session(bind=eng) as sess:
                sess.add(
                    LiveFinalizationJob(
                        live_session_id="big-recording-1",
                        recording_size_bytes=big,
                        last_observed_size_bytes=big,
                    )
                )
                sess.commit()
            with Session(bind=eng) as sess:
                row = sess.get(LiveFinalizationJob, "big-recording-1")
                assert row is not None
                assert row.recording_size_bytes == big
                assert row.last_observed_size_bytes == big
                sess.delete(row)
                sess.commit()
        finally:
            eng.dispose()

    def test_downgrade_drops_finalization_jobs_table(self, postgres_url) -> None:
        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0022_egress_proof_source_ref")
        eng = create_engine(postgres_url, future=True)
        try:
            with eng.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'civiccast' "
                        "AND table_name = 'live_finalization_jobs'"
                    )
                ).fetchall()
            assert rows == [], "live_finalization_jobs should be gone after downgrade"
        finally:
            eng.dispose()
            # Restore head for the shared module-scoped container.
            command.upgrade(_make_cfg(postgres_url), "head")


class TestRealPostgresSummaryRecordsPersistence:
    """Locks the v0.6 summary and signed-record stores against real Postgres."""

    def test_summary_approval_round_trips_against_real_postgres(self, postgres_url) -> None:
        from contextlib import contextmanager
        from datetime import UTC, datetime
        from uuid import uuid4

        from sqlalchemy.orm import Session

        from civiccast.summary.models import OperatorApproval
        from civiccast.summary.store import PostgresSummaryStore
        from tests.summary.test_summary_persistence import _summary

        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)
        summary_id = f"summary-{uuid4().hex[:8]}"
        try:

            @contextmanager
            def _factory():
                sess = Session(bind=eng)
                try:
                    yield sess
                finally:
                    sess.close()

            store = PostgresSummaryStore(_factory)
            claim_id = f"claim-{uuid4().hex[:8]}"
            base_summary = _summary()
            summary = base_summary.model_copy(
                update={
                    "summary_id": summary_id,
                    "sourced_claims": [
                        base_summary.sourced_claims[0].model_copy(update={"claim_id": claim_id})
                    ],
                }
            )
            store.create_summary(summary)
            approval = OperatorApproval(
                summary_id=summary_id,
                operator_id="staff-real-pg",
                operator_display_name="Real PG Operator",
                approved_at=datetime(2026, 5, 14, 12, 30, tzinfo=UTC),
                approval_note="Checked source cue timestamps.",
            )

            approved = store.approve_summary(
                approval, expected_audit_fingerprint=summary.audit_fingerprint
            )

            reloaded = store.get_summary(summary_id)
            assert reloaded is not None
            assert approved.status == "approved"
            assert reloaded.status == "approved"
            assert reloaded.sourced_claims[0].transcript_ranges[0].cue_id == "cue-1"
            assert store.get_approval(summary_id) == approval
        finally:
            eng.dispose()

    def test_signed_record_round_trips_against_real_postgres(self, postgres_url) -> None:
        from contextlib import contextmanager
        from datetime import UTC, datetime
        from uuid import uuid4

        from sqlalchemy.orm import Session

        from civiccast.auth.models import OperatorIdentity
        from civiccast.records.exporter import SignedRecordExporter
        from civiccast.records.store import PostgresRecordStore
        from civiccast.summary.models import OperatorApproval
        from civiccast.summary.store import PostgresSummaryStore
        from tests.summary.test_summary_persistence import _summary

        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)
        summary_id = f"summary-{uuid4().hex[:8]}"
        try:

            @contextmanager
            def _factory():
                sess = Session(bind=eng)
                try:
                    yield sess
                finally:
                    sess.close()

            summary_store = PostgresSummaryStore(_factory)
            record_store = PostgresRecordStore(_factory)
            claim_id = f"claim-{uuid4().hex[:8]}"
            base_summary = _summary()
            summary_store.create_summary(
                base_summary.model_copy(
                    update={
                        "summary_id": summary_id,
                        "sourced_claims": [
                            base_summary.sourced_claims[0].model_copy(update={"claim_id": claim_id})
                        ],
                    }
                )
            )
            summary_store.approve_summary(
                OperatorApproval(
                    summary_id=summary_id,
                    operator_id="staff-real-pg",
                    operator_display_name="Real PG Operator",
                    approved_at=datetime(2026, 5, 14, 12, 30, tzinfo=UTC),
                    approval_note="Checked source cue timestamps.",
                ),
                expected_audit_fingerprint=base_summary.audit_fingerprint,
            )
            record = SignedRecordExporter(summary_store=summary_store).export(
                summary_id=summary_id,
                operator_identity=OperatorIdentity(
                    operator_id="staff-real-pg",
                    operator_display_name="Real PG Operator",
                    token_id="token-real-pg",
                ),
            )

            stored = record_store.create_record(record, artifact_bytes=record.pdf_bytes)

            reloaded = record_store.get_record(stored.record_id)
            assert reloaded is not None
            assert reloaded.summary_id == summary_id
            assert (
                reloaded.timestamp_proof.artifact_digest == record.timestamp_proof.artifact_digest
            )
            assert record_store.get_artifact(stored.record_id) == record.pdf_bytes
        finally:
            eng.dispose()


class TestRealPostgresPodcastSubscribePersistence:
    """Locks the v0.8 podcast and subscription stores against real Postgres."""

    def test_podcast_episode_upsert_lists_against_real_postgres(self, postgres_url) -> None:
        from contextlib import contextmanager
        from datetime import UTC, datetime
        from uuid import uuid4

        from sqlalchemy.orm import Session

        from civiccast.podcast.models import PodcastChapter, PodcastEpisode
        from civiccast.podcast.store import PostgresPodcastStore

        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)
        asset_id = f"podcast-{uuid4().hex[:8]}"
        try:

            @contextmanager
            def _factory():
                with Session(bind=eng) as sess:
                    yield sess

            store = PostgresPodcastStore(_factory)
            episode = PodcastEpisode(
                episode_id=f"pod-{asset_id}",
                asset_id=asset_id,
                channel_id="gov-ch12",
                title="Council Meeting",
                audio_url=f"https://portal.example/api/public/podcast/audio/{asset_id}.mp3",
                rss_guid=f"civiccast:podcast:{asset_id}",
                loudness_lufs=-16.0,
                transcript_url=f"https://portal.example/records/{asset_id}.pdf",
                summary="Budget hearing and public comment.",
                chapters=[PodcastChapter(t=15, title="Call to order")],
                published_at=datetime.now(UTC),
            )

            store.upsert_episode(episode)
            updated = episode.model_copy(update={"title": "Council Meeting Updated"})
            store.upsert_episode(updated)

            reloaded = store.get_episode(asset_id)
            listed = store.list_for_channel("gov-ch12")

            assert reloaded is not None
            assert reloaded.title == "Council Meeting Updated"
            assert reloaded.chapters[0].title == "Call to order"
            assert any(item.asset_id == asset_id for item in listed)
            assert store.list_for_channel("other") == []
        finally:
            eng.dispose()

    def test_subscription_confirm_update_lists_against_real_postgres(self, postgres_url) -> None:
        from contextlib import contextmanager

        from sqlalchemy.orm import Session

        from civiccast.subscribe.models import SubscriptionSignupRequest
        from civiccast.subscribe.service import confirm_subscription, create_email_subscription
        from civiccast.subscribe.store import PostgresSubscribeStore

        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)
        try:

            @contextmanager
            def _factory():
                with Session(bind=eng) as sess:
                    yield sess

            store = PostgresSubscribeStore(_factory)
            created = create_email_subscription(
                SubscriptionSignupRequest(
                    email="resident@example.org",
                    target_type="channel",
                    target_id="gov-ch12",
                ),
                store=store,
            )

            reloaded = store.get(created.subscription_id)
            assert reloaded is not None
            assert reloaded.confirmation_token, (
                "subscription token is stored but intentionally omitted from public email response"
            )
            confirmed = confirm_subscription(reloaded.confirmation_token, store=store)
            reloaded = store.get(created.subscription_id)
            listed = store.list_confirmed_for_target(
                target_type="channel",
                target_id="gov-ch12",
            )

            assert confirmed.status == "confirmed"
            assert reloaded is not None
            assert reloaded.encrypted_subscriber_handle != "resident@example.org"
            assert [item.subscription_id for item in listed] == [created.subscription_id]
        finally:
            eng.dispose()


class TestRealPostgresLiveSessionStateMachineConcurrency:
    """Locks: the LiveSessionStore conditional-UPDATE pattern is
    row-level atomic against real Postgres.

    Two threads attempting the same transition on the same live session
    race the UPDATE. Postgres' row-level write lock serializes them.
    The winner sees ``rowcount == 1`` and commits the new state. The
    loser blocks on the lock, sees ``rowcount == 0`` after the winner
    commits (the ``WHERE state = <expected>`` predicate no longer
    matches), and raises :class:`LiveSessionStateError`.

    This proof matters because the SQLite test path in
    :mod:`tests.live.test_store` cannot demonstrate true row-level
    contention (SQLite serializes all writers at the database level).
    Sprint 0.4 Slice 1 Commit 4 design directive.
    """

    def test_two_concurrent_start_preflight_exactly_one_wins(self, postgres_url) -> None:
        import threading

        from civiccast.live import (
            LIVE_SESSION_STATE_PREFLIGHT,
            LiveSessionCreate,
            LiveSessionStateError,
            LiveSessionStore,
        )

        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")

        # Seed an idle session via a dedicated engine so its commit is
        # visible to the two contending stores below.
        seed_eng = create_engine(postgres_url, future=True)
        try:

            @contextmanager
            def seed_factory():
                with Session(bind=seed_eng) as sess:
                    yield sess

            LiveSessionStore(session_factory=seed_factory).create_session(
                LiveSessionCreate(
                    live_session_id="race-1",
                    channel_id="gov-ch12",
                    title="Concurrent start_preflight race",
                )
            )
        finally:
            seed_eng.dispose()

        eng_a = create_engine(postgres_url, future=True)
        eng_b = create_engine(postgres_url, future=True)

        try:

            @contextmanager
            def factory_a():
                with Session(bind=eng_a) as sess:
                    yield sess

            @contextmanager
            def factory_b():
                with Session(bind=eng_b) as sess:
                    yield sess

            store_a = LiveSessionStore(session_factory=factory_a)
            store_b = LiveSessionStore(session_factory=factory_b)

            winners: list[str] = []
            losers: list[str] = []
            barrier = threading.Barrier(2)

            def attempt(name: str, store: LiveSessionStore) -> None:
                # Sync both threads to the same start line so the
                # UPDATEs hit the row lock as concurrently as the
                # scheduler allows.
                barrier.wait()
                try:
                    store.start_preflight("race-1")
                    winners.append(name)
                except LiveSessionStateError as exc:
                    losers.append(exc.current_state)

            t_a = threading.Thread(target=attempt, args=("a", store_a))
            t_b = threading.Thread(target=attempt, args=("b", store_b))
            t_a.start()
            t_b.start()
            t_a.join(timeout=10)
            t_b.join(timeout=10)

            assert not t_a.is_alive() and not t_b.is_alive(), (
                "Concurrent transition threads did not terminate within 10s; "
                "possible row-lock deadlock or unhandled exception."
            )

            assert len(winners) == 1, (
                f"Expected exactly one winner of the concurrent "
                f"start_preflight race; got winners={winners!r} losers={losers!r}"
            )
            assert len(losers) == 1, (
                f"Expected exactly one loser; got winners={winners!r} losers={losers!r}"
            )
            # The losing thread re-reads state after rowcount=0 and
            # finds the row at preflight (the winner already moved it).
            assert losers[0] == LIVE_SESSION_STATE_PREFLIGHT, (
                f"Loser should observe state=preflight; got {losers[0]!r}"
            )
        finally:
            eng_a.dispose()
            eng_b.dispose()


class TestRealPostgresFinalizationIdempotency:
    """Locks: ``LiveRecordingFinalizer.finalize_recording`` is idempotent
    under real-Postgres concurrency.

    Two threads call ``finalize_recording`` on the same ``ending``-state
    session at the same time. The composite-PK UNIQUE on
    ``live_session_events`` serializes them: one INSERT wins and commits
    the transaction (event + asset + state advance); the other's INSERT
    collides on the PK, rolls back, and returns
    ``FinalizationResult(idempotent=True)`` after re-reading the winner's
    rows. Exactly one asset row exists at end-of-test; exactly one event
    row exists; the LiveSession is at ``recorded``.

    This proof matters because the SQLite test path in
    :mod:`tests.live.test_finalization` cannot demonstrate true row-
    level contention (SQLite serializes all writers at the database
    level). Sprint 0.4 Slice 1 Commit 7 design directive.
    """

    def test_two_concurrent_finalize_recording_calls_produce_one_asset(
        self,
        postgres_url,
    ) -> None:
        import threading

        from sqlalchemy import select as sa_select

        from civiccast.live import (
            LIVE_SESSION_EVENT_FINALIZED,
            LIVE_SESSION_STATE_ENDING,
            LIVE_SESSION_STATE_RECORDED,
            FinalizationResult,
            LiveRecordingFinalizer,
            LiveSession,
            LiveSessionEvent,
        )
        from civiccast.schedule.models import Asset

        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")

        # Seed a session directly at state='ending' so both finalizers
        # start from the same precondition.
        seed_eng = create_engine(postgres_url, future=True)
        try:
            with Session(bind=seed_eng) as sess:
                sess.add(
                    LiveSession(
                        live_session_id="race-finalize-1",
                        channel_id="gov-ch12",
                        title="Concurrent finalize race",
                        state=LIVE_SESSION_STATE_ENDING,
                    )
                )
                sess.commit()
        finally:
            seed_eng.dispose()

        eng_a = create_engine(postgres_url, future=True)
        eng_b = create_engine(postgres_url, future=True)

        try:

            @contextmanager
            def factory_a():
                with Session(bind=eng_a) as sess:
                    yield sess

            @contextmanager
            def factory_b():
                with Session(bind=eng_b) as sess:
                    yield sess

            finalizer_a = LiveRecordingFinalizer(session_factory=factory_a)
            finalizer_b = LiveRecordingFinalizer(session_factory=factory_b)

            results: list[FinalizationResult] = []
            errors: list[BaseException] = []
            barrier = threading.Barrier(2)

            def attempt(name: str, finalizer: LiveRecordingFinalizer) -> None:
                barrier.wait()
                try:
                    result = finalizer.finalize_recording(
                        "race-finalize-1",
                        recording_uri=f"file:///srv/recordings/{name}.mkv",
                    )
                    results.append(result)
                except BaseException as exc:
                    errors.append(exc)

            t_a = threading.Thread(target=attempt, args=("a", finalizer_a))
            t_b = threading.Thread(target=attempt, args=("b", finalizer_b))
            t_a.start()
            t_b.start()
            t_a.join(timeout=10)
            t_b.join(timeout=10)

            assert not t_a.is_alive() and not t_b.is_alive(), (
                "Concurrent finalize threads did not terminate within 10s; "
                "possible row-lock deadlock or unhandled exception."
            )

            assert errors == [], (
                f"No thread should raise; both should return a "
                f"FinalizationResult. Got errors={errors!r}"
            )
            assert len(results) == 2, (
                f"Expected two FinalizationResult returns; got {len(results)}."
            )

            # Exactly one result should be the writer (idempotent=False);
            # the other should be the idempotent recoverer.
            idempotent_flags = sorted(r.idempotent for r in results)
            assert idempotent_flags == [False, True], (
                f"Expected one writer + one idempotent recoverer; got "
                f"idempotent flags {idempotent_flags!r}"
            )

            # Both results MUST see the same asset_id + event payload.
            asset_ids = {r.asset.asset_id for r in results}
            assert asset_ids == {"race-finalize-1"}
            event_seqs = {r.event.event_seq for r in results}
            assert event_seqs == {1}

            # Exactly one asset row + one event row exist in the DB.
            verify_eng = create_engine(postgres_url, future=True)
            try:
                with Session(bind=verify_eng) as sess:
                    asset_rows = (
                        sess.execute(
                            sa_select(Asset).where(
                                Asset.source_live_session_id == "race-finalize-1"
                            )
                        )
                        .scalars()
                        .all()
                    )
                    event_rows = (
                        sess.execute(
                            sa_select(LiveSessionEvent).where(
                                LiveSessionEvent.live_session_id == "race-finalize-1"
                            )
                        )
                        .scalars()
                        .all()
                    )
                    session_row = sess.execute(
                        sa_select(LiveSession).where(
                            LiveSession.live_session_id == "race-finalize-1"
                        )
                    ).scalar_one()
                assert len(asset_rows) == 1, (
                    f"Expected exactly one asset row after concurrent "
                    f"finalize; got {len(asset_rows)}"
                )
                assert len(event_rows) == 1, (
                    f"Expected exactly one event row after concurrent "
                    f"finalize; got {len(event_rows)}"
                )
                assert event_rows[0].event_type == LIVE_SESSION_EVENT_FINALIZED
                assert session_row.state == LIVE_SESSION_STATE_RECORDED, (
                    f"LiveSession should be at 'recorded' after finalize; got {session_row.state!r}"
                )
            finally:
                verify_eng.dispose()
        finally:
            eng_a.dispose()
            eng_b.dispose()


class TestRealPostgresContributeInviteTokenConcurrency:
    """Locks: the single-use invite-token consume is atomic under Postgres
    concurrency.

    The guarded ``UPDATE ... WHERE consumed_at IS NULL`` + ``rowcount == 1``
    ensures exactly one racing caller wins (returns True); the other sees
    rowcount 0 and returns False (→ the service maps that to 410 Gone).
    """

    def test_concurrent_consume_delivers_exactly_one_winner(self, postgres_url: str) -> None:
        import threading
        from contextlib import contextmanager
        from datetime import UTC, datetime

        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from civiccast.live.contribution.models import ContributionRoom, GuestInvite
        from civiccast.live.contribution.store import ContributionStore

        _now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

        # Migrate the throwaway DB to head first (every test in this module
        # does this — there is no autouse migration fixture). Idempotent.
        command.upgrade(_make_cfg(postgres_url), "head")

        seed_eng = create_engine(postgres_url, future=True)
        try:

            @contextmanager
            def seed_factory():
                with Session(bind=seed_eng) as sess:
                    yield sess

            seed_store = ContributionStore(session_factory=seed_factory)
            room = seed_store.upsert_room(
                ContributionRoom(
                    room_id="race-token-room-1",
                    channel_id="ch-race",
                    name="Race Room",
                    vdo_room_name="vdo-race-1",
                    created_at=_now,
                    updated_at=_now,
                )
            )
            invite = seed_store.create_invite(
                GuestInvite(
                    invite_id="inv-race-token-1",
                    room_id=room.room_id,
                    guest_display_name="Race Guest",
                    role="council_member",
                    invite_token="r" * 64,
                    expires_at=datetime(2099, 1, 1, tzinfo=UTC),
                    created_at=_now,
                )
            )
        finally:
            seed_eng.dispose()

        eng_a = create_engine(postgres_url, future=True)
        eng_b = create_engine(postgres_url, future=True)
        try:

            @contextmanager
            def factory_a():
                with Session(bind=eng_a) as sess:
                    yield sess

            @contextmanager
            def factory_b():
                with Session(bind=eng_b) as sess:
                    yield sess

            store_a = ContributionStore(session_factory=factory_a)
            store_b = ContributionStore(session_factory=factory_b)

            results: list[bool] = []
            errors: list[BaseException] = []
            barrier = threading.Barrier(2)

            def _consume(store: ContributionStore) -> None:
                barrier.wait()
                try:
                    result = store.consume_invite_token(
                        invite.invite_token, consumed_at=datetime.now(UTC)
                    )
                    results.append(result)
                except BaseException as exc:
                    errors.append(exc)

            t_a = threading.Thread(target=_consume, args=(store_a,))
            t_b = threading.Thread(target=_consume, args=(store_b,))
            t_a.start()
            t_b.start()
            t_a.join(timeout=10)
            t_b.join(timeout=10)

            assert not t_a.is_alive() and not t_b.is_alive(), (
                "Concurrent consume threads did not terminate within 10s; "
                "possible deadlock or unhandled exception."
            )
            assert errors == [], f"No thread should raise; got errors={errors!r}"
            assert sorted(results) == [False, True], (
                f"Expected exactly one winner (True) and one loser (False); got {results!r}"
            )
        finally:
            eng_a.dispose()
            eng_b.dispose()


class TestRealPostgresFullMigrationChain:
    """Locks: the ENTIRE Alembic chain applies cleanly to a fresh real
    Postgres database — the regression net for the bug class found in the
    step-9 close (unqualified seed INSERTs, str-into-timestamptz seeds, and a
    version-table column too narrow for a long revision slug).

    These bugs were invisible on SQLite (schema-less, length-agnostic, loose
    typing). This test exercises the one path that catches them: a real
    `alembic upgrade head` against Postgres with the ``civiccast`` schema. If a
    future migration adds an unqualified seed or a too-narrow column, THIS test
    fails instead of a production deploy.
    """

    def test_upgrade_head_applies_clean_and_stamps_head(self, postgres_url: str) -> None:
        cfg = _make_cfg(postgres_url)
        # The whole chain must apply without raising on real Postgres.
        command.upgrade(cfg, "head")

        # The DB is stamped at the script directory's head revision.
        script = ScriptDirectory.from_config(cfg)
        expected_head = script.get_current_head()
        eng = create_engine(postgres_url, future=True)

        def media_control_columns() -> set[str]:
            with eng.connect() as conn:
                return set(
                    conn.execute(
                        text(
                            "SELECT column_name FROM information_schema.columns "
                            "WHERE table_schema = 'civiccast' "
                            "AND table_name = 'remote_guest_sessions' "
                            "AND column_name IN ("
                            "'media_control_state', 'media_control_action', "
                            "'media_control_requested_at')"
                        )
                    )
                    .scalars()
                    .all()
                )

        try:
            with eng.connect() as conn:
                db_rev = conn.execute(
                    text("SELECT version_num FROM civiccast.alembic_version")
                ).scalar_one()
                # The 0039 default-rule seed must have actually landed (this is
                # the unqualified-INSERT + timestamptz-seed regression guard).
                seeded = conn.execute(
                    text("SELECT count(*) FROM civiccast.alert_rules")
                ).scalar_one()
            assert db_rev == expected_head, (
                f"Chain did not stamp head: db={db_rev!r} head={expected_head!r}"
            )
            assert seeded > 0, (
                "alert_rules default-rule seed did not land on real Postgres "
                "(0039 seed regression)."
            )
            expected_media_control_columns = {
                "media_control_state",
                "media_control_action",
                "media_control_requested_at",
            }
            assert media_control_columns() == expected_media_control_columns, (
                "0089 media-control columns did not land in civiccast.remote_guest_sessions"
            )

            command.downgrade(cfg, "0088_live_caption_health_alert_rules")
            assert media_control_columns() == set(), (
                "0089 downgrade left media-control columns behind in "
                "civiccast.remote_guest_sessions"
            )
            command.upgrade(cfg, "head")
            assert media_control_columns() == expected_media_control_columns, (
                "0089 re-upgrade did not restore media-control columns in "
                "civiccast.remote_guest_sessions"
            )
        finally:
            eng.dispose()


class TestRealPostgresAsRunAndEpgMigration:
    """Locks migration 0055_asrun_and_epg against real Postgres (S23).

    - ``upgrade head`` creates ``civiccast.as_run_log`` +
      ``civiccast.epg_export_configs``.
    - The DB-level CHECK constraints reject an invalid ``source_kind`` /
      ``format`` and accept every valid one — including the reserved
      ``spot`` source_kind (S24 underwriting), which must be admitted now so
      no schema change is needed when S24 lands.
    - ``downgrade 0054_custom_metadata_fields`` drops both tables cleanly;
      0054's tables survive.
    """

    _TABLES = ("as_run_log", "epg_export_configs")

    def test_upgrade_head_creates_the_two_tables(self, postgres_url: str) -> None:
        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)
        try:
            with eng.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'civiccast' "
                        "AND table_name IN ('as_run_log', 'epg_export_configs') "
                        "ORDER BY table_name"
                    )
                ).fetchall()
            assert [r[0] for r in rows] == ["as_run_log", "epg_export_configs"]
        finally:
            eng.dispose()

    def test_source_kind_check_rejects_invalid(self, postgres_url: str) -> None:
        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)
        try:
            with eng.connect() as conn, pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO civiccast.as_run_log "
                        "(entry_id, station_id, channel_id, actual_start, "
                        "actual_end, duration_s, source_kind, verified, "
                        "created_at, updated_at) VALUES "
                        "('bad-1', 'sta_main', 'gov-ch12', now(), now(), 0, "
                        "'advert', true, now(), now())"
                    )
                )
                conn.commit()
        finally:
            eng.dispose()

    def test_source_kind_check_accepts_all_five_including_spot(self, postgres_url: str) -> None:
        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)
        try:
            from datetime import UTC, datetime

            from civiccast.reporting.models import AsRunLogEntryDb

            with Session(bind=eng) as sess:
                for i, kind in enumerate(("program", "filler", "live", "slate", "spot")):
                    sess.add(
                        AsRunLogEntryDb(
                            entry_id=f"ar-kind-{kind}",
                            station_id="sta_main",
                            channel_id="gov-ch12",
                            actual_start=datetime.now(UTC),
                            actual_end=datetime.now(UTC),
                            duration_s=i,
                            source_kind=kind,
                            verified=True,
                            created_at=datetime.now(UTC),
                            updated_at=datetime.now(UTC),
                        )
                    )
                sess.commit()
                count = sess.scalar(
                    text(
                        "SELECT count(*) FROM civiccast.as_run_log WHERE entry_id LIKE 'ar-kind-%'"
                    )
                )
                assert count == 5
        finally:
            eng.dispose()

    def test_format_check_rejects_invalid(self, postgres_url: str) -> None:
        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)
        try:
            with eng.connect() as conn, pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO civiccast.epg_export_configs "
                        "(config_id, station_id, channel_id, format, "
                        "horizon_days, field_map, created_at, updated_at) VALUES "
                        "('bad-fmt', 'sta_main', 'gov-ch12', 'json', 14, "
                        "'{}', now(), now())"
                    )
                )
                conn.commit()
        finally:
            eng.dispose()

    def test_downgrade_drops_both_tables(self, postgres_url: str) -> None:
        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0054_custom_metadata_fields")
        eng = create_engine(postgres_url, future=True)
        try:
            with eng.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'civiccast' "
                        "AND table_name IN ('as_run_log', 'epg_export_configs')"
                    )
                ).fetchall()
                # 0054's table survives the single-step downgrade.
                survivor = conn.execute(
                    text(
                        "SELECT count(*) FROM information_schema.tables "
                        "WHERE table_schema = 'civiccast' "
                        "AND table_name = 'custom_field_defs'"
                    )
                ).scalar_one()
            assert rows == [], f"Expected zero S23 tables after downgrade; found {rows}"
            assert survivor == 1
        finally:
            eng.dispose()
            # Restore head for session-scoped container cleanup.
            command.upgrade(_make_cfg(postgres_url), "head")


class TestRealPostgresReportingHoursByCategory:
    """S23 slice 3 — hours-by-category GROUP BY behavior under real Postgres.

    The aggregate query LEFT JOINs ``as_run_log`` to ``custom_field_values`` on
    ``(asset_id, field_id)``, ``COALESCE``s the joined value into the
    ``(uncategorized)`` sentinel, groups by the COALESCEd column, and orders
    uncategorized last via a CASE expression. SQLite covers the shape; this
    test locks the same behavior under Postgres (where GROUP BY / NULL /
    COALESCE / CASE semantics can diverge subtly from SQLite). DC-3.
    """

    def test_hours_by_category_aggregates_against_real_postgres(self, postgres_url: str) -> None:
        from contextlib import contextmanager
        from datetime import UTC, datetime

        from civiccast.metadata.models import CustomFieldDef, CustomFieldValue
        from civiccast.metadata.store import CustomFieldStore
        from civiccast.reporting.models import AsRunLogEntry
        from civiccast.reporting.service import ReportingService
        from civiccast.reporting.store import ReportingStore

        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)

        @contextmanager
        def factory():
            with Session(bind=eng) as session:
                yield session

        try:
            cf = CustomFieldStore(factory)
            cf.upsert_def(
                CustomFieldDef(
                    field_id="cf_cat",
                    station_id="sta_pg",
                    key="category",
                    label="Category",
                    type="text",
                )
            )
            cf.set_values(
                "ast_a",
                [CustomFieldValue(asset_id="ast_a", field_id="cf_cat", value="Government")],
                definitions=cf.list_defs("sta_pg"),
            )
            store = ReportingStore(factory)
            t0 = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
            t1 = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
            t2 = datetime(2026, 6, 1, 11, 0, tzinfo=UTC)
            store.append_as_run(
                AsRunLogEntry(
                    entry_id="pg_e1",
                    station_id="sta_pg",
                    channel_id="gov-ch12",
                    asset_id="ast_a",
                    actual_start=t0,
                    actual_end=t1,
                    duration_s=3600,
                    source_kind="program",
                )
            )
            # Filler (no asset_id) → uncategorized bucket.
            store.append_as_run(
                AsRunLogEntry(
                    entry_id="pg_filler",
                    station_id="sta_pg",
                    channel_id="gov-ch12",
                    asset_id=None,
                    actual_start=t1,
                    actual_end=t2,
                    duration_s=1800,
                    source_kind="filler",
                    verified=False,
                )
            )
            service = ReportingService(factory)
            report = service.hours_by_category(
                station_id="sta_pg",
                field_key="category",
                from_ts=datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
                to_ts=datetime(2026, 6, 2, 0, 0, tzinfo=UTC),
            )
            assert report.field_not_found is False
            cats = [(r.category, r.total_seconds) for r in report.rows]
            assert cats == [("Government", 3600), ("(uncategorized)", 1800)]
        finally:
            eng.dispose()

    def test_hours_by_category_field_not_found_against_real_postgres(
        self, postgres_url: str
    ) -> None:
        from contextlib import contextmanager
        from datetime import UTC, datetime

        from civiccast.reporting.service import ReportingService

        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)

        @contextmanager
        def factory():
            with Session(bind=eng) as session:
                yield session

        try:
            service = ReportingService(factory)
            report = service.hours_by_category(
                station_id="sta_unknown",
                field_key="no_such_field",
                from_ts=datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
                to_ts=datetime(2026, 6, 2, 0, 0, tzinfo=UTC),
            )
            assert report.field_not_found is True
            assert report.rows == []
        finally:
            eng.dispose()


# ---------------------------------------------------------------------------
# S26 paywall — real-Postgres functional coverage (T-2 fix from the
# S26 GauntletGate Test/QA lane).
#
# The SQLite-only paywall tests pass tz-naive datetimes through SQLAlchemy
# and quietly behave differently from real PG (`DateTime(timezone=True)`
# round-trips aware datetimes on PG, naive on SQLite). These cases pin
# the four PG-side behaviors that the audit identified as untested:
#
#  1. ``has_grant_for`` correctly excludes a tz-aware expired grant.
#  2. ``revoke_grants_for_subscription`` reports the correct rowcount
#     under psycopg's autocommit / transactional semantics.
#  3. The unique-station-config index rejects a second config from a
#     concurrent writer (two sessions; second raises IntegrityError).
#  4. Webhook event-id idempotency (Q-1): replaying the same event id
#     short-circuits the second call.
# ---------------------------------------------------------------------------


class TestRealPostgresPaywall:
    """T-2 fix — real-PG functional coverage for S26 paywall behavior."""

    def test_has_grant_for_excludes_expired_tz_aware(self, postgres_url) -> None:
        from contextlib import contextmanager
        from datetime import UTC, datetime, timedelta

        from civiccast.paywall.models import AccessGrant, PaywallConfig
        from civiccast.paywall.store import PaywallStore

        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)

        @contextmanager
        def factory():
            with Session(bind=eng) as sess:
                yield sess

        try:
            store = PaywallStore(factory)
            store.upsert_config(
                PaywallConfig(
                    config_id="pw-pg-1",
                    station_id="sta-pg-1",
                    enabled=True,
                    signing_secret="real-pg-secret-with-32-or-more-chars",
                )
            )
            now = datetime.now(UTC)
            past = now - timedelta(days=1)
            future = now + timedelta(days=30)
            # An expired grant + a non-expired grant for the same email/scope.
            store.upsert_grant(
                AccessGrant(
                    grant_id="g-pg-expired",
                    station_id="sta-pg-1",
                    email="viewer@example.com",
                    scope_kind="asset",
                    scope_id="vid-1",
                    granted_via="comp",
                    expires_at=past,
                )
            )
            assert store.has_grant_for("sta-pg-1", "viewer@example.com", "asset", "vid-1") is False
            # Now add a non-expired grant — should resolve True.
            store.upsert_grant(
                AccessGrant(
                    grant_id="g-pg-active",
                    station_id="sta-pg-1",
                    email="viewer@example.com",
                    scope_kind="asset",
                    scope_id="vid-2",
                    granted_via="comp",
                    expires_at=future,
                )
            )
            assert store.has_grant_for("sta-pg-1", "viewer@example.com", "asset", "vid-2") is True
        finally:
            eng.dispose()

    def test_revoke_grants_for_subscription_rowcount(self, postgres_url) -> None:
        from contextlib import contextmanager
        from datetime import UTC, datetime, timedelta

        from civiccast.paywall.models import AccessGrant, PaywallConfig
        from civiccast.paywall.store import PaywallStore

        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)

        @contextmanager
        def factory():
            with Session(bind=eng) as sess:
                yield sess

        try:
            store = PaywallStore(factory)
            store.upsert_config(
                PaywallConfig(
                    config_id="pw-pg-2",
                    station_id="sta-pg-2",
                    enabled=True,
                    signing_secret="real-pg-secret-with-32-or-more-chars",
                )
            )
            future = datetime.now(UTC) + timedelta(days=30)
            for n in range(3):
                store.upsert_grant(
                    AccessGrant(
                        grant_id=f"g-rev-{n}",
                        station_id="sta-pg-2",
                        email=f"buyer{n}@example.com",
                        scope_kind="all",
                        scope_id="",
                        granted_via="subscription",
                        subscription_id="sub_rev_pg",
                        expires_at=future,
                    )
                )
            # One grant for a DIFFERENT subscription should be preserved.
            store.upsert_grant(
                AccessGrant(
                    grant_id="g-other",
                    station_id="sta-pg-2",
                    email="other@example.com",
                    scope_kind="all",
                    scope_id="",
                    granted_via="subscription",
                    subscription_id="sub_other_pg",
                    expires_at=future,
                )
            )
            removed = store.revoke_grants_for_subscription("sub_rev_pg")
            assert removed == 3
            # The other-subscription grant is still there.
            assert store.get_grant("g-other") is not None
        finally:
            eng.dispose()

    def test_unique_station_config_index_rejects_second(self, postgres_url) -> None:
        from contextlib import contextmanager

        from sqlalchemy.exc import IntegrityError as _SAIntegrityError

        from civiccast.paywall.models import PaywallConfig
        from civiccast.paywall.store import (
            PaywallStationConfigConflictError,
            PaywallStore,
        )

        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)

        @contextmanager
        def factory():
            with Session(bind=eng) as sess:
                yield sess

        try:
            store = PaywallStore(factory)
            store.upsert_config(
                PaywallConfig(
                    config_id="pw-pg-3a",
                    station_id="sta-pg-3",
                    enabled=False,
                    signing_secret="real-pg-secret-with-32-or-more-chars",
                )
            )
            # A second config with a DIFFERENT id but the same station_id
            # must hit the unique index. The store's pre-check raises a
            # typed conflict error; the underlying DB-level IntegrityError
            # is also a valid outcome (race window).
            with pytest.raises((PaywallStationConfigConflictError, _SAIntegrityError)):
                store.upsert_config(
                    PaywallConfig(
                        config_id="pw-pg-3b",
                        station_id="sta-pg-3",
                        enabled=False,
                        signing_secret="real-pg-secret-with-32-or-more-chars",
                    )
                )
        finally:
            eng.dispose()

    def test_stripe_event_idempotency_replay_short_circuits(self, postgres_url) -> None:
        from contextlib import contextmanager

        from civiccast.paywall.store import PaywallStore

        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)

        @contextmanager
        def factory():
            with Session(bind=eng) as sess:
                yield sess

        try:
            store = PaywallStore(factory)
            # First record — inserted.
            inserted = store.record_stripe_event_seen(
                "evt_pg_idem_1",
                "sta-pg-4",
                "customer.subscription.created",
            )
            assert inserted is True
            # Replay — short-circuits.
            replay = store.record_stripe_event_seen(
                "evt_pg_idem_1",
                "sta-pg-4",
                "customer.subscription.created",
            )
            assert replay is False
        finally:
            eng.dispose()


# ---------------------------------------------------------------------------
# S21 scheduled recording — real-Postgres functional coverage.
#
# T-1 fix (S21 Test/QA lane): the merge revision 0060_recording_paywall_merge
# previously had NO real-PG coverage of the recording tables — only
# SQLite. Real PG can fail catastrophically where SQLite shrugs (FK
# ordering, default-value seed re-apply on re-upgrade, JSON column
# default semantics). This class pins:
#
#  1. ``upgrade head`` on a real PG creates ``recording_schedules`` +
#     ``recording_jobs`` in the ``civiccast`` schema.
#  2. The ``recording_jobs_state_check`` CHECK constraint rejects an
#     invalid ``state`` value on real PG.
#  3. The ``(station_id, name)`` unique constraint on
#     ``recording_schedules`` rejects a second config from a concurrent
#     writer (two sessions; second raises IntegrityError).
#  4. ``downgrade 0055_asrun_and_epg`` drops the two recording tables
#     cleanly on real PG (no FK-ordering surprise).
#
# T-3 fix (S21 Test/QA lane): ``find_overlapping_jobs`` carries a
# ``_coerce_aware`` branch that converts SQLite's tz-naive datetimes
# into aware UTC. On real PG (``DateTime(timezone=True)`` round-trips
# aware), the conversion should be a no-op. The TestRealPostgresPaywall
# class already documented this exact bug class for the paywall T-2
# fix — replicate the pattern here so an aware/naive comparison
# mismatch under SQLite doesn't slip through to PG. The fifth test
# below plants two jobs with aware datetimes on real PG and asserts
# overlap detection works.
# ---------------------------------------------------------------------------


class TestRealPostgresScheduledRecordingMigration:
    """T-1 + T-3 fix — real-PG functional coverage for S21."""

    _TABLES = ("recording_schedules", "recording_jobs")

    def test_upgrade_head_creates_recording_tables(self, postgres_url: str) -> None:
        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)
        try:
            with eng.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'civiccast' "
                        "AND table_name IN ('recording_schedules', 'recording_jobs') "
                        "ORDER BY table_name"
                    )
                ).fetchall()
            assert [r[0] for r in rows] == ["recording_jobs", "recording_schedules"]
        finally:
            eng.dispose()

    def test_state_check_rejects_invalid_state(self, postgres_url: str) -> None:
        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)
        try:
            with eng.connect() as conn, pytest.raises(IntegrityError):
                conn.execute(
                    text(
                        "INSERT INTO civiccast.recording_jobs "
                        "(job_id, station_id, planned_start, planned_end, "
                        "state, source_snapshot, encoder_profile, "
                        "custom_field_values, created_at, updated_at) VALUES "
                        "('badstate-1', 'sta_main', now(), now() + interval '1 hour', "
                        "'bogus-state', '{}'::jsonb, 'hw-h264-1080p', "
                        "'{}'::jsonb, now(), now())"
                    )
                )
                conn.commit()
        finally:
            eng.dispose()

    def test_unique_station_name_index_rejects_second(self, postgres_url: str) -> None:
        """The ``recording_schedules_station_name_unique`` constraint
        must reject a second schedule with the same (station_id, name)
        on real PG. Mirrors the paywall ``unique_station_config_index``
        coverage."""
        from contextlib import contextmanager

        from civiccast.recording.models import (
            RecordingSchedule,
            RecordingSource,
            RecurrenceSpec,
        )
        from civiccast.recording.store import (
            RecordingScheduleNameConflictError,
            RecordingStore,
        )

        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)

        @contextmanager
        def factory():
            with Session(bind=eng) as sess:
                yield sess

        try:
            store = RecordingStore(factory)
            from datetime import UTC, datetime, timedelta

            base_kwargs = {
                "station_id": "sta-rec-pg",
                "name": "PG Conflict",
                "source": RecordingSource(kind="rtsp", uri="rtsp://camera.local/feed"),
                "recurrence": RecurrenceSpec(
                    kind="one_shot",
                    start=datetime.now(UTC) + timedelta(minutes=10),
                ),
                "duration_seconds": 3600,
                "encoder_profile": "hw-h264-1080p",
            }
            store.upsert_schedule(RecordingSchedule(schedule_id="sch-pg-a", **base_kwargs))
            with pytest.raises((RecordingScheduleNameConflictError, IntegrityError)):
                store.upsert_schedule(RecordingSchedule(schedule_id="sch-pg-b", **base_kwargs))
        finally:
            eng.dispose()

    def test_downgrade_past_merge_drops_recording_tables(self, postgres_url: str) -> None:
        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        # Downgrade past the merge and past 0056. Alembic should drop
        # the recording tables cleanly on real PG.
        command.downgrade(cfg, "0055_asrun_and_epg")
        eng = create_engine(postgres_url, future=True)
        try:
            with eng.connect() as conn:
                rows = conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'civiccast' "
                        "AND table_name IN ('recording_schedules', 'recording_jobs')"
                    )
                ).fetchall()
            assert rows == [], f"Expected zero S21 tables after downgrade past merge; found {rows}"
        finally:
            eng.dispose()
            # Restore head for session-scoped container cleanup.
            command.upgrade(_make_cfg(postgres_url), "head")

    def test_find_overlapping_jobs_tz_aware_on_real_postgres(self, postgres_url: str) -> None:
        """T-3 fix — the ``_coerce_aware`` path is SQLite-specific. On
        real PG ``DateTime(timezone=True)`` round-trips aware datetimes
        so the branch should be a no-op. We prove overlap detection
        works AND that the returned ``planned_start`` carries tz info
        (regression guard against ``_coerce_aware`` silently stripping
        tzinfo).
        """
        from contextlib import contextmanager
        from datetime import UTC, datetime, timedelta

        from civiccast.recording.models import (
            RecordingJob,
            RecordingSource,
        )
        from civiccast.recording.store import RecordingStore

        cfg = _make_cfg(postgres_url)
        command.upgrade(cfg, "head")
        eng = create_engine(postgres_url, future=True)

        @contextmanager
        def factory():
            with Session(bind=eng) as sess:
                yield sess

        try:
            store = RecordingStore(factory)
            now = datetime.now(UTC)
            src = RecordingSource(kind="rtsp", uri="rtsp://camera-pg.local/feed")
            store.create_job(
                RecordingJob(
                    job_id="job-pg-overlap-1",
                    station_id="sta-rec-pg-2",
                    planned_start=now,
                    planned_end=now + timedelta(hours=1),
                    source_snapshot=src,
                    encoder_profile="hw-h264-1080p",
                )
            )
            overlaps = store.find_overlapping_jobs(
                "sta-rec-pg-2",
                src,
                now + timedelta(minutes=10),
                now + timedelta(minutes=50),
            )
            assert len(overlaps) == 1
            assert overlaps[0].job_id == "job-pg-overlap-1"
            # Regression guard: tzinfo survives the round-trip.
            assert overlaps[0].planned_start.tzinfo is not None
            assert overlaps[0].planned_end.tzinfo is not None
        finally:
            eng.dispose()
