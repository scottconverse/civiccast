# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""S7 media lifecycle worker tests.

Mirrors ``tests/schedule/test_retention_worker.py`` / ``test_media_integrity_worker.py``'s
structure and fixture style (env-gated settings, ``run_once``, SQLite
session-factory fixture) by design -- all three workers share the same shape.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from civiccast.db import Base
from civiccast.schedule import media_lifecycle_worker as worker_module
from civiccast.schedule.media_lifecycle_models import (
    DEFAULT_TRANSCODE_FORMATS,
    READINESS_MISSING_FILE,
    READINESS_NOT_READY,
    READINESS_PENDING_TRANSCODE,
    READINESS_READY,
    READINESS_REJECTED,
    AssetArchiveProof,
    AssetReadiness,
    MediaLifecycleAuditEntry,
    TranscodeJob,
)
from civiccast.schedule.media_lifecycle_worker import (
    FfmpegTranscodeExecutor,
    MediaLifecycleWorker,
    MediaLifecycleWorkerSettings,
    StubTranscodeExecutor,
    TranscodeExecutionResult,
)
from civiccast.schedule.models import Asset, ScheduleItem
from civiccast.stream import _ffmpeg as ffmpeg_module

_NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


@pytest.fixture
def engine() -> Iterator[Engine]:
    eng = create_engine("sqlite:///:memory:", future=True)
    eng = eng.execution_options(schema_translate_map={"civiccast": None})
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def session_factory(engine: Engine):  # type: ignore[no-untyped-def]
    @contextmanager
    def factory() -> Iterator[Session]:
        with Session(bind=engine) as session:
            yield session

    return factory


def _seed_asset(engine: Engine, **overrides: object) -> Asset:
    defaults: dict[str, object] = {
        "asset_id": "a1",
        "title": "Council Meeting",
        "state": "validated",
        "file_path": None,
        "file_status": "ok",
    }
    defaults.update(overrides)
    with Session(bind=engine) as session:
        asset = Asset(**defaults)  # type: ignore[arg-type]
        session.add(asset)
        session.commit()
        session.refresh(asset)
        return asset


def _worker(
    session_factory,  # type: ignore[no-untyped-def]
    *,
    dry_run: bool = False,
    executor=None,  # type: ignore[no-untyped-def]
) -> MediaLifecycleWorker:
    return MediaLifecycleWorker(
        session_factory,
        settings=MediaLifecycleWorkerSettings(mode="inline", poll_seconds=1.0, dry_run=dry_run),
        transcode_executor=executor or StubTranscodeExecutor(),
    )


# ---------------------------------------------------------------------------
# Table-driven readiness state computation
# ---------------------------------------------------------------------------


class TestReadinessStateComputation:
    def test_rejected_asset_reads_rejected(self, engine: Engine, session_factory) -> None:  # type: ignore[no-untyped-def]
        _seed_asset(engine, asset_id="a1", state="rejected")
        worker = _worker(session_factory)
        worker.run_once(now=_NOW)
        with Session(bind=engine) as session:
            row = session.get(AssetReadiness, "a1")
            assert row is not None
            assert row.readiness_state == READINESS_REJECTED

    def test_missing_file_asset_reads_missing_file(self, engine: Engine, session_factory) -> None:  # type: ignore[no-untyped-def]
        _seed_asset(engine, asset_id="a1", state="validated", file_status="missing")
        worker = _worker(session_factory)
        worker.run_once(now=_NOW)
        with Session(bind=engine) as session:
            row = session.get(AssetReadiness, "a1")
            assert row is not None
            assert row.readiness_state == READINESS_MISSING_FILE

    def test_pending_ingest_asset_reads_not_ready(self, engine: Engine, session_factory) -> None:  # type: ignore[no-untyped-def]
        _seed_asset(engine, asset_id="a1", state="pending_ingest")
        worker = _worker(session_factory)
        worker.run_once(now=_NOW)
        with Session(bind=engine) as session:
            row = session.get(AssetReadiness, "a1")
            assert row is not None
            assert row.readiness_state == READINESS_NOT_READY

    def test_validated_no_file_path_skips_transcode_seed_stays_ready(
        self,
        engine: Engine,
        session_factory,  # type: ignore[no-untyped-def]
    ) -> None:
        # No file_path -> nothing to transcode; the asset is as "ready" as
        # a manifest-only row can be (no in-flight jobs, none possible).
        _seed_asset(engine, asset_id="a1", state="validated", file_path=None)
        worker = _worker(session_factory)
        worker.run_once(now=_NOW)
        with Session(bind=engine) as session:
            row = session.get(AssetReadiness, "a1")
            assert row is not None
            assert row.readiness_state == READINESS_READY

    def test_validated_with_file_seeds_transcode_jobs_first_pass(
        self,
        engine: Engine,
        session_factory,
        tmp_path: Path,  # type: ignore[no-untyped-def]
    ) -> None:
        media_file = tmp_path / "meeting.mp4"
        media_file.write_bytes(b"\x00")
        _seed_asset(engine, asset_id="a1", state="validated", file_path=str(media_file))
        worker = _worker(
            session_factory,
            executor=StubTranscodeExecutor(),
        )
        result = worker.run_once(now=_NOW)
        assert (
            result.transcode_jobs_seeded == 1
        )  # DEFAULT_TRANSCODE_FORMATS (post ADR 0007 amendment)
        with Session(bind=engine) as session:
            jobs = session.query(TranscodeJob).filter(TranscodeJob.asset_id == "a1").all()
            assert len(jobs) == 1
            assert {j.status for j in jobs} == {"completed"}
            row = session.get(AssetReadiness, "a1")
            assert row is not None
            assert (
                row.readiness_state == READINESS_PENDING_TRANSCODE
            )  # not dispatched yet this pass

    def test_second_pass_after_dispatch_reads_ready(
        self,
        engine: Engine,
        session_factory,
        tmp_path: Path,  # type: ignore[no-untyped-def]
    ) -> None:
        media_file = tmp_path / "meeting.mp4"
        media_file.write_bytes(b"\x00")
        _seed_asset(engine, asset_id="a1", state="validated", file_path=str(media_file))
        worker = _worker(session_factory, executor=StubTranscodeExecutor())
        worker.run_once(now=_NOW)
        worker.run_once(now=_NOW)
        with Session(bind=engine) as session:
            row = session.get(AssetReadiness, "a1")
            assert row is not None
            assert row.readiness_state == READINESS_READY


# ---------------------------------------------------------------------------
# Dry-run mode: audit entries written, nothing else mutated
# ---------------------------------------------------------------------------


class TestDryRunVsApply:
    def test_dry_run_writes_no_readiness_row(
        self,
        engine: Engine,
        session_factory,
        tmp_path: Path,  # type: ignore[no-untyped-def]
    ) -> None:
        media_file = tmp_path / "meeting.mp4"
        media_file.write_bytes(b"\x00")
        _seed_asset(engine, asset_id="a1", state="validated", file_path=str(media_file))
        worker = _worker(session_factory, executor=StubTranscodeExecutor())

        result = worker.run_once(now=_NOW, dry_run=True)
        assert result.dry_run is True

        with Session(bind=engine) as session:
            assert session.get(AssetReadiness, "a1") is None
            assert session.query(TranscodeJob).count() == 0

    def test_dry_run_still_writes_audit_entries_tagged_dry_run(
        self,
        engine: Engine,
        session_factory,
        tmp_path: Path,  # type: ignore[no-untyped-def]
    ) -> None:
        media_file = tmp_path / "meeting.mp4"
        media_file.write_bytes(b"\x00")
        _seed_asset(engine, asset_id="a1", state="validated", file_path=str(media_file))
        worker = _worker(session_factory, executor=StubTranscodeExecutor())

        worker.run_once(now=_NOW, dry_run=True)

        with Session(bind=engine) as session:
            entries = (
                session.query(MediaLifecycleAuditEntry)
                .filter(MediaLifecycleAuditEntry.asset_id == "a1")
                .all()
            )
            assert entries, "dry run must still record what it would have done"
            assert all(e.dry_run for e in entries)

    def test_apply_mode_writes_readiness_row_and_unflagged_audit_entries(
        self,
        engine: Engine,
        session_factory,
        tmp_path: Path,  # type: ignore[no-untyped-def]
    ) -> None:
        media_file = tmp_path / "meeting.mp4"
        media_file.write_bytes(b"\x00")
        _seed_asset(engine, asset_id="a1", state="validated", file_path=str(media_file))
        worker = _worker(session_factory, executor=StubTranscodeExecutor())

        worker.run_once(now=_NOW, dry_run=False)

        with Session(bind=engine) as session:
            assert session.get(AssetReadiness, "a1") is not None
            entries = (
                session.query(MediaLifecycleAuditEntry)
                .filter(MediaLifecycleAuditEntry.asset_id == "a1")
                .all()
            )
            assert entries
            assert all(not e.dry_run for e in entries)


# ---------------------------------------------------------------------------
# Transcode dispatch: success + failure paths
# ---------------------------------------------------------------------------


class _FailingExecutor:
    def run(self, *, asset, output_format, output_dir):  # type: ignore[no-untyped-def]
        return TranscodeExecutionResult(success=False, error_detail="synthetic failure")


class TestTranscodeDispatch:
    @pytest.mark.parametrize(
        ("supervised", "override"), [(True, False), (True, True), (False, False)]
    )
    def test_output_root_respects_installed_service_layout(
        self,
        engine: Engine,
        session_factory,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        supervised: bool,
        override: bool,
    ) -> None:
        runtime = tmp_path / "Program Files" / "CivicCast (Native)" / "runtime"
        runtime.mkdir(parents=True)
        program_data = tmp_path / "ProgramData"
        monkeypatch.setenv("PROGRAMDATA", str(program_data))
        monkeypatch.setenv("CIVICCAST_SUPERVISED", "1" if supervised else "0")
        monkeypatch.delenv("CIVICCAST_TRANSCODE_OUTPUT_ROOT", raising=False)
        if override:
            output_root = tmp_path / "custom-transcodes"
            monkeypatch.setenv("CIVICCAST_TRANSCODE_OUTPUT_ROOT", str(output_root))
        elif supervised:
            output_root = program_data / "CivicCast" / "data" / "transcodes"
        else:
            output_root = runtime / "civiccast-data" / "transcodes"
        media_file = tmp_path / "meeting.mp4"
        media_file.write_bytes(b"\x00")
        _seed_asset(engine, file_path=str(media_file))
        # Model the installed cwd without changing the process cwd, which mutmut
        # uses to resolve its relative source paths while instrumenting calls.
        monkeypatch.setattr(Path, "cwd", classmethod(lambda cls: runtime))
        worker = MediaLifecycleWorker(
            session_factory,
            settings=MediaLifecycleWorkerSettings.from_env(),
            transcode_executor=StubTranscodeExecutor(),
        )

        result = worker.run_once(now=_NOW)

        assert result.transcode_jobs_completed == 1
        with Session(bind=engine) as session:
            job = session.query(TranscodeJob).one()
            assert Path(job.output_path).parent == output_root

    def test_failed_transcode_job_records_error_detail(
        self,
        engine: Engine,
        session_factory,
        tmp_path: Path,  # type: ignore[no-untyped-def]
    ) -> None:
        media_file = tmp_path / "meeting.mp4"
        media_file.write_bytes(b"\x00")
        _seed_asset(engine, asset_id="a1", state="validated", file_path=str(media_file))
        worker = _worker(session_factory, executor=_FailingExecutor())

        result = worker.run_once(now=_NOW)
        assert (
            result.transcode_jobs_failed == 1
        )  # DEFAULT_TRANSCODE_FORMATS (post ADR 0007 amendment)
        assert result.transcode_jobs_completed == 0

        with Session(bind=engine) as session:
            jobs = session.query(TranscodeJob).filter(TranscodeJob.asset_id == "a1").all()
            assert all(j.status == "failed" for j in jobs)
            assert all(j.error_detail == "synthetic failure" for j in jobs)

    def test_ffmpeg_executor_reports_missing_source_file_as_failure(self, tmp_path: Path) -> None:
        executor = FfmpegTranscodeExecutor()
        asset = Asset(
            asset_id="a1", title="x", state="validated", file_path=str(tmp_path / "missing.mp4")
        )
        result = executor.run(asset=asset, output_format="h264_720p_5mbps", output_dir=tmp_path)
        assert result.success is False
        assert result.error_detail is not None and "not found" in result.error_detail


# ---------------------------------------------------------------------------
# Archival gate (CLAUDE.md §4.6): portal + IA + NAS all verified, non-simulated
# ---------------------------------------------------------------------------


class TestArchiveCompleteGate:
    def test_no_proofs_never_archive_complete(self, engine: Engine, session_factory) -> None:  # type: ignore[no-untyped-def]
        _seed_asset(
            engine,
            asset_id="a1",
            state="recorded",
            manifest_url="https://portal.example/a1.m3u8",
            published_at=_NOW,
        )
        worker = _worker(session_factory)
        worker.run_once(now=_NOW)
        with Session(bind=engine) as session:
            row = session.get(AssetReadiness, "a1")
            assert row is not None
            assert row.archive_complete is False

    def test_simulated_proofs_never_count_toward_archive_complete(
        self,
        engine: Engine,
        session_factory,  # type: ignore[no-untyped-def]
    ) -> None:
        _seed_asset(
            engine,
            asset_id="a1",
            state="recorded",
            manifest_url="https://portal.example/a1.m3u8",
            published_at=_NOW,
        )
        with Session(bind=engine) as session:
            session.add(
                AssetArchiveProof(
                    asset_id="a1",
                    target_type="internet_archive",
                    target_url_or_path="https://simulated.invalid/a1",
                    verification_hash="sha256:" + "0" * 64,
                    simulated=True,
                )
            )
            session.add(
                AssetArchiveProof(
                    asset_id="a1",
                    target_type="local_nas_rsync",
                    target_url_or_path="/nas/a1.mp4",
                    verification_hash="sha256:" + "1" * 64,
                    simulated=True,
                )
            )
            session.commit()
        worker = _worker(session_factory)
        worker.run_once(now=_NOW)
        with Session(bind=engine) as session:
            row = session.get(AssetReadiness, "a1")
            assert row is not None
            assert row.archive_complete is False, (
                "a mock/simulated proof must never satisfy the gate"
            )

    def test_real_proofs_across_all_three_tiers_flip_archive_complete_true(
        self,
        engine: Engine,
        session_factory,  # type: ignore[no-untyped-def]
    ) -> None:
        _seed_asset(
            engine,
            asset_id="a1",
            state="recorded",
            manifest_url="https://portal.example/a1.m3u8",
            published_at=_NOW,
        )
        with Session(bind=engine) as session:
            session.add(
                AssetArchiveProof(
                    asset_id="a1",
                    target_type="internet_archive",
                    target_url_or_path="https://archive.org/details/a1",
                    verification_hash="sha256:" + "0" * 64,
                    simulated=False,
                )
            )
            session.add(
                AssetArchiveProof(
                    asset_id="a1",
                    target_type="local_nas_zfs",
                    target_url_or_path="zfs://civiccast/archive@a1",
                    verification_hash="sha256:" + "1" * 64,
                    simulated=False,
                )
            )
            session.commit()
        worker = _worker(session_factory)
        worker.run_once(now=_NOW)
        with Session(bind=engine) as session:
            row = session.get(AssetReadiness, "a1")
            assert row is not None
            assert row.archive_complete is True
            assert row.archive_portal_verified_at is not None
            assert row.archive_ia_verified_at is not None
            assert row.archive_nas_verified_at is not None

    def test_missing_portal_publish_blocks_archive_complete_even_with_both_proofs(
        self,
        engine: Engine,
        session_factory,  # type: ignore[no-untyped-def]
    ) -> None:
        _seed_asset(engine, asset_id="a1", state="recorded", manifest_url=None, published_at=None)
        with Session(bind=engine) as session:
            session.add(
                AssetArchiveProof(
                    asset_id="a1",
                    target_type="internet_archive",
                    target_url_or_path="https://archive.org/details/a1",
                    verification_hash="sha256:" + "0" * 64,
                    simulated=False,
                )
            )
            session.add(
                AssetArchiveProof(
                    asset_id="a1",
                    target_type="local_nas_zfs",
                    target_url_or_path="zfs://civiccast/archive@a1",
                    verification_hash="sha256:" + "1" * 64,
                    simulated=False,
                )
            )
            session.commit()
        worker = _worker(session_factory)
        worker.run_once(now=_NOW)
        with Session(bind=engine) as session:
            row = session.get(AssetReadiness, "a1")
            assert row is not None
            assert row.archive_complete is False


# ---------------------------------------------------------------------------
# Missing media (live join, not a durable flag)
# ---------------------------------------------------------------------------


class TestMissingMedia:
    def test_scheduled_item_with_unvalidated_asset_is_flagged(
        self,
        engine: Engine,
        session_factory,  # type: ignore[no-untyped-def]
    ) -> None:
        _seed_asset(engine, asset_id="a1", state="pending_ingest")
        with Session(bind=engine) as session:
            session.add(
                ScheduleItem(
                    asset_id="a1",
                    channel_id="public",
                    mode="premiere",
                    state="scheduled",
                    scheduled_at=_NOW + timedelta(days=1),
                    duration_seconds=3600,
                )
            )
            session.commit()
        worker = _worker(session_factory)
        missing = worker.list_missing_media(now=_NOW)
        assert len(missing) == 1
        assert missing[0]["asset_id"] == "a1"

    def test_validated_asset_is_not_flagged(self, engine: Engine, session_factory) -> None:  # type: ignore[no-untyped-def]
        _seed_asset(engine, asset_id="a1", state="validated")
        with Session(bind=engine) as session:
            session.add(
                ScheduleItem(
                    asset_id="a1",
                    channel_id="public",
                    mode="premiere",
                    state="scheduled",
                    scheduled_at=_NOW + timedelta(days=1),
                    duration_seconds=3600,
                )
            )
            session.commit()
        worker = _worker(session_factory)
        missing = worker.list_missing_media(now=_NOW)
        assert missing == []

    def test_item_beyond_horizon_is_not_flagged(self, engine: Engine, session_factory) -> None:  # type: ignore[no-untyped-def]
        _seed_asset(engine, asset_id="a1", state="pending_ingest")
        with Session(bind=engine) as session:
            session.add(
                ScheduleItem(
                    asset_id="a1",
                    channel_id="public",
                    mode="premiere",
                    state="scheduled",
                    scheduled_at=_NOW + timedelta(days=30),
                    duration_seconds=3600,
                )
            )
            session.commit()
        worker = _worker(session_factory)
        missing = worker.list_missing_media(now=_NOW)
        assert missing == []


# ---------------------------------------------------------------------------
# ADR 0007 amendment (S7 resource-posture / license audit): no GPL encoder
# in the default seed set, a station-level off switch for transcode
# seeding, an honest (non-silent) concurrency=1, and per-source-duration
# ffmpeg timeout + BELOW_NORMAL priority for dispatched jobs.
# ---------------------------------------------------------------------------


class TestGplLicensePosture:
    def test_no_hevc_format_in_default_seed_set(self) -> None:
        assert "h265_1080p_8mbps" not in DEFAULT_TRANSCODE_FORMATS
        assert not any("265" in fmt for fmt in DEFAULT_TRANSCODE_FORMATS)

    def test_no_libx265_literal_anywhere_in_the_format_catalog(self) -> None:
        for output_format, args in worker_module._FORMAT_FFMPEG_ARGS.items():
            assert "libx265" not in args, (
                f"{output_format!r} still carries a GPL libx265 literal: {args}"
            )


class TestTranscodeSeedingToggle:
    def test_enabled_by_default(self) -> None:
        assert MediaLifecycleWorkerSettings().transcode_seeding_enabled is True

    def test_disabled_seeds_nothing_and_reads_ready(
        self,
        engine: Engine,
        session_factory,  # type: ignore[no-untyped-def]
        tmp_path: Path,
    ) -> None:
        media_file = tmp_path / "meeting.mp4"
        media_file.write_bytes(b"\x00")
        _seed_asset(engine, asset_id="a1", state="validated", file_path=str(media_file))
        worker = MediaLifecycleWorker(
            session_factory,
            settings=MediaLifecycleWorkerSettings(
                mode="inline", poll_seconds=1.0, transcode_seeding_enabled=False
            ),
            transcode_executor=StubTranscodeExecutor(),
        )
        result = worker.run_once(now=_NOW)
        assert result.transcode_jobs_seeded == 0
        with Session(bind=engine) as session:
            assert session.query(TranscodeJob).filter(TranscodeJob.asset_id == "a1").count() == 0
            row = session.get(AssetReadiness, "a1")
            assert row is not None
            assert row.readiness_state == READINESS_READY, (
                "nothing to wait on is not the same as failed -- disabling seeding "
                "must not strand the asset in pending_transcode forever"
            )

    def test_from_env_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CIVICCAST_MEDIA_LIFECYCLE_TRANSCODE_SEEDING_ENABLED", "0")
        assert MediaLifecycleWorkerSettings.from_env().transcode_seeding_enabled is False

    def test_from_env_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CIVICCAST_MEDIA_LIFECYCLE_TRANSCODE_SEEDING_ENABLED", "true")
        assert MediaLifecycleWorkerSettings.from_env().transcode_seeding_enabled is True

    def test_from_env_unset_keeps_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CIVICCAST_MEDIA_LIFECYCLE_TRANSCODE_SEEDING_ENABLED", raising=False)
        assert MediaLifecycleWorkerSettings.from_env().transcode_seeding_enabled is True


class TestTranscodeConcurrencyIsHonest:
    """concurrency is config-surfaced but only one value is implemented --
    the field must say so instead of silently accepting an unhonored knob."""

    def test_default_is_one(self) -> None:
        assert MediaLifecycleWorkerSettings().transcode_concurrency == 1

    def test_any_other_value_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="transcode_concurrency"):
            MediaLifecycleWorkerSettings(transcode_concurrency=2)

    def test_dispatch_never_runs_two_jobs_at_once(
        self,
        engine: Engine,
        session_factory,  # type: ignore[no-untyped-def]
        tmp_path: Path,
    ) -> None:
        """Structural proof, not just a config assertion: dispatch actually
        serializes jobs one at a time (matters if a future change adds a
        thread pool without also updating transcode_concurrency)."""
        media_file = tmp_path / "meeting.mp4"
        media_file.write_bytes(b"\x00")
        _seed_asset(engine, asset_id="a1", state="validated", file_path=str(media_file))
        in_flight_count = 0
        max_observed = 0

        class _TrackingExecutor:
            def run(self, *, asset, output_format, output_dir):  # type: ignore[no-untyped-def]
                nonlocal in_flight_count, max_observed
                in_flight_count += 1
                max_observed = max(max_observed, in_flight_count)
                in_flight_count -= 1
                return TranscodeExecutionResult(success=True, output_path=None, file_size_bytes=0)

        worker = _worker(session_factory, executor=_TrackingExecutor())
        worker.run_once(now=_NOW)
        assert max_observed == 1


class TestTranscodeTimeoutBudget:
    """_transcode_timeout_seconds: per-minute-of-source budget, floored and
    capped well below ffmpeg's flat 6h default."""

    def test_unknown_duration_falls_back_to_ceiling(self) -> None:
        assert (
            worker_module._transcode_timeout_seconds(None)
            == worker_module._TRANSCODE_TIMEOUT_CEILING_SECONDS
        )

    def test_zero_or_negative_duration_falls_back_to_ceiling(self) -> None:
        assert (
            worker_module._transcode_timeout_seconds(0)
            == worker_module._TRANSCODE_TIMEOUT_CEILING_SECONDS
        )
        assert (
            worker_module._transcode_timeout_seconds(-5)
            == worker_module._TRANSCODE_TIMEOUT_CEILING_SECONDS
        )

    def test_short_clip_uses_the_floor(self) -> None:
        assert (
            worker_module._transcode_timeout_seconds(5)
            == worker_module._TRANSCODE_TIMEOUT_FLOOR_SECONDS
        )

    def test_budget_scales_with_source_duration(self) -> None:
        duration = 120
        expected = duration * worker_module._TRANSCODE_TIMEOUT_PER_SOURCE_SECOND
        assert expected > worker_module._TRANSCODE_TIMEOUT_FLOOR_SECONDS
        assert expected < worker_module._TRANSCODE_TIMEOUT_CEILING_SECONDS
        assert worker_module._transcode_timeout_seconds(duration) == expected

    def test_long_source_is_capped_below_ffmpegs_six_hour_default(self) -> None:
        result = worker_module._transcode_timeout_seconds(100_000)
        assert result == worker_module._TRANSCODE_TIMEOUT_CEILING_SECONDS
        assert result < ffmpeg_module._DEFAULT_TIMEOUT_SECONDS


class TestFfmpegTranscodeExecutorResourcePosture:
    """FfmpegTranscodeExecutor.run actually applies the never-upscale scale
    cap, the duration-based timeout, and BELOW_NORMAL priority -- not just
    that the pieces exist in isolation."""

    def _install_fakes(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
        captured: dict[str, object] = {}

        def fake_run_ffmpeg(args, **kwargs):  # type: ignore[no-untyped-def]
            captured["args"] = args
            captured["kwargs"] = kwargs
            return ffmpeg_module.FfmpegResult(returncode=1, stdout="", stderr="synthetic, unused")

        monkeypatch.setattr(ffmpeg_module, "check_ffmpeg", lambda: ("7.0", True))
        monkeypatch.setattr(ffmpeg_module, "resolve_h264_encoder", lambda **_kw: "libopenh264")
        monkeypatch.setattr(ffmpeg_module, "run_ffmpeg", fake_run_ffmpeg)
        return captured

    def test_caps_scale_at_source_height_never_upscales(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured = self._install_fakes(monkeypatch)
        media_file = tmp_path / "meeting.mp4"
        media_file.write_bytes(b"\x00")
        asset = Asset(
            asset_id="a1",
            title="x",
            state="validated",
            file_path=str(media_file),
            width_px=640,
            height_px=360,
        )
        FfmpegTranscodeExecutor().run(
            asset=asset, output_format="h264_720p_5mbps", output_dir=tmp_path
        )
        args = captured["args"]
        assert "scale=-2:360" in args
        assert "scale=-2:720" not in args, "must never upscale a 360p source to 720p"

    def test_odd_source_height_rounds_down_to_even_for_yuv420p(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured = self._install_fakes(monkeypatch)
        media_file = tmp_path / "meeting.mp4"
        media_file.write_bytes(b"\x00")
        asset = Asset(
            asset_id="a1",
            title="x",
            state="validated",
            file_path=str(media_file),
            width_px=641,
            height_px=361,
        )
        FfmpegTranscodeExecutor().run(
            asset=asset, output_format="h264_720p_5mbps", output_dir=tmp_path
        )
        assert "scale=-2:360" in captured["args"]

    def test_unknown_source_height_keeps_the_rungs_own_cap(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured = self._install_fakes(monkeypatch)
        media_file = tmp_path / "meeting.mp4"
        media_file.write_bytes(b"\x00")
        asset = Asset(
            asset_id="a1",
            title="x",
            state="validated",
            file_path=str(media_file),
            width_px=None,
            height_px=None,
        )
        FfmpegTranscodeExecutor().run(
            asset=asset, output_format="h264_720p_5mbps", output_dir=tmp_path
        )
        assert "scale=-2:720" in captured["args"]

    def test_taller_than_rung_source_still_caps_at_the_rung(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A 4K source must not push the proxy rung past its own cap."""
        captured = self._install_fakes(monkeypatch)
        media_file = tmp_path / "meeting.mp4"
        media_file.write_bytes(b"\x00")
        asset = Asset(
            asset_id="a1",
            title="x",
            state="validated",
            file_path=str(media_file),
            width_px=3840,
            height_px=2160,
        )
        FfmpegTranscodeExecutor().run(
            asset=asset, output_format="h264_720p_5mbps", output_dir=tmp_path
        )
        assert "scale=-2:720" in captured["args"]

    def test_passes_duration_based_timeout_not_the_flat_six_hour_default(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured = self._install_fakes(monkeypatch)
        media_file = tmp_path / "meeting.mp4"
        media_file.write_bytes(b"\x00")
        asset = Asset(
            asset_id="a1",
            title="x",
            state="validated",
            file_path=str(media_file),
            duration_seconds=120,
        )
        FfmpegTranscodeExecutor().run(
            asset=asset, output_format="h264_720p_5mbps", output_dir=tmp_path
        )
        assert captured["kwargs"]["timeout"] == worker_module._transcode_timeout_seconds(120)
        assert captured["kwargs"]["timeout"] < ffmpeg_module._DEFAULT_TIMEOUT_SECONDS

    def test_dispatches_at_background_priority(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        captured = self._install_fakes(monkeypatch)
        media_file = tmp_path / "meeting.mp4"
        media_file.write_bytes(b"\x00")
        asset = Asset(asset_id="a1", title="x", state="validated", file_path=str(media_file))
        FfmpegTranscodeExecutor().run(
            asset=asset, output_format="h264_720p_5mbps", output_dir=tmp_path
        )
        assert captured["kwargs"]["lower_priority"] is True

    def test_mezzanine_format_carries_no_scale_filter(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """h264_mezzanine is deliberately full-source-resolution -- no
        scale_filter placeholder, so the never-upscale cap does not apply
        (there is nothing to cap)."""
        captured = self._install_fakes(monkeypatch)
        media_file = tmp_path / "meeting.mp4"
        media_file.write_bytes(b"\x00")
        asset = Asset(
            asset_id="a1",
            title="x",
            state="validated",
            file_path=str(media_file),
            height_px=360,
        )
        FfmpegTranscodeExecutor().run(
            asset=asset, output_format="h264_mezzanine", output_dir=tmp_path
        )
        assert "-vf" not in captured["args"]
